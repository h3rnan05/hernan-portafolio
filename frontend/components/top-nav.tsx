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
  { href: "/news", key: "news" },
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
      {/* Bloomberg orange top bar */}
      <div className="bg-[var(--color-amber)] px-4 py-0.5 text-[10px] font-semibold tracking-widest text-black uppercase hidden md:flex items-center justify-between">
        <span>HERNAN TERMINAL · PAPER TRADING</span>
        <span className="font-normal opacity-80">ALPACA · SUPABASE · RENDER</span>
      </div>

      <header className="sticky top-0 z-40 border-b-2 border-[var(--color-amber)] bg-[var(--color-bg)]">
        <div className="mx-auto flex h-11 max-w-7xl items-center px-4">

          {/* Logo */}
          <Link
            href="/"
            className="flex shrink-0 items-center gap-2 font-semibold tracking-widest uppercase"
            aria-label="Hernán — home"
            onClick={() => setOpen(false)}
          >
            <span className="inline-block size-2 bg-[var(--color-amber)]" aria-hidden />
            <span className="text-[12px] text-[var(--color-amber)]">HERNÁN</span>
          </Link>

          {/* Desktop nav */}
          <span className="mx-3 hidden h-4 w-px shrink-0 bg-[var(--color-border2)] md:block" aria-hidden />
          <nav aria-label="Primary" className="hidden flex-1 items-center md:flex">
            {ROUTES.map(({ href, key, premium }) => {
              const active = isActive(href);
              return (
                <Link
                  key={href}
                  href={href}
                  aria-current={active ? "page" : undefined}
                  className={`relative inline-flex h-11 items-center gap-1 px-3 text-[11px] font-semibold tracking-wider uppercase whitespace-nowrap transition-colors duration-150 ${
                    active
                      ? "bg-[var(--color-amber)] text-black"
                      : "text-[var(--color-text2)] hover:text-[var(--color-text)] hover:bg-[var(--color-bg3)]"
                  }`}
                >
                  {t(key)}
                  {premium && <LockIcon />}
                </Link>
              );
            })}
          </nav>

          {/* Right side */}
          <div className="ml-auto flex shrink-0 items-center gap-2 pl-3 border-l border-[var(--color-border2)]">
            {/* AI button */}
            <Link
              href="/assistant"
              aria-current={pathname.startsWith("/assistant") ? "page" : undefined}
              className={`inline-flex h-7 items-center gap-1.5 px-3 text-[11px] font-semibold uppercase tracking-wider transition-colors ${
                pathname.startsWith("/assistant")
                  ? "bg-[var(--color-amber)] text-black"
                  : "border border-[var(--color-amber)] text-[var(--color-amber)] hover:bg-[var(--color-amber)] hover:text-black"
              }`}
            >
              <svg width="10" height="10" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M8 1.5l1.4 3.6L13 6.5 9.4 7.9 8 11.5 6.6 7.9 3 6.5l3.6-1.4L8 1.5z" fill="currentColor" />
              </svg>
              <span className="hidden sm:inline">{t("assistant")}</span>
            </Link>

            <div className="hidden sm:flex items-center gap-2">
              <LanguageSwitcher />
              <AuthButton />
            </div>

            {/* Hamburger — mobile only */}
            <button
              className="ml-1 flex h-8 w-8 flex-col items-center justify-center gap-1.5 hover:bg-[var(--color-bg3)] md:hidden"
              aria-label={open ? "Cerrar menú" : "Abrir menú"}
              aria-expanded={open}
              onClick={() => setOpen((v) => !v)}
            >
              <span className={`block h-px w-4 bg-[var(--color-amber)] transition-all duration-200 ${open ? "translate-y-[3px] rotate-45" : ""}`} />
              <span className={`block h-px w-4 bg-[var(--color-amber)] transition-all duration-200 ${open ? "opacity-0" : ""}`} />
              <span className={`block h-px w-4 bg-[var(--color-amber)] transition-all duration-200 ${open ? "-translate-y-[5px] -rotate-45" : ""}`} />
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
            className="absolute left-0 right-0 top-11 border-b-2 border-[var(--color-amber)] bg-[var(--color-bg)] px-4 py-3 shadow-xl"
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
                    className={`flex items-center gap-2 px-3 py-2.5 text-[12px] font-semibold tracking-wider uppercase transition-colors ${
                      active
                        ? "bg-[var(--color-amber)] text-black"
                        : "text-[var(--color-text2)] hover:bg-[var(--color-bg3)] hover:text-[var(--color-text)]"
                    }`}
                  >
                    {active && (
                      <span className="h-1.5 w-1.5 bg-black" aria-hidden />
                    )}
                    {!active && <span className="h-1.5 w-1.5" aria-hidden />}
                    {t(key)}
                    {premium && <LockIcon />}
                  </Link>
                );
              })}
            </nav>

            <div className="mt-3 flex items-center gap-2 border-t border-[var(--color-border2)] pt-3">
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
