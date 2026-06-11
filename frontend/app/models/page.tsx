/**
 * Models — split into two tabs (HER task 6):
 *   * "Modelos por Acción"     — the per-stock OLS regressions with diagnostics.
 *   * "Modelo de Portafolio"   — the rolled-up portfolio-level view (P1–P5
 *                                 profiles + aggregate stats).
 *
 * Tab state lives in the `?tab=stocks|portfolio` query param so it's
 * shareable and survives reloads.
 */

import { Suspense } from "react";

import Link from "next/link";

import { DiagnosticGauge, passes, StatusBadge } from "@/components/diagnostics";
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
  const counts = {
    pass: display.filter((m) => m.status === "PASS").length,
    review: display.filter((m) => m.status === "REVIEW").length,
    fail: display.filter((m) => m.status === "FAIL").length,
  };

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            Models · {counts.pass} pass / {counts.review} review
            {counts.fail > 0 && ` / ${counts.fail} fail`}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Fitted regressions
          </h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            One OLS model per stock with non-overlapping predictors. Only PASS
            models generate live predictions. REVIEW means the fit landed but
            failed at least one of the four diagnostic gates.
          </p>
        </div>
        <RefitButton />
      </div>

      <div className="mb-8">
        <Suspense fallback={null}>
          <TabNav
            tabs={[
              { key: "stocks", label: "Modelos por Acción" },
              { key: "portfolio", label: "Modelo de Portafolio" },
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
        <StockModelsGrid models={display} />
      )}
    </div>
  );
}

// ─── Tab 1: per-stock models ──────────────────────────────────────────────────

function StockModelsGrid({ models }: { models: ModelSummary[] }) {
  if (models.length === 0) {
    return (
      <EmptyState
        title="No models fit yet"
        description="Click 'Refit all models' once enough observations have landed."
      />
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
            className="group block rounded-[14px] bg-[var(--color-bg2)] p-5 transition-colors duration-150 hover:bg-[var(--color-bg3)] shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_1px_2px_rgba(0,0,0,0.3)]"
          >
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <div className="font-mono text-base font-semibold">
                  {m.ticker}
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">
                  Fit {fmtRelative(m.fitted_at)} ·{" "}
                  {fmtNumber(m.n_obs, { decimals: 0 })} obs
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
            <div className="mt-4">
              <div className="mb-1.5 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                Predictors
              </div>
              <div className="flex flex-wrap gap-1.5">
                {m.predictor_ids.map((p) => (
                  <span
                    key={p}
                    className="rounded-[6px] bg-[var(--color-bg4)] px-1.5 py-0.5 text-[10.5px] text-[var(--color-text2)] font-mono"
                  >
                    {p}
                  </span>
                ))}
              </div>
            </div>
            <div className="mt-3 text-[11px] text-[var(--color-text3)]">
              Training window {fmtDate(m.training_start)} →{" "}
              {fmtDate(m.training_end)}
            </div>
          </Link>
        );
      })}
    </div>
  );
}

// ─── Tab 2: portfolio-level model rollup ──────────────────────────────────────

function PortfolioModelView({
  portfolios,
  activeModels,
}: {
  portfolios: Portfolio[];
  activeModels: ModelSummary[];
}) {
  if (portfolios.length === 0) {
    return (
      <EmptyState
        title="Portfolio model not built yet"
        description="The 5 risk-profile portfolios are computed by the daily prediction job once at least one stock model is active."
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
        <StatTile label="Risk profiles" value={portfolios.length} hint="P1 → P5" />
        <StatTile
          label="Feeding models"
          value={`${passModels} / ${activeModels.length}`}
          hint="PASS stock models"
        />
        <StatTile
          label="Best MAPE 30d"
          value={mapes.length ? fmtPct(Math.min(...mapes)) : "—"}
          hint="Lower is better"
        />
        <StatTile
          label="Avg MAPE 30d"
          value={
            mapes.length
              ? fmtPct(mapes.reduce((a, b) => a + b, 0) / mapes.length)
              : "—"
          }
          hint="Across profiles"
        />
      </div>

      <SectionHeader
        eyebrow="Rollup"
        title="Profiles built from the active model fleet"
        description="Each profile reweights the same per-stock predictions. P1 leans defensive; P5 leans into predicted upside."
      />

      <div className="space-y-3">
        {portfolios.map((p) => {
          const sorted = Object.entries(p.weights).sort((a, b) => b[1] - a[1]);
          return (
            <Card key={p.id}>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="flex items-center gap-2">
                    <Link
                      href={`/portfolios?profile=${p.id}`}
                      className="text-[14px] font-semibold hover:text-[var(--color-cyan)]"
                    >
                      {p.name}
                    </Link>
                    <Badge tone="neutral">{p.id.split("_")[0]}</Badge>
                  </div>
                  {p.description && (
                    <p className="mt-1 text-[12px] text-[var(--color-text2)]">
                      {p.description}
                    </p>
                  )}
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
              <div className="mt-4 flex h-3 overflow-hidden rounded-[6px] bg-[var(--color-bg3)]">
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
