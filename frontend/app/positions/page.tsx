"use client";

import { useEffect, useState } from "react";
import {
  Badge,
  Card,
  fmtNumber,
  fmtPct,
  SectionHeader,
  Skeleton,
  StatTile,
} from "@/components/primitives";

// ─── Types ────────────────────────────────────────────────────────────────────

type Signal = {
  ticker: string;
  currentPrice: number;
  predictedPrice: number;
  deltaPct: number;
  action: "BUY" | "SELL" | "HOLD";
};

type AlpacaPosition = {
  symbol: string;
  qty: string;
  avg_entry_price: string;
  current_price: string;
  market_value: string;
  unrealized_pl: string;
  unrealized_plpc: string;
  side: string;
};

type AlpacaOrder = {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: string;
  filled_qty: string;
  filled_avg_price: string | null;
  status: string;
  created_at: string;
  type: string;
};

type AlpacaAccount = {
  equity: string;
  cash: string;
  buying_power: string;
  portfolio_value: string;
  daytrade_count: number;
  pattern_day_trader: boolean;
};

type BotData = {
  account: AlpacaAccount;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
  signals: Signal[];
  fetchedAt: string;
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function actionTone(a: string): "green" | "red" | "neutral" {
  if (a === "BUY")  return "green";
  if (a === "SELL") return "red";
  return "neutral";
}

function pnlTone(n: number): "green" | "red" | "neutral" {
  if (n > 0) return "green";
  if (n < 0) return "red";
  return "neutral";
}

function orderStatusTone(s: string): "green" | "amber" | "neutral" | "red" {
  if (s === "filled")            return "green";
  if (s === "partially_filled")  return "amber";
  if (s === "canceled" || s === "expired") return "red";
  return "neutral";
}

function fmt$(n: number | string) {
  return fmtNumber(typeof n === "string" ? parseFloat(n) : n, { decimals: 2 });
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function PositionsPage() {
  const [data, setData]       = useState<BotData | null>(null);
  const [error, setError]     = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const res = await fetch("/api/bot", { cache: "no-store" });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setData(json as BotData);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  if (loading) return <PageSkeleton />;

  if (error) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-16">
        <Card>
          <div className="flex flex-col items-center text-center gap-3">
            <Badge tone="red">Error</Badge>
            <p className="text-[13px] text-[var(--color-text2)]">{error}</p>
            <button
              onClick={load}
              className="mt-2 inline-flex h-8 items-center rounded-[8px] bg-[var(--color-bg3)] px-4 text-[12.5px] font-medium hover:bg-[var(--color-bg4)]"
            >
              Reintentar
            </button>
          </div>
        </Card>
      </div>
    );
  }

  if (!data) return null;

  const { account, positions, orders, signals, fetchedAt } = data;
  const equity   = parseFloat(account.equity);
  const cash     = parseFloat(account.cash);
  const pv       = parseFloat(account.portfolio_value);
  const dayPnl   = equity - 25000;

  const buySignals  = signals.filter((s) => s.action === "BUY");
  const sellSignals = signals.filter((s) => s.action === "SELL");

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            Alpaca Paper Trading
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Bot Dashboard</h1>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block size-1.5 rounded-full bg-[var(--color-green)] animate-pulse" />
          <span className="text-[11px] text-[var(--color-text3)]">
            Live · actualizado {new Date(fetchedAt).toLocaleTimeString("es-MX")}
          </span>
          <button
            onClick={load}
            className="ml-2 inline-flex h-7 items-center rounded-[6px] bg-[var(--color-bg3)] px-3 text-[11.5px] font-medium hover:bg-[var(--color-bg4)]"
          >
            ↻ Refresh
          </button>
        </div>
      </div>

      {/* Account Stats */}
      <Card>
        <SectionHeader eyebrow="Cuenta" title="Resumen del portafolio" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Equity total"
            value={`$${fmt$(equity)}`}
            delta={{ value: `${dayPnl >= 0 ? "+" : ""}$${fmt$(dayPnl)}`, tone: pnlTone(dayPnl) }}
            hint="vs capital inicial $25,000"
          />
          <StatTile label="Valor portafolio" value={`$${fmt$(pv)}`} />
          <StatTile label="Efectivo disponible" value={`$${fmt$(cash)}`} />
          <StatTile
            label="Poder de compra"
            value={`$${fmt$(parseFloat(account.buying_power))}`}
            hint={account.pattern_day_trader ? "PDT activo" : undefined}
          />
        </div>
      </Card>

      {/* Today's signals */}
      <Card>
        <SectionHeader
          eyebrow="Modelo OLS · señales de hoy"
          title="Predicciones del día siguiente"
          description="BUY = delta > 0.30% · SELL = delta negativo · HOLD = entre 0% y 0.30%"
        />
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                <th className="py-2 text-left">Ticker</th>
                <th className="py-2 text-right">Actual</th>
                <th className="py-2 text-right">Predicción</th>
                <th className="py-2 text-right">Delta</th>
                <th className="py-2 text-right">Señal</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-border)]">
              {signals.map((s) => (
                <tr key={s.ticker} className="hover:bg-[var(--color-bg3)] transition-colors">
                  <td className="py-2.5 font-mono font-semibold">{s.ticker}</td>
                  <td className="py-2.5 text-right font-mono">${fmt$(s.currentPrice)}</td>
                  <td className="py-2.5 text-right font-mono">${fmt$(s.predictedPrice)}</td>
                  <td className={`py-2.5 text-right font-mono font-semibold ${
                    s.deltaPct > 0 ? "text-[var(--color-green)]" : s.deltaPct < 0 ? "text-[var(--color-red)]" : "text-[var(--color-text2)]"
                  }`}>
                    {s.deltaPct >= 0 ? "+" : ""}{fmtPct(s.deltaPct / 100, { decimals: 2 })}
                  </td>
                  <td className="py-2.5 text-right">
                    <Badge tone={actionTone(s.action)}>{s.action}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-3 flex gap-4 text-[11px] text-[var(--color-text3)]">
          <span><span className="font-semibold text-[var(--color-green)]">{buySignals.length}</span> BUY</span>
          <span><span className="font-semibold text-[var(--color-red)]">{sellSignals.length}</span> SELL</span>
          <span><span className="font-semibold text-[var(--color-text2)]">{signals.length - buySignals.length - sellSignals.length}</span> HOLD</span>
        </div>
      </Card>

      {/* Open Positions */}
      <Card>
        <SectionHeader eyebrow="Alpaca · posiciones abiertas" title="Cartera activa del bot" />
        {positions.length === 0 ? (
          <div className="rounded-[10px] border border-dashed border-[var(--color-border2)] p-8 text-center text-[13px] text-[var(--color-text3)]">
            Sin posiciones abiertas — el bot no ha ejecutado órdenes aún.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  <th className="py-2 text-left">Ticker</th>
                  <th className="py-2 text-right">Qty</th>
                  <th className="py-2 text-right">Precio entrada</th>
                  <th className="py-2 text-right">Precio actual</th>
                  <th className="py-2 text-right">Valor</th>
                  <th className="py-2 text-right">P&amp;L</th>
                  <th className="py-2 text-right">%</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {positions.map((p) => {
                  const pl    = parseFloat(p.unrealized_pl);
                  const plPct = parseFloat(p.unrealized_plpc);
                  return (
                    <tr key={p.symbol} className="hover:bg-[var(--color-bg3)] transition-colors">
                      <td className="py-2.5 font-mono font-semibold">{p.symbol}</td>
                      <td className="py-2.5 text-right font-mono">{p.qty}</td>
                      <td className="py-2.5 text-right font-mono">${fmt$(p.avg_entry_price)}</td>
                      <td className="py-2.5 text-right font-mono">${fmt$(p.current_price)}</td>
                      <td className="py-2.5 text-right font-mono">${fmt$(p.market_value)}</td>
                      <td className={`py-2.5 text-right font-mono font-semibold ${
                        pl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"
                      }`}>
                        {pl >= 0 ? "+" : ""}${fmt$(pl)}
                      </td>
                      <td className="py-2.5 text-right">
                        <Badge tone={pnlTone(pl)}>
                          {plPct >= 0 ? "+" : ""}{fmtPct(plPct, { decimals: 2 })}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Recent Orders */}
      <Card>
        <SectionHeader eyebrow="Alpaca · historial" title="Órdenes recientes del bot" />
        {orders.length === 0 ? (
          <div className="rounded-[10px] border border-dashed border-[var(--color-border2)] p-8 text-center text-[13px] text-[var(--color-text3)]">
            Sin órdenes registradas aún.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  <th className="py-2 text-left">Fecha</th>
                  <th className="py-2 text-left">Ticker</th>
                  <th className="py-2 text-left">Tipo</th>
                  <th className="py-2 text-right">Qty</th>
                  <th className="py-2 text-right">Precio ejecutado</th>
                  <th className="py-2 text-right">Estado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {orders.map((o) => (
                  <tr key={o.id} className="hover:bg-[var(--color-bg3)] transition-colors">
                    <td className="py-2.5 text-[var(--color-text2)]">
                      {new Date(o.created_at).toLocaleDateString("es-MX", {
                        month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
                      })}
                    </td>
                    <td className="py-2.5 font-mono font-semibold">{o.symbol}</td>
                    <td className="py-2.5">
                      <Badge tone={o.side === "buy" ? "green" : "red"}>
                        {o.side === "buy" ? "COMPRA" : "VENTA"}
                      </Badge>
                    </td>
                    <td className="py-2.5 text-right font-mono">{o.filled_qty || o.qty}</td>
                    <td className="py-2.5 text-right font-mono">
                      {o.filled_avg_price ? `$${fmt$(o.filled_avg_price)}` : "—"}
                    </td>
                    <td className="py-2.5 text-right">
                      <Badge tone={orderStatusTone(o.status)}>{o.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

    </div>
  );
}

// ─── Skeleton ─────────────────────────────────────────────────────────────────

function PageSkeleton() {
  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <Skeleton className="h-8 w-48" />
      <Card>
        <Skeleton className="mb-4 h-4 w-32" />
        <div className="grid grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-20" />)}
        </div>
      </Card>
      <Card>
        <Skeleton className="mb-4 h-4 w-40" />
        <div className="space-y-2">
          {[...Array(6)].map((_, i) => <Skeleton key={i} className="h-10" />)}
        </div>
      </Card>
    </div>
  );
}
