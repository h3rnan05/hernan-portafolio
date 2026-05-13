/**
 * Models — the 9 fitted regressions, listed.
 *
 * Each card surfaces the four diagnostic numbers (R², DW, BP_p, max VIF),
 * the status pill, and the active predictors. Click → detail page.
 */

import Link from "next/link";

import { DiagnosticGauge, passes, StatusBadge } from "@/components/diagnostics";
import {
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  fmtRelative,
  SectionHeader,
} from "@/components/primitives";
import { RefitButton } from "@/components/refit-button";
import { api, ApiError } from "@/lib/api";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function ModelsPage() {
  let models: Awaited<ReturnType<typeof api.listModels>> = [];
  let error: string | null = null;
  try {
    // Show ALL fits (active + REVIEW + FAIL) so the user can see what's gated
    models = await api.listModels(false);
  } catch (e) {
    error = e instanceof ApiError ? e.message : String(e);
  }

  // Deduplicate per ticker: prefer is_active=True, else most recent fitted_at
  const byTicker = new Map<string, (typeof models)[number]>();
  for (const m of models) {
    const cur = byTicker.get(m.ticker);
    if (!cur) {
      byTicker.set(m.ticker, m);
    } else if (m.is_active && !cur.is_active) {
      byTicker.set(m.ticker, m);
    } else if (
      !cur.is_active && !m.is_active && m.fitted_at > cur.fitted_at
    ) {
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
      <div className="mb-8 flex items-start justify-between gap-4">
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

      {error ? (
        <Card>
          <div className="text-[13px] text-[var(--color-red)]">{error}</div>
        </Card>
      ) : display.length === 0 ? (
        <EmptyState
          title="No models fit yet"
          description="Click 'Refit all models' once enough observations have landed."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {display.map((m) => {
            const flags = passes(
              m.r2,
              m.durbin_watson,
              m.breusch_pagan_p,
              m.max_vif,
            );
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
                  <DiagnosticGauge
                    label="R²"
                    value={m.r2}
                    pass={flags.r2}
                    detail="≥ 0.90"
                  />
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
      )}
    </div>
  );
}
