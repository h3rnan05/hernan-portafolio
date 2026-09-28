"""Configuración de la estrategia v2 (PR 1): bandera, carga estricta y
los valores aprobados. Vive aquí porque CI ya corre esta carpeta."""

from __future__ import annotations

import ast
import copy
from datetime import time
from pathlib import Path

import pytest
import yaml

from estrategia_v2 import config as c

RAIZ = Path(__file__).resolve().parents[2]


def _crudo() -> dict:
    return yaml.safe_load(c.RUTA_DEFAULT.read_text(encoding="utf-8"))


def _escribir(tmp_path, crudo, nombre="v2.yaml") -> Path:
    p = tmp_path / nombre
    p.write_text(yaml.safe_dump(crudo, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _sin_bandera(monkeypatch):
    monkeypatch.delenv(c.ENV_BANDERA, raising=False)
    monkeypatch.delenv(c.ENV_RUTA, raising=False)


# ------------------------------------------------ valores aprobados (2026-09-28)


def test_los_valores_del_yaml_son_los_aprobados():
    """Cambiar un número de la v2 es una decisión de estrategia: si esta
    prueba falla, el cambio tiene que estar aprobado y la prueba se
    actualiza en el mismo PR."""
    cfg = c.cargar()
    u, k, s, r = cfg.universo, cfg.catalizador, cfg.senal, cfg.riesgo
    assert u.exchanges == ("NYSE", "NASDAQ", "AMEX")
    assert (u.excluir_otc, u.excluir_etf, u.excluir_spac) == (True, True, True)
    assert (u.precio_min, u.precio_max) == (2.0, 50.0)
    assert (u.float_min, u.float_max) == (5_000_000, 150_000_000)
    assert u.float_faltante == "excluir"
    assert u.minutos_sin_halt == 30
    assert u.accion_corporativa_hoy == "bloquear"

    assert k.fuente == "benzinga" and k.horas_maximas == 24
    assert k.niveles_operables == (1, 2) and k.direcciones_operables == ("alcista",)
    assert set(k.veto) == {"oferta_de_acciones", "dilucion", "going_concern", "investigacion_regulatoria"}
    assert "going concern" in k.veto["going_concern"]
    assert "public offering" in k.veto["oferta_de_acciones"]

    assert s.zona_horaria == "America/New_York"
    assert s.gap_min_pct == 0.04 and s.rvol_min == 3.0 and s.rvol_dias == 20
    assert s.precio_sobre_vwap and s.indice_sobre_vwap and s.indice_referencia == "SPY"
    assert (s.rango_apertura_inicio, s.rango_apertura_fin) == (time(9, 30), time(9, 35))
    assert s.vela_ruptura_vol_min_x == 1.5 and s.spread_max_pct == 0.003
    assert (s.ventana_inicio, s.ventana_fin) == (time(9, 36), time(11, 0))
    assert s.minutos_maximos_niveles == 5

    assert r.riesgo_pct_equity == 0.005 and r.tope_posicion_pct_equity == 0.25
    assert (r.stop_min_pct, r.stop_max_pct) == (0.015, 0.04)
    assert r.stop_origen == "minimo_rango_apertura" and r.stop_si_excede_max == "no_entrar"
    assert r.objetivo_r == 2.0 and r.breakeven_en_r == 1.0
    assert (r.stop_tiempo_minutos, r.stop_tiempo_r_minimo) == (30, 0.5)
    assert (r.max_posiciones, r.max_entradas_dia) == (4, 6)
    assert (r.freno_diario_pct, r.freno_semanal_pct) == (-0.015, -0.04)
    assert r.tamano_por_nivel == {1: 1.0, 2: 0.5}
    assert r.solo_largos and not r.ia_decide_entradas
    assert r.cierre_fin_de_dia == "como_v1"

    assert cfg.sombra.activa_con_v1 and str(cfg.sombra.directorio) == "/var/lib/momentum/v2_sombra"


# ----------------------------------------------------------------- bandera


@pytest.mark.parametrize("valor, esperado", [("", "v1"), ("v1", "v1"), ("V2", "v2"), ("v3", "v1")])
def test_bandera(monkeypatch, valor, esperado):
    monkeypatch.setenv(c.ENV_BANDERA, valor)
    assert c.estrategia_pedida() == esperado


def test_sin_bandera_es_v1():
    assert c.estrategia_pedida() == "v1"


def test_v1_con_yaml_valido_corre_la_sombra():
    e = c.estado()
    assert (e.modo, e.sombra, e.error) == ("v1", True, None) and e.config is not None


def test_v1_con_yaml_invalido_sigue_en_v1_sin_sombra(tmp_path):
    e = c.estado(tmp_path / "no_existe.yaml")
    assert (e.modo, e.sombra, e.config, e.error) == ("v1", False, None, "sin_archivo")


def test_v2_con_yaml_valido(monkeypatch):
    monkeypatch.setenv(c.ENV_BANDERA, "v2")
    e = c.estado()
    assert (e.modo, e.sombra) == ("v2", False)


def test_v2_con_yaml_invalido_bloquea_no_cae_a_v1(monkeypatch, tmp_path):
    monkeypatch.setenv(c.ENV_BANDERA, "v2")
    e = c.estado(tmp_path / "no_existe.yaml")
    assert (e.modo, e.config) == ("bloqueado", None)


def test_sombra_se_puede_apagar_desde_el_yaml(tmp_path):
    crudo = _crudo()
    crudo["sombra"]["activa_con_v1"] = False
    assert c.estado(_escribir(tmp_path, crudo)).sombra is False


def test_la_ruta_se_puede_fijar_por_entorno(monkeypatch, tmp_path):
    crudo = _crudo()
    crudo["senal"]["rvol_min"] = 4.0
    monkeypatch.setenv(c.ENV_RUTA, str(_escribir(tmp_path, crudo)))
    assert c.cargar().senal.rvol_min == 4.0


def test_sin_pyyaml_la_v2_queda_sin_configuracion(monkeypatch):
    import builtins
    real = builtins.__import__

    def _sin_yaml(nombre, *a, **kw):
        if nombre == "yaml":
            raise ImportError("sin yaml")
        return real(nombre, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _sin_yaml)
    e = c.estado()
    assert (e.modo, e.sombra, e.error) == ("v1", False, "sin_pyyaml")


# ------------------------------------------------------------- inválidos


def _mutar(ruta, valor):
    crudo = copy.deepcopy(_crudo())
    destino = crudo
    for clave in ruta[:-1]:
        destino = destino[clave]
    if valor is _BORRAR:
        del destino[ruta[-1]]
    else:
        destino[ruta[-1]] = valor
    return crudo


_BORRAR = object()


@pytest.mark.parametrize("ruta, valor, codigo", [
    (("senal", "rvol_min"), _BORRAR, "falta_campo"),
    (("senal", "rvol_mim"), 3.0, "campo_desconocido"),          # errata
    (("senal", "rvol_min"), "3", "valor_invalido"),
    (("senal", "gap_min_pct"), 4, "valor_invalido"),             # 4 no es 4 %
    (("senal", "spread_max_pct"), 0, "valor_invalido"),
    (("senal", "ventana_inicio"), 576, "valor_invalido"),        # 09:36 sin comillas en YAML 1.1
    (("senal", "ventana_fin"), "09:00", "valor_invalido"),       # ventana al revés
    (("senal", "zona_horaria"), "Marte/Olympus", "valor_invalido"),
    (("universo", "precio_min"), 60.0, "valor_invalido"),        # min > max
    (("universo", "float_faltante"), "cero", "valor_invalido"),  # faltante nunca es cero
    (("universo", "excluir_etf"), "si", "valor_invalido"),
    (("riesgo", "stop_min_pct"), 0.05, "valor_invalido"),
    (("riesgo", "freno_diario_pct"), 0.015, "valor_invalido"),   # un freno es una pérdida
    (("riesgo", "freno_semanal_pct"), -0.01, "valor_invalido"),  # más laxo que el diario
    (("riesgo", "max_posiciones"), 4.5, "valor_invalido"),
    (("riesgo", "max_entradas_dia"), 2, "valor_invalido"),       # menos entradas que posiciones
    (("riesgo", "tamano_por_nivel"), {1: 1.0}, "valor_invalido"),
    (("riesgo", "solo_largos"), False, "valor_invalido"),
    (("riesgo", "ia_decide_entradas"), True, "valor_invalido"),
    (("catalizador", "niveles_operables"), [0, 1], "valor_invalido"),
    (("catalizador", "fuente"), "yahoo", "valor_invalido"),
    (("catalizador", "veto"), {}, "valor_invalido"),
    (("sombra", "directorio"), "relativo/v2", "valor_invalido"),
    (("version",), 2, "version"),
])
def test_configuracion_invalida_se_rechaza_entera(tmp_path, ruta, valor, codigo):
    with pytest.raises(c.ConfigV2Invalida) as ex:
        c.cargar(_escribir(tmp_path, _mutar(ruta, valor)))
    assert ex.value.codigo == codigo


def test_sombra_dentro_del_repo_se_rechaza(tmp_path):
    crudo = _mutar(("sombra", "directorio"), str(RAIZ / "momentum_hunter" / "v2_sombra"))
    with pytest.raises(c.ConfigV2Invalida):
        c.cargar(_escribir(tmp_path, crudo))


def test_yaml_ilegible(tmp_path):
    p = tmp_path / "roto.yaml"
    p.write_text("senal: [sin cerrar", encoding="utf-8")
    with pytest.raises(c.ConfigV2Invalida) as ex:
        c.cargar(p)
    assert ex.value.codigo == "yaml_ilegible"


def test_el_veto_se_compara_en_minusculas(tmp_path):
    crudo = _mutar(("catalizador", "veto"), {"dilucion": ["Dilutive"]})
    assert c.cargar(_escribir(tmp_path, crudo)).catalizador.veto == {"dilucion": ("dilutive",)}


# ------------------------------------------------------- sin números mágicos


def test_el_codigo_v2_no_tiene_numeros_de_estrategia():
    """Todo número de la v2 vive en el YAML. En el código solo se aceptan
    0, 1 y 2 (fronteras de fracción y los niveles del catalizador, que
    son el esquema, no un umbral). Cuando entren los PRs de universo,
    señal y riesgo, sus carpetas se agregan aquí."""
    permitidos = {0, 1, 2, -1}
    carpetas = [RAIZ / "estrategia_v2"]
    hallados = []
    for carpeta in carpetas:
        for py in carpeta.rglob("*.py"):
            for nodo in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if (isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float))
                        and not isinstance(nodo.value, bool) and nodo.value not in permitidos):
                    hallados.append(f"{py.name}:{nodo.lineno}={nodo.value}")
    assert hallados == []
