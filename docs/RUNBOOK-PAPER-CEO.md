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

Dos escritores sobre el mismo JSON reventaban el rebase (clase CONFLICT, run 34641814733). Partición:

| Artefacto | Quién hace `git add` / push |
|---|---|
| `watchlist.json` | **GHA hunter** (dueño). El cron GHA watchlist puede seguir stagedándolo como escritor secundario (rechecks cuando el VPS no corre); overlap hunter↔watchlist GHA ya estaba aceptado. **VPS no lo commitea** (sí puede actualizarlo en local para paper). |
| `auditoria/` | **GHA hunter** (dueño). GHA watchlist puede stagedarlo. **VPS no.** |
| hunter telem (`momentum_hunter/telemetria/`) | **GHA hunter**. El VPS puede persistir su partición local si existe. |
| paper telem (`momentum_paper_trader/telemetria/`) | **VPS** (único para git push). GHA no (`git add` quitado: run 34624961161). |

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
momentum_paper_trader LEE TRIGGERED (nunca escribe watchlist)
  → ia_decision (fail-closed)
  → alpaca_client → https://paper-api.alpaca.markets/v2 (hardcodeado)
```

Workflows (UTC Lun–Vie): hunter `*/30 13-20`; watchlist+paper `*/5 13-20`.
**Nota operativa:** la cadencia real observada del cron corto puede ser mucho menor (gaps ~2.5h). El puente temporal `momentum-paper-cadence-bridge` itera watchlist+paper ~cada 5 min dentro de un job largo (ventana 13:00–21:00 UTC) hasta que haya VPS.

## 3. Qué requiere OK humano

| Tema | Por qué |
|---|---|
| Live / cuenta real | Paper hardcodeado; cambio de URL + aprobación humana |
| Umbrales / score / config | Humano decide; ninguna función auto-ajusta |
| VPS / servidor propio | Decisión pendiente ante latencia GHA. El puente GHA es temporal; apagarlo al tener VPS (abajo). |
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
| hunter | `*/30 13-20 * * 1-5` | sesión; grupo `momentum-opportunity-hunter` |
| watchlist (+ paper) | `*/5 13-20 * * 1-5` | fallback; GHA lo atrasa. Grupo `…-watchlist` |
| **puente cadencia paper** | `0 13-19 * * 1-5` + loop interno ~5 min | temporal hasta VPS; mismo grupo que watchlist |
| outcomes hunter | `30 21 * * 1-5` | ≈ 15:30 MT |
| Daily ingestion + predictions | `0 22 * * 1-5` | **16:00 MT = OLS/backend, NO resumen paper** |

### Puente de cadencia (temporal, hasta VPS)

- **Qué hace:** un job hosted (tope 350 min, presupuesto interno 340) corre `--solo-watchlist` + paper cada ~5 min en 13:00–21:00 UTC Lun–Vie, y se re-despacha si la ventana sigue abierta. No cambia umbrales, riesgo ni endpoint paper.
- **Cómo apagar:** (1) variable de repo `MOMENTUM_CADENCE_BRIDGE=off`; (2) Actions → `momentum-paper-cadence-bridge` → Disable workflow; (3) revertir el PR. El cron `*/5` de watchlist **no** se apaga con (1)/(2).
- **Minutos Actions (repo privado):** sleep cuenta. ~8 h/día hábil ≈ 480 min/día ≈ 10 500 min/mes. Cuotas Free/Pro/Team privadas: 2 000–3 000 min/mes — las agota en días. En repo público no se cobran. No dejarlo encendido en privado sin aceptar esa factura.

**HUECO:** el digest paper diario 16:00 MT Lun–Vie lo hace el Director (rutina de agentes Grok), no un workflow de este repo.
