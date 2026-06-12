"""PuLP integer programs: pick (15 from scratch), transfer (with the -3 hit math), XI+captain.

Shared constraints (stage-correct): exactly 15 = 2GK/5DEF/5MID/3FWD, budget (100 group ->
105 R32), max N players per country. Objective is horizon value (the optimizer's currency).
Every solve is a dumb, debuggable ILP — no heuristics that could silently violate a rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pulp

from . import constants as C
from .models import Player, POSITIONS


# ---------------------------------------------------------------------------
# Core squad ILP
# ---------------------------------------------------------------------------

def _solve_squad(players: list[Player], value: dict[int, float], *, budget: float,
                 country_cap: int, current_ids: Optional[set[int]] = None,
                 max_changes: Optional[int] = None) -> Optional[list[int]]:
    """Maximize Σ value subject to squad rules. Optionally limit changes from current_ids."""
    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    x = {p.id: pulp.LpVariable(f"x_{p.id}", cat="Binary") for p in players}

    prob += pulp.lpSum(value.get(p.id, 0.0) * x[p.id] for p in players)

    prob += pulp.lpSum(x.values()) == C.SQUAD_SIZE
    for pos, quota in C.SQUAD_QUOTA.items():
        prob += pulp.lpSum(x[p.id] for p in players if p.position == pos) == quota
    prob += pulp.lpSum(p.price * x[p.id] for p in players) <= budget

    countries: dict[str, list[int]] = {}
    for p in players:
        countries.setdefault(p.country, []).append(p.id)
    for ids in countries.values():
        prob += pulp.lpSum(x[i] for i in ids) <= country_cap

    if current_ids is not None and max_changes is not None:
        # transfers_out = number of current players dropped <= max_changes
        prob += pulp.lpSum(1 - x[i] for i in current_ids if i in x) <= max_changes

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[status] != "Optimal":
        return None
    return [p.id for p in players if x[p.id].value() and x[p.id].value() > 0.5]


def pick(players: list[Player], horizon: dict[int, float], *, budget: float,
         country_cap: int) -> Optional[list[int]]:
    """Best 15-from-scratch by horizon value — the benchmark 'ideal team'."""
    return _solve_squad(players, horizon, budget=budget, country_cap=country_cap)


# ---------------------------------------------------------------------------
# Transfers (the main use now) with transparent -3 hit math
# ---------------------------------------------------------------------------

@dataclass
class TransferPlan:
    n_transfers: int
    out_ids: list[int]
    in_ids: list[int]
    gross_gain: float            # horizon-value gain vs current squad
    hit_cost: float              # 3 * extra transfers
    net_gain: float              # gross - hit
    squad_ids: list[int]


@dataclass
class TransferResult:
    best: TransferPlan
    table: list[TransferPlan]    # one row per k = 0..K (marginal value of each extra hit)


def transfer(players: list[Player], horizon: dict[int, float], current_ids: list[int], *,
             budget: float, country_cap: int,
             free_transfers: int = C.FREE_TRANSFERS_DEFAULT,
             max_extra: int = 4) -> Optional[TransferResult]:
    """Recommend transfers. Solves the family k = 0..K and picks max net of -3 hits."""
    cur = set(current_ids)
    by_id = {p.id: p for p in players}
    base_value = sum(horizon.get(i, 0.0) for i in cur)
    k_max = min(free_transfers + max_extra, C.SQUAD_SIZE)

    table: list[TransferPlan] = []
    for k in range(0, k_max + 1):
        squad = _solve_squad(players, horizon, budget=budget, country_cap=country_cap,
                             current_ids=cur, max_changes=k)
        if squad is None:
            continue
        new = set(squad)
        out_ids = sorted(cur - new)
        in_ids = sorted(new - cur)
        gross = sum(horizon.get(i, 0.0) for i in squad) - base_value
        extra = max(0, len(out_ids) - free_transfers)
        hit = abs(C.TRANSFER_HIT_PTS) * extra
        table.append(TransferPlan(
            n_transfers=len(out_ids), out_ids=out_ids, in_ids=in_ids,
            gross_gain=gross, hit_cost=hit, net_gain=gross - hit, squad_ids=squad))

    if not table:
        return None
    # collapse to unique transfer counts (the k constraint is "<=", so dedupe by n_transfers)
    by_n: dict[int, TransferPlan] = {}
    for plan in table:
        if plan.n_transfers not in by_n or plan.gross_gain > by_n[plan.n_transfers].gross_gain:
            by_n[plan.n_transfers] = plan
    rows = [by_n[n] for n in sorted(by_n)]
    best = max(rows, key=lambda p: p.net_gain)
    return TransferResult(best=best, table=rows)


# ---------------------------------------------------------------------------
# Starting XI + captain + bench
# ---------------------------------------------------------------------------

@dataclass
class XISelection:
    xi_ids: list[int]
    captain_id: int
    bench_order: list[int]
    formation: str
    xi_points: float             # expected next-match points incl. captain doubling


def select_xi(squad_players: list[Player], next_xpts: dict[int, float]) -> Optional[XISelection]:
    """Pick the best valid-formation XI from the 15, then captain + bench order."""
    prob = pulp.LpProblem("xi", pulp.LpMaximize)
    y = {p.id: pulp.LpVariable(f"y_{p.id}", cat="Binary") for p in squad_players}

    prob += pulp.lpSum(next_xpts.get(p.id, 0.0) * y[p.id] for p in squad_players)
    prob += pulp.lpSum(y.values()) == C.XI_SIZE

    # Formation selection binary variables
    form_vars = {form: pulp.LpVariable(f"form_{form}", cat="Binary") for form in C.ALLOWED_FORMATION_COUNTS}
    prob += pulp.lpSum(form_vars.values()) == 1

    # Constrain position counts to match the chosen formation
    for pos in ("GK", "DEF", "MID", "FWD"):
        cnt = pulp.lpSum(y[p.id] for p in squad_players if p.position == pos)
        if pos == "GK":
            prob += cnt == 1
        else:
            prob += cnt == pulp.lpSum(C.ALLOWED_FORMATION_COUNTS[form][pos] * form_vars[form] for form in C.ALLOWED_FORMATION_COUNTS)

    status = prob.solve(pulp.PULP_CBC_CMD(msg=0))
    if pulp.LpStatus[status] != "Optimal":
        return None

    xi = [p for p in squad_players if y[p.id].value() and y[p.id].value() > 0.5]
    bench = [p for p in squad_players if p not in xi]

    captain = max(xi, key=lambda p: next_xpts.get(p.id, 0.0))
    # bench: outfield by xpts desc, but keep the reserve GK distinct at the end
    bench_gk = [p for p in bench if p.position == "GK"]
    bench_out = sorted([p for p in bench if p.position != "GK"],
                       key=lambda p: next_xpts.get(p.id, 0.0), reverse=True)
    bench_order = [p.id for p in bench_out] + [p.id for p in bench_gk]

    counts = {pos: sum(1 for p in xi if p.position == pos) for pos in POSITIONS}
    formation = f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"
    xi_points = (sum(next_xpts.get(p.id, 0.0) for p in xi)
                 + next_xpts.get(captain.id, 0.0))  # captain doubled => +1x extra

    return XISelection(xi_ids=[p.id for p in xi], captain_id=captain.id,
                       bench_order=bench_order, formation=formation, xi_points=xi_points)
