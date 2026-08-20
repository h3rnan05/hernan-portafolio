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
2. Busca entradas en estado `TRIGGERED` que todavía no generaron una
   orden (dedup persistido en `ordenes.json`, por `ticker` + `creado_en`
   -- la misma entrada nunca genera dos órdenes, pero el mismo ticker
   disparando en días distintos sí genera órdenes independientes).
3. Calcula el tamaño de la posición por **riesgo fijo en dólares**
   (`PaperTraderConfig.riesgo_dolares_por_operacion`, default $100):
   `acciones = riesgo ÷ (entrada − stop)`, redondeado hacia abajo. Nunca
   arriesga más de lo configurado; si el riesgo no alcanza para 1 acción
   entera, omite la orden en vez de redondear hacia arriba.
4. Coloca una **orden bracket** (Alpaca maneja el stop-loss y el
   take-profit como OCO automáticamente, sin que este sistema tenga que
   vigilar la posición después) usando exactamente los tres números que
   `momentum_hunter` ya calculó y cacheó
   (`EntradaWatchlist.ultima_entrada/ultimo_stop/ultimo_objetivo`) --
   nunca un precio nuevo, nunca un cálculo propio.
5. Manda una confirmación por Telegram (mismo bot/chat de
   `momentum_hunter`, `enviar_telegram` reusado sin duplicar) y persiste
   la orden en `ordenes.json`.

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
