/**
 * Variables — data explorer. Filterable list of all 39 variables.
 */

import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";

import { Sparkline } from "@/components/charts";
import { PremiumBadge, PremiumGate } from "@/components/premium-gate";
import {
  Badge,
  Card,
  fmtDate,
  fmtNumber,
  fmtRelative,
} from "@/components/primitives";
import { api, type Observation, type Variable } from "@/lib/api";

export const revalidate = 3600;

export default async function VariablesPage() {
  const t = await getTranslations("data");
  const variables = await api.listVariables().catch(() => [] as Variable[]);

  // Pull 30d obs for every variable that has data — parallel
  const sparkData = await Promise.all(
    variables.map(async (v) => {
      if (!v.last_observed_on) return { id: v.id, points: [] as Observation[] };
      try {
        const obs = await api.getObservations(v.id, {
          from: daysAgoISO(60),
          limit: 60,
        });
        return { id: v.id, points: obs };
      } catch {
        return { id: v.id, points: [] };
      }
    }),
  );
  const sparkMap = new Map(sparkData.map((s) => [s.id, s.points]));

  // Group by kind, then by category
  const stocks = variables.filter((v) => v.kind === "stock");
  const predictors = variables.filter((v) => v.kind === "predictor");

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8">
        <div className="mb-1 flex items-center gap-2 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          {t("eyebrow")}
          <PremiumBadge />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
          {t("subtitle")}{" "}
          <span className="text-[var(--color-text3)]">{t("premium_note")}</span>
        </p>
      </div>

      <PremiumGate>
        <section className="mb-8">
          <h2 className="mb-3 text-[11px] uppercase tracking-widest text-[var(--color-text3)]">
            {t("stocks_count", { count: stocks.length })}
          </h2>
          <VariableTable variables={stocks} sparkMap={sparkMap} />
        </section>

        <section>
          <h2 className="mb-3 text-[11px] uppercase tracking-widest text-[var(--color-text3)]">
            {t("predictors_count", { count: predictors.length })}
          </h2>
          <VariableTable variables={predictors} sparkMap={sparkMap} />
        </section>
      </PremiumGate>
    </div>
  );
}

async function VariableTable({
  variables,
  sparkMap,
}: {
  variables: Variable[];
  sparkMap: Map<string, Observation[]>;
}) {
  const t = await getTranslations("data");
  const tc = await getTranslations("common");
  const locale = await getLocale();
  return (
    <Card className="p-0 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg3)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
              <th className="px-4 py-2.5 font-medium">{t("th_id")}</th>
              <th className="px-4 py-2.5 font-medium">{t("th_name")}</th>
              <th className="px-4 py-2.5 font-medium">{t("th_category")}</th>
              <th className="px-4 py-2.5 text-right font-medium">{t("th_last")}</th>
              <th className="px-4 py-2.5 text-right font-medium">{t("th_value")}</th>
              <th className="px-4 py-2.5 font-medium">{t("th_provider")}</th>
              <th className="px-4 py-2.5 font-medium">{t("th_trend")}</th>
            </tr>
          </thead>
          <tbody>
            {variables.map((v) => {
              const series = sparkMap.get(v.id) ?? [];
              const provider =
                v.providers[0]?.name ?? "—";
              return (
                <tr
                  key={v.id}
                  className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-bg3)]"
                >
                  <td className="px-4 py-2 font-mono font-medium">
                    <Link
                      href={`/variables/${encodeURIComponent(v.id)}`}
                      className="hover:text-[var(--color-cyan)]"
                    >
                      {v.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-[var(--color-text2)]">
                    {v.display_name}
                  </td>
                  <td className="px-4 py-2 text-[var(--color-text3)]">
                    {v.category ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-right text-[var(--color-text3)]">
                    {v.last_observed_on ? (
                      <span title={fmtDate(v.last_observed_on, locale)}>
                        {fmtRelative(v.last_observed_on)}
                      </span>
                    ) : (
                      <Badge tone="amber">{tc("no_data")}</Badge>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right font-mono tabular">
                    {v.last_value !== null
                      ? fmtNumber(v.last_value, { decimals: 2 })
                      : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-[11px] text-[var(--color-text3)]">
                    {provider}
                  </td>
                  <td className="px-4 py-2">
                    <Sparkline
                      values={series.map((s) => s.value)}
                      width={80}
                      height={24}
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function daysAgoISO(d: number): string {
  const dt = new Date();
  dt.setUTCDate(dt.getUTCDate() - d);
  return dt.toISOString().slice(0, 10);
}
