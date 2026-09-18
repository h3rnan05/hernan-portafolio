# schedule-heartbeat (diagnóstico GHA)

Workflow **aparte** de `momentum_hunter.yml`. Sirve para medir la tasa
de drop del scheduler de GitHub Actions (cron pedido vs cron
ejecutado). **No** es el hunter de producción: no escanea, no escribe
watchlist, no usa secrets, no coloca órdenes. Solo paper / solo
observación.

- Workflow: `.github/workflows/schedule_heartbeat.yml`
- Nombre: `schedule-heartbeat`
- Cron: `7,22,37,52 13-20 * * 1-5` (Lun–Vie, sesión US; minutos
  desplazados para no chocar con `:00` / `:30`)
- Disparos esperados: ~32 por día hábil (8 h × 4)
- Cómo leer: Actions → `schedule-heartbeat` → comparar timestamp UTC y
  `run_id` contra la grilla 7/22/37/52. Un hueco es drop del
  scheduler, no del hunter.

No cambiar el cron de `momentum_hunter.yml` a partir de este dato sin
decisión humana: el hunter sigue en `*/30`.
