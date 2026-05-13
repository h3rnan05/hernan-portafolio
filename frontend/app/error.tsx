"use client";

/**
 * Route-level error boundary.
 *
 * Catches any client-side exception in this segment so we render a friendly
 * fallback instead of a blank page. The user can click "Try again" to
 * re-run the failed render, or navigate elsewhere via the top nav.
 */

import Link from "next/link";
import { useEffect } from "react";

export default function GlobalRouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to console — Sentry / etc. plug in here later
    if (typeof window !== "undefined") {
      console.error("Route error boundary caught:", error);
    }
  }, [error]);

  return (
    <div className="mx-auto max-w-md px-6 py-24 text-center">
      <div className="mb-2 text-[10px] uppercase tracking-widest text-[var(--color-red)]">
        Something broke
      </div>
      <h1 className="text-xl font-semibold tracking-tight">
        Couldn&rsquo;t render this page
      </h1>
      <p className="mt-2 text-[13px] text-[var(--color-text2)]">
        A client-side error came up. The rest of the app is fine — use the nav
        above to keep moving, or try this page again.
      </p>
      {error.message && (
        <pre className="mt-4 max-h-32 overflow-auto rounded-[8px] bg-[var(--color-bg3)] p-3 text-left font-mono text-[11px] text-[var(--color-text3)]">
          {error.message}
        </pre>
      )}
      <div className="mt-6 flex items-center justify-center gap-2">
        <button
          type="button"
          onClick={reset}
          className="rounded-[8px] bg-[var(--color-cyan)] px-4 py-2 text-[12px] font-semibold text-[var(--color-bg)] active:scale-[0.97]"
        >
          Try again
        </button>
        <Link
          href="/"
          className="rounded-[8px] bg-[var(--color-bg3)] px-4 py-2 text-[12px] font-medium text-[var(--color-text2)] hover:bg-[var(--color-bg4)]"
        >
          Back to overview
        </Link>
      </div>
    </div>
  );
}
