"use client";

import { useEffect, useState } from "react";
import { Badge, fmtPct } from "@/components/primitives";

type TickerAccuracy = {
  ticker: string;
  n_total: number;
  n_actual: number;
  n_filtered: number;
  hit_rate: number | null;
  mape: number | null;
  days: number;
  min_signal: number;
};

const DAYS_OPTIONS = [30, 60, 90];
const TARGET_HIT_RATE = 0.80;

// Signal threshold presets (as decimal return, e.g. 0.003 = 0.3%)
const SIGNAL_PRESETS = [
  { label: "Sin filtro", value: 0 },
  { label: "0.1%", value: 0.001 },
  { label: "0.3%", value: 0.003 },
  { label: "0.5%", value: 0.005 },
  { label: "1%",   value: 0.010 },
];

function hitColor(rate: number | null): string {
  if (rate === null) return "text-[var(--color-text3)]";
  if (rate >= TARGET_HIT_RATE) return "text-[var(--color-green)]";
  if (rate >= 0.60) return "text-[var(--color-amber)]";
  return "text-[var(--color-red)]";
}

function hitTone(rate: number | null): "green" | "amber" | "red" | "neutral" {
  if (rate === null) return "neutral";
  if (rate >= TARGET_HIT_RATE) return "green";
  if (rate >= 0.60) return "amber";
  return "red";
}

export default function AccuracyPage() {
  const [days, setDays]           = useState(90);
  const [minSignal, setMinSignal] = useState(0);
  const [data, setData]           = useState<TickerAccuracy[]>([]);
  const [loading, setLoading]     = useState(true);

  useEffect(() => {
    setLoading(true);
    fetch(`/api/accuracy?days=${days}&min_signal=${minSignal}`)
      .then((r) => r.json())
      .then((d) => Array.isArray(d) && setData(d))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [days, minSignal]);

  const withData  = data.filter((d) => d.n_actual >= 3);
  const avgHit    = withData.length
    ? withData.reduce((s, d) => s + (d.hit_rate ?? 0), 0) / withData.length
    : null;
  const passing   = withData.filter((d) => (d.hit_rate ?? 0) >= TARGET_HIT_RATE).length;
  const sorted    = [...data].sort((a, b) => (b.hit_rate ?? -1) - (a.hit_rate ?? -1));
  const totalFiltered = data.reduce((s, d) => s + (d.n_filtered ?? 0), 0);

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">

      {/* Header */}
      <div className="mb-8">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          MODELOS · MÉTRICAS EN VIVO
        </div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">Precisión de predicciones</h1>
        <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
          Hit rate direccional y MAPE por ticker. El umbral de señal filtra días donde el modelo
          predice un cambio menor al porcentaje seleccionado — días donde el modelo "no tiene convicción".
        </p>
      </div>

      {/* Controls row */}
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        {/* Summary cards */}
        <div className="flex flex-wrap gap-3">
          <div className="rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3 min-w-[140px]">
            <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">Hit rate promedio</div>
            <div className={`font-mono text-[22px] font-bold ${hitColor(avgHit)}`}>
              {avgHit !== null ? fmtPct(avgHit, { decimals: 1 }) : "—"}
            </div>
            <div className="text-[11px] text-[var(--color-text3)]">objetivo 80%</div>
          </div>

          <div className="rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3 min-w-[140px]">
            <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">Tickers ≥ 80%</div>
            <div className={`font-mono text-[22px] font-bold ${passing === withData.length && withData.length > 0 ? "text-[var(--color-green)]" : "text-[var(--color-amber)]"}`}>
              {passing} / {withData.length}
            </div>
            <div className="text-[11px] text-[var(--color-text3)]">con ≥ 3 actuals</div>
          </div>

          {minSignal > 0 && (
            <div className="rounded-none border border-[var(--color-cyan)]/30 bg-[var(--color-bg2)] px-4 py-3 min-w-[140px]">
              <div className="text-[10px] uppercase tracking-widest text-[var(--color-cyan)]">Días filtrados</div>
              <div className="font-mono text-[22px] font-bold text-[var(--color-cyan)]">
                {totalFiltered}
              </div>
              <div className="text-[11px] text-[var(--color-text3)]">señal &lt; {fmtPct(minSignal, { decimals: 1 })}</div>
            </div>
          )}
        </div>

        {/* Day selector */}
        <div className="flex gap-1 rounded-none bg-[var(--color-bg3)] p-1">
          {DAYS_OPTIONS.map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-none px-3 py-1.5 text-[12px] font-medium transition ${
                days === d
                  ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-sm"
                  : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* Signal threshold filter */}
      <div className="mb-5 rounded-none border border-[var(--color-border)] bg-[var(--color-bg2)] p-4">
        <div className="mb-2 flex items-center gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-widest text-[var(--color-text2)]">
            Umbral de señal mínima
          </span>
          {minSignal > 0 && (
            <span className="rounded-full bg-[var(--color-cyan)]/15 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-widest text-[var(--color-cyan)]">
              activo
            </span>
          )}
        </div>
        <p className="mb-3 text-[11.5px] text-[var(--color-text3)]">
          El bot solo contará un día en el hit rate si predijo un cambio mayor a este porcentaje.
          Filtra días de baja convicción donde el modelo predice movimientos insignificantes.
        </p>
        <div className="flex flex-wrap gap-2">
          {SIGNAL_PRESETS.map((p) => (
            <button
              key={p.value}
              onClick={() => setMinSignal(p.value)}
              className={`rounded-none border px-3 py-1.5 text-[12px] font-medium transition ${
                minSignal === p.value
                  ? "border-[var(--color-cyan)] bg-[var(--color-cyan)]/10 text-[var(--color-cyan)]"
                  : "border-[var(--color-border)] text-[var(--color-text3)] hover:text-[var(--color-text2)]"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {loading ? (
        <div className="flex items-center gap-2 py-8 text-[12px] text-[var(--color-text3)]">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
          Cargando métricas…
        </div>
      ) : (
        <div className="rounded-none border border-[var(--color-border)]">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg2)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                <th className="px-4 py-3 font-medium">Ticker</th>
                <th className="px-4 py-3 text-right font-medium">Hit Rate</th>
                <th className="px-4 py-3 text-center font-medium">Estado</th>
                <th className="px-4 py-3 text-right font-medium">MAPE</th>
                {minSignal > 0 && (
                  <th className="px-4 py-3 text-right font-medium text-[var(--color-cyan)]">Filtrados</th>
                )}
                <th className="px-4 py-3 text-right font-medium">Actuals / Total</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const enoughData = row.n_actual >= 3;
                return (
                  <tr key={row.ticker} className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-bg2)]">
                    <td className="px-4 py-3 font-mono font-semibold">{row.ticker}</td>
                    <td className="px-4 py-3 text-right">
                      {row.hit_rate !== null && enoughData ? (
                        <span className={`font-mono text-[14px] font-bold ${hitColor(row.hit_rate)}`}>
                          {fmtPct(row.hit_rate, { decimals: 1 })}
                        </span>
                      ) : (
                        <span className="text-[var(--color-text3)]">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-center">
                      {!enoughData ? (
                        <Badge tone="neutral">Sin datos</Badge>
                      ) : row.hit_rate === null ? (
                        <Badge tone="neutral">—</Badge>
                      ) : (
                        <Badge tone={hitTone(row.hit_rate)}>
                          {row.hit_rate >= TARGET_HIT_RATE ? "✓ Objetivo" : row.hit_rate >= 0.60 ? "Mejorando" : "Por debajo"}
                        </Badge>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-[var(--color-text2)]">
                      {row.mape !== null ? fmtPct(row.mape, { decimals: 3 }) : "—"}
                    </td>
                    {minSignal > 0 && (
                      <td className="px-4 py-3 text-right font-mono text-[var(--color-cyan)]">
                        {row.n_filtered ?? 0}
                      </td>
                    )}
                    <td className="px-4 py-3 text-right text-[var(--color-text3)]">
                      {row.n_actual} / {row.n_total}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Progress bar */}
          {withData.length > 0 && (
            <div className="border-t border-[var(--color-border)] bg-[var(--color-bg2)] px-4 py-3">
              <div className="mb-1.5 flex items-center justify-between text-[11px]">
                <span className="text-[var(--color-text3)]">Progreso hacia objetivo 80% en todos los tickers</span>
                <span className="font-mono font-medium">{passing} / {withData.length}</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg4)]">
                <div
                  className="h-full rounded-full bg-[var(--color-green)] transition-all"
                  style={{ width: `${(passing / withData.length) * 100}%` }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      <p className="mt-4 text-[11px] text-[var(--color-text3)]">
        Hit rate = % de días donde el modelo predijo correctamente si el precio subió o bajó.
        Con umbral activo, solo se cuentan días donde |predicted_return| ≥ umbral.
        Modelos Ridge activos desde Jun 2026 — se necesitan ≥ 30 días para resultados representativos.
      </p>
    </div>
  );
}
