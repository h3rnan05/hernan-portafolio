/**
 * Typed API client for the portfolio-engine backend.
 *
 * Mirrors the FastAPI schemas one-for-one. Never reads from cache so SSR
 * + 60s client polling both see fresh data.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ─── Types ───────────────────────────────────────────────────────────────────

export type Health = {
  ok: boolean;
  version: string;
  db_reachable: boolean;
};

export type ProviderConfig = {
  name: string;
  symbol: string;
};

export type Variable = {
  id: string;
  display_name: string;
  kind: "predictor" | "stock" | "portfolio";
  category: string | null;
  unit: string | null;
  providers: ProviderConfig[];
  active: boolean;
  last_observed_on: string | null;
  last_value: number | null;
};

export type Observation = {
  observed_on: string;
  value: number;
  served_by_provider: string | null;
};

export type ModelSummary = {
  ticker: string;
  fitted_at: string;
  training_start: string;
  training_end: string;
  n_obs: number;
  predictor_ids: string[];
  r2: number;
  r2_adj: number;
  durbin_watson: number;
  breusch_pagan_p: number;
  max_vif: number;
  status: "PASS" | "REVIEW" | "FAIL";
  is_active: boolean;
};

export type ModelDetail = ModelSummary & {
  intercept: number;
  coefficients: Record<string, number>;
  equation: string;
};

export type PredictionPoint = {
  predicted_for: string;
  predicted_at: string;
  predicted_return: number | null;
  predicted_price: number;
  actual_price: number | null;
  abs_error_pct: number | null;
};

export type TickerPredictions = {
  ticker: string;
  points: PredictionPoint[];
  mape: number | null;
};

export type PortfolioPredictionPoint = {
  predicted_for: string;
  predicted_value: number;
  actual_value: number | null;
  error_pct: number | null;
};

export type PortfolioPredictions = {
  portfolio_id: string;
  points: PortfolioPredictionPoint[];
  mape: number | null;
};

export type Portfolio = {
  id: string;
  name: string;
  description: string | null;
  weights: Record<string, number>;
  generated_at: string;
  mape_30d: number | null;
};

export type Position = {
  snapshot_at: string;
  account_id: string;
  ticker: string;
  quantity: number;
  avg_price: number;
  last_price: number;
  market_value: number;
  open_pnl: number;
  open_pnl_pct: number;
};

export type SimulatedTicker = {
  ticker: string;
  predicted_return: number;
  predicted_price: number;
  last_price: number;
  contributions: Record<string, number>;
};

export type SimulateResponse = {
  inputs: Record<string, number>;
  horizon_days: number;
  per_ticker: SimulatedTicker[];
  portfolio_value: number;
  portfolio_value_baseline: number;
  delta: number;
};

export type RefitOutcome = {
  ticker: string;
  status: "PASS" | "REVIEW" | "SKIPPED";
  r2: number | null;
  n_obs: number | null;
  predictor_ids: string[];
  error: string | null;
};

export type Holding = {
  id: string;
  ticker: string;
  quantity: number;
  avg_price: number;
  notes: string | null;
  added_at: string;
  updated_at: string;
  last_price: number | null;
  market_value: number | null;
  cost_basis: number | null;
  open_pnl: number | null;
  open_pnl_pct: number | null;
};

export type HoldingsSummary = {
  n: number;
  cost_basis: number;
  market_value: number;
  open_pnl: number;
  open_pnl_pct: number | null;
};

export type HoldingsResponse = {
  holdings: Holding[];
  summary: HoldingsSummary;
};

export type HoldingInput = {
  ticker: string;
  quantity: number;
  avg_price: number;
  notes?: string | null;
};

export type PortfolioSnapshot = {
  portfolio_id: string;
  weights: Record<string, number>;
  mape_30d: number | null;
  snapshotted_at: string;
};

export type HoldingProjection = {
  ticker: string;
  quantity: number;
  avg_price: number;
  last_price: number;
  market_value: number;
  open_pnl: number;
  open_pnl_pct: number;
  predicted_price: number | null;
  predicted_market_value: number | null;
  predicted_pnl_delta: number | null;
};

export type HoldingsProjection = {
  rows: HoldingProjection[];
  current_market_value: number;
  projected_market_value: number;
  projected_delta: number;
  projected_delta_pct: number | null;
};

// ─── Fetch wrapper ───────────────────────────────────────────────────────────

class ApiError extends Error {
  constructor(
    public status: number,
    public path: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

type RequestOpts = RequestInit & { adminToken?: string };

async function request<T>(path: string, init?: RequestOpts): Promise<T> {
  const url = `${API_URL}${path}`;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (init?.adminToken) {
    headers["Authorization"] = `Bearer ${init.adminToken}`;
  }

  const res = await fetch(url, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(
      res.status,
      path,
      `${res.status} ${res.statusText}: ${body.slice(0, 200)}`,
    );
  }
  // 204 / empty body — return null typed as T (callers know when this applies)
  if (res.status === 204) return null as unknown as T;
  return res.json() as Promise<T>;
}

// ─── Endpoints ───────────────────────────────────────────────────────────────

export const api = {
  health: () => request<Health>("/health"),

  listVariables: (kind?: Variable["kind"]) =>
    request<Variable[]>(`/variables${kind ? `?kind=${kind}` : ""}`),

  getVariable: (id: string) =>
    request<Variable>(`/variables/${encodeURIComponent(id)}`),

  getObservations: (
    id: string,
    opts?: { from?: string; to?: string; limit?: number },
  ) => {
    const qs = new URLSearchParams();
    if (opts?.from) qs.set("from", opts.from);
    if (opts?.to) qs.set("to", opts.to);
    if (opts?.limit) qs.set("limit", String(opts.limit));
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<Observation[]>(
      `/observations/${encodeURIComponent(id)}${suffix}`,
    );
  },

  listModels: (onlyActive = true) =>
    request<ModelSummary[]>(`/models?only_active=${onlyActive}`),

  getModel: (ticker: string) =>
    request<ModelDetail>(`/models/${encodeURIComponent(ticker)}`),

  refitAll: (adminToken: string, body?: { lookback_days?: number; k_per_stock?: number; lag_days?: number }) =>
    request<RefitOutcome[]>("/models/refit_all", {
      method: "POST",
      adminToken,
      body: JSON.stringify(body ?? {}),
    }),

  refitTicker: (ticker: string, adminToken: string) =>
    request<RefitOutcome>(`/models/${encodeURIComponent(ticker)}/refit`, {
      method: "POST",
      adminToken,
    }),

  getPredictions: (ticker: string, days = 30) =>
    request<TickerPredictions>(
      `/predictions/${encodeURIComponent(ticker)}?days=${days}`,
    ),

  getPortfolioPredictions: (portfolioId: string, days = 30) =>
    request<PortfolioPredictions>(
      `/predictions/portfolio/${encodeURIComponent(portfolioId)}?days=${days}`,
    ),

  simulate: (inputs: Record<string, number>, horizonDays = 7) =>
    request<SimulateResponse>("/predictions/simulate", {
      method: "POST",
      body: JSON.stringify({ inputs, horizon_days: horizonDays }),
    }),

  listPortfolios: () => request<Portfolio[]>("/portfolios"),

  getPortfolio: (id: string) =>
    request<Portfolio>(`/portfolios/${encodeURIComponent(id)}`),

  getPortfolioHistory: (id: string, days = 90) =>
    request<PortfolioSnapshot[]>(
      `/portfolios/${encodeURIComponent(id)}/history?days=${days}`,
    ),

  getHoldingsProjection: () =>
    request<HoldingsProjection>("/holdings/projection"),

  listPositions: () => request<Position[]>("/positions/live"),

  syncPositions: (adminToken: string) =>
    request<{ snapshot_count: number; snapshot_at: string }>(
      "/positions/sync",
      { method: "POST", adminToken },
    ),

  listHoldings: () => request<HoldingsResponse>("/holdings"),

  createHolding: (h: HoldingInput, adminToken: string) =>
    request<Holding>("/holdings", {
      method: "POST",
      adminToken,
      body: JSON.stringify(h),
    }),

  updateHolding: (ticker: string, patch: Partial<HoldingInput>, adminToken: string) =>
    request<Holding>(`/holdings/${encodeURIComponent(ticker)}`, {
      method: "PATCH",
      adminToken,
      body: JSON.stringify(patch),
    }),

  deleteHolding: (ticker: string, adminToken: string) =>
    request<null>(`/holdings/${encodeURIComponent(ticker)}`, {
      method: "DELETE",
      adminToken,
    }).catch((e: unknown) => {
      // DELETE returns 204 No Content — fetch tries to parse empty body as JSON
      if (e instanceof ApiError) throw e;
      return null;
    }),

  bulkUpsertHoldings: (rows: HoldingInput[], adminToken: string) =>
    request<HoldingsResponse>("/holdings/bulk", {
      method: "POST",
      adminToken,
      body: JSON.stringify(rows),
    }),
};

export { ApiError };
