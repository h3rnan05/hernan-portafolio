"""Form 4 (operaciones de insiders) como columnas del backtest v2.

El índice de submissions dice QUE hubo un Form 4 y cuándo se aceptó,
pero no si fue compra o venta. Eso está en el XML de cada presentación:
`https://www.sec.gov/Archives/edgar/data/<cik>/<accession sin guiones>/<primaryDocument>`
(el `primaryDocument` de un Form 4 suele venir como `xslF345X05/doc.xml`;
el XML crudo es el mismo nombre sin el prefijo `xsl.../`).

Qué se lee del XML (esquema público `ownershipDocument`):
    nonDerivativeTable/nonDerivativeTransaction/
        transactionCoding/transactionCode   P compra en mercado, S venta
        transactionAmounts/transactionShares/value
        transactionAmounts/transactionAcquiredDisposedCode/value   A | D
Solo cuentan P y S: premios (A), ejercicios (M), impuestos (F) o regalos
(G) no dicen nada de la convicción del insider. Una transacción sin
cantidad legible no se suma a cero: invalida la presentación y la
columna queda FALTANTE.

COLUMNAS (Form 4 aceptados en los 30 días anteriores al instante):
    form4_cantidad_30d      cuántos Form 4 | FALTANTE
    form4_compras_acciones  acciones compradas (P) | FALTANTE
    form4_ventas_acciones   acciones vendidas (S) | FALTANTE
    form4_neto              "compra" | "venta" | "mixto" | None (sin P/S) | FALTANTE
    form4_ultimo_horas      horas desde el último Form 4 | None | FALTANTE
Con 0 Form 4 en la ventana las cantidades son 0 y el neto None: eso es
"no hubo", afirmado con el índice completo. FALTANTE: sin índice, XML
caído o ilegible, aceptación ilegible en la ventana.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta

from fuentes.cache import Cache
from fuentes.columnas import todas_faltantes
from fuentes.comun import ErrorFuente, numero
from fuentes.edgar import LectorEdgar, Presentacion

log = logging.getLogger("fuentes.edgar_form4")

URL_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/"
DIAS_VENTANA = 30
EDAD_XML_S = None   # un Form 4 presentado no cambia: la caché no expira


@dataclass(frozen=True)
class Operaciones:
    compras: float
    ventas: float


def url_xml(cik: int, p: Presentacion) -> str | None:
    if not p.accession or not p.documento:
        return None
    doc = p.documento.split("/")[-1]
    if not doc.lower().endswith(".xml"):
        return None
    return f"{URL_ARCHIVES}{cik}/{p.accession.replace('-', '')}/{doc}"


def _texto(nodo: ET.Element | None, ruta: str) -> str | None:
    if nodo is None:
        return None
    hijo = nodo.find(ruta)
    return hijo.text.strip() if hijo is not None and hijo.text else None


def leer_form4(xml: str) -> Operaciones:
    """Suma P y S del XML. Levanta ErrorFuente si no es un
    ownershipDocument o una transacción P/S no trae cantidad."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError:
        raise ErrorFuente("xml_ilegible", "edgar_form4") from None
    if raiz.tag != "ownershipDocument":
        raise ErrorFuente("xml_ilegible", "edgar_form4")
    compras = ventas = 0.0
    for tx in raiz.findall("./nonDerivativeTable/nonDerivativeTransaction"):
        codigo = _texto(tx, "./transactionCoding/transactionCode")
        if codigo not in ("P", "S"):
            continue
        cantidad = numero(_num(_texto(tx, "./transactionAmounts/transactionShares/value")))
        if cantidad is None or cantidad < 0:
            raise ErrorFuente("cantidad_ilegible", "edgar_form4")
        if codigo == "P":
            compras += cantidad
        else:
            ventas += cantidad
    return Operaciones(compras, ventas)


def _num(texto: str | None) -> float | None:
    if texto is None:
        return None
    try:
        return float(texto.replace(",", ""))
    except ValueError:
        return None


class EdgarForm4:
    nombre = "edgar_form4"
    _NOMBRES = ["form4_cantidad_30d", "form4_compras_acciones", "form4_ventas_acciones", "form4_neto",
                "form4_ultimo_horas"]

    def __init__(self, lector: LectorEdgar | None = None, cache: Cache | None = None) -> None:
        self.lector = lector or LectorEdgar()
        self.cache = cache or self.lector.cache

    def nombres(self) -> list[str]:
        return list(self._NOMBRES)

    def operaciones(self, cik: int, p: Presentacion) -> Operaciones:
        url = url_xml(cik, p)
        if url is None:
            raise ErrorFuente("sin_documento", "edgar_form4")
        xml = self.cache.obtener("edgar_form4", url, lambda: self.lector.cliente().get(url).texto, EDAD_XML_S)
        return leer_form4(xml)

    def columnas(self, ticker: str, momento: datetime) -> dict:
        pres = self.lector.presentaciones(ticker)
        if pres is None:
            return todas_faltantes(self._NOMBRES)
        cik = self.lector.cik(ticker)
        desde = momento - timedelta(days=DIAS_VENTANA)
        en_ventana: list[Presentacion] = []
        for p in pres:
            if p.form.upper() not in ("4", "4/A"):
                continue
            if p.aceptada is None:
                if desde.date() - timedelta(days=1) <= p.fecha <= momento.date():
                    log.warning("edgar_form4: aceptación ilegible en la ventana (%s)", ticker)
                    return todas_faltantes(self._NOMBRES)
                continue
            if desde < p.aceptada <= momento:
                en_ventana.append(p)
        if not en_ventana:
            return {"form4_cantidad_30d": 0, "form4_compras_acciones": 0.0, "form4_ventas_acciones": 0.0,
                    "form4_neto": None, "form4_ultimo_horas": None}
        compras = ventas = 0.0
        for p in en_ventana:
            try:
                ops = self.operaciones(cik, p)
            except ErrorFuente as ex:
                log.warning("edgar_form4: %s (%s %s)", ex.codigo, ticker, p.accession)
                return todas_faltantes(self._NOMBRES)
            compras += ops.compras
            ventas += ops.ventas
        if compras and ventas:
            neto = "mixto"
        elif compras:
            neto = "compra"
        elif ventas:
            neto = "venta"
        else:
            neto = None
        ultimo = max(p.aceptada for p in en_ventana)
        return {"form4_cantidad_30d": len(en_ventana), "form4_compras_acciones": compras,
                "form4_ventas_acciones": ventas, "form4_neto": neto,
                "form4_ultimo_horas": round((momento - ultimo).total_seconds() / 3600.0, 2)}


# --------------------------------------------------------------- grabar


def _grabar(argv: list[str]) -> int:
    """`python -m fuentes grabar edgar_form4 TICKER`: guarda el XML del
    último Form 4 real del emisor e imprime lo que se leyó de él."""
    import argparse
    import os
    from pathlib import Path

    from fuentes.grabar import guardar

    ap = argparse.ArgumentParser(prog="python -m fuentes grabar edgar_form4")
    ap.add_argument("ticker")
    ap.add_argument("--dir", type=Path, default=None)
    args = ap.parse_args(argv)
    lector = LectorEdgar(Cache(Path(os.environ.get("TMPDIR", "/tmp")) / "fuentes_grabar"))
    pres = lector.presentaciones(args.ticker)
    if pres is None:
        print(f"{args.ticker}: sin presentaciones (¿User-Agent, CIK?)")
        return 1
    cuatro = [p for p in pres if p.form.upper() in ("4", "4/A") and url_xml(lector.cik(args.ticker), p)]
    if not cuatro:
        print(f"{args.ticker}: no tiene Form 4 con XML")
        return 1
    p = cuatro[0]
    url = url_xml(lector.cik(args.ticker), p)
    r = lector.cliente().get(url)
    guardar("edgar", f"form4_{args.ticker.upper()}_{p.accession}", url, None, r.status, r.headers, r.texto,
            ficticio=False, directorio=args.dir)
    ops = leer_form4(r.texto)
    print(f"Form 4 {p.fecha} {p.accession}: compras P={ops.compras:.0f} ventas S={ops.ventas:.0f}")
    return 0


from fuentes import __main__ as _cli  # noqa: E402  (registro al final, como en edgar.py)

_cli.registrar("edgar_form4", _grabar)
