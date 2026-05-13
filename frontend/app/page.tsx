/**
 * Phase 0 acceptance page.
 *
 * Hits /health and /variables on the backend and shows the results.
 * If both succeed, you have a working end-to-end stack. The HTML mockup
 * gets ported to this app in Phase 6 (per the brief).
 */

import { api, ApiError } from "@/lib/api";

type FetchResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string };

async function safeFetch<T>(fn: () => Promise<T>): Promise<FetchResult<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    if (e instanceof ApiError) {
      return { ok: false, error: e.message };
    }
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

export default async function Home() {
  const [healthRes, varsRes] = await Promise.all([
    safeFetch(api.health),
    safeFetch(() => api.listVariables()),
  ]);

  return (
    <main className="mx-auto max-w-4xl p-8 font-mono">
      <header className="mb-12 border-b border-[var(--color-border)] pb-6">
        <h1 className="text-2xl font-bold tracking-tight">
          Portfolio Prediction Engine
        </h1>
        <p className="mt-2 text-xs uppercase tracking-widest text-[var(--color-text3)]">
          Phase 0 — backend wiring check
        </p>
      </header>

      {/* Backend health */}
      <section className="mb-10">
        <h2 className="mb-3 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
          Backend health
        </h2>
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-bg2)] p-4">
          {healthRes.ok ? (
            <div className="flex items-baseline gap-3">
              <span className="text-lg font-bold text-[var(--color-green)]">
                ●
              </span>
              <span className="text-sm">
                Healthy · v{healthRes.data.version} · DB{" "}
                {healthRes.data.db_reachable ? "reachable" : "unreachable"}
              </span>
            </div>
          ) : (
            <div className="flex items-baseline gap-3">
              <span className="text-lg font-bold text-[var(--color-red)]">
                ●
              </span>
              <div className="text-sm">
                <div>Cannot reach backend.</div>
                <div className="mt-1 text-xs text-[var(--color-text3)]">
                  {healthRes.error}
                </div>
                <div className="mt-2 text-xs text-[var(--color-text3)]">
                  Check that the backend is running:{" "}
                  <code className="text-[var(--color-cyan)]">
                    cd backend && uv run uvicorn app.main:app --reload
                  </code>
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      {/* Variables registry */}
      <section className="mb-10">
        <h2 className="mb-3 flex items-center justify-between text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
          <span>Variables registry</span>
          {varsRes.ok && (
            <span className="rounded-md bg-[var(--color-border2)] px-2 py-0.5 text-[var(--color-text2)]">
              {varsRes.data.length} entries
            </span>
          )}
        </h2>
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-bg2)]">
          {varsRes.ok ? (
            varsRes.data.length === 0 ? (
              <div className="p-4 text-sm text-[var(--color-text3)]">
                No variables yet. Run{" "}
                <code className="text-[var(--color-cyan)]">
                  uv run python scripts/seed_variables.py
                </code>
              </div>
            ) : (
              <table className="w-full text-xs">
                <thead className="border-b border-[var(--color-border)] bg-[var(--color-bg3)]">
                  <tr className="text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                    <th className="px-4 py-2.5 font-medium">ID</th>
                    <th className="px-4 py-2.5 font-medium">Kind</th>
                    <th className="px-4 py-2.5 font-medium">Category</th>
                    <th className="px-4 py-2.5 font-medium">Last seen</th>
                    <th className="px-4 py-2.5 font-medium">Last value</th>
                  </tr>
                </thead>
                <tbody>
                  {varsRes.data.slice(0, 20).map((v) => (
                    <tr
                      key={v.id}
                      className="border-b border-[var(--color-border)] last:border-0"
                    >
                      <td className="px-4 py-2 font-medium">{v.id}</td>
                      <td className="px-4 py-2 text-[var(--color-text2)]">
                        {v.kind}
                      </td>
                      <td className="px-4 py-2 text-[var(--color-text2)]">
                        {v.category ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-[var(--color-text2)]">
                        {v.last_observed_on ?? (
                          <span className="text-[var(--color-amber)]">
                            no data yet
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right text-[var(--color-text2)]">
                        {v.last_value !== null
                          ? v.last_value.toFixed(4)
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          ) : (
            <div className="p-4 text-sm">
              <div>Failed to load variables.</div>
              <div className="mt-1 text-xs text-[var(--color-text3)]">
                {varsRes.error}
              </div>
            </div>
          )}
        </div>
        {varsRes.ok && varsRes.data.length > 20 && (
          <p className="mt-2 text-[11px] text-[var(--color-text3)]">
            Showing 20 of {varsRes.data.length}. Full list at{" "}
            <code className="text-[var(--color-cyan)]">
              {process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}
              /variables
            </code>
          </p>
        )}
      </section>

      <footer className="mt-16 border-t border-[var(--color-border)] pt-4 text-[10px] text-[var(--color-text3)]">
        Phase 0 scaffold · See{" "}
        <code className="text-[var(--color-text2)]">README.md</code> for the
        agent runbook
      </footer>
    </main>
  );
}
