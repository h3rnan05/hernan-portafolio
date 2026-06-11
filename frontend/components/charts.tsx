"use client";

/**
 * Recharts wrappers themed to our design tokens. Server-rendered shells with
 * the chart bodies marked "use client" — keeps initial paint snappy.
 */

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { fmtDate, fmtNumber } from "@/components/primitives";

type SeriesKey = {
  key: string;
  label: string;
  color: string;
  /** Line thickness in px. Defaults to 1.75. Use a heavier value to emphasise
   *  a primary series (e.g. the user's own portfolio). */
  width?: number;
  /** Render the line in a fainter tone — for secondary/benchmark series so the
   *  emphasised series reads as the focal point. */
  faded?: boolean;
};

const COLORS = {
  green: "var(--color-green)",
  red: "var(--color-red)",
  amber: "var(--color-amber)",
  blue: "var(--color-blue)",
  cyan: "var(--color-cyan)",
  violet: "var(--color-violet)",
  text2: "var(--color-text2)",
  text3: "var(--color-text3)",
  border: "var(--color-border)",
  bg2: "var(--color-bg2)",
};

function CustomTooltip({
  active,
  payload,
  label,
  unit = "",
  decimals = 2,
  percent = false,
  locale,
}: {
  active?: boolean;
  payload?: Array<{
    name?: string;
    value?: number;
    color?: string;
    dataKey?: string | number;
    payload?: Record<string, number | string | null>;
  }>;
  label?: string | number;
  unit?: string;
  decimals?: number;
  /** Treat each series value as a signed % (e.g. cumulative return): the value
   *  is coloured green/red and rendered with a sign. When the data row carries a
   *  `<dataKey>__d` field, that day's return is shown alongside it. */
  percent?: boolean;
  /** Active UI locale ("es" | "en") for date formatting + the day-return label. */
  locale?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const dateLabel =
    typeof label === "string" || typeof label === "number"
      ? fmtDate(String(label), locale)
      : String(label);
  const dayLabel = locale === "en" ? "day" : "día";
  const signed = (x: number, d = 1) => `${x >= 0 ? "+" : ""}${x.toFixed(d)}%`;
  return (
    <div className="rounded-[10px] border border-[var(--color-border2)] bg-[var(--color-bg)] p-3 text-[12px] shadow-[0_8px_24px_-8px_rgba(0,0,0,0.6)]">
      <div className="mb-1.5 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
        {dateLabel}
      </div>
      <div className="space-y-1">
        {payload.map((p, i) => {
          const v = p.value ?? null;
          const day =
            percent && p.payload ? p.payload[`${p.dataKey}__d`] : null;
          return (
            <div key={i} className="flex items-center gap-3">
              <span
                className="size-2 rounded-full"
                style={{ background: p.color }}
                aria-hidden
              />
              <span className="text-[var(--color-text2)]">{p.name ?? ""}</span>
              {percent && typeof v === "number" ? (
                <span
                  className={`ml-auto font-mono tabular ${
                    v >= 0
                      ? "text-[var(--color-green)]"
                      : "text-[var(--color-red)]"
                  }`}
                >
                  {signed(v, decimals)}
                </span>
              ) : (
                <span className="ml-auto font-mono tabular text-[var(--color-text)]">
                  {fmtNumber(v, { decimals })}
                  {unit}
                </span>
              )}
              {typeof day === "number" && (
                <span className="w-[70px] text-right font-mono tabular text-[10px] text-[var(--color-text3)]">
                  {dayLabel} {signed(day, 1)}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── TimeSeries (multi-series line) ──────────────────────────────────────────

export function TimeSeriesChart({
  data,
  series,
  height = 260,
  yDecimals = 2,
  yUnit = "",
  showLegend = true,
  hidden,
  onToggleSeries,
  percent = false,
  locale,
}: {
  data: Array<Record<string, number | string | null>>;
  series: SeriesKey[];
  height?: number;
  yDecimals?: number;
  yUnit?: string;
  showLegend?: boolean;
  /** Series keys currently hidden from the chart (their line is not drawn and
   *  they drop out of the tooltip). */
  hidden?: string[];
  /** When provided, legend entries become buttons that toggle their series.
   *  Without it the legend stays a static label row (back-compat default). */
  onToggleSeries?: (key: string) => void;
  /** Treat values as signed % (cumulative return): the Y axis renders +/-%, a
   *  dashed 0% baseline is drawn, and the tooltip shows coloured %s + day return. */
  percent?: boolean;
  /** Active UI locale ("es" | "en") so the date axis + tooltip format per-locale. */
  locale?: string;
}) {
  const isHidden = (key: string) => hidden?.includes(key) ?? false;
  const fmtPctTick = (v: number) =>
    `${v > 0 ? "+" : ""}${fmtNumber(v, { decimals: 0 })}%`;
  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 8, right: 12, left: -10, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} />
          <XAxis
            dataKey="date"
            tickFormatter={(v) => fmtDate(String(v), locale).slice(0, 6)}
            tickLine={false}
            axisLine={false}
            minTickGap={32}
          />
          <YAxis
            tickFormatter={(v) =>
              percent
                ? fmtPctTick(v)
                : fmtNumber(v, { decimals: yDecimals, compact: true })
            }
            tickLine={false}
            axisLine={false}
            width={56}
          />
          {percent && (
            <ReferenceLine
              y={0}
              stroke={COLORS.text3}
              strokeDasharray="4 4"
              strokeOpacity={0.5}
              ifOverflow="extendDomain"
            />
          )}
          <Tooltip
            content={
              <CustomTooltip
                unit={yUnit}
                decimals={yDecimals}
                percent={percent}
                locale={locale}
              />
            }
            cursor={{ stroke: COLORS.border, strokeWidth: 1 }}
          />
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={s.width ?? 1.75}
              strokeOpacity={s.faded ? 0.6 : 1}
              hide={isHidden(s.key)}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      {showLegend && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[11px] text-[var(--color-text2)]">
          {series.map((s) => {
            const off = isHidden(s.key);
            const dot = (
              <span
                className="inline-block size-2 rounded-full transition-opacity"
                style={{ background: s.color, opacity: off ? 0.3 : 1 }}
                aria-hidden
              />
            );
            // Interactive (toggle) variant — only when a handler is supplied.
            if (onToggleSeries) {
              return (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => onToggleSeries(s.key)}
                  aria-pressed={!off}
                  className={`inline-flex items-center gap-1.5 rounded-[6px] px-1 py-0.5 transition-colors hover:text-[var(--color-text)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-border2)] ${
                    off ? "text-[var(--color-text3)] line-through" : ""
                  }`}
                  title={off ? `Mostrar ${s.label}` : `Ocultar ${s.label}`}
                >
                  {dot}
                  {s.label}
                </button>
              );
            }
            // Static label row (back-compat default).
            return (
              <span key={s.key} className="inline-flex items-center gap-1.5">
                {dot}
                {s.label}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Area chart (portfolio value over time, w/ predicted band) ──────────────

export function AreaChartCmp({
  data,
  dataKey,
  label,
  color = COLORS.green,
  height = 220,
  yDecimals = 2,
  yUnit = "",
}: {
  data: Array<Record<string, number | string | null>>;
  dataKey: string;
  label: string;
  color?: string;
  height?: number;
  yDecimals?: number;
  yUnit?: string;
}) {
  const gradId = `grad-${dataKey}`;
  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 8, right: 12, left: -10, bottom: 0 }}
        >
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} />
          <XAxis
            dataKey="date"
            tickFormatter={(v) => fmtDate(String(v)).slice(0, 6)}
            tickLine={false}
            axisLine={false}
            minTickGap={32}
          />
          <YAxis
            tickFormatter={(v) =>
              fmtNumber(v, { decimals: yDecimals, compact: true })
            }
            tickLine={false}
            axisLine={false}
            width={56}
          />
          <Tooltip
            content={<CustomTooltip unit={yUnit} decimals={yDecimals} />}
            cursor={{ stroke: COLORS.border }}
          />
          <Area
            type="monotone"
            dataKey={dataKey}
            name={label}
            stroke={color}
            strokeWidth={1.75}
            fill={`url(#${gradId})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Horizontal bar chart for coefficients ──────────────────────────────────

export function HorizontalBarChart({
  data,
  height = 240,
  decimals = 4,
}: {
  data: Array<{ label: string; value: number }>;
  height?: number;
  decimals?: number;
}) {
  const max = Math.max(...data.map((d) => Math.abs(d.value)), 1e-9);
  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 8, right: 16, left: 64, bottom: 0 }}
        >
          <CartesianGrid
            strokeDasharray="3 3"
            stroke={COLORS.border}
            horizontal={false}
          />
          <XAxis
            type="number"
            domain={[-max * 1.1, max * 1.1]}
            tickFormatter={(v) =>
              fmtNumber(v as number, { decimals, compact: true })
            }
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            type="category"
            dataKey="label"
            tickLine={false}
            axisLine={false}
            width={64}
          />
          <ReferenceLine x={0} stroke={COLORS.border} />
          <Tooltip content={<CustomTooltip decimals={decimals} />} cursor={false} />
          <Bar dataKey="value" radius={[4, 4, 4, 4]} isAnimationActive={false}>
            {data.map((d, i) => (
              <Cell
                key={i}
                fill={d.value >= 0 ? COLORS.green : COLORS.red}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Forecast band chart (HER-17) ────────────────────────────────────────────

type ForecastRow = {
  date: string;
  hist: number | null;
  central: number | null;
  // Band drawn as two stacked areas: an invisible base (= lower) and a filled
  // span (= upper − lower), so the shaded region sits between lower and upper.
  bandBase: number | null;
  bandSpan: number | null;
  lower: number | null;
  upper: number | null;
};

function ForecastTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ payload?: ForecastRow }>;
  label?: string | number;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  if (!row) return null;
  const fmt = (v: number | null | undefined) =>
    v == null ? "—" : fmtNumber(v, { decimals: 2 });
  return (
    <div className="rounded-[10px] border border-[var(--color-border2)] bg-[var(--color-bg)] p-3 text-[12px] shadow-[0_8px_24px_-8px_rgba(0,0,0,0.6)]">
      <div className="mb-1.5 font-medium text-[var(--color-text2)]">
        {fmtDate(String(label))}
      </div>
      {row.hist != null && (
        <Row swatch={COLORS.green} name="Histórico" value={fmt(row.hist)} />
      )}
      {row.central != null && (
        <>
          <Row swatch={COLORS.cyan} name="Central" value={fmt(row.central)} />
          <Row swatch="transparent" name="Rango 90%" value={`${fmt(row.lower)} – ${fmt(row.upper)}`} />
        </>
      )}
    </div>
  );
}

function Row({ swatch, name, value }: { swatch: string; name: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-0.5">
      <span className="flex items-center gap-1.5 text-[var(--color-text3)]">
        <span className="inline-block h-2 w-2 rounded-full" style={{ background: swatch }} />
        {name}
      </span>
      <span className="tabular text-[var(--color-text)]">{value}</span>
    </div>
  );
}

export function ForecastBandChart({
  data,
  height = 320,
}: {
  data: ForecastRow[];
  height?: number;
}) {
  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, left: -10, bottom: 0 }}>
          <defs>
            <linearGradient id="forecast-band" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLORS.cyan} stopOpacity={0.22} />
              <stop offset="100%" stopColor={COLORS.cyan} stopOpacity={0.06} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} />
          <XAxis
            dataKey="date"
            tickFormatter={(v) => fmtDate(String(v)).slice(0, 6)}
            tickLine={false}
            axisLine={false}
            minTickGap={28}
          />
          <YAxis
            tickFormatter={(v) => fmtNumber(v, { decimals: 0, compact: true })}
            tickLine={false}
            axisLine={false}
            width={56}
            domain={["auto", "auto"]}
          />
          <Tooltip content={<ForecastTooltip />} cursor={{ stroke: COLORS.border }} />
          {/* Invisible base lifts the band up to `lower`. */}
          <Area
            dataKey="bandBase"
            stackId="band"
            stroke="none"
            fill="transparent"
            isAnimationActive={false}
            connectNulls
          />
          {/* Filled span from `lower` to `upper`. */}
          <Area
            dataKey="bandSpan"
            stackId="band"
            name="Rango 90%"
            stroke="none"
            fill="url(#forecast-band)"
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="hist"
            name="Histórico"
            stroke={COLORS.green}
            strokeWidth={1.75}
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
          <Line
            type="monotone"
            dataKey="central"
            name="Central"
            stroke={COLORS.cyan}
            strokeWidth={1.75}
            strokeDasharray="5 3"
            dot={false}
            isAnimationActive={false}
            connectNulls
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Sparkline (inline mini chart) ───────────────────────────────────────────

export function Sparkline({
  values,
  width = 80,
  height = 28,
  color,
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
}) {
  if (values.length === 0)
    return (
      <span
        className="inline-block text-[var(--color-text3)] tabular"
        style={{ width, height }}
      >
        —
      </span>
    );
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1 || 1)) * width;
      const y = height - ((v - min) / range) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const trendUp = values[values.length - 1] >= values[0];
  const c = color ?? (trendUp ? COLORS.green : COLORS.red);
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      aria-hidden
      className="inline-block align-middle"
    >
      <polyline
        fill="none"
        stroke={c}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={pts}
      />
    </svg>
  );
}
