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
- Universo, `DataProvider`, factores, catalizadores, patrones y tracking
  son 100% propios de este paquete.
- Los dos bots pueden convivir y complementarse (uno para posiciones de
  semanas/meses, el otro para trades de días), pero nunca se mezclan.

## Pivote 2026-07-26: de "screener" a "trader de momentum"

Pedido explícito del dueño del producto: "quiero que piense exactamente
como un trader profesional de momentum (Ross Cameron, Warrior Trading)
... el objetivo NO es encontrar buenas empresas, es encontrar
oportunidades ANTES de que el movimiento principal ocurra." Esto cambió
la arquitectura de raíz -- el pipeline ahora corre en **dos etapas**:

**Etapa 1 (gruesa, barras diarias)** -- el filtro de universo de
siempre: precio/cap/liquidez + catalizador + `scoring.puntuar` (40%
momentum/25% catalizador/20% liquidez/15% riesgo, igual que antes). Ya
NO decide si se alerta -- solo recorta el universo a los
`cfg.max_candidatos_intradia` (default 50) tickers que valen la pena
evaluar con datos intradía. Pedir velas de 1 minuto para miles de
tickers no es viable con ningún proveedor gratis.

**Etapa 2 (fina, velas de 1 minuto)** -- SOLO sobre esos candidatos:

1. **`factors/intradia.py`** calcula lo que un trader mira en la
   pantalla en tiempo real: VWAP real de hoy (ya no una aproximación),
   EMA9, RVOL inmediato (vela actual vs. las últimas 5, no el promedio
   de 20 días), aceleración de volumen, máximo del premarket, rango de
   apertura, gap real.
2. **`classification.py`** detecta uno de los seis patrones de Ross
   Cameron: Gap and Go, Opening Range Breakout, Bull Flag, Micro
   Pullback, High Tight Flag, Trend Continuation. Ya NO existen las
   categorías genéricas de antes del pivote (breakout/news
   momentum/earnings play/reversal/short squeeze) -- describían
   resultados sobre barras diarias, no formas del precio en tiempo real.
3. **`early_opportunity.py`** (Early Opportunity Engine, Prompt 2)
   responde "¿llegamos a tiempo?" -- una pregunta DISTINTA de "¿qué tan
   buena es la señal?". Un score alto NUNCA rescata una entrada tardía:
   el veredicto temprano/tarde sale de dos reglas duras (extensión desde
   VWAP/EMA9, velas desde que se activó el patrón), no del promedio.
4. **`evaluator.py`** corre las 5 preguntas de Prompt 4, en orden, cada
   una cortando o penalizando la anterior -- nunca un promedio que
   pueda "perdonar" una respuesta negativa con las demás.
5. Solo si el resultado es `accionable` se arma una alerta
   (`report.py`, formato de trader -- Prompts 3/5/6/7). Lo que quedó
   "cerca" pero no accionable alimenta el **Market Radar**
   (`radar.py`) en vez de desaparecer en silencio.

## Filosofía

> "Si solamente pudiera hacer una operación hoy, ¿esta sería una de
> ellas?" Si la respuesta es NO, no se manda ninguna alerta. Prefiero
> recibir una sola oportunidad excelente que veinte oportunidades
> promedio.

Determinístico de principio a fin -- cero LLM decidiendo nada (mismo
Principio #3 del `ROADMAP.md` raíz). El silencio es el resultado normal
y esperado cuando nada es accionable -- a diferencia del Investment
Analyst (que corre una vez al día y anuncia explícitamente "no encontré
nada" como confirmación de que sí corrió), este bot puede correr varias
veces al día, y repetir "nada hoy" sería exactamente el ruido que se
busca evitar.

## Arquitectura (cada módulo es reemplazable de forma independiente)

```
config.py             Universo, pesos del score y umbrales -- etapa 1 y etapa 2.
models.py             Barras/BarraIntradia/Metadata/Catalizador/
                       FactoresMomentum/FactoresIntradia/Oportunidad.
data/provider.py       DataProvider ABC + YahooProvider -- barras diarias Y velas
                       intradía (1m/5m). "El algoritmo nunca depende de Yahoo
                       específicamente": todo lo demás solo conoce BarraIntradia.
universe.py            NYSE+NASDAQ+AMEX completo desde NASDAQ Trader (no solo S&P 500).
factors/momentum.py     Etapa 1: Gap%, RVOL (20 días), breakout, EMA20/50, ATR, RSI,
                       MACD sobre barras diarias -- alimenta scoring.puntuar.
factors/intradia.py     Etapa 2: VWAP real, EMA9, RVOL inmediato, aceleración de
                       volumen, máximo premarket, rango de apertura, gap real.
catalysts/detector.py   Clasificador de catalizadores por keywords + minutos
                       transcurridos (para el Early Opportunity Engine).
classification.py      Los seis patrones de Ross Cameron -- pregunta 4 del evaluador.
early_opportunity.py    Early Opportunity Engine (Prompt 2): "¿llegamos a tiempo?".
evaluator.py            Árbol de 5 preguntas (Prompt 4) -- decide "accionable".
scoring.py              Score base 0-100 (etapa 1) -- 40% momentum + 25% catalizador +
                       20% liquidez + 15% riesgo. Cero valoración fundamental.
strategy.py             Selección de vehículo de opciones (Prompt 9) -- no conectado
                       todavía al mensaje nuevo, ver "Roadmap" abajo.
alerts.py               Recorte a la etapa 2 + filtro final por "accionable".
report.py               Mensaje de trader (Prompts 3/5/6/7): por qué apareció, por
                       qué ahora, qué patrón, dónde entro/salgo, qué invalida.
radar.py                Market Radar -- resumen de lo que quedó cerca pero no
                       accionable, agrupado por patrón.
tracker.py              Persiste cada alerta enviada (sin red).
outcomes.py             Actualiza resultados a 1/3/5/10 días con datos de mercado reales.
stats.py                Win rate, retorno promedio, drawdown máximo, expectancy,
                       Sharpe -- global y por tipo de oportunidad (Prompt 10).
run.py                  Orchestrator de las dos etapas -> alertas + radar -> Telegram -> tracker.
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

## Ejemplo de alerta (formato de trader, Prompt 3)

```
🔴 ACME -- rompiendo AHORA

Hace 12 min: fda -- "Company Receives FDA Approval" (Reuters).
El volumen se aceleró: RVOL de 4.0x ahora mismo.
Rompió el máximo del premarket ($5.00).
Gap de apertura y ya está extendiendo el movimiento.

Patrón: 🚀 GAP AND GO
Vamos temprano: el precio sigue cerca de sus anclas de corto plazo.

Entro: $5.20
Salgo (stop): $5.08
Objetivo: $5.44 -- reevalúo ahí, no es venta automática.

Qué invalida esto: Se cancela si vuelve a meterse debajo de $5.00 (el máximo del premarket).
Qué espero: Que aguante sobre el máximo del premarket en el próximo respiro.
```

## Filtros de universo (etapa 1)

| Filtro | Valor por defecto |
|---|---|
| Bolsas | NYSE, NASDAQ, AMEX |
| Precio | $0.75 - $20 |
| Capitalización | < $2,000 millones |
| Volumen promedio | ≥ 300,000 acciones/día |
| Excluye | ETFs, SPACs, closed-end funds, ADRs de baja liquidez |
| Candidatos que pasan a intradía | Top 50 por score, con catalizador confirmado |

## Limitaciones honestas (datos gratis)

- **Escanear el mercado completo es lento con datos gratis.** NYSE+NASDAQ+AMEX
  son ~8,000-11,000 símbolos; la etapa 1 (barras diarias) ya es cara, y
  la etapa 2 (velas de 1 minuto) SOLO corre sobre el top 50 por esa misma
  razón -- pedirlas para todo el universo, o correr esto cada pocos
  minutos sobre el mercado completo, no es viable sin que Yahoo bloquee
  el runner. Producción real de alta frecuencia necesitaría un
  `DataProvider` de pago con cotizaciones masivas (Polygon, Alpaca,
  Tradier) -- la interfaz ya lo permite sin tocar `factors/intradia.py`,
  `classification.py`, `early_opportunity.py` ni `evaluator.py`.
- **Sesión de mercado aproximada.** `factors/intradia.py` asume apertura
  13:30 UTC / cierre 20:00 UTC (horario de verano ET) para distinguir
  premarket de sesión regular -- se corre 1 hora en horario de invierno,
  mismo caveat que ya aceptan los cron de `.github/workflows/*.yml`.
- **Patrones intradía son aproximaciones numéricas sobre velas de 1
  minuto gratis (ruidosas)**, no el reconocimiento visual de un trader
  humano -- cada patrón exige condiciones explícitas documentadas en
  `classification.py`, nunca "se ve parecido a".
- **Borrow fee no existe gratis.** `Metadata.borrow_fee_pct` siempre es
  `None`.
- **Catalizadores** se detectan solo en los titulares que devuelve
  `yfinance` (`Ticker.news`) -- no hay integración con SEC EDGAR, FDA.gov
  ni terminales de noticias en tiempo real. `minutos_desde_catalizador`
  (usado por el Early Opportunity Engine) es `None` cuando la fuente solo
  da una fecha sin hora -- nunca se inventa la precisión que falta.

## Roadmap (fase 3)

- `DataProvider`/`NewsProvider` de pago para cotizaciones masivas y
  noticias en tiempo real con hora exacta (condición real para correr
  cada pocos minutos sobre el mercado completo).
- Reconectar `strategy.py` (selección de vehículo de opciones, Prompt 9)
  al mensaje nuevo -- quedó desacoplado del pivote a formato de trader
  porque el mockup de Prompt 3 no incluye la sección de estrategia;
  pendiente decidir cómo encaja sin alargar el mensaje.
- Integración con `risk_manager/` para sizing real de posición.
- Alertas de invalidación en vivo (avisar cuando el stop se activa o el
  objetivo se alcanza), en vez de solo medir el resultado al cerrar
  `outcomes.py`.
- Enviar el resumen de `stats.py` y el Market Radar por Telegram bajo
  demanda (comando, mismo espíritu que `/journal stats` del otro bot).
