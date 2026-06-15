"use client";

/**
 * Compares the 3 trading bot strategies against the S&P 500.
 * All bots share one Alpaca paper account — the "account" line shows their
 * combined equity. Individual bot P&L will separate once order tagging is
 * implemented. S&P 500 is fetched from Yahoo Finance and normalized to base-100.
 */

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Badge, Card, fmtNumber, SectionHeader, Skeleton, StatTile } from "@/components/primitives";
import { useCached } from "@/hooks/use-cached";

// ─── Types ───────────────────────────────────────────────────────────────────

type DataPoint = {
  date: string;
  account: number | null;
  sp500: number | null;
  equity: number | null;
  pl: number | null;
};

type BotHistoryResponse = {
  series:        DataPoint[];
  startEquity:   number;
  currentEquity: number;
  totalReturn:   number;
  botNames:      Record<string, string>;
  fetchedAt:     string;
  hasRealData:   boolean;
  error?:        string;
};

const RANGE_OPTIONS = [
  { label: "7D",  days: 7  },
  { label: "1M",  days: 30 },
  { label: "3M",  days: 90 },
];

const BOT_COLORS = {
  ols:     "var(--color-green)",
  capitol: "var(--color-cyan)",
  trailing:"var(--color-violet)",
  combined:"var(--color-green)",
  sp500:   "var(--color-text3)",
};

const BOTS = [
  { key: "ols",      label: "OLS Model Bot",       color: BOT_COLORS.ols,      desc: "Predicciones del modelo de regresión" },
  { key: "capitol",  label: "P1 Conservative Bot",   color: BOT_COLORS.capitol,  desc: "Predicciones OLS · Perfil P1 conservador" },
  { key: "trailing", label: "P0 Ultra Conservador Bot",    color: BOT_COLORS.trailing, desc: "Portafolio conservador · consumo básico baja volatilidad" },
];

// ─── Fetch ───────────────────────────────────────────────────────────────────

async function fetchBotHistory(): Promise<BotHistoryResponse> {
  const res = await fetch("/api/bot-history", { cache: "no-store" });
  return res.json() as Promise<BotHistoryResponse>;
}

// ─── Tooltip ─────────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] p-3 text-[12px] shadow-lg">
      <div className="mb-2 font-medium text-[var(--color-text3)]">{label}</div>
      {payload.map((p) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="size-1.5 rounded-full" style={{ background: p.color }} />
          <span className="text-[var(--color-text2)]">{p.name}:</span>
          <span className="font-mono font-semibold" style={{ color: p.color }}>
            {p.value != null ? `${p.value >= 100 ? "+" : ""}${fmtNumber(p.value - 100, { decimals: 2 })}%` : "—"}
          </span>
        </div>
      ))}
    </div>
  );
}

// ─── Component ───────────────────────────────────────────────────────────────

export function BotComparisonChart() {
  const [rangeDays, setRangeDays] = useState(90);
  const { data, isCold } = useCached("bot-history", fetchBotHistory);

  const cutoffDate = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - rangeDays);
    return d.toISOString().slice(0, 10);
  }, [rangeDays]);

  const chartData = useMemo(() => {
    if (!data?.series) return [];
    return data.series
      .filter((r) => r.date >= cutoffDate)
      .map((r) => ({
        date:    r.date,
        // All 3 bots share one account — combined line
        account: r.account,
        sp500:   r.sp500,
      }));
  }, [data, cutoffDate]);

  const totalReturn    = data?.totalReturn    ?? 0;
  const currentEquity  = data?.currentEquity  ?? 25000;
  const startEquity    = data?.startEquity    ?? 25000;
  const hasRealData    = data?.hasRealData    ?? false;

  // How much the account is beating/lagging S&P 500
  const sp500Return = useMemo(() => {
    if (!data?.series || data.series.length < 2) return null;
    const first = data.series.find((r) => r.sp500 !== null);
    const last  = [...data.series].reverse().find((r) => r.sp500 !== null);
    if (!first || !last || !first.sp500 || !last.sp500) return null;
    return (last.sp500 / first.sp500 - 1) * 100;
  }, [data]);

  const alpha = sp500Return !== null ? totalReturn - sp500Return : null;

  return (
    <Card>
      <SectionHeader
        eyebrow="Alpaca Paper Trading · comparativo"
        title="Bots vs S&P 500"
        description="Los 3 bots comparten una cuenta de paper trading. La línea verde refleja el equity combinado normalizado a 100."
        right={
          <div className="inline-flex items-center gap-1 rounded-none bg-[var(--color-bg3)] p-1">
            {RANGE_OPTIONS.map((opt) => (
              <button
                key={opt.days}
                type="button"
                onClick={() => setRangeDays(opt.days)}
                className={`rounded-none px-2.5 py-1 text-[11px] font-medium transition-colors ${
                  rangeDays === opt.days
                    ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
                    : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        }
      />

      {/* Stats strip */}
      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatTile
          label="Equity actual"
          value={`$${fmtNumber(currentEquity, { decimals: 2 })}`}
          hint={`vs $${fmtNumber(startEquity, { decimals: 0 })} inicial`}
        />
        <StatTile
          label="Retorno bots"
          value={`${totalReturn >= 0 ? "+" : ""}${fmtNumber(totalReturn, { decimals: 2 })}%`}
          delta={totalReturn !== 0 ? {
            value: totalReturn >= 0 ? "ganancia" : "pérdida",
            tone:  totalReturn >= 0 ? "green" : "red",
          } : undefined}
        />
        <StatTile
          label="S&P 500"
          value={sp500Return !== null ? `${sp500Return >= 0 ? "+" : ""}${fmtNumber(sp500Return, { decimals: 2 })}%` : "—"}
        />
        <StatTile
          label="Alpha vs S&P"
          value={alpha !== null ? `${alpha >= 0 ? "+" : ""}${fmtNumber(alpha, { decimals: 2 })}%` : "—"}
          delta={alpha !== null ? {
            value: alpha >= 0 ? "beating" : "lagging",
            tone:  alpha >= 0 ? "green" : "red",
          } : undefined}
        />
      </div>

      {/* Chart */}
      {isCold || !data ? (
        <Skeleton className="h-[260px]" />
      ) : !hasRealData && chartData.every((r) => r.account === null) ? (
        <div className="flex h-[260px] flex-col items-center justify-center gap-3 rounded-none border border-dashed border-[var(--color-border2)] bg-[var(--color-bg3)]">
          <Badge tone="amber">En espera</Badge>
          <p className="max-w-xs text-center text-[12px] text-[var(--color-text2)]">
            La gráfica se poblará en cuanto los bots empiecen a ejecutar órdenes. El S&P 500 ya está cargando.
          </p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" strokeOpacity={0.5} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: "var(--color-text3)" }}
              tickFormatter={(d) => {
                const dt = new Date(d + "T00:00:00Z");
                return dt.toLocaleDateString("es-MX", { month: "short", day: "numeric" });
              }}
              tickLine={false}
              axisLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              tick={{ fontSize: 10, fill: "var(--color-text3)" }}
              tickFormatter={(v) => `${v >= 100 ? "+" : ""}${fmtNumber(v - 100, { decimals: 1 })}%`}
              tickLine={false}
              axisLine={false}
              width={52}
              domain={["auto", "auto"]}
            />
            <Tooltip content={<CustomTooltip />} />
            <ReferenceLine y={100} stroke="var(--color-border)" strokeDasharray="4 4" />
            <Line
              type="monotone"
              dataKey="account"
              name="Bots (combinado)"
              stroke={BOT_COLORS.combined}
              strokeWidth={2.5}
              dot={false}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="sp500"
              name="S&P 500"
              stroke={BOT_COLORS.sp500}
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
              connectNulls
            />
            <Legend
              wrapperStyle={{ fontSize: 11, paddingTop: 12 }}
              iconType="plainline"
            />
          </LineChart>
        </ResponsiveContainer>
      )}

      {/* Bot strategy breakdown */}
      <div className="mt-4 grid grid-cols-3 gap-2">
        {BOTS.map((bot) => (
          <div
            key={bot.key}
            className="rounded-none bg-[var(--color-bg3)] p-3"
          >
            <div className="mb-1 flex items-center gap-1.5">
              <span
                className="inline-block size-1.5 rounded-full"
                style={{ background: bot.color }}
              />
              <span className="text-[10px] font-semibold text-[var(--color-text)]">
                {bot.label}
              </span>
            </div>
            <p className="text-[10px] text-[var(--color-text3)]">{bot.desc}</p>
            <div className="mt-2">
              <Badge tone="neutral">Cuenta compartida</Badge>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
