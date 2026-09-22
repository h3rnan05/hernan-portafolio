# Tanda 1 near-miss — correcciones de keywords

Lista escrita al estilo `ALIASES` (`catalysts/ancla.py`): frases que el
shadow CF etiquetó como catalizador real y el detector no veía. La
fuente de verdad operativa es la constante `TANDA1_NEAR_MISS` en
`detector.py`; este archivo es el inventario para auditar sin leer el
matching.

Human OK 2026-09-14 para abrir PR. Paper trading. No es Tanda 2.

## Qué entra (solo esto)

### buyback

- `buybacks`
- `share buybacks`
- `stock buybacks`

El singular (`share buyback`, `stock buyback`) ya estaba. El hueco era
el plural suelto: un titular tipo `Salesforce … Buybacks` no contiene
`share buyback` ni `buyback program`.

### earnings

- `upbeat q1`, `upbeat q2`, `upbeat q3`, `upbeat q4`
- `q1 earnings`, `q2 earnings`, `q3 earnings`, `q4 earnings`

Cubre `Oracle Reports Upbeat Q1`. `qN results` / `beats estimates` ya
estaban y siguen vigentes.

## Qué no entra (a propósito)

- `beats` suelto — caza ruido de mercado (`Boeing Beats Stock Market`).
- Tanda 2: `q1:`, `fiscal qN`, `reports qN`.
- Cualquier otra frase. Esta lista es el techo, no un punto de partida.

No toca ancla (#118), proveedor de noticias, cron de GHA, ni umbrales
de score / ATR / IA≥7.

## Shadow CF (por qué el cambio de prod es deliberado)

Sobre la muestra: **+2 catalizadores**. Mediana del hunter ~1,5–3 por
corrida. Alarma de explosión (~40) no se dispara. El cambio de keywords
en prod es intencional y acotado a Tanda 1.
