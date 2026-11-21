"""Pure statistics functions for the v1.5 analysis (protocol §9).

Deterministic and dependency-light: numpy for arrays, scipy for the paired Wilcoxon
test and average-rank ties. ``SEED`` is the fixed seed committed for all bootstrap
resampling (§9: "fixed seed committed in the analysis script").

Each docstring cites the protocol section the function serves.
"""
from __future__ import annotations

from typing import NamedTuple

import numpy as np

SEED = 20261117  # committed: the fixed bootstrap seed (§9)


class WilcoxonResult(NamedTuple):
    statistic: float
    p: float
    n: int
    n_zero: int
    all_zero: bool


def wilcoxon_pratt(x, y, alternative: str = "two-sided") -> WilcoxonResult:
    """Paired Wilcoxon signed-rank with Pratt zero handling (§9, H1a/H1c/H5).

    Tests d = x − y. When every difference is zero the test is degenerate (scipy returns
    NaN), so we short-circuit to p = 1.0 and set ``all_zero`` rather than calling scipy.
    """
    from scipy.stats import wilcoxon

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) != len(y):
        raise ValueError("x and y must have equal length")
    d = x - y
    n = len(d)
    n_zero = int((d == 0).sum())
    if n_zero == n:
        return WilcoxonResult(0.0, 1.0, n, n_zero, True)
    res = wilcoxon(d, zero_method="pratt", alternative=alternative)
    return WilcoxonResult(float(res.statistic), float(res.pvalue), n, n_zero, False)


def median_paired_diff(x, y) -> float:
    """Median of the paired differences x − y (effect size reported beside each test)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) != len(y):
        raise ValueError("x and y must have equal length")
    return float(np.median(x - y))


def rank_biserial(x, y) -> float:
    """Matched-pairs rank-biserial correlation (Kerby) over the non-zero differences.

    r = (ΣR+ − ΣR−) / (ΣR+ + ΣR−), where R+/R− are the ranks of |d| assigned to the
    positive/negative differences (average ranks for ties). Returns 0 when there is no
    non-zero difference to rank.
    """
    from scipy.stats import rankdata

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) != len(y):
        raise ValueError("x and y must have equal length")
    d = x - y
    nonzero = d != 0
    if not nonzero.any():
        return 0.0
    ranks = rankdata(np.abs(d[nonzero]), method="average")
    signs = np.sign(d[nonzero])
    sum_pos = ranks[signs > 0].sum()
    sum_neg = ranks[signs < 0].sum()
    return float((sum_pos - sum_neg) / (sum_pos + sum_neg))


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values (monotone, capped at 1; §9, H1a/H1c).

    Sorts p-values ascending and multiplies the i-th (0-based) by m − i, then enforces
    monotonicity so a later test is never less significant than an earlier one.
    """
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    adjusted: dict[str, float] = {}
    prev: float | None = None
    for i, (key, p) in enumerate(items):
        adj = min(1.0, p * (m - i))
        if prev is not None:
            adj = max(adj, prev)
        adjusted[key] = adj
        prev = adj
    return adjusted


def bootstrap_mean_diff(
    x, y, *, n: int = 10000, seed: int = SEED, ci: float = 0.95, sided: str = "two"
):
    """Paired bootstrap of the mean difference, resampling *pairs* (§9, H1b/H1d).

    Resamples indices with replacement and recomputes the mean of d = x − y over the
    sampled pairs, preserving the pairing (an unpaired resample would destroy the
    correlation). Percentile interval. Returns ``(estimate, lo, hi)``; ``lo`` is None
    for ``sided="upper"``.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) != len(y):
        raise ValueError("x and y must have equal length")
    if sided not in ("two", "upper"):
        raise ValueError("sided must be 'two' or 'upper'")
    d = x - y
    rng = np.random.default_rng(seed)
    estimates = np.empty(n)
    for i in range(n):
        sample = rng.integers(0, len(d), size=len(d))
        estimates[i] = d[sample].mean()
    estimate = float(d.mean())
    if sided == "upper":
        return estimate, None, float(np.percentile(estimates, ci * 100))
    alpha = 1.0 - ci
    lo = float(np.percentile(estimates, alpha / 2 * 100))
    hi = float(np.percentile(estimates, (1.0 - alpha / 2) * 100))
    return estimate, lo, hi


def wilson(k, n, conf: float = 0.95):
    """Wilson score interval (no continuity correction) for a proportion k/n (§9, H2/H4).

    Returns ``(p̂, lo, hi)``; None when n == 0.
    """
    if n == 0:
        return None
    from scipy.stats import norm

    z = norm.ppf(1.0 - (1.0 - conf) / 2.0)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z / denom * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return p, center - half, center + half


def percent_agreement(a, b):
    """Proportion of positions where two raters agree (H2c, primary agreement).

    Returns None for empty inputs, matching the κ/Wilson convention of None when the
    statistic is undefined rather than a silent 0.0.
    """
    a = list(a)
    b = list(b)
    if len(a) != len(b):
        raise ValueError("a and b must have equal length")
    if not a:
        return None
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def cohen_kappa(a, b):
    """Cohen's κ for nominal labels (H2c, secondary agreement).

    Returns None when κ is undefined: a single label across both raters (degenerate
    marginals, 0/0). Documented rather than raising.
    """
    a = list(a)
    b = list(b)
    if len(a) != len(b):
        raise ValueError("a and b must have equal length")
    n = len(a)
    if n == 0:
        return None
    labels = sorted(set(a) | set(b))
    if len(labels) < 2:
        return None
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = 0.0
    for label in labels:
        pe += (a.count(label) / n) * (b.count(label) / n)
    if pe >= 1.0:
        return None
    return (po - pe) / (1.0 - pe)


def cv(values):
    """Sample coefficient of variation (sample sd / mean). None if mean == 0 or n < 2."""
    v = np.asarray(values, dtype=float)
    if len(v) < 2:
        return None
    mean = v.mean()
    if mean == 0:
        return None
    return float(v.std(ddof=1) / mean)
