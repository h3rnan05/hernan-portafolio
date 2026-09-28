"""Invariantes del paquete: FALTANTE, caché fuera de git, HTTP fail-closed,
límite de tasa, contrato de columnas y aislamiento."""

from __future__ import annotations

import ast
import json
import pickle
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest
import requests

from fuentes import FALTANTE, ErrorFuente, es_faltante
from fuentes import cache as cache_mod
from fuentes import columnas, tiempo
from fuentes.comun import entero, numero, serializable
from fuentes.grabar import TransporteGrabado, cargar, guardar
from fuentes.http import Cliente, Limitador

RAIZ = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------ FALTANTE


def test_faltante_no_es_none_ni_cero_ni_false():
    assert FALTANTE is not None
    assert FALTANTE != 0 and FALTANTE != False and FALTANTE != None  # noqa: E712,E711
    assert not FALTANTE
    assert es_faltante(FALTANTE) and not es_faltante(None) and not es_faltante(0)


def test_faltante_no_se_opera():
    with pytest.raises(TypeError):
        FALTANTE + 1
    with pytest.raises(TypeError):
        1 + FALTANTE
    with pytest.raises(TypeError):
        float(FALTANTE)
    with pytest.raises(TypeError):
        FALTANTE < 3


def test_faltante_es_singleton_incluso_tras_pickle():
    assert pickle.loads(pickle.dumps(FALTANTE)) is FALTANTE
    assert repr(FALTANTE) == "FALTANTE"


def test_serializable_marca_la_ausencia_y_respeta_el_cero():
    assert serializable({"a": FALTANTE, "b": [0, None, FALTANTE]}) == {"a": "FALTANTE", "b": [0, None, "FALTANTE"]}
    assert json.dumps(serializable({"x": FALTANTE}))


def test_numero_y_entero():
    assert numero(0) == 0.0 and numero(True) is None and numero("3") is None and numero(float("nan")) is None
    assert entero(3.0) == 3 and entero(3.5) is None and entero(None) is None


# --------------------------------------------------------------- caché


def test_cache_rechaza_el_repo(tmp_path):
    with pytest.raises(ValueError, match="cache_dentro_del_repo"):
        cache_mod.Cache(RAIZ / "fuentes" / "cache_local")
    with pytest.raises(ValueError):
        cache_mod.Cache(RAIZ)
    cache_mod.Cache(tmp_path / "ok")


def test_cache_default_viene_del_entorno(tmp_path, monkeypatch):
    monkeypatch.setenv(cache_mod.ENV_DIR, str(tmp_path / "x"))
    assert cache_mod.Cache().dir == tmp_path / "x"
    monkeypatch.delenv(cache_mod.ENV_DIR)
    assert cache_mod.Cache().dir == cache_mod.DIR_DEFAULT


def test_cache_guarda_y_no_repite_el_calculo(cache):
    llamadas = []

    def calc():
        llamadas.append(1)
        return {"n": 1}

    assert cache.obtener("f", "k", calc) == {"n": 1}
    assert cache.obtener("f", "k", calc) == {"n": 1}
    assert len(llamadas) == 1


def test_cache_no_guarda_un_fallo(cache):
    def calc():
        raise ErrorFuente("red", "f")

    with pytest.raises(ErrorFuente):
        cache.obtener("f", "k", calc)
    assert cache.leer("f", "k") is None


def test_cache_expira_y_un_archivo_roto_se_ignora(tmp_path):
    reloj = [1000.0]
    c = cache_mod.Cache(tmp_path / "c", ahora=lambda: reloj[0])
    c.escribir("f", "k", 5)
    assert c.leer("f", "k", max_edad_s=10)["valor"] == 5
    reloj[0] += 11
    assert c.leer("f", "k", max_edad_s=10) is None
    assert c.leer("f", "k")["valor"] == 5
    c._ruta("f", "k").write_text("{roto", encoding="utf-8")
    assert c.leer("f", "k") is None


# ---------------------------------------------------------------- HTTP


def _cliente(transport, **kw):
    return Cliente("prueba", "hernan-portafolio pruebas", transport=transport, dormir=lambda s: None, **kw)


def test_cliente_exige_user_agent():
    with pytest.raises(ErrorFuente, match="sin_user_agent"):
        Cliente("prueba", "  ")


def test_cliente_devuelve_texto_y_json():
    t = TransporteGrabado()
    t.agregar("https://x.test/a", {"q": "1"}, {"status": 200, "headers": {}, "texto": '{"ok": true}'})
    c = _cliente(t)
    assert c.get_json("https://x.test/a", {"q": "1"}) == {"ok": True}
    assert t.pedidos[0][1] == {"q": "1"}
    assert "User-Agent" not in t.pedidos[0][1]


def test_cliente_reintenta_429_y_5xx_y_luego_falla_por_codigo():
    t = TransporteGrabado({"https://x.test/a": {"status": 429, "headers": {"Retry-After": "2"}, "texto": ""}})
    esperas = []
    c = Cliente("prueba", "ua", transport=t, dormir=esperas.append, reintentos=3)
    with pytest.raises(ErrorFuente) as ex:
        c.get("https://x.test/a")
    assert ex.value.codigo == "http_429"
    assert len(t.pedidos) == 3 and esperas == [2.0, 2.0]


def test_cliente_red_caida_es_error_red_sin_texto():
    t = TransporteGrabado(fallar={"https://x.test/a"})
    with pytest.raises(ErrorFuente) as ex:
        _cliente(t).get("https://x.test/a")
    assert ex.value.codigo == "red"
    assert "x.test" not in str(ex.value)


def test_cliente_401_es_auth_y_404_es_http_404():
    t = TransporteGrabado({"https://x.test/a": {"status": 401, "headers": {}, "texto": ""},
                           "https://x.test/b": {"status": 404, "headers": {}, "texto": ""}})
    with pytest.raises(ErrorFuente, match="auth"):
        _cliente(t).get("https://x.test/a")
    with pytest.raises(ErrorFuente, match="http_404"):
        _cliente(t).get("https://x.test/b")


def test_cliente_json_ilegible_es_cuerpo():
    t = TransporteGrabado({"https://x.test/a": {"status": 200, "headers": {}, "texto": "<html>"}})
    with pytest.raises(ErrorFuente, match="cuerpo"):
        _cliente(t).get_json("https://x.test/a")


def test_transporte_grabado_no_inventa_respuestas():
    with pytest.raises(AssertionError, match="no grabado"):
        TransporteGrabado()("https://x.test/nada")


def test_las_pruebas_no_salen_a_la_red():
    with pytest.raises(AssertionError):
        requests.get("https://example.com")


def test_limitador_respeta_la_ventana():
    reloj = [0.0]
    dormidas = []

    def dormir(s):
        dormidas.append(s)
        reloj[0] += s

    lim = Limitador(2, 1.0, reloj=lambda: reloj[0], dormir=dormir)
    assert lim.esperar() == 0.0
    reloj[0] = 0.3
    assert lim.esperar() == 0.0
    reloj[0] = 0.5
    assert lim.esperar() == pytest.approx(0.5)
    assert dormidas == [pytest.approx(0.5)]
    reloj[0] = 5.0
    assert lim.esperar() == 0.0
    with pytest.raises(ValueError):
        Limitador(0, 1)


def test_limitador_se_aplica_en_cada_intento():
    reloj = [0.0]
    lim = Limitador(1, 60.0, reloj=lambda: reloj[0], dormir=lambda s: reloj.__setitem__(0, reloj[0] + s))
    t = TransporteGrabado({"https://x.test/a": {"status": 503, "headers": {}, "texto": ""}})
    c = Cliente("prueba", "ua", limitador=lim, transport=t, dormir=lambda s: None, reintentos=2)
    with pytest.raises(ErrorFuente):
        c.get("https://x.test/a")
    assert reloj[0] >= 60.0


# ------------------------------------------------------------ grabadora


def test_guardar_y_cargar_respuesta(tmp_path):
    ruta = guardar("f", "n", "https://x.test", {"a": 1}, 200, {"Content-Type": "x", "Set-Cookie": "no"},
                   "cuerpo", ficticio=True, nota="prueba", directorio=tmp_path)
    reg = json.loads(ruta.read_text(encoding="utf-8"))
    assert reg["ficticio"] is True and reg["headers"] == {"Content-Type": "x"} and reg["texto"] == "cuerpo"


def test_todas_las_respuestas_grabadas_declaran_si_son_ficticias():
    base = RAIZ / "fuentes" / "tests" / "respuestas"
    for ruta in base.rglob("*.json"):
        reg = json.loads(ruta.read_text(encoding="utf-8"))
        assert isinstance(reg.get("ficticio"), bool), ruta
        assert isinstance(reg.get("texto"), str) and "status" in reg, ruta
        if reg["ficticio"]:
            assert reg.get("nota"), f"{ruta}: una respuesta ficticia dice por qué lo es"


def test_cargar_levanta_si_no_existe():
    with pytest.raises(FileNotFoundError):
        cargar("no", "existe")


# --------------------------------------------------------------- tiempo


def test_tiempo_formato_utc_y_monterrey():
    dt = datetime(2026, 9, 25, 20, 5, tzinfo=UTC)
    assert tiempo.fmt_utc_mty(dt) == "2026-09-25 20:05 UTC (14:05 Monterrey, UTC−6)"
    assert tiempo.ny(date(2026, 9, 25), time(9, 30)) == datetime(2026, 9, 25, 13, 30, tzinfo=UTC)
    assert tiempo.ny(date(2026, 1, 15), time(9, 30)) == datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_tiempo_iso_sin_zona_no_se_adivina():
    assert tiempo.leer_iso_utc("2026-09-25T16:05:00") is None
    assert tiempo.leer_iso_utc("2026-09-25T16:05:00Z") == datetime(2026, 9, 25, 16, 5, tzinfo=UTC)
    assert tiempo.leer_iso_utc("basura") is None and tiempo.leer_iso_utc(None) is None
    assert tiempo.leer_fecha("2026-09-25T00:00") == date(2026, 9, 25) and tiempo.leer_fecha(5) is None
    with pytest.raises(ValueError):
        tiempo.a_utc(datetime(2026, 1, 1))


def test_dias_habiles():
    assert tiempo.dias_habiles_despues(date(2026, 9, 25), 1) == date(2026, 9, 28)  # viernes -> lunes
    assert tiempo.dias_habiles_despues(date(2026, 9, 21), 8) == date(2026, 10, 1)


# ------------------------------------------------------------- columnas


class _FuenteOk:
    nombre = "ok"

    def nombres(self):
        return ["ok_a", "ok_b"]

    def columnas(self, ticker, momento):
        return {"ok_a": 1}


class _FuenteRota:
    nombre = "rota"

    def nombres(self):
        return ["rota_x"]

    def columnas(self, ticker, momento):
        raise RuntimeError("bug")


def test_registro_rellena_faltante_y_aisla_un_bug():
    r = columnas.Registro([_FuenteOk(), _FuenteRota()])
    fila = r.columnas("ABC", datetime(2026, 9, 25, 14, 0, tzinfo=UTC))
    assert fila == {"ok_a": 1, "ok_b": FALTANTE, "rota_x": FALTANTE}
    assert r.nombres() == ["ok_a", "ok_b", "rota_x"]


def test_registro_rechaza_columnas_repetidas_y_objetos_sin_contrato():
    r = columnas.Registro([_FuenteOk()])
    with pytest.raises(ValueError, match="repetidas"):
        r.agregar(_FuenteOk())
    with pytest.raises(TypeError):
        r.agregar(object())


# ---------------------------------------------------------- aislamiento


def _imports(path: Path) -> list[str]:
    arbol = ast.parse(path.read_text(encoding="utf-8"))
    mods = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            mods += [a.name for a in nodo.names]
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            mods.append(nodo.module)
    return mods


def test_fuentes_no_importa_el_paper_trader_ni_el_host_de_trading():
    for path in (RAIZ / "fuentes").rglob("*.py"):
        if path.name.startswith("test_"):
            continue  # esta prueba nombra los hosts para prohibirlos
        texto = path.read_text(encoding="utf-8")
        for host in ("paper-api.alpaca.markets", "https://api.alpaca.markets", "broker-api.alpaca.markets"):
            assert host not in texto, (path, host)
        for mod in _imports(path):
            assert not mod.startswith("momentum_paper_trader"), path
            assert not mod.startswith("shadow_alpaca"), path  # el enriquecedor recibe objetos, no importa el motor


def test_el_hunter_no_importa_fuentes_hasta_que_una_se_apruebe():
    for path in (RAIZ / "momentum_hunter").rglob("*.py"):
        for mod in _imports(path):
            assert mod != "fuentes" and not mod.startswith("fuentes."), path


def test_el_ci_corre_estas_pruebas():
    texto = (RAIZ / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "fuentes/tests" in texto


def test_el_paquete_ignora_su_cache_en_git():
    assert "/var/lib/momentum/fuentes" in (RAIZ / "fuentes" / "__init__.py").read_text(encoding="utf-8")
