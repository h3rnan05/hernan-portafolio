/**
 * Accounts — user portfolio accounts, auto-classified into the 5 risk profiles.
 *
 * Each account holds its own set of ticker positions. After each holdings
 * update the backend computes the nearest risk profile (P1 conservative →
 * P5 aggressive) using L2 distance on portfolio weights.
 */

import { getLocale, getTranslations } from "next-intl/server";

import { CreatePortfolio } from "@/components/create-portfolio";
import {
  Badge,
  Card,
  EmptyState,
  fmtDate,
  fmtNumber,
  fmtPct,
  SectionHeader,
} from "@/components/primitives";
import { api, ApiError } from "@/lib/api";
import { NewAccountButton } from "./new-account-button";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const PROFILE_TONES: Record<string, "neutral" | "green" | "amber" | "red" | "cyan"> = {
  P1_CONSERVATIVE: "cyan",
  P2_MOD_CONSERVATIVE: "green",
  P3_BALANCED: "neutral",
  P4_MOD_AGGRESSIVE: "amber",
  P5_AGGRESSIVE: "red",
};

function profileTone(profileId: string | null) {
  if (!profileId) return "neutral" as const;
  for (const [key, tone] of Object.entries(PROFILE_TONES)) {
    if (profileId.includes(key) || profileId === key) return tone;
  }
  return "neutral" as const;
}

export default async function AccountsPage() {
  const t = await getTranslations("accounts");
  const tc = await getTranslations("common");
  const locale = await getLocale();
  let accounts: Awaited<ReturnType<typeof api.listAccounts>> = [];
  let error: string | null = null;
  try {
    accounts = await api.listAccounts();
  } catch (e) {
    error = e instanceof ApiError ? e.message : String(e);
  }

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8 flex items-start justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {t("eyebrow", {
              count: accounts.length,
              accounts: accounts.length === 1 ? t("account_one") : t("account_other"),
            })}
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            {t("subtitle")}
          </p>
        </div>
        <NewAccountButton />
      </div>

      {/* Crear portafolio — registered users track up to 5 portfolios (HER task 5) */}
      <div className="mb-8">
        <CreatePortfolio />
      </div>

      {error ? (
        <Card>
          <div className="text-[13px] text-[var(--color-red)]">{error}</div>
        </Card>
      ) : accounts.length === 0 ? (
        <EmptyState title={t("empty_title")} description={t("empty_desc")} />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {accounts.map((a) => (
            <Card key={a.id}>
              <div className="mb-4 flex items-start justify-between gap-3">
                <div>
                  <div className="text-[15px] font-semibold">{a.name}</div>
                  {a.description && (
                    <div className="mt-0.5 text-[12px] text-[var(--color-text3)]">
                      {a.description}
                    </div>
                  )}
                  <div className="mt-1 text-[11px] text-[var(--color-text3)]">
                    {t("created_positions", {
                      date: fmtDate(a.created_at, locale),
                      count: a.holdings.length,
                      positions:
                        a.holdings.length === 1
                          ? t("position_one")
                          : t("position_other"),
                    })}
                  </div>
                </div>
                {a.profile_match ? (
                  <div className="flex flex-col items-end gap-1">
                    <Badge tone={profileTone(a.profile_match.profile_id)}>
                      {a.profile_match.profile_name}
                    </Badge>
                    <span className="text-[10px] text-[var(--color-text3)]">
                      {t("distance", {
                        value: a.profile_match.distance.toFixed(3),
                      })}
                    </span>
                  </div>
                ) : (
                  <Badge tone="neutral">{t("unclassified")}</Badge>
                )}
              </div>

              {a.total_market_value !== null && (
                <div className="mb-4 font-mono text-xl font-semibold">
                  ${fmtNumber(a.total_market_value, { decimals: 2 })}
                  <span className="ml-2 text-[12px] font-normal text-[var(--color-text3)]">
                    {t("market_value_label")}
                  </span>
                </div>
              )}

              {a.holdings.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                        <th className="py-1.5 pr-3 font-medium">{tc("ticker")}</th>
                        <th className="py-1.5 pr-3 text-right font-medium">{tc("qty")}</th>
                        <th className="py-1.5 pr-3 text-right font-medium">{tc("last")}</th>
                        <th className="py-1.5 text-right font-medium">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {a.holdings.map((h) => (
                        <tr
                          key={h.ticker}
                          className="border-b border-[var(--color-border)] last:border-0"
                        >
                          <td className="py-1.5 pr-3 font-mono font-medium">{h.ticker}</td>
                          <td className="py-1.5 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                            {fmtNumber(h.quantity, { decimals: 0 })}
                          </td>
                          <td className="py-1.5 pr-3 text-right font-mono tabular">
                            {h.last_price !== null
                              ? `$${fmtNumber(h.last_price, { decimals: 2 })}`
                              : <span className="text-[var(--color-text3)]">—</span>}
                          </td>
                          <td className="py-1.5 text-right">
                            {h.open_pnl_pct !== null ? (
                              <Badge
                                tone={h.open_pnl_pct >= 0 ? "green" : "red"}
                              >
                                {fmtPct(h.open_pnl_pct, { signed: true, decimals: 2 })}
                              </Badge>
                            ) : (
                              <span className="text-[var(--color-text3)]">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
