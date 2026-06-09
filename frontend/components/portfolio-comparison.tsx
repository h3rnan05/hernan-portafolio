"use client";

/**
 * "Comparativo de tu portafolio" (HER task 1).
 *
 * Plots the user's portfolio — the P3 Balanced risk profile, weighted by the
 * profile's own weights — against the major market benchmarks (NYSE/VTI,
 * NASDAQ/QQQ, IPC México, Nikkei 225, FTSE 100) over a selectable 7 / 30 /
 * 360-day window. Every series is re-based to 100 at the start of the active
 * window so they're comparable regardless of native units (USD, points, GBX…).
 *
 * The portfolio line is the thickest, in the accent green; benchmarks render in
 * fainter tones. The legend toggles each series on/off, and the tooltip shows
 * both the re-based value and the % change versus the window start.
 *
 * Benchmarks that aren't ingested yet simply don't render a line; the panel
 * notes which ones are pending so the chart never shows empty axes.
 */

import { useEffect, useMemo, useState } from "react";

import { TimeSeriesChart } from "@/components/charts";
import { Card, EmptyState, SectionHeader, Skeleton } from "@/components/primitives";
import { api, type Observation } from "@/lib/api";

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
// The spec's default "Mi portafolio" series is the P3 Balanced risk profile.
const PORTFOLIO_PROFILE_ID = "P3_BALANCED";

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
  // Series the user has toggled off via the legend.
  const [hidden, setHidden] = useState<string[]>([]);

  const toggleSeries = (key: string) =>
    setHidden((h) =>
      h.includes(key) ? h.filter((k) => k !== key) : [...h, key],
    );

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        // The portfolio line follows the P3 Balanced profile. Fetch its weights
        // and the variable registry together.
        const [variables, profile] = await Promise.all([
          api.listVariables(),
          api.getPortfolio(PORTFOLIO_PROFILE_ID).catch(() => null),
        ]);
        const byId = new Map(variables.map((v) => [v.id, v]));
        const stockIds = variables
          .filter((v) => v.kind === "stock")
          .map((v) => v.id);

        // P3 weights when available; otherwise fall back to an equal weight over
        // every tracked stock so the line still renders if the profile API is
        // down. Only positive-weight tickers contribute.
        const profileWeights = profile?.weights ?? {};
        const weights: Record<string, number> = Object.values(
          profileWeights,
        ).some((w) => w > 0)
          ? profileWeights
          : Object.fromEntries(stockIds.map((id) => [id, 1]));
        const portfolioTickers = Object.keys(weights).filter(
          (t) => (weights[t] ?? 0) > 0,
        );

        // Which benchmarks exist + have at least one observation.
        const benchIds = BENCHMARKS.map((b) => b.id).filter((id) => {
          const v = byId.get(id);
          return v && v.last_observed_on;
        });
        const pendingIds = BENCHMARKS.map((b) => b.id).filter(
          (id) => !benchIds.includes(id),
        );

        const from = daysAgoISO(370);

        // Fetch benchmark series + the portfolio's constituent series in parallel.
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
            portfolioTickers.map(async (id) => ({
              id,
              obs: await api
                .getObservations(id, { from, limit: 400 })
                .catch(() => [] as Observation[]),
            })),
          ),
        ]);

        // Build the base-100, weight-aware portfolio index (P3 Balanced).
        const portfolioByDate = buildWeightedIndex(weights, stockSeries);

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
      {
        key: PORTFOLIO_KEY,
        label: "Tu portafolio (P3 Balanced)",
        color: PORTFOLIO_COLOR,
        width: 2.75, // thickest line — the focal series
      },
      ...BENCHMARKS.filter((b) => available.includes(b.id)).map((b) => ({
        key: b.id,
        label: b.label,
        color: b.color,
        width: 1.5,
        faded: true, // benchmarks in fainter tones
      })),
    ],
    [available],
  );

  return (
    <Card>
      <SectionHeader
        eyebrow="Comparativo"
        title="Comparativo de tu portafolio"
        description="Tu portafolio (perfil P3 Balanced, base 100) frente a los principales índices. Todas las series se rebasan a 100 al inicio del rango. Toca la leyenda para ocultar o mostrar cada serie."
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
            hidden={hidden}
            onToggleSeries={toggleSeries}
            tooltipBaseline={100}
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
 * Weighted, base-100 index from a set of price series (the P3 Balanced
 * portfolio). Each ticker is normalised to its own first observation, then
 * combined as a weighted average of those ratios.
 *
 * Observations are forward-filled per ticker across the union date axis (so a
 * holiday on one exchange doesn't punch a hole in the line), and the weights
 * are re-normalised over the tickers that have actually started trading on each
 * date — a name whose history begins mid-window never skews the index.
 */
function buildWeightedIndex(
  weights: Record<string, number>,
  stockSeries: { id: string; obs: Observation[] }[],
): Map<string, number> {
  const usable = stockSeries.filter(
    (s) => s.obs.length > 0 && s.obs[0].value > 0 && (weights[s.id] ?? 0) > 0,
  );
  if (usable.length === 0) return new Map();

  const firsts = new Map(usable.map((s) => [s.id, s.obs[0].value]));
  const obsMaps = new Map(
    usable.map((s) => [
      s.id,
      new Map(s.obs.map((o) => [o.observed_on, o.value])),
    ]),
  );
  const dates = Array.from(
    new Set(usable.flatMap((s) => s.obs.map((o) => o.observed_on))),
  ).sort();

  const carried = new Map<string, number>();
  const out = new Map<string, number>();
  for (const date of dates) {
    let weighted = 0;
    let wsum = 0;
    for (const s of usable) {
      const v = obsMaps.get(s.id)!.get(date);
      if (v != null) carried.set(s.id, v);
      const cv = carried.get(s.id);
      if (cv == null) continue; // ticker hasn't started trading yet
      const w = weights[s.id] ?? 0;
      weighted += w * (cv / firsts.get(s.id)!);
      wsum += w;
    }
    if (wsum > 0) out.set(date, (weighted / wsum) * 100);
  }
  return out;
}
