"use client";

import { useLocale, useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import {
  Badge,
  Card,
  EmptyState,
  fmtNumber,
  fmtPct,
  SectionHeader,
  StatTile,
} from "@/components/primitives";
import { api, type SimulateResponse, type SimulatedTicker } from "@/lib/api";

// ─── Slider definitions ──────────────────────────────────────────────────────

const SLIDERS = [
  { id: "CPI_YoY_US",           tkey: "slider_cpi",          tipKey: "slider_cpi_tip",          min: -0.02, max: 0.02, step: 0.0005 },
  { id: "EUR_USD",              tkey: "slider_eurusd",       tipKey: "slider_eurusd_tip",       min: -0.03, max: 0.03, step: 0.001  },
  { id: "Brent_Crude",         tkey: "slider_brent",        tipKey: "slider_brent_tip",        min: -0.05, max: 0.05, step: 0.002  },
  { id: "Gold_Spot",            tkey: "slider_gold",         tipKey: "slider_gold_tip",         min: -0.04, max: 0.04, step: 0.001  },
  { id: "Unemployment_Rate_US", tkey: "slider_unemployment", tipKey: "slider_unemployment_tip", min: -0.02, max: 0.02, step: 0.0005 },
  { id: "USD_CNY",              tkey: "slider_usdcny",       tipKey: "slider_usdcny_tip",       min: -0.02, max: 0.02, step: 0.001  },
];

const ZERO_INPUTS = Object.fromEntries(SLIDERS.map((s) => [s.id, 0]));

// ─── Preset scenarios ─────────────────────────────────────────────────────────

type Scenario = { key: string; values: Record<string, number> };

const SCENARIOS: Scenario[] = [
  {
    key: "scenario_recession",
    values: { CPI_YoY_US: -0.005, EUR_USD: -0.015, Brent_Crude: -0.04, Gold_Spot: 0.02, Unemployment_Rate_US: 0.015, USD_CNY: 0.008 },
  },
  {
    key: "scenario_trade_war",
    values: { CPI_YoY_US: 0.008, EUR_USD: -0.01, Brent_Crude: 0.02, Gold_Spot: 0.015, Unemployment_Rate_US: 0.005, USD_CNY: 0.018 },
  },
  {
    key: "scenario_oil_crisis",
    values: { CPI_YoY_US: 0.012, EUR_USD: -0.02, Brent_Crude: 0.045, Gold_Spot: 0.01, Unemployment_Rate_US: 0.008, USD_CNY: 0.005 },
  },
  {
    key: "scenario_fed_hike",
    values: { CPI_YoY_US: 0.015, EUR_USD: -0.025, Brent_Crude: -0.01, Gold_Spot: -0.02, Unemployment_Rate_US: -0.004, USD_CNY: 0.012 },
  },
  {
    key: "scenario_tech_rally",
    values: { CPI_YoY_US: -0.003, EUR_USD: 0.008, Brent_Crude: -0.015, Gold_Spot: -0.01, Unemployment_Rate_US: -0.006, USD_CNY: -0.005 },
  },
];

// ─── Types ────────────────────────────────────────────────────────────────────

type ScenarioData = {
  values: Record<string, number>;
  scenario_name: string;
  explanation: string;
  variable_context: Record<string, string>;
};

type AlpacaPosition = {
  symbol: string;
  qty: string;
  market_value: string;
  current_price: string;
  avg_entry_price: string;
  unrealized_pl: string;
};

type BotData = {
  account: { equity: string; cash: string };
  positions: AlpacaPosition[];
  error?: string;
};

// ─── Component ────────────────────────────────────────────────────────────────

export default function SimulatorPage() {
  const t = useTranslations("simulator");
  const tc = useTranslations("common");
  const locale = useLocale();

  const [inputs, setInputs] = useState<Record<string, number>>(ZERO_INPUTS);
  const [response, setResponse] = useState<SimulateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [horizon, setHorizon] = useState(7);
  const [activeScenario, setActiveScenario] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiData, setAiData] = useState<ScenarioData | null>(null);
  const [tooltip, setTooltip] = useState<string | null>(null);
  const [botData, setBotData] = useState<BotData | null>(null);
  const [botLoading, setBotLoading] = useState(true);
  const [p0Data, setP0Data]     = useState<BotData | null>(null);
  const [p0Loading, setP0Loading] = useState(true);

  // Load both bots once on mount
  useEffect(() => {
    fetch("/api/bot?bot=ols")
      .then((r) => r.json())
      .then((d) => setBotData(d as BotData))
      .catch(() => setBotData(null))
      .finally(() => setBotLoading(false));
    fetch("/api/bot?bot=trailing")
      .then((r) => r.json())
      .then((d) => setP0Data(d as BotData))
      .catch(() => setP0Data(null))
      .finally(() => setP0Loading(false));
  }, []);

  // Debounced simulate call
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

  // Alpaca real values take priority over model values
  const alpacaEquity = botData?.account ? parseFloat(botData.account.equity) : null;

  const alpacaDollarImpact = (response?.per_ticker && botData?.positions?.length)
    ? botData.positions.reduce((sum, pos) => {
        const row = response!.per_ticker.find((r) => r.ticker === pos.symbol);
        if (!row) return sum;
        return sum + parseFloat(pos.market_value) * row.predicted_return * horizon;
      }, 0)
    : null;

  const alpacaDeltaPct = (alpacaEquity && alpacaDollarImpact !== null && alpacaEquity > 0)
    ? alpacaDollarImpact / alpacaEquity
    : null;

  // Fallback to model values if no Alpaca data
  const portfolioDeltaPct = alpacaDeltaPct ??
    (response && response.portfolio_value_baseline > 0
      ? response.delta / response.portfolio_value_baseline
      : null);

  function applyScenario(sc: Scenario) {
    setActiveScenario(sc.key);
    setAiData(null);
    setInputs({ ...ZERO_INPUTS, ...sc.values });
  }

  function resetAll() {
    setInputs(ZERO_INPUTS);
    setActiveScenario(null);
    setAiData(null);
  }

  async function loadAiScenario() {
    setAiLoading(true);
    setAiError(null);
    setActiveScenario("ai");
    try {
      const res = await fetch(`/api/scenario?lang=${locale}`);
      const json = await res.json();
      if (!res.ok || json.error) throw new Error(json.error ?? `HTTP ${res.status}`);
      setAiData(json as ScenarioData);
      setInputs({ ...ZERO_INPUTS, ...(json as ScenarioData).values });
    } catch (e) {
      setAiError(`Error: ${e instanceof Error ? e.message : String(e)}`);
      setActiveScenario(null);
    } finally {
      setAiLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
      {/* Header */}
      <div className="mb-6">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          {t("eyebrow")}
        </div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{t("title")}</h1>
        <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
          {t("subtitle")}
        </p>
      </div>

      {/* AI + Preset scenarios row */}
      <div className="mb-6 space-y-3">
        {/* AI button */}
        <button
          onClick={loadAiScenario}
          disabled={aiLoading}
          className={[
            "flex w-full items-center gap-3 rounded-none border px-4 py-3 text-left transition",
            activeScenario === "ai"
              ? "border-[var(--color-cyan)] bg-[var(--color-cyan)]/10 text-[var(--color-cyan)]"
              : "border-[var(--color-border)] bg-[var(--color-bg2)] text-[var(--color-text)] hover:border-[var(--color-cyan)]/50",
          ].join(" ")}
        >
          <span className="text-lg">✨</span>
          <div className="flex-1">
            <div className="text-[13px] font-semibold">
              {aiLoading ? t("ai_loading") : t("ai_btn")}
            </div>
            {aiData && (
              <div className="mt-0.5 text-[11.5px] opacity-80">{aiData.scenario_name}</div>
            )}
          </div>
          {aiLoading && (
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--color-cyan)] border-t-transparent" />
          )}
        </button>

        {/* Preset scenario chips */}
        <div className="flex flex-wrap gap-2">
          {SCENARIOS.map((sc) => (
            <button
              key={sc.key}
              onClick={() => applyScenario(sc)}
              className={[
                "rounded-full border px-3 py-1 text-[12px] font-medium transition active:scale-95",
                activeScenario === sc.key
                  ? "border-[var(--color-violet)] bg-[var(--color-violet)]/15 text-[var(--color-violet)]"
                  : "border-[var(--color-border)] bg-[var(--color-bg2)] text-[var(--color-text2)] hover:border-[var(--color-border3)] hover:text-[var(--color-text)]",
              ].join(" ")}
            >
              {t(sc.key as Parameters<typeof t>[0])}
            </button>
          ))}
          <button
            onClick={resetAll}
            className="rounded-full border border-[var(--color-border)] bg-[var(--color-bg2)] px-3 py-1 text-[12px] font-medium text-[var(--color-text3)] transition hover:text-[var(--color-text)]"
          >
            ↺ {t("reset")}
          </button>
        </div>
      </div>

      {/* AI narrative */}
      {aiData && (
        <Card className="mb-6">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-cyan)]">
            {t("ai_context_eyebrow")}
          </div>
          <h3 className="mb-2 text-[14px] font-semibold">{aiData.scenario_name}</h3>
          <p className="text-[13px] leading-relaxed text-[var(--color-text2)]">
            {aiData.explanation}
          </p>
        </Card>
      )}
      {aiError && (
        <div className="mb-4 rounded-none bg-[var(--color-red)]/10 px-4 py-3 text-[12.5px] text-[var(--color-red)]">
          {aiError}
        </div>
      )}

      {/* Main grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[380px_1fr]">
        {/* Sliders */}
        <Card>
          <SectionHeader
            eyebrow={t("inputs_eyebrow")}
            title={t("inputs_title")}
            description={t("inputs_desc")}
          />
          <div className="space-y-5">
            {SLIDERS.map((s) => {
              const val = inputs[s.id] ?? 0;
              const tip = aiData?.variable_context?.[s.id];
              return (
                <div key={s.id}>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <label
                        htmlFor={`sl-${s.id}`}
                        className="text-[12px] font-medium text-[var(--color-text)] truncate"
                      >
                        {t(s.tkey as Parameters<typeof t>[0])}
                      </label>
                      {/* Tooltip trigger */}
                      <button
                        type="button"
                        onMouseEnter={() => setTooltip(s.id)}
                        onMouseLeave={() => setTooltip(null)}
                        onFocus={() => setTooltip(s.id)}
                        onBlur={() => setTooltip(null)}
                        className="relative flex-shrink-0 text-[10px] text-[var(--color-text3)] hover:text-[var(--color-text2)]"
                        aria-label="info"
                      >
                        ⓘ
                        {tooltip === s.id && (
                          <div className="absolute bottom-full left-0 z-50 mb-1.5 w-56 rounded-none border border-[var(--color-border)] bg-[var(--color-bg)] p-2.5 text-left text-[11.5px] leading-relaxed text-[var(--color-text2)] shadow-lg">
                            {tip ?? t(s.tipKey as Parameters<typeof t>[0])}
                          </div>
                        )}
                      </button>
                    </div>
                    <div className="flex items-center gap-1.5 flex-shrink-0">
                      {val !== 0 && (
                        <span className={`text-[10px] font-medium ${val > 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                          {val > 0 ? "▲" : "▼"}
                        </span>
                      )}
                      <span className="font-mono text-[11px] tabular text-[var(--color-text2)]">
                        {fmtPct(val, { signed: true, decimals: 2 })}
                      </span>
                    </div>
                  </div>
                  <input
                    id={`sl-${s.id}`}
                    type="range"
                    min={s.min}
                    max={s.max}
                    step={s.step}
                    value={val}
                    onChange={(e) => {
                      setActiveScenario(null);
                      setAiData(null);
                      setInputs((p) => ({ ...p, [s.id]: parseFloat(e.target.value) }));
                    }}
                    className="h-1.5 w-full cursor-ew-resize appearance-none rounded-full bg-[var(--color-bg4)] accent-[var(--color-cyan)]"
                  />
                </div>
              );
            })}

            {/* Horizon */}
            <div className="border-t border-[var(--color-border)] pt-4">
              <div className="mb-1 flex items-center justify-between">
                <label htmlFor="horizon" className="text-[12px] font-medium text-[var(--color-text)]">
                  {t("horizon")}
                </label>
                <span className="font-mono text-[11px] tabular text-[var(--color-text2)]">{horizon}d</span>
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
          </div>
        </Card>

        {/* Results */}
        <div className="space-y-4">
          {/* Stats */}
          <div className="grid grid-cols-3 gap-3">
            <StatTile
              label={locale === "es" ? "Portafolio OLS · Alpaca" : "OLS Portfolio · Alpaca"}
              value={alpacaEquity !== null
                ? `$${fmtNumber(alpacaEquity, { decimals: 2 })}`
                : botLoading ? "…" : "—"}
              hint={alpacaEquity !== null
                ? (locale === "es" ? "Saldo real en Alpaca Paper Trading" : "Real Alpaca Paper Trading balance")
                : (locale === "es" ? "Sin datos de Alpaca" : "No Alpaca data")}
            />
            <StatTile
              label={t("stat_delta")}
              value={alpacaDollarImpact !== null
                ? `${alpacaDollarImpact >= 0 ? "+" : ""}$${fmtNumber(alpacaDollarImpact, { decimals: 2 })}`
                : response ? `$${fmtNumber(response.delta, { decimals: 2 })}` : "—"}
              hint={locale === "es" ? `Impacto estimado en ${horizon} días` : `Estimated impact over ${horizon} days`}
              delta={
                portfolioDeltaPct !== null
                  ? { value: fmtPct(portfolioDeltaPct, { signed: true, decimals: 2 }), tone: portfolioDeltaPct >= 0 ? "green" : "red" }
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
            <p className="text-[11.5px] text-[var(--color-text3)]">{t("recomputing")}</p>
          )}
          {error && (
            <Card>
              <p className="text-[12.5px] text-[var(--color-red)]">{error}</p>
            </Card>
          )}

          {/* Per-ticker table */}
          {!response || response.per_ticker.length === 0 ? (
            <EmptyState title={t("empty_title")} description={t("empty_desc")} />
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
                      <th className="py-2 pr-3 text-right font-medium">{tc("predicted")}</th>
                      <th className="py-2 pr-3 text-right font-medium">{t("th_delta_return")}</th>
                      <th className="py-2 pr-3 text-left font-medium">{t("th_top_driver")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {response.per_ticker
                      .slice()
                      .sort((a, b) => Math.abs(b.predicted_return) - Math.abs(a.predicted_return))
                      .map((row) => {
                        const driver = topDriver(row.contributions);
                        const isUp = row.predicted_return >= 0;
                        return (
                          <tr key={row.ticker} className="border-b border-[var(--color-border)] last:border-0">
                            <td className="py-2.5 pr-3">
                              <span className="font-mono font-semibold">{row.ticker}</span>
                            </td>
                            <td className="py-2.5 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                              ${fmtNumber(row.last_price, { decimals: 2 })}
                            </td>
                            <td className="py-2.5 pr-3 text-right font-mono tabular">
                              ${fmtNumber(row.predicted_price, { decimals: 2 })}
                            </td>
                            <td className="py-2.5 pr-3 text-right">
                              <Badge tone={isUp ? "green" : "red"}>
                                {fmtPct(row.predicted_return, { signed: true, decimals: 2 })}
                              </Badge>
                            </td>
                            <td className="py-2.5 pr-3 text-[var(--color-text2)]">
                              {driver ? (
                                <span className="font-mono text-[11.5px]">
                                  {friendlyDriverName(driver.name)}{" "}
                                  <span className="text-[var(--color-text3)]">
                                    ({fmtPct(driver.value, { signed: true, decimals: 2 })})
                                  </span>
                                </span>
                              ) : "—"}
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              </div>

              {/* Impact summary bar */}
              {portfolioDeltaPct !== null && (
                <div className={[
                  "mt-4 rounded-none border px-4 py-3",
                  portfolioDeltaPct >= 0
                    ? "border-[var(--color-green)]/30 bg-[var(--color-green)]/8"
                    : "border-[var(--color-red)]/30 bg-[var(--color-red)]/8",
                ].join(" ")}>
                  <div className="flex items-center justify-between">
                    <span className={`text-[13px] font-medium ${portfolioDeltaPct >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                      {portfolioDeltaPct >= 0 ? "▲" : "▼"}{" "}
                      {locale === "es"
                        ? `Tu bot OLS ${portfolioDeltaPct >= 0 ? "ganaría" : "perdería"} ${fmtPct(Math.abs(portfolioDeltaPct), { decimals: 2 })} en ${horizon} días`
                        : `Your OLS bot would ${portfolioDeltaPct >= 0 ? "gain" : "lose"} ${fmtPct(Math.abs(portfolioDeltaPct), { decimals: 2 })} over ${horizon} days`}
                    </span>
                    {alpacaDollarImpact !== null && (
                      <span className={`font-mono text-[15px] font-bold ${portfolioDeltaPct >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                        {alpacaDollarImpact >= 0 ? "+" : ""}${fmtNumber(alpacaDollarImpact, { decimals: 2 })}
                      </span>
                    )}
                  </div>
                </div>
              )}
            </Card>
          )}

          {/* OLS P4 Alpaca bot impact */}
          <OlsBotImpact
            botData={botData}
            botLoading={botLoading}
            simulatedReturns={response?.per_ticker ?? []}
            horizon={horizon}
            locale={locale}
          />
        </div>
      </div>
    </div>
  );
}

function topDriver(contribs: Record<string, number>): { name: string; value: number } | null {
  const entries = Object.entries(contribs);
  if (!entries.length) return null;
  return entries.reduce(
    (acc, [name, value]) => (Math.abs(value) > Math.abs(acc.value) ? { name, value } : acc),
    { name: entries[0][0], value: entries[0][1] },
  );
}

const DRIVER_NAMES: Record<string, string> = {
  CPI_YoY_US: "Inflación",
  EUR_USD: "EUR/USD",
  Brent_Crude: "Petróleo",
  Gold_Spot: "Oro",
  Unemployment_Rate_US: "Desempleo",
  USD_CNY: "USD/CNY",
};

function friendlyDriverName(id: string): string {
  return DRIVER_NAMES[id] ?? id;
}

// ─── OLS Bot P4 Impact Component ─────────────────────────────────────────────

function OlsBotImpact({
  botData,
  botLoading,
  simulatedReturns,
  horizon,
  locale,
}: {
  botData: BotData | null;
  botLoading: boolean;
  simulatedReturns: SimulatedTicker[];
  horizon: number;
  locale: string;
}) {
  const es = locale === "es";

  if (botLoading) {
    return (
      <Card>
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          OLS MODEL BOT · P4
        </div>
        <p className="text-[12.5px] text-[var(--color-text3)]">
          {es ? "Cargando portafolio real de Alpaca…" : "Loading Alpaca portfolio…"}
        </p>
      </Card>
    );
  }

  if (!botData || botData.error || !botData.positions?.length) {
    return (
      <Card>
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          OLS MODEL BOT · P4
        </div>
        <p className="text-[12.5px] text-[var(--color-text2)]">
          {es
            ? "El bot no tiene posiciones abiertas actualmente en Alpaca."
            : "The bot has no open positions in Alpaca right now."}
        </p>
      </Card>
    );
  }

  // Build a return map from simulated data: ticker → predicted_return
  const returnMap: Record<string, number> = {};
  for (const row of simulatedReturns) {
    returnMap[row.ticker] = row.predicted_return;
  }

  // Calculate impact per position
  const rows = botData.positions.map((pos) => {
    const mv = parseFloat(pos.market_value);
    const ret = returnMap[pos.symbol] ?? 0;
    const dollarImpact = mv * ret * horizon;
    return { symbol: pos.symbol, mv, ret, dollarImpact, qty: parseFloat(pos.qty) };
  });

  const totalMv = rows.reduce((s, r) => s + r.mv, 0);
  const totalImpact = rows.reduce((s, r) => s + r.dollarImpact, 0);
  const totalImpactPct = totalMv > 0 ? totalImpact / totalMv : 0;
  const hasScenario = simulatedReturns.length > 0;

  return (
    <Card>
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <div>
          <div className="mb-0.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-cyan)]">
            OLS MODEL BOT · P4 · ALPACA PAPER
          </div>
          <h3 className="text-[14px] font-semibold">
            {es ? "Impacto en el portafolio real" : "Impact on real portfolio"}
          </h3>
          <p className="mt-0.5 text-[11.5px] text-[var(--color-text2)]">
            {es
              ? "Posiciones reales en Alpaca Paper Trading, cruzadas con el escenario del simulador."
              : "Actual Alpaca Paper Trading positions, crossed with the simulator scenario."}
          </p>
        </div>
        <div className="text-right">
          <div className="text-[10px] text-[var(--color-text3)]">
            {es ? "Valor total" : "Total value"}
          </div>
          <div className="font-mono text-[15px] font-semibold">
            ${fmtNumber(totalMv, { decimals: 2 })}
          </div>
          <div className="text-[10px] text-[var(--color-text3)]">
            {es ? "Efectivo: " : "Cash: "}
            <span className="text-[var(--color-text2)]">
              ${fmtNumber(parseFloat(botData.account.cash ?? "0"), { decimals: 2 })}
            </span>
          </div>
        </div>
      </div>

      {/* Position rows */}
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
              <th className="py-2 pr-3 font-medium">{es ? "Acción" : "Stock"}</th>
              <th className="py-2 pr-3 text-right font-medium">{es ? "Cantidad" : "Qty"}</th>
              <th className="py-2 pr-3 text-right font-medium">{es ? "Valor actual" : "Market value"}</th>
              <th className="py-2 pr-3 text-right font-medium">{es ? "Retorno simulado" : "Simulated return"}</th>
              <th className="py-2 pr-3 text-right font-medium">
                {es ? `Ganancia/Pérdida (${horizon}d)` : `P&L (${horizon}d)`}
              </th>
            </tr>
          </thead>
          <tbody>
            {rows
              .slice()
              .sort((a, b) => Math.abs(b.dollarImpact) - Math.abs(a.dollarImpact))
              .map((row) => {
                const isUp = row.dollarImpact >= 0;
                return (
                  <tr key={row.symbol} className="border-b border-[var(--color-border)] last:border-0">
                    <td className="py-2.5 pr-3 font-mono font-semibold">{row.symbol}</td>
                    <td className="py-2.5 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                      {row.qty}
                    </td>
                    <td className="py-2.5 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                      ${fmtNumber(row.mv, { decimals: 2 })}
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      {hasScenario ? (
                        <Badge tone={row.ret >= 0 ? "green" : "red"}>
                          {fmtPct(row.ret, { signed: true, decimals: 2 })}
                        </Badge>
                      ) : (
                        <span className="text-[var(--color-text3)]">—</span>
                      )}
                    </td>
                    <td className="py-2.5 pr-3 text-right">
                      {hasScenario ? (
                        <span className={`font-mono font-medium ${isUp ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                          {isUp ? "+" : ""}${fmtNumber(row.dollarImpact, { decimals: 2 })}
                        </span>
                      ) : (
                        <span className="text-[var(--color-text3)]">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {/* Total impact banner */}
      {hasScenario && (
        <div className={[
          "mt-4 rounded-none border px-4 py-3",
          totalImpact >= 0
            ? "border-[var(--color-green)]/30 bg-[var(--color-green)]/8"
            : "border-[var(--color-red)]/30 bg-[var(--color-red)]/8",
        ].join(" ")}>
          <div className="flex items-center justify-between">
            <span className={`text-[13px] font-medium ${totalImpact >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
              {totalImpact >= 0 ? "▲" : "▼"}{" "}
              {es
                ? `El bot ${totalImpact >= 0 ? "ganaría" : "perdería"} ${fmtPct(Math.abs(totalImpactPct), { decimals: 2 })} en ${horizon} días`
                : `Bot would ${totalImpact >= 0 ? "gain" : "lose"} ${fmtPct(Math.abs(totalImpactPct), { decimals: 2 })} over ${horizon} days`}
            </span>
            <span className={`font-mono text-[14px] font-bold ${totalImpact >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
              {totalImpact >= 0 ? "+" : ""}${fmtNumber(totalImpact, { decimals: 2 })}
            </span>
          </div>
        </div>
      )}
    </Card>
  );
}
