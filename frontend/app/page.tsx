/**
 * Overview — the dashboard homepage.
 *
 * Renders system-wide state at a glance:
 *   * health + last ingestion freshness
 *   * the 5 risk-profile portfolios with MAPE + weights
 *   * your holdings vs predictions
 *   * the portfolio-vs-benchmark comparison
 *
 * Server-rendered with parallel fetches; strings come from next-intl.
 */

import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";

import { AreaChartCmp } from "@/components/charts";
import { PortfolioComparison } from "@/components/portfolio-comparison";
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
import { api, ApiError } from "@/lib/api";

// Heavy daily-data fetches are cached in Next's Data Cache (revalidate=3600 in
// lib/api). The page revalidates hourly; live bits (health, holdings) use
// no-store and keep the route dynamic, so they stay fresh.
export const revalidate = 3600;

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

/** Translate a profile id ("P3_BALANCED") to its localized name via its P# code. */
function profileName(id: string, tp: (key: string) => string): string {
  const code = id.split("_")[0];
  try {
    return tp(`${code}.name`);
  } catch {
    return id;
  }
}

export default async function Overview() {
  const t = await getTranslations("overview");
  const tp = await getTranslations("profiles");
  const tc = await getTranslations("common");
  const locale = await getLocale();

  const [healthR, varsR, portfoliosR, projectionR] = await Promise.all([
    safe(() => api.health()),
    safe(() => api.listVariables()),
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
              <h1 className="text-base font-semibold">
                {t("backend_unreachable_title")}
              </h1>
              <p className="mt-1 text-[13px] text-[var(--color-text2)]">
                {t.rich("backend_unreachable_desc", {
                  code: (chunks) => (
                    <code className="text-[var(--color-cyan)]">{chunks}</code>
                  ),
                })}
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
  const portfolios = portfoliosR.ok ? portfoliosR.data : [];

  const stocks = variables.filter((v) => v.kind === "stock");
  const predictors = variables.filter((v) => v.kind === "predictor");
  // Public-facing unit = published scenario portfolios. Data-driven: derives
  // from the `is_public` flag (proposed backend migration 0008). Until that
  // field + a flagged portfolio exist, this is 0; it updates automatically as
  // portfolios are published.
  const publicPortfolios = portfolios.filter((p) => p.is_public).length;
  const withData = variables.filter((v) => v.last_observed_on).length;
  const latestObs = variables
    .map((v) => v.last_observed_on)
    .filter((d): d is string => !!d)
    .sort()
    .pop();

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      {/* Page header */}
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {t("eyebrow_live", { version: healthR.data.version })}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            {t("hero_title")}
          </h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            {t("hero_subtitle")}
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
              {latestObs
                ? t("data_thru", { date: fmtDate(latestObs, locale) })
                : t("no_data_yet")}
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
          label={t("stat_variables_tracked")}
          value={`${withData} / ${variables.length}`}
          hint={t("stat_variables_hint", {
            predictors: predictors.length,
            stocks: stocks.length,
          })}
        />
        <StatTile
          label={t("stat_public_portfolios")}
          value={publicPortfolios}
          hint={
            publicPortfolios === 0
              ? t("stat_public_portfolios_none")
              : t("stat_public_portfolios_hint")
          }
        />
        <StatTile
          label={t("stat_risk_profiles")}
          value={portfolios.length || "—"}
          hint={
            portfolios.length === 0
              ? t("stat_risk_profiles_none")
              : t("stat_risk_profiles_range")
          }
        />
        <StatTile
          label={t("stat_best_mape")}
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
          hint={tc("lower_is_better")}
        />
      </div>

      {/* Portfolio rollup — chart for top-MAPE profile when data exists */}
      <section className="mb-10">
        <SectionHeader
          eyebrow={t("section_portfolio_eyebrow")}
          title={t("section_portfolio_title")}
          description={t("section_portfolio_desc")}
          right={
            <Link
              href="/portfolios"
              className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
            >
              {tc("all_profiles")}
            </Link>
          }
        />
        {portfolios.length === 0 ? (
          <EmptyState
            title={t("empty_weights_title")}
            description={t("empty_weights_desc")}
          />
        ) : (
          <PortfolioRollupCard portfolios={portfolios} />
        )}
      </section>

      {/* Your portfolio vs predictions */}
      {projectionR.ok && projectionR.data.rows.length > 0 && (
        <section className="mb-10">
          <SectionHeader
            eyebrow={t("your_portfolio_eyebrow")}
            title={t("your_portfolio_title")}
            description={t("your_portfolio_desc")}
            right={
              <Link
                href="/holdings"
                className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
              >
                {tc("edit_holdings")}
              </Link>
            }
          />
          <YourPortfolioCard projection={projectionR.data} />
        </section>
      )}

      {/* Comparative chart — portfolio vs major index benchmarks */}
      <section className="mb-10">
        <PortfolioComparison />
      </section>
    </div>
  );
}

// ─── Subcomponents ──────────────────────────────────────────────────────────

async function YourPortfolioCard({
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
  const t = await getTranslations("overview");
  const tc = await getTranslations("common");
  const totalPredCovered = projection.rows.filter(
    (r) => r.predicted_price !== null,
  ).length;
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_2fr]">
      <Card>
        <SectionHeader
          eyebrow={t("rollup_where_eyebrow")}
          title={t("rollup_where_title")}
          description={
            totalPredCovered === projection.rows.length
              ? t("rollup_where_all")
              : t("rollup_where_partial", {
                  covered: totalPredCovered,
                  total: projection.rows.length,
                })
          }
        />
        <div className="space-y-3">
          <StatTile
            label={t("current_mv")}
            value={`$${fmtNumber(projection.current_market_value, { decimals: 2 })}`}
            hint={t("positions_count", { count: projection.rows.length })}
          />
          <StatTile
            label={t("projected_next")}
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
                ? t("projected_gain", {
                    amount: fmtNumber(projection.projected_delta, { decimals: 2 }),
                  })
                : t("projected_loss", {
                    amount: fmtNumber(Math.abs(projection.projected_delta), {
                      decimals: 2,
                    }),
                  })
            }
          />
        </div>
      </Card>
      <Card>
        <SectionHeader
          eyebrow={t("per_ticker_eyebrow")}
          title={t("per_ticker_title")}
          description={t("per_ticker_desc")}
        />
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                <th className="py-2 pr-3 font-medium">{tc("ticker")}</th>
                <th className="py-2 pr-3 text-right font-medium">{tc("qty")}</th>
                <th className="py-2 pr-3 text-right font-medium">{tc("last")}</th>
                <th className="py-2 pr-3 text-right font-medium">{tc("predicted")}</th>
                <th className="py-2 pr-3 text-right font-medium">{t("th_delta_mv")}</th>
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
                    {r.predicted_pnl_delta !== null && r.predicted_price !== null ? (
                      <div className="flex flex-col items-end gap-0.5">
                        <Badge tone={r.predicted_pnl_delta >= 0 ? "green" : "red"}>
                          {r.predicted_pnl_delta >= 0 ? "+" : "-"}$
                          {fmtNumber(Math.abs(r.predicted_pnl_delta), {
                            decimals: 2,
                          })}
                        </Badge>
                        {r.last_price > 0 && (
                          <span className="text-[10px] text-[var(--color-text3)]">
                            {fmtPct(
                              (r.predicted_price - r.last_price) / r.last_price,
                              { signed: true, decimals: 2 },
                            )}
                          </span>
                        )}
                      </div>
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

async function PortfolioRollupCard({
  portfolios,
}: {
  portfolios: Array<{
    id: string;
    name: string;
    weights: Record<string, number>;
    mape_30d: number | null;
  }>;
}) {
  const t = await getTranslations("overview");
  const tp = await getTranslations("profiles");
  const tc = await getTranslations("common");
  const headline = portfolios[2] ?? portfolios[0];
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[2fr_1fr]">
      <Card>
        <SectionHeader
          eyebrow={t("rollup_eyebrow")}
          title={headline ? profileName(headline.id, tp) : "—"}
          description={t("rollup_chart_desc")}
        />
        <PortfolioMiniChart portfolioId={headline?.id} />
      </Card>
      <Card>
        <SectionHeader
          eyebrow={t("ranked_eyebrow")}
          title={t("ranked_title")}
          description={t("ranked_desc")}
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
                    {p.id.split("_")[0]} {profileName(p.id, tp)}
                  </span>
                </span>
                <span className="font-mono tabular text-[var(--color-text2)]">
                  {p.mape_30d !== null ? fmtPct(p.mape_30d) : tc("no_data")}
                </span>
              </li>
            ))}
        </ul>
      </Card>
    </div>
  );
}

async function PortfolioMiniChart({ portfolioId }: { portfolioId?: string }) {
  const t = await getTranslations("overview");
  const tc = await getTranslations("common");
  if (!portfolioId)
    return (
      <div className="py-12 text-center text-[12px] text-[var(--color-text3)]">
        {t("no_portfolio_selected")}
      </div>
    );
  const data = await safe(() => api.getPortfolioPredictions(portfolioId, 60));
  if (!data.ok || data.data.points.length === 0)
    return (
      <div className="py-12 text-center text-[12px] text-[var(--color-text3)]">
        {t("no_prediction_history")}
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
      label={tc("predicted")}
      yUnit=""
      yDecimals={2}
      height={200}
    />
  );
}
