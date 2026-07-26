# Roadmap — Autonomous Investment Operating System (AIOS)

Ya no es "un bot de trading" — es la base de una plataforma cuantitativa de
inversión institucional. Filosofía:

> El computador investiga. Los modelos deciden. El risk manager aprueba.
> La IA explica. El bróker ejecuta.

## Principios duros (no negociables)

1. **Preservar capital antes que rentabilidad.**
2. **Toda decisión debe ser determinística** — reglas fijas, reproducibles.
3. **La IA (LLMs) NUNCA decide qué comprar o vender.** Su trabajo es
   explicar decisiones, resumir research, leer filings/earnings calls,
   resumir noticias, generar reportes. Punto.
4. Toda decisión de inversión la genera un modelo cuantitativo con reglas
   predefinidas — nunca el juicio de un LLM.
5. Cada módulo es independiente y reemplazable.

## ⚠️ Deuda de arquitectura conocida

`telegram_bot/idea_evaluator.py` y el explorador de noticias de
`wizards_bot.py` **violan el Principio #3 hoy**: Claude decide
`INVERTIR`/`NO_INVERTIR` directamente. Se corrige cuando el Decision Engine
exista — el LLM pasará a solo extraer hechos estructurados (ticker, tesis,
catalizador) de lo que el usuario escribe; la decisión la toma el Decision
Engine con reglas fijas. Anotado, no arreglado todavía — se hace por partes.

## Capital real

$44,000. No se autoriza a operar real hasta pasar el Validation Pipeline
completo (abajo). No es opcional ni negociable con prisa.

## Mapeo de agentes → estado actual del código

| # | Agente | Responsabilidad | Estado |
|---|--------|------------------|--------|
| 1 | **Market Data Agent** | precios, fundamentales, macro, insider, institucional, calendario económico — todo cacheado localmente | 🟡 Parcial — `screener/data/provider.py` (`DataProvider` ABC + `YahooProvider`) cubre precios y fundamentales best-effort. Falta: macro, insider, institucional, opciones |
| 2 | **Research Agent** | escanea el universo, calcula Quality/Growth/Value/Momentum/Volatility/Liquidity, genera shortlist — **NO son señales de compra** | ✅ Hecho — `screener/` completo. El mensaje diario a Telegram ya NO es la shortlist corta (`texto_telegram_corto`, ahora solo se calcula/loguea, no se envía) -- pedido explícito 2026-07-23: "ya no quiero solamente saber qué empresas pasaron el screener... quiero descubrir oportunidades antes de que la mayoría del mercado las vea". El mensaje diario real ahora es el Opportunity Hunter (fila 8). `shortlist_hoy.json`/`.md` se siguen persistiendo igual que antes, así que `/report`/`/options`/`/trade`/`/list` no cambian |
| 3 | **Fundamental Analysis Agent** | lee 10-K/10-Q/earnings calls/presentaciones, arma tesis estructurada (fortalezas/debilidades/ventajas/riesgos/catalizadores/valuación) — nunca decide comprar | ❌ No existe |
| 4 | **Technical Analysis Agent** | medias móviles, ATR, RSI, MACD, ADX, momentum, breakouts, soporte/resistencia, tendencia — solo información | 🟡 Parcial — `screener/factors/technical.py` tiene medias, ATR, RSI, momentum. Falta MACD, ADX, soporte/resistencia explícitos |
| 5 | **Macro Agent** | Fed, tasas, inflación, PIB, empleo, petróleo, oro, DXY, treasuries, geopolítica — nunca compra | ❌ No existe |
| 6 | **Portfolio Optimizer** | construye el portafolio óptimo desde candidatos: max retorno esperado, min riesgo, respeta límites (sector, tamaño posición, cash mínimo, beta, correlación, drawdown objetivo) | 🟢 Parte 1 hecha — `portfolio_optimizer/` (standalone, 26 tests, sin LLM/red): ranking por retorno ajustado por riesgo × calidad factorial, asignación voraz respetando posiciones/sector/posición/cash/beta/volatilidad/correlación, arquitectura con algoritmo intercambiable (`OptimizationStrategy`). **Falta**: MVO/Risk Parity/Black-Litterman/HRP/ERC/Min-Variance reales (la interfaz ya los soporta), maximum drawdown (requiere backtesting), maximum heat (vive en `risk_manager`). **Falta integrar** con `wizards_bot`/Decision Engine |
| 7 | **Risk Manager** | ⭐ el módulo más importante — **veto power**. Riesgo máx/trade, calor máx portafolio, correlación máx, exposición sectorial máx, volatilidad máx, drawdown máx, stop por ATR, position sizing, VaR, stress testing. Si algo falla → rechaza | 🟢 Parte 1 hecha — `risk_manager/` (standalone, 14 tests, sin LLM/red): promediar, máx. posiciones, ATR stop, sizing por riesgo, tope de posición, calor, sector, reserva de cash. **Falta parte 2**: correlación, beta, drawdown objetivo, VaR, stress testing. **Falta integrar** con `wizards_bot`/Decision Engine |
| 8 | **Decision Engine** | recibe SOLO trades ya aprobados por el Risk Manager. Output: BUY / SELL / HOLD / **DO NOTHING**. "No trades today" es una salida válida y esperada, nunca se fuerza un trade | 🟡 Parcial — `screener/opportunity_hunter.py` (100% determinístico, sin LLM, corre automáticamente después del screener cada día vía `screener/run.py`): escanea el universo COMPLETO ya validado (no solo el Top 20 de la shortlist) buscando 3 patrones donde 3-4 señales independientes coinciden a la vez (ruptura confirmada con volumen, pullback sano en tendencia fuerte, infravalorada con impulso ya positivo) -- nunca dispara por un solo score alto. Si nada coincide, el mensaje del día dice explícitamente "Hoy no encontré ninguna oportunidad que cumpla mis estándares. No abriría ninguna posición." -- un no-resultado válido y esperado, nunca se fuerza contenido (pedido explícito: "prefiero recibir 2 oportunidades excelentes por semana que 20 mediocres por día"). Cada oportunidad trae Convicción (reusa los sub_scores reales de `scoring.puntuar()`, nunca inventado), decisión Comprar hoy/Esperar/No operar (reglas fijas: liquidez mínima, evitar earnings próximos), niveles de entrada/stop/objetivo (mismo motor ATR/SMA50 de `/trade`), estrategia de opciones recomendada (solo para los 0-3 tickers que ya dispararon un patrón, nunca el universo) y urgencia. Refinado tras el primer envío real (feedback directo, 2026-07-25): "Comprar hoy" ya explica en el propio mensaje que el precio de hoy está dentro de la zona que activó el patrón (nunca queda contradictorio con "Entrada ideal" -- ver el docstring del módulo sobre por qué eso es honesto SOLO porque hoy es una sola corrida diaria, y dejaría de serlo si existiera un segundo proceso de alertas en vivo); "No operar"/"Esperar" explican el motivo en prosa en vez de una frase telegráfica; cada oportunidad dice qué espera confirmar el modelo (`que_espero`), qué cambió desde ayer usando las mismas barras menos el último día (`_que_cambio`, nunca un dato nuevo) y por qué eligió esa estrategia de opciones sobre comprar acciones (`_por_que_estrategia`). Un tercer refinamiento (feedback directo, 2026-07-25, mismo día: el screener llegó a mandar 4 oportunidades en una corrida) agregó un tope DIARIO real de `LIMITE_DIARIO=3` por Convicción -- todavía no es el tope SEMANAL que pidió el dueño del producto ("si solo hicieras dos trades esta semana, serían estos"), que requeriría persistir qué tickers ya se mandaron esta semana; queda anotado junto al "próximo gran paso" de abajo. Un cuarto refinamiento (mismo día) movió ese tope de `buscar_oportunidades` (que ahora devuelve TODAS las detectadas, ordenadas) a `mensaje_oportunidades` (capa de presentación), y agregó un "🏁 Resumen del día" al final del mensaje que nombra TODAS las detectadas -- no solo las que entraron en el detalle -- con medallas para las mostradas y "Las demás (...) las vigilaría, pero no abriría posición hoy" para el resto (pedido explícito: "eso te obliga a priorizar"). También en ese cuarto refinamiento: "Qué cambió hoy" ahora aparece siempre (con "Sin cambios importantes" cuando no hay deltas reales, en vez de omitir la sección), el emoji de urgencia baja pasó de 🟢 a ⚪ (verde sugería "actúa ya", lo contrario de "Baja"), y "Capital" se reorganizó en Acciones/Opciones. "Comprar hoy" ahora también trae "🎯 Mi plan" (tipo de estrategia, precio máximo que pagaría -- spot + 1/4 del ATR real del papel, nunca un $ fijo -- y qué haría si el precio abre por encima mañana) y un "Checklist antes de comprar" que es un RESUMEN de lo que la detección del patrón y la decisión ya verificaron (nunca un filtro paralelo con criterios nuevos: aplicar "valoración atractiva" de forma universal, por ejemplo, rechazaría rupturas legítimas que no tienen por qué ser baratas) -- reusa el 1% de riesgo por operación ya configurado en `risk_manager.config.RiskLimits`, real y existente, en vez de inventar un monto en dólares atado a un capital específico. "Sin noticias negativas de alta relevancia" aparece siempre como no disponible: verificarlo de verdad requeriría un clasificador de sentimiento no-LLM (no existe) o invocar `news_analyst` (que sí usa LLM) por oportunidad, rompiendo la garantía de este pipeline de correr 100% sin LLM. Todavía **no** pasa por el Risk Manager ni por el Portfolio Optimizer (faltan integrar), y quedan pendientes para una fase 2 los patrones que necesitan datos no recolectados hoy: earnings sorpresa + guía al alza (sin historial estructurado de guidance) y volumen de opciones inusual (sin histórico de IV/volumen de opciones). También queda pendiente, explícitamente descrita por el dueño del producto como "el siguiente gran paso" y NO construida todavía: un segundo proceso que vigile el mercado en horario de mercado y solo avise cuando algo cambie de verdad (ej. "entró a tu zona de compra" o "la tesis se invalidó"), en vez de depender de la corrida diaria del screener -- ahí también encajaría un tope semanal real. Un quinto refinamiento (feedback directo, 2026-07-25: "no quiero leer cinco reportes completos todos los días") reemplazó el mensaje diario ENTERO por solo el resumen/ranking -- `mensaje_oportunidades` ya no llama a `formatear_oportunidad` (que sigue existiendo, probado, con todo el detalle rico que el dueño del producto calificó como "lo mejor" -- checklist, plan, por qué la estrategia), ahora reservado para cuando `/trade TICKER` se conecte a reutilizar este mismo razonamiento (pendiente, no implementado: requeriría persistir las oportunidades detectadas como ya se hace con `shortlist_hoy.json`). El resumen ahora traduce Convicción a una letra fija con `_grado` (A+/A/B+/B/C, pedido explícito: "más fácil recordar A+ que 85") y cierra apuntando a `/trade {ticker}` para el plan completo. También se separó "Horizonte esperado" en `horizonte_tesis` (rango fijo por patrón, independiente de cualquier vencimiento de opción) y, solo si aplica una estrategia de opciones, la fecha calendario real de vencimiento (hoy + los días reales, nunca inventada); y "Precio máximo que pagaría" ahora explica que sale del ATR real del papel |
| 9 | **AI Analyst** | genera reportes institucionales: tesis, fortalezas/debilidades/riesgos/catalizadores, por qué el modelo eligió esto y rechazó lo otro, impacto en portafolio, explicación de riesgo. Todo en lenguaje humano. **Nunca decide** | 🟡 Parcial — `news_analyst/` (28 tests, con LLM pero con guardrails duros): cruza titulares reales contra `screener/shortlist_hoy.json` y explica CON LLM solo lo que matcheó ("Why Should I Care?"). `telegram_bot/report_command.py` (`/report TICKER [--full]`, 58 tests, filosofía redefinida 2026-07-23): el modo por defecto responde en <1 minuto si vale la pena investigar (calidad fundamental) y si comprar hoy/esperar/descartar (timing: shortlist + tendencia + RSI, pregunta separada de la anterior), qué gusta/no gusta con números reales, métricas condensadas (omitiendo lo no disponible en vez de mostrar "No disponible"), un resumen de noticias de una frase + hasta 3 hechos (único uso de LLM en este modo, mismos guardrails que `news_analyst`, degrada a titulares crudos si falla), y niveles de entrada/alertas (mismo motor ATR/SMA50 que `/trade`, ahora en `screener/factors/technical.niveles_precio` para que ambos comandos lo reutilicen sin import circular). Se eliminaron el "Nivel de confianza del reporte: X%" y las "Preguntas que debo responder" (un asesor no dice eso, y el bot debe responder sus propias preguntas). `--full` conserva el memo exhaustivo de antes: Executive Summary + score breakdown (misma fórmula exacta de `scoring.puntuar()`) + técnico/fundamentales con interpretación en lenguaje llano + consenso de analistas y % de tenencia institucional/insider (snapshots reales de yfinance, no histórico de transacciones — eso no está disponible gratis) + riesgos/catalizadores por reglas fijas (nunca inventados) + noticias ordenadas por relevancia reutilizando `news_analyst`. Ahí "No disponible" sigue siendo honesto porque esa vista existe para mostrar todo lo que se intentó obtener. Executive Summary y riesgos/catalizadores son 100% determinísticos, sin LLM. `telegram_bot/options_command.py` (`/options TICKER [--full]`): motor 100% determinístico (`screener/options_math.py` Black-Scholes/Greeks/probabilidad/valor esperado vía integración numérica sobre la superficie real de volatilidad interpolada por strike — no una IV plana, ver auditoría; `screener/options_strategies.py` construye y rankea 9 de 10 estrategias con strikes reales elegidos por delta objetivo), LLM solo explica el ranking ya calculado. Por defecto (no en `--full`) se ocultan las estrategias cuya dirección contradice la tesis técnica (`_coincide_con_tesis`, nunca toca el ranking) — regresión de un caso real donde el Top 1 era bajista con la tesis en "Alcista" en el mismo mensaje. Falta: tesis institucional completa (10-K/earnings calls), Calendar Spread (necesita segunda fecha de vencimiento), IV Rank real (sin histórico de IV recolectado todavía), `/history`/`/diff`/`/quality` (no existen). `telegram_bot/trade_command.py` (`/trade TICKER [--full]`, filosofía redefinida 2026-07-23 tras feedback de que el diseño anterior — ~12 secciones — era demasiado largo para uso diario, y luego afinada tras feedback de seguimiento: mostrar "La estrategia que usaría" justo debajo del veredicto mezclaba "¿vale la pena invertir?" con "¿cómo la operaría?"): el modo por defecto separa EXPLÍCITAMENTE esas dos preguntas — "🎯 ¿Vale la pena invertir en {ticker}?" (solo la tesis: veredicto + niveles Entrada ideal/Stop/Objetivo/Horizonte + un "Porque..." de una sola frase, nunca menciona una estrategia) y "💰 ¿Cómo lo haría?" (solo la estructura, condicionada a la anterior: la estrategia top con un ranking corto contra "Comprar acciones" y una segunda alternativa que también debe coincidir con la tesis, un "¿Por qué {estrategia}?" con ventajas/desventajas reales frente a comprar la acción directamente vía un diccionario fijo por tipo de estrategia con números reales insertados, y la misma capa de coherencia tesis-vs-estrategia marcando si la top no coincide, sin tocar el ranking). `--full` conserva el tablero completo de antes sin perder nada: Semáforo del modelo (emoji + texto cualitativo, reemplazó una versión con % que se prestaba a leerse como probabilidad de ganar), Tesis + estrategia con la capa de coherencia completa, Plan del trade, Ejemplo, Capital mínimo de las 9 estrategias, "¿Qué tiene que pasar para que esta estrategia gane?", Horizonte esperado, Confianza en este plan (factores reales a favor/en contra, explícitamente NO una probabilidad de éxito), Riesgos, Plan de acción + Alertas para Yahoo Finance con estimado de días (solo si la tesis es "esperar"), y Mi decisión hoy de cierre. Idea pendiente (anotada, no implementada): adaptar la recomendación al capital real del usuario -- requiere una nueva forma de capturar ese dato en la conversación, fuera de alcance por ahora. El LLM del evaluador de ideas de Telegram sigue decidiendo, lo cual no debe (ver deuda de arquitectura) |
| 10 | **Execution Engine** | solo: conexión al bróker, ejecutar órdenes, poner stops/take-profit, monitorear, loggear. Separado por completo del research | 🟡 Parcial — `wizards_bot.py` ejecuta (mezclado con la lógica de señal/riesgo, no aislado como motor independiente); espejo a Webull verificado para futuros, no para acciones |
| 11 | **Learning Engine** | cada trade se guarda para siempre: fecha, razón, factor scores, tamaño, riesgo, stop, estado del portafolio, resultado, holding period, drawdown, motivo de salida. El sistema nunca se cambia solo — mejoras requieren backtesting + out-of-sample + walk-forward + aprobación humana | 🟡 Parcial — `journal/` (paquete standalone, sin LLM/red) + `telegram_bot/journal_command.py` (`/journal open\|close\|list\|stats`): registra automáticamente ticker, fecha, score y posición del screener, tesis, estrategia con todas sus patas/costo/riesgo máximo/ganancia máxima/probabilidad/valor esperado (los mismos números que `/options` ya calculó), y motivo. Al cerrar, el resultado real lo reporta el usuario (no hay integración con broker real, así que nunca se infiere). `journal/stats.py` calcula win rate, ganancia/pérdida promedio, expectancy, P&L total y drawdown máximo, generales y por estrategia. Falta: holding period, estado del portafolio en el momento del trade, integración con `wizards_bot`/Decision Engine, y el propio Decision Engine que consuma estas estadísticas para aprobar cambios. **Idea pendiente, propuesta explícitamente por el dueño del producto (2026-07-25) como la siguiente prioridad real** ("medir el desempeño del sistema... cuántas oportunidades alcanzan el objetivo, cuántas activan el stop y cuál es el rendimiento por estrategia"): medir el desempeño de las alertas del Opportunity Hunter EN SÍ MISMAS (no solo de los trades que el usuario decide registrar a mano en `/journal`) -- requiere un tracking automático, distinto del journal manual actual, que siga cada oportunidad detectada día a día hasta que toque su objetivo, su stop, o expire su horizonte. NO diseñado ni construido todavía -- es una pieza nueva de estado persistente, no una extensión trivial de `journal/` |

## Validation Pipeline (obligatorio, sin atajos)

Ninguna estrategia opera capital real sin pasar, en este orden:

1. **Historical Backtesting**
2. **Walk-Forward Analysis**
3. **Out-of-Sample Validation**
4. **Paper Trading — mínimo 3-6 meses** (más largo de lo que se había hablado antes)
5. **Despliegue con capital pequeño**
6. **Despliegue con capital completo**

Nadie salta pasos por prisa, aunque el capital ya esté disponible.

## Principios de ingeniería

Python, type hints, unit tests, logging, dependency injection, config files,
caching, requests async, Clean Architecture, SOLID, Repository Pattern. Cada
módulo debe poder probarse de forma aislada — como ya se hizo con
`screener/scoring.py` (7 tests con un provider falso, sin red).

## Cómo se está construyendo (por partes, sesión a sesión)

No se construyen los 11 agentes de una sentada. Orden de dependencias reales:
Research Agent (✅) → Portfolio Optimizer (🟢 parte 1 hecha) → Risk Manager
(🟢 parte 1 hecha) → Decision Engine → (arreglar la deuda de arquitectura
del LLM) → Fundamental/Macro Agents → Execution Engine aislado → Learning
Engine → Validation Pipeline completo.

**Siguiente parte a construir: por decidir con el dueño del proyecto en cada
sesión — ver la conversación para la elección más reciente.**

## Segundo sistema, deliberadamente independiente: Momentum Opportunity Hunter

Pedido explícito del dueño del producto (2026-07-26): "no copies el bot
actual... el nuevo debe ser un Opportunity Hunter" — el Investment
Analyst de arriba (agentes 1-11, `screener/`) responde "¿vale la pena
invertir en esta empresa?" sobre el S&P 500 con un horizonte de semanas
o meses; `momentum_hunter/` responde una pregunta completamente distinta
— "¿qué acción puede moverse fuerte HOY o en los próximos días?" — sobre
penny stocks/small caps/low float con un horizonte de 1 a 10 días. Son
problemas distintos a propósito, así que **no comparten universo,
`DataProvider`, factores, scoring, ni código de valoración/calidad**:
`momentum_hunter/` es un paquete nuevo, standalone, con su propia
arquitectura de principio a fin (ver `momentum_hunter/README.md` para el
detalle completo).

100% determinístico, mismo Principio #3 de arriba (sin LLM decidiendo
qué comprar). Score 0-100 = 40% momentum + 25% catalizador + 20%
liquidez + 15% gestión del riesgo (cero P/E, cero ROE, cero dividendos).
Solo manda alerta a Telegram cuando las cuatro condiciones del Prompt 7
se cumplen a la vez (score > 85, catalizador confirmado, RVOL > 4x,
liquidez suficiente), con un tope de 5 alertas/día — y se queda en
silencio cuando nada califica, en vez de anunciar "no encontré nada"
(esa es la convención del Investment Analyst, que corre una vez al día;
este bot puede correr varias veces al día, así que repetir el
no-resultado sería el mismo ruido que Prompt 2 pide evitar).

Clasifica cada oportunidad por tipo (🔥 Breakout / ⚡ News Momentum /
🚀 Short Squeeze / 💰 Earnings Play / 📈 Trend Continuation / 🔄
Reversal) y decide el vehículo de entrada (Comprar acciones/Long
Call/Bull Call Spread/Cash Secured Put/No Operar) con justificación —
nunca se limita a decir "comprar". Incluye su propio Learning Engine
(`tracker.py`/`outcomes.py`/`stats.py`): cada alerta se guarda y se mide
su resultado real a 1/3/5/10 días (win rate, retorno promedio, drawdown
máximo, expectancy, Sharpe — global y por tipo de oportunidad), la misma
pieza de "medir el desempeño de las alertas EN SÍ MISMAS" que el
Investment Analyst todavía tiene pendiente (agente 11, arriba).

Limitación honesta compartida con `screener/`: escanear TODO
NYSE+NASDAQ+AMEX (~8,000-11,000 símbolos) varias veces al día con datos
gratis por-ticker no es viable sin arriesgar que Yahoo bloquee el
runner — `run.py` opera por defecto sobre un subconjunto acotado
(`--limit`/`--universo`); producción real de alta frecuencia necesitaría
un `DataProvider` de pago con cotizaciones masivas, sin tocar el resto
del pipeline (la interfaz ya lo permite). Pendiente, no construido:
opciones de compra reales por debajo del vehículo elegido (hoy
`strategy.py` decide QUÉ vehículo usar pero no cotiza la cadena de
opciones como sí hace `screener/options_math.py` para el otro bot —
deliberado, ver `momentum_hunter/README.md`), y conectar
`risk_manager/` para sizing real de posición.
