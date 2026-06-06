"use client";

/**
 * "Comparativo de tu portafolio" (HER task 1).
 *
 * Plots an equal-weighted index of the 9 tracked stocks against the major
 * market benchmarks — NYSE (VTI), NASDAQ (QQQ), IPC México, Nikkei 225 and
 * FTSE 100 — over a selectable 7 / 30 / 360-day window. Every series is
 * re-based to 100 at the start of the active window so they're comparable
 * regardless of native units (USD, points, GBX…).
 *
 * Benchmarks that aren't ingested yet simply don't render a line; the panel
 * notes which ones are pending so the chart never shows empty axes.
 */

import { useEffect, useMemo, useState } from "react";

import { TimeSeriesChart } from "@/components/charts";
import { Card, EmptyState, SectionHeader, Skeleton } from "@/components/primitives";
import { api, type Observation, type Variable } from "@/lib/api";

type RangeKey = 7 | 30 | 360;
const RANGES: { key: RangeKey; label: string }[] = [
  { key: 7, label: "7 días" },
  { key: 30, label: "30 días" },
  { key: 360, label: "360 días" },
];

// Benchmark id → display + colour. Order controls legend order.
const BENCHMARKS: { id: string; label: string; color: string }[] = [
  { id: "NYSE_Composite", label: "NYSE (VTI)", color: "var(--color-blue)" },
  { id: "NASDAQ_Composite", label: "NASDAQ (QQQ)", color: "var(--color-violet)" },
  { id: "IPC_Mexico", label: "IPC México", color: "var(--color-amber)" },
  { id: "Nikkei_225", label: "Nikkei 225", color: "var(--color-cyan)" },
  { id: "FTSE_100", label: "FTSE 100", color: "var(--color-red)" },
];

const PORTFOLIO_KEY = "portfolio";
const PORTFOLIO_COLOR = "var(--color-green)";

function daysAgoISO(d: number): string {
  const dt = new Date();
  dt.setUTCDate(dt.getUTCDate() - d);
  return dt.toISOString().slice(0, 10);
}

type RawRow = Record<string, number | string | null>;

export function PortfolioComparison() {
  const [range, setRange] = useState<RangeKey>(30);
  const [rows, setRows] = useState<RawRow[] | null>(null);
  // Which benchmark ids actually returned data.
  const [available, setAvailable] = useState<string[]>([]);
  const [pending, setPending] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const variables: Variable[] = await api.listVariables();
        const byId = new Map(variables.map((v) => [v.id, v]));
        const stocks = variables.filter((v) => v.kind === "stock");

        // Which benchmarks exist + have at least one observation.
        const benchIds = BENCHMARKS.map((b) => b.id).filter((id) => {
          const v = byId.get(id);
          return v && v.last_observed_on;
        });
        const pendingIds = BENCHMARKS.map((b) => b.id).filter(
          (id) => !benchIds.includes(id),
        );

        const from = daysAgoISO(370);

        // Fetch benchmark series + stock series in parallel.
        const [benchSeries, stockSeries] = await Promise.all([
          Promise.all(
            benchIds.map(async (id) => ({
              id,
              obs: await api
                .getObservations(id, { from, limit: 400 })
                .catch(() => [] as Observation[]),
            })),
          ),
          Promise.all(
            stocks.map(async (s) => ({
              id: s.id,
              obs: await api
                .getObservations(s.id, { from, limit: 400 })
                .catch(() => [] as Observation[]),
            })),
          ),
        ]);

        // Build a base-100 equal-weight index of the 9 stocks as the portfolio.
        const portfolioByDate = buildEqualWeightIndex(stockSeries);

        // Per-benchmark date→value maps.
        const benchMaps = new Map<string, Map<string, number>>();
        for (const { id, obs } of benchSeries) {
          benchMaps.set(id, new Map(obs.map((o) => [o.observed_on, o.value])));
        }

        // Master date axis = union of portfolio + benchmark dates, sorted.
        const dateSet = new Set<string>(portfolioByDate.keys());
        for (const m of benchMaps.values())
          for (const d of m.keys()) dateSet.add(d);
        const dates = Array.from(dateSet).sort();

        // Forward-fill each series across the master axis.
        const carried = new Map<string, number | null>();
        carried.set(PORTFOLIO_KEY, null);
        for (const id of benchIds) carried.set(id, null);

        const built: RawRow[] = dates.map((date) => {
          const row: RawRow = { date };
          const pv = portfolioByDate.get(date);
          if (pv != null) carried.set(PORTFOLIO_KEY, pv);
          row[PORTFOLIO_KEY] = carried.get(PORTFOLIO_KEY) ?? null;
          for (const id of benchIds) {
            const bv = benchMaps.get(id)!.get(date);
            if (bv != null) carried.set(id, bv);
            row[id] = carried.get(id) ?? null;
          }
          return row;
        });

        if (!active) return;
        setRows(built);
        setAvailable(benchIds);
        setPending(pendingIds);
      } catch {
        if (active) setRows([]);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Slice to the active window and re-base every series to 100 at window start.
  const chartData = useMemo(() => {
    if (!rows || rows.length === 0) return [];
    const cutoff = daysAgoISO(range);
    const windowed = rows.filter((r) => String(r.date) >= cutoff);
    const slice = windowed.length >= 2 ? windowed : rows.slice(-2);
    const keys = [PORTFOLIO_KEY, ...available];
    const bases = new Map<string, number>();
    for (const k of keys) {
      const firstWithVal = slice.find(
        (r) => typeof r[k] === "number" && r[k] !== null,
      );
      if (firstWithVal) bases.set(k, firstWithVal[k] as number);
    }
    return slice.map((r) => {
      const out: RawRow = { date: r.date };
      for (const k of keys) {
        const base = bases.get(k);
        const v = r[k];
        out[k] =
          typeof v === "number" && base && base !== 0 ? (v / base) * 100 : null;
      }
      return out;
    });
  }, [rows, range, available]);

  const series = useMemo(
    () => [
      { key: PORTFOLIO_KEY, label: "Tu portafolio (equiponderado)", color: PORTFOLIO_COLOR },
      ...BENCHMARKS.filter((b) => available.includes(b.id)).map((b) => ({
        key: b.id,
        label: b.label,
        color: b.color,
      })),
    ],
    [available],
  );

  return (
    <Card>
      <SectionHeader
        eyebrow="Comparativo"
        title="Comparativo de tu portafolio"
        description="Tu portafolio (equiponderado, base 100) frente a los principales índices. Todas las series se rebasan a 100 al inicio del rango."
        right={
          <div className="inline-flex items-center gap-1 rounded-[8px] bg-[var(--color-bg3)] p-1">
            {RANGES.map((r) => (
              <button
                key={r.key}
                type="button"
                onClick={() => setRange(r.key)}
                aria-pressed={range === r.key}
                className={`rounded-[6px] px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  range === r.key
                    ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
                    : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
                }`}
              >
                {r.label}
              </button>
            ))}
          </div>
        }
      />

      {rows === null ? (
        <Skeleton className="h-[280px]" />
      ) : chartData.length < 2 ? (
        <EmptyState
          title="Sin datos suficientes todavía"
          description="El comparativo aparece cuando hay al menos dos observaciones en el rango. Ejecuta la ingesta diaria para poblar las series."
        />
      ) : (
        <>
          <TimeSeriesChart
            data={chartData}
            series={series}
            yDecimals={1}
            yUnit=""
            height={300}
          />
          {pending.length > 0 && (
            <p className="mt-3 text-[11px] text-[var(--color-text3)]">
              Pendientes de ingesta (sin datos aún):{" "}
              {pending
                .map((id) => BENCHMARKS.find((b) => b.id === id)?.label ?? id)
                .join(" · ")}
              .
            </p>
          )}
        </>
      )}
    </Card>
  );
}

/**
 * Equal-weight, base-100 index from a set of price series. Each ticker is
 * normalised to its own first observation, then averaged across tickers per
 * date. Dates where no ticker has yet started are skipped.
 */
function buildEqualWeightIndex(
  stockSeries: { id: string; obs: Observation[] }[],
): Map<string, number> {
  const firsts = new Map<string, number>();
  for (const { id, obs } of stockSeries) {
    if (obs.length > 0) firsts.set(id, obs[0].value);
  }
  // date → { sum, count } of normalised ratios
  const acc = new Map<string, { sum: number; count: number }>();
  for (const { id, obs } of stockSeries) {
    const base = firsts.get(id);
    if (!base) continue;
    for (const o of obs) {
      const ratio = o.value / base;
      const cur = acc.get(o.observed_on) ?? { sum: 0, count: 0 };
      cur.sum += ratio;
      cur.count += 1;
      acc.set(o.observed_on, cur);
    }
  }
  const out = new Map<string, number>();
  for (const [date, { sum, count }] of acc) {
    if (count > 0) out.set(date, (sum / count) * 100);
  }
  return out;
}
