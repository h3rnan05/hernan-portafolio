"""Main trading runner — orchestrates all 3 bots.

Bot 1: OLS Model Bot (P4 Mod Aggressive)  → ALPACA_OLS_KEY / ALPACA_OLS_SECRET
Bot 2: P0 Ultra Conservative Bot          → ALPACA_P0_KEY  / ALPACA_P0_SECRET
Bot 3: Nancy Pelosi Mirror Bot            → ALPACA_PELOSI_KEY / ALPACA_PELOSI_SECRET

If _KEY is not set, falls back to ALPACA_API_KEY / ALPACA_SECRET_KEY.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trading_run import TradingRun
from app.trading import model_bot

log = logging.getLogger(__name__)


def _key(bot: str, var: str) -> str | None:
    return os.environ.get(f"ALPACA_{bot}_{var}") or os.environ.get(f"ALPACA_{var}")


@dataclass
class BotResult:
    name:    str
    success: bool
    actions: list = field(default_factory=list)
    error:   str | None = None


async def run_all(
    session: AsyncSession,
    dry_run: bool = False,
) -> list[BotResult]:
    """Run all 3 bots in sequence. Returns results per bot."""
    results: list[BotResult] = []
    started_at = datetime.now(UTC)
    log.info("=== TRADING SESSION START %s (dry_run=%s) ===", started_at.isoformat(), dry_run)

    # ── Bot 1: OLS Model Bot (P4) ─────────────────────────────────────────────
    log.info("--- Bot 1: OLS Model Bot (P4_MOD_AGGRESSIVE) ---")
    try:
        actions = await model_bot.run(
            session=session,
            profile_id="P4_MOD_AGGRESSIVE",
            dry_run=dry_run,
            api_key=_key("OLS", "API_KEY"),
            secret_key=_key("OLS", "SECRET_KEY"),
        )
        results.append(BotResult("OLS Model Bot (P4)", success=True, actions=actions))
        log.info("Bot 1 complete: %d actions", len(actions))
        for a in actions:
            log.info("  [P4] %s %s $%.2f — %s", a.side.upper(), a.ticker, a.amount, a.reason)
    except Exception as e:
        log.error("Bot 1 failed: %s", e)
        results.append(BotResult("OLS Model Bot (P4)", success=False, error=str(e)))

    # ── Bot 2: P0 Ultra Conservative Bot ─────────────────────────────────────
    log.info("--- Bot 2: P0 Ultra Conservative Bot ---")
    p0_key    = _key("P0", "API_KEY")
    p0_secret = _key("P0", "SECRET_KEY")

    # Only run if we have a separate P0 account configured
    if os.environ.get("ALPACA_P0_API_KEY"):
        try:
            actions = await model_bot.run(
                session=session,
                profile_id="P0_ULTRA_CONSERVATIVE",
                dry_run=dry_run,
                api_key=p0_key,
                secret_key=p0_secret,
            )
            results.append(BotResult("P0 Ultra Conservative Bot", success=True, actions=actions))
            log.info("Bot 2 complete: %d actions", len(actions))
            for a in actions:
                log.info("  [P0] %s %s $%.2f — %s", a.side.upper(), a.ticker, a.amount, a.reason)
        except Exception as e:
            log.error("Bot 2 failed: %s", e)
            results.append(BotResult("P0 Ultra Conservative Bot", success=False, error=str(e)))
    else:
        log.info("Bot 2 skipped — ALPACA_P0_API_KEY not configured")
        results.append(BotResult(
            "P0 Ultra Conservative Bot",
            success=False,
            error="ALPACA_P0_API_KEY not set — add it to .env to enable",
        ))

    # ── Bot 3: P1 Conservative Model Bot ─────────────────────────────────────
    log.info("--- Bot 3: P1 Conservative Model Bot ---")
    p1_key    = _key("PELOSI", "API_KEY")
    p1_secret = _key("PELOSI", "SECRET_KEY")

    if os.environ.get("ALPACA_PELOSI_API_KEY"):
        try:
            actions = await model_bot.run(
                session=session,
                profile_id="P1_CONSERVATIVE",
                dry_run=dry_run,
                api_key=p1_key,
                secret_key=p1_secret,
            )
            results.append(BotResult("P1 Conservative Bot", success=True, actions=actions))
            log.info("Bot 3 complete: %d actions", len(actions))
            for a in actions:
                log.info("  [P1] %s %s $%.2f — %s", a.side.upper(), a.ticker, a.amount, a.reason)
        except Exception as e:
            log.error("Bot 3 failed: %s", e)
            results.append(BotResult("P1 Conservative Bot", success=False, error=str(e)))

    # ── Persist run log ───────────────────────────────────────────────────────
    if not dry_run:
        bot_keys = {"OLS Model Bot (P4)": "ols", "P0 Ultra Conservative Bot": "p0", "P1 Conservative Bot": "p1"}
        for r in results:
            key = bot_keys.get(r.name, r.name.lower()[:8])
            real_trades = len([a for a in r.actions if hasattr(a, "side") and a.side not in ("skip",)])
            session.add(TradingRun(
                bot=key,
                status="ok" if r.success else "error",
                trades=real_trades,
                error=r.error,
            ))
        await session.commit()

    elapsed = (datetime.now(UTC) - started_at).total_seconds()
    log.info("=== TRADING SESSION END (%.1fs) ===", elapsed)
    return results
