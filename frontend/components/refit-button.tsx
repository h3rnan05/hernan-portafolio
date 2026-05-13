"use client";

/**
 * Refit button — fires POST /models/refit_all and refreshes the page on success.
 *
 * Token comes from useAdminToken (prompt once, cache in localStorage).
 */

import { useState } from "react";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/primitives";
import { useAdminToken } from "@/hooks/use-admin-token";
import { api, ApiError, type RefitOutcome } from "@/lib/api";

export function RefitButton() {
  const router = useRouter();
  const { ensure } = useAdminToken();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RefitOutcome[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onClick = async () => {
    if (busy) return;
    const token = ensure();
    if (!token) return;
    if (
      !window.confirm(
        "Re-fit every model? Takes a few seconds and replaces all 9 active models with fresh fits.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const outcomes = await api.refitAll(token, { k_per_stock: 3 });
      setResult(outcomes);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col items-end gap-2">
      <button
        type="button"
        onClick={onClick}
        disabled={busy}
        className={
          "rounded-[8px] px-3.5 py-2 text-[12px] font-semibold transition active:scale-[0.97] " +
          (busy
            ? "bg-[var(--color-bg4)] text-[var(--color-text3)]"
            : "bg-[var(--color-cyan)] text-[var(--color-bg)] hover:brightness-110")
        }
      >
        {busy ? "Refitting…" : "Refit all models"}
      </button>
      {error && (
        <div className="max-w-xs rounded-[6px] bg-[color-mix(in_srgb,var(--color-red)_10%,transparent)] px-2 py-1 text-[11px] text-[var(--color-red)]">
          {error}
        </div>
      )}
      {result && (
        <div className="flex items-center gap-1.5 text-[11px]">
          <Badge tone="green">
            {result.filter((r) => r.status === "PASS").length} PASS
          </Badge>
          <Badge tone="amber">
            {result.filter((r) => r.status === "REVIEW").length} REVIEW
          </Badge>
          {result.filter((r) => r.status === "SKIPPED").length > 0 && (
            <Badge tone="neutral">
              {result.filter((r) => r.status === "SKIPPED").length} SKIPPED
            </Badge>
          )}
        </div>
      )}
    </div>
  );
}
