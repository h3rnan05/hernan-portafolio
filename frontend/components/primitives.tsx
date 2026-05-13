/**
 * Atomic primitives — Card, Badge, StatTile, SectionHeader, EmptyState.
 *
 * All composable, all dark-theme by default, no UI library — just Tailwind
 * + design tokens. Concentric border radius is enforced by convention:
 *   * Card outer radius = lg (14px)
 *   * Inner Tile radius  = md (10px)  → outer - padding(4)
 *   * Inner Badge radius = sm (6px)
 */

import type { ReactNode } from "react";

// ─── Card ────────────────────────────────────────────────────────────────────

export function Card({
  children,
  className = "",
  as: As = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article";
}) {
  return (
    <As
      className={
        "rounded-[14px] bg-[var(--color-bg2)] p-5 " +
        "shadow-[0_0_0_1px_rgba(255,255,255,0.04),0_1px_2px_rgba(0,0,0,0.3)] " +
        className
      }
    >
      {children}
    </As>
  );
}

// ─── SectionHeader ───────────────────────────────────────────────────────────

export function SectionHeader({
  eyebrow,
  title,
  description,
  right,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-end justify-between gap-4">
      <div>
        {eyebrow && (
          <div className="mb-1 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
            {eyebrow}
          </div>
        )}
        <h2 className="text-lg font-semibold tracking-tight">{title}</h2>
        {description && (
          <p className="mt-1 max-w-2xl text-[13px] text-[var(--color-text2)]">
            {description}
          </p>
        )}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}

// ─── Badge ───────────────────────────────────────────────────────────────────

type BadgeTone =
  | "neutral"
  | "green"
  | "red"
  | "amber"
  | "blue"
  | "cyan"
  | "violet";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "bg-[var(--color-bg4)] text-[var(--color-text2)]",
  green:
    "bg-[color-mix(in_srgb,var(--color-green)_15%,transparent)] text-[var(--color-green)]",
  red: "bg-[color-mix(in_srgb,var(--color-red)_15%,transparent)] text-[var(--color-red)]",
  amber:
    "bg-[color-mix(in_srgb,var(--color-amber)_15%,transparent)] text-[var(--color-amber)]",
  blue:
    "bg-[color-mix(in_srgb,var(--color-blue)_15%,transparent)] text-[var(--color-blue)]",
  cyan:
    "bg-[color-mix(in_srgb,var(--color-cyan)_15%,transparent)] text-[var(--color-cyan)]",
  violet:
    "bg-[color-mix(in_srgb,var(--color-violet)_15%,transparent)] text-[var(--color-violet)]",
};

export function Badge({
  children,
  tone = "neutral",
  className = "",
}: {
  children: ReactNode;
  tone?: BadgeTone;
  className?: string;
}) {
  return (
    <span
      className={
        "inline-flex items-center rounded-[6px] px-2 py-0.5 text-[10.5px] " +
        "font-medium uppercase tracking-[0.06em] " +
        BADGE_TONES[tone] +
        " " +
        className
      }
    >
      {children}
    </span>
  );
}

// ─── StatTile ────────────────────────────────────────────────────────────────

export function StatTile({
  label,
  value,
  delta,
  hint,
  tone,
  mono = true,
}: {
  label: string;
  value: ReactNode;
  delta?: { value: string; tone: BadgeTone };
  hint?: string;
  tone?: "default" | "muted";
  mono?: boolean;
}) {
  return (
    <div
      className={`rounded-[10px] p-4 ${
        tone === "muted" ? "bg-[var(--color-bg3)]" : "bg-[var(--color-bg3)]"
      }`}
    >
      <div className="mb-1.5 text-[10px] font-medium uppercase tracking-widest text-[var(--color-text3)]">
        {label}
      </div>
      <div
        className={`flex items-baseline gap-2 ${mono ? "font-mono" : ""} tabular`}
      >
        <div className="text-xl font-semibold leading-none text-[var(--color-text)]">
          {value}
        </div>
        {delta && <Badge tone={delta.tone}>{delta.value}</Badge>}
      </div>
      {hint && (
        <div className="mt-1.5 text-[11px] text-[var(--color-text3)]">
          {hint}
        </div>
      )}
    </div>
  );
}

// ─── EmptyState ──────────────────────────────────────────────────────────────

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[14px] border border-dashed border-[var(--color-border2)] bg-[var(--color-bg2)] p-12 text-center">
      <div className="text-sm font-medium text-[var(--color-text)]">
        {title}
      </div>
      {description && (
        <p className="mt-1.5 max-w-md text-[13px] text-[var(--color-text2)]">
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// ─── Skeleton ────────────────────────────────────────────────────────────────

export function Skeleton({
  className = "",
}: {
  className?: string;
}) {
  return (
    <div
      className={
        "animate-pulse rounded-[10px] bg-[var(--color-bg3)] " + className
      }
      aria-hidden
    />
  );
}

// ─── Formatters ──────────────────────────────────────────────────────────────

export function fmtNumber(
  n: number | null | undefined,
  opts: { decimals?: number; compact?: boolean } = {},
): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const { decimals = 2, compact = false } = opts;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    notation: compact ? "compact" : "standard",
  }).format(n);
}

export function fmtPct(
  n: number | null | undefined,
  opts: { decimals?: number; signed?: boolean } = {},
): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const { decimals = 2, signed = false } = opts;
  const formatted = new Intl.NumberFormat("en-US", {
    style: "percent",
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    signDisplay: signed ? "always" : "auto",
  }).format(n);
  return formatted;
}

export function fmtDate(d: string | null | undefined): string {
  if (!d) return "—";
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(new Date(d + (d.length === 10 ? "T00:00:00Z" : "")));
}

export function fmtRelative(d: string | null | undefined): string {
  if (!d) return "—";
  const now = Date.now();
  const then = new Date(d).getTime();
  const diffMs = then - now;
  const diffDays = Math.round(diffMs / 86400_000);
  const rtf = new Intl.RelativeTimeFormat("en-US", { numeric: "auto" });
  if (Math.abs(diffDays) < 1) {
    const diffHours = Math.round(diffMs / 3600_000);
    if (Math.abs(diffHours) < 1) {
      return rtf.format(Math.round(diffMs / 60_000), "minute");
    }
    return rtf.format(diffHours, "hour");
  }
  return rtf.format(diffDays, "day");
}
