"""Human-readable recommendation reports (markdown) with receipts.

Rule: never hide *why*. A recommendation without its causing evidence is a bug. This is
the minimal MVP renderer; Phase 4 enriches it with full news receipts (headline+source+age
for every changed/flagged player). Reports print to terminal AND save to data/reports/.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .sources import GameData, DATA_DIR
from .optimize import TransferResult, XISelection
from .models import Player

REPORT_DIR = DATA_DIR / "reports"


def _nm(by_id: dict[int, Player], pid: int) -> str:
    p = by_id.get(pid)
    return f"{p.name} ({p.country[:3].upper()}, {p.position}, ${p.price:.1f})" if p else str(pid)


def header(gd: GameData) -> str:
    lines = [
        f"# WC Fantasy 2026 — recommendations",
        f"_generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · "
        f"next round R{gd.current_round_id} ({gd.current_stage}) · "
        f"deadline {gd.next_deadline or 'n/a'}_",
    ]
    if gd.stale:
        flags = ", ".join(f"{k} stale since {v}" for k, v in gd.stale.items())
        lines.append(f"\n> ⚠ **STALE DATA**: {flags}")
    return "\n".join(lines)


def render_squad(gd: GameData, ids: list[int], horizon: dict[int, float],
                 title: str = "Squad") -> str:
    by_id = {p.id: p for p in gd.players}
    rows = sorted(ids, key=lambda i: (by_id[i].position, -horizon.get(i, 0.0)))
    total_price = sum(by_id[i].price for i in ids)
    total_hv = sum(horizon.get(i, 0.0) for i in ids)
    out = [f"\n## {title}", f"_£{total_price:.1f}m · horizon value {total_hv:.1f}_", "",
           "| Pos | Player | Team | £ | Own% | Horizon |", "|---|---|---|--:|--:|--:|"]
    for i in rows:
        p = by_id[i]
        out.append(f"| {p.position} | {p.name} | {p.country} | {p.price:.1f} | "
                   f"{p.ownership:.1f} | {horizon.get(i, 0.0):.1f} |")
    return "\n".join(out)


def render_xi(gd: GameData, xi: XISelection, next_xpts: dict[int, float]) -> str:
    by_id = {p.id: p for p in gd.players}
    out = [f"\n## Starting XI — {xi.formation}",
           f"_expected next-match points (incl. captain): {xi.xi_points:.1f}_", "",
           f"**Captain:** {_nm(by_id, xi.captain_id)}  (×2)", "",
           "| Pos | Player | Team | next xPts |", "|---|---|---|--:|"]
    order = sorted(xi.xi_ids, key=lambda i: (by_id[i].position, -next_xpts.get(i, 0.0)))
    for i in order:
        p = by_id[i]
        star = " (C)" if i == xi.captain_id else ""
        out.append(f"| {p.position} | {p.name}{star} | {p.country} | {next_xpts.get(i, 0.0):.2f} |")
    out.append("\n**Bench:** " + " → ".join(_nm(by_id, i) for i in xi.bench_order))
    return "\n".join(out)


def render_transfers(gd: GameData, res: TransferResult, horizon: dict[int, float]) -> str:
    by_id = {p.id: p for p in gd.players}
    b = res.best
    out = ["\n## Transfer recommendation"]
    if b.n_transfers == 0:
        out.append("_No transfer improves the squad after the −3 hit. Hold._")
    else:
        # Pair OUT->IN within the same position (squad quota is fixed, so the counts
        # match per position): worst out swapped for best in, for a readable narrative.
        from collections import defaultdict
        outs, ins = defaultdict(list), defaultdict(list)
        for o in b.out_ids:
            outs[by_id[o].position].append(o)
        for i in b.in_ids:
            ins[by_id[i].position].append(i)
        for pos in ("GK", "DEF", "MID", "FWD"):
            o_sorted = sorted(outs[pos], key=lambda x: horizon.get(x, 0.0))
            i_sorted = sorted(ins[pos], key=lambda x: horizon.get(x, 0.0), reverse=True)
            for o, i in zip(o_sorted, i_sorted):
                out.append(f"- **OUT** {_nm(by_id, o)} (hv {horizon.get(o, 0.0):.1f}) → "
                           f"**IN** {_nm(by_id, i)} (hv {horizon.get(i, 0.0):.1f})")
        out.append(f"\n**Net {b.net_gain:+.1f}** "
                   f"(gross {b.gross_gain:+.1f} − hit {b.hit_cost:.0f}) over {b.n_transfers} transfer(s).")
    out += ["", "### Marginal value of each extra transfer", "",
            "| Transfers | Gross gain | Hit | Net |", "|--:|--:|--:|--:|"]
    for plan in res.table:
        mark = " ⬅ best" if plan is b else ""
        out.append(f"| {plan.n_transfers} | {plan.gross_gain:+.1f} | "
                   f"{plan.hit_cost:.0f} | {plan.net_gain:+.1f}{mark} |")
    return "\n".join(out)


def save_report(text: str, name: str) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{name}_{datetime.now(timezone.utc):%Y%m%d}.md"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
