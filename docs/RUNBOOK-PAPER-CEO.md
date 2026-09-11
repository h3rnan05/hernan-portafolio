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
| JSON diario esperado | `momentum_paper_trader/telemetria/{fecha}.json` |
| Persistencia en CI | workflows hunter/watchlist hacen `git add momentum_paper_trader/telemetria` |

**HUECO operativo:** el módulo existe en `main`, pero la carpeta `momentum_paper_trader/telemetria/` con JSON aparece tras la próxima corrida paper real que persista.

**No confundir** con `momentum_hunter/telemetria/` (embudo del hunter).

### Cómo leer p50 / p95 vs ~8 velas
1. Presupuesto: `velas_maximas_desde_patron = 8` → `PRESUPUESTO_MS = 480_000`.
2. En el JSON del día: `sesion.latencia_p50_ms` / `latencia_p95_ms` por series `descubrimiento`, `alerta`, `e2e`.
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
**Nota operativa:** la cadencia real observada puede ser mucho menor que el cron (gaps ~2.5h).

## 3. Qué requiere OK humano

| Tema | Por qué |
|---|---|
| Live / cuenta real | Paper hardcodeado; cambio de URL + aprobación humana |
| Umbrales / score / config | Humano decide; ninguna función auto-ajusta |
| VPS / servidor propio | Decisión pendiente ante latencia GHA |
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
| hunter | `*/30 13-20 * * 1-5` | sesión |
| watchlist (+ paper) | `*/5 13-20 * * 1-5` | sesión |
| outcomes hunter | `30 21 * * 1-5` | ≈ 15:30 MT |
| Daily ingestion + predictions | `0 22 * * 1-5` | **16:00 MT = OLS/backend, NO resumen paper** |

**HUECO:** el digest paper diario 16:00 MT Lun–Vie lo hace el Director (rutina de agentes Grok), no un workflow de este repo.
