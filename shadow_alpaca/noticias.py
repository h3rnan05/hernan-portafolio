"""B1. Mismas keywords, dos fuentes, solo un JSONL.

Para los tickers del slot que el hunter acaba de escanear, más los de la
watchlist, se piden las noticias de Alpaca (`/v1beta1/news`, Benzinga,
50 por página) y las de `YahooNewsProvider`. Las dos listas pasan por
`detectar_catalizador` tal cual está en el hunter: este módulo no tiene
una copia de las keywords ni un umbral propio.

Si Yahoo está en la pausa de un 429, ese lado queda `disponible: false`
y `titulares: null`. Una lista vacía significaría 'la fuente respondió
y no había nada', y no es lo que pasó.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from momentum_hunter.catalysts.detector import (
    CATALYST_KEYWORDS,
    YahooNewsProvider,
    detectar_catalizador,
    minutos_desde_catalizador,
)
from momentum_hunter.config import CONFIG, MomentumConfig
from momentum_hunter.data.provider import PausaYahoo
from momentum_hunter.models import Catalizador
from momentum_hunter.universe import tickers as tickers_universo

from shadow_alpaca.cliente import ClienteDatos, ErrorDatos
from shadow_alpaca.jsonl_log import append_linea, dir_salida
from shadow_alpaca.lectura import tickers_de_watchlist, tickers_del_slot, ultimo_escaneo

log = logging.getLogger("shadow_alpaca.noticias")

RUTA_NOTICIAS = "/v1beta1/news"
LIMITE_PAGINA = 50
MAX_PAGINAS = 40
# Margen de un día: `dentro_de_ventana` ya tolera un día de huso. Pedir
# justo `dias_ventana` cortaría un titular que el detector todavía acepta.
MARGEN_DIAS = 1

# Reexportadas para que la prueba de aislamiento pueda afirmar que son
# los mismos objetos, no una copia.
__all__ = ["CATALYST_KEYWORDS", "detectar_catalizador", "correr", "main"]


def _es_limite(nombre: str) -> bool:
    n = nombre.lower()
    return "429" in n or "ratelimit" in n or "toomany" in n


class _ErroresYahoo:
    """Lo único que `YahooNewsProvider` necesita para no tragarse un fallo.

    El proveedor, ante una excepción, devuelve `[]`. Sin esto, un 429 y
    un ticker sin noticias se ven iguales.
    """

    def __init__(self) -> None:
        self.ultimo: str | None = None

    def registrar_error(self, origen: str, ex: BaseException) -> None:
        self.ultimo = type(ex).__name__
        log.warning("yahoo noticias: %s (%s)", origen, type(ex).__name__)


def _fecha_con_hora(fecha: str | None) -> bool:
    return isinstance(fecha, str) and "T" in fecha


def _a_utc(fecha: str) -> datetime | None:
    if not _fecha_con_hora(fecha):
        return None
    try:
        momento = datetime.fromisoformat(fecha.replace("Z", "+00:00"))
    except ValueError:
        return None
    if momento.tzinfo is None:
        return None
    return momento.astimezone(UTC)


def titular_de_noticia(item: object):
    """Un ítem de `/v1beta1/news` → `Titular`, o None si no hay headline.

    La fuente ausente no se rellena con 'benzinga': el endpoint es de
    Benzinga, pero un campo que no vino no es evidencia del medio. Para
    un rumor, inventar la fuente confirmaría con una sola.
    """
    from momentum_hunter.catalysts.detector import Titular

    if not isinstance(item, dict):
        return None
    headline = item.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        return None
    fuente = item.get("source")
    if not isinstance(fuente, str) or not fuente.strip():
        fuente = "desconocida"
    fecha = item.get("created_at")
    if not isinstance(fecha, str) or not fecha.strip():
        fecha = None
    else:
        fecha = fecha.strip()
        if fecha.endswith("Z"):
            # `minutos_desde_catalizador` hace fromisoformat. La Z queda
            # normalizada para no depender de la versión de Python.
            fecha = fecha[:-1] + "+00:00"
    return Titular(headline.strip(), fuente.strip(), fecha)


def simbolos_de_noticia(item: object) -> list[str]:
    if not isinstance(item, dict):
        return []
    raw = item.get("symbols")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for simbolo in raw:
        if isinstance(simbolo, str) and simbolo.strip():
            out.append(simbolo.strip().upper())
    return out


def extraer_pagina(cuerpo: object) -> tuple[list, str | None]:
    """`(noticias, next_page_token)`. Si la clave `news` no está, no es
    una página vacía: la respuesta no trae el dato."""
    if not isinstance(cuerpo, dict) or "news" not in cuerpo:
        raise ErrorDatos("news_ausente")
    news = cuerpo.get("news")
    if not isinstance(news, list):
        raise ErrorDatos("news_ilegible")
    token = cuerpo.get("next_page_token")
    if not isinstance(token, str) or not token:
        token = None
    return news, token


def _bloque_vacio(motivo: str) -> dict:
    return {
        "disponible": False,
        "motivo": motivo,
        "titulares": None,
        "catalizador": None,
        "catalizador_fecha": None,
        "catalizador_fuente": None,
        "catalizador_titular": None,
        "sin_fecha": None,
        "fechas": None,
    }


def _bloque_fuente(titulares, cfg: MomentumConfig, hoy) -> dict:
    catalizador = detectar_catalizador(list(titulares), cfg, hoy)
    fechas = [t.fecha for t in titulares if _fecha_con_hora(t.fecha)]
    sin_fecha = sum(1 for t in titulares if not _fecha_con_hora(t.fecha))
    return {
        "disponible": True,
        "motivo": None,
        "titulares": len(titulares),
        "catalizador": catalizador.tipo if catalizador is not None else None,
        "catalizador_fecha": catalizador.fecha if catalizador is not None else None,
        "catalizador_fuente": catalizador.fuente if catalizador is not None else None,
        "catalizador_titular": catalizador.titular if catalizador is not None else None,
        "sin_fecha": sin_fecha,
        "fechas": fechas,
    }


def lag_deteccion(fecha: str | None, ahora: datetime) -> float | None:
    """Minutos desde el artículo hasta `ahora`. Sin hora en el sello, None:
    `minutos_desde_catalizador` no inventa la precisión."""
    if not _fecha_con_hora(fecha):
        return None
    falso = Catalizador(tipo="sombra", titular="", fuente="", fecha=fecha)
    return minutos_desde_catalizador(falso, ahora)


def lag_entre_fuentes(fecha_alpaca: str | None, fecha_yahoo: str | None) -> tuple[float | None, str | None]:
    """Minutos absolutos entre los dos artículos, y cuál salió antes.

    Si falta uno de los dos sellos con hora, el retraso es None. Cero solo
    cuando los dos instantes son el mismo.
    """
    a = _a_utc(fecha_alpaca) if isinstance(fecha_alpaca, str) else None
    b = _a_utc(fecha_yahoo) if isinstance(fecha_yahoo, str) else None
    if a is None or b is None:
        return None, None
    delta = (a - b).total_seconds() / 60.0
    if delta < 0:
        return -delta, "alpaca"
    if delta > 0:
        return delta, "yahoo"
    return 0.0, "empate"


def armar_registro(
    ticker: str,
    *,
    titulares_alpaca,
    alpaca_ok: bool,
    alpaca_motivo: str | None,
    titulares_yahoo,
    yahoo_ok: bool,
    yahoo_motivo: str | None,
    ahora: datetime,
    cfg: MomentumConfig = CONFIG,
    en_slot: bool,
    en_watchlist: bool,
    slot: int | None,
    n_slots: int | None,
    scan_inicio_ts: str | None,
    alpaca_paginas: int | None = None,
    alpaca_truncado: bool = False,
) -> dict:
    hoy = ahora.astimezone(UTC).date()
    if alpaca_ok:
        alpaca = _bloque_fuente(titulares_alpaca, cfg, hoy)
        if alpaca_truncado and alpaca["motivo"] is None:
            alpaca["motivo"] = "paginacion_truncada"
        alpaca["paginas"] = alpaca_paginas
        alpaca["truncado"] = alpaca_truncado
    else:
        alpaca = _bloque_vacio(alpaca_motivo or "no_disponible")
        alpaca["paginas"] = None
        alpaca["truncado"] = None
    if yahoo_ok:
        yahoo = _bloque_fuente(titulares_yahoo, cfg, hoy)
    else:
        yahoo = _bloque_vacio(yahoo_motivo or "no_disponible")
    lag_a = lag_deteccion(alpaca["catalizador_fecha"], ahora) if alpaca_ok else None
    lag_y = lag_deteccion(yahoo["catalizador_fecha"], ahora) if yahoo_ok else None
    entre, primero = lag_entre_fuentes(alpaca["catalizador_fecha"], yahoo["catalizador_fecha"])
    return {
        "tipo": "noticia",
        "ts_deteccion": ahora.isoformat(timespec="seconds"),
        "ticker": ticker,
        "slot": slot,
        "n_slots": n_slots,
        "scan_inicio_ts": scan_inicio_ts,
        "en_slot": en_slot,
        "en_watchlist": en_watchlist,
        "alpaca": alpaca,
        "yahoo": yahoo,
        "lag_min_articulo_a_deteccion": {"alpaca": lag_a, "yahoo": lag_y},
        "lag_min_entre_fuentes": entre,
        "fuente_mas_temprana": primero,
    }


def _lotes(simbolos: list[str], n: int) -> list[list[str]]:
    return [simbolos[i:i + n] for i in range(0, len(simbolos), n)]


def pedir_alpaca(
    cliente: ClienteDatos, simbolos: list[str], inicio: datetime, fin: datetime,
) -> tuple[dict[str, list], set[str], dict[str, str], dict]:
    """`(por_ticker, fallidos, motivos, meta)`.

    Un ticker en `fallidos` no tiene lista: no se le anota cero titulares.
    """
    from momentum_hunter.catalysts.detector import Titular

    por_ticker: dict[str, list] = {s: [] for s in simbolos}
    fallidos: set[str] = set()
    motivos: dict[str, str] = {}
    truncados: set[str] = set()
    paginas_por: dict[str, int] = {s: 0 for s in simbolos}
    huerfanas = 0
    params_base = {
        "limit": LIMITE_PAGINA,
        "start": inicio.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "end": fin.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "sort": "desc",
        "include_content": "false",
    }
    for lote in _lotes(simbolos, LIMITE_PAGINA):
        params = dict(params_base)
        params["symbols"] = ",".join(lote)
        try:
            paginas, truncado = cliente.paginas(RUTA_NOTICIAS, params, MAX_PAGINAS)
        except ErrorDatos as ex:
            for simbolo in lote:
                fallidos.add(simbolo)
                motivos[simbolo] = ex.codigo
                por_ticker.pop(simbolo, None)
            continue
        for simbolo in lote:
            paginas_por[simbolo] = len(paginas)
            if truncado:
                truncados.add(simbolo)
        for cuerpo in paginas:
            try:
                items, _token = extraer_pagina(cuerpo)
            except ErrorDatos as ex:
                for simbolo in lote:
                    fallidos.add(simbolo)
                    motivos[simbolo] = ex.codigo
                    por_ticker.pop(simbolo, None)
                break
            for item in items:
                titular: Titular | None = titular_de_noticia(item)
                if titular is None:
                    continue
                destinos = [s for s in simbolos_de_noticia(item) if s in por_ticker]
                if not destinos:
                    huerfanas += 1
                    continue
                for destino in destinos:
                    por_ticker[destino].append(titular)
    return por_ticker, fallidos, motivos, {
        "huerfanas": huerfanas,
        "truncados": truncados,
        "paginas": paginas_por,
    }


def _yahoo_de(
    ticker: str, proveedor, pausa_activa: bool, limite_en_curso: bool,
) -> tuple[list | None, bool, str | None, bool]:
    """`(titulares o None, ok, motivo, seguir_pidiendo)`.

    `limite_en_curso` es un 429 que ya vimos en esta corrida: no se vuelve
    a pedir, y no se escribe la pausa del bot (esa pausa apaga el escaneo).
    """
    if pausa_activa or limite_en_curso:
        return None, False, "pausa_429", False
    errores = _ErroresYahoo()
    proveedor._metricas = errores
    try:
        titulares = proveedor.titulares(ticker)
    except Exception as ex:  # el proveedor ya atrapa; esto es por si no
        log.warning("yahoo noticias: %s", type(ex).__name__)
        return None, False, type(ex).__name__, not _es_limite(type(ex).__name__)
    if errores.ultimo is not None:
        motivo = "pausa_429" if _es_limite(errores.ultimo) else errores.ultimo
        return None, False, motivo, not _es_limite(errores.ultimo)
    if not isinstance(titulares, list):
        return None, False, "respuesta_ilegible", True
    return titulares, True, None, True


def _normalizar_lista(tickers: list[str] | None) -> list[str]:
    if not tickers:
        return []
    vistos: list[str] = []
    ya: set[str] = set()
    for ticker in tickers:
        if not isinstance(ticker, str):
            continue
        limpio = ticker.strip().upper()
        if not limpio or limpio in ya:
            continue
        ya.add(limpio)
        vistos.append(limpio)
    return vistos


def correr(
    *,
    cliente: ClienteDatos,
    dir_salida_path: Path,
    dir_telemetria: Path,
    watchlist_path: Path,
    ahora: datetime | None = None,
    cargar_simbolos=tickers_universo,
    proveedor_yahoo=None,
    pausa_yahoo: PausaYahoo | None = None,
    cfg: MomentumConfig = CONFIG,
    tickers_explicitos: list[str] | None = None,
) -> int:
    ahora = ahora or datetime.now(UTC)
    dia = ahora.astimezone(UTC).date().isoformat()
    destino = dir_salida_path / dia / "noticias.jsonl"
    pausa = pausa_yahoo if pausa_yahoo is not None else PausaYahoo()
    proveedor = proveedor_yahoo if proveedor_yahoo is not None else YahooNewsProvider()

    evento = None
    slot = None
    n_slots = None
    scan_inicio = None
    slot_tickers: list[str] | None = None
    slot_motivo: str | None = None
    manual = tickers_explicitos is not None
    if not manual:
        evento = ultimo_escaneo(dir_telemetria, dia)
        if isinstance(evento, dict):
            crudo_slot = evento.get("slot")
            crudo_n = evento.get("n_slots")
            # bool es int en Python: True no es un número de slot.
            slot = crudo_slot if isinstance(crudo_slot, int) and not isinstance(crudo_slot, bool) else None
            n_slots = crudo_n if isinstance(crudo_n, int) and not isinstance(crudo_n, bool) else None
            inicio = evento.get("inicio_ts")
            scan_inicio = inicio if isinstance(inicio, str) else None
        simbolos: list[str] | None
        try:
            crudos = cargar_simbolos()
            simbolos = [s.strip().upper() for s in crudos if isinstance(s, str) and s.strip()]
        except Exception as ex:
            log.warning("universo no disponible (%s)", type(ex).__name__)
            simbolos = None
        slot_tickers, slot_motivo = tickers_del_slot(evento, simbolos)
    else:
        slot_tickers = _normalizar_lista(tickers_explicitos)
        slot_motivo = None

    wl_tickers, wl_motivo = tickers_de_watchlist(watchlist_path)
    if manual:
        # Una sonda manual no arrastra la watchlist ni se anota como si
        # esos símbolos hubieran salido del slot del hunter.
        wl_tickers = []
        wl_motivo = None

    if slot_tickers is None and wl_tickers is None:
        append_linea(destino, {
            "tipo": "noticia_corrida",
            "ts_deteccion": ahora.isoformat(timespec="seconds"),
            "tickers": None,
            "motivo_slot": slot_motivo,
            "motivo_watchlist": wl_motivo,
            "alpaca_disponible": False,
        })
        log.warning("noticias: sin universo ni watchlist (%s / %s)", slot_motivo, wl_motivo)
        return 2

    if manual:
        conjunto_slot = set()
        conjunto_wl = set()
        orden = list(slot_tickers or [])
    else:
        conjunto_slot = set(slot_tickers or [])
        conjunto_wl = set(wl_tickers or [])
        orden = list(slot_tickers or []) + [t for t in (wl_tickers or []) if t not in conjunto_slot]
    if not orden:
        append_linea(destino, {
            "tipo": "noticia_corrida",
            "ts_deteccion": ahora.isoformat(timespec="seconds"),
            "tickers": 0,
            "motivo_slot": slot_motivo,
            "motivo_watchlist": wl_motivo,
            "alpaca_disponible": False,
        })
        return 0

    inicio = ahora - timedelta(days=cfg.dias_ventana_catalizador + MARGEN_DIAS)
    try:
        por_ticker, fallidos, motivos, meta = pedir_alpaca(cliente, orden, inicio, ahora)
    except ErrorDatos as ex:
        append_linea(destino, {
            "tipo": "noticia_corrida",
            "ts_deteccion": ahora.isoformat(timespec="seconds"),
            "tickers": None,
            "motivo": ex.codigo,
            "motivo_slot": slot_motivo,
            "motivo_watchlist": wl_motivo,
            "alpaca_disponible": False,
        })
        log.warning("noticias: Alpaca no respondió (%s)", ex.codigo)
        return 2

    if fallidos == set(orden) and all(motivos.get(t) in {"auth", "sin_credenciales"} for t in orden):
        # Sin feed no se piden tampoco las noticias de Yahoo: serían
        # cientos de llamadas para una comparación que no se puede cerrar.
        append_linea(destino, {
            "tipo": "noticia_corrida",
            "ts_deteccion": ahora.isoformat(timespec="seconds"),
            "tickers": None,
            "motivo": motivos.get(orden[0], "auth"),
            "motivo_slot": slot_motivo,
            "motivo_watchlist": wl_motivo,
            "alpaca_disponible": False,
        })
        log.warning("noticias: sin acceso al feed (%s)", motivos.get(orden[0]))
        return 2

    pausa_activa = pausa.activa(ahora)
    limite_en_curso = False
    for ticker in orden:
        if ticker in fallidos:
            alpaca_ok = False
            alpaca_motivo = motivos.get(ticker, "no_disponible")
            titulares_a: list = []
        else:
            alpaca_ok = True
            alpaca_motivo = None
            titulares_a = por_ticker.get(ticker, [])
        titulares_y, yahoo_ok, yahoo_motivo, seguir = _yahoo_de(
            ticker, proveedor, pausa_activa, limite_en_curso,
        )
        if not seguir:
            limite_en_curso = True
        registro = armar_registro(
            ticker,
            titulares_alpaca=titulares_a,
            alpaca_ok=alpaca_ok,
            alpaca_motivo=alpaca_motivo,
            titulares_yahoo=titulares_y or [],
            yahoo_ok=yahoo_ok,
            yahoo_motivo=yahoo_motivo,
            ahora=ahora,
            cfg=cfg,
            en_slot=ticker in conjunto_slot,
            en_watchlist=ticker in conjunto_wl,
            slot=slot if isinstance(slot, int) else None,
            n_slots=n_slots if isinstance(n_slots, int) else None,
            scan_inicio_ts=scan_inicio,
            alpaca_paginas=meta["paginas"].get(ticker),
            alpaca_truncado=ticker in meta["truncados"],
        )
        append_linea(destino, registro)
    append_linea(destino, {
        "tipo": "noticia_corrida",
        "ts_deteccion": ahora.isoformat(timespec="seconds"),
        "tickers": len(orden),
        "motivo_slot": slot_motivo,
        "motivo_watchlist": wl_motivo,
        "noticias_sin_simbolo": meta["huerfanas"],
        "yahoo_en_pausa": pausa_activa or limite_en_curso,
        "alpaca_disponible": True,
    })
    log.info("noticias sombra: %d tickers, huerfanas=%s, pausa_yahoo=%s",
             len(orden), meta["huerfanas"], pausa_activa or limite_en_curso)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sombra B1: noticias Alpaca y Yahoo, el detector del hunter, solo JSONL.",
    )
    ap.add_argument("--salida", type=Path, default=None, help="directorio raíz de la sombra")
    ap.add_argument("--telemetria", type=Path, default=None)
    ap.add_argument("--watchlist", type=Path, default=None)
    ap.add_argument("--tickers", type=str, default=None,
                    help="lista separada por comas; no lee el slot ni la watchlist")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from momentum_hunter.telemetria import DIR_TELEMETRIA
    from momentum_hunter.watchlist import PATH as WATCHLIST_PATH

    explicitos = None
    if args.tickers:
        explicitos = [p for p in args.tickers.split(",")]
    try:
        cliente = ClienteDatos()
        cliente._credenciales()
    except ErrorDatos as ex:
        log.warning("noticias: %s", ex.codigo)
        return 2
    return correr(
        cliente=cliente,
        dir_salida_path=dir_salida(args.salida),
        dir_telemetria=args.telemetria or DIR_TELEMETRIA,
        watchlist_path=args.watchlist or WATCHLIST_PATH,
        tickers_explicitos=explicitos,
    )


if __name__ == "__main__":
    raise SystemExit(main())
