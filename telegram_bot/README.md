# Telegram Bot — chat + comandos de investigación

Servicio standalone en Render (ver el docstring de `app.py` para el
despliegue). Dos funciones separadas:

1. **Chat libre** (`idea_evaluator.py`): mandas una noticia/idea y Claude
   evalúa si merece una posición, con el criterio Market Wizards. Si el
   veredicto es INVERTIR, encola la idea para que `wizards_bot.py` la
   ejecute en su siguiente corrida (sujeta a sus límites de riesgo en código).
2. **`/report TICKER`** (`report_command.py`): memo de investigación
   bajo demanda para una empresa. Nuevo — nace de separar el mensaje
   diario del screener (que ahora es corto, ver `screener/report.
   texto_telegram_corto`) de la investigación profunda (que ahora se pide
   explícitamente, no se manda automáticamente todos los días).

## Qué trae `/report TICKER`

- **Shortlist**: si el ticker pasó el screener hoy, su posición, score y
  por qué (reutiliza `screener.report.razones()`). Si no pasó, lo dice
  explícitamente — no pasar el screener hoy no significa que sea mala
  empresa, solo que no está en la shortlist de hoy.
- **Técnico**: precio actual, tendencia (medias móviles), volatilidad
  histórica anual, % del máximo de 52 semanas, RSI. Todo real, calculado
  al momento con `screener.factors.technical`.
- **Fundamentales**: P/E, P/B, ROE, margen operativo, crecimiento de
  ingresos (yfinance, best-effort).
- **Consenso de analistas y tenencia**: recomendación, precio objetivo
  promedio, % en manos institucionales, % en manos de insiders. Son
  **snapshots reales de yfinance del día** (`recommendationKey`,
  `targetMeanPrice`, `heldPercentInstitutions`, `heldPercentInsiders`) —
  no el detalle de transacciones de insiders (Form 4) ni el histórico
  13F institucional: eso no está disponible gratis, así que no se inventa
  ni se aproxima. Si yfinance no responde, dice "No disponible", nunca un
  número inventado.
- **Noticias**: titulares reales (Google News RSS) sobre la empresa. Si
  el ticker está en la shortlist de hoy, cada titular se explica con LLM
  usando el mismo módulo y los mismos guardrails que `news_analyst`
  (nunca sugiere comprar/vender — ver `news_analyst/README.md`). Si no
  está en la shortlist, se listan los titulares crudos sin forzar una
  explicación sin contexto real que la sostenga.

## Por qué NO están (todavía) en `/report`

El pedido original incluía "Insider Activity" (transacciones) e
"Institutional Ownership" (13F histórico) como secciones separadas —
ambas requieren fuentes de datos que no están disponibles gratis (yfinance
solo da el % de tenencia snapshot, no el detalle transaccional). Se
muestra el snapshot real disponible, documentado como tal, en vez de
fabricar un historial que no existe.

`/options`, `/history`, `/diff`, `/quality` — no existen todavía (el spec
original los daba por existentes; no es así). Quedan para una siguiente
parte.

## Arquitectura

`report_command.py` reutiliza directamente `screener/` y `news_analyst/`
(inserta la raíz del repo en `sys.path` para importarlos, ya que Render
corre este servicio con `telegram_bot/` como root directory) — no hay una
"Research API" separada ni agentes con nombres propios (Research Bot,
Data Quality Agent, Evidence Engine...): es código real reutilizado, no
una capa de nombres sobre nada.

`generar_reporte(ticker)` solo corre sobre EL ticker pedido, nunca sobre
el universo completo — por eso es viable bajo demanda, a diferencia de
correr el screener completo (500 tickers) en cada mensaje de Telegram.

## Despliegue

Este servicio ya está desplegado en Render. Los cambios de este módulo
requieren que Render redeploye (automático si el servicio está
configurado con auto-deploy en push a `main`; si no, hay que redesplegar
manualmente desde el dashboard de Render).
