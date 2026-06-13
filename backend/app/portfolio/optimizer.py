"""Five-profile portfolio weight builder.

Replicates brief §5 deterministically:

    P1 Conservative   → low-vol weighting scaled by model confidence (R²)
    P3 Balanced       → equal weights
    P5 Aggressive     → return × Sharpe weighting
    P2, P4            → 50/50 blends of adjacent profiles

All weight dicts are normalized to sum to 1.0 ± 1e-6 (validated by
``validate_weights``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Mapping


WEIGHT_TOLERANCE = 1e-6

# Tickers that belong to the low-volatility / consumer-staples group.
# These are the only ones included in the P0_ULTRA_CONSERVATIVE profile.
LOW_VOL_TICKERS: frozenset[str] = frozenset(
    {"KMB", "CLX", "CL", "KHC", "MDLZ", "HSY", "FDX", "AAL", "BMGL"}
)

PROFILE_IDS = (
    "P0_ULTRA_CONSERVATIVE",
    "P1_CONSERVATIVE",
    "P2_MOD_CONSERVATIVE",
    "P3_BALANCED",
    "P4_MOD_AGGRESSIVE",
    "P5_AGGRESSIVE",
)


def _normalize(s: pd.Series) -> dict[str, float]:
    """Clip negatives to zero, normalize, fall back to equal-weight on zero-sum."""
    s = s.astype(float).clip(lower=0.0)
    total = s.sum()
    if total <= 0:
        # All zero (or all negative pre-clip) — degrade to equal weight
        n = len(s)
        return {idx: 1.0 / n for idx in s.index} if n else {}
    return (s / total).to_dict()


def blend(
    a: Mapping[str, float],
    b: Mapping[str, float],
    t: float,
) -> dict[str, float]:
    """Convex combination of two weight dicts: ``(1-t)·a + t·b``.

    Both inputs are expected over the same set of tickers; missing keys are
    treated as zero.
    """
    if not 0.0 <= t <= 1.0:
        raise ValueError(f"blend t must be in [0, 1], got {t}")
    keys = set(a) | set(b)
    blended = {k: (1 - t) * a.get(k, 0.0) + t * b.get(k, 0.0) for k in keys}
    s = sum(blended.values())
    if s <= 0:
        n = len(keys)
        return {k: 1.0 / n for k in keys} if n else {}
    return {k: v / s for k, v in blended.items()}


def build_ultra_conservative(
    metrics: pd.DataFrame,
) -> dict[str, float]:
    """P0 Ultra-Conservative: minimum-variance weighting over low-vol tickers.

    Selects only the LOW_VOL_TICKERS present in ``metrics``, then applies
    inverse-variance weights (1/σ² normalized) so that the noisiest names
    get the smallest allocation.  Falls back to equal-weight when variance
    data is unavailable.
    """
    subset = metrics[metrics.index.isin(LOW_VOL_TICKERS)].copy()
    if subset.empty:
        return {}

    # Replace zero variance with a small floor so we don't divide by zero.
    var = (subset["vol_annual"] ** 2).clip(lower=1e-8)
    inv_var = 1.0 / var
    return _normalize(inv_var)


def build_portfolios(
    metrics: pd.DataFrame,
) -> dict[str, dict[str, float]]:
    """Build all five risk-profile weight dicts from per-stock metrics.

    Args:
        metrics: indexed by ticker, columns:
            - ``r2``        model fit confidence (0..1)
            - ``pred_ret_30d`` 30-day cumulative log return prediction
            - ``vol_annual`` annualized volatility (std × √252)
            - ``sharpe``     pred_ret_30d × (252/30) ÷ vol_annual

    Returns:
        ``{profile_id: {ticker: weight}}`` — five entries, weights sum to 1.0.
    """
    required = {"r2", "pred_ret_30d", "vol_annual", "sharpe"}
    missing = required - set(metrics.columns)
    if missing:
        raise ValueError(f"metrics missing columns: {sorted(missing)}")
    if metrics.empty:
        raise ValueError("metrics dataframe is empty")

    sm = metrics.copy()
    n = len(sm)

    # Normalized rank scores in [0, 1]
    sm["risk_score"] = sm["vol_annual"].rank(method="min") / n
    sm["return_score"] = sm["sharpe"].rank(method="min") / n
    sm["confidence"] = sm["r2"].clip(lower=0.0, upper=1.0)

    # P1 Conservative: prefer low-vol, weighted by model confidence
    w1 = (1.0 - sm["risk_score"]) * sm["confidence"]
    p1 = _normalize(w1)

    # P3 Balanced: equal weight
    p3 = {t: 1.0 / n for t in sm.index}

    # P5 Aggressive: prefer high return-score × positive Sharpe
    w5 = sm["return_score"] * sm["sharpe"].clip(lower=0.01)
    p5 = _normalize(w5)

    p2 = blend(p1, p3, 0.5)
    p4 = blend(p3, p5, 0.5)

    p0 = build_ultra_conservative(sm)

    result: dict[str, dict[str, float]] = {
        "P1_CONSERVATIVE":    p1,
        "P2_MOD_CONSERVATIVE": p2,
        "P3_BALANCED":        p3,
        "P4_MOD_AGGRESSIVE":  p4,
        "P5_AGGRESSIVE":      p5,
    }
    if p0:
        result["P0_ULTRA_CONSERVATIVE"] = p0
    return result


def validate_weights(
    weights: Mapping[str, float],
    tolerance: float = WEIGHT_TOLERANCE,
) -> None:
    """Property check: weights non-negative and sum within tolerance of 1.0."""
    total = sum(weights.values())
    if abs(total - 1.0) > tolerance:
        raise AssertionError(f"weights sum {total:.10f} ≠ 1.0 (tol {tolerance})")
    if any(v < -tolerance for v in weights.values()):
        raise AssertionError(f"negative weight in {weights}")


PROFILE_DESCRIPTIONS: dict[str, str] = {
    "P0_ULTRA_CONSERVATIVE": (
        "Minimum-variance weighting across consumer staples and low-volatility "
        "names (KMB, CLX, CL, KHC, MDLZ, HSY, FDX, AAL, BMGL). "
        "Inverse-variance allocation — noisiest names get smallest weight."
    ),
    "P1_CONSERVATIVE": "Low-volatility tilt scaled by model confidence (R²).",
    "P2_MOD_CONSERVATIVE": "50/50 blend of P1 and P3.",
    "P3_BALANCED": "Equal-weight across all stocks.",
    "P4_MOD_AGGRESSIVE": "50/50 blend of P3 and P5.",
    "P5_AGGRESSIVE": "Return × Sharpe weighting; tilts toward high-upside names.",
}
