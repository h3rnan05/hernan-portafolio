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

export type VariableKind = "predictor" | "stock" | "etf" | "index" | "portfolio";

export type Variable = {
  id: string;
  display_name: string;
  kind: VariableKind;
  category: string | null;
  unit: string | null;
  providers: ProviderConfig[];
  active: boolean;
  is_target: boolean;
  last_observed_on: string | null;
  last_value: number | null;
};

export type VariableCreate = {
  id: string;
  display_name: string;
  kind: VariableKind;
  category?: string | null;
  unit?: string | null;
  providers?: ProviderConfig[];
  is_target?: boolean;
};

export type VariablePatch = {
  display_name?: string;
  category?: string | null;
  unit?: string | null;
  providers?: ProviderConfig[];
  active?: boolean;
  is_target?: boolean;
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
  resid_std?: number | null;
  estimator?: string;
  alpha?: number | null;
  status: "PASS" | "REVIEW" | "FAIL";
  is_active: boolean;
};

export type ModelDetail = ModelSummary & {
  intercept: number;
  coefficients: Record<string, number>;
  equation: string;
};

// ─── Walk-forward validation (HER-13) ────────────────────────────────────────

export type ValidationCurvePoint = {
  on: string;
  strategy: number;
  buy_hold: number;
};

export type ValidationResult = {
  ticker: string;
  estimator: string;
  train_window: number;
  step: number;
  n_windows: number;
  n_predictions: number;
  hit_rate: number | null;
  up_day_base_rate: number | null;
  edge_vs_base: number | null;
  hit_rate_pvalue: number | null;
  significant: boolean;
  rmse: number | null;
  mae: number | null;
  sharpe_strategy: number | null;
  sharpe_buy_hold: number | null;
  total_return_strategy: number | null;
  total_return_buy_hold: number | null;
  cost_bps: number;
  curve: ValidationCurvePoint[];
  note: string | null;
};

// ─── Forecast (HER-17) ───────────────────────────────────────────────────────

export type ForecastPoint = {
  on: string;
  day: number;
  central: number;
  lower: number;
  upper: number;
};

export type ForecastResult = {
  ticker: string;
  as_of: string;
  last_price: number;
  horizon: number;
  confidence: number;
  direction: "up" | "down";
  direction_symbol: string;
  expected_return_1d: number;
  sigma_daily: number;
  status: "PASS" | "REVIEW" | "FAIL";
  estimator: string;
  sigma_source: "model_resid" | "realized_vol";
  points: ForecastPoint[];
  note: string | null;
};

// ─── AI assistant (chat) ─────────────────────────────────────────────────────

export type ChatRole = "user" | "assistant";
export type ChatMessage = { role: ChatRole; content: string };

export type ChatEvent =
  | { type: "text"; text: string }
  | { type: "tool"; name: string }
  | { type: "done" }
  | { type: "error"; message: string };

/**
 * Stream an assistant turn from the backend `/chat` SSE endpoint. Yields parsed
 * events as they arrive (text deltas, tool-call notices, done/error).
 */
export async function* chatStream(
  messages: ChatMessage[],
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch(`${API_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages }),
    signal,
  });
  if (!res.ok || !res.body) {
    yield { type: "error", message: `HTTP ${res.status}` };
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split("\n\n");
    buf = chunks.pop() ?? "";
    for (const chunk of chunks) {
      const line = chunk.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload) continue;
      try {
        yield JSON.parse(payload) as ChatEvent;
      } catch {
        // ignore malformed SSE frame
      }
    }
  }
}

export type ObservationAudit = {
  variable_id: string;
  observed_on: string;
  value: number;
  served_by_provider: string | null;
};

export type ModelAudit = {
  model_id: string;
  ticker: string;
  fitted_at: string;
  training_start: string;
  training_end: string;
  n_obs: number;
  predictor_ids: string[];
  intercept: number;
  coefficients: Record<string, number>;
  r2: number;
  r2_adj: number;
  durbin_watson: number;
  breusch_pagan_p: number;
  max_vif: number;
  status: "PASS" | "REVIEW" | "FAIL";
  is_active: boolean;
  observations: ObservationAudit[];
  observation_count: number;
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
  /** Whether this portfolio is published publicly. Optional until the backend
   *  `is_public` column ships (see proposed migration 0008); absent → treated
   *  as not public. */
  is_public?: boolean;
};

// ─── Growth of $10,000 (Portfolios page centerpiece) ─────────────────────────

export type GrowthPoint = {
  date: string;
  value: number;
};

export type GrowthSeries = {
  profile: string; // "P1".."P5" | "BENCH"
  label: string;
  points: GrowthPoint[];
};

export type GrowthResponse = {
  window: number;
  series: GrowthSeries[];
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

export type AccountHoldingIn = {
  ticker: string;
  quantity: number;
  avg_price: number;
  notes?: string | null;
};

export type AccountHoldingOut = {
  id: string;
  ticker: string;
  quantity: number;
  avg_price: number;
  notes: string | null;
  added_at: string;
  last_price: number | null;
  market_value: number | null;
  open_pnl: number | null;
  open_pnl_pct: number | null;
};

export type ProfileMatch = {
  profile_id: string;
  profile_name: string;
  distance: number;
};

export type UserAccount = {
  id: string;
  name: string;
  description: string | null;
  assigned_profile_id: string | null;
  profile_assigned_at: string | null;
  created_at: string;
  holdings: AccountHoldingOut[];
  profile_match: ProfileMatch | null;
  total_market_value: number | null;
};

export type UserAccountCreate = {
  name: string;
  description?: string | null;
  holdings?: AccountHoldingIn[];
};

export type UserAccountPatch = {
  name?: string;
  description?: string | null;
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

type RequestOpts = RequestInit & {
  adminToken?: string;
  /**
   * When set on a GET, the response is cached in Next's Data Cache and
   * revalidated after this many seconds (shared across requests/users → the
   * backend is hit at most once per window). Omit for live, uncached fetches
   * (the default `no-store`). Ignored on the client, where the browser fetches
   * normally and lib/api's SWR layer handles staleness.
   */
  revalidate?: number;
};

async function request<T>(path: string, init?: RequestOpts): Promise<T> {
  const url = `${API_URL}${path}`;
  const { adminToken, revalidate, ...fetchInit } = init ?? {};
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (adminToken) {
    headers["Authorization"] = `Bearer ${adminToken}`;
  }

  const method = (fetchInit.method ?? "GET").toUpperCase();
  // GET + revalidate → ISR/Data-Cache; everything else stays live (no-store).
  const cacheInit: RequestInit & { next?: { revalidate: number } } =
    revalidate !== undefined && method === "GET"
      ? { next: { revalidate } }
      : { cache: "no-store" };

  const res = await fetch(url, { ...fetchInit, headers, ...cacheInit });
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
    request<Variable[]>(`/variables${kind ? `?kind=${kind}` : ""}`, {
      revalidate: 3600,
    }),

  getVariable: (id: string) =>
    request<Variable>(`/variables/${encodeURIComponent(id)}`, {
      revalidate: 3600,
    }),

  createVariable: (body: VariableCreate, adminToken: string) =>
    request<Variable>("/variables", {
      method: "POST",
      adminToken,
      body: JSON.stringify(body),
    }),

  patchVariable: (id: string, patch: VariablePatch, adminToken: string) =>
    request<Variable>(`/variables/${encodeURIComponent(id)}`, {
      method: "PATCH",
      adminToken,
      body: JSON.stringify(patch),
    }),

  deleteVariable: (id: string, adminToken: string) =>
    request<null>(`/variables/${encodeURIComponent(id)}`, {
      method: "DELETE",
      adminToken,
    }).catch((e: unknown) => {
      if (e instanceof ApiError) throw e;
      return null;
    }),

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
      { revalidate: 3600 },
    );
  },

  listModels: (onlyActive = true) =>
    request<ModelSummary[]>(`/models?only_active=${onlyActive}`, {
      revalidate: 3600,
    }),

  getModel: (ticker: string) =>
    request<ModelDetail>(`/models/${encodeURIComponent(ticker)}`, {
      revalidate: 3600,
    }),

  getModelAudit: (ticker: string) =>
    request<ModelAudit>(`/models/${encodeURIComponent(ticker)}/audit`),

  getValidation: (
    ticker: string,
    opts?: { train_window?: number; step?: number; estimator?: string },
  ) => {
    const qs = new URLSearchParams();
    if (opts?.train_window) qs.set("train_window", String(opts.train_window));
    if (opts?.step) qs.set("step", String(opts.step));
    if (opts?.estimator) qs.set("estimator", opts.estimator);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ValidationResult>(
      `/models/${encodeURIComponent(ticker)}/validation${suffix}`,
    );
  },

  getForecast: (ticker: string, horizon = 5) =>
    request<ForecastResult>(
      `/models/${encodeURIComponent(ticker)}/forecast?horizon=${horizon}`,
    ),

  chatEnabled: () => request<{ enabled: boolean }>("/chat/health"),

  modelAuditUrl: (ticker: string) =>
    `${API_URL}/models/${encodeURIComponent(ticker)}/audit`,

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
      { revalidate: 3600 },
    ),

  getPortfolioPredictions: (portfolioId: string, days = 30) =>
    request<PortfolioPredictions>(
      `/predictions/portfolio/${encodeURIComponent(portfolioId)}?days=${days}`,
      { revalidate: 3600 },
    ),

  simulate: (inputs: Record<string, number>, horizonDays = 7) =>
    request<SimulateResponse>("/predictions/simulate", {
      method: "POST",
      body: JSON.stringify({ inputs, horizon_days: horizonDays }),
    }),

  listPortfolios: () =>
    request<Portfolio[]>("/portfolios", { revalidate: 3600 }),

  getPortfolioGrowth: (window: 30 | 90 | 360 = 90) =>
    request<GrowthResponse>(`/portfolios/growth?window=${window}`, {
      revalidate: 3600,
    }),

  getPortfolio: (id: string) =>
    request<Portfolio>(`/portfolios/${encodeURIComponent(id)}`, {
      revalidate: 3600,
    }),

  getPortfolioHistory: (id: string, days = 90) =>
    request<PortfolioSnapshot[]>(
      `/portfolios/${encodeURIComponent(id)}/history?days=${days}`,
      { revalidate: 3600 },
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

  listAccounts: () => request<UserAccount[]>("/accounts"),

  getAccount: (id: string) =>
    request<UserAccount>(`/accounts/${encodeURIComponent(id)}`),

  createAccount: (body: UserAccountCreate) =>
    request<UserAccount>("/accounts", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  patchAccount: (id: string, patch: UserAccountPatch) =>
    request<UserAccount>(`/accounts/${encodeURIComponent(id)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  deleteAccount: (id: string) =>
    request<null>(`/accounts/${encodeURIComponent(id)}`, {
      method: "DELETE",
    }).catch((e: unknown) => {
      if (e instanceof ApiError) throw e;
      return null;
    }),

  replaceAccountHoldings: (id: string, holdings: AccountHoldingIn[]) =>
    request<UserAccount>(`/accounts/${encodeURIComponent(id)}/holdings`, {
      method: "PUT",
      body: JSON.stringify(holdings),
    }),
};

export { ApiError };
