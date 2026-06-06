"use client";

/**
 * "Crear portafolio" flow (HER task 5).
 *
 * A registered user can track up to 5 portfolios. Each portfolio has a name and
 * a set of assets chosen from the 9 tracked tickers (custom tickers allowed).
 * Configuration is saved to Supabase (`public.user_portfolios`) under the
 * signed-in user, one row per slot (1–5), enforced by a unique (user_id, slot)
 * constraint and a max of 5 in the UI.
 */

import { useCallback, useEffect, useState } from "react";

import { useAuth } from "@/components/auth-provider";
import { Badge, Card, EmptyState, SectionHeader } from "@/components/primitives";
import { getSupabaseBrowserClient } from "@/lib/supabase/client";
import { MAX_PORTFOLIOS, type UserPortfolio } from "@/lib/supabase/types";

// The 9 tracked tickers users can pick from (plus custom).
const AVAILABLE_ASSETS = [
  "AMZN",
  "BA",
  "CAT",
  "CRM",
  "GOOGL",
  "NVDA",
  "QCOM",
  "V",
  "XOM",
];

type Draft = {
  id: string | null; // null = not yet persisted
  slot: number;
  name: string;
  assets: string[];
};

export function CreatePortfolio() {
  const { configured, loading: authLoading, user } = useAuth();
  const supabase = getSupabaseBrowserClient();

  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savingSlot, setSavingSlot] = useState<number | null>(null);
  const [customTicker, setCustomTicker] = useState<Record<number, string>>({});

  const load = useCallback(async () => {
    if (!supabase || !user) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const { data, error } = await supabase
      .from("user_portfolios")
      .select("*")
      .eq("user_id", user.id)
      .order("slot");
    if (error) setError(error.message);
    setDrafts(
      ((data as UserPortfolio[]) ?? []).map((r) => ({
        id: r.id,
        slot: r.slot,
        name: r.name,
        assets: Array.isArray(r.assets) ? r.assets : [],
      })),
    );
    setLoading(false);
  }, [supabase, user]);

  useEffect(() => {
    load();
  }, [load]);

  function lowestFreeSlot(): number | null {
    for (let s = 1; s <= MAX_PORTFOLIOS; s++) {
      if (!drafts.some((d) => d.slot === s)) return s;
    }
    return null;
  }

  function addPortfolio() {
    const slot = lowestFreeSlot();
    if (slot === null) return;
    setDrafts((d) => [
      ...d,
      { id: null, slot, name: `Portafolio ${slot}`, assets: [] },
    ]);
  }

  function patchDraft(slot: number, patch: Partial<Draft>) {
    setDrafts((d) => d.map((x) => (x.slot === slot ? { ...x, ...patch } : x)));
  }

  function toggleAsset(slot: number, ticker: string) {
    setDrafts((d) =>
      d.map((x) =>
        x.slot === slot
          ? {
              ...x,
              assets: x.assets.includes(ticker)
                ? x.assets.filter((t) => t !== ticker)
                : [...x.assets, ticker],
            }
          : x,
      ),
    );
  }

  function addCustom(slot: number) {
    const raw = (customTicker[slot] ?? "").trim().toUpperCase();
    if (!raw) return;
    setDrafts((d) =>
      d.map((x) =>
        x.slot === slot && !x.assets.includes(raw)
          ? { ...x, assets: [...x.assets, raw] }
          : x,
      ),
    );
    setCustomTicker((c) => ({ ...c, [slot]: "" }));
  }

  async function save(draft: Draft) {
    if (!supabase || !user) return;
    if (!draft.name.trim()) {
      setError("El portafolio necesita un nombre.");
      return;
    }
    setSavingSlot(draft.slot);
    setError(null);
    const { error } = await supabase.from("user_portfolios").upsert(
      {
        user_id: user.id,
        slot: draft.slot,
        name: draft.name.trim(),
        assets: draft.assets,
      },
      { onConflict: "user_id,slot" },
    );
    setSavingSlot(null);
    if (error) {
      setError(error.message);
      return;
    }
    await load();
  }

  async function remove(draft: Draft) {
    if (!draft.id) {
      // Unsaved draft — just drop it locally.
      setDrafts((d) => d.filter((x) => x.slot !== draft.slot));
      return;
    }
    if (!supabase || !user) return;
    if (!window.confirm(`¿Eliminar "${draft.name}"?`)) return;
    setSavingSlot(draft.slot);
    const { error } = await supabase
      .from("user_portfolios")
      .delete()
      .eq("id", draft.id);
    setSavingSlot(null);
    if (error) {
      setError(error.message);
      return;
    }
    await load();
  }

  // ─── Render guards ──────────────────────────────────────────────────────
  if (!configured) {
    return (
      <Card>
        <SectionHeader
          eyebrow="Crear portafolio"
          title="Configura tus portafolios"
          description="Conecta Supabase (NEXT_PUBLIC_SUPABASE_URL / ANON_KEY) para guardar portafolios por usuario."
        />
      </Card>
    );
  }

  if (authLoading) {
    return (
      <Card>
        <div className="text-[12.5px] text-[var(--color-text3)]">Cargando…</div>
      </Card>
    );
  }

  if (!user) {
    return (
      <Card>
        <SectionHeader
          eyebrow="Crear portafolio"
          title="Inicia sesión para crear portafolios"
          description="Necesitas una cuenta para seleccionar y guardar hasta 5 portafolios. Usa el botón 'Iniciar sesión' arriba a la derecha."
        />
      </Card>
    );
  }

  const atMax = drafts.length >= MAX_PORTFOLIOS;

  return (
    <Card>
      <SectionHeader
        eyebrow="Crear portafolio"
        title="Tus portafolios"
        description="Selecciona hasta 5 portafolios para seguir. Para cada uno elige los activos a incluir."
        right={
          <button
            type="button"
            onClick={addPortfolio}
            disabled={atMax}
            className="rounded-[8px] bg-[var(--color-cyan)] px-3 py-1.5 text-[12px] font-semibold text-black hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
            title={atMax ? "Máximo 5 portafolios" : "Añadir portafolio"}
          >
            + Añadir ({drafts.length}/{MAX_PORTFOLIOS})
          </button>
        }
      />

      {error && (
        <div className="mb-4 rounded-[10px] border border-[var(--color-red)] bg-[color-mix(in_srgb,var(--color-red)_10%,transparent)] px-3 py-2 text-[12px] text-[var(--color-red)]">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-[12.5px] text-[var(--color-text3)]">Cargando portafolios…</div>
      ) : drafts.length === 0 ? (
        <EmptyState
          title="Sin portafolios todavía"
          description="Pulsa '+ Añadir' para crear tu primer portafolio (hasta 5)."
        />
      ) : (
        <div className="space-y-3">
          {drafts
            .slice()
            .sort((a, b) => a.slot - b.slot)
            .map((d) => (
              <div
                key={d.slot}
                className="rounded-[12px] bg-[var(--color-bg3)] p-4"
              >
                <div className="mb-3 flex items-center gap-3">
                  <Badge tone="neutral">Slot {d.slot}</Badge>
                  <input
                    value={d.name}
                    onChange={(e) => patchDraft(d.slot, { name: e.target.value })}
                    placeholder="Nombre del portafolio"
                    className="flex-1 rounded-[8px] bg-[var(--color-bg)] px-3 py-1.5 text-[13px] font-medium text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
                  />
                  {!d.id && <Badge tone="amber">sin guardar</Badge>}
                </div>

                <div className="mb-2 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  Activos ({d.assets.length})
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {AVAILABLE_ASSETS.map((t) => {
                    const on = d.assets.includes(t);
                    return (
                      <button
                        key={t}
                        type="button"
                        onClick={() => toggleAsset(d.slot, t)}
                        className={`rounded-[6px] px-2 py-1 font-mono text-[11.5px] transition-colors ${
                          on
                            ? "bg-[var(--color-cyan)] text-black"
                            : "bg-[var(--color-bg4)] text-[var(--color-text2)] hover:text-[var(--color-text)]"
                        }`}
                      >
                        {t}
                      </button>
                    );
                  })}
                  {/* Custom tickers already chosen but outside the standard set */}
                  {d.assets
                    .filter((t) => !AVAILABLE_ASSETS.includes(t))
                    .map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => toggleAsset(d.slot, t)}
                        className="rounded-[6px] bg-[var(--color-violet)] px-2 py-1 font-mono text-[11.5px] text-black"
                        title="Quitar"
                      >
                        {t} ×
                      </button>
                    ))}
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <input
                    value={customTicker[d.slot] ?? ""}
                    onChange={(e) =>
                      setCustomTicker((c) => ({ ...c, [d.slot]: e.target.value }))
                    }
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addCustom(d.slot);
                      }
                    }}
                    placeholder="Ticker personalizado (p. ej. AAPL)"
                    className="w-56 rounded-[8px] bg-[var(--color-bg)] px-2.5 py-1.5 font-mono text-[12px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
                  />
                  <button
                    type="button"
                    onClick={() => addCustom(d.slot)}
                    className="rounded-[8px] bg-[var(--color-bg4)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)]"
                  >
                    Añadir activo
                  </button>

                  <div className="ml-auto flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => remove(d)}
                      disabled={savingSlot === d.slot}
                      className="rounded-[8px] px-2.5 py-1.5 text-[12px] font-medium text-[var(--color-red)] hover:bg-[color-mix(in_srgb,var(--color-red)_12%,transparent)] disabled:opacity-50"
                    >
                      Eliminar
                    </button>
                    <button
                      type="button"
                      onClick={() => save(d)}
                      disabled={savingSlot === d.slot}
                      className="rounded-[8px] bg-[var(--color-green)] px-3 py-1.5 text-[12px] font-semibold text-[var(--color-bg)] disabled:opacity-50 active:scale-[0.97]"
                    >
                      {savingSlot === d.slot ? "Guardando…" : "Guardar"}
                    </button>
                  </div>
                </div>
              </div>
            ))}
        </div>
      )}
    </Card>
  );
}
