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
python -m momentum_hunter.run --solo-watchlist        # chequeo liviano de la watchlist ("Fase 2")
```

Con `MOMENTUM_TELEGRAM_BOT_TOKEN`/`MOMENTUM_TELEGRAM_CHAT_ID` (o, si no
existen, `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`) en el entorno, manda
las alertas por Telegram. `.github/workflows/momentum_hunter.yml` corre
el escaneo completo varias veces al día en horario de mercado;
`.github/workflows/momentum_hunter_watchlist.yml` corre el chequeo
liviano de la watchlist cada ~5 minutos (ver "Fase 2" más abajo);
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

## Detector de entradas, "Fase 1" (pedido 2026-08-10)

Pedido explícito del dueño del producto: "el bot está funcionando como
radar, no como un detector de entrada... quiero que me avise ÚNICAMENTE
cuando una oportunidad esté realmente lista para entrar". La narrativa
de arriba sigue existiendo (`report.formatear()`, se imprime en el log
de cada corrida), pero **ya no es lo que llega a Telegram** -- una
alerta accionable ahora manda el mensaje corto de `report.
formatear_entrada()`, pensado para leerse en 5-10 segundos:

```
🚨 ENTRADA CONFIRMADA

🔴 RKLB
💵 $80.25

ENTRADA
$80.20–$81.40

🛑 STOP
$79.40

🎯 OBJETIVO
$81.95

R/R
2.0 : 1

POR QUÉ AHORA
✓ Ruptura confirmada
✓ Volumen acelerándose
✓ Momentum a favor
✓ Catalizador confirmado

⏱ ENTRADA: AHORA

⚠️ Si pasa de $81.40:
NO PERSEGUIR.
```

Lo que cambió en la lógica de decisión (no solo en el formato):

- **Zona de entrada, no un precio único** (`report.zona_entrada`): el
  mismo nivel de ruptura del patrón que ya usaba `_nivel_invalidacion`
  (si se pierde, cancela la idea), más una tolerancia editorial fija
  (`cfg.tolerancia_zona_entrada_pct`, 1.5% por defecto) -- pasar ese
  techo es la señal de "esto ya corrió, no perseguir", visible en el
  propio mensaje.
- **Veto explícito de riesgo/recompensa** (`evaluator.py`, gate nuevo):
  antes, una candidata sin `stop` definido (sin VWAP/EMA9/ATR
  disponibles) podía volverse accionable igual. Ahora, sin un
  riesgo/recompensa de al menos `cfg.riesgo_recompensa_minimo` (1.5:1
  por defecto), la respuesta es no, sin importar el score.
- **MACD intradía como confirmación, nunca como gate**
  (`factors/intradia.macd_intradia`, mismos periodos 12/26/9 que el
  MACD diario de `factors/momentum.py`): si está a favor, aparece como
  "Momentum a favor" en el mensaje -- nunca decide nada por sí solo.
- **La regla de "no perseguir" real sigue siendo la de siempre**
  (`early_opportunity.py`: extensión desde VWAP/EMA9 + velas desde la
  ruptura) -- Fase 1 no duplicó esa lógica, solo la hace visible con la
  zona de entrada y la advertencia explícita en el mensaje.

**La vigilancia en vivo de lo que queda en observación (era la
limitación honesta de esta sección) ya existe -- ver "State Engine y
vigilancia persistida, Fase 2" más abajo.**

## State Engine y vigilancia persistida, "Fase 2" (pedido 2026-08-11)

Pedido explícito: "Yo NO quiero un screener que me diga qué acciones son
interesantes. Quiero un Opportunity Hunter que vigile continuamente un
pequeño grupo de acciones y me avise EXACTAMENTE cuando exista una
entrada accionable." Antes de escribir código se hizo una auditoría
completa de lo que ya existía (competencia relativa, regla de no
perseguir, `DataProvider` desacoplado, vigilancia post-alerta de
`vigilancia.py`) contra lo que realmente faltaba (estado persistido,
instrumentación de latencia). El dueño del producto priorizó
explícitamente: "State Engine primero, todo lo demás depende de él",
dejando el comando `/trade` para después, aparte.

### Los 5 estados (`watchlist.py`)

Cada candidata con catalizador confirmado que llega a la etapa 2 entra a
una watchlist persistida (`momentum_hunter/watchlist.json`, mismo patrón
de persistencia por archivo JSON que `tracker.py`/`heartbeat.py`) con
exactamente 5 estados, cada transición con su propio timestamp:

- **WATCHING** -- se detectó un catalizador + patrón preliminar, pero
  todavía no confirma una entrada accionable.
- **TRIGGERED** -- confirmó una entrada de verdad (el mismo árbol de 5
  preguntas de `evaluator.py`, la misma competencia relativa de
  `seleccionar_y_auditar` si compiten varias a la vez) -- se manda la
  alerta corta a Telegram.
- **INVALIDATED** -- el catalizador salió de la ventana de vigencia
  (`catalysts.detector.dentro_de_ventana`, reutilizada, no reinventada)
  antes de confirmar nada.
- **MISSED** -- el Early Opportunity Engine (`early_opportunity.py`, sin
  cambios) dio veredicto "tarde": ya se movió demasiado para entrar sin
  perseguir.
- **EXPIRED** -- lleva más de `cfg.minutos_maximos_en_watching` (120
  minutos por defecto) sin resolver nada.

```
09:42       -- RKLB -> WATCHING (contrato + patrón preliminar)
09:49:02    -- RKLB -> TRIGGERED (confirmó entrada)
09:49:03    -- Telegram enviado
```

### `signal_latency_ms`

Cuatro timestamps con nombre explícito por candidata, capturados en el
momento real en que ocurre cada paso (no estimados): `market_event_ts`
(la vela que confirmó), `data_received_ts` (cuándo llegó el dato del
proveedor), `evaluador_ts` (cuándo terminó de evaluar) y
`telegram_enviado_ts` (cuándo salió el mensaje). `signal_latency_ms` es
la diferencia entre el primero y el último -- la métrica real de qué tan
rápido avisa el bot, no una promesa de marketing.

### Por qué 5 minutos, no "tiempo real"

Pedido explícito: "No quiero que simplemente pongas GitHub Actions cada
5 minutos y consideres esto 'tiempo real'." La respuesta honesta, medida
en corridas reales de este proyecto:

- El escaneo completo (`momentum_hunter.yml`, universo completo con
  Yahoo secuencial, ver `data/provider.py`) tarda ~6 minutos.
- El chequeo liviano de la watchlist (`--solo-watchlist`, sin barras
  diarias, solo sobre los tickers ya en observación) tarda ~7-20 segundos.
- GitHub Actions no garantiza cron por debajo de 5 minutos de forma
  confiable (documentado por GitHub: puede haber demora bajo carga) --
  pedir "cada minuto" prometería algo que la plataforma no cumple.

Por eso `.github/workflows/momentum_hunter_watchlist.yml` corre
`--solo-watchlist` cada 5 minutos, en un workflow SEPARADO del escaneo
completo (que sigue corriendo cada 30 minutos para descubrir candidatas
nuevas) -- los dos comparten `concurrency.group` para nunca pisarse
escribiendo `watchlist.json` a la vez. 5 minutos es la cadencia máxima
realista con la arquitectura actual (Yahoo + GitHub Actions gratis); una
cadencia de segundos requeriría un proveedor de pago con websockets
(Polygon, Alpaca), lo cual `DataProvider` ya permite sin tocar ninguna
lógica de trading (ver "Arquitectura" arriba).

### Sigue siendo RESEARCH + SIGNAL + ALERT, nunca EXECUTION

Reafirmado explícitamente por el dueño del producto en este pedido: "El
sistema debe seguir siendo estrictamente: RESEARCH + SIGNAL + ALERT.
Nunca: RESEARCH + SIGNAL + EXECUTION" y "NO quiero que el sistema opere
automáticamente. Yo siempre tomo la decisión final." Fase 2 no agrega
ninguna conexión a un broker -- `revisar_watchlist()` termina en un
mensaje de Telegram, igual que siempre.

### Uso

```bash
python -m momentum_hunter.run --solo-watchlist              # chequeo liviano, cada ~5 min
python -m momentum_hunter.run --solo-watchlist --dry-run    # calcula y muestra, no manda ni persiste
```

### Lo que Fase 2 explícitamente NO incluyó (implementado después, ver "Fase 3" abajo)

Priorización explícita del dueño del producto ("State Engine primero,
todo lo demás depende de él" / "/trade después, aparte"):

- **Comandos de Telegram** (`/trade`, `/status`, `/radar`) -- pospuestos
  a propósito en Fase 2 (requerían un cambio de superficie distinto:
  recibir webhooks, no solo enviar `requests.post`) -- implementados en
  "Fase 3" (ver sección dedicada más abajo), en el servicio de Render ya
  desplegado (`telegram_bot/`), no en este cron.
- **Backtest/replay minuto a minuto** contra sesiones históricas, para
  poder responder objetivamente "¿el bot realmente habría detectado esto
  a tiempo?" -- pospuesto a propósito, después de que la vigilancia en
  vivo lleve unos días corriendo con datos reales que auditar.
- Los 14 escenarios de robustez pedidos (reversión inmediata tras
  ruptura, gap ya extendido, 2-5 triggers simultáneos, fallo del
  proveedor, velas faltantes/duplicadas, precio exacto en el borde de la
  zona, stop y objetivo tocados en la misma vela, fallo de Telegram,
  reinicio del proceso en distintos momentos del ciclo de vida) están
  cubiertos parcialmente por las pruebas actuales (`test_watchlist.py`,
  `test_run_watchlist.py`) -- el estado persistido en JSON YA sobrevive
  un reinicio del proceso (es la garantía central de este diseño), pero
  no los 14 casos exactos tienen todos una prueba dedicada todavía.

### ¿Está listo para usar dinero real?

**No.** Con honestidad, no solo porque los tests pasan:

1. La cadencia real (5 minutos) significa que una ruptura puede tardar
   hasta 5 minutos en detectarse -- para un patrón que se mueve fuerte en
   segundos, esa ventana ya se puede haber cerrado. El diseño evita
   alertar TARDE (el veredicto "tarde" de `early_opportunity.py` sigue
   aplicando), pero no puede prometer alertar en el segundo exacto.
2. Sin backtest/replay contra sesiones reales, no existe todavía
   evidencia medida de qué tan bien funciona esta cadencia en la
   práctica -- solo el razonamiento de diseño, no datos.
3. Los 14 escenarios de robustez pedidos no están todos cubiertos por
   pruebas dedicadas (ver arriba).
4. Sigue habiendo dependencia de datos gratis de Yahoo (ver
   "Limitaciones honestas" abajo) -- sin un proveedor de pago, un fallo o
   bloqueo de IP puede dejar la watchlist sin re-chequear por un ciclo
   completo.

Lo que SÍ está listo: la arquitectura de estado (nunca se pierde una
candidata al reiniciar el proceso), la instrumentación de latencia (se
puede medir objetivamente qué tan rápido es el sistema, en vez de
asumirlo), y la garantía de que el sistema nunca ejecuta nada por su
cuenta -- toda decisión de entrar sigue siendo del dueño del producto.

## Integración de Telegram en tiempo real, "Fase 3" (pedido 2026-08-11)

Pedido explícito: "convertir el bot en una interfaz de decisión en
tiempo real... ALERT FIRST, ANALYTICS SECOND". El State Engine de Fase 2
sigue siendo la ÚNICA fuente de verdad -- esta fase NO le agrega
decisiones nuevas, solo traduce cada transición a un mensaje corto y
legible en 5-10 segundos, y agrega la capacidad de CONSULTAR (nunca
ejecutar) ese estado desde Telegram.

```
WATCHLIST / STATE ENGINE  →  TRANSICIÓN  →  MENSAJE DE TELEGRAM
```

### Un mensaje por transición, nunca por "sigue subiendo"

Cada uno de los 5 estados tiene su propio formato corto
(`report.mensaje_watching`/`mensaje_invalidated`/`mensaje_missed`/
`mensaje_expired`, y `formatear_entrada` para TRIGGERED):

- **WATCHING** -- se manda UNA sola vez, en el instante en que la
  candidata entra a vigilancia (no en cada re-chequeo mientras sigue
  esperando). Incluye la zona de entrada y qué le falta
  (`evaluator.explicar_rechazo`, reusado, nunca reinventado).
- **TRIGGERED** -- entrada, stop, objetivo, R/R, por qué ahora, TIMING,
  y "cancelo la idea si..." -- todo con valores que el pipeline YA
  calculó, cero indicadores nuevos.
- **INVALIDATED** -- motivo real (el mismo texto que ya queda en
  `Transicion.motivo`), sin explicación larga.
- **MISSED** -- entrada original vs. precio actual, "NO PERSEGUIR" --
  nunca se convierte en una entrada nueva solo porque el precio sigue
  subiendo.
- **EXPIRED** -- venció el TTL, "No operar."

### Deduplicación -- qué garantiza y qué NO

Cada transición del State Engine ocurre, por diseño, como máximo una vez
(una entrada nace en WATCHING una sola vez; un estado terminal
-TRIGGERED/INVALIDATED/MISSED/EXPIRED- no se vuelve a evaluar, ver
`watchlist.ESTADOS_TERMINALES` y `run._filtrar_ya_resueltas_hoy`). El
archivo se persiste (`watchlist.guardar`) ANTES de intentar cualquier
envío a Telegram -- si el proceso muere justo después de mandar un
mensaje, el estado en disco ya refleja la transición.

**Lo que esto NO puede garantizar** (honestidad explícita, sin declarar
"exactly once" sin poder probarlo): si el contenedor de GitHub Actions
muere DESPUÉS de enviar un mensaje pero ANTES de que el workflow haga el
commit de `watchlist.json` a `main`, la siguiente corrida arranca desde
el último estado COMMITEADO (anterior al envío) y puede reprocesar esa
transición. Es una ventana real, de infraestructura (cron + commit de
git), no de la lógica de decisión -- cerrarla del todo requeriría
persistencia incremental fuera de este diseño (fuera de alcance de esta
fase, ver "NO optimices infraestructura todavía").

### Latencia -- qué se mide y qué no

Cada transición (`watchlist.Transicion`) guarda, cuando el dato existe
de verdad:

- `deteccion_ts` -- cuándo llegaron los datos que originaron la
  transición (`None` para EXPIRED -- un TTL de reloj, no un dato de
  mercado puntual).
- `evaluacion_ts` -- cuándo la lógica de decisión resolvió el veredicto.
- `timestamp` -- cuándo cambió el estado (siempre existe).
- `mensaje_generado_ts` / `telegram_enviado_ts` -- alrededor del envío real.
- `latencia_desde_deteccion_ms` / `latencia_desde_evaluacion_ms` /
  `latencia_desde_transicion_ms` -- calculadas SOLO si su punto de
  partida existe, nunca inventadas.

Limitación real (Yahoo Finance, datos gratis): "detección" es cuándo
llegó la VELA de 1 minuto, no el tick exacto del mercado -- la latencia
medida siempre incluye ese margen de hasta ~60 segundos que no es
atribuible al bot. Ver `telegram_bot/README.md` para más detalle.

#### Latencia REAL de punta a punta (medida 2026-08-21)

Medido sobre 60 corridas programadas reales del re-chequeo de watchlist
(el camino por el que dispara una señal) más 201 transiciones con
latencia registrada:

| Etapa | p50 | p90 | máximo medido |
|---|---|---|---|
| Espera al siguiente chequeo (cron de 5 min) | 2,5 min | 5 min | 5 min |
| **Retraso de GitHub en arrancar el cron** | **2,2 min** | 3,9 min | 4,9 min |
| Duración del análisis | 32 s | 4,2 min | 10,7 min |
| Envío a Telegram | 13 s | 22 s | 27 s |
| **Total evento de mercado → orden colocada** | **~5 min** | **~13 min** | **~20 min** |

**El código tarda 32 segundos; el resto es infraestructura.** Los dos
cuellos de botella son ajenos a la lógica: el cron de GitHub Actions no
baja de 5 minutos, y además arranca tarde ~2 minutos en promedio (nunca
puntual, medido). Optimizar el código no mueve esta aguja.

Consecuencia para el trading, dicha sin maquillar: para un operador de
momentum, 5-13 minutos es tarde. Lo que amortigua el golpe es que la
entrada es una orden LIMITADA al precio calculado, nunca a mercado: si
el precio se escapó durante la demora, la orden simplemente no se llena.
El retraso se paga en oportunidades perdidas, no en entradas caras --
la falla menos peligrosa de las dos. (Y desde el 2026-08-21 hay además
un tope de frescura: ver `momentum_paper_trader.config.
minutos_maximos_niveles`.)

**Anotado para el futuro, NO implementado**: la única forma real de bajar
de 5 minutos es sacar el bot del cron de GitHub Actions y ponerlo en un
proceso siempre encendido (el mismo tipo de alojamiento que ya usa
`telegram_bot/` en Render), revisando cada ~30 segundos. Eso llevaría el
total a menos de un minuto. Es un cambio de arquitectura, no un ajuste, y
la decisión explícita (2026-08-21) fue esperar: primero hace falta saber
si las señales sirven. Acelerar un sistema que elige mal solo hace que
pierda más rápido. Re-evaluar cuando `outcomes.py` tenga resultados
medidos que justifiquen la inversión.

### Comandos (`/trade`, `/status`, `/radar`, `/help`)

momentum_hunter en sí sigue siendo puro cron (GitHub Actions), sin
servidor propio -- no puede recibir webhooks. Los comandos se sirven
desde el servicio de Telegram YA desplegado en Render
(`telegram_bot/app.py`), en una ruta NUEVA y separada
(`POST /momentum/webhook`, bot de Telegram DISTINTO al del wizards bot,
que ya tenía su propio `/trade`) -- lee `watchlist.json`/`auditoria/`
vía la API de contenidos de GitHub, nunca escribe nada. Ver
`telegram_bot/README.md` para los comandos exactos, las env vars nuevas,
y el paso manual pendiente (registrar el webhook con el token real, algo
que solo el dueño del bot puede hacer).

### Sigue siendo RESEARCH + SIGNAL + ALERT -- los comandos son READ-ONLY

Ningún comando nuevo escribe `watchlist.json`, se conecta a un broker, ni
coloca una orden -- `/trade TICKER` lee el estado EXISTENTE (los niveles
que el pipeline ya cacheó en `EntradaWatchlist.ultima_entrada/
ultimo_stop/ultimo_objetivo`, ver `watchlist.actualizar_niveles`), nunca
evalúa un ticker nuevo bajo demanda.

## Filtros de universo (etapa 1)

| Filtro | Banda small-cap (default) | Banda large-cap (complementaria) |
|---|---|---|
| Bolsas | NYSE, NASDAQ, AMEX | NYSE, NASDAQ, AMEX |
| Precio | $0.75 - $20 | > $20 (sin techo) |
| Capitalización | < $2,000 millones | sin techo |
| Volumen promedio | ≥ 300,000 acciones/día | ≥ 1,000,000 acciones/día |
| Excluye | ETFs, SPACs, closed-end funds, ADRs de baja liquidez | igual |
| Candidatos que pasan a intradía | Top 50 por score, con catalizador confirmado | igual |

Las dos bandas corren en la MISMA corrida, nunca se excluyen entre sí --
cualquier ticker cae en una banda o en la otra según su precio, nunca en
ninguna a la vez.

## Modo large-cap (pedido 2026-08-07, tras el gap de 17% de Airbnb en un día)

El dueño del producto vio a ABNB subir 17% en un día y preguntó por qué
el bot no lo había alertado -- la respuesta honesta fue "porque está
diseñado para small-caps, y Airbnb ni siquiera entra al universo que
escanea". Este modo abre una segunda banda de universo (arriba)
COMPLEMENTARIA a la small-cap de siempre, para empresas de cualquier
tamaño de capitalización.

**Lo que cambia mecánicamente** (`evaluator.py`, pregunta 3 del árbol de
decisión): una small-cap puede explotar con relativamente poco volumen
porque tiene poco float en circulación (desequilibrio oferta/demanda);
una mega-cap no tiene ese mecanismo estructural -- exigirlo ahí sería una
penalización disfrazada de pregunta. Para un candidato `es_large_cap`,
esa pregunta se omite por completo (ni penaliza, ni aparece en las
explicaciones de rechazo): el catalizador confirmado + un patrón real ya
en marcha (`gap_and_go`/`opening_range_breakout`, que por definición
exigen un gap real -- ver `classification.py`) hacen ese trabajo. El
resto del árbol (catalizador confirmado, dinero entrando, patrón claro,
Early Opportunity Engine) es idéntico para las dos bandas.

**Honestidad explícita** (la misma que ya rige todo el proyecto,
Principio 3/CIO): esto NO predice un movimiento antes de que exista
ninguna señal pública -- para una acción con cobertura total de Wall
Street, eso no es alcanzable por ningún bot. Lo que sí hace es avisar en
el instante en que el catalizador + el gap premarket ya son detectables,
antes de que abra el mercado regular -- una ventana real de minutos u
horas, no una promesa de anticipación imposible.

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
- **`--limit N` corta por ranking, no por relevancia -- bug real
  encontrado el 2026-08-20 (Moderna/MRNA no se avisó a tiempo).** Cuando
  el universo cae al respaldo de la SEC (ver `universe.py`, activo desde
  el 404 de NASDAQ Trader del 2026-07-27), la lista llega ordenada por
  capitalización de mercado descendente -- `--limit` corta esa lista tal
  cual, así que cualquier ticker más allá de la posición N queda
  completamente fuera de la etapa 1, sin importar qué tan en movimiento
  esté ese día. MRNA estaba en la posición #530; con el `--limit 500` de
  ese momento, ni siquiera se le pidió la barra diaria. Subido a 1000
  (ver `.github/workflows/momentum_hunter.yml`, verificado contra
  tiempos reales de corrida: ~7-8 min con 500, presupuesto de 25-30 min)
  como mitigación inmediata -- pero sigue siendo un corte duro, y
  estructuralmente favorece a las empresas más grandes del mercado sobre
  el universo small-cap que este bot dice priorizar. No resuelto de
  fondo: requeriría no depender de un ranking por market cap para decidir
  qué N tickers escanear (rotación del universo entre corridas, un
  proveedor de cotizaciones masivas, o un pre-filtro barato de
  gap/volumen antes de gastar la llamada cara de barras diarias).
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
- **5 minutos es el piso real de la vigilancia en vivo** (ver "Fase 2"
  arriba), no segundos -- limitación de GitHub Actions + Yahoo gratis,
  no de la lógica de decisión.

## Roadmap (fase 4)

- **`/trade TICKER --full`** con el tablero completo (Semáforo, señales
  que confirman/fallan, ventana estimada) -- Fase 3 solo implementó la
  versión corta (`/trade TICKER`, ver "Integración de Telegram" arriba).
- **Backtest/replay minuto a minuto** contra sesiones históricas
  (pospuesto explícitamente otra vez en Fase 3: "NO hagas un backtest en
  esta fase") -- para medir objetivamente qué tan bien funciona la
  cadencia de 5 minutos en la práctica, en vez de solo razonarlo por diseño.
- **Interfaz de opciones** (pedido explícito de Fase 3, sección 15: dejar
  espacio en el mensaje sin implementar selección automática) -- Acción/
  Vencimiento/Strike/Prima/Delta/Riesgo máximo, cuando exista lógica real
  de selección que no modifique el mensaje corto actual.
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
