"use client";

/**
 * Cuentas — hub central de los 3 portafolios reales en Alpaca Paper Trading.
 * Muestra valor, P&L, posiciones y perfil de riesgo de cada bot.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { Badge, Card, fmtNumber, fmtPct } from "@/components/primitives";

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

type BotAccount = {
  equity: string;
  cash: string;
  buying_power: string;
  daytrade_count: number;
};

type BotData = {
  bot: string;
  botLabel: string;
  account: BotAccount;
  positions: Position[];
  orders: Record<string, unknown>[];
  error?: string;
};

// ─── Bot config ───────────────────────────────────────────────────────────────

const BOTS = [
  {
    key: "ols",
    label: "OLS Model Bot",
    profile: "P4 · Moderado Agresivo",
    description: "Opera usando predicciones de regresión OLS sobre 9 acciones vs variables macro.",
    color: "var(--color-green)",
    accent: "border-[var(--color-green)]/40 bg-[var(--color-green)]/5",
    dot: "bg-[var(--color-green)]",
    tone: "green" as const,
    href: "/positions?bot=ols",
  },
  {
    key: "capitol",
    label: "Capitol Trades Bot",
    profile: "Seguidor de congresistas",
    description: "Copia las operaciones de Nancy Pelosi y otros congresistas de EE.UU.",
    color: "var(--color-cyan)",
    accent: "border-[var(--color-cyan)]/40 bg-[var(--color-cyan)]/5",
    dot: "bg-[var(--color-cyan)]",
    tone: "neutral" as const,
    href: "/positions?bot=capitol",
  },
  {
    key: "trailing",
    label: "P0 Ultra Conservador Bot",
    profile: "Monitor dinámico TSLA",
    description: "Invierte en acciones de bajo riesgo según los pesos del perfil P0 Ultra Conservador.",
    color: "var(--color-violet)",
    accent: "border-[var(--color-violet)]/40 bg-[var(--color-violet)]/5",
    dot: "bg-[var(--color-violet)]",
    tone: "neutral" as const,
    href: "/positions?bot=trailing",
  },
];

// ─── Component ────────────────────────────────────────────────────────────────

export default function AccountsPage() {
  const [data, setData] = useState<Record<string, BotData | null>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({
    ols: true, capitol: true, trailing: true,
  });

  useEffect(() => {
    BOTS.forEach(({ key }) => {
      fetch(`/api/bot?bot=${key}`)
        .then((r) => r.json())
        .then((d) => setData((prev) => ({ ...prev, [key]: d as BotData })))
        .catch(() => setData((prev) => ({ ...prev, [key]: null })))
        .finally(() => setLoading((prev) => ({ ...prev, [key]: false })));
    });
  }, []);

  // Resumen global
  const totalEquity = BOTS.reduce((sum, b) => {
    const d = data[b.key];
    return sum + (d?.account ? parseFloat(d.account.equity) : 0);
  }, 0);

  const totalPnl = BOTS.reduce((sum, b) => {
    const d = data[b.key];
    if (!d?.positions) return sum;
    return sum + d.positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0);
  }, 0);

  const allLoaded = Object.values(loading).every((v) => !v);

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">

      {/* Header */}
      <div className="mb-8">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          ALPACA PAPER TRADING
        </div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Mis Portafolios</h1>
        <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
          Los 3 bots activos en Alpaca Paper Trading. Cada uno sigue una estrategia distinta.
          Los datos se actualizan en tiempo real desde Alpaca.
        </p>
      </div>

      {/* Tarjeta resumen global */}
      {allLoaded && totalEquity > 0 && (
        <div className="mb-8 rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] p-5">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            CONSOLIDADO · 3 BOTS
          </div>
          <div className="flex flex-wrap items-end gap-6">
            <div>
              <div className="text-[11px] text-[var(--color-text3)]">Valor total</div>
              <div className="font-mono text-[28px] font-bold tracking-tight">
                ${fmtNumber(totalEquity, { decimals: 2 })}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-[var(--color-text3)]">P&L no realizado</div>
              <div className={`font-mono text-[20px] font-semibold ${totalPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                {totalPnl >= 0 ? "+" : ""}${fmtNumber(totalPnl, { decimals: 2 })}
              </div>
            </div>
            <div className="ml-auto">
              <Link
                href="/simulator"
                className="rounded-none bg-[var(--color-bg4)] px-4 py-2 text-[12px] font-medium text-[var(--color-text2)] transition hover:bg-[var(--color-border3)] hover:text-[var(--color-text)]"
              >
                ✨ Simular escenario
              </Link>
            </div>
          </div>
        </div>
      )}

      {/* Cards por bot */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-1">
        {BOTS.map((bot) => {
          const d = data[bot.key];
          const isLoading = loading[bot.key];
          const equity = d?.account ? parseFloat(d.account.equity) : null;
          const cash = d?.account ? parseFloat(d.account.cash) : null;
          const positions = d?.positions ?? [];
          const totalPositionPnl = positions.reduce((s, p) => s + parseFloat(p.unrealized_pl), 0);
          const hasError = d?.error;

          return (
            <div
              key={bot.key}
              className={`rounded-none border p-5 transition ${bot.accent}`}
            >
              {/* Bot header */}
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div className="flex items-start gap-3">
                  <div className={`mt-1.5 h-2.5 w-2.5 flex-shrink-0 rounded-full ${bot.dot}`} />
                  <div>
                    <div className="text-[15px] font-semibold">{bot.label}</div>
                    <div className="mt-0.5 text-[12px] text-[var(--color-text3)]">{bot.description}</div>
                    <div className="mt-1">
                      <Badge tone={bot.tone}>{bot.profile}</Badge>
                    </div>
                  </div>
                </div>

                {/* Equity + P&L */}
                {!isLoading && !hasError && equity !== null && (
                  <div className="text-right">
                    <div className="font-mono text-[20px] font-bold">
                      ${fmtNumber(equity, { decimals: 2 })}
                    </div>
                    <div className="text-[11px] text-[var(--color-text3)]">
                      Efectivo disponible: ${fmtNumber(cash ?? 0, { decimals: 2 })}
                    </div>
                    {positions.length > 0 && (
                      <div className={`mt-0.5 font-mono text-[13px] font-medium ${totalPositionPnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                        P&L: {totalPositionPnl >= 0 ? "+" : ""}${fmtNumber(totalPositionPnl, { decimals: 2 })}
                      </div>
                    )}
                  </div>
                )}

                {isLoading && (
                  <div className="flex items-center gap-2 text-[12px] text-[var(--color-text3)]">
                    <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                    Cargando…
                  </div>
                )}
              </div>

              {/* Error */}
              {!isLoading && hasError && (
                <div className="mb-3 rounded-none bg-[var(--color-red)]/10 px-3 py-2 text-[12px] text-[var(--color-red)]">
                  Error al conectar con Alpaca. Verifica las claves de API.
                </div>
              )}

              {/* Posiciones */}
              {!isLoading && !hasError && positions.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                        <th className="py-2 pr-3 font-medium">Acción</th>
                        <th className="py-2 pr-3 text-right font-medium">Cant.</th>
                        <th className="py-2 pr-3 text-right font-medium">Precio entrada</th>
                        <th className="py-2 pr-3 text-right font-medium">Precio actual</th>
                        <th className="py-2 pr-3 text-right font-medium">Valor</th>
                        <th className="py-2 text-right font-medium">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map((p) => {
                        const pnl = parseFloat(p.unrealized_pl);
                        const pnlPct = parseFloat(p.unrealized_plpc);
                        return (
                          <tr key={p.symbol} className="border-b border-[var(--color-border)] last:border-0">
                            <td className="py-2 pr-3 font-mono font-semibold">{p.symbol}</td>
                            <td className="py-2 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                              {parseFloat(p.qty)}
                            </td>
                            <td className="py-2 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                              ${fmtNumber(parseFloat(p.avg_entry_price), { decimals: 2 })}
                            </td>
                            <td className="py-2 pr-3 text-right font-mono tabular">
                              ${fmtNumber(parseFloat(p.current_price), { decimals: 2 })}
                            </td>
                            <td className="py-2 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                              ${fmtNumber(parseFloat(p.market_value), { decimals: 2 })}
                            </td>
                            <td className="py-2 text-right">
                              <div className={`font-mono font-medium ${pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                                {pnl >= 0 ? "+" : ""}${fmtNumber(pnl, { decimals: 2 })}
                              </div>
                              <div className={`text-[10px] ${pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                                {fmtPct(pnlPct, { signed: true, decimals: 2 })}
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Sin posiciones */}
              {!isLoading && !hasError && positions.length === 0 && (
                <div className="rounded-none bg-[var(--color-bg3)] px-4 py-3 text-[12.5px] text-[var(--color-text3)]">
                  Sin posiciones abiertas actualmente.
                </div>
              )}

              {/* Footer */}
              <div className="mt-4 flex items-center justify-between">
                <div className="text-[10px] text-[var(--color-text3)]">
                  {!isLoading && !hasError && (
                    <span>{positions.length} posición{positions.length !== 1 ? "es" : ""} abiertas · {d?.orders?.length ?? 0} órdenes recientes</span>
                  )}
                </div>
                <Link
                  href={bot.href}
                  className="rounded-none border border-[var(--color-border)] px-3 py-1.5 text-[11.5px] font-medium text-[var(--color-text2)] transition hover:border-[var(--color-border3)] hover:text-[var(--color-text)]"
                >
                  Ver detalle →
                </Link>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
