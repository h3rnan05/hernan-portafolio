/**
 * Positions — live Capital.com snapshot reconciled against predictions.
 */

import {
  Badge,
  Card,
  EmptyState,
  fmtNumber,
  fmtPct,
  fmtRelative,
} from "@/components/primitives";
import { api, ApiError, type Position } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function PositionsPage() {
  let positions: Position[] = [];
  let error: string | null = null;
  try {
    positions = await api.listPositions();
  } catch (e) {
    error = e instanceof ApiError ? e.message : String(e);
  }

  const totalMv = positions.reduce((s, p) => s + p.market_value, 0);
  const totalPnl = positions.reduce((s, p) => s + p.open_pnl, 0);
  const lastSnap = positions.map((p) => p.snapshot_at).sort().pop();

  return (
    <div className="mx-auto max-w-7xl px-6 py-8">
      <div className="mb-8 flex items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            Positions · Capital.com demo
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Live positions
          </h1>
          <p className="mt-1.5 max-w-2xl text-[13px] text-[var(--color-text2)]">
            Most recent snapshot from the broker. Refreshed by the daily cron
            and on-demand from the admin endpoint.
          </p>
        </div>
        {lastSnap && (
          <span className="rounded-[6px] bg-[var(--color-bg3)] px-2.5 py-1 text-[11px] text-[var(--color-text2)]">
            Last snapshot {fmtRelative(lastSnap)}
          </span>
        )}
      </div>

      {error ? (
        <Card>
          <div className="text-[13px] text-[var(--color-red)]">{error}</div>
        </Card>
      ) : positions.length === 0 ? (
        <EmptyState
          title="No positions snapshot yet"
          description="Once Capital.com demo creds are in env + `scripts/snapshot_positions.py` runs (cron or manual), open positions show here."
        />
      ) : (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3">
            <SummaryTile label="Open positions" value={String(positions.length)} />
            <SummaryTile
              label="Market value"
              value={`$${fmtNumber(totalMv, { decimals: 2 })}`}
            />
            <SummaryTile
              label="Unrealized P&L"
              value={`$${fmtNumber(totalPnl, { decimals: 2 })}`}
              tone={totalPnl >= 0 ? "green" : "red"}
            />
          </div>

          <Card>
            <div className="overflow-x-auto">
              <table className="w-full text-[12.5px]">
                <thead>
                  <tr className="border-b border-[var(--color-border)] text-left text-[10px] uppercase tracking-widest text-[var(--color-text3)]">
                    <th className="py-2 pr-3 font-medium">Ticker</th>
                    <th className="py-2 pr-3 text-right font-medium">Qty</th>
                    <th className="py-2 pr-3 text-right font-medium">Avg</th>
                    <th className="py-2 pr-3 text-right font-medium">Last</th>
                    <th className="py-2 pr-3 text-right font-medium">Mkt Value</th>
                    <th className="py-2 pr-3 text-right font-medium">P&L</th>
                    <th className="py-2 pr-3 text-right font-medium">P&L %</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr
                      key={`${p.ticker}-${p.snapshot_at}`}
                      className="border-b border-[var(--color-border)] last:border-0"
                    >
                      <td className="py-2 pr-3 font-mono font-medium">
                        {p.ticker}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono tabular">
                        {fmtNumber(p.quantity, { decimals: 0 })}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono tabular text-[var(--color-text2)]">
                        ${fmtNumber(p.avg_price, { decimals: 2 })}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono tabular">
                        ${fmtNumber(p.last_price, { decimals: 2 })}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono tabular">
                        ${fmtNumber(p.market_value, { decimals: 2 })}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono tabular">
                        ${fmtNumber(p.open_pnl, { decimals: 2 })}
                      </td>
                      <td className="py-2 pr-3 text-right">
                        <Badge tone={p.open_pnl_pct >= 0 ? "green" : "red"}>
                          {fmtPct(p.open_pnl_pct, { signed: true, decimals: 2 })}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function SummaryTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "green" | "red";
}) {
  const valueColor =
    tone === "green"
      ? "text-[var(--color-green)]"
      : tone === "red"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-text)]";
  return (
    <div className="rounded-[10px] bg-[var(--color-bg3)] p-4">
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
        {label}
      </div>
      <div className={"font-mono tabular text-xl font-semibold " + valueColor}>
        {value}
      </div>
    </div>
  );
}
