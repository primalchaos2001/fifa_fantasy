"""Math checks on the Poisson 1X2 model, clean-sheet probability, and xpts formula."""
from __future__ import annotations

import math

import pytest

from wc_fantasy import constants as C
from wc_fantasy import scoring, sources, xpts
from wc_fantasy.models import Player, Prior, PlayerState, TeamStrength


def make_player(pos: str = "MID", ownership: float = 50.0) -> Player:
    return Player(id=1, name="Test Player", country="Testland", squad_id=1,
                  position=pos, price=8.0, ownership=ownership, total_points=0,
                  status="playing")


def zero_prior() -> Prior:
    return Prior(player_id=1, goal_share=0.0, assist_share=0.0,
                 penalty_taker=False, setpiece_taker=False, stat_baseline=0.0)


def state(p_start: float = 1.0) -> PlayerState:
    return PlayerState(player_id=1, p_start=p_start, fit_mult=1.0, status="playing")


def strength(lam_for: float = 1.5, lam_against: float = 1.0) -> TeamStrength:
    return TeamStrength(match_id=1, team="Testland", opponent="Other",
                        lambda_for=lam_for, lambda_against=lam_against,
                        p_win=0.4, p_draw=0.27, p_loss=0.33)


# ---------------------------------------------------------------------------
# _poisson_1x2
# ---------------------------------------------------------------------------

def test_poisson_1x2_favourite_has_higher_win_prob():
    pw, pd, pl = sources._poisson_1x2(1.8, 0.9)
    assert pw > pl
    # the scoreline grid is truncated at 8 goals, so the mass is just under 1
    assert 0.999 < pw + pd + pl <= 1.0 + 1e-9
    assert all(0.0 <= p <= 1.0 for p in (pw, pd, pl))


def test_poisson_1x2_symmetric_when_lambdas_equal():
    pw, pd, pl = sources._poisson_1x2(1.3, 1.3)
    assert pw == pytest.approx(pl, abs=1e-9)
    assert pd > 0.0


def test_poisson_1x2_perspective_swap():
    pw, pd, pl = sources._poisson_1x2(1.8, 0.9)
    pw2, pd2, pl2 = sources._poisson_1x2(0.9, 1.8)
    assert pw == pytest.approx(pl2, abs=1e-9)
    assert pd == pytest.approx(pd2, abs=1e-9)


def test_poisson_1x2_dixon_coles_rho_zero():
    # With rho = 0, it should match the independent Poisson calculation exactly
    pw, pd, pl = sources._poisson_1x2(1.5, 1.0, rho=0.0)
    
    # Calculate independent Poisson manually
    import math
    def pmf(k, lam):
        return math.exp(-lam) * lam ** k / math.factorial(k)
    pw_ind = pd_ind = pl_ind = 0.0
    for h in range(9):
        for a in range(9):
            p = pmf(h, 1.5) * pmf(a, 1.0)
            if h > a:
                pw_ind += p
            elif h == a:
                pd_ind += p
            else:
                pl_ind += p
    assert pw == pytest.approx(pw_ind, abs=1e-9)
    assert pd == pytest.approx(pd_ind, abs=1e-9)
    assert pl == pytest.approx(pl_ind, abs=1e-9)


def test_poisson_1x2_dixon_coles_draw_inflation():
    # Negative rho (like -0.13) should inflate draw probability for low scores
    pw_ind, pd_ind, pl_ind = sources._poisson_1x2(1.0, 1.0, rho=0.0)
    pw_dc, pd_dc, pl_dc = sources._poisson_1x2(1.0, 1.0, rho=-0.13)
    
    assert pd_dc > pd_ind
    assert pw_dc < pw_ind
    assert pl_dc < pl_ind
    assert 0.999 < pw_dc + pd_dc + pl_dc <= 1.0 + 1e-9


def test_poisson_1x2_dixon_coles_negative_tau_clipping():
    # With very high lambda and negative rho, tau would be negative if not clipped.
    # Clip validation: check that no probability is negative.
    pw, pd, pl = sources._poisson_1x2(10.0, 10.0, max_goals=25, rho=-0.15)
    assert all(p >= 0.0 for p in (pw, pd, pl))
    assert 0.999 < pw + pd + pl <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# clean-sheet probability inside the xpts formula
# ---------------------------------------------------------------------------

def test_clean_sheet_probability_is_exp_minus_lambda_against():
    """For a zero-prior GK with p_start=1 the formula reduces to
    appearance + exp(-lam)*CS_pts + conceded_term, so the CS prob is recoverable."""
    lam = 1.25
    p = make_player("GK", ownership=50.0)  # ownership >= 5 -> no scouting term
    xp = xpts.xpts_for_match(p, zero_prior(), state(1.0), strength(1.5, lam))
    conceded = scoring.expected_conceded_penalty("GK", lam)
    implied_cs_prob = (xp - scoring.appearance_pts() - conceded) / C.CLEAN_SHEET_PTS["GK"]
    assert implied_cs_prob == pytest.approx(math.exp(-lam), rel=1e-6)


def test_expected_conceded_penalty_math():
    lam = 1.4
    # E[max(0, conceded-1)] = lam - P(conceded >= 1) for Poisson(lam)
    expected = -1.0 * (lam - (1 - math.exp(-lam)))
    assert scoring.expected_conceded_penalty("GK", lam) == pytest.approx(expected)
    assert scoring.expected_conceded_penalty("DEF", lam) == pytest.approx(expected)
    assert scoring.expected_conceded_penalty("MID", lam) == 0.0
    assert scoring.expected_conceded_penalty("FWD", lam) == 0.0


def test_forwards_get_no_clean_sheet_points():
    lam = 0.4  # very likely clean sheet
    fwd = make_player("FWD", ownership=50.0)
    xp_low = xpts.xpts_for_match(fwd, zero_prior(), state(1.0), strength(1.5, lam))
    xp_high = xpts.xpts_for_match(fwd, zero_prior(), state(1.0), strength(1.5, 3.0))
    # a FWD with zero priors is unaffected by the defensive lambda
    assert xp_low == pytest.approx(xp_high)


# ---------------------------------------------------------------------------
# xpts monotonicity in p_start
# ---------------------------------------------------------------------------

def test_xpts_increases_with_p_start():
    p = make_player("MID", ownership=50.0)
    prior = Prior(player_id=1, goal_share=0.2, assist_share=0.15, stat_baseline=1.2)
    s = strength(1.6, 1.1)
    xs = [xpts.xpts_for_match(p, prior, state(ps), s)
          for ps in (0.0, 0.3, 0.6, 0.9, 1.0)]
    for lo, hi in zip(xs, xs[1:]):
        assert hi > lo
    assert xs[0] == pytest.approx(0.0)  # p_start 0 -> no points at all


def test_xpts_increases_with_p_start_for_low_owned_player():
    """Scouting bonus is monotone in xp, so the total stays monotone in p_start."""
    p = make_player("FWD", ownership=1.0)
    prior = Prior(player_id=1, goal_share=0.3, assist_share=0.1, stat_baseline=1.0)
    s = strength(1.8, 1.2)
    x_lo = xpts.xpts_for_match(p, prior, state(0.4), s)
    x_hi = xpts.xpts_for_match(p, prior, state(0.95), s)
    assert x_hi > x_lo


# ---------------------------------------------------------------------------
# scouting bonus
# ---------------------------------------------------------------------------

def test_scouting_bonus_positive_below_5_percent():
    ev = scoring.scouting_bonus_ev(ownership=2.0, expected_match_pts=5.0)
    assert ev > 0.0
    assert ev <= C.SCOUTING_BONUS_PTS


def test_scouting_bonus_zero_at_or_above_5_percent():
    assert scoring.scouting_bonus_ev(5.0, 5.0) == 0.0
    assert scoring.scouting_bonus_ev(37.5, 8.0) == 0.0


def test_scouting_bonus_flows_into_xpts():
    prior = Prior(player_id=1, goal_share=0.25, assist_share=0.1, stat_baseline=1.0)
    s = strength(1.7, 1.1)
    low_owned = make_player("FWD", ownership=1.5)
    high_owned = make_player("FWD", ownership=40.0)
    xp_low = xpts.xpts_for_match(low_owned, prior, state(1.0), s)
    xp_high = xpts.xpts_for_match(high_owned, prior, state(1.0), s)
    assert xp_low > xp_high  # identical inputs except ownership -> bonus EV only
    assert xp_low - xp_high <= C.SCOUTING_BONUS_PTS + 1e-9
