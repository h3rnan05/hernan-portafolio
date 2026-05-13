"""OLS regression + four-diagnostic battery.

Implements brief §4.2 verbatim:
    R² ≥ 0.90
    Durbin-Watson ∈ [1.5, 2.5]
    Breusch-Pagan p > 0.05
    max VIF < 10

The function takes a clean (no-NaN, aligned) y/X pair and returns a plain
dict — JSON-serializable straight into the ``models.coefficients`` column.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.outliers_influence import variance_inflation_factor
from statsmodels.stats.stattools import durbin_watson

R2_FLOOR = 0.90
DW_BAND = (1.5, 2.5)
BP_P_FLOOR = 0.05
VIF_CEILING = 10.0


class DiagnosticResult(TypedDict):
    """JSON-friendly fit summary, ready for direct insert into ``models``."""

    coefficients: dict[str, float]
    intercept: float
    r2: float
    r2_adj: float
    durbin_watson: float
    breusch_pagan_p: float
    max_vif: float
    status: str  # 'PASS' | 'REVIEW'
    n_obs: int


@dataclass(frozen=True, slots=True)
class FitInputs:
    """Convenience wrapper so callers can pass the original frames around."""

    y: pd.Series
    X: pd.DataFrame


def _passed(r2: float, dw: float, bp_p: float, max_vif: float) -> bool:
    return (
        r2 >= R2_FLOOR
        and DW_BAND[0] <= dw <= DW_BAND[1]
        and bp_p > BP_P_FLOOR
        and max_vif < VIF_CEILING
    )


def fit_and_diagnose(y: pd.Series, X: pd.DataFrame) -> DiagnosticResult:
    """Fit OLS on (y, X) and return the four diagnostic stats.

    Pre-conditions:
        * ``y`` and ``X`` share an index (caller aligned + dropna'd).
        * ``X`` has no constant column — we add one.

    Edge case: if X has a single predictor, VIF is undefined; we return 1.0
    so the test trivially passes.
    """
    if y.empty or X.empty or len(y) != len(X):
        raise ValueError("y and X must be non-empty and aligned")

    X_const = sm.add_constant(X, has_constant="add")
    res = sm.OLS(y.astype(float), X_const.astype(float)).fit()

    r2 = float(res.rsquared)
    r2_adj = float(res.rsquared_adj)
    dw = float(durbin_watson(res.resid))
    _bp_lm, bp_p, _, _ = het_breuschpagan(res.resid, X_const)
    bp_p = float(bp_p)

    if X_const.shape[1] <= 2:  # constant + 1 predictor
        max_vif = 1.0
    else:
        max_vif = float(
            max(
                variance_inflation_factor(X_const.values, i)
                for i in range(1, X_const.shape[1])
            )
        )
        # NaN VIF means perfect collinearity; treat as fail-fast big number
        if not np.isfinite(max_vif):
            max_vif = float("inf")

    coefficients = {col: float(res.params[col]) for col in X.columns}
    intercept = float(res.params["const"])

    return DiagnosticResult(
        coefficients=coefficients,
        intercept=intercept,
        r2=r2,
        r2_adj=r2_adj,
        durbin_watson=dw,
        breusch_pagan_p=bp_p,
        max_vif=max_vif,
        status="PASS" if _passed(r2, dw, bp_p, max_vif) else "REVIEW",
        n_obs=int(res.nobs),
    )


def log_returns(prices: pd.Series) -> pd.Series:
    """Log returns of a price series — drops the first NaN automatically."""
    return np.log(prices.astype(float)).diff().dropna()
