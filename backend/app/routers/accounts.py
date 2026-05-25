"""User accounts — named portfolios auto-classified into the 5 risk profiles."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.modeling.data import latest_price
from app.models import AccountHolding, Portfolio, UserAccount
from app.schemas import (
    AccountHoldingIn,
    AccountHoldingOut,
    ProfileMatch,
    UserAccountCreate,
    UserAccountOut,
    UserAccountPatch,
)

router = APIRouter(prefix="/accounts", tags=["accounts"])


# ─── Classification ──────────────────────────────────────────────────────────

def _classify(
    holding_weights: dict[str, float],
    profiles: list[Portfolio],
) -> ProfileMatch | None:
    """Pick the risk profile whose weights are closest (L2) to the user's weights.

    Both vectors are built over the union of all tickers that appear in any
    profile. Missing tickers are treated as weight=0.
    """
    if not profiles or not holding_weights:
        return None

    all_tickers: set[str] = set(holding_weights)
    for p in profiles:
        all_tickers.update((p.weights or {}).keys())
    tickers = sorted(all_tickers)

    user_vec = [holding_weights.get(t, 0.0) for t in tickers]

    best: tuple[float, Portfolio] | None = None
    for p in profiles:
        w = {k: float(v) for k, v in (p.weights or {}).items()}
        profile_vec = [w.get(t, 0.0) for t in tickers]
        dist = math.sqrt(sum((u - v) ** 2 for u, v in zip(user_vec, profile_vec)))
        if best is None or dist < best[0]:
            best = (dist, p)

    if best is None:
        return None
    return ProfileMatch(profile_id=best[1].id, profile_name=best[1].name, distance=best[0])


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _enrich_holding(session: AsyncSession, h: AccountHolding) -> AccountHoldingOut:
    last = await latest_price(session, h.ticker)
    last_price = last[1] if last else None

    qty = float(h.quantity)
    avg = float(h.avg_price)
    mv = qty * last_price if last_price is not None else None
    cost = qty * avg
    pnl = (mv - cost) if mv is not None else None
    pnl_pct = (pnl / cost) if (pnl is not None and cost > 0) else None

    return AccountHoldingOut(
        id=h.id,
        ticker=h.ticker,
        quantity=qty,
        avg_price=avg,
        notes=h.notes,
        added_at=h.added_at,
        last_price=last_price,
        market_value=mv,
        open_pnl=pnl,
        open_pnl_pct=pnl_pct,
    )


async def _build_account_out(
    session: AsyncSession,
    account: UserAccount,
    profiles: list[Portfolio],
) -> UserAccountOut:
    holdings_rows = (
        await session.execute(
            select(AccountHolding)
            .where(AccountHolding.account_id == account.id)
            .order_by(AccountHolding.ticker.asc())
        )
    ).scalars().all()

    enriched = [await _enrich_holding(session, h) for h in holdings_rows]

    total_mv = sum(h.market_value for h in enriched if h.market_value is not None)

    weights: dict[str, float] = {}
    if total_mv > 0:
        for h in enriched:
            if h.market_value is not None:
                weights[h.ticker] = h.market_value / total_mv

    match = _classify(weights, profiles)

    return UserAccountOut(
        id=account.id,
        name=account.name,
        description=account.description,
        assigned_profile_id=account.assigned_profile_id,
        profile_assigned_at=account.profile_assigned_at,
        created_at=account.created_at,
        holdings=enriched,
        profile_match=match,
        total_market_value=total_mv if total_mv > 0 else None,
    )


async def _get_profiles(session: AsyncSession) -> list[Portfolio]:
    return list(
        (await session.execute(select(Portfolio).order_by(Portfolio.id.asc()))).scalars().all()
    )


async def _save_classification(
    session: AsyncSession,
    account: UserAccount,
    profiles: list[Portfolio],
    holdings_rows: list[AccountHolding],
) -> None:
    """Recompute and persist the best-matching profile for an account."""
    total_mv = 0.0
    prices: dict[str, float] = {}
    for h in holdings_rows:
        last = await latest_price(session, h.ticker)
        if last:
            prices[h.ticker] = last[1]
            total_mv += float(h.quantity) * last[1]

    weights: dict[str, float] = {}
    if total_mv > 0:
        for h in holdings_rows:
            if h.ticker in prices:
                weights[h.ticker] = (float(h.quantity) * prices[h.ticker]) / total_mv

    match = _classify(weights, profiles)
    account.assigned_profile_id = match.profile_id if match else None
    account.profile_assigned_at = datetime.now(UTC) if match else None


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("", response_model=list[UserAccountOut])
async def list_accounts(
    session: AsyncSession = Depends(get_session),
) -> list[UserAccountOut]:
    """List all user accounts with their holdings and risk-profile classification."""
    accounts = (
        await session.execute(
            select(UserAccount).order_by(UserAccount.created_at.asc())
        )
    ).scalars().all()

    profiles = await _get_profiles(session)
    return [await _build_account_out(session, a, profiles) for a in accounts]


@router.post("", response_model=UserAccountOut, status_code=201)
async def create_account(
    body: UserAccountCreate,
    session: AsyncSession = Depends(get_session),
) -> UserAccountOut:
    """Create a new user account with optional initial holdings.

    The account is immediately classified into the nearest risk profile if
    any holdings are provided.
    """
    account = UserAccount(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
    )
    session.add(account)
    await session.flush()

    for h_in in body.holdings:
        session.add(
            AccountHolding(
                id=uuid.uuid4(),
                account_id=account.id,
                ticker=h_in.ticker.upper(),
                quantity=h_in.quantity,
                avg_price=h_in.avg_price,
                notes=h_in.notes,
            )
        )
    await session.flush()

    profiles = await _get_profiles(session)
    holdings_rows = (
        await session.execute(
            select(AccountHolding).where(AccountHolding.account_id == account.id)
        )
    ).scalars().all()
    await _save_classification(session, account, profiles, list(holdings_rows))

    await session.commit()
    await session.refresh(account)
    return await _build_account_out(session, account, profiles)


@router.get("/{account_id}", response_model=UserAccountOut)
async def get_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> UserAccountOut:
    account = await session.get(UserAccount, account_id)
    if account is None:
        raise HTTPException(404, f"Account {account_id} not found")
    profiles = await _get_profiles(session)
    return await _build_account_out(session, account, profiles)


@router.patch("/{account_id}", response_model=UserAccountOut)
async def patch_account(
    account_id: uuid.UUID,
    body: UserAccountPatch,
    session: AsyncSession = Depends(get_session),
) -> UserAccountOut:
    """Update the account name or description."""
    account = await session.get(UserAccount, account_id)
    if account is None:
        raise HTTPException(404, f"Account {account_id} not found")
    if body.name is not None:
        account.name = body.name
    if body.description is not None:
        account.description = body.description
    await session.commit()
    await session.refresh(account)
    profiles = await _get_profiles(session)
    return await _build_account_out(session, account, profiles)


@router.delete("/{account_id}", status_code=204)
async def delete_account(
    account_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete an account and all its holdings."""
    account = await session.get(UserAccount, account_id)
    if account is None:
        raise HTTPException(404, f"Account {account_id} not found")
    await session.execute(
        delete(AccountHolding).where(AccountHolding.account_id == account_id)
    )
    await session.delete(account)
    await session.commit()


@router.put("/{account_id}/holdings", response_model=UserAccountOut)
async def replace_holdings(
    account_id: uuid.UUID,
    body: list[AccountHoldingIn],
    session: AsyncSession = Depends(get_session),
) -> UserAccountOut:
    """Replace all holdings for an account and re-classify its risk profile.

    Sends the full list of current positions — existing holdings not in the
    list are removed.
    """
    account = await session.get(UserAccount, account_id)
    if account is None:
        raise HTTPException(404, f"Account {account_id} not found")

    await session.execute(
        delete(AccountHolding).where(AccountHolding.account_id == account_id)
    )
    for h_in in body:
        session.add(
            AccountHolding(
                id=uuid.uuid4(),
                account_id=account_id,
                ticker=h_in.ticker.upper(),
                quantity=h_in.quantity,
                avg_price=h_in.avg_price,
                notes=h_in.notes,
            )
        )
    await session.flush()

    profiles = await _get_profiles(session)
    holdings_rows = (
        await session.execute(
            select(AccountHolding).where(AccountHolding.account_id == account_id)
        )
    ).scalars().all()
    await _save_classification(session, account, profiles, list(holdings_rows))

    await session.commit()
    await session.refresh(account)
    return await _build_account_out(session, account, profiles)
