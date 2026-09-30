# Backtest estrategia v2 — 2025-09-26 a 2026-09-25 — variante peadnoticia

**Variante `peadnoticia`:** plan B: PEAD con titular de resultados de Benzinga como evento (sec.gov da 403 en Actions). Un solo cambio sobre la config base; el YAML no cambia.

Generado 2026-09-29 02:13 UTC / 20:13 Monterrey (UTC−6). Config: `estrategia_v2.yaml` (versión 1, prompt v1, modelo `claude-sonnet-5`). Equity inicial $5,000, slippage 0.15% por lado, comisión 0.0.

## Veredicto: NO APRUEBA

| criterio | resultado | ¿cumple? |
|---|---:|:---:|
| ≥ 150 trades | 27 | no |
| expectativa > 0.2 R | +0.22 R | sí |
| factor de beneficio > 1.3 | 1.36 | sí |
| drawdown < 10% | — | no |

## Resultados

| métrica | valor |
|---|---:|
| trades | 27 |
| tasa de acierto | 44.4% |
| ganancia media | +1.84 R |
| pérdida media | -1.08 R |
| expectativa por trade | +0.22 R |
| factor de beneficio | 1.36 |
| drawdown máximo | — |
| MFE promedio / mediana | +1.69 R / +1.43 R |
| MAE promedio / mediana | +1.15 R / +0.82 R |
| P&L simulado | $144.46 |

## Desglose

### Por nivel de catalizador

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| nivel 1 | 27 | 44.4% | +1.84 R | -1.08 R | +0.22 R | 1.36 |
| nivel 2 | 0 | — | — | — | — | — |

### Por hora de entrada (NY; 09:36 NY = 13:36 UTC en verano = 07:36 Monterrey)

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| 09:36–09:59 | 0 | — | — | — | — | — |
| 10:00–10:29 | 0 | — | — | — | — | — |
| 10:30–11:00 | 0 | — | — | — | — | — |

### Por rango de precio

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| $2–10 | 15 | 46.7% | +1.88 R | -0.88 R | +0.41 R | 1.87 |
| $10–20 | 11 | 45.5% | +1.79 R | -1.35 R | +0.07 R | 1.10 |
| $20–50 | 0 | — | — | — | — | — |

### Por motivo de salida

| grupo | trades | acierto | ganancia media | pérdida media | expectativa | FB |
|---|---:|---:|---:|---:|---:|---:|
| cierre | 16 | 75.0% | +1.84 R | -0.52 R | +1.25 R | 10.64 |
| stop | 11 | 0.0% | — | -1.28 R | -1.28 R | 0.00 |

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
| con noticia en 24 h | 0 | 0.0% |
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
| gap_rvol_precio | 4520 |
| con_titular_resultados | 27 |
| senal | 27 |
| trades | 27 |

Por qué no hubo señal (peor intento de cada símbolo-día, puede sumar más de uno):

- rvol: 14211
- precio: 5321
- sin_titular_resultados: 4493

Sin dato (excluido, nunca contado como cero):

- gap_oficial: 978
- rvol_diario: 309

## Limitaciones (no se maquillan)

- Evento = titular de resultados de Benzinga entre el cierre previo y la apertura (sin IA), porque sec.gov responde 403 a los runners de GitHub Actions. Es un proxy más ruidoso que el 8-K 2.02.
- PLAN B EXPLORATORIO, solo lectura: no es la v2 ni toca producción.
- MANTIENE POSICIONES DE UN DÍA PARA OTRO: rompe la regla de cierre diario de la v1 y necesitaría gestión de riesgo nueva (gap nocturno, tamaño, halts fuera de sesión). Solo para medir.
- Entrada al cierre diario (barra diaria) + slippage; stop 6 % mirado sobre mínimos diarios: si el día abre por debajo del stop, se sale a la apertura. R = 6 %.
- Evento = 8-K 2.02 aceptado entre el cierre previo y la apertura (EDGAR, Z = UTC). Sin dato EDGAR el símbolo-día se excluye, nunca cuenta como «sin 8-K».
- RVOL diario = volumen del día / promedio de 20 sesiones previas (barras diarias con ajuste split).
- Tamaño 0,5 % del equity inicial entre el riesgo, sin capitalizar ni límites de cartera: mide expectativa por trade.
- Float, ETF y SPAC no se filtran aquí (el embudo llega hasta acción corporativa); universo: listados de hoy.
