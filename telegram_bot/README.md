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
- **`--full` vs. por defecto**: por defecto muestra el Top 4 con su
  Score real (0-100, `options_strategies.puntuar()` -- no estrellas);
  `--full` muestra las ~9 completas con el desglose entero (patas,
  Greeks netos, breakeven, probabilidad, valor esperado, liquidez).
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
`/trade` (`trade_command.py`) reempaqueta los mismos números que ya
calculan `/report` y `/options` (ningún cálculo nuevo) en un formato
legible en menos de 30 segundos, con `/report`/`/options --full` al
final para quien quiera profundizar. Sin estrellas en ningún lado
(tampoco en `/options`) -- solo scores numéricos reales.

100% determinístico, sin LLM -- ninguna sección depende de una llamada a
Claude (que podía fallar en silencio). Cada línea sale de reglas fijas
sobre números ya reales.

- **Score cuantitativo del modelo**: el score REAL del screener (0-100,
  `screener.scoring.puntuar()`, el mismo que aparece en la shortlist
  diaria) con emoji 🟢/🟡/🔴 por umbral -- "No disponible" si el ticker no
  está en la shortlist de hoy. Se llama "Score cuantitativo" y no
  "Confianza"/"Convicción" a propósito: ese nombre podía leerse como una
  probabilidad de ganar, que no es lo que mide (Principio #3).
- **Mi conclusión**: dos frases deterministas -- si compraría la acción
  hoy (según tendencia técnica, valoración y RSI) y si investigaría una
  estrategia de opciones.
- **Tesis del modelo vs. estrategia**: antes de presentar la estrategia
  top se compara la tesis del screener (Alcista/Bajista/Esperar/Neutral)
  con la dirección real de esa estrategia
  (`options_strategies.direccion_estrategia`). Si coinciden, se muestra
  como "Mejor estrategia"; si no, como "Si aun así quieres operar..." --
  el ranking matemático NUNCA cambia por esto, solo cómo se presenta (ver
  `_tesis_coincide_con_estrategia`), para no mostrar mensajes
  contradictorios como "No compraría acciones hoy" seguido de un Long
  Call sin más contexto.
- **¿Por qué [estrategia]?**: combina una explicación FIJA por tipo de
  estrategia (qué gana, qué arriesga, cuál es su problema estructural --
  hechos sobre cómo funciona ese tipo de estrategia, iguales para
  cualquier ticker) con hechos reales del día (RSI, precio vs. objetivo
  de analistas).
- **Ejemplo**: patas/strikes reales, costo máximo (`riesgo_maximo`) y
  ganancia máxima.
- **¿Qué tiene que pasar para que esta estrategia gane?**: reemplaza a
  una sección anterior de "escenarios" que a veces mostraba pérdida en
  los 3 precios evaluados (ej. un Long Call pierde tanto si el precio
  baja como si se queda igual) y leía como contradictorio. La condición
  de ganancia usa el breakeven y el vencimiento REALES -- estrategias de
  crédito (Bull Put Spread, Bear Call Spread) piden que el precio se
  "mantenga" del lado bueno; estrategias de débito (Long Call/Put, Bull
  Call/Bear Put Spread) piden que el precio "se mueva" hasta cruzarlo.
  "Lo que puede salir mal" es una lista fija por tipo de estrategia
  (verdades estructurales, no una predicción sobre este ticker).
- **Riesgos**: mismas banderas reales de `/report` (máx. 3, en orden de
  severidad).
- **Plan de acción**: solo aparece cuando la conclusión es "esperar".
  "Precio ideal para volver a revisar" usa la media móvil de 50 días
  (`screener.factors.technical.sma`) y el nivel de ruptura para
  reconsiderar usa el máximo de 52 semanas
  (`screener.factors.technical.maximo_52s`) -- niveles técnicos reales,
  no porcentajes inventados. El nivel de ruptura usa el máximo de 52
  semanas y no el breakeven de la estrategia, porque el breakeven mide
  dónde una estrategia de OPCIONES específica empieza a ganar, no dónde
  técnicamente se confirma que la tesis alcista de la ACCIÓN se
  reactivó (para una estrategia de ingreso como Covered Call, el
  breakeven queda por debajo del spot y no serviría como señal de
  ruptura al alza). Si hay fecha real de próximos resultados, también
  recuerda revisar 2 días antes.

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
