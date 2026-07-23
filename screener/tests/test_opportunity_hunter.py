"""Pruebas del Opportunity Hunter -- sin red. Verifica que cada patrón
exige TODAS sus condiciones independientes (no dispara con una sola
señal), que "Convicción" reusa los sub_scores reales, que la decisión
(Comprar hoy/Esperar/No operar) es determinística, y que el mensaje final
dice explícitamente "no encontré ninguna oportunidad" cuando corresponde
-- nunca fuerza contenido."""

from __future__ import annotations

from screener import opportunity_hunter as oh
from screener.data.provider import Barras, Fundamentales
from screener.scoring import Puntuacion


def _barras_ruptura(vol_normal=1e6, vol_ultima=2e6, n=300) -> Barras:
    """Tendencia alcista fuerte, en nuevos máximos, con volumen 2x el
    promedio de los últimos 20 días -- dispara el patrón "ruptura"."""
    precios = [100.0 * (1.003 ** i) for i in range(n)]
    vols = [vol_normal] * n
    vols[-1] = vol_ultima
    return Barras("RUP", [str(i) for i in range(n)], precios,
                 [p * 1.005 for p in precios], [p * 0.995 for p in precios], vols)


def _barras_pullback(n=300) -> Barras:
    """Tendencia alcista fuerte de fondo, con una corrección reciente de 8
    barras que deja el precio a ~3% de la media de 50 días y el RSI en
    zona saludable (~43) -- dispara el patrón "pullback"."""
    precios = []
    p = 100.0
    for i in range(n):
        if i < n - 8:
            p *= 1.003
        else:
            p *= 0.994 if i % 2 == 0 else 1.0
        precios.append(p)
    return Barras("PB", [str(i) for i in range(n)], precios,
                 [x * 1.005 for x in precios], [x * 0.995 for x in precios], [1e6] * n)


def _barras_planas(n=300, precio=100.0) -> Barras:
    """Sin tendencia clara -- no debe disparar ningún patrón técnico."""
    precios = [precio] * n
    return Barras("FLAT", [str(i) for i in range(n)], precios,
                 [p * 1.01 for p in precios], [p * 0.99 for p in precios], [1e6] * n)


# ------------------------- detección de patrones -------------------------

def test_detectar_ruptura_exige_las_tres_condiciones():
    b = _barras_ruptura()
    assert oh._detectar_ruptura(b) is True
    # sin volumen inusual, no dispara aunque el resto se cumpla
    b_sin_volumen = _barras_ruptura(vol_normal=1e6, vol_ultima=1e6)
    assert oh._detectar_ruptura(b_sin_volumen) is False


def test_detectar_pullback_exige_negocio_solido():
    b = _barras_pullback()
    fund_sin_crecimiento = Fundamentales("PB", crecimiento_ingresos=None)
    # sin calidad alta ni crecimiento, no alcanza -- técnico solo no basta
    assert oh._detectar_pullback(b, {"tendencia": 100.0}, fund_sin_crecimiento) is False
    assert oh._detectar_pullback(b, {"calidad": 70.0}, fund_sin_crecimiento) is True
    fund_creciendo = Fundamentales("PB", crecimiento_ingresos=0.15)
    assert oh._detectar_pullback(b, {}, fund_creciendo) is True


def test_detectar_pullback_fuera_de_la_banda_no_dispara():
    b = _barras_planas()  # precio == sma50 pero tendencia no es 3.0 (sin pendiente)
    assert oh._detectar_pullback(b, {"calidad": 90.0}, Fundamentales("FLAT")) is False


def test_detectar_valor_impulso_exige_los_tres_percentiles():
    assert oh._detectar_valor_impulso({"valor": 80, "calidad": 70, "momentum": 80}) is True
    assert oh._detectar_valor_impulso({"valor": 80, "calidad": 70, "momentum": 50}) is False
    assert oh._detectar_valor_impulso({"valor": 50, "calidad": 70, "momentum": 80}) is False
    assert oh._detectar_valor_impulso({"valor": 80, "calidad": 30, "momentum": 80}) is False


def test_detectar_patron_prioridad_ruptura_sobre_el_resto():
    b = _barras_ruptura()
    # esta barra también podría calificar como valor_impulso si se mirara
    # el sub aislado, pero ruptura tiene prioridad
    sub = {"valor": 90.0, "calidad": 90.0, "momentum": 90.0}
    assert oh.detectar_patron(b, sub, Fundamentales("RUP")) == "ruptura"


def test_detectar_patron_ninguno_da_none():
    b = _barras_planas()
    assert oh.detectar_patron(b, {}, Fundamentales("FLAT")) is None


def test_volumen_ratio_calculo_basico():
    b = _barras_ruptura(vol_normal=1e6, vol_ultima=1.5e6)
    assert oh._volumen_ratio(b) == 1.5


def test_volumen_ratio_historia_insuficiente_da_none():
    b = Barras("X", ["0"], [100.0], [101.0], [99.0], [1e6])
    assert oh._volumen_ratio(b) is None


# ------------------------- convicción / objetivo / decisión -------------------------

def test_conviccion_reusa_los_sub_scores_reales():
    sub = {"momentum": 80.0, "tendencia": 60.0, "liquidez": 40.0}
    # 0.5*80 + 0.3*60 + 0.2*40 = 40+18+8 = 66
    assert oh._conviccion("ruptura", sub) == 66


def test_conviccion_sin_datos_da_neutral():
    assert oh._conviccion("ruptura", {}) == 50


def test_objetivo_usa_maximo_52s_si_esta_meaningfully_arriba():
    assert oh._objetivo(spot=100.0, max52=110.0, cancelar=90.0) == 110.0


def test_objetivo_usa_measured_move_si_ya_esta_en_maximos():
    # max52 apenas por encima del spot (ej. ya rompiendo) -> measured move
    assert oh._objetivo(spot=100.0, max52=100.5, cancelar=90.0) == 110.0


def test_objetivo_sin_datos_da_none():
    assert oh._objetivo(spot=100.0, max52=None, cancelar=None) is None


def test_decision_liquidez_muy_baja_es_no_operar():
    decision, motivo = oh._decision(sub_liquidez=10.0, dias_a_resultados=None)
    assert decision == "No operar"
    assert "liquidez" in motivo.lower()


def test_decision_earnings_proximos_es_esperar():
    decision, motivo = oh._decision(sub_liquidez=80.0, dias_a_resultados=2)
    assert decision == "Esperar"
    assert "resultados" in motivo.lower()


def test_decision_normal_es_comprar_hoy():
    decision, motivo = oh._decision(sub_liquidez=80.0, dias_a_resultados=None)
    assert decision == "Comprar hoy"
    assert motivo is None


def test_decision_earnings_lejanos_no_afecta():
    decision, _ = oh._decision(sub_liquidez=80.0, dias_a_resultados=30)
    assert decision == "Comprar hoy"


# ------------------------- construcción + formato del mensaje -------------------------

def test_construir_oportunidad_ruptura_de_extremo_a_extremo(monkeypatch):
    monkeypatch.setattr(oh, "_estrategia_recomendada", lambda ticker, spot: (None, None, None))
    monkeypatch.setattr(oh, "_dias_a_resultados", lambda ticker: None)
    b = _barras_ruptura()
    p = Puntuacion(ticker="RUP", score_total=80.0,
                   sub={"momentum": 90.0, "tendencia": 100.0, "liquidez": 70.0}, nombre="Ruptura Corp")
    o = oh.construir_oportunidad(p, b, Fundamentales("RUP"), "ruptura")
    assert o.ticker == "RUP"
    assert o.patron == "ruptura"
    assert o.urgencia == "Alta"
    assert o.decision == "Comprar hoy"
    assert o.entrada == b.close[-1]
    assert o.stop is not None and o.stop < o.entrada


def test_mensaje_oportunidades_vacio_da_texto_explicito():
    assert oh.mensaje_oportunidades([]) == oh.SIN_OPORTUNIDADES
    assert "no encontré ninguna oportunidad" in oh.SIN_OPORTUNIDADES.lower()


def test_formatear_oportunidad_incluye_todos_los_campos_del_formato_pedido():
    o = oh.Oportunidad(
        ticker="JPM", nombre="JPMorgan Chase", patron="ruptura", conviccion=78,
        urgencia="Alta", decision="Comprar hoy", motivo_decision=None,
        que_ocurrio="JPM rompe su máximo de 52 semanas.", que_invalida="Si vuelve a caer, se invalida.",
        entrada=349.76, stop=306.15, objetivo=370.0, horizonte_dias=36,
        capital_acciones=34976.0, capital_estrategia=980.0, estrategia_nombre="Long Call",
    )
    texto = oh.formatear_oportunidad(o)
    assert "🚨 Oportunidad detectada" in texto
    assert "JPM" in texto and "JPMorgan Chase" in texto
    assert "Convicción del modelo: 78/100" in texto
    assert "Mi decisión: Comprar hoy" in texto
    assert "¿Por qué?" in texto
    assert "JPM rompe su máximo de 52 semanas." in texto
    assert "Entrada ideal: $350" in texto
    assert "Stop: $306" in texto
    assert "Objetivo: $370" in texto
    assert "Horizonte esperado: 36 días" in texto
    assert "Capital mínimo recomendado: $34,976" in texto
    assert "Estrategia recomendada: Long Call" in texto
    assert "Crear alertas en estos niveles:" in texto
    assert "Nivel de urgencia: Alta" in texto
    assert "Acción siguiente: Comprar hoy" in texto


def test_formatear_oportunidad_esperar_sugiere_alertas_como_accion():
    o = oh.Oportunidad(
        ticker="X", nombre=None, patron="pullback", conviccion=60, urgencia="Media",
        decision="Esperar", motivo_decision="Resultados en 2 días.",
        que_ocurrio="ocurrió algo", que_invalida="se invalida si...",
        entrada=100.0, stop=90.0, objetivo=110.0, horizonte_dias=None,
        capital_acciones=10000.0, capital_estrategia=None, estrategia_nombre=None,
    )
    texto = oh.formatear_oportunidad(o)
    assert "Resultados en 2 días." in texto
    assert "Acción siguiente: Crear alertas y esperar confirmación" in texto
    assert "Estrategia recomendada: Comprar acciones directamente (opciones no disponibles hoy)" in texto


def test_formatear_oportunidad_no_operar():
    o = oh.Oportunidad(
        ticker="X", nombre=None, patron="valor_impulso", conviccion=55, urgencia="Baja",
        decision="No operar", motivo_decision="Liquidez muy baja.",
        que_ocurrio="ocurrió algo", que_invalida="se invalida si...",
        entrada=100.0, stop=None, objetivo=None, horizonte_dias=None,
        capital_acciones=10000.0, capital_estrategia=None, estrategia_nombre=None,
    )
    texto = oh.formatear_oportunidad(o)
    assert "Acción siguiente: No abrir posición hoy" in texto


# ------------------------- buscar_oportunidades (integración sin red) -------------------------

def test_buscar_oportunidades_escanea_el_universo_completo(monkeypatch):
    monkeypatch.setattr(oh, "_estrategia_recomendada", lambda ticker, spot: (None, None, None))
    monkeypatch.setattr(oh, "_dias_a_resultados", lambda ticker: None)
    ranking = [
        Puntuacion(ticker="RUP", score_total=80.0, sub={"momentum": 90.0, "tendencia": 100.0}),
        Puntuacion(ticker="FLAT", score_total=50.0, sub={}),
    ]
    barras = {"RUP": _barras_ruptura(), "FLAT": _barras_planas()}
    fund = {"RUP": Fundamentales("RUP"), "FLAT": Fundamentales("FLAT")}
    oportunidades = oh.buscar_oportunidades(ranking, barras, fund)
    assert len(oportunidades) == 1
    assert oportunidades[0].ticker == "RUP"


def test_buscar_oportunidades_sin_barras_no_rompe(monkeypatch):
    monkeypatch.setattr(oh, "_estrategia_recomendada", lambda ticker, spot: (None, None, None))
    ranking = [Puntuacion(ticker="SINDATOS", score_total=50.0, sub={})]
    assert oh.buscar_oportunidades(ranking, {}, {}) == []


def test_buscar_oportunidades_un_ticker_fallido_no_tumba_los_demas(monkeypatch):
    def _falla(ticker, spot):
        if ticker == "RUP":
            raise RuntimeError("boom")
        return None, None, None
    monkeypatch.setattr(oh, "_estrategia_recomendada", _falla)
    monkeypatch.setattr(oh, "_dias_a_resultados", lambda ticker: None)
    ranking = [Puntuacion(ticker="RUP", score_total=80.0, sub={"momentum": 90.0, "tendencia": 100.0})]
    barras = {"RUP": _barras_ruptura()}
    fund = {"RUP": Fundamentales("RUP")}
    assert oh.buscar_oportunidades(ranking, barras, fund) == []
