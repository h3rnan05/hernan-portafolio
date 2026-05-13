/**
 * Portfolios — the 5 risk profiles, side-by-side.
 *
 * Each profile shows its weights as a horizontal stacked bar, its MAPE,
 * and a sparkline of recent predicted portfolio value.
 */

import Link from "next/link";

import {
  Badge,
  Card,
  EmptyState,
  fmtNumber,
  fmtPct,
  SectionHeader,
} from "@/components/primitives";
import { api, ApiError, type Portfolio } from "@/lib/api";

export const dynamic = "force-dynamic";

const PROFILE_TONES: Record<string, "blue" | "cyan" | "green" | "amber" | "red"> = {
  P1_CONSERVATIVE: "blue",
  P2_MOD_CONSERVATIVE: "cyan",
  P3_BALANCED: "green",
  P4_MOD_AGGRESSIVE: "amber",
  P5_AGGRESSIVE: "red",
};

export default async function PortfoliosPage() {
  let portfolios: Portfolio[] = [];
  let error: string | null = null;
  try {
    portfolios = await api.listPortfolios();
  } catch (e) {
    error = e instanceof ApiError ? e.message : String(e);
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8">
        <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          Portfolios
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Five risk profiles
        </h1>
        <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
          Deterministic weight builders from the active models&rsquo; quality
          and predicted Sharpe. P1 weights inversely to volatility; P5 leans
          into upside.
        </p>
      </div>

      {error ? (
        <Card>
          <div className="text-[13px] text-[var(--color-red)]">{error}</div>
        </Card>
      ) : portfolios.length === 0 ? (
        <EmptyState
          title="Portfolios not built yet"
          description="The portfolio table is populated by the daily prediction job after at least one model is active."
        />
      ) : (
        <div className="space-y-3">
          {portfolios.map((p) => (
            <PortfolioRow key={p.id} portfolio={p} />
          ))}
        </div>
      )}
    </div>
  );
}

function PortfolioRow({ portfolio }: { portfolio: Portfolio }) {
  const tone = PROFILE_TONES[portfolio.id] ?? "blue";
  const sorted = Object.entries(portfolio.weights).sort(
    (a, b) => b[1] - a[1],
  );
  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Link
              href={`/portfolios/${portfolio.id}`}
              className="text-[15px] font-semibold hover:text-[var(--color-cyan)]"
            >
              {portfolio.name}
            </Link>
            <Badge tone={tone}>{portfolio.id.split("_")[0]}</Badge>
          </div>
          {portfolio.description && (
            <p className="mt-1 text-[12.5px] text-[var(--color-text2)]">
              {portfolio.description}
            </p>
          )}
        </div>
        <div className="flex items-center gap-4">
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
              MAPE 30d
            </div>
            <div className="mt-0.5 font-mono tabular text-base font-semibold">
              {portfolio.mape_30d !== null
                ? fmtPct(portfolio.mape_30d)
                : "—"}
            </div>
          </div>
          <Link
            href={`/portfolios/${portfolio.id}`}
            className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1.5 text-[11px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
          >
            History →
          </Link>
        </div>
      </div>

      {/* Weight bar */}
      <div className="mt-4">
        <div className="flex h-3 overflow-hidden rounded-[6px] bg-[var(--color-bg3)]">
          {sorted.map(([ticker, w], i) => (
            <div
              key={ticker}
              title={`${ticker}: ${fmtPct(w, { decimals: 1 })}`}
              style={{ width: `${(w * 100).toFixed(2)}%` }}
              className={
                "h-full transition-colors " +
                weightBarColor(i, sorted.length)
              }
            />
          ))}
        </div>
        <div className="mt-3 grid grid-cols-3 gap-x-4 gap-y-1 text-[11.5px] md:grid-cols-5">
          {sorted.map(([ticker, w], i) => (
            <div
              key={ticker}
              className="flex items-center justify-between gap-2"
            >
              <span className="inline-flex items-center gap-1.5 font-mono text-[var(--color-text2)]">
                <span
                  className={
                    "inline-block size-1.5 rounded-full " +
                    weightDotColor(i, sorted.length)
                  }
                />
                {ticker}
              </span>
              <span className="font-mono tabular text-[var(--color-text)]">
                {fmtNumber(w * 100, { decimals: 1 })}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function weightBarColor(i: number, n: number): string {
  // Cycle through tonal palette
  const colors = [
    "bg-[var(--color-green)]",
    "bg-[var(--color-cyan)]",
    "bg-[var(--color-blue)]",
    "bg-[var(--color-violet)]",
    "bg-[var(--color-amber)]",
    "bg-[var(--color-red)]",
    "bg-[var(--color-green-dim)]",
    "bg-[var(--color-text3)]",
    "bg-[var(--color-text4)]",
  ];
  return colors[i % colors.length];
}

function weightDotColor(i: number, n: number): string {
  return weightBarColor(i, n);
}
