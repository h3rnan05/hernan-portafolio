"""Pruebas de las heurísticas puras de `data/provider.py` -- sin red."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

from momentum_hunter.data.provider import (
    PausaYahoo,
    YahooProvider,
    _num,
    _parece_cef,
    _parece_spac,
    _velas_finales_en_formacion,
)
from momentum_hunter.models import Metadata


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _chart_payload(volumenes: list[float | None]) -> dict:
    n = len(volumenes)
    ts = list(range(1_700_000_000, 1_700_000_000 + n * 60, 60))
    precios = [100.0 + i for i in range(n)]
    return {
        "chart": {"result": [{
            "timestamp": ts,
            "indicators": {"quote": [{
                "open": precios, "close": precios, "high": precios, "low": precios,
                "volume": volumenes,
            }]},
        }]},
    }


# ------------------------- bug real 2026-08-20: volumen None -> 0 inventado -------------------------
# La vela más reciente (en formación) de Yahoo suele llegar con precio ya
# confirmado pero `volume: null` -- el agregado de volumen va con
# retraso. `float(v or 0)` convertía eso en un CERO real, dejando
# `rvol_actual` en 0.0 de forma sistemática para todo ticker, todos los
# días -- bloqueando para siempre la pregunta "¿está entrando dinero
# ahora?" del evaluador. Este es el bug que hizo que MRNA (y todo lo
# demás) nunca se alertara a tiempo.

def test_barras_intradia_descarta_la_vela_con_volumen_none_no_la_pone_en_cero(monkeypatch):
    # 5 velas reales + la última en formación, sin volumen todavía --
    # `barras_intradia` exige >= 5 velas válidas, así que se necesitan
    # al menos 6 en total para aislar el descarte de la última.
    payload = _chart_payload([1000.0, 2000.0, 3000.0, 1500.0, 2500.0, None])
    monkeypatch.setattr(
        "momentum_hunter.data.provider.requests.get", lambda *a, **kw: _FakeResponse(payload))

    provider = YahooProvider(pausa=0)
    resultado = provider.barras_intradia(["ACME"])

    assert "ACME" in resultado
    bi = resultado["ACME"]
    assert len(bi) == 5   # la vela con volumen None se descartó entera, no se coló con volumen 0
    assert 0.0 not in bi.volume   # ningún cero inventado


def test_barras_intradia_preserva_un_volumen_cero_real_y_explicito(monkeypatch):
    # Un 0 explícito de Yahoo (de verdad no hubo operaciones ese minuto)
    # es un dato real -- distinto de `None` (dato ausente) -- y debe
    # conservarse, no descartarse también.
    payload = _chart_payload([1000.0, 0.0, 3000.0, 1500.0, 2500.0])
    monkeypatch.setattr(
        "momentum_hunter.data.provider.requests.get", lambda *a, **kw: _FakeResponse(payload))

    provider = YahooProvider(pausa=0)
    resultado = provider.barras_intradia(["ACME"])

    bi = resultado["ACME"]
    assert len(bi) == 5
    assert bi.volume == [1000.0, 0.0, 3000.0, 1500.0, 2500.0]


def test_barras_diarias_descarta_la_vela_con_volumen_none_no_la_pone_en_cero(monkeypatch):
    payload = _chart_payload([500_000.0, 600_000.0, None])
    monkeypatch.setattr(
        "momentum_hunter.data.provider.requests.get", lambda *a, **kw: _FakeResponse(payload))

    provider = YahooProvider(pausa=0)
    resultado = provider.barras(["ACME"])

    # barras() exige >= 20 velas válidas -- con solo 2 tras descartar la
    # de volumen None, el ticker se omite (comportamiento correcto, no
    # es lo que este test verifica). Se prueba el parseo directo en su
    # lugar para aislar la lógica del descarte.
    assert resultado == {}


def test_barras_una_directa_descarta_volumen_none(monkeypatch):
    payload = _chart_payload([500_000.0] * 25 + [None])
    monkeypatch.setattr(
        "momentum_hunter.data.provider.requests.get", lambda *a, **kw: _FakeResponse(payload))

    provider = YahooProvider(pausa=0)
    b = provider._barras_una("ACME", "1y")

    assert b is not None
    assert len(b) == 25   # la vela final con volumen None se descartó
    assert 0.0 not in b.volume


# ------------------------- bug real 2026-08-21: volumen 0 EXPLÍCITO (no None) -------------------------
# El fix de arriba no bastaba -- confirmado contra la respuesta cruda de
# Yahoo (IBM en vivo): la vela en curso casi siempre llega con volumen 0
# explícito, no `None` -- el minuto todavía no acumuló ningún trade en
# el instante exacto de la consulta. Esta es la razón real de que
# `momentum_paper_trader` nunca haya colocado una orden.

def test_velas_finales_en_formacion_cuenta_los_ceros_del_final():
    assert _velas_finales_en_formacion([100.0, 200.0, 300.0, 0.0]) == 1
    assert _velas_finales_en_formacion([100.0, 200.0, 0.0, 0.0]) == 2
    assert _velas_finales_en_formacion([100.0, 200.0, 300.0]) == 0


def test_velas_finales_en_formacion_no_toca_un_cero_en_medio_de_la_sesion():
    # Una acción líquida que de verdad no operó un minuto completo en
    # medio del día es rarísimo, pero posible -- eso NO es "en
    # formación", y no debe recortarse.
    assert _velas_finales_en_formacion([100.0, 0.0, 300.0, 400.0]) == 0


def test_velas_finales_en_formacion_deja_al_menos_una_vela():
    assert _velas_finales_en_formacion([0.0, 0.0, 0.0]) == 2


def test_barras_intradia_recorta_la_vela_en_formacion_con_volumen_cero_explicito(monkeypatch):
    # Mismo escenario que se vio en la respuesta real de Yahoo para IBM:
    # la última vela trae precio confirmado pero volumen 0 literal.
    payload = _chart_payload([1000.0, 2000.0, 3000.0, 1500.0, 2500.0, 0.0])
    monkeypatch.setattr(
        "momentum_hunter.data.provider.requests.get", lambda *a, **kw: _FakeResponse(payload))

    provider = YahooProvider(pausa=0)
    bi = provider.barras_intradia(["ACME"])["ACME"]

    assert len(bi) == 5   # la vela en formación (volumen 0) se recortó
    assert bi.volume[-1] == 2500.0   # queda la última vela YA CERRADA, con volumen real
    assert 0.0 not in bi.volume


def test_rvol_actual_deja_de_ser_siempre_cero_con_el_fix(monkeypatch):
    # La prueba de fondo: antes de este fix, rvol_actual daba 0.0 sin
    # importar los datos reales -- acá se confirma que ahora refleja el
    # volumen de la última vela YA CERRADA, no la que sigue en formación.
    from momentum_hunter.factors.intradia import rvol_actual

    payload = _chart_payload([1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 8000.0, 0.0])
    monkeypatch.setattr(
        "momentum_hunter.data.provider.requests.get", lambda *a, **kw: _FakeResponse(payload))

    provider = YahooProvider(pausa=0)
    bi = provider.barras_intradia(["ACME"])["ACME"]

    assert rvol_actual(bi) == 8.0   # 8000 / promedio(1000,1000,1000,1000,1000) -- ya no es 0.0


def test_parece_spac_detecta_nombre_tipico():
    assert _parece_spac("Acme Acquisition Corp") is True
    assert _parece_spac("Acme Blank Check Company") is True


def test_parece_spac_no_falso_positivo_en_empresa_normal():
    assert _parece_spac("Apple Inc.") is False


def test_parece_spac_none_es_false():
    assert _parece_spac(None) is False


def test_parece_cef_por_quote_type():
    assert _parece_cef("Cualquiera", "CLOSEDEND") is True


def test_parece_cef_por_nombre():
    assert _parece_cef("Acme Municipal Income Fund", None) is True


def test_parece_cef_no_falso_positivo():
    assert _parece_cef("Apple Inc.", "EQUITY") is False


def test_num_convierte_valores_validos():
    assert _num("3.5") == 3.5
    assert _num(10) == 10.0


def test_num_none_con_nan_o_invalido():
    assert _num(float("nan")) is None
    assert _num(None) is None
    assert _num("no-es-numero") is None


# ------------------------- cache diario de metadata (Yahoo .info) -------------------------

class _TickerFalso:
    def __init__(self, info):
        self._info = info

    @property
    def info(self):
        if isinstance(self._info, BaseException):
            raise self._info
        return self._info


class _YFinanceFalso:
    def __init__(self):
        self.llamadas: list[str] = []
        self.por_ticker: dict = {}
        self.error: BaseException | None = None

    def Ticker(self, ticker: str):
        self.llamadas.append(ticker)
        if self.error is not None:
            return _TickerFalso(self.error)
        return _TickerFalso(self.por_ticker.get(ticker, {}))


def _info_acme(**extra) -> dict:
    base = {
        "longName": "Acme Corp",
        "quoteType": "EQUITY",
        "exchange": "NMS",
        "marketCap": 1_000_000,
        "floatShares": 250_000,
        "shortPercentOfFloat": 0.12,
        "shortRatio": 3.5,
    }
    base.update(extra)
    return base


def _provider_cache(tmp_path, reloj, pausa_429=None):
    return YahooProvider(
        pausa=0,
        cache_metadata=tmp_path / "yahoo_metadata_cache.json",
        reloj=reloj,
        pausa_429=pausa_429,
    )


def test_metadata_el_mismo_dia_de_nueva_york_no_vuelve_a_llamar(monkeypatch, tmp_path):
    yf = _YFinanceFalso()
    yf.por_ticker["ACME"] = _info_acme()
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    # 15:00 UTC = 11:00 EDT. Las dos llamadas son el mismo día de Nueva York.
    ahora = {"t": datetime(2026, 9, 28, 15, 0, tzinfo=UTC)}
    p = _provider_cache(tmp_path, lambda: ahora["t"])

    primera = p.metadata(["ACME"])["ACME"]
    segunda = p.metadata(["ACME"])["ACME"]

    assert yf.llamadas == ["ACME"]
    assert segunda == primera
    assert isinstance(segunda, Metadata)
    assert segunda.market_cap == 1_000_000
    assert segunda.shares_float == 250_000
    assert segunda.bolsa == "NASDAQ"
    assert segunda.es_etf is False


def test_metadata_al_dia_siguiente_en_nueva_york_vuelve_a_pedir(monkeypatch, tmp_path):
    yf = _YFinanceFalso()
    yf.por_ticker["ACME"] = _info_acme(marketCap=100)
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    # 03:30 UTC del 28 = 23:30 del 27 en Nueva York. 04:30 UTC ya es el 28.
    ahora = {"t": datetime(2026, 9, 28, 3, 30, tzinfo=UTC)}
    p = _provider_cache(tmp_path, lambda: ahora["t"])

    assert p.metadata(["ACME"])["ACME"].market_cap == 100
    assert p.metadata(["ACME"])["ACME"].market_cap == 100
    assert yf.llamadas == ["ACME"]

    yf.por_ticker["ACME"] = _info_acme(marketCap=200)
    ahora["t"] = datetime(2026, 9, 28, 4, 30, tzinfo=UTC)
    assert p.metadata(["ACME"])["ACME"].market_cap == 200
    assert yf.llamadas == ["ACME", "ACME"]


def test_metadata_campo_ausente_sigue_en_none_y_no_es_cero(monkeypatch, tmp_path):
    yf = _YFinanceFalso()
    info = _info_acme()
    info.pop("floatShares")
    info.pop("shortPercentOfFloat")
    info.pop("shortRatio")
    yf.por_ticker["ACME"] = info
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    ahora = {"t": datetime(2026, 9, 28, 15, 0, tzinfo=UTC)}
    p = _provider_cache(tmp_path, lambda: ahora["t"])

    meta = p.metadata(["ACME"])["ACME"]
    assert meta.shares_float is None
    assert meta.short_pct_float is None
    assert meta.days_to_cover is None
    assert meta.market_cap == 1_000_000
    # El None queda cacheado: la segunda lectura no rellena con 0 ni vuelve a pedir.
    otra = p.metadata(["ACME"])["ACME"]
    assert yf.llamadas == ["ACME"]
    assert otra.shares_float is None
    crudo = json.loads((tmp_path / "yahoo_metadata_cache.json").read_text())
    assert crudo["tickers"]["ACME"]["campos"]["shares_float"] is None


def test_metadata_cero_explicito_de_yahoo_no_se_borra(monkeypatch, tmp_path):
    # Un 0 que Yahoo mandó es un dato. Lo que no se inventa es el ausente.
    yf = _YFinanceFalso()
    yf.por_ticker["ACME"] = _info_acme(floatShares=0)
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    ahora = {"t": datetime(2026, 9, 28, 15, 0, tzinfo=UTC)}
    p = _provider_cache(tmp_path, lambda: ahora["t"])
    assert p.metadata(["ACME"])["ACME"].shares_float == 0.0


def test_metadata_error_de_yahoo_no_envenena_el_cache(monkeypatch, tmp_path):
    yf = _YFinanceFalso()
    yf.error = RuntimeError("yahoo caído")
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    ahora = {"t": datetime(2026, 9, 28, 15, 0, tzinfo=UTC)}
    p = _provider_cache(tmp_path, lambda: ahora["t"])

    meta = p.metadata(["ACME"])["ACME"]
    assert meta.market_cap is None
    assert meta.shares_float is None
    assert meta.nombre is None
    crudo = json.loads((tmp_path / "yahoo_metadata_cache.json").read_text())
    assert "ACME" not in crudo["tickers"]
    assert crudo["fallos"]["ACME"]["intentos"] == 1

    # Dentro del backoff no se insiste, y tampoco se sirve el vacío como válido.
    assert p.metadata(["ACME"])["ACME"].market_cap is None
    assert yf.llamadas == ["ACME"]

    yf.error = None
    yf.por_ticker["ACME"] = _info_acme(marketCap=50)
    ahora["t"] = ahora["t"] + timedelta(seconds=61)
    recuperada = p.metadata(["ACME"])["ACME"]
    assert yf.llamadas == ["ACME", "ACME"]
    assert recuperada.market_cap == 50
    assert recuperada.shares_float == 250_000
    crudo = json.loads((tmp_path / "yahoo_metadata_cache.json").read_text())
    assert "ACME" not in crudo["fallos"]
    assert crudo["tickers"]["ACME"]["campos"]["market_cap"] == 50


def test_metadata_info_vacio_no_se_cachea_como_valido(monkeypatch, tmp_path):
    # yfinance a veces devuelve {} en vez de lanzar. Eso no es un snapshot.
    yf = _YFinanceFalso()
    yf.por_ticker["ACME"] = {}
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    ahora = {"t": datetime(2026, 9, 28, 15, 0, tzinfo=UTC)}
    p = _provider_cache(tmp_path, lambda: ahora["t"])

    assert p.metadata(["ACME"])["ACME"].shares_float is None
    crudo = json.loads((tmp_path / "yahoo_metadata_cache.json").read_text())
    assert "ACME" not in crudo["tickers"]

    yf.por_ticker["ACME"] = _info_acme()
    ahora["t"] = ahora["t"] + timedelta(seconds=61)
    assert p.metadata(["ACME"])["ACME"].shares_float == 250_000
    assert yf.llamadas == ["ACME", "ACME"]


def test_metadata_respeta_la_pausa_de_429(monkeypatch, tmp_path):
    yf = _YFinanceFalso()
    yf.por_ticker["ACME"] = _info_acme()
    monkeypatch.setitem(sys.modules, "yfinance", yf)
    ahora = {"t": datetime(2026, 9, 28, 15, 0, tzinfo=UTC)}
    pausa = PausaYahoo(str(tmp_path / "pausa.json"))
    p = _provider_cache(tmp_path, lambda: ahora["t"], pausa_429=pausa)

    # Pausa ya activa: no se llama y no se guarda un vacío válido.
    pausa.pausar(ahora["t"])
    assert p.metadata(["ACME"])["ACME"].market_cap is None
    assert yf.llamadas == []
    cache = tmp_path / "yahoo_metadata_cache.json"
    if cache.exists():
        assert "ACME" not in json.loads(cache.read_text()).get("tickers", {})

    # Un 429 de yfinance escribe la misma pausa y tampoco deja el día envenenado.
    class _Limite(Exception):
        status_code = 429

    # La pausa anterior sigue vigente: se levanta el reloj para poder ver el 429.
    ahora["t"] = ahora["t"] + timedelta(seconds=901)
    yf.error = _Limite("429")
    assert p.metadata(["ACME"])["ACME"].shares_float is None
    assert yf.llamadas == ["ACME"]
    assert pausa.activa(ahora["t"]) is True
    crudo = json.loads(cache.read_text())
    assert "ACME" not in crudo["tickers"]

    yf.error = None
    assert p.metadata(["ACME"])["ACME"].market_cap is None
    assert yf.llamadas == ["ACME"]

    ahora["t"] = ahora["t"] + timedelta(seconds=901)
    assert p.metadata(["ACME"])["ACME"].market_cap == 1_000_000
    assert yf.llamadas == ["ACME", "ACME"]
