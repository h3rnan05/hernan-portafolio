# Telegram Bot — chat + comandos de investigación

Servicio standalone en Render (ver el docstring de `app.py` para el
despliegue). Dos funciones separadas:

1. **Chat libre** (`idea_evaluator.py`): mandas una noticia/idea y Claude
   evalúa si merece una posición, con el criterio Market Wizards. Si el
   veredicto es INVERTIR, encola la idea para que `wizards_bot.py` la
   ejecute en su siguiente corrida (sujeta a sus límites de riesgo en código).
2. **`/report TICKER`** (`report_command.py`): decide en menos de un
   minuto si vale la pena investigar la empresa. `--full` para el memo
   exhaustivo. Nace de separar el mensaje diario del screener (que ahora
   es corto, ver `screener/report.texto_telegram_corto`) de la
   investigación profunda (que ahora se pide explícitamente, no se manda
   automáticamente todos los días). Ver la sección dedicada más abajo.
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

Filosofía (redefinida 2026-07-23 tras feedback directo: el reporte
anterior parecía una hoja de datos para un analista, no la opinión de un
gestor para un inversionista): el objetivo NO es mostrar todos los datos
disponibles. Es ayudar a decidir en menos de un minuto si vale la pena
seguir investigando. Sin `--full`, cualquier dato que no esté disponible
se **omite** en vez de mostrarse como "No disponible" — esta vista existe
para decidir rápido, no para auditar qué faltó.

- **🎯 Mi plan para hoy** (primerísima sección, antes de "En 20
  segundos" -- pedido explícito: "eso resume absolutamente todo"): la
  decisión, el porqué en una frase (`_por_que_una_linea`, ver abajo) y
  una sola alerta ya lista para crear ("✅ Ya puedes crear esta alerta" --
  esa es literalmente la siguiente acción). Para quien solo lee las
  primeras líneas.
- **📌 En 20 segundos**: dos veredictos independientes, cada uno con su
  propia pregunta. **¿Vale la pena investigarla?**
  (`_veredicto_investigar`) se basa en calidad fundamental del negocio —
  el sub-score `calidad` de la shortlist, o si ya pasó todos los filtros
  hoy — nunca en si es buen momento para comprar. **¿Comprar hoy, esperar
  o descartarla?** (`_timing`) es una pregunta de timing separada: pasó
  el screener hoy + tendencia técnica + RSI. Un negocio puede valer la
  pena investigar el mismo día que no es buen momento para comprar (y
  viceversa) — separar ambas preguntas evita el problema del diseño
  anterior, donde el reporte nunca respondía "¿la compro o no?" de forma
  directa. También muestra precio actual, el rango de entrada que sí
  interesaría (mismos niveles ATR/SMA50 que ya usa `/trade`, vía
  `screener.factors.technical.niveles_precio`) y el objetivo de
  analistas.
- **🎯 ¿Por qué me gusta? / ⚠️ ¿Qué no me gusta?**: fortalezas y
  debilidades concretas, cada una con el número real que la sostiene (ej.
  "Crecimiento de ingresos de 30%.", "Valuación barata (P/E 14.9)."),
  nunca una etiqueta genérica sin verificar. 100% determinístico.
  `_por_que_una_linea` reusa estos mismos bullets (hasta 3) para componer
  UNA frase de justificación ("Porque...") que aparece en "Mi plan para
  hoy" y en "Qué haría yo" -- pedido explícito: "Sí la compraría hoy"
  sin decir por qué no bastaba.
- **📊 Lo importante**: la conclusión en lenguaje llano va ARRIBA ("Empresa
  rentable.", "Acción barata.", "No está sobrecomprada.") y el dato
  técnico crudo (P/E, ROE, RSI, etc.) va ABAJO en una sola línea compacta
  — pedido explícito: la mayoría de la gente no sabe interpretar un ROE o
  un P/E sueltos, el bot debe pensar como un asesor, no como una terminal
  de Bloomberg. Cualquier dato no disponible se omite en ambos niveles.
- **📰 Lo que pasó esta semana**: un resumen de una frase + hasta 3 hechos
  concretos (`news_analyst.explicador.resumir_noticias`), no una lista de
  titulares sueltos — la única sección de `/report` (modo simple) que usa
  LLM, con los mismos guardrails de siempre (prompt + filtro de palabras
  prohibidas, nunca sugiere comprar/vender). Si el LLM no está disponible
  o su respuesta no pasa el filtro, se degrada mostrando hasta 3 titulares
  crudos en vez de dejar la sección vacía en silencio.
- **🎯 Qué haría yo**: cierre determinístico con el mismo "Porque..." de
  "Mi plan para hoy" (misma frase, no se recalcula).
- **🔔 Comprar si ocurre UNA de estas cosas**: reencuadra los niveles de
  precio como una lista de condiciones "o" (rebota en el soporte / rompe
  el máximo de 52 semanas / después de los resultados trimestrales) en
  vez de una tabla de precios sueltos sin conexión entre sí — pedido
  explícito: así es como de verdad se piensa una alerta. "Cancelar" se
  mantiene aparte. Mismo motor de niveles que `/trade` (ATR + SMA50 +
  máximo de 52 semanas), nunca un número inventado.

Se eliminó el "Nivel de confianza del reporte: X%" (un asesor no dice
"tengo 57% de confianza") y la sección "Preguntas que debo responder"
(el bot debe responderlas, no dejarlas de tarea al usuario).

### `/report TICKER --full`

El memo exhaustivo de antes, debajo de las mismas secciones de arriba:
Executive Summary (con estrellas de calidad **solo si el dato existe** —
ya no se muestra "Calidad del negocio: No disponible"), posición en la
shortlist y por qué (`screener.report.razones()`), score breakdown
factor por factor (misma fórmula exacta de `screener.scoring.puntuar()`),
técnico y fundamentales completos con interpretación en lenguaje llano,
consenso de analistas y tenencia (snapshot real de yfinance — no el
detalle de transacciones de insiders ni el histórico 13F institucional,
eso no está disponible gratis), riesgos y catalizadores, y noticias
clasificadas por relevancia (Alta/Media/Baja según `nivel_importancia`
del LLM, cada titular explicado individualmente). Aquí "No disponible"
sigue siendo honesto: esta vista existe para mostrar TODO lo que se
intentó obtener.

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
- **Coherencia con la tesis (por defecto, no en `--full`)**: se ocultan
  las estrategias cuya dirección (`options_strategies.
  direccion_estrategia`) contradice la tesis técnica -- regresión de un
  caso real donde el Top 1 era "Bear Put Spread" (bajista) con la tesis
  técnica en "Alcista" en el mismo mensaje, lo cual rompía la confianza
  ("¿por qué me recomiendas una estrategia bajista si la tesis es
  alcista?"). Las estrategias de dirección "neutral" (ej. Iron Condor)
  nunca se ocultan -- no contradicen ninguna tesis direccional, apuestan
  a que el precio se quede quieto. El ranking matemático NUNCA cambia por
  esto (`_coincide_con_tesis`), solo qué se muestra por defecto; si
  ninguna estrategia coincide, se cae al Top N normal con una nota
  explícita en vez de mostrar una lista vacía. `--full` sigue mostrando
  las 9 sin filtrar.
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
calculan `/report` y `/options` (ningún cálculo nuevo). Sin estrellas en
ningún lado (tampoco en `/options`) -- solo scores numéricos reales.

Filosofía (redefinida 2026-07-23 tras feedback directo: "no quiero leer
60 líneas, quiero saber si compro o no, a qué precio, dónde el stop,
cuál el objetivo, qué estrategia, cuánto capital y qué alerta pongo"):
el modo por defecto ("simple") responde exactamente esas 7 preguntas en
menos de 20 líneas. `/trade TICKER --full` conserva el tablero completo
de antes para quien sí quiera profundizar -- nada de ese detalle se
perdió, solo se dejó de mostrar por defecto.

### `/trade TICKER` (modo simple, por defecto)

- **🎯 Mi decisión**: un solo emoji + veredicto (🟢 Sí compraría hoy / 🟡
  No compraría hoy, esperaría / 🔴 tendencia bajista / ⚪ sin señal clara
  o sin información suficiente) sobre la misma `_tesis_categoria` de
  siempre (alcista/bajista/esperar/neutral/no_determinable).
- **Entrada ideal / Stop / Objetivo**: mismos niveles ATR/SMA50/máximo de
  52 semanas de siempre (`screener.factors.technical.niveles_precio`),
  con la relación riesgo/beneficio real junto al objetivo -- nunca
  forzada a un múltiplo bonito.
- **Horizonte**: el vencimiento REAL de la opción elegida.
- **Capital mínimo**: solo dos cifras -- comprar 100 acciones y la
  estrategia top (`top.riesgo_maximo`) -- no las 9 estrategias completas
  (eso vive en `--full`).
- **La estrategia que usaría**: el Top 1 del ranking. Si su dirección
  (`options_strategies.direccion_estrategia`) NO coincide con la tesis
  técnica, se marca explícitamente ("no coincide con la tesis de hoy --
  ver --full") en vez de presentarla sin más contexto -- misma capa de
  coherencia de siempre (`_tesis_coincide_con_estrategia`), el ranking
  matemático nunca cambia.
- **¿Por qué?**: una sola frase que reusa hechos ya calculados
  (tendencia, valuación, crecimiento de ingresos, RSI, objetivo de
  analistas) -- nunca un dato nuevo ni una llamada a Claude. Omitida por
  completo si no hay ningún hecho real que la sostenga.
- **Próximo paso**: la alerta concreta a crear, con el mismo nivel ya
  mostrado en "Entrada ideal" -- ningún número nuevo.

### `/trade TICKER --full`

100% determinístico, sin LLM -- ninguna sección depende de una llamada a
Claude (que podía fallar en silencio). Cada línea sale de reglas fijas
sobre números ya reales.

- **En 15 segundos** (primera sección del mensaje): para quien tiene
  prisa -- el veredicto sobre acciones, hacia dónde esperaría una
  corrección (si aplica), qué ruptura haría reconsiderar, la mejor
  estrategia de hoy, el horizonte real (vencimiento de la opción) y el
  nivel de riesgo. Todo reusa valores ya calculados más abajo -- ningún
  cálculo nuevo, es solo el resumen ejecutivo del resto del mensaje.
- **Semáforo del modelo**: reemplaza a una versión anterior ("Score de
  oportunidad hoy") que mostraba un % por acción posible (Comprar
  acciones/Comprar opciones/Esperar) -- se retiró porque un usuario lee
  naturalmente un % como "probabilidad de ganar", aunque la etiqueta
  dijera "% de reglas objetivas". El Semáforo usa emoji + texto
  cualitativo en su lugar (🟢/🟡/🔴 por tendencia/valoración/RSI para
  Acciones; 🟢/🟡/🔴 por el score del ranking para Opciones), y resuelve
  en la misma línea la contradicción "Comprar opciones: sí, pero no
  abriría hoy" en vez de dejar que el usuario la infiera -- si la
  conclusión es "esperar", la línea de Opciones lo dice explícitamente
  ("Sí investigaría, pero no abriría hoy").
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
- **Plan del trade** (justo antes de "Ejemplo", solo cuando la conclusión
  es "esperar"): tabla compacta Entrada/Objetivo 1/Objetivo 2/Stop, ver
  detalle más abajo. Se muestra aquí -- inmediatamente después de conocer
  la estrategia elegida y antes de ver sus strikes concretos -- en vez de
  al final del mensaje, para que el plan de niveles quede junto a la
  estrategia a la que pertenece.
- **Ejemplo**: patas/strikes reales, costo máximo (`riesgo_maximo`) y
  ganancia máxima.
- **Capital mínimo recomendado** (siempre que haya estrategias
  construidas, no solo cuando la conclusión es "esperar"): reempaqueta
  `riesgo_maximo` -- que para Covered Call y Cash Secured Put ya
  representa el capital real comprometido, no solo la prima -- de cada
  estrategia ya construida y rankeada, más el costo de comprar 100
  acciones al precio actual. Ningún cálculo nuevo, solo agrupado para
  planear cuánto capital necesitas antes de decidir cuál estrategia
  perseguir.
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
- **Horizonte esperado**: el vencimiento REAL de la opción elegida
  (nunca un rango inventado como "3-6 semanas" sin base) -- se dice
  explícitamente que el trade está atado a esa fecha, para que no se
  confunda con una tesis de inversión de largo plazo.
- **Confianza en este plan**: lista fija de factores reales a favor/en
  contra (tendencia, RSI, valuación, liquidez de las opciones,
  crecimiento de ingresos) con un % de cuántos aplican -- cada factor
  solo aparece si hay un dato real que lo sostenga (nunca se fuerza uno
  neutral hacia un lado). Se etiqueta explícitamente como "factores
  objetivos a favor vs. en contra", nunca como probabilidad de éxito.
- **Riesgos**: mismas banderas reales de `/report` (máx. 3, en orden de
  severidad).
- **Plan de acción** (solo cuando la conclusión es "esperar"): responde 4
  preguntas fijas -- ✅ a qué precio me interesaría comprar, 🚀 en qué
  ruptura también me interesaría, ❌ a qué precio cancelaría la idea, ⏰
  cuándo volver a revisar -- con 4 niveles de precio calculados por
  reglas objetivas (`_niveles_precio`), nunca inventados por el LLM:
    - **Entrada**: spot − 1×ATR (`screener.factors.technical.atr`, mismo
      cálculo y periodo que ya usa `wizards_bot.py` para su stop de
      Turtle Trading).
    - **Ideal**: el más profundo entre la entrada y la media móvil de 50
      días (`technical.sma`) -- nunca queda por encima de la entrada.
    - **Cancelar**: 2×ATR por debajo del nivel ideal (mismo múltiplo de
      stop que ya usa `wizards_bot.py` en producción, no uno inventado
      para esta ocasión).
    - **Ruptura para reconsiderar**: el máximo de 52 semanas
      (`technical.maximo_52s`), no el breakeven de la estrategia --el
      breakeven mide dónde una estrategia de OPCIONES específica empieza
      a ganar, no dónde técnicamente se confirma que la tesis alcista de
      la ACCIÓN se reactivó (para una estrategia de ingreso como Covered
      Call, el breakeven queda por debajo del spot y no serviría como
      señal de ruptura al alza).
  Si hay fecha real de próximos resultados, también recuerda revisar 2
  días antes. Cualquier nivel que no se pueda calcular con los datos
  disponibles simplemente no aparece.
- **Plan del trade** (ver arriba dónde aparece en el mensaje -- misma
  condición que el Plan de acción, tesis "esperar"): tabla compacta
  Entrada/Objetivo 1/Objetivo 2/Stop. Objetivo 1 es el máximo de 52
  semanas (mismo nivel real de "ruptura para reconsiderar"); Objetivo 2
  es una extensión de igual distancia más allá del objetivo 1 ("measured
  move", convención técnica estándar, no un número arbitrario). La
  relación riesgo/beneficio se muestra junto a CADA objetivo (no como una
  sola línea) porque el objetivo 1 puede quedar cerca de la entrada justo
  cuando el precio ya está cerca de máximos -- exactamente el escenario
  donde esta sección se activa (tesis "esperar" por sobrecompra). Mostrar
  la relación real, aunque sea modesta, es más honesto que forzar un
  múltiplo fijo que no refleje el nivel técnico real.
- **Alertas para Yahoo Finance** (misma condición que el Plan de acción):
  reempaqueta los mismos 4 niveles ya calculados en formato listo para
  configurar alertas de precio, cada uno con una línea "¿Por qué?"
  (de dónde sale ese nivel: ATR, media móvil de 50 días, o máximo de 52
  semanas) -- ningún cálculo nuevo. Cada alerta también incluye, si hay
  volatilidad anualizada disponible, un "¿Cuándo?" con un estimado (nunca
  una garantía) de en cuántos días de operación podría activarse
  (`_dias_estimados`, escalamiento de varianza en el tiempo σ√t sobre la
  distancia real hasta el nivel) -- se muestra como un rango (mitad al
  doble del estimado central), nunca un solo número, porque el
  movimiento de precios es aleatorio. Si la volatilidad medida es tan
  baja que el estimado se dispara a una cifra sin sentido (cientos de
  miles de días), la línea simplemente se omite en vez de mostrar un
  número absurdo.
- **Mi decisión hoy** (última sección del mensaje, antes de "Próximo
  paso", siempre presente): resume todo el reporte en una sola acción
  concreta -- para quien ya leyó todo, o se saltó directo al final.
  Ningún dato nuevo: reempaqueta la misma tesis y los mismos niveles ya
  calculados arriba. Si la conclusión es "esperar", lista hasta 2 alertas
  concretas (entrada y ruptura); en cualquier otro caso, una frase de
  cierre acorde a la tesis (alcista/bajista/neutral/no determinable).

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
