"""Pruebas de /report TICKER: red (Yahoo/yfinance/Google News/Anthropic)
mockeada por completo -- verifica que el memo diga lo que de verdad sabe y
nunca invente lo que no sabe (ej. si el ticker no está en la shortlist de
hoy, o si no hay noticias), y que el Executive Summary/riesgos/
catalizadores sean 100% deterministas (reglas fijas, no juicio de LLM)."""

from __future__ import annotations

import json

import pytest

import report_command as rc
from news_analyst.models import Explicacion
from screener.data.provider import Barras, Fundamentales


@pytest.fixture(autouse=True)
def _sin_fecha_resultados_real(monkeypatch):
    """Por defecto ningún test pega a yfinance por la fecha de earnings --
    los tests que sí quieran probar ese camino la mockean explícitamente."""
    monkeypatch.setattr(rc, "_obtener_fecha_resultados", lambda ticker: None)


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


def test_ticker_sin_datos_de_precio_da_mensaje_claro(monkeypatch):
    monkeypatch.setattr(rc, "YahooProvider", lambda: _FakeProvider({}, {}))
    texto = rc.generar_reporte("ZZZZ")
    assert "no pude obtener datos de precio" in texto.lower()


def test_ticker_fuera_de_la_shortlist_no_inventa_una_posicion(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "no_existe.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"XOM": _barras("XOM")},
                                              {"XOM": Fundamentales("XOM", nombre="Exxon Mobil")}))
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])

    texto = rc.generar_reporte("xom")  # minúsculas: debe normalizar
    assert "No está en la shortlist de hoy" in texto
    assert "Posición #" not in texto
    assert "Exxon Mobil (XOM)" in texto
    assert "Score breakdown" not in texto  # no hay sub_scores que desglosar
    assert rc.DISCLAIMER in texto


def test_ticker_en_la_shortlist_muestra_posicion_razones_y_breakdown(monkeypatch, tmp_path):
    _escribir_shortlist(tmp_path, [
        {"ticker": "BNY", "score": 83.0, "sector": "Financial Services",
         "sub_scores": {"momentum": 90, "calidad": 85}, "nombre": "BNY Mellon", "industria": "Banks"},
        {"ticker": "AAPL", "score": 81.0, "sector": "Technology",
         "sub_scores": {"momentum": 89.4, "calidad": 68.6, "valor": 3.4}},
    ])
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "shortlist_hoy.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"AAPL": _barras("AAPL")}, {"AAPL": Fundamentales("AAPL")}))
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Apple anuncia algo nuevo"])
    monkeypatch.setattr(rc, "generar_explicacion",
                        lambda titular, entrada: Explicacion(texto="Explicación de prueba.",
                                                              tono="positivo", nivel_importancia=3))

    texto = rc.generar_reporte("AAPL")
    assert "Posición #2 de la shortlist de hoy" in texto
    assert "sobre 480 empresas analizadas" in texto  # universo_n viene del archivo real, no fijo
    assert "Explicación de prueba." in texto
    assert "Media relevancia" in texto  # nivel_importancia=3 -> bucket medio
    # Score breakdown: Momentum pesa 30% -> 89.4 * 0.30 = 26.8
    assert "Momentum (peso 30%): 89/100 -> 26.8/30 pts" in texto
    assert "TOTAL: 81/100" in texto


def test_ticker_en_shortlist_sin_titulares_no_rompe(monkeypatch, tmp_path):
    _escribir_shortlist(tmp_path, [{"ticker": "MSFT", "score": 80.0, "sector": "Technology", "sub_scores": {}}])
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "shortlist_hoy.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"MSFT": _barras("MSFT")}, {"MSFT": Fundamentales("MSFT")}))
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])

    texto = rc.generar_reporte("MSFT")
    assert "no se encontraron titulares recientes" in texto.lower()


def test_fundamentales_faltantes_dicen_no_disponible(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "SHORTLIST_PATH", tmp_path / "no_existe.json")
    monkeypatch.setattr(rc, "YahooProvider",
                        lambda: _FakeProvider({"JNJ": _barras("JNJ")}, {}))  # sin fundamentales
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: [])

    texto = rc.generar_reporte("JNJ")
    assert "P/E: No disponible" in texto
    assert "Recomendación: No disponible" in texto
    assert "% en manos institucionales: No disponible" in texto


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


# ------------------------- executive summary -------------------------

def test_resumen_ejecutivo_incluye_los_campos_pedidos():
    entrada = {"sub_scores": {"calidad": 90, "valor": 10}}
    riesgos = ["RSI en 89 -- zona de sobrecompra."]
    lineas = rc._resumen_ejecutivo(entrada, pe=40.5, tendencia_label="alcista", riesgos=riesgos)
    texto = "\n".join(lineas)
    assert "Calidad del negocio: ⭐⭐⭐⭐⭐" in texto
    assert "Tendencia: Alcista" in texto
    assert "Valoración: Exigente (P/E 40.5)" in texto
    assert "Riesgo principal: RSI en 89" in texto
    assert "Conclusión:" in texto


def test_resumen_ejecutivo_sin_shortlist_no_inventa_calidad():
    lineas = rc._resumen_ejecutivo(None, pe=20.0, tendencia_label="neutral", riesgos=["ninguno"])
    texto = "\n".join(lineas)
    assert "Calidad del negocio: No disponible" in texto


# ------------------------- veredicto -------------------------

def test_fortalezas_y_debilidades_derivan_de_las_mismas_senales():
    fortalezas = rc._fortalezas(tendencia_label="alcista", calidad_score=90, liquidez_score=80, crecimiento=0.15)
    assert "Tendencia alcista muy sólida." in fortalezas
    assert "Negocio de buena calidad." in fortalezas
    assert "Alta liquidez." in fortalezas
    assert "Crecimiento de ingresos saludable." in fortalezas

    debilidades = rc._debilidades(valoracion="Exigente", rsi=89, vol=0.15, tendencia_label="alcista")
    assert "Valuación elevada." in debilidades
    assert "RSI en zona de sobrecompra." in debilidades


def test_fortalezas_sin_senales_lo_dice_explicito():
    assert rc._fortalezas("neutral", None, None, None) == [
        "Sin fortalezas destacadas detectadas hoy con los datos disponibles."]
    assert rc._debilidades("Razonable", 50, 0.15, "neutral") == [
        "Sin debilidades destacadas detectadas hoy con los datos disponibles."]


def test_preguntas_pendientes_no_pregunta_lo_que_ya_sabe():
    con_fecha = rc._preguntas_pendientes(fecha_resultados="2026-08-15", valoracion="Razonable")
    sin_fecha = rc._preguntas_pendientes(fecha_resultados=None, valoracion="Razonable")
    assert not any("earnings" in p for p in con_fecha)
    assert any("earnings" in p for p in sin_fecha)


def test_preguntas_pendientes_pregunta_por_valuacion_si_es_exigente():
    preguntas = rc._preguntas_pendientes(fecha_resultados="2026-08-15", valoracion="Exigente")
    assert any("crecimiento esperado" in p for p in preguntas)


def test_confianza_reporte_refleja_cuantos_datos_llegaron():
    fund_completo = Fundamentales("AAPL", pe=40.5, roe=1.4, analista_recomendacion="buy", pct_institucional=0.66)
    completa = rc._confianza_reporte({"posicion": 1}, fund_completo, "2026-08-15", True)
    fund_vacio = Fundamentales("ZZZZ")
    vacia = rc._confianza_reporte(None, fund_vacio, None, False)
    assert completa == 100
    assert vacia == 0


def test_veredicto_incluye_los_cuatro_bloques():
    fund = Fundamentales("AAPL", pe=40.5, roe=1.4, crecimiento_ingresos=0.166,
                         analista_recomendacion="buy", pct_institucional=0.66)
    entrada = {"posicion": 2, "score": 82.0, "sub_scores": {"calidad": 98, "liquidez": 99}}
    lineas = rc._veredicto(entrada, fund, "alcista", rsi=89, vol=0.28, valoracion="Exigente",
                          fecha_resultados=None, hubo_noticias_explicadas=False)
    texto = "\n".join(lineas)
    assert "🎯 Veredicto" in texto
    assert "Fortalezas:" in texto and "✅" in texto
    assert "Debilidades:" in texto and "⚠️" in texto
    assert "Preguntas que aún debo responder antes de invertir:" in texto and "☐" in texto
    assert "Nivel de confianza del reporte:" in texto


# ------------------------- noticias por relevancia -------------------------

def test_noticias_se_ordenan_por_relevancia_no_por_fecha(monkeypatch, tmp_path):
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
    lineas, hubo_explicadas = rc._seccion_noticias("AAPL", "Apple Inc.", entrada)
    texto = "\n".join(lineas)
    idx_alta = texto.index("Alta relevancia")
    idx_media = texto.index("Media relevancia")
    idx_baja = texto.index("Baja relevancia")
    assert idx_alta < idx_media < idx_baja
    assert texto.index("Titular alto") < idx_media
    assert idx_media < texto.index("Titular medio") < idx_baja
    assert texto.index("Titular bajo") > idx_baja
    assert hubo_explicadas is True


def test_noticias_sin_explicar_van_en_su_propio_bloque(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "titulares_google_news", lambda query, maximo=5: ["Sin explicar"])
    monkeypatch.setattr(rc, "generar_explicacion", lambda titular, entrada: None)

    entrada = {"posicion": 1, "score": 80.0, "sector": None, "industria": None, "sub_scores": {}}
    lineas, hubo_explicadas = rc._seccion_noticias("MSFT", "Microsoft", entrada)
    texto = "\n".join(lineas)
    assert "no se pudieron clasificar por impacto" in texto
    assert "Sin explicar" in texto  # el titular literal también contiene esa palabra, ok
    assert hubo_explicadas is False
