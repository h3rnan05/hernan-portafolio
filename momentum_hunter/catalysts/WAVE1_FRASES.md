# CATALYST_KEYWORDS — Tanda 1 (SHADOW)

Lista visible estilo ALIASES (#118). **Este PR no enciende producción.**
Las frases de Tanda 1 viven acá y en el clasificador shadow. El PR de
producción es el **#121**.

Pedido Tanda 1 (mínima): `buybacks` / `share buybacks` / `stock buybacks`
+ `upbeat q1`…`q4` + `q1 earnings`…`q4 earnings`. Tanda 2
(`reports qN`, `q1:`, `beats` pelado) no entra.

El ancla (#118), IA≥7, ATR, umbrales y universo no se tocan.

Versión: `tanda1-v1`.

`shadow.py` lee **solo** el bloque entre `TANDA1_INICIO` y `TANDA1_FIN`.

<!-- TANDA1_INICIO -->

## buyback

Producción hoy: `share buyback`, `repurchase program`, `stock buyback`,
`buyback program`.

| frase | near-miss que cierra | hueco vs producción |
|---|---|---|
| `buybacks` | CRM: "Salesforce Spent a Record $27.1 Billion on Buybacks in One Quarter" | no lleva share/stock/program |
| `share buybacks` | "share buybacks after Q2" | explícito; substring de `share buyback` ya lo cubría |
| `stock buybacks` | "stock buybacks: what the program means" | explícito; substring de `stock buyback` ya lo cubría |

No se propone el singular pelado `buyback` ("no buyback this year").

## earnings

Producción hoy: `quarterly results`, `earnings results`,
`beats estimates`, `misses estimates`, `q1 results`…`q4 results`,
`reports revenue of`.

| frase | near-miss que cierra | hueco vs producción |
|---|---|---|
| `upbeat q1` | ORCL: "upbeat Q1" | no es `q1 results` |
| `upbeat q2` | "upbeat Q2" | idem Q2 |
| `upbeat q3` | "upbeat Q3" | idem Q3 |
| `upbeat q4` | "upbeat Q4" | idem Q4 |
| `q1 earnings` | vs exacto `q1 results` | mismo print, otra palabra |
| `q2 earnings` | "Q2 earnings" | idem Q2 |
| `q3 earnings` | "Q3 earnings" | idem Q3 |
| `q4 earnings` | "Q4 earnings" | idem Q4 |

Limitación honesta: `qN earnings` también pega templates Yahoo
"Q2 Earnings Call Highlights". El Buscador midió 50 tickers 5→7 y
~230 títulos 55→57; alarma ~40 no disparó.

<!-- TANDA1_FIN -->

## Tanda 2 — no aplicar (tampoco en shadow de este PR)

| frase | por qué espera |
|---|---|
| `reports q1`…`q4` / `fiscal qN` | Tanda 2 |
| `q1:` (pelado) | Tanda 2 |
| `beats` pelado | "beats the market" / Zacks |

## Qué no se toca

- `CATALYST_KEYWORDS` de producción (el expand está en #121)
- `ancla.py`
- IA `confianza < 7`, ATR, umbrales, universo
- paper endpoint
