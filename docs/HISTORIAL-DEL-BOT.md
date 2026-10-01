# Bot de trading automático con IA — contexto completo

**Qué es esto.** El relato de cómo llegó el bot a ser lo que es: qué se rompió,
por qué, y qué se decidió a cambio. Está escrito para leerse SIN el código
delante, así que sirve igual para alguien que acaba de llegar al equipo, para
una IA con acceso al repo o para una que solo tenga este archivo.

**Dónde está el resto.** `CLAUDE.md` (raíz) tiene las reglas duras y el estado
actual; los README de `momentum_hunter/` y `momentum_paper_trader/` son la
referencia técnica detallada. Este documento es la memoria: lo que ninguno de
los otros dos cuenta, porque no es cómo funciona sino por qué terminó así.

**La sección 6 es la más útil.** Trece bugs reales, con su causa. Se entiende
más del sistema leyéndolos que leyendo cualquier descripción de la
arquitectura, y varios se repetirían si nadie los hubiera anotado.

Estado a: **10 de septiembre de 2026**.

> **Sobre credenciales:** las claves de API (Alpaca, Anthropic, Telegram) viven
> en GitHub Secrets. No están en el código ni en este documento, y no deben
> pedirse ni pegarse en ningún chat.

---

## 1. Qué hace, en una frase

Escanea ~7.600 acciones de EE.UU. buscando movimientos intradía fuertes con una
noticia real detrás, y cuando una cumple todas las condiciones le pregunta a un
LLM si vale la pena entrar. Si dice que sí, coloca la orden solo, en una
**cuenta de práctica de Alpaca** (nunca dinero real).

---

## 2. Arquitectura: dos mitades que no se pisan

### `momentum_hunter/` — el buscador (SIN IA, 100 % determinista)

Encuentra y vigila oportunidades. **Regla dura del proyecto: nunca toma
decisiones con IA, nunca se conecta a un bróker, nunca ejecuta.** Hay una
prueba automatizada que falla si alguien importa algo de ejecución aquí dentro.

Pipeline en dos etapas:

1. **Descubrimiento** (~cada 30 min): universo → filtros de precio/volumen →
   detección de catalizador de noticias → factores de momentum → puntuación →
   las que pasan entran a una watchlist persistida.
2. **Re-chequeo** (~cada 5 min nominal): re-evalúa solo las de la watchlist con
   datos intradía frescos, sin re-escanear el universo.

### `momentum_paper_trader/` — el ejecutor (CON IA)

Lee la watchlist (**solo lectura**, nunca la modifica). Para cada señal
disparada y no revisada: aplica guardarraíles deterministas, consulta al LLM, y
si aprueba coloca una orden en Alpaca.

**El único canal entre las dos mitades es `watchlist.json`.** Unidireccional: el
buscador escribe, el ejecutor solo lee. Así el ejecutor no puede inventarse una
oportunidad, y el buscador no puede colocar una orden.

### La máquina de estados

Cada candidata está en uno de cinco estados:

| Estado | Significa | ¿Terminal? |
|---|---|---|
| `WATCHING` | vigilando, aún no cumple todo | no (único activo) |
| `TRIGGERED` | cumplió todo, es candidata a operar | **sí** |
| `INVALIDATED` | la tesis se rompió (perdió el nivel que la hacía válida) | sí |
| `MISSED` | llegamos tarde, ya se movió demasiado | sí |
| `EXPIRED` | se venció el TTL de vigilancia (120 min) | sí |

Los terminales se conservan 7 días y luego se purgan.

---

## 3. El embudo real, con datos medidos

Acumulado de 33 corridas registradas:

```
universo escaneado    33.000
operables             11.399    35 %
con alguna noticia    10.484    92 %
con catalizador          159   1,5 %   <- el filtro duro
evaluadas                159    100 %
ACCIONABLES               11    6,9 %
```

**Produce ~0,33 señales accionables por corrida.** El embudo funciona.

### Para que una señal dispare, cuatro cosas deben ser ciertas A LA VEZ

1. **Patrón técnico** formándose (gap_and_go, opening_range_breakout,
   trend_continuation)
2. **Dinero entrando**: RVOL ≥ 3,0 (volumen actual vs. promedio)
3. **Riesgo definido**: stop y objetivo que den al menos 1,5:1
4. **A tiempo**: la ruptura ocurrió hace ≤ 8 velas

Las tres primeras y la cuarta son **requisitos duros**. Además el score ajustado
debe superar 55.

El score parte de una base (típicamente 50-71) y se le restan penalizaciones:
−20 si no hay dinero entrando, −15 si no hay desequilibrio de oferta/demanda
(float bajo / short alto), y −100 por cada requisito duro que falte.

---

## 4. La capa de ejecución (Alpaca)

**Cuenta:** paper, ~$5.000. Endpoint hardcodeado a
`https://paper-api.alpaca.markets/v2` — no es configurable por variable de
entorno, a propósito.

**Entrada — una sola orden bracket:**

```json
{
  "symbol": "NTLA", "qty": "58", "side": "buy",
  "type": "limit", "limit_price": "12.88",
  "time_in_force": "day", "order_class": "bracket",
  "extended_hours": false,
  "take_profit": {"limit_price": "13.09"},
  "stop_loss":   {"stop_price": "12.78"},
  "client_order_id": "momentum-NTLA-<timestamp-de-la-señal>"
}
```

Siempre LIMIT para entrar, nunca a mercado. Las dos salidas van en el mismo
pedido y Alpaca las maneja como OCO (cuando una se ejecuta, la otra se cancela).

**Tamaño de posición, en este orden exacto:**

```
1. acciones = $100 de riesgo / (entrada − stop)      <- sizing por riesgo
2. × fracción que pida la IA (0,25 a 1,0)            <- intención de la IA
3. recortado por el MENOR de:
     - 15 % del equity total    (tope de concentración)
     - efectivo real disponible (nunca buying_power: no operamos con margen)
```

El orden importa y costó un trade aprenderlo (ver bug #7 abajo).

**Guardarraíles deterministas, antes de gastar una llamada al LLM:**

- niveles frescos (< 15 min)
- el tope de concentración debe permitir al menos 4 acciones (si no, la fracción
  de la IA redondearía a cero y su decisión no sería expresable)
- mercado abierto de verdad (`GET /v2/clock` de Alpaca)
- el símbolo debe estar `tradable`
- ticker no comprometido ya (ni posición ni orden viva)
- máximo 5 posiciones simultáneas

**Cierre del día:** a las 15:50 ET liquida todo, sin excepción. La lógica para
que la IA decida aguantar hasta mañana está escrita y probada pero **apagada**
detrás de un flag: sin historial no hay forma de distinguir su criterio de su
optimismo, y un stop no protege contra un hueco de apertura.

**Datos de mercado:** Yahoo Finance, NO Alpaca. (El plan gratis de Alpaca da
IEX, ~2,5 % del volumen, inservible para small caps.)

---

## 5. Estado actual: la única operación

**8 de septiembre de 2026 — NTLA**

```
58 acciones · entrada $12,88 · stop $12,78 · objetivo $13,09
confianza de la IA: 7/10 · tamaño reducido a la mitad
resultado: salió por stop a $12,77 · P&L −$6,38 · duró 24 minutos
```

Razonamiento textual de la IA antes de entrar:

> "El catalizador es concreto y verificable — revisión prioritaria de la FDA,
> con la noticia recién saliendo y el precio activándose pocas velas después. La
> lectura intradía respalda la entrada: RVOL de 6.52, precio sobre VWAP y EMA9,
> veredicto 'temprano', no euforia tardía. **Sin embargo el stop ($0.10) es muy
> ajustado frente al ATR diario ($0.73), lo que eleva el riesgo de ser barrido
> por ruido normal**, y el sistema no tiene historial real con catalizadores FDA
> (solo 1 muestra). Entro pero con tamaño reducido para compensar esa fragilidad
> estadística y estructural."

**La IA predijo exactamente cómo iba a fallar.** La operación fue barrida por
ruido sin que la tesis llegara a probarse. Acertó el riesgo aunque no el trade,
y por eso la pérdida fue de $6,38 en vez del doble.

Total histórico: **4 señales llegaron a la IA** (LLY no, AEM no, SHEL no, NTLA
sí). **1 orden colocada. 1 operación cerrada.**

---

## 6. Historial de bugs (lo más útil para entender el sistema)

El bot pasó semanas encontrando cosas sin ejecutar ni una operación. En orden:

1. **`rvol_actual` siempre 0.0.** La vela más reciente de Yahoo trae precio pero
   volumen `null` o `0` explícito (el minuto aún no acumuló trades). El código
   hacía `float(v or 0)`, convirtiendo el dato faltante en un CERO real. Como el
   volumen relativo se calcula solo con esa vela, quedaba en 0 para todo ticker,
   todos los días. **0 alertas en 3.065 candidatos.**
2. **Umbral inalcanzable.** El score mínimo estaba en 85; el máximo jamás
   observado en 3.161 muestras era 81,2. Matemáticamente imposible. Ahora 55.
3. **Universo mal cortado.** `--limit 500` sobre una lista ordenada por
   capitalización dejaba fuera lo que se movía y favorecía a las grandes, justo
   lo contrario del universo small-cap que el bot dice priorizar.
4. **Faltaba salida por tesis rota.** Se agregó un `stop_tesis` congelado: el
   stop normal se recalcula en cada chequeo y persigue al precio hacia abajo,
   así que compararlo con el precio nunca disparaba.
5. **Respuesta vacía del LLM.** Primera consulta real: HTTP 200 con texto vacío.
   Se subió `max_tokens` y se agregó reintento.
6. **Un fallo técnico quemaba la señal.** El fallo anterior se registró como
   "decisión revisada", matando la señal. Se agregó un flag `fallo_tecnico` que
   distingue "el LLM dijo que no" de "no pude preguntarle".
7. **Los niveles de una señal disparada nunca se refrescaban.** La función que
   devuelve señales activas solo devolvía las `WATCHING`, así que al pasar a
   `TRIGGERED` los precios quedaban congelados para siempre. Como el ejecutor
   descarta niveles de más de 15 min, cada señal tenía **UNA sola oportunidad**.
   Si algo fallaba ahí, moría — y el reintento del bug #6 nacía inutilizable.
8. **Concentración de capital.** Con LLY a $1.245, el recorte por efectivo
   dejaba 4 acciones = $4.980: el 99,6 % de la cuenta en UNA posición.
9. **Precisión de precios.** Todo se enviaba con 2 decimales; un stop de
   $0,7512 llegaba como "0.75". El bot opera desde $0,75, así que es real.
10. **Órdenes fuera de sesión.** Los workflows corren 13:00-20:55 UTC pero la
    sesión es 13:30-20:00. Una orden colocada fuera **no se rechaza**: queda
    encolada para la apertura siguiente y se ejecuta al día siguiente con el
    precio límite de hoy.
11. **Un punto y coma.** El LLM devolvió un veredicto perfecto salvo por un `;`
    de más antes del `}`. `json.loads` rechazó el documento entero y no se
    operó, aunque la IA había dicho que sí.
12. **La fracción de la IA se aplicaba DESPUÉS de los topes.** El tope dejó 1
    acción, la IA pidió la mitad, `1 × 0,5 = 0`, y no se operó pese a un "sí"
    explícito. Se recortaba dos veces por lo mismo.
13. **El buscador fabricaba señales fuera de sesión.** Se arregló el lado que
    COLOCA órdenes (bug #10) pero no el que las CREA. 4 de 6 señales vivas
    habían disparado con el mercado cerrado y ninguna podía operarse jamás.

---

## 7. Problemas abiertos — aquí necesitamos ayuda

### A. El bot llega tarde (el problema dominante)

Las cuatro condiciones coinciden durante **minutos**. GitHub Actions no respeta
los horarios programados: el cron pide cada 5 minutos y en la práctica corre
cada 30-90, y hay días que no corre en absoluto.

Ejemplos reales de un solo día, todas con dinero entrando **y** patrón formado,
todas rechazadas correctamente:

| Señal | Qué encontró | Cuándo la vio |
|---|---|---|
| GAP | ruptura con volumen 4,4× | **362 velas tarde** |
| S | ruptura con volumen 4,6× | **118 velas tarde** |
| RPC | ruptura con volumen 9,7× | **91 velas tarde** |

El bot las rechazó diciendo *"esta ya corrió sin nosotros y perseguirla es mal
negocio"*. Tenía razón las tres veces. El problema no es el criterio: es que
nadie estaba mirando cuando la oportunidad existía.

**Pregunta:** ¿un servidor propio (~$5/mes, re-chequeo cada 1-2 min) es la
solución obvia, o hay algo mejor? ¿Qué tan determinante es la latencia aquí de
verdad?

### B. El stop es más ajustado que el ruido de la acción

Los niveles se calculan así:

```python
anclas = [vwap, ema9 que estén por debajo del precio]
stop = max(anclas) * 0.995        # max = el ancla MÁS CERCANA al precio
objetivo = precio + (precio − stop) * 2.0
```

En NTLA: precio $12,880 · EMA9 $12,840 · VWAP $12,676. Tomó el EMA9 (4 centavos
abajo) → stop a 10 centavos. Con el VWAP habrían sido 27 centavos.

**La contradicción:** el sistema exige que la señal sea "temprana" (que el
precio acabe de cruzar sus anclas). Pero si acaba de cruzar el EMA9, el EMA9
está pegado al precio por definición, y el stop sale pegado también. **Mientras
más fresca la señal, más apretado el stop.** Las dos reglas se pelean.

El ATR (volatilidad típica diaria) solo se usa como respaldo cuando no hay
ancla. Nunca como piso mínimo. NTLA se movía 73 centavos al día y el stop estaba
a 10: la operación entera cabía en menos de la mitad del ruido diario.

**Pregunta:** ¿el stop debería ser `min(stop_estructural, precio − k×ATR)`? ¿Qué
valor de k? Y ojo con el costo: un stop más ancho aleja el objetivo de 2R, que
pasa a ser un movimiento de ~1 ATR, difícil de alcanzar intradía.

### C. Calibración sin historial

Números elegidos por razonamiento, no por evidencia (1 operación no es muestra):

- umbral de alerta: 55
- riesgo por operación: $100 sobre $5.000 (2 %)
- máximo 5 posiciones; máximo 15 % del equity por posición
- RVOL mínimo: 3,0
- frescura máxima de niveles: 15 min
- máximo 8 velas desde la ruptura

**Nota honesta:** en una cuenta de $5.000 el tope de concentración MANDA sobre
el riesgo por operación. $100 de riesgo sobre una posición de $750 exigiría un
stop del 13 %, y los de momentum son del 2-5 %. El riesgo real por operación
ronda los $15-35, no los $100. Ese número es un techo, no una meta.

### D. Falta backtest

Nunca se probó la estrategia contra historial. La idea menos mentirosa que
tenemos: correr el evaluador sobre el registro de auditoría acumulado, con
llenados conservadores (limit solo si el máximo/mínimo de la vela lo toca, nunca
el cierre). Los datos point-in-time de noticias gratis casi no existen.

### E. Otros pendientes menores

- El cierre diario no conoce feriados ni medias sesiones (sí conoce el horario
  de verano/invierno).
- Halts intradía: Alpaca no expone un campo para eso; solo verificamos
  `tradable`, que no cubre un halt reciente.
- Falta medir expectancy, MAE/MFE, tiempo hasta el llenado, y qué porcentaje de
  señales disparadas mueren por precios rancios.

---

## 8. Restricciones que no se negocian

- **100 % paper trading.** Nada de dinero real sin una decisión explícita y
  separada. El endpoint está hardcodeado a paper a propósito.
- `momentum_hunter/` no puede incorporar IA, brókers ni ejecución. Nunca.
- Sin margen: se opera contra el efectivo real, jamás contra `buying_power`.
- Los límites de riesgo son deterministas en código, nunca los decide el LLM.
- Todo fail-closed: ante cualquier duda, no colocar la orden.
- Las claves viven en GitHub Secrets y no se pegan en ningún chat.

---

## 9. Datos técnicos de referencia

**Regla PDT:** FINRA la retiró y Alpaca implementó el marco de margen intradía
el **4 de junio de 2026**. Ya no existe el mínimo de $25.000 ni el límite de 3
day trades en 5 días. Verificado en la documentación de Alpaca. Con $5.000 la
estrategia es viable.

**Otros detalles de Alpaca aprendidos:**
- Las fraccionarias NO se pueden combinar con órdenes bracket.
- Precios: 2 decimales si ≥ $1, 4 si < $1, y como string.
- `POST` con 200 no significa llenado: puede ser `accepted` / `pending_new`.
- Un bracket no opera en extended hours.
- El `time_in_force` aplica a TODA la orden: un bracket GTC dejaría la orden de
  entrada viva toda la noche.

**Estado del código:** 653 pruebas automatizadas en verde. Todo comentado en
español explicando el POR QUÉ, incluidas las limitaciones conocidas sin
maquillar.
