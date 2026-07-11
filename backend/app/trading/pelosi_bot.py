"""Nancy Pelosi / Congressional trades mirror bot.

Data source: QuiverQuant congressional trading API (free tier).
Fallback: House Stock Watcher public data.

Strategy
--------
* Fetch all Pelosi trades disclosed in the last LOOKBACK_DAYS days.
* Skip any we've already executed (tracked in a local state JSON file).
* Purchase → market buy a fixed notional (TRADE_SIZE_USD).
* Sale (Full) → close the position if held.
* Sale (Partial) → sell half of current holding.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.trading.alpaca import AlpacaClient

log = logging.getLogger(__name__)

LOOKBACK_DAYS  = 45           # How far back to look for trades
TRADE_SIZE_USD = 1000.0       # Fixed dollar amount per Pelosi trade
STATE_FILE     = Path(__file__).parent / "pelosi_executed.json"


def _load_state() -> set[str]:
    """Load set of already-executed trade IDs."""
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def _save_state(executed: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(executed), indent=2))


def _trade_id(trade: dict[str, Any]) -> str:
    return f"{trade.get('ticker','')}-{trade.get('transaction_date','')}-{trade.get('type','')}"


# ── Data fetchers (try in order) ─────────────────────────────────────────────

async def _fetch_quiverquant(lookback: int) -> list[dict[str, Any]]:
    """QuiverQuant free tier — congressional trading for Pelosi."""
    api_key = os.environ.get("QUIVERQUANT_API_KEY", "")
    if not api_key:
        return []
    cutoff = (date.today() - timedelta(days=lookback)).isoformat()
    url = "https://api.quiverquant.com/beta/live/congresstrading"
    headers = {"Authorization": f"Token {api_key}"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(url, headers=headers)
        if r.status_code != 200:
            log.warning("QuiverQuant returned %s", r.status_code)
            return []
        data = r.json()
    trades = [
        t for t in data
        if "pelosi" in t.get("Representative", "").lower()
        and t.get("Date", "") >= cutoff
    ]
    return [
        {
            "ticker":           t.get("Ticker", ""),
            "transaction_date": t.get("Date", ""),
            "type":             t.get("Transaction", ""),
            "amount":           t.get("Amount", ""),
            "source":           "quiverquant",
        }
        for t in trades
        if t.get("Ticker", "").isalpha()
    ]


def _parse_date(raw: str) -> str:
    """Normalize transaction_date to YYYY-MM-DD regardless of source format."""
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            from datetime import datetime as dt
            return dt.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw  # return as-is if unrecognized


async def _fetch_house_stock_watcher(lookback: int) -> list[dict[str, Any]]:
    """House Stock Watcher — tries actively-maintained GitHub mirror first."""
    cutoff = (date.today() - timedelta(days=lookback)).isoformat()
    urls = [
        # Actively maintained mirror (updated daily via GitHub Actions)
        "https://raw.githubusercontent.com/TattooedHead/house-stock-watcher-data/main/data/all_transactions.json",
        # Original S3 (may redirect or be offline)
        "https://house-stock-watcher-data.s3-us-east-2.amazonaws.com/data/all_transactions.json",
    ]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        for url in urls:
            try:
                r = await c.get(url)
                if r.status_code != 200 or len(r.content) < 1000:
                    continue
                data = r.json()
                trades = []
                for t in data:
                    if "pelosi" not in t.get("representative", "").lower():
                        continue
                    ticker = t.get("ticker", "").replace("$", "").strip()
                    if not ticker.isalpha():
                        continue
                    tx_date = _parse_date(t.get("transaction_date", ""))
                    if tx_date < cutoff:
                        continue
                    trades.append({
                        "ticker":           ticker.upper(),
                        "transaction_date": tx_date,
                        "type":             t["type"],
                        "amount":           t.get("amount", ""),
                        "disclosure_date":  _parse_date(t.get("disclosure_date", "")),
                        "source":           "housestockwatcher",
                    })
                if trades:
                    log.info("HSW: %d Pelosi trades from %s", len(trades), url.split("/")[2])
                    return trades
            except Exception as e:
                log.debug("HSW fetch failed (%s): %s", url, e)
    return []


async def fetch_pelosi_trades(lookback: int = LOOKBACK_DAYS) -> list[dict[str, Any]]:
    """Try each data source in order and return the first successful result."""
    for fetcher in [_fetch_quiverquant, _fetch_house_stock_watcher]:
        try:
            trades = await fetcher(lookback)
            if trades:
                log.info("Pelosi trades fetched via %s: %d trades", fetcher.__name__, len(trades))
                return trades
        except Exception as e:
            log.warning("%s failed: %s", fetcher.__name__, e)
    log.warning("All Pelosi data sources failed — no trades to mirror")
    return []


# ── Bot runner ────────────────────────────────────────────────────────────────

async def run(
    dry_run: bool = False,
    api_key: str | None = None,
    secret_key: str | None = None,
    trade_size_usd: float = TRADE_SIZE_USD,
) -> list[dict[str, Any]]:
    """Mirror unexecuted Pelosi trades. Returns list of action dicts."""
    alpaca   = AlpacaClient(api_key=api_key, secret_key=secret_key)
    executed = _load_state()
    actions: list[dict[str, Any]] = []

    trades = await fetch_pelosi_trades()
    if not trades:
        log.info("No Pelosi trades found — nothing to do")
        return actions

    positions = await alpaca.get_positions()

    for trade in trades:
        tid    = _trade_id(trade)
        ticker = trade["ticker"].upper()

        if tid in executed:
            log.debug("Already executed: %s", tid)
            continue

        tx_type = trade["type"].lower()
        is_buy  = "purchase" in tx_type or "buy" in tx_type
        is_sell = "sale" in tx_type or "sell" in tx_type

        if not (is_buy or is_sell):
            log.info("Unknown transaction type %r for %s — skip", trade["type"], ticker)
            continue

        action: dict[str, Any] = {
            "ticker":  ticker,
            "type":    "buy" if is_buy else "sell",
            "amount":  trade_size_usd,
            "source":  trade.get("source", "unknown"),
            "date":    trade.get("transaction_date", ""),
            "dry_run": dry_run,
            "success": False,
        }

        if is_buy:
            log.info("Pelosi BUY  %s $%.0f (disclosed %s)", ticker, trade_size_usd, trade.get("transaction_date", ""))
            if not dry_run:
                try:
                    await alpaca.market_buy(ticker, trade_size_usd)
                    action["success"] = True
                    executed.add(tid)
                except Exception as e:
                    log.error("Pelosi BUY %s failed: %s", ticker, e)
                    action["error"] = str(e)
            else:
                action["success"] = True
                executed.add(tid)

        elif is_sell:
            pos = positions.get(ticker)
            if pos is None:
                log.info("Pelosi SELL %s — not held, skipping", ticker)
                action["type"]    = "sell_skip"
                action["success"] = True
                executed.add(tid)
            else:
                current_mv = float(pos["market_value"])
                full_sale  = "full" in tx_type
                sell_amt   = current_mv if full_sale else current_mv / 2
                sell_amt   = max(sell_amt, 1.0)
                action["amount"] = sell_amt
                log.info("Pelosi SELL %s $%.2f (%s)", ticker, sell_amt, "full" if full_sale else "partial")
                if not dry_run:
                    try:
                        if full_sale:
                            await alpaca.close_position(ticker)
                        else:
                            await alpaca.market_sell_notional(ticker, sell_amt)
                        action["success"] = True
                        executed.add(tid)
                    except Exception as e:
                        log.error("Pelosi SELL %s failed: %s", ticker, e)
                        action["error"] = str(e)
                else:
                    action["success"] = True
                    executed.add(tid)

        actions.append(action)

    if not dry_run:
        _save_state(executed)

    return actions
