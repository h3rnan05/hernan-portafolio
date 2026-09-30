# Backtest estrategia v2 — 2025-09-26 a 2026-09-25 — variante seguimiento

**Variante `seguimiento`:** plan B: nivel 1 al día siguiente (ruptura del máximo del día 1 en el día 2, stop 4 %, cierre EOD). Un solo cambio sobre la config base; el YAML no cambia.

Generado 2026-09-29 01:59 UTC / 19:59 Monterrey (UTC−6). Config: `estrategia_v2.yaml` (versión 1, prompt v1, modelo `claude-sonnet-5`). Equity inicial $5,000, slippage 0.15% por lado, comisión 0.0.

## Veredicto: NO APRUEBA

| criterio | resultado | ¿cumple? |
|---|---:|:---:|
| ≥ 150 trades | 186 | sí |
| expectativa > 0.2 R | -0.16 R | no |
| factor de beneficio > 1.3 | 0.71 | no |
| drawdown < 10% | — | no |

## Resultados

| métrica | valor |
|---|---:|
| trades | 186 |
| tasa de acierto | 31.2% |
| ganancia media | +1.32 R |
| pérdida media | -0.83 R |
| expectativa por trade | -0.16 R |
| factor de beneficio | 0.71 |
| drawdown máximo | — |
| MFE promedio / mediana | +1.11 R / +0.53 R |
| MAE promedio / mediana | +0.80 R / +0.95 R |
| P&L simulado | $-751.17 |

## Desglose

### Por nivel de catalizador

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| nivel 1 | 186 | 31.2% | +1.32 R | -0.83 R | -0.16 R | 0.71 |
| nivel 2 | 0 | — | — | — | — | — |

### Por hora de entrada (NY; 09:36 NY = 13:36 UTC en verano = 07:36 Monterrey)

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| 09:36–09:59 | 35 | 42.9% | +0.84 R | -1.00 R | -0.22 R | 0.62 |
| 10:00–10:29 | 20 | 45.0% | +1.09 R | -0.77 R | +0.07 R | 1.16 |
| 10:30–11:00 | 7 | 14.3% | +0.53 R | -0.89 R | -0.69 R | 0.10 |

### Por rango de precio

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| $2–10 | 69 | 31.9% | +1.86 R | -0.93 R | -0.04 R | 0.94 |
| $10–20 | 60 | 31.7% | +1.02 R | -0.83 R | -0.24 R | 0.57 |
| $20–50 | 56 | 30.4% | +0.94 R | -0.72 R | -0.22 R | 0.57 |

### Por motivo de salida

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| cierre | 99 | 58.6% | +1.32 R | -0.40 R | +0.61 R | 4.66 |
| stop | 87 | 0.0% | — | -1.04 R | -1.04 R | 0.00 |

### MFE de los trades que salieron por tiempo (0)

Cuánto llegó a ir a favor cada uno antes de que el stop de tiempo lo cerrara:

| MFE | trades |
|---|---:|
| < 0.00 R | 0 |
| 0.00–0.25 R | 0 |
| 0.25–0.50 R | 0 |
| 0.50–1.00 R | 0 |
| 1.00–1.50 R | 0 |
| ≥ 1.50 R | 0 |

## Embudo etapa por etapa

Símbolo-días que sobreviven cada filtro, en orden (una etapa se cuenta si alguna vela de la ventana pasó esa etapa y todas las anteriores; el spread se mira en las primeras 3 velas que pasaron las previas).

| etapa | sobreviven | % de la anterior |
|---|---:|---:|
| universo (símbolos × sesiones) | 1926676 | — |
| gap ≥ mínimo (subasta oficial) | 24568 | 1.3% |
| sin acción corporativa | 24361 | 99.2% |
| con noticia en 24 h | 8833 | 36.3% |
| gap (en la ventana, con velas) | 0 | 0.0% |
| RVOL ≥ mínimo | 0 | 0.0% |
| precio > VWAP | 0 | 0.0% |
| ruptura del rango de apertura (con volumen) | 0 | 0.0% |
| spread ≤ máximo | 0 | 0.0% |
| SPY > su VWAP | 0 | 0.0% |
| dentro de la ventana | 0 | 0.0% |
| catalizador operable (IA) | 0 | 0.0% |
| stop ≤ 4% | 0 | 0.0% |

### Descartados por stop (tope 4%)

Señales: 0; descartadas por stop: 0. Distancia del mínimo del rango de apertura al precio de la señal (el stop que habría requerido):

| stop requerido | todas las señales | descartadas |
|---|---:|---:|
| 0%–2% | 0 | 0 |
| 2%–4% | 0 | 0 |
| 4%–6% | 0 | 0 |
| 6%–8% | 0 | 0 |
| 8%–10% | 0 | 0 |
| 10%–15% | 0 | 0 |
| ≥ 15% | 0 | 0 |

## Embudo (etapas alcanzadas, sin orden)

| etapa | símbolo-días |
|---|---:|
| universo | 7676 |
| pre_gap | 83672 |
| gap_oficial | 24568 |
| sin_accion_corporativa | 24361 |
| con_noticias | 8833 |
| nivel_1 | 677 |
| senal | 186 |
| trades | 186 |

Por qué no hubo señal (peor intento de cada símbolo-día, puede sumar más de uno):

- sin_ruptura_dia1: 491
- veto: 150

Clasificaciones de la IA:

- error_api: 2070
- invalida_intento_1:truncada: 8
- invalida_intento_1:vacia: 16
- nivel_0: 462
- nivel_1: 257
- nivel_2: 187
- reintento: 24
- tope_de_llamadas: 15508

Sin dato (excluido, nunca contado como cero):

- gap_oficial: 978

## Limitaciones (no se maquillan)

- PLAN B EXPLORATORIO, solo lectura: no es la v2 ni toca producción.
- Día 1 = gap ≥ 4 % sin acción corporativa con una noticia nivel 1 alcista (IA, misma caché); día 2 = ruptura del máximo del día 1 en velas de 1 min (09:31–15:30), entrada a la apertura de la vela siguiente + slippage.
- Stop 4 % bajo la entrada (R = 4 %), salida a las 15:50 o por stop; sin objetivo ni breakeven.
- Tamaño 0,5 % del equity inicial entre el riesgo, sin capitalizar ni límites de cartera.
- Float, ETF, SPAC, RVOL, VWAP, SPY y spread NO se aplican: es la versión más simple de la idea.
