"""`velas_desde_ruptura` en la watchlist: solo se guarda al disparar, viaja
por el overlay VPS, y un campo desconocido no descarta la entrada."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion

AHORA = datetime(2026, 9, 18, 15, 0, tzinfo=UTC)


def _entrada(ticker="RKLB") -> watchlist.EntradaWatchlist:
    cat = Catalizador(tipo="contrato", titular="x", fuente="Reuters", fecha="2026-09-18T13:45:00+00:00")
    c = CandidatoDiario(
        ticker=ticker, nombre="Rocket Lab", precio=80.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=1.5), catalizador=cat, meta=Metadata(ticker=ticker),
        puntuacion=Puntuacion(ticker=ticker, score_total=88.0, sub={}),
    )
    return watchlist.desde_candidato_diario(c, AHORA)


def test_por_defecto_es_none_no_cero():
    e = _entrada()
    watchlist.marcar_triggered(e, "2026-09-18T14:59:00+00:00", "d", "ev", AHORA)
    assert e.velas_desde_ruptura is None


def test_marcar_triggered_guarda_el_valor_tal_cual():
    e = _entrada()
    watchlist.marcar_triggered(e, "2026-09-18T14:59:00+00:00", "d", "ev", AHORA, velas_desde_ruptura=5)
    assert e.velas_desde_ruptura == 5
    assert e.estado == watchlist.ESTADO_TRIGGERED


def test_se_conserva_al_guardar_y_cargar(tmp_path):
    e = _entrada()
    watchlist.marcar_triggered(e, "2026-09-18T14:59:00+00:00", "d", "ev", AHORA, velas_desde_ruptura=0)
    ruta = tmp_path / "watchlist.json"
    watchlist.guardar([e], ruta)
    (cargada,) = watchlist.cargar(ruta)
    assert cargada.velas_desde_ruptura == 0


def test_campo_desconocido_se_ignora_sin_descartar_la_entrada(tmp_path):
    e = _entrada()
    d = asdict(e)
    d["campo_de_una_version_futura"] = 123
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [d]}))
    entradas = watchlist.cargar(ruta)
    assert [x.ticker for x in entradas] == ["RKLB"]


def test_watchlist_vieja_sin_el_campo_sigue_cargando(tmp_path):
    d = asdict(_entrada())
    d.pop("velas_desde_ruptura")
    ruta = tmp_path / "watchlist.json"
    ruta.write_text(json.dumps({"entradas": [d]}))
    (cargada,) = watchlist.cargar(ruta)
    assert cargada.velas_desde_ruptura is None


def test_el_overlay_vps_lleva_el_campo():
    assert "velas_desde_ruptura" in watchlist.CAMPOS_OVERLAY
