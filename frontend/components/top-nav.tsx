"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ROUTES: { href: string; label: string }[] = [
  { href: "/", label: "Overview" },
  { href: "/accounts", label: "Accounts" },
  { href: "/holdings", label: "Holdings" },
  { href: "/models", label: "Models" },
  { href: "/portfolios", label: "Portfolios" },
  { href: "/simulator", label: "Simulator" },
  { href: "/positions", label: "Positions" },
  { href: "/variables", label: "Data" },
];

export function TopNav() {
  const pathname = usePathname();
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[color-mix(in_srgb,var(--color-bg)_92%,transparent)] backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-8 px-6">
        <Link
          href="/"
          className="flex items-center gap-2 font-semibold tracking-tight"
          aria-label="Hernán — home"
        >
          <span
            className="inline-block size-2 rounded-full bg-[var(--color-green)]"
            aria-hidden
          />
          <span>Hernán</span>
          <span className="hidden text-[10px] uppercase tracking-widest text-[var(--color-text3)] sm:inline">
            Portfolio Engine
          </span>
        </Link>

        <nav
          aria-label="Primary"
          className="ml-auto flex items-center gap-1 overflow-x-auto"
        >
          {ROUTES.map(({ href, label }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`relative inline-flex h-9 items-center px-3 text-[13px] font-medium transition-colors duration-150 ${
                  active
                    ? "text-[var(--color-text)]"
                    : "text-[var(--color-text2)] hover:text-[var(--color-text)]"
                }`}
              >
                {label}
                {active && (
                  <span
                    aria-hidden
                    className="absolute inset-x-2 -bottom-px h-px bg-[var(--color-green)]"
                  />
                )}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
