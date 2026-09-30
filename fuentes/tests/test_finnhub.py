"""Finnhub (F7): noticias point-in-time por empresa y earnings de hoy/ayer.

Respuestas FICTICIAS con el formato público (el entorno de desarrollo no
llega a finnhub.io); el calendario es el mismo fixture de F2a."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest

from fuentes import FALTANTE, ErrorFuente, finnhub as fh, resultados
from fuentes.cache import Cache
from fuentes.columnas import Registro
from fuentes.grabar import TransporteGrabado, cargar, transporte_desde

TOKEN = "clave-secreta-finnhub"
NOTICIAS = ["noticias_FICA_20260923_20260925", "noticias_FICA_20260926_20260928_vacio",
            "noticias_ROTO_20260923_20260925_error", "noticias_SINHORA_20260923_20260925"]
# 09:40 NY del viernes 25/9/2026: el calendario ficticio pone FICA amc el 24.
SENAL = datetime(2026, 9, 25, 13, 40, tzinfo=UTC)


class _ConCabeceras(TransporteGrabado):
    """Además de (url, params), guarda las cabeceras de cada pedido."""

    def __init__(self, base: TransporteGrabado) -> None:
        super().__init__(base.registros, base.fallar)
        self.cabeceras: list[dict] = []

    def __call__(self, url, params=None, headers=None, timeout=None, json=None):
        self.cabeceras.append(dict(headers or {}))
        return super().__call__(url, params, headers, timeout, json)


def _calendario(simbolo: str) -> tuple[str, dict]:
    reg = cargar("resultados", "calendario_FICA")
    return reg["url"], {**reg["params"], "symbol": simbolo, "token": TOKEN}


def _fuente(tmp_path, monkeypatch, token=TOKEN, hoy=date(2026, 10, 1), fallar=None):
    if token:
        monkeypatch.setenv(fh.ENV_TOKEN, token)
    t = _ConCabeceras(transporte_desde("finnhub", NOTICIAS, fallar))
    reg = cargar("resultados", "calendario_FICA")
    url, params = _calendario("FICA")
    t.agregar(url, params, reg)
    for sim in ("ROTO", "SINHORA"):
        t.fallar.add(TransporteGrabado.clave(*_calendario(sim)))
    dormidas: list[float] = []
    c = resultados.cliente_finnhub(transport=t, dormir=dormidas.append)
    return fh.Finnhub(Cache(tmp_path / "c"), c, hoy=hoy), t, dormidas


def _noticias(t: TransporteGrabado) -> list[dict]:
    return [p for u, p in t.pedidos if u == fh.URL_NOTICIAS]


# ------------------------------------------------------------ parser


def test_leer_noticias_quita_duplicados_y_lee_utc():
    lista = fh.leer_noticias(__import__("json").loads(cargar("finnhub", "noticias_FICA_20260923_20260925")["texto"]))
    assert len(lista) == 7   # 8 artículos, el 103 repetido
    horas = {i: h for i, h in lista}
    assert horas[105] == datetime(2026, 9, 25, 13, 10, tzinfo=UTC)


def test_cuerpo_de_error_o_articulo_sin_hora_invalida_la_respuesta():
    with pytest.raises(ErrorFuente, match="cuerpo"):
        fh.leer_noticias({"error": "You don't have access to this resource."})
    for roto in ([{"id": 1, "datetime": 0}], [{"id": 1}], [{"id": 1, "datetime": "1758800000"}],
                 [{"id": 1, "datetime": True}], ["texto"]):
        with pytest.raises(ErrorFuente, match="item_ilegible"):
            fh.leer_noticias(roto)
    assert fh.leer_noticias([]) == []


def test_rango_pedido_cubre_la_fecha_ny_y_la_utc():
    # 00:30 UTC del 25 = 20:30 NY del 24: la ventana empieza el 24 UTC (23 NY).
    assert fh.rango_pedido(datetime(2026, 9, 25, 0, 30, tzinfo=UTC)) == (date(2026, 9, 23), date(2026, 9, 25))
    assert fh.rango_pedido(SENAL) == (date(2026, 9, 23), date(2026, 9, 25))


# ------------------------------------------------------ point-in-time


def test_columnas_solo_cuentan_lo_estrictamente_anterior(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", SENAL)
    # 24 h: 13:10 del 25, 20:05 del 24 (una vez) y 13:40:00 del 24 (borde inferior incluido).
    # Fuera: 13:40 del 25 (misma marca que la señal), 14:00 del 25 (posterior), 13:39:59 del 24.
    assert fila == {"finnhub_noticias_24h": 3, "finnhub_noticia_60min": True,
                    "finnhub_earnings_hoy": False, "finnhub_earnings_ayer": True}
    assert _noticias(t) == [{"symbol": "FICA", "from": "2026-09-23", "to": "2026-09-25"}]


def test_la_noticia_en_el_minuto_de_la_senal_no_es_de_los_60_min(tmp_path, monkeypatch):
    f, _, _ = _fuente(tmp_path, monkeypatch)
    # A las 13:10:00 exactas el artículo 105 todavía no se sabía; las 24 h
    # tienen el 103, el 102 y el 101 (13:39:59 del 24 ya entra en esta ventana).
    fila = f.columnas("FICA", datetime(2026, 9, 25, 13, 10, tzinfo=UTC))
    assert fila["finnhub_noticia_60min"] is False and fila["finnhub_noticias_24h"] == 3
    fila = f.columnas("FICA", datetime(2026, 9, 25, 13, 10, 1, tzinfo=UTC))
    assert fila["finnhub_noticia_60min"] is True and fila["finnhub_noticias_24h"] == 4


def test_mismo_dia_mismo_pedido_y_cache_en_disco(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    f.columnas("FICA", SENAL)
    f.columnas("FICA", datetime(2026, 9, 25, 12, 0, tzinfo=UTC))
    assert len(_noticias(t)) == 1
    g = fh.Finnhub(f.cache, f._cliente, hoy=date(2026, 10, 1))
    assert g.columnas("FICA", SENAL)["finnhub_noticias_24h"] == 3
    assert len(_noticias(t)) == 1


def test_dia_sin_articulos_dentro_de_la_historia_es_cero_afirmado(tmp_path, monkeypatch):
    f, _, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", datetime(2026, 9, 28, 13, 40, tzinfo=UTC))   # lunes
    assert fila["finnhub_noticias_24h"] == 0 and fila["finnhub_noticia_60min"] is False
    # Lunes: "ayer" es el viernes 25, sin reporte de FICA (el bmo del 25 es de OTRO).
    assert fila["finnhub_earnings_hoy"] is False and fila["finnhub_earnings_ayer"] is False


def test_fuera_de_la_historia_del_plan_es_faltante_sin_pedir(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch, hoy=date(2027, 10, 1))
    fila = f.columnas("FICA", SENAL)
    assert fila["finnhub_noticias_24h"] is FALTANTE and fila["finnhub_noticia_60min"] is FALTANTE
    assert _noticias(t) == []


# ------------------------------------------------------------ earnings


def test_earnings_hoy_ayer_y_cobertura():
    # Jueves 24/9 a las 10:00 NY: FICA reporta ese día (amc).
    jueves = datetime(2026, 9, 24, 14, 0, tzinfo=UTC)
    assert fh.earnings_hoy_ayer([date(2026, 5, 7), date(2026, 9, 24)], jueves) == (True, False)
    # Lunes: "ayer" es el viernes.
    lunes = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    assert fh.earnings_hoy_ayer([date(2026, 5, 7), date(2026, 9, 25)], lunes) == (False, True)
    # Calendario vacío o que empieza después de "ayer": sin cobertura, no False.
    assert fh.earnings_hoy_ayer([], lunes) == (FALTANTE, FALTANTE)
    assert fh.earnings_hoy_ayer([date(2026, 9, 28)], lunes) == (FALTANTE, FALTANTE)
    assert fh.dia_habil_anterior(date(2026, 9, 28)) == date(2026, 9, 25)


def test_earnings_comparte_el_pedido_y_la_cache_de_f2a(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    f.columnas("FICA", SENAL)
    r = resultados.ResultadosFinnhub(f.cache, f._cliente)
    assert r.columnas("FICA", SENAL)["resultados_reciente"] is True
    assert sum(1 for u, _ in t.pedidos if u == resultados.URL_CALENDARIO) == 1


# ------------------------------------------------------------ fail-closed


def test_sin_clave_todo_faltante_y_ningun_pedido(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch, token="")
    fila = f.columnas("FICA", SENAL)
    assert fila == {n: FALTANTE for n in f.nombres()} and t.pedidos == []


def test_error_de_la_api_es_faltante_nunca_cero(tmp_path, monkeypatch):
    f, _, _ = _fuente(tmp_path, monkeypatch)
    for sim in ("ROTO", "SINHORA"):
        fila = f.columnas(sim, SENAL)
        assert all(v is FALTANTE for v in fila.values()), (sim, fila)
        assert 0 not in fila.values() and False not in fila.values()


def test_red_caida_es_faltante_y_no_se_cachea(tmp_path, monkeypatch):
    caida = TransporteGrabado.clave(fh.URL_NOTICIAS, {"symbol": "FICA", "from": "2026-09-23", "to": "2026-09-25"})
    f, _, _ = _fuente(tmp_path, monkeypatch, fallar={caida})
    fila = f.columnas("FICA", SENAL)
    assert fila["finnhub_noticias_24h"] is FALTANTE and fila["finnhub_noticia_60min"] is FALTANTE
    assert fila["finnhub_earnings_ayer"] is True   # el calendario sí respondió
    assert not list((tmp_path / "c").glob("finnhub_noticias/*.json"))


def test_hora_naive_no_se_adivina(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    fila = Registro([f]).columnas("FICA", datetime(2026, 9, 25, 13, 40))
    assert all(v is FALTANTE for v in fila.values()) and t.pedidos == []


# ------------------------------------------------------------ la clave


def test_la_clave_va_en_cabecera_y_no_a_url_cache_ni_log(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    f, t, _ = _fuente(tmp_path, monkeypatch)
    f.columnas("FICA", SENAL)
    f.columnas("ROTO", SENAL)
    for u, p in t.pedidos:
        if u == fh.URL_NOTICIAS:
            assert "token" not in p and TOKEN not in u
    assert any(h.get(fh.CABECERA_TOKEN) == TOKEN for h in t.cabeceras)
    archivos = list((tmp_path / "c").rglob("*.json"))
    assert archivos and all(TOKEN not in a.read_text(encoding="utf-8") for a in archivos)
    assert TOKEN not in caplog.text
    for n in NOTICIAS:
        reg = cargar("finnhub", n)
        assert "token" not in reg["params"] and TOKEN not in reg["texto"]


def test_error_auth_no_lleva_la_clave(tmp_path, monkeypatch):
    monkeypatch.setenv(fh.ENV_TOKEN, TOKEN)
    params = {"symbol": "FICA", "from": "2026-09-23", "to": "2026-09-25"}
    t = TransporteGrabado()
    t.agregar(fh.URL_NOTICIAS, params, {"status": 401, "headers": {}, "texto": '{"error": "Invalid API key"}'})
    f = fh.Finnhub(Cache(tmp_path / "c"), resultados.cliente_finnhub(transport=t, dormir=lambda s: None),
                   hoy=date(2026, 10, 1))
    assert f.noticias("FICA", date(2026, 9, 23), date(2026, 9, 25)) is None
    with pytest.raises(ErrorFuente) as ex:
        f._cliente.get(fh.URL_NOTICIAS, params, {fh.CABECERA_TOKEN: TOKEN})
    assert ex.value.codigo == "auth" and TOKEN not in str(ex.value)


# ------------------------------------------------------------ límite


def test_limitador_a_30_por_minuto(tmp_path, monkeypatch):
    f, t, dormidas = _fuente(tmp_path, monkeypatch)
    reloj = [0.0]
    lim = f._cliente.limitador
    lim._reloj = lambda: reloj[0]
    lim._dormir = lambda s: (dormidas.append(s), reloj.__setitem__(0, reloj[0] + s))
    for _ in range(31):
        lim.esperar()
    assert lim.llamadas == 30 and lim.segundos == 60.0
    assert dormidas and dormidas[-1] == pytest.approx(60.0)


def test_contrato_y_registro(tmp_path, monkeypatch):
    f, _, _ = _fuente(tmp_path, monkeypatch)
    r = Registro([f])
    assert r.nombres() == f.nombres() and all(n.startswith("finnhub_") for n in f.nombres())
    r.agregar(resultados.ResultadosFinnhub(f.cache, f._cliente))   # sin columnas repetidas con F2a
