# Momentum Paper Trader

Ejecución automática de las señales de `momentum_hunter/` contra una
cuenta de **PRÁCTICA** (paper trading) de [Alpaca](https://alpaca.markets)
-- pedido explícito (2026-08-12): "quiero probar los picks... sin tener
que estar metiendo los trades manualmente".

## Por qué es un proyecto separado

`momentum_hunter/` fue diseñado, desde el primer día, con una regla
inquebrantable repetida en cada fase: **RESEARCH + SIGNAL + ALERT,
NUNCA EXECUTION**. Este módulo no cambia esa regla -- la extiende con un
componente completamente distinto, que solo *lee* lo que
`momentum_hunter` ya decidió (las entradas TRIGGERED de
`watchlist.json`, con sus niveles ya calculados) y las ejecuta contra
una cuenta de práctica. `momentum_hunter` no sabe que este módulo
existe: no lo importa, no depende de él, y sigue funcionando exactamente
igual si este módulo se desinstala.

## Por qué es 100% paper, sin excepción

- El endpoint de Alpaca está **hardcodeado** en `alpaca_client.py`
  (`https://paper-api.alpaca.markets`) -- no es una variable de entorno
  ni un parámetro de configuración. No hay ninguna combinación de
  secrets o flags que lo apunte a la cuenta real
  (`https://api.alpaca.markets`).
- Las credenciales se llaman `ALPACA_PAPER_API_KEY`/`_SECRET` a
  propósito (no `ALPACA_API_KEY` genérico) -- para que quede explícito
  en cada lugar donde se configuran (GitHub Actions secrets) que son
  las de la cuenta de práctica.
- Cada mensaje de confirmación en Telegram arranca con `🧪 [PAPER]` y
  termina explícitamente: "Cuenta de práctica -- ningún dinero real se
  movió."

## Qué hace

1. Lee `momentum_hunter/watchlist.json` (mismo parseo tolerante a
   corrupción que ya usa `momentum_hunter.watchlist.cargar`, sin
   duplicar esa lógica).
2. Busca entradas en estado `TRIGGERED` que todavía no tienen una
   revisión registrada (dedup persistido en `revisiones.json`, por
   `ticker` + `creado_en` -- la misma entrada nunca se revisa dos veces,
   pero el mismo ticker disparando en días distintos sí se revisa por
   separado).
3. Le pide su criterio a la IA (`ia_decision.decidir`, ver la sección de
   abajo) sobre esa señal concreta. Si la IA dice que no, la revisión
   queda registrada igual (con el razonamiento) y ahí termina -- nunca
   se coloca una orden.
4. Si la IA aprueba, calcula el tamaño de la posición por **riesgo fijo
   en dólares** (`PaperTraderConfig.riesgo_dolares_por_operacion`,
   default $100): `acciones = riesgo ÷ (entrada − stop)`, redondeado
   hacia abajo. Nunca arriesga más de lo configurado; si el riesgo no
   alcanza para 1 acción entera, omite la orden en vez de redondear
   hacia arriba (este chequeo ocurre ANTES de consultar a la IA, para no
   gastar una llamada en una señal que de todas formas no se podría
   operar).
5. Coloca una **orden bracket** (Alpaca maneja el stop-loss y el
   take-profit como OCO automáticamente, sin que este sistema tenga que
   vigilar la posición después) usando exactamente los tres números que
   `momentum_hunter` ya calculó y cacheó
   (`EntradaWatchlist.ultima_entrada/ultimo_stop/ultimo_objetivo`) --
   nunca un precio nuevo, ni del código ni de la IA.
6. Manda una confirmación por Telegram (mismo bot/chat de
   `momentum_hunter`, `enviar_telegram` reusado sin duplicar), **con el
   razonamiento de la IA incluido** (pedido explícito del usuario: "cada
   que haga un trade, que me avise qué hizo"), y persiste la revisión en
   `revisiones.json`.

## La capa de decisión con IA (`ia_decision.py`)

Esto es una **reversión deliberada y explícita** del principio original
de `momentum_hunter` ("ninguna IA decide, solo genera texto" -- ver su
README). El usuario pidió puntualmente (2026-08-21) que el bot "actúe
autónomamente" con el criterio de un trader, no solo mecánicamente, y
confirmó por escrito, ante una pregunta directa, que: (a) el sistema
sigue siendo 100% paper trading, y (b) sí quiere que una IA (Claude) tome
la decisión de entrar o no. Esta sección documenta esa decisión y sus
guardarraíles, para que quede tan trazable como cualquier otra regla de
este repo.

**Por qué esto no reabre la puerta que `momentum_hunter` cerró:** el
límite original protegía la parte del sistema que decide qué es una
oportunidad real entre miles de candidatas ruidosas -- ahí, una
alucinación del modelo podría fabricar una señal de la nada. Acá la IA
nunca ve "miles de candidatas": solo entradas que YA pasaron el filtro
mecánico completo de `momentum_hunter` (catalizador confirmado, float,
volumen relativo, ruptura de nivel) Y el veto fatal del escéptico
(`momentum_hunter/skeptic.py`) -- por construcción, nunca por promesa del
prompt, porque este módulo solo lee entradas en estado `TRIGGERED`. Su
única pregunta es "¿esta señal concreta, con esta evidencia concreta,
vale la pena arriesgar el dinero (simulado)?", nunca "¿existe una señal
aquí?".

Guardarraíles, todos verificables en el código (no solo en el prompt):

- **Nunca inventa un precio.** `entrada`/`stop`/`objetivo` siempre son
  los que ya cacheó `EntradaWatchlist` -- la IA solo puede decidir
  SÍ/NO, nunca "a qué precio" (ver la forma de `DecisionIA`: no tiene
  ningún campo de precio).
- **Fail-closed en todos los caminos.** Sin `ANTHROPIC_API_KEY`,
  respuesta no-JSON, JSON con forma inesperada, confianza insuficiente,
  o cualquier excepción de red/API -> siempre `entrar=False`. Nunca
  "entrar de todos modos" ante una falla. Mismo principio que
  `telegram_bot/idea_evaluator.py`.
- **Cinturón y tirantes sobre el propio LLM.** La regla dura del prompt
  ("confianza >= 7 para entrar") se re-valida en código -- no se confía
  ciegamente en que el modelo la haya aplicado bien.
- **Auditoría completa.** Cada revisión -- apruebe o rechace -- queda
  registrada en `revisiones.json` con el razonamiento completo, así que
  toda decisión de la IA es reconstruible después.
- **Una sola oportunidad por señal.** Una vez revisada (con cualquier
  resultado), una entrada TRIGGERED no se le vuelve a preguntar a la IA
  -- evita tanto gastar llamadas de más como darle al modelo múltiples
  tiradas de dado sobre la misma señal hasta que diga que sí por azar.

## Uso

```bash
python -m momentum_paper_trader.run              # coloca órdenes paper reales (cuenta de práctica)
python -m momentum_paper_trader.run --dry-run     # calcula y muestra, no coloca nada ni requiere credenciales
```

Corre automáticamente al final de `momentum_hunter.yml` y
`momentum_hunter_watchlist.yml` (mismo job, después de que la watchlist
ya se actualizó) -- ver esos workflows.

## Variables de entorno

- `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_API_SECRET` -- credenciales del
  entorno **paper** de Alpaca (Dashboard → Paper Trading → API Keys, NO
  las de la cuenta live). Sin ellas, el comando no hace nada (mismo
  principio que `momentum_hunter.run.enviar_telegram`: falta de
  secrets nunca es un error fatal).
- `ANTHROPIC_API_KEY` -- para la capa de decisión con IA
  (`ia_decision.py`). Mismo secret que ya usan `news_analyst.yml` y
  `wizards_bot.yml` en este repo, reutilizado acá. Sin ella, no se opera
  (fail-closed, ver arriba).
- `MOMENTUM_TELEGRAM_BOT_TOKEN`/`_CHAT_ID` (o su fallback) -- las mismas
  que ya usa `momentum_hunter` para las confirmaciones.

## Seguridad

- Read-only sobre `momentum_hunter/`: nunca escribe `watchlist.json`,
  nunca modifica una `EntradaWatchlist`, nunca re-evalúa una señal.
- Nunca coloca una orden fuera del entorno paper (ver arriba).
- Un fallo al colocar una orden para un ticker (símbolo no soportado,
  Alpaca caído, etc.) se loguea y se omite -- nunca tumba el resto de
  la corrida ni queda una orden a medias sin registrar.
- Sin capacidad de vender, cerrar, ni modificar una posición existente
  -- solo coloca la orden bracket inicial; las salidas (stop/objetivo)
  las resuelve Alpaca del lado del broker, no un loop de este código
  vigilando precios.

## Qué requeriría (y con cuánto escrutinio) ir a una cuenta real algún día

Esto NO está implementado, y no se va a implementar sin una decisión
explícita y separada -- pero para que quede documentado qué distancia
real hay:

1. Cambiar `_BASE_URL` en `alpaca_client.py` -- un cambio de una línea,
   deliberadamente aislado para que sea imposible de hacer "sin querer".
2. Sizing por riesgo fijo en dólares deja de ser suficiente -- con
   dinero real hace falta sizing como % del equity de la cuenta,
   límites de exposición total, y probablemente un circuit breaker
   (máximo de pérdida diaria que pausa el sistema).
3. Sin backtest/replay medido todavía (ver roadmap de
   `momentum_hunter/README.md`) -- no hay evidencia numérica de qué tan
   bien funciona la señal, más allá del razonamiento de diseño.
4. Revisión de qué pasa ante un fallo parcial (orden colocada pero la
   confirmación de Telegram falla, doble ejecución si el workflow
   corre dos veces muy seguido, etc.) con el mismo nivel de rigor que
   se le dio a la deduplicación de Telegram en `momentum_hunter`.
5. Aprobación humana explícita y documentada -- nunca un cambio
   silencioso.
