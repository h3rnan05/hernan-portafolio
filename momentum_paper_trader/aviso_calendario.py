"""Aviso de calendario ausente, una vez por día de Nueva York.

Lo llama el ejecutor, el cierre y el vigía cuando el archivo no cubre
hoy. Reusa el Telegram del paper (`notify`) y el dedupe de disco: un
tick cada 60 s no puede convertirse en un mensaje por minuto. El texto
lo escribimos nosotros; nunca el cuerpo de una excepción.
"""

from __future__ import annotations

from datetime import datetime

from momentum_hunter import calendario

from momentum_paper_trader import dedupe_avisos, notify


def avisar_si_desconocido(ahora: datetime) -> bool:
    """True si este llamado mandó el aviso. False si no hacía falta o ya se mandó."""
    consulta = calendario.consultar(ahora)
    if not consulta.desconocido:
        return False
    marca = dedupe_avisos.clave("calendario", "sesion", ahora)
    if dedupe_avisos.ya_avisada(marca):
        return False
    texto = notify.formatear_error(
        tipo="calendario de sesión",
        detalle=(
            f"No hay calendario para hoy ({consulta.motivo}). "
            "No se abren entradas. El cierre de fin de día usa las 13:00 America/New_York."
        ),
    )
    notify.enviar(texto)
    dedupe_avisos.marcar(marca, ahora)
    return True
