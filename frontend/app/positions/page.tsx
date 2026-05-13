/**
 * Positions — broker mirror placeholder.
 *
 * Webull doesn't expose a public API for personal accounts, so there's
 * no automated mirror to plug in. Until a different broker integration
 * ships, this page stays as a placeholder. Real holdings are managed
 * via /holdings (manual entry, full P&L computed against live prices).
 */

import Link from "next/link";

import { Card } from "@/components/primitives";

export const dynamic = "force-dynamic";

export default function PositionsPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-16">
      <Card>
        <div className="flex flex-col items-center text-center">
          <div className="mb-3 inline-flex items-center gap-2 rounded-[6px] bg-[var(--color-bg3)] px-3 py-1.5">
            <span className="size-1.5 rounded-full bg-[var(--color-amber)]" />
            <span className="text-[10px] font-medium uppercase tracking-widest text-[var(--color-amber)]">
              Under construction
            </span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Broker integration
          </h1>
          <p className="mt-3 max-w-md text-[13px] text-[var(--color-text2)]">
            Webull doesn&rsquo;t expose a public API for personal accounts,
            so there&rsquo;s no automated way to mirror live broker
            positions here yet. Manage your portfolio on the Holdings page
            instead — P&amp;L is computed automatically against the latest
            close in the database.
          </p>
          <Link
            href="/holdings"
            className="mt-6 inline-flex h-9 items-center rounded-[8px] bg-[var(--color-cyan)] px-4 text-[12.5px] font-semibold text-[var(--color-bg)] active:scale-[0.97]"
          >
            Go to Holdings →
          </Link>
        </div>
      </Card>
    </div>
  );
}
