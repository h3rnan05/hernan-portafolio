"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "hernan.adminToken";

/**
 * Lightweight admin-token helper:
 *   - reads from localStorage on mount
 *   - prompts the user the first time an admin action runs
 *   - persists for the session
 *
 * Not security UX (anyone with the device gets it). It's a friction
 * reducer for the single user who already knows the token.
 */
export function useAdminToken(): {
  token: string | null;
  ensure: () => string | null;
  clear: () => void;
} {
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const t = window.localStorage.getItem(STORAGE_KEY);
    if (t) setToken(t);
  }, []);

  const ensure = useCallback(() => {
    if (typeof window === "undefined") return null;
    const cached = window.localStorage.getItem(STORAGE_KEY);
    if (cached) {
      setToken(cached);
      return cached;
    }
    const entered = window.prompt(
      "Admin bearer token (from backend/.env ADMIN_BEARER_TOKEN):",
    );
    if (!entered) return null;
    window.localStorage.setItem(STORAGE_KEY, entered.trim());
    setToken(entered.trim());
    return entered.trim();
  }, []);

  const clear = useCallback(() => {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(STORAGE_KEY);
    setToken(null);
  }, []);

  return { token, ensure, clear };
}
