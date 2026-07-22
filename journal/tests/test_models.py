from __future__ import annotations

from journal.models import EntradaJournal, PataJournal


def _entrada() -> EntradaJournal:
    return EntradaJournal(
        id="abc123", ticker="BNY", fecha_apertura="2026-07-22T12:00:00+00:00",
        score_screener=83.0, posicion_shortlist=1, tesis="Alcista",
        estrategia="Bull Call Spread",
        patas=[PataJournal("call", "comprar", 45.0, 2.10), PataJournal("call", "vender", 50.0, 0.80)],
        costo=130.0, riesgo_maximo=130.0, ganancia_maxima=370.0,
        probabilidad_exito=0.42, valor_esperado=15.5, motivo="Momentum alto",
    )


def test_to_dict_from_dict_roundtrip():
    original = _entrada()
    reconstruida = EntradaJournal.from_dict(original.to_dict())
    assert reconstruida == original


def test_to_dict_serializa_patas_como_dicts():
    d = _entrada().to_dict()
    assert isinstance(d["patas"], list)
    assert d["patas"][0] == {"tipo": "call", "accion": "comprar", "strike": 45.0, "prima": 2.10}


def test_estado_por_defecto_es_abierta():
    e = _entrada()
    assert e.estado == "abierta"
    assert e.fecha_cierre is None
    assert e.resultado is None
