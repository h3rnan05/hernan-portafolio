"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

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
  const pathname  = usePathname();
  const t         = useTranslations("nav");
  const [open, setOpen] = useState(false);

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  return (
    <>
      <header className="sticky top-0 z-40 border-b border-[var(--color-border)] bg-[color-mix(in_srgb,var(--color-bg)_92%,transparent)] backdrop-blur-md">
        <div className="mx-auto flex h-12 max-w-7xl items-center px-4">

          {/* Logo */}
          <Link
            href="/"
            className="flex shrink-0 items-center gap-1.5 font-semibold tracking-tight"
            aria-label="Hernán — home"
            onClick={() => setOpen(false)}
          >
            <span className="inline-block size-2 rounded-full bg-[var(--color-green)]" aria-hidden />
            <span className="text-[13px]">Hernán</span>
          </Link>

          {/* Desktop nav */}
          <span className="mx-3 hidden h-4 w-px shrink-0 bg-[var(--color-border)] md:block" aria-hidden />
          <nav aria-label="Primary" className="hidden flex-1 items-center md:flex">
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
                  {premium && <LockIcon />}
                  {active && (
                    <span aria-hidden className="absolute inset-x-1 -bottom-px h-px bg-[var(--color-green)]" />
                  )}
                </Link>
              );
            })}
          </nav>

          {/* Right side */}
          <div className="ml-auto flex shrink-0 items-center gap-1.5 md:border-l md:border-[var(--color-border)] md:pl-3">
            {/* AI button — text hidden on mobile */}
            <Link
              href="/assistant"
              aria-current={pathname.startsWith("/assistant") ? "page" : undefined}
              className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-[12px] font-semibold text-[var(--color-text)] transition active:scale-[0.96]"
              style={{
                background: "linear-gradient(135deg, color-mix(in oklab, var(--color-green) 22%, transparent), color-mix(in oklab, var(--color-cyan) 18%, transparent))",
                boxShadow: pathname.startsWith("/assistant")
                  ? "0 0 0 1px color-mix(in oklab, var(--color-green) 60%, transparent)"
                  : "0 1px 0 0 color-mix(in oklab, var(--color-green) 35%, transparent) inset",
              }}
            >
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M8 1.5l1.4 3.6L13 6.5 9.4 7.9 8 11.5 6.6 7.9 3 6.5l3.6-1.4L8 1.5z" fill="var(--color-green)" />
                <path d="M13 10.5l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6.6-1.6z" fill="var(--color-cyan)" />
              </svg>
              <span className="hidden sm:inline">{t("assistant")}</span>
            </Link>

            <div className="hidden sm:flex items-center gap-1.5">
              <LanguageSwitcher />
              <AuthButton />
            </div>

            {/* Hamburger — mobile only */}
            <button
              className="ml-1 flex h-8 w-8 flex-col items-center justify-center gap-1.5 rounded-[6px] hover:bg-[var(--color-bg3)] md:hidden"
              aria-label={open ? "Cerrar menú" : "Abrir menú"}
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
            >
              <span className={`block h-px w-4 bg-[var(--color-text2)] transition-all duration-200 ${open ? "translate-y-[3px] rotate-45" : ""}`} />
              <span className={`block h-px w-4 bg-[var(--color-text2)] transition-all duration-200 ${open ? "opacity-0" : ""}`} />
              <span className={`block h-px w-4 bg-[var(--color-text2)] transition-all duration-200 ${open ? "-translate-y-[5px] -rotate-45" : ""}`} />
            </button>
          </div>

        </div>
      </header>

      {/* Mobile drawer */}
      {open && (
        <div
          className="fixed inset-0 z-30 md:hidden"
          onClick={() => setOpen(false)}
        >
          <div
            className="absolute left-0 right-0 top-12 border-b border-[var(--color-border)] bg-[var(--color-bg)] px-4 py-3 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <nav className="flex flex-col gap-0.5">
              {ROUTES.map(({ href, key, premium }) => {
                const active = isActive(href);
                return (
                  <Link
                    key={href}
                    href={href}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setOpen(false)}
                    className={`flex items-center gap-2 rounded-[8px] px-3 py-2.5 text-[14px] font-medium transition-colors ${
                      active
                        ? "bg-[var(--color-bg3)] text-[var(--color-text)]"
                        : "text-[var(--color-text2)] hover:bg-[var(--color-bg2)]"
                    }`}
                  >
                    {active && (
                      <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-green)]" aria-hidden />
                    )}
                    {!active && <span className="h-1.5 w-1.5" aria-hidden />}
                    {t(key)}
                    {premium && <LockIcon />}
                  </Link>
                );
              })}
            </nav>

            <div className="mt-3 flex items-center gap-2 border-t border-[var(--color-border)] pt-3">
              <LanguageSwitcher />
              <AuthButton />
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function LockIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 16 16" fill="none" className="text-[var(--color-amber)]">
      <path d="M4.5 7V5.5a3.5 3.5 0 117 0V7m-8 0h9a1 1 0 011 1v5a1 1 0 01-1 1h-9a1 1 0 01-1-1V8a1 1 0 011-1z"
        stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
