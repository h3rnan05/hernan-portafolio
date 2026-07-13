# Roadmap — Sistema de Trading Cuantitativo

Visión: Research → Decision → Portfolio → **(portón de backtesting)** →
Execution → Self-improvement. "El computador busca, el humano decide",
evolucionando hacia autonomía solo donde la validación histórica lo respalda.

## Decisiones acordadas (2026-07-13)

- **Capital real**: $44,000, para operar pronto. No es un ejemplo ilustrativo.
- El sistema nuevo **reemplaza** a `wizards_bot.py` (el sistema de ruptura de
  55 días sobre 10 ETFs se retira cuando la Fase 4 esté lista).
- Se **mantiene** el canal de Telegram como entrada de ideas al Decision Bot
  — es la pieza de `wizards_bot`/`telegram_bot/` que sí se conserva.
- **El backtesting histórico es un portón obligatorio antes de la Fase 4**
  con dinero real. No se salta aunque haya prisa por operar — es la regla que
  el propio dueño del proyecto puso: "cualquier cambio al modelo debe
  validarse con backtesting y pruebas fuera de muestra, no solo porque una
  racha reciente fue buena o mala."

## Fases

### Fase 1 — Research Bot ✅ HECHO
`screener/` — S&P 500, factores normalizados cross-sectional 0-100 (momentum,
tendencia, volatilidad, liquidez, calidad, valor), shortlist diaria de ~20
nombres con razones, entregada a Telegram. Workflow `screener.yml`, corre
12:00 UTC lun-vie.

### Fase 2 — Decision Bot 🔜 SIGUIENTE
Toma la shortlist del screener (y las ideas que lleguen por Telegram) y
decide: ¿comprar o no?, ¿cuánto?, ¿dónde el stop?, ¿cuál es el riesgo?,
¿cómo afecta al portafolio? — con la razón explícita de cada decisión.

Reutiliza: `telegram_bot/idea_evaluator.py` (Claude + razonamiento
estructurado), la lógica de sizing/stop de `wizards_bot.comprar()`
(1.5% riesgo, 2×ATR).
Nuevo: conectar la salida del screener a la capa de decisión — hoy el
screener solo notifica, no alimenta ninguna decisión.

### Fase 3 — Portfolio Manager
Reglas a **nivel portafolio**, no por trade individual: riesgo máx. 1% por
operación, máx. 5 posiciones, máx. 30% en un sector, beta máximo del
portafolio 1.1, mínimo 15% en efectivo. Calibrado a $44k, pero todavía
**virtual** (no toca dinero real en esta fase).

Nuevo: trackear exposición sectorial y beta agregado del portafolio — no
existe hoy (los gates actuales de `wizards_bot` son por-trade: calor 6%,
máx. 4 posiciones, sin noción de sector ni beta conjunto).

### Fase 3.5 — Backtesting histórico 🚧 PORTÓN OBLIGATORIO
No es opcional ni "fase 5 nice-to-have": es el paso que decide si la Fase 4
se autoriza con capital real o se queda en papel.

Backtest de 10+ años del sistema completo (screener + decisión + reglas de
portafolio) sobre datos históricos gratis (Yahoo). Métricas: CAGR, Sharpe,
Sortino, max drawdown, win rate, alpha/beta vs. buy-and-hold.

Advertencia metodológica que hay que resolver, no esconder: **survivorship
bias** — usar el S&P 500 de HOY para simular el pasado ignora empresas que
quebraron o salieron del índice, lo que infla los resultados. Se documenta
explícitamente en el reporte del backtest.

**No se autoriza la Fase 4 con capital real sin pasar este portón con
números que sostengan la tesis.**

### Fase 4 — Execution Bot
Solo tras aprobar el backtest. Arranca con una **fracción** de los $44k, no
el total — el tamaño exacto se decide con evidencia del backtest, no a
priori.

Pendiente de verificar antes de operar acciones reales: **Webull equities**
— hasta ahora solo se verificó el sandbox para futuros (trigo/ZW). Requiere
una sonda de verificación como la que se hizo con ZWU6, pero para el lado de
acciones, antes de asumir que sirve igual.

Retira: la lógica de ETFs por ruptura de 55 días de `wizards_bot.py`.
Mantiene: el canal de Telegram como entrada de ideas humanas.

### Fase 5 — Self-Improving Bot
Bitácora enriquecida por trade **desde el día 1** (aunque sea virtual/papel)
— para cuando llegue el dinero real ya hay historial con el que comparar, no
se empieza de cero.

- Cada trade registra: timestamps entrada/salida, señal que disparó la
  entrada + valores de indicadores en ese momento, niveles planeados
  (entrada/stop/riesgo), resultado real (salida/P&L/motivo), contexto de
  mercado (tendencia según una media larga).
- Revisión periódica cada **20 trades cerrados o 3 semanas** (lo que ocurra
  primero) — nunca por trade individual, para evitar sobreajuste con poca
  muestra.
- Cualquier comparación entre grupos requiere **mínimo 8 muestras** por
  grupo. Menos que eso no produce conclusiones.
- El reporte de la revisión propone ajustes concretos pero **nunca los
  aplica solo** — queda como propuesta pendiente hasta aprobación explícita
  ("aprobar cambio X" por Telegram).

(Diseño ya discutido y acordado en esta sesión; falta implementar la
bitácora y el script de revisión periódica.)

## Próximo paso concreto

Construir la **Fase 2 (Decision Bot)** — es la que más reutiliza código
existente y no toca capital real todavía.
