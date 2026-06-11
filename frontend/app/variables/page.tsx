/**
 * Variables — data explorer. Filterable list of all 39 variables.
 */

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
          Data
          <PremiumBadge />
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Variables registry
        </h1>
        <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
          Every series this engine tracks, with its provider chain and last
          observation. Click a row to see the full history.{" "}
          <span className="text-[var(--color-text3)]">
            Sección exclusiva para usuarios Premium.
          </span>
        </p>
      </div>

      <PremiumGate>
        <section className="mb-8">
          <h2 className="mb-3 text-[11px] uppercase tracking-widest text-[var(--color-text3)]">
            Stocks · {stocks.length}
          </h2>
          <VariableTable variables={stocks} sparkMap={sparkMap} />
        </section>

        <section>
          <h2 className="mb-3 text-[11px] uppercase tracking-widest text-[var(--color-text3)]">
            Predictors · {predictors.length}
          </h2>
          <VariableTable variables={predictors} sparkMap={sparkMap} />
        </section>
      </PremiumGate>
    </div>
  );
}

function VariableTable({
  variables,
  sparkMap,
}: {
  variables: Variable[];
  sparkMap: Map<string, Observation[]>;
}) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg3)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
              <th className="px-4 py-2.5 font-medium">ID</th>
              <th className="px-4 py-2.5 font-medium">Name</th>
              <th className="px-4 py-2.5 font-medium">Category</th>
              <th className="px-4 py-2.5 text-right font-medium">Last</th>
              <th className="px-4 py-2.5 text-right font-medium">Value</th>
              <th className="px-4 py-2.5 font-medium">Provider</th>
              <th className="px-4 py-2.5 font-medium">Trend (60d)</th>
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
                      <span title={fmtDate(v.last_observed_on)}>
                        {fmtRelative(v.last_observed_on)}
                      </span>
                    ) : (
                      <Badge tone="amber">no data</Badge>
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
