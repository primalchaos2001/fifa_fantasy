"""Headline -> Event extraction against the offline headlines fixture and player list."""
from __future__ import annotations

import json

import pytest

from wc_fantasy import extract, sources

HEADLINES_PATH = sources.FIXTURE_DIR / "sample_headlines.json"


@pytest.fixture(scope="module")
def headlines():
    with open(HEADLINES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def events(headlines, gd, config):
    # config.yaml has extract.llm_extract: false -> deterministic keyword path, offline.
    # Disable the 48h age-drop here: it uses datetime.now(), and the fixture's published
    # dates are fixed, so these *classification* tests would otherwise rot as real days pass.
    cfg = {**config, "news": {**config.get("news", {}), "drop_older_than_hours": 24 * 365 * 100}}
    return extract.extract_events(headlines, gd.players, cfg)


def events_for(events, name_fragment: str):
    frag = extract._norm(name_fragment)
    return [e for e in events if frag in extract._norm(e.player)]


def test_some_events_extracted(events):
    assert events, "keyword extraction produced no events at all"


def test_mbappe_ruled_out(events):
    evs = events_for(events, "Mbapp")
    assert evs, "no event matched Mbappé"
    types = {e.event_type for e in evs}
    assert "ruled_out" in types
    ruled = next(e for e in evs if e.event_type == "ruled_out")
    assert ruled.confidence == "high"   # major_news source keeps high confidence


def test_pedri_suspension(events):
    evs = events_for(events, "Pedri")
    assert evs, "no event matched Pedri (knownName match)"
    assert any(e.event_type == "suspension" for e in evs)


def test_harry_kane_benched_not_confirmed_start(events):
    evs = events_for(events, "Harry Kane")
    assert evs, "no event matched Harry Kane"
    types = {e.event_type for e in evs}
    assert "benched" in types
    # the headline also contains 'starts on the bench' / 'XI' — benched must win
    assert "confirmed_start" not in types


def test_no_substring_false_positives(events):
    """'Bellingham' must not trigger Joe Bell; 'Morocco' must not trigger Nikola Moro."""
    for innocent in ("Joe Bell", "Nikola Moro"):
        hits = [e for e in events if extract._norm(e.player) == extract._norm(innocent)]
        assert not hits, f"false positive: {innocent} got events {hits}"
    # sanity: Bellingham himself IS matched by his doubt headline
    bell = events_for(events, "Bellingham")
    assert any(e.event_type == "injury_doubt" for e in bell)


def test_noise_headlines_produce_no_events(events):
    noise_fragments = (
        "Five things we learned",
        "Transfer rumour",
        "most open World Cup yet",
        "Predicted lineups: rotation expected",
        "full knockout bracket explained",
    )
    for frag in noise_fragments:
        offenders = [e for e in events if frag.lower() in e.source_title.lower()]
        assert not offenders, f"noise headline produced events: {offenders}"


def test_dedupe_drops_repeated_titles(headlines, gd, config):
    doubled = headlines + [dict(headlines[0])]  # exact repeat of the Mbappé item
    once = extract.extract_events(headlines, gd.players, config)
    twice = extract.extract_events(doubled, gd.players, config)
    assert len(twice) == len(once)


def test_classify_low_trust_source_caps_confidence():
    et, conf = extract._classify("Player X ruled out of the tournament", "google_news")
    assert et == "ruled_out"
    assert conf == "medium"  # high capped to medium for low-trust sources
    et2, conf2 = extract._classify("Player X ruled out of the tournament", "major_news")
    assert (et2, conf2) == ("ruled_out", "high")
