"use client";

/**
 * Simulator — push hypothetical predictor returns through every active
 * model, see the per-ticker + portfolio-level impact in real time.
 */

import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";

import {
  Badge,
  Card,
  EmptyState,
  fmtNumber,
  fmtPct,
  SectionHeader,
  StatTile,
} from "@/components/primitives";
import { api, type SimulateResponse, type Variable } from "@/lib/api";

const PRESET_SLIDERS: Array<{
  id: string;
  tkey: string;
  min: number;
  max: number;
  step: number;
  unit: string;
}> = [
  { id: "CPI_YoY_US", tkey: "slider_cpi", min: -0.02, max: 0.02, step: 0.0005, unit: "log-ret" },
  { id: "EUR_USD", tkey: "slider_eurusd", min: -0.03, max: 0.03, step: 0.001, unit: "log-ret" },
  { id: "Brent_Crude", tkey: "slider_brent", min: -0.05, max: 0.05, step: 0.002, unit: "log-ret" },
  { id: "Gold_Spot", tkey: "slider_gold", min: -0.04, max: 0.04, step: 0.001, unit: "log-ret" },
  { id: "Unemployment_Rate_US", tkey: "slider_unemployment", min: -0.02, max: 0.02, step: 0.0005, unit: "log-ret" },
  { id: "USD_CNY", tkey: "slider_usdcny", min: -0.02, max: 0.02, step: 0.001, unit: "log-ret" },
];

export default function SimulatorPage() {
  const t = useTranslations("simulator");
  const tc = useTranslations("common");
  const [variables, setVariables] = useState<Variable[]>([]);
  const [inputs, setInputs] = useState<Record<string, number>>(
    Object.fromEntries(PRESET_SLIDERS.map((s) => [s.id, 0])),
  );
  const [response, setResponse] = useState<SimulateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [horizon, setHorizon] = useState(7);

  useEffect(() => {
    api.listVariables("predictor").then(setVariables).catch(() => {});
  }, []);

  // Debounce simulate call
  useEffect(() => {
    const handle = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const r = await api.simulate(inputs, horizon);
        setResponse(r);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setResponse(null);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => clearTimeout(handle);
  }, [inputs, horizon]);

  const portfolioDeltaPct =
    response && response.portfolio_value_baseline > 0
      ? response.delta / response.portfolio_value_baseline
      : null;

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          {t("eyebrow")}
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
          {t("subtitle")}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[420px_1fr]">
        {/* Inputs */}
        <Card>
          <SectionHeader
            eyebrow={t("inputs_eyebrow")}
            title={t("inputs_title")}
            description={t("inputs_desc")}
          />
          <div className="space-y-5">
            {PRESET_SLIDERS.map((s) => (
              <div key={s.id}>
                <div className="mb-1 flex items-center justify-between">
                  <label
                    htmlFor={`slider-${s.id}`}
                    className="text-[12px] font-medium text-[var(--color-text)]"
                  >
                    {t(s.tkey)}
                  </label>
                  <span className="font-mono tabular text-[11.5px] text-[var(--color-text2)]">
                    {fmtPct(inputs[s.id] ?? 0, { signed: true, decimals: 3 })}
                  </span>
                </div>
                <input
                  id={`slider-${s.id}`}
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={inputs[s.id] ?? 0}
                  onChange={(e) =>
                    setInputs((p) => ({
                      ...p,
                      [s.id]: parseFloat(e.target.value),
                    }))
                  }
                  className="h-1.5 w-full cursor-ew-resize appearance-none rounded-full bg-[var(--color-bg4)] accent-[var(--color-cyan)]"
                />
              </div>
            ))}

            <div className="border-t border-[var(--color-border)] pt-4">
              <div className="mb-1 flex items-center justify-between">
                <label
                  htmlFor="horizon"
                  className="text-[12px] font-medium text-[var(--color-text)]"
                >
                  {t("horizon")}
                </label>
                <span className="font-mono tabular text-[11.5px] text-[var(--color-text2)]">
                  {horizon}d
                </span>
              </div>
              <input
                id="horizon"
                type="range"
                min={1}
                max={30}
                step={1}
                value={horizon}
                onChange={(e) => setHorizon(parseInt(e.target.value, 10))}
                className="h-1.5 w-full cursor-ew-resize appearance-none rounded-full bg-[var(--color-bg4)] accent-[var(--color-cyan)]"
              />
            </div>

            <button
              type="button"
              onClick={() =>
                setInputs(
                  Object.fromEntries(PRESET_SLIDERS.map((s) => [s.id, 0])),
                )
              }
              className="w-full rounded-[10px] bg-[var(--color-bg4)] py-2 text-[12px] font-medium text-[var(--color-text2)] transition active:scale-[0.97] hover:bg-[var(--color-border3)] hover:text-[var(--color-text)]"
            >
              {t("reset")}
            </button>
          </div>
        </Card>

        {/* Outputs */}
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <StatTile
              label={t("stat_portfolio_value")}
              value={
                response
                  ? `$${fmtNumber(response.portfolio_value, { decimals: 2 })}`
                  : "—"
              }
              hint={
                response
                  ? t("stat_portfolio_vs", { value: fmtNumber(response.portfolio_value_baseline, { decimals: 2 }) })
                  : undefined
              }
            />
            <StatTile
              label={t("stat_delta")}
              value={
                response
                  ? `$${fmtNumber(response.delta, { decimals: 2 })}`
                  : "—"
              }
              delta={
                portfolioDeltaPct !== null
                  ? {
                      value: fmtPct(portfolioDeltaPct, {
                        signed: true,
                        decimals: 2,
                      }),
                      tone: portfolioDeltaPct >= 0 ? "green" : "red",
                    }
                  : undefined
              }
            />
            <StatTile
              label={t("stat_horizon")}
              value={`${response?.horizon_days ?? horizon}d`}
              hint={t("stat_horizon_hint")}
              mono={false}
            />
          </div>

          {loading && (
            <div className="text-[11.5px] text-[var(--color-text3)]">
              {t("recomputing")}
            </div>
          )}
          {error && (
            <Card>
              <div className="text-[12.5px] text-[var(--color-red)]">{error}</div>
            </Card>
          )}

          {!response || response.per_ticker.length === 0 ? (
            <EmptyState
              title={t("empty_title")}
              description={t("empty_desc")}
            />
          ) : (
            <Card>
              <SectionHeader
                eyebrow={t("per_ticker_eyebrow")}
                title={t("per_ticker_title")}
                description={t("per_ticker_desc")}
              />
              <div className="overflow-x-auto">
                <table className="w-full text-[12.5px]">
                  <thead>
                    <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                      <th className="py-2 pr-3 font-medium">{tc("ticker")}</th>
                      <th className="py-2 pr-3 text-right font-medium">{tc("last")}</th>
                      <th className="py-2 pr-3 text-right font-medium">
                        {tc("predicted")}
                      </th>
                      <th className="py-2 pr-3 text-right font-medium">
                        {t("th_delta_return")}
                      </th>
                      <th className="py-2 pr-3 text-left font-medium">
                        {t("th_top_driver")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {response.per_ticker.map((t) => {
                      const driver = topDriver(t.contributions);
                      return (
                        <tr
                          key={t.ticker}
                          className="border-b border-[var(--color-border)] last:border-0"
                        >
                          <td className="py-2 pr-3 font-mono font-medium">
                            {t.ticker}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                            ${fmtNumber(t.last_price, { decimals: 2 })}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono tabular">
                            ${fmtNumber(t.predicted_price, { decimals: 2 })}
                          </td>
                          <td className="py-2 pr-3 text-right">
                            <Badge
                              tone={t.predicted_return >= 0 ? "green" : "red"}
                            >
                              {fmtPct(t.predicted_return, {
                                signed: true,
                                decimals: 3,
                              })}
                            </Badge>
                          </td>
                          <td className="py-2 pr-3 text-[var(--color-text2)]">
                            {driver ? (
                              <span className="font-mono">
                                {driver.name}{" "}
                                <span className="text-[var(--color-text3)]">
                                  ({fmtPct(driver.value, { signed: true, decimals: 3 })})
                                </span>
                              </span>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function topDriver(contribs: Record<string, number>): { name: string; value: number } | null {
  const entries = Object.entries(contribs);
  if (entries.length === 0) return null;
  const max = entries.reduce(
    (acc, [name, value]) =>
      Math.abs(value) > Math.abs(acc.value) ? { name, value } : acc,
    { name: entries[0][0], value: entries[0][1] },
  );
  return max;
}
