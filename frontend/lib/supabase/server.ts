/**
 * Server-side Supabase client, bound to the request cookie jar.
 *
 * Used by server components / route handlers that need to read the signed-in
 * user (e.g. premium gating enforced on the server). Returns `null` when the
 * Supabase env vars are absent.
 */

import { createServerClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";
import { cookies } from "next/headers";

const URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const ANON = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export async function getSupabaseServerClient(): Promise<SupabaseClient | null> {
  if (!URL || !ANON) return null;
  const cookieStore = await cookies();
  return createServerClient(URL, ANON, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // `set` throws in pure Server Components (no mutable cookies). Safe to
          // ignore — session refresh is handled in middleware / route handlers.
        }
      },
    },
  });
}

/** Convenience: returns the signed-in user's profile or null. */
export async function getServerProfile() {
  const supabase = await getSupabaseServerClient();
  if (!supabase) return null;
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;
  const { data } = await supabase
    .from("profiles")
    .select("*")
    .eq("id", user.id)
    .maybeSingle();
  return data as { id: string; is_premium: boolean; plan: string } | null;
}
