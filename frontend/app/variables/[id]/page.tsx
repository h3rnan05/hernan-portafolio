/**
 * Variable detail — full history + provider chain.
 */

import Link from "next/link";
import { notFound } from "next/navigation";

import { AreaChartCmp } from "@/components/charts";
import {
  Badge,
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  SectionHeader,
} from "@/components/primitives";
import { api, ApiError } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function VariableDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let variable;
  try {
    variable = await api.getVariable(id);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) notFound();
    throw e;
  }

  const obs = await api
    .getObservations(id, { from: daysAgoISO(365 * 2), limit: 1000 })
    .catch(() => []);

  const chartData = obs.map((o) => ({
    date: o.observed_on,
    value: o.value,
  }));

  return (
    <div className="mx-auto max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
      <div className="mb-2">
        <Link
          href="/variables"
          className="text-[12px] text-[var(--color-text3)] hover:text-[var(--color-text2)]"
        >
          ← All variables
        </Link>
      </div>
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {variable.kind} · {variable.category ?? "—"}
          </div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
            {variable.display_name}
          </h1>
          <p className="mt-1.5 font-mono text-[12px] text-[var(--color-text3)]">
            {variable.id}
            {variable.unit ? ` · ${variable.unit}` : ""}
          </p>
        </div>
        {variable.last_observed_on && (
          <div className="text-right">
            <div className="text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
              Last value
            </div>
            <div className="mt-0.5 font-mono tabular text-2xl font-semibold">
              {fmtNumber(variable.last_value, { decimals: 2 })}
            </div>
            <div className="text-[11px] text-[var(--color-text3)]">
              {fmtDate(variable.last_observed_on)}
            </div>
          </div>
        )}
      </div>

      <Card className="mb-6">
        <SectionHeader
          eyebrow={`${chartData.length} observations`}
          title="Full history"
          description="Daily values since first ingestion."
        />
        {chartData.length === 0 ? (
          <EmptyState
            title="No data ingested yet"
            description={`Run scripts/run_ingestion.py — provider chain: ${variable.providers
              .map((p) => p.name)
              .join(" → ")}`}
          />
        ) : (
          <AreaChartCmp
            data={chartData}
            dataKey="value"
            label={variable.display_name}
            color="var(--color-cyan)"
            yDecimals={2}
            height={320}
          />
        )}
      </Card>

      <Card>
        <SectionHeader eyebrow="Provider chain" title="Fallback order" />
        <ol className="space-y-2">
          {variable.providers.map((p, i) => (
            <li
              key={`${p.name}-${i}`}
              className="flex items-center gap-3 rounded-none bg-[var(--color-bg3)] px-3 py-2 text-[12.5px]"
            >
              <span className="inline-flex size-6 items-center justify-center rounded-full bg-[var(--color-bg4)] font-mono text-[10px] font-semibold text-[var(--color-text2)]">
                {i + 1}
              </span>
              <span className="font-medium text-[var(--color-text)]">
                {p.name}
              </span>
              <code className="ml-auto text-[var(--color-text3)]">{p.symbol}</code>
              {i === 0 && <Badge tone="green">primary</Badge>}
            </li>
          ))}
        </ol>
      </Card>
    </div>
  );
}

function daysAgoISO(d: number): string {
  const dt = new Date();
  dt.setUTCDate(dt.getUTCDate() - d);
  return dt.toISOString().slice(0, 10);
}
