"""Model-driven trading bot.

Reads the latest OLS predictions and portfolio weights from Supabase,
then rebalances the Alpaca paper-trading account to match the target allocation.

Strategy
--------
* Long-only: only hold positions where the model predicts a positive return.
* Target weight comes from the portfolio profile stored in the DB (default P4).
* If predicted_return ≤ 0 for a ticker → target weight = 0 (cash).
* Rebalance tolerance: skip trades smaller than MIN_TRADE_USD.
* Max single-stock allocation is capped at MAX_WEIGHT_PCT of equity.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Portfolio, Prediction
from app.trading.alpaca import AlpacaClient

log = logging.getLogger(__name__)

MIN_TRADE_USD   = 5.0    # skip rebalance legs smaller than this
MAX_WEIGHT_PCT  = 0.30   # never put more than 30% in one stock
MIN_SIGNAL      = 0.003  # min |predicted_return| to enter a position (0.3%)
DEFAULT_PROFILE = "P4_MOD_AGGRESSIVE"


@dataclass
class TradeAction:
    ticker:  str
    side:    str          # "buy" | "sell" | "close" | "skip"
    amount:  float        # USD notional
    reason:  str


async def run(
    session: AsyncSession,
    profile_id: str = DEFAULT_PROFILE,
    dry_run: bool = False,
    api_key: str | None = None,
    secret_key: str | None = None,
) -> list[TradeAction]:
    """Execute one rebalance cycle. Returns list of actions taken (or simulated)."""

    alpaca  = AlpacaClient(api_key=api_key, secret_key=secret_key)
    actions: list[TradeAction] = []

    # ── 1. Fetch portfolio weights ────────────────────────────────────────────
    portfolio = await session.get(Portfolio, profile_id)
    if portfolio is None:
        raise RuntimeError(f"Portfolio {profile_id!r} not found in DB")
    weights: dict[str, float] = portfolio.weights or {}
    if not weights:
        raise RuntimeError(f"Portfolio {profile_id!r} has no weights")

    # ── 2. Fetch latest predictions for each ticker ───────────────────────────
    cutoff = date.today() - timedelta(days=3)
    rows = (
        await session.execute(
            select(Prediction)
            .where(Prediction.ticker.in_(list(weights.keys())))
            .where(Prediction.predicted_for >= cutoff)
            .order_by(Prediction.ticker, Prediction.predicted_at.desc())
        )
    ).scalars().all()

    # Keep only the most recent prediction per ticker
    latest: dict[str, Prediction] = {}
    for p in rows:
        if p.ticker not in latest:
            latest[p.ticker] = p

    log.info("Predictions loaded for %s: %s", profile_id, list(latest.keys()))

    # ── 3. Build target allocations ───────────────────────────────────────────
    equity = await alpaca.get_equity()
    log.info("Account equity: $%.2f", equity)

    # Signal filter: zero weight if model predicts down or no prediction
    effective_weights: dict[str, float] = {}
    for ticker, w in weights.items():
        pred = latest.get(ticker)
        if pred is None:
            log.warning("No prediction for %s — skipping", ticker)
            continue
        ret = float(pred.predicted_return or 0.0)
        if ret >= MIN_SIGNAL:
            effective_weights[ticker] = w
        else:
            log.info("Signal below threshold for %s (ret=%.4f < %.4f) → 0 weight", ticker, ret, MIN_SIGNAL)

    # Re-normalize so weights still sum to 1.0
    total_w = sum(effective_weights.values())
    if total_w > 0:
        effective_weights = {t: w / total_w for t, w in effective_weights.items()}

    # Cap each ticker at MAX_WEIGHT_PCT
    for ticker in list(effective_weights):
        if effective_weights[ticker] > MAX_WEIGHT_PCT:
            effective_weights[ticker] = MAX_WEIGHT_PCT
    # Re-normalize after cap
    total_w = sum(effective_weights.values())
    if total_w > 0:
        effective_weights = {t: w / total_w for t, w in effective_weights.items()}

    target_values: dict[str, float] = {
        t: w * equity for t, w in effective_weights.items()
    }

    log.info("Target allocations: %s", {t: f"${v:.2f}" for t, v in target_values.items()})

    # ── 4. Get current positions ──────────────────────────────────────────────
    positions = await alpaca.get_positions()
    current_values: dict[str, float] = {
        t: float(p["market_value"]) for t, p in positions.items()
    }

    # ── 5. Close positions for tickers no longer in target ───────────────────
    all_tickers = set(target_values) | set(current_values)
    for ticker in all_tickers:
        target  = target_values.get(ticker, 0.0)
        current = current_values.get(ticker, 0.0)
        delta   = target - current

        if abs(delta) < MIN_TRADE_USD:
            actions.append(TradeAction(ticker, "skip", abs(delta), "delta too small"))
            continue

        if delta > 0:
            action = TradeAction(ticker, "buy", delta, f"target=${target:.2f} current=${current:.2f}")
            actions.append(action)
            if not dry_run:
                try:
                    await alpaca.market_buy(ticker, delta)
                    log.info("BUY  %s $%.2f", ticker, delta)
                except Exception as e:
                    log.error("BUY %s failed: %s", ticker, e)
        else:
            sell_amt = abs(delta)
            if current <= MIN_TRADE_USD:
                # Close full position
                action = TradeAction(ticker, "close", current, "closing full position")
                actions.append(action)
                if not dry_run:
                    try:
                        await alpaca.close_position(ticker)
                        log.info("CLOSE %s (full)", ticker)
                    except Exception as e:
                        log.error("CLOSE %s failed: %s", ticker, e)
            else:
                action = TradeAction(ticker, "sell", sell_amt, f"target=${target:.2f} current=${current:.2f}")
                actions.append(action)
                if not dry_run:
                    try:
                        await alpaca.market_sell_notional(ticker, sell_amt)
                        log.info("SELL %s $%.2f", ticker, sell_amt)
                    except Exception as e:
                        log.error("SELL %s failed: %s", ticker, e)

    return actions
