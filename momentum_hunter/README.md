# Momentum Opportunity Hunter

> "¿Qué acción puede moverse fuerte HOY o en los próximos días?" -- no
> "¿vale la pena invertir en esta empresa?". Un problema completamente
> distinto al del Investment Analyst (`screener/`), resuelto con un
> proyecto completamente independiente.

## Por qué es un proyecto separado, no una extensión del screener

`screener/` (el Investment Analyst) puntúa el S&P 500 con factores
pensados para empresas grandes y establecidas: calidad de negocio (ROE,
márgenes), valoración (P/E, P/B), tendencia de largo plazo. Responde
"¿es una buena empresa para sostener semanas o meses?".

Este bot busca lo opuesto: penny stocks, small caps, low float y
high-relative-volume con un catalizador reciente, para un horizonte de
**1 a 10 días**. No hay solapamiento posible entre los dos criterios --
una empresa "de calidad" casi nunca es la que va a moverse 40% en tres
días, y viceversa. Por eso:

- **Cero código compartido** de valoración/calidad/scoring con `screener/`.
- **Cero fundamentales** (P/E, ROE, dividendos, márgenes) en el score.
- Universo, `DataProvider`, factores, catalizadores, clasificación,
  estrategia y tracking son 100% propios de este paquete.
- Los dos bots pueden convivir y complementarse (uno para posiciones de
  semanas/meses, el otro para trades de días), pero nunca se mezclan.

## Filosofía

> "¿Existe una probabilidad estadísticamente superior de que esta acción
> tenga un movimiento explosivo en los próximos días?" Si sí, alerta. Si
> no, se ignora. Prefiero perder oportunidades que recibir demasiadas
> falsas alarmas.

Determinístico de principio a fin -- cero LLM decidiendo nada (mismo
Principio #3 del `ROADMAP.md` raíz). Una alerta solo se manda cuando
**las cuatro condiciones del Prompt 7** se cumplen a la vez (score > 85,
catalizador confirmado, RVOL > 4x, liquidez suficiente) -- nunca por una
sola señal alta. Si nada califica, el bot **se queda en silencio** (ver
`alerts.py`): a diferencia del Investment Analyst (que corre una vez al
día y anuncia explícitamente "no encontré nada" como confirmación de que
sí corrió), este bot puede correr varias veces al día, y repetir "nada
hoy" en cada corrida sería exactamente el ruido que se busca evitar.

## Arquitectura (cada módulo es reemplazable de forma independiente)

```
config.py            Universo, pesos del score y umbrales de alerta -- un solo lugar.
models.py            Barras/Metadata/Catalizador/FactoresMomentum/Oportunidad.
data/provider.py      DataProvider ABC + YahooProvider (barras con open, float,
                      short interest, pre/after-market best-effort).
universe.py           NYSE+NASDAQ+AMEX completo desde NASDAQ Trader (no solo S&P 500).
factors/momentum.py    Gap%, RVOL, breakout, distancia a 52s, EMA20/50, VWAP proxy,
                      ATR, RSI, MACD -- sin pandas, corre ligero.
catalysts/detector.py  Clasificador de catalizadores por keywords (earnings, FDA,
                      contratos, adquisiciones, patentes, buybacks, insider buying,
                      upgrades, rumores con regla de múltiples fuentes).
classification.py     Etiqueta el TIPO de oportunidad: 🔥 BREAKOUT / ⚡ NEWS MOMENTUM /
                      🚀 SHORT SQUEEZE / 💰 EARNINGS PLAY / 📈 TREND CONTINUATION / 🔄 REVERSAL.
scoring.py             Score 0-100: 40% momentum + 25% catalizador + 20% liquidez +
                      15% riesgo. Cero valoración fundamental.
strategy.py            Decide el vehículo (Comprar acciones/Long Call/Bull Call
                      Spread/Cash Secured Put/No Operar) con justificación.
alerts.py              Filtro de envío (Prompt 7) + tope diario de 5 alertas.
report.py              Ensambla la Oportunidad final y arma el mensaje de Telegram
                      (Prompt 8).
tracker.py             Persiste cada alerta enviada (sin red).
outcomes.py            Actualiza resultados a 1/3/5/10 días con datos de mercado reales.
stats.py               Win rate, retorno promedio, drawdown máximo, expectancy,
                      Sharpe -- global y por tipo de oportunidad (Prompt 10).
run.py                 Orchestrator: universo → datos → score → alertas → Telegram → tracker.
```

## Uso

```bash
pip install -r momentum_hunter/requirements.txt

python -m momentum_hunter.run                       # NYSE+NASDAQ+AMEX completo
python -m momentum_hunter.run --limit 500            # subconjunto (recomendado, ver abajo)
python -m momentum_hunter.run --universo watchlist.txt   # watchlist propia
python -m momentum_hunter.run --dry-run              # calcula y muestra, no envía ni registra
python -m momentum_hunter.run --actualizar-resultados     # actualiza tracker + imprime stats
```

Con `MOMENTUM_TELEGRAM_BOT_TOKEN`/`MOMENTUM_TELEGRAM_CHAT_ID` (o, si no
existen, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`) en el entorno, manda
las alertas por Telegram. `.github/workflows/momentum_hunter.yml` corre
el escaneo varias veces al día en horario de mercado;
`.github/workflows/momentum_hunter_outcomes.yml` corre una vez al día
después del cierre para actualizar el tracking de resultados.

## Filtros de universo (Prompt 3)

| Filtro | Valor por defecto |
|---|---|
| Bolsas | NYSE, NASDAQ, AMEX |
| Precio | $0.75 - $20 |
| Capitalización | < $2,000 millones |
| Volumen promedio | ≥ 300,000 acciones/día |
| Excluye | ETFs, SPACs, closed-end funds, ADRs de baja liquidez |

## Limitaciones honestas (datos gratis)

- **Escanear el mercado completo es lento con datos gratis.** NYSE+NASDAQ+AMEX
  son ~8,000-11,000 símbolos; pedir barras+metadata por-ticker a Yahoo
  para todos ellos varias veces al día no es viable sin que Yahoo bloquee
  el runner (mismo riesgo que ya documenta `screener/`). `run.py` expone
  `--limit`/`--universo` para acotar el universo; producción real varias
  veces por hora necesitaría un `DataProvider` de pago con cotizaciones
  masivas (Polygon, Finnhub, IEX Cloud) -- la interfaz ya lo permite sin
  tocar el resto del pipeline.
- **VWAP es una aproximación**, no el VWAP intradía real (este proyecto
  no descarga ticks, solo barras diarias) -- ver `factors/momentum.py`.
- **Borrow fee no existe gratis.** `Metadata.borrow_fee_pct` siempre es
  `None`; se reporta así explícitamente en cada alerta en vez de fingir
  que se verificó.
- **Pre-market/after-hours** vía yfinance son best-effort e inconsistentes
  fuera de la ventana extendida -- pueden quedar en `None`.
- **Clasificación SPAC/CEF** es heurística por nombre (Yahoo no expone un
  flag dedicado) -- puede fallar en casos raros.
- **Catalizadores** se detectan solo en los titulares que devuelve
  `yfinance` (`Ticker.news`) -- no hay integración con SEC EDGAR, FDA.gov
  ni terminales de noticias en tiempo real; una fase 2 podría conectar
  esas fuentes sin tocar la interfaz `NewsProvider`.

## Roadmap (fase 2)

- `DataProvider`/`NewsProvider` de pago para cotizaciones masivas y
  noticias en tiempo real (condición real para correr cada pocos minutos
  sobre el mercado completo).
- Integración con `risk_manager/` para sizing real de posición (hoy
  `capital_minimo` es solo el capital de referencia de 100 acciones).
- Alertas de invalidación en vivo (avisar cuando el stop se activa o el
  objetivo se alcanza), en vez de solo medir el resultado al cerrar
  `outcomes.py`.
- Enviar el resumen de `stats.py` por Telegram bajo demanda (comando
  `/momentum stats`, mismo espíritu que `/journal stats` del otro bot).
