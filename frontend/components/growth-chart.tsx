"use client";

/**
 * Growth of $10,000 — the Portfolios page centerpiece.
 *
 * One line per risk profile (P1–P5, cool→warm color scale) plus the market
 * benchmark as a dashed gray line, all normalized to $10,000 at the start of
 * the selected window. Toggles re-fetch per window (30/90/360 días, default
 * 90); each window is a separate SWR key so switching back is instant.
 * Legend shows the final value + total return per profile and click-toggles
 * line visibility.
 */

import { useMemo, useState } from "react";
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

import {
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  SectionHeader,
} from "@/components/primitives";
import { useCached } from "@/hooks/use-cached";
import { api, type GrowthResponse, type GrowthSeries } from "@/lib/api";
import { useLocale, useTranslations } from "next-intl";

type WindowKey = 30 | 90 | 360;
const WINDOW_KEYS: WindowKey[] = [30, 90, 360];

// Cool (P1) → warm (P5); benchmark dashed gray.
const SERIES_STYLE: Record<string, { color: string; dash?: string; width: number }> = {
  P1: { color: "var(--color-blue)", width: 1.75 },
  P2: { color: "var(--color-cyan)", width: 1.75 },
  P3: { color: "var(--color-green)", width: 1.75 },
  P4: { color: "var(--color-amber)", width: 1.75 },
  P5: { color: "var(--color-red)", width: 1.75 },
  BENCH: { color: "var(--color-text3)", dash: "6 4", width: 1.5 },
};

export function GrowthChart() {
  const t = useTranslations("growth");
  const tp = useTranslations("profiles");
  const locale = useLocale();
  const [window, setWindow] = useState<WindowKey>(90);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  // Localized display label for a series ("P3 Balanceado", or the benchmark).
  const labelFor = (profile: string) =>
    profile === "BENCH" ? t("benchmark") : `${profile} ${tp(`${profile}.name`)}`;

  // Per-window SWR key → toggles re-fetch; revisited windows serve instantly.
  const { data, isCold } = useCached<GrowthResponse>(
    `portfolio-growth-${window}`,
    () => api.getPortfolioGrowth(window),
  );

  const series = useMemo(
    () => (data?.series ?? []).filter((s) => s.points.length >= 2),
    [data],
  );

  // Pivot to one row per date: { date, P1: v, P2: v, ..., BENCH: v }
  const chartData = useMemo(() => {
    const byDate = new Map<string, Record<string, number | string | null>>();
    for (const s of series) {
      for (const p of s.points) {
        if (!byDate.has(p.date)) byDate.set(p.date, { date: p.date });
        byDate.get(p.date)![s.profile] = p.value;
      }
    }
    return Array.from(byDate.values()).sort((a, b) =>
      String(a.date).localeCompare(String(b.date)),
    );
  }, [series]);

  const toggle = (profile: string) =>
    setHidden((h) => {
      const next = new Set(h);
      if (next.has(profile)) next.delete(profile);
      else next.add(profile);
      return next;
    });

  return (
    <Card>
      <SectionHeader
        eyebrow={t("eyebrow")}
        title={t("title")}
        description={t("subtitle")}
        right={
          <div className="inline-flex items-center gap-1 rounded-none bg-[var(--color-bg3)] p-1">
            {WINDOW_KEYS.map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => setWindow(w)}
                aria-pressed={window === w}
                className={`rounded-none px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  window === w
                    ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
                    : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
                }`}
              >
                {t(`window_${w}`)}
              </button>
            ))}
          </div>
        }
      />

      {isCold ? (
        <div
          className="h-[320px] w-full animate-pulse rounded-none bg-[var(--color-bg3)]"
          aria-hidden
        />
      ) : series.length === 0 ? (
        <EmptyState title={t("empty_title")} description={t("empty_desc")} />
      ) : (
        <>
          <div className="w-full" style={{ height: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={chartData}
                margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                <XAxis
                  dataKey="date"
                  tickFormatter={(v) => fmtDate(String(v), locale).slice(0, 6)}
                  tickLine={false}
                  axisLine={false}
                  minTickGap={32}
                />
                <YAxis
                  tickFormatter={(v) =>
                    `$${fmtNumber(Number(v), { decimals: 0, compact: true })}`
                  }
                  tickLine={false}
                  axisLine={false}
                  width={56}
                  domain={["auto", "auto"]}
                />
                <ReferenceLine
                  y={10000}
                  stroke="var(--color-text3)"
                  strokeDasharray="4 4"
                  strokeWidth={1}
                />
                <Tooltip
                  content={
                    <GrowthTooltip
                      hidden={hidden}
                      labelFor={labelFor}
                      locale={locale}
                    />
                  }
                  cursor={{ stroke: "var(--color-border)" }}
                />
                {series.map((s) => {
                  const style = SERIES_STYLE[s.profile] ?? SERIES_STYLE.BENCH;
                  return (
                    <Line
                      key={s.profile}
                      type="monotone"
                      dataKey={s.profile}
                      name={labelFor(s.profile)}
                      hide={hidden.has(s.profile)}
                      stroke={style.color}
                      strokeWidth={style.width}
                      strokeDasharray={style.dash}
                      dot={false}
                      activeDot={{ r: 3.5, strokeWidth: 0 }}
                      isAnimationActive={false}
                      connectNulls
                    />
                  );
                })}
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Legend: final value + total return per profile; click to show/hide */}
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
            {series.map((s) => (
              <LegendItem
                key={s.profile}
                series={s}
                label={labelFor(s.profile)}
                showHideTitle={hidden.has(s.profile) ? t("show_line") : t("hide_line")}
                hidden={hidden.has(s.profile)}
                onToggle={() => toggle(s.profile)}
              />
            ))}
          </div>
        </>
      )}
    </Card>
  );
}

function LegendItem({
  series,
  label,
  showHideTitle,
  hidden,
  onToggle,
}: {
  series: GrowthSeries;
  label: string;
  showHideTitle: string;
  hidden: boolean;
  onToggle: () => void;
}) {
  const style = SERIES_STYLE[series.profile] ?? SERIES_STYLE.BENCH;
  const final = series.points[series.points.length - 1]?.value ?? 10000;
  const ret = (final - 10000) / 10000;
  const retTxt = `${ret >= 0 ? "+" : ""}${(ret * 100).toFixed(1)}%`;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={!hidden}
      title={showHideTitle}
      className={`inline-flex items-center gap-2 rounded-none bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11.5px] transition-opacity ${
        hidden ? "opacity-40" : "hover:bg-[var(--color-bg4)]"
      }`}
    >
      <span
        className="inline-block h-[3px] w-4 rounded-full"
        style={{
          background: style.color,
          backgroundImage: style.dash
            ? `repeating-linear-gradient(90deg, ${style.color} 0 4px, transparent 4px 7px)`
            : undefined,
        }}
        aria-hidden
      />
      <span className="font-medium text-[var(--color-text2)]">{label}</span>
      <span className="font-mono tabular text-[var(--color-text)]">
        ${fmtNumber(final, { decimals: 0 })}
      </span>
      <span
        className="font-mono tabular"
        style={{ color: ret >= 0 ? "var(--color-green)" : "var(--color-red)" }}
      >
        {retTxt}
      </span>
    </button>
  );
}

function GrowthTooltip({
  active,
  payload,
  label,
  hidden,
  labelFor,
  locale,
}: {
  active?: boolean;
  payload?: Array<{ dataKey?: string | number; value?: number; color?: string }>;
  label?: string | number;
  hidden?: Set<string>;
  labelFor?: (profile: string) => string;
  locale?: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const rows = payload
    .filter((p) => !hidden?.has(String(p.dataKey)) && typeof p.value === "number")
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));

  return (
    <div className="rounded-none border border-[var(--color-border2)] bg-[var(--color-bg)] p-3 text-[12px] shadow-[0_8px_24px_-8px_rgba(0,0,0,0.6)]">
      <div className="mb-1.5 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
        {fmtDate(String(label), locale)}
      </div>
      <div className="space-y-1">
        {rows.map((p, i) => (
          <div key={i} className="flex items-center gap-3">
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ background: p.color }}
              aria-hidden
            />
            <span className="min-w-[130px] text-[var(--color-text2)]">
              {labelFor ? labelFor(String(p.dataKey)) : String(p.dataKey)}
            </span>
            <span className="ml-auto font-mono tabular text-[var(--color-text)]">
              ${fmtNumber(p.value ?? 0, { decimals: 2 })}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
