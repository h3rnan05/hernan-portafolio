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
| 2 | **Research Agent** | escanea el universo, calcula Quality/Growth/Value/Momentum/Volatility/Liquidity, genera shortlist — **NO son señales de compra** | ✅ Hecho — `screener/` completo. El mensaje diario a Telegram ahora es corto (solo ticker/score/industria, `texto_telegram_corto`); la investigación profunda por empresa se pide bajo demanda con `/report TICKER` (`telegram_bot/report_command.py`) |
| 3 | **Fundamental Analysis Agent** | lee 10-K/10-Q/earnings calls/presentaciones, arma tesis estructurada (fortalezas/debilidades/ventajas/riesgos/catalizadores/valuación) — nunca decide comprar | ❌ No existe |
| 4 | **Technical Analysis Agent** | medias móviles, ATR, RSI, MACD, ADX, momentum, breakouts, soporte/resistencia, tendencia — solo información | 🟡 Parcial — `screener/factors/technical.py` tiene medias, ATR, RSI, momentum. Falta MACD, ADX, soporte/resistencia explícitos |
| 5 | **Macro Agent** | Fed, tasas, inflación, PIB, empleo, petróleo, oro, DXY, treasuries, geopolítica — nunca compra | ❌ No existe |
| 6 | **Portfolio Optimizer** | construye el portafolio óptimo desde candidatos: max retorno esperado, min riesgo, respeta límites (sector, tamaño posición, cash mínimo, beta, correlación, drawdown objetivo) | 🟢 Parte 1 hecha — `portfolio_optimizer/` (standalone, 26 tests, sin LLM/red): ranking por retorno ajustado por riesgo × calidad factorial, asignación voraz respetando posiciones/sector/posición/cash/beta/volatilidad/correlación, arquitectura con algoritmo intercambiable (`OptimizationStrategy`). **Falta**: MVO/Risk Parity/Black-Litterman/HRP/ERC/Min-Variance reales (la interfaz ya los soporta), maximum drawdown (requiere backtesting), maximum heat (vive en `risk_manager`). **Falta integrar** con `wizards_bot`/Decision Engine |
| 7 | **Risk Manager** | ⭐ el módulo más importante — **veto power**. Riesgo máx/trade, calor máx portafolio, correlación máx, exposición sectorial máx, volatilidad máx, drawdown máx, stop por ATR, position sizing, VaR, stress testing. Si algo falla → rechaza | 🟢 Parte 1 hecha — `risk_manager/` (standalone, 14 tests, sin LLM/red): promediar, máx. posiciones, ATR stop, sizing por riesgo, tope de posición, calor, sector, reserva de cash. **Falta parte 2**: correlación, beta, drawdown objetivo, VaR, stress testing. **Falta integrar** con `wizards_bot`/Decision Engine |
| 8 | **Decision Engine** | recibe SOLO trades ya aprobados por el Risk Manager. Output: BUY / SELL / HOLD / **DO NOTHING**. "No trades today" es una salida válida y esperada, nunca se fuerza un trade | ❌ No existe como módulo determinístico — hoy la "decisión" la toma un LLM (ver deuda de arquitectura arriba) |
| 9 | **AI Analyst** | genera reportes institucionales: tesis, fortalezas/debilidades/riesgos/catalizadores, por qué el modelo eligió esto y rechazó lo otro, impacto en portafolio, explicación de riesgo. Todo en lenguaje humano. **Nunca decide** | 🟡 Parcial — `news_analyst/` (28 tests, con LLM pero con guardrails duros): cruza titulares reales contra `screener/shortlist_hoy.json` y explica CON LLM solo lo que matcheó ("Why Should I Care?"). `telegram_bot/report_command.py` (`/report TICKER [--full]`, 58 tests, filosofía redefinida 2026-07-23): el modo por defecto responde en <1 minuto si vale la pena investigar (calidad fundamental) y si comprar hoy/esperar/descartar (timing: shortlist + tendencia + RSI, pregunta separada de la anterior), qué gusta/no gusta con números reales, métricas condensadas (omitiendo lo no disponible en vez de mostrar "No disponible"), un resumen de noticias de una frase + hasta 3 hechos (único uso de LLM en este modo, mismos guardrails que `news_analyst`, degrada a titulares crudos si falla), y niveles de entrada/alertas (mismo motor ATR/SMA50 que `/trade`, ahora en `screener/factors/technical.niveles_precio` para que ambos comandos lo reutilicen sin import circular). Se eliminaron el "Nivel de confianza del reporte: X%" y las "Preguntas que debo responder" (un asesor no dice eso, y el bot debe responder sus propias preguntas). `--full` conserva el memo exhaustivo de antes: Executive Summary + score breakdown (misma fórmula exacta de `scoring.puntuar()`) + técnico/fundamentales con interpretación en lenguaje llano + consenso de analistas y % de tenencia institucional/insider (snapshots reales de yfinance, no histórico de transacciones — eso no está disponible gratis) + riesgos/catalizadores por reglas fijas (nunca inventados) + noticias ordenadas por relevancia reutilizando `news_analyst`. Ahí "No disponible" sigue siendo honesto porque esa vista existe para mostrar todo lo que se intentó obtener. Executive Summary y riesgos/catalizadores son 100% determinísticos, sin LLM. `telegram_bot/options_command.py` (`/options TICKER [--full]`): motor 100% determinístico (`screener/options_math.py` Black-Scholes/Greeks/probabilidad/valor esperado vía integración numérica sobre la superficie real de volatilidad interpolada por strike — no una IV plana, ver auditoría; `screener/options_strategies.py` construye y rankea 9 de 10 estrategias con strikes reales elegidos por delta objetivo), LLM solo explica el ranking ya calculado. Por defecto (no en `--full`) se ocultan las estrategias cuya dirección contradice la tesis técnica (`_coincide_con_tesis`, nunca toca el ranking) — regresión de un caso real donde el Top 1 era bajista con la tesis en "Alcista" en el mismo mensaje. Falta: tesis institucional completa (10-K/earnings calls), Calendar Spread (necesita segunda fecha de vencimiento), IV Rank real (sin histórico de IV recolectado todavía), `/history`/`/diff`/`/quality` (no existen). `telegram_bot/trade_command.py` (`/trade TICKER [--full]`, filosofía redefinida 2026-07-23 tras feedback de que el diseño anterior — ~12 secciones — era demasiado largo para uso diario, y luego afinada tras feedback de seguimiento: mostrar "La estrategia que usaría" justo debajo del veredicto mezclaba "¿vale la pena invertir?" con "¿cómo la operaría?"): el modo por defecto separa EXPLÍCITAMENTE esas dos preguntas — "🎯 ¿Vale la pena invertir en {ticker}?" (solo la tesis: veredicto + niveles Entrada ideal/Stop/Objetivo/Horizonte + un "Porque..." de una sola frase, nunca menciona una estrategia) y "💰 ¿Cómo lo haría?" (solo la estructura, condicionada a la anterior: la estrategia top con un ranking corto contra "Comprar acciones" y una segunda alternativa que también debe coincidir con la tesis, un "¿Por qué {estrategia}?" con ventajas/desventajas reales frente a comprar la acción directamente vía un diccionario fijo por tipo de estrategia con números reales insertados, y la misma capa de coherencia tesis-vs-estrategia marcando si la top no coincide, sin tocar el ranking). `--full` conserva el tablero completo de antes sin perder nada: Semáforo del modelo (emoji + texto cualitativo, reemplazó una versión con % que se prestaba a leerse como probabilidad de ganar), Tesis + estrategia con la capa de coherencia completa, Plan del trade, Ejemplo, Capital mínimo de las 9 estrategias, "¿Qué tiene que pasar para que esta estrategia gane?", Horizonte esperado, Confianza en este plan (factores reales a favor/en contra, explícitamente NO una probabilidad de éxito), Riesgos, Plan de acción + Alertas para Yahoo Finance con estimado de días (solo si la tesis es "esperar"), y Mi decisión hoy de cierre. Idea pendiente (anotada, no implementada): adaptar la recomendación al capital real del usuario -- requiere una nueva forma de capturar ese dato en la conversación, fuera de alcance por ahora. El LLM del evaluador de ideas de Telegram sigue decidiendo, lo cual no debe (ver deuda de arquitectura) |
| 10 | **Execution Engine** | solo: conexión al bróker, ejecutar órdenes, poner stops/take-profit, monitorear, loggear. Separado por completo del research | 🟡 Parcial — `wizards_bot.py` ejecuta (mezclado con la lógica de señal/riesgo, no aislado como motor independiente); espejo a Webull verificado para futuros, no para acciones |
| 11 | **Learning Engine** | cada trade se guarda para siempre: fecha, razón, factor scores, tamaño, riesgo, stop, estado del portafolio, resultado, holding period, drawdown, motivo de salida. El sistema nunca se cambia solo — mejoras requieren backtesting + out-of-sample + walk-forward + aprobación humana | 🟡 Parcial — `journal/` (paquete standalone, sin LLM/red) + `telegram_bot/journal_command.py` (`/journal open\|close\|list\|stats`): registra automáticamente ticker, fecha, score y posición del screener, tesis, estrategia con todas sus patas/costo/riesgo máximo/ganancia máxima/probabilidad/valor esperado (los mismos números que `/options` ya calculó), y motivo. Al cerrar, el resultado real lo reporta el usuario (no hay integración con broker real, así que nunca se infiere). `journal/stats.py` calcula win rate, ganancia/pérdida promedio, expectancy, P&L total y drawdown máximo, generales y por estrategia. Falta: holding period, estado del portafolio en el momento del trade, integración con `wizards_bot`/Decision Engine, y el propio Decision Engine que consuma estas estadísticas para aprobar cambios |

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
