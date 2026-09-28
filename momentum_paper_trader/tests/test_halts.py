"""El ejecutor ante un halt: observar registra, enforce no manda la
entrada, el aviso no se repite y nadie vende."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from momentum_hunter.data.halts import LecturaHalt
from momentum_paper_trader import bloqueos, estado, halts, telemetria
from momentum_paper_trader.tests.test_executor import (
    AHORA,
    CFG,
    _FakeAlpacaClient,
    _entrada_triggered,
    _parchear,
)
from momentum_paper_trader.executor import ejecutar


def _env(monkeypatch, tmp_path, modo):
    monkeypatch.setenv("MOMENTUM_HALTS", modo)
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path / "estado"))
    monkeypatch.setenv("MOMENTUM_AVISOS_DIR", str(tmp_path / "avisos"))


def _lectura(**cambios) -> LecturaHalt:
    base = dict(
        ticker="RKLB", situacion="halt", en_halt=True, restringe_luld=None,
        fresco=True, fuente="snapshot", evento_en="2026-09-28T14:50:00+00:00",
        halt_id="quote:1", limit_up=None, limit_down=None, indicador_luld=None,
        detalle="quote Z cinta C",
    )
    base.update(cambios)
    return LecturaHalt(**base)


def _fijar(monkeypatch, lectura: LecturaHalt):
    def _leer(tickers, ahora, precios=None, **_kw):
        return {t.strip().upper(): lectura for t in tickers}

    monkeypatch.setattr("momentum_paper_trader.executor.halts.lecturas_de", _leer)


def test_modo_default_es_observar_y_un_typo_no_cambia(monkeypatch):
    monkeypatch.delenv("MOMENTUM_HALTS", raising=False)
    assert halts.modo() == "observar"
    assert halts.modo("ENFORCE") == "enforce"
    assert halts.modo("off") == "off"
    assert halts.modo("si") == "observar"


def test_enforce_con_halt_no_envia_la_entrada_ni_quema_la_senal(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "enforce")
    _fijar(monkeypatch, _lectura())
    e = _entrada_triggered()
    wl_path, rev_path, enviados, contextos = _parchear(monkeypatch, tmp_path, [e])
    antes = wl_path.read_text(encoding="utf-8")
    client = _FakeAlpacaClient(cash=40_000.0)
    metricas = telemetria.Metricas()

    assert ejecutar(client, CFG, dry_run=False, ahora=AHORA, metricas=metricas) == []
    assert client.ordenes_colocadas == []
    assert contextos == []
    assert estado.cargar(rev_path) == []
    assert wl_path.read_text(encoding="utf-8") == antes
    assert metricas.bloqueos[bloqueos.BLOQUEO_HALT] == 1
    lineas = (tmp_path / "estado" / "halts").glob("*.jsonl")
    texto = "".join(p.read_text(encoding="utf-8") for p in lineas)
    assert "BLOQUEO_HALT" not in texto or "bloqueada" in texto
    assert '"accion": "bloqueada"' in texto
    assert "RKLB" in texto
    assert not any(hasattr(client, nombre) and nombre.startswith("vend") for nombre in ("vender", "sell"))


def test_observar_con_halt_registra_y_igual_envia_la_entrada(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "observar")
    _fijar(monkeypatch, _lectura())
    e = _entrada_triggered()
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=40_000.0)
    metricas = telemetria.Metricas()

    nuevas = ejecutar(client, CFG, dry_run=False, ahora=AHORA, metricas=metricas)
    assert len(nuevas) == 1
    assert client.ordenes_colocadas
    assert bloqueos.BLOQUEO_HALT not in metricas.bloqueos
    texto = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "estado" / "halts").glob("*.jsonl"))
    assert '"accion": "observada"' in texto
    assert '"situacion": "halt"' in texto


def test_enforce_con_dato_ausente_no_entra(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "enforce")
    _fijar(monkeypatch, _lectura(
        situacion="desconocido", en_halt=None, fresco=False, halt_id=None,
        detalle="snapshot sin condiciones", fuente="snapshot",
    ))
    e = _entrada_triggered()
    _, rev_path, _, contextos = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=40_000.0)
    metricas = telemetria.Metricas()

    assert ejecutar(client, CFG, dry_run=False, ahora=AHORA, metricas=metricas) == []
    assert client.ordenes_colocadas == []
    assert contextos == []
    assert estado.cargar(rev_path) == []
    assert metricas.bloqueos[bloqueos.BLOQUEO_HALT] == 1


def test_enforce_con_reanudacion_fresca_si_entra(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "enforce")
    _fijar(monkeypatch, _lectura(
        situacion="reanudado", en_halt=False, halt_id=None, detalle="reanudación T",
        fuente="stream",
    ))
    e = _entrada_triggered()
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=40_000.0)

    assert len(ejecutar(client, CFG, dry_run=False, ahora=AHORA)) == 1
    assert client.ordenes_colocadas


def test_enforce_luld_fuera_de_banda_no_entra_y_dentro_si(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "enforce")
    e = _entrada_triggered()
    _parchear(monkeypatch, tmp_path, [e])
    fuera = _lectura(
        situacion="luld", en_halt=False, restringe_luld=True, halt_id=None,
        limit_up=3.24, limit_down=2.65, indicador_luld="B", detalle="LULD i=B",
        fuente="stream",
    )
    _fijar(monkeypatch, fuera)
    client = _FakeAlpacaClient(cash=40_000.0)
    metricas = telemetria.Metricas()
    assert ejecutar(client, CFG, dry_run=False, ahora=AHORA, metricas=metricas) == []
    assert metricas.bloqueos[bloqueos.BLOQUEO_HALT] == 1

    dentro = _lectura(
        situacion="reanudado", en_halt=False, restringe_luld=False, halt_id=None,
        limit_up=90.0, limit_down=70.0, indicador_luld="B", detalle="LULD i=B",
        fuente="stream",
    )
    _fijar(monkeypatch, dentro)
    client2 = _FakeAlpacaClient(cash=40_000.0)
    assert len(ejecutar(client2, CFG, dry_run=False, ahora=AHORA)) == 1


def test_off_no_consulta(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "off")

    def _boom(*_a, **_k):
        raise AssertionError("off no consulta")

    monkeypatch.setattr("momentum_paper_trader.executor.halts.lecturas_de", _boom)
    e = _entrada_triggered()
    _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=40_000.0)
    assert len(ejecutar(client, CFG, dry_run=False, ahora=AHORA)) == 1


def test_aviso_de_halt_una_vez_por_episodio_y_otro_halt_si_avisa(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "observar")
    enviados: list[str] = []
    halt = _lectura(halt_id="status:2026-09-28T14:00:00+00:00")
    base = tmp_path / "estado"
    assert halts.avisar_halt(halt, AHORA, base=base, enviar=enviados.append) is True
    assert halts.avisar_halt(halt, AHORA, base=base, enviar=enviados.append) is False
    assert len(enviados) == 1
    assert "RKLB" in enviados[0] and "halt" in enviados[0]
    assert "No se vende ni se cancela el stop." in enviados[0]
    assert "🧪 [PAPER]" in enviados[0]

    # Un desconocido no cierra el episodio y no manda otro aviso.
    claro_no = _lectura(situacion="desconocido", en_halt=None, halt_id=None, detalle="sin dato")
    assert halts.avisar_halt(claro_no, AHORA, base=base, enviar=enviados.append) is False
    assert len(enviados) == 1

    # La reanudación cierra. El halt siguiente, con otro id, avisa.
    reanudo = _lectura(situacion="reanudado", en_halt=False, halt_id=None, detalle="reanudación T")
    assert halts.avisar_halt(reanudo, AHORA, base=base, enviar=enviados.append) is False
    otro = _lectura(halt_id="status:2026-09-28T18:00:00+00:00", evento_en="2026-09-28T18:00:00+00:00")
    assert halts.avisar_halt(otro, AHORA, base=base, enviar=enviados.append) is True
    assert len(enviados) == 2


def test_posicion_abierta_en_halt_avisa_y_no_hay_camino_de_venta(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, "observar")
    rev = estado.RevisionIA(
        ticker="NTLA", creado_en="2026-09-08T14:00:00+00:00", entro=True,
        confianza=8, razonamiento="x", timestamp="2026-09-08T14:05:00+00:00",
        resultado="abierta",
    )
    enviados: list[str] = []

    def traer(_tickers):
        return {}

    # Sin snapshot el dato es desconocido: no se avisa (no es un halt)
    # y tampoco se vende. Con un halt inyectado en la lectura, sí.
    assert halts.revisar_posiciones_abiertas(
        AHORA, revisiones=[rev], traer=traer, base=tmp_path / "estado",
        directorio=tmp_path / "sin-stream", enviar=enviados.append,
    ) == []
    assert enviados == []

    halt = _lectura(ticker="NTLA", halt_id="status:ntla")

    def _leer(tickers, ahora, precios=None, **_kw):
        return {t: halt for t in tickers}

    monkeypatch.setattr(halts, "lecturas_de", _leer)
    avisados = halts.revisar_posiciones_abiertas(
        AHORA, revisiones=[rev], base=tmp_path / "estado", enviar=enviados.append,
    )
    assert avisados == ["NTLA"]
    avisados2 = halts.revisar_posiciones_abiertas(
        AHORA, revisiones=[rev], base=tmp_path / "estado", enviar=enviados.append,
    )
    assert avisados2 == []
    assert len(enviados) == 1

    fuente = open(halts.__file__, encoding="utf-8").read()
    for palabra in ("AlpacaPaperClient", "colocar_orden", "paper-api", "sell(", "cancel_order"):
        assert palabra not in fuente


def test_un_resultado_ausente_no_es_posicion_abierta():
    rev = estado.RevisionIA(
        ticker="NTLA", creado_en="t", entro=True, confianza=1, razonamiento="x",
        timestamp="t", resultado=None,
    )
    assert halts.posiciones_abiertas([rev]) == []


def test_el_registro_no_cae_dentro_del_repo(monkeypatch, tmp_path):
    lectura = _lectura()
    # Un directorio colgando del checkout no se escribe.
    repo = tmp_path  # no es el repo; el guard mira el checkout real
    adentro = __import__("pathlib").Path(halts.__file__).resolve().parents[1] / "momentum_paper_trader" / "_halts_test"
    try:
        halts.registrar(lectura, "observar", "observada", datetime.now(UTC), base=adentro)
        assert not (adentro / "halts").exists()
    finally:
        pass
    assert repo is not None
