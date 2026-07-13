# Screener cuantitativo

Motor modular de generación de ideas: escanea el S&P 500 cada día, puntúa cada
acción con factores cuantitativos normalizados (0–100) y produce una
**shortlist de ~20 nombres** para investigación humana.

> "El computador busca; tú decides."

## Arquitectura (cada módulo es reemplazable de forma independiente)

```
universe.py        Universo a escanear (S&P 500 desde Wikipedia, cacheado)
data/provider.py   Abstracción de datos. YahooProvider (gratis) hoy;
                   escribir FMPProvider/SharadarProvider para datos de pago
                   NO obliga a tocar nada más (dependency injection).
factors/           Cálculo de factores crudos (momentum, tendencia, vol,
                   liquidez, RSI, 52-semanas...). Sin pandas: corre ligero.
scoring.py         Normalización cross-sectional a 0–100 + score compuesto
                   ponderado (pesos en config.py). Re-normaliza si faltan
                   datos, en vez de castigar con 0.
report.py          Formatea la shortlist (Telegram + markdown) con el "why".
run.py             Orchestrator: universo → datos → scoring → reporte → envío.
config.py          Pesos, universo y umbrales — un solo lugar para ajustar.
```

## Uso

```bash
pip install -r screener/requirements.txt
python -m screener.run                 # S&P 500 completo
python -m screener.run --limit 30      # subconjunto (pruebas rápidas)
python -m screener.run --no-fund       # solo factores de precio (sin yfinance)
```

Con `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` en el entorno, manda la shortlist
por Telegram. El workflow `.github/workflows/screener.yml` lo corre a diario
antes de la apertura.

## Factores actuales

| Factor | Peso | Fuente | Notas |
|--------|-----:|--------|-------|
| Momentum (3/6/12m) | 30% | precio | robusto con datos gratis |
| Tendencia (vs 50/100/200 DMA) | 20% | precio | robusto |
| Baja volatilidad | 15% | precio | robusto |
| Liquidez ($-volume) | 10% | precio | robusto |
| Calidad (ROE, márgenes) | 15% | fundamental | *best-effort* con yfinance |
| Valor (P/E, P/B inv.) | 10% | fundamental | *best-effort* con yfinance |

## Limitaciones honestas (datos gratis)

- **Fundamentales parciales:** yfinance da P/E, P/B, ROE y márgenes de forma
  irregular. Los factores fundamentales degradan con gracia (se re-normaliza
  el score sobre los factores presentes). Para grado institucional real
  —insider, institucional, estados financieros completos, todo el mercado—
  hay que conectar una fuente de pago escribiendo un `DataProvider` nuevo.
- **Solo S&P 500**, no las ~6,000 acciones de todo el mercado US: escanear
  todo con datos gratis es inviable (rate-limiting). Ampliar el universo =
  cambiar `config.universe` una vez que haya una fuente de datos que aguante.

## Roadmap (fase 2)

- Motor de backtesting (CAGR, Sharpe, Sortino, drawdown, alpha/beta) para
  validar qué combinación de factores funcionó en 10+ años.
- Factores fundamentales completos + insider/institucional (requiere datos
  de pago).
- Dashboard web (heatmap sectorial, top momentum/calidad/valor).
- Motor de riesgo de portafolio (VaR, correlaciones, sizing).
