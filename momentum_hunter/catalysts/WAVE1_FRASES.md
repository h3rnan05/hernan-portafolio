# CATALYST_KEYWORDS — Wave 1 (SHADOW)

Pedido del dueño 2026-09-14: ampliar keywords **solo como
contrafactual**. Esta lista NO entra a producción. El detector sigue
usando `CATALYST_KEYWORDS` en `detector.py` tal cual. Activar estas
frases requiere un OK explícito aparte.

Objetivo de Wave 1: **cerrar huecos de wording**, no aflojar el
criterio. Tipos de catalizador genuinamente nuevos = Wave 2, fuera de
alcance. El filtro de ancla (#118) no se toca. Tampoco IA≥7, ATR,
umbrales ni filtros de universo.

Versión de esta tabla: `wave1-shadow-v1`.

Cómo se usa: `shadow.py` lee **solo** el bloque entre
`WAVE1_PROPUESTAS_INICIO` y `WAVE1_PROPUESTAS_FIN`. Las frases
rechazadas más abajo son documentación para humanos — el loader no las
carga. Matching idéntico al de producción: substring en minúsculas,
primer tipo según `ORDEN_PRIORIDAD`.

<!-- WAVE1_PROPUESTAS_INICIO -->

## buyback

Producción hoy: `share buyback`, `repurchase program`, `stock buyback`,
`buyback program`.

El hueco medido: un anuncio de recompra que **no** lleva esos prefijos.
`stock buybacks` / `share buybacks` ya matchean porque el plural
contiene el singular (`stock buyback` ⊂ `stock buybacks`). Lo que no
matchea es el plural pelado o `share repurchase` sin la palabra
`program`.

| frase | near-miss que cierra | hueco vs producción |
|---|---|---|
| `buybacks` | "Company announces $2B in buybacks" | no es share/stock buyback ni buyback program |
| `share repurchase` | "Board authorizes a $500M share repurchase" | no es `repurchase program` |
| `share repurchases` | "accelerates share repurchases" | mismo hueco, plural |
| `stock repurchase` | "announces a stock repurchase" | no es `stock buyback` ni `repurchase program` |
| `stock repurchases` | "stock repurchases resume" | mismo hueco, plural |
| `repurchase of shares` | "authorizes repurchase of shares" | orden invertido vs `repurchase program` |
| `share-repurchase` | "share-repurchase plan" | mismo anuncio, con guion |
| `stock-repurchase` | "stock-repurchase authorization" | mismo anuncio, con guion |

No se propone el singular pelado `buyback`: en titulares de comentario
("no buyback this year", "the buyback debate") no hay anuncio. Wave 1
no abre ese grifo.

## earnings

Producción hoy: `quarterly results`, `earnings results`,
`beats estimates`, `misses estimates`, `q1 results`…`q4 results`,
`reports revenue of`.

Dos huecos distintos, mismo tipo. No se añade un tipo nuevo.

### Variantes de trimestre (Q1–Q4)

El patrón `qN results` exige esas dos palabras pegadas. Titulares reales
dicen "upbeat Q1", "reports Q1 loss", "posts Q2 sales" — mismo print de
resultados, otra redacción.

| frase | near-miss que cierra | hueco vs producción |
|---|---|---|
| `upbeat q1` | "Upbeat Q1 lifts shares" | no contiene `q1 results` |
| `upbeat q2` | "Upbeat Q2 lifts shares" | idem Q2 |
| `upbeat q3` | "Upbeat Q3 lifts shares" | idem Q3 |
| `upbeat q4` | "Upbeat Q4 lifts shares" | idem Q4 |
| `reports q1` | "Acme reports Q1 earnings" / Zacks "Reports Q1 Loss" | `reports` y `q1` van juntos, sin `results` |
| `reports q2` | "Reports Q2 Loss, Misses Revenue Estimates" | idem Q2 |
| `reports q3` | "Reports Q3 Loss" | idem Q3 |
| `reports q4` | "Reports Q4 Loss" | idem Q4 |
| `reported q1` | "Company reported Q1 profit" | pasado de `reports q1` |
| `reported q2` | "Company reported Q2 profit" | idem Q2 |
| `reported q3` | "Company reported Q3 profit" | idem Q3 |
| `reported q4` | "Company reported Q4 profit" | idem Q4 |
| `posts q1` | "Posts Q1 sales in line with estimates" | verbo distinto a `reports` |
| `posts q2` | "Posts Q2 CY2026 sales in line with estimates" | idem Q2 |
| `posts q3` | "Posts Q3 beat on revenue" | idem Q3 |
| `posts q4` | "Posts Q4 sales" | idem Q4 |
| `posted q1` | "Posted Q1 profit" | pasado de `posts q1` |
| `posted q2` | "Posted Q2 profit" | idem Q2 |
| `posted q3` | "Posted Q3 profit" | idem Q3 |
| `posted q4` | "Posted Q4 profit" | idem Q4 |
| `first-quarter results` | "First-quarter results top views" | `q1 results` escrito en palabras, con guion |
| `first quarter results` | "First quarter results top views" | idem, sin guion |
| `second-quarter results` | "Second-quarter results" | analogía de `q2 results` |
| `second quarter results` | "Second quarter results" | idem, sin guion |
| `third-quarter results` | "Third-quarter results" | analogía de `q3 results` |
| `third quarter results` | "Third quarter results" | idem, sin guion |
| `fourth-quarter results` | "Fourth-quarter results" | analogía de `q4 results` |
| `fourth quarter results` | "Fourth quarter results" | idem, sin guion |

No se propone `q1 earnings` / `q2 earnings` / … : en Yahoo es el
template "Q2 Earnings Call Highlights" y "Q2 Earnings Estimates". En el
snapshot del 2026-09-14 esas dos palabras solas inflaron el delta hacia
decenas de tickers extra. Eso ya no es un hueco de wording, es aflojar
el criterio. Queda en rechazadas.

Misma limitación que ya tiene producción: `reports q2` también pegaría
un "reports Q2 outlook" si existiera. No se inventa un filtro negativo
nuevo — Wave 1 no cambia la regla de matching.

### Variantes de beats estimates

`beats estimates` exige esas dos palabras pegadas. El print real a
menudo mete palabras en el medio ("beats Wall Street estimates") o usa
pasado ("Beat Estimates") o "expectations" / "consensus" / "tops".

Cuidado deliberado: **no** `beats` pelado, **no** `beats the market`,
**no** `beats the s&p`. Eso es ranking Zacks / performance vs índice,
no un print de earnings.

| frase | near-miss que cierra | hueco vs producción |
|---|---|---|
| `beats expectations` | "quarterly results beat expectations" ya lo cubre `quarterly results`; "EPS beats expectations" no | `estimates` vs `expectations` |
| `beat expectations` | "Results beat expectations" | pasado |
| `beats earnings estimates` | "profit beats earnings estimates" | palabras entre `beats` y `estimates` |
| `beats wall street estimates` | "Profit beats Wall Street estimates" | idem |
| `beats analyst estimates` | "EPS beats analyst estimates" | idem |
| `beats consensus estimates` | "revenue beats consensus estimates" | idem |
| `beats consensus` | "EPS beats consensus as margins expand" | sin la palabra `estimates` |
| `tops estimates` | "Retailer tops estimates on strong demand" | verbo distinto |
| `topped estimates` | "Retailer topped estimates" | pasado de `tops estimates` |
| `beat estimates` | Zacks "Q2 Earnings and Revenues Beat Estimates" | producción pide `beats` (con s) |
| `beats estimate` | "beats estimate" en singular | producción pide `estimates` (plural) |
| `beats revenue estimates` | "beats revenue estimates" | palabras en el medio |
| `beats eps estimates` | "beats EPS estimates" | idem |
| `beats profit estimates` | "beats profit estimates" | idem |
| `tops wall street estimates` | "tops Wall Street estimates" | `tops` + medio |
| `earnings beat` | "Shares jump after first-quarter earnings beat" / "stock jumps on Q2 earnings beat" | print de beat sin `estimates` |

<!-- WAVE1_PROPUESTAS_FIN -->

## Rechazadas a propósito (no cargar)

Estas se consideraron y se dejaron fuera. Wave 2 (tipos nuevos) tampoco
está acá.

| frase | por qué no |
|---|---|
| `buyback` (singular pelado) | comentario ("no buyback this year", "the buyback debate"), no anuncio |
| `repurchase` (pelado) | recompra de activos, no necesariamente de acciones |
| `q1 earnings` / `q2 earnings` / `q3 earnings` / `q4 earnings` | template Yahoo "Q2 Earnings Call Highlights" y "Q2 Earnings Estimates"; en el snapshot 2026-09-14 el delta de titulares se iba a +72 y el de tickers hacia una zona que el dueño marcó como demasiado suelta (~40 vs mediana histórica ~3) |
| `beats the market` | ranking Zacks / performance vs mercado, no print |
| `beats the s&p` | idem |
| `beats` (pelado) | "beats the market", "this stock beats", ruido |
| `misses revenue estimates` y simétricos de miss | el pedido de Wave 1 cubre beats, no misses; el hueco existe y queda anotado para más adelante |
| FDA / M&A slang / contratos nuevos | tipo nuevo = Wave 2 |

## Qué no se toca

- `CATALYST_KEYWORDS` de producción
- `ancla.py` (ALIASES, GENERIC_TOKENS, la regla)
- `score_minimo_alerta`, ATR, early/entry/risk
- IA `confianza < 7`
- filtros de universo
- paper endpoint (sigue hardcodeado)
