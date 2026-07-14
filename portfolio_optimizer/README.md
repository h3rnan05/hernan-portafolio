# Portfolio Optimizer

El agente #6 del AIOS (ver `ROADMAP.md`): construye el portafolio óptimo a
partir de una lista de candidatos. **100% determinístico** — sin LLM, sin
red, sin aleatoriedad: la misma entrada siempre produce la misma salida.

No es un stock picker. No decide "comprar X". Recibe candidatos (que ya
vienen de Research/Fundamental/Technical Agents) y decide **cuáles entran,
con qué peso, y por qué** — nunca maximizando solo el retorno esperado,
siempre retorno ajustado por riesgo.

## Uso

```python
from portfolio_optimizer.config import OptimizerConstraints
from portfolio_optimizer.models import Candidate, Holding
from portfolio_optimizer.optimizer import PortfolioOptimizer

restricciones = OptimizerConstraints(max_posiciones=5, max_sector_pct=0.30,
                                     max_posicion_pct=0.25, min_cash_pct=0.15)
opt = PortfolioOptimizer(restricciones)

candidatos = [
    Candidate(ticker="AAPL", sector="Technology", expected_return=0.15,
              volatilidad=0.20, beta=1.2, liquidez_usd=5e9,
              quality_score=80, momentum_score=70, growth_score=75, value_score=60),
    Candidate(ticker="JNJ", sector="Healthcare", expected_return=0.08,
              volatilidad=0.12, beta=0.6, liquidez_usd=1e9,
              quality_score=90, momentum_score=50, growth_score=40, value_score=70),
]
correlaciones = {("AAPL", "JNJ"): 0.15}
cartera_actual: list[Holding] = []

resultado = opt.construir_portafolio(candidatos, cartera_actual, correlaciones)

for a in resultado.asignaciones:
    print(f"{a.ticker}: {a.peso:.1%}")
for r in resultado.rechazados:
    print(f"rechazado {r.ticker}: {r.motivo}")
print(resultado.resumen_riesgo)
```

## Arquitectura: algoritmo intercambiable

`PortfolioOptimizer` (`optimizer.py`) es un orquestador delgado — no
implementa ningún algoritmo. Valida `OptimizerConstraints` y delega en una
`OptimizationStrategy` (`strategies/base.py`), inyectable:

```python
opt = PortfolioOptimizer(restricciones, estrategia=MiEstrategiaPropia())
```

Esto es lo que permite reemplazar el algoritmo sin tocar el resto del
sistema: los modelos de entrada (`Candidate`, `Holding`) y salida
(`PortafolioOptimo`, `Allocation`, `CandidatoRechazado`) no cambian.
Algoritmos futuros que pueden implementar la misma interfaz:
Mean-Variance Optimization, Risk Parity, Black-Litterman, Hierarchical
Risk Parity, Equal Risk Contribution, Minimum Variance.

`math_utils.py` tiene la matemática de riesgo compartida (varianza de
portafolio con matriz de correlación completa, beta ponderado, HHI, score
de diversificación) para que cualquier estrategia nueva la reuse en vez de
reimplementarla.

## Estrategia de Parte 1: `ScoreWeightedGreedy`

**No es Mean-Variance Optimization real** (no resuelve un problema de
optimización cuadrática sobre el frente eficiente) — es un ranking +
asignación voraz (greedy), determinístico y auditable:

1. Rankea candidatos por **retorno ajustado por riesgo × calidad
   factorial** (`expected_return / volatilidad`, tipo Sharpe simplificado,
   multiplicado por el promedio de Quality/Momentum/Growth/Value 0-100).
   Nunca rankea por retorno esperado solo.
2. Recorre el ranking de mejor a peor. Para cada candidato, en orden:
   - nunca promediar (ticker ya en cartera actual) → rechaza
   - máximo de posiciones alcanzado → rechaza
   - calcula peso tentativo = mínimo entre tope de posición, presupuesto
     invertible restante (1 − cash mínimo − lo ya invertido), y tope de
     sector restante
   - si el tentativo es menor al peso mínimo significativo → rechaza
   - correlación máxima con cualquier posición ya elegida → rechaza si
     excede el límite
   - si agregarlo excede la volatilidad o el beta máximo del portafolio
     (calculado con la matriz de correlación completa), **reduce el peso**
     en vez de rechazar de una — mismo patrón que `risk_manager` con la
     reserva de cash. Si ni el tamaño mínimo cabe, ahí sí rechaza.
3. El cash restante nunca baja de `min_cash_pct` porque el presupuesto
   invertible ya lo descuenta desde el principio.

## Salida

`PortafolioOptimo`: asignaciones (con ticker, sector, peso, retorno
esperado, si es nueva o preexistente), peso de cash, retorno/volatilidad/
beta esperados del portafolio completo, exposición por sector, score de
diversificación (0-1, basado en concentración HHI y correlación promedio
entre posiciones), rechazados con motivo explícito, y un resumen de riesgo
en texto. Todo auditable — nunca una caja negra.

## Explícitamente fuera de alcance (Parte 1)

- **Maximum portfolio heat**: requiere distancia entrada-stop por
  posición (ATR), que no es un input de este optimizador — eso ya lo
  calcula `risk_manager` cuando traduce un peso objetivo en tamaño de
  posición real con stop. Separación de responsabilidades: el optimizador
  decide QUÉ tener y en qué PESO; `risk_manager` decide CUÁNTAS acciones y
  DÓNDE va el stop.
- **Maximum drawdown target**: existe el campo en `OptimizerConstraints`
  para no romper la interfaz a futuro, pero no se aplica todavía —
  requiere simulación histórica (backtesting/Monte Carlo), no un cálculo
  de un solo período. Se implementa con el Validation Pipeline
  (`ROADMAP.md`).
- Algoritmos reales de optimización de portafolio (MVO, Risk Parity,
  Black-Litterman, HRP, ERC, Minimum Variance) — la arquitectura ya los
  soporta (`OptimizationStrategy`), pero `ScoreWeightedGreedy` es el único
  implementado hoy.

## Integración pendiente

Standalone y probado (26 pruebas), no conectado todavía a `wizards_bot.py`
ni al Decision Engine. `risk_manager` sigue siendo el que tiene la última
palabra sobre cada trade individual antes de ejecutar — el Portfolio
Optimizer decide la composición objetivo, `risk_manager` la valida
posición por posición.
