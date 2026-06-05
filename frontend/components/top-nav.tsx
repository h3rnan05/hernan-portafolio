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

        <div className="ml-3 shrink-0 border-l border-[var(--color-border)] pl-3">
          <Link
            href="/assistant"
            aria-current={pathname.startsWith("/assistant") ? "page" : undefined}
            className="group inline-flex h-9 items-center gap-1.5 rounded-full px-3.5 text-[13px] font-semibold text-[var(--color-text)] transition active:scale-[0.96]"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in oklab, var(--color-green) 22%, transparent), color-mix(in oklab, var(--color-cyan) 18%, transparent))",
              boxShadow: pathname.startsWith("/assistant")
                ? "0 0 0 1px color-mix(in oklab, var(--color-green) 60%, transparent), 0 1px 10px -2px color-mix(in oklab, var(--color-green) 55%, transparent)"
                : "0 1px 0 0 color-mix(in oklab, var(--color-green) 35%, transparent) inset, 0 1px 8px -2px color-mix(in oklab, var(--color-green) 40%, transparent)",
            }}
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden>
              <path d="M8 1.5l1.4 3.6L13 6.5 9.4 7.9 8 11.5 6.6 7.9 3 6.5l3.6-1.4L8 1.5z" fill="var(--color-green)" />
              <path d="M13 10.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6.6-1.6z" fill="var(--color-cyan)" />
            </svg>
            Asistente AI
          </Link>
        </div>
      </div>
    </header>
  );
}
