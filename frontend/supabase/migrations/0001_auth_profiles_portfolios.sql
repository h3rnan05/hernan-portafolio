-- ─────────────────────────────────────────────────────────────────────────────
-- Auth-adjacent schema for the portfolio engine frontend.
--
-- Adds two tables on top of Supabase's built-in `auth.users`:
--   * public.profiles        — one row per user, carries the paid-plan flag.
--   * public.user_portfolios — up to 5 tracked portfolios per user, each with a
--                              list of asset tickers (the "Crear portafolio" flow).
--
-- Run against your Supabase project (SQL editor or `supabase db push`).
-- Idempotent-ish: guarded with IF NOT EXISTS where possible.
-- ─────────────────────────────────────────────────────────────────────────────

-- ─── profiles ────────────────────────────────────────────────────────────────
create table if not exists public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  email       text,
  plan        text not null default 'free',          -- 'free' | 'premium'
  is_premium  boolean not null default false,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

alter table public.profiles enable row level security;

drop policy if exists "profiles_select_own" on public.profiles;
create policy "profiles_select_own"
  on public.profiles for select
  using (auth.uid() = id);

drop policy if exists "profiles_update_own" on public.profiles;
create policy "profiles_update_own"
  on public.profiles for update
  using (auth.uid() = id);

-- Auto-create a profile row whenever a new auth user signs up.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ─── user_portfolios ─────────────────────────────────────────────────────────
create table if not exists public.user_portfolios (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users (id) on delete cascade,
  slot        int  not null check (slot between 1 and 5),
  name        text not null,
  assets      jsonb not null default '[]'::jsonb,     -- array of ticker strings
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (user_id, slot)
);

create index if not exists user_portfolios_user_idx
  on public.user_portfolios (user_id);

alter table public.user_portfolios enable row level security;

drop policy if exists "user_portfolios_all_own" on public.user_portfolios;
create policy "user_portfolios_all_own"
  on public.user_portfolios for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
