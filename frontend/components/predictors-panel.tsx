"use client";

/**
 * Predictors coverage panel — which predictors are populated, grouped into the
 * balanced category buckets from lib/predictor-categories (HER task 4).
 *
 * Moved off the Overview page (HER tasks 2 & 3) and fetches client-side.
 */

import { useTranslations } from "next-intl";
import Link from "next/link";

import {
  Badge,
  Card,
  fmtNumber,
} from "@/components/primitives";
import { PredictorsSkeleton } from "@/components/skeleton";
import { useCached } from "@/hooks/use-cached";
import { api, type Variable } from "@/lib/api";
import { regroupPredictors, type PredictorGroup } from "@/lib/predictor-categories";

async function fetchPredictorGroups(): Promise<PredictorGroup[]> {
  const predictors: Variable[] = await api.listVariables("predictor");
  return regroupPredictors(predictors);
}

export function PredictorsPanel() {
  const t = useTranslations("predictors");
  const tc = useTranslations("common");
  const { data: groups, isCold } = useCached(
    "predictors-panel",
    fetchPredictorGroups,
  );

  if (isCold || !groups) {
    return <PredictorsSkeleton />;
  }

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {groups.map((g) => (
        <Card key={g.label}>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
              {g.label}
            </span>
            <span className="text-[10px] text-[var(--color-text3)]">
              {t("live_count", {
                live: g.items.filter((v) => v.last_observed_on).length,
                total: g.items.length,
              })}
            </span>
          </div>
          <ul className="divide-y divide-[var(--color-border)]">
            {g.items.map((v) => (
              <li
                key={v.id}
                className="flex items-center justify-between gap-3 py-2 text-[12.5px]"
              >
                <Link
                  href={`/variables/${encodeURIComponent(v.id)}`}
                  className="truncate font-medium hover:text-[var(--color-cyan)]"
                  title={v.display_name}
                >
                  {v.display_name}
                </Link>
                {v.last_observed_on ? (
                  <span className="flex shrink-0 items-center gap-2 text-[var(--color-text3)]">
                    <span className="font-mono tabular text-[var(--color-text2)]">
                      {fmtNumber(v.last_value, { decimals: 2 })}
                    </span>
                    <Badge tone="green">{tc("live")}</Badge>
                  </span>
                ) : (
                  <Badge tone="amber">{tc("no_data")}</Badge>
                )}
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}
