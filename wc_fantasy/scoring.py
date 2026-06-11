"""Scoring helpers — thin, typed accessors over the constants point table.

Keeping the numbers in constants.py and the *logic* here means xpts.py reads like the
handover formula and never hardcodes magic numbers. The scouting bonus is modelled as an
expected-value term (presence of the term matters more than precision — handover §4.4).
"""
from __future__ import annotations

import math

from . import constants as C


def appearance_pts(p_play_60: float = 1.0) -> float:
    """Expected appearance points for a starter (assume mostly 60+ minutes)."""
    return p_play_60 * C.APPEARANCE_60_PLUS + (1 - p_play_60) * C.APPEARANCE_UNDER_60


def goal_pts(position: str) -> int:
    return C.GOAL_PTS[position]


def assist_pts() -> int:
    return C.ASSIST_PTS


def clean_sheet_pts(position: str) -> int:
    return C.CLEAN_SHEET_PTS[position]


def keeps_clean_sheet_value(position: str) -> bool:
    return C.CLEAN_SHEET_PTS.get(position, 0) > 0


def expected_conceded_penalty(position: str, lambda_against: float) -> float:
    """Expected goals-conceded penalty (GK/DEF): -1 per goal beyond the first."""
    if position not in C.CONCEDED_PENALTY_POSITIONS:
        return 0.0
    # E[max(0, conceded - 1)] for conceded ~ Poisson(lambda_against)
    p0 = math.exp(-lambda_against)
    expected_over_zero = lambda_against - (1 - p0)  # E[goals] - P(>=1)
    return C.CONCEDED_PTS_PER_EXTRA * max(0.0, expected_over_zero)


def penalty_bump(is_taker: bool, team_xg: float) -> float:
    """Extra expected points from penalty duty: rough p(pen awarded) * convert * goal value."""
    if not is_taker:
        return 0.0
    p_pen_awarded = min(0.35, 0.12 * team_xg)  # crude: stronger attacks win more pens
    return p_pen_awarded * C.PENALTY_CONVERSION  # value folded into goal term by caller


def scouting_bonus_ev(ownership: float, expected_match_pts: float) -> float:
    """Expected scouting-bonus points for a low-owned player.

    Awarded when an <5%-owned player returns >=4 points. We approximate P(return>=4)
    with a logistic on expected points (precision unimportant; presence is the point).
    """
    if ownership >= C.SCOUTING_OWNERSHIP_MAX:
        return 0.0
    p_return_4 = 1.0 / (1.0 + math.exp(-(expected_match_pts - C.SCOUTING_POINTS_THRESHOLD)))
    return p_return_4 * C.SCOUTING_BONUS_PTS


def verify_against_actuals(players, computed_per_match: dict[int, float]) -> list[str]:
    """Self-check: flag large gaps between our computed xPts and official roundPoints.

    Only meaningful once matches are played (roundPoints populated). Returns warning
    strings for the report; never raises. Catches gross scoring-table errors.
    """
    warnings: list[str] = []
    for p in players:
        if not p.round_points:
            continue
        actual_avg = sum(p.round_points) / len(p.round_points)
        expected = computed_per_match.get(p.id)
        if expected is None:
            continue
        if actual_avg >= 3 and expected < actual_avg * 0.3:
            warnings.append(
                f"scoring drift: {p.name} avg actual {actual_avg:.1f} vs computed {expected:.1f}")
    return warnings
