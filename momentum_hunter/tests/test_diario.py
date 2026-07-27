"""Pruebas del diario automático -- páginas deterministas desde números
reales medidos, escritas una sola vez por alerta."""

from __future__ import annotations

from momentum_hunter.diario import escribir_nuevas, pagina
from momentum_hunter.tracker import AlertaRegistrada


def _alerta(id_="a1", resuelta=True, diario_escrito=False, retorno_3d=0.08,
           max_pct=0.12, min_pct=-0.02) -> AlertaRegistrada:
    return AlertaRegistrada(
        id=id_, ticker="ACME", fecha="2026-07-20T13:35:00+00:00",
        precio_entrada=5.20, stop=5.00, objetivo1=5.60, objetivo2=None,
        clasificacion="gap_and_go", estrategia="", score=95.0,
        resultados_pct={"1d": 0.05, "3d": retorno_3d, "5d": 0.06, "10d": 0.04},
        precio_maximo_pct=max_pct, precio_minimo_pct=min_pct,
        resuelta=resuelta, diario_escrito=diario_escrito,
        hora_utc=13, catalizador_tipo="fda", rvol=4.0,
    )


def test_pagina_ganadora_que_toco_objetivo():
    # objetivo 5.60 sobre entrada 5.20 = +7.7%; max_pct 12% -> lo tocó.
    texto = pagina(_alerta())
    assert "# ACME -- 2026-07-20 (funcionó)" in texto
    assert "¿Tocó el objetivo?: sí" in texto
    assert "¿Tocó el stop?: no" in texto
    assert "tomar al menos una parte de la ganancia" in texto
    assert "Por qué funcionó" in texto


def test_pagina_perdedora_que_toco_stop():
    # stop 5.00 sobre entrada 5.20 = -3.8%; min_pct -10% -> lo tocó.
    texto = pagina(_alerta(retorno_3d=-0.06, max_pct=0.01, min_pct=-0.10))
    assert "(falló)" in texto
    assert "¿Tocó el stop?: sí" in texto
    assert "salir ahí sin discutir" in texto
    assert "Por qué falló" in texto
    # Honestidad: lo que no se puede medir se declara, no se inventa.
    assert "lo declaro en vez de inventarlo" in texto


def test_pagina_que_toco_ambos_pide_gestion_activa():
    texto = pagina(_alerta(retorno_3d=0.02, max_pct=0.12, min_pct=-0.10))
    assert "tocó tanto el objetivo como el stop" in texto
    assert "no sé cuál tocó primero" in texto


def test_pagina_incluye_los_numeros_reales():
    texto = pagina(_alerta())
    assert "+8.0%" in texto     # retorno 3d
    assert "+12.0%" in texto    # mejor momento
    assert "RVOL 4.0x" in texto


def test_escribir_nuevas_solo_resueltas_sin_pagina(tmp_path):
    resuelta = _alerta(id_="r1")
    pendiente = _alerta(id_="p1", resuelta=False)
    ya_escrita = _alerta(id_="e1", diario_escrito=True)
    rutas = escribir_nuevas([resuelta, pendiente, ya_escrita], dir_diario=tmp_path)
    assert len(rutas) == 1
    assert rutas[0].name == "2026-07-20-ACME-r1.md"
    assert resuelta.diario_escrito is True
    assert pendiente.diario_escrito is False


def test_escribir_nuevas_es_idempotente(tmp_path):
    a = _alerta()
    escribir_nuevas([a], dir_diario=tmp_path)
    assert escribir_nuevas([a], dir_diario=tmp_path) == []


def test_escribir_nuevas_sin_pendientes_no_crea_directorio(tmp_path):
    destino = tmp_path / "diario"
    assert escribir_nuevas([_alerta(resuelta=False)], dir_diario=destino) == []
    assert not destino.exists()
