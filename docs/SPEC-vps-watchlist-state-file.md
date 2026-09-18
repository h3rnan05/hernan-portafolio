# SPEC — Archivo de estado VPS para mutaciones de watchlist

**Estado:** IMPLEMENTADO (paper only; timer de rechequeo sigue OFF)
**Fecha:** 2026-09-18
**Contexto:** `--solo-watchlist` en VPS mutaba `watchlist.json` en disco
(ej. VRA watching→expired) y bloqueaba `git pull --rebase`. Dejar de
hacer `git add` no bastaba: el VPS **seguía escribiendo el mismo PATH**
que GHA versiona.

**Constraints vigentes:** Timer `momentum-watchlist` OFF. Paso 2 hunter
discovery HOLD. Paper trading only. Sin `--force`.

---

## Condiciones de aprobación (Claude)

Este PR cubre las tres condiciones que Claude pidió para aprobar el
diseño, no solo el SPEC original:

1. **Fail-loud.** Cualquier `watchlist.guardar()` contra el PATH
   canónico durante `--solo-watchlist` con flag ON lanza
   `EscrituraWatchlistCanonicaProhibida`. Hay un test por cada call
   site cortado y uno que fuerza un call site escapado.
2. **Limitación v1 documentada.** Las transiciones VPS **no** llegan al
   GitHub. Quien analice `watchlist.json` en origin ve solo la foto de
   GHA. Fase 2 (sync state→GHA) queda fuera.
3. **Backup diario concreto.** `scripts/backup_watchlist_vps_state.sh`
   copia el state a
   `/var/backups/watchlist-vps-state-YYYY-MM-DD.json` (fallback
   `/var/lib/momentum/backups/` si `/var/backups` no es escribible).
   Unidades systemd versionadas; **no** se habilitan en este PR. El
   wrapper VPS también lo invoca en cada corrida (best-effort).

Feature flag / rollback (ya estaba en el SPEC):
`MOMENTUM_WATCHLIST_VPS_STATE=0` restaura `guardar` → PATH.

---

## 1) Qué archivo y formato

| Pieza | Ruta | En git? |
|---|---|---|
| Canónico (solo GHA escribe) | `momentum_hunter/watchlist.json` | Sí |
| Estado runtime VPS | `/var/lib/momentum/watchlist_vps_state.json` (override: `MOMENTUM_WATCHLIST_STATE`) | **No** |

Permisos: owner `momentum:momentum`, mode `0640`; directorio
`/var/lib/momentum` `0750`.

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
      "overlay_ts": "2026-09-18T16:43:07+00:00",
      "tarde_consecutivas": 0,
      "transiciones_append": [
        {"a": "expired", "motivo": "…", "en": "2026-09-18T16:43:07+00:00"}
      ]
    }
  }
}
```

`overlay_ts` no estaba en el diseño original. Existe porque
`actualizar_niveles` y `tarde_consecutivas` **no** tocan
`actualizado_en`: sin un reloj de escritura, el empate devolvería
siempre GHA y se perderían esos mutadores.

**Clave:** ticker. **Campos overlay:** estado, actualizado_en,
tarde_consecutivas, niveles (`ultima_*` / `stop_tesis`), latencias,
`clima_mercado`, `transiciones_append`.

**No van al state** (siguen solo en canónico GHA): `nombre`,
`creado_en`, `catalizador_*`, `shares_float`, `short_pct_float`,
`es_large_cap`, `score_base`, `atr_diario`, `gap_pct_congelado`.

`transiciones_append` **añade** transiciones locales; no reescribe el
historial canónico (dedupe por estado+timestamp+motivo).

---

## 2) Call sites exactos (post-implementación)

`watchlist.guardar()` → `PATH.write_text` (`momentum_hunter/watchlist.py`).

### A. Path VPS — `revisar_watchlist` (`run.py`, CLI `--solo-watchlist`)

La función pública activa la guardia canónica y delega en
`_revisar_watchlist_cuerpo`. Los cuatro `guardar` pasaron por
`_persistir_rechequeo` (state si flag ON, PATH si OFF / dry-run no llama):

| Línea | Rama | Qué hace |
|---|---|---|
| ~946 | `cargar` + `aplicar_overlay` si flag | Canónico read-only + overlay |
| ~961 | watchlist vacía / solo terminales (tras `purgar_antiguas`) | persistir |
| ~1019 | proveedor sin candidatos (tras `expirar_vencidas`) | persistir |
| ~1065 | COMMIT pre-Telegram | persistir |
| ~1098 | post-latencia Telegram | persistir |

Mutators in-memory (el disco es `_persistir_rechequeo`):
`purgar_antiguas`, `expirar_vencidas`, `actualizar_niveles`,
`marcar_triggered`, y vía `_evaluar_no_disparada`: `marcar_invalidated`,
`marcar_missed`.

`_revisar_watchlist_cuerpo` **no** contiene `watchlist.guardar(` —
lo verifica `test_revisar_watchlist_cuerpo_tiene_exactamente_cuatro_persistencias_y_cero_guardar`.

### B. Path full scan — SIN cambio

| Línea | Función | Nota |
|---|---|---|
| ~707 | `_actualizar_watchlist` → `guardar` | Dueño canónico; GHA |
| ~1326 | `main` post-Telegram latencia → `guardar` | GHA full |

### C. Paper trader en el mismo host

Con flag ON, `executor` / `archivo` / clima leen con overlay. ARCHIVED
sin `path_watchlist` persiste al state, no a PATH (si no, el paper
volvería a suciar el tree después del hunter).

---

## 3) Quién consume el state y cuándo

1. GHA (full o watchlist workflow) es el **único escritor** de
   `watchlist.json` en git. **No** consume el state en v1.
2. VPS `--solo-watchlist` (flag ON):
   - Lee `watchlist.json` del checkout (solo lectura).
   - Aplica el state por ticker.
   - Evalúa / muta en memoria.
   - Persiste **solo** al state file.
3. Paper trader en el mismo VPS usa la misma verdad operativa.

### Limitación v1 (condición Claude #2)

Mutaciones VPS (ej. VRA→expired, un TRIGGERED local, ARCHIVED paper)
**no** aparecen en origin hasta que GHA re-escriba ese ticker en su
propia corrida. Análisis de `watchlist.json` en GitHub = visión GHA.

---

## 4) Si GHA y VPS discrepan: quién gana

| Situación | Ganador |
|---|---|
| Ticker solo en canónico | GHA |
| Overlay de un ticker **ausente** del canónico | Descartar overlay (no resucitar fantasmas) |
| Canónico ya **terminal** y overlay cambia `estado` | GHA (no reabrir / no degradar) |
| Canónico `watching` y overlay más nuevo (`actualizado_en` o `overlay_ts`) | VPS |
| Snapshot catalizador/float/score/gap | Siempre GHA |
| Empate de timestamps | GHA |

Canónico terminal **con el mismo estado** (TRIGGERED) sí puede recibir
niveles/latencia más nuevos del overlay: no es un cambio de estado.

---

## 5) Feature flag y rollback

| Valor | Efecto |
|---|---|
| unset / `0` / `false` / `off` | Código Python: OFF. `guardar` → PATH (GHA, tests, rollback). |
| `1` / `true` / `yes` / `on` | Overlay + state file. Guardia canónica ON en `--solo-watchlist`. |
| Wrapper VPS | `export MOMENTUM_WATCHLIST_VPS_STATE="${MOMENTUM_WATCHLIST_VPS_STATE:-1}"` |

### Señales de rollback

- Persist telem vuelve a fallar por dirty watchlist.
- Paper / Telegram pierden disparos que el VPS sí veía.
- Discrepancia masiva overlay vs canónico (>10% watching divergente >2h).

### Orden de rollback

1. `sudo systemctl stop momentum-watchlist.timer` (si estuviera ON).
2. `MOMENTUM_WATCHLIST_VPS_STATE=0` en `/etc/momentum/paper.env` **o**
   revertir este PR.
3. **No** reaplicar el state al canónico automáticamente.
4. Conservar evidencia:
   `cp /var/lib/momentum/watchlist_vps_state.json /var/backups/watchlist-vps-state-….json`
5. Opcional: renombrar el state para forzar solo canónico.
6. Verificar `git status` limpio en `/opt/hernan-portafolio`.
7. Re-enable del timer de rechequeo: solo con GO humano/Claude.

### Qué NO hacer

- `git push --force`
- Mezclar a mano state → `watchlist.json` sin revisión
- Arrancar hunter discovery (Paso 2 HOLD) como atajo
- Habilitar `momentum-watchlist.timer` desde este PR

---

## 6) Backup diario (condición Claude #3)

El state vive fuera de git. Backup concreto:

```text
/var/backups/watchlist-vps-state-YYYY-MM-DD.json
```

- Script: `scripts/backup_watchlist_vps_state.sh`
  (`MOMENTUM_WATCHLIST_STATE`, `MOMENTUM_WATCHLIST_STATE_BACKUP_DIR`).
- systemd (versionado, **no habilitado** aquí):
  `infra/systemd/momentum-watchlist-state-backup.{service,timer}` —
  00:30 UTC, `Persistent=true`, corre como root para poder escribir
  `/var/backups`.
- El wrapper VPS lo invoca en cada corrida (best-effort). Si
  `/var/backups` no es escribible por `momentum`, cae a
  `/var/lib/momentum/backups/`.

Una sola vez en el VPS (no arranca el timer de rechequeo):

```bash
sudo install -d -o momentum -g momentum -m 0750 /var/lib/momentum
sudo cp infra/systemd/momentum-watchlist-state-backup.* /etc/systemd/system/
sudo systemctl daemon-reload
# Opcional, independiente del rechequeo:
# sudo systemctl enable --now momentum-watchlist-state-backup.timer
```

---

## 7) API

- `watchlist.STATE_PATH_DEFAULT` /
  `state_path()` (`MOMENTUM_WATCHLIST_STATE`)
- `vps_state_habilitado()`
- `cargar(..., apply_vps_state=False)` — GHA no pasa True
- `cargar_con_overlay()` / `aplicar_overlay()`
- `guardar_vps_state(entradas)` — nunca PATH
- `prohibir_escritura_canonica()` / `EscrituraWatchlistCanonicaProhibida`

---

## Fuera de alcance

- Start de `momentum-watchlist.timer`
- Paso 2 hunter discovery en VPS
- Fase 2 sync state→GHA
- Cambios a umbrales / lag / `#121`
