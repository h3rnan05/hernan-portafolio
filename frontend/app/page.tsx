/**
 * Overview — dashboard principal.
 */

import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";

import { BotComparisonChart } from "@/components/bot-comparison-chart";
import { PortfolioComparison } from "@/components/portfolio-comparison";
import { AlpacaStatsBar, OlsBotStatus, CapitolBotStatus, P0BotStatus, RecentActivity } from "@/components/overview-alpaca";
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

export const revalidate = 3600;

type Safe<T> = { ok: true; data: T } | { ok: false; error: string };

async function safe<T>(fn: () => Promise<T>): Promise<Safe<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    return { ok: false, error: e instanceof ApiError ? e.message : String(e) };
  }
}

function profileName(id: string, tp: (key: string) => string): string {
  const code = id.split("_")[0];
  try { return tp(`${code}.name`); } catch { return id; }
}

export default async function Overview() {
  const t      = await getTranslations("overview");
  const tp     = await getTranslations("profiles");
  const tc     = await getTranslations("common");
  const locale = await getLocale();

  const [healthR, varsR, portfoliosR, projectionR] = await Promise.all([
    safe(() => api.health()),
    safe(() => api.listVariables()),
    safe(() => api.listPortfolios()),
    safe(() => api.getHoldingsProjection()),
  ]);

  if (!healthR.ok) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-16">
        <Card>
          <div className="flex items-start gap-4">
            <span className="mt-1 inline-block size-2 rounded-full bg-[var(--color-red)]" />
            <div>
              <h1 className="text-base font-semibold">{t("backend_unreachable_title")}</h1>
              <p className="mt-1 text-[13px] text-[var(--color-text2)]">
                {t.rich("backend_unreachable_desc", {
                  code: (chunks) => <code className="text-[var(--color-cyan)]">{chunks}</code>,
                })}
              </p>
              <p className="mt-2 font-mono text-[11px] text-[var(--color-text3)]">{healthR.error}</p>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  const variables  = varsR.ok ? varsR.data : [];
  const portfolios = portfoliosR.ok ? portfoliosR.data : [];
  const latestObs  = variables
    .map((v) => v.last_observed_on)
    .filter((d): d is string => !!d)
    .sort()
    .pop();

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">

      {/* Header */}
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            EN VIVO · v{healthR.data.version}
          </div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
            Panel de control
          </h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            Tus 3 bots operando en Alpaca Paper Trading, las señales del modelo OLS de hoy
            y la predicción de mañana para cada acción.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-2 rounded-none bg-[var(--color-bg3)] px-2.5 py-1 text-[11px] text-[var(--color-text2)]">
            <span className={`size-1.5 rounded-full ${latestObs ? "bg-[var(--color-green)]" : "bg-[var(--color-amber)]"}`} />
            <span className="font-medium">
              {latestObs ? t("data_thru", { date: fmtDate(latestObs, locale) }) : t("no_data_yet")}
            </span>
            {latestObs && (
              <span className="text-[var(--color-text3)]">· {fmtRelative(latestObs)}</span>
            )}
          </span>
        </div>
      </div>

      {/* Stats bar — live Alpaca data */}
      <AlpacaStatsBar />

      {/* 3 bot cards */}
      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <OlsBotStatus />
        <CapitolBotStatus />
        <P0BotStatus />
      </div>

      {/* Recent activity — tabbed by bot */}
      <div className="mb-10">
        <RecentActivity />
      </div>

      {/* Bot comparison chart */}
      <section className="mb-10">
        <BotComparisonChart />
      </section>

      {/* Portfolio rollup */}
      <section className="mb-10">
        <SectionHeader
          eyebrow={t("section_portfolio_eyebrow")}
          title={t("section_portfolio_title")}
          description={t("section_portfolio_desc")}
          right={
            <Link
              href="/portfolios"
              className="rounded-none bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
            >
              {tc("all_profiles")}
            </Link>
          }
        />
        {portfolios.length === 0 ? (
          <EmptyState title={t("empty_weights_title")} description={t("empty_weights_desc")} />
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
                className="rounded-none bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
              >
                {tc("edit_holdings")}
              </Link>
            }
          />
          <YourPortfolioCard projection={projectionR.data} />
        </section>
      )}

      {/* Comparative chart */}
      <section className="mb-10">
        <PortfolioComparison />
      </section>
    </div>
  );
}

// ─── Subcomponents ────────────────────────────────────────────────────────────

async function YourPortfolioCard({ projection }: {
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
  const t  = await getTranslations("overview");
  const tc = await getTranslations("common");
  const totalPredCovered = projection.rows.filter((r) => r.predicted_price !== null).length;

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_2fr]">
      <Card>
        <SectionHeader
          eyebrow={t("rollup_where_eyebrow")}
          title={t("rollup_where_title")}
          description={
            totalPredCovered === projection.rows.length
              ? t("rollup_where_all")
              : t("rollup_where_partial", { covered: totalPredCovered, total: projection.rows.length })
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
                ? { value: fmtPct(projection.projected_delta_pct, { signed: true, decimals: 3 }), tone: projection.projected_delta >= 0 ? "green" : "red" }
                : undefined
            }
            hint={
              projection.projected_delta >= 0
                ? t("projected_gain", { amount: fmtNumber(projection.projected_delta, { decimals: 2 }) })
                : t("projected_loss", { amount: fmtNumber(Math.abs(projection.projected_delta), { decimals: 2 }) })
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
                <tr key={r.ticker} className="border-b border-[var(--color-border)] last:border-0">
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
                          {fmtNumber(Math.abs(r.predicted_pnl_delta), { decimals: 2 })}
                        </Badge>
                        {r.last_price > 0 && (
                          <span className="text-[10px] text-[var(--color-text3)]">
                            {fmtPct((r.predicted_price - r.last_price) / r.last_price, { signed: true, decimals: 2 })}
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

async function PortfolioRollupCard({ portfolios }: {
  portfolios: Array<{ id: string; name: string; weights: Record<string, number>; mape_30d: number | null }>;
}) {
  const t  = await getTranslations("overview");
  const tp = await getTranslations("profiles");
  const tc = await getTranslations("common");

  const sorted = [...portfolios].sort((a, b) => {
    if (a.mape_30d === null) return 1;
    if (b.mape_30d === null) return -1;
    return a.mape_30d - b.mape_30d;
  });

  const maxMape = Math.max(...sorted.map((p) => p.mape_30d ?? 0));

  return (
    <Card>
      <SectionHeader
        eyebrow={t("ranked_eyebrow")}
        title={t("ranked_title")}
        description={t("ranked_desc")}
      />
      <ul className="space-y-3">
        {sorted.map((p, i) => {
          const isActive = p.id.includes("P4") || p.id.includes("P1") || p.id.includes("P0");
          const barPct = p.mape_30d !== null && maxMape > 0 ? (p.mape_30d / maxMape) * 100 : 0;
          return (
            <li key={p.id}>
              <div className="mb-1 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[11px] tabular text-[var(--color-text3)]">#{i + 1}</span>
                  <span className={`text-[12.5px] font-medium ${isActive ? "text-[var(--color-green)]" : "text-[var(--color-text)]"}`}>
                    {p.id.split("_")[0]} {profileName(p.id, tp)}
                  </span>
                  {isActive && (
                    <span className="rounded-full bg-[var(--color-green)]/15 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-widest text-[var(--color-green)]">
                      activo
                    </span>
                  )}
                </div>
                <span className="font-mono text-[12px] text-[var(--color-text2)]">
                  {p.mape_30d !== null ? fmtPct(p.mape_30d) : tc("no_data")}
                </span>
              </div>
              {/* Visual bar */}
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg4)]">
                <div
                  className={`h-full rounded-full transition-all ${isActive ? "bg-[var(--color-green)]" : "bg-[var(--color-border3)]"}`}
                  style={{ width: `${barPct}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-[10.5px] text-[var(--color-text3)]">
        Menor MAPE = predicciones más precisas. La barra muestra el error relativo entre perfiles.
      </p>
    </Card>
  );
}
