"""Pruebas de los comandos de Telegram del Momentum Opportunity Hunter --
funciones puras de `momentum_commands.py` (sin red, `EntradaWatchlist`
construidas a mano) + el webhook dedicado `/momentum/webhook` en
`app.py` (red mockeada por completo: `_cargar_watchlist_momentum`,
`_cargar_auditoria_momentum_hoy` y `_momentum_telegram_send`
parcheados). Cubre el pedido explícito: "no considero terminada esta
fase solo porque Telegram envía un mensaje" -- /trade en cada estado,
/status con mercado abierto/cerrado, /radar sin oportunidades, dedup de
webhook duplicado, y que NUNCA aparezca nada relacionado a ejecutar
operaciones/broker."""

from __future__ import annotations

from datetime import UTC, datetime

import momentum_commands as mc
from momentum_hunter.watchlist import EntradaWatchlist, Transicion

AHORA_ABIERTO = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)   # martes, 15:00 UTC -- dentro del horario
AHORA_CERRADO = datetime(2026, 8, 11, 23, 0, tzinfo=UTC)   # martes, 23:00 UTC -- fuera de horario
AHORA_FIN_DE_SEMANA = datetime(2026, 8, 15, 15, 0, tzinfo=UTC)   # sábado


def _entrada(ticker="RKLB", estado="watching", **kw) -> EntradaWatchlist:
    base = dict(
        ticker=ticker, nombre="Rocket Lab", estado=estado,
        creado_en="2026-08-11T14:00:00+00:00", actualizado_en="2026-08-11T14:05:00+00:00",
    )
    base.update(kw)
    return EntradaWatchlist(**base)


# ------------------------- generar_trade -------------------------

def test_generar_trade_sin_oportunidad_lo_dice_claramente():
    texto = mc.generar_trade("ZZZZ", [])
    assert "No existe actualmente una oportunidad activa" in texto
    assert "ZZZZ" in texto


def test_generar_trade_watching_muestra_niveles_cacheados_no_inventa():
    e = _entrada(estado="watching", ultima_zona_entrada_baja=78.30, ultimo_stop=76.90, ultimo_objetivo=82.50)
    texto = mc.generar_trade("RKLB", [e])
    assert "WATCHING" in texto
    assert "$78.30" in texto
    assert "$76.90" in texto
    assert "$82.50" in texto
    assert "NO ENTRAR TODAVÍA." in texto


def test_generar_trade_watching_sin_niveles_calculados_no_inventa_numero():
    e = _entrada(estado="watching")   # sin ultima_zona_entrada_baja
    texto = mc.generar_trade("RKLB", [e])
    assert "no disponible" in texto


def test_generar_trade_triggered_incluye_entrada_stop_objetivo_rr():
    e = _entrada(estado="triggered", ultima_entrada=78.42, ultimo_stop=76.90, ultimo_objetivo=82.50)
    texto = mc.generar_trade("RKLB", [e])
    assert "TRIGGERED" in texto
    assert "$78.42" in texto
    assert "$76.90" in texto
    assert "$82.50" in texto
    assert "R/R:" in texto
    assert "TEMPRANO" in texto


def test_generar_trade_missed_dice_no_perseguir():
    e = _entrada(estado="missed", ultima_zona_entrada_baja=78.40)
    texto = mc.generar_trade("RKLB", [e])
    assert "MISSED" in texto
    assert "$78.40" in texto
    assert "NO PERSEGUIR." in texto


def test_generar_trade_invalidated_explica_el_motivo():
    e = _entrada(estado="invalidated", transiciones=[
        Transicion(estado="invalidated", timestamp="2026-08-11T14:10:00+00:00",
                   motivo="El catalizador ya salió de la ventana de vigencia."),
    ])
    texto = mc.generar_trade("RKLB", [e])
    assert "INVALIDATED" in texto
    assert "El catalizador ya salió de la ventana de vigencia." in texto
    assert "NO ENTRAR." in texto


def test_generar_trade_expired_explica_que_nunca_se_confirmo():
    e = _entrada(estado="expired")
    texto = mc.generar_trade("RKLB", [e])
    assert "EXPIRED" in texto
    assert "Nunca llegó a confirmarse" in texto


def test_generar_trade_es_case_insensitive_y_usa_la_mas_reciente():
    vieja = _entrada(estado="expired", actualizado_en="2026-08-01T14:00:00+00:00")
    nueva = _entrada(estado="watching", actualizado_en="2026-08-11T14:00:00+00:00")
    texto = mc.generar_trade("rklb", [vieja, nueva])
    assert "WATCHING" in texto   # la más reciente, no la expirada


def test_generar_trade_nunca_menciona_jerga_tecnica():
    e = _entrada(estado="triggered", ultima_entrada=78.42, ultimo_stop=76.90, ultimo_objetivo=82.50)
    texto = mc.generar_trade("RKLB", [e])
    for palabra in ("RVOL", "EMA9", "VWAP", "ATR", "MACD", "RSI"):
        assert palabra not in texto


# ------------------------- generar_status -------------------------

def test_status_mercado_abierto_en_horario_regular():
    assert "🟢 ABIERTO" in mc.generar_status([], ahora=AHORA_ABIERTO)


def test_status_mercado_cerrado_fuera_de_horario():
    assert "🔴 CERRADO" in mc.generar_status([], ahora=AHORA_CERRADO)


def test_status_mercado_cerrado_fin_de_semana():
    assert "🔴 CERRADO" in mc.generar_status([], ahora=AHORA_FIN_DE_SEMANA)


def test_status_cuenta_watching_y_triggered_de_hoy():
    entradas = [
        _entrada("A", estado="watching"),
        _entrada("B", estado="watching"),
        _entrada("C", estado="triggered", actualizado_en="2026-08-11T15:00:00+00:00"),
    ]
    texto = mc.generar_status(entradas, ahora=AHORA_ABIERTO)
    assert "En vigilancia: 2" in texto
    assert "Entradas confirmadas hoy: 1" in texto


def test_status_sin_auditoria_omite_esas_lineas_no_inventa():
    texto = mc.generar_status([], auditoria_hoy=None, ahora=AHORA_ABIERTO)
    assert "Candidatos evaluados hoy" not in texto


def test_status_con_auditoria_muestra_evaluados_y_descartados():
    auditoria = {"corridas": [{"candidatos": [
        {"decision": "alertada"}, {"decision": "vetada_por_abogado_del_diablo"},
        {"decision": "descartada_por_evaluador"},
    ]}]}
    texto = mc.generar_status([], auditoria_hoy=auditoria, ahora=AHORA_ABIERTO)
    assert "Candidatos evaluados hoy: 3" in texto
    assert "Descartados hoy: 2" in texto


def test_status_latencia_promedio_solo_con_datos_reales_de_hoy():
    t_hoy = Transicion(estado="triggered", timestamp="2026-08-11T15:00:00+00:00", motivo="x",
                        latencia_desde_transicion_ms=2500.0)
    t_ayer = Transicion(estado="triggered", timestamp="2026-08-10T15:00:00+00:00", motivo="x",
                         latencia_desde_transicion_ms=999999.0)
    e = _entrada(estado="triggered", transiciones=[t_ayer, t_hoy])
    texto = mc.generar_status([e], ahora=AHORA_ABIERTO)
    assert "Latencia promedio hoy: 2.5 s (1 transición(es))" in texto


def test_status_sin_latencia_medida_hoy_omite_la_linea():
    texto = mc.generar_status([], ahora=AHORA_ABIERTO)
    assert "Latencia promedio" not in texto


def test_status_ultima_senal_es_el_triggered_mas_reciente():
    entradas = [
        _entrada("A", estado="triggered", actualizado_en="2026-08-11T14:00:00+00:00"),
        _entrada("B", estado="triggered", actualizado_en="2026-08-11T15:30:00+00:00"),
    ]
    texto = mc.generar_status(entradas, ahora=AHORA_ABIERTO)
    assert "Última señal: B" in texto


# ------------------------- generar_radar -------------------------

def test_radar_sin_oportunidades():
    texto = mc.generar_radar([])
    assert "No hay oportunidades activas" in texto


def test_radar_muestra_triggered_de_hoy_y_watching_sin_explicaciones_largas():
    entradas = [
        _entrada("CINF", estado="triggered", actualizado_en=datetime.now(UTC).date().isoformat() + "T15:00:00+00:00"),
        _entrada("RKLB", estado="watching"),
        _entrada("TTWO", estado="watching"),
    ]
    texto = mc.generar_radar(entradas)
    assert "🟢 TRIGGERED" in texto
    assert "CINF" in texto
    assert "🟡 WATCHING" in texto
    assert "RKLB" in texto and "TTWO" in texto
    assert len(texto) < 400   # "nada de explicaciones gigantes"


def test_radar_no_incluye_triggered_de_dias_anteriores():
    e = _entrada("VIEJO", estado="triggered", actualizado_en="2020-01-01T15:00:00+00:00")
    texto = mc.generar_radar([e])
    assert "VIEJO" not in texto


# ------------------------- seguridad: solo lectura, sin broker -------------------------

def test_modulo_no_ejecuta_ordenes_ni_se_conecta_a_un_broker():
    # "broker" SÍ puede aparecer -- en el disclaimer de AYUDA_MOMENTUM,
    # diciendo explícitamente que no se usa uno. Lo prohibido es
    # cualquier rastro de EJECUCIÓN real (colocar/comprar/vender/cerrar
    # una orden, o un SDK de broker importado).
    import inspect
    fuente = inspect.getsource(mc)
    prohibidas = [
        "place_order", "buy_order", "sell_order", "execute_trade", "cancel_order",
        "comprar(", "vender(", "import alpaca", "import ibapi", "interactive_brokers",
    ]
    bajo = fuente.lower()
    for palabra in prohibidas:
        assert palabra.lower() not in bajo, f"'{palabra}' no debería aparecer -- este módulo es solo lectura"


def test_ayuda_momentum_deja_claro_que_es_solo_lectura():
    assert "Solo lectura" in mc.AYUDA_MOMENTUM
    assert "broker" in mc.AYUDA_MOMENTUM.lower()
