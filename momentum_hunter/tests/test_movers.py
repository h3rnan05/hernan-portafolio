"""Descubrimiento "movers" en sombra (movers.py). Sin red: yfinance, el
proveedor de datos y las noticias se sustituyen por dobles."""

from __future__ import annotations

import inspect
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest

from momentum_hunter import movers, run as run_mod
from momentum_hunter.catalysts.detector import NewsProvider, Titular
from momentum_hunter.config import MomentumConfig
from momentum_hunter.data.provider import DataProvider
from momentum_hunter.models import BarraIntradia, Barras

CFG = MomentumConfig()
# Lunes 21 sep 2026, 10:00 ET = 14:00 UTC: 30 de 390 minutos de sesión.
AHORA = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


def _fila(symbol="ACME", precio=5.5, cambio=12.0, vol=900_000, mcap=4e8, **extra):
    fila = {"symbol": symbol, "regularMarketPrice": precio, "regularMarketChangePercent": cambio,
            "regularMarketVolume": vol, "marketCap": mcap, "shortName": f"{symbol} Corp",
            "regularMarketTime": int(AHORA.timestamp()) - 60}
    fila.update(extra)
    return fila


def _bi(ticker="ACME", n=30, precio=5.5, vol=30_000.0, sube=True):
    """n velas de 1 min de la sesión regular de HOY (desde 13:30 UTC)."""
    base = datetime(2026, 9, 21, 13, 30, tzinfo=UTC)
    ts, o, c, h, lo, v = [], [], [], [], [], []
    for i in range(n):
        t = base.replace(minute=30 + i) if 30 + i < 60 else base.replace(hour=14, minute=30 + i - 60)
        ts.append(t.isoformat())
        p = precio + (i * 0.01 if sube else -i * 0.01)
        o.append(p)
        c.append(p)
        h.append(p + 0.02)
        lo.append(p - 0.02)
        v.append(vol)
    return BarraIntradia(ticker, ts, o, c, h, lo, v)


def _diarias(ticker="ACME", dias=25, vol=1_000_000.0):
    return Barras(ticker, [f"d{i}" for i in range(dias)], [5.0] * dias, [5.0] * dias, [5.1] * dias, [4.9] * dias, [vol] * dias)


class _Provider(DataProvider):
    def __init__(self, intradia=None, diarias=None, falla=None):
        self._i, self._d, self._falla = intradia or {}, diarias or {}, falla

    def barras(self, tickers, dias=280):
        if self._falla == "diarias":
            raise RuntimeError("yahoo caído")
        return {t: self._d[t] for t in tickers if t in self._d}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        if self._falla == "intradia":
            raise RuntimeError("yahoo caído")
        return {t: self._i[t] for t in tickers if t in self._i}

    def metadata(self, tickers):
        return {}


class _Noticias(NewsProvider):
    def __init__(self, por_ticker=None):
        self._por = por_ticker or {}

    def titulares(self, ticker):
        return self._por.get(ticker, [])


# ───────────── screener ─────────────

def test_query_lleva_los_cinco_filtros_del_pedido():
    d = movers.construir_query(CFG).to_dict()
    assert d["operator"] == "AND"
    ops = {(o["operator"], o["operands"][0]): o["operands"][1] for o in d["operands"]}
    assert ops[("EQ", "region")] == "us"
    assert ops[("GTE", "intradayprice")] == 0.75 and ops[("LTE", "intradayprice")] == 20.0
    assert ops[("LT", "intradaymarketcap")] == 2e9
    assert ops[("GT", "dayvolume")] == 300_000
    assert ops[("GTE", "percentchange")] == 5.0


def test_parsear_exige_cada_campo_y_no_inventa_ceros():
    rechazos = Counter()
    filas = [_fila("OK1"), _fila("SINVOL", vol=None), _fila("TXT", precio="5.5"), _fila("BOOL", mcap=True),
             _fila(""), "basura"]
    out = movers.parsear_cotizaciones({"quotes": filas}, rechazos)
    assert [m.ticker for m in out] == ["OK1"]
    assert rechazos == Counter({"campo_faltante:regularMarketVolume": 1, "campo_faltante:regularMarketPrice": 1,
                                "campo_faltante:marketCap": 1, "campo_faltante:symbol": 1, "fila_ilegible": 1})
    assert out[0].hora_dato == "2026-09-21T13:59:00+00:00" and out[0].nombre == "OK1 Corp"
    assert movers.parsear_cotizaciones({"quotes": None}) == [] and movers.parsear_cotizaciones(None) == []


def test_consultar_screener_pide_top_por_cambio_y_ordena():
    llamadas = []

    def screen(query, **kw):
        llamadas.append(kw)
        return {"quotes": [_fila("B", cambio=7.0), _fila("A", cambio=30.0), _fila("C", cambio=9.0)]}
    cfg = MomentumConfig(movers_top=2)
    out = movers.consultar_screener(cfg, screen)
    assert [m.ticker for m in out] == ["A", "C"]
    assert llamadas == [{"size": 2, "sortField": "percentchange", "sortAsc": False}]


# ───────────── dinero entrando ─────────────

def test_fraccion_de_sesion_en_hora_de_nueva_york():
    assert movers.fraccion_sesion(AHORA) == pytest.approx(30 / 390)
    assert movers.fraccion_sesion(datetime(2026, 9, 21, 13, 0, tzinfo=UTC)) is None      # premarket
    assert movers.fraccion_sesion(datetime(2026, 9, 21, 20, 30, tzinfo=UTC)) is None     # después del cierre
    assert movers.fraccion_sesion(datetime(2026, 9, 19, 15, 0, tzinfo=UTC)) is None      # sábado
    assert movers.fraccion_sesion(datetime(2026, 9, 21, 20, 0, tzinfo=UTC)) == pytest.approx(1.0)


def test_rvol_ajustado_a_la_hora():
    # 250k acciones a las 10:00 con promedio de 1M/día: 250k / (1M × 30/390) = 3,25
    assert movers.rvol_ajustado(250_000, 1_000_000, 30 / 390) == pytest.approx(3.25)
    assert movers.rvol_ajustado(None, 1_000_000, 0.5) is None
    assert movers.rvol_ajustado(250_000, 0, 0.5) is None
    assert movers.rvol_ajustado(250_000, 1_000_000, None) is None


def test_promedio_20d_exige_20_dias_completos():
    assert movers.promedio_volumen_20d(_diarias(dias=19)) is None
    assert movers.promedio_volumen_20d(_diarias(dias=25, vol=2.0)) == 2.0
    assert movers.promedio_volumen_20d(None) is None


def test_evaluar_pasa_con_rvol_dolares_y_vwap():
    # 30 velas × 30k = 900k hoy; promedio 1M; fracción 30/390 → RVOL 11,7. $ ≈ 5M. Sube: sobre VWAP.
    c = movers.evaluar(movers.Mover("ACME", 5.5, 12.0, 900_000, 4e8), _bi(), _diarias(), AHORA, CFG)
    assert c.pasa_filtro and c.motivo_rechazo is None
    assert c.rvol_ajustado == pytest.approx(900_000 / (1_000_000 * 30 / 390))
    assert c.volumen_dolares > 2e6 and c.sobre_vwap is True and c.velas_hoy == 30


@pytest.mark.parametrize("bi,diarias,ahora,motivo", [
    (None, _diarias(), AHORA, "sin_intradia"),
    (_bi(), _diarias(dias=10), AHORA, "sin_promedio_20d"),
    (_bi(), _diarias(), datetime(2026, 9, 21, 12, 0, tzinfo=UTC), "fuera_de_sesion"),
    (_bi(vol=2_000.0), _diarias(), AHORA, "rvol_bajo"),
    (_bi(precio=0.8, vol=60_000.0), _diarias(vol=500_000.0), AHORA, "dolares_bajos"),
    (_bi(sube=False), _diarias(), AHORA, "bajo_vwap"),
])
def test_evaluar_rechaza_con_motivo_explicito(bi, diarias, ahora, motivo):
    c = movers.evaluar(movers.Mover("ACME", 5.5, 12.0, 900_000, 4e8), bi, diarias, ahora, CFG)
    assert not c.pasa_filtro and c.motivo_rechazo == motivo


# ───────────── catalizador como etiqueta ─────────────

def _pasada(rvol):
    c = movers.Candidata("ACME", 5.5, 12.0, 900_000, 4e8, nombre="Acme Corp")
    c.pasa_filtro, c.rvol_ajustado = True, rvol
    return c


def test_clase_a_con_catalizador_anclado_al_ticker():
    noticias = _Noticias({"ACME": [Titular("Acme Corp receives FDA approval for its device", "prwire")]})
    c = movers.etiquetar(_pasada(3.5), noticias, CFG, AHORA.date())
    assert c.clase == "A" and c.catalizador_tipo == "fda" and c.motivo_rechazo is None and c.titulares == 1


def test_catalizador_de_otro_ticker_no_es_clase_a():
    noticias = _Noticias({"ACME": [Titular("Globex wins FDA approval", "prwire")]})
    c = movers.etiquetar(_pasada(3.5), noticias, CFG, AHORA.date())
    assert c.clase is None and c.ancla_motivo and "sin_catalizador" in c.motivo_rechazo
    assert movers.etiquetar(_pasada(6.0), noticias, CFG, AHORA.date()).clase == "B"


def test_clase_b_solo_con_rvol_extremo():
    assert movers.etiquetar(_pasada(5.0), _Noticias(), CFG, AHORA.date()).clase == "B"
    c = movers.etiquetar(_pasada(4.9), _Noticias(), CFG, AHORA.date())
    assert c.clase is None and c.motivo_rechazo == "sin_catalizador_y_rvol_insuficiente_para_clase_b"


# ───────────── corrida en sombra ─────────────

def _leer(tmp_path):
    ruta = tmp_path / "2026-09-21" / "local" / movers.ARCHIVO_TELEMETRIA
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]


def test_corrida_completa_deja_telemetria_y_nada_mas(tmp_path, monkeypatch):
    monkeypatch.delenv("MOMENTUM_TELEM_FUENTE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    screen = lambda q, **kw: {"quotes": [_fila("ACME", cambio=20.0), _fila("SLOW", cambio=8.0), _fila("ROTO", vol=None)]}
    prov = _Provider(intradia={"ACME": _bi("ACME"), "SLOW": _bi("SLOW", vol=2_000.0)},
                     diarias={"ACME": _diarias("ACME"), "SLOW": _diarias("SLOW")})
    noticias = _Noticias({"ACME": [Titular("ACME Corp receives FDA approval", "prwire")]})
    corrida = movers.correr_sombra(CFG, prov, noticias, screen, AHORA, tmp_path)
    assert corrida.screener_ok and corrida.error is None
    assert corrida.embudo == {"screener": 3, "validas": 2, "con_intradia": 2, "pasan_filtro": 1,
                              "clase_a": 1, "clase_b": 0, "descartadas": 1}
    assert corrida.rechazos == Counter({"campo_faltante:regularMarketVolume": 1, "rvol_bajo": 1})
    registros = _leer(tmp_path)
    assert len(registros) == 1
    r = registros[0]
    assert r["fuente"] == "local" and r["inicio_ts"] == "2026-09-21T14:00:00+00:00" and r["timestamp"]
    por_ticker = {c["ticker"]: c for c in r["candidatas"]}
    assert por_ticker["ACME"]["clase"] == "A" and por_ticker["ACME"]["hora_dato"] == "2026-09-21T13:59:00+00:00"
    assert por_ticker["SLOW"]["motivo_rechazo"] == "rvol_bajo" and por_ticker["SLOW"]["rvol_ajustado"] < 3
    # Nada más en disco: ni watchlist, ni alertas.
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == [movers.ARCHIVO_TELEMETRIA]


def test_screener_caido_o_vacio_termina_sin_candidatas(tmp_path, monkeypatch):
    monkeypatch.delenv("MOMENTUM_TELEM_FUENTE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    def explota(q, **kw):
        raise RuntimeError("429")
    corrida = movers.correr_sombra(CFG, _Provider(), _Noticias(), explota, AHORA, tmp_path)
    assert not corrida.screener_ok and corrida.error == "screener:RuntimeError" and corrida.candidatas == []
    corrida = movers.correr_sombra(CFG, _Provider(), _Noticias(), lambda q, **kw: {"quotes": []}, AHORA, tmp_path)
    assert corrida.screener_ok and corrida.error == "screener_vacio" and corrida.candidatas == []
    assert len(_leer(tmp_path)) == 2   # las dos corridas quedaron registradas


def test_fallo_de_intradia_no_tumba_la_corrida(tmp_path, monkeypatch):
    monkeypatch.delenv("MOMENTUM_TELEM_FUENTE", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    corrida = movers.correr_sombra(CFG, _Provider(falla="intradia"), _Noticias(),
                                   lambda q, **kw: {"quotes": [_fila("ACME")]}, AHORA, tmp_path)
    assert corrida.rechazos["intradia:RuntimeError"] == 1
    assert corrida.candidatas[0].motivo_rechazo == "sin_intradia"


# ───────────── bandera y reglas ─────────────

def test_cli_apagado_por_omision_no_corre_nada(monkeypatch):
    monkeypatch.delenv("MOMENTUM_MOVERS_SOMBRA", raising=False)
    monkeypatch.setattr("sys.argv", ["run", "--movers-sombra"])
    def explota(*a, **k):
        raise AssertionError("no debía correr la sombra")
    monkeypatch.setattr(movers, "correr_sombra", explota)
    run_mod.main()   # sale sin hacer nada
    assert CFG.movers_sombra is False


def test_cli_con_variable_corre_la_sombra(monkeypatch):
    monkeypatch.setenv("MOMENTUM_MOVERS_SOMBRA", "1")
    monkeypatch.setattr("sys.argv", ["run", "--movers-sombra"])
    llamadas = []
    monkeypatch.setattr(movers, "correr_sombra", lambda cfg: llamadas.append(cfg))
    run_mod.main()
    assert len(llamadas) == 1


def test_movers_no_menciona_ejecucion_ni_brokers():
    fuente = inspect.getsource(movers).lower()
    for palabra in ("place_order", "buy_order", "sell_order", "alpaca", "ibapi", "interactive_brokers"):
        assert palabra not in fuente


def test_unidades_de_sombra_no_se_instalan_solas_y_no_bloquean_al_escaneo():
    raiz = Path(__file__).resolve().parents[2] / "infra" / "systemd"
    timer = (raiz / "momentum-movers-sombra.timer").read_text(encoding="utf-8")
    assert "OnCalendar=Mon..Fri *-*-* 13..20:2/5:00 UTC" in timer and "NO se instala solo" in timer
    wrapper = (raiz / "bin" / "run_movers_sombra.sh").read_text(encoding="utf-8")
    assert 'if [ "${MOMENTUM_MOVERS_SOMBRA:-0}" != "1" ]' in wrapper
    assert "flock -n 9" in wrapper and "momentum-paper-git.lock" not in wrapper
    assert "--movers-sombra" in wrapper and "git " not in wrapper
    assert "NO se instala solo" in (raiz / "README.md").read_text(encoding="utf-8")
