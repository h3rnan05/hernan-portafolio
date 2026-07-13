# Risk Manager

El módulo con **poder de veto** del AIOS (ver `ROADMAP.md`). 100%
determinístico — sin LLM, sin red, sin aleatoriedad: la misma entrada
siempre produce la misma salida.

## Uso

```python
from risk_manager.config import RiskLimits
from risk_manager.engine import RiskManager
from risk_manager.models import PortfolioState, Posicion, TradeCandidate

limites = RiskLimits(riesgo_por_trade_pct=0.01, max_posiciones=5,
                     max_sector_pct=0.30, min_cash_pct=0.15)
rm = RiskManager(limites)

portafolio = PortfolioState(equity=44_000.0, cash=44_000.0, posiciones={})
candidato = TradeCandidate(ticker="AAPL", precio=200.0, atr=5.0, sector="Technology")

veredicto = rm.evaluar(candidato, portafolio)
if veredicto.aprobado:
    print(f"comprar {veredicto.qty} @ stop {veredicto.stop}")
else:
    print(f"rechazado: {veredicto.motivo}")
```

## Reglas de esta parte (extraídas y formalizadas de `wizards_bot.py`)

1. Nunca promediar (no se añade a una posición ya abierta).
2. Máximo de posiciones simultáneas.
3. Stop por ATR (`entrada − atr_mult × ATR`).
4. Position sizing por riesgo fijo (% del equity por operación).
5. Tope de tamaño de posición (% del equity, notional).
6. No exceder el efectivo disponible.
7. Calor máximo del portafolio (riesgo abierto total, proyectado con el candidato).
8. Exposición máxima por sector (si se conoce el sector del candidato).
9. Reserva mínima de efectivo — si no alcanza, **reduce el tamaño** en vez de rechazar de golpe.

Orden de evaluación: promediar → posiciones → ATR válido → sizing → calor →
sector → reserva de cash. Un candidato puede ser rechazado por la primera
regla que incumpla; las pruebas aíslan cada regla relajando las demás.

## Lo que falta (parte 2, ver ROADMAP.md)

Correlación máxima, beta máximo del portafolio, drawdown objetivo, VaR,
stress testing. Requieren datos que el sistema no calcula todavía (matriz de
correlación entre posiciones, betas por ticker).

## Integración pendiente

Este módulo es standalone y no está conectado a `wizards_bot.py` todavía —
es una pieza aislada y probada, lista para que el Decision Engine (siguiente
parte del roadmap) la consuma.
