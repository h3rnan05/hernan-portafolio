# Contrafactual Tanda 1 — PRE vs producción

Generado 2026-09-14. Versión de frases: `tanda1-v1`. Paper only. Este PR es **shadow**: `CATALYST_KEYWORDS` de producción no cambia. El expand Tanda 1 de producción es #121. Este reporte compara PRE vs PRE∪Tanda1.

## Referencia Buscador (dueño, 2026-09-14)

- 50 tickers: **5→7**
- ~230 títulos: **55→57**
- Alarma ~40: **no disparó**.

## Resumen (este repo, snapshot Yahoo 2026-09-14 + auditoría)

- Frases Tanda 1 en el markdown: **11** (solo `buybacks`/`share buybacks`/`stock buybacks` + `upbeat qN` + `qN earnings`).
- Mediana histórica `embudo.con_catalizador`: **2.5 (n=34 escaneos en telemetría)**. El dueño marcó ~40 como demasiado suelto.
- **Sin FLAG de '~40' en unidades de embudo. Yahoo+ancla 21→40 (×1.90); extrapolado sobre mediana 2.5: ~4.8 cats/escaneo. El sample tuvo 40 tickers con match — no confundir con 40 cats/corrida. Referencia Buscador: 5→7 tickers, 55→57 títulos, alarma no disparó.** Rebanada universo (sin watchlist): ancla 2→19 (×9.50), extrapolado ~23.8. n chico; no es ~40.

| corpus | titulares | tickers | cats titular actual | cats titular propuesto | delta | cats ticker+ancla actual | cats ticker+ancla propuesto |
|---|---:|---:|---:|---:|---:|---:|---:|
| auditoria | 319 | 208 | 319 | 319 | +0 | 144 | 144 |
| yahoo snapshot 2026-09-14 | 887 | 93 | 35 | 93 | +58 | 21 | 40 |
| yahoo · solo watchlist | 432 | 44 | 33 | 41 | +8 | 19 | 21 |
| yahoo · solo universo (no watchlist) | 455 | 49 | 2 | 52 | +50 | 2 | 19 |
| curadas (near-miss + trampas) | 10 | 7 | 2 | 5 | +3 | 2 | 4 |

Auditoría = titulares que **ya** calificaron (control). Yahoo = snapshot de `yfinance` el 2026-09-14 sobre 116 tickers (watchlist + rebanada del universo); 93 trajeron noticia. La watchlist ya venía con catalizador: el delta útil está en la rebanada de universo. Curadas = huecos y trampas escritos a mano para las pruebas. Este dump de 887 títulos salta más que el Buscador (55→57) porque `qN earnings` pega templates Zacks ('Q2 Earnings Call Highlights'). El dueño midió 50 tickers / ~230 títulos y dio OK.

## Números por corpus

### auditoria (ya calificados)

- Titulares únicos: **319** · tickers: **208**
- Cats (titular, solo keyword): actual **319** → propuesto **319** (delta +0)
- Cats (ticker, solo keyword): actual **208** → propuesto **208** (delta +0)
- Cats (ticker, keyword + ancla #118): actual **144** → propuesto **144** (delta +0)

Nuevos matches (ticker|titular|tipo|frase):

_(ninguno)_


Cambios de tipo (prioridad: buyback gana a earnings si ambas frases están):

| ticker | actual | propuesto | frase nueva | titular |
|---|---|---|---|---|
| HWM | earnings | buyback | `buybacks` | Did Strong Q2 Results, Higher Guidance and Buybacks Just Shift Howmet Aerospace's (HWM) Investment Narrative? |
| MO | earnings | buyback | `buybacks` | Altria Group (MO) Reported Q2 Results And Buybacks, Is It Trading At A Discount? |


### yahoo snapshot 2026-09-14

- Titulares únicos: **887** · tickers: **93**
- Cats (titular, solo keyword): actual **35** → propuesto **93** (delta +58)
- Cats (ticker, solo keyword): actual **30** → propuesto **49** (delta +19)
- Cats (ticker, keyword + ancla #118): actual **21** → propuesto **40** (delta +19)

Nuevos matches (ticker|titular|tipo|frase):

| ticker | tipo | frase | titular |
|---|---|---|---|
| VOXR | earnings | `q2 earnings` | Vox Royalty Corp. (VOXR) Q2 Earnings Surpass Estimates |
| VOXR | earnings | `q2 earnings` | Almonty Industries Inc. (ALM) Q2 Earnings Match Estimates |
| XPOF | buyback | `buybacks` | Can PLNT's Buybacks Sustain Higher EPS Growth Despite Margin Pressure? |
| CRM | buyback | `buybacks` | Salesforce Spent a Record $27.1 Billion on Buybacks in One Quarter. Here Is Why That Signal Matters. |
| ADBE | earnings | `q3 earnings` | Should You Play Adobe's Q3 Earnings With ETFs? |
| ON | earnings | `q2 earnings` | Q2 Earnings Roundup: onsemi (NASDAQ:ON) And The Rest Of The Analog Semiconductors Segment |
| GMED | earnings | `q3 earnings` | COO Q3 Earnings Top Estimates, Revenues Miss on Destocking, Stock Down |
| SUNB | earnings | `q4 earnings` | Sunbelt Rentals Q4 Earnings Call Focuses on Specialty, Margin Path |
| LOVE | earnings | `q2 earnings` | Lovesac Q2 Earnings Call Highlights |
| LOVE | earnings | `q2 earnings` | Lovesac (LOVE) Q2 Earnings: How Key Metrics Compare to Wall Street Estimates |
| TTGT | earnings | `q2 earnings` | TechTarget Q2 Earnings Call Highlights |
| TTGT | earnings | `q1 earnings` | TechTarget Q1 Earnings Call Highlights |
| SENS | earnings | `q2 earnings` | Senseonics Holdings, Inc. Common Stock Q2 Earnings Call Highlights |
| SENS | earnings | `q2 earnings` | KORU Medical Systems, Inc. (KRMD) Q2 Earnings and Revenues Beat Estimates |
| SENS | earnings | `q2 earnings` | Omnicell (OMCL) Q2 Earnings and Revenues Top Estimates |
| SENS | earnings | `q1 earnings` | Senseonics Holdings, Inc. Common Stock Q1 Earnings Call Highlights |
| OSUR | earnings | `q2 earnings` | OraSure Technologies Q2 Earnings Call Highlights |
| OSUR | earnings | `q1 earnings` | OraSure Technologies Q1 Earnings Call Highlights |
| TLSI | earnings | `q2 earnings` | TriSalus Life Sciences Q2 Earnings Call Highlights |
| TLSI | earnings | `q1 earnings` | TriSalus Life Sciences Q1 Earnings Call Highlights |
| TLSI | earnings | `q2 earnings` | VAREX IMAGING (VREX) Q2 Earnings and Revenues Miss Estimates |
| FXNC | earnings | `q2 earnings` | First National Corp. (FXNC) Beats Q2 Earnings and Revenue Estimates |
| FXNC | earnings | `q2 earnings` | Capital Bancorp (CBNK) Surpasses Q2 Earnings Estimates |
| FXNC | earnings | `q2 earnings` | Bankwell Financial Group, Inc. (BWFG) Q2 Earnings and Revenues Top Estimates |
| MPAA | earnings | `q1 earnings` | Motorcar Parts of America Q1 Earnings Call Highlights |
| MPAA | earnings | `q4 earnings` | Motorcar Parts of America Q4 Earnings Call Highlights |
| ACH | earnings | `q2 earnings` | ICU Medical (ICUI) Tops Q2 Earnings and Revenue Estimates |
| RGCO | earnings | `q3 earnings` | RGC Resources Q3 Earnings Call Highlights |
| RGCO | earnings | `q2 earnings` | RGC Resources Q2 Earnings Call Highlights |
| RGCO | earnings | `q1 earnings` | RGC Resources Q1 Earnings Call Highlights |
| ATLO | earnings | `q2 earnings` | First Business Financial Services (FBIZ) Q2 Earnings and Revenues Top Estimates |
| ATLO | earnings | `q2 earnings` | First Financial Corp. (THFF) Q2 Earnings Beat Estimates |
| ATLO | earnings | `q2 earnings` | Lakeland Financial (LKFN) Q2 Earnings and Revenues Beat Estimates |
| ATLO | earnings | `q2 earnings` | Peoples Bancorp (PEBO) Q2 Earnings Beat Estimates |
| ATLO | earnings | `q4 earnings` | Ames National: Q4 Earnings Snapshot |
| TG | earnings | `q2 earnings` | Tredegar Q2 Earnings Rise Y/Y on Aluminum Extrusions Gains |
| TG | earnings | `q1 earnings` | TG Stock Down 20% Despite Q1 Earnings Jump Y/Y on Pricing Gains |
| TG | earnings | `q4 earnings` | Tredegar's Q4 Earnings Soar Y/Y on Aluminum Extrusions Strength |
| TG | earnings | `q4 earnings` | Tredegar: Q4 Earnings Snapshot |
| ESCA | earnings | `q2 earnings` | Escalade Q2 Earnings Call Highlights |
| PPHC | earnings | `q2 earnings` | Public Policy Q2 Earnings Call Highlights |
| BPRN | earnings | `q2 earnings` | Princeton Bancorp (BPRN) Q2 Earnings: How Key Metrics Compare to Wall Street Estimates |
| BPRN | earnings | `q2 earnings` | Princeton Bancorp (BPRN) Q2 Earnings and Revenues Beat Estimates |
| BPRN | earnings | `q2 earnings` | Bank OZK (OZK) Tops Q2 Earnings Estimates |
| BPRN | earnings | `q2 earnings` | LCNB (LCNB) Surpasses Q2 Earnings and Revenue Estimates |
| FLWS | earnings | `q4 earnings` | 1-800 FLOWERS.COM Q4 Earnings Call Highlights |
| HCKT | earnings | `q2 earnings` | The Hackett Group Q2 Earnings Call Highlights |
| HCKT | earnings | `q2 earnings` | Hackett Group (HCKT) Meets Q2 Earnings Estimates |
| POWW | earnings | `q1 earnings` | Outdoor Q1 Earnings Call Highlights |
| POWW | earnings | `q1 earnings` | Outdoor Holding Company (POWW) Q1 Earnings and Revenues Surpass Estimates |
| NAGE | earnings | `q2 earnings` | Niagen Bioscience (NAGE) Meets Q2 Earnings Estimates |
| HDSN | earnings | `q2 earnings` | The Top 5 Analyst Questions From Hudson Technologies’s Q2 Earnings Call |
| HDSN | earnings | `q2 earnings` | Reflecting On Specialty Equipment Distributors Stocks’ Q2 Earnings: Hudson Technologies (NASDAQ:HDSN) |
| HDSN | earnings | `q2 earnings` | Hudson Technologies Q2 Earnings Call Highlights |
| HDSN | earnings | `q2 earnings` | Hudson Technologies (HDSN) Misses Q2 Earnings Estimates |
| DUOT | earnings | `q2 earnings` | Duos Technologies Stock Jumps on Q2 Earnings Beat |
| DUOT | earnings | `q2 earnings` | Duos Technologies Group Q2 Earnings Call Highlights |
| DUOT | earnings | `q2 earnings` | GDS Holdings (GDS) Misses Q2 Earnings and Revenue Estimates |


De esos nuevos, el ancla (#118, **sin cambiar**) bloquearía:

| ticker | motivo | tipo | frase | titular |
|---|---|---|---|---|
| VOXR | sin_ancla | earnings | `q2 earnings` | Almonty Industries Inc. (ALM) Q2 Earnings Match Estimates |
| XPOF | sin_ancla | buyback | `buybacks` | Can PLNT's Buybacks Sustain Higher EPS Growth Despite Margin Pressure? |
| GMED | sin_ancla | earnings | `q3 earnings` | COO Q3 Earnings Top Estimates, Revenues Miss on Destocking, Stock Down |
| SENS | sin_ancla | earnings | `q2 earnings` | KORU Medical Systems, Inc. (KRMD) Q2 Earnings and Revenues Beat Estimates |
| SENS | sin_ancla | earnings | `q2 earnings` | Omnicell (OMCL) Q2 Earnings and Revenues Top Estimates |
| TLSI | sin_ancla | earnings | `q2 earnings` | VAREX IMAGING (VREX) Q2 Earnings and Revenues Miss Estimates |
| FXNC | sin_ancla | earnings | `q2 earnings` | Capital Bancorp (CBNK) Surpasses Q2 Earnings Estimates |
| FXNC | sin_ancla | earnings | `q2 earnings` | Bankwell Financial Group, Inc. (BWFG) Q2 Earnings and Revenues Top Estimates |
| ACH | sin_ancla | earnings | `q2 earnings` | ICU Medical (ICUI) Tops Q2 Earnings and Revenue Estimates |
| ATLO | sin_ancla | earnings | `q2 earnings` | First Business Financial Services (FBIZ) Q2 Earnings and Revenues Top Estimates |
| ATLO | sin_ancla | earnings | `q2 earnings` | First Financial Corp. (THFF) Q2 Earnings Beat Estimates |
| ATLO | sin_ancla | earnings | `q2 earnings` | Lakeland Financial (LKFN) Q2 Earnings and Revenues Beat Estimates |
| ATLO | sin_ancla | earnings | `q2 earnings` | Peoples Bancorp (PEBO) Q2 Earnings Beat Estimates |
| BPRN | sin_ancla | earnings | `q2 earnings` | Bank OZK (OZK) Tops Q2 Earnings Estimates |
| BPRN | sin_ancla | earnings | `q2 earnings` | LCNB (LCNB) Surpasses Q2 Earnings and Revenue Estimates |
| DUOT | sin_ancla | earnings | `q2 earnings` | GDS Holdings (GDS) Misses Q2 Earnings and Revenue Estimates |


### curadas near-miss + trampas

- Titulares únicos: **10** · tickers: **7**
- Cats (titular, solo keyword): actual **2** → propuesto **5** (delta +3)
- Cats (ticker, solo keyword): actual **2** → propuesto **4** (delta +2)
- Cats (ticker, keyword + ancla #118): actual **2** → propuesto **4** (delta +2)

Nuevos matches (ticker|titular|tipo|frase):

| ticker | tipo | frase | titular |
|---|---|---|---|
| CRM | buyback | `buybacks` | Salesforce Spent a Record $27.1 Billion on Buybacks in One Quarter. Here Is Why That Signal Matters. |
| ORCL | earnings | `upbeat q1` | Oracle shares jump after upbeat Q1 |
| ORCL | earnings | `q1 earnings` | ORCL Q1 earnings beat lifts cloud outlook |


## Qué se midió y qué no

- El ancla se aplicó como **filtro posterior**, igual que en `run.py`. No se modificó (`flag demasiado suelto=False` se calcula sobre ticker+ancla del snapshot Yahoo mixto).
- No se re-corrió el embudo completo (universo → operables → noticias). Un escaneo de ~1000 tickers no cabe en este contrafactual offline.
- `clasificar_titular` de producción (sin Tanda 1 en este PR) sigue viendo catalizador en los titulares de auditoría.

## Qué no se tocó

- `CATALYST_KEYWORDS` en `detector.py` (expand = #121)
- `ancla.py` (ALIASES, GENERIC, la regla)
- IA≥7, ATR, umbrales, universo
- Tanda 2 (`reports qN`, `beats` pelado, `q1:`)
- paper endpoint
