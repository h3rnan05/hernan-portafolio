"""Datos históricos para el backtest v2, con caché en disco.

Todo sale del host de DATOS de Alpaca (SIP) salvo el float (Yahoo,
actual). Cada pedido se guarda en `<almacen>/v2/...` (fuera del repo):
una segunda corrida no vuelve a pedir lo mismo.

Reutiliza, sin copiar:
  - `shadow_alpaca.cliente.ClienteDatos` (#196) para barras, noticias y quotes;
  - `momentum_hunter.data.subastas.descargar` (#202) para la apertura y el
    cierre oficiales;
  - `momentum_hunter.data.acciones_corporativas.ClienteAcciones` (#201).

Un pedido que falla levanta `ErrorDatos`: el motor lo cuenta como "sin
dato" para ese símbolo-día; nunca se convierte en cero.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from estrategia_v2.reglas import Vela

from shadow_alpaca.cliente import ClienteDatos, ErrorDatos
from shadow_alpaca.jsonl_log import exigir_directorio_aislado
from shadow_alpaca.numeros import numero

log = logging.getLogger("shadow_alpaca.backtest_v2.datos")

NY = ZoneInfo("America/New_York")
FEED = "sip"
AJUSTE_DIARIO = "split"   # igual que el hunter; el gap real sale de las subastas (crudas)
LOTE_BARRAS = 100
LOTE_NOTICIAS = 50
LIMITE_BARRAS = 10_000
LIMITE_NOTICIAS = 50
MAX_PAGINAS = 500


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def en_ny(d: date, h: time) -> datetime:
    return datetime.combine(d, h, tzinfo=NY).astimezone(UTC)


class Cache:
    def __init__(self, almacen: Path) -> None:
        self.dir = exigir_directorio_aislado(almacen) / "v2"

    def _ruta(self, tipo: str, clave: str) -> Path:
        h = hashlib.sha1(clave.encode()).hexdigest()[:16]
        return self.dir / tipo / f"{h}.json.gz"

    def obtener(self, tipo: str, clave: str, calcular):
        ruta = self._ruta(tipo, clave)
        if ruta.exists():
            try:
                with gzip.open(ruta, "rt", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                pass
        valor = calcular()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(".tmp")
        with gzip.open(temporal, "wt", encoding="utf-8") as fh:
            json.dump(valor, fh)
        temporal.replace(ruta)
        return valor


def _lotes(xs: list[str], n: int) -> list[list[str]]:
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def _query(ticker: str) -> str:
    """El feed pide la clase con punto (`BRK.B`); el universo la trae con guion."""
    return ticker.replace("-", ".")


# ------------------------------------------------------------------ barras


def _barras(cliente: ClienteDatos, simbolos: list[str], timeframe: str, inicio: datetime, fin: datetime,
            ajuste: str) -> dict[str, list[list]]:
    """símbolo pedido -> [[t_iso, o, h, l, c, v], ...]. Una vela a la que
    le falta OHLCV se descarta (no se rellena)."""
    pedido_de = {_query(s): s for s in simbolos}
    params = {"symbols": ",".join(pedido_de), "timeframe": timeframe, "start": _iso(inicio),
              "end": _iso(fin), "limit": LIMITE_BARRAS, "adjustment": ajuste, "feed": FEED, "sort": "asc"}
    paginas, truncado = cliente.paginas("/v2/stocks/bars", params, MAX_PAGINAS)
    if truncado:
        raise ErrorDatos("paginacion")
    out: dict[str, list[list]] = {}
    for cuerpo in paginas:
        bars = cuerpo.get("bars") if isinstance(cuerpo, dict) else None
        if bars is None:
            continue
        if not isinstance(bars, dict):
            raise ErrorDatos("bars_ilegible")
        for sim, serie in bars.items():
            if sim not in pedido_de or not isinstance(serie, list):
                continue
            for b in serie:
                if not isinstance(b, dict):
                    continue
                vals = [numero(b.get(k)) for k in ("o", "h", "l", "c", "v")]
                if not isinstance(b.get("t"), str) or None in vals:
                    continue
                out.setdefault(pedido_de[sim], []).append([b["t"], *vals])
    return out


def a_velas(filas: list[list]) -> list[Vela]:
    velas = []
    for t, o, h, lo, c, v in filas:
        velas.append(Vela(datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(UTC), o, h, lo, c, v))
    velas.sort(key=lambda x: x.t)
    return velas


def diarias(cliente: ClienteDatos, cache: Cache, simbolos: list[str], desde: date, hasta: date
            ) -> dict[str, list[list]]:
    out: dict[str, list[list]] = {}
    lotes = _lotes(sorted(set(simbolos)), LOTE_BARRAS)
    for k, lote in enumerate(lotes):
        clave = f"1Day|{desde}|{hasta}|{','.join(lote)}"
        datos = cache.obtener("diarias", clave, lambda lote=lote: _barras(
            cliente, lote, "1Day", en_ny(desde, time(0)), en_ny(hasta + timedelta(days=1), time(0)),
            AJUSTE_DIARIO))
        out.update(datos)
        if (k + 1) % 10 == 0:
            log.info("diarias: %d/%d lotes", k + 1, len(lotes))
    return out


def minutos(cliente: ClienteDatos, cache: Cache, simbolos: list[str], dia: date, desde: time, hasta: time
            ) -> dict[str, list[list]]:
    """Velas de 1 min de `dia` entre dos horas de NY, para varios símbolos.
    Sin ajuste: el rango del día y la subasta son del mismo día."""
    out: dict[str, list[list]] = {}
    for lote in _lotes(sorted(set(simbolos)), LOTE_BARRAS):
        clave = f"1Min|{dia}|{desde}|{hasta}|{','.join(lote)}"
        out.update(cache.obtener("minutos", clave, lambda lote=lote: _barras(
            cliente, lote, "1Min", en_ny(dia, desde), en_ny(dia, hasta), "raw")))
    return out


# ----------------------------------------------------------------- subastas


def subastas_del_dia(cache: Cache, simbolos: list[str], dia: date) -> dict[str, dict]:
    """símbolo -> {fecha: {apertura, cierre}} de la semana hasta `dia` (#202)."""
    from momentum_hunter.data import subastas as sub

    clave = f"subastas|{dia}|{','.join(sorted(set(simbolos)))}"

    def _calc():
        crudos = sub.descargar(simbolos, ahora=en_ny(dia, time(12, 0)), dias=7)
        return {t: {f: {"apertura": d.apertura, "cierre": d.cierre} for f, d in dias.items()}
                for t, dias in crudos.items()}
    return cache.obtener("subastas", clave, _calc)


def gap_oficial(por_fecha: dict[str, dict], dia: date) -> float | None:
    from momentum_hunter.data.subastas import SubastaDia, gap_oficial as calc

    dias = {f: SubastaDia(f, v.get("apertura"), None, v.get("cierre"), None) for f, v in por_fecha.items()}
    return calc(dias, dia.isoformat())


# ------------------------------------------------------ acciones corporativas


def acciones_corporativas(cache: Cache, simbolos: list[str], desde: date, hasta: date) -> set[tuple[str, str]]:
    """{(símbolo, fecha efectiva)} del período (#201). Si falla, levanta:
    sin saber, no se puede decir "no hubo"."""
    from momentum_hunter.data.acciones_corporativas import ClienteAcciones, ErrorDatosAlpaca, normalizar

    out: set[tuple[str, str]] = set()
    for lote in _lotes(sorted(set(simbolos)), LOTE_BARRAS):
        clave = f"acciones|{desde}|{hasta}|{','.join(lote)}"

        def _calc(lote=lote):
            try:
                acciones, _ = ClienteAcciones().pedir(desde, hasta, simbolos=lote)
            except ErrorDatosAlpaca as ex:
                raise ErrorDatos(ex.codigo) from None
            return [[s, a.fecha_efectiva().isoformat()] for a in acciones if a.fecha_efectiva()
                    for s in a.simbolos]
        for s, f in cache.obtener("acciones", clave, _calc):
            out.add((normalizar(s), f))
    return out


# ------------------------------------------------------------------ noticias


@dataclass(frozen=True)
class Noticia:
    id: str
    titular: str
    resumen: str
    creada: datetime
    simbolos: tuple[str, ...]


def noticias(cliente: ClienteDatos, cache: Cache, simbolos: list[str], inicio: datetime, fin: datetime
             ) -> list[Noticia]:
    """Benzinga (`/v1beta1/news`) con titular + resumen. Usa el mismo
    parseo de página que la sombra de noticias (#196)."""
    from shadow_alpaca.noticias import extraer_pagina, simbolos_de_noticia

    filas: list[list] = []
    for lote in _lotes(sorted({_query(s) for s in simbolos}), LOTE_NOTICIAS):
        clave = f"news|{_iso(inicio)}|{_iso(fin)}|{','.join(lote)}"

        def _calc(lote=lote):
            params = {"symbols": ",".join(lote), "start": _iso(inicio), "end": _iso(fin),
                      "limit": LIMITE_NOTICIAS, "sort": "asc", "include_content": "false"}
            paginas, truncado = cliente.paginas("/v1beta1/news", params, MAX_PAGINAS)
            if truncado:
                raise ErrorDatos("paginacion")
            res = []
            for cuerpo in paginas:
                items, _ = extraer_pagina(cuerpo)
                for it in items:
                    if not isinstance(it, dict) or not isinstance(it.get("headline"), str):
                        continue
                    if not isinstance(it.get("created_at"), str):
                        continue   # sin hora no se puede ubicar antes o después de la entrada
                    res.append([str(it.get("id")), it["headline"], it.get("summary") or "",
                                it["created_at"], simbolos_de_noticia(it)])
            return res
        filas.extend(cache.obtener("noticias", clave, _calc))
    vistas, out = set(), []
    for nid, tit, res, creada, syms in filas:
        if nid in vistas:
            continue
        vistas.add(nid)
        out.append(Noticia(nid, tit, res, datetime.fromisoformat(creada.replace("Z", "+00:00")),
                           tuple(s.replace(".", "-") for s in syms)))
    return out


# -------------------------------------------------------------------- quotes


def spread_pct(cliente: ClienteDatos, cache: Cache, simbolo: str, momento: datetime) -> float | None:
    """(ask − bid) / punto medio de la última quote SIP antes de `momento`.
    Sin quote en el minuto previo, o con bid/ask imposibles: None."""
    clave = f"quote|{simbolo}|{_iso(momento)}"

    def _calc():
        params = {"symbols": _query(simbolo), "start": _iso(momento - timedelta(minutes=1)),
                  "end": _iso(momento), "limit": 1, "sort": "desc", "feed": FEED}
        cuerpo = cliente.get("/v2/stocks/quotes", params)
        quotes = (cuerpo.get("quotes") or {}).get(_query(simbolo)) or []
        if not quotes:
            return None
        q = quotes[0]
        return [numero(q.get("bp")), numero(q.get("ap"))]
    par = cache.obtener("quotes", clave, _calc)
    if not par or None in par:
        return None
    bid, ask = par
    if bid <= 0 or ask < bid:
        return None
    return (ask - bid) / ((ask + bid) / 2)


# --------------------------------------------------------------------- float


def metadata(cache: Cache, simbolos: list[str], proveedor=None) -> dict[str, dict]:
    """Float y marcas ETF/SPAC ACTUALES de Yahoo (no las de cada fecha).
    Un símbolo sin metadata no aparece: el motor lo excluye."""
    if proveedor is None:
        from momentum_hunter.data.provider import YahooProvider
        proveedor = YahooProvider()
    out: dict[str, dict] = {}
    faltan = []
    for s in sorted(set(simbolos)):
        ruta = cache._ruta("metadata", s)
        if ruta.exists():
            with gzip.open(ruta, "rt", encoding="utf-8") as fh:
                dato = json.load(fh)
            if dato:
                out[s] = dato
        else:
            faltan.append(s)
    for lote in _lotes(faltan, LOTE_BARRAS):
        try:
            metas = proveedor.metadata(lote)
        except Exception as ex:   # noqa: BLE001 -- sin dato, no se inventa
            log.warning("metadata: lote falló (%s)", type(ex).__name__)
            continue
        for s in lote:
            m = metas.get(s)
            if m is None or (m.nombre is None and m.shares_float is None):
                # Yahoo no contestó (429, red): `_metadata_una` devuelve un
                # Metadata vacío que no se distingue de "no tiene float". No se
                # guarda, así la próxima corrida lo reintenta. Para esta
                # corrida, sin dato = excluido.
                continue
            dato = {"float": m.shares_float, "es_etf": m.es_etf, "es_spac": m.es_spac}
            cache.obtener("metadata", s, lambda dato=dato: dato)
            out[s] = dato
    return out
