# Modeling — next steps (post modeling-v2 sprint)

These are the highest-value follow-ups identified during the HER-13…HER-18 sprint.
They are deliberately *not* in that sprint — each is a focused, independently
shippable improvement. Listed in rough priority order.

## 1. Feed out-of-sample RMSE into the forecast band (not in-sample σ)

**Where:** `backend/app/modeling/forecast.py` (`sigma_daily`), `routers/models.py`
(`forecast_model`).

The HER-17 confidence band currently uses the model's **in-sample** residual
standard error (`models.resid_std`), or the recent realized volatility as a
fallback. In-sample residual σ *understates* real forecast uncertainty — the
model fits its training window better than it predicts the future. The honest
width comes from the **HER-13 walk-forward out-of-sample RMSE**.

**Do:** persist the OOS RMSE per ticker when validation runs (e.g. a
`models.oos_rmse` column, or a small `model_validation` table refreshed on the
weekly cron), and have the forecast endpoint prefer it over `resid_std`. The
band will widen to its honest size and stop implying more precision than the
model has.

## 2. Let HER-13 metrics drive the PASS / REVIEW gate

**Where:** `backend/app/modeling/regression.py` (`_passed`),
`modeling/validation.py`, `modeling/data.py` (`save_active_model`).

Today PASS/REVIEW is decided purely **in-sample** (R² ≥ 0.02, DW, BP, VIF). A
model can clear that bar and still be a coin flip out-of-sample — which the
sprint showed is the common case here. The gate should consider the
walk-forward result: a model whose OOS directional hit rate does not beat its
up-day base rate *with* statistical significance (binomial p < 0.05) should be
REVIEW, regardless of in-sample R².

**Do:** run the walk-forward as part of (or right after) `refit_all`, store the
result, and fold `validation.significant` into the active-model gate. This makes
"active" mean "has demonstrated out-of-sample skill," not just "fits its own
training data."

## 3. Transaction-cost realism in the strategy backtest

**Where:** `backend/app/modeling/validation.py` (`_metrics`, the long/flat
strategy + Sharpe).

The walk-forward strategy already charges a flat `cost_bps` on every change in
position — good. But it's a single constant and ignores: bid/ask spread that
varies by name, slippage on the open, and the fact that a daily sign-flipping
signal churns the book. The cost-free (or under-costed) Sharpe is the most
over-sold number in backtesting.

**Do:** make `cost_bps` per-ticker (or model it as spread + slippage), and
report turnover alongside Sharpe so the cost sensitivity is visible. Consider a
"net of realistic costs" vs "gross" Sharpe pair in the UI.
