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
```

`log_event` nunca lanza excepciones: si no puede escribir, el bot sigue igual.

## 2. Variables

| Variable | Para qué | Por defecto |
|---|---|---|
| `ALPACA_PAPER_API_KEY`, `ALPACA_PAPER_API_SECRET` | llaves paper, las mismas del ejecutor (`APCA_*` sirve de respaldo) | obligatorias |
| `DASH_WATCHLIST` | watchlist canónica que escribe GHA | `momentum_hunter/watchlist.json` |
| `DASH_WATCHLIST_ESTADO` | overlay del VPS (`--solo-watchlist`); si no existe, manda el canónico | `MOMENTUM_WATCHLIST_STATE` o `/var/lib/momentum/watchlist_vps_state.json` |
| `DASH_EVENTOS` | ruta del log de eventos (`logs/` está en `.gitignore`) | `logs/events.jsonl` |
| `DASH_SALIDA` | carpeta donde se escribe index.html | `dashboard_site` |
| `DASH_VELA_MIN` | minutos por vela, para calcular latencia | sin valor: no se calcula |
| `DASH_PRESUPUESTO_VELAS` | línea roja del gráfico | `8` |
| `DASH_TZ` | zona horaria de las horas mostradas | `UTC` |

La tabla de watchlist muestra las entradas activas (`watching`, `triggered`) y las que cambiaron
de estado hoy. El estado sale del overlay del VPS cuando existe, porque el JSON de GitHub
solo tiene la vista de GHA (ver `docs/SPEC-vps-watchlist-state-file.md`).

## 3. Probar a mano

```bash
python -m pytest tests/test_dashboard.py -q
DASH_VELA_MIN=5 python -m dashboard.build_dashboard
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
