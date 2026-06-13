"use client";

/**
 * Client-side widgets for the Overview page that pull live Alpaca data.
 * Separated from the server component so Alpaca fetches don't block SSR.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { Badge, fmtNumber, fmtPct } from "@/components/primitives";

// ─── Types ────────────────────────────────────────────────────────────────────

type Position = {
  symbol: string;
  qty: string;
  market_value: string;
  current_price: string;
  avg_entry_price: string;
  unrealized_pl: string;
  unrealized_plpc: string;
};

type Order = {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: string;
  filled_qty: string;
  status: string;
  filled_avg_price: string | null;
  submitted_at: string;
  filled_at: string | null;
  type: string;
};

type Signal = {
  ticker: string;
  currentPrice: number;
  predictedPrice: number;
  deltaPct: number;
  action: "BUY" | "SELL" | "HOLD";
};

type BotData = {
  account: { equity: string; cash: string; buying_power: string };
  positions: Position[];
  orders: Order[];
  signals: Signal[];
  fetchedAt: string;
  error?: string;
};

const BOT_CONFIGS = [
  { key: "ols",      label: "OLS Bot",           dot: "bg-[var(--color-green)]"  },
  { key: "capitol",  label: "Capitol Trades Bot", dot: "bg-[var(--color-cyan)]"   },
  { key: "trailing", label: "Trailing Stop Bot",  dot: "bg-[var(--color-violet)]" },
];

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `hace ${diff}s`;
  if (diff < 3600) return `hace ${Math.floor(diff / 60)}min`;
  if (diff < 86400) return `hace ${Math.floor(diff / 3600)}h`;
  return `hace ${Math.floor(diff / 86400)}d`;
}

// ─── Top stats bar (replaces the 4 technical stat tiles) ─────────────────────

export function AlpacaStatsBar() {
  const [bots, setBots] = useState<Record<string, BotData | null>>({});
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all(
      BOT_CONFIGS.map(({ key }) =>
        fetch(`/api/bot?bot=${key}`)
          .then((r) => r.json())
          .then((d) => [key, d as BotData])
          .catch(() => [key, null])
      )
    ).then((results) => {
      setBots(Object.fromEntries(results));
      setLoading(false);
    });
  }, []);

  const totalEquity = BOT_CONFIGS.reduce((sum, { key }) => {
    const d = bots[key];
    return sum + (d?.account ? parseFloat(d.account.equity) : 0);
  }, 0);

  const totalPnl = BOT_CONFIGS.reduce((sum, { key }) => {
    const d = bots[key];
    if (!d?.positions) return sum;
    return sum + d.positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0);
  }, 0);

  const olsData = bots["ols"];
  const topBuy = olsData?.signals
    ?.filter((s) => s.action === "BUY")
    .sort((a, b) => b.deltaPct - a.deltaPct)[0] ?? null;

  const buyCount  = olsData?.signals?.filter((s) => s.action === "BUY").length ?? 0;
  const sellCount = olsData?.signals?.filter((s) => s.action === "SELL").length ?? 0;

  const stats = [
    {
      label: "Total en Alpaca",
      value: loading ? "…" : `$${fmtNumber(totalEquity, { decimals: 2 })}`,
      hint: "3 bots combinados · Paper Trading",
      color: "text-[var(--color-text)]",
    },
    {
      label: "P&L no realizado",
      value: loading ? "…" : `${totalPnl >= 0 ? "+" : ""}$${fmtNumber(totalPnl, { decimals: 2 })}`,
      hint: "Ganancia/pérdida de posiciones abiertas",
      color: totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]",
    },
    {
      label: "Señales OLS hoy",
      value: loading ? "…" : `${buyCount} compra · ${sellCount} venta`,
      hint: "Basado en predicciones del modelo",
      color: "text-[var(--color-text)]",
    },
    {
      label: "Mejor señal OLS",
      value: loading ? "…" : topBuy ? `${topBuy.ticker} +${topBuy.deltaPct.toFixed(2)}%` : "Sin señales BUY",
      hint: topBuy ? `Precio actual $${fmtNumber(topBuy.currentPrice, { decimals: 2 })} → predicho $${fmtNumber(topBuy.predictedPrice, { decimals: 2 })}` : "El modelo no ve oportunidades hoy",
      color: topBuy ? "text-[var(--color-green)]" : "text-[var(--color-text3)]",
    },
  ];

  return (
    <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
      {stats.map((s) => (
        <div key={s.label} className="rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {s.label}
          </div>
          <div className={`font-mono text-[17px] font-semibold ${s.color}`}>
            {s.value}
          </div>
          {s.hint && (
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">{s.hint}</div>
          )}
        </div>
      ))}
    </div>
  );
}

// ─── OLS Bot status card ──────────────────────────────────────────────────────

export function OlsBotStatus() {
  const [data, setData] = useState<BotData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/bot?bot=ols")
      .then((r) => r.json())
      .then((d) => setData(d as BotData))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, []);

  const signals = data?.signals ?? [];
  const buys    = signals.filter((s) => s.action === "BUY");
  const sells   = signals.filter((s) => s.action === "SELL");
  const holds   = signals.filter((s) => s.action === "HOLD");

  return (
    <div className="rounded-[16px] border border-[var(--color-green)]/30 bg-[var(--color-green)]/5 p-5">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--color-green)] opacity-50" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-[var(--color-green)]" />
          </span>
          <div>
            <div className="text-[13px] font-semibold">OLS Model Bot · Activo</div>
            <div className="text-[11px] text-[var(--color-text3)]">
              Perfil P4 · Moderado Agresivo · 9 acciones
            </div>
          </div>
        </div>
        {data?.fetchedAt && (
          <span className="text-[10px] text-[var(--color-text3)]">
            Actualizado {timeAgo(data.fetchedAt)}
          </span>
        )}
      </div>

      {loading ? (
        <div className="text-[12px] text-[var(--color-text3)]">Cargando señales…</div>
      ) : (
        <>
          {/* Signal summary */}
          <div className="mb-4 grid grid-cols-3 gap-2">
            {[
              { label: "Comprar", count: buys.length, color: "text-[var(--color-green)]", bg: "bg-[var(--color-green)]/10" },
              { label: "Vender",  count: sells.length, color: "text-[var(--color-red)]",   bg: "bg-[var(--color-red)]/10"   },
              { label: "Esperar", count: holds.length, color: "text-[var(--color-text2)]", bg: "bg-[var(--color-bg3)]"      },
            ].map((item) => (
              <div key={item.label} className={`rounded-[8px] ${item.bg} px-3 py-2 text-center`}>
                <div className={`font-mono text-[20px] font-bold ${item.color}`}>{item.count}</div>
                <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">{item.label}</div>
              </div>
            ))}
          </div>

          {/* Signal list */}
          {signals.length > 0 && (
            <div className="space-y-1.5">
              {signals
                .filter((s) => s.action !== "HOLD")
                .sort((a, b) => Math.abs(b.deltaPct) - Math.abs(a.deltaPct))
                .slice(0, 5)
                .map((s) => (
                  <div key={s.ticker} className="flex items-center justify-between rounded-[6px] bg-[var(--color-bg2)] px-3 py-1.5">
                    <span className="font-mono text-[12px] font-medium">{s.ticker}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-[var(--color-text3)]">
                        ${fmtNumber(s.currentPrice, { decimals: 2 })} → ${fmtNumber(s.predictedPrice, { decimals: 2 })}
                      </span>
                      <Badge tone={s.action === "BUY" ? "green" : "red"}>
                        {s.action === "BUY" ? "Comprar" : "Vender"} {s.deltaPct > 0 ? "+" : ""}{s.deltaPct.toFixed(2)}%
                      </Badge>
                    </div>
                  </div>
                ))}
            </div>
          )}

          {signals.length === 0 && (
            <div className="rounded-[8px] bg-[var(--color-bg3)] px-3 py-2 text-[12px] text-[var(--color-text3)]">
              Sin señales disponibles — el modelo necesita datos actualizados.
            </div>
          )}

          {/* Positions summary */}
          {data?.positions && data.positions.length > 0 && (
            <div className="mt-4 border-t border-[var(--color-border)] pt-3">
              <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                Posiciones abiertas
              </div>
              <div className="flex flex-wrap gap-2">
                {data.positions.map((p) => {
                  const pl = parseFloat(p.unrealized_plpc);
                  return (
                    <div key={p.symbol} className="flex items-center gap-1.5 rounded-[6px] bg-[var(--color-bg2)] px-2.5 py-1">
                      <span className="font-mono text-[12px] font-medium">{p.symbol}</span>
                      <span className={`text-[11px] font-mono ${pl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                        {pl >= 0 ? "+" : ""}{fmtPct(pl, { decimals: 2 })}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-[var(--color-border)] pt-3">
        <div className="text-[11px] text-[var(--color-text3)]">
          Próxima ejecución: <span className="text-[var(--color-text2)]">lunes–viernes 9:35 AM ET</span>
        </div>
        <Link
          href="/positions?bot=ols"
          className="text-[11.5px] font-medium text-[var(--color-green)] hover:underline"
        >
          Ver detalle →
        </Link>
      </div>
    </div>
  );
}

// ─── Recent activity feed ─────────────────────────────────────────────────────

export function RecentActivity() {
  const [data, setData] = useState<BotData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/bot?bot=ols")
      .then((r) => r.json())
      .then((d) => setData(d as BotData))
      .catch(() => null)
      .finally(() => setLoading(false));
  }, []);

  const orders = (data?.orders ?? [])
    .filter((o) => o.status === "filled")
    .slice(0, 6);

  return (
    <div className="rounded-[16px] border border-[var(--color-border)] bg-[var(--color-bg2)] p-5">
      <div className="mb-4">
        <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          ACTIVIDAD RECIENTE
        </div>
        <h3 className="text-[14px] font-semibold">Últimos trades ejecutados</h3>
        <p className="mt-0.5 text-[11.5px] text-[var(--color-text2)]">
          Órdenes completadas del bot OLS en Alpaca Paper Trading.
        </p>
      </div>

      {loading ? (
        <div className="text-[12px] text-[var(--color-text3)]">Cargando órdenes…</div>
      ) : orders.length === 0 ? (
        <div className="rounded-[8px] bg-[var(--color-bg3)] px-4 py-3 text-[12.5px] text-[var(--color-text3)]">
          No hay trades ejecutados aún. El bot opera a las 9:35 AM ET en días hábiles.
        </div>
      ) : (
        <div className="space-y-2">
          {orders.map((o) => {
            const isBuy  = o.side === "buy";
            const price  = o.filled_avg_price ? parseFloat(o.filled_avg_price) : null;
            const qty    = parseFloat(o.filled_qty || o.qty);
            const total  = price ? price * qty : null;
            const when   = o.filled_at ?? o.submitted_at;
            return (
              <div
                key={o.id}
                className="flex items-center justify-between rounded-[8px] border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2"
              >
                <div className="flex items-center gap-3">
                  <div className={`flex h-7 w-7 items-center justify-center rounded-full text-[11px] font-bold ${isBuy ? "bg-[var(--color-green)]/15 text-[var(--color-green)]" : "bg-[var(--color-red)]/15 text-[var(--color-red)]"}`}>
                    {isBuy ? "C" : "V"}
                  </div>
                  <div>
                    <div className="text-[12.5px] font-semibold font-mono">{o.symbol}</div>
                    <div className="text-[10.5px] text-[var(--color-text3)]">
                      {qty} acciones · {isBuy ? "Compra" : "Venta"}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  {price && (
                    <div className="font-mono text-[12.5px] font-medium">
                      ${fmtNumber(price, { decimals: 2 })}
                      {total && <span className="ml-1 text-[10.5px] text-[var(--color-text3)]">(${fmtNumber(total, { decimals: 0 })})</span>}
                    </div>
                  )}
                  <div className="text-[10.5px] text-[var(--color-text3)]">{timeAgo(when)}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-3 text-right">
        <Link href="/positions?bot=ols" className="text-[11.5px] font-medium text-[var(--color-text3)] hover:text-[var(--color-text)]">
          Ver todas las órdenes →
        </Link>
      </div>
    </div>
  );
}
