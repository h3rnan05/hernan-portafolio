/**
 * Browser-side Supabase client (singleton).
 *
 * Returns `null` when the Supabase env vars are absent so the app still builds
 * and runs locally without auth configured — callers must null-check. The
 * auth-provider treats a null client as "auth not configured".
 */

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const isSupabaseConfigured = Boolean(URL && ANON);

let cached: SupabaseClient | null = null;

export function getSupabaseBrowserClient(): SupabaseClient | null {
  if (!isSupabaseConfigured) return null;
  if (cached) return cached;
  cached = createBrowserClient(URL!, ANON!);
  return cached;
}
