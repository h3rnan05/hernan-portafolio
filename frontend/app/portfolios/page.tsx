/**
 * Portfolios — the 5 risk profiles, shown as horizontal tabs (HER task 7).
 *
 * Each tab (P1 Conservative → P5 Aggressive) renders the full detail for that
 * profile: current weights, weight bar, and the historical weight-evolution
 * chart. Histories are fetched server-side in parallel and handed to the
 * client tab component.
 */

import { getTranslations } from "next-intl/server";

import { GrowthChart } from "@/components/growth-chart";
import { Card, EmptyState } from "@/components/primitives";
import {
  PortfolioTabs,
  type ProfileDetail,
} from "@/components/portfolio-tabs";
import { api, ApiError, type Portfolio, type PortfolioSnapshot } from "@/lib/api";

export const revalidate = 3600;

// Canonical display order for the five profiles.
const PROFILE_ORDER = [
  "P1_CONSERVATIVE",
  "P2_MOD_CONSERVATIVE",
  "P3_BALANCED",
  "P4_MOD_AGGRESSIVE",
  "P5_AGGRESSIVE",
];

export default async function PortfoliosPage({
  searchParams,
}: {
  searchParams: Promise<{ profile?: string }>;
}) {
  const { profile: initialId = "P4_MOD_AGGRESSIVE" } = await searchParams;
  const t = await getTranslations("portfolios");

  let portfolios: Portfolio[] = [];
  let error: string | null = null;
  try {
    portfolios = await api.listPortfolios();
  } catch (e) {
    error = e instanceof ApiError ? e.message : String(e);
  }

  portfolios = [...portfolios].sort(
    (a, b) =>
      (PROFILE_ORDER.indexOf(a.id) + 1 || 99) -
      (PROFILE_ORDER.indexOf(b.id) + 1 || 99),
  );

  // Fetch each profile's history in parallel and pre-shape chart rows.
  const details: ProfileDetail[] = await Promise.all(
    portfolios.map(async (portfolio) => {
      const history: PortfolioSnapshot[] = await api
        .getPortfolioHistory(portfolio.id, 90)
        .catch(() => []);
      const tickers = Object.entries(portfolio.weights)
        .sort((a, b) => b[1] - a[1])
        .map(([t]) => t);
      const chartData = history.map((snap) => {
        const row: Record<string, string | number | null> = {
          date: snap.snapshotted_at.slice(0, 10),
        };
        for (const t of tickers) row[t] = (snap.weights[t] ?? 0) * 100;
        return row;
      });
      return {
        portfolio,
        tickers,
        chartData,
        firstSnapshot: history[0]?.snapshotted_at ?? null,
        lastSnapshot: history[history.length - 1]?.snapshotted_at ?? null,
      };
    }),
  );

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

      {/* Centerpiece: growth of $10,000 per profile vs the market */}
      <div className="mb-8">
        <GrowthChart />
      </div>

      {error ? (
        <Card>
          <div className="text-[13px] text-[var(--color-red)]">{error}</div>
        </Card>
      ) : details.length === 0 ? (
        <EmptyState title={t("empty_title")} description={t("empty_desc")} />
      ) : (
        <PortfolioTabs profiles={details} initialId={initialId} />
      )}
    </div>
  );
}
