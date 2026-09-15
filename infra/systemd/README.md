# `infra/systemd/` — unidades del VPS paper

Copia versionada de lo que corre en `momentum-paper` (Oracle Free ARM,
Ubuntu 24.04). Hasta el 2026-09-15 esto vivía **solo** en el VPS: cuatro
unidades, dos drop-ins y un wrapper que ningún repositorio conocía. Una
ruta rota en un `ExecStart` fallaba en el journal del VPS y en ningún
otro lado.

La fuente de verdad es este directorio. El VPS se alinea copiando; nunca
al revés. `momentum_paper_trader/tests/test_systemd_units.py` verifica en
CI que todo path bajo `/opt/` en una línea `Exec*=` exista en el repo y
sea ejecutable, y que los wrappers tengan sintaxis bash válida.

**PAPER ONLY.** Nada de acá toca umbrales, credenciales ni el endpoint de
Alpaca.

## Qué hay y dónde va

| En el repo | En el VPS | Qué es |
|---|---|---|
| `momentum-scan.service` | `/etc/systemd/system/` | oneshot: escaneo completo (`--limit 1000`, ventana rotativa) + paper trader + persist |
| `momentum-scan.timer` | `/etc/systemd/system/` | cada 30 min en punto y media, Lun–Vie 13:00–20:30 UTC |
| `momentum-scan-watchdog.service` / `.timer` | `/etc/systemd/system/` | Telegram si el escaneo lleva >3000 s sin terminar OK dentro de sesión; revisa cada 15 min |
| `momentum-watchlist.service` | `/etc/systemd/system/` | oneshot: `--solo-watchlist` + paper trader + persist |
| `momentum-watchlist.service.d/10-restart-notify.conf` | `/etc/systemd/system/momentum-watchlist.service.d/` | `OnFailure` apagado (anti-spam, decisión del 2026-09-11) |
| `momentum-watchlist.service.d/20-ownership-wrapper.conf` | ídem | reemplaza el `ExecStart` por el wrapper en modo `watchlist` |
| `momentum-watchlist.timer` | `/etc/systemd/system/` | cada 5 min, Lun–Vie 13–20 UTC |
| `momentum-watchlist-watchdog.service` / `.timer` | `/etc/systemd/system/` | Telegram si el re-chequeo lleva >1200 s sin terminar OK dentro de sesión; revisa cada 10 min |
| `bin/run_momentum_paper.sh` | `/opt/momentum/bin/` | el wrapper único de las dos unidades (`escaneo` / `watchlist`) |

**No versionado a propósito:** `/etc/momentum/paper.env` (credenciales;
viven en el VPS y en GitHub Secrets, nunca en el repo).

## Por qué el escaneo corre en el VPS y no en GitHub Actions

Medido la semana del 7 al 14 de septiembre de 2026: el cron `*/30` de
GHA disparaba 2-3 veces al día en vez de 16, y siempre a las mismas
horas (~17:00, ~20:00, ~22:20 UTC). Como el slot del universo se deriva
del reloj (`universe.ventana_rotativa`), eso significaba que cada día se
visitaban 2-4 de los 8 slots, siempre los mismos, y el slot 6 solo se
visitó una vez en la semana, a mano. Seis de las diez señales MISSED de
esa semana se explican por eso: el catalizador apareció, el slot del
ticker no se escaneó durante 18-140 horas, y cuando por fin se escaneó
la ruptura llevaba 47-345 minutos.

El timer de systemd sí se cumple. Con `momentum-scan.timer` cada slot
se visita dos veces por día.

## Un solo escritor de estado

Hasta el 2026-09-15 había dos escritores de estado en git: GHA
(watchlist, auditoría, telemetría del hunter) y el VPS (revisiones,
archivo, telemetría paper). Cuando coincidían, el `git pull --rebase`
de GHA reventaba con conflicto en `watchlist.json` y `auditoria/`, y el
escaneo entero se perdía: pasó cuatro veces el 2026-09-11.

Desde este cambio **el VPS es el único escritor.** El wrapper stagea
todo lo que producen hunter y paper (la lista `PATHS` en
`bin/run_momentum_paper.sh`). Los workflows de GHA quedan solo con
`workflow_dispatch` (PR aparte) para disparos manuales, que siguen
committeando: un disparo manual mientras el VPS corre puede chocar
igual que antes. Es un riesgo aceptado para un uso ocasional, no una
cadencia.

Consecuencia: `scripts/run_watchlist_paper.sh` sigue siendo el
`ExecStart` de la unidad base y sigue siendo código muerto en el VPS
(el drop-in lo reemplaza). Conservarlo o borrarlo es una decisión
aparte del dueño.

### Cómo se reparten las dos unidades el `watchlist.json`

Las dos escriben el mismo archivo, así que no corren a la vez: comparten
un `flock` sobre `/tmp/momentum-paper-run.lock`.

- El re-chequeo **no espera**: si hay un escaneo en curso se salta y el
  timer vuelve en 5 minutos.
- El escaneo **sí espera** hasta 300 s a que termine un re-chequeo.

Limitación honesta: durante un escaneo (~9 min cada 30) se pierden 1-2
re-chequeos. El escaneo también re-evalúa la watchlist y corre el paper
trader, así que una TRIGGERED nueva no queda esperando; lo que sí se
alarga es el intervalo entre re-chequeos de las que ya están en
observación. Reducirlo exige acortar el escaneo (menos tickers por
ventana, rotación más rápida), que es una decisión de estrategia.

Otra a vigilar: todo el tráfico a Yahoo sale ahora de una sola IP. El
contador `sin_barras` de `rechazos_universo` (telemetría del hunter) es
el que dirá si Yahoo empieza a recortar.

## Por qué el wrapper vive en `/opt/momentum/bin/` y no en `scripts/`

Hace `git pull --rebase` sobre el árbol al arrancar. bash lee el script
por trozos, y un pull que reemplace el archivo a mitad de ejecución lo
rompería. Se instala copiando (abajo) y hay que volver a copiarlo cada
vez que cambie en el repo.

## Instalación / actualización en el VPS

Desde `/opt/hernan-portafolio` con `main` al día:

```bash
sudo cp infra/systemd/*.service infra/systemd/*.timer /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/momentum-watchlist.service.d
sudo cp infra/systemd/momentum-watchlist.service.d/*.conf \
        /etc/systemd/system/momentum-watchlist.service.d/
sudo install -m 755 infra/systemd/bin/* /opt/momentum/bin/
sudo rm -f /opt/momentum/bin/run_watchlist_paper.sh
sudo systemctl daemon-reload
sudo systemctl enable --now momentum-scan.timer momentum-scan-watchdog.timer \
                            momentum-watchlist.timer momentum-watchlist-watchdog.timer
systemctl list-units 'momentum*' --all
```

Deben aparecer exactamente ocho unidades: cuatro `.service` (`inactive
dead` entre corridas, son oneshot) y cuatro `.timer` (`active waiting`).

### Cambio de dueño: hacerlo UNA vez, en este orden

1. Mergear primero el PR que quita los `schedule` de GHA. Si el VPS
   empieza a stagear `watchlist.json` mientras GHA todavía lo commitea,
   vuelven los conflictos.
2. En el VPS, descartar la copia local de los archivos que hasta ahora
   eran de GHA, para que el primer `git pull --rebase` del wrapper nuevo
   no choque:

   ```bash
   cd /opt/hernan-portafolio
   git status --short
   git checkout -- momentum_hunter/watchlist.json momentum_hunter/auditoria \
                   momentum_hunter/alertas_enviadas.json momentum_hunter/estado_diario.json
   git pull --rebase origin main
   ```

   Se pierde lo que el VPS haya cambiado en local en esos archivos desde
   su último pull exitoso. No hay forma de fusionarlo a mano con garantías
   y `main` es la copia que GHA venía publicando.
3. Instalar (bloque de arriba) y mirar la primera corrida de cada unidad:

   ```bash
   sudo systemctl start momentum-watchlist.service && journalctl -u momentum-watchlist.service -n 30 --no-pager
   sudo systemctl start momentum-scan.service && journalctl -u momentum-scan.service -n 40 --no-pager
   git log --oneline -3
   ```

   Esperado: `persist ok (intento 1)` en las dos y dos commits nuevos en
   `main`, uno de cada unidad.

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

El watchdog del escaneo es el mismo script con `WATCHDOG_UNIT` y
`WATCHDOG_THRESHOLD_SEC` distintos (ver `momentum-scan-watchdog.service`).

### Aplicar el arreglo del watchdog en el VPS (una vez)

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
uno real. Como root (así corre la unidad), con `paper.env` cargado:

```bash
sudo bash -c 'set -a; . /etc/momentum/paper.env; set +a; \
  WATCHDOG_NOW_EPOCH=$(date -u -d "2026-09-15 15:00:00" +%s) \
  WATCHDOG_LAST_OK_EPOCH=$(date -u -d "2026-09-15 14:30:00" +%s) \
  /opt/hernan-portafolio/scripts/watchdog_timer_miss.sh; echo "rc=$?"'
```

Esperado: la línea `ERROR [paper][vps] ... silent 1800s`, luego
`TELEGRAM_NOTIFY OK`, `rc=1`, y el mensaje en el chat. Si sale
`TELEGRAM_NOTIFY FAIL: missing token or chat id`, `paper.env` no se
cargó; si sale `WARN: telegram notify failed`, la ruta sigue mal.
