"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { api } from "@/lib/api";

type HoldingRow = { ticker: string; quantity: string; avg_price: string };

export function NewAccountButton() {
  const t = useTranslations("accounts");
  const tc = useTranslations("common");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [holdings, setHoldings] = useState<HoldingRow[]>([
    { ticker: "", quantity: "", avg_price: "" },
  ]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function addRow() {
    setHoldings((h) => [...h, { ticker: "", quantity: "", avg_price: "" }]);
  }

  function removeRow(i: number) {
    setHoldings((h) => h.filter((_, idx) => idx !== i));
  }

  function updateRow(i: number, field: keyof HoldingRow, value: string) {
    setHoldings((h) => h.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError(t("err_name_required"));
      return;
    }
    const validHoldings = holdings.filter(
      (h) => h.ticker.trim() && h.quantity && h.avg_price,
    );
    setLoading(true);
    try {
      await api.createAccount({
        name: name.trim(),
        description: description.trim() || null,
        holdings: validHoldings.map((h) => ({
          ticker: h.ticker.trim().toUpperCase(),
          quantity: parseFloat(h.quantity),
          avg_price: parseFloat(h.avg_price),
        })),
      });
      setOpen(false);
      setName("");
      setDescription("");
      setHoldings([{ ticker: "", quantity: "", avg_price: "" }]);
      router.refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("err_create"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded-[8px] bg-[var(--color-bg3)] px-3 py-2 text-[12px] font-medium text-[var(--color-text)] hover:bg-[var(--color-bg4)]"
      >
        {t("new_account")}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-lg rounded-[16px] bg-[var(--color-bg2)] p-6 shadow-xl">
            <h2 className="mb-4 text-[15px] font-semibold">{t("modal_title")}</h2>
            <form onSubmit={submit} className="space-y-4">
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  {t("field_name_required")}
                </label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder={t("field_name_placeholder")}
                  className="w-full rounded-[8px] bg-[var(--color-bg3)] px-3 py-2 text-[13px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                  {t("field_description")}
                </label>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder={t("field_description_placeholder")}
                  className="w-full rounded-[8px] bg-[var(--color-bg3)] px-3 py-2 text-[13px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
                />
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between">
                  <label className="text-[11px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
                    {t("holdings_label")}
                  </label>
                  <button
                    type="button"
                    onClick={addRow}
                    className="text-[11px] text-[var(--color-cyan)] hover:underline"
                  >
                    {t("add_row")}
                  </button>
                </div>
                <div className="space-y-2">
                  <div className="grid grid-cols-[2fr_1fr_1fr_auto] gap-2 text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                    <span>{tc("ticker")}</span>
                    <span>{tc("qty")}</span>
                    <span>{t("field_avg_price")}</span>
                    <span />
                  </div>
                  {holdings.map((h, i) => (
                    <div key={i} className="grid grid-cols-[2fr_1fr_1fr_auto] gap-2">
                      <input
                        type="text"
                        value={h.ticker}
                        onChange={(e) => updateRow(i, "ticker", e.target.value)}
                        placeholder="AAPL"
                        className="rounded-[6px] bg-[var(--color-bg3)] px-2 py-1.5 font-mono text-[12px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
                      />
                      <input
                        type="number"
                        min="0"
                        step="any"
                        value={h.quantity}
                        onChange={(e) => updateRow(i, "quantity", e.target.value)}
                        placeholder="100"
                        className="rounded-[6px] bg-[var(--color-bg3)] px-2 py-1.5 text-[12px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
                      />
                      <input
                        type="number"
                        min="0"
                        step="any"
                        value={h.avg_price}
                        onChange={(e) => updateRow(i, "avg_price", e.target.value)}
                        placeholder="150.00"
                        className="rounded-[6px] bg-[var(--color-bg3)] px-2 py-1.5 text-[12px] text-[var(--color-text)] outline-none focus:ring-1 focus:ring-[var(--color-cyan)]"
                      />
                      <button
                        type="button"
                        onClick={() => removeRow(i)}
                        className="px-1 text-[var(--color-text3)] hover:text-[var(--color-red)]"
                        aria-label={t("remove_row")}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              {error && (
                <div className="text-[12px] text-[var(--color-red)]">{error}</div>
              )}

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="rounded-[8px] px-3 py-2 text-[12px] font-medium text-[var(--color-text3)] hover:text-[var(--color-text)]"
                >
                  {tc("cancel")}
                </button>
                <button
                  type="submit"
                  disabled={loading}
                  className="rounded-[8px] bg-[var(--color-cyan)] px-4 py-2 text-[12px] font-medium text-black hover:opacity-90 disabled:opacity-50"
                >
                  {loading ? t("creating") : t("create")}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
