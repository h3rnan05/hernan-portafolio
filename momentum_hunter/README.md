# Momentum Opportunity Hunter

> "¿Qué acción puede moverse fuerte HOY o en los próximos días?" -- no
> "¿vale la pena invertir en esta empresa?". Un problema completamente
> distinto al del Investment Analyst (`screener/`), resuelto con un
> proyecto completamente independiente.

## Por qué es un proyecto separado, no una extensión del screener

`screener/` (el Investment Analyst) puntúa el S&P 500 con factores
pensados para empresas grandes y establecidas: calidad de negocio (ROE,
márgenes), valoración (P/E, P/B), tendencia de largo plazo. Responde
"¿es una buena empresa para sostener semanas o meses?".

Este bot busca lo opuesto: penny stocks, small caps, low float y
high-relative-volume con un catalizador reciente, para un horizonte de
**1 a 10 días**. No hay solapamiento posible entre los dos criterios --
una empresa "de calidad" casi nunca es la que va a moverse 40% en tres
días, y viceversa. Por eso:

- **Cero código compartido** de valoración/calidad/scoring con `screener/`.
- **Cero fundamentales** (P/E, ROE, dividendos, márgenes) en el score.
- Universo, `DataProvider`, factores, catalizadores, patrones y tracking
  son 100% propios de este paquete.
- Los dos bots pueden convivir y complementarse (uno para posiciones de
  semanas/meses, el otro para trades de días), pero nunca se mezclan.

## Pivote 2026-07-26: de "screener" a "trader de momentum"

Pedido explícito del dueño del producto: "quiero que piense exactamente
como un trader profesional de momentum (Ross Cameron, Warrior Trading)
... el objetivo NO es encontrar buenas empresas, es encontrar
oportunidades ANTES de que el movimiento principal ocurra." Esto cambió
la arquitectura de raíz -- el pipeline ahora corre en **dos etapas**:

**Etapa 1 (gruesa, barras diarias)** -- el filtro de universo de
siempre: precio/cap/liquidez + catalizador + `scoring.puntuar` (40%
momentum/25% catalizador/20% liquidez/15% riesgo, igual que antes). Ya
NO decide si se alerta -- solo recorta el universo a los
`cfg.max_candidatos_intradia` (default 50) tickers que valen la pena
evaluar con datos intradía. Pedir velas de 1 minuto para miles de
tickers no es viable con ningún proveedor gratis.

**Etapa 2 (fina, velas de 1 minuto)** -- SOLO sobre esos candidatos:

1. **`factors/intradia.py`** calcula lo que un trader mira en la
   pantalla en tiempo real: VWAP real de hoy (ya no una aproximación),
   EMA9, RVOL inmediato (vela actual vs. las últimas 5, no el promedio
   de 20 días), aceleración de volumen, máximo del premarket, rango de
   apertura, gap real.
2. **`classification.py`** detecta uno de los seis patrones de Ross
   Cameron: Gap and Go, Opening Range Breakout, Bull Flag, Micro
   Pullback, High Tight Flag, Trend Continuation. Ya NO existen las
   categorías genéricas de antes del pivote (breakout/news
   momentum/earnings play/reversal/short squeeze) -- describían
   resultados sobre barras diarias, no formas del precio en tiempo real.
3. **`early_opportunity.py`** (Early Opportunity Engine, Prompt 2)
   responde "¿llegamos a tiempo?" -- una pregunta DISTINTA de "¿qué tan
   buena es la señal?". Un score alto NUNCA rescata una entrada tardía:
   el veredicto temprano/tarde sale de dos reglas duras (extensión desde
   VWAP/EMA9, velas desde que se activó el patrón), no del promedio.
4. **`evaluator.py`** corre las 5 preguntas de Prompt 4, en orden, cada
   una cortando o penalizando la anterior -- nunca un promedio que
   pueda "perdonar" una respuesta negativa con las demás.
5. Solo si el resultado es `accionable` se arma una alerta
   (`report.py`, formato de trader -- Prompts 3/5/6/7). Lo que quedó
   "cerca" pero no accionable alimenta el **Market Radar**
   (`radar.py`) en vez de desaparecer en silencio.

## Segundo pivote 2026-07-26: cero jerga, solo lenguaje humano

Pedido explícito del dueño del producto, el mismo día: "el usuario nunca
debería sentir que necesita saber análisis técnico... si mi papá, que
nunca ha hecho trading, leyera este mensaje, ¿entendería exactamente por
qué vale la pena esta oportunidad?" Esto NO cambió ningún cálculo -- todo
lo de arriba (RVOL, EMA9, VWAP, ATR, MACD, RSI, patrones) se sigue
calculando exactamente igual. Lo que cambió es que **ningún indicador
crudo ni nombre técnico de patrón llega al mensaje**: `report.py` los
traduce a una historia de cuatro pasos (qué pasó -> qué hizo el mercado
-> qué está pasando ahora -> ¿todavía vale la pena?), más por qué llegó
esta alerta y qué la cancelaría. Ver el ejemplo abajo.

## Memoria para aprendizaje futuro (sin optimizar todavía)

Pedido explícito, mismo día: "quiero que el sistema tenga MEMORIA...
qué patrón gana más, qué horario funciona mejor, qué tipo de noticia
funciona mejor, qué float funciona mejor, qué gap funciona mejor, qué
RVOL termina siendo el más rentable... no quiero optimizar eso todavía,
solo quiero que la arquitectura quede preparada." Por eso cada alerta
guarda (en `Oportunidad`/`AlertaRegistrada`, nunca en el mensaje) el
patrón, la hora UTC, el tipo de catalizador, el float, el gap y el RVOL
del momento en que se mandó. `stats.py` ya sabe agrupar los resultados
por cualquiera de esas dimensiones (`calcular_por_clasificacion`,
`calcular_por_hora`, `calcular_por_catalizador`, `calcular_por_float`,
`calcular_por_gap`, `calcular_por_rvol`) -- pero ninguna de esas
funciones ajusta `scoring.py` ni `config.py` sola. Es la mitad de
"medir", deliberadamente no la de "decidir": ese ajuste, cuando haya
suficientes alertas resueltas para que signifique algo, sigue siendo una
decisión humana explícita (mismo Validation Pipeline del `ROADMAP.md`
raíz).

## Filosofía

> "Si solamente pudiera hacer una operación hoy, ¿esta sería una de
> ellas?" Si la respuesta es NO, no se manda ninguna alerta. Prefiero
> recibir una sola oportunidad excelente que veinte oportunidades
> promedio.

Determinístico de principio a fin -- cero LLM decidiendo nada (mismo
Principio #3 del `ROADMAP.md` raíz). El silencio es el resultado normal
y esperado cuando nada es accionable -- a diferencia del Investment
Analyst (que corre una vez al día y anuncia explícitamente "no encontré
nada" como confirmación de que sí corrió), este bot puede correr varias
veces al día, y repetir "nada hoy" sería exactamente el ruido que se
busca evitar.

## Tercer pivote 2026-07-27: los 14 principios del CIO

Pedido explícito: "construye un sistema en el que confiaría si TODO mi
patrimonio dependiera de él... tu trabajo es proteger capital y solo
actuar cuando exista una ventaja estadística clara." Cada principio se
tradujo en una pieza verificable de código, no en una intención:

| Principio | Dónde vive |
|---|---|
| 1-2. Escéptico: cada oportunidad debe demostrar por qué NO operarse | `skeptic.py` -- el abogado del diablo corre DESPUÉS de que una candidata ya es accionable, y busca matarla: sin salida clara (fatal), riesgo > 8% (fatal), dinero saliendo (fatal), última hora/noticia fría/volumen enfriándose (advertencias que viajan al mensaje como "qué podría salir mal") |
| 3. Probabilidades, nunca certezas | `memoria.frase_probabilidad` -- cita el win rate REAL medido solo con ≥10 casos resueltos; con menos, el mensaje dice literalmente "no voy a inventar confianza que no tengo" |
| 4. Competencia relativa | `config.solo_la_mejor` (default True) + `run.seleccionar_y_auditar` -- una sola alerta por corrida: la mejor que además sobreviva el debate. El mensaje lo dice con datos ("hoy evalué N candidatas...") |
| 5. Preservar capital / minimizar errores de comisión | Las objeciones fatales del skeptic ganan siempre, sin importar el score -- perder una buena oportunidad es aceptable; una mala operación no |
| 6-7. Verificable, nunca caja negra | `evaluator.explicar_rechazo` -- cada descarte dice exactamente qué condición falló y qué tendría que cambiar (nunca "el score fue bajo"); cada objeción del skeptic trae su `que_cambiaria` |
| 8. Aprende pero nunca se auto-modifica | `stats.py`/`memoria.py` miden y reportan; NINGUNA función ajusta `scoring.py` ni `config.py` -- el ciclo es medir → demostrar → proponer → el humano decide |
| 9. Auditoría completa | `audit.py` -- snapshot de CADA candidato evaluado (precio, volumen, factores, catalizador con titular/fuente/hora, resultado de cada pregunta, decisión y motivos) en `auditoria/AAAA-MM-DD.json`, committeado por el workflow; se cruza con el tracker para el "qué ocurrió realmente" |
| 10. Empresa ≠ oportunidad ≠ ejecución | Ya estructural: calidad de empresa es del Investment Analyst (`screener/`, otro sistema); calidad de oportunidad es el evaluador; calidad de ejecución es el Early Opportunity Engine + skeptic |
| 11. El sistema duda (dos analistas) | El pipeline entero convence (`evaluator.py`); `skeptic.py` destruye. Solo lo que sobrevive a ambos se alerta |
| 12. Memoria contextual que ajusta confianza sin prohibir | `memoria.advertencias_contextuales` -- "mis últimas N alertas con este tipo de jugada solo funcionaron X%" entra al debate como advertencia, NUNCA como veto |
| 13. Optimizar para ganar dinero, no para tener razón | `stats.py` ya mide expectancy/drawdown/Sharpe por grupo -- rendimiento ajustado a riesgo, no % de aciertos a secas |
| 14. "¿Pondría dinero aquí?" | Es la suma de todo lo anterior: score alto sin salida definida = no hay alerta |

## Cuarto refinamiento 2026-07-27: pensar como Head Trader

Pedido explícito: "deja de pensar como un desarrollador que agrega
funciones... no quiero más complejidad, quiero mejorar la calidad de
las decisiones." Diez puntos, cada uno sobre módulos ya existentes:

1. **Ranking absoluto** -- cada alerta abre con "La #1 del día entre
   N acciones escaneadas" (N real de la etapa 1, nunca inventado).
2. **Máximo pocas alertas** -- ya cubierto por `solo_la_mejor` + el
   silencio como resultado válido.
3. **¿Por qué esta y no las demás?** -- checklist ✔ construido SOLO con
   las condiciones que realmente se verificaron en el evaluador
   (`report._por_que_unica`), nunca una lista fija de marketing.
4. **Ventana estimada** -- "≈15 min / ≈30 min / ≈1 hora / hasta el
   cierre" por tipo de jugada, acotada por el tiempo real de sesión que
   queda. HONESTO: hoy son valores editoriales documentados (el
   historial para calibrarlos aún no existe); el propio mensaje dice
   "estimación, no una promesa".
5. **Calidad en estrellas** -- `memoria.estrellas`: ★ basadas SOLO en el
   win rate real del propio sistema (≥10 casos); sin muestra, la línea
   dice "sin calificar todavía" en vez de inventar tres estrellas.
6. **Qué espero ver después** -- señales de confirmación (✔) y de falla
   (✘) por patrón, sin jerga -- enseña a leer el mercado.
7. **Confianza explicada** -- `memoria.confianza`: nivel + por qué
   ("jugada vista 42 veces, funcionó 68%"), rebajado un nivel si el
   debate acumuló 2+ dudas (rastreables en el propio mensaje).
8. **Vigilancia post-alerta** -- `vigilancia.py`: cada corrida revisa
   las alertas de HOY (sigue válida / debilitándose / rompió stop /
   alcanzó objetivo / volumen desapareció) y avisa SOLO en cambios de
   estado. Stop antes que objetivo cuando ambos se tocaron: lectura
   conservadora.
9. **Diario automático** -- `diario.py`: al resolverse cada alerta se
   escribe una página markdown en `diario/` (qué ocurrió realmente, qué
   hubiera hecho un profesional, qué aprender) -- plantillas
   deterministas sobre números medidos; lo que no se puede medir se
   declara fuera de alcance en la propia página.
10. **La última pregunta** -- filtro final en `run.seleccionar_y_auditar`:
    si una candidata sobrevive todo pero acumula más de 2 dudas, ya no
    es un "sí claro" -- y sin un sí claro no hay alerta (decisión
    `no_paso_la_ultima_pregunta` en la auditoría).

### La regla inquebrantable

**El bot investiga, filtra, puntúa, explica, vigila y recomienda. La
decisión de ejecutar una operación con dinero real la toma SIEMPRE el
humano.** Este sistema no tiene, y no debe tener, conexión a ningún
broker -- y cada alerta lo dice en su última línea ("La decisión de
operar siempre es tuya -- yo solo investigo y aviso"). Es la misma
regla que el `ROADMAP.md` raíz ya impone al resto de la plataforma, y
aquí es además un pedido explícito del dueño del producto (2026-07-27).

## Arquitectura (cada módulo es reemplazable de forma independiente)

```
config.py             Universo, pesos del score y umbrales -- etapa 1 y etapa 2.
models.py             Barras/BarraIntradia/Metadata/Catalizador/
                       FactoresMomentum/FactoresIntradia/Oportunidad.
data/provider.py       DataProvider ABC + YahooProvider -- barras diarias Y velas
                       intradía (1m/5m). "El algoritmo nunca depende de Yahoo
                       específicamente": todo lo demás solo conoce BarraIntradia.
universe.py            NYSE+NASDAQ+AMEX completo desde NASDAQ Trader (no solo S&P 500).
factors/momentum.py     Etapa 1: Gap%, RVOL (20 días), breakout, EMA20/50, ATR, RSI,
                       MACD sobre barras diarias -- alimenta scoring.puntuar.
factors/intradia.py     Etapa 2: VWAP real, EMA9, RVOL inmediato, aceleración de
                       volumen, máximo premarket, rango de apertura, gap real.
catalysts/detector.py   Clasificador de catalizadores por keywords + minutos
                       transcurridos (para el Early Opportunity Engine).
classification.py      Los seis patrones de Ross Cameron (pregunta 4 del evaluador) +
                       DESCRIPCION_HUMANA (traducción sin jerga para Telegram).
early_opportunity.py    Early Opportunity Engine (Prompt 2): "¿llegamos a tiempo?".
evaluator.py            Árbol de 5 preguntas (Prompt 4) -- decide "accionable".
scoring.py              Score base 0-100 (etapa 1) -- 40% momentum + 25% catalizador +
                       20% liquidez + 15% riesgo. Cero valoración fundamental.
strategy.py             Selección de vehículo de opciones (Prompt 9) -- no conectado
                       todavía al mensaje nuevo, ver "Roadmap" abajo.
alerts.py               Recorte a la etapa 2 + competencia relativa (solo_la_mejor).
skeptic.py              Abogado del diablo: intenta destruir cada tesis accionable --
                       objeciones fatales matan la alerta; advertencias viajan al
                       mensaje como "qué podría salir mal".
memoria.py              Memoria contextual: probabilidad histórica honesta (o la
                       admisión de que no hay muestra) + advertencias cuando el
                       historial medido de un patrón/catalizador es débil.
audit.py                Auditoría completa: snapshot reconstruible de CADA candidato
                       evaluado, con decisión, motivos y qué tendría que cambiar.
report.py               Traduce todo a lenguaje humano (cero indicadores/jerga) y arma
                       la historia de 4 pasos: qué pasó/qué hizo el mercado/qué está
                       pasando ahora/¿todavía vale la pena?
radar.py                Market Radar -- resumen en lenguaje humano de lo que quedó
                       cerca pero no accionable, incluidas subcampeonas y vetadas
                       con su motivo.
tracker.py              Persiste cada alerta enviada (sin red) -- incluye la materia
                       prima para el aprendizaje futuro (patrón, hora, catalizador,
                       float, gap, RVOL), nunca mostrada en el mensaje.
outcomes.py             Actualiza resultados a 1/3/5/10 días con datos de mercado reales.
stats.py                Win rate, retorno promedio, drawdown máximo, expectancy, Sharpe
                       -- global y agrupado por patrón/hora/catalizador/float/gap/RVOL.
heartbeat.py             Un mensaje, una sola vez, cerca del cierre, SOLO si hoy no se
                       mandó ninguna alerta -- confirma que el bot corrió y decidió
                       que no había nada, no que se cayó.
run.py                  Orchestrator de las dos etapas -> alertas + radar -> Telegram -> tracker.
```

## Uso

```bash
pip install -r momentum_hunter/requirements.txt

python -m momentum_hunter.run                       # NYSE+NASDAQ+AMEX completo
python -m momentum_hunter.run --limit 500            # subconjunto (recomendado, ver abajo)
python -m momentum_hunter.run --universo watchlist.txt   # watchlist propia
python -m momentum_hunter.run --dry-run              # calcula y muestra, no envía ni registra
python -m momentum_hunter.run --actualizar-resultados     # actualiza tracker + imprime stats
```

Con `MOMENTUM_TELEGRAM_BOT_TOKEN`/`MOMENTUM_TELEGRAM_CHAT_ID` (o, si no
existen, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`) en el entorno, manda
las alertas por Telegram. `.github/workflows/momentum_hunter.yml` corre
el escaneo varias veces al día en horario de mercado;
`.github/workflows/momentum_hunter_outcomes.yml` corre una vez al día
después del cierre para actualizar el tracking de resultados.

## Ejemplo de alerta (lenguaje humano, cero jerga)

```
🔴 ACME -- rompiendo con fuerza justo al abrir

Acme Corp

1) Hace 12 min: La FDA le aprobó algo importante.
2) Abrió mucho más arriba de lo normal y el dinero está entrando cada vez con más fuerza.
3) Sigue subiendo sin parar desde que abrió el mercado.
4) ¿Todavía vale la pena? Sí. Apenas lleva unos minutos formando este movimiento -- todavía se puede entrar a buen precio.

De todo lo que vi hoy, esta es de las mejores oportunidades.

Si decides entrar: cerca de $5.20.
Si te equivocas, sal cerca de $5.07.
Si funciona, la primera meta es $5.45.

Si vuelve a caer por debajo de $5.00, se cancela la idea.

Fuente: "Company Receives FDA Approval" (Reuters)
```

Ni "RVOL", ni "EMA9", ni "VWAP", ni el nombre del patrón ("Gap and Go")
aparecen en el texto -- se calculan igual que siempre, pero el mensaje
solo muestra la traducción a lenguaje humano (`classification.
DESCRIPCION_HUMANA`, `report._CATALIZADOR_HUMANO`, `report.
_QUE_PASA_AHORA`). `test_report.py` verifica automáticamente que
ninguna de esas palabras pueda colarse en un mensaje real.

## Filtros de universo (etapa 1)

| Filtro | Valor por defecto |
|---|---|
| Bolsas | NYSE, NASDAQ, AMEX |
| Precio | $0.75 - $20 |
| Capitalización | < $2,000 millones |
| Volumen promedio | ≥ 300,000 acciones/día |
| Excluye | ETFs, SPACs, closed-end funds, ADRs de baja liquidez |
| Candidatos que pasan a intradía | Top 50 por score, con catalizador confirmado |

## Limitaciones honestas (datos gratis)

- **Escanear el mercado completo es lento con datos gratis.** NYSE+NASDAQ+AMEX
  son ~8,000-11,000 símbolos; la etapa 1 (barras diarias) ya es cara, y
  la etapa 2 (velas de 1 minuto) SOLO corre sobre el top 50 por esa misma
  razón -- pedirlas para todo el universo, o correr esto cada pocos
  minutos sobre el mercado completo, no es viable sin que Yahoo bloquee
  el runner. Producción real de alta frecuencia necesitaría un
  `DataProvider` de pago con cotizaciones masivas (Polygon, Alpaca,
  Tradier) -- la interfaz ya lo permite sin tocar `factors/intradia.py`,
  `classification.py`, `early_opportunity.py` ni `evaluator.py`.
- **Sesión de mercado aproximada.** `factors/intradia.py` asume apertura
  13:30 UTC / cierre 20:00 UTC (horario de verano ET) para distinguir
  premarket de sesión regular -- se corre 1 hora en horario de invierno,
  mismo caveat que ya aceptan los cron de `.github/workflows/*.yml`.
- **Patrones intradía son aproximaciones numéricas sobre velas de 1
  minuto gratis (ruidosas)**, no el reconocimiento visual de un trader
  humano -- cada patrón exige condiciones explícitas documentadas en
  `classification.py`, nunca "se ve parecido a".
- **Borrow fee no existe gratis.** `Metadata.borrow_fee_pct` siempre es
  `None`.
- **Catalizadores** se detectan solo en los titulares que devuelve
  `yfinance` (`Ticker.news`) -- no hay integración con SEC EDGAR, FDA.gov
  ni terminales de noticias en tiempo real. `minutos_desde_catalizador`
  (usado por el Early Opportunity Engine) es `None` cuando la fuente solo
  da una fecha sin hora -- nunca se inventa la precisión que falta.

## Roadmap (fase 3)

- `DataProvider`/`NewsProvider` de pago para cotizaciones masivas y
  noticias en tiempo real con hora exacta (condición real para correr
  cada pocos minutos sobre el mercado completo).
- Reconectar `strategy.py` (selección de vehículo de opciones, Prompt 9)
  al mensaje nuevo -- quedó desacoplado del pivote a formato de trader
  porque el mockup de Prompt 3 no incluye la sección de estrategia;
  pendiente decidir cómo encaja sin alargar el mensaje.
- Integración con `risk_manager/` para sizing real de posición.
- Alertas de invalidación en vivo (avisar cuando el stop se activa o el
  objetivo se alcanza), en vez de solo medir el resultado al cerrar
  `outcomes.py`.
- Enviar el resumen de `stats.py` y el Market Radar por Telegram bajo
  demanda (comando, mismo espíritu que `/journal stats` del otro bot).
- **Aprendizaje real** (explícitamente NO construido todavía, ver
  "Memoria para aprendizaje futuro" arriba): una vez que haya
  suficientes alertas resueltas, usar `stats.calcular_por_*` para
  proponer ajustes a `config.MomentumConfig` (ej. subir el umbral de
  RVOL si las bandas bajas nunca ganan) -- siempre como una decisión
  humana explícita y documentada, nunca un ajuste automático silencioso.
