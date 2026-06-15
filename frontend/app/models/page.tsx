/**
 * Models — split into two tabs (HER task 6):
 *   * "Modelos por Acción"     — the per-stock OLS regressions with diagnostics.
 *   * "Modelo de Portafolio"   — the rolled-up portfolio-level view (P1–P5
 *                                 profiles + aggregate stats).
 *
 * Tab state lives in the `?tab=stocks|portfolio` query param so it's
 * shareable and survives reloads.
 */

import { getLocale, getTranslations } from "next-intl/server";
import { Suspense } from "react";

import Link from "next/link";

import { DiagnosticGauge, passes, StatusBadge } from "@/components/diagnostics";
import { PredictorsPanel } from "@/components/predictors-panel";
import { StocksGrid } from "@/components/stocks-grid";
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
import { RefitButton } from "@/components/refit-button";
import { TabNav } from "@/components/tab-nav";
import { api, ApiError, type ModelSummary, type Portfolio } from "@/lib/api";

export const revalidate = 3600;

const WEIGHT_COLORS = [
  "bg-[var(--color-green)]",
  "bg-[var(--color-cyan)]",
  "bg-[var(--color-blue)]",
  "bg-[var(--color-violet)]",
  "bg-[var(--color-amber)]",
  "bg-[var(--color-red)]",
  "bg-[var(--color-green-dim)]",
  "bg-[var(--color-text3)]",
  "bg-[var(--color-text4)]",
];

export default async function ModelsPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const { tab: tabParam } = await searchParams;
  const tab = tabParam === "portfolio" ? "portfolio" : "stocks";
  const t = await getTranslations("models");

  let models: ModelSummary[] = [];
  let portfolios: Portfolio[] = [];
  let error: string | null = null;
  try {
    // Show ALL fits (active + REVIEW + FAIL) so the user can see what's gated
    [models, portfolios] = await Promise.all([
      api.listModels(false),
      api.listPortfolios().catch(() => [] as Portfolio[]),
    ]);
  } catch (e) {
    error = e instanceof ApiError ? e.message : String(e);
  }

  // Deduplicate per ticker: prefer is_active=True, else most recent fitted_at
  const byTicker = new Map<string, ModelSummary>();
  for (const m of models) {
    const cur = byTicker.get(m.ticker);
    if (!cur) {
      byTicker.set(m.ticker, m);
    } else if (m.is_active && !cur.is_active) {
      byTicker.set(m.ticker, m);
    } else if (!cur.is_active && !m.is_active && m.fitted_at > cur.fitted_at) {
      byTicker.set(m.ticker, m);
    }
  }
  const display = Array.from(byTicker.values()).sort((a, b) =>
    a.ticker.localeCompare(b.ticker),
  );

  // Fetch directional accuracy for all displayed tickers in parallel
  const accuracyMap: Record<string, number | null> = {};
  await Promise.all(
    display.map(async (m) => {
      try {
        const pred = await api.getPredictions(m.ticker, 90);
        accuracyMap[m.ticker] = pred.directional_accuracy ?? null;
      } catch {
        accuracyMap[m.ticker] = null;
      }
    }),
  );
  const counts = {
    pass: display.filter((m) => m.status === "PASS").length,
    review: display.filter((m) => m.status === "REVIEW").length,
    fail: display.filter((m) => m.status === "FAIL").length,
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {t("eyebrow", { pass: counts.pass, review: counts.review })}
            {counts.fail > 0 && t("eyebrow_fail", { fail: counts.fail })}
          </div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{t("title")}</h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            {t("subtitle")}
          </p>
        </div>
        <RefitButton />
      </div>

      <div className="mb-8">
        <Suspense fallback={null}>
          <TabNav
            tabs={[
              { key: "stocks", label: t("tab_stocks") },
              { key: "portfolio", label: t("tab_portfolio") },
            ]}
          />
        </Suspense>
      </div>

      {error ? (
        <Card>
          <div className="text-[13px] text-[var(--color-red)]">{error}</div>
        </Card>
      ) : tab === "portfolio" ? (
        <PortfolioModelView portfolios={portfolios} activeModels={display} />
      ) : (
        <>
          <StockModelsGrid models={display} accuracyMap={accuracyMap} />
          <section className="mt-10">
            <SectionHeader
              eyebrow="Predicciones · precios"
              title="Las 9 acciones del modelo"
              description="Precio actual vs predicho para mañana, directo del modelo OLS."
            />
            <StocksGrid />
          </section>
          <section className="mt-10">
            <SectionHeader
              eyebrow="Variables · cobertura"
              title="Predictores del modelo"
              description="Variables macroeconómicas y de mercado que alimentan las predicciones."
            />
            <PredictorsPanel />
          </section>
        </>
      )}
    </div>
  );
}

// ─── Tab 1: per-stock models ──────────────────────────────────────────────────

async function StockModelsGrid({ models, accuracyMap }: { models: ModelSummary[]; accuracyMap: Record<string, number | null> }) {
  const t = await getTranslations("models");
  const locale = await getLocale();
  if (models.length === 0) {
    return (
      <EmptyState title={t("empty_title")} description={t("empty_desc")} />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {models.map((m) => {
        const flags = passes(m.r2, m.durbin_watson, m.breusch_pagan_p, m.max_vif);
        return (
          <Link
            key={m.ticker}
            href={`/models/${m.ticker}`}
            className="group block rounded-none bg-[var(--color-bg2)] p-5 transition-colors duration-150 hover:bg-[var(--color-bg3)] shadow-[0_0_0_1px_rgba(0,0,0,0.06),0_1px_3px_rgba(0,0,0,0.1)]"
          >
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <div className="font-mono text-base font-semibold">
                  {m.ticker}
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">
                  {t("fit_relative", {
                    when: fmtRelative(m.fitted_at),
                    n: fmtNumber(m.n_obs, { decimals: 0 }),
                  })}
                </div>
              </div>
              <StatusBadge status={m.status} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <DiagnosticGauge label="R²" value={m.r2} pass={flags.r2} detail="≥ 0.02" />
              <DiagnosticGauge
                label="Durbin-Watson"
                value={m.durbin_watson}
                pass={flags.dw}
                detail="∈ [1.5, 2.5]"
              />
              <DiagnosticGauge
                label="Breusch-Pagan p"
                value={m.breusch_pagan_p}
                pass={flags.bp_p}
                detail="> 0.05"
              />
              <DiagnosticGauge
                label="Max VIF"
                value={m.max_vif}
                pass={flags.max_vif}
                decimals={2}
                detail="< 10"
              />
            </div>
            {/* Directional accuracy metric */}
            {(() => {
              const da = accuracyMap[m.ticker];
              if (da === null || da === undefined) return null;
              const pct = Math.round(da * 100);
              const color = pct >= 60 ? "var(--color-green)" : pct >= 50 ? "var(--color-amber)" : "var(--color-red)";
              return (
                <div className="mt-3 flex items-center justify-between border border-[var(--color-border)] bg-[var(--color-bg3)] px-3 py-2">
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text3)]">
                    Acierto de dirección · 90d
                  </span>
                  <span className="font-mono text-sm font-bold tabular" style={{ color }}>
                    {pct}%
                  </span>
                </div>
              );
            })()}
            <div className="mt-4">
              <div className="mb-1.5 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                {t("predictors_label")}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {m.predictor_ids.map((p) => (
                  <span
                    key={p}
                    className="rounded-none bg-[var(--color-bg4)] px-1.5 py-0.5 text-[10.5px] text-[var(--color-text2)] font-mono"
                  >
                    {p}
                  </span>
                ))}
              </div>
            </div>
            <div className="mt-3 text-[11px] text-[var(--color-text3)]">
              {t("training_window", {
                start: fmtDate(m.training_start, locale),
                end: fmtDate(m.training_end, locale),
              })}
            </div>
          </Link>
        );
      })}
    </div>
  );
}

// ─── Tab 2: portfolio-level model rollup ──────────────────────────────────────

async function PortfolioModelView({
  portfolios,
  activeModels,
}: {
  portfolios: Portfolio[];
  activeModels: ModelSummary[];
}) {
  const t = await getTranslations("models");
  const tp = await getTranslations("profiles");
  const tc = await getTranslations("common");
  if (portfolios.length === 0) {
    return (
      <EmptyState
        title={t("portfolio_empty_title")}
        description={t("portfolio_empty_desc")}
      />
    );
  }

  const mapes = portfolios
    .map((p) => p.mape_30d)
    .filter((m): m is number => m !== null);
  const passModels = activeModels.filter((m) => m.status === "PASS").length;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label={t("stat_risk_profiles")} value={portfolios.length} hint="P1 → P5" />
        <StatTile
          label={t("stat_feeding_models")}
          value={`${passModels} / ${activeModels.length}`}
          hint={t("stat_feeding_hint")}
        />
        <StatTile
          label={t("stat_best_mape")}
          value={mapes.length ? fmtPct(Math.min(...mapes)) : "—"}
          hint={tc("lower_is_better")}
        />
        <StatTile
          label={t("stat_avg_mape")}
          value={
            mapes.length
              ? fmtPct(mapes.reduce((a, b) => a + b, 0) / mapes.length)
              : "—"
          }
          hint={t("stat_avg_mape_hint")}
        />
      </div>

      <SectionHeader
        eyebrow={t("rollup_eyebrow")}
        title={t("rollup_title")}
        description={t("rollup_desc")}
      />

      <div className="space-y-3">
        {portfolios.map((p) => {
          const sorted = Object.entries(p.weights).sort((a, b) => b[1] - a[1]);
          const code = p.id.split("_")[0];
          return (
            <Card key={p.id}>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/portfolios?profile=${p.id}`}
                      className="text-[14px] font-semibold hover:text-[var(--color-cyan)]"
                    >
                      {code} {tp(`${code}.name`)}
                    </Link>
                    <Badge tone="neutral">{code}</Badge>
                  </div>
                  {(() => {
                    const desc = tp.has(`${code}.description`)
                      ? tp(`${code}.description`)
                      : p.description;
                    return desc ? (
                      <p className="mt-1 text-[12px] text-[var(--color-text2)]">
                        {desc}
                      </p>
                    ) : null;
                  })()}
                </div>
                <div className="text-right">
                  <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                    MAPE 30d
                  </div>
                  <div className="mt-0.5 font-mono tabular text-base font-semibold">
                    {p.mape_30d !== null ? fmtPct(p.mape_30d) : "—"}
                  </div>
                </div>
              </div>
              <div className="mt-4 flex h-3 overflow-hidden rounded-none bg-[var(--color-bg3)]">
                {sorted.map(([ticker, w], i) => (
                  <div
                    key={ticker}
                    title={`${ticker}: ${fmtPct(w, { decimals: 1 })}`}
                    style={{ width: `${(w * 100).toFixed(2)}%` }}
                    className={`h-full ${WEIGHT_COLORS[i % WEIGHT_COLORS.length]}`}
                  />
                ))}
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
