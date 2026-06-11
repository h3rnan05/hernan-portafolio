/**
 * Layout-matching skeleton loaders.
 *
 * Shown only on true cold loads (no cached data). Background refreshes keep the
 * last data on screen, so these never flash on revisits. The base `Block` is a
 * simple pulse placeholder; the exported skeletons mirror each page/widget's
 * layout so the swap-in doesn't shift the page.
 */

function Block({ className = "" }: { className?: string }) {
  return (
    <div
      className={"animate-pulse rounded-[10px] bg-[var(--color-bg3)] " + className}
      aria-hidden
    />
  );
}

function Card({ children }: { children?: React.ReactNode }) {
  return (
    <div className="rounded-[14px] bg-[var(--color-bg2)] p-5 shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_1px_2px_rgba(0,0,0,0.3)]">
      {children}
    </div>
  );
}

// ─── Widget skeletons (client components) ────────────────────────────────────

export function ComparisonSkeleton() {
  return (
    <Card>
      <div className="mb-4 flex items-end justify-between gap-4">
        <div className="space-y-2">
          <Block className="h-2.5 w-20" />
          <Block className="h-4 w-56" />
        </div>
        <Block className="h-8 w-44" />
      </div>
      <Block className="h-[300px] w-full" />
      <div className="mt-3 flex gap-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <Block key={i} className="h-3 w-24" />
        ))}
      </div>
    </Card>
  );
}

export function StocksGridSkeleton({ count = 9 }: { count?: number }) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
      {Array.from({ length: count }).map((_, i) => (
        <Block key={i} className="h-[104px]" />
      ))}
    </div>
  );
}

export function PredictorsSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {Array.from({ length: count }).map((_, i) => (
        <Block key={i} className="h-40" />
      ))}
    </div>
  );
}

// ─── Page skeletons ──────────────────────────────────────────────────────────

function MetricStrip() {
  return (
    <div className="mb-8 grid grid-cols-2 gap-3 md:grid-cols-4">
      {Array.from({ length: 4 }).map((_, i) => (
        <Block key={i} className="h-[88px]" />
      ))}
    </div>
  );
}

export function OverviewSkeleton() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8 space-y-2">
        <Block className="h-2.5 w-24" />
        <Block className="h-7 w-80" />
        <Block className="h-4 w-[36rem] max-w-full" />
      </div>
      <MetricStrip />
      <div className="mb-10 grid grid-cols-1 gap-3 lg:grid-cols-[2fr_1fr]">
        <Block className="h-[280px]" />
        <Block className="h-[280px]" />
      </div>
      <ComparisonSkeleton />
    </div>
  );
}

export function PortfoliosSkeleton() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8 space-y-2">
        <Block className="h-2.5 w-20" />
        <Block className="h-7 w-72" />
      </div>
      <Block className="mb-6 h-11 w-full max-w-xl" />
      <div className="space-y-6">
        <Card>
          <Block className="mb-4 h-3 w-full" />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Block key={i} className="h-4" />
            ))}
          </div>
        </Card>
        <Card>
          <Block className="h-[340px] w-full" />
        </Card>
      </div>
    </div>
  );
}

export function ModelsSkeleton() {
  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-6 space-y-2">
        <Block className="h-2.5 w-40" />
        <Block className="h-7 w-72" />
      </div>
      <Block className="mb-8 h-10 w-72" />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Block key={i} className="h-[260px]" />
        ))}
      </div>
    </div>
  );
}

export function HoldingsTableSkeleton() {
  return (
    <Card>
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Block key={i} className="h-8 w-full" />
        ))}
      </div>
    </Card>
  );
}
