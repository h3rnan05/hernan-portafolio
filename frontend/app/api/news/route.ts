/**
 * Server-side proxy for Alpaca News API.
 * Uses KEY_1 (OLS bot) — news is not account-specific.
 * Returns articles for all portfolio tickers combined.
 */

import { NextRequest, NextResponse } from "next/server";

const ALPACA_DATA = "https://data.alpaca.markets";

// OLS bot stocks
const OLS_TICKERS = ["AMZN", "BA", "CAT", "CRM", "GOOGL", "NVDA", "QCOM", "V", "XOM"];
// P0 Ultra Conservador stocks
const P0_TICKERS  = ["MDLZ", "CL", "KHC", "KMB", "HSY", "AAL"];

export const ALL_TICKERS = [...new Set([...OLS_TICKERS, ...P0_TICKERS])];

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const portfolio = searchParams.get("portfolio") ?? "all";
  const limit     = Math.min(parseInt(searchParams.get("limit") ?? "50"), 50);

  const tickers =
    portfolio === "ols" ? OLS_TICKERS :
    portfolio === "p0"  ? P0_TICKERS  :
    ALL_TICKERS;

  const key    = process.env.ALPACA_API_KEY    ?? "";
  const secret = process.env.ALPACA_SECRET_KEY ?? "";

  if (!key || !secret) {
    return NextResponse.json({ error: "Alpaca keys not configured" }, { status: 500 });
  }

  const url = `${ALPACA_DATA}/v1beta1/news?symbols=${tickers.join(",")}&limit=${limit}&sort=desc&include_content=false`;

  try {
    const res = await fetch(url, {
      headers: {
        "APCA-API-KEY-ID":     key,
        "APCA-API-SECRET-KEY": secret,
      },
      next: { revalidate: 300 }, // cache 5 min
    });

    if (!res.ok) {
      const text = await res.text();
      return NextResponse.json({ error: text }, { status: res.status });
    }

    const data = await res.json();
    return NextResponse.json({
      news: data.news ?? [],
      tickers,
    });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
