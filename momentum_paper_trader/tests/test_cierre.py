"""Pruebas del cierre diario -- Alpaca y Telegram mockeados.

El hueco que esto tapa: las patas de salida del bracket son órdenes "del
día", así que si no se ejecutan se cancelan al cerrar el mercado y la
posición queda abierta durante la noche SIN stop y SIN objetivo."""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_paper_trader import cierre, ia_decision
from momentum_paper_trader.config import PaperTraderConfig

CFG = PaperTraderConfig()
# Aguantar hasta mañana está apagado por defecto desde el 2026-08-25
# (ver `config.permitir_aguantar_overnight`). Los tests que cubren esa
# lógica la encienden explícitamente con esta config.
_CFG_CON_OVERNIGHT = PaperTraderConfig(permitir_aguantar_overnight=True)
_CFG_SIN_OVERNIGHT = PaperTraderConfig(permitir_aguantar_overnight=False)


def _t(hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 8, 24, hora, minuto, tzinfo=UTC)


class _FakeClient:
    def __init__(self, posiciones=None, falla_leer=False, falla_cerrar=False,
                 falla_stop=False):
        self._posiciones = posiciones or []
        self._falla_leer = falla_leer
        self._falla_cerrar = falla_cerrar
        self._falla_stop = falla_stop
        self.cerradas: list[str] = []
        self.stops: list[tuple] = []

    @property
    def cerro(self) -> bool:
        return bool(self.cerradas)

    def posiciones(self):
        if self._falla_leer:
            raise RuntimeError("Alpaca caído")
        return self._posiciones

    def ordenes_abiertas(self):
        return []

    def cancelar_ordenes_de(self, ticker, abiertas):
        return 0

    def colocar_stop_protector(self, ticker, cantidad, stop):
        if self._falla_stop:
            raise RuntimeError("rechazado")
        self.stops.append((ticker, cantidad, stop))
        return "stop-1"

    def cerrar_posicion(self, ticker):
        if self._falla_cerrar:
            raise RuntimeError("rechazado")
        self.cerradas.append(ticker)
        return {}


def _parchear(monkeypatch, cerrar=True, razon="tesis agotada"):
    """Por defecto la IA dice CERRAR -- el comportamiento conservador."""
    enviados: list[str] = []
    monkeypatch.setattr(cierre, "enviar_telegram", lambda t: enviados.append(t))
    monkeypatch.setattr(
        cierre.ia_decision, "decidir_cierre",
        lambda ctx: ia_decision.DecisionCierre(cerrar=cerrar, confianza=9, razonamiento=razon))
    return enviados


_POSICION = {"symbol": "RKLB", "qty": "65", "unrealized_pl": "123.45",
             "current_price": "80.00", "avg_entry_price": "78.10"}


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

def test_mensaje_suma_solo_lo_realizado():
    texto = cierre._mensaje(
        [({"symbol": "AAA", "qty": "10", "unrealized_pl": "100.00"}, "salió"),
         ({"symbol": "BBB", "qty": "5", "unrealized_pl": "-40.00"}, "se rompió")], [])
    assert "+$100.00" in texto and "-$40.00" in texto
    assert "+$60.00" in texto   # 100 - 40


def test_mensaje_sin_pl_no_inventa_cifras():
    texto = cierre._mensaje([({"symbol": "AAA", "qty": "10"}, "x")], [])
    assert "AAA" in texto
    assert "Resultado realizado" not in texto


def test_mensaje_muestra_razonamiento_y_stop_de_las_aguantadas():
    texto = cierre._mensaje([], [({"symbol": "CCC", "qty": "8"}, "sigue viva", 77.5)])
    assert "Se mantienen hasta mañana" in texto
    assert "sigue viva" in texto
    assert "$77.50" in texto
    assert "hueco de apertura" in texto   # el aviso honesto siempre acompaña


# ------------------------- la IA decide (2026-08-21) -------------------------
# El usuario señaló que una regla fija no distingue "esto se rompió" de
# "esto va lento pero sigue vivo" -- que es justo lo que la capa de IA
# existe para juzgar. Pero aguantar SIN protección sería peor que
# cualquiera de las dos opciones: es el estado que este módulo nació para
# eliminar. De ahí la condición innegociable.

def test_si_la_ia_aguanta_se_pone_stop_protector_y_no_se_cierra(monkeypatch):
    # Con el flag ENCENDIDO: este test cubre la lógica de aguantar, que
    # sigue entera aunque hoy esté apagada por defecto (2026-08-25).
    enviados = _parchear(monkeypatch, cerrar=False, razon="el catalizador sigue vivo")
    client = _FakeClient([_POSICION])

    cerradas = cierre.cerrar_si_toca(client, _CFG_CON_OVERNIGHT, _t(19, 50))

    assert cerradas == []            # no se cerró
    assert client.cerradas == []
    assert len(client.stops) == 1    # pero SÍ quedó protegida
    ticker, cantidad, stop = client.stops[0]
    assert ticker == "RKLB" and cantidad == 65
    assert stop == round(80.00 * 0.97, 2)   # 3% bajo el precio actual
    assert "Se mantienen hasta mañana" in enviados[0]
    assert "el catalizador sigue vivo" in enviados[0]


def test_si_falla_el_stop_protector_se_cierra_igual(monkeypatch):
    # La condición innegociable: nunca queda una posición aguantada sin
    # protección. Si el stop no se puede poner, se cierra.
    enviados = _parchear(monkeypatch, cerrar=False, razon="quiero aguantar")
    client = _FakeClient([_POSICION], falla_stop=True)

    cerradas = cierre.cerrar_si_toca(client, CFG, _t(19, 50))

    assert client.cerradas == ["RKLB"]
    assert len(cerradas) == 1


def test_sin_precio_para_calcular_el_stop_se_cierra(monkeypatch):
    _parchear(monkeypatch, cerrar=False)
    sin_precio = {"symbol": "RKLB", "qty": "65"}   # ni current_price ni avg_entry_price
    client = _FakeClient([sin_precio])

    cierre.cerrar_si_toca(client, CFG, _t(19, 50))

    assert client.cerradas == ["RKLB"]
    assert client.stops == []


def test_decide_una_por_una_no_todo_o_nada(monkeypatch):
    # Lo que motivó el rediseño: puede cerrar una y aguantar otra.
    enviados: list[str] = []
    monkeypatch.setattr(cierre, "enviar_telegram", lambda t: enviados.append(t))

    def _por_ticker(ctx):
        aguanta = "BUENA" in ctx
        return ia_decision.DecisionCierre(
            cerrar=not aguanta, confianza=9,
            razonamiento="sigue viva" if aguanta else "se rompió")

    monkeypatch.setattr(cierre.ia_decision, "decidir_cierre", _por_ticker)
    client = _FakeClient([
        {"symbol": "MALA", "qty": "10", "current_price": "5.00", "unrealized_pl": "-20"},
        {"symbol": "BUENA", "qty": "20", "current_price": "9.00", "unrealized_pl": "50"},
    ])

    cerradas = cierre.cerrar_si_toca(client, _CFG_CON_OVERNIGHT, _t(19, 50))

    assert client.cerradas == ["MALA"]
    assert [s[0] for s in client.stops] == ["BUENA"]
    assert len(cerradas) == 1
    assert "MALA" in enviados[0] and "BUENA" in enviados[0]


def test_una_posicion_que_falla_al_cerrar_no_frena_las_demas(monkeypatch):
    _parchear(monkeypatch, cerrar=True)
    client = _FakeClient([_POSICION], falla_cerrar=True)

    assert cierre.cerrar_si_toca(client, CFG, _t(19, 50)) == []


def test_el_stop_protector_va_por_debajo_del_precio_actual():
    stop = cierre._stop_protector({"current_price": "100.00"}, CFG)
    assert stop == 97.0
    assert stop < 100.0


def test_contexto_incluye_resultado_abierto_y_clima():
    ctx = cierre._contexto_posicion(_POSICION, clima="debil")
    assert "RKLB" in ctx
    assert "+$123.45" in ctx
    assert "debil" in ctx


# ------------------------- aguantar desactivado (2026-08-25) -------------------------
# Decisión del usuario: liquidar todo al cierre hasta tener ~50
# operaciones con las que juzgar si aguantar aporta algo. La lógica de
# IA no se borró, se apagó -- ver `config.permitir_aguantar_overnight`.

_EN_VENTANA = _t(19, 55)


def test_con_aguantar_desactivado_se_cierra_todo(monkeypatch):
    # Aunque la IA quisiera aguantar, no se le pregunta siquiera.
    consultas: list[str] = []
    enviados = _parchear(monkeypatch, cerrar=False, razon="el catalizador sigue vivo")
    real = cierre.ia_decision.decidir_cierre
    monkeypatch.setattr(
        cierre.ia_decision, "decidir_cierre",
        lambda ctx: (consultas.append(ctx), real(ctx))[1])
    client = _FakeClient(posiciones=[dict(_POSICION)])

    cerradas = cierre.cerrar_si_toca(client, _CFG_SIN_OVERNIGHT, _EN_VENTANA)

    assert [c.get("symbol") for c in cerradas] == ["RKLB"]
    assert client.cerradas == ["RKLB"]
    assert client.stops == [], "no debe quedar ninguna posición viva de un día para otro"
    assert consultas == [], "no se gasta una llamada a la IA si su respuesta no se puede acatar"
    assert "desactivado" in enviados[0]


def test_con_aguantar_activado_la_ia_vuelve_a_mandar(monkeypatch):
    # El flag apaga la función, no la borra: encendido, todo el camino
    # de decisión con IA sigue funcionando igual que antes.
    _parchear(monkeypatch, cerrar=False, razon="el catalizador sigue vivo")
    client = _FakeClient(posiciones=[dict(_POSICION)])

    cerradas = cierre.cerrar_si_toca(client, _CFG_CON_OVERNIGHT, _EN_VENTANA)

    assert cerradas == []
    assert client.stops != []   # aguantó, con su stop protector


def test_el_default_de_la_config_es_no_aguantar():
    # Que nadie encienda esto sin querer: el default del sistema es la
    # decisión del usuario, no la de quien construya un PaperTraderConfig.
    assert PaperTraderConfig().permitir_aguantar_overnight is False
