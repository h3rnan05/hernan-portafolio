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
```

`log_event` nunca lanza excepciones: si no puede escribir, el bot sigue igual.

Desde bash existe la misma entrada como CLI (siempre sale con 0):
`python -m dashboard.events persist_fallido motivo="git persist failed" intentos=5`.
`scripts/run_watchlist_paper.sh` la usa cuando el push a `main` agota los
reintentos o no consigue el `flock`; el panel pinta el evento en rojo (píldora
en la cabecera y etapa Rechequeo en "Revisar") y el script manda un Telegram,
como mucho uno por día, si `MOMENTUM_TELEGRAM_BOT_TOKEN`/`_CHAT_ID` (o
`TELEGRAM_*`) están en `paper.env`.

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
| `DASH_VELAS_PAUSA_SEG` | cuánto deja de pedir a Yahoo tras un 429 (todos los tickers) | `900` |

La tabla de watchlist muestra las entradas activas (`watching`, `triggered`) y las que cambiaron
de estado hoy. El estado sale del overlay del VPS cuando existe, porque el JSON de GitHub
solo tiene la vista de GHA (ver `docs/SPEC-vps-watchlist-state-file.md`).

La latencia es **ruptura → orden** en velas de 1 minuto: las velas que el hunter ya contaba
al disparar (`velas_desde_ruptura`) más las que pasaron desde el disparo. Es la misma medida
del presupuesto de 8 velas. Si una orden no trae las dos partes, no entra al gráfico.

### Velas del ticker en operación

Las velas se piden con la misma petición y se parsean con la misma función que el hunter
(`provider.parsear_chart_intradia`), así que coinciden con lo que vio el bot. Yahoo se comparte
con el bot desde la misma IP, y el panel no puede perjudicarlo: un ticker se pide como mucho
una vez por TTL, hay tope de tickers, y ante un 429 el panel deja de pedir velas durante
`DASH_VELAS_PAUSA_SEG` y muestra la copia vieja marcada como "caché vencida" con la hora
(o "Sin datos" si no hay copia). Las marcas (ruptura, entrada del fill, stop) salen de la
watchlist y de Alpaca: si falta una, no se dibuja y el pie dice "sin dato".

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

## 5. Abrirlo desde tu computadora

```bash
ssh -N -L 8787:127.0.0.1:8787 ubuntu@momentum-paper
```

Luego abre http://localhost:8787. El servidor escucha solo en 127.0.0.1, así que no
hace falta abrir ningún puerto en Oracle Cloud.
