"""Pruebas de telemetría y del reporte semanal -- sin red, con archivos
fabricados en tmp_path."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from momentum_hunter import reporte_semanal, telemetria


def _metricas(**kw) -> telemetria.Metricas:
    m = telemetria.Metricas(modo=kw.pop("modo", "escaneo"))
    for k, v in kw.items():
        setattr(m, k, v)
    return m


# ------------------------- contadores -------------------------

def test_sumar_separa_por_banda():
    m = telemetria.Metricas()
    m.sumar(m.operables, "small")
    m.sumar(m.operables, "small")
    m.sumar(m.operables, "large")
    assert dict(m.operables) == {"small": 2, "large": 1}


def test_banda_invalida_no_se_pierde_ni_se_asigna_mal():
    # Un dato sin banda no debe contarse como small ni como large --
    # inventarle una banda distorsionaría justo la comparación que el
    # reporte usa para decidir si la cobertura de noticias es el problema.
    m = telemetria.Metricas()
    m.sumar(m.operables, None)
    m.sumar(m.operables, "otra_cosa")
    assert dict(m.operables) == {"desconocida": 2}


def test_como_dict_incluye_rechazos_y_titulares_en_el_embudo():
    m = telemetria.Metricas()
    m.titulares_total = 7
    m.rechazos_universo["vol_bajo_small"] = 3
    m.rechazos_universo["market_cap"] = 1
    embudo = m.como_dict()["embudo"]
    assert embudo["titulares_total"] == 7
    assert embudo["rechazos_universo"] == {"vol_bajo_small": 3, "market_cap": 1}


def test_registrar_error_guarda_tipo_y_origen_no_el_mensaje():
    # El mensaje puede traer una URL con credenciales -- solo el tipo.
    m = telemetria.Metricas()
    m.registrar_error("noticias", ValueError("token=SECRETO123 falló"))
    m.registrar_error("noticias", ValueError("otra"))
    m.registrar_error("datos", TimeoutError("x"))
    assert dict(m.errores) == {"noticias:ValueError": 2, "datos:TimeoutError": 1}
    assert "SECRETO123" not in json.dumps(m.como_dict())


# ------------------------- persistencia -------------------------

def test_registrar_corrida_crea_jsonl_partido_por_fuente(tmp_path):
    ahora = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    path = telemetria.registrar_corrida(
        _metricas(universo_escaneado=1000), tmp_path, ahora, fuente="gha")
    assert path is not None
    assert path == tmp_path / "2026-08-24" / "gha" / "events.jsonl"
    lineas = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(lineas) == 1
    assert lineas[0]["universo_escaneado"] == 1000
    assert lineas[0]["fuente"] == "gha"
    assert not (tmp_path / "2026-08-24.json").exists()


def test_varias_corridas_se_acumulan_en_el_mismo_dia(tmp_path):
    ahora = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    telemetria.registrar_corrida(_metricas(), tmp_path, ahora, fuente="gha")
    telemetria.registrar_corrida(_metricas(), tmp_path, ahora, fuente="gha")
    lineas = (tmp_path / "2026-08-24" / "gha" / "events.jsonl").read_text().splitlines()
    assert len([l for l in lineas if l.strip()]) == 2


def test_linea_jsonl_rota_no_tumba_ni_borra_las_demas(tmp_path):
    dest = tmp_path / "2026-08-24" / "gha" / "events.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"modo": "escaneo"}\n{roto\n')
    ahora = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
    path = telemetria.registrar_corrida(_metricas(), tmp_path, ahora, fuente="gha")
    assert path is not None
    assert "{roto" in path.read_text()
    corridas = telemetria.cargar_dias("2026-08-24", "2026-08-24", tmp_path)
    assert len(corridas) == 2


def test_un_fallo_al_guardar_nunca_propaga(tmp_path, monkeypatch):
    # La telemetría existe para vigilar fallos: sería absurdo que causara
    # uno. Debe tragarse su propio error y devolver None.
    monkeypatch.setattr(
        telemetria.Path, "mkdir",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("disco lleno")))
    assert telemetria.registrar_corrida(_metricas(), tmp_path / "x") is None


def test_cargar_dias_respeta_el_rango(tmp_path):
    for dia in ("2026-08-21", "2026-08-24", "2026-08-28"):
        (tmp_path / f"{dia}.json").write_text(json.dumps({"corridas": [{"modo": "escaneo"}]}))
    corridas = telemetria.cargar_dias("2026-08-24", "2026-08-28", tmp_path)
    assert len(corridas) == 2
    assert {c["dia"] for c in corridas} == {"2026-08-24", "2026-08-28"}


def test_cargar_dias_omite_lo_ilegible(tmp_path):
    (tmp_path / "2026-08-24.json").write_text(json.dumps({"corridas": [{"modo": "escaneo"}]}))
    (tmp_path / "2026-08-25.json").write_text("{roto")
    assert len(telemetria.cargar_dias("2026-08-01", "2026-12-31", tmp_path)) == 1


def test_cargar_dias_mezcla_legacy_y_jsonl_partido(tmp_path):
    (tmp_path / "2026-08-24.json").write_text(json.dumps({"corridas": [{"modo": "escaneo"}]}))
    dest = tmp_path / "2026-08-25" / "vps" / "events.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps({"modo": "watchlist", "fuente": "vps"}) + "\n")
    corridas = telemetria.cargar_dias("2026-08-24", "2026-08-25", tmp_path)
    assert [c["modo"] for c in corridas] == ["escaneo", "watchlist"]
    assert corridas[1]["fuente"] == "vps"


# ------------------------- reporte semanal -------------------------

def test_rango_semana_ancla_al_lunes():
    # Un miércoles y un viernes de la misma semana deben cubrir el mismo
    # periodo -- si no, dos ejecuciones darían números distintos.
    desde_mi, _ = reporte_semanal.rango_semana(datetime(2026, 8, 26, tzinfo=UTC))
    desde_vi, _ = reporte_semanal.rango_semana(datetime(2026, 8, 28, tzinfo=UTC))
    assert desde_mi == desde_vi == "2026-08-24"


def _escribir(tmp_path, dia, corrida):
    (tmp_path / f"{dia}.json").write_text(json.dumps({"corridas": [corrida]}))


def test_reporte_sin_datos_no_revienta(tmp_path):
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "REPORTE SEMANAL" in texto
    assert "Cuenta de práctica" in texto


def test_reporte_detecta_baja_cobertura_en_small_caps(tmp_path):
    # La pregunta abierta del 2026-08-24, contestada con datos.
    _escribir(tmp_path, "2026-08-24", {
        "embudo": {"operables": {"small": 1000, "large": 100},
                   "con_alguna_noticia": {"small": 20, "large": 80},
                   "con_catalizador": {"small": 2, "large": 20},
                   "evaluadas": {"small": 2, "large": 20},
                   "accionables": {}},
        "condiciones": {}, "errores": {}, "score_maximo": 50})
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "small-caps: 2%" in texto and "large-caps: 80%" in texto
    assert "FUENTE DE DATOS" in texto


def test_reporte_dice_que_la_cobertura_no_es_el_problema_si_es_pareja(tmp_path):
    _escribir(tmp_path, "2026-08-24", {
        "embudo": {"operables": {"small": 100, "large": 100},
                   "con_alguna_noticia": {"small": 60, "large": 70},
                   "con_catalizador": {"small": 5, "large": 6},
                   "evaluadas": {"small": 5, "large": 6}, "accionables": {}},
        "condiciones": {}, "errores": {}, "score_maximo": 50})
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "NO parece" in texto


def test_reporte_señala_la_condicion_que_mas_descarta(tmp_path):
    _escribir(tmp_path, "2026-08-24", {
        "embudo": {"operables": {"large": 50}, "evaluadas": {"large": 100},
                   "con_alguna_noticia": {}, "con_catalizador": {}, "accionables": {}},
        "condiciones": {"patron": 90, "temprano": 95, "riesgo_definido": 88,
                         "dinero_entrando": 4, "sobre_umbral": 60},
        "errores": {}, "score_maximo": 70})
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "había dinero entrando" in texto
    assert "La que más descarta: «había dinero entrando»" in texto


def test_reporte_alarma_si_nadie_alcanzo_el_umbral(tmp_path):
    # La alarma temprana contra el error que ya costó semanas.
    _escribir(tmp_path, "2026-08-24", {
        "embudo": {"operables": {"large": 10}, "evaluadas": {"large": 10},
                   "con_alguna_noticia": {}, "con_catalizador": {}, "accionables": {}},
        "condiciones": {}, "errores": {}, "score_maximo": 12.0})
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "Ninguna candidata llegó al umbral" in texto


def test_reporte_alarma_con_muchos_errores(tmp_path):
    _escribir(tmp_path, "2026-08-24", {
        "embudo": {"operables": {"large": 10}, "evaluadas": {"large": 10},
                   "con_alguna_noticia": {}, "con_catalizador": {}, "accionables": {}},
        "condiciones": {}, "errores": {"noticias:SSLError": 500}, "score_maximo": 99})
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "500 errores" in texto
    assert "SSLError" in texto


def test_reporte_lee_jsonl_partido_igual_que_el_json_legacy(tmp_path):
    dest = tmp_path / "2026-08-24" / "gha" / "events.jsonl"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps({
        "embudo": {"operables": {"large": 10}, "evaluadas": {"large": 10},
                   "con_alguna_noticia": {}, "con_catalizador": {}, "accionables": {}},
        "condiciones": {}, "errores": {}, "score_maximo": 99,
    }) + "\n")
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "Sin señales de alarma" in texto


def test_reporte_sin_alarmas_lo_dice(tmp_path):
    _escribir(tmp_path, "2026-08-24", {
        "embudo": {"operables": {"large": 10}, "evaluadas": {"large": 10},
                   "con_alguna_noticia": {}, "con_catalizador": {}, "accionables": {}},
        "condiciones": {}, "errores": {}, "score_maximo": 99})
    texto = reporte_semanal.construir("2026-08-24", "2026-08-28", tmp_path)
    assert "Sin señales de alarma" in texto
