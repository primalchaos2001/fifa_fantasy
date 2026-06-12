"""Game rules encoded as DATA, not magic numbers scattered around the code.

Values sourced from public 2026 guides (Yahoo Sports / FantasyFootballScout, June 2026)
and the handover doc. Anything the public sources did not state exactly is marked
`# VERIFY` — re-check against https://play.fifa.com/fantasy/help. The tool also
self-checks computed points against official `roundPoints` once matches are played
(see scoring.verify_against_actuals), which flags drift automatically.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Scoring point table (per the 2026 game). Keyed by position where it differs.
# ---------------------------------------------------------------------------

# Appearance: 1 pt for <60 min, 2 pts for 60+ min. (Standard WC-fantasy; VERIFY 60+.)
APPEARANCE_UNDER_60 = 1
APPEARANCE_60_PLUS = 2  # VERIFY: one guide listed +1; FPL/WC-2022 convention is +2

# Goal points by position.  GK 9 / DEF 7 / MID 6 / FWD 5 (2026 inflated values).
GOAL_PTS = {"GK": 9, "DEF": 7, "MID": 6, "FWD": 5}

ASSIST_PTS = 3

# Clean sheet (requires 60+ min). GK/DEF 5, MID 1, FWD 0.
CLEAN_SHEET_PTS = {"GK": 5, "DEF": 5, "MID": 1, "FWD": 0}

# Goals conceded: first conceded = 0, then -1 per additional goal (GK & DEF only).
CONCEDED_PENALTY_POSITIONS = ("GK", "DEF")
CONCEDED_FREE_GOALS = 1          # first goal conceded is free
CONCEDED_PTS_PER_EXTRA = -1

# Goalkeeper specifics.
SAVE_PTS_PER = 1
SAVES_PER_POINT = 3              # +1 every 3 saves
PENALTY_SAVE_PTS = 3

# 2026 stat points (apply to all outfield players; priors carry per-position rates).
SOT_PER_POINT = 2               # +1 every 2 shots on target
CHANCES_PER_POINT = 2           # +1 every 2 chances created
TACKLES_PER_POINT = 3           # +1 every 3 tackles

# Cards / misc.
YELLOW_CARD_PTS = -1
RED_CARD_PTS = -2
OWN_GOAL_PTS = -2
PENALTY_MISS_PTS = -2           # VERIFY: not stated in public guides
FREEKICK_GOAL_BONUS = 1         # +1 bonus for a direct free-kick goal

# Scouting bonus: +2 when an <5%-owned player returns 4+ points in a match.
SCOUTING_BONUS_PTS = 2
SCOUTING_OWNERSHIP_MAX = 5.0    # percent
SCOUTING_POINTS_THRESHOLD = 4

# Penalty conversion assumption for the pen_bump term in xpts.
PENALTY_CONVERSION = 0.78       # league-average pen conversion (VERIFY/tunable)

# ---------------------------------------------------------------------------
# Squad / budget / transfer rules.
# ---------------------------------------------------------------------------

SQUAD_QUOTA = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}   # exactly 15
SQUAD_SIZE = 15
XI_SIZE = 11

# Budget rises automatically once R32 transfers open (handover §2).
BUDGET_BY_STAGE = {
    "GROUP": 100.0,
    "R32": 105.0,
    "R16": 105.0,
    "QF": 105.0,
    "SF": 105.0,
    "F": 105.0,
}

# Max players per country, per stage. 3 through group+R32, rising as teams are
# eliminated. VERIFY the rising values against the help page.
COUNTRY_CAP_BY_STAGE = {
    "GROUP": 3,
    "R32": 3,
    "R16": 4,   # VERIFY
    "QF": 5,    # VERIFY
    "SF": 6,    # VERIFY
    "F": 6,     # VERIFY
}

# Official players.json `matchStatus` -> baseline p_start. This is a confirmed-lineup
# signal that the game populates ~1h before kickoff for the imminent matchday — the
# highest-trust availability source, free, no scraping. News only supplements it.
#   start = named in the XI; sub = on the bench; not_in_squad = omitted.
MATCHSTATUS_P_START = {"start": 1.0, "sub": 0.15, "not_in_squad": 0.0}
DEFAULT_P_START = 0.7          # status 'playing' but no lineup confirmed yet

FREE_TRANSFERS_DEFAULT = 2      # per matchday (group). Knockout rounds may differ. VERIFY
TRANSFER_HIT_PTS = -3           # per extra transfer beyond free
CAPTAIN_MULTIPLIER = 2

# ---------------------------------------------------------------------------
# Formations: a valid XI must satisfy these per-position ranges (GK always 1).
# This range check is more robust than enumerating; ALLOWED_FORMATIONS is the
# canonical list for display/labelling.
# ---------------------------------------------------------------------------

FORMATION_RANGES = {"GK": (1, 1), "DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}

ALLOWED_FORMATIONS = (
    "3-4-3", "3-5-2", "4-3-3", "4-4-2", "4-5-1",
    "5-3-2", "5-4-1", "5-2-3", "3-3-4",  # VERIFY exact allowed set
)

DEFAULT_FORMATION = "4-4-2"

# ---------------------------------------------------------------------------
# Tournament structure.
# ---------------------------------------------------------------------------

# Round id -> stage (matches rounds.json). Group matchday = round id for GROUP.
ROUND_STAGE = {1: "GROUP", 2: "GROUP", 3: "GROUP", 4: "R32", 5: "R16", 6: "QF", 7: "SF", 8: "F"}

# Stage ordering for horizon discounting / "alive at stage" monotonicity.
STAGE_ORDER = ("GROUP", "R32", "R16", "QF", "SF", "F")

# Group-stage advancement: top 2 per group + 8 best third-placed teams -> R32.
GROUP_QUALIFY_DIRECT = 2        # top 2 per group advance
BEST_THIRD_PLACED = 8          # plus 8 best third-placed across 12 groups
NUM_GROUPS = 12

# Horizon discount applied per stage beyond the next one (news uncertainty).
HORIZON_STAGE_DISCOUNT = 0.8

# Fixed seed for the Monte-Carlo sim so horizon values are reproducible run-to-run
# (10k sims is already accurate; the run-to-run jitter was just sampling noise that made
# data.json churn on every run). The CLI `--reseed` flag overrides this with fresh draws.
SIM_SEED = 2026

# Fallback R32 bracket mapping. The live rounds.json fills knockout fixtures in as
# teams qualify, which advance.py prefers; this is only used before that happens.
# Encode the official 2026 bracket here when verified.  # VERIFY / TODO
R32_BRACKET_FALLBACK: dict = {}
