/**
 * Overview — the dashboard homepage.
 *
 * Renders system-wide state at a glance:
 *   * health + last ingestion freshness
 *   * the 9 stocks with their last close + sparkline
 *   * the 5 risk-profile portfolios with MAPE + weights
 *   * the model coverage status
 *
 * Server-rendered with parallel fetches; client polling layer would be
 * added per-section if/when latency budgets require it.
 */

import Link from "next/link";

import { AreaChartCmp, Sparkline } from "@/components/charts";
import { StatusBadge } from "@/components/diagnostics";
import {
  Badge,
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  fmtPct,
  fmtRelative,
  SectionHeader,
  StatTile,
} from "@/components/primitives";
import { api, ApiError, type Observation, type Variable } from "@/lib/api";

export const dynamic = "force-dynamic";
export const revalidate = 0;

type Safe<T> = { ok: true; data: T } | { ok: false; error: string };

async function safe<T>(fn: () => Promise<T>): Promise<Safe<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    return {
      ok: false,
      error: e instanceof ApiError ? e.message : String(e),
    };
  }
}

export default async function Overview() {
  const [healthR, varsR, modelsR, portfoliosR, projectionR] = await Promise.all([
    safe(() => api.health()),
    safe(() => api.listVariables()),
    safe(() => api.listModels(true)),
    safe(() => api.listPortfolios()),
    safe(() => api.getHoldingsProjection()),
  ]);

  // ─── Backend unreachable → block render with a clear message ──────────────
  if (!healthR.ok) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-16">
        <Card>
          <div className="flex items-start gap-4">
            <span className="mt-1 inline-block size-2 rounded-full bg-[var(--color-red)]" />
            <div>
              <h1 className="text-base font-semibold">Backend unreachable</h1>
              <p className="mt-1 text-[13px] text-[var(--color-text2)]">
                The API isn&rsquo;t responding. Start it locally with{" "}
                <code className="text-[var(--color-cyan)]">
                  cd backend &amp;&amp; uv run uvicorn app.main:app --reload
                </code>{" "}
                or check the deploy.
              </p>
              <p className="mt-2 font-mono text-[11px] text-[var(--color-text3)]">
                {healthR.error}
              </p>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  const variables = varsR.ok ? varsR.data : [];
  const models = modelsR.ok ? modelsR.data : [];
  const portfolios = portfoliosR.ok ? portfoliosR.data : [];

  const stocks = variables.filter((v) => v.kind === "stock");
  const predictors = variables.filter((v) => v.kind === "predictor");
  const withData = variables.filter((v) => v.last_observed_on).length;
  const latestObs = variables
    .map((v) => v.last_observed_on)
    .filter((d): d is string => !!d)
    .sort()
    .pop();

  // Build sparklines server-side for the 9 stocks (cheap: 30-day windows)
  const stockSparklines = await Promise.all(
    stocks.map(async (s) => {
      const obs = await safe(() =>
        api.getObservations(s.id, {
          from: daysAgoISO(60),
          limit: 60,
        }),
      );
      return {
        ticker: s.id,
        display: s.display_name,
        last: s.last_value,
        series: obs.ok ? obs.data : ([] as Observation[]),
      };
    }),
  );

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      {/* Page header */}
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            Live · v{healthR.data.version}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Portfolio prediction engine
          </h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            Lagged OLS regression on 9 stocks against ~30 macro &amp; market
            predictors. Daily ingestion · weekly refit · four-test diagnostic
            battery before any model goes live.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="inline-flex items-center gap-2 rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1 text-[11px] text-[var(--color-text2)]"
            title={latestObs ?? undefined}
          >
            <span
              className={
                "size-1.5 rounded-full " +
                (latestObs
                  ? "bg-[var(--color-green)]"
                  : "bg-[var(--color-amber)]")
              }
            />
            <span className="font-medium">
              {latestObs ? `Data thru ${fmtDate(latestObs)}` : "No data yet"}
            </span>
            {latestObs && (
              <span className="text-[var(--color-text3)]">
                · {fmtRelative(latestObs)}
              </span>
            )}
          </span>
        </div>
      </div>

      {/* Top metric strip */}
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label="Variables tracked"
          value={`${withData} / ${variables.length}`}
          hint={`${predictors.length} predictors · ${stocks.length} stocks`}
        />
        <StatTile
          label="Active models"
          value={`${models.filter((m) => m.is_active && m.status === "PASS").length} / ${stocks.length}`}
          hint={
            models.length === 0
              ? "No fits yet — run refit_all"
              : `${models.filter((m) => m.status === "PASS").length} pass diagnostics`
          }
        />
        <StatTile
          label="Risk profiles"
          value={portfolios.length || "—"}
          hint={
            portfolios.length === 0
              ? "Computed after first refit"
              : "P1 conservative → P5 aggressive"
          }
        />
        <StatTile
          label="Best MAPE 30d"
          value={
            portfolios.some((p) => p.mape_30d !== null)
              ? fmtPct(
                  Math.min(
                    ...portfolios
                      .map((p) => p.mape_30d)
                      .filter((m): m is number => m !== null),
                  ),
                )
              : "—"
          }
          hint="Lower is better"
        />
      </div>

      {/* Portfolio rollup — chart for top-MAPE profile when data exists */}
      <section className="mb-10">
        <SectionHeader
          eyebrow="Portfolio"
          title="Predicted vs. realized value"
          description="The active model fleet, rolled up across the 5 risk profiles."
          right={
            <Link
              href="/portfolios"
              className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
            >
              All profiles →
            </Link>
          }
        />
        {portfolios.length === 0 ? (
          <EmptyState
            title="Portfolio weights not computed yet"
            description="Run the daily prediction pipeline once models are fit — weights get rebuilt automatically."
          />
        ) : (
          <PortfolioRollupCard portfolios={portfolios} />
        )}
      </section>

      {/* Your portfolio vs predictions */}
      {projectionR.ok && projectionR.data.rows.length > 0 && (
        <section className="mb-10">
          <SectionHeader
            eyebrow="Your portfolio"
            title="Today's position vs tomorrow's prediction"
            description="Your real holdings priced today, vs. what the active models predict for each ticker. Delta is what you'd see if the predictions play out."
            right={
              <Link
                href="/holdings"
                className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
              >
                Edit holdings →
              </Link>
            }
          />
          <YourPortfolioCard projection={projectionR.data} />
        </section>
      )}

      {/* Stocks grid — 9 cards, current close + sparkline */}
      <section className="mb-10">
        <SectionHeader
          eyebrow="Stocks"
          title="Active holdings"
          description="The 9 names this engine predicts. Each card links to its model + history."
        />
        {stocks.length === 0 ? (
          <EmptyState
            title="No stocks loaded"
            description="Seed the variables registry first."
          />
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-3">
            {stockSparklines.map((s) => (
              <StockCard
                key={s.ticker}
                ticker={s.ticker}
                display={s.display}
                last={s.last}
                series={s.series}
              />
            ))}
          </div>
        )}
      </section>

      {/* Coverage status — predictor health */}
      <section className="mb-10">
        <SectionHeader
          eyebrow="Predictors"
          title="Coverage status"
          description="Which predictors are populated. The model layer needs the macro variables to fit meaningful equations."
          right={
            <Link
              href="/variables"
              className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
            >
              All data →
            </Link>
          }
        />
        <PredictorTable variables={predictors} />
      </section>
    </div>
  );
}

// ─── Subcomponents ──────────────────────────────────────────────────────────

function StockCard({
  ticker,
  display,
  last,
  series,
}: {
  ticker: string;
  display: string;
  last: number | null;
  series: Observation[];
}) {
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

function YourPortfolioCard({
  projection,
}: {
  projection: {
    rows: Array<{
      ticker: string;
      quantity: number;
      last_price: number;
      market_value: number;
      open_pnl_pct: number;
      predicted_price: number | null;
      predicted_pnl_delta: number | null;
    }>;
    current_market_value: number;
    projected_market_value: number;
    projected_delta: number;
    projected_delta_pct: number | null;
  };
}) {
  const totalPredCovered = projection.rows.filter(
    (r) => r.predicted_price !== null,
  ).length;
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_2fr]">
      <Card>
        <SectionHeader
          eyebrow="Rollup"
          title="Where you stand"
          description={
            totalPredCovered === projection.rows.length
              ? "All holdings have a live prediction."
              : `${totalPredCovered} of ${projection.rows.length} holdings have a live prediction; others use current price.`
          }
        />
        <div className="space-y-3">
          <StatTile
            label="Current market value"
            value={`$${fmtNumber(projection.current_market_value, { decimals: 2 })}`}
            hint={`${projection.rows.length} positions`}
          />
          <StatTile
            label="Projected (next trading day)"
            value={`$${fmtNumber(projection.projected_market_value, { decimals: 2 })}`}
            delta={
              projection.projected_delta_pct !== null
                ? {
                    value: fmtPct(projection.projected_delta_pct, {
                      signed: true,
                      decimals: 3,
                    }),
                    tone: projection.projected_delta >= 0 ? "green" : "red",
                  }
                : undefined
            }
            hint={
              projection.projected_delta >= 0
                ? `+$${fmtNumber(projection.projected_delta, { decimals: 2 })} if predictions play out`
                : `-$${fmtNumber(Math.abs(projection.projected_delta), { decimals: 2 })} if predictions play out`
            }
          />
        </div>
      </Card>
      <Card>
        <SectionHeader
          eyebrow="Per-ticker"
          title="Holding vs prediction"
          description="Each row: today's market price vs. the model's next-day prediction for that ticker."
        />
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                <th className="py-2 pr-3 font-medium">Ticker</th>
                <th className="py-2 pr-3 text-right font-medium">Qty</th>
                <th className="py-2 pr-3 text-right font-medium">Last</th>
                <th className="py-2 pr-3 text-right font-medium">Predicted</th>
                <th className="py-2 pr-3 text-right font-medium">Δ MV</th>
              </tr>
            </thead>
            <tbody>
              {projection.rows.map((r) => (
                <tr
                  key={r.ticker}
                  className="border-b border-[var(--color-border)] last:border-0"
                >
                  <td className="py-2 pr-3 font-mono font-medium">{r.ticker}</td>
                  <td className="py-2 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                    {fmtNumber(r.quantity, { decimals: 0 })}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono tabular">
                    ${fmtNumber(r.last_price, { decimals: 2 })}
                  </td>
                  <td className="py-2 pr-3 text-right font-mono tabular">
                    {r.predicted_price !== null
                      ? `$${fmtNumber(r.predicted_price, { decimals: 2 })}`
                      : <span className="text-[var(--color-text3)]">—</span>}
                  </td>
                  <td className="py-2 pr-3 text-right">
                    {r.predicted_pnl_delta !== null ? (
                      <Badge tone={r.predicted_pnl_delta >= 0 ? "green" : "red"}>
                        {r.predicted_pnl_delta >= 0 ? "+" : "-"}$
                        {fmtNumber(Math.abs(r.predicted_pnl_delta), {
                          decimals: 2,
                        })}
                      </Badge>
                    ) : (
                      <span className="text-[var(--color-text3)]">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function PortfolioRollupCard({
  portfolios,
}: {
  portfolios: Array<{
    id: string;
    name: string;
    weights: Record<string, number>;
    mape_30d: number | null;
  }>;
}) {
  // No prediction history yet → degrade gracefully but show weights
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[2fr_1fr]">
      <Card>
        <SectionHeader
          eyebrow="Most-tracked profile"
          title={portfolios[2]?.name ?? portfolios[0]?.name ?? "—"}
          description="Predicted portfolio value vs. realized actuals. Filled once daily predictions backfill."
        />
        <PortfolioMiniChart portfolioId={portfolios[2]?.id ?? portfolios[0]?.id} />
      </Card>
      <Card>
        <SectionHeader
          eyebrow="Ranked"
          title="By 30-day MAPE"
          description="Lower error = better."
        />
        <ul className="space-y-2">
          {[...portfolios]
            .sort((a, b) => {
              if (a.mape_30d === null) return 1;
              if (b.mape_30d === null) return -1;
              return a.mape_30d - b.mape_30d;
            })
            .map((p, i) => (
              <li
                key={p.id}
                className="flex items-center justify-between rounded-[8px] bg-[var(--color-bg3)] px-3 py-2 text-[12px]"
              >
                <span className="flex items-center gap-2">
                  <span className="font-mono tabular text-[var(--color-text3)]">
                    #{i + 1}
                  </span>
                  <span className="font-medium text-[var(--color-text)]">
                    {p.name}
                  </span>
                </span>
                <span className="font-mono tabular text-[var(--color-text2)]">
                  {p.mape_30d !== null ? fmtPct(p.mape_30d) : "no data"}
                </span>
              </li>
            ))}
        </ul>
      </Card>
    </div>
  );
}

async function PortfolioMiniChart({ portfolioId }: { portfolioId?: string }) {
  if (!portfolioId)
    return (
      <div className="py-12 text-center text-[12px] text-[var(--color-text3)]">
        No portfolio selected.
      </div>
    );
  const data = await safe(() => api.getPortfolioPredictions(portfolioId, 60));
  if (!data.ok || data.data.points.length === 0)
    return (
      <div className="py-12 text-center text-[12px] text-[var(--color-text3)]">
        No prediction history yet.
      </div>
    );
  const chartData = data.data.points.map((p) => ({
    date: p.predicted_for,
    predicted: p.predicted_value,
    actual: p.actual_value,
  }));
  return (
    <AreaChartCmp
      data={chartData}
      dataKey="predicted"
      label="Predicted"
      yUnit=""
      yDecimals={2}
      height={200}
    />
  );
}

function PredictorTable({ variables }: { variables: Variable[] }) {
  // Group by category for scannability
  const grouped = new Map<string, Variable[]>();
  for (const v of variables) {
    const key = v.category ?? "Other";
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key)!.push(v);
  }
  const cats = Array.from(grouped.keys()).sort();

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {cats.map((cat) => (
        <Card key={cat}>
          <div className="mb-2 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {cat}
          </div>
          <ul className="divide-y divide-[var(--color-border)]">
            {grouped.get(cat)!.map((v) => (
              <li
                key={v.id}
                className="flex items-center justify-between gap-3 py-2 text-[12.5px]"
              >
                <Link
                  href={`/variables/${encodeURIComponent(v.id)}`}
                  className="truncate font-medium hover:text-[var(--color-cyan)]"
                  title={v.display_name}
                >
                  {v.display_name}
                </Link>
                {v.last_observed_on ? (
                  <span className="flex shrink-0 items-center gap-2 text-[var(--color-text3)]">
                    <span className="font-mono tabular text-[var(--color-text2)]">
                      {fmtNumber(v.last_value, { decimals: 2 })}
                    </span>
                    <Badge tone="green">live</Badge>
                  </span>
                ) : (
                  <Badge tone="amber">no data</Badge>
                )}
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function daysAgoISO(d: number): string {
  const dt = new Date();
  dt.setUTCDate(dt.getUTCDate() - d);
  return dt.toISOString().slice(0, 10);
}
