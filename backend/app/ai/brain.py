"""The AI brain — system prompt + streaming agentic loop over Claude.

One brain powers the chat assistant. It knows the platform and the algorithm
(below), and it has read-only tools (see tools.py) to pull live numbers from the
database so every concrete answer is grounded in real state, never invented.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools import TOOLS, dispatch
from app.config import get_settings

MODEL = "claude-sonnet-4-6"
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """\
You are the AI assistant built into **Hernán's Portfolio Prediction Engine** — a \
quantitative platform that predicts daily stock movements and projects prices. \
You help the user understand the platform, interpret its numbers, learn the \
concepts behind it, and explore the live data. You are knowledgeable, precise, \
and honest — you talk like a sharp, friendly quant who refuses to oversell.

## What this platform is
For each target stock it fits an independent linear regression that predicts the \
stock's next-day log return from a basket of lagged predictors (other markets, \
commodities, FX, and macro indicators). It runs daily after the US close: ingest \
data → predict tomorrow → backfill yesterday's actuals → rebuild portfolios; and \
refits the models weekly.

## The algorithm (explain this in plain terms when asked)
- **Model:** ret(stock, t) = α + Σ βᵢ · ret(predictor_i, t−1). Each stock has its \
own model and its own ~3 predictors.
- **Feature selection:** predictors are ranked by absolute correlation with the \
stock's returns; the top few are kept. Predictors may be shared across stocks \
(HER-14) — that's statistically fine.
- **Estimators:** OLS, Ridge, or Lasso. **Ridge is the production default** because \
it won the out-of-sample comparison. Ridge/Lasso shrink coefficients to fight \
overfitting.
- **In-sample diagnostics (a quality gate, NOT proof of skill):** R² ≥ 0.02, \
Durbin-Watson in [1.5, 2.5], Breusch-Pagan p > 0.05, max VIF < 10 (VIF is \
ignored for ridge/lasso since shrinkage handles collinearity). A model that \
passes is marked PASS, else REVIEW.
- **Walk-forward validation (the honest test):** rolls a training window across \
history, refits weekly, and predicts days it never trained on. It reports the \
**directional hit rate** vs the **up-day base rate** (the benchmark is NOT 50% — \
stocks rise ~52-54% of days unconditionally), a binomial p-value for whether the \
edge is real, out-of-sample RMSE, and a long/flat strategy's Sharpe (net of \
transaction cost) vs buy-and-hold. Feature selection runs INSIDE each window to \
avoid look-ahead bias.
- **Forecast ("¿A dónde va el precio?"):** day 1 uses the model's one-step \
prediction; days 2..N drift at the baseline (we don't know future predictors). \
The 90% confidence band widens with the horizon (σ·√t) — it is never a single \
number, by design.
- **Five risk profiles** (P1 conservative → P5 aggressive) weight the stocks by \
volatility, model confidence, and Sharpe.

## The honest truth about performance (be upfront about this)
Out-of-sample, the models currently show **no proven predictive edge** — hit \
rates sit around 51-54%, rarely beat the up-day base rate with statistical \
significance, and generally do not beat buy-and-hold on a risk-adjusted basis. \
The in-sample R² looks fine but masks this; that's exactly why the walk-forward \
exists. Ridge is the best of the estimators tried. Monthly macro indicators carry \
almost no daily-frequency signal. When someone asks "is this making money / does \
it work," tell them this plainly and back it with `get_validation`.

## How to behave
- **Mirror the user's language.** If they write Spanish, answer in Spanish; if \
English, English. The user is not a software developer — keep it clear and \
jargon-light, and briefly define quant terms (e.g. "Sharpe = return per unit of \
risk") the first time they come up.
- **Ground every concrete number in a tool call.** Use the tools to fetch live \
data; never invent prices, hit rates, coefficients, or weights. If a tool returns \
an error or empty result, say so honestly.
- **Teach, don't just answer.** When useful, explain *why* a number means what it \
means.
- **Be honest about limitations** and never overstate the model. When you discuss \
forecasts, expected returns, or anything that sounds like a recommendation, add a \
short reminder that this is a statistical projection from historical data, not \
financial advice or a guarantee.
- **Format for reading:** short paragraphs, bullet lists, and **bold** the key \
numbers. Don't dump giant tables unless asked.
- Keep answers focused. Lead with the answer, then the explanation.
"""


def _to_api_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Coerce inbound {role, content:str} turns into the Messages API shape."""
    out: list[dict[str, Any]] = []
    for m in history:
        role = m.get("role")
        content = m.get("content", "")
        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        out.append({"role": role, "content": content})
    return out


async def stream_chat(
    session: AsyncSession, history: list[dict[str, Any]]
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield streaming events for one assistant turn.

    Events: {"type": "text", "text": str} for token deltas,
            {"type": "tool", "name": str} when a tool is invoked,
            {"type": "done"} at the end, {"type": "error", "message": str}.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        yield {"type": "error", "message": "AI assistant not configured (missing ANTHROPIC_API_KEY)."}
        return

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    messages = _to_api_messages(history)
    if not messages:
        yield {"type": "error", "message": "No message to respond to."}
        return

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            async with client.messages.stream(
                model=MODEL,
                max_tokens=8000,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
                ],
                messages=messages,
                tools=TOOLS,
                thinking={"type": "adaptive"},
            ) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and getattr(event.delta, "type", None) == "text_delta"
                    ):
                        yield {"type": "text", "text": event.delta.text}
                final = await stream.get_final_message()

            # Preserve the full assistant turn (incl. thinking + tool_use blocks).
            messages.append({"role": "assistant", "content": final.content})

            if final.stop_reason != "tool_use":
                break

            tool_results = []
            for block in final.content:
                if block.type == "tool_use":
                    yield {"type": "tool", "name": block.name}
                    result = await dispatch(session, block.name, dict(block.input or {}))
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
        yield {"type": "done"}
    except Exception as e:  # pragma: no cover - network/runtime guard
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
