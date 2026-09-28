"""Halt y LULD a partir de mensajes de datos y de snapshots. Sin red
y sin cliente de trading."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter.data import halts as h

AHORA = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
FRESCO = "2026-09-28T14:59:00Z"
VIEJO = "2026-09-28T12:00:00Z"


def _quote(condiciones, cuando, tape="C"):
    return {"t": cuando, "c": condiciones, "z": tape, "bp": 10.0, "ap": 10.05, "bs": 1, "as": 1, "bx": "Q", "ax": "P"}


def _trade(condiciones, cuando):
    return {"t": cuando, "c": condiciones, "p": 10.0, "s": 100, "z": "C", "x": "Q"}


def _snap(quote, trade=None):
    cuerpo = {"latestQuote": quote}
    if trade is not None:
        cuerpo["latestTrade"] = trade
    return cuerpo


def test_quote_z_es_halt_y_no_se_lee_como_operando():
    lectura = h.interpretar_snapshot(
        "RKLB", _snap(_quote(["Z"], FRESCO), _trade(["@"], FRESCO)), AHORA,
    )
    assert lectura.en_halt is True
    assert lectura.situacion == "halt"
    assert lectura.halt_id
    assert lectura.debe_bloquear is True


def test_quote_l_en_tape_a_es_halt():
    lectura = h.interpretar_snapshot(
        "IBM", _snap(_quote(["L"], FRESCO, tape="A"), _trade([" "], FRESCO)), AHORA,
    )
    assert lectura.en_halt is True


def test_reanudacion_no_sale_de_una_quote_regular_vieja():
    """El último print regular, si ya no es fresco, no es 'no hay halt'."""
    lectura = h.interpretar_snapshot(
        "RKLB", _snap(_quote(["R"], VIEJO), _trade(["@"], VIEJO)), AHORA,
    )
    assert lectura.en_halt is None
    assert lectura.situacion == "viejo"
    assert lectura.debe_bloquear is True


def test_print_regular_fresco_si_es_operando():
    lectura = h.interpretar_snapshot(
        "RKLB", _snap(_quote(["R"], FRESCO), _trade(["@", "I"], FRESCO)), AHORA,
    )
    assert lectura.en_halt is False
    assert lectura.fresco is True
    assert lectura.situacion == "operando"
    assert lectura.debe_bloquear is False


def test_espacio_en_tape_a_es_quote_regular():
    lectura = h.interpretar_snapshot(
        "IBM",
        _snap(_quote([" "], FRESCO, tape="A"), _trade([" ", "F"], FRESCO)),
        AHORA,
    )
    assert lectura.en_halt is False
    assert lectura.situacion == "operando"


def test_dato_ausente_no_es_no_halt():
    for cuerpo in (
        None,
        {},
        {"latestQuote": {"t": FRESCO, "z": "C"}},
        {"latestQuote": {"t": FRESCO, "z": "C", "c": []}, "latestTrade": _trade(["@"], FRESCO)},
        {"latestTrade": _trade(["@"], FRESCO)},
    ):
        lectura = h.interpretar_snapshot("RKLB", cuerpo, AHORA)
        assert lectura.en_halt is None, cuerpo
        assert lectura.en_halt is not False
        assert lectura.debe_bloquear is True


def test_slow_quote_luld_restringe_sin_inventar_banda():
    lectura = h.interpretar_snapshot(
        "IBM", _snap(_quote(["U"], FRESCO, tape="A"), _trade([" "], FRESCO)), AHORA,
    )
    assert lectura.situacion == "luld"
    assert lectura.restringe_luld is True
    assert lectura.limit_up is None
    assert lectura.limit_down is None
    assert lectura.en_halt is None


def test_status_halt_y_reanudacion_en_orden():
    eventos = [
        {"T": "s", "S": "RKLB", "sc": "H", "rc": "T12", "sm": "Trading Halt", "t": "2026-09-28T14:00:00Z", "z": "C"},
        {"T": "s", "S": "RKLB", "sc": "T", "t": "2026-09-28T14:30:00Z", "z": "C"},
    ]
    lectura = h.reducir_eventos("RKLB", eventos, AHORA, stream_fresco=True)
    assert lectura is not None
    assert lectura.en_halt is False
    assert lectura.situacion == "reanudado"
    assert lectura.halt_id is None
    assert lectura.debe_bloquear is False


def test_una_reanudacion_vieja_no_afirma_que_sigue_operando():
    eventos = [
        {"T": "s", "S": "RKLB", "sc": "H", "t": "2026-09-28T12:00:00Z", "z": "C"},
        {"T": "s", "S": "RKLB", "sc": "T", "t": "2026-09-28T12:10:00Z", "z": "C"},
    ]
    lectura = h.reducir_eventos("RKLB", eventos, AHORA, stream_fresco=False)
    assert lectura is not None
    assert lectura.en_halt is None
    assert lectura.situacion == "viejo"
    assert lectura.debe_bloquear is True


def test_solo_cotizacion_no_es_reanudacion():
    eventos = [
        {"T": "s", "S": "RKLB", "sc": "H", "t": "2026-09-28T14:00:00Z", "z": "C"},
        {"T": "s", "S": "RKLB", "sc": "Q", "t": "2026-09-28T14:20:00Z", "z": "C"},
    ]
    lectura = h.reducir_eventos("RKLB", eventos, AHORA, stream_fresco=True)
    assert lectura is not None
    assert lectura.en_halt is True


def test_luld_dentro_de_banda_no_restringe_si_ya_reanudo():
    eventos = [
        {"T": "s", "S": "RKLB", "sc": "T", "t": "2026-09-28T14:40:00Z", "z": "C"},
        {"T": "l", "S": "RKLB", "u": 12.0, "d": 9.0, "i": "B", "t": FRESCO, "z": "C"},
    ]
    lectura = h.reducir_eventos("RKLB", eventos, AHORA, stream_fresco=True, precio_entrada=10.0)
    assert lectura is not None
    assert lectura.limit_up == 12.0
    assert lectura.limit_down == 9.0
    assert lectura.restringe_luld is False
    assert lectura.en_halt is False
    assert lectura.debe_bloquear is False


def test_luld_fuera_de_banda_restringe_aunque_haya_reanudado():
    eventos = [
        {"T": "s", "S": "RKLB", "sc": "3", "t": "2026-09-28T14:40:00Z", "z": "A"},
        {"T": "l", "S": "RKLB", "u": 3.24, "d": 2.65, "i": "B", "t": FRESCO, "z": "A"},
    ]
    lectura = h.reducir_eventos("RKLB", eventos, AHORA, stream_fresco=True, precio_entrada=10.0)
    assert lectura is not None
    assert lectura.restringe_luld is True
    assert lectura.en_halt is False
    assert lectura.debe_bloquear is True


def test_luld_indicador_d_es_halt_y_una_reanudacion_posterior_lo_cierra():
    eventos = [
        {"T": "l", "S": "RKLB", "u": 11.0, "d": 9.0, "i": "D", "t": "2026-09-28T14:10:00Z", "z": "C"},
        {"T": "s", "S": "RKLB", "sc": "T", "t": "2026-09-28T14:40:00Z", "z": "C"},
    ]
    cerrado = h.reducir_eventos("RKLB", eventos, AHORA, stream_fresco=True, precio_entrada=10.0)
    assert cerrado is not None
    assert cerrado.en_halt is False
    assert cerrado.situacion == "reanudado"

    solo_d = h.reducir_eventos("RKLB", eventos[:1], AHORA, stream_fresco=True, precio_entrada=10.0)
    assert solo_d is not None
    assert solo_d.en_halt is True
    assert solo_d.halt_id


def test_luld_sin_lado_no_inventa_la_banda():
    eventos = [{"T": "l", "S": "RKLB", "i": "B", "t": FRESCO, "z": "C"}]
    lectura = h.reducir_eventos("RKLB", eventos, AHORA, stream_fresco=True, precio_entrada=10.0)
    assert lectura is not None
    assert lectura.limit_up is None
    assert lectura.limit_down is None
    assert lectura.restringe_luld is not True


def test_linea_ilegible_no_es_reanudacion(tmp_path):
    raiz = tmp_path / "sip_stream"
    dia = raiz / "eventos"
    dia.mkdir(parents=True)
    (dia / "2026-09-28.jsonl").write_text(
        "esto no es json\n"
        + json.dumps({"T": "s", "S": "RKLB", "sc": "H", "t": "2026-09-28T14:10:00Z", "z": "C"})
        + "\n",
        encoding="utf-8",
    )
    (raiz / "estado.json").write_text(json.dumps({
        "version": 1, "conectado": True, "hueco_abierto": False,
        "ultimo_heartbeat_en": AHORA.isoformat(),
    }), encoding="utf-8")
    lecturas = h.leer_stream(["RKLB"], AHORA, directorio=raiz)
    assert lecturas["RKLB"].en_halt is True


def test_sin_directorio_de_stream_no_se_inventa_un_claro(tmp_path):
    assert h.leer_stream(["RKLB"], AHORA, directorio=tmp_path / "no-esta") == {}


def test_snapshot_ausente_con_stream_en_halt_sigue_en_halt(tmp_path):
    raiz = tmp_path / "sip_stream"
    (raiz / "eventos").mkdir(parents=True)
    (raiz / "eventos" / "2026-09-28.jsonl").write_text(
        json.dumps({"tipo": "status", "T": "s", "S": "RKLB", "sc": "2", "t": "2026-09-28T14:00:00Z", "z": "A"}) + "\n",
        encoding="utf-8",
    )
    (raiz / "estado.json").write_text(json.dumps({
        "version": 1, "conectado": True, "hueco_abierto": False,
        "ultimo_heartbeat_en": AHORA.isoformat(),
    }), encoding="utf-8")

    def traer(_tickers):
        return None

    lecturas = h.consultar(["RKLB"], AHORA, directorio=raiz, traer=traer)
    assert lecturas["RKLB"].en_halt is True
    assert lecturas["RKLB"].fuente == "stream"


def test_symbolo_que_el_snapshot_no_trae_es_desconocido():
    lecturas = h.consultar(["RKLB"], AHORA, directorio=Path("/no/existe"), traer=lambda _t: {})
    assert lecturas["RKLB"].en_halt is None
    assert lecturas["RKLB"].situacion == "desconocido"
    assert lecturas["RKLB"].debe_bloquear is True


def test_print_posterior_cubre_un_halt_cuyo_socket_ya_no_late():
    stream = h.reducir_eventos(
        "RKLB",
        [{"T": "s", "S": "RKLB", "sc": "H", "t": "2026-09-28T13:00:00Z", "z": "C"}],
        AHORA, stream_fresco=False,
    )
    snap = h.interpretar_snapshot(
        "RKLB", _snap(_quote(["R"], FRESCO), _trade(["@"], FRESCO)), AHORA,
    )
    fusion = h.fusionar(stream, snap, "RKLB", AHORA)
    assert fusion.en_halt is False
    assert fusion.situacion == "operando"


def test_el_hunter_no_importa_clientes_de_trading():
    """Ni el módulo de halts ni el resto del hunter hablan con el
    cliente de órdenes. El host de datos no es el host de trading."""
    import momentum_hunter
    raiz = Path(momentum_hunter.__file__).resolve().parent
    prohibidos = (
        "momentum_paper_trader.alpaca_client",
        "AlpacaPaperClient",
        "paper-api.alpaca.markets",
        "place_order",
        "TradingClient",
    )
    vistos = 0
    for path in raiz.rglob("*.py"):
        if "tests" in path.parts:
            continue
        texto = path.read_text(encoding="utf-8")
        vistos += 1
        for palabra in prohibidos:
            assert palabra not in texto, f"{path.name} menciona {palabra}"
    assert vistos > 10
    halts_src = (raiz / "data" / "halts.py").read_text(encoding="utf-8")
    assert "momentum_paper_trader" not in halts_src
    assert "data.alpaca.markets" in (raiz / "data" / "alpaca_datos.py").read_text(encoding="utf-8")
    assert "wss://stream.data.alpaca.markets/v2/sip" in halts_src
