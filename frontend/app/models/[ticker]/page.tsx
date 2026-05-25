/**
 * Model detail — equation, coefficients, predicted vs. actual history,
 * residuals plot, full diagnostics.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { HorizontalBarChart, TimeSeriesChart } from "@/components/charts";
import { DiagnosticGauge, passes, StatusBadge } from "@/components/diagnostics";
import {
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  fmtPct,
  SectionHeader,
  StatTile,
} from "@/components/primitives";
import { api, ApiError } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function ModelDetail({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = await params;

  let model: Awaited<ReturnType<typeof api.getModel>>;
  try {
    model = await api.getModel(ticker);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }

  // Fetch predictions + price history in parallel
  const [predsR, obsR] = await Promise.all([
    api.getPredictions(ticker, 90).catch(() => null),
    api
      .getObservations(ticker, { from: daysAgoISO(120), limit: 200 })
      .catch(() => []),
  ]);

  const flags = passes(
    model.r2,
    model.durbin_watson,
    model.breusch_pagan_p,
    model.max_vif,
  );

  const coefData = Object.entries(model.coefficients)
    .map(([label, value]) => ({ label, value }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value));

  // Build a date-aligned predicted-vs-actual series for the chart
  const series = mergePredVsActual(predsR?.points ?? [], obsR);

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-2">
        <Link
          href="/models"
          className="text-[12px] text-[var(--color-text3)] hover:text-[var(--color-text2)]"
        >
          ← All models
        </Link>
      </div>
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            Model · {model.is_active ? "active" : "inactive"}
          </div>
          <h1 className="font-mono text-2xl font-semibold tracking-tight">
            {model.ticker}
          </h1>
          <p className="mt-1.5 text-[13px] text-[var(--color-text2)]">
            Fit {fmtDate(model.fitted_at)} ·{" "}
            {fmtNumber(model.n_obs, { decimals: 0 })} observations · training{" "}
            {fmtDate(model.training_start)} → {fmtDate(model.training_end)}
          </p>
        </div>
        <StatusBadge status={model.status} />
      </div>

      {/* Diagnostic strip */}
      <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-5">
        <DiagnosticGauge
          label="R²"
          value={model.r2}
          pass={flags.r2}
          detail="≥ 0.02"
        />
        <DiagnosticGauge
          label="R² adj"
          value={model.r2_adj}
          pass={model.r2_adj >= 0.85}
          detail="info only"
        />
        <DiagnosticGauge
          label="Durbin-Watson"
          value={model.durbin_watson}
          pass={flags.dw}
          detail="∈ [1.5, 2.5]"
        />
        <DiagnosticGauge
          label="Breusch-Pagan p"
          value={model.breusch_pagan_p}
          pass={flags.bp_p}
          detail="> 0.05"
        />
        <DiagnosticGauge
          label="Max VIF"
          value={model.max_vif}
          pass={flags.max_vif}
          decimals={2}
          detail="< 10"
        />
      </div>

      {/* Equation */}
      <Card className="mb-8">
        <SectionHeader eyebrow="Equation" title="Linear model" />
        <pre className="overflow-x-auto rounded-[10px] bg-[var(--color-bg3)] p-4 text-[12.5px] leading-relaxed">
          {prettyEquation(model.intercept, model.coefficients, model.ticker)}
        </pre>
      </Card>

      {/* Predicted vs actual */}
      <Card className="mb-8">
        <SectionHeader
          eyebrow="Last 90 days"
          title="Predicted vs realized price"
          description="Predictions are made each evening for next-day close. Actuals backfill the day after."
          right={
            predsR && (
              <StatTile
                label="MAPE"
                value={fmtPct(predsR.mape ?? null)}
                hint="Mean abs. % error"
              />
            )
          }
        />
        {series.length === 0 ? (
          <EmptyState
            title="No prediction history yet"
            description="Once daily predictions accumulate, this chart will show predicted vs. actual close prices."
          />
        ) : (
          <TimeSeriesChart
            data={series}
            series={[
              {
                key: "predicted",
                label: "Predicted",
                color: "var(--color-cyan)",
              },
              {
                key: "actual",
                label: "Actual",
                color: "var(--color-green)",
              },
            ]}
            yDecimals={2}
            height={320}
          />
        )}
      </Card>

      {/* Coefficients */}
      <Card className="mb-8">
        <SectionHeader
          eyebrow="Coefficients"
          title={`${Object.keys(model.coefficients).length} predictors, ranked by magnitude`}
          description="Sign indicates direction of effect on next-day log return."
        />
        <HorizontalBarChart data={coefData} decimals={5} height={Math.max(220, coefData.length * 38)} />
      </Card>
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function mergePredVsActual(
  preds: { predicted_for: string; predicted_price: number; actual_price: number | null }[],
  obs: { observed_on: string; value: number }[],
): Array<{ date: string; predicted: number | null; actual: number | null }> {
  const byDate = new Map<string, { predicted: number | null; actual: number | null }>();
  for (const p of preds) {
    byDate.set(p.predicted_for, {
      predicted: p.predicted_price,
      actual: p.actual_price,
    });
  }
  for (const o of obs) {
    const cur = byDate.get(o.observed_on);
    if (cur) {
      byDate.set(o.observed_on, { ...cur, actual: o.value });
    } else {
      byDate.set(o.observed_on, { predicted: null, actual: o.value });
    }
  }
  return Array.from(byDate.entries())
    .map(([date, v]) => ({ date, ...v }))
    .sort((a, b) => (a.date < b.date ? -1 : 1));
}

function prettyEquation(
  intercept: number,
  coefs: Record<string, number>,
  ticker: string,
): string {
  const terms = Object.entries(coefs)
    .map(([name, beta]) => {
      const sign = beta >= 0 ? "+" : "−";
      const abs = Math.abs(beta).toExponential(3);
      return `  ${sign} ${abs} · ret(${name}, t−1)`;
    })
    .join("\n");
  const sign = intercept >= 0 ? "+" : "−";
  const abs = Math.abs(intercept).toExponential(3);
  return `ret(${ticker}, t) = ${sign} ${abs}\n${terms}\n        + ε`;
}

function daysAgoISO(d: number): string {
  const dt = new Date();
  dt.setUTCDate(dt.getUTCDate() - d);
  return dt.toISOString().slice(0, 10);
}
