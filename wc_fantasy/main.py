"""CLI entrypoint.

  py -m wc_fantasy.main update      # full pipeline -> dated report (cron-safe, idempotent)
  py -m wc_fantasy.main pick        # ideal 15-from-scratch benchmark + its XI
  py -m wc_fantasy.main recommend   # XI + captain + bench for your saved squad
  py -m wc_fantasy.main transfer    # recommended moves with -3 hit math
  py -m wc_fantasy.main status      # squad health: flags, stale sources, deadline
  py -m wc_fantasy.main simulate    # advancement probability table (needs advance.py)
  py -m wc_fantasy.main set-squad --ids 1,2,3,...   # store your 15 (one-time)
  py -m wc_fantasy.main schedule    # register a daily Windows task (automation)
  py -m wc_fantasy.main serve       # serve the docs/ web GUI over HTTP + open browser
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import optimize, report
from . import xpts as xptsmod
from . import constants as C
from .sources import (DATA_DIR, load_config, load_game_data, team_xg_lookup, GameData,
                      build_news_queries, gather_news)
from .extract import extract_events
from .player_state import compute_states, serialize_states, deserialize_states
from .models import FetchError, Player

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
    def __init__(self, offline: bool = False, with_news: bool = False, n_sims: int = 4000,
                 sim_seed: int | None = C.SIM_SEED, free_transfers_override: int | None = None):
        self.cfg = load_config()
        self.gd: GameData = load_game_data(self.cfg, offline=offline)
        self.priors = xptsmod.build_priors(self.gd.players, _load_priors_yaml())
        self.free_transfers = (free_transfers_override
                               if free_transfers_override is not None
                               else self.gd.free_transfers())
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

        self.sim_seed = sim_seed
        home_adv = self.cfg.get("strength", {}).get("home_advantage_goals", 0.0)
        self.xg = team_xg_lookup(self.gd, home_adv_goals=home_adv)
        try:
            from . import advance
            self.p_alive = advance.simulate(self.gd, n_sims=n_sims, seed=sim_seed)
        except Exception:
            self.p_alive = None  # horizon_values falls back to PRIOR_SURVIVAL
        self.horizon = xptsmod.horizon_values(
            self.gd, self.gd.players, self.priors, self.states, self.xg, p_alive=self.p_alive)
        self.next_xpts = xptsmod.next_match_xpts(
            self.gd, self.gd.players, self.priors, self.states, self.xg)


# --------------------------------------------------------------------------- commands
def cmd_pick(ctx: Context, formation: str | None = None) -> str:
    gd = ctx.gd
    ids = optimize.pick(gd.players, ctx.horizon,
                        budget=gd.budget(), country_cap=gd.country_cap())
    if not ids:
        return report.header(gd) + "\n\n**No feasible squad found.** Check data/staleness."
    squad_players = [p for p in gd.players if p.id in ids]
    xi = optimize.select_xi(squad_players, ctx.next_xpts, formation=formation)
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


def cmd_recommend(ctx: Context, formation: str | None = None) -> str:
    gd = ctx.gd
    ids = _current_squad_ids(ctx)
    if not ids:
        return (report.header(gd) +
                "\n\n_No saved squad. Run `set-squad --ids ...`, or see `pick` for the ideal team._")
    squad_players = [p for p in gd.players if p.id in ids]
    xi = optimize.select_xi(squad_players, ctx.next_xpts, formation=formation)
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
                            budget=gd.budget(), country_cap=gd.country_cap(),
                            free_transfers=ctx.free_transfers)
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
    table = advance.advancement_table(ctx.gd, seed=ctx.sim_seed)
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
        
        web_json_path = report.save_web_json(ctx.gd, ctx.horizon, ctx.next_xpts, ctx.states,
                                             ctx.cfg, seed=ctx.sim_seed)
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


def resolve_player(text: str, players: list) -> Optional[Player]:
    # try as ID
    try:
        pid = int(text.strip())
        for p in players:
            if p.id == pid:
                return p
    except ValueError:
        pass
    
    # fuzzy matching by name
    from .extract import _norm
    target = _norm(text)
    
    # Exact match first
    for p in players:
        if _norm(p.name) == target:
            return p
            
    # Substring match next
    matches = [p for p in players if target in _norm(p.name)]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        # Prefer exact word match if possible
        word_matches = [p for p in matches if any(target == w for w in _norm(p.name).split())]
        if len(word_matches) == 1:
            return word_matches[0]
        # otherwise return first or none
        return matches[0]
        
    return None


def cmd_swap(ctx: Context, out_arg: str | None, in_arg: str | None, top_n: int = 5) -> str:
    gd = ctx.gd
    squad_ids = _current_squad_ids(ctx)
    if not squad_ids:
        return report.header(gd) + "\n\n_No saved squad. Run `set-squad --ids ...` first._"
        
    if out_arg and in_arg:
        return report.header(gd) + "\n\n_Provide either --out or --in, not both, to identify the swap direction._"
    if not out_arg and not in_arg:
        return report.header(gd) + "\n\n_Provide --out <player> (to find replacements) or --in <player> (to find drops)._"
        
    if out_arg:
        p_out = resolve_player(out_arg, gd.players)
        if not p_out:
            return report.header(gd) + f"\n\n_Could not resolve out player: {out_arg}_"
        if p_out.id not in set(squad_ids):
            return report.header(gd) + f"\n\n_Player {p_out.name} is not in your squad._"
            
        candidates = optimize.best_replacements(
            gd.players, ctx.horizon, squad_ids, p_out.id,
            budget=gd.budget(), country_cap=gd.country_cap(), top_n=top_n
        )
        return report.header(gd) + "\n" + report.render_swaps(gd, candidates, p_out.name, is_out_mode=True)
        
    if in_arg:
        p_in = resolve_player(in_arg, gd.players)
        if not p_in:
            return report.header(gd) + f"\n\n_Could not resolve in player: {in_arg}_"
        if p_in.id in set(squad_ids):
            return report.header(gd) + f"\n\n_Player {p_in.name} is already in your squad._"
            
        candidates = optimize.drops_for_target(
            gd.players, ctx.horizon, squad_ids, p_in.id,
            budget=gd.budget(), country_cap=gd.country_cap(), top_n=top_n
        )
        return report.header(gd) + "\n" + report.render_swaps(gd, candidates, p_in.name, is_out_mode=False)
        
    return ""


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


def cmd_serve(port: int = 8000, open_browser: bool = True) -> int:
    """Serve the docs/ web GUI over HTTP and (optionally) open the browser.

    The GUI fetch()es data.json, which browsers block under the file:// protocol — so it
    MUST be served over HTTP. This does not touch live data; run `update` to refresh data.json.
    """
    import functools
    import http.server
    import socketserver
    import webbrowser

    docs = DATA_DIR.parent / "docs"
    if not (docs / "data.json").exists():
        print("⚠ docs/data.json not found — run `py -m wc_fantasy.main update` first to compile it.")

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(docs))
    socketserver.TCPServer.allow_reuse_address = True
    try:
        httpd = socketserver.TCPServer(("", port), handler)
    except OSError as exc:
        print(f"Could not bind port {port}: {exc}\nTry another port: "
              f"py -m wc_fantasy.main serve --port {port + 1}", file=sys.stderr)
        return 2

    url = f"http://localhost:{port}"
    print(f"Serving {docs} at {url}  (press Ctrl+C to stop)")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    with httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped.")
    return 0


# --------------------------------------------------------------------------- argparse
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wc_fantasy", description="WC Fantasy 2026 recommender")
    ap.add_argument("--offline", action="store_true", help="use cached snapshots/fixtures only")
    ap.add_argument("--reseed", action="store_true",
                    help="use fresh random Monte-Carlo draws (default is a fixed seed for reproducible data)")
    ap.add_argument("--free-transfers", type=int, default=None,
                    help="override number of free transfers for the current matchday")
    sub = ap.add_subparsers(dest="command", required=True)
    
    sub.add_parser("update")
    
    p_pick = sub.add_parser("pick")
    p_pick.add_argument("--formation", type=str, default=None, help="force starting XI formation (e.g. 3-5-2)")
    
    p_rec = sub.add_parser("recommend")
    p_rec.add_argument("--formation", type=str, default=None, help="force starting XI formation (e.g. 3-5-2)")
    
    sub.add_parser("transfer")
    sub.add_parser("status")
    sub.add_parser("simulate")
    sub.add_parser("schedule")
    
    sq = sub.add_parser("set-squad")
    sq.add_argument("--ids", help="comma-separated player ids")
    sq.add_argument("--file", help="json file with a list of player ids")
    
    sw = sub.add_parser("swap")
    sw.add_argument("--out", type=str, default=None, help="squad player to transfer OUT (name or ID)")
    sw.add_argument("--in", dest="in_player", type=str, default=None, help="database player to transfer IN (name or ID)")
    sw.add_argument("--top", type=int, default=5, help="number of candidates to display (default 5)")
    
    srv = sub.add_parser("serve")
    srv.add_argument("--port", type=int, default=8000, help="HTTP port (default 8000)")
    srv.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
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

    if args.command == "serve":
        return cmd_serve(port=args.port, open_browser=not args.no_browser)

    seed = None if args.reseed else C.SIM_SEED
    try:
        if args.command == "update":
            ctx = Context(offline=args.offline, with_news=True, n_sims=10000, sim_seed=seed,
                          free_transfers_override=args.free_transfers)
        else:
            ctx = Context(offline=args.offline, sim_seed=seed,
                          free_transfers_override=args.free_transfers)
    except FetchError as exc:
        print(f"FETCH ERROR: {exc}\nTry --offline to use cached data.", file=sys.stderr)
        return 2

    dispatch = {
        "update": cmd_update, "pick": cmd_pick, "recommend": cmd_recommend,
        "transfer": cmd_transfer, "status": cmd_status, "simulate": cmd_simulate,
        "schedule": cmd_schedule,
    }
    
    if args.command in ("pick", "recommend"):
        text = dispatch[args.command](ctx, formation=args.formation)
    elif args.command == "swap":
        text = cmd_swap(ctx, out_arg=args.out, in_arg=args.in_player, top_n=args.top)
    else:
        text = dispatch[args.command](ctx)
        
    print(text)
    if args.command in ("recommend", "transfer", "pick", "swap"):
        report.save_report(text, args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
