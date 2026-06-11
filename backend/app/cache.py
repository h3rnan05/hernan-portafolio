"""In-process TTL cache for expensive read endpoints.

The underlying data changes ~once per day (daily ingestion + the prediction /
portfolio rebuild), so heavy GET responses are cached for an hour instead of
recomputed on every request. This is deliberately dependency-free — a plain
dict keyed by route + query params, no Redis.

Cross-process invalidation
--------------------------
The API runs in a different process from the daily CLI scripts, so an in-memory
``dict.clear()`` in a script wouldn't reach the server. We use a filesystem
"bust marker": :func:`bust_cache` touches a marker file, and the server treats
any cache entry created *before* the marker's mtime as stale. Within a single
host this gives near-instant invalidation; across hosts the 1-hour TTL bounds
worst-case staleness. Point all processes at a shared path via the
``CACHE_BUST_FILE`` env var when running multi-host.

This module changes *when* results are computed and *how* they're reused — never
the calculations themselves.
"""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

from fastapi import Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["bust_cache", "cache_clear", "cache_stats", "ttl_cache"]

# Default TTL and the Cache-Control advertised to shared caches / the CDN.
DEFAULT_TTL_SECONDS = 3600
STALE_WHILE_REVALIDATE = 86400

# Soft cap on distinct cache keys to bound memory; oldest entries evicted first.
_MAX_ENTRIES = 512

# key -> (created_at_epoch, value)
_store: dict[str, tuple[float, Any]] = {}

_BUST_FILE = Path(
    os.environ.get(
        "CACHE_BUST_FILE",
        Path(tempfile.gettempdir()) / "portfolio_engine_cache.bust",
    )
)

T = TypeVar("T")


def _bust_mtime() -> float:
    """Epoch mtime of the bust marker, or 0.0 if it doesn't exist yet."""
    try:
        return _BUST_FILE.stat().st_mtime
    except OSError:
        return 0.0


def cache_clear() -> None:
    """Drop every entry in *this* process's cache (does not touch the marker)."""
    _store.clear()


def bust_cache() -> None:
    """Invalidate caches across all API processes.

    Touches the marker file (so other processes see their entries as stale) and
    clears this process's in-memory store. Call this from the daily scripts /
    refit endpoints after the data changes.
    """
    try:
        _BUST_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BUST_FILE.touch()
    except OSError:
        # Read-only FS or similar — fall back to in-process clear only.
        pass
    _store.clear()


def cache_stats() -> dict[str, Any]:
    """Lightweight introspection for debugging/verification."""
    return {"entries": len(_store), "bust_marker": str(_BUST_FILE)}


def _make_key(func: Callable[..., Any], args: tuple, kwargs: dict) -> str:
    """Build a cache key from the route handler + its path/query params.

    Framework-injected arguments (DB session, Request, Response) are excluded so
    they never affect the key — only path params and query params remain.
    """
    parts = [func.__module__, func.__qualname__]
    for i, v in enumerate(args):
        if isinstance(v, (AsyncSession, Request, Response)):
            continue
        parts.append(f"#{i}={v!r}")
    for k in sorted(kwargs):
        v = kwargs[k]
        if isinstance(v, (AsyncSession, Request, Response)):
            continue
        parts.append(f"{k}={v!r}")
    return "|".join(parts)


def _evict_if_needed() -> None:
    if len(_store) <= _MAX_ENTRIES:
        return
    # Drop the oldest ~10% by creation time.
    drop = max(1, _MAX_ENTRIES // 10)
    for key in sorted(_store, key=lambda k: _store[k][0])[:drop]:
        _store.pop(key, None)


def ttl_cache(
    seconds: int = DEFAULT_TTL_SECONDS,
    *,
    cache_control: bool | str = True,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Cache an async route handler's result keyed by path + query params.

    Usage (the route decorator stays outermost so FastAPI still sees the real
    signature via ``functools.wraps``)::

        @router.get("", response_model=list[PortfolioOut])
        @ttl_cache(seconds=3600)
        async def list_portfolios(response: Response, session=Depends(...)):
            ...

    When the handler declares a ``response: Response`` parameter, the
    ``Cache-Control`` header is set on both hits and misses so shared caches /
    the CDN can serve the response too.
    """
    if cache_control is True:
        header_value = (
            f"public, s-maxage={seconds}, "
            f"stale-while-revalidate={STALE_WHILE_REVALIDATE}"
        )
    elif isinstance(cache_control, str):
        header_value = cache_control
    else:
        header_value = ""

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            response = next(
                (v for v in (*args, *kwargs.values()) if isinstance(v, Response)),
                None,
            )
            key = _make_key(func, args, kwargs)
            now = time.time()
            entry = _store.get(key)
            fresh = (
                entry is not None
                and (now - entry[0]) < seconds
                and entry[0] >= _bust_mtime()
            )
            if fresh:
                value = entry[1]  # type: ignore[index]
            else:
                value = await func(*args, **kwargs)
                _store[key] = (now, value)
                _evict_if_needed()

            if header_value and response is not None:
                response.headers["Cache-Control"] = header_value
            return value

        return wrapper

    return decorator
