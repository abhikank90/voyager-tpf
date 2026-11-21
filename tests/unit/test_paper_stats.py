"""Unit tests for scripts/paper_stats.py — hand-checkable values (protocol §9)."""
import numpy as np
import pytest

from scripts.paper_stats import (
    SEED,
    bootstrap_mean_diff,
    cohen_kappa,
    cv,
    holm,
    median_paired_diff,
    percent_agreement,
    rank_biserial,
    wilcoxon_pratt,
    wilson,
)

# ── Wilson score interval ─────────────────────────────────────────────────────

def test_wilson_8_of_10():
    p, lo, hi = wilson(8, 10)
    assert p == pytest.approx(0.8)
    assert lo == pytest.approx(0.490, abs=1e-3)
    assert hi == pytest.approx(0.943, abs=1e-3)


def test_wilson_extremes():
    p0, lo0, hi0 = wilson(0, 10)
    assert p0 == 0.0 and lo0 == 0.0 and 0.0 < hi0 < 1.0
    p1, lo1, hi1 = wilson(10, 10)
    assert p1 == 1.0 and 0.0 < lo1 < 1.0 and hi1 == pytest.approx(1.0)


def test_wilson_n_zero_is_none():
    assert wilson(0, 0) is None


# ── Holm step-down ────────────────────────────────────────────────────────────

def test_holm_basic():
    assert holm({"a": 0.01, "b": 0.04, "c": 0.03}) == {
        "a": pytest.approx(0.03), "b": pytest.approx(0.06), "c": pytest.approx(0.06),
    }


def test_holm_monotone():
    # c's naive multiplier (x1) would drop below b's adjusted value; step-down keeps it.
    assert holm({"a": 0.04, "b": 0.09, "c": 0.10}) == {
        "a": pytest.approx(0.12), "b": pytest.approx(0.18), "c": pytest.approx(0.18),
    }


# ── Rank-biserial ─────────────────────────────────────────────────────────────

def test_rank_biserial_ties_and_zeros():
    # d = x − y = [-2, 0, 1, 1, 3]; non-zero |d| = [2, 1, 1, 3].
    # Ranks (average for ties): 2→3, 1→1.5, 1→1.5, 3→4.
    # ΣR+ = 1.5 + 1.5 + 4 = 7, ΣR− = 3 → r = (7−3)/(7+3) = 0.4.
    x = [3, 0, 2, 2, 4]
    y = [5, 0, 1, 1, 1]
    assert rank_biserial(x, y) == pytest.approx(0.4)


def test_rank_biserial_all_positive_is_one():
    assert rank_biserial([3, 4, 5], [0, 0, 0]) == pytest.approx(1.0)


def test_rank_biserial_no_nonzero_is_zero():
    assert rank_biserial([1, 2], [1, 2]) == 0.0


# ── Paired Wilcoxon (Pratt) ───────────────────────────────────────────────────

def test_wilcoxon_matches_scipy():
    from scipy.stats import wilcoxon as scipy_wilcoxon

    x = [1, 2, 3, 4, 5]
    y = [1, 1, 1, 1, 1]
    res = wilcoxon_pratt(x, y)
    scipy_res = scipy_wilcoxon(np.array(x) - np.array(y), zero_method="pratt")
    assert res.p == pytest.approx(scipy_res.pvalue)
    assert res.statistic == pytest.approx(scipy_res.statistic)
    assert res.n == 5 and res.n_zero == 1 and res.all_zero is False


def test_wilcoxon_all_zero_flag():
    res = wilcoxon_pratt([1, 2, 3], [1, 2, 3])
    assert res.all_zero is True and res.p == 1.0 and res.n_zero == 3


def test_wilcoxon_counts_zeros():
    res = wilcoxon_pratt([1, 1, 2, 2], [1, 1, 1, 1])
    assert res.n == 4 and res.n_zero == 2


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def test_bootstrap_same_seed_same_bounds():
    x = [1, 3, 5, 7, 9]
    y = [0, 2, 4, 6, 8]
    assert bootstrap_mean_diff(x, y, n=2000) == bootstrap_mean_diff(x, y, n=2000)


def test_bootstrap_constant_diff_zero_width():
    # d = [1, 1, 1]: resampling pairs keeps the difference constant, so both bounds
    # equal the estimate. An unpaired resample of x and y independently would spread.
    est, lo, hi = bootstrap_mean_diff([1, 2, 3], [0, 1, 2], n=1000)
    assert est == pytest.approx(1.0)
    assert lo == pytest.approx(1.0) and hi == pytest.approx(1.0)


def test_bootstrap_upper_bound_at_least_estimate():
    x = [1] * 9 + [20]
    y = [0] * 10
    est, lo, hi = bootstrap_mean_diff(x, y, n=5000, sided="upper")
    assert lo is None
    assert hi >= est


def test_bootstrap_uses_committed_seed():
    assert SEED == 20261117


# ── Cohen's κ and percent agreement ───────────────────────────────────────────

def test_kappa_2x2_hand_computed():
    # a: 6×0 then 4×1; b: 5×0 then 5×1. po = 9/10, pe = 0.6·0.5 + 0.4·0.5 = 0.5
    # κ = (0.9 − 0.5)/(1 − 0.5) = 0.8
    a = [0] * 6 + [1] * 4
    b = [0] * 5 + [1] * 5
    assert percent_agreement(a, b) == pytest.approx(0.9)
    assert cohen_kappa(a, b) == pytest.approx(0.8)


def test_percent_agreement_empty_is_none():
    assert percent_agreement([], []) is None


def test_kappa_perfect_agreement_is_one():
    assert cohen_kappa([0, 1, 0, 1], [0, 1, 0, 1]) == 1.0


def test_kappa_degenerate_marginals_is_none():
    assert cohen_kappa([0, 0, 0], [0, 0, 0]) is None


# ── Coefficient of variation ──────────────────────────────────────────────────

def test_cv_known_example():
    # mean 2.5, sample sd = sqrt(5/3) → cv ≈ 0.5164
    assert cv([1, 2, 3, 4]) == pytest.approx(np.sqrt(5 / 3) / 2.5)


def test_cv_zero_mean_is_none():
    assert cv([-1, 1]) is None
    assert cv([1]) is None  # n < 2


# ── Median paired difference ──────────────────────────────────────────────────

def test_median_paired_diff():
    assert median_paired_diff([3, 4, 5], [1, 1, 1]) == pytest.approx(3.0)
