"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, SectionHeader, Skeleton } from "@/components/primitives";

// ─── Types ────────────────────────────────────────────────────────────────────

type NewsArticle = {
  id: number;
  headline: string;
  summary: string;
  author: string;
  created_at: string;
  url: string;
  source: string;
  symbols: string[];
  images: { url: string; size: string }[];
};

// ─── Constants ────────────────────────────────────────────────────────────────

const OLS_TICKERS = ["AMZN", "BA", "CAT", "CRM", "GOOGL", "NVDA", "QCOM", "V", "XOM"];
const P1_TICKERS  = OLS_TICKERS; // P1 trades the same OLS universe
const P0_TICKERS  = ["MDLZ", "CL", "KHC", "KMB", "HSY", "AAL"];

const FILTERS = [
  { key: "all",    label: "TODAS",              tickers: [...new Set([...OLS_TICKERS, ...P0_TICKERS])] },
  { key: "ols",    label: "OLS BOT",            tickers: OLS_TICKERS },
  { key: "p1",     label: "P1 CONSERVADOR",     tickers: P1_TICKERS },
  { key: "p0",     label: "P0 ULTRA CONS.",     tickers: P0_TICKERS },
];

// ─── Impact detection ─────────────────────────────────────────────────────────

const HIGH_IMPACT_KEYWORDS = [
  "earnings", "resultados", "guidance", "merger", "adquisición", "acquisition",
  "lawsuit", "demanda", "sec", "fda", "bankruptcy", "quiebra",
  "downgrade", "upgrade", "layoffs", "despidos", "ceo", "recall",
  "retirada", "investigation", "investigación", "fraud", "fraude",
  "beats", "misses", "revenue beat", "revenue miss", "profit warning",
  "dividend", "dividendo", "buyback", "recompra", "split",
];

const MED_IMPACT_KEYWORDS = [
  "analyst", "analista", "forecast", "outlook", "perspectivas",
  "quarter", "trimestre", "annual", "anual", "report", "reporte",
  "partnership", "alianza", "contract", "contrato", "deal", "acuerdo",
  "expansion", "expansión", "growth", "crecimiento", "target", "price target",
  "raises", "cuts", "initiate", "reiterate",
];

type Impact = "high" | "medium" | "normal";

function getImpact(headline: string, summary: string): Impact {
  const text = `${headline} ${summary}`.toLowerCase();
  if (HIGH_IMPACT_KEYWORDS.some((kw) => text.includes(kw))) return "high";
  if (MED_IMPACT_KEYWORDS.some((kw) => text.includes(kw)))  return "medium";
  return "normal";
}

const IMPACT_CONFIG: Record<Impact, { label: string; bar: string; badge: string; text: string }> = {
  high:   { label: "ALTO IMPACTO", bar: "border-l-[var(--color-red)]",   badge: "bg-[var(--color-red)] text-white",   text: "text-[var(--color-red)]" },
  medium: { label: "MED. IMPACTO", bar: "border-l-[var(--color-amber)]", badge: "bg-[var(--color-amber)] text-black", text: "text-[var(--color-amber)]" },
  normal: { label: "",             bar: "border-l-[var(--color-border)]", badge: "",                                   text: "" },
};

// ─── Helpers ─────────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60)    return `hace ${diff}s`;
  if (diff < 3600)  return `hace ${Math.floor(diff / 60)}min`;
  if (diff < 86400) return `hace ${Math.floor(diff / 3600)}h`;
  return `hace ${Math.floor(diff / 86400)}d`;
}

function portfolioForTicker(symbol: string): string {
  const parts: string[] = [];
  if (OLS_TICKERS.includes(symbol)) parts.push("OLS", "P1");
  if (P0_TICKERS.includes(symbol))  parts.push("P0");
  return parts.join(" · ");
}

// ─── Article card ─────────────────────────────────────────────────────────────

function ArticleCard({ article }: { article: NewsArticle }) {
  const portfolioSymbols = article.symbols.filter(
    (s) => OLS_TICKERS.includes(s) || P0_TICKERS.includes(s),
  );
  const impact = getImpact(article.headline, article.summary ?? "");
  const cfg    = IMPACT_CONFIG[impact];

  return (
    <div className={`border border-[var(--color-border)] bg-[var(--color-bg2)] p-4 border-l-2 ${cfg.bar} transition-colors hover:bg-[var(--color-bg3)]`}>
      {/* Meta row */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {/* Impact badge */}
        {impact !== "normal" && (
          <span className={`px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest ${cfg.badge}`}>
            {cfg.label}
          </span>
        )}

        {/* Ticker badges */}
        {portfolioSymbols.slice(0, 4).map((sym) => (
          <span
            key={sym}
            className="bg-[var(--color-amber)] px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-black"
          >
            {sym}
          </span>
        ))}
        {portfolioSymbols.length > 4 && (
          <span className="text-[9px] text-[var(--color-text3)]">
            +{portfolioSymbols.length - 4} más
          </span>
        )}

        {/* Portfolio label */}
        {portfolioSymbols.length > 0 && (
          <span className="ml-auto text-[9px] font-semibold uppercase tracking-widest text-[var(--color-text3)]">
            {portfolioForTicker(portfolioSymbols[0])} BOT
          </span>
        )}
      </div>

      {/* Headline */}
      <p className={`mb-1.5 text-[13px] font-semibold leading-snug ${impact === "high" ? cfg.text : "text-[var(--color-text)]"}`}>
        {article.headline}
      </p>

      {/* Summary */}
      {article.summary && article.summary !== article.headline && (
        <p className="mb-3 text-[11px] leading-relaxed text-[var(--color-text2)] line-clamp-2">
          {article.summary}
        </p>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[10px] text-[var(--color-text3)]">
          <span className="font-semibold uppercase">{article.source}</span>
          <span>·</span>
          <span>{timeAgo(article.created_at)}</span>
          {article.author && (
            <>
              <span>·</span>
              <span>{article.author}</span>
            </>
          )}
        </div>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[10px] font-semibold uppercase tracking-wider text-[var(--color-amber)] hover:underline"
        >
          VER →
        </a>
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function NewsPage() {
  const [portfolio, setPortfolio] = useState("all");
  const [articles, setArticles]   = useState<NewsArticle[]>([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchNews = useCallback(async (port: string) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/news?portfolio=${port}&limit=50`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error ?? "Error fetching news");
      setArticles(data.news ?? []);
      setLastUpdated(new Date());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchNews(portfolio);
  }, [portfolio, fetchNews]);

  const handlePortfolioChange = (key: string) => {
    setPortfolio(key);
  };

  const activeFilter = FILTERS.find((f) => f.key === portfolio)!;

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-8">

      {/* Header */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text3)]">
            ALPACA NEWS · EN TIEMPO REAL
          </div>
          <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
            Noticias de tu portafolio
          </h1>
          <p className="mt-1.5 text-[13px] text-[var(--color-text2)]">
            Últimas noticias de las acciones en tus bots activos.
          </p>
        </div>

        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="text-[10px] text-[var(--color-text3)]">
              Act. {lastUpdated.toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          <button
            onClick={() => fetchNews(portfolio)}
            disabled={loading}
            className="border border-[var(--color-amber)] px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--color-amber)] transition hover:bg-[var(--color-amber)] hover:text-black disabled:opacity-40"
          >
            {loading ? "CARGANDO..." : "↺ ACTUALIZAR"}
          </button>
        </div>
      </div>

      {/* Portfolio filter */}
      <div className="mb-6 flex flex-wrap items-center gap-1 border-b border-[var(--color-border)] pb-4">
        <span className="mr-2 text-[10px] font-semibold uppercase tracking-widest text-[var(--color-text3)]">
          Portafolio:
        </span>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => handlePortfolioChange(f.key)}
            aria-pressed={portfolio === f.key}
            className={`px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition-colors border ${
              portfolio === f.key
                ? "bg-[var(--color-amber)] border-[var(--color-amber)] text-black"
                : "border-[var(--color-border2)] text-[var(--color-text2)] hover:border-[var(--color-amber)] hover:text-[var(--color-amber)]"
            }`}
          >
            {f.label}
          </button>
        ))}

        {/* Ticker pills */}
        <div className="ml-4 hidden flex-wrap gap-1 sm:flex">
          {activeFilter.tickers.map((t) => (
            <span
              key={t}
              className="border border-[var(--color-border)] px-1.5 py-0.5 text-[9px] font-mono text-[var(--color-text3)]"
            >
              {t}
            </span>
          ))}
        </div>
      </div>

      {/* Content */}
      {error ? (
        <div className="border-l-2 border-l-[var(--color-red)] bg-[var(--color-bg2)] p-4 text-[13px] text-[var(--color-red)]">
          Error al cargar noticias: {error}
        </div>
      ) : loading ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-[140px]" />
          ))}
        </div>
      ) : articles.length === 0 ? (
        <div className="border border-dashed border-[var(--color-border2)] bg-[var(--color-bg2)] p-12 text-center">
          <div className="text-[13px] font-medium text-[var(--color-text2)]">
            No hay noticias recientes
          </div>
          <div className="mt-1 text-[11px] text-[var(--color-text3)]">
            Intenta actualizar o cambia el filtro de portafolio
          </div>
        </div>
      ) : (
        <>
          {/* Summary counts */}
          <div className="mb-4 flex flex-wrap items-center gap-4 text-[10px] font-semibold uppercase tracking-widest">
            <span className="text-[var(--color-text3)]">{articles.length} ARTÍCULOS · {activeFilter.label}</span>
            {(() => {
              const high = articles.filter(a => getImpact(a.headline, a.summary ?? "") === "high").length;
              const med  = articles.filter(a => getImpact(a.headline, a.summary ?? "") === "medium").length;
              return (
                <>
                  {high > 0 && <span className="text-[var(--color-red)]">{high} ALTO IMPACTO</span>}
                  {med  > 0 && <span className="text-[var(--color-amber)]">{med} MED. IMPACTO</span>}
                </>
              );
            })()}
          </div>

          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {[...articles]
              .sort((a, b) => {
                const order: Record<Impact, number> = { high: 0, medium: 1, normal: 2 };
                const ia = getImpact(a.headline, a.summary ?? "");
                const ib = getImpact(b.headline, b.summary ?? "");
                if (order[ia] !== order[ib]) return order[ia] - order[ib];
                return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
              })
              .map((article) => (
                <ArticleCard key={article.id} article={article} />
              ))}
          </div>
        </>
      )}
    </div>
  );
}
