"use client";

/**
 * "Comparativo de tu portafolio" (HER task 1, revisado).
 *
 * Grafica el RETORNO ACUMULADO en % desde el inicio de la ventana activa para
 * 6 series: mi portafolio (serie REALIZADA de P3 Balanced) + NYSE (^NYA/VTI),
 * NASDAQ (^IXIC/QQQ), IPC México (^MXX), Nikkei 225 (^N225) y FTSE 100 (^FTSE).
 *
 *   pct_acum[t] = (precio[t] / precio[primer_día_ventana] - 1) * 100
 *
 * Los botones 7D / 30D / 360D reanclan el "inicio" al primer día de la ventana,
 * así que el acumulado se recalcula con base 0% en el borde izquierdo. El
 * tooltip muestra, por serie, el retorno del día y el acumulado.
 *
 * Todas las series se alinean por fecha y se hace forward-fill en días de bolsa
 * cerrada para que el "% día" nunca sea NaN.
 */

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, EmptyState, fmtDate, SectionHeader, Skeleton } from "@/components/primitives";
import {
  api,
  type Observation,
  type PortfolioPredictions,
  type Variable,
} from "@/lib/api";

type RangeKey = 7 | 30 | 360;
const RANGES: { key: RangeKey; label: string }[] = [
  { key: 7, label: "7 días" },
  { key: 30, label: "30 días" },
  { key: 360, label: "360 días" },
];

const PORTFOLIO_KEY = "portfolio";
const PORTFOLIO_PROFILE = "P3_BALANCED";

type SeriesMeta = {
  key: string;
  label: string;
  color: string;
  width: number;
  opacity: number;
};

// Mi portafolio: línea gruesa, verde de acento, opacidad plena.
const PORTFOLIO_META: SeriesMeta = {
  key: PORTFOLIO_KEY,
  label: "Tu portafolio · P3 Balanced",
  color: "var(--color-green)",
  width: 2.75,
  opacity: 1,
};

// Índices: distinguibles entre sí, pero más tenues (más finos + menos opacos).
const BENCHMARKS: SeriesMeta[] = [
  { key: "NYSE_Composite", label: "NYSE (^NYA)", color: "var(--color-blue)", width: 1.5, opacity: 0.72 },
  { key: "NASDAQ_Composite", label: "NASDAQ (^IXIC)", color: "var(--color-violet)", width: 1.5, opacity: 0.72 },
  { key: "IPC_Mexico", label: "IPC México (^MXX)", color: "var(--color-amber)", width: 1.5, opacity: 0.72 },
  { key: "Nikkei_225", label: "Nikkei 225 (^N225)", color: "var(--color-cyan)", width: 1.5, opacity: 0.72 },
  { key: "FTSE_100", label: "FTSE 100 (^FTSE)", color: "var(--color-red)", width: 1.5, opacity: 0.72 },
];

function daysAgoISO(d: number): string {
  const dt = new Date();
  dt.setUTCDate(dt.getUTCDate() - d);
  return dt.toISOString().slice(0, 10);
}

// Fila con los PRECIOS rellenados (forward-fill) por serie + el % de cada día,
// precalculado una sola vez (independiente de la ventana): `${key}__day`.
type FilledRow = Record<string, number | string | null>;

export function PortfolioComparison() {
  const [range, setRange] = useState<RangeKey>(30);
  const [rows, setRows] = useState<FilledRow[] | null>(null);
  const [available, setAvailable] = useState<string[]>([]);
  const [pending, setPending] = useState<string[]>([]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const variables: Variable[] = await api.listVariables();
        const byId = new Map(variables.map((v) => [v.id, v]));

        const benchIds = BENCHMARKS.map((b) => b.key).filter((id) => {
          const v = byId.get(id);
          return v && v.last_observed_on;
        });
        const pendingIds = BENCHMARKS.map((b) => b.key).filter(
          (id) => !benchIds.includes(id),
        );

        const from = daysAgoISO(370);

        // Serie REALIZADA de P3 (actual_value) + benchmarks, en paralelo.
        const [p3, benchSeries] = await Promise.all([
          api
            .getPortfolioPredictions(PORTFOLIO_PROFILE, 400)
            .catch(() => null as PortfolioPredictions | null),
          Promise.all(
            benchIds.map(async (id) => ({
              id,
              obs: await api
                .getObservations(id, { from, limit: 400 })
                .catch(() => [] as Observation[]),
            })),
          ),
        ]);

        // date → precio del portafolio (realizado).
        let portfolioByDate = new Map<string, number>();
        if (p3) {
          for (const pt of p3.points) {
            if (pt.actual_value != null)
              portfolioByDate.set(pt.predicted_for, pt.actual_value);
          }
        }
        // Fallback: índice equiponderado de las 9 acciones si P3 no tiene actuals.
        if (portfolioByDate.size < 2) {
          const stocks = variables.filter((v) => v.kind === "stock");
          const stockSeries = await Promise.all(
            stocks.map(async (s) => ({
              id: s.id,
              obs: await api
                .getObservations(s.id, { from, limit: 400 })
                .catch(() => [] as Observation[]),
            })),
          );
          portfolioByDate = buildEqualWeightIndex(stockSeries);
        }

        const benchMaps = new Map<string, Map<string, number>>();
        for (const { id, obs } of benchSeries) {
          benchMaps.set(id, new Map(obs.map((o) => [o.observed_on, o.value])));
        }

        // Eje maestro = unión de fechas, ordenado.
        const dateSet = new Set<string>(portfolioByDate.keys());
        for (const m of benchMaps.values())
          for (const d of m.keys()) dateSet.add(d);
        const dates = Array.from(dateSet).sort();

        // Forward-fill + % del día por serie.
        const keys = [PORTFOLIO_KEY, ...benchIds];
        const carried = new Map<string, number | null>();
        const prev = new Map<string, number | null>();
        for (const k of keys) {
          carried.set(k, null);
          prev.set(k, null);
        }

        const built: FilledRow[] = dates.map((date) => {
          const row: FilledRow = { date };
          for (const k of keys) {
            const raw =
              k === PORTFOLIO_KEY
                ? portfolioByDate.get(date)
                : benchMaps.get(k)!.get(date);
            const before = carried.get(k) ?? null;
            if (raw != null) carried.set(k, raw);
            const val = carried.get(k) ?? null;
            row[k] = val;
            // % del día contra el día anterior rellenado (cerrado ⇒ 0%).
            const p = prev.get(k);
            row[`${k}__day`] =
              val != null && p != null && p !== 0 ? (val / p - 1) * 100 : null;
            prev.set(k, val);
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

  const meta = useMemo<SeriesMeta[]>(
    () => [PORTFOLIO_META, ...BENCHMARKS.filter((b) => available.includes(b.key))],
    [available],
  );

  // Reancla el acumulado al inicio de la ventana activa.
  const chartData = useMemo(() => {
    if (!rows || rows.length === 0) return [];
    const cutoff = daysAgoISO(range);
    const windowed = rows.filter((r) => String(r.date) >= cutoff);
    const slice = windowed.length >= 2 ? windowed : rows.slice(-2);
    const bases = new Map<string, number>();
    for (const m of meta) {
      const first = slice.find(
        (r) => typeof r[m.key] === "number" && r[m.key] !== null,
      );
      if (first) bases.set(m.key, first[m.key] as number);
    }
    return slice.map((r) => {
      const out: FilledRow = { date: r.date };
      for (const m of meta) {
        const base = bases.get(m.key);
        const v = r[m.key];
        out[m.key] =
          typeof v === "number" && base && base !== 0 ? (v / base - 1) * 100 : null;
        // El % del día viaja en el row para el tooltip (no se grafica).
        out[`${m.key}__day`] = r[`${m.key}__day`] ?? null;
      }
      return out;
    });
  }, [rows, range, meta]);

  return (
    <Card>
      <SectionHeader
        eyebrow="Comparativo"
        title="Comparativo de tu portafolio"
        description="Retorno acumulado (%) desde el inicio del rango. Tu portafolio (P3 Balanced, serie realizada) frente a los principales índices."
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
        <Skeleton className="h-[300px]" />
      ) : chartData.length < 2 ? (
        <EmptyState
          title="Sin datos suficientes todavía"
          description="El comparativo aparece cuando hay al menos dos observaciones en el rango. Ejecuta la ingesta diaria para poblar las series."
        />
      ) : (
        <>
          <div className="w-full" style={{ height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={chartData}
                margin={{ top: 8, right: 12, left: -8, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="date"
                  tickFormatter={(v) => fmtDate(String(v)).slice(0, 6)}
                  tickLine={false}
                  axisLine={false}
                  minTickGap={32}
                />
                <YAxis
                  tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
                  tickLine={false}
                  axisLine={false}
                  width={52}
                />
                {/* Línea base de referencia en 0%. */}
                <ReferenceLine
                  y={0}
                  stroke="var(--color-text3)"
                  strokeDasharray="4 4"
                  strokeWidth={1}
                />
                <Tooltip
                  content={<ComparisonTooltip meta={meta} />}
                  cursor={{ stroke: "var(--color-border)" }}
                />
                {meta.map((m) => (
                  <Line
                    key={m.key}
                    type="monotone"
                    dataKey={m.key}
                    name={m.label}
                    stroke={m.color}
                    strokeWidth={m.width}
                    strokeOpacity={m.opacity}
                    dot={false}
                    activeDot={{ r: m.key === PORTFOLIO_KEY ? 4 : 3, strokeWidth: 0 }}
                    isAnimationActive={false}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Leyenda */}
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[11px] text-[var(--color-text2)]">
            {meta.map((m) => (
              <span key={m.key} className="inline-flex items-center gap-1.5">
                <span
                  className="inline-block rounded-full"
                  style={{
                    width: m.key === PORTFOLIO_KEY ? 14 : 10,
                    height: m.key === PORTFOLIO_KEY ? 3 : 2,
                    background: m.color,
                    opacity: m.opacity,
                  }}
                  aria-hidden
                />
                {m.label}
              </span>
            ))}
          </div>

          {pending.length > 0 && (
            <p className="mt-3 text-[11px] text-[var(--color-text3)]">
              Pendientes de ingesta (sin datos aún):{" "}
              {pending
                .map((id) => BENCHMARKS.find((b) => b.key === id)?.label ?? id)
                .join(" · ")}
              .
            </p>
          )}
        </>
      )}
    </Card>
  );
}

// ─── Tooltip: "+X.XX% día · +Y.YY% acumulado" por serie ──────────────────────

function ComparisonTooltip({
  active,
  payload,
  label,
  meta,
}: {
  active?: boolean;
  payload?: Array<{
    dataKey?: string | number;
    value?: number;
    color?: string;
    payload?: Record<string, number | string | null>;
  }>;
  label?: string | number;
  meta?: SeriesMeta[];
}) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload ?? {};
  const labelFor = (key: string) =>
    meta?.find((m) => m.key === key)?.label ?? key;

  return (
    <div className="rounded-[10px] border border-[var(--color-border2)] bg-[var(--color-bg)] p-3 text-[12px] shadow-[0_8px_24px_-8px_rgba(0,0,0,0.6)]">
      <div className="mb-1.5 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
        {fmtDate(String(label))}
      </div>
      <div className="space-y-1.5">
        {payload.map((p, i) => {
          const key = String(p.dataKey);
          const cum = typeof p.value === "number" ? p.value : null;
          const dayRaw = row[`${key}__day`];
          const day = typeof dayRaw === "number" ? dayRaw : null;
          return (
            <div key={i} className="flex items-center gap-3">
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ background: p.color }}
                aria-hidden
              />
              <span className="min-w-[120px] text-[var(--color-text2)]">
                {labelFor(key)}
              </span>
              <span className="ml-auto font-mono tabular">
                <SignedPct value={day} /> día
                <span className="mx-1 text-[var(--color-text3)]">·</span>
                <SignedPct value={cum} /> acumulado
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SignedPct({ value }: { value: number | null }) {
  if (value == null)
    return <span className="text-[var(--color-text3)]">—</span>;
  const color =
    value >= 0 ? "var(--color-green)" : "var(--color-red)";
  const txt = `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
  return <span style={{ color }}>{txt}</span>;
}

// ─── Índice equiponderado base-precio (fallback si P3 no tiene actuals) ───────

function buildEqualWeightIndex(
  stockSeries: { id: string; obs: Observation[] }[],
): Map<string, number> {
  const firsts = new Map<string, number>();
  for (const { id, obs } of stockSeries) {
    if (obs.length > 0) firsts.set(id, obs[0].value);
  }
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
