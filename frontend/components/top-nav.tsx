"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { AuthButton } from "@/components/auth-button";
import { LanguageSwitcher } from "@/components/language-switcher";

const ROUTES: { href: string; key: string; premium?: boolean }[] = [
  { href: "/", key: "overview" },
  { href: "/accounts", key: "accounts" },
  { href: "/positions", key: "positions" },
  { href: "/models", key: "models" },
  { href: "/portfolios", key: "portfolios" },
  { href: "/simulator", key: "simulator" },
  { href: "/variables", key: "data", premium: true },
];

export function TopNav() {
  const pathname = usePathname();
  const t = useTranslations("nav");
  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[color-mix(in_srgb,var(--color-bg)_92%,transparent)] backdrop-blur-md">
      <div className="mx-auto flex h-12 max-w-7xl items-center gap-0 px-4">

        {/* Logo */}
        <Link
          href="/"
          className="flex shrink-0 items-center gap-1.5 font-semibold tracking-tight"
          aria-label="Hernán — home"
        >
          <span className="inline-block size-2 rounded-full bg-[var(--color-green)]" aria-hidden />
          <span className="text-[13px]">Hernán</span>
        </Link>

        {/* Divider */}
        <span className="mx-3 h-4 w-px shrink-0 bg-[var(--color-border)]" aria-hidden />

        {/* Nav tabs — centered, take all available space */}
        <nav aria-label="Primary" className="flex flex-1 items-center">
          {ROUTES.map(({ href, key, premium }) => {
            const active = isActive(href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={`relative inline-flex h-12 items-center gap-1 px-2.5 text-[12.5px] font-medium whitespace-nowrap transition-colors duration-150 ${
                  active
                    ? "text-[var(--color-text)]"
                    : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
                }`}
              >
                {t(key)}
                {premium && (
                  <svg width="10" height="10" viewBox="0 0 16 16" fill="none"
                    aria-label={t("premium")} className="text-[var(--color-amber)]">
                    <path d="M4.5 7V5.5a3.5 3.5 0 117 0V7m-8 0h9a1 1 0 011 1v5a1 1 0 01-1 1h-9a1 1 0 01-1-1V8a1 1 0 011-1z"
                      stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
                {active && (
                  <span aria-hidden
                    className="absolute inset-x-1 -bottom-px h-px bg-[var(--color-green)]" />
                )}
              </Link>
            );
          })}
        </nav>

        {/* Right side: AI button + controls */}
        <div className="flex shrink-0 items-center gap-1.5 border-l border-[var(--color-border)] pl-3">
          <Link
            href="/assistant"
            aria-current={pathname.startsWith("/assistant") ? "page" : undefined}
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[12px] font-semibold text-[var(--color-text)] transition active:scale-[0.96]"
            style={{
              background:
                "linear-gradient(135deg, color-mix(in oklab, var(--color-green) 22%, transparent), color-mix(in oklab, var(--color-cyan) 18%, transparent))",
              boxShadow: pathname.startsWith("/assistant")
                ? "0 0 0 1px color-mix(in oklab, var(--color-green) 60%, transparent)"
                : "0 1px 0 0 color-mix(in oklab, var(--color-green) 35%, transparent) inset",
            }}
          >
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
              <path d="M8 1.5l1.4 3.6L13 6.5 9.4 7.9 8 11.5 6.6 7.9 3 6.5l3.6-1.4L8 1.5z" fill="var(--color-green)" />
              <path d="M13 10.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6.6-1.6z" fill="var(--color-cyan)" />
            </svg>
            {t("assistant")}
          </Link>
          <LanguageSwitcher />
          <AuthButton />
        </div>

      </div>
    </header>
  );
}
