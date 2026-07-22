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
3. **`/list`** (`report_command.generar_lista_completa`): la shortlist
   completa de hoy. El mensaje diario automático solo muestra el Top 10
   (`cfg.top_n_mensaje_diario`) para que sea corto y legible en segundos;
   `/list` muestra las hasta ~20 que sí pasaron el screener, con el mismo
   formato (emoji de sector + score) que el mensaje diario. Solo lee el
   archivo `shortlist_hoy.json` ya generado — no hace llamadas de red.
4. **`/options TICKER`** (`options_command.py`): ranking cuantitativo de
   estrategias de opciones para una empresa. `--full` para el detalle
   completo (Top 4 por defecto). Ver la sección dedicada más abajo.
5. **`/journal open|close|list|stats`** (`journal_command.py`): registro
   de paper trading. Ver la sección dedicada más abajo.
6. **`/trade TICKER`** (`trade_command.py`): el "tablero de trader" --
   el resumen ejecutivo de `/report` + `/options` en menos de 30 segundos
   de lectura. Ver la sección dedicada más abajo.

## Qué trae `/report TICKER`

- **Executive Summary**: calidad del negocio (⭐ desde el sub-score
  `calidad` de la shortlist), tendencia, valoración (Atractiva/Razonable/
  Exigente), riesgo principal y una conclusión de una frase. **100%
  determinístico** — plantillas de texto sobre números ya calculados,
  sin LLM (no hace falta: son solo reglas fijas, no una síntesis abierta).
- **Shortlist**: si el ticker pasó el screener hoy, su posición, score y
  por qué (reutiliza `screener.report.razones()`). Si no pasó, lo dice
  explícitamente — no pasar el screener hoy no significa que sea mala
  empresa, solo que no está en la shortlist de hoy.
- **Score breakdown**: el desglose factor por factor (peso × sub-score)
  que arma el score total — la misma fórmula exacta de
  `screener.scoring.puntuar()`, no una aproximación. Solo si el ticker
  está en la shortlist de hoy (si no, no hay sub-scores que desglosar).
- **Técnico**: precio actual, tendencia (medias móviles), volatilidad
  histórica anual, % del máximo de 52 semanas, RSI **con interpretación en
  lenguaje llano** (ej. "zona de sobrecompra"). Todo real, calculado al
  momento con `screener.factors.technical`.
- **Fundamentales**: P/E, P/B, ROE, margen operativo, crecimiento de
  ingresos (yfinance, best-effort), cada uno con su interpretación
  (rangos fijos, ej. P/E > 30 = "valuación exigente").
- **Consenso de analistas y tenencia**: recomendación, precio objetivo
  promedio, % en manos institucionales, % en manos de insiders. Son
  **snapshots reales de yfinance del día** (`recommendationKey`,
  `targetMeanPrice`, `heldPercentInstitutions`, `heldPercentInsiders`) —
  no el detalle de transacciones de insiders (Form 4) ni el histórico
  13F institucional: eso no está disponible gratis, así que no se inventa
  ni se aproxima. Si yfinance no responde, dice "No disponible", nunca un
  número inventado.
- **Riesgos principales** (máx. 3): banderas por reglas fijas y en orden
  de severidad — RSI extremo, valuación exigente, volatilidad alta,
  tendencia bajista. Si ninguna aplica, lo dice explícitamente en vez de
  forzar un riesgo que no existe.
- **Catalizadores**: solo lo que se puede confirmar con datos reales (hoy:
  la próxima fecha de resultados, vía `screener.options_ideas.
  proxima_fecha_resultados`). No se inventan lanzamientos de producto ni
  recompras de acciones sin una fuente real que los sostenga.
- **Noticias**: titulares reales (Google News RSS) sobre la empresa,
  **ordenados por relevancia** (Alta/Media/Baja, según
  `nivel_importancia` del LLM), no por fecha. Si el ticker está en la
  shortlist de hoy, cada titular se explica con LLM usando el mismo
  módulo y los mismos guardrails que `news_analyst` (nunca sugiere
  comprar/vender — ver `news_analyst/README.md`). Si no está en la
  shortlist, o si no hay `ANTHROPIC_API_KEY`/la llamada falla, los
  titulares van en un bloque "sin explicar" — nunca se fuerza una
  explicación sin contexto real que la sostenga.

## Por qué NO están (todavía) en `/report`

El pedido original incluía "Insider Activity" (transacciones) e
"Institutional Ownership" (13F histórico) como secciones separadas —
ambas requieren fuentes de datos que no están disponibles gratis (yfinance
solo da el % de tenencia snapshot, no el detalle transaccional). Se
muestra el snapshot real disponible, documentado como tal, en vez de
fabricar un historial que no existe.

`/history`, `/diff`, `/quality` — no existen todavía. Quedan para una
siguiente parte.

## Qué trae `/options TICKER`

Motor 100% determinístico (`screener/options_math.py` +
`screener/options_strategies.py`) sobre la cadena de opciones real del
ticker (`screener/options_ideas.obtener_cadena`) -- **el LLM nunca elige
strikes, nunca puntúa ni cambia el orden de las estrategias**, solo
traduce a lenguaje llano el ranking que el motor matemático ya calculó
(Principio #3 del AIOS, ver `ROADMAP.md`).

- **Tesis técnica** (Alcista/Bajista/Neutral): de `screener.options_ideas.
  clasificar_tendencia` sobre los mismos datos técnicos que ya usa
  `/report`.
- **9 de las 10 estrategias del spec**: Long Call, Long Put, Bull Call
  Spread, Bear Put Spread, Bull Put Spread, Bear Call Spread, Covered
  Call, Cash Secured Put, Iron Condor. Cada una con strikes reales de la
  cadena (elegidos por delta objetivo -- convenciones estándar: ~30 delta
  para el leg corto de spreads/covered call/cash secured put, ~16/8 delta
  para las alas del iron condor), riesgo máximo, ganancia máxima,
  breakeven(s), probabilidad de éxito (bajo la IV implícita, vía
  Black-Scholes), valor esperado (integrando el payoff real sobre esa
  misma distribución) y liquidez (open interest + bid-ask spread).
  **Calendar Spread no está incluido** -- necesita una segunda fecha de
  vencimiento, se puede agregar después.
- **Ranking puramente cuantitativo**: 50% valor esperado por dólar de
  riesgo, 30% probabilidad de éxito, 20% liquidez -- cada métrica
  normalizada a su percentil dentro de las estrategias candidatas (misma
  técnica de scoring cross-sectional que `screener/scoring.py` ya usa
  para los factores del stock screener) antes de combinarlas, para que
  ninguna domine solo por vivir en una escala distinta.
- **`--full` vs. por defecto**: por defecto muestra el Top 4 con
  estrellas relativas a este ranking; `--full` muestra las ~9 completas
  con el desglose entero (patas, Greeks netos, breakeven, probabilidad,
  valor esperado, liquidez).
- **Explicación del LLM**: un párrafo por estrategia explicando ventajas/
  desventajas/cuándo tendría sentido/qué la invalidaría -- nunca "cuál
  operar". Mismo guardrail de belt-and-suspenders que `news_analyst/
  explicador.py`: si el LLM se desvía y sugiere una acción de todas
  formas, la explicación se descarta entera antes de mostrarse. Sin
  `ANTHROPIC_API_KEY` o si la respuesta no pasa el filtro, el memo lo
  dice explícitamente en vez de omitirlo en silencio.
- **IV Rank: siempre "No disponible"**. Calcularlo de verdad requiere un
  histórico de IV (percentil de la IV actual contra ~1 año de historia)
  que este screener no recolecta hoy -- se documenta la ausencia, nunca
  se inventa un número.

## Qué trae `/journal`

Registro de paper trading (`journal_command.py` + el paquete `journal/`
en la raíz del repo). El objetivo: poder responder con datos reales, tras
30-50 operaciones, si el screener + /options realmente tienen ventaja
estadística -- no solo "sentir" que funcionan.

- **`/journal open TICKER ESTRATEGIA [motivo]`**: re-calcula la
  estrategia pedida con datos de opciones EN VIVO (mismo motor de
  `/options`, `screener/options_strategies.construir_estrategias`) y
  registra automáticamente: ticker, fecha, score y posición del screener
  ese día (si estaba en la shortlist), tesis técnica, la estrategia con
  todas sus patas/strikes/primas reales, costo, riesgo máximo, ganancia
  máxima, probabilidad de éxito, valor esperado, y el motivo (el que
  escribas, o la razón determinística de la estrategia si no escribes
  nada). `ESTRATEGIA` debe ser uno de los 9 nombres exactos que produce
  el motor (ej. "Bull Call Spread", "Iron Condor").
- **`/journal close TICKER RESULTADO [notas]`**: cierra la operación
  abierta más reciente de ese ticker con el resultado real en dólares
  (ej. `+150` o `-80`) que **tú reportas** -- este sistema no tiene
  acceso a tu cuenta de paper trading real, así que nunca inventa ni
  infiere un resultado.
- **`/journal list`**: operaciones abiertas.
- **`/journal stats`**: win rate, ganancia/pérdida promedio, expectancy,
  P&L total y drawdown máximo -- generales y desglosados por estrategia
  (`journal/stats.py`). Solo sobre operaciones CERRADAS; nunca estima
  nada sobre las abiertas.

Costo: para estrategias de débito (Long Call/Put, Bull Call Spread, Bear
Put Spread, y Covered Call/Cash Secured Put que comprometen capital) es
`riesgo_maximo` (lo que efectivamente arriesgas). Para estrategias de
crédito (Bull Put Spread, Bear Call Spread, Iron Condor) es
`-ganancia_maxima` (negativo: recibes esa prima al abrir).

Persistencia: `journal/journal.json` en el repo, leído/escrito vía la API
de contenidos de GitHub -- mismo patrón que `wizards_inbox.json`/
`wizards_state.json`, porque este servicio no tiene disco persistente en
Render.

Ningún LLM interviene en ningún punto de `/journal`: construir la
estrategia, calcular el costo, y las estadísticas son puro cálculo
determinístico.

## Qué trae `/trade TICKER`

Decisión de producto: `/report` y `/options` son correctos pero son
*reportes* -- este bot existe para ayudar a decidir, no solo para leer.
`/trade` (`trade_command.py`) reempaqueta exactamente los mismos números
que ya calculan `/report` y `/options` (ningún dato nuevo, ningún cálculo
nuevo) en un formato legible en menos de 30 segundos, con `/report`/
`/options` disponibles al final para quien quiera profundizar.

- **Mi opinión**: estrellas + un veredicto de una frase, puramente
  determinístico sobre el promedio de 6 señales (nunca lo redacta el
  LLM).
- **Confianza**: mide qué tan ALINEADAS están las señales entre sí
  (`abs(promedio - 0.5) * 2`) -- se etiqueta explícitamente como "% de
  señales alineadas, no una probabilidad de éxito" para no dar la
  impresión de que el sistema predice resultados (Principio #3).
- **¿Qué veo?**: 6 señales con estrellas/emoji -- Negocio (sub-score
  `calidad` de la shortlist), Tendencia, Valuación, Noticias (tono
  promedio de las explicadas hoy), Analistas (consenso yfinance), Riesgo
  (número de banderas reales de `report_command._riesgos`). Cualquier
  señal sin dato real dice "No disponible"/"No determinable", nunca se
  inventa.
- **Si hoy quisiera entrar...**: TODAS las estrategias que `/options`
  pudo construir, con estrellas relativas a ese ranking (mismo
  `_estrellas_por_posicion` de `options_command.py`), más "Comprar
  acciones" con las mismas estrellas del veredicto general (la única
  "estrategia" que no depende de la cadena de opciones).
- **¿Por qué?**: 3-4 bullets del LLM explicando por qué el motor
  determinístico puso primera a esa estrategia -- mismo guardrail
  belt-and-suspenders que `/options` (nunca sugiere abrir/comprar/vender;
  si se desvía, la explicación se descarta entera).
- **Ejemplo educativo**: la estrategia top con sus patas/strikes/primas
  reales, costo (`options_strategies.costo_apertura`), pérdida máxima,
  ganancia máxima, objetivo (precio objetivo REAL de analistas vía
  yfinance, o "No disponible" -- nunca inventado) y "Salir si" (el/los
  breakeven(s) reales de la estrategia).
- **¿Qué espero?**: escenarios (no una predicción) evaluando el payoff
  REAL de la estrategia (`options_strategies.evaluar_payoff`) en 3
  precios concretos: objetivo de analistas, precio actual sin cambios, y
  un movimiento del 15% en la dirección que perjudica a la estrategia
  (según el signo de su delta neto).
- **¿Qué eventos debo esperar?**: fecha real de próximos resultados y
  días restantes -- deliberadamente SIN una calificación de "qué tan
  importante será", porque eso requeriría un histórico de volatilidad
  alrededor de earnings pasados que este sistema no recolecta (fabricar
  esa cifra sería peor que omitirla).
- **Riesgos**: mismas banderas reales de `/report` (máx. 3, en orden de
  severidad).

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
