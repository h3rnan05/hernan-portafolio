"""Tools the AI assistant can call to read the live engine.

Each tool is read-only and maps to the same data layer the API uses, so the
assistant always answers from real database state — never made-up numbers. The
dispatcher runs each call against the request's AsyncSession.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modeling.data import list_tickers
from app.modeling.forecast import compute_forecast
from app.modeling.regression import ESTIMATORS
from app.modeling.validation import walk_forward
from app.models import ModelFit, Portfolio, Prediction, Variable

# ─── Tool schemas (sent to Claude) ───────────────────────────────────────────

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_stocks",
        "description": (
            "List every stock the engine tracks (the regression targets) with "
            "its current active model status, estimator, in-sample R², and which "
            "predictors it uses. Call this first when the user asks 'what stocks "
            "do you cover' or to find a ticker."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_model",
        "description": (
            "Get the active regression model for one ticker: the equation, "
            "coefficients, intercept, estimator (ols/ridge/lasso), regularization "
            "alpha, in-sample diagnostics (R², Durbin-Watson, Breusch-Pagan, VIF), "
            "training window, and PASS/REVIEW status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Stock ticker, e.g. NVDA"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_forecast",
        "description": (
            "Get the price forecast for a ticker over the next N business days: "
            "central path, 90% confidence band (lower/upper per day, widening with "
            "horizon), expected direction, and the σ source. Day 1 uses the model "
            "signal; later days drift at the baseline."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "horizon": {"type": "integer", "description": "Business days ahead (1-30)", "default": 5},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_validation",
        "description": (
            "Run the walk-forward OUT-OF-SAMPLE validation for a ticker and return "
            "the honest skill metrics: directional hit rate, the up-day base rate "
            "to compare against, whether the edge is statistically significant "
            "(binomial p-value), RMSE, and the long/flat strategy Sharpe vs "
            "buy-and-hold (net of transaction cost). THIS is what tells you if a "
            "model actually predicts — not the in-sample R². Slower (~a few seconds)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "estimator": {
                    "type": "string",
                    "enum": list(ESTIMATORS),
                    "description": "Which estimator to validate (default ridge, the production default).",
                    "default": "ridge",
                },
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "get_recent_predictions",
        "description": (
            "Get the most recent daily predictions for a ticker with the realized "
            "actual price and percentage error, newest first. Use to show how the "
            "model has been doing day to day."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "days": {"type": "integer", "description": "How many recent predictions (max 60)", "default": 14},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "list_variables",
        "description": (
            "List the input variables (predictors and stocks) with their kind, "
            "category, transform (return/level/surprise), per-variable lag, and "
            "last observed value/date. Optionally filter by kind."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["predictor", "stock", "etf", "index", "portfolio"],
                    "description": "Optional filter.",
                }
            },
        },
    },
    {
        "name": "get_portfolios",
        "description": (
            "Get the five risk-profile portfolios (P1 conservative → P5 aggressive) "
            "with their current per-stock weights and descriptions."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


# ─── Implementations ─────────────────────────────────────────────────────────


async def _list_stocks(session: AsyncSession) -> dict:
    tickers = await list_tickers(session)
    active = {
        m.ticker: m
        for m in (
            await session.execute(select(ModelFit).where(ModelFit.is_active.is_(True)))
        ).scalars().all()
    }
    out = []
    for t in tickers:
        m = active.get(t)
        out.append(
            {
                "ticker": t,
                "has_active_model": m is not None,
                "status": m.status if m else None,
                "estimator": m.estimator if m else None,
                "r2_in_sample": round(float(m.r2), 4) if m else None,
                "predictors": list(m.predictor_ids or []) if m else [],
            }
        )
    return {"stocks": out, "count": len(out)}


async def _get_model(session: AsyncSession, ticker: str) -> dict:
    m = (
        await session.execute(
            select(ModelFit)
            .where(ModelFit.ticker == ticker.upper(), ModelFit.is_active.is_(True))
            .limit(1)
        )
    ).scalar_one_or_none()
    if m is None:
        return {"error": f"No active model for {ticker.upper()}."}
    coefs = {k: float(v) for k, v in (m.coefficients or {}).items()}
    eq_terms = " ".join(f"{b:+.5f}·{n}(t-lag)" for n, b in coefs.items())
    return {
        "ticker": m.ticker,
        "estimator": m.estimator,
        "alpha": float(m.alpha) if m.alpha is not None else None,
        "status": m.status,
        "equation": f"ret_t = {float(m.intercept):+.5f} {eq_terms}",
        "intercept": float(m.intercept),
        "coefficients": {k: round(v, 6) for k, v in coefs.items()},
        "predictors": list(m.predictor_ids or []),
        "n_obs": m.n_obs,
        "training_start": m.training_start.isoformat(),
        "training_end": m.training_end.isoformat(),
        "diagnostics": {
            "r2_in_sample": round(float(m.r2), 4),
            "r2_adj": round(float(m.r2_adj), 4),
            "durbin_watson": round(float(m.durbin_watson), 3),
            "breusch_pagan_p": round(float(m.breusch_pagan_p), 4),
            "max_vif": round(float(m.max_vif), 3),
            "resid_std": round(float(m.resid_std), 5) if m.resid_std is not None else None,
        },
    }


async def _get_validation(session: AsyncSession, ticker: str, estimator: str = "ridge") -> dict:
    if estimator not in ESTIMATORS:
        estimator = "ridge"
    r = await walk_forward(session, ticker.upper(), estimator=estimator)
    return {
        "ticker": r.ticker,
        "estimator": r.estimator,
        "n_out_of_sample_predictions": r.n_predictions,
        "hit_rate": round(r.hit_rate, 4) if r.hit_rate is not None else None,
        "up_day_base_rate": round(r.up_day_base_rate, 4) if r.up_day_base_rate is not None else None,
        "edge_vs_base": round(r.edge_vs_base, 4) if r.edge_vs_base is not None else None,
        "hit_rate_pvalue": round(r.hit_rate_pvalue, 4) if r.hit_rate_pvalue is not None else None,
        "statistically_significant": r.significant,
        "rmse": round(r.rmse, 5) if r.rmse is not None else None,
        "sharpe_strategy_net_of_cost": round(r.sharpe_strategy, 3) if r.sharpe_strategy is not None else None,
        "sharpe_buy_and_hold": round(r.sharpe_buy_hold, 3) if r.sharpe_buy_hold is not None else None,
        "total_return_strategy": round(r.total_return_strategy, 4) if r.total_return_strategy is not None else None,
        "total_return_buy_and_hold": round(r.total_return_buy_hold, 4) if r.total_return_buy_hold is not None else None,
        "note": r.note,
    }


async def _get_recent_predictions(session: AsyncSession, ticker: str, days: int = 14) -> dict:
    days = max(1, min(days, 60))
    rows = (
        await session.execute(
            select(Prediction)
            .where(Prediction.ticker == ticker.upper())
            .order_by(Prediction.predicted_for.desc())
            .limit(days)
        )
    ).scalars().all()
    return {
        "ticker": ticker.upper(),
        "predictions": [
            {
                "for_date": p.predicted_for.isoformat(),
                "predicted_price": round(float(p.predicted_price), 2),
                "actual_price": round(float(p.actual_price), 2) if p.actual_price is not None else None,
                "abs_error_pct": round(float(p.abs_error_pct), 4) if p.abs_error_pct is not None else None,
            }
            for p in rows
        ],
    }


async def _list_variables(session: AsyncSession, kind: str | None = None) -> dict:
    stmt = select(Variable).where(Variable.active.is_(True))
    if kind:
        stmt = stmt.where(Variable.kind == kind)
    stmt = stmt.order_by(Variable.kind, Variable.id)
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "variables": [
            {
                "id": v.id,
                "display_name": v.display_name,
                "kind": v.kind,
                "category": v.category,
                "transform": v.transform,
                "lag_days": v.lag_days,
            }
            for v in rows
        ],
        "count": len(rows),
    }


async def _get_portfolios(session: AsyncSession) -> dict:
    rows = (await session.execute(select(Portfolio).order_by(Portfolio.id))).scalars().all()
    return {
        "portfolios": [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "weights": {k: round(float(v), 4) for k, v in (p.weights or {}).items()},
            }
            for p in rows
        ]
    }


async def dispatch(session: AsyncSession, name: str, tool_input: dict[str, Any]) -> dict:
    """Run a tool by name and return a JSON-serializable result."""
    try:
        if name == "list_stocks":
            return await _list_stocks(session)
        if name == "get_model":
            return await _get_model(session, tool_input["ticker"])
        if name == "get_forecast":
            res = await compute_forecast(
                session, tool_input["ticker"].upper(), int(tool_input.get("horizon", 5) or 5)
            )
            return res if res is not None else {"error": f"No active model/price for {tool_input['ticker'].upper()}."}
        if name == "get_validation":
            return await _get_validation(session, tool_input["ticker"], tool_input.get("estimator", "ridge"))
        if name == "get_recent_predictions":
            return await _get_recent_predictions(session, tool_input["ticker"], int(tool_input.get("days", 14) or 14))
        if name == "list_variables":
            return await _list_variables(session, tool_input.get("kind"))
        if name == "get_portfolios":
            return await _get_portfolios(session)
        return {"error": f"Unknown tool: {name}"}
    except Exception as e:  # surface tool errors to the model so it can recover
        return {"error": f"{type(e).__name__}: {e}"}
