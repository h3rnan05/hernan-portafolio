/**
 * Portfolio detail — current weights + historical evolution.
 *
 * Chart shows how each ticker's weight in this profile drifted over the
 * last N days. The /portfolios route lists all profiles; clicking one
 * lands here.
 */

import { getLocale } from "next-intl/server";
import Link from "next/link";
import { notFound } from "next/navigation";

import { TimeSeriesChart } from "@/components/charts";
import {
  Badge,
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  fmtPct,
  SectionHeader,
} from "@/components/primitives";
import { api, ApiError, type PortfolioSnapshot } from "@/lib/api";

export const dynamic = "force-dynamic";

const SERIES_COLORS = [
  "var(--color-green)",
  "var(--color-cyan)",
  "var(--color-blue)",
  "var(--color-violet)",
  "var(--color-amber)",
  "var(--color-red)",
  "var(--color-green-dim)",
  "var(--color-text2)",
  "var(--color-text3)",
];

export default async function PortfolioDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const locale = await getLocale();

  let portfolio;
  try {
    portfolio = await api.getPortfolio(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }

  const history: PortfolioSnapshot[] = await api
    .getPortfolioHistory(id, 90)
    .catch(() => []);

  // Sort tickers in the current portfolio by weight desc for chart legend
  const tickers = Object.entries(portfolio.weights)
    .sort((a, b) => b[1] - a[1])
    .map(([t]) => t);

  // Build chart data: one row per snapshot, one column per ticker (weight %)
  const chartData = history.map((snap) => {
    const row: Record<string, string | number | null> = {
      date: snap.snapshotted_at.slice(0, 10),
    };
    for (const t of tickers) {
      row[t] = (snap.weights[t] ?? 0) * 100;
    }
    return row;
  });

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-2">
        <Link
          href="/portfolios"
          className="text-[12px] text-[var(--color-text3)] hover:text-[var(--color-text2)]"
        >
          ← All profiles
        </Link>
      </div>
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            Portfolio · {portfolio.id}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {portfolio.name}
          </h1>
          {portfolio.description && (
            <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
              {portfolio.description}
            </p>
          )}
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
            MAPE 30d
          </div>
          <div className="mt-0.5 font-mono tabular text-base font-semibold">
            {portfolio.mape_30d !== null ? fmtPct(portfolio.mape_30d) : "—"}
          </div>
        </div>
      </div>

      {/* Current weights */}
      <Card className="mb-6">
        <SectionHeader
          eyebrow="Current"
          title="Active weights"
          description="Most recent rebuild of this risk profile."
        />
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-3">
          {tickers.map((t, i) => (
            <div
              key={t}
              className="flex items-center justify-between gap-2 text-[12.5px]"
            >
              <span className="inline-flex items-center gap-2 font-mono text-[var(--color-text2)]">
                <span
                  className="inline-block size-1.5 rounded-full"
                  style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }}
                />
                {t}
              </span>
              <span className="font-mono tabular text-[var(--color-text)]">
                {fmtNumber(portfolio.weights[t] * 100, { decimals: 2 })}%
              </span>
            </div>
          ))}
        </div>
      </Card>

      {/* History */}
      <Card>
        <SectionHeader
          eyebrow={`${chartData.length} snapshots`}
          title="Weight evolution"
          description="How the algorithm has reallocated capital between holdings over time. Each line is one ticker."
        />
        {chartData.length < 2 ? (
          <EmptyState
            title={
              chartData.length === 0
                ? "No snapshots yet"
                : "Just one snapshot so far"
            }
            description="History accumulates one row per cron run (daily). The line chart unlocks after ~3 days of snapshots."
          />
        ) : (
          <TimeSeriesChart
            data={chartData}
            series={tickers.map((t, i) => ({
              key: t,
              label: t,
              color: SERIES_COLORS[i % SERIES_COLORS.length],
            }))}
            yDecimals={1}
            yUnit="%"
            height={360}
            locale={locale}
          />
        )}
      </Card>

      {history.length > 0 && (
        <div className="mt-4 text-[11px] text-[var(--color-text3)]">
          First snapshot {fmtDate(history[0].snapshotted_at, locale)} · latest{" "}
          {fmtDate(history[history.length - 1].snapshotted_at, locale)}
        </div>
      )}
    </div>
  );
}
