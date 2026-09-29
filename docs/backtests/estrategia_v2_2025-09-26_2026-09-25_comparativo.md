# Backtest v2 — comparativo de variantes (2025-09-26 a 2026-09-25)

Generado 2026-09-29 01:59 UTC / 19:59 Monterrey (UTC−6). Cada variante es UN cambio sobre la base; misma caché de clasificaciones, mismos datos. Salidas: stop / tiempo / objetivo / breakeven / cierre. MFE y MAE en R (promedio / mediana).

## Variantes

- **base**: config del YAML sin cambios
- **e1**: entrada por retroceso al máx. del ORB / VWAP, stop = mínimo del retroceso, espera 20 min
- **e2**: orden límite en el máximo del ORB tras la ruptura, válida 15 min
- **e3**: ruptura del máximo del premercado 09:31–09:45 con volumen ≥ 1,5×, stop = mínimo de los 3 primeros min
- **pead**: plan B: deriva post-resultados (8-K 2.02, gap ≥ 4 %, RVOL ≥ 3, $2–20; cierre día 0 → cierre día 3 o stop 6 %)
- **seguimiento**: plan B: nivel 1 al día siguiente (ruptura del máximo del día 1 en el día 2, stop 4 %, cierre EOD)
- **u1**: universo solo $2–20

## Resumen

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| base | 17 | 23.5% | +0.03 R | 1.15 | 1.1% | 1 / 12 / 2 / 1 / 1 | +0.57 R / +0.23 R | +0.47 R / +0.38 R |
| e1 | 50 | 18.0% | -0.27 R | 0.53 | 6.4% | 20 / 11 / 8 / 11 / 0 | +0.83 R / +0.65 R | +0.87 R / +0.79 R |
| e2 | 17 | 41.2% | +0.06 R | 1.27 | 0.7% | 2 / 12 / 2 / 0 / 1 | +0.62 R / +0.30 R | +0.39 R / +0.32 R |
| e3 | 0 | — | — | — | 0.0% | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| pead | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| seguimiento | 186 | 31.2% | -0.16 R | 0.71 | — | 87 / 0 / 0 / 0 / 99 | +1.11 R / +0.53 R | +0.80 R / +0.95 R |
| u1 | 9 | 33.3% | +0.24 R | 2.18 | 0.8% | 1 / 4 / 2 / 1 / 1 | +0.98 R / +0.56 R | +0.53 R / +0.28 R |

## MFE de los trades que salieron por tiempo

Cuánto llegó a ir a favor cada uno antes del stop de tiempo (trades por tramo).

| variante | < 0.00 R | 0.00–0.25 R | 0.25–0.50 R | 0.50–1.00 R | 1.00–1.50 R | ≥ 1.50 R | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| base | 2 | 8 | 2 | 0 | 0 | 0 | 12 |
| e1 | 2 | 4 | 4 | 1 | 0 | 0 | 11 |
| e2 | 0 | 5 | 7 | 0 | 0 | 0 | 12 |
| e3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| pead | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| seguimiento | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| u1 | 0 | 2 | 2 | 0 | 0 | 0 | 4 |

## Por rango de precio

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| base $2–10 | 5 | 40.0% | +0.45 R | 2.51 | — | 1 / 2 / 2 / 0 / 0 | +1.04 R / +0.56 R | +0.60 R / +0.27 R |
| e1 $2–10 | 23 | 21.7% | -0.19 R | 0.67 | — | 11 / 2 / 5 / 5 / 0 | +0.94 R / +0.70 R | +1.01 R / +1.07 R |
| e2 $2–10 | 5 | 20.0% | -0.09 R | 0.82 | — | 2 / 2 / 1 / 0 / 0 | +0.73 R / +0.49 R | +0.54 R / +0.18 R |
| e3 $2–10 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| pead $2–10 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| seguimiento $2–10 | 69 | 31.9% | -0.04 R | 0.94 | — | 37 / 0 / 0 / 0 / 32 | +1.61 R / +0.52 R | +0.87 R / +1.01 R |
| u1 $2–10 | 5 | 40.0% | +0.45 R | 2.51 | — | 1 / 2 / 2 / 0 / 0 | +1.04 R / +0.56 R | +0.60 R / +0.27 R |
| base $10–20 | 4 | 25.0% | -0.02 R | 0.82 | — | 0 / 2 / 0 / 1 / 1 | +0.92 R / +0.89 R | +0.45 R / +0.37 R |
| e1 $10–20 | 13 | 15.4% | -0.29 R | 0.50 | — | 5 / 3 / 2 / 3 / 0 | +0.82 R / +0.66 R | +0.76 R / +0.74 R |
| e2 $10–20 | 5 | 60.0% | +0.39 R | 4.77 | — | 0 / 3 / 1 / 0 / 1 | +1.02 R / +0.48 R | +0.34 R / +0.32 R |
| e3 $10–20 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| pead $10–20 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| seguimiento $10–20 | 60 | 31.7% | -0.24 R | 0.57 | — | 29 / 0 / 0 / 0 / 31 | +0.96 R / +0.70 R | +0.77 R / +0.91 R |
| u1 $10–20 | 4 | 25.0% | -0.02 R | 0.82 | — | 0 / 2 / 0 / 1 / 1 | +0.92 R / +0.89 R | +0.45 R / +0.37 R |
| base $20–50 | 8 | 12.5% | -0.21 R | 0.00 | — | 0 / 8 / 0 / 0 / 0 | +0.10 R / +0.12 R | +0.41 R / +0.42 R |
| e1 $20–50 | 14 | 14.3% | -0.38 R | 0.27 | — | 4 / 6 / 1 / 3 / 0 | +0.65 R / +0.45 R | +0.74 R / +0.76 R |
| e2 $20–50 | 7 | 42.9% | -0.07 R | 0.49 | — | 0 / 7 / 0 / 0 / 0 | +0.25 R / +0.25 R | +0.32 R / +0.34 R |
| e3 $20–50 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| pead $20–50 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| seguimiento $20–50 | 56 | 30.4% | -0.22 R | 0.57 | — | 20 / 0 / 0 / 0 / 36 | +0.69 R / +0.34 R | +0.73 R / +0.76 R |
| u1 $20–50 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |

## Por nivel de catalizador

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| base nivel 1 | 11 | 18.2% | -0.00 R | 0.98 | — | 1 / 7 / 1 / 1 / 1 | +0.65 R / +0.25 R | +0.49 R / +0.38 R |
| e1 nivel 1 | 41 | 17.1% | -0.33 R | 0.46 | — | 19 / 7 / 6 / 9 / 0 | +0.78 R / +0.64 R | +0.94 R / +1.00 R |
| e2 nivel 1 | 10 | 50.0% | +0.03 R | 1.10 | — | 2 / 6 / 1 / 0 / 1 | +0.73 R / +0.47 R | +0.43 R / +0.33 R |
| e3 nivel 1 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| pead nivel 1 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| seguimiento nivel 1 | 186 | 31.2% | -0.16 R | 0.71 | — | 87 / 0 / 0 / 0 / 99 | +1.11 R / +0.53 R | +0.80 R / +0.95 R |
| u1 nivel 1 | 7 | 28.6% | +0.05 R | 1.20 | — | 1 / 3 / 1 / 1 / 1 | +0.93 R / +0.56 R | +0.61 R / +0.46 R |
| base nivel 2 | 6 | 33.3% | +0.10 R | 1.41 | — | 0 / 5 / 1 / 0 / 0 | +0.42 R / +0.12 R | +0.43 R / +0.36 R |
| e1 nivel 2 | 9 | 22.2% | -0.00 R | 1.00 | — | 1 / 4 / 2 / 2 / 0 | +1.04 R / +1.20 R | +0.52 R / +0.20 R |
| e2 nivel 2 | 7 | 28.6% | +0.11 R | 1.59 | — | 0 / 6 / 1 / 0 / 0 | +0.47 R / +0.22 R | +0.33 R / +0.18 R |
| e3 nivel 2 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| pead nivel 2 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| seguimiento nivel 2 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| u1 nivel 2 | 2 | 50.0% | +0.93 R | 17.45 | — | 0 / 1 / 1 / 0 / 0 | +1.18 R / +1.18 R | +0.23 R / +0.23 R |

## Señales y no entradas

| variante | señales | stop | tiempo | objetivo | breakeven | cierre | no entradas |
|---|---:|---:|---:|---:|---:|---:|---|
| base | 71 | 1 | 12 | 2 | 1 | 1 | stop_mayor_al_maximo 54 |
| e1 | 53 | 20 | 11 | 8 | 11 | 0 | sin_entrada_e1 18, stop_mayor_al_maximo 3 |
| e2 | 59 | 2 | 12 | 2 | 0 | 1 | sin_entrada_e2 12, stop_mayor_al_maximo 42 |
| e3 | 8 | 0 | 0 | 0 | 0 | 0 | stop_mayor_al_maximo 8 |
| pead | 0 | 0 | 0 | 0 | 0 | 0 | — |
| seguimiento | 186 | 87 | 0 | 0 | 0 | 99 | — |
| u1 | 55 | 1 | 4 | 2 | 1 | 1 | stop_mayor_al_maximo 46 |
