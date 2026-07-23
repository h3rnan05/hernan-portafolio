"""Pruebas de /report TICKER: red (Yahoo/yfinance/Google News/Anthropic)
mockeada por completo.

Filosofía bajo prueba: el modo por defecto ("simple") responde en <1
minuto si vale la pena investigar, si se compraría hoy, qué gusta/no
gusta y a qué precio -- y OMITE cualquier dato que no esté disponible
(nunca "No disponible"). El modo "--full" es el memo exhaustivo de
antes, donde "No disponible" sigue siendo honesto porque esa vista
existe para mostrar todo lo que se intentó obtener. Ambos modos nunca
inventan lo que no saben (ej. si el ticker no está en la shortlist de
hoy, o si no hay noticias), y el resumen de noticias/veredicto son 100%
deterministas salvo el resumen agregado de noticias, que usa el LLM solo
para condensar (nunca para decidir)."""

from __future__ import annotations

import json

import pytest

import report_command as rc
from news_analyst.models import Explicacion, ResumenNoticias
from screener.data.provider import Barras, Fundamentales


@pytest.fixture(autouse=True)
def _sin_fecha_resultados_real(monkeypatch):
    """Por defecto ningún test pega a yfinance por la fecha de earnings --
    los tests que sí quieran probar ese camino la mockean explícitamente."""
    monkeypatch.setattr(rc, "_obtener_fecha_resultados", lambda ticker: None)


@pytest.fixture(autouse=True)
def _sin_noticias_por_defecto(monkeypatch):
    """Por defecto ningún test pega a Google News -- los que sí quieran
    probar ese camino lo mockean explícitamente."""
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])


def _barras(ticker="AAPL", n=260, precio_final=200.0):
    precios = [precio_final - (n - i) * 0.1 for i in range(n)]
    return Barras(ticker, [str(i) for i in range(n)], precios,
                  [p * 1.01 for p in precios], [p * 0.99 for p in precios],
                  [1e7] * n)


class _FakeProvider:
    def __init__(self, barras_por_ticker, fund_por_ticker):
        self._b, self._f = barras_por_ticker, fund_por_ticker

    def barras(self, tickers, dias=400):
        return {t: self._b[t] for t in tickers if t in self._b}

    def fundamentales(self, tickers):
        return {t: self._f.get(t, Fundamentales(t)) for t in tickers}


def _escribir_shortlist(tmp_path, filas):
    archivo = tmp_path / "shortlist_hoy.json"
    archivo.write_text(json.dumps({
        "fecha": "2026-07-16T12:00:00+00:00",
        "universo_escaneado": 480,
        "shortlist": filas,
    }))
    return archivo


def _mock_red_basica(monkeypatch, ticker="AAPL", fund=None, tmp_path=None):
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({ticker: _barras(ticker)},
                                              {ticker: fund or Fundamentales(ticker, nombre="Apple Inc.")}))
    if tmp_path is not None:
        monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "no_existe.json")


def test_ticker_sin_datos_de_precio_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(rc, "YahooProvider", lambda: _FakeProvider({}, {}))
    texto = rc.generar_reporte("ZZZZ")
    assert "no pude obtener datos de precio" in texto.lower()


def test_reporte_simple_normaliza_ticker_a_mayusculas(monkeypatch, tmp_path):
    _mock_red_basica(monkeypatch, "XOM", fund=Fundamentales("XOM", nombre="Exxon Mobil"), tmp_path=tmp_path)
    texto = rc.generar_reporte("xom")
    assert "📊 XOM — Exxon Mobil" in texto
    assert rc.DISCLAIMER in texto


def test_reporte_simple_incluye_las_secciones_clave(monkeypatch, tmp_path):
    _mock_red_basica(monkeypatch, tmp_path=tmp_path)
    texto = rc.generar_reporte("AAPL")
    assert "🎯 Mi plan para hoy" in texto
    assert "📌 En 20 segundos" in texto
    assert "🎯 ¿Por qué me gusta?" in texto
    assert "⚠️ ¿Qué no me gusta?" in texto
    assert "📊 Lo importante" in texto
    assert "📰 Lo que pasó esta semana" in texto
    assert "🎯 Qué haría yo" in texto
    assert "/report AAPL --full" in texto
    # "Mi plan para hoy" es lo primero que se lee, antes de "En 20 segundos".
    assert texto.index("🎯 Mi plan para hoy") < texto.index("📌 En 20 segundos")


def test_reporte_simple_no_muestra_nunca_no_disponible(monkeypatch, tmp_path):
    """El objetivo del modo simple es decidir rápido -- un dato faltante
    se omite, nunca se muestra como "No disponible" (a diferencia del
    modo --full, que sí lo hace a propósito)."""
    _mock_red_basica(monkeypatch, fund=Fundamentales("AAPL", nombre="Apple Inc."), tmp_path=tmp_path)
    texto = rc.generar_reporte("AAPL")
    assert "No disponible" not in texto


def test_reporte_simple_no_muestra_nivel_de_confianza_ni_preguntas(monkeypatch, tmp_path):
    """Pedido explícito: un asesor no dice "tengo 57% de confianza", y las
    preguntas pendientes las debe responder el bot, no dejarlas de tarea."""
    _mock_red_basica(monkeypatch, tmp_path=tmp_path)
    texto = rc.generar_reporte("AAPL")
    assert "confianza del reporte" not in texto.lower()
    assert "Preguntas que" not in texto


def test_reporte_simple_sin_shortlist_igual_da_veredicto(monkeypatch, tmp_path):
    _mock_red_basica(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(rc, "_timing", lambda *a, **k: ("no_paso", "🟡 No compraría hoy porque no pasó el screener."))
    texto = rc.generar_reporte("AAPL")
    assert "No pasó todos los filtros cuantitativos del modelo hoy." in texto


def test_reporte_full_conserva_todos_los_fundamentales(monkeypatch, tmp_path):
    _escribir_shortlist(tmp_path, [
        {"ticker": "AAPL", "score": 81.0, "sector": "Technology",
         "sub_scores": {"momentum": 89.4, "calidad": 68.6, "valor": 3.4}},
    ])
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "shortlist_hoy.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras("AAPL")}, {"AAPL": Fundamentales("AAPL")}))

    texto = rc.generar_reporte("AAPL", modo="full")
    assert "Si quieres profundizar" in texto
    assert "Posición #1 de la shortlist de hoy" in texto
    assert "sobre 480 empresas analizadas" in texto
    # Score breakdown: Momentum pesa 30% -> 89.4 * 0.30 = 26.8
    assert "Momentum (peso 30%): 89/100 -> 26.8/30 pts" in texto
    assert "TOTAL: 81/100" in texto
    assert "P/E: No disponible" in texto  # en modo full "No disponible" sigue siendo honesto


def test_reporte_full_omite_calidad_del_negocio_si_no_esta_disponible(monkeypatch, tmp_path):
    """Pedido explícito: si la calidad no está disponible, se omite la
    línea en vez de mostrar "Calidad del negocio: No disponible"."""
    _mock_red_basica(monkeypatch, tmp_path=tmp_path)
    texto = rc.generar_reporte("AAPL", modo="full")
    assert "Calidad del negocio: No disponible" not in texto


def test_reporte_full_muestra_calidad_cuando_esta_disponible(monkeypatch, tmp_path):
    _escribir_shortlist(tmp_path, [
        {"ticker": "AAPL", "score": 81.0, "sector": "Technology", "sub_scores": {"calidad": 90}},
    ])
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "shortlist_hoy.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras("AAPL")}, {"AAPL": Fundamentales("AAPL")}))
    texto = rc.generar_reporte("AAPL", modo="full")
    assert "Calidad del negocio: ⭐⭐⭐⭐⭐" in texto


def test_shortlist_entry_tolera_archivo_inexistente(tmp_path):
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = tmp_path / "no_existe.json"
        resultado = rc._shortlist_entry("AAPL")
    finally:
        rc.SHORTLIST_PATH = original
    assert resultado is None


def test_shortlist_entry_tolera_json_corrupto(tmp_path):
    archivo = tmp_path / "shortlist_hoy.json"
    archivo.write_text("esto no es json valido {{{")
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = archivo
        assert rc._shortlist_entry("AAPL") is None
    finally:
        rc.SHORTLIST_PATH = original


# ------------------------- interpretaciones deterministas -------------------------

def test_interpretar_rsi_extremos_y_neutral():
    assert "sobrecompra" in rc._interpretar_rsi(89)
    assert "sobreventa" in rc._interpretar_rsi(15)
    assert "neutral" in rc._interpretar_rsi(50)
    assert rc._interpretar_rsi(None) == "No disponible."


def test_interpretar_pe_por_rango():
    assert "baja" in rc._interpretar_pe(10)
    assert "razonable" in rc._interpretar_pe(20)
    assert "exigente" in rc._interpretar_pe(45)


def test_interpretar_roe_y_pb():
    assert "Muy alta" in rc._interpretar_roe(0.30)
    assert "sólida" in rc._interpretar_roe(0.12)
    assert "por debajo" in rc._interpretar_roe(0.05)
    assert "por debajo de su valor en libros" in rc._interpretar_pb(0.8)
    assert "razonable" in rc._interpretar_pb(3.0)
    assert "muy por encima" in rc._interpretar_pb(20.0)


def test_interpretar_precio_objetivo_por_debajo_del_actual():
    # Caso real verificado: AAPL objetivo $318.25 vs precio $333.74
    texto = rc._interpretar_precio_objetivo(precio_objetivo=318.25, precio_actual=333.74)
    assert "por debajo" in texto
    assert "4.6%" in texto


def test_interpretar_precio_objetivo_por_encima_del_actual():
    texto = rc._interpretar_precio_objetivo(precio_objetivo=150.0, precio_actual=100.0)
    assert "por encima" in texto
    assert "50.0%" in texto


def test_interpretar_precio_objetivo_sin_datos():
    assert rc._interpretar_precio_objetivo(None, 100.0) == "No disponible."
    assert rc._interpretar_precio_objetivo(150.0, None) == "No disponible."


def test_clasificar_valoracion_prefiere_subscore_sobre_pe():
    # valor_subscore alto (barata) pero P/E también luciría "exigente":
    # el sub-score cross-sectional manda porque es más riguroso.
    assert rc._clasificar_valoracion(pe=45.0, valor_subscore=85.0) == "Atractiva"
    assert rc._clasificar_valoracion(pe=45.0, valor_subscore=None) == "Exigente"
    assert rc._clasificar_valoracion(pe=None, valor_subscore=None) == "No determinable"


def test_estrellas_100():
    assert rc._estrellas_100(100) == "⭐⭐⭐⭐⭐"
    assert rc._estrellas_100(0) == "☆☆☆☆☆"
    assert rc._estrellas_100(None) == "No disponible"


# ------------------------- riesgos / catalizadores -------------------------

def test_riesgos_detecta_sobrecompra_y_valuacion_exigente():
    riesgos = rc._riesgos(rsi=89, pe=40.5, vol=0.28, tendencia_label="alcista")
    assert any("sobrecompra" in r for r in riesgos)
    assert any("Valuación exigente" in r for r in riesgos)
    assert len(riesgos) <= 3


def test_riesgos_sin_banderas_lo_dice_explicito():
    riesgos = rc._riesgos(rsi=50, pe=20, vol=0.15, tendencia_label="alcista")
    assert riesgos == ["Sin banderas de riesgo técnico relevantes detectadas hoy con los datos disponibles."]


def test_catalizadores_sin_fecha_no_inventa():
    cats = rc._catalizadores(None)
    assert "No identifiqué catalizadores confirmados" in cats[0]
    assert "producto" not in cats[0].lower() or "sin una fuente" in cats[0].lower()


def test_catalizadores_con_fecha_real():
    cats = rc._catalizadores("2026-08-15")
    assert cats == ["Próximos resultados trimestrales: 2026-08-15."]


# ------------------------- veredicto conciso -------------------------

def test_veredicto_investigar_pasa_screener():
    emoji, texto = rc._veredicto_investigar(calidad_score=None, entrada={"posicion": 1})
    assert emoji == "🟢" and "Sí" in texto


def test_veredicto_investigar_no_paso_pero_calidad_alta():
    emoji, texto = rc._veredicto_investigar(calidad_score=75, entrada=None)
    assert emoji == "🟢" and "Sí" in texto


def test_veredicto_investigar_calidad_baja_y_no_paso():
    emoji, texto = rc._veredicto_investigar(calidad_score=10, entrada=None)
    assert emoji == "🔴" and "No" in texto


def test_veredicto_investigar_sin_datos_es_con_cautela():
    emoji, texto = rc._veredicto_investigar(calidad_score=None, entrada=None)
    assert emoji == "🟡" and "cautela" in texto


def test_timing_bajista():
    codigo, texto = rc._timing(entrada={"posicion": 1}, tendencia_label="bajista", rsi=50)
    assert codigo == "bajista"
    assert "bajista" in texto


def test_timing_sobrecomprado():
    codigo, texto = rc._timing(entrada={"posicion": 1}, tendencia_label="alcista", rsi=80)
    assert codigo == "sobrecomprado"
    assert "sobrecompra" in texto


def test_timing_no_paso_el_screener():
    codigo, texto = rc._timing(entrada=None, tendencia_label="alcista", rsi=50)
    assert codigo == "no_paso"
    assert "no pasó el screener" in texto


def test_timing_compraria():
    codigo, texto = rc._timing(entrada={"posicion": 1}, tendencia_label="alcista", rsi=50)
    assert codigo == "compraria"
    assert "Sí, la compraría hoy." in texto


def test_en_20_segundos_agrega_lista_de_seguimiento_si_no_compraria():
    lineas = rc._en_20_segundos("🟢", "Sí la investigaría.", "no_paso",
                                "🟡 No compraría hoy porque no pasó el screener.",
                                347.79, {"entrada": None, "ideal": None}, None)
    texto = "\n".join(lineas)
    assert "La tendría en mi lista de seguimiento." in texto


def test_en_20_segundos_sin_lista_de_seguimiento_si_compraria():
    lineas = rc._en_20_segundos("🟢", "Sí la investigaría.", "compraria",
                                "🟢 Sí, la compraría hoy.", 347.79, {"entrada": None, "ideal": None}, None)
    texto = "\n".join(lineas)
    assert "lista de seguimiento" not in texto


def test_en_20_segundos_muestra_rango_de_entrada():
    lineas = rc._en_20_segundos("🟢", "Sí la investigaría.", "no_paso", "texto",
                                347.79, {"entrada": 335.0, "ideal": 330.0}, 370.0)
    texto = "\n".join(lineas)
    assert "Me interesaría cerca de:" in texto
    assert "$330.00-$335.00" in texto
    assert "Objetivo de analistas:" in texto
    assert "$370.00" in texto


def test_en_20_segundos_omite_precio_objetivo_si_no_hay():
    lineas = rc._en_20_segundos("🟢", "Sí la investigaría.", "compraria", "texto",
                                347.79, {"entrada": None, "ideal": None}, None)
    texto = "\n".join(lineas)
    assert "Objetivo de analistas" not in texto


def test_lo_que_me_gusta_incluye_numeros_reales():
    items = rc._lo_que_me_gusta(tendencia_label="alcista", roe=0.178, crecimiento=0.30,
                                valoracion="Atractiva", pe=14.9, liquidez_score=80)
    assert "Tendencia alcista." in items
    assert any("ROE 17.8%" in i for i in items)
    assert any("30%" in i for i in items)
    assert any("14.9" in i for i in items)
    assert "Alta liquidez." in items


def test_lo_que_me_gusta_vacio_si_nada_aplica():
    assert rc._lo_que_me_gusta("bajista", None, None, "Exigente", None, None) == []


def test_lo_que_no_me_gusta_no_paso_el_screener():
    items = rc._lo_que_no_me_gusta("no_paso", rsi=50, valoracion="Razonable", pe=20, vol=0.15)
    assert "No pasó todos los filtros cuantitativos del modelo hoy." in items


def test_lo_que_no_me_gusta_sobrecomprado_y_valuacion_exigente():
    items = rc._lo_que_no_me_gusta("sobrecomprado", rsi=89, valoracion="Exigente", pe=45.0, vol=0.15)
    assert any("sobrecompra" in i for i in items)
    assert any("45.0" in i for i in items)


def test_lo_que_no_me_gusta_vacio_si_nada_aplica():
    assert rc._lo_que_no_me_gusta("compraria", rsi=50, valoracion="Razonable", pe=20, vol=0.15) == []


def test_lo_importante_omite_datos_no_disponibles():
    lineas = rc._lo_importante(precio=347.79, pe=None, roe=None, crecimiento=None, rsi=None,
                               analista_recomendacion=None, precio_objetivo=None)
    texto = "\n".join(lineas)
    assert "Precio: $347.79" in texto
    assert "P/E" not in texto
    assert "ROE" not in texto
    assert "RSI" not in texto
    assert "Analistas" not in texto
    assert "Potencial" not in texto


def test_lo_importante_lidera_con_conclusion_en_lenguaje_llano():
    """Pedido explícito: la conclusión en lenguaje llano va ARRIBA, el
    dato técnico crudo va ABAJO en una sola línea compacta -- la mayoría
    de la gente no sabe interpretar un ROE o un P/E sueltos."""
    lineas = rc._lo_importante(precio=347.79, pe=14.9, roe=0.178, crecimiento=0.30, rsi=63,
                               analista_recomendacion="buy", precio_objetivo=370.0)
    texto = "\n".join(lineas)
    assert "Empresa rentable." in texto
    assert "Acción barata." in texto
    assert "No está sobrecomprada." in texto
    assert "Creciendo rápido (+30%)." in texto
    # el dato crudo va en una sola línea compacta, después de las conclusiones
    idx_conclusion = texto.index("Empresa rentable.")
    idx_datos = texto.index("P/E 14.9")
    assert idx_conclusion < idx_datos
    assert "ROE 17.8%" in texto
    assert "RSI 63" in texto
    assert "Analistas Buy" in texto
    assert "Potencial" in texto


def test_lo_importante_roe_bajo_y_valuacion_cara():
    lineas = rc._lo_importante(precio=100.0, pe=45.0, roe=0.03, crecimiento=-0.05, rsi=50,
                               analista_recomendacion=None, precio_objetivo=None)
    texto = "\n".join(lineas)
    assert "Rentabilidad débil." in texto
    assert "Acción cara." in texto
    assert "Ingresos en contracción." in texto


def test_por_que_una_linea_compraria_usa_lo_que_gusta():
    linea = rc._por_que_una_linea("compraria", gusta=["Tendencia alcista.", "Negocio rentable (ROE 17.8%)."],
                                  no_gusta=["No pasó todos los filtros cuantitativos del modelo hoy."])
    assert linea == "Porque Tendencia alcista y Negocio rentable (ROE 17.8%)."


def test_por_que_una_linea_no_compraria_usa_lo_que_no_gusta_y_no_rompe_siglas():
    """Regresión: minusculizar a ciegas el primer carácter rompía siglas
    como "RSI" ("rSI en sobrecompra...")."""
    linea = rc._por_que_una_linea("sobrecomprado", gusta=["Tendencia alcista."],
                                  no_gusta=["RSI en sobrecompra (89) -- no hay un punto de entrada atractivo todavía."])
    assert linea == "Porque RSI en sobrecompra (89) -- no hay un punto de entrada atractivo todavía."


def test_por_que_una_linea_sin_hechos_da_none():
    assert rc._por_que_una_linea("compraria", gusta=[], no_gusta=[]) is None
    assert rc._por_que_una_linea("no_paso", gusta=["algo"], no_gusta=[]) is None


def test_mi_plan_para_hoy_compraria():
    lineas = rc._mi_plan_para_hoy("compraria", "Porque está barata.", {"entrada": None}, maximo_52s=350.0)
    texto = "\n".join(lineas)
    assert "🎯 Mi plan para hoy" in texto
    assert "Sí, la compraría hoy." in texto
    assert "Porque está barata." in texto
    assert "✅ Ya puedes crear esta alerta: $350.00" in texto
    assert "Si llega ahí, la vuelvo a analizar." in texto


def test_mi_plan_para_hoy_no_compraria_usa_nivel_de_entrada():
    lineas = rc._mi_plan_para_hoy("no_paso", None, {"entrada": 330.0}, maximo_52s=350.0)
    texto = "\n".join(lineas)
    assert "No haría nada. Esperaría." in texto
    assert "$330.00" in texto
    assert "$350.00" not in texto


def test_mi_plan_para_hoy_sin_niveles_omite_alerta():
    lineas = rc._mi_plan_para_hoy("no_paso", None, {"entrada": None}, maximo_52s=None)
    assert "Ya puedes crear esta alerta" not in "\n".join(lineas)


def test_que_haria_yo_compraria():
    lineas = rc._que_haria_yo("compraria", None, {"entrada": None, "ideal": None}, None)
    assert lineas == ["🎯 Qué haría yo", "", "Sí, la compraría hoy."]


def test_que_haria_yo_compraria_incluye_el_porque():
    lineas = rc._que_haria_yo("compraria", "Porque está barata.", {"entrada": None, "ideal": None}, None)
    assert "Porque está barata." in lineas


def test_que_haria_yo_no_compraria_usa_niveles_reales():
    niveles = {"entrada": 330.0, "ideal": 325.0}
    lineas = rc._que_haria_yo("no_paso", None, niveles, maximo_52s=350.0)
    texto = "\n".join(lineas)
    assert "No compraría hoy." in texto
    assert "$325.00" in texto
    assert "$330.00" in texto
    assert "$350.00" in texto


def test_alertas_reporte_muestra_condiciones_o():
    niveles = {"entrada": 335.0, "ideal": 330.0, "cancelar": 310.0}
    lineas = rc._alertas_reporte(niveles, maximo_52s=350.0, fecha_resultados="2026-08-15")
    texto = "\n".join(lineas)
    assert "🔔 Comprar si ocurre UNA de estas cosas" in texto
    assert "Corrige hacia $335.00" in texto
    assert "Rebota con fuerza en el soporte ($330.00)" in texto
    assert "Rompe el máximo de 52 semanas ($350.00)" in texto
    assert "Después de los resultados trimestrales (2026-08-15)" in texto
    assert "❌ Cancelar la idea si cae debajo de $310.00" in texto


def test_alertas_reporte_vacia_si_no_hay_condiciones():
    assert rc._alertas_reporte({"entrada": None, "ideal": None, "cancelar": None}, None, None) == []


# ------------------------- resumen de noticias -------------------------

def test_seccion_noticias_resumen_sin_titulares(monkeypatch):
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])
    lineas = rc._seccion_noticias_resumen("AAPL", "Apple Inc.", None)
    assert "No encontré noticias recientes." in "\n".join(lineas)


def test_seccion_noticias_resumen_usa_el_resumen_del_llm(monkeypatch):
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Titular 1", "Titular 2"])
    monkeypatch.setattr(rc, "resumir_noticias", lambda titulares, entrada: ResumenNoticias(
        resumen="La mayoría de las noticias fueron positivas.",
        puntos=["AAPL sorprendió con buenos resultados.", "No hubo noticias negativas importantes."]))
    lineas = rc._seccion_noticias_resumen("AAPL", "Apple Inc.", None)
    texto = "\n".join(lineas)
    assert "La mayoría de las noticias fueron positivas." in texto
    assert "AAPL sorprendió con buenos resultados." in texto


def test_seccion_noticias_resumen_degrada_a_titulares_si_el_llm_falla(monkeypatch):
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Titular crudo 1"])
    monkeypatch.setattr(rc, "resumir_noticias", lambda titulares, entrada: None)
    lineas = rc._seccion_noticias_resumen("AAPL", "Apple Inc.", None)
    texto = "\n".join(lineas)
    assert "No pude generar un resumen" in texto
    assert "Titular crudo 1" in texto


def test_seccion_noticias_resumen_pasa_contexto_de_shortlist(monkeypatch):
    capturado = {}

    def _fake_resumir(titulares, entrada):
        capturado["entrada"] = entrada
        return ResumenNoticias(resumen="ok", puntos=[])

    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Titular 1"])
    monkeypatch.setattr(rc, "resumir_noticias", _fake_resumir)
    entrada = {"posicion": 2, "score": 81.0, "sector": "Technology", "sub_scores": {}}
    rc._seccion_noticias_resumen("AAPL", "Apple Inc.", entrada)
    assert capturado["entrada"] is not None
    assert capturado["entrada"].ticker == "AAPL"
    assert capturado["entrada"].posicion == 2


def test_reporte_simple_incluye_resumen_de_noticias_en_vivo(monkeypatch, tmp_path):
    _mock_red_basica(monkeypatch, tmp_path=tmp_path)
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Apple anuncia algo nuevo"])
    monkeypatch.setattr(rc, "resumir_noticias", lambda titulares, entrada: ResumenNoticias(
        resumen="Tono mixto.", puntos=["Apple anunció algo nuevo."]))
    texto = rc.generar_reporte("AAPL")
    assert "Tono mixto." in texto
    assert "Apple anunció algo nuevo." in texto


# ------------------------- noticias por relevancia (modo --full) -------------------------

def test_noticias_full_se_ordenan_por_relevancia_no_por_fecha(monkeypatch, tmp_path):
    _escribir_shortlist(tmp_path, [{"ticker": "AAPL", "score": 81.0, "sector": "Technology", "sub_scores": {}}])
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "shortlist_hoy.json")
    titulares = ["Titular alto", "Titular medio", "Titular bajo"]
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: titulares)

    niveles = {"Titular alto": 5, "Titular medio": 3, "Titular bajo": 1}

    def _fake_explicacion(titular, entrada):
        return Explicacion(texto=f"Explicación de {titular}", tono="neutral",
                           nivel_importancia=niveles[titular])

    monkeypatch.setattr(rc, "generar_explicacion", _fake_explicacion)

    entrada = {"posicion": 1, "score": 81.0, "sector": "Technology",
              "industria": None, "sub_scores": {}}
    lineas = rc._seccion_noticias("AAPL", "Apple Inc.", entrada)
    texto = "\n".join(lineas)
    idx_alta = texto.index("Alta relevancia")
    idx_media = texto.index("Media relevancia")
    idx_baja = texto.index("Baja relevancia")
    assert idx_alta < idx_media < idx_baja
    assert texto.index("Titular alto") < idx_media
    assert idx_media < texto.index("Titular medio") < idx_baja
    assert texto.index("Titular bajo") > idx_baja


def test_noticias_full_sin_explicar_van_en_su_propio_bloque(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Sin explicar"])
    monkeypatch.setattr(rc, "generar_explicacion", lambda titular, entrada: None)

    entrada = {"posicion": 1, "score": 80.0, "sector": None, "industria": None, "sub_scores": {}}
    lineas = rc._seccion_noticias("MSFT", "Microsoft", entrada)
    texto = "\n".join(lineas)
    assert "no se pudieron clasificar por impacto" in texto
    assert "Sin explicar" in texto  # el titular literal también contiene esa palabra, ok


def test_noticias_full_ticker_fuera_de_shortlist_lista_titulares_crudos(monkeypatch):
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Titular crudo"])
    lineas = rc._seccion_noticias("XOM", "Exxon Mobil", None)
    texto = "\n".join(lineas)
    assert "Titular crudo" in texto


# ------------------------- /list -------------------------

def test_lista_completa_sin_shortlist_da_mensaje_claro(tmp_path):
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = tmp_path / "no_existe.json"
        texto = rc.generar_lista_completa()
    finally:
        rc.SHORTLIST_PATH = original
    assert "no ha corrido" in texto.lower()


def test_lista_completa_con_json_corrupto_da_mensaje_claro(tmp_path):
    archivo = tmp_path / "shortlist_hoy.json"
    archivo.write_text("esto no es json valido {{{")
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = archivo
        texto = rc.generar_lista_completa()
    finally:
        rc.SHORTLIST_PATH = original
    assert "no pude leer" in texto.lower()


def test_lista_completa_vacia_da_mensaje_claro(tmp_path):
    archivo = _escribir_shortlist(tmp_path, [])
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = archivo
        texto = rc.generar_lista_completa()
    finally:
        rc.SHORTLIST_PATH = original
    assert "vacía" in texto.lower()


def test_lista_completa_renderiza_todas_las_filas_sin_truncar(tmp_path):
    filas = [
        {"ticker": "BNY", "score": 83.0, "sector": "Financial Services", "sub_scores": {}},
        {"ticker": "AAPL", "score": 81.0, "sector": "Technology", "sub_scores": {}},
        {"ticker": "XOM", "score": 70.0, "sector": "Energy", "sub_scores": {}},
    ]
    archivo = _escribir_shortlist(tmp_path, filas)
    original = rc.SHORTLIST_PATH
    try:
        rc.SHORTLIST_PATH = archivo
        texto = rc.generar_lista_completa()
    finally:
        rc.SHORTLIST_PATH = original
    assert "🏦 BNY" in texto
    assert "💻 AAPL" in texto
    assert "🛢️ XOM" in texto
    assert "Shortlist completa de hoy" in texto
    assert "/report TICKER" in texto
