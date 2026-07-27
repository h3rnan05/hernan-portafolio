"""Pruebas de la memoria contextual -- sobre todo el Principio 3: con
muestra insuficiente el sistema ADMITE que no sabe, nunca cita un
porcentaje sobre 3 casos."""

from __future__ import annotations

from momentum_hunter.memoria import (
    N_MINIMO_HISTORIAL,
    advertencias_contextuales,
    confianza,
    contexto_catalizador,
    contexto_patron,
    estrellas,
    frase_probabilidad,
    linea_calidad,
)
from momentum_hunter.tracker import AlertaRegistrada


def _alerta(id_, retorno_3d, clasificacion="gap_and_go", catalizador_tipo="fda") -> AlertaRegistrada:
    return AlertaRegistrada(
        id=id_, ticker=f"T{id_}", fecha=f"2026-07-{int(id_) % 28 + 1:02d}",
        precio_entrada=10.0, stop=9.5, objetivo1=11.0, objetivo2=None,
        clasificacion=clasificacion, estrategia="", score=90.0,
        resultados_pct={"3d": retorno_3d}, resuelta=True, catalizador_tipo=catalizador_tipo,
    )


def _historial(n, retorno=0.05, **kwargs):
    return [_alerta(str(i), retorno, **kwargs) for i in range(n)]


def test_contexto_patron_solo_cuenta_ese_patron():
    historial = _historial(3, clasificacion="gap_and_go") + _historial(2, clasificacion="bull_flag")
    # ids duplicados entre grupos no importan para el conteo
    ctx = contexto_patron(historial, "gap_and_go")
    assert ctx.n == 3
    assert ctx.dimension == "patron"


def test_contexto_patron_none_devuelve_n_cero():
    ctx = contexto_patron(_historial(5), None)
    assert ctx.n == 0


def test_contexto_catalizador_agrupa_por_tipo():
    historial = _historial(4, catalizador_tipo="fda") + _historial(2, catalizador_tipo="earnings")
    ctx = contexto_catalizador(historial, "fda")
    assert ctx.n == 4


def test_frase_sin_historial_admite_que_no_sabe():
    ctx = contexto_patron([], "gap_and_go")
    frase = frase_probabilidad(ctx)
    assert "no puedo darte una probabilidad honesta" in frase
    assert "%" not in frase


def test_frase_con_muestra_chica_no_cita_porcentaje():
    ctx = contexto_patron(_historial(3), "gap_and_go")
    frase = frase_probabilidad(ctx)
    assert "No voy a inventar confianza" in frase
    assert "3" in frase
    # El único uso de % permitido aquí sería un win rate -- no debe haberlo.
    assert "de las veces" not in frase


def test_frase_con_muestra_suficiente_cita_el_dato_real():
    historial = _historial(N_MINIMO_HISTORIAL, retorno=0.05)   # todas ganadoras -> 100%
    ctx = contexto_patron(historial, "gap_and_go")
    frase = frase_probabilidad(ctx)
    assert "100%" in frase
    assert f"{N_MINIMO_HISTORIAL} casos medidos" in frase
    assert "El pasado no garantiza nada" in frase


def test_advertencia_con_historial_debil_y_muestra_suficiente():
    historial = _historial(N_MINIMO_HISTORIAL, retorno=-0.05)   # todas perdedoras -> 0%
    ctx = contexto_patron(historial, "gap_and_go")
    avisos = advertencias_contextuales([ctx])
    assert len(avisos) == 1
    assert "baja mi confianza" in avisos[0]


def test_sin_advertencia_con_muestra_chica_aunque_pierda_todo():
    # 3 derrotas seguidas no son evidencia -- son ruido (mismo umbral que
    # exige frase_probabilidad para citar un porcentaje).
    historial = _historial(3, retorno=-0.05)
    ctx = contexto_patron(historial, "gap_and_go")
    assert advertencias_contextuales([ctx]) == []


def test_sin_advertencia_con_historial_bueno():
    historial = _historial(N_MINIMO_HISTORIAL, retorno=0.05)
    ctx = contexto_patron(historial, "gap_and_go")
    assert advertencias_contextuales([ctx]) == []


# --------- Refinamiento "Head Trader": estrellas + confianza ---------

def _ctx_mixto(ganadoras, perdedoras):
    # ids numéricos: el helper _alerta deriva la fecha de int(id_).
    historial = (
        [_alerta(str(i), 0.05) for i in range(ganadoras)]
        + [_alerta(str(100 + i), -0.05) for i in range(perdedoras)]
    )
    return contexto_patron(historial, "gap_and_go")


def test_estrellas_none_sin_muestra_suficiente():
    assert estrellas(contexto_patron(_historial(4), "gap_and_go")) is None


def test_estrellas_por_win_rate():
    assert estrellas(_ctx_mixto(8, 2)) == "★★★★★"    # 80%
    assert estrellas(_ctx_mixto(13, 7)) == "★★★★☆"   # 65%
    assert estrellas(_ctx_mixto(11, 9)) == "★★★☆☆"   # 55%
    assert estrellas(_ctx_mixto(9, 11)) == "★★☆☆☆"   # 45%
    assert estrellas(_ctx_mixto(2, 8)) == "★☆☆☆☆"    # 20%


def test_linea_calidad_sin_muestra_lo_admite():
    linea = linea_calidad(contexto_patron(_historial(4), "gap_and_go"))
    assert "sin calificar todavía" in linea
    assert "★" not in linea


def test_linea_calidad_con_muestra_cita_el_dato():
    linea = linea_calidad(_ctx_mixto(13, 7))
    assert "★★★★☆" in linea
    assert "65%" in linea
    assert "20 casos" in linea


def test_confianza_baja_sin_muestra_y_lo_dice():
    nivel, texto = confianza(contexto_patron([], "gap_and_go"), n_advertencias=0)
    assert nivel == "Baja"
    assert "No invento confianza que no tengo" in texto


def test_confianza_alta_con_historial_fuerte():
    nivel, texto = confianza(_ctx_mixto(13, 7), n_advertencias=0)   # 65%
    assert nivel == "Alta"
    assert "vista 20 veces" in texto
    assert "65%" in texto


def test_confianza_se_rebaja_un_nivel_con_dos_dudas():
    nivel, texto = confianza(_ctx_mixto(13, 7), n_advertencias=2)
    assert nivel == "Media"
    assert "Le resté un nivel" in texto


def test_confianza_baja_con_historial_debil_aunque_haya_muestra():
    nivel, _ = confianza(_ctx_mixto(3, 17), n_advertencias=0)   # 15%
    assert nivel == "Baja"
