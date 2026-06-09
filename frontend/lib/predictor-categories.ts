/**
 * Predictor category regrouping (HER task 4).
 *
 * The raw `category` field on each predictor variable is very granular — many
 * categories carry a single member ("Brazil Index", "Swiss Stock", "Shipping").
 * For the Predictors coverage panel we roll those up into a small set of
 * balanced buckets (3–5 members each) so the grid reads cleanly.
 *
 * The mapping is keyed by variable id (stable) with a category-based fallback so
 * any predictor we don't explicitly know still lands in a sensible "Other …"
 * bucket rather than spawning a one-item group.
 */

import type { Variable } from "@/lib/api";

export type PredictorGroup = {
  label: string;
  items: Variable[];
};

// Ordered so the rendered grid has a deterministic, sensible flow.
// Buckets are kept at 3–5 members each so the grid reads evenly.
const BUCKET_ORDER = [
  "US Macro — Prices & Labor",
  "US Macro — Growth & Activity",
  "Americas Indices",
  "Europe Indices",
  "Asia-Pacific Indices",
  "FX Rates",
  "Commodities",
  "International Stocks",
] as const;

// Explicit id → bucket assignments. Keeps each bucket at 3–5 members.
const ID_TO_BUCKET: Record<string, string> = {
  // US Macro — Prices & Labor
  CPI_YoY_US: "US Macro — Prices & Labor",
  PPI_YoY_US: "US Macro — Prices & Labor",
  Unemployment_Rate_US: "US Macro — Prices & Labor",
  Consumer_Confidence: "US Macro — Prices & Labor",
  // US Macro — Growth & Activity
  Industrial_Prod_Index: "US Macro — Growth & Activity",
  Building_Permits_US: "US Macro — Growth & Activity",
  ISM_Manufacturing_PMI: "US Macro — Growth & Activity",
  Citi_Econ_Surprise_US: "US Macro — Growth & Activity",
  Conference_Board_LEI: "US Macro — Growth & Activity",
  // Europe Indices
  FTSE_100: "Europe Indices",
  DAX: "Europe Indices",
  CAC_40: "Europe Indices",
  // Americas Indices (US broad-market proxies + LatAm)
  NYSE_Composite: "Americas Indices",
  NASDAQ_Composite: "Americas Indices",
  IPC_Mexico: "Americas Indices",
  Bovespa: "Americas Indices",
  // Asia-Pacific Indices
  Nikkei_225: "Asia-Pacific Indices",
  Hang_Seng: "Asia-Pacific Indices",
  KOSPI: "Asia-Pacific Indices",
  BSE_Sensex: "Asia-Pacific Indices",
  // FX Rates
  EUR_USD: "FX Rates",
  EUR_JPY: "FX Rates",
  USD_MXN: "FX Rates",
  USD_CNY: "FX Rates",
  // Commodities (folds "Shipping" → Baltic_Dry_Index)
  Gold_Spot: "Commodities",
  Brent_Crude: "Commodities",
  Wheat_Futures: "Commodities",
  Copper_Futures: "Commodities",
  Baltic_Dry_Index: "Commodities",
  // International Stocks (folds Madrid/Paris/Swiss/India single-member stock cats)
  Banco_Santander_MAD: "International Stocks",
  LVMH_PAR: "International Stocks",
  Nestle_SWX: "International Stocks",
  Reliance_NSE: "International Stocks",
};

/** Category-substring fallback for predictors not in ID_TO_BUCKET. */
function fallbackBucket(v: Variable): string {
  const cat = (v.category ?? "").toLowerCase();
  if (cat.includes("index")) return "Other International Indices";
  if (cat.includes("stock") || cat.includes("equity")) return "Other Stocks";
  if (cat.includes("fx") || cat.includes("rate")) return "FX Rates";
  if (cat.includes("commodity") || cat.includes("shipping")) return "Commodities";
  return "Other Predictors";
}

export function regroupPredictors(variables: Variable[]): PredictorGroup[] {
  const groups = new Map<string, Variable[]>();
  for (const v of variables) {
    const bucket = ID_TO_BUCKET[v.id] ?? fallbackBucket(v);
    if (!groups.has(bucket)) groups.set(bucket, []);
    groups.get(bucket)!.push(v);
  }

  // Sort: known buckets first in BUCKET_ORDER, then any fallback buckets A→Z.
  const ordered: PredictorGroup[] = [];
  for (const label of BUCKET_ORDER) {
    const items = groups.get(label);
    if (items && items.length) {
      ordered.push({ label, items });
      groups.delete(label);
    }
  }
  for (const label of Array.from(groups.keys()).sort()) {
    ordered.push({ label, items: groups.get(label)! });
  }
  return ordered;
}
