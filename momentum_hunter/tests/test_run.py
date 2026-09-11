"""Pruebas del filtro de universo en dos bandas (small-cap de siempre +
large-cap complementaria, pedido 2026-08-07 tras el gap de 17% de ABNB)
-- sin red, con un `DataProvider` falso (mismo patrón que
`test_outcomes.py`)."""

from __future__ import annotations

import json
import sys

from momentum_hunter import run as run_mod
from momentum_hunter import telemetria
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import Barras, BarraIntradia, FactoresMomentum, Metadata
from momentum_hunter.run import (
    _banda_de_universo,
    _clasificar_banda_de_universo,
    _construir_candidato_intradia,
    construir_candidatos_diarios,
    construir_candidatos_intradia,
    tamano_estimado,
)
from momentum_hunter.scoring import Puntuacion

CFG = MomentumConfig()


def _barras(ticker: str, precio: float, vol_prom: float, n: int = 25) -> Barras:
    fechas = [str(1_700_000_000 + i * 86_400) for i in range(n)]
    closes = [precio] * n
    return Barras(ticker, fechas, closes, closes, closes, closes, [vol_prom] * n)


class _FakeProvider(DataProvider):
    def __init__(self, metadata: dict[str, Metadata]) -> None:
        self._metadata = metadata

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {t: self._metadata[t] for t in tickers if t in self._metadata}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


# ------------------------- _banda_de_universo -------------------------

def test_banda_small_con_precio_y_volumen_dentro_del_rango():
    b = _barras("PENNY", precio=5.0, vol_prom=500_000.0)
    assert _banda_de_universo(b, CFG) == "small"


def test_banda_none_si_volumen_insuficiente_en_rango_small():
    b = _barras("ILIQUIDO", precio=5.0, vol_prom=1_000.0)
    assert _banda_de_universo(b, CFG) is None


def test_banda_large_si_precio_por_encima_del_techo_small_y_liquido():
    b = _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)
    assert _banda_de_universo(b, CFG) == "large"


def test_banda_none_si_precio_alto_pero_iliquido():
    b = _barras("CARO_ILIQUIDO", precio=178.0, vol_prom=100.0)
    assert _banda_de_universo(b, CFG) is None


def test_banda_none_si_incluir_large_cap_apagado():
    cfg = MomentumConfig(incluir_large_cap=False)
    b = _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)
    assert _banda_de_universo(b, cfg) is None


def test_banda_none_sin_barras_o_precio_invalido():
    assert _banda_de_universo(Barras("X", [], [], [], [], [], []), CFG) is None


# Motivos de rechazo -- espejo de las ramas de `_banda_de_universo`.
# No cambian el filtro: `_banda_de_universo` sigue devolviendo lo mismo.

def test_motivo_sin_close_si_no_hay_barras_o_precio_invalido():
    assert _clasificar_banda_de_universo(Barras("X", [], [], [], [], [], []), CFG) == (None, "sin_close")
    cero = _barras("CERO", precio=0.0, vol_prom=500_000.0)
    assert _banda_de_universo(cero, CFG) is None
    assert _clasificar_banda_de_universo(cero, CFG) == (None, "sin_close")


def test_motivo_precio_bajo_por_debajo_del_piso():
    b = _barras("CENTAVO", precio=0.50, vol_prom=500_000.0)
    assert _banda_de_universo(b, CFG) is None
    assert _clasificar_banda_de_universo(b, CFG) == (None, "precio_bajo")


def test_motivo_vol_bajo_small_en_rango_de_precio():
    b = _barras("ILIQUIDO", precio=5.0, vol_prom=1_000.0)
    assert _banda_de_universo(b, CFG) is None
    assert _clasificar_banda_de_universo(b, CFG) == (None, "vol_bajo_small")


def test_motivo_vol_insuficiente_historial_si_faltan_velas():
    # `_volumen_promedio` pide 20 sesiones -- con menos no hay vol20,
    # y el filtro original ya devolvía None (no se inventa un 0).
    b = _barras("CORTO", precio=5.0, vol_prom=500_000.0, n=10)
    assert _banda_de_universo(b, CFG) is None
    assert _clasificar_banda_de_universo(b, CFG) == (None, "vol_insuficiente_historial")


def test_motivo_vol_bajo_large_sobre_el_techo_small():
    b = _barras("CARO_ILIQUIDO", precio=178.0, vol_prom=100.0)
    assert _banda_de_universo(b, CFG) is None
    assert _clasificar_banda_de_universo(b, CFG) == (None, "vol_bajo_large")


def test_motivo_precio_fuera_rango_small_si_large_cap_apagado():
    cfg = MomentumConfig(incluir_large_cap=False)
    b = _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)
    assert _banda_de_universo(b, cfg) is None
    assert _clasificar_banda_de_universo(b, cfg) == (None, "precio_fuera_rango_small")


def test_clasificar_aceptados_no_inventa_motivo():
    small = _barras("PENNY", precio=5.0, vol_prom=500_000.0)
    large = _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)
    assert _clasificar_banda_de_universo(small, CFG) == ("small", None)
    assert _clasificar_banda_de_universo(large, CFG) == ("large", None)


# ------------------------- construir_candidatos_diarios -------------------------

def test_large_cap_no_se_descarta_por_market_cap_max():
    barras = {"ABNB": _barras("ABNB", precio=178.0, vol_prom=2_000_000.0)}
    meta = {"ABNB": Metadata(ticker="ABNB", market_cap=100_000_000_000.0)}  # muy por
    # encima de market_cap_max -- en small-cap esto se descartaría.
    provider = _FakeProvider(meta)
    candidatos = construir_candidatos_diarios(
        ["ABNB"], barras, provider, CFG, con_catalizadores=False, bandas={"ABNB": "large"},
    )
    assert len(candidatos) == 1
    assert candidatos[0].es_large_cap is True


def test_small_cap_se_sigue_descartando_por_market_cap_max():
    # Mismo market_cap alto, pero SIN banda "large" (comportamiento de
    # siempre) -- debe descartarse igual que antes de este cambio.
    barras = {"TST": _barras("TST", precio=5.0, vol_prom=500_000.0)}
    meta = {"TST": Metadata(ticker="TST", market_cap=100_000_000_000.0)}
    provider = _FakeProvider(meta)
    metricas = telemetria.Metricas()
    candidatos = construir_candidatos_diarios(
        ["TST"], barras, provider, CFG, con_catalizadores=False, bandas={"TST": "small"},
        metricas=metricas,
    )
    assert candidatos == []
    assert metricas.rechazos_universo["market_cap"] == 1


# ------------------------- bug real 2026-08-21: el techo de tamaño se saltaba -------------------------
# `meta.market_cap is not None and meta.market_cap > techo` hacía
# cortocircuito cuando Yahoo NO mandaba la capitalización (51% de las
# veces, medido sobre 3.161 candidatas auditadas). Resultado real: NOK
# (~$44 mil millones, 4.443M de float) entró seis veces a la banda
# small-cap. La banda "small" filtraba de hecho por PRECIO, no por
# tamaño de empresa.

def test_tamano_usa_market_cap_cuando_existe():
    b = _barras("X", precio=10.0, vol_prom=500_000.0)
    valor, origen = tamano_estimado(Metadata(ticker="X", market_cap=1_500_000_000.0), b)
    assert valor == 1_500_000_000.0
    assert origen == "market_cap"


def test_tamano_cae_a_precio_por_float_sin_market_cap():
    # El caso NOK exacto: sin capitalización, pero con float enorme.
    b = _barras("NOK", precio=9.97, vol_prom=5_000_000.0)
    valor, origen = tamano_estimado(
        Metadata(ticker="NOK", market_cap=None, shares_float=4_443_588_231.0), b)
    assert origen == "precio x float"
    assert valor == 4_443_588_231.0 * 9.97   # ~$44,3 mil millones


def test_tamano_none_cuando_no_hay_ni_cap_ni_float():
    b = _barras("X", precio=10.0, vol_prom=500_000.0)
    valor, origen = tamano_estimado(Metadata(ticker="X", market_cap=None, shares_float=None), b)
    assert valor is None
    assert origen == "sin dato"


def test_small_cap_sin_market_cap_pero_float_enorme_se_descarta():
    # La regresión que importa: antes esto PASABA el filtro y contaminaba
    # la banda small-cap con una mega-cap barata.
    barras = {"NOK": _barras("NOK", precio=9.97, vol_prom=5_000_000.0)}
    meta = {"NOK": Metadata(ticker="NOK", market_cap=None, shares_float=4_443_588_231.0)}
    candidatos = construir_candidatos_diarios(
        ["NOK"], barras, _FakeProvider(meta), CFG, con_catalizadores=False, bandas={"NOK": "small"},
    )
    assert candidatos == []


def test_small_cap_de_verdad_sin_market_cap_sigue_pasando():
    # El fix no debe cerrarle la puerta a una small-cap legítima cuyo
    # market_cap Yahoo no manda: 15M de float x $4 = $60M, bien bajo el techo.
    barras = {"TINY": _barras("TINY", precio=4.0, vol_prom=500_000.0)}
    meta = {"TINY": Metadata(ticker="TINY", market_cap=None, shares_float=15_000_000.0)}
    candidatos = construir_candidatos_diarios(
        ["TINY"], barras, _FakeProvider(meta), CFG, con_catalizadores=False, bandas={"TINY": "small"},
    )
    assert len(candidatos) == 1


def test_small_cap_con_tamano_no_verificable_se_descarta():
    # Fail-closed: sin capitalización NI float no se puede comprobar que
    # sea small-cap, y el techo es lo único que define esa banda.
    barras = {"???": _barras("???", precio=4.0, vol_prom=500_000.0)}
    meta = {"???": Metadata(ticker="???", market_cap=None, shares_float=None)}
    candidatos = construir_candidatos_diarios(
        ["???"], barras, _FakeProvider(meta), CFG, con_catalizadores=False, bandas={"???": "small"},
    )
    assert candidatos == []


def test_large_cap_se_salta_el_techo_aunque_falte_el_market_cap():
    # La banda large NO debe verse afectada por el fix: ese techo es
    # justamente lo que la define.
    barras = {"BIG": _barras("BIG", precio=178.0, vol_prom=2_000_000.0)}
    meta = {"BIG": Metadata(ticker="BIG", market_cap=None, shares_float=4_000_000_000.0)}
    candidatos = construir_candidatos_diarios(
        ["BIG"], barras, _FakeProvider(meta), CFG, con_catalizadores=False, bandas={"BIG": "large"},
    )
    assert len(candidatos) == 1
    assert candidatos[0].es_large_cap is True


def test_sin_bandas_se_comporta_como_small_cap_de_siempre():
    # Compatibilidad: llamar sin `bandas` (como antes de este cambio) no
    # debe darle un pase gratis a nada -- se sigue aplicando market_cap_max.
    barras = {"TST": _barras("TST", precio=5.0, vol_prom=500_000.0)}
    meta = {"TST": Metadata(ticker="TST", market_cap=100_000_000_000.0)}
    provider = _FakeProvider(meta)
    candidatos = construir_candidatos_diarios(["TST"], barras, provider, CFG, con_catalizadores=False)
    assert candidatos == []


# ------------------------- construir_candidatos_intradia -------------------------
# ("Fase 2", 2026-08-11): cubre el núcleo compartido `_construir_candidato_intradia`
# extraído de este bucle, y las garantías de robustez del docstring original
# ("un ticker que falle no tumba la corrida completa").

def _bi_regular(ticker: str, n: int = 3) -> BarraIntradia:
    marcas = [f"2026-08-11T14:{30 + i:02d}:00+00:00" for i in range(n)]
    closes = [5.20 + i * 0.01 for i in range(n)]
    return BarraIntradia(ticker, marcas, closes, closes, closes, closes, [5_000.0] * n)


def _barras_diarias(ticker: str, cierre_ayer: float = 4.80) -> Barras:
    fechas = [str(1_754_870_400 + i * 86_400) for i in range(5)]   # termina el 2026-08-11
    closes = [cierre_ayer] * 4 + [5.22]
    return Barras(ticker, fechas, closes, closes, closes, closes, [500_000.0] * 5)


def _candidato_diario_intradia(ticker: str) -> CandidatoDiario:
    return CandidatoDiario(
        ticker=ticker, nombre=None, precio=5.20, volumen_promedio=500_000.0,
        factores=FactoresMomentum(atr=0.30),
        catalizador=Catalizador(tipo="contrato", titular="x", fuente="Reuters",
                                 fecha="2026-08-11T13:45:00+00:00"),
        meta=Metadata(ticker=ticker), puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
    )


class _FakeProviderIntradia(DataProvider):
    def __init__(self, barras: dict[str, BarraIntradia]) -> None:
        self._barras = barras

    def barras(self, tickers, dias=280):
        return {}

    def metadata(self, tickers):
        return {}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {t: self._barras[t] for t in tickers if t in self._barras}


def test_construir_candidatos_intradia_devuelve_un_candidato_por_ticker_con_datos():
    shortlist = [_candidato_diario_intradia("RKLB")]
    barras_diarias = {"RKLB": _barras_diarias("RKLB")}
    provider = _FakeProviderIntradia({"RKLB": _bi_regular("RKLB")})
    candidatos = construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG)
    assert len(candidatos) == 1
    assert candidatos[0].ticker == "RKLB"


def test_construir_candidatos_intradia_llama_on_datos_recibidos_tras_el_fetch():
    # Bug real encontrado en revisión de PR (2026-08-11, sexta vuelta):
    # `main()` necesita un reloj capturado DESPUÉS de que las velas
    # intradía llegan (no antes de pedirlas, que atribuye mal el tiempo
    # de descarga) -- este callback es lo que se lo permite sin romper
    # la firma existente.
    shortlist = [_candidato_diario_intradia("RKLB")]
    barras_diarias = {"RKLB": _barras_diarias("RKLB")}
    provider = _FakeProviderIntradia({"RKLB": _bi_regular("RKLB")})
    llamadas = []
    construir_candidatos_intradia(
        shortlist, barras_diarias, provider, CFG, on_datos_recibidos=lambda: llamadas.append(1))
    assert llamadas == [1]


def test_construir_candidatos_intradia_ignora_ticker_sin_barras_del_proveedor():
    shortlist = [_candidato_diario_intradia("SINDATOS")]
    barras_diarias = {"SINDATOS": _barras_diarias("SINDATOS")}
    provider = _FakeProviderIntradia({})   # el proveedor no devolvió nada para este ticker
    assert construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG) == []


def test_construir_candidatos_intradia_ignora_ticker_sin_velas_de_hoy():
    shortlist = [_candidato_diario_intradia("VACIO")]
    barras_diarias = {"VACIO": _barras_diarias("VACIO")}
    bi_vacia = BarraIntradia("VACIO", [], [], [], [], [], [])
    provider = _FakeProviderIntradia({"VACIO": bi_vacia})
    assert construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG) == []


def test_construir_candidatos_intradia_un_ticker_roto_no_tumba_el_resto():
    # A "ROTO" le falta su entrada en `barras_diarias` -- el KeyError de
    # `_cierre_anterior` lo descarta (try/except del bucle), pero "OK" se
    # sigue evaluando con normalidad.
    shortlist = [_candidato_diario_intradia("ROTO"), _candidato_diario_intradia("OK")]
    barras_diarias = {"OK": _barras_diarias("OK")}
    provider = _FakeProviderIntradia({"ROTO": _bi_regular("ROTO"), "OK": _bi_regular("OK")})
    candidatos = construir_candidatos_intradia(shortlist, barras_diarias, provider, CFG)
    assert [c.ticker for c in candidatos] == ["OK"]


def test_construir_candidato_intradia_none_sin_velas_de_hoy():
    bi_vacia = BarraIntradia("X", [], [], [], [], [], [])
    resultado = _construir_candidato_intradia(
        "X", None, None, Metadata(ticker="X"), False, 0.30, 88.0, None, bi_vacia, CFG)
    assert resultado is None


def test_construir_candidato_intradia_deriva_cierre_anterior_de_bi_sin_barras_diarias():
    # Bug real encontrado en revisión de PR (2026-08-11): sin esto, una
    # candidata descubierta ANTES de la apertura regular (sin gap
    # congelado todavía) se quedaba sin gap para siempre en el chequeo
    # liviano de la watchlist, que nunca pasa `cierre_anterior` (no pide
    # barras diarias). Ahora se deriva directo de las velas intradía.
    marcas = ["2026-08-10T13:30:00+00:00", "2026-08-10T19:59:00+00:00"] + [
        f"2026-08-11T14:{30 + i:02d}:00+00:00" for i in range(3)
    ]
    cierres_ayer = [4.00, 4.00]
    cierres_hoy = [4.60, 4.65, 4.70]   # gap real: (4.60 - 4.00) / 4.00 = 15%
    closes = cierres_ayer + cierres_hoy
    bi = BarraIntradia("RKLB", marcas, closes, closes, closes, closes, [5_000.0] * len(marcas))

    candidato = _construir_candidato_intradia(
        "RKLB", None, None, Metadata(ticker="RKLB"), False, 0.30, 88.0,
        None, bi, CFG,   # cierre_anterior=None, sin gap_pct_fallback tampoco
    )
    assert candidato is not None
    assert candidato.factores.gap_pct is not None
    assert abs(candidato.factores.gap_pct - 0.15) < 1e-9


def test_construir_candidato_intradia_usa_el_gap_congelado_si_no_hay_cierre_anterior():
    # Sin `cierre_anterior` (None, como en el chequeo liviano de la
    # watchlist) `fi.calcular` no puede derivar el gap solo -- debe caer
    # al `gap_pct_fallback` congelado en vez de perderlo.
    bi = _bi_regular("RKLB")
    candidato = _construir_candidato_intradia(
        "RKLB", None, None, Metadata(ticker="RKLB"), False, 0.30, 88.0, None, bi, CFG,
        gap_pct_fallback=0.10,
    )
    assert candidato is not None
    assert candidato.factores.gap_pct == 0.10


# ------------------------- telemetría en silencios de main() -------------------------
# Bug 2026-09-11: return temprano con shortlist vacía se saltaba
# `registrar_corrida`. GHA 34636830680 terminó en `nothing to persist`.

def _jsonl_de_escaneo(tmp_path):
    hallados = list(tmp_path.glob("*/*/events.jsonl"))
    assert len(hallados) == 1, hallados
    lineas = [json.loads(l) for l in hallados[0].read_text().splitlines() if l.strip()]
    assert lineas
    return hallados[0], lineas


class _FakeProviderEscaneo(DataProvider):
    def __init__(self, barras):
        self._barras = barras

    def barras(self, tickers, dias=280):
        return {t: self._barras[t] for t in tickers if t in self._barras}

    def metadata(self, tickers):
        return {t: Metadata(ticker=t, market_cap=100_000_000.0) for t in tickers}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        return {}


def _preparar_main_escaneo(monkeypatch, tmp_path, barras, argv=None, diarios=None):
    monkeypatch.setattr(sys, "argv", argv or ["momentum_hunter.run"])
    # Default de `registrar_corrida` se fija al importar -- parchear
    # DIR_TELEMETRIA no alcanza; se redirige la escritura a tmp_path.
    real = telemetria.registrar_corrida
    monkeypatch.setattr(
        run_mod.telemetria, "registrar_corrida",
        lambda m, dir_telemetria=tmp_path, ahora=None, fuente=None: real(
            m, dir_telemetria=tmp_path, ahora=ahora, fuente=fuente),
    )
    monkeypatch.setattr(run_mod, "_cargar_tickers", lambda args: list(barras))
    monkeypatch.setattr(run_mod.universe, "tickers", lambda **kw: list(barras))
    monkeypatch.setattr(run_mod, "YahooProvider", lambda: _FakeProviderEscaneo(barras))
    monkeypatch.setattr(run_mod, "_revisar_resumen_cierre", lambda *a, **k: None)
    if diarios is not None:
        monkeypatch.setattr(run_mod, "construir_candidatos_diarios", diarios)
    return barras


def test_main_shortlist_vacia_igual_escribe_telemetria_jsonl(monkeypatch, tmp_path, caplog):
    barras = {
        "OK": _barras("OK", precio=5.0, vol_prom=500_000.0),
        "ILIQUIDO": _barras("ILIQUIDO", precio=5.0, vol_prom=1_000.0),
    }

    def _diarios(validos, barras, provider, cfg, con_cat, bandas=None, metricas=None):
        if metricas is not None:
            for t in validos:
                metricas.sumar(metricas.operables, "small")
            metricas.titulares_total = 12
            metricas.sumar(metricas.con_alguna_noticia, "small", n=len(validos))
        return []

    _preparar_main_escaneo(monkeypatch, tmp_path, barras, diarios=_diarios)
    caplog.set_level("INFO", logger="momentum_hunter.run")

    run_mod.main()

    path, lineas = _jsonl_de_escaneo(tmp_path)
    assert path.parent.name in telemetria.FUENTES_VALIDAS
    assert len(lineas) == 1
    embudo = lineas[0]["embudo"]
    assert embudo["operables"] == {"small": 1}          # ILIQUIDO no entra a etapa 1
    assert embudo["con_alguna_noticia"] == {"small": 1}
    assert embudo["titulares_total"] == 12
    assert embudo["con_catalizador"] == {}
    assert embudo["rechazos_universo"] == {"vol_bajo_small": 1}
    assert "operables=1" in caplog.text
    assert "vol_bajo_small" in caplog.text


def test_main_sin_validos_igual_escribe_rechazos_en_jsonl(monkeypatch, tmp_path):
    barras = {
        "VACIO": Barras("VACIO", [], [], [], [], [], []),
        "BARATO": _barras("BARATO", precio=0.40, vol_prom=500_000.0),
    }
    _preparar_main_escaneo(monkeypatch, tmp_path, barras)
    run_mod.main()
    _, lineas = _jsonl_de_escaneo(tmp_path)
    assert lineas[0]["embudo"]["operables"] == {}
    assert lineas[0]["embudo"]["rechazos_universo"] == {
        "sin_close": 1,
        "precio_bajo": 1,
    }


def test_main_dry_run_no_escribe_telemetria(monkeypatch, tmp_path):
    barras = {"OK": _barras("OK", precio=5.0, vol_prom=500_000.0)}
    _preparar_main_escaneo(
        monkeypatch, tmp_path, barras, argv=["momentum_hunter.run", "--dry-run"],
        diarios=lambda *a, **kw: [])
    run_mod.main()
    assert list(tmp_path.glob("*/*/events.jsonl")) == []


def test_main_camino_normal_sigue_a_intradia_y_persiste_una_vez(monkeypatch, tmp_path):
    barras = {"RKLB": _barras("RKLB", precio=5.0, vol_prom=500_000.0)}
    llamadas_intradia = []

    def _diarios(validos, barras, provider, cfg, con_cat, bandas=None, metricas=None):
        if metricas is not None:
            metricas.sumar(metricas.operables, "small")
            metricas.sumar(metricas.con_alguna_noticia, "small")
            metricas.sumar(metricas.con_catalizador, "small")
            metricas.titulares_total = 3
        return [_candidato_diario_intradia("RKLB")]

    _preparar_main_escaneo(monkeypatch, tmp_path, barras, diarios=_diarios)
    monkeypatch.setattr(
        run_mod, "construir_candidatos_intradia",
        lambda *a, **k: llamadas_intradia.append(1) or [])
    monkeypatch.setattr(run_mod, "seleccionar_y_auditar", lambda *a, **k: ([], {}, []))
    monkeypatch.setattr(
        run_mod.mercado, "evaluar",
        lambda provider: type("C", (), {"veredicto": "ok"})())
    monkeypatch.setattr(
        run_mod, "_actualizar_watchlist", lambda *a, **k: ([], {}, []))
    monkeypatch.setattr(run_mod.audit, "registrar_corrida", lambda snapshots: None)
    monkeypatch.setattr(run_mod.radar, "construir_resumen", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.tracker, "cargar", lambda: [])
    monkeypatch.setattr(run_mod.tracker, "guardar", lambda xs: None)
    monkeypatch.setattr(run_mod.vigilancia, "vigilar", lambda *a, **k: [])
    monkeypatch.setattr(run_mod, "enviar_telegram", lambda *a, **k: None)

    run_mod.main()

    assert llamadas_intradia == [1]   # no se cortó en el silencio de etapa 1
    _, lineas = _jsonl_de_escaneo(tmp_path)
    assert len(lineas) == 1
    assert lineas[0]["embudo"]["con_catalizador"] == {"small": 1}
    assert lineas[0]["embudo"]["rechazos_universo"] == {}
