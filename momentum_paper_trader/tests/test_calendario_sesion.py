"""Calendario de sesión: invierno, media sesión, feriado, archivo ausente.

El archivo de estas pruebas reemplaza al calendario normal de la suite.
Alpaca está simulado: nadie llama a la red.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from momentum_hunter import calendario, sesion
from momentum_hunter.report import _ventana_texto
from momentum_paper_trader import cierre, vigia
from momentum_paper_trader.aviso_calendario import avisar_si_desconocido
from momentum_paper_trader.calendario_job import refrescar
from momentum_paper_trader.config import PaperTraderConfig
from momentum_paper_trader.tests.test_executor import (
    AHORA,
    CFG,
    _FakeAlpacaClient,
    _entrada_triggered,
    _parchear,
)
from momentum_paper_trader import executor

NY = ZoneInfo("America/New_York")
_CFG = PaperTraderConfig()


def _utc(anio, mes, dia, hora, minuto=0, segundo=0):
    return datetime(anio, mes, dia, hora, minuto, segundo, tzinfo=UTC)


def _ny(anio, mes, dia, hora, minuto=0):
    return datetime(anio, mes, dia, hora, minuto, tzinfo=NY)


def _poner(monkeypatch, tmp_path, dias, desde, hasta, fetched_at="2026-11-01T12:00:00+00:00"):
    path = tmp_path / "calendario.json"
    calendario.guardar(path, {
        "fetched_at": fetched_at,
        "rango": {"desde": desde, "hasta": hasta},
        "clock": {"timestamp": fetched_at, "is_open": False},
        "dias": dias,
    })
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(path))
    return path


def _normal(monkeypatch, tmp_path, *fechas):
    return _poner(
        monkeypatch, tmp_path,
        {f: {"open": "09:30", "close": "16:00"} for f in fechas},
        min(fechas), max(fechas),
    )


# ------------------------- verano: igual que antes -------------------------

def test_verano_la_ventana_y_el_cierre_no_se_mueven(monkeypatch, tmp_path):
    _normal(monkeypatch, tmp_path, "2026-08-26")
    assert vigia.en_ventana(_utc(2026, 8, 26, 13, 0)) is True
    assert vigia.en_ventana(_utc(2026, 8, 26, 12, 59, 59)) is False
    assert vigia.en_ventana(_utc(2026, 8, 26, 20, 0, 59)) is True
    assert vigia.en_ventana(_utc(2026, 8, 26, 20, 1)) is False
    assert cierre.en_ventana_de_cierre(_utc(2026, 8, 26, 19, 50), _CFG) is True
    assert cierre.en_ventana_de_cierre(_utc(2026, 8, 26, 19, 45), _CFG) is False
    assert cierre.en_ventana_de_cierre(_utc(2026, 8, 26, 20, 0), _CFG) is False
    assert sesion.en_sesion(_utc(2026, 8, 26, 17, 0)) is True
    assert "≈15 minutos" in _ventana_texto(
        "gap_and_go", 19.9, _utc(2026, 8, 26, 19, 54))


# ------------------------- invierno, después del 2026-11-01 -------------------------

def test_invierno_el_vigia_sigue_hasta_el_cierre_de_las_2100_utc(monkeypatch, tmp_path):
    _normal(monkeypatch, tmp_path, "2026-12-02")
    # Apertura 14:30 UTC, cierre 21:00 UTC. La ventana arranca 30 min antes.
    assert vigia.en_ventana(_utc(2026, 12, 2, 13, 59)) is False
    assert vigia.en_ventana(_utc(2026, 12, 2, 14, 0)) is True
    assert vigia.en_ventana(_utc(2026, 12, 2, 20, 50)) is True
    assert vigia.en_ventana(_utc(2026, 12, 2, 21, 0, 59)) is True
    assert vigia.en_ventana(_utc(2026, 12, 2, 21, 1)) is False
    # El cierre diario cae a las 20:50 UTC (15:50 ET), no a las 19:50.
    assert cierre.en_ventana_de_cierre(_utc(2026, 12, 2, 20, 50), _CFG) is True
    assert cierre.en_ventana_de_cierre(_utc(2026, 12, 2, 19, 50), _CFG) is False
    assert cierre.en_ventana_de_cierre(_utc(2026, 12, 2, 21, 0), _CFG) is False
    assert sesion.hay_tiempo_para_operar(_utc(2026, 12, 2, 20, 0), 30) is True
    assert sesion.hay_tiempo_para_operar(_utc(2026, 12, 2, 20, 40), 30) is False


# ------------------------- 2026-11-27, media sesión -------------------------

def test_media_sesion_el_cierre_es_antes_de_las_1800_utc(monkeypatch, tmp_path):
    _poner(monkeypatch, tmp_path, {"2026-11-27": {"open": "09:30", "close": "13:00"}},
           "2026-11-27", "2026-11-27")
    # 13:00 ET = 18:00 UTC. Ventana del vigía 14:00–18:01 UTC.
    assert vigia.en_ventana(_utc(2026, 11, 27, 13, 59)) is False
    assert vigia.en_ventana(_utc(2026, 11, 27, 14, 0)) is True
    assert vigia.en_ventana(_utc(2026, 11, 27, 17, 50)) is True
    assert vigia.en_ventana(_utc(2026, 11, 27, 18, 0, 30)) is True
    assert vigia.en_ventana(_utc(2026, 11, 27, 18, 1)) is False
    assert cierre.en_ventana_de_cierre(_utc(2026, 11, 27, 17, 50), _CFG) is True
    assert cierre.en_ventana_de_cierre(_utc(2026, 11, 27, 17, 40), _CFG) is False
    assert cierre.en_ventana_de_cierre(_utc(2026, 11, 27, 18, 0), _CFG) is False
    # A las 12:00 ET todavía se puede entrar (falta una hora, mínimo 30).
    assert sesion.hay_tiempo_para_operar(_ny(2026, 11, 27, 12, 0), 30) is True
    assert sesion.hay_tiempo_para_operar(_ny(2026, 11, 27, 12, 40), 30) is False


# ------------------------- feriado 2026-11-26 -------------------------

def test_feriado_no_hay_ventana_ni_entradas_ni_cierre(monkeypatch, tmp_path):
    enviados = []
    monkeypatch.setattr("momentum_paper_trader.notify.enviar", lambda t: enviados.append(t))
    _poner(
        monkeypatch, tmp_path,
        {
            "2026-11-25": {"open": "09:30", "close": "16:00"},
            "2026-11-27": {"open": "09:30", "close": "13:00"},
        },
        "2026-11-25", "2026-11-27",
    )
    momento = _utc(2026, 11, 26, 15, 0)   # 10:00 ET, en plena "sesión" si nadie mira el calendario
    assert calendario.consultar(momento).motivo == calendario.MOTIVO_CERRADO
    assert vigia.en_ventana(momento) is False
    assert vigia.en_ventana(_utc(2026, 11, 26, 17, 50)) is False
    assert sesion.en_sesion(momento) is False
    assert sesion.hay_tiempo_para_operar(momento, 20) is False
    assert cierre.en_ventana_de_cierre(_utc(2026, 11, 26, 17, 50), _CFG) is False
    assert cierre.en_ventana_de_cierre(_utc(2026, 11, 26, 20, 50), _CFG) is False
    assert avisar_si_desconocido(momento) is False
    assert enviados == []


# ------------------------- sin archivo / archivo viejo -------------------------

def test_sin_archivo_no_entra_y_liquida_a_las_1300_ny(monkeypatch, tmp_path):
    enviados = []
    monkeypatch.setattr("momentum_paper_trader.notify.enviar", lambda t: enviados.append(t))
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(tmp_path / "no-esta.json"))
    # 2/11/2026 ya es horario de invierno: 13:00 ET = 18:00 UTC.
    assert sesion.en_sesion(_ny(2026, 11, 2, 11, 0)) is False
    assert sesion.hay_tiempo_para_operar(_ny(2026, 11, 2, 11, 0), 20) is False
    assert vigia.en_ventana(_ny(2026, 11, 2, 12, 29)) is False
    assert vigia.en_ventana(_ny(2026, 11, 2, 12, 50)) is True
    assert cierre.en_ventana_de_cierre(_ny(2026, 11, 2, 12, 40), _CFG) is False
    assert cierre.en_ventana_de_cierre(_ny(2026, 11, 2, 12, 50), _CFG) is True
    assert cierre.en_ventana_de_cierre(_ny(2026, 11, 2, 13, 0), _CFG) is False
    assert len(enviados) == 1
    assert "13:00" in enviados[0]
    assert "http" not in enviados[0].lower()
    # El segundo llamado, el mismo día, no repite el Telegram.
    assert cierre.en_ventana_de_cierre(_ny(2026, 11, 2, 12, 55), _CFG) is True
    assert len(enviados) == 1


def test_archivo_viejo_que_no_cubre_hoy_liquida_igual_a_las_1300(monkeypatch, tmp_path):
    enviados = []
    monkeypatch.setattr("momentum_paper_trader.notify.enviar", lambda t: enviados.append(t))
    _poner(monkeypatch, tmp_path, {"2026-11-01": {"open": "09:30", "close": "16:00"}},
           "2026-10-25", "2026-11-01")
    assert calendario.consultar(_utc(2026, 11, 2, 17, 50)).motivo == calendario.MOTIVO_NO_CUBRE
    assert sesion.en_sesion(_ny(2026, 11, 2, 11, 0)) is False
    assert cierre.en_ventana_de_cierre(_ny(2026, 11, 2, 12, 50), _CFG) is True
    assert len(enviados) == 1
    assert avisar_si_desconocido(_ny(2026, 11, 2, 12, 51)) is False
    assert len(enviados) == 1


def test_alpaca_caido_no_pisa_un_archivo_bueno(monkeypatch, tmp_path):
    path = _normal(monkeypatch, tmp_path, "2026-11-02")
    antes = path.read_text(encoding="utf-8")
    enviados = []
    monkeypatch.setattr("momentum_paper_trader.notify.enviar", lambda t: enviados.append(t))

    class _Caido:
        def calendario(self, inicio, fin):
            raise RuntimeError("timeout")

        def reloj_mercado(self):
            raise RuntimeError("timeout")

    assert refrescar(_Caido(), path, _utc(2026, 11, 2, 12, 0)) is False
    assert path.read_text(encoding="utf-8") == antes
    assert sesion.en_sesion(_utc(2026, 11, 2, 15, 0)) is True
    assert avisar_si_desconocido(_utc(2026, 11, 2, 15, 0)) is False
    assert enviados == []


def test_el_refresco_escribe_rango_y_no_inventa_una_fila_sin_cierre(tmp_path):
    path = tmp_path / "nuevo.json"

    class _Ok:
        def calendario(self, inicio, fin):
            self.rango = (inicio, fin)
            return [
                {"date": "2026-11-02", "open": "09:30", "close": "16:00"},
                {"date": "2026-11-27", "open": "09:30"},          # sin cierre: se descarta
                {"date": "2026-11-03", "open": "09:30", "close": "16:00"},
            ]

        def reloj_mercado(self):
            return {"timestamp": "2026-11-02T15:00:00+00:00", "is_open": True,
                    "next_open": "2026-11-03T14:30:00+00:00", "next_close": "2026-11-02T21:00:00+00:00"}

    cliente = _Ok()
    assert refrescar(cliente, path, _utc(2026, 11, 2, 15, 0)) is True
    doc = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert doc["rango"] == {"desde": "2026-10-26", "hasta": "2027-12-07"}
    assert "fetched_at" in doc and doc["clock"]["is_open"] is True
    assert doc["dias"]["2026-11-02"] == {"open": "09:30", "close": "16:00"}
    assert "2026-11-27" not in doc["dias"]
    assert cliente.rango == ("2026-10-26", "2027-12-07")


def test_un_calendario_vacio_no_borra_el_archivo_anterior(tmp_path):
    path = tmp_path / "previo.json"
    path.write_text('{"rango": {"desde": "2026-11-02", "hasta": "2026-11-02"}, "dias": {}}\n', encoding="utf-8")

    class _Vacio:
        def calendario(self, inicio, fin):
            return []

        def reloj_mercado(self):
            return {"is_open": False}

    assert refrescar(_Vacio(), path, _utc(2026, 11, 2, 12, 0)) is False
    assert "2026-11-02" in path.read_text(encoding="utf-8")


def test_el_get_de_calendario_va_al_host_paper(monkeypatch):
    from momentum_paper_trader.alpaca_client import AlpacaPaperClient
    llamadas = []

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"date": "2026-11-02", "open": "09:30", "close": "16:00"}]

    def _get(url, headers, timeout, params=None):
        llamadas.append((url, params))
        return _R()

    monkeypatch.setattr("momentum_paper_trader.alpaca_client.requests.get", _get)
    assert AlpacaPaperClient("k", "s").calendario("2026-10-26", "2027-12-07")[0]["close"] == "16:00"
    assert llamadas[0][0] == "https://paper-api.alpaca.markets/v2/calendar"
    assert llamadas[0][1] == {"start": "2026-10-26", "end": "2027-12-07"}
    # El método no arma la URL a mano: sale de `_BASE_URL`, que está
    # hardcodeado al host paper. Si alguien lo apunta a la cuenta real,
    # este assert falla.
    fuente = inspect.getsource(AlpacaPaperClient.calendario)
    assert "_BASE_URL" in fuente
    assert "api.alpaca.markets" not in fuente
    modulo = inspect.getsource(__import__("momentum_paper_trader.alpaca_client", fromlist=["x"]))
    assert '_BASE_URL = "https://paper-api.alpaca.markets/v2"' in modulo


def test_sin_calendario_el_ejecutor_no_abre_aunque_el_reloj_diga_abierto(monkeypatch, tmp_path):
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(tmp_path / "no-esta.json"))
    e = _entrada_triggered()
    _, rev_path, enviados, contextos = _parchear(monkeypatch, tmp_path, [e])
    client = _FakeAlpacaClient(cash=40_000.0, mercado_abierto=True)

    assert executor.ejecutar(client, CFG, dry_run=False, ahora=AHORA) == []
    assert client.ordenes_colocadas == []
    assert contextos == []
    from momentum_paper_trader import estado
    assert estado.cargar(rev_path) == []
    assert any("calendario" in t.lower() for t in enviados)
    assert any("13:00" in t for t in enviados)
