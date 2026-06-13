/**
 * Server-side proxy for Alpaca paper trading data.
 * Keys never reach the browser — only called from Next.js server.
 */

import { NextResponse } from "next/server";

const ALPACA_KEY    = process.env.ALPACA_API_KEY    ?? "";
const ALPACA_SECRET = process.env.ALPACA_SECRET_KEY ?? "";
const ALPACA_URL    = process.env.ALPACA_BASE_URL   ?? "https://paper-api.alpaca.markets";
const BACKEND_URL   = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TICKERS = ["AMZN", "BA", "CAT", "CRM", "GOOGL", "NVDA", "QCOM", "V", "XOM"];

const alpacaHeaders = {
  "APCA-API-KEY-ID":     ALPACA_KEY,
  "APCA-API-SECRET-KEY": ALPACA_SECRET,
  "Content-Type":        "application/json",
};

async function alpacaFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${ALPACA_URL}/v2${path}`, {
    headers: alpacaHeaders,
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Alpaca ${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export async function GET() {
  try {
    const [account, positions, orders] = await Promise.all([
      alpacaFetch<Record<string, unknown>>("/account"),
      alpacaFetch<Record<string, unknown>[]>("/positions"),
      alpacaFetch<Record<string, unknown>[]>("/orders?status=all&limit=20&direction=desc"),
    ]);

    // Fetch today's signals from the portfolio backend
    const signals = await Promise.all(
      TICKERS.map(async (ticker) => {
        try {
          const r = await fetch(`${BACKEND_URL}/predictions/${ticker}`, { cache: "no-store" });
          if (!r.ok) return null;
          const data = (await r.json()) as { points: { predicted_price: number; actual_price: number | null }[] };
          const pts = data.points ?? [];
          const future = pts.slice().reverse().find((p) => p.actual_price === null);
          const lastActual = pts.slice().reverse().find((p) => p.actual_price !== null);
          if (!future || !lastActual) return null;
          const delta = (future.predicted_price - lastActual.actual_price!) / lastActual.actual_price! * 100;
          return {
            ticker,
            currentPrice:   lastActual.actual_price,
            predictedPrice: future.predicted_price,
            deltaPct:       Math.round(delta * 10000) / 10000,
            action:         delta > 0.3 ? "BUY" : delta < 0 ? "SELL" : "HOLD",
          };
        } catch {
          return null;
        }
      }),
    );

    return NextResponse.json({
      account,
      positions,
      orders,
      signals: signals.filter(Boolean),
      fetchedAt: new Date().toISOString(),
    });
  } catch (err) {
    return NextResponse.json(
      { error: String(err) },
      { status: 500 },
    );
  }
}
