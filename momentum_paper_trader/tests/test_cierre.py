"""Pruebas del cierre diario -- Alpaca y Telegram mockeados.

El hueco que esto tapa: las patas de salida del bracket son órdenes "del
día", así que si no se ejecutan se cancelan al cerrar el mercado y la
posición queda abierta durante la noche SIN stop y SIN objetivo."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_paper_trader import cierre
from momentum_paper_trader.config import PaperTraderConfig

CFG = PaperTraderConfig()


def _t(hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 8, 24, hora, minuto, tzinfo=UTC)


class _FakeClient:
    def __init__(self, posiciones=None, falla_leer=False, falla_cerrar=False):
        self._posiciones = posiciones or []
        self._falla_leer = falla_leer
        self._falla_cerrar = falla_cerrar
        self.cerro = False

    def posiciones(self):
        if self._falla_leer:
            raise RuntimeError("Alpaca caído")
        return self._posiciones

    def cerrar_todas_las_posiciones(self):
        if self._falla_cerrar:
            raise RuntimeError("rechazado")
        self.cerro = True
        return []


def _parchear(monkeypatch):
    enviados: list[str] = []
    monkeypatch.setattr(cierre, "enviar_telegram", lambda t: enviados.append(t))
    return enviados


_POSICION = {"symbol": "RKLB", "qty": "65", "unrealized_pl": "123.45"}


# ------------------------- la ventana -------------------------

def test_ventana_activa_10_min_antes_del_cierre():
    assert cierre.en_ventana_de_cierre(_t(19, 50), CFG) is True
    assert cierre.en_ventana_de_cierre(_t(19, 55), CFG) is True


def test_fuera_de_la_ventana_durante_el_dia():
    for h, m in ((13, 35), (15, 0), (18, 30), (19, 45)):
        assert cierre.en_ventana_de_cierre(_t(h, m), CFG) is False


def test_despues_del_cierre_ya_no_intenta():
    # A mercado cerrado una orden no se ejecutaría hasta mañana -- justo
    # lo contrario de lo que se busca.
    assert cierre.en_ventana_de_cierre(_t(20, 0), CFG) is False
    assert cierre.en_ventana_de_cierre(_t(21, 30), CFG) is False


def test_ventana_configurable():
    cfg = PaperTraderConfig(minutos_antes_del_cierre=30)
    assert cierre.en_ventana_de_cierre(_t(19, 35), cfg) is True
    assert cierre.en_ventana_de_cierre(_t(19, 25), cfg) is False


# ------------------------- el cierre -------------------------

def test_cierra_y_avisa_dentro_de_la_ventana(monkeypatch):
    enviados = _parchear(monkeypatch)
    client = _FakeClient([_POSICION])

    cerradas = cierre.cerrar_si_toca(client, CFG, _t(19, 50))

    assert len(cerradas) == 1
    assert client.cerro is True
    assert len(enviados) == 1
    assert "CIERRE DEL DÍA" in enviados[0]
    assert "RKLB" in enviados[0] and "+$123.45" in enviados[0]
    assert "[PAPER]" in enviados[0]


def test_no_cierra_fuera_de_la_ventana(monkeypatch):
    enviados = _parchear(monkeypatch)
    client = _FakeClient([_POSICION])

    assert cierre.cerrar_si_toca(client, CFG, _t(15, 0)) == []
    assert client.cerro is False
    assert enviados == []


def test_sin_posiciones_no_avisa(monkeypatch):
    # El caso normal de la mayoría de los días: nada abierto, silencio.
    enviados = _parchear(monkeypatch)
    client = _FakeClient([])

    assert cierre.cerrar_si_toca(client, CFG, _t(19, 50)) == []
    assert client.cerro is False
    assert enviados == []


def test_es_idempotente_en_la_segunda_corrida(monkeypatch):
    # 19:50 cierra; a las 19:55 ya no hay nada que cerrar.
    enviados = _parchear(monkeypatch)
    assert cierre.cerrar_si_toca(_FakeClient([_POSICION]), CFG, _t(19, 50)) != []
    assert cierre.cerrar_si_toca(_FakeClient([]), CFG, _t(19, 55)) == []
    assert len(enviados) == 1   # un solo aviso, no dos


def test_apagado_por_configuracion_no_hace_nada(monkeypatch):
    enviados = _parchear(monkeypatch)
    cfg = PaperTraderConfig(cerrar_antes_del_cierre=False)
    client = _FakeClient([_POSICION])

    assert cierre.cerrar_si_toca(client, cfg, _t(19, 50)) == []
    assert client.cerro is False


def test_fallo_al_leer_posiciones_no_lanza(monkeypatch):
    _parchear(monkeypatch)
    client = _FakeClient(falla_leer=True)
    assert cierre.cerrar_si_toca(client, CFG, _t(19, 50)) == []


def test_fallo_al_cerrar_no_lanza_ni_avisa_en_falso(monkeypatch):
    # Si el cierre falló, NO debe mandarse un mensaje diciendo que se
    # liquidó -- se reintenta en la corrida siguiente de la ventana.
    enviados = _parchear(monkeypatch)
    client = _FakeClient([_POSICION], falla_cerrar=True)

    assert cierre.cerrar_si_toca(client, CFG, _t(19, 50)) == []
    assert enviados == []


# ------------------------- el mensaje -------------------------

def test_mensaje_suma_el_resultado_del_dia():
    texto = cierre._mensaje([
        {"symbol": "AAA", "qty": "10", "unrealized_pl": "100.00"},
        {"symbol": "BBB", "qty": "5", "unrealized_pl": "-40.00"},
    ])
    assert "+$100.00" in texto and "-$40.00" in texto
    assert "+$60.00" in texto   # 100 - 40


def test_mensaje_sin_pl_no_inventa_cifras():
    texto = cierre._mensaje([{"symbol": "AAA", "qty": "10"}])
    assert "AAA" in texto
    assert "Resultado del día" not in texto
