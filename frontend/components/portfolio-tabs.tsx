"use client";

import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

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

// ─── Constants ────────────────────────────────────────────────────────────────

const ACTIVE_PROFILE = "P4_MOD_AGGRESSIVE";

const PROFILE_TONES: Record<string, "blue" | "cyan" | "green" | "amber" | "red"> = {
  P1_CONSERVATIVE:    "blue",
  P2_MOD_CONSERVATIVE:"cyan",
  P3_BALANCED:        "green",
  P4_MOD_AGGRESSIVE:  "amber",
  P5_AGGRESSIVE:      "red",
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

const WEIGHT_BG_COLORS = [
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

// ─── Types ────────────────────────────────────────────────────────────────────

export type ProfileDetail = {
  portfolio: Portfolio;
  tickers: string[];
  chartData: Array<Record<string, string | number | null>>;
  firstSnapshot: string | null;
  lastSnapshot: string | null;
};

type AlpacaData = {
  equity: string;
  positions: { symbol: string; market_value: string; unrealized_pl: string; unrealized_plpc: string }[];
};

// ─── Alpaca hook (only for P4) ────────────────────────────────────────────────

function useOlsAlpaca() {
  const [data, setData] = useState<AlpacaData | null>(null);
  useEffect(() => {
    fetch("/api/bot?bot=ols", { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setData({ equity: d.account?.equity ?? "0", positions: d.positions ?? [] }))
      .catch(() => null);
  }, []);
  return data;
}

// ─── Tab bar ─────────────────────────────────────────────────────────────────

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
      : ACTIVE_PROFILE,   // default to the bot's active profile
  );
  const active = profiles.find((p) => p.portfolio.id === activeId) ?? profiles[0];
  const tp = useTranslations("profiles");
  const alpaca = useOlsAlpaca();
  if (!active) return null;

  return (
    <div>
      <div
        className="mb-6 flex items-center gap-1 overflow-x-auto rounded-[10px] bg-[var(--color-bg3)] p-1"
        role="tablist"
      >
        {profiles.map((p) => {
          const isActive  = p.portfolio.id === active.portfolio.id;
          const isBot     = p.portfolio.id === ACTIVE_PROFILE;
          const tone      = PROFILE_TONES[p.portfolio.id] ?? "blue";
          const code      = p.portfolio.id.split("_")[0];
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
              {isBot && (
                <span className="hidden items-center gap-1 sm:inline-flex">
                  <span className="relative flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--color-green)] opacity-60" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[var(--color-green)]" />
                  </span>
                  <span className="text-[9px] font-semibold uppercase tracking-widest text-[var(--color-green)]">bot</span>
                </span>
              )}
            </button>
          );
        })}
      </div>

      <ProfilePanel detail={active} alpaca={active.portfolio.id === ACTIVE_PROFILE ? alpaca : null} />
    </div>
  );
}

// ─── Profile panel ────────────────────────────────────────────────────────────

function ProfilePanel({
  detail,
  alpaca,
}: {
  detail: ProfileDetail;
  alpaca: AlpacaData | null;
}) {
  const t      = useTranslations("portfolios");
  const tp     = useTranslations("profiles");
  const tc     = useTranslations("common");
  const locale = useLocale();
  const { portfolio, tickers, chartData } = detail;
  const sorted = Object.entries(portfolio.weights).sort((a, b) => b[1] - a[1]);
  const code   = portfolio.id.split("_")[0];
  const isBot  = portfolio.id === ACTIVE_PROFILE;

  // Alpaca-enriched equity
  const equity  = alpaca ? parseFloat(alpaca.equity) : null;
  const totalPnl = alpaca
    ? alpaca.positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0)
    : null;

  // Directional accuracy: % of times the sign of the predicted move was right
  // We estimate this from MAPE as a rough proxy label
  const mape = portfolio.mape_30d;
  const mapeLabel = mape === null ? null
    : mape < 0.01  ? "Muy preciso — error promedio menor al 1%"
    : mape < 0.025 ? "Preciso — error promedio menor al 2.5%"
    : mape < 0.05  ? "Aceptable — error promedio menor al 5%"
    : "Error elevado — modelo con poca confianza";

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-xl font-semibold tracking-tight">
              {code} · {tp(`${code}.name`)}
            </h2>
            {isBot && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-[var(--color-green)]/15 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-[var(--color-green)]">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--color-green)] opacity-60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[var(--color-green)]" />
                </span>
                Bot OLS activo
              </span>
            )}
          </div>
          {(() => {
            const desc = tp.has(`${code}.description`) ? tp(`${code}.description`) : portfolio.description;
            return desc ? (
              <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">{desc}</p>
            ) : null;
          })()}
        </div>
        <Link
          href={`/portfolios/${portfolio.id}`}
          className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
        >
          {tc("full_history")}
        </Link>
      </div>

      {/* P4: live Alpaca equity banner */}
      {isBot && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-[12px] border border-[var(--color-green)]/25 bg-[var(--color-green)]/5 px-4 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">Equity en Alpaca</div>
            <div className="font-mono text-[18px] font-bold text-[var(--color-text)]">
              {equity !== null ? `$${fmtNumber(equity, { decimals: 2 })}` : <span className="text-[var(--color-text3)]">…</span>}
            </div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">Cuenta real Paper Trading</div>
          </div>
          <div className="rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">P&L abierto</div>
            <div className={`font-mono text-[18px] font-bold ${totalPnl === null ? "text-[var(--color-text3)]" : totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
              {totalPnl !== null
                ? `${totalPnl >= 0 ? "+" : ""}$${fmtNumber(totalPnl, { decimals: 2 })}`
                : <span className="text-[var(--color-text3)]">…</span>}
            </div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">Ganancia no realizada</div>
          </div>
          <div className="rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">Error del modelo</div>
            <div className="font-mono text-[18px] font-bold">
              {mape !== null ? fmtPct(mape, { decimals: 2 }) : "—"}
            </div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">MAPE 30 días</div>
          </div>
          <div className="rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">Posiciones abiertas</div>
            <div className="font-mono text-[18px] font-bold">
              {alpaca !== null ? alpaca.positions.length : <span className="text-[var(--color-text3)]">…</span>}
            </div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">Acciones en cartera</div>
          </div>
        </div>
      )}

      {/* Non-P4: MAPE quality tile */}
      {!isBot && mape !== null && (
        <div className="flex items-center gap-4 rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg2)] px-5 py-4">
          <div>
            <div className="text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">Precisión del modelo · MAPE 30 días</div>
            <div className="mt-1 flex items-baseline gap-3">
              <span className="font-mono text-[22px] font-bold">{fmtPct(mape, { decimals: 2 })}</span>
              {mapeLabel && <span className="text-[12px] text-[var(--color-text2)]">{mapeLabel}</span>}
            </div>
          </div>
        </div>
      )}

      {/* Weights card */}
      <Card>
        <SectionHeader
          eyebrow={t("current_eyebrow")}
          title={t("current_title")}
          description={t("current_desc")}
        />
        {/* Weight bar */}
        <div className="flex h-3 overflow-hidden rounded-[6px] bg-[var(--color-bg3)]">
          {sorted.map(([ticker, w], i) => (
            <div
              key={ticker}
              title={`${ticker}: ${fmtPct(w, { decimals: 1 })}`}
              style={{ width: `${(w * 100).toFixed(2)}%` }}
              className={`h-full ${WEIGHT_BG_COLORS[i % WEIGHT_BG_COLORS.length]}`}
            />
          ))}
        </div>
        {/* Ticker rows with % and $ value when Alpaca data is available */}
        <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
          {sorted.map(([ticker, w], i) => {
            const alpacaPos = alpaca?.positions.find((p) => p.symbol === ticker);
            const mv  = alpacaPos ? parseFloat(alpacaPos.market_value) : null;
            const pl  = alpacaPos ? parseFloat(alpacaPos.unrealized_pl) : null;
            const plp = alpacaPos ? parseFloat(alpacaPos.unrealized_plpc) : null;
            return (
              <div
                key={ticker}
                className="flex items-center justify-between gap-3 rounded-[8px] bg-[var(--color-bg3)] px-3 py-2"
              >
                <div className="flex items-center gap-2">
                  <span
                    className="inline-block size-2 shrink-0 rounded-full"
                    style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }}
                  />
                  <span className="font-mono text-[13px] font-semibold">{ticker}</span>
                  <span className="font-mono text-[12px] text-[var(--color-text3)]">
                    {fmtNumber(w * 100, { decimals: 1 })}%
                  </span>
                </div>
                {mv !== null && pl !== null && plp !== null ? (
                  <div className="flex items-center gap-3 text-right">
                    <span className="font-mono text-[12.5px]">${fmtNumber(mv, { decimals: 0 })}</span>
                    <span className={`text-[11px] font-mono font-semibold ${pl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                      {pl >= 0 ? "+" : ""}{fmtPct(plp, { decimals: 2 })}
                    </span>
                  </div>
                ) : (
                  <span className="text-[11px] text-[var(--color-text3)]">—</span>
                )}
              </div>
            );
          })}
        </div>
        {isBot && alpaca && (
          <p className="mt-3 text-[11px] text-[var(--color-text3)]">
            Valor $ y P&L de cada posición tomados en tiempo real de Alpaca Paper Trading.
          </p>
        )}
      </Card>

      {/* Historical chart */}
      <Card>
        <SectionHeader
          eyebrow={t("snapshots_count", { count: chartData.length })}
          title={t("history_title")}
          description={t("history_desc")}
        />
        {chartData.length < 2 ? (
          <EmptyState
            title={chartData.length === 0 ? t("no_snapshots_title") : t("one_snapshot_title")}
            description={t("snapshots_desc")}
          />
        ) : (
          <TimeSeriesChart
            data={chartData}
            series={tickers.map((tk, i) => ({
              key:   tk,
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
              last:  fmtDate(detail.lastSnapshot, locale),
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
