"""Greedy no-overlap feature selection.

Brief §4.3 — the Excel uses different predictors for each of the 9 stocks
with no predictor reused across models. Greedy is fine for v1; ILP upgrade
path is left for Phase 7.

Algorithm:
    For each stock (in input order — caller may pre-sort):
        1. Compute |corr| of each *unused* predictor against the stock's
           returns, using the lagged predictor frame.
        2. Pick the top ``k_per_stock`` (clamped to ``[k_min, k_max]``).
        3. Mark them used so subsequent stocks cannot reuse them.
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
) -> dict[str, list[str]]:
    """Pick predictors per ticker without reuse.

    Args:
        returns_df: a DataFrame whose columns are ticker IDs *and* predictor
            IDs, all already as **return** series. The caller is responsible
            for ensuring the same index across all columns.
        tickers: stock IDs to fit.
        predictors: predictor IDs eligible for selection.
        lag_days: lag applied to the predictor columns (matches §4.1).
        k_per_stock: target number of predictors per stock.
        k_min, k_max: hard floor and ceiling per stock.

    Returns:
        ``{ticker: [predictor_id, …]}`` with no predictor appearing in two lists.
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
    lagged = returns_df[predictors].shift(lag_days)
    aligned = pd.concat([returns_df[tickers], lagged], axis=1)

    used: set[str] = set()
    chosen: dict[str, list[str]] = {}

    for tkr in tickers:
        # |corr| of each unused predictor vs. this stock's contemporaneous returns
        candidate_cols = [p for p in predictors if p not in used]
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
        used.update(picks)

    return chosen
