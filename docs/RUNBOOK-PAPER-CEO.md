# RUNBOOK PAPER — CEO / Propietario

> **Estado:** documentación operativa · solo paper · sin secretos  
> **Repo:** https://github.com/h3rnan05/hernan-portafolio · rama `main`  
> **Fuentes:** PR #107, `CLAUDE.md`, READMEs hunter/paper, `telemetria.py`, workflows  
> **Huecos marcados** donde el repo no tiene evidencia.

## 1. Telemetría post-PR #107

### Qué es
Instrumentación **solo paper** de latencia e2e (PR #107, mergeado ~2026-09-11 UTC, commit `9f69dff`).  
Mide descubrimiento (vela → persistir `TRIGGERED`) vs punta a punta (vela → orden paper o rechazo) contra un presupuesto de ~8 velas de 1 minuto.  
**No** cambia umbrales, score, criterio de entrada, riesgo ni endpoint Alpaca.

### Dónde vive
| Qué | Path |
|---|---|
| Código | `momentum_paper_trader/telemetria.py` |
| JSONL por fuente (actual) | `momentum_paper_trader/telemetria/{fecha}/{vps\|gha\|local}/events.jsonl` |
| Rollup de esa fuente | `.../{fecha}/{fuente}/sesion.json` (`sesion.latencia_p50_ms`) |
| JSON legado (solo lectura) | `momentum_paper_trader/telemetria/{fecha}.json` |
| Persistencia | **VPS** commitea paper telem. GHA **no** (`git add` quitado: conflicto rebase run 34624961161). |

El VPS exporta `MOMENTUM_TELEM_FUENTE=vps`. GHA, si corre paper, escribe en `gha/` pero no lo persiste. `cargar_dias` / `cargar_sesion` mezclan legado + todas las fuentes.

**No confundir** con `momentum_hunter/telemetria/` (embudo del hunter; GHA sí la persiste, partida en `{fecha}/gha/`).

### Quién commitea qué (git push)

Dos escritores sobre el mismo JSON reventaban el rebase (clase CONFLICT, run 34641814733; 4 escaneos perdidos el 2026-09-11). **Desde el 2026-09-15 hay UN solo escritor: el VPS** (`infra/systemd/bin/run_momentum_paper.sh`, lista `PATHS`). Ningún workflow de momentum tiene cron; un `workflow_dispatch` manual en GHA sigue committeando y puede chocar con el VPS si coincide (uso ocasional, riesgo aceptado).

| Artefacto | Quién hace `git add` / push |
|---|---|
| `watchlist.json`, `auditoria/`, `alertas_enviadas.json`, `estado_diario.json`, `universo_cache.json` | **VPS** (escaneo cada 30 min y re-chequeo cada 5). |
| hunter telem (`momentum_hunter/telemetria/`) | **VPS** (`fuente=vps`). |
| paper telem, `revisiones.json`, `archivo_triggered.jsonl` | **VPS**. GHA no (`git add` quitado: run 34624961161). |

No se apaga `momentum_hunter_watchlist.yml`: es fallback de re-chequeo, no el dueño de discovery. No cambia umbrales ni el endpoint paper.

### Cómo leer p50 / p95 vs ~8 velas
1. Presupuesto: `velas_maximas_desde_patron = 8` → `PRESUPUESTO_MS = 480_000`.
2. Día completo (todas las fuentes): `cargar_sesion(fecha)` o, por fuente, `sesion.json`.
3. Comparar con `480000` ms; `sobre_presupuesto` cuenta muestras por encima.
4. Percentil vacío → `null` (no interpretar como 0).

## 2. Organigrama bots Grok + flujo operativo

### Organigrama Grok (fuera del repo)
**HUECO en repo:** no hay organigrama Grok en GitHub. Organigrama de coordinación de agentes:

```
Propietario humano
└── CEO / Director del Sistema
    ├── Infraestructura
    ├── Buscador
    ├── Ejecutor
    ├── Riesgos
    ├── Backtesting
    ├── Resultados
    ├── Seguridad
    ├── Calidad
    └── Documentación
```

### Flujo verificado en código

```
momentum_hunter → watchlist.json (WATCHING/TRIGGERED/...)
momentum_paper_trader LEE TRIGGERED; escribe ARCHIVED solo tras
desenlace paper terminal (JSONL durable). No inventa oportunidades.
  → ia_decision (fail-closed)
  → alpaca_client → https://paper-api.alpaca.markets/v2 (hardcodeado)
```

Cadencia (UTC Lun–Vie, timers de systemd en el VPS, ver `infra/systemd/`): escaneo cada 30 min 13:00–20:30; watchlist+paper cada 5 min 13–20.
**Nota operativa:** GHA ya no tiene cron para nada de momentum (2026-09-15). Medido la semana del 7 al 14/9: disparaba 2-3 de 16 veces por día, siempre a las mismas horas, y solo se visitaban 2-4 de los 8 slots del universo. Los workflows quedan como `workflow_dispatch` de emergencia.

## 3. Qué requiere OK humano

| Tema | Por qué |
|---|---|
| Live / cuenta real | Paper hardcodeado; cambio de URL + aprobación humana |
| Umbrales / score / config | Humano decide; ninguna función auto-ajusta |
| VPS / servidor propio | Decidido el 2026-09-15: el VPS corre escaneo y re-chequeo y es el único escritor. Los workflows GHA quedan solo para disparo manual. |
| Stops / ATR como piso | Problema B abierto; no calibrar a ciegas |
| Overnight | `permitir_aguantar_overnight = False` por decisión del usuario |

PR #107 no toca esos controles.

## 4. Cómo revisar logs / Actions y el resumen 16:00 MT

```bash
gh run list --repo h3rnan05/hernan-portafolio --limit 15
gh run view <RUN_ID> --repo h3rnan05/hernan-portafolio --log
```

UI: https://github.com/h3rnan05/hernan-portafolio/actions

| Workflow | Cron | Nota |
|---|---|---|
| hunter | ninguno (solo `workflow_dispatch`) | la cadencia vive en `momentum-scan.timer` del VPS |
| watchlist (+ paper) | ninguno (solo `workflow_dispatch`) | la cadencia vive en `momentum-watchlist.timer` del VPS |
| puente cadencia paper | ninguno (solo `workflow_dispatch`) | emergencia si el VPS se cae; mientras corre hay dos escritores |
| outcomes hunter | `30 21 * * 1-5` | ≈ 15:30 MT |
| Daily ingestion + predictions | `0 22 * * 1-5` | **16:00 MT = OLS/backend, NO resumen paper** |

### Puente de cadencia (solo emergencia manual desde el 2026-09-15)

- **Qué hace:** un job hosted (tope 350 min, presupuesto interno 340) corre `--solo-watchlist` + paper cada ~5 min en 13:00–21:00 UTC Lun–Vie, y se re-despacha si la ventana sigue abierta. No cambia umbrales, riesgo ni endpoint paper.
- **Cómo apagar:** (1) variable de repo `MOMENTUM_CADENCE_BRIDGE=off`; (2) Actions → `momentum-paper-cadence-bridge` → Disable workflow; (3) revertir el PR. Ya no hay cron que apagar: solo corre si alguien lo dispara.
- **Minutos Actions (repo privado):** sleep cuenta. ~8 h/día hábil ≈ 480 min/día ≈ 10 500 min/mes. Cuotas Free/Pro/Team privadas: 2 000–3 000 min/mes — las agota en días. En repo público no se cobran. No dejarlo encendido en privado sin aceptar esa factura.

**HUECO:** el digest paper diario 16:00 MT Lun–Vie lo hace el Director (rutina de agentes Grok), no un workflow de este repo.

### Watchdog de silencio (VPS paper)

`scripts/watchdog_timer_miss.sh` (`momentum-watchlist-watchdog.timer`) alerta por Telegram si `momentum-watchlist.service` no termina OK en >1200 s **dentro de la sesión** (Lun–Vie 13–20 UTC). El silencio de finde / overnight no cuenta: si el último OK es anterior a las 13:00 UTC de hoy, la edad se mide desde el open de sesión, no desde el viernes (FP del 2026-09-14). Sin timestamp conocido, no alerta.
