/**
 * Typed API client for the portfolio-engine backend.
 *
 * Reads the base URL from NEXT_PUBLIC_API_URL. All endpoints return
 * already-decoded JSON, throwing on non-2xx.
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
  last_observed_on: string | null; // ISO date
  last_value: number | null;
};

export type Observation = {
  observed_on: string; // ISO date
  value: number;
  served_by_provider: string | null;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_URL}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store", // server-rendered Next.js — don't cache
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(
      res.status,
      path,
      `${res.status} ${res.statusText}: ${body.slice(0, 200)}`,
    );
  }
  return res.json() as Promise<T>;
}

// ─── Endpoints ───────────────────────────────────────────────────────────────

export const api = {
  health: () => request<Health>("/health"),

  listVariables: (kind?: Variable["kind"]) =>
    request<Variable[]>(`/variables${kind ? `?kind=${kind}` : ""}`),

  getVariable: (id: string) => request<Variable>(`/variables/${encodeURIComponent(id)}`),

  getObservations: (
    id: string,
    opts?: { from?: string; to?: string; limit?: number },
  ) => {
    const qs = new URLSearchParams();
    if (opts?.from) qs.set("from", opts.from);
    if (opts?.to) qs.set("to", opts.to);
    if (opts?.limit) qs.set("limit", String(opts.limit));
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<Observation[]>(`/observations/${encodeURIComponent(id)}${suffix}`);
  },
};

export { ApiError };
