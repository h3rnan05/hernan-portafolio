# SPEC — Archivo de estado VPS para mutaciones de watchlist

**Estado:** DISEÑO APROBADO (Claude, 3 condiciones) — implementación en PR aparte, sin merge, timer OFF  
**Fecha:** 2026-09-18  
**Autor:** Gerente de Infraestructura (pedido Claude vía Director)  
**Contexto:** Tras medir que `--solo-watchlist` en VPS muta `watchlist.json` en disco (ej. VRA watching→expired) y bloquea `git pull --rebase`, el ownership actual (GHA dueño de git; VPS ya no hace `git add` de watchlist) es insuficiente: el VPS **sigue escribiendo el mismo archivo** que GHA versiona.

**Constraints vigentes:** Timer `momentum-watchlist` OFF. Paso 2 hunter discovery HOLD. Paper trading only.

### Limitación v1 explícita (condición Claude #2)

- Las transiciones / mutaciones que el VPS escribe en `/var/lib/momentum/watchlist_vps_state.json` **NO llegan al repo** ni a `origin/main` en v1.
- Cualquier análisis, dashboard o revisión humana de `momentum_hunter/watchlist.json` **en GitHub = solo la vista GHA** (descubrimiento + estado canónico de Actions).
- No inferir el estado operativo del paper en VPS mirando solo el JSON de GitHub; para runtime VPS hay que leer el state file (o logs/Telegram del host).
- Sync VPS→GHA queda **fase 2** (fuera de este PR).


---

## 1) Qué archivo y formato

### Ruta propuesta

| Pieza | Ruta | En git? |
|---|---|---|
| Canónico (solo GHA escribe) | `momentum_hunter/watchlist.json` | Sí |
| Estado runtime VPS (nuevo) | `/var/lib/momentum/watchlist_vps_state.json` | **No** |

Justificación de ruta fuera del repo:

- Evita dirty tree y fallos de persist telem (`cannot pull with rebase: unstaged changes`).
- Sobrevive a `git checkout` / `git pull` del repo.
- Permisos: owner `momentum:momentum`, mode `0640`; directorio `/var/lib/momentum` `0750`.

### Backup diario concreto (condición Claude #1)

Además del backup ad-hoc pre-migración, **cron diario** en el VPS:

| Campo | Valor |
|---|---|
| Schedule | `15 2 * * *` (02:15 America/Monterrey; crontab del user `momentum` o root con `sudo -u momentum`) |
| Source | `/var/lib/momentum/watchlist_vps_state.json` |
| Destino | `/var/backups/momentum/watchlist_vps_state-$(date +\%F).json` |
| Retención | `find /var/backups/momentum -name 'watchlist_vps_state-*.json' -mtime +14 -delete` |
| Idempotencia | `cp -a` solo si el source existe (`[ -f ... ] && cp -a ...`) |

Ejemplo de línea crontab:

```cron
15 2 * * * [ -f /var/lib/momentum/watchlist_vps_state.json ] && cp -a /var/lib/momentum/watchlist_vps_state.json /var/backups/momentum/watchlist_vps_state-$(date +\%F).json; find /var/backups/momentum -name 'watchlist_vps_state-*.json' -mtime +14 -delete
```

Crear dir una vez: `sudo mkdir -p /var/backups/momentum && sudo chown momentum:momentum /var/backups/momentum`.

Backup ad-hoc pre-cambio (mismo patrón ya usado 2026-09-18):  
`/var/backups/watchlist-vps-mutado-YYYY-MM-DD-HHMM.json`.

### Formato JSON

```json
{
  "schema": 1,
  "updated_at": "2026-09-18T16:43:07+00:00",
  "source": "vps-solo-watchlist",
  "entries": {
    "VRA": {
      "estado": "expired",
      "actualizado_en": "2026-09-18T16:43:07+00:00",
      "tarde_consecutivas": 0,
      "transiciones_append": [
        {
          "a": "expired",
          "motivo": "…",
          "en": "2026-09-18T16:43:07+00:00"
        }
      ],
      "market_event_ts": null,
      "data_received_ts": null,
      "evaluador_ts": null,
      "mensaje_generado_ts": null,
      "telegram_enviado_ts": null,
      "signal_latency_ms": null,
      "watchlist_escrito_ts": null,
      "ultima_entrada": null,
      "ultimo_stop": null,
      "ultimo_objetivo": null,
      "ultima_zona_entrada_baja": null,
      "ultimos_niveles_ts": null,
      "stop_tesis": null,
      "clima_mercado": null
    }
  }
}
```

### Clave y campos

- **Clave:** `ticker` (string, mayúsculas como en `EntradaWatchlist.ticker`).
- **Campos = solo lo que hoy muta el path VPS `--solo-watchlist` / `revisar_watchlist`** (evidencia en `watchlist.py` + `run.py`):

| Campo | Quién lo muta hoy en VPS solo-watchlist |
|---|---|
| `estado` | `marcar_triggered` / `marcar_invalidated` / `marcar_missed` / `expirar_vencidas` / (vía `_transicionar`) |
| `actualizado_en` | toda transición |
| `transiciones` (append) | `_transicionar` |
| `tarde_consecutivas` | `_evaluar_no_disparada` |
| `ultima_entrada`, `ultimo_stop`, `ultimo_objetivo`, `ultima_zona_entrada_baja`, `ultimos_niveles_ts`, `stop_tesis` (solo 1ª vez) | `actualizar_niveles` |
| `clima_mercado` | si aplica en el chequeo |
| Latencias: `market_event_ts`, `data_received_ts`, `evaluador_ts`, `mensaje_generado_ts`, `telegram_enviado_ts`, `signal_latency_ms`, `watchlist_escrito_ts` | `marcar_triggered` / `registrar_latencia` / efecto de `guardar` |

**Explicitamente NO van al state file (siguen solo en canónico GHA):**  
`nombre`, `creado_en`, `catalizador_*`, `shares_float`, `short_pct_float`, `es_large_cap`, `score_base`, `atr_diario`, `gap_pct_congelado`. Alta y snapshot de catalizador = solo GHA (`agregar_nuevas` en `_actualizar_watchlist`).

`transiciones_append`: el overlay **añade** transiciones locales; no reescribe el historial canónico completo (evita pérdida si GHA ya tenía historial más largo).

---

## 2) VPS debe DEJAR de escribir `momentum_hunter/watchlist.json` en disco

Hoy el wrapper ya **no** hace `git add` de watchlist (paso 7 ownership). Eso no basta: `watchlist.guardar()` sigue haciendo `path.write_text(...)` sobre `PATH = …/watchlist.json`.

### Escritura real hoy

```text
watchlist.guardar()  →  PATH.write_text(...)   # momentum_hunter/watchlist.json
```

Default: `momentum_hunter/watchlist.py` L75 `PATH`, L365–377 `guardar`.

### Call sites a cambiar (lista)

**A. Path VPS primario — `revisar_watchlist` (`run.py`, entrada `--solo-watchlist` L1147–1148)** — líneas **EXACTAS** (HEAD main VPS `/opt/hernan-portafolio`, 2026-09-18):

| Línea | Qué hace hoy | Cambio diseño | Test unitario requerido |
|---|---|---|---|
| **916** | `watchlist.cargar()` | Cargar canónico read-only + aplicar overlay state | `test_cargar_aplica_overlay_por_ticker` |
| **930** | `guardar` tras `purgar_antiguas` (early exit vacío) | Escribir **solo** state file (no `PATH` canónico) | `test_solo_watchlist_early_empty_writes_state_not_canonical` — monkeypatch `PATH.write_text` → fail si se llama; assert state file existe |
| **988** | `guardar` tras expirar mid-loop (sin candidatos) | → state file | `test_solo_watchlist_no_candidates_expires_to_state_only` |
| **1034** | `guardar` COMMIT pre-Telegram | → state file | `test_solo_watchlist_commit_before_telegram_state_only` — mutar a triggered en memoria; assert canónico sha/mtime intacto; state tiene ticker |
| **1067** | `guardar` post-latencia Telegram | → state file | `test_solo_watchlist_latency_second_flush_state_only` |

Guard transversal (todos los tests anteriores + uno dedicado):

| Guard | Test |
|---|---|
| En `--solo-watchlist`, cualquier `guardar(..., path=PATH)` canónico → **fail loud** (`RuntimeError` / assert) | `test_solo_watchlist_canonical_write_fail_loud` — forzar call a `guardar` con PATH default bajo flag solo-watchlist → expect exception |
| Full scan / GHA path sigue pudiendo escribir canónico | `test_full_scan_guardar_still_writes_canonical` (no regresión L707 / L1295) |

Mutators usados en ese path (no escriben disco solos; el disco es `guardar`):  
`purgar_antiguas`, `expirar_vencidas`, `actualizar_niveles`, `marcar_triggered`, y vía `_evaluar_no_disparada`: `marcar_invalidated`, `marcar_missed`, `actualizar_niveles`.

Anclas `watchlist.py` (exactas): `PATH` **L75**; `def guardar` **L365**; `path.write_text` **L377**.

**B. Path full scan (GHA / discovery) — NO desactivar escritura a `watchlist.json`**

| Línea ~ | Función | Nota |
|---|---|---|
| 707 | `_actualizar_watchlist` → `guardar` | Dueño canónico; GHA |
| 1295 | `main` post-Telegram latencia → `guardar` | GHA full |

**C. API a introducir (diseño; sin implementar aún)**

1. `watchlist.STATE_PATH` → `/var/lib/momentum/watchlist_vps_state.json` (o env `MOMENTUM_WATCHLIST_STATE`).
2. `cargar_con_overlay()` / flag en `cargar(apply_vps_state=True)` usado solo por `--solo-watchlist`.
3. `guardar_vps_state(entradas)` o `guardar(..., path=STATE_PATH, mode="overlay")` que serializa **solo** el dict `entries` de tickers tocados / activas mutadas — **nunca** `PATH` canónico cuando `solo_watchlist`.
4. En `--solo-watchlist`: **assert / guard** que prohíba `guardar(..., path=PATH)` (fail loud si un call site se escapa).
5. Implementación = PR CloudAgent aparte (este SPEC es contrato); **sin merge** hasta GO; timer OFF.

**D. Fuera de alcance de este SPEC (no call sites de watchlist canónica)**  
`tracker.guardar`, `diario.py`, tests que usan `path` temporal — sin cambio de ownership.

**E. Wrapper**  
`/opt/momentum/bin/run_watchlist_paper.sh` ya excluye watchlist del stage; sin cambio obligatorio. Opcional: log “state file mtime/sha” post-corrida para watchdog.

---

## 3) Quién consume el state file y cuándo — UNA opción

### Opción elegida: **capa al cargar en VPS (overlay on load)**

**Flujo:**

1. GHA (full o watchlist workflow) sigue siendo el **único escritor** de `momentum_hunter/watchlist.json` en git.
2. VPS en cada `--solo-watchlist`:
   - Lee `watchlist.json` del checkout (solo lectura).
   - Aplica `/var/lib/momentum/watchlist_vps_state.json` en memoria (por ticker).
   - Evalúa / muta en memoria.
   - Persiste mutaciones **solo** al state file.
3. GHA **no** consume el state file en v1.

### Justificación

- Cierra de inmediato el fallo medido (dirty `watchlist.json` → persist FAIL ×5) sin rediseñar GHA ni inventar un canal VPS→GitHub en v1.
- Ownership alineada a la tabla de campos ya entregada: alta+catalizador = GHA; rechequeo runtime = VPS local.
- El ejecutor / paper en el mismo VPS, si lee watchlist, debe usar la misma `cargar_con_overlay` (mismo host = misma verdad operativa).
- Sync GHA←VPS queda como **fase 2** (artifact/S3/`workflow_dispatch` input), no bloquea el desbloqueo del timer de rechequeo.

### Qué se acepta conscientemente en v1

- Mutaciones VPS (ej. VRA→expired) **no** aparecen en origin hasta que GHA re-escriba ese ticker en su propia corrida.
- Dos escritores lógicos de “estado de ticker”, un solo archivo canónico en git.

---

## 4) Si GHA y VPS discrepan sobre un ticker: quién gana

Reglas de merge al aplicar overlay (VPS load):

| Situación | Ganador | Regla |
|---|---|---|
| Ticker solo en canónico | Canónico (GHA) | Sin overlay |
| Ticker en overlay pero **ausente** del canónico | Descartar overlay de ese ticker | GHA “dueño del universo”; no resucitar fantasmas |
| Ambos presentes; canónico ya **terminal** (`triggered`/`missed`/`expired`/`invalidated`/`archived`) y overlay también muta estado | **GHA (canónico)** | No degradar ni reabrir desde VPS si GHA ya cerró |
| Canónico `watching`; overlay tiene transición más nueva (`actualizado_en` overlay > canónico) | **Overlay VPS** | Rechequeo local gana en runtime |
| Campos snapshot (catalizador_*, gap, float, score…) | **Siempre GHA** | Overlay no los escribe |
| Empate de timestamps | **GHA** | Fail-safe estabilidad |

Al **próximo** `guardar` canónico de GHA: el JSON en git refleja solo la visión GHA; el overlay VPS sigue aplicándose encima en el VPS hasta que una regla de arriba lo invalide (p.ej. GHA pasó a terminal o borró el ticker).

---

## 5) Qué se revierte y cómo si sale mal

### Señales de rollback

- Persist telem vuelve a fallar por dirty watchlist (regresión: VPS escribió canónico otra vez).
- Paper / Telegram pierden disparos que antes veían en VPS.
- Watchdog `persist_fail` o `ahead` anómalo post-deploy.
- Discrepancia masiva estados overlay vs canónico (umbral sugerido: >10% tickers watching con overlay divergente >2h).

### Rollback (orden)

1. `sudo systemctl stop momentum-watchlist.timer` (si estuviera ON).
2. Desplegar commit previo (revert del PR de overlay) **o** feature flag env `MOMENTUM_WATCHLIST_VPS_STATE=0` que restaura `guardar`→`PATH` (preferible flag en el mismo PR).
3. **No** reaplicar mutaciones del state al canónico automáticamente.
4. Conservar evidencia:  
   `cp /var/lib/momentum/watchlist_vps_state.json /var/backups/…`  
   (mismo patrón que `/var/backups/watchlist-vps-mutado-2026-09-18-1647.json`).
5. Opcional: borrar o renombrar state file para forzar “solo canónico”.
6. Verificar: `git status` limpio en `/opt/hernan-portafolio`; oneshot dry; watchdog sin FIRE falso.
7. Re-enable timer solo con GO humano/Claude.

### Qué NO hacer en rollback

- `git push --force`
- Mezclar a mano state → `watchlist.json` sin revisión
- Arrancar hunter discovery (Paso 2 HOLD) como “atajo”

---

## Evidencia de código (ancla)

- `PATH` / `guardar` / campos: `momentum_hunter/watchlist.py`
- `--solo-watchlist` → `revisar_watchlist`: `momentum_hunter/run.py` ~898–1067, CLI ~1147
- Full scan `guardar`: `_actualizar_watchlist` ~707; main ~1295
- GHA escribe watchlist: `.github/workflows/momentum_hunter.yml`, `momentum_hunter_watchlist.yml`
- Medición 2026-09-18: post-oneshot sha local ≠ origin; VRA watching→expired; persist FAIL por dirty

---

## Fuera de alcance (este doc / este PR)

- Merge del PR de implementación
- Start de `momentum-watchlist.timer`
- Paso 2 hunter discovery en VPS
- Fase 2 sync state→GHA (ver limitación v1)
- Cambios a `#121` / lag
- Instalar el cron de backup en VPS (ops post-merge; documentado arriba)

## Próximo paso

1. SPEC actualizado con 3 condiciones Claude — **este archivo**.
2. CloudAgent abre PR de implementación (overlay + state file + fail-loud + tests call sites) — **sin merge**.
3. Review humano/Claude → merge → cron backup → GO timer rechequeo.

---

## Apéndice — implementación en este PR (sin merge, timer OFF)

Las líneas L930/L988/L1034/L1067 son de **origin/main** (2026-09-18). Verificado por contexto. En este PR `revisar_watchlist` activa la guardia y delega en `_revisar_watchlist_cuerpo`; esos cuatro `guardar` pasan por `_persistir_rechequeo`:

| Ancla main | Rama | Línea post-PR | Test |
|---|---|---|---|
| L916 `cargar()` | overlay on load | ~946 | `test_cargar_aplica_overlay_por_ticker` |
| **L930** `guardar` early empty | state only | ~961 | `test_solo_watchlist_early_empty_writes_state_not_canonical` |
| **L988** `guardar` sin candidatos | state only | ~1019 | `test_solo_watchlist_no_candidates_expires_to_state_only` |
| **L1034** `guardar` pre-Telegram | state only | ~1065 | `test_solo_watchlist_commit_before_telegram_state_only` |
| **L1067** `guardar` post-latencia | state only | ~1098 | `test_solo_watchlist_latency_second_flush_state_only` |
| Guard `guardar(PATH)` | fail-loud | `EscrituraWatchlistCanonicaProhibida` | `test_solo_watchlist_canonical_write_fail_loud` |
| L707 / L1295 full scan | canónico intacto | ~707 / ~1326 | `test_full_scan_guardar_still_writes_canonical` |

`watchlist.py` post-PR: `PATH` L77; `def guardar` L442 (el `write_text` canónico sigue ahí; la guardia lo bloquea en VPS).

Backup concreto (condición #1):

- Cron versionado: `infra/cron/momentum-watchlist-state-backup` — `15 2 * * *` `CRON_TZ=America/Monterrey`; solo llama a `scripts/backup_watchlist_vps_state.sh` (alternativa al timer systemd)
- Destino: `/var/backups/momentum/watchlist_vps_state-$(date +%F).json` y `/var/backups/momentum/events-$(date +%F).jsonl` (log del panel, `MOMENTUM_EVENTS_LOG` / `DASH_EVENTOS`, default `/var/lib/momentum/events.jsonl`)
- Retención: `find … -mtime +14 -delete` para ambos patrones en `scripts/backup_watchlist_vps_state.sh`
- systemd equivalente (preferido): `momentum-watchlist-state-backup.timer` 02:15, `User=momentum`, `TZ=America/Monterrey`

Feature flag: `MOMENTUM_WATCHLIST_VPS_STATE=0` restaura `guardar` → PATH. Wrapper VPS exporta `=1` por defecto.

**Limitación v1 (condición #2):** las mutaciones VPS **no** llegan al repo. `watchlist.json` en GitHub = vista GHA. Sync = fase 2.

