"use client";

import { useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import {
  Badge,
  Card,
  fmtNumber,
  fmtPct,
  SectionHeader,
  Skeleton,
} from "@/components/primitives";

// ─── Bot definitions ──────────────────────────────────────────────────────────

const BOTS = [
  {
    key:      "ols",
    label:    "OLS Model Bot",
    desc:     "Predicciones de regresión OLS · Perfil P4 moderado",
    color:    "var(--color-green)",
    hasSignals: true,
  },
  {
    key:      "capitol",
    label:    "P1 Conservative Bot",
    desc:     "Predicciones de regresión OLS · Perfil P1 conservador",
    color:    "var(--color-cyan)",
    hasSignals: true,
  },
  {
    key:      "trailing",
    label:    "P0 Ultra Conservador Bot",
    desc:     "Portafolio conservador · consumo básico baja volatilidad",
    color:    "var(--color-violet)",
    hasSignals: false,
  },
] as const;

type BotKey = (typeof BOTS)[number]["key"];

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
};

type AlpacaOrder = {
  id: string;
  symbol: string;
  side: "buy" | "sell";
  qty: string;
  filled_qty: string;
  filled_avg_price: string | null;
  status: string;
  submitted_at: string;
  filled_at: string | null;
  created_at: string;
};

type AlpacaAccount = {
  equity: string;
  cash: string;
  buying_power: string;
};

type BotData = {
  bot: BotKey;
  account: AlpacaAccount;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
  signals: Signal[];
  fetchedAt: string;
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmt$(n: number | string) {
  return fmtNumber(typeof n === "string" ? parseFloat(n) : n, { decimals: 2 });
}

function pnlTone(n: number): "green" | "red" | "neutral" {
  return n > 0 ? "green" : n < 0 ? "red" : "neutral";
}

function actionTone(a: string): "green" | "red" | "neutral" {
  return a === "BUY" ? "green" : a === "SELL" ? "red" : "neutral";
}

const STATUS_LABEL: Record<string, string> = {
  filled:            "Ejecutada",
  partially_filled:  "Parcial",
  canceled:          "Cancelada",
  expired:           "Expirada",
  pending_new:       "Pendiente",
  new:               "Nueva",
  accepted:          "Aceptada",
  held:              "En espera",
};

function statusTone(s: string): "green" | "amber" | "neutral" | "red" {
  if (s === "filled")           return "green";
  if (s === "partially_filled") return "amber";
  if (s === "canceled" || s === "expired") return "red";
  return "neutral";
}

function fmtOrderDate(iso: string) {
  return new Date(iso).toLocaleDateString("es-MX", {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function PositionsPage() {
  const params   = useSearchParams();
  const botParam = params.get("bot") as BotKey | null;
  const validBot = BOTS.find((b) => b.key === botParam)?.key ?? "ols";

  const [activeBot, setActiveBot] = useState<BotKey>(validBot);
  const [data,      setData]      = useState<BotData | null>(null);
  const [error,     setError]     = useState<string | null>(null);
  const [loading,   setLoading]   = useState(true);

  const load = useCallback(async (bot: BotKey) => {
    setLoading(true);
    setError(null);
    try {
      const res  = await fetch(`/api/bot?bot=${bot}`, { cache: "no-store" });
      const json = await res.json();
      if (json.error) throw new Error(json.error);
      setData(json as BotData);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(activeBot); }, [activeBot, load]);

  useEffect(() => {
    const id = setInterval(() => load(activeBot), 60_000);
    return () => clearInterval(id);
  }, [activeBot, load]);

  const activeBotDef = BOTS.find((b) => b.key === activeBot)!;

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-4 py-6 sm:px-6 sm:py-10">

      {/* Header */}
      <div>
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          Alpaca Paper Trading · en vivo
        </div>
        <h1 className="mb-4 text-xl font-semibold tracking-tight sm:text-2xl">Broker Dashboard</h1>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {BOTS.map((bot) => {
            const active = bot.key === activeBot;
            return (
              <button
                key={bot.key}
                onClick={() => setActiveBot(bot.key)}
                className={`rounded-none p-4 text-left transition-all duration-150 ${
                  active
                    ? "bg-[var(--color-bg2)] shadow-[0_0_0_1px_rgba(255,255,255,0.08),0_1px_3px_rgba(0,0,0,0.4)]"
                    : "bg-[var(--color-bg3)] opacity-60 hover:bg-[var(--color-bg2)] hover:opacity-100"
                }`}
              >
                <div className="mb-1.5 flex items-center gap-2">
                  <span
                    className={`inline-block size-2 rounded-full ${active ? "animate-pulse" : ""}`}
                    style={{ background: bot.color }}
                  />
                  <span className="text-[12px] font-semibold">{bot.label}</span>
                </div>
                <p className="text-[11px] text-[var(--color-text3)]">{bot.desc}</p>
                {active && (
                  <div className="mt-2">
                    <Badge tone="neutral">Viendo ahora</Badge>
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      {loading ? (
        <PageSkeleton />
      ) : error ? (
        <Card>
          <div className="flex flex-col items-center gap-3 text-center">
            <Badge tone="red">Error de conexión</Badge>
            <p className="text-[13px] text-[var(--color-text2)]">{error}</p>
            <button
              onClick={() => load(activeBot)}
              className="inline-flex h-8 items-center rounded-none bg-[var(--color-bg3)] px-4 text-[12.5px] font-medium hover:bg-[var(--color-bg4)]"
            >
              ↻ Reintentar
            </button>
          </div>
        </Card>
      ) : data ? (
        <BotDetail
          data={data}
          botDef={activeBotDef}
          onRefresh={() => load(activeBot)}
        />
      ) : null}

    </div>
  );
}

// ─── Bot detail view ──────────────────────────────────────────────────────────

function BotDetail({
  data,
  botDef,
  onRefresh,
}: {
  data: BotData;
  botDef: typeof BOTS[number];
  onRefresh: () => void;
}) {
  const { account, positions, orders, signals, fetchedAt } = data;

  const equity       = parseFloat(account.equity);
  const cash         = parseFloat(account.cash);
  const invested     = positions.reduce((s, p) => s + parseFloat(p.market_value), 0);
  const totalPnl     = positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0);
  const buySignals   = signals.filter((s) => s.action === "BUY");
  const sellSignals  = signals.filter((s) => s.action === "SELL");

  const signalMap = Object.fromEntries(signals.map((s) => [s.ticker, s]));

  return (
    <div className="space-y-5">

      {/* Timestamp + refresh */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className="size-1.5 rounded-full animate-pulse"
            style={{ background: botDef.color }}
          />
          <span className="text-[11px] text-[var(--color-text3)]">
            Datos al {new Date(fetchedAt).toLocaleTimeString("es-MX")}
          </span>
        </div>
        <button
          onClick={onRefresh}
          className="inline-flex h-7 items-center rounded-none bg-[var(--color-bg3)] px-3 text-[11.5px] font-medium hover:bg-[var(--color-bg4)]"
        >
          ↻ Actualizar
        </button>
      </div>

      {/* Account stats — 4 tiles */}
      <Card>
        <SectionHeader eyebrow="Cuenta Alpaca" title="Balance de la cuenta" />
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-none bg-[var(--color-bg3)] px-3 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">Equity total</div>
            <div className="font-mono text-[16px] font-semibold">${fmt$(equity)}</div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">Capital + posiciones</div>
          </div>
          <div className="rounded-none bg-[var(--color-bg3)] px-3 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">P&amp;L abierto</div>
            <div className={`font-mono text-[16px] font-semibold ${totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
              {totalPnl >= 0 ? "+" : ""}${fmt$(totalPnl)}
            </div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">Ganancia/pérdida no realizada</div>
          </div>
          <div className="rounded-none bg-[var(--color-bg3)] px-3 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">Invertido</div>
            <div className="font-mono text-[16px] font-semibold">${fmt$(invested)}</div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">{positions.length} posiciones abiertas</div>
          </div>
          <div className="rounded-none bg-[var(--color-bg3)] px-3 py-3">
            <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">Efectivo libre</div>
            <div className="font-mono text-[16px] font-semibold">${fmt$(cash)}</div>
            <div className="mt-0.5 text-[11px] text-[var(--color-text3)]">Listo para invertir</div>
          </div>
        </div>
      </Card>

      {/* Signals — only for OLS bot */}
      {botDef.hasSignals && signals.length > 0 && (
        <Card>
          <SectionHeader
            eyebrow="Señales del día · Modelo OLS"
            title="¿Qué dice el modelo hoy?"
            description="BUY = sube más de 0.30% · SELL = baja · HOLD = movimiento mínimo esperado"
          />
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  <th className="py-2 text-left">Acción</th>
                  <th className="py-2 text-right">Precio actual</th>
                  <th className="py-2 text-right">Precio predicho</th>
                  <th className="py-2 text-right">Diferencia</th>
                  <th className="py-2 text-right">Señal</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {signals
                  .sort((a, b) => Math.abs(b.deltaPct) - Math.abs(a.deltaPct))
                  .map((s) => (
                  <tr key={s.ticker} className="transition-colors hover:bg-[var(--color-bg3)]">
                    <td className="py-2.5 font-mono font-semibold">{s.ticker}</td>
                    <td className="py-2.5 text-right font-mono">${fmt$(s.currentPrice)}</td>
                    <td className="py-2.5 text-right font-mono">${fmt$(s.predictedPrice)}</td>
                    <td className={`py-2.5 text-right font-mono font-semibold ${
                      s.deltaPct > 0 ? "text-[var(--color-green)]" : s.deltaPct < 0 ? "text-[var(--color-red)]" : "text-[var(--color-text2)]"
                    }`}>
                      {s.deltaPct >= 0 ? "+" : ""}{fmtPct(s.deltaPct / 100, { decimals: 2 })}
                    </td>
                    <td className="py-2.5 text-right">
                      <Badge tone={actionTone(s.action)}>
                        {s.action === "BUY" ? "Comprar" : s.action === "SELL" ? "Vender" : "Esperar"}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 flex gap-4 text-[11px] text-[var(--color-text3)]">
            <span><span className="font-semibold text-[var(--color-green)]">{buySignals.length}</span> para comprar</span>
            <span><span className="font-semibold text-[var(--color-red)]">{sellSignals.length}</span> para vender</span>
            <span><span className="font-semibold text-[var(--color-text2)]">{signals.length - buySignals.length - sellSignals.length}</span> en espera</span>
          </div>
        </Card>
      )}

      {!botDef.hasSignals && (
        <div className="rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] px-5 py-4 text-[12.5px] text-[var(--color-text3)]">
          Las señales de predicción solo están disponibles para el <strong className="text-[var(--color-text2)]">OLS Model Bot</strong>. Los demás bots operan con su propia lógica integrada.
        </div>
      )}

      {/* Open positions */}
      <Card>
        <SectionHeader
          eyebrow="Posiciones abiertas"
          title="Cartera activa"
          description={positions.length > 0 ? `${positions.length} posiciones · valor total $${fmt$(invested)}` : undefined}
        />
        {positions.length === 0 ? (
          <div className="rounded-none border border-dashed border-[var(--color-border2)] p-8 text-center text-[13px] text-[var(--color-text3)]">
            Sin posiciones abiertas. El bot ejecutará órdenes a las 9:35 AM ET en el próximo día hábil.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  <th className="py-2 text-left">Acción</th>
                  <th className="py-2 text-right">Qty</th>
                  <th className="py-2 text-right">Entrada</th>
                  <th className="py-2 text-right">Actual</th>
                  <th className="py-2 text-right">Valor</th>
                  <th className="py-2 text-right">P&amp;L $</th>
                  <th className="py-2 text-right">P&amp;L %</th>
                  {botDef.hasSignals && <th className="py-2 text-right">Señal hoy</th>}
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {positions
                  .sort((a, b) => Math.abs(parseFloat(b.unrealized_pl)) - Math.abs(parseFloat(a.unrealized_pl)))
                  .map((p) => {
                  const pl     = parseFloat(p.unrealized_pl);
                  const plPct  = parseFloat(p.unrealized_plpc);
                  const signal = signalMap[p.symbol];
                  return (
                    <tr key={p.symbol} className="transition-colors hover:bg-[var(--color-bg3)]">
                      <td className="py-2.5 font-mono font-semibold">{p.symbol}</td>
                      <td className="py-2.5 text-right font-mono text-[var(--color-text2)]">{p.qty}</td>
                      <td className="py-2.5 text-right font-mono">${fmt$(p.avg_entry_price)}</td>
                      <td className="py-2.5 text-right font-mono">${fmt$(p.current_price)}</td>
                      <td className="py-2.5 text-right font-mono">${fmt$(p.market_value)}</td>
                      <td className={`py-2.5 text-right font-mono font-semibold ${pl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                        {pl >= 0 ? "+" : ""}${fmt$(pl)}
                      </td>
                      <td className="py-2.5 text-right">
                        <Badge tone={pnlTone(pl)}>
                          {plPct >= 0 ? "+" : ""}{fmtPct(plPct, { decimals: 2 })}
                        </Badge>
                      </td>
                      {botDef.hasSignals && (
                        <td className="py-2.5 text-right">
                          {signal ? (
                            <Badge tone={actionTone(signal.action)}>
                              {signal.action === "BUY" ? "Comprar" : signal.action === "SELL" ? "Vender" : "Esperar"}
                            </Badge>
                          ) : (
                            <span className="text-[var(--color-text3)]">—</span>
                          )}
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
              {positions.length > 1 && (
                <tfoot>
                  <tr className="border-t-2 border-[var(--color-border)]">
                    <td colSpan={4} className="py-2 text-[11px] text-[var(--color-text3)]">Total</td>
                    <td className="py-2 text-right font-mono font-semibold">${fmt$(invested)}</td>
                    <td className={`py-2 text-right font-mono font-semibold ${totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                      {totalPnl >= 0 ? "+" : ""}${fmt$(totalPnl)}
                    </td>
                    <td colSpan={botDef.hasSignals ? 2 : 1} />
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </Card>

      {/* Order history */}
      <Card>
        <SectionHeader
          eyebrow="Historial de órdenes"
          title="Trades ejecutados"
          description="Órdenes enviadas por el bot a Alpaca, ordenadas por fecha de ejecución."
        />
        {orders.length === 0 ? (
          <div className="rounded-none border border-dashed border-[var(--color-border2)] p-8 text-center text-[13px] text-[var(--color-text3)]">
            Sin órdenes registradas aún.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  <th className="py-2 text-left">Fecha</th>
                  <th className="py-2 text-left">Acción</th>
                  <th className="py-2 text-left">Tipo</th>
                  <th className="py-2 text-right">Qty</th>
                  <th className="py-2 text-right">Precio ejec.</th>
                  <th className="py-2 text-right">Total</th>
                  <th className="py-2 text-right">Estado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {orders.map((o) => {
                  const qty   = parseFloat(o.filled_qty || o.qty);
                  const price = o.filled_avg_price ? parseFloat(o.filled_avg_price) : null;
                  const total = price ? qty * price : null;
                  const date  = o.filled_at ?? o.submitted_at ?? o.created_at;
                  return (
                    <tr key={o.id} className="transition-colors hover:bg-[var(--color-bg3)]">
                      <td className="py-2.5 text-[var(--color-text2)]">{fmtOrderDate(date)}</td>
                      <td className="py-2.5 font-mono font-semibold">{o.symbol}</td>
                      <td className="py-2.5">
                        <Badge tone={o.side === "buy" ? "green" : "red"}>
                          {o.side === "buy" ? "Compra" : "Venta"}
                        </Badge>
                      </td>
                      <td className="py-2.5 text-right font-mono">{qty}</td>
                      <td className="py-2.5 text-right font-mono">
                        {price ? `$${fmt$(price)}` : <span className="text-[var(--color-text3)]">—</span>}
                      </td>
                      <td className="py-2.5 text-right font-mono font-medium">
                        {total ? `$${fmt$(total)}` : <span className="text-[var(--color-text3)]">—</span>}
                      </td>
                      <td className="py-2.5 text-right">
                        <Badge tone={statusTone(o.status)}>
                          {STATUS_LABEL[o.status] ?? o.status}
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

    </div>
  );
}

// ─── Skeleton ─────────────────────────────────────────────────────────────────

function PageSkeleton() {
  return (
    <div className="space-y-5">
      <Card>
        <Skeleton className="mb-4 h-4 w-32" />
        <div className="grid grid-cols-4 gap-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-20" />)}
        </div>
      </Card>
      <Card>
        <Skeleton className="mb-4 h-4 w-40" />
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => <Skeleton key={i} className="h-10" />)}
        </div>
      </Card>
    </div>
  );
}
