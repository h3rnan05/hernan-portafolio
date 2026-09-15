# `infra/systemd/` — unidades del VPS paper

Copia versionada de lo que corre en `momentum-paper` (Oracle Free ARM,
Ubuntu 24.04). Hasta el 2026-09-15 esto vivía **solo** en el VPS: cuatro
unidades, dos drop-ins y un wrapper que ningún repositorio conocía. Una
ruta rota en un `ExecStart` fallaba en el journal del VPS y en ningún
otro lado.

Desde ahora la fuente de verdad es este directorio. El VPS se alinea
copiando; nunca al revés. `momentum_paper_trader/tests/test_systemd_units.py`
verifica en CI que todo path bajo `/opt/` en una línea `Exec*=` exista en
el repo y sea ejecutable.

**PAPER ONLY.** Nada de acá toca umbrales, credenciales ni el endpoint de
Alpaca.

## Qué hay y dónde va

| En el repo | En el VPS | Qué es |
|---|---|---|
| `momentum-watchlist.service` | `/etc/systemd/system/` | oneshot: `--solo-watchlist` + paper trader + persist |
| `momentum-watchlist.service.d/10-restart-notify.conf` | `/etc/systemd/system/momentum-watchlist.service.d/` | `OnFailure` apagado (anti-spam, decisión del 2026-09-11) |
| `momentum-watchlist.service.d/20-ownership-wrapper.conf` | ídem | reemplaza el `ExecStart` por el wrapper de abajo |
| `momentum-watchlist.timer` | `/etc/systemd/system/` | cada 5 min, Lun–Vie 13–20 UTC |
| `momentum-watchlist-watchdog.service` | `/etc/systemd/system/` | avisa por Telegram si el oneshot lleva >1200 s sin terminar OK dentro de sesión |
| `momentum-watchlist-watchdog.timer` | `/etc/systemd/system/` | cada 10 min, Lun–Vie 13–20 UTC |
| `bin/run_watchlist_paper.sh` | `/opt/momentum/bin/` | el wrapper (ver abajo por qué vive fuera del árbol) |
| `bin/watchdog_timer_miss.sh` | `/opt/momentum/bin/` | copia byte a byte de `scripts/watchdog_timer_miss.sh` (ver "Conocido") |

**No versionado a propósito:** `/etc/momentum/paper.env` (credenciales;
viven en el VPS y en GitHub Secrets, nunca en el repo).

## Por qué el wrapper vive en `/opt/momentum/bin/` y no en `scripts/`

Reparto de ownership entre los dos escritores de estado (decisión del
2026-09-11, PR #117): GHA commitea `watchlist.json`, `auditoria/` y la
telemetría del hunter; el VPS commitea `revisiones.json`,
`archivo_triggered.jsonl` y la telemetría paper. Dos escritores sobre los
mismos archivos reventaban el rebase.

El wrapper es `scripts/run_watchlist_paper.sh` **sin** `momentum_hunter/
alertas_enviadas.json` ni `momentum_hunter/telemetria` en la lista de
persistencia. Vive fuera del árbol para que el `git pull --rebase` que él
mismo hace al arrancar no lo pueda pisar con la versión del repo.

Consecuencia: `scripts/run_watchlist_paper.sh` es código muerto en el VPS
(el drop-in `20-ownership-wrapper.conf` lo reemplaza), pero sigue siendo
el `ExecStart` de la unidad base. Conservarlo o unificar es una decisión
aparte; el validador acepta ambos porque ambos existen.

## Instalación / actualización en el VPS

Desde `/opt/hernan-portafolio` con `main` al día:

```bash
sudo cp infra/systemd/*.service infra/systemd/*.timer /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/momentum-watchlist.service.d
sudo cp infra/systemd/momentum-watchlist.service.d/*.conf \
        /etc/systemd/system/momentum-watchlist.service.d/
sudo install -m 755 infra/systemd/bin/* /opt/momentum/bin/
sudo systemctl daemon-reload
sudo systemctl enable --now momentum-watchlist.timer momentum-watchlist-watchdog.timer
systemctl list-units 'momentum*' --all
```

Deben aparecer exactamente cuatro unidades: dos `.service` (`inactive
dead` entre corridas, es un oneshot) y dos `.timer` (`active waiting`).

## Conocido (2026-09-15): la ruta de notificación del watchdog

`watchdog_timer_miss.sh` deriva la ruta de `notify_telegram.sh` de su
propia ubicación (`ROOT=$SCRIPT_DIR/..`). Ejecutado desde
`/opt/momentum/bin/` eso resuelve a `/opt/momentum/scripts/notify_telegram.sh`,
que no es un archivo del repo. Por eso las alertas del watchdog no
llegaban a Telegram.

Hoy está **mitigado, no arreglado**: en el VPS existe un symlink
`/opt/momentum/scripts/notify_telegram.sh -> /opt/hernan-portafolio/scripts/notify_telegram.sh`
creado a mano el 2026-09-14 17:33. Funciona, no está versionado, y nada
lo verifica. El arreglo va en un PR aparte.
