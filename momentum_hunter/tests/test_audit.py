"""Pruebas de la auditoría -- el snapshot debe alcanzar para responder,
meses después, las preguntas del Principio 9 (qué datos había, qué
patrón, qué noticia, qué precio, qué volumen, por qué la decisión)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from momentum_hunter.alerts import CandidatoIntradia
from momentum_hunter.audit import (
    DECISION_ALERTADA,
    DECISION_VETADA,
    registrar_corrida,
    snapshot_candidato,
)
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.early_opportunity import EarlyOpportunity
from momentum_hunter.evaluator import ResultadoEvaluacion
from momentum_hunter.models import BarraIntradia, FactoresIntradia, Metadata


def _candidato(ticker="ACME") -> CandidatoIntradia:
    factores = FactoresIntradia(precio_actual=5.20, vwap=5.10, ema9=5.10, rvol_actual=4.0,
                                gap_pct=0.10, maximo_premarket=5.00, velas_desde_ruptura=1)
    early = EarlyOpportunity(score=90.0, veredicto="temprano", razon="ok", motivo_veredicto="x")
    resultado = ResultadoEvaluacion(
        paso_detenido=None, dinero_entrando=True, desequilibrio=True, patron="gap_and_go",
        temprano=True, early=early, penalizaciones=[], score_base=95.0,
        score_ajustado=95.0, accionable=True,
    )
    bi = BarraIntradia(ticker, ["2026-07-27T13:33:00+00:00"], [5.2], [5.2], [5.25], [5.15], [5000.0])
    return CandidatoIntradia(
        ticker=ticker, nombre="Acme Corp",
        catalizador=Catalizador(tipo="fda", titular="FDA Approval", fuente="Reuters",
                                fecha="2026-07-27T13:20:00+00:00"),
        minutos_desde_catalizador=13.0, factores=factores, bi_hoy=bi,
        meta=Metadata(ticker=ticker, shares_float=12_000_000, short_pct_float=0.20,
                      market_cap=80_000_000, bolsa="NASDAQ"),
        atr_diario=0.30, resultado=resultado,
    )


def test_snapshot_responde_las_preguntas_del_principio_9():
    s = snapshot_candidato(_candidato(), DECISION_ALERTADA, ["motivo"], [])
    assert s["ticker"] == "ACME"
    assert s["precio_actual"] == 5.20                                 # qué precio tenía
    assert s["factores_intradia"]["rvol_actual"] == 4.0                # qué volumen tenía
    assert s["catalizador"]["titular"] == "FDA Approval"               # qué noticias había
    assert s["catalizador"]["fecha"] == "2026-07-27T13:20:00+00:00"    # qué datos existían en ese momento
    assert s["evaluacion"]["patron"] == "gap_and_go"                   # qué patrón detectó
    assert s["evaluacion"]["early"]["veredicto"] == "temprano"         # qué esperaba el sistema
    assert s["decision"] == DECISION_ALERTADA                           # por qué apareció
    assert s["meta"]["float_acciones"] == 12_000_000


def test_snapshot_de_rechazo_guarda_motivos_y_que_cambiaria():
    s = snapshot_candidato(
        _candidato(), DECISION_VETADA,
        ["El dinero está dejando de entrar."],
        ["Que el volumen vuelva a acelerarse."],
    )
    assert s["motivos"] == ["El dinero está dejando de entrar."]
    assert s["que_tendria_que_cambiar"] == ["Que el volumen vuelva a acelerarse."]


def test_registrar_corrida_escribe_json_del_dia(tmp_path):
    ahora = datetime(2026, 7, 27, 14, 0, tzinfo=UTC)
    s = snapshot_candidato(_candidato(), DECISION_ALERTADA, [], [])
    path = registrar_corrida([s], dir_auditoria=tmp_path, ahora=ahora)
    assert path == tmp_path / "2026-07-27.json"
    data = json.loads(path.read_text())
    assert len(data["corridas"]) == 1
    assert data["corridas"][0]["candidatos"][0]["ticker"] == "ACME"


def test_registrar_corrida_appendea_no_sobreescribe(tmp_path):
    ahora = datetime(2026, 7, 27, 14, 0, tzinfo=UTC)
    s = snapshot_candidato(_candidato(), DECISION_ALERTADA, [], [])
    registrar_corrida([s], dir_auditoria=tmp_path, ahora=ahora)
    registrar_corrida([s], dir_auditoria=tmp_path, ahora=ahora)
    data = json.loads((tmp_path / "2026-07-27.json").read_text())
    assert len(data["corridas"]) == 2


def test_registrar_corrida_vacia_no_escribe_nada(tmp_path):
    assert registrar_corrida([], dir_auditoria=tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_registrar_corrida_archivo_corrupto_no_tumba_ni_borra(tmp_path):
    ahora = datetime(2026, 7, 27, 14, 0, tzinfo=UTC)
    corrupto = tmp_path / "2026-07-27.json"
    corrupto.write_text("{esto no es json")
    s = snapshot_candidato(_candidato(), DECISION_ALERTADA, [], [])
    path = registrar_corrida([s], dir_auditoria=tmp_path, ahora=ahora)
    assert json.loads(path.read_text())["corridas"]
    # El archivo corrupto quedó renombrado, no destruido.
    assert (tmp_path / "2026-07-27.corrupto.json").exists()
