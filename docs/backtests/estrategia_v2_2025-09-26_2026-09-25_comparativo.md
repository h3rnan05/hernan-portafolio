# Backtest v2 — comparativo de variantes (2025-09-26 a 2026-09-25)

Generado 2026-09-29 02:15 UTC / 20:15 Monterrey (UTC−6). Cada variante es UN cambio sobre la base; misma caché de clasificaciones, mismos datos. Salidas: stop / tiempo / objetivo / breakeven / cierre. MFE y MAE en R (promedio / mediana).

## Variantes

- **peadnoticia**: plan B: PEAD con titular de resultados de Benzinga como evento (sec.gov da 403 en Actions)
- **u1_obj15**: universo solo $2–20; objetivo 1,5 R (base 2 R)
- **u1_st45**: universo solo $2–20; stop de tiempo 45 min (base 30)

## Resumen

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| peadnoticia | 27 | 44.4% | +0.22 R | 1.36 | — | 11 / 0 / 0 / 0 / 16 | +1.69 R / +1.43 R | +1.15 R / +0.82 R |
| u1_obj15 | 9 | 44.4% | +0.41 R | 3.04 | 0.8% | 1 / 4 / 4 / 0 / 0 | +0.82 R / +0.56 R | +0.53 R / +0.28 R |
| u1_st45 | 9 | 33.3% | +0.22 R | 1.92 | 0.8% | 1 / 4 / 2 / 1 / 1 | +0.98 R / +0.56 R | +0.53 R / +0.30 R |

## MFE de los trades que salieron por tiempo

Cuánto llegó a ir a favor cada uno antes del stop de tiempo (trades por tramo).

| variante | < 0.00 R | 0.00–0.25 R | 0.25–0.50 R | 0.50–1.00 R | 1.00–1.50 R | ≥ 1.50 R | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| peadnoticia | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| u1_obj15 | 0 | 2 | 2 | 0 | 0 | 0 | 4 |
| u1_st45 | 0 | 2 | 2 | 0 | 0 | 0 | 4 |

## Por rango de precio

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| peadnoticia $2–10 | 15 | 46.7% | +0.41 R | 1.87 | — | 6 / 0 / 0 / 0 / 9 | +2.09 R / +1.49 R | +1.16 R / +0.82 R |
| u1_obj15 $2–10 | 5 | 40.0% | +0.25 R | 1.82 | — | 1 / 2 / 2 / 0 / 0 | +0.81 R / +0.56 R | +0.60 R / +0.27 R |
| u1_st45 $2–10 | 5 | 40.0% | +0.44 R | 2.39 | — | 1 / 2 / 2 / 0 / 0 | +1.04 R / +0.56 R | +0.60 R / +0.30 R |
| peadnoticia $10–20 | 11 | 45.5% | +0.07 R | 1.10 | — | 4 / 0 / 0 / 0 / 7 | +1.27 R / +0.59 R | +1.13 R / +0.82 R |
| u1_obj15 $10–20 | 4 | 50.0% | +0.61 R | 8.92 | — | 0 / 2 / 2 / 0 / 0 | +0.83 R / +0.85 R | +0.45 R / +0.37 R |
| u1_st45 $10–20 | 4 | 25.0% | -0.06 R | 0.56 | — | 0 / 2 / 0 / 1 / 1 | +0.92 R / +0.89 R | +0.45 R / +0.37 R |
| peadnoticia $20–50 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| u1_obj15 $20–50 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| u1_st45 $20–50 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |

## Por nivel de catalizador

| variante | trades | acierto | expectativa | FB | drawdown | salidas stop / tiempo / objetivo / breakeven / cierre | MFE prom. / med. | MAE prom. / med. |
|---|---:|---:|---:|---:|---:|---|---|---|
| peadnoticia nivel 1 | 27 | 44.4% | +0.22 R | 1.36 | — | 11 / 0 / 0 / 0 / 16 | +1.69 R / +1.43 R | +1.15 R / +0.82 R |
| u1_obj15 nivel 1 | 7 | 42.9% | +0.34 R | 2.40 | — | 1 / 3 / 3 / 0 / 0 | +0.80 R / +0.56 R | +0.61 R / +0.46 R |
| u1_st45 nivel 1 | 7 | 28.6% | +0.03 R | 1.12 | — | 1 / 3 / 1 / 1 / 1 | +0.93 R / +0.56 R | +0.61 R / +0.46 R |
| peadnoticia nivel 2 | 0 | — | — | — | — | 0 / 0 / 0 / 0 / 0 | — / — | — / — |
| u1_obj15 nivel 2 | 2 | 50.0% | +0.66 R | 12.66 | — | 0 / 1 / 1 / 0 / 0 | +0.88 R / +0.88 R | +0.23 R / +0.23 R |
| u1_st45 nivel 2 | 2 | 50.0% | +0.86 R | 8.18 | — | 0 / 1 / 1 / 0 / 0 | +1.18 R / +1.18 R | +0.25 R / +0.25 R |

## Señales y no entradas

| variante | señales | stop | tiempo | objetivo | breakeven | cierre | no entradas |
|---|---:|---:|---:|---:|---:|---:|---|
| peadnoticia | 27 | 11 | 0 | 0 | 0 | 16 | — |
| u1_obj15 | 55 | 1 | 4 | 4 | 0 | 0 | stop_mayor_al_maximo 46 |
| u1_st45 | 55 | 1 | 4 | 2 | 1 | 1 | stop_mayor_al_maximo 46 |
