"""Trust-weighted event application: official baseline beats news, conservatism floor holds."""
from __future__ import annotations

import pytest

from wc_fantasy import player_state
from wc_fantasy.models import Event, Player


def make_player(pid: int, name: str, *, status: str = "playing",
                match_status=None) -> Player:
    return Player(id=pid, name=name, country="Testland", squad_id=1, position="MID",
                  price=7.0, ownership=20.0, total_points=0, status=status,
                  match_status=match_status)


def make_event(name: str, event_type: str, *, confidence: str = "high",
               source_kind: str = "official") -> Event:
    return Event(player=name, team="Testland", event_type=event_type,
                 confidence=confidence, source_title=f"{name} {event_type}",
                 source_url="https://example.com", source_kind=source_kind,
                 timestamp="2026-06-11T00:00:00+00:00")


def test_high_trust_ruled_out_zeroes_p_start(config):
    p = make_player(1, "Alpha One")
    ev = make_event("Alpha One", "ruled_out", confidence="high", source_kind="official")
    states = player_state.compute_states([p], [ev], config, {})
    st = states[1]
    assert st.p_start == 0.0
    assert st.status == "ruled_out"


def test_high_trust_suspension_zeroes_p_start(config):
    p = make_player(2, "Beta Two")
    ev = make_event("Beta Two", "suspension", confidence="high", source_kind="major_news")
    # trust = 0.7 * 1.0 = 0.7 >= 0.6 -> hard zero
    states = player_state.compute_states([p], [ev], config, {})
    assert states[2].p_start == 0.0
    assert states[2].status == "suspended"


def test_single_low_trust_doubt_respects_conservatism_floor(config):
    floor = config["state"]["conservatism_floor"]
    assert floor == 0.5  # the documented default this test depends on
    p = make_player(3, "Gamma Three")  # no matchStatus -> baseline p_start 0.7
    ev = make_event("Gamma Three", "injury_doubt", confidence="medium",
                    source_kind="google_news")  # trust 0.5 * 0.7 = 0.35 < 0.6
    states = player_state.compute_states([p], [ev], config, {})
    st = states[3]
    assert st.p_start >= floor
    assert st.p_start < 0.7  # the doubt still moved it down, just not below the floor


def test_low_trust_doubt_on_confirmed_starter_stays_at_floor_or_above(config):
    p = make_player(4, "Delta Four", match_status="start")  # baseline 1.0 (nailed)
    ev = make_event("Delta Four", "injury_doubt", confidence="low",
                    source_kind="low")  # trust 0.3 * 0.4 = 0.12
    states = player_state.compute_states([p], [ev], config, {})
    assert states[4].p_start >= config["state"]["conservatism_floor"]


def test_official_not_in_squad_overrides_positive_news(config):
    p = make_player(5, "Epsilon Five", match_status="not_in_squad")
    ev = make_event("Epsilon Five", "confirmed_start", confidence="high",
                    source_kind="official")
    states = player_state.compute_states([p], [ev], config, {})
    st = states[5]
    assert st.p_start == 0.0
    assert st.status == "unavailable"  # status was 'playing', flagged unavailable


def test_official_transferred_overrides_positive_news(config):
    p = make_player(6, "Zeta Six", status="transferred")
    ev = make_event("Zeta Six", "returned_to_training", confidence="high",
                    source_kind="official")
    states = player_state.compute_states([p], [ev], config, {})
    st = states[6]
    assert st.p_start == 0.0
    assert st.status == "transferred"


def test_no_events_keeps_official_baseline(config):
    players = [
        make_player(7, "Eta Seven"),                            # default 0.7
        make_player(8, "Theta Eight", match_status="start"),    # 1.0
        make_player(9, "Iota Nine", match_status="sub"),        # 0.15
        make_player(10, "Kappa Ten", match_status="not_in_squad"),  # 0.0
    ]
    states = player_state.compute_states(players, [], config, {})
    assert states[7].p_start == pytest.approx(0.7)
    assert states[8].p_start == pytest.approx(1.0)
    assert states[9].p_start == pytest.approx(0.15)
    assert states[10].p_start == pytest.approx(0.0)


def test_sticky_ruled_out_persists_from_prior_states(config):
    p = make_player(11, "Lambda Eleven")
    prior = player_state.PlayerState(11, 0.0, 1.0, "ruled_out", [])
    states = player_state.compute_states([p], [], config, {11: prior})
    assert states[11].p_start == 0.0
    assert states[11].status == "ruled_out"
