"""Expected points per player per match, and horizon value across the tournament.

xpts(match) follows the handover §4.4 formula exactly. Horizon value (handover §4.5) is
the optimizer's currency: it sums a player's expected points over remaining concrete
group fixtures plus future knockout stages weighted by P(team alive at that stage).

Clean injection points (so this stays decoupled and Fable-friendly):
  - priors:   build_priors() uses priors.yaml when present, else a price-derived default.
  - states:   xpts consumes a dict[pid -> PlayerState]; player_state.py produces the real
              ones from news. default_states() is an MVP stand-in.
  - survival: horizon_values() takes p_alive[(squad_id, stage)] from advance.py; until that
              exists it falls back to PRIOR_SURVIVAL (a generic per-stage prior).
"""
from __future__ import annotations

import math
from typing import Optional

from . import constants as C
from . import scoring
from .models import Player, Prior, PlayerState, TeamStrength
from .sources import GameData, BASE_TOTAL_GOALS

# --- MVP default priors derived from price percentile within position ----------
POSITION_STAT_BASELINE = {"GK": 0.2, "DEF": 1.0, "MID": 1.4, "FWD": 1.2}
POSITION_GOAL_SHARE = {"GK": 0.0, "DEF": 0.05, "MID": 0.16, "FWD": 0.28}
POSITION_ASSIST_SHARE = {"GK": 0.0, "DEF": 0.07, "MID": 0.16, "FWD": 0.12}

# Generic survival prior until advance.py supplies real Monte-Carlo probabilities.
PRIOR_SURVIVAL = {"R32": 0.62, "R16": 0.36, "QF": 0.20, "SF": 0.10, "F": 0.05}

NEUTRAL_XG = BASE_TOTAL_GOALS / 2  # ~1.3 vs an average opponent


# ---------------------------------------------------------------------------
# Priors
# ---------------------------------------------------------------------------

def _price_scale(price: float, lo: float, hi: float) -> float:
    """Map a price within its position to a 0.5..1.5 multiplier."""
    if hi <= lo:
        return 1.0
    return 0.5 + (price - lo) / (hi - lo)


def default_prior(player: Player, lo: float, hi: float) -> Prior:
    scale = _price_scale(player.price, lo, hi)
    pos = player.position
    return Prior(
        player_id=player.id,
        goal_share=POSITION_GOAL_SHARE.get(pos, 0.0) * scale,
        assist_share=POSITION_ASSIST_SHARE.get(pos, 0.0) * scale,
        penalty_taker=False,
        setpiece_taker=False,
        stat_baseline=POSITION_STAT_BASELINE.get(pos, 0.5) * scale,
    )


def build_priors(players: list[Player],
                 priors_yaml: Optional[dict] = None) -> dict[int, Prior]:
    """Return a Prior for every player: from priors.yaml if listed, else price-derived.

    priors_yaml format (optional): {player_id: {goal_share, assist_share, penalty_taker,
    setpiece_taker, stat_baseline}}. Blending toward tournament actuals happens here once
    roundPoints accrue (weight n/(n+3)) — applied to stat_baseline as a light example.
    """
    # price ranges per position for the default scaling
    ranges: dict[str, tuple[float, float]] = {}
    for pos in C.SQUAD_QUOTA:
        prices = [p.price for p in players if p.position == pos] or [0.0]
        ranges[pos] = (min(prices), max(prices))

    priors_yaml = priors_yaml or {}
    out: dict[int, Prior] = {}
    for p in players:
        lo, hi = ranges.get(p.position, (0.0, 0.0))
        seed = priors_yaml.get(p.id)
        if seed:
            out[p.id] = Prior(
                player_id=p.id,
                goal_share=float(seed.get("goal_share", 0.0)),
                assist_share=float(seed.get("assist_share", 0.0)),
                penalty_taker=bool(seed.get("penalty_taker", False)),
                setpiece_taker=bool(seed.get("setpiece_taker", False)),
                stat_baseline=float(seed.get("stat_baseline",
                                             POSITION_STAT_BASELINE.get(p.position, 0.5))),
            )
        else:
            out[p.id] = default_prior(p, lo, hi)
    return out


def official_p_start(player: Player) -> float:
    """Baseline p_start from official fields only (highest-trust, free, no scraping).

    `status != playing` (e.g. transferred) -> 0. Otherwise the imminent-MD lineup signal
    in `matchStatus` (start/sub/not_in_squad) wins; absent that, a neutral prior.
    """
    if player.status != "playing":
        return 0.0
    if player.match_status in C.MATCHSTATUS_P_START:
        return C.MATCHSTATUS_P_START[player.match_status]
    return C.DEFAULT_P_START


def default_states(players: list[Player]) -> dict[int, PlayerState]:
    """MVP player states from official data (superseded/extended by player_state.py).

    News (Phase 3) layers on top of this official baseline; it never contradicts an
    official 'not_in_squad'/'transferred' downward-to-available.
    """
    states: dict[int, PlayerState] = {}
    for p in players:
        states[p.id] = PlayerState(
            player_id=p.id,
            p_start=official_p_start(p),
            fit_mult=1.0,
            status=p.status,
        )
    return states


# ---------------------------------------------------------------------------
# Expected points
# ---------------------------------------------------------------------------

def xpts_for_match(player: Player, prior: Prior, state: PlayerState,
                   strength: TeamStrength) -> float:
    """Expected fantasy points for one player in one match (handover §4.4 formula)."""
    pos = player.position
    team_xg = strength.lambda_for
    lam_against = strength.lambda_against

    goal_term = prior.goal_share * team_xg * scoring.goal_pts(pos)
    assist_term = prior.assist_share * team_xg * scoring.assist_pts()

    p_clean_sheet = math.exp(-lam_against)
    cs_term = p_clean_sheet * scoring.clean_sheet_pts(pos)
    conceded_term = scoring.expected_conceded_penalty(pos, lam_against)

    pen_goals = scoring.penalty_bump(prior.penalty_taker, team_xg)
    pen_term = pen_goals * scoring.goal_pts(pos)

    base = (scoring.appearance_pts()
            + goal_term + assist_term + cs_term + conceded_term
            + prior.stat_baseline + pen_term)

    xp = state.p_start * base * state.fit_mult
    xp += scoring.scouting_bonus_ev(player.ownership, xp)
    return xp


def neutral_xpts(player: Player, prior: Prior, state: PlayerState) -> float:
    """xpts vs an average opponent — used for future knockout stages with no fixture yet."""
    neutral = TeamStrength(-1, player.country, "AVG", NEUTRAL_XG, NEUTRAL_XG,
                           0.4, 0.27, 0.33)
    return xpts_for_match(player, prior, state, neutral)


def next_match_xpts(gd: GameData, players: list[Player], priors: dict[int, Prior],
                    states: dict[int, PlayerState],
                    xg_lookup: dict[tuple[int, int], TeamStrength]) -> dict[int, float]:
    """Each player's xpts for their team's *next* unplayed fixture (for XI/captain)."""
    # next fixture per team
    next_fix: dict[int, int] = {}
    for f in sorted(gd.fixtures, key=lambda x: x.kickoff):
        if f.is_played:
            continue
        for sid in (f.home_squad_id, f.away_squad_id):
            next_fix.setdefault(sid, f.match_id)
    out: dict[int, float] = {}
    for p in players:
        mid = next_fix.get(p.squad_id)
        strength = xg_lookup.get((p.squad_id, mid)) if mid is not None else None
        out[p.id] = xpts_for_match(p, priors[p.id], states[p.id], strength) if strength else 0.0
    return out


def _stage_offset(current_stage: str, target_stage: str) -> int:
    order = C.STAGE_ORDER
    try:
        return max(0, order.index(target_stage) - order.index(current_stage))
    except ValueError:
        return 0


def horizon_values(gd: GameData, players: list[Player], priors: dict[int, Prior],
                   states: dict[int, PlayerState],
                   xg_lookup: dict[tuple[int, int], TeamStrength],
                   p_alive: Optional[dict[tuple[int, str], float]] = None) -> dict[int, float]:
    """The optimizer's currency: expected points over the player's remaining horizon.

    = Σ remaining concrete group fixtures (xpts, discounted by stage)
    + Σ future knockout stages P(team alive at stage) × neutral xpts × 0.8^offset

    p_alive[(squad_id, stage)] comes from advance.py; falls back to PRIOR_SURVIVAL.
    """
    # remaining concrete fixtures per team
    team_remaining: dict[int, list] = {}
    for f in gd.fixtures:
        if f.is_played:
            continue
        for sid in (f.home_squad_id, f.away_squad_id):
            team_remaining.setdefault(sid, []).append(f)

    knockout_stages = ("R32", "R16", "QF", "SF", "F")
    out: dict[int, float] = {}
    for p in players:
        prior, state = priors[p.id], states[p.id]
        total = 0.0
        concrete_stages = set()
        for f in team_remaining.get(p.squad_id, []):
            strength = xg_lookup.get((p.squad_id, f.match_id))
            if not strength:
                continue
            xp = xpts_for_match(p, prior, state, strength)
            total += xp * (C.HORIZON_STAGE_DISCOUNT ** _stage_offset(gd.current_stage, f.stage))
            concrete_stages.add(f.stage)
        # future knockout stages with no concrete fixture yet
        nxp = neutral_xpts(p, prior, state)
        for stage in knockout_stages:
            if stage in concrete_stages:
                continue
            if _stage_offset(gd.current_stage, stage) == 0 and stage != gd.current_stage:
                continue
            if p_alive is not None:
                pa = p_alive.get((p.squad_id, stage), 0.0)
            else:
                pa = PRIOR_SURVIVAL.get(stage, 0.0)
            total += pa * nxp * (C.HORIZON_STAGE_DISCOUNT ** _stage_offset(gd.current_stage, stage))
        out[p.id] = total
    return out
