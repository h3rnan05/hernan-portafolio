/**
 * Hand-maintained types for the Supabase tables this app touches. Kept in sync
 * with supabase/migrations/0001_auth_profiles_portfolios.sql.
 */

export type Profile = {
  id: string;
  email: string | null;
  plan: "free" | "premium" | string;
  is_premium: boolean;
  created_at: string;
  updated_at: string;
};

export type UserPortfolio = {
  id: string;
  user_id: string;
  slot: number; // 1..5
  name: string;
  assets: string[]; // ticker symbols
  created_at: string;
  updated_at: string;
};

/** Shape used by the "Crear portafolio" form before it's persisted. */
export type UserPortfolioInput = {
  slot: number;
  name: string;
  assets: string[];
};

export const MAX_PORTFOLIOS = 5;
