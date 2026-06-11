"use client";

/**
 * ES/EN language switcher — a compact segmented control for the top nav.
 *
 * Writes the NEXT_LOCALE cookie (read server-side in i18n/request.ts) and calls
 * router.refresh() so server components re-render in the chosen language. The
 * choice persists across navigation and reloads via the cookie. No URL locale
 * prefixes, so existing links and the revalidate config are untouched.
 */

import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";

const LOCALE_COOKIE = "NEXT_LOCALE";
const ONE_YEAR = 60 * 60 * 24 * 365;
const LANGS = ["es", "en"] as const;

export function LanguageSwitcher() {
  const locale = useLocale();
  const t = useTranslations("language");
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  function choose(next: (typeof LANGS)[number]) {
    if (next === locale) return;
    document.cookie = `${LOCALE_COOKIE}=${next}; path=/; max-age=${ONE_YEAR}; samesite=lax`;
    startTransition(() => router.refresh());
  }

  return (
    <div
      role="group"
      aria-label={t("label")}
      className="inline-flex items-center gap-0.5 rounded-full bg-[var(--color-bg3)] p-0.5"
    >
      {LANGS.map((l) => {
        const active = locale === l;
        return (
          <button
            key={l}
            type="button"
            onClick={() => choose(l)}
            disabled={pending}
            aria-pressed={active}
            title={t(l === "es" ? "spanish" : "english")}
            className={`rounded-full px-2 py-1 text-[11px] font-semibold transition-colors disabled:opacity-60 ${
              active
                ? "bg-[var(--color-bg)] text-[var(--color-text)] shadow-[0_0_0_1px_rgba(255,255,255,0.06)]"
                : "text-[var(--color-text3)] hover:text-[var(--color-text2)]"
            }`}
          >
            {t(l)}
          </button>
        );
      })}
    </div>
  );
}
