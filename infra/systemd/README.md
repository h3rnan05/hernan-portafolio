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
| `momentum-watchlist-state-backup.service` | `/etc/systemd/system/` | oneshot: copia diaria del overlay (`scripts/backup_watchlist_vps_state.sh`, 02:15 America/Monterrey) |
| `momentum-watchlist-state-backup.timer` | `/etc/systemd/system/` | `*-*-* 02:15:00`; **no** se habilita junto al rechequeo |
| `momentum-watchlist-watchdog.service` | `/etc/systemd/system/` | avisa por Telegram si el oneshot lleva >1200 s sin terminar OK dentro de sesión; ejecuta `scripts/watchdog_timer_miss.sh` **desde el árbol** (ver "Watchdog") |
| `momentum-watchlist-watchdog.timer` | `/etc/systemd/system/` | cada 10 min, Lun–Vie 13–20 UTC |
| `bin/run_watchlist_paper.sh` | `/opt/momentum/bin/` | el wrapper (ver abajo por qué vive fuera del árbol). Un `git pull` NO lo actualiza: cada cambio en él exige volver a hacer `install` |
| `momentum-scan.service` | `/etc/systemd/system/` | oneshot: **escaneo completo** (`--limit 1000`, slot rotativo) + paper trader + persist (desde 2026-09-21; antes vivía en GitHub Actions) |
| `momentum-scan.timer` | `/etc/systemd/system/` | cada 30 min en :01 y :31, Lun–Vie 13–20 UTC |
| `bin/run_scan_paper.sh` | `/opt/momentum/bin/` | wrapper del escaneo: sin candado durante el escaneo; candado solo al escribir la watchlist y al commitear |
| `momentum-movers-sombra.service` / `.timer` | **NO se instala solo** | ejemplo: descubrimiento "movers" en sombra cada 5 min (:02, :07, …), solo telemetría (`momentum_hunter/movers.py`) |
| `bin/run_movers_sombra.sh` | `/opt/momentum/bin/` (solo si se instala la sombra) | wrapper: no-op salvo `MOMENTUM_MOVERS_SOMBRA=1`; flock solo contra sí mismo |

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
# NO arrancar momentum-watchlist.timer desde el PR del overlay.
# El timer de backup del state es independiente del rechequeo
# (`15 2 * * *` America/Monterrey, ver infra/cron/):
# sudo systemctl enable --now momentum-watchlist-state-backup.timer
sudo systemctl enable --now momentum-watchlist-watchdog.timer
systemctl list-units 'momentum*' --all
```

Deben aparecer las unidades de watchdog. El oneshot de rechequeo y su
timer siguen OFF hasta GO humano/Claude. El overlay VPS
(`/var/lib/momentum/watchlist_vps_state.json`) se documenta en
`docs/SPEC-vps-watchlist-state-file.md`.

## Watchdog: por qué corre desde `scripts/` y no desde `/opt/momentum/bin/`

`watchdog_timer_miss.sh` deriva la ruta de `notify_telegram.sh` de su
propia ubicación (`ROOT=$SCRIPT_DIR/..`). Hasta el 2026-09-15 la unidad
ejecutaba una **copia** en `/opt/momentum/bin/`, y desde ahí eso
resuelve a `/opt/momentum/scripts/notify_telegram.sh` — que no es un
archivo del repo. Por eso las alertas del watchdog nunca llegaron a
Telegram. El 2026-09-14 17:33 alguien lo mitigó a mano con un symlink
en esa ruta: funcionaba, no estaba versionado, nada lo verificaba.

El arreglo es no copiar: `ExecStart=/opt/hernan-portafolio/scripts/watchdog_timer_miss.sh`.
El watchdog solo lee el journal y llama a `notify` — no escribe en git —
así que no tiene la razón de ownership que sí tiene el wrapper para
vivir fuera del árbol. Ejecutado desde `scripts/`, `ROOT` resuelve al
repo y `NOTIFY` a `scripts/notify_telegram.sh` sin symlinks, y el
`git pull` del wrapper lo mantiene al día.

### Aplicar el arreglo en el VPS (una vez)

```bash
cd /opt/hernan-portafolio && git pull --rebase origin main
sudo cp infra/systemd/momentum-watchlist-watchdog.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo rm -f /opt/momentum/bin/watchdog_timer_miss.sh
sudo rm -f /opt/momentum/scripts/notify_telegram.sh && sudo rmdir /opt/momentum/scripts
systemctl cat momentum-watchlist-watchdog.service | grep ^ExecStart
```

### Verificar que una alerta llega DE VERDAD a Telegram

El script acepta `WATCHDOG_NOW_EPOCH` y `WATCHDOG_LAST_OK_EPOCH` por
entorno: se simula un silencio de 30 min dentro de sesión, sin esperar
uno real. `WATCHDOG_TIMER_ACTIVE=active` fuerza el chequeo de silencio:
post-#132 `momentum-watchlist.timer` está OFF a propósito, y sin el
override el script registra `INFO: timer inactive; skip` y no Telegram.
Como root (así corre la unidad), con `paper.env` cargado:

```bash
sudo bash -c 'set -a; . /etc/momentum/paper.env; set +a; \
  WATCHDOG_NOW_EPOCH=$(date -u -d "2026-09-15 15:00:00" +%s) \
  WATCHDOG_LAST_OK_EPOCH=$(date -u -d "2026-09-15 14:30:00" +%s) \
  WATCHDOG_TIMER_ACTIVE=active \
  /opt/hernan-portafolio/scripts/watchdog_timer_miss.sh; echo "rc=$?"'
```

Esperado: la línea `ERROR [paper][vps] ... silent 1800s`, luego
`TELEGRAM_NOTIFY OK`, `rc=1`, y el mensaje en el chat. Si sale
`TELEGRAM_NOTIFY FAIL: missing token or chat id`, `paper.env` no se
cargó; si sale `WARN: telegram notify failed`, la ruta sigue mal.

## Escaneo completo en el VPS (2026-09-21)

Desde hoy el escaneo del universo corre acá (`momentum-scan.timer`) y **el VPS
es el dueño** de `watchlist.json`, `auditoria/`, `alertas_enviadas.json` y la
telemetría del hunter: los commitea él, después de volcar el overlay del
rechequeo al canónico (`python -m momentum_hunter.run --materializar-overlay`).
GitHub conserva sus dos workflows solo como **respaldo**: actúan si el VPS lleva
más de 20 min sin commitear telemetría de hoy, ya pasaron 20 min desde las
13:00 UTC y todavía no son las 21:00 UTC, cuando la ventana del VPS ya cerró y
su silencio es normal (`.github/scripts/respaldo_gha.py`), y cada Telegram de ese modo
sale con `[RESPALDO GITHUB]`. El paso de paper en GitHub está apagado salvo
`MOMENTUM_PAPER_GHA=on` (variable de repo): `revisiones.json` tiene un solo
escritor, el VPS.

**Candados.** Ninguno durante el escaneo (~9 min): el rechequeo de 5 min nunca
espera ni se salta. El escaneo escribe el canónico con el overlay aplicado en el
instante de escribir (`watchlist.guardar_canonico_fusionado`, candado
`watchlist_vps_state.json.lock`, milisegundos); si los dos llegaron a un estado
terminal distinto, gana la decisión anterior. El commit/push usa el mismo flock
que el rechequeo.

**Yahoo.** Escaneo y panel salen de la misma IP. Ante un 429 el bot escribe
`/var/lib/momentum/yahoo_pausa_bot.json` y deja de pedir 15 min (sin
reintentos); el panel lo lee y se frena también (`DASH_YAHOO_PAUSA_BOT`). El bot
nunca obedece la pausa que escribe el panel (`dashboard_cache/yahoo_pausa.json`).

**Instalar** (además de lo de arriba):

```bash
sudo cp infra/systemd/bin/run_scan_paper.sh /opt/momentum/bin/ && sudo chmod +x /opt/momentum/bin/run_scan_paper.sh
sudo cp infra/systemd/momentum-scan.service infra/systemd/momentum-scan.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now momentum-scan.timer
```

**Vuelta atrás.** (1) `sudo systemctl disable --now momentum-scan.timer` y, en
GitHub, variable `MOMENTUM_PAPER_GHA=on`: se vuelve al estado anterior sin tocar
código (GitHub escanea y opera cuando su cron dispara; el VPS solo rechequea).
(2) `MOMENTUM_SCAN_VPS=0` en `paper.env` deja el wrapper del escaneo como no-op.
(3) Revertir el squash del PR.

Limitación anotada: si el push del VPS falla (conflicto con un respaldo de
GitHub), el commit local queda y el siguiente `git pull --rebase` fallará hasta
resolverlo a mano; #150 lo hace visible en el panel y por Telegram.

## "Movers" en sombra (2026-09-21): NO se instala solo

Descubrimiento alternativo que solo deja telemetría
(`momentum_hunter/telemetria/<fecha>/vps/movers.jsonl`, la commitea el
rechequeo con el resto). No escribe la watchlist ni manda Telegram. Para
encenderlo en el VPS, a mano y solo cuando el dueño lo decida:

```bash
echo 'MOMENTUM_MOVERS_SOMBRA=1' | sudo tee -a /etc/momentum/paper.env
sudo install -m 755 infra/systemd/bin/run_movers_sombra.sh /opt/momentum/bin/
sudo cp infra/systemd/momentum-movers-sombra.service infra/systemd/momentum-movers-sombra.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now momentum-movers-sombra.timer
```

Apagar: `sudo systemctl disable --now momentum-movers-sombra.timer` (y quitar la
variable, aunque sin ella el wrapper ya es no-op). No bloquea al escaneo ni al
rechequeo: por decisión del dueño nada los espera; la convivencia con Yahoo la
resuelve el archivo de pausa del bot.
