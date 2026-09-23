"""Pruebas del reporte de embudo -- todas sobre archivos temporales con
la MISMA forma que escriben `momentum_hunter.telemetria`, `audit`,
`watchlist`, `estado`, `momentum_paper_trader.telemetria` y `movers`.
Nada de red, nada del repo real."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario
from momentum_hunter.catalysts.detector import Catalizador
from momentum_hunter.models import FactoresMomentum, Metadata
from momentum_hunter.scoring import Puntuacion
from momentum_paper_trader import embudo, estado

DIA = "2026-09-22"
AHORA = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)


def _candidato_diario(ticker: str, large: bool = False) -> CandidatoDiario:
    cat = Catalizador(tipo="earnings", titular="x", fuente="Reuters", fecha=f"{DIA}T13:45:00+00:00")
    return CandidatoDiario(
        ticker=ticker, nombre=ticker, precio=10.0, volumen_promedio=2_000_000.0,
        factores=FactoresMomentum(atr=0.5), catalizador=cat,
        meta=Metadata(ticker=ticker, market_cap=50e9 if large else 500e6),
        puntuacion=Puntuacion(ticker=ticker, score_total=70.0, sub={}),
    )


def _evaluacion(patron, temprano, riesgo, score, accionable, penal=()):
    return {"patron": patron, "temprano": temprano, "riesgo_definido": riesgo,
            "score_ajustado": score, "accionable": accionable, "penalizaciones": list(penal)}


def _armar_fuentes(tmp_path: Path) -> dict:
    # 1) telemetría del escaneo: dos corridas de escaneo y un rechequeo
    th = tmp_path / "telem_hunter"
    (th / DIA / "vps").mkdir(parents=True)
    corridas = [
        {"timestamp": f"{DIA}T13:11:00+00:00", "modo": "escaneo", "universo_escaneado": 1000,
         "embudo": {"operables": {"small": 300, "large": 100}, "con_alguna_noticia": {"small": 290, "large": 95},
                    "con_catalizador": {"small": 3, "large": 4}, "evaluadas": {"small": 3, "large": 4},
                    "accionables": {"large": 1}, "rechazos_universo": {"vol_bajo_small": 40},
                    "keyword_rechazos": {"fuera_ventana": 500}},
         "condiciones": {"patron": 2, "temprano": 6, "riesgo_definido": 7, "dinero_entrando": 1, "sobre_umbral": 1}},
        {"timestamp": f"{DIA}T13:41:00+00:00", "modo": "escaneo", "universo_escaneado": 1000,
         "embudo": {"operables": {"small": 250, "large": 120}, "con_catalizador": {"small": 1},
                    "rechazos_universo": {"vol_bajo_small": 10, "sin_barras": 2}},
         "condiciones": {"patron": 1}},
        {"timestamp": f"{DIA}T13:42:00+00:00", "modo": "watchlist", "universo_escaneado": 0, "embudo": {}},
    ]
    (th / DIA / "vps" / "events.jsonl").write_text("\n".join(json.dumps(c) for c in corridas) + "\n")
    # sombra de movers en el mismo árbol
    movers = [{"timestamp": f"{DIA}T13:02:00+00:00", "screener_ok": True, "error": None,
               "embudo": {"clase_a": 0, "clase_b": 2}, "rechazos": {"rvol_bajo": 20, "bajo_vwap": 5}},
              {"timestamp": f"{DIA}T13:07:00+00:00", "screener_ok": True, "error": None,
               "embudo": {"clase_a": 1, "clase_b": 1}, "rechazos": {"rvol_bajo": 15}}]
    (th / DIA / "vps" / "movers.jsonl").write_text("\n".join(json.dumps(c) for c in movers) + "\n")

    # 2) auditoría: GS accionable en una lectura; VGZ con las tres
    # condiciones pero score 50,8 (el caso real); SFIX sin patrón.
    aud = tmp_path / "auditoria"
    aud.mkdir()
    data = {"corridas": [
        {"timestamp": f"{DIA}T13:41:00+00:00", "candidatos": [
            {"ticker": "GS", "evaluacion": _evaluacion("orb", True, True, 40.0, False,
                                                       ["El volumen no muestra que esté entrando dinero ahora mismo."])},
            {"ticker": "VGZ", "evaluacion": _evaluacion(None, True, True, 0.0, False,
                                                        ["No hay un patrón técnico claro formándose todavía."])},
            {"ticker": "SFIX", "evaluacion": _evaluacion(None, True, True, 0.0, False)},
        ]},
        {"timestamp": f"{DIA}T13:46:00+00:00", "candidatos": [
            {"ticker": "GS", "evaluacion": _evaluacion("orb", True, True, 60.0, True)},
            {"ticker": "VGZ", "evaluacion": _evaluacion("orb", True, True, 50.8, False,
                                                        ["No hay un desequilibrio claro de oferta/demanda."])},
            {"ticker": "SFIX", "evaluacion": _evaluacion(None, True, True, 0.0, False)},
        ]},
    ]}
    (aud / f"{DIA}.json").write_text(json.dumps(data))

    # 3) watchlist: GS disparó y se archivó; VGZ MISSED a 14 velas; SFIX
    # invalidada por precio; y una entrada de OTRO día que no cuenta.
    gs = watchlist.desde_candidato_diario(_candidato_diario("GS", large=True), AHORA)
    gs.es_large_cap = True   # la banda la fija el pipeline al crear la entrada; acá se simula
    watchlist.marcar_triggered(gs, "m", "d", "ev", AHORA)
    vgz = watchlist.desde_candidato_diario(_candidato_diario("VGZ"), AHORA)
    watchlist.marcar_missed(vgz, "El patrón se activó hace 14 velas -- ya pasó la ventana.", AHORA)
    sfix = watchlist.desde_candidato_diario(_candidato_diario("SFIX"), AHORA)
    watchlist.marcar_invalidated(sfix, "El precio ($2.90) cayó por debajo del stop de la idea ($2.95).", AHORA)
    otro = watchlist.desde_candidato_diario(_candidato_diario("OTRO"), datetime(2026, 9, 10, 14, 0, tzinfo=UTC))
    wl = tmp_path / "watchlist.json"
    watchlist.guardar([gs, vgz, sfix, otro], wl)

    # 4) revisiones: GS revisada por la IA con 6 (no entró, umbral 7 en
    # ese momento); LOW sin IA por precio fuera de alcance; una vieja.
    revs = [
        estado.RevisionIA(ticker="GS", creado_en=gs.creado_en, entro=False, confianza=6,
                          razonamiento="casi", timestamp=f"{DIA}T15:00:00+00:00", ia_entraria=False),
        estado.RevisionIA(ticker="LOW", creado_en=f"{DIA}T14:00:00+00:00", entro=False, confianza=0,
                          razonamiento="no cabe", timestamp=f"{DIA}T15:01:00+00:00", ia_entraria=None,
                          motivo_no_operada=estado.MOTIVO_PRECIO_FUERA_DE_ALCANCE),
        estado.RevisionIA(ticker="VIEJA", creado_en="2026-09-10T14:00:00+00:00", entro=True, confianza=8,
                          razonamiento="sí", timestamp="2026-09-10T15:00:00+00:00", ia_entraria=True),
    ]
    rp = tmp_path / "revisiones.json"
    estado.guardar(revs, rp)

    # 5) telemetría paper: tres ticks, una orden
    tp = tmp_path / "telem_paper"
    (tp / DIA / "vps").mkdir(parents=True)
    ticks = [{"timestamp": f"{DIA}T13:0{i}:00+00:00", "ordenes_colocadas": 1 if i == 2 else 0, "revisiones": 0}
             for i in range(3)]
    (tp / DIA / "vps" / "events.jsonl").write_text("\n".join(json.dumps(t) for t in ticks) + "\n")

    return {"dir_telemetria_hunter": th, "dir_auditoria": aud, "path_watchlist": wl,
            "path_revisiones": rp, "dir_telemetria_paper": tp}


def test_construir_cruza_las_seis_fuentes(tmp_path):
    emb = embudo.construir(DIA, DIA, umbral_score=55.0, **_armar_fuentes(tmp_path))

    # Etapa 1: solo corridas de escaneo (el rechequeo no suma), por banda.
    assert emb.escaneos == 2 and emb.universo_escaneado == 2000
    assert emb.etapa1["operables"] == 770 and emb.etapa1_por_banda["operables"] == {"small": 550, "large": 220}
    assert emb.etapa1["con_catalizador"] == 8 and emb.etapa1["accionables"] == 1
    assert emb.rechazos_universo == {"vol_bajo_small": 50, "sin_barras": 2}
    assert emb.condiciones["patron"] == 3 and emb.condiciones["sobre_umbral"] == 1
    # Etapa 2: ticker-días únicos, y el "casi" con su score máximo.
    assert emb.evaluadas_unicas == 3 and emb.con_patron_alguna_vez == 2
    assert emb.con_tres_condiciones_alguna_vez == 2 and emb.accionables_unicas == 1
    assert emb.casi == [{"dia": DIA, "ticker": "VGZ", "score_max": 50.8}]
    assert emb.casi_pasarian_con_score == {"45": 1, "50": 1, "55": 0}
    # Watchlist: solo las creadas en el rango.
    assert emb.watchlist_creadas == 3 and emb.watchlist_dispararon == 1
    assert emb.watchlist_por_banda == {"small": 2, "large": 1}
    assert emb.missed_velas == [14]
    assert emb.invalidated_motivos == {"precio bajo el stop": 1}
    # IA: la revisión sin consulta no cuenta como confianza ni como veredicto.
    assert emb.revisiones == 2 and emb.ia_consultada == 1 and emb.entraron == 0
    assert emb.ia_confianza == {"6": 1}
    assert emb.ia_aprobarian_con_umbral == {"5": 1, "6": 1, "7": 0}
    assert emb.no_operadas_por_motivo == {"precio_fuera_de_alcance": 1}
    # Paper y sombra.
    assert emb.ticks_paper == 3 and emb.ordenes_colocadas == 1
    assert emb.sombra_corridas == 2 and emb.sombra_clase_a == 1 and emb.sombra_clase_b == 3
    assert emb.sombra_rechazos == {"rvol_bajo": 35, "bajo_vwap": 5}
    assert emb.fuentes_vacias == []


def test_fuentes_ausentes_se_dicen_y_no_se_rellenan(tmp_path):
    emb = embudo.construir(
        DIA, DIA,
        dir_telemetria_hunter=tmp_path / "no", dir_auditoria=tmp_path / "no",
        path_watchlist=tmp_path / "no.json", path_revisiones=tmp_path / "no.json",
        dir_telemetria_paper=tmp_path / "no")
    assert set(emb.fuentes_vacias) == {
        "telemetria_hunter", "auditoria", "watchlist", "revisiones", "telemetria_paper", "sombra_movers"}
    assert emb.escaneos == 0 and emb.evaluadas_unicas == 0 and emb.revisiones == 0
    texto = embudo.formatear(emb)
    assert "Fuentes sin datos en el rango" in texto and "(sin datos)" in texto


def test_rango_fuera_de_los_dias_con_datos_queda_vacio(tmp_path):
    emb = embudo.construir("2026-09-01", "2026-09-02", **_armar_fuentes(tmp_path))
    assert emb.escaneos == 0 and emb.watchlist_creadas == 0 and emb.revisiones == 0
    assert "auditoria" in emb.fuentes_vacias


def test_formatear_cuenta_la_historia_completa(tmp_path):
    emb = embudo.construir(DIA, DIA, umbral_score=55.0, **_armar_fuentes(tmp_path))
    texto = embudo.formatear(emb)
    assert texto.startswith(f"EMBUDO {DIA} → {DIA}")
    for frase in ("1) Escaneo", "2) Evaluador", "3) Watchlist", "4) Ejecutor e IA", "5) Sombra de movers",
                  "casi (tres condiciones sí, score no): 1", "VGZ score máx 50.8",
                  "creadas 3 · dispararon 1", "MISSED: velas al marcar → mín 14",
                  "aprobarían con umbral (cota superior): 5 1, 6 1, 7 0",
                  "órdenes colocadas 1", "clase A 1 · clase B 3"):
        assert frase in texto, frase
    assert "Fuentes sin datos" not in texto


def test_auditoria_ilegible_no_tumba_el_reporte(tmp_path):
    fuentes = _armar_fuentes(tmp_path)
    (fuentes["dir_auditoria"] / f"{DIA}.json").write_text("{esto no es json")
    emb = embudo.construir(DIA, DIA, **fuentes)
    assert "auditoria" in emb.fuentes_vacias and emb.escaneos == 2


def test_main_imprime_texto_y_json(tmp_path, monkeypatch, capsys):
    fuentes = _armar_fuentes(tmp_path)
    real = embudo.construir
    monkeypatch.setattr(embudo, "construir", lambda d, h, **kw: real(d, h, **fuentes))
    assert embudo.main(["--desde", DIA, "--hasta", DIA, "--json"]) == 0
    salida = capsys.readouterr().out
    assert "EMBUDO" in salida and '"watchlist_dispararon": 1' in salida


def test_embudo_muestra_bloqueos_del_ejecutor_por_codigo(tmp_path):
    fuentes = _armar_fuentes(tmp_path)
    tp = fuentes["dir_telemetria_paper"] / DIA / "vps" / "events.jsonl"
    ticks = [json.loads(l) for l in tp.read_text().splitlines() if l.strip()]
    ticks[0]["bloqueos"] = {"MAXIMO_POSICIONES": 1}
    ticks[0]["capacidad_llena"] = "MAXIMO_POSICIONES"
    ticks[1]["bloqueos"] = {"DATO_FALTANTE:niveles": 2, "MAXIMO_POSICIONES": 1}
    ticks[1]["capacidad_llena"] = "MAXIMO_POSICIONES"
    tp.write_text("\n".join(json.dumps(t) for t in ticks) + "\n")
    emb = embudo.construir(DIA, DIA, **fuentes)
    assert emb.bloqueos_por_codigo == {"MAXIMO_POSICIONES": 2, "DATO_FALTANTE:niveles": 2}
    assert emb.corridas_sin_capacidad == {"MAXIMO_POSICIONES": 2}
    texto = embudo.formatear(emb)
    assert "bloqueos del ejecutor por código: MAXIMO_POSICIONES 2, DATO_FALTANTE:niveles 2" in texto
    assert "corridas sin capacidad (límite global lleno): MAXIMO_POSICIONES 2" in texto
