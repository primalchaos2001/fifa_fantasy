"""CLI entrypoint.

  py -m wc_fantasy.main update      # full pipeline -> dated report (cron-safe, idempotent)
  py -m wc_fantasy.main pick        # ideal 15-from-scratch benchmark + its XI
  py -m wc_fantasy.main recommend   # XI + captain + bench for your saved squad
  py -m wc_fantasy.main transfer    # recommended moves with -3 hit math
  py -m wc_fantasy.main status      # squad health: flags, stale sources, deadline
  py -m wc_fantasy.main simulate    # advancement probability table (needs advance.py)
  py -m wc_fantasy.main set-squad --ids 1,2,3,...   # store your 15 (one-time)
  py -m wc_fantasy.main schedule    # register a daily Windows task (automation)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import optimize, report
from . import xpts as xptsmod
from .sources import (DATA_DIR, load_config, load_game_data, team_xg_lookup, GameData,
                      build_news_queries, gather_news)
from .extract import extract_events
from .player_state import compute_states, serialize_states, deserialize_states
from .models import FetchError

STATE_PATH = DATA_DIR / "state.json"
PRIORS_PATH = Path(__file__).resolve().parents[1] / "priors.yaml"


# --------------------------------------------------------------------------- state io
def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, ensure_ascii=False)


def _load_priors_yaml() -> dict | None:
    if not PRIORS_PATH.exists():
        return None
    import yaml
    with open(PRIORS_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # accept {players: {id: {...}}} or {id: {...}}
    return data.get("players", data)


# --------------------------------------------------------------------------- context
class Context:
    def __init__(self, offline: bool = False, with_news: bool = False, n_sims: int = 4000):
        self.cfg = load_config()
        self.gd: GameData = load_game_data(self.cfg, offline=offline)
        self.priors = xptsmod.build_priors(self.gd.players, _load_priors_yaml())
        self.news_events: list = []
        self.news_stale: dict = {}

        # Player states: fresh official baseline, plus the news layer.
        states = xptsmod.default_states(self.gd.players)
        persisted = deserialize_states(load_state().get("player_states", {}))
        if with_news and self.cfg.get("news", {}).get("enabled", True) and not offline:
            squad = load_state().get("squad", []) or []
            queries = build_news_queries(self.gd, squad, self.cfg.get("watchlist_size", 60))
            items, self.news_stale = gather_news(self.cfg, queries)
            self.news_events = extract_events(items, self.gd.players, self.cfg)
            states = compute_states(self.gd.players, self.news_events, self.cfg, persisted)
            st = load_state()
            st["player_states"] = serialize_states(states)
            save_state(st)
        else:
            # between updates: keep fresh lineup baseline but honour sticky news negatives
            for pid, ps in persisted.items():
                if pid in states and ps.status in ("ruled_out", "suspended"):
                    states[pid].p_start, states[pid].status = 0.0, ps.status
        self.states = states

        home_adv = self.cfg.get("strength", {}).get("home_advantage_goals", 0.0)
        self.xg = team_xg_lookup(self.gd, home_adv_goals=home_adv)
        try:
            from . import advance
            self.p_alive = advance.simulate(self.gd, n_sims=n_sims)
        except Exception:
            self.p_alive = None  # horizon_values falls back to PRIOR_SURVIVAL
        self.horizon = xptsmod.horizon_values(
            self.gd, self.gd.players, self.priors, self.states, self.xg, p_alive=self.p_alive)
        self.next_xpts = xptsmod.next_match_xpts(
            self.gd, self.gd.players, self.priors, self.states, self.xg)


# --------------------------------------------------------------------------- commands
def cmd_pick(ctx: Context) -> str:
    gd = ctx.gd
    ids = optimize.pick(gd.players, ctx.horizon,
                        budget=gd.budget(), country_cap=gd.country_cap())
    if not ids:
        return report.header(gd) + "\n\n**No feasible squad found.** Check data/staleness."
    squad_players = [p for p in gd.players if p.id in ids]
    xi = optimize.select_xi(squad_players, ctx.next_xpts)
    parts = [report.header(gd),
             report.render_squad(gd, ids, ctx.horizon, title="Ideal squad (benchmark)")]
    if xi:
        parts.append(report.render_xi(gd, xi, ctx.next_xpts))
    return "\n".join(parts)


def _current_squad_ids(ctx: Context) -> list[int] | None:
    state = load_state()
    ids = state.get("squad")
    if not ids:
        return None
    valid = {p.id for p in ctx.gd.players}
    return [i for i in ids if i in valid]


def cmd_recommend(ctx: Context) -> str:
    gd = ctx.gd
    ids = _current_squad_ids(ctx)
    if not ids:
        return (report.header(gd) +
                "\n\n_No saved squad. Run `set-squad --ids ...`, or see `pick` for the ideal team._")
    squad_players = [p for p in gd.players if p.id in ids]
    xi = optimize.select_xi(squad_players, ctx.next_xpts)
    parts = [report.header(gd), report.render_squad(gd, ids, ctx.horizon, title="Your squad")]
    if xi:
        parts.append(report.render_xi(gd, xi, ctx.next_xpts))
    return "\n".join(parts)


def cmd_transfer(ctx: Context) -> str:
    gd = ctx.gd
    ids = _current_squad_ids(ctx)
    if not ids:
        return (report.header(gd) +
                "\n\n_No saved squad to transfer from. Run `set-squad --ids ...` first._")
    res = optimize.transfer(gd.players, ctx.horizon, ids,
                            budget=gd.budget(), country_cap=gd.country_cap())
    parts = [report.header(gd), report.render_squad(gd, ids, ctx.horizon, title="Your squad")]
    if res:
        parts.append(report.render_transfers(gd, res, ctx.horizon))
    else:
        parts.append("\n_Transfer optimization infeasible._")
    return "\n".join(parts)


def cmd_status(ctx: Context) -> str:
    gd = ctx.gd
    out = [report.header(gd), "\n## Status"]
    ids = _current_squad_ids(ctx)
    if ids:
        by_id = {p.id: p for p in gd.players}
        problems, notes = [], []
        for i in ids:
            p = by_id[i]
            if p.status != "playing" or p.match_status == "not_in_squad":
                problems.append(f"- ⚠ OUT/unavailable: {p.name} ({p.country}) — "
                                f"status={p.status} matchStatus={p.match_status}")
            elif p.match_status == "sub":
                notes.append(f"- 🪑 benched (named as sub): {p.name} ({p.country})")
            elif p.match_status == "start":
                notes.append(f"- ✅ confirmed to start: {p.name} ({p.country})")
        if problems:
            out.append("**Availability problems:**")
            out.extend(problems)
        if notes:
            out.append("\n**Confirmed lineup info (imminent MD):**")
            out.extend(notes)
        if not problems and not notes:
            out.append("_No availability flags or confirmed-lineup info yet for your squad._")
    else:
        out.append("_No saved squad._")
    out.append(f"\n_Sources: {'STALE — ' + ', '.join(gd.stale) if gd.stale else 'fresh'}_")
    return "\n".join(out)


def cmd_simulate(ctx: Context) -> str:
    try:
        from . import advance  # Phase 2 (Fable leaf)
    except ImportError:
        return (report.header(ctx.gd) +
                "\n\n_simulate: advance.py (Monte Carlo) not built yet — Phase 2._")
    table = advance.advancement_table(ctx.gd)
    return report.header(ctx.gd) + "\n\n" + advance.render_table(table, ctx.gd)


def _age(ts: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hrs = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        return f"{hrs:.0f}h ago" if hrs >= 1 else "just now"
    except (ValueError, TypeError):
        return "unknown age"


def _render_receipts(ctx: Context) -> str:
    """Show the headlines that drove changes — receipts. Never hide why."""
    if not ctx.news_events:
        return "\n## News receipts\n_No new availability news this run._"
    squad = set(load_state().get("squad", []) or [])
    by_name = {p.name: p.id for p in ctx.gd.players}
    lines, shown = ["\n## News receipts"], 0
    for ev in ctx.news_events:
        pid = by_name.get(ev.player)
        important = (pid in squad) or ev.event_type in ("ruled_out", "suspension", "confirmed_start")
        if not important:
            continue
        flag = "⚠" if ev.event_type in ("ruled_out", "suspension", "injury_doubt", "benched") else "✅"
        lines.append(f"- {flag} **{ev.player}** — {ev.event_type} ({ev.confidence}) · "
                     f"{ev.source_title} _[{ev.source_kind}, {_age(ev.timestamp)}]_")
        shown += 1
        if shown >= 25:
            break
    return "\n".join(lines) if shown > 1 else "\n## News receipts\n_No squad-relevant news this run._"


def cmd_update(ctx: Context) -> str:
    """Full idempotent pipeline -> a dated report combining recommend + transfer + receipts + status."""
    ctx.gd.stale.update({f"news:{k}": v for k, v in ctx.news_stale.items()})
    parts = [cmd_recommend(ctx)]
    if _current_squad_ids(ctx):
        parts.append(_section(cmd_transfer(ctx)))
    parts.append(_render_receipts(ctx))
    parts.append(_section(cmd_status(ctx)))
    text = "\n".join(parts)
    path = report.save_report(text, "update")
    print(f"[saved] {path}")

    # Export for web GUI
    try:
        docs_dir = report.DATA_DIR.parent / "docs"
        assets_dir = docs_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        video_path = assets_dir / "kickoff.mp4"
        if not video_path.exists():
            video_url = ctx.cfg.get("web", {}).get("video_url")
            if video_url:
                print(f"[web] downloading default kickoff video from {video_url}...")
                try:
                    import urllib.request
                    req = urllib.request.Request(video_url, headers={"User-Agent": "wc-fantasy/0.1"})
                    with urllib.request.urlopen(req, timeout=30) as resp, open(video_path, "wb") as fh:
                        fh.write(resp.read())
                    print("[web] downloaded kickoff video successfully.")
                except Exception as exc:
                    print(f"[web] WARNING: failed to download kickoff video: {exc}.")
        
        web_json_path = report.save_web_json(ctx.gd, ctx.horizon, ctx.next_xpts, ctx.states, ctx.cfg)
        print(f"[web saved] {web_json_path}")
    except Exception as exc:
        print(f"[web WARNING] failed to generate web json or directory: {exc}")

    return text



def _section(full_report: str) -> str:
    """Drop the duplicated header/squad block when stitching sub-reports together."""
    marker = "## Transfer recommendation"
    if marker in full_report:
        return "\n" + marker + full_report.split(marker, 1)[1]
    marker = "## Status"
    if marker in full_report:
        return "\n" + marker + full_report.split(marker, 1)[1]
    return full_report


def cmd_set_squad(ids_arg: str | None, file_arg: str | None) -> str:
    if file_arg:
        with open(file_arg, encoding="utf-8") as fh:
            ids = json.load(fh)
    elif ids_arg:
        ids = [int(x) for x in ids_arg.replace(" ", "").split(",") if x]
    else:
        return "Provide --ids '1,2,3,...' or --file squad.json"
    state = load_state()
    state["squad"] = ids
    save_state(state)
    return f"Saved squad of {len(ids)} players to {STATE_PATH}"


def cmd_schedule(ctx: Context) -> str:
    sch = ctx.cfg.get("schedule", {})
    hour = sch.get("normal_hour", 6)
    py = sys.executable
    cmd = (f'schtasks /Create /SC DAILY /TN "WCFantasyDailyUpdate" '
           f'/TR "{py} -m wc_fantasy.main update" /ST {hour:02d}:00 /F')
    return ("## Automation\nRun this once (PowerShell) to update daily and write a report:\n\n"
            f"```\n{cmd}\n```\n\n"
            "_Within 24h of a deadline, run more often (e.g. add an hourly task) — the "
            "deadline is read live from rounds.json._")


# --------------------------------------------------------------------------- argparse
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wc_fantasy", description="WC Fantasy 2026 recommender")
    ap.add_argument("--offline", action="store_true", help="use cached snapshots/fixtures only")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("update", "pick", "recommend", "transfer", "status", "simulate", "schedule"):
        sub.add_parser(name)
    sq = sub.add_parser("set-squad")
    sq.add_argument("--ids", help="comma-separated player ids")
    sq.add_argument("--file", help="json file with a list of player ids")
    return ap


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which can't print player names / arrows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    args = build_parser().parse_args(argv)

    if args.command == "set-squad":
        print(cmd_set_squad(args.ids, args.file))
        return 0

    try:
        if args.command == "update":
            ctx = Context(offline=args.offline, with_news=True, n_sims=10000)
        else:
            ctx = Context(offline=args.offline)
    except FetchError as exc:
        print(f"FETCH ERROR: {exc}\nTry --offline to use cached data.", file=sys.stderr)
        return 2

    dispatch = {
        "update": cmd_update, "pick": cmd_pick, "recommend": cmd_recommend,
        "transfer": cmd_transfer, "status": cmd_status, "simulate": cmd_simulate,
        "schedule": cmd_schedule,
    }
    text = dispatch[args.command](ctx)
    print(text)
    if args.command in ("recommend", "transfer", "pick"):
        report.save_report(text, args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
