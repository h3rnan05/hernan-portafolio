"""Pruebas del abogado del diablo -- cada objeción se dispara por su
condición exacta y por nada más; la lista vacía significa que la tesis
sobrevivió el debate."""

from __future__ import annotations

from momentum_hunter.models import FactoresIntradia
from momentum_hunter.skeptic import refutar


def _factores(**kwargs) -> FactoresIntradia:
    base = dict(precio_actual=10.0, aceleracion_volumen=1.5)
    base.update(kwargs)
    return FactoresIntradia(**base)


def _claves(objeciones, solo_fatales=None):
    if solo_fatales is None:
        return [o.clave for o in objeciones]
    return [o.clave for o in objeciones if o.fatal == solo_fatales]


def test_tesis_limpia_sobrevive_sin_objeciones():
    objeciones = refutar(_factores(), minutos_desde_catalizador=10.0, stop=9.5, hora_utc=14.0)
    assert objeciones == []


def test_sin_stop_es_fatal():
    objeciones = refutar(_factores(), 10.0, stop=None, hora_utc=14.0)
    assert _claves(objeciones, solo_fatales=True) == ["sin_salida"]


def test_sin_precio_tambien_es_fatal():
    objeciones = refutar(_factores(precio_actual=None), 10.0, stop=9.5, hora_utc=14.0)
    assert "sin_salida" in _claves(objeciones, solo_fatales=True)


def test_stop_demasiado_lejos_es_fatal():
    # stop a 15% del precio (> 8% permitido)
    objeciones = refutar(_factores(), 10.0, stop=8.5, hora_utc=14.0)
    assert _claves(objeciones, solo_fatales=True) == ["riesgo_amplio"]


def test_stop_cercano_no_objeta():
    objeciones = refutar(_factores(), 10.0, stop=9.5, hora_utc=14.0)  # 5% de riesgo
    assert "riesgo_amplio" not in _claves(objeciones)


def test_dinero_saliendo_es_fatal():
    objeciones = refutar(_factores(aceleracion_volumen=0.5), 10.0, stop=9.5, hora_utc=14.0)
    assert "dinero_saliendo" in _claves(objeciones, solo_fatales=True)


def test_volumen_enfriandose_es_solo_advertencia():
    objeciones = refutar(_factores(aceleracion_volumen=0.85), 10.0, stop=9.5, hora_utc=14.0)
    assert "volumen_enfriandose" in _claves(objeciones, solo_fatales=False)
    assert _claves(objeciones, solo_fatales=True) == []


def test_ultima_hora_es_advertencia():
    objeciones = refutar(_factores(), 10.0, stop=9.5, hora_utc=19.5)
    assert "poco_tiempo" in _claves(objeciones, solo_fatales=False)


def test_noticia_fria_es_advertencia():
    objeciones = refutar(_factores(), minutos_desde_catalizador=180.0, stop=9.5, hora_utc=14.0)
    assert "noticia_fria" in _claves(objeciones, solo_fatales=False)


def test_advertencias_de_memoria_entran_como_no_fatales():
    aviso = "Mis últimas 12 alertas con este mismo tipo de jugada solo funcionaron 25% de las veces."
    objeciones = refutar(_factores(), 10.0, stop=9.5, hora_utc=14.0, advertencias_memoria=(aviso,))
    memoria = [o for o in objeciones if o.clave == "historial_debil"]
    assert len(memoria) == 1
    assert memoria[0].fatal is False
    assert memoria[0].texto == aviso


def test_fatales_van_primero_en_la_lista():
    objeciones = refutar(
        _factores(aceleracion_volumen=0.5), minutos_desde_catalizador=180.0,
        stop=None, hora_utc=19.5,
    )
    fatales_al_inicio = [o.fatal for o in objeciones]
    assert fatales_al_inicio == sorted(fatales_al_inicio, reverse=True)


def test_toda_objecion_explica_que_tendria_que_cambiar():
    objeciones = refutar(
        _factores(aceleracion_volumen=0.5), minutos_desde_catalizador=180.0,
        stop=None, hora_utc=19.5,
    )
    assert objeciones and all(o.que_cambiaria for o in objeciones)
