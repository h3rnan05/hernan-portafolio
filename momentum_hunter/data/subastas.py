"""Precio OFICIAL de apertura y de cierre, leído de las subastas SIP
(`GET /v2/stocks/auctions`, host de datos, solo lectura).

POR QUÉ EXISTE. El gap del hunter se medía con dos aproximaciones: el
`open` de la primera vela regular de 1 minuto y el `close` de la barra
diaria anterior. Ninguna es el número oficial: la apertura oficial la
fija la subasta de las 9:30 ET y el cierre oficial la de las 16:00 ET.
En un gapper se separan: la primera vela puede abrir con un print suelto
antes del cruce.

UN CRUCE POR LADO, NUNCA UNA SUMA. Cada día trae prints de apertura
('O' = print del cruce, 'Q' = apertura oficial del mercado) y de cierre
('6' = print del cruce, 'M' = cierre oficial), a veces de varios
exchanges, y el mismo cruce aparece dos veces ('O' y 'Q' del mismo
sitio). Este módulo no suma volúmenes ni pliega nada en las velas: elige
UN precio por lado y lo usa solo para el gap y el reporte. Así no puede
contar la subasta dos veces (el error que tenía #188 al plegarla en el
volumen).

CUÁL ES EL OFICIAL. El de la bolsa de listado, que es la subasta que
concentra el volumen:
  1. el print de cruce ('O' / '6') con más acciones marca el exchange;
  2. el precio es el oficial ('Q' / 'M') de ese exchange si vino, y si
     no, el del propio print de cruce;
  3. si ningún print trae size y los oficiales de distintos exchanges no
     coinciden, no hay forma honesta de elegir: None.
Nunca se inventa el precio ni el volumen (un size ausente no es 0).

SIN DATO NO HAY GAP OFICIAL. Si falta la apertura de hoy o el cierre de
la sesión anterior, `gap_oficial` devuelve None y el caller se queda con
el gap de siempre (que es un dato real). La sesión anterior es la ÚLTIMA
fecha con subastas antes de hoy; si esa fecha no trae cierre, no se salta
a una más vieja: sería el gap de otro día.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from momentum_hunter.data.alpaca_datos import (
    LIMITE_PAGINA,
    AlpacaProvider,
    ErrorDatosAlpaca,
    _momento,
    _numero,
    _pares,
)

log = logging.getLogger("momentum_hunter.data.subastas")

RUTA = "/v2/stocks/auctions"
# Cinco días de calendario: cubren un fin de semana y dejan la sesión
# anterior dentro de la ventana, igual que el `periodo="5d"` de las velas.
DIAS_VENTANA = 5
LOTE = 50
# (condición del print del cruce, condición del precio oficial)
APERTURA = ("O", "Q")
CIERRE = ("6", "M")
# Dos oficiales sin size de exchanges distintos son "el mismo" solo si
# coinciden al centavo. Es igualdad, no un umbral.
TOLERANCIA_MISMO_PRECIO = 0.005


@dataclass(frozen=True)
class SubastaDia:
    """Un día de un símbolo. Cualquier campo puede ser None: faltó o no
    se pudo elegir sin adivinar. None no es cero."""

    fecha: str
    apertura: float | None
    vol_apertura: float | None
    cierre: float | None
    vol_cierre: float | None


def _prints(crudos: object) -> list[dict]:
    """Prints legibles. Uno sin hora, precio o condición se descarta;
    uno sin size se conserva con size None (no 0)."""
    if not isinstance(crudos, list):
        return []
    out = []
    for p in crudos:
        if not isinstance(p, dict):
            continue
        t, precio, cond = _momento(p.get("t")), _numero(p.get("p")), p.get("c")
        if t is None or precio is None or precio <= 0 or not isinstance(cond, str) or not cond.strip():
            continue
        size = _numero(p.get("s")) if p.get("s") is not None else None
        x = p.get("x") if isinstance(p.get("x"), str) else ""
        out.append({"t": t, "p": precio, "s": size if size and size > 0 else None,
                    "c": cond.strip().upper(), "x": x})
    return out


def oficial_de_lado(prints: list[dict], condiciones: tuple[str, str]) -> tuple[float | None, float | None]:
    """(precio oficial, acciones del cruce) de un lado. Ver docstring."""
    cond_cruce, cond_oficial = condiciones
    cruces = [p for p in prints if p["c"] == cond_cruce and p["s"] is not None]
    oficiales = [p for p in prints if p["c"] == cond_oficial]
    if cruces:
        mayor = max(cruces, key=lambda p: (p["s"], -p["t"].timestamp()))
        del_mismo_sitio = [p for p in oficiales if p["x"] == mayor["x"]]
        precio = del_mismo_sitio[0]["p"] if del_mismo_sitio else mayor["p"]
        return precio, mayor["s"]
    con_size = [p for p in oficiales if p["s"] is not None]
    if con_size:
        mayor = max(con_size, key=lambda p: (p["s"], -p["t"].timestamp()))
        return mayor["p"], mayor["s"]
    if oficiales:
        precios = [p["p"] for p in oficiales]
        if max(precios) - min(precios) <= TOLERANCIA_MISMO_PRECIO:
            return precios[0], None
    return None, None


def oficiales_por_dia(dias_crudos: list) -> dict[str, SubastaDia]:
    """Días crudos del endpoint -> fecha -> `SubastaDia`. Un día repetido
    en dos páginas se junta antes de elegir: sigue siendo UN cruce."""
    por_dia: dict[str, dict[str, list]] = {}
    for dia in dias_crudos or []:
        if not isinstance(dia, dict):
            continue
        fecha = dia.get("d")
        if not isinstance(fecha, str) or len(fecha) < 10:
            continue
        slot = por_dia.setdefault(fecha[:10], {"o": [], "c": []})
        slot["o"].extend(_prints(dia.get("o")))
        slot["c"].extend(_prints(dia.get("c")))
    out: dict[str, SubastaDia] = {}
    for fecha, slot in por_dia.items():
        ap, vol_ap = oficial_de_lado(slot["o"], APERTURA)
        ci, vol_ci = oficial_de_lado(slot["c"], CIERRE)
        out[fecha] = SubastaDia(fecha, ap, vol_ap, ci, vol_ci)
    return out


def gap_oficial(dias: dict[str, SubastaDia], hoy: str) -> float | None:
    """(apertura oficial de hoy - cierre oficial de la sesión anterior)
    / cierre oficial de la sesión anterior. None si falta cualquiera."""
    hoy_d = dias.get(hoy)
    if hoy_d is None or hoy_d.apertura is None:
        return None
    anteriores = sorted(f for f in dias if f < hoy)
    if not anteriores:
        return None
    previo = dias[anteriores[-1]]
    if previo.cierre is None or previo.cierre <= 0:
        return None
    return (hoy_d.apertura - previo.cierre) / previo.cierre


def _dias_crudos(transporte: AlpacaProvider, tickers: list[str], inicio: datetime, fin: datetime) -> dict:
    """Ticker pedido -> días crudos. Mismo mapeo de símbolos que las
    velas (`_pares`: `BRK-B` se pide como `BRK.B`). Un lote que falla se
    omite y se registra: ese ticker queda sin dato, no sin subasta."""
    pares, _directos = _pares(tickers)
    out: dict[str, list] = {}
    for i in range(0, len(pares), LOTE):
        lote = pares[i:i + LOTE]
        try:
            paginas = transporte._paginas(RUTA, {
                "symbols": ",".join(q for q, _ in lote),
                "start": inicio.isoformat(timespec="seconds"),
                "end": fin.isoformat(timespec="seconds"),
                "limit": LIMITE_PAGINA,
                "feed": "sip",
                "sort": "asc",
            })
        except ErrorDatosAlpaca as ex:
            log.warning("subastas: lote de %d símbolos no disponible (%s)", len(lote), ex.codigo)
            continue
        pedidos_de = dict(lote)
        for cuerpo in paginas:
            auctions = cuerpo.get("auctions") if isinstance(cuerpo, dict) else None
            if not isinstance(auctions, dict):
                continue
            for simbolo, dias in auctions.items():
                if not isinstance(dias, list):
                    continue
                for pedido in pedidos_de.get(str(simbolo).upper(), []):
                    out.setdefault(pedido, []).extend(dias)
    return out


def descargar(
    tickers: list[str], ahora: datetime | None = None, dias: int = DIAS_VENTANA,
    transporte: AlpacaProvider | None = None,
) -> dict[str, dict[str, SubastaDia]]:
    """Ticker pedido -> fecha -> `SubastaDia`. Nunca levanta."""
    ahora = ahora or datetime.now(UTC)
    nombres = [t for t in tickers if isinstance(t, str) and t.strip()]
    if not nombres:
        return {}
    try:
        # Subastas oficiales solo existen en SIP: sin respaldo IEX.
        transporte = transporte or AlpacaProvider(feed="sip", respaldo_iex=False)
        crudos = _dias_crudos(transporte, nombres, ahora - timedelta(days=dias), ahora)
    except Exception as ex:   # noqa: BLE001 -- es un extra; se registra el TIPO
        log.warning("subastas: no disponibles (%s) -- se usa el gap de las velas", type(ex).__name__)
        return {}
    return {t: oficiales_por_dia(d) for t, d in crudos.items()}


def gaps_oficiales(
    tickers: list[str], hoy: str, ahora: datetime | None = None,
    transporte: AlpacaProvider | None = None,
) -> dict[str, float]:
    """Solo los tickers con gap oficial calculable. Los demás no
    aparecen: el caller conserva su gap de velas para ellos."""
    out: dict[str, float] = {}
    for t, dias in descargar(tickers, ahora, transporte=transporte).items():
        g = gap_oficial(dias, hoy)
        if g is not None:
            out[t] = g
    return out
