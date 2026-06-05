"""Greedy feature selection (reuse-enabled by default — HER-14).

Brief §4.3 originally forbade reusing a predictor across stocks, copying the
Excel layout. That rule is an artifact of the spreadsheet, not statistics:
each per-stock OLS is fit independently, so forcing NVDA and QCOM onto
*different* predictors when both load the same factor (semiconductors)
strictly hurts both models. HER-14 makes reuse the default and keeps the old
no-overlap path behind ``allow_reuse=False`` for A/B comparison.

Algorithm (per stock, in input order — caller may pre-sort):
    1. Compute |corr| of each candidate predictor against the stock's returns,
       using the lagged predictor frame.
    2. Pick the top ``k_per_stock`` (clamped to ``[k_min, k_max]``).
    3. If ``allow_reuse`` is False, mark the picks used so subsequent stocks
       cannot select them (legacy no-overlap behaviour).

NOTE (HER-13): when called from the walk-forward validator this runs *inside*
each training window, so selection never sees future data. Do not hoist a
single global selection out of the rolling loop — that reintroduces
look-ahead bias.
"""

from __future__ import annotations

import pandas as pd

from app.config import K_PER_STOCK

# Re-export so existing callers that imported DEFAULT_K_PER_STOCK from here
# keep working, but the source of truth lives in app.config.
DEFAULT_K_PER_STOCK = K_PER_STOCK
DEFAULT_K_MIN = 3
DEFAULT_K_MAX = 5


def select_features_greedy(
    returns_df: pd.DataFrame,
    tickers: list[str],
    predictors: list[str],
    lag_days: int = 1,
    k_per_stock: int = DEFAULT_K_PER_STOCK,
    k_min: int = DEFAULT_K_MIN,
    k_max: int = DEFAULT_K_MAX,
    allow_reuse: bool = True,
    lag_overrides: dict[str, int] | None = None,
) -> dict[str, list[str]]:
    """Pick the top-k predictors per ticker by |lagged correlation|.

    Args:
        returns_df: a DataFrame whose columns are ticker IDs *and* predictor
            IDs, all already as **return** (or transform) series. The caller is
            responsible for ensuring the same index across all columns.
        tickers: stock IDs to fit.
        predictors: predictor IDs eligible for selection.
        lag_days: default lag applied to the predictor columns (matches §4.1).
        k_per_stock: target number of predictors per stock.
        k_min, k_max: hard floor and ceiling per stock.
        allow_reuse: when True (default, HER-14) each stock selects its best
            predictors independently and predictors may be shared across
            stocks. When False, a predictor chosen by an earlier ticker is
            removed from later tickers' candidate pools (legacy §4.3 path).
        lag_overrides: optional ``{predictor_id: lag_days}`` map (HER-15) so
            monthly macro can use a different lag than daily market series.
            Predictors absent from the map use ``lag_days``.

    Returns:
        ``{ticker: [predictor_id, …]}``. With ``allow_reuse=True`` the same
        predictor may appear in multiple lists.
    """
    if not (k_min <= k_per_stock <= k_max):
        raise ValueError(f"k_per_stock={k_per_stock} outside [{k_min}, {k_max}]")

    # Validate columns
    missing = [c for c in tickers + predictors if c not in returns_df.columns]
    if missing:
        raise ValueError(f"returns_df missing columns: {missing}")

    # Lag the predictors and keep stocks contemporaneous. Do NOT whole-row
    # dropna here: with mixed-frequency data (daily stocks + monthly macro)
    # most rows have at least one NaN, which would empty the frame. pandas
    # `corrwith` handles NaN pairwise — which is the right primitive.
    # Each predictor is lagged by its own override (HER-15) or the global lag.
    overrides = lag_overrides or {}
    lagged = pd.DataFrame(index=returns_df.index)
    for p in predictors:
        lagged[p] = returns_df[p].shift(overrides.get(p, lag_days))
    aligned = pd.concat([returns_df[tickers], lagged], axis=1)

    used: set[str] = set()
    chosen: dict[str, list[str]] = {}

    for tkr in tickers:
        # |corr| of each candidate predictor vs. this stock's contemporaneous
        # returns. Under no-reuse, predictors already claimed are excluded.
        candidate_cols = predictors if allow_reuse else [p for p in predictors if p not in used]
        if not candidate_cols:
            chosen[tkr] = []
            continue

        corrs = (
            aligned[candidate_cols]
            .corrwith(aligned[tkr])
            .abs()
            .sort_values(ascending=False)
        )
        # Drop NaN correlations (constant series, all-NaN windows)
        corrs = corrs.dropna()

        target = min(k_per_stock, len(corrs))
        target = max(k_min if len(corrs) >= k_min else len(corrs), target)
        target = min(target, k_max)

        picks = list(corrs.head(target).index)
        chosen[tkr] = picks
        if not allow_reuse:
            used.update(picks)

    return chosen
