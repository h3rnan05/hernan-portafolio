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

type SeriesKey = { key: string; label: string; color: string };

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
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: number; color?: string }>;
  label?: string | number;
  unit?: string;
  decimals?: number;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const dateLabel =
    typeof label === "string" || typeof label === "number"
      ? fmtDate(String(label))
      : String(label);
  return (
    <div className="rounded-[10px] border border-[var(--color-border2)] bg-[var(--color-bg)] p-3 text-[12px] shadow-[0_8px_24px_-8px_rgba(0,0,0,0.6)]">
      <div className="mb-1.5 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
        {dateLabel}
      </div>
      <div className="space-y-1">
        {payload.map((p, i) => (
          <div key={i} className="flex items-center gap-3">
            <span
              className="size-2 rounded-full"
              style={{ background: p.color }}
              aria-hidden
            />
            <span className="text-[var(--color-text2)]">{p.name ?? ""}</span>
            <span className="ml-auto font-mono tabular text-[var(--color-text)]">
              {fmtNumber(p.value ?? null, { decimals })}
              {unit}
            </span>
          </div>
        ))}
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
}: {
  data: Array<Record<string, number | string | null>>;
  series: SeriesKey[];
  height?: number;
  yDecimals?: number;
  yUnit?: string;
  showLegend?: boolean;
}) {
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
            content={
              <CustomTooltip unit={yUnit} decimals={yDecimals} />
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
              strokeWidth={1.75}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      {showLegend && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[11px] text-[var(--color-text2)]">
          {series.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span
                className="inline-block size-2 rounded-full"
                style={{ background: s.color }}
                aria-hidden
              />
              {s.label}
            </span>
          ))}
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
