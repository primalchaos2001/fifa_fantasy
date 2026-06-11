"""Shared data contract — the single set of dataclasses every module imports.

This module is intentionally dependency-free (stdlib only) and is FROZEN early in
the build: every other module (and every Fable-delegated leaf module) codes against
these types so the data model can never drift. Keep it dumb and debuggable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Canonical position codes used everywhere (matches the official players.json).
POSITIONS = ("GK", "DEF", "MID", "FWD")

# Stage codes used everywhere (matches the official rounds.json `stage`).
STAGES = ("GROUP", "R32", "R16", "QF", "SF", "F")


@dataclass
class Player:
    """Static + official-baseline facts about a player (from players.json).

    Prices are static for the whole tournament. `status`/`match_status` are the
    official availability ground truth that news may supplement but never contradict
    downward (see player_state.py).
    """

    id: int
    name: str
    country: str               # team name, e.g. "Algeria" (resolved via fixtures)
    squad_id: int              # official numeric team id (links to fixtures)
    position: str              # one of POSITIONS
    price: float               # $m, static all tournament
    ownership: float           # percentSelected (%) — drives the <5% scouting bonus
    total_points: int
    status: str                # 'playing' | 'transferred' (official)
    match_status: Optional[str] = None   # per-match availability flag (usually None)
    round_points: tuple[int, ...] = ()   # actual points per played round (for blending)


@dataclass
class Prior:
    """Manually-seeded per-90 priors for a fantasy-relevant player (priors.yaml).

    Auto-sourcing club per-90 stats is out of scope (handover SoI #2); these are
    hand-seeded for the top ~100 and blended toward tournament actuals as MDs accrue.
    """

    player_id: int
    goal_share: float = 0.0       # share of team xG this player converts (0..1)
    assist_share: float = 0.0     # share of team xG this player assists (0..1)
    penalty_taker: bool = False
    setpiece_taker: bool = False
    stat_baseline: float = 0.0    # expected pts/match from SoT + chances + tackles


@dataclass
class Event:
    """A structured availability fact extracted from a news item (extract.py)."""

    player: str
    team: str
    event_type: str    # injury_doubt|ruled_out|returned_to_training|suspension|
                       # confirmed_start|benched|penalty_taker_change|none
    confidence: str    # high|medium|low
    source_title: str
    source_url: str
    source_kind: str   # official|lineup|major_news|google_news|low (drives trust weight)
    timestamp: str     # ISO-8601


@dataclass
class PlayerState:
    """The mutable, news-updated layer persisted in state.json."""

    player_id: int
    p_start: float
    fit_mult: float
    status: str
    last_events: list[Event] = field(default_factory=list)


@dataclass
class Fixture:
    """A single match (from rounds.json `tournaments`)."""

    match_id: int
    stage: str         # one of STAGES
    round_id: int      # 1..8 (group matchday = round_id for GROUP rounds)
    home_squad_id: int
    away_squad_id: int
    home: str
    away: str
    kickoff: str       # ISO-8601
    status: str        # scheduled | live | played ...
    home_score: Optional[int] = None
    away_score: Optional[int] = None

    @property
    def is_played(self) -> bool:
        return self.home_score is not None and self.away_score is not None


@dataclass
class TeamStrength:
    """De-vigged match strength → Poisson goal expectations (sources.py)."""

    match_id: int
    team: str
    opponent: str
    lambda_for: float
    lambda_against: float
    p_win: float
    p_draw: float
    p_loss: float


@dataclass
class Squad:
    """The user's 15-man squad and current XI choices (persisted in state.json)."""

    player_ids: list[int] = field(default_factory=list)
    captain_id: Optional[int] = None
    bench_order: list[int] = field(default_factory=list)
    formation: Optional[str] = None
    budget_remaining: Optional[float] = None


class FetchError(Exception):
    """Raised by any fetcher on failure so staleness propagates to the report.

    Never swallow these into empty/stale data the optimizer would silently trust.
    """

    def __init__(self, source: str, message: str, last_good_ts: Optional[str] = None):
        super().__init__(f"[{source}] {message}")
        self.source = source
        self.message = message
        self.last_good_ts = last_good_ts
