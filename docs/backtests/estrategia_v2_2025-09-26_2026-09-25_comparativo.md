# Backtest v2 — comparativo de variantes (2025-09-26 a 2026-09-25)

Generado 2026-09-29 00:22 UTC / 18:22 Monterrey (UTC−6). Cada variante es UN cambio sobre la base; misma caché de clasificaciones, mismos datos. Salidas: stop / tiempo / objetivo / breakeven / cierre. MFE y MAE en R (promedio / mediana).

## Variantes

- **base**: config del YAML sin cambios
- **v1**: stop = mínimo de la vela de ruptura (1 min), acotado 1,5–4 %
- **v2**: spread máximo 0,6 % (base 0,3 %)
- **v3**: rango de apertura 09:30–09:45 y ventana 09:46–11:00
- **v4**: stop de tiempo 60 min (base 30)

## Resumen

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| base | 17 | 23.5% | +0.03 R | 1.15 | 1.1% | 1 / 12 / 2 / 1 / 1 | +0.57 R / +0.23 R | +0.47 R / +0.38 R |
| v1 | 62 | 17.7% | -0.24 R | 0.54 | 5.8% | 23 / 15 / 9 / 14 / 1 | +0.89 R / +0.47 R | +0.81 R / +0.87 R |
| v2 | 27 | 29.6% | +0.01 R | 1.03 | 1.7% | 1 / 20 / 3 / 1 / 2 | +0.52 R / +0.22 R | +0.49 R / +0.45 R |
| v3 | 9 | 22.2% | -0.13 R | 0.20 | 0.4% | 0 / 7 / 0 / 0 / 2 | +0.33 R / +0.14 R | +0.38 R / +0.30 R |
| v4 | 17 | 29.4% | +0.00 R | 1.01 | 1.3% | 1 / 10 / 2 / 3 / 1 | +0.71 R / +0.30 R | +0.54 R / +0.45 R |

## MFE de los trades que salieron por tiempo

Cuánto llegó a ir a favor cada uno antes del stop de tiempo (trades por tramo).

| variante | < 0.00 R | 0.00–0.25 R | 0.25–0.50 R | 0.50–1.00 R | 1.00–1.50 R | ≥ 1.50 R | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| base | 2 | 8 | 2 | 0 | 0 | 0 | 12 |
| v1 | 1 | 7 | 7 | 0 | 0 | 0 | 15 |
| v2 | 1 | 15 | 4 | 0 | 0 | 0 | 20 |
| v3 | 1 | 5 | 1 | 0 | 0 | 0 | 7 |
| v4 | 1 | 5 | 4 | 0 | 0 | 0 | 10 |

## Por rango de precio

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| base $2–10 | 5 | 40.0% | +0.45 R | 2.51 | — | 1 / 2 / 2 / 0 / 0 | +1.04 R / +0.56 R | +0.60 R / +0.27 R |
| v1 $2–10 | 31 | 29.0% | +0.05 R | 1.11 | — | 10 / 5 / 8 / 7 / 1 | +1.23 R / +1.17 R | +0.78 R / +0.81 R |
| v2 $2–10 | 10 | 30.0% | +0.05 R | 1.14 | — | 1 / 6 / 2 / 0 / 1 | +0.68 R / +0.38 R | +0.62 R / +0.66 R |
| v3 $2–10 | 1 | 0.0% | -0.23 R | 0.00 | — | 0 / 0 / 0 / 0 / 1 | +0.53 R / +0.53 R | +0.98 R / +0.98 R |
| v4 $2–10 | 5 | 40.0% | +0.47 R | 2.70 | — | 1 / 2 / 2 / 0 / 0 | +1.04 R / +0.56 R | +0.60 R / +0.30 R |
| base $10–20 | 4 | 25.0% | -0.02 R | 0.82 | — | 0 / 2 / 0 / 1 / 1 | +0.92 R / +0.89 R | +0.45 R / +0.37 R |
| v1 $10–20 | 15 | 6.7% | -0.46 R | 0.22 | — | 6 / 4 / 1 / 4 / 0 | +0.61 R / +0.32 R | +0.80 R / +0.88 R |
| v2 $10–20 | 6 | 33.3% | +0.02 R | 1.23 | — | 0 / 4 / 0 / 1 / 1 | +0.68 R / +0.29 R | +0.37 R / +0.29 R |
| v3 $10–20 | 3 | 33.3% | -0.00 R | 0.98 | — | 0 / 2 / 0 / 0 / 1 | +0.64 R / +0.25 R | +0.30 R / +0.28 R |
| v4 $10–20 | 4 | 25.0% | -0.10 R | 0.44 | — | 0 / 2 / 0 / 1 / 1 | +0.92 R / +0.89 R | +0.47 R / +0.42 R |
| base $20–50 | 8 | 12.5% | -0.21 R | 0.00 | — | 0 / 8 / 0 / 0 / 0 | +0.10 R / +0.12 R | +0.41 R / +0.42 R |
| v1 $20–50 | 16 | 6.2% | -0.60 R | 0.00 | — | 7 / 6 / 0 / 3 / 0 | +0.47 R / +0.25 R | +0.88 R / +0.92 R |
| v2 $20–50 | 11 | 27.3% | -0.04 R | 0.83 | — | 0 / 10 / 1 / 0 / 0 | +0.29 R / +0.13 R | +0.43 R / +0.45 R |
| v3 $20–50 | 5 | 20.0% | -0.19 R | 0.00 | — | 0 / 5 / 0 / 0 / 0 | +0.10 R / +0.13 R | +0.31 R / +0.30 R |
| v4 $20–50 | 8 | 25.0% | -0.24 R | 0.08 | — | 0 / 6 / 0 / 2 / 0 | +0.40 R / +0.23 R | +0.53 R / +0.49 R |

## Por nivel de catalizador

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| base nivel 1 | 11 | 18.2% | -0.00 R | 0.98 | — | 1 / 7 / 1 / 1 / 1 | +0.65 R / +0.25 R | +0.49 R / +0.38 R |
| v1 nivel 1 | 48 | 16.7% | -0.20 R | 0.60 | — | 16 / 12 / 7 / 12 / 1 | +0.93 R / +0.48 R | +0.79 R / +0.84 R |
| v2 nivel 1 | 20 | 25.0% | -0.03 R | 0.89 | — | 1 / 14 / 2 / 1 / 2 | +0.55 R / +0.24 R | +0.53 R / +0.46 R |
| v3 nivel 1 | 6 | 16.7% | -0.09 R | 0.37 | — | 0 / 4 / 0 / 0 / 2 | +0.46 R / +0.22 R | +0.40 R / +0.33 R |
| v4 nivel 1 | 11 | 27.3% | -0.05 R | 0.80 | — | 1 / 6 / 1 / 2 / 1 | +0.75 R / +0.36 R | +0.53 R / +0.45 R |
| base nivel 2 | 6 | 33.3% | +0.10 R | 1.41 | — | 0 / 5 / 1 / 0 / 0 | +0.42 R / +0.12 R | +0.43 R / +0.36 R |
| v1 nivel 2 | 14 | 21.4% | -0.40 R | 0.39 | — | 7 / 3 / 2 / 2 / 0 | +0.75 R / +0.40 R | +0.87 R / +1.03 R |
| v2 nivel 2 | 7 | 42.9% | +0.10 R | 1.56 | — | 0 / 6 / 1 / 0 / 0 | +0.45 R / +0.13 R | +0.38 R / +0.27 R |
| v3 nivel 2 | 3 | 33.3% | -0.23 R | 0.00 | — | 0 / 3 / 0 / 0 / 0 | +0.06 R / +0.07 R | +0.35 R / +0.30 R |
| v4 nivel 2 | 6 | 33.3% | +0.10 R | 1.43 | — | 0 / 4 / 1 / 1 / 0 | +0.63 R / +0.24 R | +0.55 R / +0.49 R |

## Señales y no entradas

| variante | señales | stop | tiempo | objetivo | breakeven | cierre | no entradas |
|---|---:|---:|---:|---:|---:|---:|---|
| base | 71 | 1 | 12 | 2 | 1 | 1 | stop_mayor_al_maximo 54 |
| v1 | 71 | 23 | 15 | 9 | 14 | 1 | stop_mayor_al_maximo 9 |
| v2 | 147 | 1 | 20 | 3 | 1 | 2 | stop_mayor_al_maximo 120 |
| v3 | 67 | 0 | 7 | 0 | 0 | 2 | stop_mayor_al_maximo 58 |
| v4 | 71 | 1 | 10 | 2 | 3 | 1 | stop_mayor_al_maximo 54 |
