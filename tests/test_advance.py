"""Monte-Carlo advancement probabilities: bounded, stage-monotone, and sensible."""
from __future__ import annotations

import pytest

from wc_fantasy import advance

N_SIMS = 500
STAGE_CHAIN = ("R32", "R16", "QF", "SF", "F")


@pytest.fixture(scope="module")
def probs(gd):
    return advance.simulate(gd, n_sims=N_SIMS)


def test_probabilities_in_unit_interval(probs):
    assert probs, "simulation produced no probabilities"
    for key, p in probs.items():
        assert 0.0 <= p <= 1.0, f"{key} -> {p}"


def test_stage_keys_are_knockout_stages(probs):
    stages = {stage for (_sid, stage) in probs}
    assert stages <= set(STAGE_CHAIN)


def test_survival_monotone_per_team(gd, probs):
    for sid in gd.teams:
        chain = [probs.get((sid, stage), 0.0) for stage in STAGE_CHAIN]
        for earlier, later in zip(chain, chain[1:]):
            assert earlier >= later, (
                f"{gd.team_of(sid)} ({sid}): non-monotone survival {chain}")


def test_r32_probabilities_sum_to_32_teams(probs):
    # exactly 32 teams reach the R32 in every sim -> expected mass is exactly 32
    total = sum(p for (sid, stage), p in probs.items() if stage == "R32")
    assert total == pytest.approx(32.0, abs=1e-9)


def test_strong_team_outlasts_weak_team(gd, probs):
    """Argentina (top Elo seed) should reach the QF more often than the weakest seed."""
    name_to_sid = {name: sid for sid, name in gd.teams.items()}
    strong = name_to_sid.get("Argentina")
    weak = name_to_sid.get("Qatar") or name_to_sid.get("Cabo Verde")
    if strong is None or weak is None:
        pytest.skip("expected teams not present in offline data")
    assert probs.get((strong, "QF"), 0.0) > probs.get((weak, "QF"), 0.0)
