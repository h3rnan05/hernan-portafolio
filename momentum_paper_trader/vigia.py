"""Vigía: proceso permanente que reemplaza al timer de 5 minutos (2026-09-22).

Pedido del dueño: "que corra todo el tiempo sin estar parándose cada 5
minutos; lo quiero al tiro". Hasta hoy el rechequeo era una unidad oneshot
disparada por un timer cada 5 min: arrancar Python, pedir velas a Yahoo,
evaluar, consultar a la IA, colocar y commitear. Una señal podía llevar
6-8 minutos viva cuando se colocaba la orden. Este proceso hace lo mismo,
pero cada 60 s y sin esperar a git.

Qué hace, por tick (por omisión a los :05 de cada minuto, para que la
vela de 1 min recién cerrada ya esté disponible en Yahoo):
  1. `python -m momentum_hunter.run --solo-watchlist`  (rechequeo, sin cambios)
  2. `python -m momentum_paper_trader.run`             (ejecutor, sin cambios)
     bajo un candado corto compartido con el paso paper del escaneo, para
     que dos ejecutores no revisen la misma señal en el mismo instante.
Cada N ticks (5 por omisión) y al cerrar la ventana, corre el wrapper de
persistencia en modo "solo persistir" (backup del state, git pull,
materializar overlay, commit y push, con su aviso si falla). La orden ya
no espera al commit: git dejó de estar en el camino crítico.

Al cerrar la ventana, antes de ese persist final, corre una vez el libro
sombra del día (`libro_sombra.py`, 2026-09-23): simula variantes de
filtros a precios reales, sin órdenes, y deja `sombra.json` en la
telemetría para que suba en el mismo commit. Best-effort: si falla, el
persist corre igual.

Lo que NO cambia: límites de riesgo, veto de la IA, endpoint paper,
overlay, regla 2 (este módulo vive en el paper trader, no en el hunter).
Fail-closed por tick: si un paso falla o se cuelga (timeout), se registra
y el siguiente tick vuelve a intentar; nunca se "compensa" nada.

Datos: Yahoo a 60 s (decisión del dueño, 2026-09-22: se prueba hasta el
viernes; si no alcanza, feed de Alpaca en tiempo real). Con ~10 tickers en
vigilancia son ~12 peticiones por minuto; un 429 activa la pausa del bot
de 15 min (`data/provider.py`) y los ticks salen vacíos hasta que pase.

Vuelta atrás: parar `momentum-vigia.service` y reactivar
`momentum-watchlist.timer`. Ver infra/systemd/README.md."""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, time as dtime, timedelta
from pathlib import Path

log = logging.getLogger("momentum_paper_trader.vigia")

# Ventana de la sesión regular en UTC, [inicio, fin). El último tick cae
# a las 20:00:05: el cierre diario del paper (`cierre.py`) se decide ahí.
INICIO_VENTANA = dtime(13, 0)
FIN_VENTANA = dtime(20, 1)
CADENCIA_SEG = 60
DESFASE_SEG = 5           # los ticks caen a los :05 de cada minuto
PERSISTIR_CADA_TICKS = 5  # misma cadencia de commit que el timer viejo
TIMEOUT_RECHEQUEO_SEG = 240
TIMEOUT_PAPER_SEG = 240
# Libro sombra al cerrar la ventana (2026-09-23): baja las velas del día
# de ~50-200 tickers y simula variantes; con la pausa entre peticiones
# de Yahoo son un par de minutos. Corre DESPUÉS del último tick y ANTES
# del persist final, para que `sombra.json` suba en el mismo commit.
TIMEOUT_SOMBRA_SEG = 600
TIMEOUT_PERSIST_SEG = 420
ESPERA_CANDADO_PAPER_SEG = 60
WRAPPER_PERSIST_DEFAULT = "/opt/momentum/bin/run_watchlist_paper.sh"
LOCK_PAPER_DEFAULT = "/tmp/momentum-paper-exec.lock"
LATIDO_DEFAULT = "/var/lib/momentum/vigia_latido.json"


def en_ventana(ahora: datetime) -> bool:
    """Lun-Vie, 13:00 <= hora UTC < 20:01. Los feriados los resuelven el
    hunter y el paper por su cuenta (mercado cerrado = no se opera)."""
    u = ahora.astimezone(UTC)
    return u.weekday() < 5 and INICIO_VENTANA <= u.time() < FIN_VENTANA


def proximo_tick(ahora: datetime, cadencia: int = CADENCIA_SEG, desfase: int = DESFASE_SEG) -> datetime:
    """Primer instante ESTRICTAMENTE posterior a `ahora` que cae a
    `desfase` segundos dentro de un múltiplo de `cadencia` (UTC)."""
    u = ahora.astimezone(UTC)
    epoch = int(u.timestamp())
    base = epoch - (epoch % cadencia) + desfase
    if base <= epoch:
        base += cadencia
    return datetime.fromtimestamp(base, tz=UTC)


def inicio_proxima_ventana(ahora: datetime) -> datetime:
    u = ahora.astimezone(UTC)
    candidato = datetime.combine(u.date(), INICIO_VENTANA, tzinfo=UTC)
    if u >= candidato:
        candidato += timedelta(days=1)
    while candidato.weekday() >= 5:
        candidato += timedelta(days=1)
    return candidato


@dataclass
class Resultado:
    nombre: str
    rc: int | None          # None = timeout
    segundos: float

    @property
    def ok(self) -> bool:
        return self.rc == 0


def _ejecutar_subproceso(cmd: list[str], timeout: float, env: dict | None = None) -> int | None:
    """Corre el comando heredando stdout/stderr (van al journal). None si
    se agotó el timeout (el hijo queda matado por subprocess)."""
    try:
        return subprocess.run(cmd, timeout=timeout, env=env, check=False).returncode
    except subprocess.TimeoutExpired:
        return None


class Vigia:
    def __init__(
        self,
        ejecutar=_ejecutar_subproceso,
        reloj=lambda: datetime.now(UTC),
        dormir=time.sleep,
        wrapper_persist: str | None = None,
        lock_paper: str | None = None,
        latido: str | None = None,
        persistir_cada: int = PERSISTIR_CADA_TICKS,
        cadencia: int = CADENCIA_SEG,
        python: str | None = None,
    ) -> None:
        self.ejecutar = ejecutar
        self.reloj = reloj
        self.dormir = dormir
        self.wrapper_persist = Path(wrapper_persist or os.environ.get("MOMENTUM_VIGIA_WRAPPER_PERSIST", WRAPPER_PERSIST_DEFAULT))
        self.lock_paper = Path(lock_paper or os.environ.get("MOMENTUM_PAPER_LOCK", LOCK_PAPER_DEFAULT))
        self.latido = Path(latido or os.environ.get("MOMENTUM_VIGIA_LATIDO", LATIDO_DEFAULT))
        self.persistir_cada = max(1, persistir_cada)
        self.cadencia = cadencia
        self.python = python or sys.executable
        self.ticks = 0
        self.pendiente_persistir = False
        self.detener = False
        # Día (ISO) del último tick y día para el que ya corrió el libro
        # sombra: se corre UNA vez por día, solo al salir de la ventana
        # de forma natural (no en una parada por señal a media sesión).
        self.dia_ultimo_tick: str | None = None
        self.sombra_hecha: str | None = None

    # ── pasos ──
    def _medir(self, nombre: str, cmd: list[str], timeout: float, env: dict | None = None) -> Resultado:
        t0 = self.reloj()
        rc = self.ejecutar(cmd, timeout, env)
        seg = (self.reloj() - t0).total_seconds()
        r = Resultado(nombre, rc, seg)
        if rc is None:
            log.error("%s: timeout tras %.0f s (se reintenta en el próximo tick)", nombre, timeout)
        elif rc != 0:
            log.warning("%s: rc=%s en %.1f s (se reintenta en el próximo tick)", nombre, rc, seg)
        return r

    def rechequeo(self) -> Resultado:
        return self._medir("rechequeo", [self.python, "-m", "momentum_hunter.run", "--solo-watchlist"], TIMEOUT_RECHEQUEO_SEG)

    def paper(self) -> Resultado:
        """El ejecutor corre bajo un candado corto compartido con el paso
        paper del escaneo (run_scan_paper.sh usa el mismo archivo): a 60 s
        la probabilidad de que los dos revisen la misma señal en el mismo
        instante deja de ser despreciable. Si el candado no llega en
        `ESPERA_CANDADO_PAPER_SEG`, este tick no ejecuta (fail-closed): el
        otro ejecutor ya está mirando esa misma señal."""
        self.lock_paper.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_paper.open("a+") as fh:
            limite = time.monotonic() + ESPERA_CANDADO_PAPER_SEG
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= limite:
                        log.warning("paper: candado ocupado %d s; este tick no ejecuta", ESPERA_CANDADO_PAPER_SEG)
                        return Resultado("paper", 75, 0.0)
                    self.dormir(0.5)
            try:
                return self._medir("paper", [self.python, "-m", "momentum_paper_trader.run"], TIMEOUT_PAPER_SEG)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def sombra(self, dia: str) -> Resultado:
        """Libro sombra del día (`libro_sombra.py`): qué habría pasado con
        filtros distintos, a precios reales, sin órdenes. Best-effort: si
        falla o se cuelga, se registra y el persist sigue igual; el
        libro de ese día se puede rehacer a mano con `--dia`."""
        r = self._medir("sombra", [self.python, "-m", "momentum_paper_trader.libro_sombra", "--dia", dia],
                        TIMEOUT_SOMBRA_SEG)
        self.sombra_hecha = dia
        return r

    def persistir(self) -> Resultado:
        env = dict(os.environ)
        env["MOMENTUM_WRAPPER_SOLO_PERSISTIR"] = "1"
        r = self._medir("persistir", ["bash", str(self.wrapper_persist)], TIMEOUT_PERSIST_SEG, env)
        if r.ok:
            self.pendiente_persistir = False
        return r

    def _latir(self, ahora: datetime, resultados: list[Resultado]) -> None:
        """Marca de vida fuera de git, para el panel/watchdog. Best-effort."""
        try:
            import json
            self.latido.parent.mkdir(parents=True, exist_ok=True)
            self.latido.write_text(json.dumps({
                "ts": ahora.isoformat(timespec="seconds"), "tick": self.ticks,
                "pasos": {r.nombre: {"rc": r.rc, "segundos": round(r.segundos, 1)} for r in resultados},
            }), encoding="utf-8")
        except Exception:
            pass

    def tick(self, ahora: datetime | None = None) -> list[Resultado]:
        ahora = ahora or self.reloj()
        self.ticks += 1
        self.dia_ultimo_tick = ahora.astimezone(UTC).date().isoformat()
        resultados = [self.rechequeo(), self.paper()]
        self.pendiente_persistir = True
        log.info("tick %d %s · %s", self.ticks, ahora.astimezone(UTC).strftime("%H:%M:%S"),
                 " · ".join(f"{r.nombre} rc={r.rc} {r.segundos:.1f}s" for r in resultados))
        if self.ticks % self.persistir_cada == 0:
            resultados.append(self.persistir())
        self._latir(ahora, resultados)
        return resultados

    # ── bucle ──
    def correr(self, max_ticks: int | None = None) -> int:
        """Bucle principal. `max_ticks` es solo para pruebas."""
        log.info("vigía arrancado: cadencia %d s, persistir cada %d ticks, ventana %s-%s UTC Lun-Vie",
                 self.cadencia, self.persistir_cada, INICIO_VENTANA.strftime("%H:%M"), FIN_VENTANA.strftime("%H:%M"))
        while not self.detener:
            ahora = self.reloj()
            if not en_ventana(ahora):
                # Al salir de la ventana, una vez por día: corre el libro
                # sombra del día y sube lo último, sombra incluida. La
                # sombra NO puede colgar de `pendiente_persistir`: si el
                # último tick de la sesión cayó en un múltiplo de
                # `persistir_cada` ya persistió y dejó esa bandera en
                # False, y así el libro sombra de ese día no se generaba
                # nunca (medido 2026-09-23: un reinicio a media sesión
                # desfasó el conteo y el último tick coincidió con un
                # persist). Al producir `sombra.json` hay algo nuevo que
                # subir, así que se vuelve a marcar el persist como pendiente.
                if self.dia_ultimo_tick and self.sombra_hecha != self.dia_ultimo_tick:
                    self.sombra(self.dia_ultimo_tick)
                    self.pendiente_persistir = True
                if self.pendiente_persistir:
                    self.persistir()
                espera = (inicio_proxima_ventana(ahora) - ahora).total_seconds()
                self.dormir(max(1.0, min(60.0, espera)))
                continue
            proximo = proximo_tick(ahora, self.cadencia)
            self.dormir(max(0.0, (proximo - ahora).total_seconds()))
            if self.detener:
                break
            if not en_ventana(proximo):
                continue
            self.tick(proximo)
            if max_ticks is not None and self.ticks >= max_ticks:
                break
        if self.pendiente_persistir:
            self.persistir()
        log.info("vigía detenido tras %d tick(s)", self.ticks)
        return 0

    def pedir_parada(self, *_args) -> None:
        """SIGTERM/SIGINT: termina el paso en curso, persiste y sale. El
        subproceso en curso NO se mata: una orden a medio colocar no se
        interrumpe (systemd espera TimeoutStopSec)."""
        log.info("señal de parada recibida; se termina el tick en curso")
        self.detener = True


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Vigía: rechequeo + paper cada 60 s en sesión (PAPER ONLY)")
    ap.add_argument("--cadencia", type=int, default=int(os.environ.get("MOMENTUM_VIGIA_CADENCIA_SEG", CADENCIA_SEG)))
    ap.add_argument("--persistir-cada", type=int, default=int(os.environ.get("MOMENTUM_VIGIA_PERSISTIR_CADA", PERSISTIR_CADA_TICKS)))
    ap.add_argument("--max-ticks", type=int, default=None, help="solo para pruebas manuales")
    args = ap.parse_args(argv)
    v = Vigia(persistir_cada=args.persistir_cada, cadencia=args.cadencia)
    signal.signal(signal.SIGTERM, v.pedir_parada)
    signal.signal(signal.SIGINT, v.pedir_parada)
    return v.correr(max_ticks=args.max_ticks)


if __name__ == "__main__":
    raise SystemExit(main())
