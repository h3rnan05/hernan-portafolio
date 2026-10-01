# Panel del bot (escritorio)

Página estática de solo lectura. Se regenera cada minuto en el VPS y se abre por túnel SSH.
No envía órdenes, no usa POST y el endpoint de Alpaca está fijo en paper.

## Archivos

```
dashboard/__init__.py
dashboard/events.py            log_event(): una línea JSON por evento en logs/events.jsonl
dashboard/build_dashboard.py   lee las fuentes y escribe index.html
tests/test_dashboard.py        15 pruebas
deploy/*.service, *.timer      systemd
```

## 1. Registrar eventos en el bot

Sin estos eventos el panel muestra Alpaca y la watchlist, pero las etapas, la latencia,
las dudas y los bloqueos quedan en "—".

```python
from dashboard.events import log_event

log_event("rechequeo", n_tickers=len(watchlist))                        # al terminar cada corrida del VPS
log_event("deteccion", ticker=t)                                         # primera vez que el ejecutor ve la señal
log_event("decision", ticker=t, entra=False, motivo=respuesta_llm)       # cada respuesta del LLM
log_event("orden", ticker=t, lado="buy", estado="enviada", velas=n)      # "velas" es opcional
log_event("bloqueo_riesgo", ticker=t, limite="perdida_diaria", motivo=m) # cuando un límite corta
log_event("persist_fallido", motivo="git persist failed", intentos=5)   # el VPS no pudo subir su estado a main
log_event("ia_fallo_tecnico", codigo="credito", consecutivos=1,
          motivo="saldo Anthropic insuficiente")                      # la IA no pudo decidir; la señal sigue TRIGGERED
```

`log_event` nunca lanza excepciones: si no puede escribir, el bot sigue igual.

Desde bash existe la misma entrada como CLI (siempre sale con 0):
`python -m dashboard.events persist_fallido motivo="git persist failed" intentos=5`.
`scripts/run_watchlist_paper.sh` la usa cuando el push a `main` agota los
reintentos o no consigue el `flock`; el panel pinta el evento en rojo (píldora
en la cabecera y etapa Rechequeo en "Revisar") y el script manda un Telegram,
como mucho uno por día, si `MOMENTUM_TELEGRAM_BOT_TOKEN`/`_CHAT_ID` (o
`TELEGRAM_*`) están en `paper.env`.

`ia_fallo_tecnico` lo escribe el ejecutor (`momentum_paper_trader/aviso_fallo_ia.py`),
no el script de persist. Es el otro silencio: Anthropic rechaza por saldo
(HTTP 400) o la consulta falla varias corridas seguidas, la señal queda
TRIGGERED y no se coloca orden. Píldora roja propia (no pisa la de persist)
y Telegram aparte, también como mucho uno por día por clase. Sin
`MOMENTUM_AVISOS_DIR` la marca vive en `/var/lib/momentum/ia_fallo_tecnico.json`.

## 2. Variables

| Variable | Para qué | Por defecto |
|---|---|---|
| `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET` | llaves paper, las mismas del ejecutor (`APCA_*` sirve de respaldo) | obligatorias |
| `DASH_WATCHLIST` | watchlist canónica que escribe GHA | `momentum_hunter/watchlist.json` |
| `DASH_WATCHLIST_ESTADO` | overlay del VPS (`--solo-watchlist`); si no existe, manda el canónico | `MOMENTUM_WATCHLIST_STATE` o `/var/lib/momentum/watchlist_vps_state.json` |
| `DASH_EVENTOS` | ruta del log de eventos (`logs/` está en `.gitignore`) | `logs/events.jsonl` |
| `DASH_SALIDA` | carpeta donde se escribe index.html | `dashboard_site` |
| `DASH_PRESUPUESTO_VELAS` | línea roja del gráfico | `8` |
| `DASH_TZ` | zona horaria de las horas mostradas | `UTC` |
| `DASH_CACHE_VELAS` | caché de velas de 1 min (fuera de git; en el VPS `/var/lib/momentum/dashboard_cache`). Si falta, el temporal del sistema; si apunta dentro del repo, se ignora con aviso | temporal del sistema |
| `DASH_VELAS_TTL_SEG` | cuánto vale una copia de velas antes de volver a pedir | `120` |
| `DASH_VELAS_MAX_TICKERS` | tope de tickers graficados por corrida | `6` |
| `DASH_VELAS_PAUSA_SEG` | cuánto deja de pedir a Yahoo tras un 429 (todos los tickers). No frena el feed SIP | `900` |
| `ALPACA_DATA_FEED` | feed de `data.alpaca.markets` para las velas del gráfico (`sip` o `iex`). Lo lee `AlpacaProvider`, igual que el hunter | `sip` |
| `DASH_GHA_REPO` | repo público cuyo Actions se consulta para la última corrida OK del hunter (vacío = no preguntar) | `h3rnan05/hernan-portafolio` |
| `DASH_GHA_WORKFLOW` | archivo del workflow del hunter | `momentum_hunter.yml` |
| `DASH_GHA_TTL_SEG` | cuánto vale la respuesta de Actions antes de volver a preguntar | `300` |
| `DASH_TELEM_HUNTER` | carpeta de telemetría del hunter; el estado del Hunter sale del último escaneo `vps` de hoy | `momentum_hunter/telemetria` |
| `DASH_YAHOO_PAUSA_BOT` | archivo de pausa del BOT ante un 429 de Yahoo (solo lectura): el panel no le pide a Yahoo tampoco. No frena el feed SIP | (vacío) |
| `DASH_NOTICIAS_LEIDAS` | registro de auditoría del escaneo para `noticias.html` (solo lectura) | `momentum_hunter/noticias_leidas.json` en `MOMENTUM_ESTADO_DIR` |
| `DASH_REVISIONES` | `revisiones.json` del ejecutor, solo para el aviso de reconciliación | el del paquete |

La tabla de watchlist muestra las entradas activas (`watching`, `triggered`) y las que cambiaron
de estado hoy. El estado sale del overlay del VPS cuando existe, porque el JSON de GitHub
solo tiene la vista de GHA (ver `docs/SPEC-vps-watchlist-state-file.md`).

La latencia es **ruptura → orden** en velas de 1 minuto: las velas que el hunter ya contaba
al disparar (`velas_desde_ruptura`) más las que pasaron desde el disparo. Es la misma medida
del presupuesto de 8 velas. Si una orden no trae las dos partes, no entra al gráfico.

### Cuenta en el broker (desde 2026-09-28)

Lo que está abierto lo dice Alpaca, no `revisiones.json` ni la watchlist. El 28/9
el contador de posiciones marcaba 1 (MNST) y el gráfico seguía pintando DLB, NBIS
y TWST, cerradas a las 09:53, porque cualquier orden de hoy contaba como "en
operación" y la entrada/stop salían "sin dato".

Tres bloques, todos de GET:

- **Posiciones abiertas**: cantidad, entrada (`avg_entry_price`), precio actual,
  P&L abierto (`unrealized_pl`, y el porcentaje si vino `unrealized_plpc`), stop
  y objetivo. El stop de un bracket ya lleno no está en `status=open`: esa
  lista trae solo el take-profit (`new`) con `legs` vacío. El stop queda
  `held` bajo la compra ya `filled` (MNST, 28/9: padre `265e093f`, stop
  `f2d920f1` a $41.62, límite `bb5baab2` a $42.36). La columna y el aviso
  piden `ordenes_de_simbolos` (`status=all&nested=true&symbols=...`) y usan
  la regla de `reconciliacion.detectar`: venta `stop` / `stop_limit` /
  `trailing_stop` con status `held`, `new`, `accepted` o `pending_new`, en
  la fila o en una pata, o una venta a mercado en curso. Un límite de
  take-profit no es el stop. Si esas órdenes no se pueden leer, no se
  afirma que falte.
- **Órdenes pendientes**: compras de entrada que todavía no llenan (ACN, NTAP),
  con sus patas. No se mezclan con la posición.
- **Cerradas hoy**: ventas llenas de hoy de un símbolo que ya no está abierto,
  con el P&L realizado por FIFO contra los fills que alcanzó a ver (hasta 500
  órdenes cerradas, el tope de Alpaca). Si la entrada no está en ese historial,
  el P&L queda "—". No se rellena con cero ni con `revisiones.json`.

Un aviso rojo si el broker tiene una posición que ninguna revisión viva sigue,
o una sin stop de venta (ni venta a mercado en curso). Es `reconciliacion.detectar`
con el listado anidado de `ordenes_de_simbolos`: el detector mira la fila y
sus `legs`, y solo cuenta una pata viva. Si esas órdenes no se pudieron leer,
no se afirma que falte el stop. `DASH_REVISIONES` apunta al libro; por defecto es el
`revisiones.json` del paquete.

La píldora "Fuente de datos" sale de la telemetría de hoy del VPS, bloque
`datos` (`fuente`: `yahoo` / `alpaca` / `mixto`, y `feed`: `sip` o `iex`).
Es lo que contestó, no lo que estaba configurado: si `fuente` viene vacía
se usa la medición anterior del mismo día, y si ninguna corrida la trajo
no se escribe "Yahoo" por costumbre. `alpaca` + `sip` se muestra como
Alpaca SIP; `iex` no se disfraza de SIP; `mixto` avisa que hubo respaldo
Yahoo. `fuente: vps` en la raíz del JSONL es el escritor, no el feed.

### Velas de posiciones abiertas

El gráfico pide primero el feed de datos de Alpaca (`https://data.alpaca.markets`,
`AlpacaProvider.barras_intradia`, feed `ALPACA_DATA_FEED` o `sip`) y recorta a hoy
con `barras_de_hoy`. Menos de 5 velas de hoy no se dibuja: se cae a Yahoo, una sola
petición parseada con `provider.parsear_chart_intradia`. El subtítulo de cada ticker
dice `SIP` o `Yahoo (respaldo)`. La caché guarda esa etiqueta.

Yahoo se comparte con el bot desde la misma IP, y el panel no puede perjudicarlo: un
ticker se pide como mucho una vez por TTL, hay tope de tickers, y ante un 429 el panel
deja de pedirle a Yahoo durante `DASH_VELAS_PAUSA_SEG`. Esa pausa no bloquea el feed.
Si las dos fuentes fallan, se muestra la copia vieja marcada como "caché vencida" con
la hora (o "Sin datos" si no hay copia). Nunca una serie de ceros. Se grafican las
posiciones abiertas y, si cabe en el tope, las compras pendientes, con la marca
"pendiente". Una cerrada hoy no se grafica: va a la tabla. Las marcas (ruptura de la
watchlist, entrada del fill, stop) salen de la API paper de Alpaca y, la ruptura, de
la watchlist: si falta una, no se dibuja y el pie dice "sin dato".

## 3. Probar a mano

```bash
python -m pytest tests/test_dashboard.py -q
python -m dashboard.build_dashboard
```

## 4. Instalar en el VPS

Los servicios corren como `momentum`, en `/opt/hernan-portafolio` y con `/etc/momentum/paper.env`,
igual que `momentum-watchlist.service`. El HTML se escribe en `/var/lib/momentum/dashboard_site`,
fuera de git, para no ensuciar el árbol ni romper el `git pull --rebase` del wrapper.

```bash
sudo cp /opt/hernan-portafolio/deploy/momentum-dashboard.service \
        /opt/hernan-portafolio/deploy/momentum-dashboard.timer \
        /opt/hernan-portafolio/deploy/momentum-dashboard-http.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start momentum-dashboard.service      # primera generación, a mano
sudo systemctl status momentum-dashboard.service     # debe terminar sin error
sudo systemctl enable --now momentum-dashboard.timer momentum-dashboard-http.service
```

Para actualizar el panel cuando ya está instalado (el HTML se regenera en
`/var/lib/momentum/dashboard_site`; el servidor HTTP solo lo sirve):

```bash
cd /opt/hernan-portafolio && git pull --rebase
sudo systemctl start momentum-dashboard.service
```

`momentum-dashboard.service` es oneshot: `start` lo vuelve a correr. El timer
lo lanza solo cada minuto. No hace falta reiniciar `momentum-dashboard-http`.

## 5. Abrirlo desde tu computadora

```bash
ssh -N -L 8787:127.0.0.1:8787 ubuntu@momentum-paper
```

Luego abre http://localhost:8787. El servidor escucha solo en 127.0.0.1, así que no
hace falta abrir ningún puerto en Oracle Cloud.

### Tema claro / oscuro (2026-09-24)

El panel tiene un botón **Tema** en la cabecera que alterna claro y oscuro.
La elección se guarda en el navegador (`localStorage`, por dispositivo) y se
aplica antes de pintar, así el refresco automático cada 60 s no parpadea. Sin
elección guardada, sigue la preferencia del sistema
(`prefers-color-scheme`). Es 100 % del lado del navegador: no toca el bot ni
lo que se sube a git. Los colores se definen como variables CSS que se
redefinen para el oscuro; el resto del CSS ya las usa, por eso el claro queda
idéntico.

## Estado del Hunter: último escaneo del VPS (desde 2026-09-21)

El escaneo corre en el VPS. El estado "Hunter" sale del último registro de modo
`escaneo` en `momentum_hunter/telemetria/<hoy UTC>/vps/events.jsonl` (hora de
fin, slot, evaluadas). Sin registro de hoy: "Sin datos", aunque la watchlist o
GitHub sean frescos. La última corrida en GitHub Actions (abajo) se muestra al
lado como dato, sin decidir el estado: GitHub es solo respaldo.

## Última corrida en GitHub Actions (respaldo)

El estado "Hunter" no sale de la hora de la watchlist sino de la última corrida
**exitosa** de `momentum_hunter.yml` según la API pública de GitHub Actions
(`dashboard/gha.py`). Una corrida con 0 candidatos no cambia la watchlist ni
commitea nada, y antes el panel la daba por no ocurrida. La petición es un GET
sin token (el repo es público), se cachea `DASH_GHA_TTL_SEG` segundos en
`DASH_CACHE_VELAS` para no agotar el límite anónimo de 60 por hora, y ante un
403/429 el panel deja de preguntar 15 min y muestra la copia vieja marcada
"caché vencida". Si Actions no responde y no hay copia, el Hunter queda
"Sin datos" aunque la watchlist sea fresca: no se inventa una hora.

## Bloqueos de riesgo: únicos, capacidad llena y cuándo dice "Revisar" (2026-09-23)

El 23/9 la tarjeta Riesgo marcó "Revisar" con 942 bloqueos contra 7
decisiones. No era una falla: con 5 posiciones abiertas, el tope de
posiciones bloqueaba cada señal disparada en cada tick de 60 s, y el
panel contaba eventos crudos. Desde entonces:

- Los bloqueos se cuentan **únicos por (ticker, código)**, con veces y
  última hora; el número de eventos crudos se muestra al lado. El código
  viene en el evento (`codigo`, ver `momentum_paper_trader/bloqueos.py`);
  los eventos viejos que solo traen `limite` se mapean al catálogo.
- Un límite global lleno llega como UN evento `capacidad_llena` por
  corrida y se resume en una línea: código, desde, hasta y corridas.
- **"Revisar" solo** si hay un bloqueo `DATO_FALTANTE:<campo>` (dato nulo,
  faltante o viejo) o un código que el catálogo no conoce. Un límite
  conocido bloqueando, por muchas veces que se repita, es "OK".
- La tarjeta Hunter pasa a "Revisar", y se lista en "Datos incompletos",
  si GitHub Actions (`momentum_hunter.yml`, el respaldo) lleva más de
  `DASH_GHA_MAX_MIN` (45) minutos sin una corrida exitosa en sesión. Sin
  dato de Actions no se inventa alerta.

## Riesgo, velas y watchlist (2026-09-29)

- **Velas**: la ventana arranca 15 min antes de la apertura de Nueva York
  (`recortar_a_sesion`); si con eso quedan menos de 5 velas se deja la serie
  entera. Se dibuja el **objetivo** (límite de venta vivo del bracket) y el
  **VWAP** acumulado desde la apertura, calculado con las mismas velas del
  gráfico; si a una vela de la sesión le falta el volumen, el VWAP se corta
  ahí. Las etiquetas se reparten para no encimarse (`repartir_etiquetas`):
  las líneas quedan en su precio, solo el texto se mueve.
- **`held`** se muestra según dónde está la pata: en una posición llena es
  "activo (OCO)" (Alpaca lo vigila y protege); en una compra pendiente,
  "tras el fill".
- **Riesgo por fila**: (entrada − stop) × cantidad y objetivo/riesgo en R.
  En pendientes, el riesgo si llena al límite y cuánto lleva esperando
  contra `minutos_maximos_entrada_sin_llenar`. Un stop por encima de la
  entrada se muestra como ganancia asegurada.
- **Uso de límites** (panel Límites de riesgo): cupo de jugadas contado
  como `executor._leer_cuenta` (símbolos con posición ∪ con orden abierta)
  contra `maximo_posiciones_abiertas`; concentración por posición contra
  `maximo_pct_efectivo_por_posicion`; riesgo total al stop; invertido y
  efectivo. Los topes se leen de `momentum_paper_trader.config.CONFIG`,
  no se copian. Un dato que falta es "sin dato", nunca 0.
- **Watchlist**: activas arriba (disparadas primero); las terminales de hoy
  quedan plegadas. El título resume disparadas, vigilando y banda de cap.
- **Panel viejo**: el HTML lleva su hora de generación; si el navegador ve
  que tiene 3 min o más, aparece una píldora roja en la cabecera.

## Datos consistentes (2026-09-29)

- **Ruptura al decidir vs actual.** La ruptura de la watchlist se sigue
  refrescando después de la orden (en los patrones que no son gap_and_go ni
  opening_range_breakout es la EMA9 o el VWAP, y se mueve). El panel la
  mostraba como si fuera la de la compra: NVS, entrada $144.79 y "ruptura"
  $145.24. Ahora el evento `orden` trae `ruptura_al_decidir`,
  `patron_al_decidir`, `vwap_al_decidir` y `precio_entrada` (solo
  registro; el hunter guarda `ultimo_patron` / `ultimo_vwap` junto a la
  ruptura). La gráfica y el pie dicen "ruptura al decidir" y, aparte,
  "ruptura actual". Una orden sin esos campos dice "sin dato", nunca la
  actual en su lugar.
- **Velas.** Con el plan gratis, Alpaca responde 403 a las velas SIP de los
  últimos 15 min. El panel lo detecta, deja de pedir el feed por 6 h
  (`alpaca_feed_pausa.json` en `DASH_CACHE_VELAS`) y rotula las velas
  "Yahoo" (no "respaldo"), con un aviso en la sección. Otro fallo del feed
  sigue siendo "Yahoo (respaldo)" y el aviso trae su código.
- **Hunter.** La última corrida de GitHub va aparte, en gris, como
  "respaldo GitHub (histórico)". El estado lo decide el escaneo del VPS.
- **P&L.** La gráfica de hoy termina en un punto "en vivo" con el equity de
  la cuenta, el mismo número que la tarjeta P&L del día. Sin punto en vivo,
  "Último" dice de qué vela es.
- **Latencia.** Cada barra separa la reacción del bot de la espera por cupo
  lleno (gris), reconstruida con los eventos `MAXIMO_POSICIONES` entre la
  ruptura y la orden. Las cifras y "Llegaron tarde" cuentan solo la
  reacción. Las compras sin barra se listan con su motivo (el evento no
  trae las velas, o Alpaca tiene la compra y el log no). El eje va de 0 al
  doble del límite; una barra más alta se corta con su total escrito.

### Noticias leídas (`noticias.html`, desde 2026-09-30)

Página aparte, enlazada desde la cabecera ("Noticias leídas"). Muestra qué
titulares leyó el escaneo en su última corrida y por qué cada acción pasó o
no el filtro de catalizador. Es auditoría, no un feed, y **no es en vivo**:
cambia cuando termina un escaneo.

- La escribe el hunter (`momentum_hunter/noticias_leidas.py`) con los
  titulares que ya tenía en memoria: cero llamadas extra. Guarda las últimas
  3 corridas y hasta 25 titulares por acción.
- Filtros: **Casi pasan** (hubo keyword pero la frenó el ancla, la ventana
  de días o la regla de rumores) y **Sin keyword** (tenía noticias y ninguna
  coincidió: sirve para buscar keywords que faltan).
- El ejecutor no la lee; `watchlist.json` sigue siendo el único canal.
- Archivo ausente, vacío o corrupto: la página lo dice y el panel sigue igual.
- Limitación: si yfinance se traga un HTTP 500 (devuelve `[]`) y el RSS de
  respaldo también viene vacío, la acción aparece como "Sin noticias".
