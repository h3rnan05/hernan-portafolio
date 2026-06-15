"use client";

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

// ─── Config ───────────────────────────────────────────────────────────────────

const BOT_CONFIGS = [
  {
    key:        "ols",
    label:      "OLS Model Bot",
    desc:       "Perfil P4 · Moderado Agresivo · 9 acciones",
    accentVar:  "--color-green",
    href:       "/positions?bot=ols",
    schedule:   "lunes–viernes 9:35 AM ET",
    hasSignals: true,
  },
  {
    key:        "capitol",
    label:      "P1 Conservative Bot",
    desc:       "Predicciones OLS · Perfil P1 conservador",
    accentVar:  "--color-cyan",
    href:       "/positions?bot=capitol",
    schedule:   "Actualización semanal",
    hasSignals: false,
  },
  {
    key:        "trailing",
    label:      "P0 Ultra Conservador",
    desc:       "Consumo básico · baja volatilidad · 6 acciones",
    accentVar:  "--color-violet",
    href:       "/positions?bot=trailing",
    schedule:   "Rebalanceo diario 9:40 AM ET",
    hasSignals: false,
  },
] as const;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)    return `hace ${diff}s`;
  if (diff < 3600)  return `hace ${Math.floor(diff / 60)}min`;
  if (diff < 86400) return `hace ${Math.floor(diff / 3600)}h`;
  return `hace ${Math.floor(diff / 86400)}d`;
}

function useBotData(botKey: string) {
  const [data, setData]       = useState<BotData | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    fetch(`/api/bot?bot=${botKey}`)
      .then((r) => r.json())
      .then((d) => setData(d as BotData))
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [botKey]);
  return { data, loading };
}

// ─── Top stats bar ────────────────────────────────────────────────────────────

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

  const olsData  = bots["ols"];
  const topBuy   = olsData?.signals?.filter((s) => s.action === "BUY").sort((a, b) => b.deltaPct - a.deltaPct)[0] ?? null;
  const buyCount  = olsData?.signals?.filter((s) => s.action === "BUY").length ?? 0;
  const sellCount = olsData?.signals?.filter((s) => s.action === "SELL").length ?? 0;

  const stats = [
    {
      label: "Total en Alpaca",
      value: loading ? "…" : `$${fmtNumber(totalEquity, { decimals: 2 })}`,
      hint:  "3 bots combinados · Paper Trading",
      color: "text-[var(--color-text)]",
    },
    {
      label: "P&L no realizado",
      value: loading ? "…" : `${totalPnl >= 0 ? "+" : ""}$${fmtNumber(Math.abs(totalPnl), { decimals: 2 })}`,
      hint:  "Ganancia/pérdida de posiciones abiertas",
      color: totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]",
    },
    {
      label: "Señales OLS hoy",
      value: loading ? "…" : `${buyCount} compra · ${sellCount} venta`,
      hint:  "Basado en predicciones del modelo",
      color: "text-[var(--color-text)]",
    },
    {
      label: "Mejor señal OLS",
      value: loading ? "…" : topBuy ? `${topBuy.ticker} +${topBuy.deltaPct.toFixed(2)}%` : "Sin señales BUY",
      hint:  topBuy
        ? `$${fmtNumber(topBuy.currentPrice, { decimals: 2 })} → predicho $${fmtNumber(topBuy.predictedPrice, { decimals: 2 })}`
        : "El modelo no ve oportunidades hoy",
      color: topBuy ? "text-[var(--color-green)]" : "text-[var(--color-text3)]",
    },
  ];

  return (
    <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
      {stats.map((s) => (
        <div key={s.label} className="rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {s.label}
          </div>
          <div className={`font-mono text-[17px] font-semibold ${s.color}`}>{s.value}</div>
          {s.hint && <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">{s.hint}</div>}
        </div>
      ))}
    </div>
  );
}

// ─── Positions chip row ───────────────────────────────────────────────────────

function PositionChips({ positions }: { positions: Position[] }) {
  if (!positions.length) return (
    <div className="rounded-none bg-[var(--color-bg3)] px-3 py-2 text-[12px] text-[var(--color-text3)]">
      Sin posiciones abiertas.
    </div>
  );
  return (
    <div className="flex flex-wrap gap-2">
      {positions.map((p) => {
        const pl = parseFloat(p.unrealized_plpc);
        return (
          <div key={p.symbol} className="flex items-center gap-1.5 rounded-none bg-[var(--color-bg)] px-2.5 py-1 border border-[var(--color-border)]">
            <span className="font-mono text-[12px] font-medium">{p.symbol}</span>
            <span className={`text-[11px] font-mono ${pl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
              {pl >= 0 ? "+" : ""}{fmtPct(pl, { decimals: 2 })}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// ─── OLS Bot card ─────────────────────────────────────────────────────────────

export function OlsBotStatus() {
  const { data, loading } = useBotData("ols");
  const signals = data?.signals ?? [];
  const buys    = signals.filter((s) => s.action === "BUY");
  const sells   = signals.filter((s) => s.action === "SELL");
  const holds   = signals.filter((s) => s.action === "HOLD");

  return (
    <div className="flex flex-col rounded-none border border-[var(--color-green)]/30 bg-[var(--color-green)]/5 p-5">
      <BotHeader label="OLS Model Bot" desc="Perfil P4 · Moderado Agresivo · 9 acciones"
        accentVar="--color-green" fetchedAt={data?.fetchedAt} />

      {loading ? <LoadingMsg /> : (
        <>
          <div className="mb-4 grid grid-cols-3 gap-2">
            {[
              { label: "Comprar", count: buys.length,  color: "text-[var(--color-green)]",  bg: "bg-[var(--color-green)]/10" },
              { label: "Vender",  count: sells.length, color: "text-[var(--color-red)]",    bg: "bg-[var(--color-red)]/10"   },
              { label: "Esperar", count: holds.length, color: "text-[var(--color-text2)]",  bg: "bg-[var(--color-bg3)]"      },
            ].map((item) => (
              <div key={item.label} className={`rounded-none ${item.bg} px-3 py-2 text-center`}>
                <div className={`font-mono text-[20px] font-bold ${item.color}`}>{item.count}</div>
                <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">{item.label}</div>
              </div>
            ))}
          </div>

          {signals.filter((s) => s.action !== "HOLD").length > 0 ? (
            <div className="mb-4 space-y-1.5">
              {signals
                .filter((s) => s.action !== "HOLD")
                .sort((a, b) => Math.abs(b.deltaPct) - Math.abs(a.deltaPct))
                .slice(0, 5)
                .map((s) => (
                  <div key={s.ticker} className="flex items-center justify-between rounded-none bg-[var(--color-bg2)] px-3 py-1.5">
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
          ) : (
            <div className="mb-4 rounded-none bg-[var(--color-bg3)] px-3 py-2 text-[12px] text-[var(--color-text3)]">
              Sin señales activas — el modelo no ve movimientos significativos hoy.
            </div>
          )}

          {data?.positions && data.positions.length > 0 && (
            <div className="border-t border-[var(--color-border)] pt-3">
              <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                Posiciones abiertas
              </div>
              <PositionChips positions={data.positions} />
            </div>
          )}
        </>
      )}

      <BotFooter schedule="lunes–viernes 9:35 AM ET" href="/positions?bot=ols"
        accentVar="--color-green" equity={data?.account?.equity} />
    </div>
  );
}

// ─── P1 Conservative Bot card ──────────────────────────────────────────────────

export function CapitolBotStatus() {
  const { data, loading } = useBotData("capitol");
  const positions = data?.positions ?? [];
  const orders    = (data?.orders ?? []).filter((o) => o.status === "filled").slice(0, 4);
  const totalPnl  = positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0);

  return (
    <div className="flex flex-col rounded-none border border-[var(--color-cyan)]/30 bg-[var(--color-cyan)]/5 p-5">
      <BotHeader label="P1 Conservative Bot" desc="Predicciones OLS · Perfil P1 conservador"
        accentVar="--color-cyan" fetchedAt={data?.fetchedAt} />

      {loading ? <LoadingMsg /> : (
        <>
          {/* Mini account stats */}
          <div className="mb-4 grid grid-cols-2 gap-2">
            <div className="rounded-none bg-[var(--color-bg3)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">Posiciones</div>
              <div className="font-mono text-[18px] font-bold text-[var(--color-cyan)]">{positions.length}</div>
            </div>
            <div className="rounded-none bg-[var(--color-bg3)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">P&L abierto</div>
              <div className={`font-mono text-[18px] font-bold ${totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                {totalPnl >= 0 ? "+" : ""}${fmtNumber(Math.abs(totalPnl), { decimals: 0 })}
              </div>
            </div>
          </div>

          {/* Positions */}
          <div className="mb-4">
            <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
              Cartera actual
            </div>
            <PositionChips positions={positions} />
          </div>

          {/* Recent fills */}
          {orders.length > 0 && (
            <div className="border-t border-[var(--color-border)] pt-3">
              <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                Últimos trades
              </div>
              <div className="space-y-1.5">
                {orders.map((o) => {
                  const isBuy = o.side === "buy";
                  const price = o.filled_avg_price ? parseFloat(o.filled_avg_price) : null;
                  const qty   = parseFloat(o.filled_qty || o.qty);
                  return (
                    <div key={o.id} className="flex items-center justify-between rounded-none bg-[var(--color-bg2)] px-3 py-1.5">
                      <div className="flex items-center gap-2">
                        <span className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-bold ${isBuy ? "bg-[var(--color-green)]/15 text-[var(--color-green)]" : "bg-[var(--color-red)]/15 text-[var(--color-red)]"}`}>
                          {isBuy ? "C" : "V"}
                        </span>
                        <span className="font-mono text-[12px] font-medium">{o.symbol}</span>
                        <span className="text-[11px] text-[var(--color-text3)]">×{qty}</span>
                      </div>
                      {price && <span className="font-mono text-[11px] text-[var(--color-text2)]">${fmtNumber(price, { decimals: 2 })}</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}

      <BotFooter schedule="Actualización semanal" href="/positions?bot=capitol"
        accentVar="--color-cyan" equity={data?.account?.equity} />
    </div>
  );
}

// ─── P0 Ultra Conservador Bot card ────────────────────────────────────────────

const P0_WEIGHTS: Record<string, number> = {
  AAL: 0.0435, CL: 0.1865, HSY: 0.1437, KHC: 0.1773, KMB: 0.1674, MDLZ: 0.2816,
};

export function P0BotStatus() {
  const { data, loading } = useBotData("trailing");
  const positions  = data?.positions ?? [];
  const totalPnl   = positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0);
  const equity     = data?.account ? parseFloat(data.account.equity) : 0;

  return (
    <div className="flex flex-col rounded-none border border-[var(--color-violet)]/30 bg-[var(--color-violet)]/5 p-5">
      <BotHeader label="P0 Ultra Conservador" desc="Consumo básico · baja volatilidad · 6 acciones"
        accentVar="--color-violet" fetchedAt={data?.fetchedAt} />

      {loading ? <LoadingMsg /> : (
        <>
          {/* Mini account stats */}
          <div className="mb-4 grid grid-cols-2 gap-2">
            <div className="rounded-none bg-[var(--color-bg3)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">Posiciones</div>
              <div className="font-mono text-[18px] font-bold text-[var(--color-violet)]">{positions.length}</div>
            </div>
            <div className="rounded-none bg-[var(--color-bg3)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">P&L abierto</div>
              <div className={`font-mono text-[18px] font-bold ${totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                {totalPnl >= 0 ? "+" : ""}${fmtNumber(Math.abs(totalPnl), { decimals: 0 })}
              </div>
            </div>
          </div>

          {/* Portfolio allocation bars */}
          <div className="mb-4">
            <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
              Asignación objetivo
            </div>
            <div className="space-y-1.5">
              {Object.entries(P0_WEIGHTS).sort((a, b) => b[1] - a[1]).map(([sym, w]) => {
                const pos       = positions.find((p) => p.symbol === sym);
                const actualVal = pos ? parseFloat(pos.market_value) : 0;
                const targetVal = equity * w;
                const diff      = actualVal - targetVal;
                return (
                  <div key={sym}>
                    <div className="mb-0.5 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[12px] font-semibold">{sym}</span>
                        <span className="text-[10px] text-[var(--color-text3)]">{(w * 100).toFixed(1)}%</span>
                      </div>
                      <span className={`text-[10px] font-mono ${Math.abs(diff) < 50 ? "text-[var(--color-text3)]" : diff > 0 ? "text-[var(--color-green)]" : "text-[var(--color-amber)]"}`}>
                        {Math.abs(diff) < 50 ? "✓" : diff > 0 ? `+$${fmtNumber(diff, { decimals: 0 })}` : `-$${fmtNumber(Math.abs(diff), { decimals: 0 })}`}
                      </span>
                    </div>
                    <div className="h-1 w-full overflow-hidden rounded-full bg-[var(--color-bg3)]">
                      <div
                        className="h-full rounded-full bg-[var(--color-violet)]"
                        style={{ width: `${w * 100 / 0.2816 * 100}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}

      <BotFooter schedule="Rebalanceo diario 9:40 AM ET" href="/positions?bot=trailing"
        accentVar="--color-violet" equity={data?.account?.equity} />
    </div>
  );
}

// ─── Shared sub-components ────────────────────────────────────────────────────

function BotHeader({ label, desc, accentVar, fetchedAt }: {
  label: string; desc: string; accentVar: string; fetchedAt?: string;
}) {
  return (
    <div className="mb-4 flex items-center justify-between">
      <div className="flex items-center gap-2.5">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-50"
            style={{ background: `var(${accentVar})` }} />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full"
            style={{ background: `var(${accentVar})` }} />
        </span>
        <div>
          <div className="text-[13px] font-semibold">{label} · Activo</div>
          <div className="text-[11px] text-[var(--color-text3)]">{desc}</div>
        </div>
      </div>
      {fetchedAt && (
        <span className="text-[10px] text-[var(--color-text3)]">{timeAgo(fetchedAt)}</span>
      )}
    </div>
  );
}

function BotFooter({ schedule, href, accentVar, equity }: {
  schedule: string; href: string; accentVar: string; equity?: string;
}) {
  return (
    <div className="mt-auto flex items-center justify-between border-t border-[var(--color-border)] pt-3">
      <div className="text-[11px] text-[var(--color-text3)]">
        {equity
          ? <>Equity: <span className="font-mono font-medium text-[var(--color-text2)]">${fmtNumber(parseFloat(equity), { decimals: 2 })}</span></>
          : schedule}
      </div>
      <Link href={href} className="text-[11.5px] font-medium hover:underline"
        style={{ color: `var(${accentVar})` }}>
        Ver detalle →
      </Link>
    </div>
  );
}

function LoadingMsg() {
  return <div className="flex-1 py-2 text-[12px] text-[var(--color-text3)]">Cargando datos…</div>;
}

// ─── Recent activity feed (all bots) ─────────────────────────────────────────

export function RecentActivity() {
  const [activeTab, setActiveTab] = useState<"ols" | "capitol" | "trailing">("ols");
  const { data, loading } = useBotData(activeTab);

  const orders = (data?.orders ?? [])
    .filter((o) => o.status === "filled")
    .slice(0, 6);

  const tabs = [
    { key: "ols",      label: "OLS",     color: "var(--color-green)"  },
    { key: "capitol",  label: "Capitol", color: "var(--color-cyan)"   },
    { key: "trailing", label: "P0",      color: "var(--color-violet)" },
  ] as const;

  return (
    <div className="rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] p-5">
      <div className="mb-4 flex items-start justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            ACTIVIDAD RECIENTE
          </div>
          <h3 className="text-[14px] font-semibold">Últimos trades ejecutados</h3>
        </div>
        {/* Tab switcher */}
        <div className="flex gap-1 rounded-none bg-[var(--color-bg3)] p-1">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`rounded-none px-2.5 py-1 text-[11px] font-medium transition-all ${
                activeTab === t.key
                  ? "bg-[var(--color-bg)] shadow-sm"
                  : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
              }`}
              style={activeTab === t.key ? { color: t.color } : {}}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="text-[12px] text-[var(--color-text3)]">Cargando órdenes…</div>
      ) : orders.length === 0 ? (
        <div className="rounded-none bg-[var(--color-bg3)] px-4 py-3 text-[12.5px] text-[var(--color-text3)]">
          No hay trades ejecutados aún para este bot.
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
              <div key={o.id} className="flex items-center justify-between rounded-none border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-2">
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
        <Link
          href={`/positions?bot=${activeTab}`}
          className="text-[11.5px] font-medium text-[var(--color-text3)] hover:text-[var(--color-text)]"
        >
          Ver todas las órdenes →
        </Link>
      </div>
    </div>
  );
}
