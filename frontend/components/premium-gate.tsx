"use client";

/**
 * Premium gate (HER task 8).
 *
 * Wraps premium-only content. When the signed-in user is on a paid plan (or
 * Supabase isn't configured at all, so there's nothing to gate against) the
 * children render normally. Otherwise the children are blurred and a lock
 * overlay with an upgrade/sign-in CTA is shown on top.
 */

import { useAuth } from "@/components/auth-provider";

export function PremiumBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-[6px] bg-[color-mix(in_srgb,var(--color-amber)_15%,transparent)] px-2 py-1 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[var(--color-amber)]">
      <LockIcon className="size-3" />
      Premium
    </span>
  );
}

export function PremiumGate({ children }: { children: React.ReactNode }) {
  const { configured, loading, user, isPremium } = useAuth();

  // Nothing to gate when auth isn't wired up, or while we don't know yet.
  if (!configured || loading || isPremium) {
    return <>{children}</>;
  }

  return (
    <div className="relative">
      <div
        aria-hidden
        className="pointer-events-none select-none blur-[6px] saturate-50 opacity-60"
      >
        {children}
      </div>
      <div className="absolute inset-0 z-10 flex items-center justify-center p-6">
        <div className="max-w-sm rounded-[16px] border border-[var(--color-border2)] bg-[var(--color-bg2)] p-6 text-center shadow-[0_8px_32px_-8px_rgba(0,0,0,0.6)]">
          <div className="mx-auto mb-3 grid size-11 place-items-center rounded-full bg-[color-mix(in_srgb,var(--color-amber)_18%,transparent)] text-[var(--color-amber)]">
            <LockIcon className="size-5" />
          </div>
          <h3 className="text-[15px] font-semibold">
            Accede con tu cuenta Premium
          </h3>
          <p className="mt-1.5 text-[12.5px] text-[var(--color-text2)]">
            {user
              ? "Esta sección de datos es exclusiva para usuarios de paga. Actualiza tu plan a Premium para ver la información completa."
              : "Esta sección de datos es exclusiva para usuarios de paga. Inicia sesión con tu cuenta Premium desde el botón en la esquina superior derecha."}
          </p>
          <p className="mt-3 text-[11px] text-[var(--color-text3)]">
            {user
              ? "Tu plan actual es Free."
              : "¿Aún no tienes cuenta? Crea una en segundos."}
          </p>
        </div>
      </div>
    </div>
  );
}

function LockIcon({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path
        d="M4.5 7V5.5a3.5 3.5 0 117 0V7m-8 0h9a1 1 0 011 1v5a1 1 0 01-1 1h-9a1 1 0 01-1-1V8a1 1 0 011-1z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
