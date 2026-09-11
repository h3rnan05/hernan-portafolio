# Contexto del proyecto

Este repo contiene varios proyectos. El que está en desarrollo activo es el
**bot de trading automático**: `momentum_hunter/` + `momentum_paper_trader/`.

Los README de esos dos módulos son la referencia detallada (~1.050 líneas entre
los dos). Este archivo es la orientación: lo que hay que saber ANTES de tocar
nada, y dónde está el resto.

`docs/HISTORIAL-DEL-BOT.md` cuenta la otra mitad: los trece bugs que costaron
semanas y qué se decidió a cambio. Vale la pena leerlo antes de proponer
cambios grandes -- varios de esos errores no son obvios y se repetirían.

---

## Reglas que no se negocian

Estas no son preferencias de estilo. Cada una existe porque su violación
costaría dinero o rompería una separación deliberada.

1. **100 % paper trading.** El endpoint de Alpaca está hardcodeado a
   `https://paper-api.alpaca.markets/v2` a propósito: no es configurable por
   variable de entorno ni por argumento, para que ningún error de configuración
   pueda apuntarlo a una cuenta real. Pasar a dinero real requiere una decisión
   explícita y separada del usuario, nunca una inferencia.

2. **`momentum_hunter/` no puede incorporar IA, brókers ni ejecución.** Nunca.
   Hay una prueba (`test_ningun_modulo_de_esta_fase_importa_nada_de_ejecucion_de_ordenes`)
   que falla si alguien lo intenta — incluso si la palabra aparece en un
   docstring. Si esa prueba falla, la solución es reformular el texto, jamás
   debilitar la prueba.

3. **Sin margen.** Se opera contra el efectivo real (`cash`), jamás contra
   `buying_power`. El margen 4x de Alpaca no es capital nuestro.

4. **Los límites de riesgo son deterministas.** Nunca los decide el LLM. La IA
   solo puede REDUCIR el tamaño, jamás aumentarlo, y no tiene ningún campo donde
   escribir un precio.

5. **Fail-closed en todo.** Sin credenciales, sin respuesta, con el mercado
   cerrado o con un dato ilegible: no se opera. Ante la duda, nunca colocar.

6. **Nunca inventar un dato que falta, en ninguna de las dos direcciones.** Un
   campo ausente no es evidencia de nada. (Este proyecto ya perdió semanas por
   un `float(v or 0)` que convertía un volumen faltante en un cero real.)

7. **Las credenciales viven en GitHub Secrets.** No se hardcodean, no se
   escriben en logs, y no se piden por chat. Los mensajes de error registran el
   TIPO de excepción y su origen, nunca el texto completo (puede traer una URL
   con credenciales).

---

## Arquitectura en dos frases

`momentum_hunter/` busca oportunidades con reglas deterministas y escribe en
`watchlist.json`. `momentum_paper_trader/` lee ese archivo (solo lee, nunca
escribe), consulta a un LLM y coloca órdenes en Alpaca.

Esa frontera unidireccional es la que impide que el ejecutor invente
oportunidades y que el buscador coloque órdenes.

**Máquina de estados:** `WATCHING` (único activo) → `TRIGGERED` / `INVALIDATED`
/ `MISSED` / `EXPIRED` (los cuatro terminales, se purgan a los 7 días).

**Mapa visual:** https://claude.ai/code/artifact/694cf3d4-f768-4465-a938-9504645736b4

---

## Estado a septiembre de 2026

**Una operación cerrada.** NTLA, 8 de septiembre: 58 acciones a $12,88, salió
por stop a $12,77, −$6,38, duró 24 minutos. El pipeline completo funciona de
punta a punta; lo que falta no es que ejecute, es que ejecute bien.

El embudo produce ~0,33 señales accionables por corrida y está sano. 653 pruebas
en verde.

### Los dos problemas abiertos

**A. El bot llega tarde.** Las cuatro condiciones para disparar (patrón, dinero
entrando, riesgo definido, a tiempo) coinciden durante minutos. GitHub Actions
no respeta los horarios: pide cada 5 min y corre cada 30-90, con días en que no
corre. Ejemplos medidos, todas oportunidades reales rechazadas con razón: GAP
362 velas tarde, S 118, RPC 91. La solución probable es un servidor propio; es
decisión del usuario y está pendiente.

**B. El stop sale más apretado que el ruido de la acción.** Los niveles se
calculan desde el ancla intradía MÁS CERCANA (`max(anclas) * 0.995`). Pero el
sistema exige que la señal sea "temprana", o sea que el precio acabe de cruzar
sus anclas — con lo cual el ancla está pegada al precio y el stop sale pegado
también. Las dos reglas se pelean. En NTLA: stop de 10 centavos sobre una acción
que se mueve 73 al día. El ATR solo se usa como respaldo, nunca como piso
mínimo. Diagnosticado, no arreglado: el arreglo aleja el objetivo de 2R y el
usuario todavía no decidió.

### Cuidado al calibrar

Casi todos los umbrales están elegidos por razonamiento, no por evidencia, y hay
UNA sola operación de historial. Ajustar números sobre n=1 es adivinar con más
pasos. Lo del stop (problema B) es distinto: ahí hay una contradicción lógica en
el diseño, y eso sí se puede corregir sin esperar datos.

Nota honesta que conviene no olvidar: en una cuenta de $5.000 el tope de
concentración (15 %) MANDA sobre el riesgo por operación ($100). El riesgo real
ronda los $15-35. Ese $100 es un techo, no una meta.

---

## Cómo trabajar aquí

- **Pruebas:** `python -m pytest momentum_hunter/tests momentum_paper_trader/tests -q`.
  Deben pasar todas antes de commitear. Los tests de `backend/` y
  `telegram_bot/` necesitan dependencias que no siempre están instaladas en
  local; CI sí las instala.
- **Flujo:** rama desde `origin/main` → implementar → pruebas → PR → esperar CI
  (`backend` y `frontend` en verde) → squash merge.
- **No crear PRs sin que el usuario lo pida.**
- **Comentarios en español**, explicando el POR QUÉ y no el QUÉ. Las
  limitaciones conocidas se documentan sin maquillar — este repo prefiere una
  limitación anotada honestamente a una promesa que no se cumple.
- **Los workflows committean solos** (`[skip ci]`) los archivos de estado:
  `watchlist.json`, `revisiones.json`, `auditoria/`, `telemetria/`. Antes de
  trabajar conviene `git fetch origin main && git reset --hard origin/main`.

## Datos operativos útiles

- La regla PDT ya no existe: FINRA la retiró y Alpaca implementó el marco de
  margen intradía el 4 de junio de 2026. Ya no hay mínimo de $25.000 ni límite
  de 3 day trades. Con $5.000 la estrategia es viable.
- Las fraccionarias de Alpaca NO se combinan con órdenes bracket.
- Precios a Alpaca: 2 decimales si ≥ $1, 4 si < $1, siempre como string.
- Un `POST` con 200 no significa llenado (`accepted` / `pending_new`).
- El `time_in_force` aplica a toda la orden bracket: en GTC la orden de ENTRADA
  también sobreviviría la noche.
- Los datos de mercado salen de Yahoo, no de Alpaca (el plan gratis de Alpaca da
  IEX, ~2,5 % del volumen, inservible para small caps).
