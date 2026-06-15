/**
 * Fetches latest stock snapshots from Alpaca Data API.
 * Snapshot includes: latestTrade (current price) + dailyBar (open) → calculates % change.
 */

import { NextResponse } from "next/server";

const ALPACA_DATA = "https://data.alpaca.markets";

const TICKERS = [
  "AMZN", "BA", "CAT", "CRM", "GOOGL", "NVDA", "QCOM", "V", "XOM",
  "MDLZ", "CL", "KHC", "KMB", "HSY", "AAL",
];

export async function GET() {
  const key    = process.env.ALPACA_API_KEY    ?? "";
  const secret = process.env.ALPACA_SECRET_KEY ?? "";

  if (!key || !secret) {
    return NextResponse.json({ error: "Missing keys" }, { status: 500 });
  }

  const url = `${ALPACA_DATA}/v2/stocks/snapshots?symbols=${TICKERS.join(",")}&feed=iex`;

  try {
    const res = await fetch(url, {
      headers: {
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
      },
      next: { revalidate: 60 },
    });

    if (!res.ok) {
      return NextResponse.json({ error: await res.text() }, { status: res.status });
    }

    const data: Record<string, {
      latestTrade: { p: number };
      dailyBar:    { o: number; c: number };
      prevDailyBar: { c: number };
    }> = await res.json();

    const quotes = TICKERS
      .filter((t) => data[t]?.latestTrade)
      .map((symbol) => {
        const snap   = data[symbol];
        const price  = snap.latestTrade.p;
        // Use previous day close as baseline for % change
        const prev   = snap.prevDailyBar?.c ?? snap.dailyBar?.o ?? price;
        const change = prev > 0 ? ((price - prev) / prev) * 100 : 0;
        return { symbol, price, change };
      });

    return NextResponse.json({ quotes });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
