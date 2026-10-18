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
from fuentes.http import Limitador

TOKEN = "clave-secreta-finnhub"
NOTICIAS = ["noticias_FICA_20260923", "noticias_FICA_20260924", "noticias_FICA_20260925", "noticias_FICA_20260927",
            "noticias_FICA_20260928", "noticias_ROTO_20260924", "noticias_SINHORA_20260924"]
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
        # Su día 25 (la ventana de 60 min de SENAL) también falla: cada día roto por su lado.
        t.fallar.add(TransporteGrabado.clave(fh.URL_NOTICIAS, _dia(sim, "2026-09-25")))
    dormidas: list[float] = []
    c = resultados.cliente_finnhub(transport=t, dormir=dormidas.append)
    return fh.Finnhub(Cache(tmp_path / "c"), c, hoy=hoy), t, dormidas


def _noticias(t: TransporteGrabado) -> list[dict]:
    return [p for u, p in t.pedidos if u == fh.URL_NOTICIAS]


def _dia(sim: str, d: str) -> dict:
    return {"symbol": sim, "from": d, "to": d}


def _articulos(n: int, desde_ts: int, sim: str = "MUCHO") -> str:
    """n artículos sintéticos, uno por minuto desde `desde_ts`."""
    import json
    return json.dumps([{"datetime": desde_ts + 60 * i, "id": 900000 + i, "headline": f"a{i}", "related": sim}
                       for i in range(n)])


# ------------------------------------------------------------ parser


def test_leer_noticias_quita_duplicados_y_lee_utc():
    import json
    lista = fh.leer_noticias(json.loads(cargar("finnhub", "noticias_FICA_20260924")["texto"]))
    assert [i for i, _ in lista] == [103, 102, 101]   # 4 artículos, el 103 repetido
    horas = dict(fh.leer_noticias(json.loads(cargar("finnhub", "noticias_FICA_20260925")["texto"])))
    assert horas[105] == datetime(2026, 9, 25, 13, 10, tzinfo=UTC)


def test_unir_deduplica_entre_dias():
    h = datetime(2026, 9, 24, 20, 5, tzinfo=UTC)
    otra = datetime(2026, 9, 25, 13, 10, tzinfo=UTC)
    assert fh.unir([[(103, h), (102, h)], [(105, otra), (103, h)]]) == [(103, h), (102, h), (105, otra)]


def test_cuerpo_de_error_o_articulo_sin_hora_invalida_la_respuesta():
    with pytest.raises(ErrorFuente, match="cuerpo"):
        fh.leer_noticias({"error": "You don't have access to this resource."})
    for roto in ([{"id": 1, "datetime": 0}], [{"id": 1}], [{"id": 1, "datetime": "1758800000"}],
                 [{"id": 1, "datetime": True}], ["texto"]):
        with pytest.raises(ErrorFuente, match="item_ilegible"):
            fh.leer_noticias(roto)
    assert fh.leer_noticias([]) == []


def test_dias_de_cubren_la_fecha_ny_y_la_utc():
    # 24 h antes de las 00:30 UTC del 25 = 20:30 NY del 23: del 23 (NY) al 25 (UTC).
    m = datetime(2026, 9, 25, 0, 30, tzinfo=UTC)
    assert fh.dias_de(m - fh.VENTANA_24H, m) == [date(2026, 9, 23), date(2026, 9, 24), date(2026, 9, 25)]
    assert fh.dias_de(SENAL - fh.VENTANA_24H, SENAL) == [date(2026, 9, 24), date(2026, 9, 25)]
    # 60 min antes de las 00:30 UTC del 25 son las 19:30 NY del 24: dos días.
    assert fh.dias_de(m - fh.VENTANA_60MIN, m) == [date(2026, 9, 24), date(2026, 9, 25)]
    assert fh.dias_de(SENAL - fh.VENTANA_60MIN, SENAL) == [date(2026, 9, 25)]


# ------------------------------------------------------ point-in-time


def test_columnas_solo_cuentan_lo_estrictamente_anterior(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", SENAL)
    # 24 h: 13:10 del 25, 20:05 del 24 (una vez) y 13:40:00 del 24 (borde inferior incluido).
    # Fuera: 13:40 del 25 (misma marca que la señal), 14:00 del 25 (posterior), 13:39:59 del 24.
    # El 103 llega bajo el 24 (dos veces) y bajo el 25: cuenta una vez.
    assert fila == {"finnhub_noticias_24h": 3, "finnhub_noticia_60min": True,
                    "finnhub_earnings_hoy": False, "finnhub_earnings_ayer": True}
    # Un pedido por día, from = to; la ventana de 60 min reusa el 25 sin volver a pedir.
    assert _noticias(t) == [_dia("FICA", "2026-09-24"), _dia("FICA", "2026-09-25")]


def test_ventana_de_tres_dias_se_parte_en_tres_pedidos(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", datetime(2026, 9, 25, 0, 30, tzinfo=UTC))
    assert _noticias(t) == [_dia("FICA", "2026-09-23"), _dia("FICA", "2026-09-24"), _dia("FICA", "2026-09-25")]
    # [24 00:30, 25 00:30): 103 (una vez), 102, 101; el 100 (23 22:00) queda antes.
    assert fila["finnhub_noticias_24h"] == 3 and fila["finnhub_noticia_60min"] is False


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
    assert len(_noticias(t)) == 2   # los días 24 y 25, una vez cada uno
    g = fh.Finnhub(f.cache, f._cliente, hoy=date(2026, 10, 1))
    assert g.columnas("FICA", SENAL)["finnhub_noticias_24h"] == 3
    assert len(_noticias(t)) == 2   # otra instancia: sale de la caché en disco


def test_dia_sin_articulos_dentro_de_la_historia_es_cero_afirmado(tmp_path, monkeypatch):
    f, _, _ = _fuente(tmp_path, monkeypatch)
    fila = f.columnas("FICA", datetime(2026, 9, 28, 13, 40, tzinfo=UTC))   # lunes
    assert fila["finnhub_noticias_24h"] == 0 and fila["finnhub_noticia_60min"] is False
    # Lunes: "ayer" es el viernes 25, sin reporte de FICA (el bmo del 25 es de OTRO).
    assert fila["finnhub_earnings_hoy"] is False and fila["finnhub_earnings_ayer"] is False


def test_dia_en_el_tope_es_faltante_solo_en_las_columnas_que_lo_tocan(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    ts24 = int(datetime(2026, 9, 24, 10, 0, tzinfo=UTC).timestamp())
    t.agregar(fh.URL_NOTICIAS, _dia("MUCHO", "2026-09-24"),
              {"status": 200, "headers": {}, "texto": _articulos(fh.TOPE_DIA, ts24)})
    t.agregar(fh.URL_NOTICIAS, _dia("MUCHO", "2026-09-25"),
              {"status": 200, "headers": {}, "texto": _articulos(3, int(SENAL.timestamp()) - 1800)})
    t.fallar.add(TransporteGrabado.clave(*_calendario("MUCHO")))
    fila = f.columnas("MUCHO", SENAL)
    # El 24 llegó con 240: puede faltar lo más viejo del día. El conteo de
    # 24 h lo toca: FALTANTE, no 240+3. La de 60 min solo mira el 25.
    assert fila["finnhub_noticias_24h"] is FALTANTE
    assert fila["finnhub_noticia_60min"] is True


def test_un_articulo_menos_que_el_tope_si_es_conteo(tmp_path, monkeypatch):
    f, t, _ = _fuente(tmp_path, monkeypatch)
    ts24 = int(datetime(2026, 9, 24, 14, 0, tzinfo=UTC).timestamp())
    t.agregar(fh.URL_NOTICIAS, _dia("MUCHO", "2026-09-24"),
              {"status": 200, "headers": {}, "texto": _articulos(fh.TOPE_DIA - 1, ts24)})
    t.agregar(fh.URL_NOTICIAS, _dia("MUCHO", "2026-09-25"), {"status": 200, "headers": {}, "texto": "[]"})
    t.fallar.add(TransporteGrabado.clave(*_calendario("MUCHO")))
    fila = f.columnas("MUCHO", SENAL)
    assert fila["finnhub_noticias_24h"] == fh.TOPE_DIA - 1 and fila["finnhub_noticia_60min"] is False


def test_el_tope_cuenta_articulos_crudos_antes_de_deduplicar():
    import json
    cuerpo = json.loads(_articulos(fh.TOPE_DIA, 1790208000))
    cuerpo[1]["id"] = cuerpo[0]["id"]
    assert fh.recortado(cuerpo) and not fh.recortado(cuerpo[:-1]) and not fh.recortado({"error": "x"})


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
    caida = TransporteGrabado.clave(fh.URL_NOTICIAS, _dia("FICA", "2026-09-24"))
    f, t, _ = _fuente(tmp_path, monkeypatch, fallar={caida})
    fila = f.columnas("FICA", SENAL)
    # Cayó el 24: el conteo de 24 h no se puede afirmar; la de 60 min solo mira el 25.
    assert fila["finnhub_noticias_24h"] is FALTANTE and fila["finnhub_noticia_60min"] is True
    assert fila["finnhub_earnings_ayer"] is True   # el calendario sí respondió
    # El 24 no se cachea; el 25 sí.
    assert len(list((tmp_path / "c").glob("finnhub_noticias/*.json"))) == 1


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
    params = _dia("FICA", "2026-09-23")
    t = TransporteGrabado()
    t.agregar(fh.URL_NOTICIAS, params, {"status": 401, "headers": {}, "texto": '{"error": "Invalid API key"}'})
    f = fh.Finnhub(Cache(tmp_path / "c"), resultados.cliente_finnhub(transport=t, dormir=lambda s: None),
                   hoy=date(2026, 10, 1))
    assert f.noticias_del_dia("FICA", date(2026, 9, 23)) is None
    with pytest.raises(ErrorFuente) as ex:
        f._cliente.get(fh.URL_NOTICIAS, params, {fh.CABECERA_TOKEN: TOKEN})
    assert ex.value.codigo == "auth" and TOKEN not in str(ex.value)


# ------------------------------------------------------------ límite


def test_limitador_a_50_por_minuto(tmp_path, monkeypatch):
    f, t, dormidas = _fuente(tmp_path, monkeypatch)
    reloj = [0.0]
    lim = f._cliente.limitador
    lim._reloj = lambda: reloj[0]
    lim._dormir = lambda s: (dormidas.append(s), reloj.__setitem__(0, reloj[0] + s))
    for _ in range(51):
        lim.esperar()
    assert lim.llamadas == 50 and lim.segundos == 60.0
    assert dormidas and dormidas[-1] == pytest.approx(60.0)


def test_f2a_y_f7_comparten_un_limitador_por_defecto(tmp_path):
    """Sin inyectar nada, todos los clientes de Finnhub del proceso usan el
    mismo limitador: F2a + F7 juntas no pasan de 50/min."""
    f7 = fh.Finnhub(Cache(tmp_path / "c"))
    f2a = resultados.ResultadosFinnhub(f7.cache)
    lim = resultados.limitador_finnhub()
    assert f7._cliente.limitador is lim and f2a.cliente().limitador is lim
    assert f7._calendario.cliente().limitador is lim
    assert lim.llamadas == resultados.LLAMADAS_FINNHUB_MIN == 50


def test_dos_clientes_con_el_limitador_compartido_suman_un_solo_tope(tmp_path, monkeypatch):
    monkeypatch.setenv(fh.ENV_TOKEN, TOKEN)
    reloj = [0.0]
    dormidas: list[float] = []

    def dormir(s):
        dormidas.append(s)
        reloj[0] += s

    lim = Limitador(resultados.LLAMADAS_FINNHUB_MIN, 60.0, reloj=lambda: reloj[0], dormir=dormir)
    t = TransporteGrabado()
    t.agregar("https://finnhub.test/x", None, {"status": 200, "headers": {}, "texto": "[]"})
    a = resultados.cliente_finnhub(transport=t, dormir=lambda s: None, limitador=lim)
    b = resultados.cliente_finnhub(transport=t, dormir=lambda s: None, limitador=lim)
    for i in range(50):
        (a if i % 2 else b).get("https://finnhub.test/x")
    assert dormidas == []
    a.get("https://finnhub.test/x")
    assert dormidas == [pytest.approx(60.0)]


def test_grabar_pide_dia_por_dia_y_avisa_el_tope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(fh.ENV_TOKEN, TOKEN)
    t = _ConCabeceras(transporte_desde("finnhub", ["noticias_FICA_20260925"]))
    ts24 = int(datetime(2026, 9, 24, 10, 0, tzinfo=UTC).timestamp())
    t.agregar(fh.URL_NOTICIAS, _dia("FICA", "2026-09-24"),
              {"status": 200, "headers": {}, "texto": _articulos(fh.TOPE_DIA + 5, ts24, "FICA")})
    monkeypatch.setattr(fh, "cliente_finnhub", lambda: resultados.cliente_finnhub(transport=t, dormir=lambda s: None))
    assert fh._grabar(["FICA", "2026-09-24", "2026-09-25", "--dir", str(tmp_path / "g")]) == 5
    assert _noticias(t) == [_dia("FICA", "2026-09-24"), _dia("FICA", "2026-09-25")]
    salida = capsys.readouterr().out
    assert "FICA 2026-09-24: 245 artículos" in salida and "TOPE" in salida.splitlines()[0]
    assert "TOPE" not in salida.splitlines()[1] and "2026-09-24. Sus columnas quedan FALTANTE" in salida
    grabados = sorted((tmp_path / "g" / "finnhub").glob("*.json"))
    assert [g.name for g in grabados] == ["noticias_FICA_20260924.json", "noticias_FICA_20260925.json"]
    assert all(TOKEN not in g.read_text(encoding="utf-8") for g in grabados)


def test_contrato_y_registro(tmp_path, monkeypatch):
    f, _, _ = _fuente(tmp_path, monkeypatch)
    r = Registro([f])
    assert r.nombres() == f.nombres() and all(n.startswith("finnhub_") for n in f.nombres())
    r.agregar(resultados.ResultadosFinnhub(f.cache, f._cliente))   # sin columnas repetidas con F2a
