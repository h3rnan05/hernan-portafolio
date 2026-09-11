"""Puente temporal de cadencia paper (GitHub Actions).

POR QUÉ EXISTE. El cron de `momentum_hunter_watchlist.yml` pide cada 5
minutos (`*/5 13-20 * * 1-5`) pero GitHub Actions no lo honra: en la
práctica el re-chequeo + paper corre cada ~30-90 min, con huecos medidos
de ~2,5 h. Mientras el VPS (Oracle) no está disponible, este script
ocupa UN job hosted y repite el mismo camino watchlist → paper cada
~5 minutos, hasta agotar un presupuesto bajo el tope de 6 h de los
runners hosted.

QUÉ NO ES. No es un módulo de trading. No cambia umbrales, score,
riesgo, stops ni el endpoint de Alpaca. Invoca los MISMOS comandos que
ya corre `momentum_hunter_watchlist.yml`. La deduplicación sigue siendo
`revisiones.json` (`ticker` + `creado_en`) y el `client_order_id`
determinista del executor -- un overlap con el cron corto no duplica
órdenes.

TEMPORAL. Apagar: variable de repo `MOMENTUM_CADENCE_BRIDGE=off`, o
Actions → este workflow → Disable, o revertir el PR. Ver el cuerpo del
workflow y `docs/RUNBOOK-PAPER-CEO.md`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, time as dt_time, timedelta
from pathlib import Path

# Misma ventana que los cron `13-20` de hunter/watchlist: Lun–Vie
# 13:00 inclusive a 21:00 exclusive UTC. En invierno la sesión regular
# es 14:30–21:00 UTC -- este puente hereda el caveat ya documentado,
# no intenta adivinar el DST.
VENTANA_INICIO_UTC = dt_time(13, 0)
VENTANA_FIN_UTC = dt_time(21, 0)

# 340 min deja ~20 min para checkout, pip, persistir el último ciclo y
# re-despachar antes del hard-cap de 360 de los runners hosted.
MAX_MINUTOS_DEFAULT = 340
INTERVALO_SEGUNDOS_DEFAULT = 300
ESPERA_PRE_VENTANA_MAX_MIN = 20

REPO_ROOT = Path(__file__).resolve().parents[2]

ARCHIVOS_A_PERSISTIR = (
    "momentum_hunter/watchlist.json",
    "momentum_hunter/alertas_enviadas.json",
    "momentum_paper_trader/revisiones.json",
)
DIRS_A_PERSISTIR = (
    "momentum_hunter/auditoria",
    "momentum_hunter/telemetria",
    "momentum_paper_trader/telemetria",
)

COMMIT_MSG = "momentum_hunter: re-chequeo de watchlist [skip ci]"


def _env_int(nombre: str, default: int) -> int:
    raw = os.getenv(nombre, "").strip()
    if not raw:
        return default
    try:
        valor = int(raw)
    except ValueError:
        return default
    return valor if valor > 0 else default


def esta_habilitado(valor: str | None = None) -> bool:
    """Kill switch. Ausente / vacío = encendido (el puente tiene que
    funcionar al mergear, sin un paso manual extra). `off` / `0` /
    `false` / `no` lo apagan sin borrar el workflow."""
    if valor is None:
        valor = os.getenv("MOMENTUM_CADENCE_BRIDGE", "")
    normalizado = (valor or "").strip().lower()
    return normalizado not in {"off", "0", "false", "no"}


def en_ventana_mercado(ahora: datetime) -> bool:
    """Lun–Vie (weekday 0-4) y [13:00, 21:00) UTC.

    No consulta el reloj de Alpaca a propósito: este orquestador no
    decide si se opera -- eso lo hace el paper trader (fail-closed si
    el mercado está cerrado, feriado o ilegible). Acá solo se evita
    quemar minutos de Actions de madrugada o el fin de semana."""
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=UTC)
    ahora = ahora.astimezone(UTC)
    if ahora.weekday() >= 5:
        return False
    # time() de un datetime aware trae tzinfo y no se compara con
    # time() naive -- se compara la hora UTC, no un offset inventado.
    return VENTANA_INICIO_UTC <= ahora.replace(tzinfo=None).time() < VENTANA_FIN_UTC


def minutos_hasta_ventana(ahora: datetime) -> float | None:
    """Minutos hasta el próximo 13:00 UTC de un día hábil, o None si
    ya estamos dentro. None tampoco se inventa si el cálculo no aplica
    (ya dentro): el caller distingue 'esperar' de 'correr'."""
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=UTC)
    ahora = ahora.astimezone(UTC)
    if en_ventana_mercado(ahora):
        return None
    candidato = ahora.replace(hour=13, minute=0, second=0, microsecond=0)
    if ahora.replace(tzinfo=None).time() >= VENTANA_FIN_UTC:
        candidato += timedelta(days=1)
    while candidato.weekday() >= 5:
        candidato += timedelta(days=1)
    return (candidato - ahora).total_seconds() / 60.0


def presupuesto_agotado(inicio: datetime, ahora: datetime, max_minutos: int) -> bool:
    return (ahora - inicio).total_seconds() >= max_minutos * 60


def segundos_de_espera(inicio_iter: datetime, ahora: datetime, intervalo: int) -> int:
    """Duerme lo que falte para completar `intervalo` desde el arranque
    de ESTA iteración. Si la iteración ya duró más que el intervalo,
    0 -- no se acumula deuda (un Yahoo lento no dispara ráfagas)."""
    transcurrido = (ahora - inicio_iter).total_seconds()
    resto = intervalo - transcurrido
    return int(resto) if resto > 0 else 0


def debe_redispatch(motivo: str, ahora: datetime) -> bool:
    """Solo si salimos por presupuesto Y la ventana sigue abierta.
    Fuera de ventana / deshabilitado / error de arranque: no, para no
    crear un loop de jobs vacíos de madrugada."""
    return motivo == "presupuesto" and en_ventana_mercado(ahora)


def _escribir_salida(iteraciones: int, motivo: str, redispatch: bool) -> None:
    dest = os.getenv("GITHUB_OUTPUT")
    lineas = (
        f"iteraciones={iteraciones}\n"
        f"motivo_salida={motivo}\n"
        f"redispatch={'true' if redispatch else 'false'}\n"
    )
    if dest:
        with open(dest, "a", encoding="utf-8") as fh:
            fh.write(lineas)
    print(lineas, end="")


def _correr(cmd: list[str], *, check: bool = False) -> int:
    print(f"+ {' '.join(cmd)}", flush=True)
    completed = subprocess.run(cmd, cwd=REPO_ROOT, check=check)
    return completed.returncode


def _persistir() -> None:
    """Mismos archivos que `momentum_hunter_watchlist.yml`. Un fallo
    de git no tumba el loop: la siguiente iteración reintenta sobre
    el working tree que quede."""
    _correr(["git", "config", "user.name", "momentum-opportunity-hunter"])
    _correr(["git", "config", "user.email",
             "momentum-opportunity-hunter@users.noreply.github.com"])
    for rel in ARCHIVOS_A_PERSISTIR:
        path = REPO_ROOT / rel
        if path.is_file():
            _correr(["git", "add", rel])
    for rel in DIRS_A_PERSISTIR:
        path = REPO_ROOT / rel
        if path.is_dir():
            _correr(["git", "add", rel])
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT
    )
    if staged.returncode == 0:
        return
    _correr(["git", "commit", "-m", COMMIT_MSG])
    _correr(["git", "pull", "--rebase", "origin", "main"])
    _correr(["git", "push"])


def _una_iteracion() -> None:
    # Traer lo que el hunter completo haya commiteado (WATCHING nuevos)
    # antes de re-evaluar. Si el rebase falla se sigue con lo que hay:
    # mejor un ciclo sobre estado viejo que abortar 5 h de vigilancia.
    _correr(["git", "pull", "--rebase", "origin", "main"])
    rc_watch = _correr([sys.executable, "-m", "momentum_hunter.run",
                        "--solo-watchlist"])
    if rc_watch != 0:
        print(f"watchlist salió {rc_watch} -- se sigue con paper (continue-on-error)",
              flush=True)
    rc_paper = _correr([sys.executable, "-m", "momentum_paper_trader.run"])
    if rc_paper != 0:
        print(f"paper trader salió {rc_paper} -- el loop no se corta",
              flush=True)
    _persistir()


def main(argv: list[str] | None = None) -> int:
    del argv  # CLI reservado; la config viaja por env (inputs del workflow)
    if not esta_habilitado():
        print("MOMENTUM_CADENCE_BRIDGE está apagado -- no se corre nada")
        _escribir_salida(0, "deshabilitado", False)
        return 0

    max_minutos = _env_int("CADENCE_BRIDGE_MAX_MINUTOS", MAX_MINUTOS_DEFAULT)
    intervalo = _env_int("CADENCE_BRIDGE_INTERVALO_SEG", INTERVALO_SEGUNDOS_DEFAULT)
    solo_planificar = os.getenv("CADENCE_BRIDGE_SOLO_PLANIFICAR", "").strip() in {
        "1", "true", "yes",
    }

    ahora = datetime.now(UTC)
    espera = minutos_hasta_ventana(ahora)
    if espera is not None:
        if espera > ESPERA_PRE_VENTANA_MAX_MIN:
            print(f"fuera de ventana ({ahora.isoformat()}); faltan {espera:.1f} min -- saliendo")
            _escribir_salida(0, "fuera_de_ventana", False)
            return 0
        print(f"ventana en {espera:.1f} min -- esperando apertura del puente")
        if not solo_planificar:
            time.sleep(int(espera * 60) + 1)

    inicio = datetime.now(UTC)
    iteraciones = 0
    motivo = "fuera_de_ventana"
    print(
        f"puente paper: max {max_minutos} min, intervalo {intervalo}s, "
        f"ventana {VENTANA_INICIO_UTC.isoformat()}-"
        f"{VENTANA_FIN_UTC.isoformat()} UTC",
        flush=True,
    )

    while True:
        ahora = datetime.now(UTC)
        if presupuesto_agotado(inicio, ahora, max_minutos):
            motivo = "presupuesto"
            break
        if not en_ventana_mercado(ahora):
            motivo = "fuera_de_ventana"
            break

        iter_inicio = ahora
        iteraciones += 1
        print(f"--- iteración {iteraciones} @ {ahora.isoformat()} ---", flush=True)
        if solo_planificar:
            print("CADENCE_BRIDGE_SOLO_PLANIFICAR=1 -- no se invocan hunter/paper")
        else:
            try:
                _una_iteracion()
            except Exception as ex:
                # Un ciclo roto no mata el puente: el siguiente reintenta.
                # El trader ya avisa sus propias fallas por Telegram.
                print(f"iteración {iteraciones} falló ({type(ex).__name__}) -- se sigue",
                      flush=True)

        ahora = datetime.now(UTC)
        if presupuesto_agotado(inicio, ahora, max_minutos):
            motivo = "presupuesto"
            break
        if not en_ventana_mercado(ahora):
            motivo = "fuera_de_ventana"
            break
        descanso = segundos_de_espera(iter_inicio, ahora, intervalo)
        print(f"sleep {descanso}s hasta la próxima iteración", flush=True)
        if descanso and not solo_planificar:
            time.sleep(descanso)
        elif solo_planificar:
            # En tests no dormimos el intervalo real.
            break

    redispatch = debe_redispatch(motivo, datetime.now(UTC))
    print(f"salida: {motivo}; iteraciones={iteraciones}; redispatch={redispatch}")
    _escribir_salida(iteraciones, motivo, redispatch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
