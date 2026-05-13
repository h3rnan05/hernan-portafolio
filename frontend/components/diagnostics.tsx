/**
 * Diagnostic primitives — the four-test gauges + status badge.
 *
 * Acceptance thresholds (per brief §4.2):
 *   R²            ≥ 0.90
 *   Durbin-Watson ∈ [1.5, 2.5]
 *   Breusch-Pagan p > 0.05
 *   max VIF       < 10
 */

import { Badge, fmtNumber } from "@/components/primitives";

type GaugeProps = {
  label: string;
  value: number | null;
  pass: boolean;
  decimals?: number;
  detail?: string;
};

export function DiagnosticGauge({
  label,
  value,
  pass,
  decimals = 3,
  detail,
}: GaugeProps) {
  return (
    <div className="rounded-[10px] bg-[var(--color-bg3)] p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
          {label}
        </span>
        <span
          className={
            "inline-block size-1.5 rounded-full " +
            (pass ? "bg-[var(--color-green)]" : "bg-[var(--color-amber)]")
          }
          aria-label={pass ? "passing" : "review"}
        />
      </div>
      <div className="font-mono tabular text-base font-semibold leading-none text-[var(--color-text)]">
        {fmtNumber(value, { decimals })}
      </div>
      {detail && (
        <div className="mt-1 text-[10.5px] text-[var(--color-text3)]">
          {detail}
        </div>
      )}
    </div>
  );
}

export function StatusBadge({
  status,
}: {
  status: "PASS" | "REVIEW" | "FAIL" | "SKIPPED";
}) {
  const tone =
    status === "PASS"
      ? "green"
      : status === "REVIEW"
        ? "amber"
        : status === "FAIL"
          ? "red"
          : "neutral";
  return <Badge tone={tone}>{status}</Badge>;
}

// Convenience: compute pass/fail flags from raw diagnostic values
export function passes(
  r2: number,
  dw: number,
  bp_p: number,
  max_vif: number,
): {
  r2: boolean;
  dw: boolean;
  bp_p: boolean;
  max_vif: boolean;
  overall: boolean;
} {
  const r2Pass = r2 >= 0.9;
  const dwPass = dw >= 1.5 && dw <= 2.5;
  const bpPass = bp_p > 0.05;
  const vifPass = max_vif < 10;
  return {
    r2: r2Pass,
    dw: dwPass,
    bp_p: bpPass,
    max_vif: vifPass,
    overall: r2Pass && dwPass && bpPass && vifPass,
  };
}
