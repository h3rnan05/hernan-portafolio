"""Códigos de bloqueo del ejecutor -- un catálogo, sin lógica.

POR QUÉ EXISTE (2026-09-23). El panel marcó "Revisar" con 942 bloqueos
contra 7 decisiones: con 5 posiciones abiertas, el tope de posiciones
bloqueaba cada señal disparada en cada tick de 60 s, y el panel contaba
eventos crudos. Ninguno de esos 942 decía nada nuevo: era UN límite
lleno, repetido 8 señales × 120 ticks. Para que el conteo signifique
algo hacen falta tres cosas, y este módulo es la base de las tres:

  1. Un CÓDIGO estable por motivo, en cada evento `bloqueo_riesgo`, para
     que el panel y el embudo agrupen por motivo y no por texto libre.
  2. Distinguir los bloqueos por DATO faltante, nulo o viejo (fail-closed
     porque no se sabe) de los bloqueos por LÍMITE (fail-closed porque se
     sabe): los primeros piden revisión humana; los segundos son el
     sistema haciendo su trabajo. Prefijo `DATO_FALTANTE:<campo>`.
  3. Un límite GLOBAL lleno (mercado cerrado, poca sesión, cuenta
     ilegible, tope de posiciones) se registra UNA vez por corrida como
     `capacidad_llena` y no se evalúan candidatos uno por uno.

Nada de esto cambia ningún umbral, límite ni criterio de entrada. Solo
cómo se nombra y se cuenta lo que ya se bloqueaba. `dashboard/` importa
este módulo (sin dependencias) para saber qué códigos son conocidos: un
código nuevo en el log también es motivo de "Revisar".
"""

from __future__ import annotations

PREFIJO_DATO_FALTANTE = "DATO_FALTANTE"


def dato_faltante(campo: str) -> str:
    """Código para un bloqueo por dato nulo, faltante o viejo: el campo
    dice QUÉ faltó, para que la revisión humana no tenga que adivinar."""
    return f"{PREFIJO_DATO_FALTANTE}:{campo}"


def es_dato_faltante(codigo: str | None) -> bool:
    return isinstance(codigo, str) and codigo.startswith(PREFIJO_DATO_FALTANTE + ":")


# -- Globales: llenan la capacidad de la corrida entera (`capacidad_llena`) --
MERCADO_CERRADO = "MERCADO_CERRADO"
CIERRE_CERCANO = "CIERRE_CERCANO"
MAXIMO_POSICIONES = "MAXIMO_POSICIONES"
DATO_FALTANTE_RELOJ = dato_faltante("reloj_mercado")     # /v2/clock ilegible
DATO_FALTANTE_CUENTA = dato_faltante("cuenta")           # cash/equity ilegibles o ausentes

# -- Por señal (`bloqueo_riesgo`) --
DATO_FALTANTE_NIVELES = dato_faltante("niveles")                   # TRIGGERED sin entrada/stop/objetivo
DATO_FALTANTE_NIVELES_VIEJOS = dato_faltante("ultimos_niveles_ts")  # niveles más viejos que el tope
DATO_FALTANTE_ACTIVO = dato_faltante("activo")                     # ficha del símbolo ilegible
RIESGO_POR_OPERACION = "RIESGO_POR_OPERACION"
TICKER_COMPROMETIDO = "TICKER_COMPROMETIDO"
CONCENTRACION = "CONCENTRACION"
PRECIO_FUERA_DE_ALCANCE = "PRECIO_FUERA_DE_ALCANCE"
ACTIVO_NO_OPERABLE = "ACTIVO_NO_OPERABLE"
FUERA_DE_BANDA = "FUERA_DE_BANDA"
FRACCION_INSUFICIENTE = "FRACCION_INSUFICIENTE"

CODIGOS_GLOBALES = frozenset({
    MERCADO_CERRADO, CIERRE_CERCANO, MAXIMO_POSICIONES,
    DATO_FALTANTE_RELOJ, DATO_FALTANTE_CUENTA,
})
CODIGOS_POR_SENAL = frozenset({
    DATO_FALTANTE_NIVELES, DATO_FALTANTE_NIVELES_VIEJOS, DATO_FALTANTE_ACTIVO,
    RIESGO_POR_OPERACION, TICKER_COMPROMETIDO, CONCENTRACION, PRECIO_FUERA_DE_ALCANCE,
    ACTIVO_NO_OPERABLE, FUERA_DE_BANDA, FRACCION_INSUFICIENTE,
})
CODIGOS_CONOCIDOS = CODIGOS_GLOBALES | CODIGOS_POR_SENAL

# Los eventos anteriores a este catálogo solo traen `limite`; se les
# asigna un código para que el panel no los declare "motivo nuevo".
CODIGO_POR_LIMITE_LEGADO = {
    "mercado_cerrado": MERCADO_CERRADO,
    "cierre_cercano": CIERRE_CERCANO,
    "cuenta_ilegible": DATO_FALTANTE_CUENTA,
    "niveles_rancios": DATO_FALTANTE_NIVELES_VIEJOS,
    "riesgo_por_operacion": RIESGO_POR_OPERACION,
    "ticker_comprometido": TICKER_COMPROMETIDO,
    "maximo_posiciones": MAXIMO_POSICIONES,
    "concentracion": CONCENTRACION,
    "activo_no_operable": ACTIVO_NO_OPERABLE,
    "fuera_de_banda": FUERA_DE_BANDA,
    "fraccion_insuficiente": FRACCION_INSUFICIENTE,
}


def codigo_de_evento(evento: dict) -> str:
    """El código de un evento `bloqueo_riesgo`/`capacidad_llena`: el campo
    `codigo` si viene; si no, el que corresponde a su `limite` legado; si
    tampoco, el propio límite en mayúsculas (será "motivo nuevo")."""
    codigo = evento.get("codigo")
    if isinstance(codigo, str) and codigo:
        return codigo
    limite = str(evento.get("limite") or "").strip()
    if limite in CODIGO_POR_LIMITE_LEGADO:
        return CODIGO_POR_LIMITE_LEGADO[limite]
    return limite.upper() or "SIN_CODIGO"
