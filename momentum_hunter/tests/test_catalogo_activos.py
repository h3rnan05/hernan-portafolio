"""El hunter solo lee el JSON local. Sin archivo, viejo o de otro día:
el dato es None y no se filtra. Con archivo fresco: tradable, exchange
y la forma BRK-B / BRK.B."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime, timedelta

from momentum_hunter import catalogo_activos as cat
from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Barras, Metadata
from momentum_hunter.run import construir_candidatos_diarios

AHORA = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
CFG = MomentumConfig()


def _escribir(path, filas, fecha=None):
    path.write_text(json.dumps({
        "fecha_generacion": (fecha if fecha is not None else AHORA).isoformat(timespec="seconds"),
        "assets": filas,
    }), encoding="utf-8")
    return path


def _fila(symbol, **extra):
    base = {
        "symbol": symbol,
        "exchange": "NASDAQ",
        "tradable": True,
        "fractionable": False,
        "status": "active",
        "name": f"{symbol} Inc",
    }
    base.update(extra)
    return base


def _barras(ticker: str) -> Barras:
    n = 25
    fechas = [str(1_700_000_000 + i * 86_400) for i in range(n)]
    closes = [5.0] * n
    return Barras(ticker, fechas, closes, closes, closes, closes, [500_000.0] * n)


class _Meta(DataProvider):
    def __init__(self, metadata):
        self._metadata = metadata

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {t: self._metadata[t] for t in tickers if t in self._metadata}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


def test_el_modulo_no_llama_a_ningun_endpoint():
    fuente = inspect.getsource(cat)
    for palabra in ("requests", "paper-api", "api.alpaca.markets", "ALPACA_PAPER", "urllib"):
        assert palabra not in fuente
    assert cat.EDAD_MAXIMA == timedelta(hours=36)


def test_archivo_ausente_es_desconocido_y_no_filtra(tmp_path):
    path = tmp_path / "no_esta.json"
    ficha = cat.consultar("AAA", path=path, ahora=AHORA)
    assert ficha.catalogo_fresco is False
    assert ficha.tradable is None
    assert ficha.exchange is None
    assert ficha.en_catalogo is False
    informe = cat.filtrar_por_catalogo(["AAA", "HALT"], path=path, ahora=AHORA)
    assert informe.desconocido is True
    assert informe.tickers == ["AAA", "HALT"]
    assert informe.descartados is None
    assert informe.motivos is None
    veredicto = cat.resolver_simbolo("BRK-B", path=path, ahora=AHORA)
    assert veredicto.catalogo_fresco is False
    assert veredicto.simbolo_feed is None


def test_archivo_viejo_o_de_otro_dia_tampoco_filtra(tmp_path):
    path = tmp_path / "activos.json"
    # Más de 36 h, y además no es hoy.
    _escribir(path, [_fila("HALT", tradable=False)], fecha=AHORA - timedelta(hours=40))
    informe = cat.filtrar_por_catalogo(["HALT", "AAA"], path=path, ahora=AHORA)
    assert informe.desconocido is True
    assert informe.tickers == ["HALT", "AAA"]
    assert cat.consultar("HALT", path=path, ahora=AHORA).tradable is None

    # 20 h, pero de ayer: no cubre hoy aunque entre en las 36 h.
    ayer = datetime(2026, 9, 27, 14, 0, tzinfo=UTC)
    hoy_temprano = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    _escribir(path, [_fila("HALT", tradable=False)], fecha=ayer)
    informe = cat.filtrar_por_catalogo(["HALT"], path=path, ahora=hoy_temprano)
    assert informe.desconocido is True
    assert informe.descartados is None
    assert (hoy_temprano - ayer) < cat.EDAD_MAXIMA


def test_archivo_ilegible_o_vacio_es_desconocido(tmp_path):
    path = tmp_path / "activos.json"
    path.write_text("{no es json", encoding="utf-8")
    assert cat.filtrar_por_catalogo(["AAA"], path=path, ahora=AHORA).desconocido is True
    _escribir(path, [], fecha=AHORA)
    assert cat.filtrar_por_catalogo(["AAA"], path=path, ahora=AHORA).desconocido is True
    path.write_text(json.dumps({"assets": [_fila("AAA")]}), encoding="utf-8")
    # Sin fecha_generacion no se puede saber si cubre hoy.
    assert cat.consultar("AAA", path=path, ahora=AHORA).catalogo_fresco is False


def test_archivo_fresco_saca_no_tradable_y_conserva_el_exchange(tmp_path):
    path = tmp_path / "activos.json"
    _escribir(path, [
        _fila("OK", exchange="NYSE", name="Ok Corp"),
        _fila("HALT", tradable=False, exchange="NASDAQ"),
        _fila("RARO", tradable=None, exchange="AMEX", name="Raro"),
        _fila("DUERME", status="inactive"),
        _fila("FRACC", fractionable=False),
    ])
    informe = cat.filtrar_por_catalogo(
        ["OK", "HALT", "RARO", "DUERME", "FRACC", "NO_ESTA"], path=path, ahora=AHORA,
    )
    assert informe.desconocido is False
    assert informe.tickers == ["OK", "RARO", "FRACC"]
    assert informe.descartados == 3
    assert informe.motivos == {"no_tradable": 1, "status": 1, "no_listado": 1}
    halt = cat.consultar("HALT", path=path, ahora=AHORA)
    assert halt.tradable is False
    assert halt.exchange == "NASDAQ"
    raro = cat.consultar("RARO", path=path, ahora=AHORA)
    assert raro.tradable is None
    assert raro.en_catalogo is True
    assert raro.exchange == "AMEX"
    ok = cat.consultar("OK", path=path, ahora=AHORA)
    assert ok.tradable is True
    assert ok.exchange == "NYSE"
    assert ok.name == "Ok Corp"


def test_brk_con_guion_y_con_punto_son_el_mismo_simbolo_del_archivo(tmp_path):
    path = tmp_path / "activos.json"
    _escribir(path, [_fila("BRK.B", exchange="NYSE", name="Berkshire Hathaway Inc.")])
    for pedido in ("BRK-B", "BRK.B", "brk-b"):
        ficha = cat.consultar(pedido, path=path, ahora=AHORA)
        assert ficha.catalogo_fresco is True
        assert ficha.en_catalogo is True
        assert ficha.symbol == "BRK.B"
        assert ficha.exchange == "NYSE"
        assert ficha.tradable is True
        veredicto = cat.resolver_simbolo(pedido, path=path, ahora=AHORA)
        assert veredicto.catalogo_fresco is True
        assert veredicto.simbolo_feed == "BRK.B"
    informe = cat.filtrar_por_catalogo(["BRK-B", "OTRO"], path=path, ahora=AHORA)
    assert informe.tickers == ["BRK-B"]
    assert informe.motivos == {"no_listado": 1}


def test_un_tradable_en_texto_no_se_lee_como_false(tmp_path):
    path = tmp_path / "activos.json"
    _escribir(path, [_fila("AAA", tradable="false")])
    ficha = cat.consultar("AAA", path=path, ahora=AHORA)
    assert ficha.tradable is None
    assert cat.filtrar_por_catalogo(["AAA"], path=path, ahora=AHORA).tickers == ["AAA"]


def test_el_exchange_fresco_queda_en_la_metadata_y_el_viejo_no(tmp_path):
    path = tmp_path / "activos.json"
    _escribir(path, [_fila("AAA", exchange="NYSE", name="Desde el archivo")])
    meta = Metadata(ticker="AAA", bolsa="OTRO", nombre="Ya tenia nombre")
    cat.anotar_exchange(meta, path=path, ahora=AHORA)
    assert meta.bolsa == "NYSE"
    assert meta.nombre == "Ya tenia nombre"
    sin_nombre = Metadata(ticker="AAA", nombre=None)
    cat.anotar_exchange(sin_nombre, path=path, ahora=AHORA)
    assert sin_nombre.nombre == "Desde el archivo"

    _escribir(path, [_fila("AAA", exchange="NYSE")], fecha=AHORA - timedelta(hours=40))
    vieja = Metadata(ticker="AAA", bolsa="OTRO", nombre=None)
    cat.anotar_exchange(vieja, path=path, ahora=AHORA)
    assert vieja.bolsa == "OTRO"
    assert vieja.nombre is None


def test_construir_candidatos_usa_el_exchange_cuando_el_archivo_esta_fresco(monkeypatch, tmp_path):
    path = tmp_path / "activos.json"
    _escribir(path, [_fila("AAA", exchange="NYSE")])
    barras = {"AAA": _barras("AAA")}
    meta = {"AAA": Metadata(ticker="AAA", bolsa="OTRO", market_cap=50_000_000.0, nombre="Yahoo")}
    # El autouse deja el archivo ausente: la bolsa que ya traía la metadata se queda.
    sin_archivo = construir_candidatos_diarios(
        ["AAA"], barras, _Meta(meta), CFG, con_catalizadores=False,
        bandas={"AAA": "small"}, ahora=AHORA,
    )
    assert len(sin_archivo) == 1
    assert sin_archivo[0].meta.bolsa == "OTRO"
    monkeypatch.setenv("MOMENTUM_CATALOGO_ACTIVOS", str(path))
    con_archivo = construir_candidatos_diarios(
        ["AAA"], barras, _Meta(meta), CFG, con_catalizadores=False,
        bandas={"AAA": "small"}, ahora=AHORA,
    )
    assert len(con_archivo) == 1
    assert con_archivo[0].meta.bolsa == "NYSE"
    assert con_archivo[0].meta.nombre == "Yahoo"
