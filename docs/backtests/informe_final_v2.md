# Informe final — backtest de la estrategia v2 (2025-09-26 a 2026-09-25)

Cierre del encargo del 29 de septiembre de 2026 (fases 1 a 5). Datos: 12 meses
de velas SIP de Alpaca, noticias de Benzinga vía Alpaca, subasta oficial para
el gap, clasificación de catalizadores con `claude-sonnet-5` (misma caché en
todas las corridas). Equity inicial $5.000, slippage 0,15 % por lado, sin
comisión. Todo corrió en GitHub Actions (`backtest_v2.yml`, runs 4 a 8); los
informes por variante están en esta misma carpeta
(`estrategia_v2_2025-09-26_2026-09-25_<variante>.md`) y el comparativo de cada
corrida sobreescribe `..._comparativo.md`.

**Fase alcanzada: 5 (cierre).** Ninguna variante de entrada, salida ni
universo cumple el criterio de promesa (expectativa > 0,2 R, factor de
beneficio > 1,3, MFE mediana > 0,5 R y al menos 25 trades). La única que pasa
las tres métricas de calidad (`u1`, universo $2–20) lo hace con 9 trades, y las
dos iteraciones de la fase 3 sobre ella (`u1_obj15`, `u1_st45`) no pueden
subir de esos 9 porque el filtro de precio deja 55 señales y el tope de stop
del 4 % descarta 46. De los planes B, el PEAD con titular de resultados
(`peadnoticia`) es lo único que pasa los cuatro números del criterio, y lo
pasa por muy poco (27 trades, +0,22 R, FB 1,36), con un evento que es un proxy
del pedido y manteniendo posiciones de un día para otro: se reporta como
pista, no como estrategia. No se abre la fase 4: no hay YAML v2 nuevo, no hay
cambios en el hunter ni en el ejecutor, no se toca producción.

---

## 1. Tabla comparativa de todo lo corrido

Salidas: stop / tiempo / objetivo / breakeven / cierre. MFE y MAE en R
(promedio / mediana). «Promesa» = expectativa > 0,2 R y FB > 1,3 y MFE mediana
> 0,5 R y ≥ 25 trades.

| corrida | variante | qué cambia | trades | acierto | expectativa | FB | drawdown | salidas | MFE prom. / med. | MAE prom. / med. | promesa |
|---|---|---|---:|---:|---:|---:|---:|---|---|---|:---:|
| 4 | base (stop 6 %) | `stop_max_pct` 0,06 | 32 | 15,6 % | −0,16 R | 0,54 | 2,6 % | — | +0,51 / — | +0,54 / — | no |
| 5 | base (stop 4 %) | config del YAML | 17 | 23,5 % | +0,03 R | 1,15 | 1,1 % | 1 / 12 / 2 / 1 / 1 | +0,57 / +0,23 | +0,47 / +0,38 | no |
| 5 | v1 | stop = mín. vela de ruptura, 1,5–4 % | 62 | 17,7 % | −0,24 R | 0,54 | 5,8 % | 23 / 15 / 9 / 14 / 1 | +0,89 / +0,47 | +0,81 / +0,87 | no |
| 5 | v2 | spread máx. 0,6 % | 27 | 29,6 % | +0,01 R | 1,03 | 1,7 % | 1 / 20 / 3 / 1 / 2 | +0,52 / +0,22 | +0,49 / +0,45 | no |
| 5 | v3 | ORB 15 min, ventana 09:46–11:00 | 9 | 22,2 % | −0,13 R | 0,20 | 0,4 % | 0 / 7 / 0 / 0 / 2 | +0,33 / +0,14 | +0,38 / +0,30 | no |
| 5 | v4 | stop de tiempo 60 min | 17 | 29,4 % | 0,00 R | 1,01 | 1,3 % | 1 / 10 / 2 / 3 / 1 | +0,71 / +0,30 | +0,54 / +0,45 | no |
| 7 | e1 | entrada en el retroceso al máx. ORB / VWAP, stop = mín. del retroceso, espera 20 min | 50 | 18,0 % | −0,27 R | 0,53 | 6,4 % | 20 / 11 / 8 / 11 / 0 | +0,83 / +0,65 | +0,87 / +0,79 | no |
| 7 | e2 | orden límite en el máx. del ORB, válida 15 min | 17 | 41,2 % | +0,06 R | 1,27 | 0,7 % | 2 / 12 / 2 / 0 / 1 | +0,62 / +0,30 | +0,39 / +0,32 | no |
| 7 | e3 | ruptura del máx. premercado 09:31–09:45, vol ≥ 1,5×, stop = mín. 3 primeros min | 0 | — | — | — | — | 8 señales, 8 con stop > 4 % | — | — | no |
| 7 | u1 | universo $2–20 | 9 | 33,3 % | +0,24 R | 2,18 | 0,8 % | 1 / 4 / 2 / 1 / 1 | +0,98 / +0,56 | +0,53 / +0,28 | no (n = 9) |
| 8 | u1_obj15 | u1 + objetivo 1,5 R | 9 | 44,4 % | +0,41 R | 3,04 | 0,8 % | 1 / 4 / 4 / 0 / 0 | +0,82 / +0,56 | +0,53 / +0,28 | no (n = 9) |
| 8 | u1_st45 | u1 + stop de tiempo 45 min | 9 | 33,3 % | +0,22 R | 1,92 | 0,8 % | 1 / 4 / 2 / 1 / 1 | +0,98 / +0,56 | +0,53 / +0,30 | no (n = 9) |
| 7 | pead (plan B) | 8-K 2.02 + gap ≥ 4 % + RVOL ≥ 3, $2–20, cierre d0 → cierre d3 o stop 6 % | 0 | — | — | — | — | EDGAR 403 en Actions (1.943 / 1.943) | — | — | no medible |
| 8 | peadnoticia (plan B) | igual, con titular de resultados de Benzinga como evento | 27 | 44,4 % | +0,22 R | 1,36 | — | 11 / 0 / 0 / 0 / 16 | +1,69 / +1,43 | +1,15 / +0,82 | sí en números, con reservas (§4b) |
| 7 | seguimiento (plan B) | nivel 1 día 1 → ruptura del máx. del día 1 en el día 2, stop 4 %, cierre EOD | 186 | 31,2 % | −0,16 R | 0,71 | — | 87 / 0 / 0 / 0 / 99 | +1,11 / +0,53 | +0,80 / +0,95 | no (muestra parcial) |

Notas de la tabla:

- Las corridas 4 y 5 usaron la misma caché de clasificaciones que las 7 y 8;
  lo único que cambió entre la 4 y la 5 fue el tope del stop (6 % → 4 %) y el
  arreglo de lectura de la IA (16 respuestas ilegibles al primer intento, 1
  tras el reintento, contra 154 de 1.000 antes del arreglo).
- En la corrida 4 el informe aún no calculaba medianas.
- Salidas por tiempo: en ninguna variante de la v2 hay un trade cerrado por el
  stop de tiempo que haya pasado de +0,5 R antes de cerrarse (base 12 trades:
  2 negativos, 8 entre 0 y 0,25 R, 2 entre 0,25 y 0,5 R; e2: 0 / 5 / 7 / 0;
  v4 con 60 min: 1 / 5 / 4 / 0).

### Embudo de la base (corrida 5, stop 4 %)

| etapa | sobreviven | % de la anterior |
|---|---:|---:|
| universo (símbolos × sesiones) | 1.926.676 | — |
| gap ≥ mínimo (subasta oficial) | 24.568 | 1,3 % |
| sin acción corporativa | 24.361 | 99,2 % |
| con noticia en 24 h | 8.856 | 36,4 % |
| RVOL ≥ mínimo | 5.494 | 62,2 % |
| precio > VWAP | 4.802 | 87,4 % |
| ruptura del rango de apertura (con volumen) | 1.299 | 27,1 % |
| spread ≤ máximo | 339 | 26,1 % |
| SPY > su VWAP | 219 | 64,6 % |
| catalizador operable (IA) | 71 | 32,4 % |
| stop ≤ 4 % | 17 | 23,9 % |

De las 71 señales, 54 se descartan por el stop: la distancia del mínimo del
rango de apertura al precio de la señal tiene mediana 8,7 % (máximo 22 %); solo
17 señales piden menos del 4 %. Es el mismo cuello de botella con el tope al 6
% (corrida 4: 32 trades) y la calidad no mejora al abrirlo.

---

## 2. Diagnóstico de entrada (fase 1)

Sobre las 71 señales base, sin simular trades, mirando los 60 minutos que
siguen al cierre de la vela de ruptura (`diagnostico.py`):

| medida | valor |
|---|---:|
| volvió al máximo del ORB | 88,7 % (63 de 71), minuto mediano 2 |
| volvió al VWAP | 70,4 % (50 de 71), minuto mediano 11 |
| retroceso mínimo (mediana) | 2,2 % bajo el precio de la señal, en el minuto 17 |
| máximo tras el retroceso (mediana, R = precio − mínimo) | +0,57 R |

| entrada hipotética | MFE mediana | señales con MFE > 0,5 R |
|---|---:|---:|
| cierre de la vela de ruptura (R = ORB acotado 1,5–4 %) | +0,62 R | 41 |
| retest del máximo del ORB (R = mínimo del retroceso) | +1,45 R | 49 |
| retest del VWAP (R = mínimo del retroceso) | +1,28 R | 40 |

Lectura: la ruptura del ORB casi siempre se devuelve (9 de cada 10 señales
vuelven a tocar el máximo del rango en los dos minutos siguientes) y el
recorrido posterior, medido desde el nivel del retest, es más del doble que
desde el cierre de ruptura. **Pero ese MFE es un techo, no un resultado:** la
variante `e1`, que intenta capturar exactamente ese retest con un stop en el
mínimo del retroceso, sale con −0,27 R y un 18 % de acierto (50 trades). La
diferencia entre el techo (+1,45 R de MFE mediana) y lo realizado es que el
retroceso que define el stop es a la vez el que saca al trade: MAE mediana
+0,79 R con stops de 1,5–4 %, y 20 de 50 trades salen por stop. Lo que el
diagnóstico ve con el mínimo ya conocido, la regla no lo puede saber en el
momento de entrar.

Lo que explica el 15–25 % de acierto de la v2, con números:

1. **El stop del ORB no cabe.** Mediana del stop requerido 8,7 % contra un tope
   de 4 %. Las 17 señales que sí caben son las de rango de apertura estrecho,
   y en esas el precio se mueve poco después (MFE mediana +0,23 R en la base).
2. **El precio va más en contra que a favor desde el cierre de ruptura.** MAE
   mediana +0,38 R contra MFE mediana +0,23 R en la base; en `v1` (stop en la
   vela de ruptura) MAE +0,87 R contra MFE +0,47 R.
3. **El stop de tiempo cierra trades que no llegaron a nada.** 12 de 17 salen
   por tiempo con MFE mediana +0,14 R; alargarlo (v4, 60 min; st45) no cambia
   el resultado porque los trades no estaban yendo a ningún lado.
4. **El rango $20–50 no aporta.** 8 trades, 12,5 % de acierto, −0,21 R, los 8
   por tiempo, MFE mediana +0,12 R. Es la razón de que `u1` mejore: quita ese
   tramo sin agregar nada.

---

## 3. Decisión por fases

**Fase 2 (variantes de entrada):**

- `e1`: 50 trades, −0,27 R, FB 0,53, MFE mediana +0,65 R. Cumple MFE y n, no
  expectativa ni FB.
- `e2`: 17 trades, +0,06 R, FB 1,27, MFE mediana +0,30 R. No cumple nada
  salvo estar cerca del FB; solo 5 de 17 fueron por debajo de $10 y ahí pierde
  (−0,09 R).
- `e3`: 8 señales, 0 trades. Las 8 piden un stop mayor al 4 % (mediana 11,4
  %): el mínimo de los tres primeros minutos queda demasiado lejos de una
  ruptura que llega a las 09:31–09:45.
- `u1`: 9 trades, +0,24 R, FB 2,18, MFE mediana +0,56 R. Cumple las tres
  métricas de calidad y falla el mínimo de 25 trades.

**Fase 3:** ninguna variante cumple el criterio de promesa. `u1` es la única
con expectativa > 0 y MFE mediana > 0,4 R, así que se corrió una iteración con
objetivo 1,5 R (`u1_obj15`) y otra con stop de tiempo 45 min (`u1_st45`):

| variante | trades | acierto | expectativa | FB | salidas stop / tiempo / objetivo / breakeven / cierre | MFE med. | MAE med. |
|---|---:|---:|---:|---:|---|---:|---:|
| u1 | 9 | 33,3 % | +0,24 R | 2,18 | 1 / 4 / 2 / 1 / 1 | +0,56 R | +0,28 R |
| u1_obj15 | 9 | 44,4 % | +0,41 R | 3,04 | 1 / 4 / 4 / 0 / 0 | +0,56 R | +0,28 R |
| u1_st45 | 9 | 33,3 % | +0,22 R | 1,92 | 1 / 4 / 2 / 1 / 1 | +0,56 R | +0,30 R |

El objetivo a 1,5 R convierte en objetivo dos trades que con 2 R salían por
breakeven y por cierre (4 objetivos en vez de 2), y por eso sube la
expectativa a +0,41 R. El stop de tiempo a 45 min no cambia ninguna salida
(las mismas 4 por tiempo) y solo mueve el resultado −0,02 R.

Ninguna de las dos cambia el número de trades (las 9 entradas son las mismas:
55 señales $2–20, 46 descartadas por stop), así que no pueden llegar a 25 y el
criterio no se cumple por construcción. Con 9 trades, dos de ellos por
objetivo con +1,88 R cada uno, la expectativa de +0,24 R depende de dos
operaciones: no es evidencia de nada.

**Fase 4:** no se abre. No hay YAML v2 nuevo, no se toca `momentum_hunter/` ni
`momentum_paper_trader/`, no se arma el enriquecedor de fuentes ni el veto en
tres variantes (eso sigue condicionado a una decisión del dueño, no a este
resultado).

**Fase 5:** este informe.

---

## 4. Planes B

Los tres son exploratorios, solo lectura, y no tienen nada que ver con la v2
salvo compartir datos y caché. Ninguno toca producción.

### 4a. PEAD con 8-K 2.02 (`pead`) — no medible en Actions

Evento = 8-K con ítem 2.02 aceptado en EDGAR entre el cierre previo y la
apertura, gap ≥ 4 % (subasta oficial), RVOL diario ≥ 3, precio $2–20, entrada
al cierre del día 0, salida al cierre del día 3 o stop 6 % sobre mínimos
diarios. **Resultado: 0 trades porque sec.gov respondió 403 a los 1.943
pedidos** hechos desde los runners de GitHub Actions (con User-Agent con
contacto, como pide la SEC). Los 4.520 símbolo-días que pasaron gap, RVOL y
precio quedaron como «sin dato EDGAR», nunca como «sin 8-K». No hay número
que reportar; el código y la variante quedan listos para correr desde el VPS,
donde el hunter ya habla con sec.gov sin 403.

**Actualización 2026-09-30: medido desde el VPS.** Variante `pead_u1`
(universo solo $2–20), 3 años (2023-09-26 a 2026-09-25), sin IA, 0 fallos de
EDGAR. **No aprueba:** 1.804 trades, acierto 41,3 %, expectativa +0,10 R,
factor de beneficio 1,20, MFE mediana +0,86 R. Por año se degrada sin pausa:
2023 +0,28 R (FB 1,68) · 2024 +0,22 R (1,49) · 2025 +0,08 R (1,16) ·
2026 −0,05 R (0,92). Las 994 salidas al cierre del día 3 dan +1,06 R; las
737 por stop −1,02 R y las 73 con apertura bajo el stop −1,52 R. Informe
completo en `estrategia_v2_2023-09-26_2026-09-25_pead_u1.md` y métricas en
`metricas/metricas_pead_u1.json`. Ojo: es un periodo tres veces más largo
que el del resto de este informe, no la misma ventana de un año.

### 4b. PEAD con titular de resultados (`peadnoticia`) — proxy

Mismo plan, con el evento tomado de Benzinga: un titular entre el cierre
previo y la apertura que contenga palabras de resultados (earnings, quarter,
results, EPS, revenue, guidance…), sin IA. Es más ruidoso que el 8-K (una
noticia que comenta los resultados de otro, o una previa, también cuenta) y
puede perder empresas que Benzinga no cubre.

| métrica | valor |
|---|---:|
| símbolo-días con gap ≥ 4 %, RVOL ≥ 3 y $2–20 | 4.520 |
| con titular de resultados en la ventana | 27 (4.493 sin titular) |
| trades | 27 (todos $2–20: 15 en $2–10, 11 en $10–20) |
| acierto | 44,4 % |
| expectativa | +0,22 R (R = 6 %) |
| factor de beneficio | 1,36 |
| ganancia / pérdida media | +1,84 R / −1,08 R |
| salidas | 11 por stop (−1,28 R de media: el gap nocturno abre por debajo del stop), 16 al cierre del día 3 (75 % positivas, +1,25 R) |
| MFE / MAE mediana | +1,43 R / +0,82 R |
| por precio | $2–10: 15 trades, +0,41 R, FB 1,87; $10–20: 11 trades, +0,07 R, FB 1,10 |
| P&L simulado | +$144 con 0,5 % del equity por trade |

Es lo único de todo el encargo que pasa los cuatro números del criterio de
promesa (expectativa > 0,2 R, FB > 1,3, MFE mediana > 0,5 R, ≥ 25 trades), y
hay que decir en la misma frase por qué no se abre la fase 4 con ello:

- Pasa por muy poco (+0,22 R contra 0,2; FB 1,36 contra 1,3; 27 trades contra
  25). Un trade de los 27 cambia el veredicto.
- El evento es un proxy. El plan B pedía el 8-K 2.02; el titular de Benzinga
  mete previas, resúmenes de terceros y resultados de otra empresa, y deja
  fuera a quien Benzinga no cubre. No sabemos cuántos de los 27 tienen 8-K.
- El stop del 6 % sobre mínimos diarios lo pasa por encima el gap nocturno: la
  pérdida media por stop es −1,28 R, no −1 R. Es la forma más benigna del
  riesgo de mantener posiciones de un día para otro; la más maligna (un −30 %
  en la apertura) no aparece en 27 trades, pero existe.
- No hay drawdown calculado (tamaño fijo del 0,5 % del equity inicial, sin
  capitalizar ni cartera), así que no se sabe qué hubiera pasado con varias
  posiciones abiertas a la vez.

**Advertencia que no se negocia:** el PEAD mantiene posiciones de un día para
otro. Rompe la regla de cierre diario de la v1 y necesitaría una gestión de
riesgo nueva (gap nocturno, tamaño, halts fuera de sesión, órdenes GTC). Aquí
solo se mide; llevarlo a paper es una decisión aparte.

### 4c. Seguimiento de nivel 1 al día siguiente (`seguimiento`)

Día 1 = gap ≥ 4 % sin acción corporativa con una noticia nivel 1 alcista (IA,
misma caché); día 2 = compra en la ruptura del máximo del día 1 (velas de 1
min, 09:31–15:30), stop 4 %, cierre a las 15:50. Sin RVOL, VWAP, SPY, spread
ni float: la versión más simple de la idea.

| métrica | valor |
|---|---:|
| trades | 186 |
| acierto | 31,2 % |
| expectativa | −0,16 R |
| factor de beneficio | 0,71 |
| ganancia / pérdida media | +1,32 R / −0,83 R |
| salidas | 87 por stop (todas −1,04 R), 99 al cierre (58,6 % positivas, +0,61 R) |
| MFE / MAE mediana | +0,53 R / +0,95 R |
| por precio | $2–10: 69 trades, −0,04 R, FB 0,94; $10–20: −0,24 R; $20–50: −0,22 R |
| por hora de entrada | 09:36–09:59: 35, −0,22 R; 10:00–10:29: 20, +0,07 R; 10:30–11:00: 7, −0,69 R (el resto entra después de las 11:00) |

**Muestra parcial, hay que decirlo:** de los 8.833 símbolo-días con noticia,
solo 677 tienen nivel 1 evaluado. La corrida topó con 2.070 errores 400
(`BadRequestError`) de `api.anthropic.com`, compatible con créditos agotados
de la API, más el tope de 3.000 llamadas por corrida (15.508 símbolo-días
quedaron sin clasificar por el tope). Los 186 trades son los que la caché
existente permitía; no es una muestra aleatoria del año (son los símbolo-días
que la v2 ya había clasificado, o sea los que además pasaban RVOL, VWAP y
ruptura del ORB el día 1). Con esa salvedad: pierde en los tres rangos de
precio, el stop del 4 % se lleva 87 de 186 trades enteros y el MAE mediano
(+0,95 R) casi toca el stop. La idea, tal como está, no funciona; una versión
con stop más ancho o entrada más tarde es otra hipótesis, no una calibración.

### Recomendación honesta sobre los planes B

- El seguimiento de nivel 1, como está definido, no.
- El PEAD no se pudo medir con el evento correcto (8-K). El proxy con titular
  da +0,22 R / FB 1,36 / 27 trades: justo sobre el criterio, con un evento
  que no es el pedido. Es la única pista con números a favor en todo el
  encargo y merece un paso más, que no es operarla: correr la variante `pead`
  (8-K 2.02) desde el VPS, donde sec.gov no da 403, y ver si con el evento
  real se sostiene. Si se sostiene, la decisión de aceptar posiciones
  nocturnas es del dueño y viene antes que cualquier código.
  **Resultado (2026-09-30, §4a):** con el 8-K real y 3 años no se sostiene:
  +0,10 R / FB 1,20 sobre 1.804 trades, y el último año ya es negativo
  (−0,05 R). El +0,22 R del proxy de titulares no se confirma con el evento
  correcto.
- El rango $2–10 es donde ambos planes B y la v2 se ven menos mal ($2–10:
  peadnoticia +0,41 R, u1 +0,45 R, seguimiento −0,04 R). Con estas muestras
  es una coincidencia hasta que se demuestre lo contrario.

---

## 5. Limitaciones (sin maquillar)

- **Muestra.** 71 señales y 17 trades en la base; 9 en `u1`. Todo lo que se
  compara aquí está dentro del ruido: un trade cambia la expectativa de la base
  en ±0,1 R. La lectura sólida es la del embudo y del diagnóstico (n = 71 y n =
  1.299 rupturas), no la de las expectativas.
- **El backtest es un techo.** Universo de listados de hoy (sesgo de
  supervivencia), float / ETF / SPAC actuales y no los de cada fecha, sin
  halts históricos (el filtro «sin halt en 30 min» no se aplicó), sin
  rechazos ni impacto de mercado, y `e2` se llena exactamente en el límite sin
  slippage de entrada.
- **Slippage simulado** 0,15 % por lado, fijo. En small caps de $2–10 con
  spread de hasta 0,3 % (0,6 % en v2) puede quedarse corto.
- **La IA no es reproducible al 100 %.** Las clasificaciones vienen de una
  caché; con otra corrida sin caché el nivel de algunas noticias cambiaría.
  Tras el arreglo de lectura quedan < 1 % de respuestas ilegibles, pero el
  criterio del modelo no se mide aquí (`auditoria_ia.jsonl.gz` tiene todas
  las respuestas para revisarlo).
- **Créditos / tope de la API.** El seguimiento quedó a medias por 2.070
  errores 400 y el tope de 3.000 llamadas; no habrá más clasificaciones nuevas
  hasta que eso se resuelva.
- **EDGAR desde Actions.** 403 sistemático; el PEAD con 8-K solo se puede
  correr desde el VPS.
- **Todo es paper y solo lectura.** Nada de esto cambió el YAML de producción,
  el hunter ni el ejecutor.

---

## 6. Qué queda

- PR #221 sigue abierto y **no se fusiona** sin el OK del dueño. Ahora mismo
  tiene conflictos con `main` en 9 archivos (`main` recibió por otro camino
  #197, #202, #206 y #209, que tocan `momentum_hunter/run.py`, los workflows
  del hunter, `config/estrategia_v2.yaml` y tres archivos de pruebas que
  existen en ambos lados). Resolverlo es una decisión de qué versión manda en
  cada archivo, no un rebase mecánico; queda para cuando el dueño diga qué
  hacer con el PR.
- Los PRs de fuentes (#210 a #220) siguen apilados y sin fusionar; el paso 3
  (enriquecedor + veto en tres variantes) seguía condicionado a fusionar el
  backtest, y con la fase 5 no tiene base sobre la que enriquecer.
- Si se quiere una hipótesis nueva para la v2, la que más apoyo tiene en el
  diagnóstico es cambiar de dónde sale el stop (el mínimo del ORB no cabe en
  el 4 % en 54 de 71 señales) sin cambiar la entrada; `v1` ya probó una
  versión (mínimo de la vela de ruptura) y salió peor (−0,24 R), así que
  tampoco es gratis.
