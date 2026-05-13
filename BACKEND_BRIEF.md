# Portfolio Prediction Engine — Backend Implementation Brief

**For:** Coding agent (Claude Code, Cursor, or equivalent)
**Project owner:** Emilio (Gravity / Wunderwelt LLC)
**Status:** Greenfield backend. Frontend exists as static HTML mockup (`index.html`). Excel prototype exists (`Portfolio1_Prediction_Model.xlsx`) and defines the model semantics.

---

## 0. TL;DR — what you are building

A backend service that:

1. **Ingests** ~35 daily time-series (9 portfolio stocks + 30 macro/market predictors) from ~5 data providers.
2. **Fits** a separate lagged multivariable OLS regression for each portfolio stock, with an enforced *no-overlap* constraint on predictors across stocks, and validates each model against four econometric tests (R² ≥ 0.90, Durbin-Watson ∈ [1.5, 2.5], Breusch-Pagan p > 0.05, max VIF < 10).
3. **Predicts** next-N-day returns/prices for each stock and rolls those into a portfolio-level prediction.
4. **Optimizes** weights across 5 risk profiles (conservative → aggressive).
5. **Reconciles** predictions against (a) realized market prices and (b) the user's live Capital.com positions.
6. **Exposes** all of this via a clean REST API the existing HTML/Next.js frontend will consume.

The Excel file is the source of truth for *model semantics*. The HTML file is the source of truth for *UX*. Neither is the source of truth for *data* — you are replacing the synthetic Excel data with live feeds.

---

## 1. Recommended tech stack

| Layer | Tech | Why |
|---|---|---|
| Backend language | **Python 3.11+** | Mandatory for the model layer — `statsmodels` is the only mature library that gives us OLS + Durbin-Watson + Breusch-Pagan + VIF in one place. Don't try to do this in Node. |
| API framework | **FastAPI** | Async, OpenAPI out of the box, Pydantic validation, easy to deploy. |
| Database | **Postgres via Supabase** | Owner already runs Supabase. Use one project. Time-series fits fine in plain Postgres at this scale (≤ 5M rows). Skip TimescaleDB. |
| Job scheduling | **GitHub Actions cron** *or* **Railway/Fly cron** | One workflow per ingestion job. Don't use Vercel cron — too short timeouts. |
| Caching | Postgres `materialized view` + 5-min HTTP cache headers | No Redis needed at this scale. |
| Frontend | **Next.js 15 (App Router) + Tailwind** | Port the existing `index.html` mockup. Use the same color tokens (`--bg`, `--green`, etc.). |
| Hosting | Backend → **Railway** or **Fly.io** (free tier OK for v1). Frontend → **Vercel** (free). DB → **Supabase** (free tier: 500 MB, plenty for daily time series at this scale). | |
| Secrets | `.env` locally, Railway/Vercel secret stores in production. **Never** commit API keys. |
| **Total infra cost** | **$0/month for v1** (all free tiers). Plan to revisit when daily DB writes exceed ~50k or you need a stable paid data feed. | |

### Project layout

```
portfolio-engine/
├── backend/                        # Python FastAPI service
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                 # FastAPI app + routers
│   │   ├── config.py               # Pydantic Settings (env vars)
│   │   ├── db.py                   # SQLAlchemy / asyncpg
│   │   ├── models/                 # SQLAlchemy ORM
│   │   ├── schemas/                # Pydantic response models
│   │   ├── ingestion/
│   │   │   ├── fred.py             # 13 macro + FX + 2 commodities (free)
│   │   │   ├── stooq.py            # primary intl source (free, no auth)
│   │   │   ├── yfinance_provider.py # fallback (free, fragile)
│   │   │   ├── polygon.py          # US stocks fallback (free tier)
│   │   │   ├── capital_com.py      # live positions
│   │   │   ├── baltic.py           # scraper for BDI
│   │   │   ├── ism_pmi.py          # monthly scraper
│   │   │   └── runner.py           # daily orchestrator + fallback chain
│   │   ├── modeling/
│   │   │   ├── regression.py       # OLS + diagnostics
│   │   │   ├── feature_select.py   # no-overlap selector
│   │   │   └── prediction.py
│   │   ├── portfolio/
│   │   │   ├── optimizer.py        # 5 risk profiles
│   │   │   └── backtest.py
│   │   └── routers/
│   │       ├── predictions.py
│   │       ├── portfolio.py
│   │       ├── models.py
│   │       └── positions.py
│   └── tests/
├── frontend/                       # Next.js
│   ├── package.json
│   ├── app/
│   ├── components/
│   └── lib/api.ts
├── infra/
│   ├── github-actions/             # cron workflows
│   └── supabase/
│       └── migrations/
└── README.md
```

---

## 2. Data sources — the full inventory (free-tier stack)

The Excel file uses **30 predictor variables** plus **9 portfolio stocks**. Here is every single one, with the recommended provider, endpoint, frequency, and a worked example.

> **Cost summary: $0/month for v1.** The full data layer can be built on free providers if you accept the reliability trade-offs and design for them (§2.5). The original draft of this brief recommended Twelve Data ($29/mo) — keep that as the upgrade path if/when free providers get unstable enough that uptime matters more than cost.
>
> - **FRED** — free, no rate limit issues. The reliable backbone.
> - **Stooq** — free CSV downloads, no API key, no auth. Underrated and very stable. Becomes the primary international data source.
> - **yfinance** — free Python wrapper for Yahoo Finance. **Increasingly fragile** in 2024–2025; Yahoo actively rate-limits. Use as fallback only, not primary.
> - **Polygon.io free tier** — 5 req/min, EOD, 2-yr history. Trivially within limits for 9 daily US stock pulls.
> - **Alpha Vantage free tier** — 25 req/day, last-resort fallback.
> - **Capital.com** — free with account.

#### Provider summary card

| Variable group | Count | Primary | Fallback 1 | Fallback 2 |
|---|---|---|---|---|
| US macro indicators | 7 | FRED | — | — |
| FX rates | 4 | FRED | Stooq | — |
| Gold + Brent | 2 | FRED | Stooq | yfinance |
| Wheat + Copper | 2 | Stooq | yfinance | — |
| International indices | 8 | Stooq | yfinance | — |
| International stocks | 4 | Stooq | yfinance | — |
| Baltic Dry Index | 1 | scrape (tradingeconomics) | `BDRY` ETF via Stooq | — |
| ISM Manufacturing PMI | 1 | scrape (ismworld.org, monthly) | scrape (S&P PMI) | — |
| Citi Surprise (substituted) | 1 | FRED `ADSBCISMV` | — | — |
| **Predictors total** | **30** | | | |
| Portfolio stocks (US) | 9 | Stooq | Polygon free | yfinance |
| Live positions | — | Capital.com | — | — |

### 2.1 — The 9 portfolio stocks

**Primary:** Stooq direct CSV. **Fallback 1:** Polygon.io free tier. **Fallback 2:** yfinance.

Stooq URL pattern (no key, no auth):
```
https://stooq.com/q/d/l/?s={ticker}.us&i=d&d1={YYYYMMDD}&d2={YYYYMMDD}
```
Returns a CSV with columns: `Date,Open,High,Low,Close,Volume`. If the response is empty or contains "No data", the symbol is wrong or Stooq is having a bad day — fall through to the next provider.

| Ticker | Stooq symbol | Polygon symbol | yfinance symbol |
|---|---|---|---|
| NVDA | `nvda.us` | `NVDA` | `NVDA` |
| XOM | `xom.us` | `XOM` | `XOM` |
| CAT | `cat.us` | `CAT` | `CAT` |
| AMZN | `amzn.us` | `AMZN` | `AMZN` |
| CRM | `crm.us` | `CRM` | `CRM` |
| QCOM | `qcom.us` | `QCOM` | `QCOM` |
| BA | `ba.us` | `BA` | `BA` |
| V | `v.us` | `V` | `V` |
| GOOGL | `googl.us` | `GOOGL` | `GOOGL` |

Polygon free tier is 5 calls/min and 2-year history — trivially within bounds for 9 EOD pulls/day. Endpoint: `/v2/aggs/ticker/{T}/range/1/day/{from}/{to}`.

### 2.2 — The 30 predictors

The Excel file groups them into 6 buckets. I'm reorganizing by data provider because that's how you'll write the ingestion code.

#### Bucket A — FRED (free, unlimited, no rate limit issues)

Get a free API key at https://fred.stlouisfed.org/docs/api/api_key.html.

Endpoint pattern: `https://api.stlouisfed.org/fred/series/observations?series_id={ID}&api_key={KEY}&file_type=json&observation_start=2026-01-01`

| # | Excel name | FRED series ID | Frequency | Notes |
|---|---|---|---|---|
| 1 | `CPI_YoY_US` | `CPIAUCSL` | Monthly | Compute YoY = pct_change(12). |
| 2 | `Unemployment_Rate_US` | `UNRATE` | Monthly | Direct value. |
| 3 | `Industrial_Prod_Index` | `INDPRO` | Monthly | Direct. |
| 4 | `Building_Permits_US` | `PERMIT` | Monthly | Thousands. |
| 5 | `PPI_YoY_US` | `PPIACO` | Monthly | Compute YoY. |
| 6 | `Conference_Board_LEI` | `USSLIND` | Monthly | Conference Board's index — close enough. *(True LEI requires Conference Board paid feed.)* |
| 7 | `Consumer_Confidence` | `UMCSENT` | Monthly | UMich Sentiment — substitute for Conference Board CCI which is paywalled. |
| 8 | `EUR_USD` | `DEXUSEU` | Daily | Direct. |
| 9 | `EUR_JPY` | derived: `DEXUSEU` ÷ `DEXJPUS` × constant | Daily | Or pull directly from Stooq `eurjpy`. |
| 10 | `USD_MXN` | `DEXMXUS` | Daily | Direct. |
| 11 | `USD_CNY` | `DEXCHUS` | Daily | Direct. |
| 12 | `Brent_Crude` | `DCOILBRENTEU` | Daily | Direct. |
| 13 | `Gold_Spot` | `GOLDAMGBD228NLBM` | Daily | London PM fix. |

#### Bucket B — Stooq + yfinance (free, fallback chain)

**Primary: Stooq.** Same URL pattern as the US stocks — `https://stooq.com/q/d/l/?s={symbol}&i=d`. Stooq's symbol conventions:
- Indices use a `^` prefix (e.g., `^ftm`, `^dax`).
- International stocks use a region suffix (e.g., `.es` Spain, `.fr` France, `.ch` Switzerland, `.in` India).
- Commodity futures use a `.f` suffix or a continuous symbol.

**Fallback: yfinance.** Use `yf.download()` with a list of tickers, `period='3mo'`, `interval='1d'`. Wrap in `try/except`, treat `YFRateLimitError` as recoverable (back off 60s and retry once, then give up for the day).

> **Symbol-resolution discipline.** Stooq's symbols are not always intuitive and *can change*. Don't hardcode them in the ingestion code. Store the resolved `(provider, provider_symbol)` pair in the `variables` table and reference it from the ingestion runner. When onboarding the agent, the first task in this bucket is to verify each symbol manually by hitting the Stooq URL in a browser, save the working symbol to the DB, and only then write the loop.

International stock indices (8):

| # | Excel name | Stooq (try first) | yfinance (fallback) |
|---|---|---|---|
| 14 | `FTSE_100` | `^ftm` | `^FTSE` |
| 15 | `DAX` | `^dax` | `^GDAXI` |
| 16 | `CAC_40` | `^cac` | `^FCHI` |
| 17 | `Nikkei_225` | `^nkx` | `^N225` |
| 18 | `Hang_Seng` | `^hsi` | `^HSI` |
| 19 | `Bovespa` | `^bvp` | `^BVSP` |
| 20 | `KOSPI` | `^kos` | `^KS11` |
| 21 | `BSE_Sensex` | `^snx` | `^BSESN` |

International stocks (4):

| # | Excel name | Stooq (try first) | yfinance (fallback) |
|---|---|---|---|
| 22 | `Banco_Santander_MAD` | `san.es` | `SAN.MC` |
| 23 | `LVMH_PAR` | `mc.fr` | `MC.PA` |
| 24 | `Nestle_SWX` | `nesn.ch` | `NESN.SW` |
| 25 | `Reliance_NSE` | `reliance.in` | `RELIANCE.NS` |

Commodities (FRED already covers Brent + Gold above, leaving Wheat + Copper):

| # | Excel name | Stooq (try first) | yfinance (fallback) |
|---|---|---|---|
| 26 | `Wheat_Futures` | `zw.f` | `ZW=F` |
| 27 | `Copper_Futures` | `hg.f` | `HG=F` |

#### Bucket C — Tricky / scraped / substituted

These three have no clean free API. Choose one of the strategies below for each.

| # | Excel name | Strategy A (recommended) | Strategy B (fallback) |
|---|---|---|---|
| 28 | `Baltic_Dry_Index` | **Scrape** `https://tradingeconomics.com/commodity/baltic` daily *(server-rendered, ~1 KB HTML)*. Parse the headline number. Add a `User-Agent` and respect a 1-req/day rate. | Use ETF proxy `BDRY` via Polygon (correlated, not identical). |
| 29 | `ISM_Manufacturing_PMI` | **Scrape** `https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/pmi/` once a month (1st business day). | Use `S&P Global US Manufacturing PMI` from `https://www.pmi.spglobal.com` *(also free, monthly)*. |
| 30 | `Citi_Econ_Surprise_US` | **Substitute** with FRED's **Aruoba-Diebold-Scotti** Business Conditions Index (`ADSBCIMV`) — same conceptual signal (data surprises vs. expectations), free, daily. | Build your own surprise index from forecast-vs-actual on FRED ALFRED's vintage data. Heavier lift; do later. |

**Scraping rules** (apply to Baltic + ISM):
- Cache the response in `raw_html` table with timestamp.
- Use `httpx` with explicit timeout, retry 3× with exponential backoff.
- Regex-extract the value, then schema-validate (e.g., BDI must be int between 200 and 20000).
- Wrap in try/except — if the scrape fails, the daily ingestion job logs a warning but continues with stale data.

### 2.3 — Capital.com (live portfolio positions)

Free with a Capital.com account. **Use the demo account for development** so you don't risk real money.

- Demo base URL: `https://demo-api-capital.backend-capital.com`
- Live base URL: `https://api-capital.backend-capital.com`
- Docs: https://open-api.capital.com/

**Setup steps for the user (Emilio) before coding:**
1. Open a Capital.com demo account.
2. Enable 2FA (required before API key generation).
3. Settings → API integrations → Generate API key. Set a custom password for it.
4. Note three credentials: `API_KEY`, `IDENTIFIER` (your login email), `API_PASSWORD` (the custom password from step 3 — *not* your account password).

**Auth flow (every 10 min — sessions expire):**
```
POST /api/v1/session
Headers: X-CAP-API-KEY: {API_KEY}
Body: { "identifier": "{IDENTIFIER}", "password": "{API_PASSWORD}", "encryptedPassword": false }
→ Returns headers: CST, X-SECURITY-TOKEN
```
Pass `CST` and `X-SECURITY-TOKEN` on every subsequent call.

**Endpoints we need:**
- `GET /api/v1/positions` → live open positions (qty, avgPrice, currentPrice, P&L).
- `GET /api/v1/accounts` → balance + currency.
- `GET /api/v1/markets/{epic}` → current price for a single instrument *(use as a sanity check vs. Polygon)*.

**Limits to know:** 10 req/sec global, 1 req/sec on `/session`. Sessions live 10 min — implement a session cache with auto-refresh.

> **Security note for Emilio:** if you ever connect a *live* (not demo) Capital.com account to this backend, the API key will be able to *open and close real positions*. Treat that key with the same care as a brokerage password. Store it in Railway/Fly secret store, never in `.env.example`, never in git.

### 2.4 — Reliability strategy for the free-tier stack

Free providers fail more often than paid ones. The architecture must absorb that. Six rules:

**1. Provider chain per variable.** Don't hardcode a single provider in the ingestion call site. The `variables` table should carry a JSON column `providers` listing 1–3 providers in priority order:
```json
{ "providers": [
    { "name": "stooq",    "symbol": "^ftm" },
    { "name": "yfinance", "symbol": "^FTSE" }
] }
```
The ingestion runner walks the list, returns on first success, logs which provider served the row.

**2. Conservative request rate.** Run ingestion *once per day*, not on every page load. The cron job pulls everything and writes to Postgres; the API serves from the DB. Yahoo's rate-limiting kicks in at sustained high frequency, not at 30 calls/day spaced out.

**3. Stale-but-valid policy.** If a variable wasn't updated today (provider down, weekend, holiday), serve yesterday's value flagged `is_stale: true`. Predictions still run on the most recent observation. The frontend can show a small `STALE` badge but doesn't block.

**4. User-Agent + jitter.** When using yfinance or Stooq directly, set a real-looking User-Agent header (`Mozilla/5.0 ...`) and add 1–3 seconds of random jitter between requests. The default Python `requests` UA gets blocked faster.

**5. Cache responses for 23 hours.** Daily data doesn't need to be refetched within the day. Use the `observations` table itself as cache: before calling any provider, check if `(variable_id, today)` already exists.

**6. Health monitoring.** Keep an `ingestion_runs` row per provider per day. A weekly Slack summary of "X% success rate per provider this week" surfaces drift early. If a provider drops below ~80% success over a week, swap its position in the chain.

```python
# Reference fallback chain implementation
async def fetch_with_fallback(var: Variable, target_date: date) -> Observation | None:
    for provider_cfg in var.providers:
        try:
            value = await PROVIDERS[provider_cfg.name].fetch(provider_cfg.symbol, target_date)
            if value is not None:
                logger.info(f"{var.id} served by {provider_cfg.name}")
                return Observation(var.id, target_date, value, provider_cfg.name)
        except RateLimited:
            logger.warning(f"{provider_cfg.name} rate-limited, trying next")
            await asyncio.sleep(2 + random.random())
        except Exception as e:
            logger.warning(f"{provider_cfg.name} failed for {var.id}: {e}")
    logger.error(f"All providers failed for {var.id}")
    return None
```

**7. yfinance/Stooq legal note.** Both are *unofficial* relative to their data sources. Yahoo's TOS technically prohibit commercial redistribution of their data; Stooq is more permissive but not licensed for resale. **For Emilio's stated use case** (personal portfolio simulation, no resale, no public-facing SaaS) this is in the same legal gray zone where most quant hobbyists live — fine. **If this ever becomes a product sold to other users**, swap the affected providers for licensed feeds (Twelve Data, Polygon paid, EOD Historical Data) before going live.

---

## 3. Database schema

Start with these tables. All times UTC. All prices in USD unless `currency` says otherwise — convert at ingestion time using the FX rates also being ingested.

```sql
-- 3.1 The variable registry (30 predictors + 9 stocks + portfolio)
create table variables (
  id              text primary key,           -- e.g. 'CPI_YoY_US', 'NVDA', 'PORTFOLIO_1'
  display_name    text not null,
  kind            text not null check (kind in ('predictor','stock','portfolio')),
  category        text,                       -- 'Published Index', 'FX Rate', 'Commodity', etc.
  unit            text,
  provider        text not null,              -- 'fred', 'twelve_data', 'polygon', 'scrape', 'capital_com'
  provider_symbol text not null,              -- the upstream identifier (FRED series id, TD symbol, etc.)
  active          boolean default true,
  created_at      timestamptz default now()
);

-- 3.2 The single observations table (long format)
create table observations (
  variable_id text not null references variables(id),
  observed_on date not null,                  -- the trading/release date
  value       numeric not null,
  source_run_id uuid not null,                -- FK to ingestion_runs
  primary key (variable_id, observed_on)
);
create index obs_var_date on observations(variable_id, observed_on desc);

-- 3.3 Ingestion run metadata (for debugging, retries, audit)
create table ingestion_runs (
  id              uuid primary key default gen_random_uuid(),
  provider        text not null,
  started_at      timestamptz default now(),
  completed_at    timestamptz,
  status          text check (status in ('running','ok','partial','failed')),
  rows_inserted   int default 0,
  error_message   text
);

-- 3.4 Fitted models (one row per stock per refit)
create table models (
  id              uuid primary key default gen_random_uuid(),
  ticker          text not null,
  fitted_at       timestamptz default now(),
  training_start  date not null,
  training_end    date not null,
  n_obs           int not null,
  predictor_ids   text[] not null,            -- variable ids used (3-5 of them)
  intercept       numeric not null,
  coefficients    jsonb not null,             -- { 'CPI_YoY_US': 0.0845, 'KOSPI': 0.0015, ... }
  r2              numeric not null,
  r2_adj          numeric not null,
  durbin_watson   numeric not null,
  breusch_pagan_p numeric not null,
  max_vif         numeric not null,
  status          text check (status in ('PASS','REVIEW','FAIL')),
  is_active       boolean default true        -- only one active model per ticker
);
create unique index uniq_active_model on models(ticker) where is_active;

-- 3.5 Daily predictions (one row per ticker per prediction date)
create table predictions (
  model_id      uuid references models(id),
  ticker        text not null,
  predicted_for date not null,                -- the date we are predicting
  predicted_at  timestamptz default now(),    -- when the prediction was made
  predicted_return numeric,
  predicted_price  numeric not null,
  actual_price     numeric,                   -- nullable, filled in next day's run
  abs_error_pct    numeric,                   -- |pred - actual| / actual
  primary key (model_id, predicted_for)
);

-- 3.6 Portfolio definitions (the 5 risk profiles)
create table portfolios (
  id          text primary key,               -- 'P1_CONSERVATIVE', ... 'P5_AGGRESSIVE'
  name        text not null,
  description text,
  weights     jsonb not null,                 -- { 'NVDA': 0.22, 'XOM': 0.11, ... } sums to 1.0
  generated_at timestamptz default now()
);

-- 3.7 Live positions snapshot from Capital.com
create table positions_snapshots (
  id              uuid primary key default gen_random_uuid(),
  snapshot_at     timestamptz default now(),
  account_id      text not null,
  ticker          text not null,
  quantity        numeric not null,
  avg_price       numeric not null,
  last_price      numeric not null,
  market_value    numeric not null,
  open_pnl        numeric not null,
  open_pnl_pct    numeric not null
);
create index pos_snap_lookup on positions_snapshots(snapshot_at desc, ticker);
```

---

## 4. The model layer (the hard part — get this right)

### 4.1 Lag structure

The Excel uses **1-day lagged returns** for predictors:
```
ret(stock, t)     = α + Σ βᵢ · ret(predictor_i, t-1) + ε
```
The HTML mockup talks about a 7-day lag in one of its versions; **stick with t-1 (one trading day lag)** to match the Excel — it's what passes the diagnostic tests in `Summary` sheet. Make the lag a `LAG_DAYS` constant in `config.py` so it can be changed later.

Use **log returns** (`np.log(p_t / p_{t-1})`) not simple returns — it's what real practitioners use and what makes the heteroscedasticity test more meaningful.

### 4.2 The four diagnostic tests (all must pass)

Use `statsmodels`. Reference implementation:

```python
import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.outliers_influence import variance_inflation_factor

def fit_and_diagnose(y: pd.Series, X: pd.DataFrame) -> dict:
    """Fit OLS and return all four diagnostic stats. y and X are aligned, no NaNs."""
    X_const = sm.add_constant(X, has_constant='add')
    res = sm.OLS(y, X_const).fit()

    # Test 1: R²
    r2 = res.rsquared
    r2_adj = res.rsquared_adj

    # Test 2: Durbin-Watson (autocorrelation)
    from statsmodels.stats.stattools import durbin_watson
    dw = durbin_watson(res.resid)

    # Test 3: Breusch-Pagan (heteroscedasticity)
    bp_lm, bp_p, _, _ = het_breuschpagan(res.resid, X_const)

    # Test 4: Max VIF (multicollinearity) — exclude the constant
    vifs = []
    for i in range(1, X_const.shape[1]):
        vifs.append(variance_inflation_factor(X_const.values, i))
    max_vif = max(vifs) if vifs else 0.0

    passed = (r2 >= 0.90) and (1.5 <= dw <= 2.5) and (bp_p > 0.05) and (max_vif < 10)

    return {
        'coefficients': dict(zip(X.columns, res.params[1:])),
        'intercept': float(res.params[0]),
        'r2': float(r2), 'r2_adj': float(r2_adj),
        'durbin_watson': float(dw),
        'breusch_pagan_p': float(bp_p),
        'max_vif': float(max_vif),
        'status': 'PASS' if passed else 'REVIEW',
        'n_obs': int(res.nobs),
    }
```

### 4.3 The no-overlap feature selection

This is the trickiest piece. The Excel uses different predictors for each of the 9 stocks, with no predictor used twice. The naive way is greedy; the right way is a small ILP. Implement greedy first, ILP second.

**Greedy (build this first):**

```python
def select_features_greedy(returns_df: pd.DataFrame,  # cols: stock tickers + predictor ids
                           tickers: list[str],
                           predictors: list[str],
                           lag_days: int = 1,
                           k_per_stock: int = 4) -> dict[str, list[str]]:
    """
    For each stock, greedily pick the k predictors with highest |corr|
    against the stock's returns, skipping any predictor already used.
    """
    # 1. Compute lagged predictor returns
    lagged = returns_df[predictors].shift(lag_days)
    aligned = pd.concat([returns_df[tickers], lagged], axis=1).dropna()

    used = set()
    chosen = {}
    # Sort tickers by hardest-to-fit first (those with widest set of "needs")
    # In practice just go in portfolio order.
    for tkr in tickers:
        corrs = aligned[predictors].corrwith(aligned[tkr]).abs().sort_values(ascending=False)
        picks = []
        for var_id in corrs.index:
            if var_id in used: continue
            picks.append(var_id)
            if len(picks) >= k_per_stock: break
        chosen[tkr] = picks
        used.update(picks)
    return chosen
```

**Then upgrade to ILP** once greedy is working: model as "assign each predictor to at most one stock, max ΣR² across stocks, min `k_per_stock` per stock, max 5". Use `pulp` or `scipy.optimize.milp`. Don't gold-plate this in v1 — greedy is fine to ship.

### 4.4 Refit cadence

- **Daily ingestion** → write to `observations`.
- **Weekly refit** (Sunday 02:00 UTC) → re-run feature selection + fit + diagnostics. Insert new row in `models`. If `status='PASS'`, mark new row `is_active=true` and previous row `is_active=false` (in a transaction — use `uniq_active_model` partial index above).
- **If new fit fails diagnostics** → keep the old active model, log a warning, alert via email/Slack webhook.

### 4.5 Prediction generation

- **Daily** (after ingestion completes, ~22:00 UTC):
  - For each ticker, load the active model.
  - Pull yesterday's predictor returns (lagged inputs for today's prediction).
  - Compute `predicted_return = α + Σ βᵢ · ret(xᵢ, t-1)`.
  - Convert to `predicted_price = last_price × exp(predicted_return)` (log returns).
  - Insert into `predictions` table with `predicted_for = today`.
- **Backfill `actual_price` and `abs_error_pct`** in the *next* day's run, when the realized price is in.

---

## 5. Portfolio optimization (the 5 risk profiles)

The Excel's `Step 2` produced 5 portfolios from "conservative" to "aggressive" using elasticity + resistance scores. Replicate that logic, but make it deterministic and test-covered.

**Inputs per stock:**
- `r2` (model fit quality) → "resistance" component.
- `predicted_return_30d` (sum of next 30 daily predictions).
- `vol_annualized` = std(daily returns) × √252.
- `sharpe` = predicted_return_30d × (252/30) ÷ vol_annualized.

**Algorithm** (replicate the Excel weights):

```python
def build_portfolios(stock_metrics: pd.DataFrame) -> dict[str, dict]:
    """stock_metrics has columns: r2, pred_ret_30d, vol_annual, sharpe."""
    sm = stock_metrics.copy()
    sm['risk_score']  = sm['vol_annual'].rank() / len(sm)        # 0..1, higher = riskier
    sm['return_score'] = sm['sharpe'].rank() / len(sm)            # 0..1, higher = more upside
    sm['confidence']   = sm['r2']                                 # already 0..1

    profiles = {}
    # P1 Conservative: weight inversely to risk, scaled by confidence
    w = (1 - sm['risk_score']) * sm['confidence']
    profiles['P1_CONSERVATIVE'] = (w / w.sum()).to_dict()
    # P3 Balanced: equal weights
    profiles['P3_BALANCED']     = {t: 1/len(sm) for t in sm.index}
    # P5 Aggressive: weight by return × sharpe
    w = sm['return_score'] * sm['sharpe'].clip(lower=0.01)
    profiles['P5_AGGRESSIVE']   = (w / w.sum()).to_dict()
    # P2 / P4: 50/50 blends of adjacent profiles
    profiles['P2_MOD_CONSERVATIVE'] = blend(profiles['P1_CONSERVATIVE'], profiles['P3_BALANCED'], 0.5)
    profiles['P4_MOD_AGGRESSIVE']   = blend(profiles['P3_BALANCED'], profiles['P5_AGGRESSIVE'], 0.5)
    return profiles

def blend(a, b, t): return {k: (1-t)*a[k] + t*b[k] for k in a}
```

**Validation check:** every portfolio's weights must sum to 1.0 ± 1e-6. Add a property test.

---

## 6. REST API (FastAPI)

Endpoints the frontend needs. Keep responses tight — the frontend will paginate/aggregate as needed.

| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/health` | Liveness | `{ "ok": true, "version": "..." }` |
| GET | `/variables` | List all 30 predictors + 9 stocks | `[{ id, display_name, kind, category, last_observed_on, last_value }]` |
| GET | `/observations/{variable_id}?from=&to=` | Time series for one variable | `[{ date, value }]` |
| GET | `/models` | All active models with diagnostics | `[{ ticker, r2, dw, bp_p, max_vif, status, predictor_ids, fitted_at }]` |
| GET | `/models/{ticker}` | Full model detail incl. coefficients | `{ ...diagnostics, coefficients: { ... }, equation: "..." }` |
| POST | `/models/{ticker}/refit` | Force a refit (admin only) | `{ model_id, status, ... }` |
| GET | `/predictions/{ticker}?days=30` | Last N predictions vs actuals | `[{ date, predicted, actual, error_pct }]` |
| GET | `/predictions/portfolio/{portfolio_id}?days=30` | Portfolio-level rolled-up prediction | `[{ date, predicted_value, actual_value, error_pct }]` |
| POST | `/simulate` | Body: 4 macro values → return 7-day prediction (matches the HTML simulator tab) | `{ predicted_value, delta, contributions: { var: $ } }` |
| GET | `/portfolios` | The 5 risk profiles + weights + accuracy ranking | `[{ id, name, weights, mape_30d, rank }]` |
| GET | `/positions/live` | Capital.com live positions | `[{ ticker, qty, avg_price, last_price, mkt_value, pnl, pnl_pct }]` |
| POST | `/positions/sync` | Force a Capital.com pull | `{ snapshot_id, count }` |

**Auth:** put a single bearer token in front of write/admin endpoints (`/refit`, `/sync`). Read endpoints can be public during dev, then move behind Supabase Auth (JWT) for prod.

**CORS:** allow the Vercel frontend domain only. No `*` in prod.

---

## 7. Daily orchestration (the cron sequence)

Run as one GitHub Actions workflow (`infra/github-actions/daily-ingestion.yml`) on schedule `0 22 * * 1-5` (22:00 UTC, weekdays). It executes a single Python entrypoint that runs each step, and **each step is independently retryable** — partial failure shouldn't block the rest.

```
22:00 UTC  →  ingest:fred           (fast, 13 series, ~5s)
22:01      →  ingest:twelve_data    (14 series, ~30s with rate limits)
22:02      →  ingest:polygon        (9 stocks, ~10s)
22:03      →  ingest:scrapes        (Baltic, ISM if 1st-of-month)
22:04      →  ingest:capital_com    (live positions snapshot)
22:05      →  predictions:run       (compute today's predictions)
22:06      →  predictions:backfill  (fill yesterday's actual_price)
22:07      →  portfolios:rebuild    (recompute the 5 weight sets)
                                     │
                                     └─ Sunday only:
                                        models:refit_all (re-fit all 9)
22:10      →  notify:summary         (Slack/email: rows ingested, errors)
```

**Idempotency:** every ingestion writes with `INSERT ... ON CONFLICT (variable_id, observed_on) DO UPDATE SET value=EXCLUDED.value`. Re-running a day is safe.

**Retry policy:** each step retries 3× with exponential backoff. On final failure, write a row to a `job_failures` table and continue.

---

## 8. Environment variables

```bash
# .env.example
DATABASE_URL=postgresql+asyncpg://postgres:...@db.supabase.co:5432/postgres
DATABASE_URL_SYNC=postgresql://postgres:...@db.supabase.co:5432/postgres   # for Alembic

# Data providers — free tier
FRED_API_KEY=                 # required, free at https://fred.stlouisfed.org/docs/api/api_key.html
POLYGON_API_KEY=              # optional, free tier at https://polygon.io
ALPHA_VANTAGE_API_KEY=        # optional, last-resort fallback

# Stooq + yfinance need NO keys but require the right User-Agent
HTTP_USER_AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Broker
CAPITAL_API_KEY=
CAPITAL_IDENTIFIER=
CAPITAL_API_PASSWORD=
CAPITAL_BASE_URL=https://demo-api-capital.backend-capital.com

# App
ADMIN_BEARER_TOKEN=           # protect /refit, /sync
ALLOWED_ORIGINS=http://localhost:3000,https://portfolio.vercel.app

# Observability (optional)
SENTRY_DSN=
SLACK_WEBHOOK_URL=

# --- Upgrade path (uncomment when reliability matters more than cost) ---
# TWELVE_DATA_API_KEY=        # $29/mo Grow plan replaces Stooq+yfinance with one stable provider
```

---

## 9. Implementation phases

Build in this order. Each phase ends with something demonstrable.

### Phase 0 — bootstrap (½ day)
- [ ] Create monorepo, `backend/` + `frontend/` + `infra/`.
- [ ] Set up Supabase project, run migrations.
- [ ] Get all API keys: FRED (free, mandatory), Polygon (free tier, optional), Capital.com (demo). No paid keys needed for v1.
- [ ] Local FastAPI hello-world + Next.js hello-world both wired to Supabase.

### Phase 1 — ingestion (3 days)
- [ ] Implement `ingestion/fred.py` — pulls 13 FRED series, writes to `observations`.
- [ ] Implement `ingestion/stooq.py` — primary for international stocks, indices, and futures (no auth, CSV download).
- [ ] Implement `ingestion/yfinance_provider.py` — fallback chain with rate-limit handling.
- [ ] Implement `ingestion/polygon.py` — fallback for the 9 US stocks (free tier, 5 req/min).
- [ ] Implement `ingestion/baltic.py` — scraper with caching.
- [ ] Implement `ingestion/runner.py` — orchestrator with `fetch_with_fallback()` (§2.4).
- [ ] **Manually verify all Stooq symbols** by hitting URLs in a browser, save resolved symbols to `variables.providers` JSON column.
- [ ] Write GH Actions workflow.
- [ ] **Demo:** trigger manually, see 30+ days of history populate in Supabase, with a per-row `served_by_provider` column populated.

### Phase 2 — model layer (3 days)
- [ ] `modeling/regression.py` — `fit_and_diagnose()` from §4.2.
- [ ] `modeling/feature_select.py` — greedy no-overlap selector.
- [ ] `modeling/prediction.py` — generate next-day prediction from active model.
- [ ] One-shot CLI: `python -m app.modeling.refit_all` writes 9 models.
- [ ] **Demo:** all 9 models pass with R² ≥ 0.90.

### Phase 3 — predictions + portfolios (2 days)
- [ ] Daily prediction runner (writes to `predictions` table).
- [ ] Backfill `actual_price` job.
- [ ] `portfolio/optimizer.py` — the 5 weight profiles.
- [ ] `portfolio/backtest.py` — replays predictions and computes MAPE.
- [ ] **Demo:** `/predictions/NVDA?days=30` returns real numbers; `/portfolios` returns 5 weight sets.

### Phase 4 — Capital.com (1 day)
- [ ] `ingestion/capital_com.py` — session auth + position fetch.
- [ ] Snapshot job in cron.
- [ ] `/positions/live` endpoint.
- [ ] **Demo:** demo account positions show up in `/positions/live`.

### Phase 5 — API surface (1 day)
- [ ] All endpoints from §6.
- [ ] OpenAPI spec exposed at `/docs`.
- [ ] Bearer-token middleware for write endpoints.
- [ ] CORS configured for Vercel.

### Phase 6 — frontend port (3–5 days)
- [ ] Port `index.html` to Next.js App Router.
- [ ] Replace hardcoded `POSITIONS`/`HIST`/`MODEL` constants with API calls (`lib/api.ts`).
- [ ] Move chart rendering from inline canvas to `recharts` or `visx`.
- [ ] Move design tokens (`--bg`, `--green`, etc.) to Tailwind config.
- [ ] **Demo:** end-to-end live dashboard.

### Phase 7 — polish (ongoing)
- [ ] Sentry/logging.
- [ ] Add Portfolio 2, Portfolio 3 etc. (currently hardcoded for 9 tickers — make `tickers` a portfolio config row in DB).
- [ ] Replace greedy feature selection with ILP.

---

## 10. Things to clarify with Emilio before coding

These are decisions the agent should ask the user (Emilio) about, not guess at. Surface these as a single message at the *end* of Phase 0:

1. **Production vs demo Capital.com.** Demo for development is non-negotiable. Should we ever connect a live account, or always read-only demo? *(Recommend: read-only live with a separate API key that has no trade permissions.)*
2. **Refit cadence.** Weekly is the default. Does Emilio want a "refit on demand" button in the UI? *(Adds a `POST /models/refit_all` admin endpoint.)*
3. **Multi-portfolio support.** The Excel shows "Portfolio 1" with 9 specific tickers. The schema above (`portfolios` table) supports any portfolio. Should the user be able to define new portfolios via the UI in v1, or hardcode Portfolio 1 only?
4. **Trading simulation depth.** The HTML mockup talks about "trading strategy simulation" — does that mean (a) just predicted-vs-actual accuracy tracking, or (b) actually simulating trades on signals (long if predicted return > threshold, with P&L curves)? Spec out (b) before building it.
5. **Frequency of UI refresh.** Live (WebSocket from Capital.com) or polling every 60s? *(Polling is fine for v1 and simpler.)*
6. **Citi Surprise substitute is permanent or temporary?** The free stack uses ADS Index from FRED as a stand-in. Confirm with Emilio that this is acceptable for v1; if a paid Bloomberg/Refinitiv feed is added later, the substitution gets reversed.
7. **Acceptable staleness threshold.** Free providers occasionally drop a day. Is "yesterday's value, flagged stale" OK for predictions, or does the model refuse to predict if any input is >24h old?

---

## 11. Acceptance tests (write these as pytest before merging Phase 2)

```python
def test_all_nine_models_pass_diagnostics():
    """Re-fit all 9 stocks, every one passes R²≥0.90, DW∈[1.5,2.5], BP_p>0.05, VIF<10."""
    # ...

def test_no_predictor_appears_in_two_models():
    """Across the 9 active models, the union of predictor_ids has no duplicates."""
    # ...

def test_portfolio_weights_sum_to_one():
    for p in get_all_portfolios():
        assert abs(sum(p.weights.values()) - 1.0) < 1e-6

def test_idempotent_ingestion():
    """Running ingestion twice for the same date inserts no new rows."""
    # ...

def test_capital_com_session_refreshes():
    """Session token auto-refreshes after 10 minutes of inactivity."""
    # ...

def test_prediction_endpoint_under_500ms():
    """GET /predictions/NVDA?days=30 returns in <500ms with a warm DB."""
    # ...
```

---

## 12. Out of scope for v1

Be explicit. Do not build:

- Order execution / trading bots. This is a **predict-and-display** system, not a trading system.
- Mobile native apps.
- User accounts / multi-tenancy. Single-user system for now.
- Alternative model classes (XGBoost, neural nets). OLS is the spec.
- Options / derivatives data.
- News sentiment / NLP. The Excel doesn't use it.

---

## 13. References

- Excel source of truth: `Portfolio1_Prediction_Model.xlsx` (sheets `Source`, `Summary`, plus one per ticker).
- Frontend mockup: `index.html` (single file, ~700 lines).
- FRED API docs: https://fred.stlouisfed.org/docs/api/fred/
- Stooq CSV download: https://stooq.com/q/d/?s={symbol} (no auth, no docs page — explore via UI)
- yfinance docs: https://github.com/ranaroussi/yfinance
- Polygon.io API docs: https://polygon.io/docs/stocks
- Capital.com API docs: https://open-api.capital.com/
- Capital.com Postman collection: https://github.com/capital-com-sv/capital-api-postman
- statsmodels OLS guide: https://www.statsmodels.org/stable/regression.html
- (Upgrade path) Twelve Data API docs: https://twelvedata.com/docs

---

**End of brief.** A coding agent should be able to start at Phase 0 and ship Phase 6 in ~2 weeks of focused work.
