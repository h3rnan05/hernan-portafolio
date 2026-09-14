# Contrafactual Wave 1 — CURRENT vs PROPOSED

Generado 2026-09-14. Versión de frases: `wave1-shadow-v1`. Paper only. Producción `CATALYST_KEYWORDS` **no cambia**.

## Resumen

- Frases Wave 1 cargadas del markdown: **52** (solo buyback + earnings; cero tipos nuevos).
- Mediana histórica `embudo.con_catalizador`: **2.5 (n=34 escaneos en telemetría)**. El dueño marcó ~40 como demasiado suelto.
- **Sin FLAG de '~40'. Yahoo+ancla 21→31 (×1.48). Extrapolado sobre mediana 2.5: ~3.7 cats/escaneo -- lejos de 40. (Extrapolación grosera: el snapshot no es un escaneo de 1000 tickers.)** Rebanada universo (sin watchlist): ancla 2→9 (×4.50), extrapolado ~11.2. n chico; no es ~40.

| corpus | titulares | tickers | cats titular actual | cats titular propuesto | delta | cats ticker+ancla actual | cats ticker+ancla propuesto |
|---|---:|---:|---:|---:|---:|---:|---:|
| auditoria | 319 | 208 | 319 | 319 | +0 | 144 | 144 |
| yahoo snapshot 2026-09-14 | 887 | 93 | 35 | 58 | +23 | 21 | 31 |
| yahoo · solo watchlist | 432 | 44 | 33 | 40 | +7 | 19 | 22 |
| yahoo · solo universo (no watchlist) | 455 | 49 | 2 | 18 | +16 | 2 | 9 |
| curadas (near-miss + trampas) | 20 | 10 | 2 | 10 | +8 | 2 | 5 |

Auditoría = titulares que **ya** calificaron (control). Yahoo = snapshot de `yfinance` el 2026-09-14 sobre 116 tickers (watchlist + rebanada del universo); 93 trajeron noticia. La watchlist ya venía con catalizador: el delta útil está en la rebanada de universo. Curadas = huecos y trampas escritos a mano para las pruebas.

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
- Cats (titular, solo keyword): actual **35** → propuesto **58** (delta +23)
- Cats (ticker, solo keyword): actual **30** → propuesto **40** (delta +10)
- Cats (ticker, keyword + ancla #118): actual **21** → propuesto **31** (delta +10)

Nuevos matches (ticker|titular|tipo|frase):

| ticker | tipo | frase | titular |
|---|---|---|---|
| XPOF | buyback | `buybacks` | Can PLNT's Buybacks Sustain Higher EPS Growth Despite Margin Pressure? |
| GLUE | earnings | `reports q2` | Monte Rosa Therapeutics (GLUE) Reports Q2 Loss, Misses Revenue Estimates |
| GLUE | earnings | `reports q1` | Monte Rosa Therapeutics (GLUE) Reports Q1 Loss, Misses Revenue Estimates |
| BEAM | earnings | `reports q2` | Beam Therapeutics Inc. (BEAM) Reports Q2 Loss, Lags Revenue Estimates |
| BEAM | earnings | `reports q2` | Myriad Genetics (MYGN) Reports Q2 Loss, Misses Revenue Estimates |
| CRM | buyback | `buybacks` | Salesforce Spent a Record $27.1 Billion on Buybacks in One Quarter. Here Is Why That Signal Matters. |
| AVGO | earnings | `earnings beat` | Broadcom Just Named Its Next Customer to Pass Google: Anthropic. Here's Why That Matters More Than the Earnings Beat. |
| TRDA | earnings | `reports q2` | Entrada Therapeutics, Inc. (TRDA) Reports Q2 Loss, Tops Revenue Estimates |
| TRDA | earnings | `reports q2` | Compugen (CGEN) Reports Q2 Loss, Misses Revenue Estimates |
| TRDA | earnings | `reports q2` | Wave Life Sciences (WVE) Reports Q2 Loss, Misses Revenue Estimates |
| TRDA | earnings | `reports q1` | Entrada Therapeutics, Inc. (TRDA) Reports Q1 Loss, Misses Revenue Estimates |
| LOVE | earnings | `posts q2` | Lovesac (NASDAQ:LOVE) Posts Q2 CY2026 Sales In Line With Estimates But Stock Drops 12.7% |
| LOVE | earnings | `reports q2` | Lovesac (LOVE) Reports Q2 Loss, Lags Revenue Estimates |
| SENS | earnings | `reports q2` | Senseonics Holdings (SENS) Reports Q2 Loss, Tops Revenue Estimates |
| SENS | earnings | `beat estimates` | KORU Medical Systems, Inc. (KRMD) Q2 Earnings and Revenues Beat Estimates |
| TLSI | earnings | `reports q1` | TriSalus Life Sciences, Inc. (TLSI) Reports Q1 Loss, Lags Revenue Estimates |
| ACH | earnings | `earnings beat` | Accendra Health (ACH) Shares Jump After First-Quarter Earnings Beat |
| ATLO | earnings | `beat estimates` | First Financial Corp. (THFF) Q2 Earnings Beat Estimates |
| ATLO | earnings | `beat estimates` | Lakeland Financial (LKFN) Q2 Earnings and Revenues Beat Estimates |
| ATLO | earnings | `beat estimates` | Peoples Bancorp (PEBO) Q2 Earnings Beat Estimates |
| BPRN | earnings | `beat estimates` | Princeton Bancorp (BPRN) Q2 Earnings and Revenues Beat Estimates |
| FLWS | earnings | `reports q4` | 1-800-Flowers.com (FLWS) Reports Q4 Loss, Misses Revenue Estimates |
| DUOT | earnings | `earnings beat` | Duos Technologies Stock Jumps on Q2 Earnings Beat |


De esos nuevos, el ancla (#118, **sin cambiar**) bloquearía:

| ticker | motivo | tipo | frase | titular |
|---|---|---|---|---|
| XPOF | sin_ancla | buyback | `buybacks` | Can PLNT's Buybacks Sustain Higher EPS Growth Despite Margin Pressure? |
| BEAM | sin_ancla | earnings | `reports q2` | Myriad Genetics (MYGN) Reports Q2 Loss, Misses Revenue Estimates |
| TRDA | sin_ancla | earnings | `reports q2` | Compugen (CGEN) Reports Q2 Loss, Misses Revenue Estimates |
| TRDA | sin_ancla | earnings | `reports q2` | Wave Life Sciences (WVE) Reports Q2 Loss, Misses Revenue Estimates |
| SENS | sin_ancla | earnings | `beat estimates` | KORU Medical Systems, Inc. (KRMD) Q2 Earnings and Revenues Beat Estimates |
| ATLO | sin_ancla | earnings | `beat estimates` | First Financial Corp. (THFF) Q2 Earnings Beat Estimates |
| ATLO | sin_ancla | earnings | `beat estimates` | Lakeland Financial (LKFN) Q2 Earnings and Revenues Beat Estimates |
| ATLO | sin_ancla | earnings | `beat estimates` | Peoples Bancorp (PEBO) Q2 Earnings Beat Estimates |


### curadas near-miss + trampas

- Titulares únicos: **20** · tickers: **10**
- Cats (titular, solo keyword): actual **2** → propuesto **10** (delta +8)
- Cats (ticker, solo keyword): actual **2** → propuesto **6** (delta +4)
- Cats (ticker, keyword + ancla #118): actual **2** → propuesto **5** (delta +3)

Nuevos matches (ticker|titular|tipo|frase):

| ticker | tipo | frase | titular |
|---|---|---|---|
| DEMO | buyback | `buybacks` | Company announces $2B in buybacks |
| DEMO | buyback | `share repurchase` | Board authorizes a $500 million share repurchase |
| DEMO | earnings | `upbeat q1` | Upbeat Q1 lifts shares |
| GLUE | earnings | `reports q2` | Monte Rosa Therapeutics (GLUE) Reports Q2 Loss, Misses Revenue Estimates |
| DEMO | earnings | `beats wall street estimates` | Profit beats Wall Street estimates |
| DEMO | earnings | `tops estimates` | Retailer tops estimates on strong demand |
| BPRN | earnings | `beat estimates` | Princeton Bancorp (BPRN) Q2 Earnings and Revenues Beat Estimates |
| DUOT | earnings | `earnings beat` | Duos Technologies Stock Jumps on Q2 Earnings Beat |


De esos nuevos, el ancla (#118, **sin cambiar**) bloquearía:

| ticker | motivo | tipo | frase | titular |
|---|---|---|---|---|
| DEMO | sin_ancla | buyback | `buybacks` | Company announces $2B in buybacks |
| DEMO | sin_ancla | buyback | `share repurchase` | Board authorizes a $500 million share repurchase |
| DEMO | sin_ancla | earnings | `upbeat q1` | Upbeat Q1 lifts shares |
| DEMO | sin_ancla | earnings | `beats wall street estimates` | Profit beats Wall Street estimates |
| DEMO | sin_ancla | earnings | `tops estimates` | Retailer tops estimates on strong demand |


## Qué se midió y qué no

- El ancla se aplicó como **filtro posterior**, igual que en `run.py`. No se modificó (`flag demasiado suelto=False` se calcula sobre ticker+ancla del snapshot Yahoo mixto).
- No se re-corrió el embudo completo (universo → operables → noticias). Un escaneo de ~1000 tickers no cabe en este contrafactual offline.
- `clasificar_titular` de producción sigue viendo catalizador en los titulares de auditoría -- el shadow no reemplaza esa función ni muta `CATALYST_KEYWORDS`.

## Qué no se tocó

- `CATALYST_KEYWORDS` en `detector.py`
- `ancla.py`
- IA≥7, ATR, umbrales, universo
- paper endpoint
