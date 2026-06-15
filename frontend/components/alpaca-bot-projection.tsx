"use client";

import { useEffect, useState } from "react";
import { Badge, Card, fmtNumber, fmtPct, SectionHeader } from "@/components/primitives";

type Position = {
  symbol: string;
  qty: string;
  market_value: string;
  current_price: string;
  unrealized_pl: string;
  unrealized_plpc: string;
};

type Signal = {
  ticker: string;
  currentPrice: number;
  predictedPrice: number;
  deltaPct: number;
  action: "BUY" | "SELL" | "HOLD";
};

type BotData = {
  account: { equity: string; cash: string };
  positions: Position[];
  signals: Signal[];
  error?: string;
};

const BOTS = [
  { key: "ols",      label: "OLS Bot · P4",    color: "var(--color-green)",  dot: "bg-[var(--color-green)]"  },
  { key: "capitol",  label: "P1 Conservador",  color: "var(--color-cyan)",   dot: "bg-[var(--color-cyan)]"   },
  { key: "trailing", label: "P0 Ultra Cons.",  color: "var(--color-violet)", dot: "bg-[var(--color-violet)]" },
];

export function AlpacaBotProjection() {
  const [activeBot, setActiveBot] = useState("ols");
  const [cache, setCache] = useState<Record<string, BotData | null>>({});
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (cache[activeBot] !== undefined) return;
    setLoading(true);
    fetch(`/api/bot?bot=${activeBot}`)
      .then((r) => r.json())
      .then((d) => setCache((p) => ({ ...p, [activeBot]: d as BotData })))
      .catch(() => setCache((p) => ({ ...p, [activeBot]: null })))
      .finally(() => setLoading(false));
  }, [activeBot, cache]);

  const data = cache[activeBot];
  const positions = data?.positions ?? [];
  const signalMap = new Map((data?.signals ?? []).map((s) => [s.ticker, s]));
  const equity = data?.account ? parseFloat(data.account.equity) : null;

  const rows = positions.map((p) => {
    const sig = signalMap.get(p.symbol);
    const qty = parseFloat(p.qty);
    const lastPrice = parseFloat(p.current_price);
    const mv = parseFloat(p.market_value);
    const predictedPrice = sig?.predictedPrice ?? null;
    const deltaDollar = predictedPrice !== null ? (predictedPrice - lastPrice) * qty : null;
    return { symbol: p.symbol, qty, lastPrice, mv, predictedPrice, deltaDollar, deltaPct: sig?.deltaPct ?? null };
  });

  const currentMv = rows.reduce((s, r) => s + r.mv, 0);
  const projectedDelta = rows.reduce((s, r) => s + (r.deltaDollar ?? 0), 0);
  const projectedMv = currentMv + projectedDelta;
  const projectedDeltaPct = currentMv > 0 ? projectedDelta / currentMv : null;

  const activeBotCfg = BOTS.find((b) => b.key === activeBot)!;

  return (
    <div>
      {/* Tab bar */}
      <div className="mb-4 flex gap-1 rounded-none bg-[var(--color-bg3)] p-1 w-fit">
        {BOTS.map((b) => (
          <button
            key={b.key}
            onClick={() => setActiveBot(b.key)}
            className={`rounded-none px-3 py-1.5 text-[12px] font-medium transition ${
              activeBot === b.key
                ? "bg-[var(--color-bg)] shadow-sm"
                : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
            }`}
            style={activeBot === b.key ? { color: b.color } : {}}
          >
            {b.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-6 text-[12px] text-[var(--color-text3)]">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Cargando datos de Alpaca…
        </div>
      ) : !data || data.error || positions.length === 0 ? (
        <div className="rounded-none border border-[var(--color-border)] bg-[var(--color-bg3)] px-4 py-4 text-[12.5px] text-[var(--color-text3)]">
          Sin posiciones abiertas en este bot.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_2fr]">
          {/* Summary tile */}
          <Card>
            <div className="mb-3 flex items-center gap-2">
              <span className={`h-2 w-2 rounded-full ${activeBotCfg.dot}`} />
              <span className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: activeBotCfg.color }}>
                {activeBotCfg.label}
              </span>
            </div>
            <div className="space-y-3">
              <div>
                <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">Valor actual</div>
                <div className="font-mono text-[20px] font-bold">${fmtNumber(currentMv, { decimals: 2 })}</div>
                <div className="text-[11px] text-[var(--color-text3)]">{positions.length} posiciones abiertas</div>
              </div>
              {projectedDeltaPct !== null && (
                <div>
                  <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">Proyectado (próx. día hábil)</div>
                  <div className="font-mono text-[18px] font-semibold">${fmtNumber(projectedMv, { decimals: 2 })}</div>
                  <div className={`font-mono text-[13px] font-medium ${projectedDelta >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                    {projectedDelta >= 0 ? "+" : ""}${fmtNumber(projectedDelta, { decimals: 2 })}
                    {" "}({fmtPct(projectedDeltaPct, { signed: true, decimals: 3 })})
                  </div>
                  <div className="mt-0.5 text-[10px] text-[var(--color-text3)]">
                    {rows.filter((r) => r.predictedPrice !== null).length} de {rows.length} tickers con predicción
                  </div>
                </div>
              )}
              {equity !== null && (
                <div className="border-t border-[var(--color-border)] pt-2">
                  <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">Equity total cuenta</div>
                  <div className="font-mono text-[14px] font-medium">${fmtNumber(equity, { decimals: 2 })}</div>
                </div>
              )}
            </div>
          </Card>

          {/* Per-ticker table */}
          <Card>
            <SectionHeader eyebrow="POR TICKER" title="Posición vs. predicción"
              description="Precio actual vs. predicción OLS del próximo día hábil." />
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                    <th className="py-2 pr-3 font-medium">Ticker</th>
                    <th className="py-2 pr-3 text-right font-medium">Cant.</th>
                    <th className="py-2 pr-3 text-right font-medium">Último</th>
                    <th className="py-2 pr-3 text-right font-medium">Predicho</th>
                    <th className="py-2 pr-3 text-right font-medium">Δ Valor</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.symbol} className="border-b border-[var(--color-border)] last:border-0">
                      <td className="py-2 pr-3 font-mono font-medium">{r.symbol}</td>
                      <td className="py-2 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                        {fmtNumber(r.qty, { decimals: 0 })}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono tabular">
                        ${fmtNumber(r.lastPrice, { decimals: 2 })}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono tabular">
                        {r.predictedPrice !== null
                          ? `$${fmtNumber(r.predictedPrice, { decimals: 2 })}`
                          : <span className="text-[var(--color-text3)]">—</span>}
                      </td>
                      <td className="py-2 pr-3 text-right">
                        {r.deltaDollar !== null ? (
                          <div className="flex flex-col items-end gap-0.5">
                            <Badge tone={r.deltaDollar >= 0 ? "green" : "red"}>
                              {r.deltaDollar >= 0 ? "+" : "-"}${fmtNumber(Math.abs(r.deltaDollar), { decimals: 2 })}
                            </Badge>
                            {r.deltaPct !== null && (
                              <span className="text-[10px] text-[var(--color-text3)]">
                                {fmtPct(r.deltaPct / 100, { signed: true, decimals: 2 })}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="text-[var(--color-text3)]">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
