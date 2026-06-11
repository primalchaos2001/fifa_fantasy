"""Apply extracted events to player states with trust, recency, conservatism, and an audit log.

The official baseline (status / matchStatus -> p_start, see xpts.official_p_start) is the
ground truth that news SUPPLEMENTS but never contradicts downward-to-available. Every mutation
is appended to data/events.log with the triggering headline — the debugging lifeline.

Recency decay is implicit: extract.py drops items >48h old, so an un-reinforced injury_doubt
naturally fades back to baseline on the next run. Sticky negatives (ruled_out / suspension)
persist across runs via prior_states until an explicit positive event reverses them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .models import Event, Player, PlayerState
from .xpts import official_p_start
from .sources import DATA_DIR

EVENTS_LOG = DATA_DIR / "events.log"

CONFIDENCE_FACTOR = {"high": 1.0, "medium": 0.7, "low": 0.4}
STICKY = ("ruled_out", "suspension")


def _trust(event: Event, config: dict) -> float:
    weights = config.get("trust_weights", {})
    base = weights.get(event.source_kind, weights.get("google_news", 0.5))
    if event.source_kind == "llm":
        base = weights.get("major_news", 0.7)
    return base * CONFIDENCE_FACTOR.get(event.confidence, 0.4)


def _apply_event(state: PlayerState, event: Event, w: float, floor: float) -> None:
    """Mutate one player's state for one event, weighted by trust w."""
    et = event.event_type
    if et == "ruled_out":
        state.p_start = min(state.p_start, max(0.0, 1.0 - w))
        if w >= 0.6:
            state.p_start, state.status = 0.0, "ruled_out"
    elif et == "suspension":
        if w >= 0.6:
            state.p_start, state.status = 0.0, "suspended"
        else:
            state.p_start = min(state.p_start, 0.4)
    elif et == "injury_doubt":
        reduced = state.p_start * (1.0 - 0.55 * w)
        # conservatism: one low-trust doubt can't sink a nailed starter on its own
        if w < 0.6 and state.p_start >= 0.7:
            reduced = max(reduced, floor)
        state.p_start = reduced
    elif et in ("returned_to_training", "confirmed_start"):
        if state.status not in STICKY:  # a positive event can clear a non-sticky status
            state.p_start = max(state.p_start, w if et == "confirmed_start" else 0.7 * w + state.p_start * (1 - w))
            state.status = "playing"
        elif w >= 0.7:  # strong positive reverses a sticky negative
            state.p_start, state.status = max(0.6, w), "playing"
    elif et == "benched":
        state.p_start = min(state.p_start, 0.15 + 0.1 * (1 - w))
    # penalty_taker_change affects priors, not p_start — recorded in last_events only
    state.last_events.append(event)


def compute_states(players: list[Player], events: list[Event], config: dict,
                   prior_states: Optional[dict[int, PlayerState]] = None) -> dict[int, PlayerState]:
    prior_states = prior_states or {}
    floor = config.get("state", {}).get("conservatism_floor", 0.5)

    # seed from official baseline, carrying sticky negatives from the previous run
    states: dict[int, PlayerState] = {}
    for p in players:
        base = official_p_start(p)
        prior = prior_states.get(p.id)
        if prior and prior.status in STICKY:
            states[p.id] = PlayerState(p.id, 0.0, 1.0, prior.status, [])
        else:
            states[p.id] = PlayerState(p.id, base, 1.0, p.status, [])

    # index players by name to route events to ids; by id for the official-override lookup
    by_name = {p.name: p.id for p in players}
    by_id = {p.id: p for p in players}
    log_lines: list[str] = []
    for ev in events:
        pid = by_name.get(ev.player)
        if pid is None or pid not in states:
            continue
        st = states[pid]
        before = st.p_start
        _apply_event(st, ev, _trust(ev, config), floor)
        # official availability overrides news downward (suspended/transferred/not_in_squad)
        p = by_id[pid]
        if official_p_start(p) == 0.0:
            st.p_start, st.status = 0.0, p.status if p.status != "playing" else "unavailable"
        if abs(st.p_start - before) > 1e-6 or ev.event_type in STICKY:
            log_lines.append(
                f"{datetime.now(timezone.utc).isoformat()}\t{ev.player}\t"
                f"p_start {before:.2f}->{st.p_start:.2f}\t{ev.event_type}\t"
                f"{ev.source_title}\t{ev.source_url}")

    if log_lines:
        EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTS_LOG, "a", encoding="utf-8") as fh:
            fh.write("\n".join(log_lines) + "\n")
    return states


# --- persistence helpers (state.json holds squad + serialized player states) ---

def serialize_states(states: dict[int, PlayerState]) -> dict:
    return {str(pid): {"p_start": round(s.p_start, 3), "fit_mult": s.fit_mult,
                       "status": s.status} for pid, s in states.items()}


def deserialize_states(blob: dict) -> dict[int, PlayerState]:
    out: dict[int, PlayerState] = {}
    for k, v in (blob or {}).items():
        pid = int(k)
        out[pid] = PlayerState(pid, v.get("p_start", 0.7), v.get("fit_mult", 1.0),
                               v.get("status", "playing"), [])
    return out
