/**
 * Fetches bot account equity history from Alpaca + S&P 500 from Yahoo Finance.
 * Normalizes both series to base-100 at the first data point so they're
 * directly comparable on the same chart.
 */

import { NextResponse } from "next/server";

const ALPACA_KEY    = process.env.ALPACA_API_KEY    ?? "";
const ALPACA_SECRET = process.env.ALPACA_SECRET_KEY ?? "";
const ALPACA_URL    = process.env.ALPACA_BASE_URL   ?? "https://paper-api.alpaca.markets";

const BOT_NAMES: Record<string, string> = {
  capitol_trades: "Capitol Trades Bot",
  ols_model:      "OLS Model Bot",
  trailing_stop:  "Trailing Stop Bot",
};

async function getAlpacaHistory(period = "3M") {
  const res = await fetch(
    `${ALPACA_URL}/v2/account/portfolio/history?period=${period}&timeframe=1D&extended_hours=false`,
    {
      headers: {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
      },
      cache: "no-store",
    },
  );
  if (!res.ok) throw new Error(`Alpaca history ${res.status}`);
  const data = await res.json() as {
    timestamp: number[];
    equity: number[];
    profit_loss: number[];
  };
  return data;
}

async function getSP500History(range = "3mo") {
  const res = await fetch(
    `https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=${range}`,
    {
      headers: { "User-Agent": "Mozilla/5.0" },
      cache: "no-store",
    },
  );
  if (!res.ok) throw new Error(`Yahoo Finance ${res.status}`);
  const data = await res.json() as {
    chart: {
      result: Array<{
        timestamp: number[];
        indicators: { quote: Array<{ close: number[] }> };
      }>;
    };
  };
  const result = data.chart.result[0];
  return result.timestamp.map((ts, i) => ({
    date: new Date(ts * 1000).toISOString().slice(0, 10),
    value: result.indicators.quote[0].close[i] ?? null,
  })).filter((p) => p.value !== null);
}

function toBase100(values: number[]): number[] {
  const first = values.find((v) => v && v > 0);
  if (!first) return values;
  return values.map((v) => (v && v > 0 ? (v / first) * 100 : 0));
}

export async function GET() {
  try {
    const [alpaca, sp500] = await Promise.all([
      getAlpacaHistory("3M"),
      getSP500History("3mo"),
    ]);

    // Build Alpaca date→equity map (skip zero-equity days at the start)
    const alpacaRows = alpaca.timestamp
      .map((ts, i) => ({
        date: new Date(ts * 1000).toISOString().slice(0, 10),
        equity: alpaca.equity[i] ?? 0,
        pl: alpaca.profit_loss[i] ?? 0,
      }))
      .filter((r) => r.equity > 0);

    // Normalize Alpaca equity to base-100
    const alpacaBase = alpacaRows[0]?.equity ?? 25000;
    const alpacaNorm = alpacaRows.map((r) => ({
      date:      r.date,
      equity:    r.equity,
      equityIdx: (r.equity / alpacaBase) * 100,
      pl:        r.pl,
    }));

    // Normalize S&P 500 to base-100 starting from the same window
    const sp500Base = sp500[0]?.value ?? 1;
    const sp500Norm = sp500.map((r) => ({
      date:    r.date,
      sp500:   r.value,
      sp500Idx: ((r.value as number) / sp500Base) * 100,
    }));

    // Merge on date
    const sp500Map = new Map(sp500Norm.map((r) => [r.date, r]));
    const allDates = Array.from(
      new Set([
        ...alpacaNorm.map((r) => r.date),
        ...sp500Norm.map((r) => r.date),
      ]),
    ).sort();

    let lastAlpaca: number | null = null;
    let lastSP500: number | null  = null;

    const merged = allDates.map((date) => {
      const a = alpacaNorm.find((r) => r.date === date);
      const s = sp500Map.get(date);
      if (a) lastAlpaca = a.equityIdx;
      if (s) lastSP500  = s.sp500Idx;
      return {
        date,
        account:  lastAlpaca,
        sp500:    lastSP500,
        equity:   a?.equity ?? null,
        pl:       a?.pl ?? null,
      };
    });

    // Current account stats
    const latest = alpacaNorm.at(-1);
    const startEquity = alpacaBase;
    const currentEquity = latest?.equity ?? startEquity;
    const totalReturn = ((currentEquity - startEquity) / startEquity) * 100;

    return NextResponse.json({
      series:        merged,
      startEquity,
      currentEquity,
      totalReturn,
      botNames:      BOT_NAMES,
      fetchedAt:     new Date().toISOString(),
      hasRealData:   alpacaNorm.length > 0,
    });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
