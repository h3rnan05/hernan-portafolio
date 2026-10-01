"""Metadata por Finnhub detrás de MOMENTUM_METADATA_PROVIDER y su sombra."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

from momentum_hunter import sombra_metadata
from momentum_hunter.data import fuente
from momentum_hunter.data.finnhub_metadata import ENV_TOKEN, FinnhubMetadata, bolsa_de, metadata_de_perfil
from momentum_hunter.models import Barras, Metadata

RAIZ = Path(__file__).resolve().parents[2]

# Formato público de /stock/profile2 con un emisor FICTICIO (el entorno
# de desarrollo no llega a finnhub.io). marketCapitalization en millones.
PERFIL_FICA = {"country": "US", "currency": "USD", "exchange": "NASDAQ NMS - GLOBAL MARKET", "finnhubIndustry": "Biotechnology",
               "ipo": "2019-05-01", "logo": "", "marketCapitalization": 215.4, "name": "Ficticia A Corp", "phone": "",
               "shareOutstanding": 52.0, "ticker": "FICA", "weburl": ""}


class _Resp:
    def __init__(self, status, cuerpo):
        self.status_code = status
        self._cuerpo = cuerpo

    def json(self):
        if isinstance(self._cuerpo, str):
            raise ValueError("no json")
        return self._cuerpo


def _transport(respuestas):
    pedidos = []

    def get(url, params=None, timeout=None, **kw):
        pedidos.append((url, dict(params or {})))
        r = respuestas.get(params["symbol"])
        if isinstance(r, Exception):
            raise r
        return r
    get.pedidos = pedidos
    return get


def test_perfil_a_metadata_convierte_millones_y_deja_el_float_en_none():
    m = metadata_de_perfil("FICA", PERFIL_FICA)
    assert m.nombre == "Ficticia A Corp" and m.bolsa == "NASDAQ" and m.market_cap == 215_400_000.0
    assert m.shares_float is None and m.short_pct_float is None and m.es_etf is False
    assert metadata_de_perfil("X", {}) == Metadata("X")


@pytest.mark.parametrize("texto, bolsa", [("NASDAQ NMS - GLOBAL MARKET", "NASDAQ"), ("NEW YORK STOCK EXCHANGE, INC.", "NYSE"),
                                          ("NYSE MKT LLC", "AMEX"), ("", None), (None, None), ("OTC", None)])
def test_bolsa_de(texto, bolsa):
    assert bolsa_de(texto) == bolsa


def test_metadata_por_ticker_con_fallos_marcados(monkeypatch):
    t = _transport({"FICA": _Resp(200, PERFIL_FICA), "NADA": _Resp(200, {}), "CAIDO": requests.ConnectionError("x"),
                    "LIMITE": _Resp(429, {}), "ROTO": _Resp(200, "<html>")})
    f = FinnhubMetadata(token="clave", transport=t, reloj=lambda: 0.0, dormir=lambda s: None)
    out = f.metadata(["FICA", "NADA", "CAIDO", "LIMITE", "ROTO"])
    assert out["FICA"].market_cap == 215_400_000.0
    assert out["NADA"] == Metadata("NADA") and "NADA" not in f.fallidos   # Finnhub no lo tiene: no es un fallo
    assert f.fallidos == ["CAIDO", "LIMITE", "ROTO"]
    assert all(p[1]["token"] == "clave" for p in t.pedidos)


def test_sin_clave_no_se_pide_nada(monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    t = _transport({})
    f = FinnhubMetadata(transport=t)
    assert f.metadata(["FICA"]) == {"FICA": Metadata("FICA")} and f.fallidos == ["FICA"] and t.pedidos == []
    assert f.ultimo_codigo == "sin_credenciales"


def test_limite_de_30_por_minuto():
    reloj = [0.0]
    dormidas = []

    def dormir(s):
        dormidas.append(s)
        reloj[0] += s
    t = _transport({"FICA": _Resp(200, PERFIL_FICA)})
    f = FinnhubMetadata(token="k", transport=t, reloj=lambda: reloj[0], dormir=dormir)
    for _ in range(31):
        f.perfil("FICA")
    assert len(dormidas) == 1 and dormidas[0] == pytest.approx(60.0)


# ------------------------------------------------------------ bandera


class _Precios:
    def barras(self, tickers, dias=280):
        return {t: "barras" for t in tickers}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}

    def metadata(self, tickers):
        return {t: Metadata(t, nombre="yahoo") for t in tickers}


def test_default_yahoo_no_cambia_nada(monkeypatch):
    monkeypatch.delenv(fuente.ENV_METADATA, raising=False)
    monkeypatch.delenv(fuente.ENV_PROVEEDOR, raising=False)
    p = fuente.proveedor_configurado(construir_yahoo=_Precios)
    assert p.metadata(["A"])["A"].nombre == "yahoo"
    assert fuente.proveedor_metadata_configurado() == "yahoo"


def test_valor_desconocido_se_queda_en_yahoo(monkeypatch):
    monkeypatch.setenv(fuente.ENV_METADATA, "polygon")
    assert fuente.proveedor_metadata_configurado() == "yahoo"


def test_finnhub_solo_cambia_la_metadata(monkeypatch):
    monkeypatch.setenv(fuente.ENV_METADATA, "finnhub")
    # Precios del doble (yahoo): esta prueba es sobre la metadata.
    monkeypatch.setenv(fuente.ENV_PROVEEDOR, "yahoo")
    monkeypatch.setenv(ENV_TOKEN, "k")
    p = fuente.proveedor_configurado(construir_yahoo=_Precios)
    assert isinstance(p, fuente.ConMetadataAparte)
    assert p.barras(["A"]) == {"A": "barras"}
    p._metadata = FinnhubMetadata(token="k", transport=_transport({"FICA": _Resp(200, PERFIL_FICA)}))
    assert p.metadata(["FICA"])["FICA"].nombre == "Ficticia A Corp"
    assert fuente.informe_de(p) is not None   # la telemetría de precios sigue midiendo


# -------------------------------------------------------------- sombra


def test_comparar_no_cuenta_el_float_como_discrepancia():
    y = Metadata("A", nombre="Ficticia A Corp", bolsa="NASDAQ", market_cap=200e6, shares_float=12e6)
    f = Metadata("A", nombre="FICTICIA A CORP", bolsa="NASDAQ", market_cap=215.4e6, shares_float=None)
    dif = sombra_metadata.comparar(y, f)
    assert dif == {"float_faltaria_con_finnhub": True}
    f2 = Metadata("A", nombre="Otra", bolsa="NYSE", market_cap=300e6)
    dif = sombra_metadata.comparar(y, f2)
    assert dif["nombre_distinto"] and dif["bolsa_distinto"] and dif["market_cap_dif_rel"] == 0.5
    dif = sombra_metadata.comparar(Metadata("A"), f)
    assert dif["nombre_falta_yahoo"] and dif["market_cap_falta_yahoo"] and dif["float_faltaria_con_finnhub"] is False


def test_correr_escribe_jsonl_y_resumen(tmp_path):
    class _F:
        fallidos = ["CAIDO"]

        def metadata(self, tickers):
            return {"FICA": metadata_de_perfil("FICA", PERFIL_FICA), "CAIDO": Metadata("CAIDO")}

    class _Y:
        def metadata(self, tickers):
            return {t: Metadata(t, nombre="Ficticia A Corp", bolsa="NASDAQ", market_cap=100e6, shares_float=1e6) for t in tickers}

    salida = tmp_path / "s" / "2026-09-28.jsonl"
    res = sombra_metadata.correr(["FICA", "CAIDO"], _Y(), _F(), salida, datetime(2026, 9, 28, 13, 0, tzinfo=UTC))
    assert res == {"fecha": "2026-09-28", "tickers": 2, "finnhub_fallidos": 1, "con_discrepancia": 1,
                   "float_faltaria_con_finnhub": 1}
    lineas = [json.loads(l) for l in salida.read_text(encoding="utf-8").splitlines()]
    assert lineas[0]["dif"]["market_cap_dif_rel"] == 1.154 and lineas[1]["dif"] == {"finnhub_fallo": True}


def test_main_apagada_por_defecto_y_completa_a_las_5(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(sombra_metadata.ENV_ACTIVA, raising=False)
    assert sombra_metadata.main([]) == 0 and "no-op" in capsys.readouterr().out
    monkeypatch.setenv(sombra_metadata.ENV_ACTIVA, "1")
    d = sombra_metadata.directorio()
    assert str(d).startswith(str(tmp_path))   # MOMENTUM_ESTADO_DIR de la suite
    for i in range(5):
        (d / f"2026-09-2{i}.jsonl").write_text("", encoding="utf-8")
    assert sombra_metadata.main([]) == 0 and "SOMBRA COMPLETA" in capsys.readouterr().out


def test_main_corre_una_sesion_y_no_la_repite(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(sombra_metadata.ENV_ACTIVA, "1")
    monkeypatch.setattr(sombra_metadata, "YahooProvider", lambda: _Y2())
    monkeypatch.setattr(sombra_metadata, "FinnhubMetadata", lambda: _F2())
    assert sombra_metadata.main(["--tickers", "FICA"]) == 0
    assert "sesión 1/5" in capsys.readouterr().out
    assert sombra_metadata.sesiones_hechas(sombra_metadata.directorio()) == 1
    assert sombra_metadata.main(["--tickers", "FICA"]) == 0 and "ya está" in capsys.readouterr().out


class _Y2:
    def metadata(self, tickers):
        return {t: Metadata(t, nombre="x") for t in tickers}


class _F2:
    fallidos: list = []

    def metadata(self, tickers):
        return {t: Metadata(t, nombre="x") for t in tickers}


def test_muestra_desde_el_entorno(monkeypatch):
    monkeypatch.setenv(sombra_metadata.ENV_TICKERS, "fica, ficb")
    assert sombra_metadata.muestra() == ["FICA", "FICB"]
    monkeypatch.delenv(sombra_metadata.ENV_TICKERS)

    class S:
        def __init__(self, t):
            self.ticker = t
    assert sombra_metadata.muestra(lambda: [S("A"), S("B")]) == ["A", "B"]


def test_unidades_de_la_sombra_no_se_instalan_solas():
    base = RAIZ / "infra" / "systemd"
    for nombre in ("momentum-metadata-sombra.service", "momentum-metadata-sombra.timer"):
        assert "NO se instala solo" in (base / nombre).read_text(encoding="utf-8")
    timer = (base / "momentum-metadata-sombra.timer").read_text(encoding="utf-8")
    assert "13:00:00 UTC" in timer
    sh = (base / "bin" / "run_metadata_sombra.sh").read_text(encoding="utf-8")
    assert 'MOMENTUM_METADATA_SOMBRA:-0}" != "1"' in sh and "flock -n 9" in sh
    codigo = "\n".join(l for l in sh.splitlines() if not l.lstrip().startswith("#"))
    assert "watchlist.json" not in codigo and "git " not in codigo
    readme = (base / "README.md").read_text(encoding="utf-8")
    assert "momentum-metadata-sombra.timer" in readme and "MOMENTUM_METADATA_SOMBRA=1" in readme
