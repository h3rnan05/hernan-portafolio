"""Abogado del diablo -- Principios 1, 2 y 11 del pedido de 2026-07-27
("construye un sistema en el que confiaría para invertir mi propio
dinero"): "cada oportunidad debe intentar demostrar por qué NO debería
operarse... debe comportarse como dos analistas: uno intenta convencer,
otro intenta destruir la idea. Solo si la tesis sobrevive a ese debate
interno se convierte en alerta."

El "analista que convence" ya existe: es todo el pipeline hasta
`evaluator.evaluar` (catalizador + dinero + desequilibrio + patrón +
temprano). Este módulo es el otro analista: recibe una candidata que YA
es accionable y busca razones para matarla. Corre DESPUÉS del evaluador
a propósito -- ser accionable es la condición de entrada al debate, no
el veredicto final.

Dos clases de objeción, ambas 100% deterministas y trazables a un dato
real (Principio 6: "¿qué datos objetivos te hicieron llegar a esa
conclusión?"):

- **Fatales**: matan la alerta sin importar el score. Todas son casos de
  "no puedo definir o acotar la pérdida" o "el combustible se acabó" --
  la respuesta directa a "¿qué tendría que salir mal para perder dinero
  aquí?" cuando la respuesta es inaceptable. Perder una buena
  oportunidad es mejor que una mala operación (Principio 5).
- **Advertencias**: no matan la alerta, pero viajan con ella hasta el
  mensaje ("qué tendría que salir mal") -- el usuario decide con los
  riesgos a la vista, nunca con una versión optimista de la tesis.

Cada objeción incluye `que_cambiaria` -- qué tendría que ser distinto
para que la objeción desapareciera (Principio 7: nunca una caja negra).
"""

from __future__ import annotations

from dataclasses import dataclass

from momentum_hunter.models import FactoresIntradia

# Umbrales fijos y documentados -- decisiones editoriales explícitas,
# nunca ajustadas por un modelo (Principio 8).
UMBRAL_RIESGO_STOP_PCT = 0.08        # distancia al stop > 8% del precio = pérdida inaceptable por operación
UMBRAL_VOLUMEN_MURIENDO = 0.7        # aceleración < 0.7 = el dinero está saliendo, no entrando
UMBRAL_VOLUMEN_ENFRIANDOSE = 1.0     # aceleración < 1.0 = ya no acelera (advertencia, no muerte)
HORA_UTC_ULTIMA_HORA = 19.0          # 3:00pm ET (verano) -- queda <1h de sesión regular
MINUTOS_CATALIZADOR_FRIO = 120.0     # la reacción fuerte a una noticia suele darse antes de 2h


@dataclass(frozen=True)
class Objecion:
    clave: str            # identificador estable para auditoría/tests, nunca se parsea el texto
    fatal: bool
    texto: str            # lenguaje humano -- puede llegar al mensaje (advertencias) o a la auditoría
    que_cambiaria: str    # qué tendría que ser distinto para retirar la objeción


def refutar(
    factores: FactoresIntradia,
    minutos_desde_catalizador: float | None,
    stop: float | None,
    hora_utc: float,
    advertencias_memoria: tuple[str, ...] = (),
) -> list[Objecion]:
    """El debate interno. Devuelve TODAS las objeciones encontradas
    (fatales primero), no solo la primera -- la auditoría debe registrar
    el caso completo en contra, no una muestra. Lista vacía = la tesis
    sobrevivió el debate.

    `advertencias_memoria` son las frases ya construidas por
    `memoria.advertencias_contextuales` (Principio 12: la memoria ajusta
    la confianza, nunca prohíbe -- por eso entran como advertencias, no
    como fatales)."""
    objeciones: list[Objecion] = []
    precio = factores.precio_actual

    # --- Fatales: la pérdida no se puede definir o acotar ---
    if stop is None or precio is None:
        objeciones.append(Objecion(
            clave="sin_salida", fatal=True,
            texto="No encuentro una salida clara si esto sale mal. Sin saber dónde cortar "
                  "la pérdida, no se arriesga dinero -- esa regla no tiene excepciones.",
            que_cambiaria="Que el precio construya un piso claro que sirva como punto de salida.",
        ))
    elif precio > 0 and (precio - stop) / precio > UMBRAL_RIESGO_STOP_PCT:
        pct = (precio - stop) / precio
        objeciones.append(Objecion(
            clave="riesgo_amplio", fatal=True,
            texto=f"La salida queda a {pct:.0%} del precio de entrada -- si sale mal, se pierde "
                  "demasiado de un solo golpe. Prefiero perder la oportunidad que ese dinero.",
            que_cambiaria="Que el precio se acerque a un piso más cercano, para que la pérdida "
                          "posible sea pequeña y definida.",
        ))

    acel = factores.aceleracion_volumen
    if acel is not None and acel < UMBRAL_VOLUMEN_MURIENDO:
        objeciones.append(Objecion(
            clave="dinero_saliendo", fatal=True,
            texto="El dinero está dejando de entrar: en los últimos minutos se negocia mucho "
                  "menos que hace un rato. Sin combustible, el movimiento se apaga.",
            que_cambiaria="Que el volumen vuelva a acelerarse en vez de apagarse.",
        ))
    elif acel is not None and acel < UMBRAL_VOLUMEN_ENFRIANDOSE:
        objeciones.append(Objecion(
            clave="volumen_enfriandose", fatal=False,
            texto="El interés se está enfriando: entra menos dinero que hace unos minutos.",
            que_cambiaria="Que el volumen vuelva a acelerarse.",
        ))

    # --- Advertencias: riesgos reales que el usuario debe ver antes de decidir ---
    if hora_utc >= HORA_UTC_ULTIMA_HORA:
        objeciones.append(Objecion(
            clave="poco_tiempo", fatal=False,
            texto="Queda menos de una hora de mercado -- la idea tiene poco tiempo para "
                  "trabajar hoy.",
            que_cambiaria="Que la misma configuración aparezca con más sesión por delante.",
        ))

    if minutos_desde_catalizador is not None and minutos_desde_catalizador > MINUTOS_CATALIZADOR_FRIO:
        objeciones.append(Objecion(
            clave="noticia_fria", fatal=False,
            texto="La noticia ya tiene más de dos horas. La reacción más fuerte suele darse "
                  "al principio -- puede que lo mejor del movimiento ya haya pasado.",
            que_cambiaria="Un catalizador más fresco, o una señal nueva que renueve el interés.",
        ))

    for texto in advertencias_memoria:
        objeciones.append(Objecion(
            clave="historial_debil", fatal=False, texto=texto,
            que_cambiaria="Que este tipo de jugada empiece a funcionar mejor en resultados "
                          "reales medidos.",
        ))

    objeciones.sort(key=lambda o: not o.fatal)  # fatales primero, orden estable
    return objeciones
