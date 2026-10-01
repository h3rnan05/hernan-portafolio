"""Régimen + racha (PR-E): niveles, histéresis y fail-closed. Solo sombra."""
from datetime import UTC, datetime, timedelta

from momentum_hunter.models import Barras, BarraIntradia
from momentum_paper_trader import regimen as rg

AHORA = datetime(2026, 9, 30, 17, 0, tzinfo=UTC)


def _diarias(ticker, closes, fin="2026-09-29"):
    f0 = datetime.fromisoformat(fin)
    fechas = [(f0 - timedelta(days=len(closes) - 1 - i)).date().isoformat() for i in range(len(closes))]
    return Barras(ticker, fechas, closes, closes, closes, closes, [1.0] * len(closes))


def _intradia(ticker, closes, dia="2026-09-30"):
    ts = [f"{dia}T{14 + (i // 60):02d}:{i % 60:02d}:00+00:00" for i in range(len(closes))]
    return BarraIntradia(ticker, ts, closes, closes, closes, closes, [100.0] * len(closes))


class Prov:
    def __init__(self, d=None, i=None, rompe=False):
        self.d, self.i, self.rompe = d or {}, i or {}, rompe

    def barras(self, tickers, dias=280):
        if self.rompe:
            raise RuntimeError("red")
        return self.d

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        if self.rompe:
            raise RuntimeError("red")
        return self.i


def _t(pnl, salida, r=None):
    return {"pnl": pnl, "salida_ts": salida, "r": r if r is not None else (1.0 if pnl > 0 else -1.0)}


def test_racha_trades_y_sesiones():
    ts = [_t(5, "2026-09-26T19:00:00"), _t(-1, "2026-09-28T19:00:00"), _t(-1, "2026-09-29T15:00:00"),
          _t(-2, "2026-09-29T19:00:00")]
    r = rg.racha(ts, hoy="2026-09-30")
    assert r["perdedores_seguidos"] == 3 and r["sesiones_rojas_seguidas"] == 2 and r["disparada"]
    assert r["r_ultimos_10"] is None


def test_racha_vacia_no_dispara():
    r = rg.racha([], hoy="2026-09-30")
    assert r["disparada"] is False and r["perdedores_seguidos"] == 0


def test_racha_r10_con_none_no_inventa():
    ts = [_t(-1, f"2026-09-{10 + i:02d}T19:00:00") for i in range(10)]
    ts[-1]["r"] = None
    assert rg.racha(ts)["r_ultimos_10"] is None


def test_normal_cuando_todo_sano():
    spy = _diarias("SPY", [100.0] * 20 + [110.0])
    prov = Prov({"SPY": spy, "VIXY": _diarias("VIXY", [10.0] * 21)},
                {"SPY": _intradia("SPY", [100 + i * 0.1 for i in range(30)]),
                 "VIXY": _intradia("VIXY", [10.0] * 30)})
    out = rg.evaluar(prov, [], AHORA)
    assert out["nivel"] == rg.NORMAL and out["modo"] == "sombra"
    assert out["acciones_sombra"]["max_posiciones"] is None


def test_dato_faltante_nunca_es_normal():
    out = rg.evaluar(Prov(rompe=True), [], AHORA)
    assert out["nivel"] == rg.CAUTELA
    assert all(s["activa"] is None for s in out["senales"])
    assert any("fail-closed" in m for m in out["motivos"])


def test_defensivo_con_racha_y_spy_bajo_sma20():
    spy = _diarias("SPY", [110.0] * 20 + [100.0])
    prov = Prov({"SPY": spy, "VIXY": _diarias("VIXY", [10.0] * 21)},
                {"SPY": _intradia("SPY", [100 + i * 0.1 for i in range(30)]),
                 "VIXY": _intradia("VIXY", [10.0] * 30)})
    ts = [_t(-1, "2026-09-28T19:00:00"), _t(-1, "2026-09-29T15:00:00"), _t(-2, "2026-09-29T19:00:00")]
    out = rg.evaluar(prov, ts, AHORA)
    assert out["nivel"] == rg.DEFENSIVO
    assert out["acciones_sombra"]["max_posiciones"] == 2
    assert out["acciones_sombra"]["sin_entradas"] is True


def test_vixy_sube():
    s = rg.vixy_sube(_diarias("VIXY", [10.0] * 3), _intradia("VIXY", [10.0, 10.6]), "2026-09-30")
    assert s["activa"] is True and s["valor"] == 0.06


def test_histeresis_baja_con_dos_lecturas_y_de_a_uno():
    prev = {"nivel": rg.DEFENSIVO, "lecturas_para_bajar": 0}
    n, k = rg.con_histeresis(rg.NORMAL, prev)
    assert (n, k) == (rg.DEFENSIVO, 1)
    n, k = rg.con_histeresis(rg.NORMAL, {"nivel": n, "lecturas_para_bajar": k})
    assert (n, k) == (rg.CAUTELA, 0)
    assert rg.con_histeresis(rg.DEFENSIVO, {"nivel": rg.NORMAL}) == (rg.DEFENSIVO, 0)
    assert rg.con_histeresis(rg.NORMAL, None) == (rg.NORMAL, 0)


def test_vigente_cachea_y_nunca_lanza(tmp_path):
    p = tmp_path / "r.json"
    llamadas = []

    def calc(prev):
        llamadas.append(prev)
        return {"nivel": rg.CAUTELA, "calculado_en": AHORA.isoformat()}
    assert rg.vigente(AHORA, calc, p)["nivel"] == rg.CAUTELA
    assert rg.vigente(AHORA + timedelta(seconds=60), calc, p)["nivel"] == rg.CAUTELA
    assert len(llamadas) == 1

    def rompe(prev):
        raise RuntimeError("x")
    assert rg.vigente(AHORA + timedelta(seconds=600), rompe, p)["nivel"] == rg.CAUTELA


def test_archivo_corrupto_es_none(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{no")
    assert rg.cargar(p) is None
