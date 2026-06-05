"use client";

/**
 * "¿A dónde va el precio?" — honest price forecast (HER-17).
 *
 * Shows the model's central path with a confidence band that widens with the
 * horizon, a plain-language summary line, the out-of-sample hit rate from the
 * HER-13 walk-forward (so the user knows how reliable the model has been), and
 * an always-visible disclaimer. REVIEW-status models are dimmed and flagged.
 */

import { useEffect, useState } from "react";

import { ForecastBandChart } from "@/components/charts";
import { EmptyState, fmtDate, fmtNumber, fmtPct } from "@/components/primitives";
import {
  api,
  type ForecastResult,
  type ValidationResult,
} from "@/lib/api";

type HistoryPoint = { date: string; price: number };

const usd = (n: number) => `$${fmtNumber(n, { decimals: 2 })}`;

export function ForecastSection({
  ticker,
  history,
}: {
  ticker: string;
  history: HistoryPoint[];
}) {
  const [fc, setFc] = useState<ForecastResult | null>(null);
  const [fcError, setFcError] = useState(false);
  const [val, setVal] = useState<ValidationResult | null>(null);
  const [valDone, setValDone] = useState(false);

  useEffect(() => {
    let alive = true;
    setFc(null);
    setFcError(false);
    api
      .getForecast(ticker, 5)
      .then((r) => alive && setFc(r))
      .catch(() => alive && setFcError(true));
    return () => {
      alive = false;
    };
  }, [ticker]);

  useEffect(() => {
    let alive = true;
    setVal(null);
    setValDone(false);
    // Ridge is the validated-best estimator (see HER-13/16). The walk-forward
    // recomputes server-side, so this can take a few seconds — stream it in.
    api
      .getValidation(ticker, { estimator: "ridge" })
      .then((r) => alive && setVal(r))
      .catch(() => alive && setVal(null))
      .finally(() => alive && setValDone(true));
    return () => {
      alive = false;
    };
  }, [ticker]);

  if (fcError) {
    return (
      <EmptyState
        title="Pronóstico no disponible"
        description="Este ticker no tiene un modelo activo, así que todavía no se puede proyectar el precio."
      />
    );
  }

  if (!fc) {
    return (
      <div className="flex h-[320px] items-center justify-center text-[13px] text-[var(--color-text3)]">
        Calculando pronóstico…
      </div>
    );
  }

  const last = fc.points[fc.points.length - 1];
  const isReview = fc.status === "REVIEW";

  // Build chart rows: recent history, then the forecast band. The junction row
  // (last historical point) seeds the central line + band at zero width so they
  // connect visually to the realized price.
  const tail = history.slice(-20);
  const rows = [
    ...tail.map((h, i) => {
      const isJunction = i === tail.length - 1;
      return {
        date: h.date,
        hist: h.price,
        central: isJunction ? h.price : null,
        bandBase: isJunction ? h.price : null,
        bandSpan: isJunction ? 0 : null,
        lower: isJunction ? h.price : null,
        upper: isJunction ? h.price : null,
      };
    }),
    ...fc.points.map((p) => ({
      date: p.on,
      hist: null,
      central: p.central,
      bandBase: p.lower,
      bandSpan: p.upper - p.lower,
      lower: p.lower,
      upper: p.upper,
    })),
  ];

  const dirWord = fc.direction === "up" ? "alza" : "baja";
  const dirColor =
    fc.direction === "up" ? "var(--color-green)" : "var(--color-red)";

  return (
    <div>
      {isReview && (
        <div
          role="status"
          className="mb-4 rounded-[10px] border border-[var(--color-amber)]/40 bg-[var(--color-amber)]/10 px-3.5 py-2.5 text-[12.5px] text-[var(--color-amber)]"
        >
          Modelo en revisión (no superó todos los diagnósticos). El pronóstico se
          muestra atenuado — tómalo con cautela.
        </div>
      )}

      {/* Headline summary */}
      <div className="mb-4 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[14px] text-[var(--color-text)] text-pretty">
        <span
          className="inline-flex items-center gap-1.5 font-medium"
          style={{ color: dirColor }}
        >
          <span aria-hidden className="text-[16px]">
            {fc.direction_symbol}
          </span>
          Dirección esperada: {dirWord}
        </span>
        <span className="text-[var(--color-text3)]">·</span>
        <span>
          Precio central a {fmtDate(last.on)}:{" "}
          <span className="tabular font-medium">{usd(last.central)}</span>
        </span>
        <span className="text-[var(--color-text3)]">·</span>
        <span className="text-[var(--color-text2)]">
          Rango probable (90%):{" "}
          <span className="tabular">
            {usd(last.lower)} – {usd(last.upper)}
          </span>
        </span>
      </div>

      <div
        className="transition-opacity"
        style={{ opacity: isReview ? 0.55 : 1 }}
      >
        <ForecastBandChart data={rows} />
      </div>

      {/* Out-of-sample reliability + disclaimer */}
      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-stretch">
        <HitRateCard val={val} done={valDone} />
        <p
          role="note"
          className="flex-1 rounded-[10px] border border-[var(--color-bg4)] bg-[var(--color-bg3)] px-3.5 py-2.5 text-[11.5px] leading-relaxed text-[var(--color-text3)] text-pretty"
        >
          Proyección estadística basada en datos históricos. No es garantía de
          rendimiento. La incertidumbre crece con el horizonte — la banda se
          ensancha cada día. {fc.sigma_source === "realized_vol" && "Banda estimada con la volatilidad realizada reciente."}
        </p>
      </div>
    </div>
  );
}

function HitRateCard({
  val,
  done,
}: {
  val: ValidationResult | null;
  done: boolean;
}) {
  let body: React.ReactNode;
  if (!done) {
    body = (
      <span className="text-[13px] text-[var(--color-text3)]">
        Calculando histórico…
      </span>
    );
  } else if (!val || val.hit_rate === null || val.n_predictions === 0) {
    body = (
      <span className="text-[13px] text-[var(--color-text3)]">
        Histórico insuficiente
      </span>
    );
  } else {
    const sig = val.significant;
    body = (
      <>
        <div className="flex items-baseline gap-2">
          <span className="tabular text-[22px] font-semibold text-[var(--color-text)]">
            {fmtPct(val.hit_rate, { decimals: 1 })}
          </span>
          <span
            className="rounded-full px-2 py-0.5 text-[10px] font-medium"
            style={{
              background: sig
                ? "color-mix(in oklab, var(--color-green) 18%, transparent)"
                : "var(--color-bg4)",
              color: sig ? "var(--color-green)" : "var(--color-text3)",
            }}
          >
            {sig ? "significativo" : "no significativo"}
          </span>
        </div>
        <span className="mt-0.5 text-[11.5px] text-[var(--color-text3)] tabular">
          base días al alza {fmtPct(val.up_day_base_rate, { decimals: 1 })} ·{" "}
          {val.n_predictions} días fuera de muestra
        </span>
      </>
    );
  }

  return (
    <div className="flex min-w-[220px] flex-col rounded-[10px] border border-[var(--color-bg4)] bg-[var(--color-bg2)] px-3.5 py-2.5">
      <span className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
        Acierto direccional (fuera de muestra)
      </span>
      {body}
    </div>
  );
}
