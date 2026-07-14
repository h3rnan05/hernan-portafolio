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
| 2 | **Research Agent** | escanea el universo, calcula Quality/Growth/Value/Momentum/Volatility/Liquidity, genera shortlist — **NO son señales de compra** | ✅ Hecho — `screener/` completo, shortlist diaria a Telegram |
| 3 | **Fundamental Analysis Agent** | lee 10-K/10-Q/earnings calls/presentaciones, arma tesis estructurada (fortalezas/debilidades/ventajas/riesgos/catalizadores/valuación) — nunca decide comprar | ❌ No existe |
| 4 | **Technical Analysis Agent** | medias móviles, ATR, RSI, MACD, ADX, momentum, breakouts, soporte/resistencia, tendencia — solo información | 🟡 Parcial — `screener/factors/technical.py` tiene medias, ATR, RSI, momentum. Falta MACD, ADX, soporte/resistencia explícitos |
| 5 | **Macro Agent** | Fed, tasas, inflación, PIB, empleo, petróleo, oro, DXY, treasuries, geopolítica — nunca compra | ❌ No existe |
| 6 | **Portfolio Optimizer** | construye el portafolio óptimo desde candidatos: max retorno esperado, min riesgo, respeta límites (sector, tamaño posición, cash mínimo, beta, correlación, drawdown objetivo) | 🟢 Parte 1 hecha — `portfolio_optimizer/` (standalone, 26 tests, sin LLM/red): ranking por retorno ajustado por riesgo × calidad factorial, asignación voraz respetando posiciones/sector/posición/cash/beta/volatilidad/correlación, arquitectura con algoritmo intercambiable (`OptimizationStrategy`). **Falta**: MVO/Risk Parity/Black-Litterman/HRP/ERC/Min-Variance reales (la interfaz ya los soporta), maximum drawdown (requiere backtesting), maximum heat (vive en `risk_manager`). **Falta integrar** con `wizards_bot`/Decision Engine |
| 7 | **Risk Manager** | ⭐ el módulo más importante — **veto power**. Riesgo máx/trade, calor máx portafolio, correlación máx, exposición sectorial máx, volatilidad máx, drawdown máx, stop por ATR, position sizing, VaR, stress testing. Si algo falla → rechaza | 🟢 Parte 1 hecha — `risk_manager/` (standalone, 14 tests, sin LLM/red): promediar, máx. posiciones, ATR stop, sizing por riesgo, tope de posición, calor, sector, reserva de cash. **Falta parte 2**: correlación, beta, drawdown objetivo, VaR, stress testing. **Falta integrar** con `wizards_bot`/Decision Engine |
| 8 | **Decision Engine** | recibe SOLO trades ya aprobados por el Risk Manager. Output: BUY / SELL / HOLD / **DO NOTHING**. "No trades today" es una salida válida y esperada, nunca se fuerza un trade | ❌ No existe como módulo determinístico — hoy la "decisión" la toma un LLM (ver deuda de arquitectura arriba) |
| 9 | **AI Analyst** | genera reportes institucionales: tesis, fortalezas/debilidades/riesgos/catalizadores, por qué el modelo eligió esto y rechazó lo otro, impacto en portafolio, explicación de riesgo. Todo en lenguaje humano. **Nunca decide** | 🟡 Parcial — el LLM ya explica bien (ver las respuestas del evaluador de Telegram), pero hoy también decide, lo cual no debe |
| 10 | **Execution Engine** | solo: conexión al bróker, ejecutar órdenes, poner stops/take-profit, monitorear, loggear. Separado por completo del research | 🟡 Parcial — `wizards_bot.py` ejecuta (mezclado con la lógica de señal/riesgo, no aislado como motor independiente); espejo a Webull verificado para futuros, no para acciones |
| 11 | **Learning Engine** | cada trade se guarda para siempre: fecha, razón, factor scores, tamaño, riesgo, stop, estado del portafolio, resultado, holding period, drawdown, motivo de salida. El sistema nunca se cambia solo — mejoras requieren backtesting + out-of-sample + walk-forward + aprobación humana | ❌ No existe (diseño acordado antes en esta sesión, no implementado) |

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
