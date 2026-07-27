"""Pruebas de la memoria contextual -- sobre todo el Principio 3: con
muestra insuficiente el sistema ADMITE que no sabe, nunca cita un
porcentaje sobre 3 casos."""

from __future__ import annotations

from momentum_hunter.memoria import (
    N_MINIMO_HISTORIAL,
    advertencias_contextuales,
    contexto_catalizador,
    contexto_patron,
    frase_probabilidad,
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
