"""ILP squad/XI/transfer optimizer tests on a small synthetic, fully controlled pool."""
from __future__ import annotations

import pytest

from wc_fantasy import constants as C
from wc_fantasy import optimize
from wc_fantasy.models import Player

BUDGET = 100.0
COUNTRY_CAP = 3


def make_pool() -> list[Player]:
    """60 players: 10 countries x (1 GK, 2 DEF, 2 MID, 1 FWD). Deterministic prices."""
    players: list[Player] = []
    pid = 0
    for c in range(10):
        for pos, n in (("GK", 1), ("DEF", 2), ("MID", 2), ("FWD", 1)):
            for _ in range(n):
                pid += 1
                players.append(Player(
                    id=pid, name=f"P{pid}", country=f"C{c}", squad_id=c,
                    position=pos, price=4.0 + (pid % 7) * 0.5, ownership=10.0,
                    total_points=0, status="playing",
                ))
    return players


def make_horizon(players: list[Player]) -> dict[int, float]:
    """Country C0 made hugely attractive so the country cap must actually bite."""
    horizon = {}
    for p in players:
        horizon[p.id] = 10.0 + p.id * 0.1
        if p.country == "C0":
            horizon[p.id] += 50.0
    return horizon


def assert_valid_squad(ids: list[int], players: list[Player],
                       budget: float = BUDGET, cap: int = COUNTRY_CAP) -> None:
    by_id = {p.id: p for p in players}
    squad = [by_id[i] for i in ids]
    assert len(ids) == C.SQUAD_SIZE == 15
    assert len(set(ids)) == 15, "duplicate player ids in squad"
    counts = {pos: sum(1 for p in squad if p.position == pos) for pos in C.SQUAD_QUOTA}
    assert counts == {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
    assert sum(p.price for p in squad) <= budget + 1e-6
    per_country: dict[str, int] = {}
    for p in squad:
        per_country[p.country] = per_country.get(p.country, 0) + 1
    assert max(per_country.values()) <= cap, f"country cap violated: {per_country}"


def current_squad_ids(players: list[Player]) -> list[int]:
    """A valid but deliberately weak 15: 3 players each from countries C0..C4."""
    want = {0: ["GK", "DEF", "DEF"], 1: ["GK", "DEF", "DEF"],
            2: ["DEF", "MID", "FWD"], 3: ["MID", "MID", "FWD"],
            4: ["MID", "MID", "FWD"]}
    ids: list[int] = []
    for c, positions in want.items():
        pool = [p for p in players if p.squad_id == c]
        for pos in positions:
            pick = next(p for p in pool if p.position == pos and p.id not in ids)
            ids.append(pick.id)
    return ids


# ---------------------------------------------------------------------------
# pick
# ---------------------------------------------------------------------------

def test_pick_returns_valid_15():
    players = make_pool()
    horizon = make_horizon(players)
    squad = optimize.pick(players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    assert squad is not None
    assert_valid_squad(squad, players)


def test_pick_country_cap_binds_on_stacked_country():
    """C0 players carry +50 value; without the cap the ILP would take all 6 of them."""
    players = make_pool()
    horizon = make_horizon(players)
    squad = optimize.pick(players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    by_id = {p.id: p for p in players}
    n_c0 = sum(1 for i in squad if by_id[i].country == "C0")
    assert n_c0 == COUNTRY_CAP  # cap is tight: it wants more, gets exactly 3


def test_pick_respects_tight_budget():
    players = make_pool()
    horizon = make_horizon(players)
    tight = 70.0  # cheapest possible 15 costs ~60-70, so this forces real budget pressure
    squad = optimize.pick(players, horizon, budget=tight, country_cap=COUNTRY_CAP)
    if squad is not None:
        by_id = {p.id: p for p in players}
        assert sum(by_id[i].price for i in squad) <= tight + 1e-6


def test_pick_on_real_offline_players(gd):
    horizon = {p.id: p.ownership + p.price for p in gd.players}
    squad = optimize.pick(gd.players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    assert squad is not None
    assert_valid_squad(squad, gd.players)


# ---------------------------------------------------------------------------
# select_xi
# ---------------------------------------------------------------------------

def test_select_xi_valid_formation_and_captain():
    players = make_pool()
    horizon = make_horizon(players)
    squad_ids = optimize.pick(players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    by_id = {p.id: p for p in players}
    squad_players = [by_id[i] for i in squad_ids]
    next_xpts = {p.id: 1.0 + p.id * 0.01 for p in squad_players}

    sel = optimize.select_xi(squad_players, next_xpts)
    assert sel is not None
    assert len(sel.xi_ids) == C.XI_SIZE == 11
    assert len(set(sel.xi_ids)) == 11

    counts = {pos: sum(1 for i in sel.xi_ids if by_id[i].position == pos)
              for pos in ("GK", "DEF", "MID", "FWD")}
    assert counts["GK"] == 1
    assert 3 <= counts["DEF"] <= 5
    assert 2 <= counts["MID"] <= 5
    assert 1 <= counts["FWD"] <= 3
    assert sum(counts.values()) == 11

    # captain in the XI and is the XI's max-xpts player
    assert sel.captain_id in sel.xi_ids
    best = max(sel.xi_ids, key=lambda i: next_xpts[i])
    assert sel.captain_id == best

    # bench = the remaining 4, no overlap with the XI
    assert len(sel.bench_order) == 4
    assert set(sel.bench_order) | set(sel.xi_ids) == set(squad_ids)
    assert not set(sel.bench_order) & set(sel.xi_ids)


def test_select_xi_formation_ranges_cap_loaded_position():
    """All DEF carry huge xpts: the XI must still stop at 5 DEF and keep MID>=2, FWD>=1."""
    players = make_pool()
    horizon = make_horizon(players)
    squad_ids = optimize.pick(players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    by_id = {p.id: p for p in players}
    squad_players = [by_id[i] for i in squad_ids]
    next_xpts = {p.id: (100.0 if p.position == "DEF" else 1.0) for p in squad_players}

    sel = optimize.select_xi(squad_players, next_xpts)
    assert sel is not None
    counts = {pos: sum(1 for i in sel.xi_ids if by_id[i].position == pos)
              for pos in ("GK", "DEF", "MID", "FWD")}
    assert counts["DEF"] == 5      # wants more, capped at 5
    assert counts["GK"] == 1
    assert counts["MID"] >= 2
    assert counts["FWD"] >= 1
    assert sel.formation == f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"


# ---------------------------------------------------------------------------
# transfer
# ---------------------------------------------------------------------------

def test_transfer_constraints_and_monotonic_gains():
    players = make_pool()
    horizon = make_horizon(players)
    cur = current_squad_ids(players)
    assert_valid_squad(cur, players)  # sanity: the starting squad itself is legal

    res = optimize.transfer(players, horizon, cur,
                            budget=BUDGET, country_cap=COUNTRY_CAP,
                            free_transfers=2, max_extra=3)
    assert res is not None
    assert res.table, "expected at least the 0-transfer row"

    # every recommended squad (including best) must satisfy all squad rules
    for plan in res.table:
        assert_valid_squad(plan.squad_ids, players)
        assert plan.n_transfers == len(plan.out_ids) == len(plan.in_ids)
        assert set(plan.out_ids) <= set(cur)
        assert not set(plan.in_ids) & set(cur)
        # hit math: -3 per transfer beyond the free allowance
        expected_hit = 3 * max(0, plan.n_transfers - 2)
        assert plan.hit_cost == pytest.approx(expected_hit)
        assert plan.net_gain == pytest.approx(plan.gross_gain - plan.hit_cost)
    assert_valid_squad(res.best.squad_ids, players)

    # monotonic-ish: more allowed transfers never reduce the best achievable gross gain
    rows = sorted(res.table, key=lambda p: p.n_transfers)
    for a, b in zip(rows, rows[1:]):
        assert b.gross_gain >= a.gross_gain - 1e-6

    # the chosen plan maximizes net gain over the table
    assert res.best.net_gain == pytest.approx(max(p.net_gain for p in res.table))
    # and never loses to simply doing nothing
    zero_rows = [p for p in res.table if p.n_transfers == 0]
    if zero_rows:
        assert res.best.net_gain >= zero_rows[0].net_gain - 1e-6


def test_transfer_zero_changes_when_squad_already_optimal():
    players = make_pool()
    horizon = make_horizon(players)
    best15 = optimize.pick(players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    res = optimize.transfer(players, horizon, best15,
                            budget=BUDGET, country_cap=COUNTRY_CAP,
                            free_transfers=2, max_extra=2)
    assert res is not None
    assert res.best.gross_gain == pytest.approx(0.0, abs=1e-6)
    assert res.best.n_transfers == 0
