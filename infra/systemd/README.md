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
| `momentum-watchlist.timer` | `/etc/systemd/system/` | cada 5 min, Lun–Vie 13–20 UTC. **Apagado desde el 2026-09-22**: lo reemplaza el vigía (abajo); queda instalado como vuelta atrás |
| `momentum-vigia.service` | `/etc/systemd/system/` | **proceso permanente** (2026-09-22): rechequeo + paper cada 60 s en sesión, git cada 5 min. `Conflicts=momentum-watchlist.timer` |
| `bin/run_vigia.sh` | `/opt/momentum/bin/` | wrapper del vigía (mismo entorno que el del rechequeo); el bucle es `momentum_paper_trader/vigia.py` |
| `momentum-watchlist-state-backup.service` | `/etc/systemd/system/` | oneshot: copia diaria del overlay (`scripts/backup_watchlist_vps_state.sh`, 02:15 America/Monterrey) |
| `momentum-watchlist-state-backup.timer` | `/etc/systemd/system/` | `*-*-* 02:15:00`; **no** se habilita junto al rechequeo |
| `momentum-watchlist-watchdog.service` | `/etc/systemd/system/` | avisa por Telegram si el oneshot lleva >1200 s sin terminar OK dentro de sesión; ejecuta `scripts/watchdog_timer_miss.sh` **desde el árbol** (ver "Watchdog") |
| `momentum-watchlist-watchdog.timer` | `/etc/systemd/system/` | cada 10 min, Lun–Vie 13–20 UTC |
| `bin/run_watchlist_paper.sh` | `/opt/momentum/bin/` | el wrapper (ver abajo por qué vive fuera del árbol). Un `git pull` NO lo actualiza: cada cambio en él exige volver a hacer `install` |
| `momentum-scan.service` | `/etc/systemd/system/` | oneshot: **escaneo completo** (`--limit 1000`, slot rotativo) + paper trader + persist (desde 2026-09-21; antes vivía en GitHub Actions) |
| `momentum-scan.timer` | `/etc/systemd/system/` | cada 30 min en :01:20 y :31:20, Lun–Vie 13–20 UTC |
| `bin/run_scan_paper.sh` | `/opt/momentum/bin/` | wrapper del escaneo: sin candado durante el escaneo; candado solo al escribir la watchlist y al commitear |
| `momentum-movers-sombra.service` / `.timer` | **NO se instala solo** | descubrimiento "movers" en sombra cada 5 min al segundo :30, solo telemetría (`momentum_hunter/movers.py`) |
| `bin/run_movers_sombra.sh` | `/opt/momentum/bin/` (solo si se instala la sombra) | wrapper: no-op salvo `MOMENTUM_MOVERS_SOMBRA=1`; flock propio y, mientras escribe, el mismo candado que el persist |
| `shadow/momentum-shadow-noticias.service` / `.timer` | **NO se instala solo** | sombra B1: noticias Alpaca+Yahoo, JSONL fuera del repo (`shadow_alpaca/`) |
| `shadow/momentum-shadow-screener.service` / `.timer` | **NO se instala solo** | sombra B2: most-actives y movers cada 5 min; no filtra market cap |
| `bin/run_shadow_noticias.sh`, `bin/run_shadow_screener.sh` | `/opt/momentum/bin/` (solo si se instala la sombra Alpaca) | no-op salvo `SHADOW_ALPACA=1`; no hacen git ni tocan la watchlist |

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
# El glob no entra en shadow/: esas unidades no son del camino operativo.
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
variable, aunque sin ella el wrapper ya es no-op). El escaneo sigue sin candado
durante sus ~9 min. La sombra sí toma `/tmp/momentum-paper-git.lock` mientras
escribe `movers.jsonl`: el 2026-09-28 esa escritura cayó entre el stash y el
pop del vigía y el stash entero quedó abandonado, con `revisiones.json` (la
entrada viva), `alertas_enviadas.json` y la auditoría adentro. Si el persist
tiene el candado, la sombra espera hasta 60 s y, si no lo consigue, se salta
esa corrida: mejor perder un snapshot que pisar el pull. El timer dispara al
segundo 30 de cada 5 min, fuera del tick :05. La convivencia con Yahoo la
resuelve el archivo de pausa del bot.

**Actualizar un VPS que ya tiene la sombra** (un `git pull` no actualiza
`/opt/momentum/bin/` ni las unidades; hace falta instalar y rearmar el timer):

```bash
cd /opt/hernan-portafolio
sudo install -m 755 infra/systemd/bin/run_movers_sombra.sh /opt/momentum/bin/
sudo cp infra/systemd/momentum-movers-sombra.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart momentum-movers-sombra.timer
systemctl list-timers momentum-movers-sombra.timer
```

## Vigía (2026-09-22): rechequeo + paper cada 60 s, sin timer

Pedido del dueño: "que corra todo el tiempo sin pararse cada 5 minutos; al
tiro". `momentum-vigia.service` es un proceso permanente
(`momentum_paper_trader/vigia.py`) que en sesión (Lun–Vie 13:00–20:00 UTC)
corre a los :05 de cada minuto `momentum_hunter.run --solo-watchlist` y
`momentum_paper_trader.run`, y cada 5 ticks (y al cerrar la ventana) llama
al wrapper del rechequeo en modo `MOMENTUM_WRAPPER_SOLO_PERSISTIR=1` para
subir el estado a main. La orden ya no espera al commit. Fuera de sesión
duerme. Un paso que falla o se cuelga se registra y el siguiente tick
reintenta; nunca se compensa nada.

El paso paper corre bajo un candado corto (`/tmp/momentum-paper-exec.lock`)
compartido con el paso paper del escaneo: a 60 s, dos ejecutores podrían
revisar la misma señal en el mismo instante. El escaneo en sí sigue sin
candado (#152).

Datos: Yahoo a 60 s hasta el viernes 2026-09-25 (decisión del dueño); si no
alcanza, feed de Alpaca en tiempo real. Con ~10 tickers vigilados son ~12
peticiones por minuto; un 429 activa la pausa del bot 15 min y los ticks
salen vacíos hasta que pase. La telemetría paper escribe una línea por
tick (~400/día) en `/var/lib/momentum/estado`, no en el repo.

Instalar (desde `/opt/hernan-portafolio` con `main` al día):

```bash
sudo install -m 755 infra/systemd/bin/run_vigia.sh infra/systemd/bin/run_watchlist_paper.sh infra/systemd/bin/run_scan_paper.sh /opt/momentum/bin/
sudo cp infra/systemd/momentum-vigia.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable --now momentum-watchlist.timer
sudo systemctl enable --now momentum-vigia.service
sudo journalctl -u momentum-vigia.service -f
```

### Estado fuera del repo (2026-09-28)

Watchlist, revisiones, auditoría, telemetría (incluye `sesion.json` y
`movers.jsonl`), alertas, diario, caché de universo y `estado_diario.json`
viven en `MOMENTUM_ESTADO_DIR` (default `/var/lib/momentum/estado`), con
las mismas subcarpetas. El checkout ya no los tiene. El único proceso
que hace pull es el wrapper del rechequeo (lo llama el vigía), bajo
flock, y solo sube `vps_latido.json`. El escaneo, movers y los workflows
de GitHub no tocan git para este estado.

Decisión de la watchlist: en el VPS el buscador y el ejecutor comparten
disco, así que `watchlist.json` también sale del repo. El ejecutor sigue
sin escribirlo (overlay en `/var/lib/momentum/watchlist_vps_state.json`).
El panel lee el directorio de estado. GitHub ya no recibe la telemetría:
`respaldo_gha.py` mira `vps_latido.json`. Limitación honesta: si el VPS
está caído, el respaldo de GitHub puede escanear y avisar, pero no le
deja una watchlist al VPS cuando vuelva, porque el ejecutor tampoco
estaría leyendo GitHub. Hasta el primer latido después del deploy,
GitHub puede creer que el VPS está en silencio.

`scripts/git_pull_con_estado_local.sh` se queda como red de seguridad
por si el árbol todavía tiene estado sucio. Nunca `--force` ni
`reset --hard`. No uses `git stash` para desplegar esto: un pop abortado
fue lo que perdió el libro el 2026-09-28. Hay stashes de ese día en el
VPS; no se tiran hasta comprobar que la copia tiene la entrada viva.

Primera migración, con los servicios parados, ANTES del pull (el commit
borra los archivos del índice y un pull sobre un worktree sucio falla,
o un pop abortado se los lleva):

```bash
sudo systemctl stop momentum-vigia.service momentum-scan.timer momentum-movers-sombra.timer
sudo systemctl stop momentum-scan.service momentum-movers-sombra.service
sudo mkdir -p /var/lib/momentum/estado
sudo chown -R momentum:momentum /var/lib/momentum
cd /opt/hernan-portafolio
sudo -u momentum env MOMENTUM_ESTADO_DIR=/var/lib/momentum/estado bash scripts/migrar_estado_fuera_del_repo.sh
sudo -u momentum grep -n 783f81bc /var/lib/momentum/estado/momentum_paper_trader/revisiones.json
sudo -u momentum git pull --rebase origin main
sudo install -m 755 infra/systemd/bin/run_vigia.sh infra/systemd/bin/run_watchlist_paper.sh infra/systemd/bin/run_scan_paper.sh infra/systemd/bin/run_movers_sombra.sh /opt/momentum/bin/
sudo cp infra/systemd/momentum-vigia.service infra/systemd/momentum-scan.service infra/systemd/momentum-scan.timer infra/systemd/momentum-movers-sombra.service infra/systemd/momentum-movers-sombra.timer infra/systemd/momentum-watchlist.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart momentum-vigia.service momentum-scan.timer momentum-movers-sombra.timer
```

Si `revisiones.json` del directorio de estado no tiene `783f81bc`,
recuperar desde `git stash list` hacia ese directorio y no soltar el
stash. Un pull no actualiza `/opt/momentum/bin/`.

El escaneo ya no hace pre-pull. Su timer sigue en el segundo :20.

Vuelta atrás del vigía (un comando cada uno):

```bash
sudo systemctl disable --now momentum-vigia.service
sudo systemctl enable --now momentum-watchlist.timer
```

El watchdog (`momentum-watchlist-watchdog.timer`) omite el chequeo de
silencio mientras `momentum-watchlist.timer` está apagado, así que no
avisa por el vigía. Lo que sí lo vigila: `Restart=always` de systemd ante
una caída, el panel (Rechequeo pasa a "Revisar" sin evento en 12 min) y el
respaldo de GitHub (actúa si `vps_latido.json` lleva >20 min en sesión).
Marca de vida fuera de git: `/var/lib/momentum/vigia_latido.json`.

### Despliegue desde GitHub Actions (2026-09-23)

`.github/workflows/deploy_vps.yml` (solo `workflow_dispatch`, nunca por
cron) entra al VPS por SSH y corre `scripts/deploy_vps.sh` por stdin, que
hace la copia a `/var/lib/momentum/estado` antes del `git pull --rebase
origin main` e instala los wrappers en `/opt/momentum/bin/`. No reinicia
servicios (el daemon-reload y el restart son del operador), no usa
`--force` ni `reset --hard`,
y se niega a correr en sesión (13:00-20:05 UTC) salvo que se marque
`forzar_en_sesion`.

Secretos del repositorio (Settings → Secrets and variables → Actions):
`VPS_SSH_KEY` (la llave privada completa) y `VPS_HOST` (la IP). Opcional
`VPS_USER` (por omisión `ubuntu`). Sin los dos primeros, el workflow
falla antes de intentar nada. La llave vive solo en GitHub (regla 7 del
CLAUDE.md); el runner la escribe con permisos 600 y la borra al final.
Cada despliegue queda en el historial de Actions con su motivo.

## Sombra Alpaca (`shadow_alpaca/`, 2026-09-28): NO se instala sola

Herramienta de comparación, en un paquete que el hunter y el paper trader
no importan. Habla solo con `https://data.alpaca.markets` (noticias
Benzinga, screener y barras SIP). No escribe `watchlist.json`, no manda
Telegram, no coloca órdenes y no entra en el `git add` del vigía: el JSONL
vive en `/var/lib/momentum/shadow_alpaca`, fuera del repo.

El `cp infra/systemd/*.service` de arriba no copia `shadow/` a propósito.
B3 (histórico SIP) no tiene unidad: es un CLI offline, sin timer.

Las dos unidades quedan en no-op mientras `SHADOW_ALPACA` no sea `1`.
No escriben la pausa de Yahoo del bot; si esa pausa ya está activa, B1
anota el lado Yahoo como no disponible en vez de como una lista vacía.

Instalar, a mano, desde `/opt/hernan-portafolio` con `main` al día:

```bash
echo 'SHADOW_ALPACA=1' | sudo tee -a /etc/momentum/paper.env
sudo install -d /var/lib/momentum/shadow_alpaca
sudo install -m 755 infra/systemd/bin/run_shadow_noticias.sh infra/systemd/bin/run_shadow_screener.sh /opt/momentum/bin/
sudo cp infra/systemd/shadow/momentum-shadow-noticias.service infra/systemd/shadow/momentum-shadow-noticias.timer /etc/systemd/system/
sudo cp infra/systemd/shadow/momentum-shadow-screener.service infra/systemd/shadow/momentum-shadow-screener.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now momentum-shadow-noticias.timer momentum-shadow-screener.timer
```

B1 corre a :18 y :48 (el escaneo arranca a :01 y :31). B2 corre cada 5 min
en :04, :09, …, Lun–Vie 13–20 UTC, y dentro del proceso no llama a Alpaca
si la sesión regular está cerrada.

Apagar: `sudo systemctl disable --now momentum-shadow-noticias.timer momentum-shadow-screener.timer`.
Sin la variable, el wrapper ya sale enseguida.

Histórico SIP (no se programa; no lo llama el vigía ni el escaneo):

```bash
python -m shadow_alpaca historia descargar --simbolos AAPL --desde 2016-01-01 --hasta 2016-01-31 --almacen /var/lib/momentum/shadow_alpaca/sip
python -m shadow_alpaca historia diff --simbolos AAPL --desde 2016-01-01 --hasta 2016-01-31 --almacen /var/lib/momentum/shadow_alpaca/sip
python -m shadow_alpaca informe --sesiones 5
```

Un rango de minutos desde 2016 es mucho volumen: el CLI pagina y, si corta
por el tope, lo anota en el `.meta.json` (`truncado: true`). No rellena
velas incompletas con ceros.

