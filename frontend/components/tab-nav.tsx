"use client";

/**
 * Query-param tab navigation. Renders a row of pill tabs that update a single
 * search param (default `tab`) while preserving the current pathname, so tab
 * state is shareable/bookmarkable and the page can read it server-side.
 */

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

export type TabDef = { key: string; label: string };

export function TabNav({
  tabs,
  param = "tab",
  className = "",
}: {
  tabs: TabDef[];
  param?: string;
  className?: string;
}) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const current = searchParams.get(param) ?? tabs[0]?.key;

  function hrefFor(key: string) {
    const params = new URLSearchParams(searchParams.toString());
    params.set(param, key);
    return `${pathname}?${params.toString()}`;
  }

  return (
    <div
      className={`inline-flex items-center gap-1 rounded-[10px] bg-[var(--color-bg3)] p-1 ${className}`}
      role="tablist"
    >
      {tabs.map((t) => {
        const active = t.key === current;
        return (
          <Link
            key={t.key}
            href={hrefFor(t.key)}
            scroll={false}
            role="tab"
            aria-selected={active}
            className={`rounded-[7px] px-3.5 py-1.5 text-[12.5px] font-medium transition-colors ${
              active
                ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
                : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
            }`}
          >
            {t.label}
          </Link>
        );
      })}
    </div>
  );
}
