"""Analysis primitives tests — Wilson CI, Cohen's kappa, anchor matching."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from analyze import wilson_ci  # noqa: E402
from validate_annotation import bootstrap_kappa_ci, cohens_kappa  # noqa: E402


def test_wilson_ci_perfect_proportion() -> None:
    lo, hi = wilson_ci(10, 10)
    # 10/10: upper bound ≈ 1, lower bound is non-trivially below 1.
    assert lo < 1.0
    assert hi == pytest.approx(1.0)


def test_wilson_ci_zero_proportion() -> None:
    lo, hi = wilson_ci(0, 10)
    assert lo == 0.0
    assert 0 < hi < 1.0


def test_wilson_ci_known_value() -> None:
    # 5/10 → ~0.5 with width ~0.55 (Wilson is asymmetric near edges)
    lo, hi = wilson_ci(5, 10)
    assert 0.20 < lo < 0.30
    assert 0.70 < hi < 0.80


def test_cohens_kappa_perfect_agreement() -> None:
    r1 = ["Y", "N", "Y", "N", "Y"]
    r2 = ["Y", "N", "Y", "N", "Y"]
    assert cohens_kappa(r1, r2) == 1.0


def test_cohens_kappa_chance_agreement() -> None:
    # Two raters labeling identically random — kappa near 0.
    r1 = ["Y"] * 50 + ["N"] * 50
    r2 = ["Y", "N"] * 50
    k = cohens_kappa(r1, r2)
    assert k is not None
    assert -0.2 < k < 0.2


def test_cohens_kappa_anti_agreement() -> None:
    r1 = ["Y", "Y", "Y", "Y"]
    r2 = ["N", "N", "N", "N"]
    # All marginals on one class each — pe = 0, po = 0 → undefined; we
    # treat as 0.0 per implementation.
    k = cohens_kappa(r1, r2)
    assert k is not None


def test_bootstrap_kappa_ci_returns_pair() -> None:
    r1 = ["Y", "N"] * 20
    r2 = ["Y", "N"] * 20
    ci = bootstrap_kappa_ci(r1, r2, n_iter=100, seed=1)
    assert ci is not None
    lo, hi = ci
    assert lo is not None and hi is not None
    assert lo <= hi
