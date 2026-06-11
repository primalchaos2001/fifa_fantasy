"""Monte-Carlo tournament simulator -> P(team alive at each stage).

This produces the survival weights the optimizer's horizon_values() consumes:
p_alive[(squad_id, stage)] for stages R32..F. The group stage is simulated
fixture-by-fixture with Poisson goals from sources.team_strengths(); already-played
results are taken as fixed, never re-simulated.

KNOCKOUT APPROXIMATION: rounds.json carries ZERO knockout fixtures until the group
stage ends (the draw isn't published), so we cannot read the real bracket yet.
We approximate: collect the 32 qualifiers (top 2 per group + 8 best third-placed),
then play single-elimination rounds by RANDOMLY pairing the survivors each round,
each tie decided by Elo win probability. A random bracket slightly flattens the
probabilities for strong teams vs the real seeded bracket, which is fine for
horizon weighting. TODO: when rounds.json later contains knockout fixtures (or
constants.R32_BRACKET_FALLBACK is filled in), read the real bracket instead.

Stdlib only (random/math) — keep it dumb, debuggable, and lean in the inner loop.
"""
from __future__ import annotations

import math
import random
from typing import Optional

from . import constants as C
from .sources import GameData, ELO_SEED, _elo, team_strengths

KNOCKOUT_STAGES = ("R32", "R16", "QF", "SF", "F")


# ---------------------------------------------------------------------------
# Small samplers / helpers
# ---------------------------------------------------------------------------

def _poisson(exp_neg_lam: float, rng: random.Random) -> int:
    """Knuth's Poisson sampler. Takes exp(-lambda) precomputed (the per-fixture
    constant) because the inner loop calls this ~1.4M times at 10k sims."""
    k = 0
    p = rng.random()
    while p > exp_neg_lam:
        p *= rng.random()
        k += 1
    return k


def _elo_win_prob(elo_a: float, elo_b: float) -> float:
    """Standard Elo expected score for A beating B (draws folded in — a knockout
    tie always produces a winner via ET/pens, which Elo expectancy approximates)."""
    return 1.0 / (1.0 + 10.0 ** (-(elo_a - elo_b) / 400.0))


# ---------------------------------------------------------------------------
# Core simulation
# ---------------------------------------------------------------------------

def _run(gd: GameData, n_sims: int, elo_table: Optional[dict] = None,
         seed: Optional[int] = None) -> tuple[dict[tuple[int, str], int], dict[int, int]]:
    """Run n_sims tournaments. Returns (alive_counts[(sid, stage)], win_counts[sid])."""
    table = elo_table or ELO_SEED
    rng = random.Random(seed)

    # --- precompute everything fixture-shaped ONCE, outside the sim loop ---
    strengths = team_strengths(gd, elo_table=elo_table)  # unplayed fixtures only

    group_sids = [sid for sids in gd.groups.values() for sid in sids]
    base_stats: dict[int, list] = {sid: [0, 0, 0] for sid in group_sids}  # pts, GD, GF
    to_sim: list[tuple[int, int, float, float]] = []  # (home_sid, away_sid, e^-lam_h, e^-lam_a)

    for f in gd.fixtures:
        if f.stage != "GROUP":
            continue  # knockout fixtures (none yet) — see module docstring TODO
        if f.is_played:
            # Real results are fixed facts: fold them into the base table once.
            _apply_result(base_stats, f.home_squad_id, f.away_squad_id,
                          f.home_score, f.away_score)
        else:
            s = strengths.get(f.match_id)
            if s is None:
                continue  # defensive: shouldn't happen for unplayed fixtures
            to_sim.append((f.home_squad_id, f.away_squad_id,
                           math.exp(-s.lambda_for), math.exp(-s.lambda_against)))

    elo_by_sid = {sid: _elo(name, table) for sid, name in gd.teams.items()}
    group_items = list(gd.groups.items())

    alive_counts: dict[tuple[int, str], int] = {}
    win_counts: dict[int, int] = {}

    for _ in range(n_sims):
        # -- group stage: copy the fixed base, then sample the unplayed games --
        stats = {sid: row[:] for sid, row in base_stats.items()}
        for h, a, enl_h, enl_a in to_sim:
            _apply_result(stats, h, a, _poisson(enl_h, rng), _poisson(enl_a, rng))

        # Rank within each group by points, GD, GF, then RANDOM tiebreak.
        # (Real FIFA tiebreakers go head-to-head, fair play, etc. — random is an
        # acceptable approximation for horizon weighting.)
        qualifiers: list[int] = []
        thirds: list[int] = []
        for _label, sids in group_items:
            ranked = sorted(
                sids,
                key=lambda s: (stats[s][0], stats[s][1], stats[s][2], rng.random()),
                reverse=True,
            )
            qualifiers.extend(ranked[:C.GROUP_QUALIFY_DIRECT])
            if len(ranked) > C.GROUP_QUALIFY_DIRECT:
                thirds.append(ranked[C.GROUP_QUALIFY_DIRECT])

        # Best third-placed teams across all groups fill the remaining R32 slots.
        thirds.sort(key=lambda s: (stats[s][0], stats[s][1], stats[s][2], rng.random()),
                    reverse=True)
        qualifiers.extend(thirds[:C.BEST_THIRD_PLACED])

        # -- knockouts: random bracket, Elo-decided ties (see module docstring) --
        alive = qualifiers
        for stage in KNOCKOUT_STAGES:
            for sid in alive:  # reaching a later stage implies all earlier ones
                key = (sid, stage)
                alive_counts[key] = alive_counts.get(key, 0) + 1
            rng.shuffle(alive)
            winners = []
            for i in range(0, len(alive) - 1, 2):
                a, b = alive[i], alive[i + 1]
                p_a = _elo_win_prob(elo_by_sid.get(a, 1700.0), elo_by_sid.get(b, 1700.0))
                winners.append(a if rng.random() < p_a else b)
            alive = winners
        if alive:  # the lone survivor after the F round won the tournament
            win_counts[alive[0]] = win_counts.get(alive[0], 0) + 1

    return alive_counts, win_counts


def _apply_result(stats: dict[int, list], home: int, away: int,
                  home_goals: int, away_goals: int) -> None:
    """Update [points, goal_diff, goals_for] for both teams from one scoreline."""
    if home not in stats or away not in stats:
        return  # defensive: fixture references a team outside the derived groups
    hs, as_ = stats[home], stats[away]
    hs[1] += home_goals - away_goals
    hs[2] += home_goals
    as_[1] += away_goals - home_goals
    as_[2] += away_goals
    if home_goals > away_goals:
        hs[0] += 3
    elif home_goals < away_goals:
        as_[0] += 3
    else:
        hs[0] += 1
        as_[0] += 1


# ---------------------------------------------------------------------------
# Public API (called from xpts.horizon_values and main.cmd_simulate)
# ---------------------------------------------------------------------------

def simulate(gd: GameData, n_sims: int = 10000, elo_table: Optional[dict] = None,
             seed: Optional[int] = None) -> dict[tuple[int, str], float]:
    """P(team alive AT/REACHES stage), keyed by (squad_id, stage) for R32..F.

    GROUP is implicitly 1.0 for any team with remaining group games. Already-played
    group results are treated as fixed; only the remaining fixtures are sampled.
    Pass `seed` for reproducible sims (tests / debugging).
    """
    alive_counts, _ = _run(gd, n_sims, elo_table, seed=seed)
    return {key: cnt / n_sims for key, cnt in alive_counts.items()}


def advancement_table(gd: GameData, n_sims: int = 10000, elo_table: Optional[dict] = None,
                      seed: Optional[int] = None) -> list[dict]:
    """Per-team probability rows for the CLI, sorted by P(win the final) desc."""
    alive_counts, win_counts = _run(gd, n_sims, elo_table, seed=seed)
    group_of = {sid: label for label, sids in gd.groups.items() for sid in sids}

    rows = []
    for label, sids in sorted(gd.groups.items()):
        for sid in sids:
            rows.append({
                "squad_id": sid,
                "team": gd.team_of(sid),
                "group": group_of.get(sid, "?"),
                "p_r32": alive_counts.get((sid, "R32"), 0) / n_sims,
                "p_r16": alive_counts.get((sid, "R16"), 0) / n_sims,
                "p_qf": alive_counts.get((sid, "QF"), 0) / n_sims,
                "p_sf": alive_counts.get((sid, "SF"), 0) / n_sims,
                "p_final": alive_counts.get((sid, "F"), 0) / n_sims,
                "p_win": win_counts.get(sid, 0) / n_sims,
            })
    rows.sort(key=lambda r: r["p_win"], reverse=True)
    return rows


def render_table(table: list[dict], gd: GameData) -> str:
    """Markdown table of advancement probabilities, one row per team."""
    lines = [
        "## Advancement probabilities (Monte Carlo)",
        "",
        "_Knockout bracket approximated by random pairing (real draw not yet published)._",
        "",
        "| Team | Grp | R32 | R16 | QF | SF | Final | Win |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in table:
        lines.append(
            "| {team} | {group} | {p_r32:.1%} | {p_r16:.1%} | {p_qf:.1%} | "
            "{p_sf:.1%} | {p_final:.1%} | {p_win:.1%} |".format(**r)
        )
    return "\n".join(lines)
