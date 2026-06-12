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


def test_select_xi_rejects_loose_invalid_formations():
    """Verify that select_xi never returns an invalid formation (like 5-2-3) even if the xpts are highly skewed.

    If 5 DEFs are extremely valuable and 3 FWDs are moderately valuable, a loose range solver would
    choose 5-2-3. The strict solver must choose a valid formation like 5-3-2.
    """
    players = make_pool()
    horizon = make_horizon(players)
    squad_ids = optimize.pick(players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    by_id = {p.id: p for p in players}
    squad_players = [by_id[i] for i in squad_ids]

    # Ensure we have a squad with exactly 2 GK, 5 DEF, 5 MID, 3 FWD
    assert sum(1 for p in squad_players if p.position == "GK") == 2
    assert sum(1 for p in squad_players if p.position == "DEF") == 5
    assert sum(1 for p in squad_players if p.position == "MID") == 5
    assert sum(1 for p in squad_players if p.position == "FWD") == 3

    # Define next_xpts that would favor 5-2-3 if loose ranges were used
    next_xpts = {}
    gks = [p for p in squad_players if p.position == "GK"]
    for i, p in enumerate(gks):
        next_xpts[p.id] = 10.0 if i == 0 else 0.0
    for p in squad_players:
        if p.position == "DEF":
            next_xpts[p.id] = 100.0  # highly valuable
        elif p.position == "MID":
            next_xpts[p.id] = 1.0    # least valuable
        elif p.position == "FWD":
            next_xpts[p.id] = 10.0   # moderately valuable

    sel = optimize.select_xi(squad_players, next_xpts)
    assert sel is not None
    assert sel.formation != "5-2-3", "Invalid formation 5-2-3 was selected!"
    assert sel.formation in ("5-3-2", "5-4-1"), f"Expected a valid 5-at-the-back formation, got {sel.formation}"


def test_gamedata_free_transfers(gd):
    # Test round-based free transfers resolution
    orig_round = gd.current_round_id
    try:
        gd.current_round_id = 2
        assert gd.free_transfers() == 2
        gd.current_round_id = 4
        assert gd.free_transfers() == 99
        gd.current_round_id = 5
        assert gd.free_transfers() == 4
        gd.current_round_id = 7
        assert gd.free_transfers() == 5
        gd.current_round_id = 8
        assert gd.free_transfers() == 6
    finally:
        gd.current_round_id = orig_round


def test_select_xi_forced_formation():
    players = make_pool()
    horizon = make_horizon(players)
    squad_ids = optimize.pick(players, horizon, budget=BUDGET, country_cap=COUNTRY_CAP)
    by_id = {p.id: p for p in players}
    squad_players = [by_id[i] for i in squad_ids]
    next_xpts = {p.id: 1.0 + p.id * 0.01 for p in squad_players}

    # Force 3-5-2
    sel = optimize.select_xi(squad_players, next_xpts, formation="3-5-2")
    assert sel is not None
    assert sel.formation == "3-5-2"
    counts = {pos: sum(1 for i in sel.xi_ids if by_id[i].position == pos) for pos in ("GK", "DEF", "MID", "FWD")}
    assert counts == {"GK": 1, "DEF": 3, "MID": 5, "FWD": 2}

    # Force 5-4-1
    sel = optimize.select_xi(squad_players, next_xpts, formation="5-4-1")
    assert sel is not None
    assert sel.formation == "5-4-1"
    counts = {pos: sum(1 for i in sel.xi_ids if by_id[i].position == pos) for pos in ("GK", "DEF", "MID", "FWD")}
    assert counts == {"GK": 1, "DEF": 5, "MID": 4, "FWD": 1}

    # Force invalid formation in select_xi raises ValueError
    with pytest.raises(ValueError):
        optimize.select_xi(squad_players, next_xpts, formation="5-2-3")


def test_best_replacements_and_drops_for_target():
    players = make_pool()
    horizon = make_horizon(players)
    squad_ids = current_squad_ids(players) # weak 15
    by_id = {p.id: p for p in players}
    squad_players = [by_id[i] for i in squad_ids]
    
    # 1. Test best_replacements
    out_id = squad_ids[0]
    out_p = by_id[out_id]
    reps = optimize.best_replacements(
        players, horizon, squad_ids, out_id, budget=BUDGET, country_cap=COUNTRY_CAP, top_n=5
    )
    assert len(reps) > 0
    assert len(reps) <= 5
    for c in reps:
        assert c.out_id == out_id
        in_p = by_id[c.in_id]
        assert in_p.position == out_p.position
        assert in_p.status == "playing"
        assert c.price_delta <= BUDGET - sum(p.price for p in squad_players)
        
        # Verify country count check
        temp_squad = [p for p in squad_players if p.id != out_id] + [in_p]
        temp_countries = {}
        for p in temp_squad:
            temp_countries[p.country] = temp_countries.get(p.country, 0) + 1
        assert max(temp_countries.values()) <= COUNTRY_CAP
        
    # Verify reps are sorted by horizon value descending (using hv_in)
    for r1, r2 in zip(reps, reps[1:]):
        assert r2.hv_in <= r1.hv_in

    # 2. Test drops_for_target
    target_in_id = next(p.id for p in players if p.id not in squad_ids and p.position == "DEF" and p.status == "playing")
    target_in_p = by_id[target_in_id]
    
    drops = optimize.drops_for_target(
        players, horizon, squad_ids, target_in_id, budget=BUDGET, country_cap=COUNTRY_CAP, top_n=5
    )
    assert len(drops) > 0
    for c in drops:
        assert c.in_id == target_in_id
        out_p = by_id[c.out_id]
        assert out_p.position == target_in_p.position
        assert out_p.id in squad_ids
        
        # Verify budget and country cap
        temp_squad = [p for p in squad_players if p.id != out_p.id] + [target_in_p]
        assert sum(p.price for p in temp_squad) <= BUDGET + 1e-6
        temp_countries = {}
        for p in temp_squad:
            temp_countries[p.country] = temp_countries.get(p.country, 0) + 1
        assert max(temp_countries.values()) <= COUNTRY_CAP

    # Verify drops are sorted by hv_gain descending
    for d1, d2 in zip(drops, drops[1:]):
        assert d2.hv_gain <= d1.hv_gain
