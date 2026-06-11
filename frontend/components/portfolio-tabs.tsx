"use client";

/**
 * Portfolio risk-profile tabs (HER task 7).
 *
 * Horizontal tabs for P1 Conservative → P5 Aggressive. Selecting a tab shows
 * that profile's full detail: current weights, the weight bar, and the
 * historical weight-evolution chart. Data is fetched server-side and passed in.
 */

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";

import { TimeSeriesChart } from "@/components/charts";
import {
  Badge,
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  fmtPct,
  SectionHeader,
} from "@/components/primitives";
import type { Portfolio } from "@/lib/api";

const PROFILE_TONES: Record<string, "blue" | "cyan" | "green" | "amber" | "red"> = {
  P1_CONSERVATIVE: "blue",
  P2_MOD_CONSERVATIVE: "cyan",
  P3_BALANCED: "green",
  P4_MOD_AGGRESSIVE: "amber",
  P5_AGGRESSIVE: "red",
};

const SERIES_COLORS = [
  "var(--color-green)",
  "var(--color-cyan)",
  "var(--color-blue)",
  "var(--color-violet)",
  "var(--color-amber)",
  "var(--color-red)",
  "var(--color-green-dim)",
  "var(--color-text2)",
  "var(--color-text3)",
];

export type ProfileDetail = {
  portfolio: Portfolio;
  tickers: string[];
  chartData: Array<Record<string, string | number | null>>;
  firstSnapshot: string | null;
  lastSnapshot: string | null;
};

export function PortfolioTabs({
  profiles,
  initialId,
}: {
  profiles: ProfileDetail[];
  initialId?: string;
}) {
  const [activeId, setActiveId] = useState(
    initialId && profiles.some((p) => p.portfolio.id === initialId)
      ? initialId
      : profiles[0]?.portfolio.id,
  );
  const active = profiles.find((p) => p.portfolio.id === activeId) ?? profiles[0];
  const tp = useTranslations("profiles");
  if (!active) return null;

  return (
    <div>
      {/* Horizontal tab bar */}
      <div
        className="mb-6 flex items-center gap-1 overflow-x-auto rounded-[10px] bg-[var(--color-bg3)] p-1"
        role="tablist"
      >
        {profiles.map((p) => {
          const isActive = p.portfolio.id === active.portfolio.id;
          const tone = PROFILE_TONES[p.portfolio.id] ?? "blue";
          const code = p.portfolio.id.split("_")[0];
          return (
            <button
              key={p.portfolio.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              onClick={() => setActiveId(p.portfolio.id)}
              className={`flex shrink-0 items-center gap-2 rounded-[7px] px-3.5 py-2 text-[12.5px] font-medium transition-colors ${
                isActive
                  ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
                  : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
              }`}
            >
              <Badge tone={tone}>{code}</Badge>
              <span className="hidden sm:inline">{tp(`${code}.name`)}</span>
            </button>
          );
        })}
      </div>

      <ProfilePanel detail={active} />
    </div>
  );
}

function ProfilePanel({ detail }: { detail: ProfileDetail }) {
  const t = useTranslations("portfolios");
  const tp = useTranslations("profiles");
  const tc = useTranslations("common");
  const locale = useLocale();
  const { portfolio, tickers, chartData } = detail;
  const sorted = Object.entries(portfolio.weights).sort((a, b) => b[1] - a[1]);
  const code = portfolio.id.split("_")[0];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">
            {code} {tp(`${code}.name`)}
          </h2>
          {(() => {
            const desc = tp.has(`${code}.description`)
              ? tp(`${code}.description`)
              : portfolio.description;
            return desc ? (
              <p className="mt-1 max-w-2xl text-[13px] text-[var(--color-text2)]">
                {desc}
              </p>
            ) : null;
          })()}
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
              {t("mape_30d")}
            </div>
            <div className="mt-0.5 font-mono tabular text-base font-semibold">
              {portfolio.mape_30d !== null ? fmtPct(portfolio.mape_30d) : "—"}
            </div>
          </div>
          <Link
            href={`/portfolios/${portfolio.id}`}
            className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
          >
            {tc("full_history")}
          </Link>
        </div>
      </div>

      {/* Current weights */}
      <Card>
        <SectionHeader
          eyebrow={t("current_eyebrow")}
          title={t("current_title")}
          description={t("current_desc")}
        />
        <div className="flex h-3 overflow-hidden rounded-[6px] bg-[var(--color-bg3)]">
          {sorted.map(([ticker, w], i) => (
            <div
              key={ticker}
              title={`${ticker}: ${fmtPct(w, { decimals: 1 })}`}
              style={{ width: `${(w * 100).toFixed(2)}%` }}
              className={`h-full ${weightColor(i)}`}
            />
          ))}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 md:grid-cols-3">
          {sorted.map(([ticker, w], i) => (
            <div
              key={ticker}
              className="flex items-center justify-between gap-2 text-[12.5px]"
            >
              <span className="inline-flex items-center gap-2 font-mono text-[var(--color-text2)]">
                <span
                  className="inline-block size-1.5 rounded-full"
                  style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }}
                />
                {ticker}
              </span>
              <span className="font-mono tabular text-[var(--color-text)]">
                {fmtNumber(w * 100, { decimals: 2 })}%
              </span>
            </div>
          ))}
        </div>
      </Card>

      {/* History */}
      <Card>
        <SectionHeader
          eyebrow={t("snapshots_count", { count: chartData.length })}
          title={t("history_title")}
          description={t("history_desc")}
        />
        {chartData.length < 2 ? (
          <EmptyState
            title={
              chartData.length === 0
                ? t("no_snapshots_title")
                : t("one_snapshot_title")
            }
            description={t("snapshots_desc")}
          />
        ) : (
          <TimeSeriesChart
            data={chartData}
            series={tickers.map((tk, i) => ({
              key: tk,
              label: tk,
              color: SERIES_COLORS[i % SERIES_COLORS.length],
            }))}
            yDecimals={1}
            yUnit="%"
            height={340}
            locale={locale}
          />
        )}
        {detail.firstSnapshot && detail.lastSnapshot && (
          <div className="mt-3 text-[11px] text-[var(--color-text3)]">
            {t("first_snapshot", {
              first: fmtDate(detail.firstSnapshot, locale),
              last: fmtDate(detail.lastSnapshot, locale),
            })}
          </div>
        )}
      </Card>
    </div>
  );
}

function weightColor(i: number): string {
  const colors = [
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
  return colors[i % colors.length];
}
