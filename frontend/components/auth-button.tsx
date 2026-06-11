"use client";

/**
 * Top-nav auth control: shows a sign-in button when logged out and the user's
 * email + plan + sign-out when logged in. Opens an inline email/password modal.
 */

import { useTranslations } from "next-intl";
import { useState } from "react";

import { useAuth } from "@/components/auth-provider";

export function AuthButton() {
  const t = useTranslations("auth");
  const { configured, loading, user, profile, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  if (!configured) {
    return (
      <span
        className="hidden text-[11px] text-[var(--color-text3)] sm:inline"
        title={t("offline_title")}
      >
        {t("offline")}
      </span>
    );
  }

  if (loading) {
    return <span className="size-7 animate-pulse rounded-full bg-[var(--color-bg3)]" />;
  }

  if (!user) {
    return (
      <>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-full bg-[var(--color-bg3)] px-3.5 py-1.5 text-[12px] font-semibold text-[var(--color-text)] hover:bg-[var(--color-bg4)] active:scale-[0.97]"
        >
          {t("sign_in")}
        </button>
        {open && <AuthModal onClose={() => setOpen(false)} />}
      </>
    );
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setMenuOpen((m) => !m)}
        className="flex items-center gap-2 rounded-full bg-[var(--color-bg3)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--color-text)] hover:bg-[var(--color-bg4)]"
      >
        <span className="grid size-5 place-items-center rounded-full bg-[var(--color-cyan)] text-[10px] font-bold text-black">
          {(user.email ?? "?").slice(0, 1).toUpperCase()}
        </span>
        <span className="hidden max-w-[140px] truncate sm:inline">{user.email}</span>
        {profile?.is_premium && (
          <span className="rounded-[5px] bg-[color-mix(in_srgb,var(--color-amber)_18%,transparent)] px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-[var(--color-amber)]">
            {t("premium")}
          </span>
        )}
      </button>
      {menuOpen && (
        <div className="absolute right-0 z-50 mt-2 w-56 rounded-[12px] border border-[var(--color-border)] bg-[var(--color-bg2)] p-2 shadow-xl">
          <div className="border-b border-[var(--color-border)] px-2 pb-2 text-[11px] text-[var(--color-text3)]">
            <div className="truncate text-[var(--color-text2)]">{user.email}</div>
            <div className="mt-0.5">
              {t("plan", { plan: profile?.is_premium ? t("premium") : t("free") })}
            </div>
          </div>
          <button
            type="button"
            onClick={async () => {
              setMenuOpen(false);
              await signOut();
            }}
            className="mt-1 w-full rounded-[8px] px-2 py-1.5 text-left text-[12px] text-[var(--color-text2)] hover:bg-[var(--color-bg3)] hover:text-[var(--color-text)]"
          >
            {t("sign_out")}
          </button>
        </div>
      )}
    </div>
  );
}

function AuthModal({ onClose }: { onClose: () => void }) {
  const t = useTranslations("auth");
  const tc = useTranslations("common");
  const { signIn, signUp } = useAuth();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setBusy(true);
    const fn = mode === "signin" ? signIn : signUp;
    const { error } = await fn(email.trim(), password);
    setBusy(false);
    if (error) {
      setError(error);
      return;
    }
    if (mode === "signup") {
      setInfo(t("signup_confirm"));
      return;
    }
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-[16px] bg-[var(--color-bg2)] p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-2">
          <button
            type="button"
            onClick={() => setMode("signin")}
            className={`text-[13px] font-semibold ${mode === "signin" ? "text-[var(--color-text)]" : "text-[var(--color-text3)]"}`}
          >
            {t("sign_in")}
          </button>
          <span className="text-[var(--color-text3)]">·</span>
          <button
            type="button"
            onClick={() => setMode("signup")}
            className={`text-[13px] font-semibold ${mode === "signup" ? "text-[var(--color-text)]" : "text-[var(--color-text3)]"}`}
          >
            {t("create_account")}
          </button>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder={t("email_placeholder")}
            className="w-full rounded-[8px] bg-[var(--color-bg3)] px-3 py-2 text-[13px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
          />
          <input
            type="password"
            required
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t("password_placeholder")}
            className="w-full rounded-[8px] bg-[var(--color-bg3)] px-3 py-2 text-[13px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
          />
          {error && <div className="text-[12px] text-[var(--color-red)]">{error}</div>}
          {info && <div className="text-[12px] text-[var(--color-green)]">{info}</div>}
          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="rounded-[8px] px-3 py-2 text-[12px] font-medium text-[var(--color-text3)] hover:text-[var(--color-text)]"
            >
              {tc("cancel")}
            </button>
            <button
              type="submit"
              disabled={busy}
              className="rounded-[8px] bg-[var(--color-cyan)] px-4 py-2 text-[12px] font-semibold text-black hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "…" : mode === "signin" ? t("enter") : t("create")}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
