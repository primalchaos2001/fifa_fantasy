"""Offline GameData integrity: team map, derived groups, fixture stages, xG lookup."""
from __future__ import annotations

import pytest

from wc_fantasy import sources
from wc_fantasy.models import STAGES


def test_every_player_squad_id_is_a_known_team(gd):
    assert gd.players, "offline load returned no players"
    missing = {p.squad_id for p in gd.players if p.squad_id not in gd.teams}
    assert not missing, f"players reference unknown squad ids: {sorted(missing)}"


def test_derived_groups_are_groups_of_four(gd):
    groups = sources._derive_groups(gd.fixtures)
    assert groups == gd.groups  # load_game_data used the same derivation
    assert all(len(sids) == 4 for sids in groups.values()), \
        {label: len(sids) for label, sids in groups.items()}
    total = sum(len(sids) for sids in groups.values())
    assert total == len(gd.teams)
    # no team in two groups
    flat = [sid for sids in groups.values() for sid in sids]
    assert len(flat) == len(set(flat))


def test_fixture_stages_are_valid(gd):
    assert gd.fixtures, "offline load returned no fixtures"
    bad = {f.stage for f in gd.fixtures} - set(STAGES)
    assert not bad, f"unknown stages in fixtures: {bad}"
    assert gd.current_stage in STAGES


def test_fixture_teams_resolve(gd):
    for f in gd.fixtures:
        assert f.home_squad_id in gd.teams
        assert f.away_squad_id in gd.teams
        assert f.home_squad_id != f.away_squad_id


def test_team_xg_lookup_symmetric(gd):
    lookup = sources.team_xg_lookup(gd)
    unplayed = [f for f in gd.fixtures if not f.is_played]
    assert unplayed, "no unplayed fixtures in the offline data"
    for f in unplayed:
        home_key = (f.home_squad_id, f.match_id)
        away_key = (f.away_squad_id, f.match_id)
        assert home_key in lookup, f"missing home entry for match {f.match_id}"
        assert away_key in lookup, f"missing away entry for match {f.match_id}"
        h, a = lookup[home_key], lookup[away_key]
        # mirrored perspectives of the same match
        assert h.lambda_for == pytest.approx(a.lambda_against)
        assert h.lambda_against == pytest.approx(a.lambda_for)
        assert h.p_win == pytest.approx(a.p_loss)
        assert h.p_loss == pytest.approx(a.p_win)
        assert h.p_draw == pytest.approx(a.p_draw)
        assert h.lambda_for >= sources.MIN_LAMBDA
        assert h.lambda_against >= sources.MIN_LAMBDA


def test_team_xg_lookup_skips_played_fixtures(gd):
    lookup = sources.team_xg_lookup(gd)
    for f in gd.fixtures:
        if f.is_played:
            assert (f.home_squad_id, f.match_id) not in lookup
            assert (f.away_squad_id, f.match_id) not in lookup


def test_load_game_data_offline_never_hits_network(monkeypatch):
    def boom(*args, **kwargs):  # pragma: no cover - should never fire
        raise AssertionError("network access attempted in offline mode")
    monkeypatch.setattr(sources.urllib.request, "urlopen", boom)
    gd = sources.load_game_data(offline=True)
    assert gd.players
    assert gd.stale, "offline load should flag cached data as stale"
