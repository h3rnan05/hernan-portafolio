/**
 * Server-side proxy for Alpaca paper trading data.
 * Accepts ?bot=ols|capitol|trailing to select which bot's account to query.
 * Keys never reach the browser.
 */

import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const ALPACA_BASE = "https://paper-api.alpaca.markets";
const TICKERS     = ["AMZN", "BA", "CAT", "CRM", "GOOGL", "NVDA", "QCOM", "V", "XOM"];

const BOTS = {
  ols: {
    key:    process.env.ALPACA_API_KEY    ?? "",
    secret: process.env.ALPACA_SECRET_KEY ?? "",
    label:  "OLS Model Bot",
  },
  capitol: {
    key:    process.env.ALPACA_API_KEY_2    ?? "",
    secret: process.env.ALPACA_SECRET_KEY_2 ?? "",
    label:  "Capitol Trades Bot",
  },
  trailing: {
    key:    process.env.ALPACA_API_KEY_3    ?? "",
    secret: process.env.ALPACA_SECRET_KEY_3 ?? "",
    label:  "P0 Ultra Conservador Bot",
  },
} as const;

type BotKey = keyof typeof BOTS;

function alpacaHeaders(key: string, secret: string) {
  return {
    "APCA-API-KEY-ID":     key,
    "APCA-API-SECRET-KEY": secret,
    "Content-Type":        "application/json",
  };
}

async function alpacaFetch<T>(path: string, key: string, secret: string): Promise<T> {
  const res = await fetch(`${ALPACA_BASE}/v2${path}`, {
    headers: alpacaHeaders(key, secret),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Alpaca ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function GET(req: NextRequest) {
  const botParam = (req.nextUrl.searchParams.get("bot") ?? "ols") as BotKey;
  const bot = BOTS[botParam] ?? BOTS.ols;

  try {
    const [account, positions, orders] = await Promise.all([
      alpacaFetch<Record<string, unknown>>("/account", bot.key, bot.secret),
      alpacaFetch<Record<string, unknown>[]>("/positions", bot.key, bot.secret),
      alpacaFetch<Record<string, unknown>[]>("/orders?status=all&limit=30&direction=desc", bot.key, bot.secret),
    ]);

    // Signals only apply to the OLS bot (driven by the model predictions)
    let signals: unknown[] = [];
    if (botParam === "ols") {
      signals = (await Promise.all(
        TICKERS.map(async (ticker) => {
          try {
            const r = await fetch(`${BACKEND_URL}/predictions/${ticker}`, { cache: "no-store" });
            if (!r.ok) return null;
            const data = (await r.json()) as { points: { predicted_price: number; actual_price: number | null }[] };
            const pts    = data.points ?? [];
            const future = pts.slice().reverse().find((p) => p.actual_price === null);
            const last   = pts.slice().reverse().find((p) => p.actual_price !== null);
            if (!future || !last) return null;
            const delta = (future.predicted_price - last.actual_price!) / last.actual_price! * 100;
            return {
              ticker,
              currentPrice:   last.actual_price,
              predictedPrice: future.predicted_price,
              deltaPct:       Math.round(delta * 10000) / 10000,
              action:         delta > 0.3 ? "BUY" : delta < 0 ? "SELL" : "HOLD",
            };
          } catch { return null; }
        }),
      )).filter(Boolean);
    }

    return NextResponse.json({
      bot:       botParam,
      botLabel:  bot.label,
      account,
      positions,
      orders,
      signals,
      fetchedAt: new Date().toISOString(),
    });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
