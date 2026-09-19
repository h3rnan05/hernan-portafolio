# Panel del bot (escritorio)

Página estática de solo lectura. Se regenera cada minuto en el VPS y se abre por túnel SSH.
No envía órdenes, no usa POST y el endpoint de Alpaca está fijo en paper.

## Archivos

```
dashboard/__init__.py
dashboard/events.py            log_event(): una línea JSON por evento en logs/events.jsonl
dashboard/build_dashboard.py   lee las fuentes y escribe index.html
tests/test_dashboard.py        10 pruebas
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
| `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` | llaves de la cuenta paper | obligatorias |
| `DASH_WATCHLIST` | ruta de watchlist.json | `watchlist.json` |
| `DASH_EVENTOS` | ruta del log de eventos | `logs/events.jsonl` |
| `DASH_SALIDA` | carpeta donde se escribe index.html | `dashboard_site` |
| `DASH_VELA_MIN` | minutos por vela, para calcular latencia | sin valor: no se calcula |
| `DASH_PRESUPUESTO_VELAS` | línea roja del gráfico | `8` |
| `DASH_TZ` | zona horaria de las horas mostradas | `UTC` |

Si tus llaves tienen otro nombre en el `.env`, cámbialo en `alpaca_get()`.
Si tu watchlist.json tiene otra forma, ajusta `leer_watchlist()`; el panel avisa en rojo cuando no la reconoce.

## 3. Probar a mano

```bash
python -m pytest tests/test_dashboard.py -q
DASH_VELA_MIN=5 python -m dashboard.build_dashboard
```

## 4. Instalar en el VPS

```bash
sudo cp deploy/momentum-dashboard.service deploy/momentum-dashboard.timer deploy/momentum-dashboard-http.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now momentum-dashboard.timer momentum-dashboard-http.service
```

Revisa antes las rutas `WorkingDirectory` y `EnvironmentFile` en `momentum-dashboard.service`.

## 5. Abrirlo desde tu computadora

```bash
ssh -N -L 8787:127.0.0.1:8787 ubuntu@momentum-paper
```

Luego abre http://localhost:8787. El servidor escucha solo en 127.0.0.1, así que no
hace falta abrir ningún puerto en Oracle Cloud.
