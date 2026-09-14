# CATALYST_KEYWORDS — Tanda 1 (aplicada)

OK del dueño 2026-09-14: estas frases entran a producción
(`detector.py`). Misma idea que `ALIASES` en #118 — lista humana
visible, no solo el tuple en código.

Tanda 1 cierra huecos de wording ya medidos. No inventa tipos. El
ancla (#118), IA≥7, ATR, umbrales y universo no se tocan.

Tanda 2 (`reports qN`, `q1:`, `beats` pelado, `share repurchase`, …)
sigue **fuera**. El contrafactual de Tanda 1 no disparó la alarma ~40
(Buscador: 50 tickers 5→7, ~230 títulos 55→57).

Versión: `tanda1-v1`.

El loader de `shadow.py` lee **solo** el bloque entre
`TANDA1_INICIO` y `TANDA1_FIN` — para contrastar PRE vs producción y
para clavar que el markdown y `CATALYST_KEYWORDS` son la misma lista.

<!-- TANDA1_INICIO -->

## buyback

Producción previa: `share buyback`, `repurchase program`,
`stock buyback`, `buyback program`. Se mantienen.

| frase | near-miss que cierra | hueco vs producción previa |
|---|---|---|
| `buybacks` | CRM: "Salesforce Spent a Record $27.1 Billion on Buybacks in One Quarter" | no lleva share/stock/program |
| `share buybacks` | "share buybacks after Q2" | explícito en el pedido; substring de `share buyback` ya lo cubría |
| `stock buybacks` | "stock buybacks: what the program means" | explícito en el pedido; substring de `stock buyback` ya lo cubría |

No se añade el singular pelado `buyback` ("no buyback this year").

## earnings

Producción previa: `quarterly results`, `earnings results`,
`beats estimates`, `misses estimates`, `q1 results`…`q4 results`,
`reports revenue of`. Se mantienen.

| frase | near-miss que cierra | hueco vs producción previa |
|---|---|---|
| `upbeat q1` | ORCL: "upbeat Q1" | no es `q1 results` |
| `upbeat q2` | "upbeat Q2" | idem Q2 |
| `upbeat q3` | "upbeat Q3" | idem Q3 |
| `upbeat q4` | "upbeat Q4" | idem Q4 |
| `q1 earnings` | "ORCL Q1 earnings" vs exacto `q1 results` | mismo print, otra palabra |
| `q2 earnings` | "Q2 earnings beat estimates" | idem Q2 |
| `q3 earnings` | "Q3 earnings" | idem Q3 |
| `q4 earnings` | "Q4 earnings" | idem Q4 |

Limitación honesta: `qN earnings` también pega templates Yahoo del tipo
"Q2 Earnings Call Highlights". El dueño lo vio en el CF del Buscador
(55→57 títulos, no ~40) y lo aceptó en Tanda 1. No se añade un filtro
negativo nuevo.

<!-- TANDA1_FIN -->

## Tanda 2 — no aplicar

Pedido explícito: no entra ahora.

| frase | por qué espera |
|---|---|
| `reports q1`…`reports q4` / `posted qN` / `fiscal qN` | Tanda 2 |
| `q1:` (pelado) | Tanda 2 |
| `beats` pelado | "beats the market" / Zacks; Tanda 2 |
| `share repurchase` / `stock repurchase` | Tanda 2 |
| `beat estimates` / `beats expectations` / `tops estimates` | Tanda 2 |

## Qué no se toca

- `ancla.py` (ALIASES, GENERIC_TOKENS, la regla)
- `score_minimo_alerta`, ATR, early/entry/risk
- IA `confianza < 7`
- filtros de universo
- paper endpoint (sigue hardcodeado)
