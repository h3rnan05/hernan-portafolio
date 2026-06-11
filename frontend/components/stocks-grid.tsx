"use client";

/**
 * Stocks grid — the 9 tracked names as cards with last close + 60-day sparkline.
 *
 * Originally lived on the Overview page (server-rendered). Moved to the Holdings
 * page (HER tasks 2 & 3); fetches client-side so it can drop into the existing
 * client Holdings page without a server round-trip.
 */

import Link from "next/link";

import { Sparkline } from "@/components/charts";
import {
  Badge,
  EmptyState,
  fmtNumber,
  fmtPct,
} from "@/components/primitives";
import { StocksGridSkeleton } from "@/components/skeleton";
import { useCached } from "@/hooks/use-cached";
import { api, type Observation, type Variable } from "@/lib/api";

type StockSeries = {
  ticker: string;
  display: string;
  last: number | null;
  series: Observation[];
};

function daysAgoISO(d: number): string {
  const dt = new Date();
  dt.setUTCDate(dt.getUTCDate() - d);
  return dt.toISOString().slice(0, 10);
}

async function fetchStocksGrid(): Promise<StockSeries[]> {
  const stocks: Variable[] = await api.listVariables("stock");
  return Promise.all(
    stocks.map(async (s) => {
      let series: Observation[] = [];
      try {
        series = await api.getObservations(s.id, {
          from: daysAgoISO(60),
          limit: 60,
        });
      } catch {
        series = [];
      }
      return {
        ticker: s.id,
        display: s.display_name,
        last: s.last_value,
        series,
      };
    }),
  );
}

export function StocksGrid() {
  // SWR cache → instant on revisits, background refresh, skeleton only on cold.
  const { data, isCold } = useCached("stocks-grid", fetchStocksGrid);

  if (isCold) {
    return <StocksGridSkeleton />;
  }

  if (!data || data.length === 0) {
    return (
      <EmptyState
        title="No stocks loaded"
        description="Seed the variables registry first."
      />
    );
  }

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
      {data.map((s) => (
        <StockCard key={s.ticker} {...s} />
      ))}
    </div>
  );
}

function StockCard({ ticker, display, last, series }: StockSeries) {
  const values = series.map((o) => o.value);
  const first = values[0];
  const lastVal = values[values.length - 1] ?? last;
  const pct =
    first != null && lastVal != null && first !== 0
      ? (lastVal - first) / first
      : null;
  return (
    <Link
      href={`/models/${ticker}`}
      className="group block rounded-[14px] bg-[var(--color-bg2)] p-4 transition-colors duration-150 hover:bg-[var(--color-bg3)] focus-visible:bg-[var(--color-bg3)] shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_1px_2px_rgba(0,0,0,0.3)]"
    >
      <div className="flex items-start justify-between">
        <div>
          <div className="font-mono text-[15px] font-semibold tracking-tight">
            {ticker}
          </div>
          <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">
            {display}
          </div>
        </div>
        {pct !== null && (
          <Badge tone={pct >= 0 ? "green" : "red"}>
            {fmtPct(pct, { signed: true, decimals: 2 })}
          </Badge>
        )}
      </div>
      <div className="mt-4 flex items-end justify-between gap-3">
        <div className="font-mono tabular text-xl font-semibold">
          {last != null ? `$${fmtNumber(last, { decimals: 2 })}` : "—"}
        </div>
        <Sparkline values={values} width={96} height={32} />
      </div>
    </Link>
  );
}
