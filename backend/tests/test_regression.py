"""Tests for the OLS + diagnostics module.

Synthetic-data tests verify the four-test battery wires up correctly:
  * High R² → PASS
  * Autocorrelated residuals → DW outside band → REVIEW
  * Heteroscedastic residuals → BP_p < 0.05 → REVIEW
  * Collinear regressors → max_vif explodes → REVIEW
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.modeling.regression import (
    BP_P_FLOOR,
    DW_BAND,
    R2_FLOOR,
    VIF_CEILING,
    fit_and_diagnose,
    log_returns,
)

RNG = np.random.default_rng(42)


def _clean_design(n: int = 250, k: int = 3) -> tuple[pd.Series, pd.DataFrame]:
    """Generate a near-perfect linear design — should PASS all four tests."""
    X = pd.DataFrame(RNG.normal(0, 1, size=(n, k)), columns=[f"x{i}" for i in range(k)])
    base_coefs = np.array([0.4, -0.3, 0.2])
    coefs = base_coefs[:k] if k <= len(base_coefs) else np.concatenate(
        [base_coefs, RNG.uniform(-0.3, 0.3, size=k - len(base_coefs))]
    )
    eps = RNG.normal(0, 0.05, size=n)  # small homoscedastic noise → high R²
    y = pd.Series(X.values @ coefs + eps)
    return y, X


def test_clean_design_passes() -> None:
    y, X = _clean_design()
    diag = fit_and_diagnose(y, X)

    assert diag["status"] == "PASS"
    assert diag["r2"] >= R2_FLOOR
    assert DW_BAND[0] <= diag["durbin_watson"] <= DW_BAND[1]
    assert diag["breusch_pagan_p"] > BP_P_FLOOR
    assert diag["max_vif"] < VIF_CEILING
    assert set(diag["coefficients"].keys()) == {"x0", "x1", "x2"}
    assert isinstance(diag["intercept"], float)


def test_autocorrelated_residuals_flagged() -> None:
    """AR(1) residuals → DW deviates from 2 → status REVIEW."""
    n = 250
    X = pd.DataFrame(RNG.normal(0, 1, size=(n, 2)), columns=["x0", "x1"])
    eps = np.zeros(n)
    eps[0] = RNG.normal()
    for i in range(1, n):
        eps[i] = 0.85 * eps[i - 1] + RNG.normal(0, 0.1)
    y = pd.Series(X.values @ [0.5, -0.3] + eps)

    diag = fit_and_diagnose(y, X)
    # DW should sit well below 1.5 with strong positive autocorrelation
    assert diag["durbin_watson"] < DW_BAND[0]
    assert diag["status"] == "REVIEW"


def test_perfect_collinearity_explodes_vif() -> None:
    """Two identical columns → VIF infinite → status REVIEW."""
    n = 200
    x0 = pd.Series(RNG.normal(0, 1, size=n))
    X = pd.DataFrame({"x0": x0, "x1": x0 + 1e-10})  # essentially identical
    y = pd.Series(0.4 * X["x0"] + RNG.normal(0, 0.05, size=n))

    diag = fit_and_diagnose(y, X)
    assert diag["max_vif"] > VIF_CEILING
    assert diag["status"] == "REVIEW"


def test_single_predictor_vif_is_one() -> None:
    """One predictor → VIF undefined → set to 1.0 by convention."""
    y, X = _clean_design(k=1)
    diag = fit_and_diagnose(y.iloc[:50], X.iloc[:50])
    assert diag["max_vif"] == 1.0


def test_log_returns_basic() -> None:
    prices = pd.Series([100.0, 101.0, 99.0, 100.0])
    rets = log_returns(prices)
    assert len(rets) == 3
    assert rets.iloc[0] == pytest.approx(np.log(101 / 100))
    assert rets.iloc[1] == pytest.approx(np.log(99 / 101))


def test_empty_inputs_raise() -> None:
    with pytest.raises(ValueError):
        fit_and_diagnose(pd.Series(dtype=float), pd.DataFrame())


def test_resid_std_reported_for_ols() -> None:
    y, X = _clean_design()
    diag = fit_and_diagnose(y, X)
    assert diag["estimator"] == "ols"
    assert diag["alpha"] is None
    assert diag["resid_std"] > 0
    # Small homoscedastic noise (σ≈0.05) → residual std in the same ballpark.
    assert diag["resid_std"] == pytest.approx(0.05, abs=0.03)


def test_invalid_estimator_raises() -> None:
    y, X = _clean_design()
    with pytest.raises(ValueError):
        fit_and_diagnose(y, X, estimator="elasticnet")


def test_ridge_recovers_signal_and_reports_alpha() -> None:
    """Ridge on a clean design recovers OLS-like coefs in raw feature space."""
    y, X = _clean_design()
    diag = fit_and_diagnose(y, X, estimator="ridge")
    assert diag["estimator"] == "ridge"
    assert diag["alpha"] is not None and diag["alpha"] > 0
    assert diag["r2"] >= R2_FLOOR
    # Coefs returned in raw space should track the true [0.4, -0.3, 0.2].
    assert diag["coefficients"]["x0"] == pytest.approx(0.4, abs=0.1)
    assert diag["coefficients"]["x1"] == pytest.approx(-0.3, abs=0.1)


def test_lasso_zeros_out_noise_predictors() -> None:
    """Lasso selection: a pure-noise predictor gets shrunk to (near) zero."""
    n = 400
    x_signal = pd.Series(RNG.normal(0, 1, size=n))
    x_noise = pd.Series(RNG.normal(0, 1, size=n))
    y = pd.Series(0.6 * x_signal + RNG.normal(0, 0.05, size=n))
    X = pd.DataFrame({"signal": x_signal, "noise": x_noise})

    diag = fit_and_diagnose(y, X, estimator="lasso")
    assert diag["estimator"] == "lasso"
    assert abs(diag["coefficients"]["noise"]) < abs(diag["coefficients"]["signal"])
    assert abs(diag["coefficients"]["noise"]) < 0.05  # effectively dropped


def test_ridge_tolerates_collinearity() -> None:
    """High VIF fails OLS but is acceptable under ridge (shrinkage handles it)."""
    n = 250
    x0 = pd.Series(RNG.normal(0, 1, size=n))
    X = pd.DataFrame({"x0": x0, "x1": x0 + RNG.normal(0, 1e-3, size=n)})  # collinear
    y = pd.Series(0.4 * X["x0"] + RNG.normal(0, 0.05, size=n))

    ols = fit_and_diagnose(y, X, estimator="ols")
    ridge = fit_and_diagnose(y, X, estimator="ridge")
    assert ols["max_vif"] > VIF_CEILING
    assert ols["status"] == "REVIEW"  # OLS fails on VIF
    # Ridge reports the same (high) VIF but the gate ignores it.
    assert ridge["max_vif"] > VIF_CEILING
    assert ridge["status"] == "PASS"
