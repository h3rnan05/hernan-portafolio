"use client";

/**
 * Holdings — the user's editable portfolio.
 *
 * Distinct from /positions which is the live broker mirror. Here the user
 * manually enters their positions (qty + avg cost) and we enrich each row
 * with the latest market price from observations + computed P&L.
 *
 * Admin-token-gated for writes. Token is prompted once and cached in
 * localStorage via useAdminToken.
 */

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

import {
  Badge,
  Card,
  EmptyState,
  fmtNumber,
  fmtPct,
  fmtRelative,
  SectionHeader,
} from "@/components/primitives";
import { PredictorsPanel } from "@/components/predictors-panel";
import { StocksGrid } from "@/components/stocks-grid";
import { useAdminToken } from "@/hooks/use-admin-token";
import {
  api,
  ApiError,
  type Holding,
  type HoldingInput,
  type HoldingsResponse,
} from "@/lib/api";

const SUPPORTED_TICKERS = [
  "NVDA",
  "XOM",
  "CAT",
  "AMZN",
  "CRM",
  "QCOM",
  "BA",
  "V",
  "GOOGL",
];

export default function HoldingsPage() {
  const t = useTranslations("holdings");
  const tc = useTranslations("common");
  const { ensure: ensureToken, clear: clearToken } = useAdminToken();
  const [data, setData] = useState<HoldingsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // New-row form
  const [newTicker, setNewTicker] = useState("NVDA");
  const [newQty, setNewQty] = useState("");
  const [newAvg, setNewAvg] = useState("");
  const [newNotes, setNewNotes] = useState("");

  // Bulk import textarea
  const [bulkText, setBulkText] = useState("");
  const [bulkOpen, setBulkOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.listHoldings();
      setData(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const token = ensureToken();
    if (!token) return;
    const qty = parseFloat(newQty);
    const avg = parseFloat(newAvg);
    if (Number.isNaN(qty) || Number.isNaN(avg)) {
      setError(t("err_numbers"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.createHolding(
        {
          ticker: newTicker.trim().toUpperCase(),
          quantity: qty,
          avg_price: avg,
          notes: newNotes.trim() || null,
        },
        token,
      );
      setNewQty("");
      setNewAvg("");
      setNewNotes("");
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handlePatch = async (
    ticker: string,
    patch: Partial<HoldingInput>,
  ) => {
    const token = ensureToken();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await api.updateHolding(ticker, patch, token);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (ticker: string) => {
    if (!window.confirm(t("delete_confirm", { ticker }))) return;
    const token = ensureToken();
    if (!token) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteHolding(ticker, token);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleBulk = async () => {
    const token = ensureToken();
    if (!token) return;
    const rows = parseBulkText(bulkText);
    if (rows.length === 0) {
      setError(t("err_bulk_parse"));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.bulkUpsertHoldings(rows, token);
      setBulkText("");
      setBulkOpen(false);
      await refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {t("eyebrow")}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            {t("subtitle")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setBulkOpen((b) => !b)}
            className="rounded-[6px] bg-[var(--color-bg3)] px-3 py-1.5 text-[12px] font-medium text-[var(--color-text2)] hover:text-[var(--color-text)] active:scale-[0.97]"
          >
            {bulkOpen ? t("cancel_bulk") : t("bulk_import")}
          </button>
          <button
            type="button"
            onClick={clearToken}
            className="rounded-[6px] bg-[var(--color-bg3)] px-3 py-1.5 text-[11px] text-[var(--color-text3)] hover:text-[var(--color-text2)]"
            title={t("clear_token_title")}
          >
            {t("clear_token")}
          </button>
        </div>
      </div>

      {/* Summary */}
      {data && data.summary.n > 0 && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          <SummaryTile label={t("summary_positions")} value={String(data.summary.n)} />
          <SummaryTile
            label={t("summary_cost_basis")}
            value={`$${fmtNumber(data.summary.cost_basis, { decimals: 2 })}`}
          />
          <SummaryTile
            label={t("summary_market_value")}
            value={`$${fmtNumber(data.summary.market_value, { decimals: 2 })}`}
          />
          <SummaryTile
            label={t("summary_unrealized_pnl")}
            value={`$${fmtNumber(data.summary.open_pnl, { decimals: 2 })}`}
            delta={
              data.summary.open_pnl_pct !== null
                ? {
                    value: fmtPct(data.summary.open_pnl_pct, {
                      signed: true,
                      decimals: 2,
                    }),
                    tone: data.summary.open_pnl_pct >= 0 ? "green" : "red",
                  }
                : undefined
            }
          />
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-[10px] border border-[var(--color-red)] bg-[color-mix(in_srgb,var(--color-red)_10%,transparent)] px-4 py-3 text-[12.5px] text-[var(--color-red)]">
          {error}
        </div>
      )}

      {/* Bulk import */}
      {bulkOpen && (
        <Card className="mb-6">
          <SectionHeader
            eyebrow={t("bulk_eyebrow")}
            title={t("bulk_title")}
            description={t("bulk_desc")}
          />
          <textarea
            rows={6}
            value={bulkText}
            onChange={(e) => setBulkText(e.target.value)}
            placeholder={"NVDA, 10, 150.50\nAMZN, 5, 175.00\nXOM, 20, 110.25, dividend play"}
            className="w-full rounded-[8px] bg-[var(--color-bg3)] p-3 font-mono text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text3)] focus:outline-none"
            spellCheck={false}
          />
          <div className="mt-3 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={handleBulk}
              disabled={busy}
              className="rounded-[8px] bg-[var(--color-cyan)] px-3 py-1.5 text-[12px] font-semibold text-[var(--color-bg)] disabled:opacity-50 active:scale-[0.97]"
            >
              {busy ? t("importing") : t("import")}
            </button>
          </div>
        </Card>
      )}

      {/* Holdings table */}
      <Card className="mb-6 overflow-hidden p-0">
        {loading && !data ? (
          <div className="space-y-2 p-4" aria-hidden>
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-8 w-full animate-pulse rounded-[8px] bg-[var(--color-bg3)]"
              />
            ))}
          </div>
        ) : !data || data.holdings.length === 0 ? (
          <div className="p-8">
            <EmptyState
              title={t("empty_title")}
              description={t("empty_desc")}
            />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg3)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                  <th className="px-4 py-2.5 font-medium">{tc("ticker")}</th>
                  <th className="px-4 py-2.5 text-right font-medium">{tc("qty")}</th>
                  <th className="px-4 py-2.5 text-right font-medium">{t("th_avg")}</th>
                  <th className="px-4 py-2.5 text-right font-medium">{tc("last")}</th>
                  <th className="px-4 py-2.5 text-right font-medium">{t("th_mkt_value")}</th>
                  <th className="px-4 py-2.5 text-right font-medium">P&L</th>
                  <th className="px-4 py-2.5 text-right font-medium">P&L %</th>
                  <th className="px-4 py-2.5 font-medium">{tc("notes")}</th>
                  <th className="px-4 py-2.5 text-right font-medium">{tc("updated")}</th>
                  <th className="px-4 py-2.5 w-12"></th>
                </tr>
              </thead>
              <tbody>
                {data.holdings.map((h) => (
                  <HoldingRow
                    key={h.id}
                    holding={h}
                    onPatch={handlePatch}
                    onDelete={handleDelete}
                    busy={busy}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Add-new form */}
      <Card>
        <SectionHeader
          eyebrow={t("add_eyebrow")}
          title={t("add_title")}
          description={t("add_desc")}
        />
        <form
          onSubmit={handleAdd}
          className="grid grid-cols-2 gap-3 md:grid-cols-[120px_120px_120px_1fr_120px]"
        >
          <label className="flex flex-col gap-1 text-[11px] text-[var(--color-text3)]">
            {t("field_ticker")}
            <select
              value={newTicker}
              onChange={(e) => setNewTicker(e.target.value)}
              className="h-9 rounded-[8px] bg-[var(--color-bg3)] px-2 text-[13px] text-[var(--color-text)] font-mono focus:outline-none"
            >
              {SUPPORTED_TICKERS.map((tk) => (
                <option key={tk} value={tk}>
                  {tk}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-[var(--color-text3)]">
            {t("field_quantity")}
            <input
              type="number"
              step="any"
              min="0"
              value={newQty}
              onChange={(e) => setNewQty(e.target.value)}
              required
              className="h-9 rounded-[8px] bg-[var(--color-bg3)] px-2 text-[13px] text-[var(--color-text)] font-mono focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-[var(--color-text3)]">
            {t("field_avg_price")}
            <input
              type="number"
              step="any"
              min="0"
              value={newAvg}
              onChange={(e) => setNewAvg(e.target.value)}
              required
              className="h-9 rounded-[8px] bg-[var(--color-bg3)] px-2 text-[13px] text-[var(--color-text)] font-mono focus:outline-none"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-[var(--color-text3)]">
            {t("field_notes")}
            <input
              type="text"
              value={newNotes}
              onChange={(e) => setNewNotes(e.target.value)}
              className="h-9 rounded-[8px] bg-[var(--color-bg3)] px-2 text-[13px] text-[var(--color-text)] focus:outline-none"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="h-9 self-end rounded-[8px] bg-[var(--color-green)] px-3 text-[12px] font-semibold text-[var(--color-bg)] disabled:opacity-50 active:scale-[0.97]"
          >
            {busy ? tc("saving") : t("add_button")}
          </button>
        </form>
      </Card>

      {/* Stocks — the 9 tracked names (moved here from Overview) */}
      <section className="mt-10">
        <SectionHeader
          eyebrow={t("stocks_eyebrow")}
          title={t("stocks_title")}
          description={t("stocks_desc")}
        />
        <StocksGrid />
      </section>

      {/* Predictors — coverage status, balanced categories (moved from Overview) */}
      <section className="mt-10">
        <SectionHeader
          eyebrow={t("predictors_eyebrow")}
          title={t("predictors_title")}
          description={t("predictors_desc")}
        />
        <PredictorsPanel />
      </section>
    </div>
  );
}

// ─── Sub-components ─────────────────────────────────────────────────────────

function HoldingRow({
  holding,
  onPatch,
  onDelete,
  busy,
}: {
  holding: Holding;
  onPatch: (ticker: string, patch: Partial<HoldingInput>) => void | Promise<void>;
  onDelete: (ticker: string) => void | Promise<void>;
  busy: boolean;
}) {
  const tc = useTranslations("common");
  const [editing, setEditing] = useState(false);
  const [qty, setQty] = useState(holding.quantity.toString());
  const [avg, setAvg] = useState(holding.avg_price.toString());

  const save = () => {
    const q = parseFloat(qty);
    const a = parseFloat(avg);
    if (Number.isNaN(q) || Number.isNaN(a)) return;
    onPatch(holding.ticker, { quantity: q, avg_price: a });
    setEditing(false);
  };

  return (
    <tr className="border-b border-[var(--color-border)] last:border-0 hover:bg-[var(--color-bg3)]/40">
      <td className="px-4 py-2 font-mono font-semibold">{holding.ticker}</td>
      <td className="px-4 py-2 text-right font-mono tabular">
        {editing ? (
          <input
            value={qty}
            onChange={(e) => setQty(e.target.value)}
            className="h-6 w-20 rounded-[4px] bg-[var(--color-bg)] px-1.5 text-right text-[12px] font-mono focus:outline-none"
            type="number"
            step="any"
            min="0"
          />
        ) : (
          fmtNumber(holding.quantity, { decimals: holding.quantity < 1 ? 4 : 0 })
        )}
      </td>
      <td className="px-4 py-2 text-right font-mono tabular text-[var(--color-text2)]">
        {editing ? (
          <input
            value={avg}
            onChange={(e) => setAvg(e.target.value)}
            className="h-6 w-20 rounded-[4px] bg-[var(--color-bg)] px-1.5 text-right text-[12px] font-mono focus:outline-none"
            type="number"
            step="any"
            min="0"
          />
        ) : (
          `$${fmtNumber(holding.avg_price, { decimals: 2 })}`
        )}
      </td>
      <td className="px-4 py-2 text-right font-mono tabular">
        {holding.last_price !== null
          ? `$${fmtNumber(holding.last_price, { decimals: 2 })}`
          : "—"}
      </td>
      <td className="px-4 py-2 text-right font-mono tabular">
        {holding.market_value !== null
          ? `$${fmtNumber(holding.market_value, { decimals: 2 })}`
          : "—"}
      </td>
      <td className="px-4 py-2 text-right font-mono tabular">
        {holding.open_pnl !== null
          ? `$${fmtNumber(holding.open_pnl, { decimals: 2 })}`
          : "—"}
      </td>
      <td className="px-4 py-2 text-right">
        {holding.open_pnl_pct !== null ? (
          <Badge tone={holding.open_pnl_pct >= 0 ? "green" : "red"}>
            {fmtPct(holding.open_pnl_pct, { signed: true, decimals: 2 })}
          </Badge>
        ) : (
          "—"
        )}
      </td>
      <td className="max-w-[200px] truncate px-4 py-2 text-[var(--color-text3)]">
        {holding.notes ?? ""}
      </td>
      <td className="px-4 py-2 text-right text-[11px] text-[var(--color-text3)]">
        {fmtRelative(holding.updated_at)}
      </td>
      <td className="px-4 py-2">
        <div className="flex items-center justify-end gap-1">
          {editing ? (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={save}
                className="rounded-[4px] bg-[var(--color-green)] px-2 py-0.5 text-[10.5px] font-semibold text-[var(--color-bg)] active:scale-[0.96]"
              >
                {tc("save")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditing(false);
                  setQty(holding.quantity.toString());
                  setAvg(holding.avg_price.toString());
                }}
                className="rounded-[4px] bg-[var(--color-bg4)] px-2 py-0.5 text-[10.5px] text-[var(--color-text2)]"
              >
                ×
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setEditing(true)}
                className="rounded-[4px] px-2 py-0.5 text-[10.5px] text-[var(--color-text2)] hover:text-[var(--color-text)]"
              >
                {tc("edit")}
              </button>
              <button
                type="button"
                onClick={() => onDelete(holding.ticker)}
                disabled={busy}
                className="rounded-[4px] px-2 py-0.5 text-[10.5px] text-[var(--color-red)] hover:bg-[color-mix(in_srgb,var(--color-red)_15%,transparent)]"
              >
                {tc("delete")}
              </button>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

function SummaryTile({
  label,
  value,
  delta,
}: {
  label: string;
  value: string;
  delta?: { value: string; tone: "green" | "red" };
}) {
  return (
    <div className="rounded-[10px] bg-[var(--color-bg3)] p-4">
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
        {label}
      </div>
      <div className="flex items-baseline gap-2 font-mono tabular">
        <div className="text-xl font-semibold leading-none">{value}</div>
        {delta && <Badge tone={delta.tone}>{delta.value}</Badge>}
      </div>
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function parseBulkText(raw: string): HoldingInput[] {
  const out: HoldingInput[] = [];
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const parts = trimmed.split(/\s*,\s*/);
    if (parts.length < 3) continue;
    const [ticker, qtyStr, avgStr, ...rest] = parts;
    const qty = parseFloat(qtyStr);
    const avg = parseFloat(avgStr);
    if (Number.isNaN(qty) || Number.isNaN(avg)) continue;
    if (!SUPPORTED_TICKERS.includes(ticker.toUpperCase())) continue;
    out.push({
      ticker: ticker.toUpperCase(),
      quantity: qty,
      avg_price: avg,
      notes: rest.join(", ") || null,
    });
  }
  return out;
}
