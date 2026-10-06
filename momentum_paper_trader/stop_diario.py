"""Stop de pérdida diaria de la cuenta (pedido del dueño, 2026-10-01).

Regla: si `equity − last_equity ≤ −pct × last_equity`, el ejecutor no
abre entradas nuevas en lo que queda de la sesión. `equity` de Alpaca ya
incluye lo realizado del día y lo no realizado de las posiciones
abiertas (también las que vienen de la noche anterior), y `last_equity`
es el equity al cierre de la sesión previa. Por eso la medida es una
sola resta, sin reconstruir fills.

Qué hace al cruzarse:
  - Bloqueo global `PERDIDA_DIARIA` (ver `bloqueos.py`) en esta corrida
    y en todas las de la misma sesión: queda pegado en disco aunque el
    P&L se recupere.
  - Cancela las órdenes de ENTRADA que no se han llenado nada (compra,
    `filled_qty` = 0, estado vivo). Nunca una pata de salida (stop o
    take-profit, que son ventas) ni una entrada parcialmente llena: en
    esa, cancelar el padre podría arrastrar las patas de las acciones ya
    compradas.
  - Un solo Telegram por sesión.
  - NO cierra posiciones: sus stops siguen como están. La liquidación
    existe detrás de `MOMENTUM_STOP_DIARIO_LIQUIDAR=1` y viene apagada.

Fail-closed: `equity` o `last_equity` ausentes, no numéricos o
`last_equity ≤ 0` bloquean entradas (`DATO_FALTANTE:...`), nunca se
toman como 0. Un estado del día ilegible también bloquea.

Reset: el estado se guarda por fecha de sesión (Nueva York). La sesión
siguiente usa otro archivo y Alpaca trae un `last_equity` nuevo.

Configuración por entorno:
  - `MOMENTUM_STOP_DIARIO_PCT` (default 1.0, en % de `last_equity`;
    válido 0.1–5). Un valor inválido usa 1.0 y avisa una vez por sesión:
    nunca apaga la regla.
  - `MOMENTUM_STOP_DIARIO` = `enforce` (default) | `observar` | `off`.
    Un valor desconocido se queda en `enforce`.
  - `MOMENTUM_STOP_DIARIO_LIQUIDAR` = `1` liquida todo al cruzar (una
    vez por sesión). Cualquier otro valor: apagado (default)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter.data.halts import directorio_estado
from momentum_paper_trader import bloqueos, dedupe_avisos, notify

log = logging.getLogger("momentum_paper_trader.stop_diario")

ENV_MODO = "MOMENTUM_STOP_DIARIO"
ENV_PCT = "MOMENTUM_STOP_DIARIO_PCT"
ENV_LIQUIDAR = "MOMENTUM_STOP_DIARIO_LIQUIDAR"

MODO_ENFORCE = "enforce"
MODO_OBSERVAR = "observar"
MODO_OFF = "off"
_MODOS = (MODO_ENFORCE, MODO_OBSERVAR, MODO_OFF)

PCT_DEFAULT = 1.0
PCT_MIN = 0.1
PCT_MAX = 5.0

# Entradas que todavía no tienen nada lleno. `partially_filled` queda
# fuera a propósito (ver docstring del módulo).
_ESTADOS_ENTRADA_VIVA = frozenset({"new", "accepted", "pending_new", "accepted_for_bidding"})

_RETENCION = timedelta(days=14)


# ───────────────────────── configuración ─────────────────────────

def modo() -> str:
    s = os.environ.get(ENV_MODO, "").strip().lower()
    if not s:
        return MODO_ENFORCE
    if s in _MODOS:
        return s
    log.warning("%s=%r no es enforce|observar|off; se queda en enforce", ENV_MODO, s)
    return MODO_ENFORCE


def leer_pct() -> tuple[float, str | None]:
    """(pct, problema). Sin variable: default, sin problema. Inválido o
    fuera de 0.1–5: default y el texto del problema, para avisar."""
    raw = os.environ.get(ENV_PCT)
    if raw is None or not raw.strip():
        return PCT_DEFAULT, None
    try:
        pct = float(raw.strip().replace(",", "."))
    except ValueError:
        return PCT_DEFAULT, f"{ENV_PCT}={raw.strip()!r} no es un número"
    if pct != pct or not (PCT_MIN <= pct <= PCT_MAX):
        return PCT_DEFAULT, f"{ENV_PCT}={raw.strip()!r} fuera de {PCT_MIN}–{PCT_MAX}"
    return pct, None


def liquidar_activado() -> bool:
    return os.environ.get(ENV_LIQUIDAR, "").strip().lower() in ("1", "true", "si", "sí", "yes")


# ───────────────────────── estado por sesión ─────────────────────────

def directorio(base: Path | None = None) -> Path:
    return directorio_estado(base) / "stop_diario"


def ruta_estado(fecha: str, base: Path | None = None) -> Path:
    return directorio(base) / f"{fecha}.json"


class EstadoIlegible(Exception):
    """El archivo del día existe pero no se puede leer o no tiene forma."""


def cargar_estado(fecha: str, base: Path | None = None) -> dict | None:
    """None si no hay estado para esa sesión. `EstadoIlegible` si existe
    pero no se puede leer: quien llama bloquea (no saber si ya se cruzó
    no es 'no se cruzó')."""
    path = ruta_estado(fecha, base)
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as ex:
        raise EstadoIlegible(type(ex).__name__) from ex
    if not isinstance(data, dict) or not isinstance(data.get("activo"), bool):
        raise EstadoIlegible("forma inesperada")
    return data


def guardar_estado(fecha: str, data: dict, base: Path | None = None) -> bool:
    path = ruta_estado(fecha, base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as ex:
        log.error("stop diario: no se pudo guardar el estado de %s (%s)", fecha, type(ex).__name__)
        return False
    _podar(path.parent, fecha)
    return True


def _podar(carpeta: Path, fecha_actual: str) -> None:
    try:
        limite = (datetime.fromisoformat(fecha_actual) - _RETENCION).date().isoformat()
        for p in carpeta.glob("*.json"):
            if p.stem < limite:
                p.unlink()
    except Exception:
        pass


# ───────────────────────── evaluación ─────────────────────────

def _numero(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


@dataclass
class Evaluacion:
    modo: str
    fecha_sesion: str
    pct: float
    equity: float | None
    last_equity: float | None
    pnl: float | None
    pnl_pct: float | None
    umbral_usd: float | None
    cruzado_ahora: bool
    activo: bool              # pegado: se cruzó en algún momento de la sesión
    bloquea: bool             # si esta corrida no debe abrir entradas
    codigo: str | None
    motivo: str
    problema_config: str | None = None
    recien_activado: bool = False
    activado_en: str | None = None

    def como_dict(self) -> dict:
        return asdict(self)


def evaluar(cuenta: dict | None, ahora: datetime, *, base: Path | None = None) -> Evaluacion:
    """Lee la cuenta cruda de `/v2/account` y el estado del día. Escribe
    el estado si la regla se cruza por primera vez en la sesión. No
    avisa ni cancela: eso lo hace `aplicar`."""
    md = modo()
    pct, problema = leer_pct()
    fecha = dedupe_avisos.fecha_sesion(ahora)
    cuenta = cuenta if isinstance(cuenta, dict) else {}
    equity = _numero(cuenta.get("equity"))
    last_equity = _numero(cuenta.get("last_equity"))
    enforce = md == MODO_ENFORCE

    def _ev(**kw) -> Evaluacion:
        base_kw = dict(modo=md, fecha_sesion=fecha, pct=pct, equity=equity, last_equity=last_equity,
                       pnl=None, pnl_pct=None, umbral_usd=None, cruzado_ahora=False, activo=False,
                       bloquea=False, codigo=None, motivo="", problema_config=problema)
        base_kw.update(kw)
        return Evaluacion(**base_kw)

    if md == MODO_OFF:
        return _ev(motivo="stop diario apagado (MOMENTUM_STOP_DIARIO=off)")

    # Estado pegado del día. Ilegible = no se sabe si ya se cruzó: bloquea.
    try:
        previo = cargar_estado(fecha, base)
    except EstadoIlegible as ex:
        return _ev(bloquea=enforce, codigo=bloqueos.DATO_FALTANTE_STOP_DIARIO,
                   motivo=f"estado del stop diario de {fecha} ilegible ({ex}) -- no se abren entradas (fail-closed)")
    activo_previo = bool(previo and previo.get("activo"))

    if last_equity is None or last_equity <= 0:
        return _ev(activo=activo_previo, bloquea=enforce, codigo=bloqueos.DATO_FALTANTE_LAST_EQUITY,
                   activado_en=(previo or {}).get("activado_en"),
                   motivo="la cuenta vino sin last_equity legible y positivo -- no se abren entradas (fail-closed)")
    umbral = round(pct / 100.0 * last_equity, 2)
    if equity is None:
        return _ev(activo=activo_previo, bloquea=enforce, codigo=bloqueos.DATO_FALTANTE_CUENTA,
                   umbral_usd=umbral, activado_en=(previo or {}).get("activado_en"),
                   motivo="la cuenta vino sin equity legible -- no se abren entradas (fail-closed)")

    pnl = round(equity - last_equity, 2)
    pnl_pct = round(pnl / last_equity * 100.0, 3)
    cruzado = pnl <= -umbral
    recien = cruzado and not activo_previo
    activado_en = (previo or {}).get("activado_en")
    if recien:
        activado_en = ahora.astimezone(UTC).isoformat(timespec="seconds")
        guardar_estado(fecha, {
            "activo": True, "activado_en": activado_en, "pnl": pnl, "pnl_pct": pnl_pct,
            "umbral_usd": umbral, "pct": pct, "last_equity": last_equity, "equity": equity,
            "modo": md, "liquidado": False,
        }, base)
    activo = activo_previo or cruzado
    texto = (f"P&L del día {pnl:+.2f} USD ({pnl_pct:+.2f} %) vs umbral −{umbral:.2f} USD "
             f"(−{pct:.2f} % de last_equity {last_equity:.2f})")
    if activo:
        motivo = (f"stop diario activo desde {activado_en or '?'}: {texto} -- "
                  "no se abren entradas nuevas en esta sesión")
    else:
        motivo = f"stop diario OK: {texto}"
    return _ev(pnl=pnl, pnl_pct=pnl_pct, umbral_usd=umbral, cruzado_ahora=cruzado, activo=activo,
               bloquea=enforce and activo, codigo=bloqueos.PERDIDA_DIARIA if activo else None,
               motivo=motivo, recien_activado=recien, activado_en=activado_en)


# ───────────────────────── acciones ─────────────────────────

def es_entrada_sin_llenar(orden: dict) -> bool:
    """Compra de nivel superior con 0 acciones llenas y estado vivo.
    Cualquier dato dudoso (sin `filled_qty`, no numérico, otro lado,
    otro estado) = NO se toca."""
    if not isinstance(orden, dict):
        return False
    if str(orden.get("side") or "").lower() != "buy":
        return False
    if str(orden.get("status") or "").lower() not in _ESTADOS_ENTRADA_VIVA:
        return False
    lleno = _numero(orden.get("filled_qty"))
    if lleno is None or lleno != 0:
        return False
    return bool(orden.get("id"))


def entradas_a_cancelar(ordenes_abiertas: list[dict] | None) -> list[dict]:
    """Solo órdenes de nivel superior (las patas anidadas son ventas y
    no se miran)."""
    return [o for o in (ordenes_abiertas or []) if es_entrada_sin_llenar(o)]


def cancelar_entradas(client, ordenes_abiertas: list[dict] | None) -> list[str]:
    canceladas: list[str] = []
    for o in entradas_a_cancelar(ordenes_abiertas):
        try:
            client.cancelar_orden(str(o["id"]))
            canceladas.append(str(o.get("symbol") or o["id"]))
        except Exception as ex:
            log.warning("stop diario: no se pudo cancelar la entrada %s (%s)",
                        o.get("symbol"), type(ex).__name__)
    return canceladas


def _avisar_una_vez(clave: str, texto: str, ahora: datetime,
                    categoria: str = notify.CATEGORIA_INFO) -> bool:
    if dedupe_avisos.ya_avisada(clave):
        return False
    try:
        notify.enviar(texto, categoria=categoria)
    except Exception as ex:
        log.warning("stop diario: no se pudo mandar el Telegram (%s)", type(ex).__name__)
        return False
    dedupe_avisos.marcar(clave, ahora)
    return True


def formatear_aviso(ev: Evaluacion, canceladas: list[str], liquidado: bool) -> str:
    lineas = [
        f"{notify.PREFIJO} 🛑 <b>STOP DIARIO</b>",
        notify.escapar(f"P&L del día {ev.pnl:+.2f} USD ({ev.pnl_pct:+.2f} %) ≤ −{ev.pct:.2f} % "
                       f"de last_equity (−{ev.umbral_usd:.2f} USD)."),
    ]
    if ev.modo == MODO_OBSERVAR:
        lineas.append("Modo observar: NO se bloquea nada.")
    else:
        lineas.append("Sin entradas nuevas en lo que queda de la sesión.")
        if canceladas:
            lineas.append(notify.escapar("Entradas pendientes canceladas: " + ", ".join(canceladas)))
        lineas.append("Liquidación: hecha." if liquidado
                      else "Posiciones abiertas: siguen con sus stops (no se liquida).")
    return "\n".join(lineas)


def aplicar(ev: Evaluacion, client, ordenes_abiertas: list[dict] | None, ahora: datetime,
            *, base: Path | None = None) -> list[str]:
    """Efectos del stop diario en una corrida real (no dry-run). Devuelve
    los símbolos de entradas canceladas. Nunca lanza."""
    canceladas: list[str] = []
    try:
        if ev.problema_config:
            _avisar_una_vez(
                dedupe_avisos.clave("stop_diario_config", "CUENTA", ahora),
                f"{notify.PREFIJO} ⚠️ stop diario: "
                + notify.escapar(f"{ev.problema_config}; se usa {PCT_DEFAULT:.1f} %."),
                ahora)
        if ev.codigo and bloqueos.es_dato_faltante(ev.codigo) and ev.bloquea:
            _avisar_una_vez(
                dedupe_avisos.clave("stop_diario_dato", "CUENTA", ahora),
                f"{notify.PREFIJO} {notify.ESTADO_ERROR} stop diario sin datos: "
                + notify.escapar(ev.motivo), ahora)
        if not ev.activo:
            return canceladas
        liquidado = False
        if ev.modo == MODO_ENFORCE:
            canceladas = cancelar_entradas(client, ordenes_abiertas)
            if liquidar_activado():
                liquidado = _liquidar_una_vez(ev, client, base=base)
        if ev.pnl is not None and ev.umbral_usd is not None:
            # Crítico: el stop diario disparó (2026-10-06). Los avisos de
            # configuración / dato faltante de arriba son informativos.
            _avisar_una_vez(dedupe_avisos.clave("stop_diario", "CUENTA", ahora),
                            formatear_aviso(ev, canceladas, liquidado), ahora,
                            categoria=notify.CATEGORIA_CRITICO)
    except Exception as ex:  # pragma: no cover - cinturón
        log.warning("stop diario: fallo al aplicar efectos (%s)", type(ex).__name__)
    return canceladas


def _liquidar_una_vez(ev: Evaluacion, client, *, base: Path | None = None) -> bool:
    """Solo con `MOMENTUM_STOP_DIARIO_LIQUIDAR=1`. Una vez por sesión."""
    try:
        estado = cargar_estado(ev.fecha_sesion, base) or {}
    except EstadoIlegible:
        return False
    if estado.get("liquidado"):
        return False
    try:
        client.cerrar_todas_las_posiciones()
    except Exception as ex:
        log.error("stop diario: la liquidación falló (%s)", type(ex).__name__)
        return False
    estado.update({"activo": True, "liquidado": True})
    guardar_estado(ev.fecha_sesion, estado, base)
    return True
