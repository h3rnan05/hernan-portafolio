"""Filtro central de Telegram: solo entradas + seguridad crítica.

Pedido del dueño (2026-10-06): "que me llegue Telegram SOLO cuando
momentum ENTRA a un trade". Mientras el sistema evalúa si entrar
(WATCHING, SEÑAL DISPARADA, veredicto de la IA, NO ENTRA, expiradas,
re-chequeos, escaneos, latidos, resúmenes, reportes, avisos
informativos) no se manda nada: el texto queda en el log.

Categorías (las pone quien llama; el default es la más silenciosa):

  - ``entrada``  -- la compra se LLENÓ (LLENADA, `seguimiento.py`). Se
                    usa el fill, no la colocación: COLOCADA es "aceptada",
                    todavía no es un trade.
  - ``salida``   -- (dueño, 2026-10-06 12:26 Monterrey) la posición
                    individual se CERRÓ: CERRADA por objetivo
                    (take-profit) o por stop, con su P&L en USD y %
                    (`seguimiento.py`). El resumen de liquidación de
                    fin de día NO es esto: sigue en ``info``.
  - ``critico``  -- seguridad, rara vez dispara: posición sin stop /
                    sin salidas / sin seguimiento, el cierre de fin de día
                    falló o dejó la posición abierta, y el stop diario
                    (1 %) disparó.
  - ``info``     -- todo lo demás. Default.

Con el filtro activo, ``info`` NO sale: se loguea y se devuelve. Con el
filtro apagado vuelve el comportamiento anterior (todo sale igual que
antes de este cambio).

INTERRUPTOR -- sin deploy y sin reiniciar nada:

  1. Archivo ``/etc/momentum/telegram_solo_entradas`` (ruta cambiable con
     ``MOMENTUM_TELEGRAM_FLAG_FILE``). Si existe, su primer renglón manda:
     ``0`` apaga el filtro, ``1`` lo prende. Se lee en cada envío, así
     que el vigía (proceso largo) lo ve en el siguiente aviso.
  2. Si no hay archivo: la variable ``TELEGRAM_SOLO_ENTRADAS`` (``0`` /
     ``1``) del entorno (``/etc/momentum/paper.env``).
  3. Sin ninguna de las dos: ACTIVO (``1``).

Mismo criterio en ``scripts/notify_telegram.sh`` (los avisos de bash),
con la categoría en ``TELEGRAM_CATEGORIA``.

Solo stdlib: lo importan el hunter, el ejecutor y ``uso_api`` sin
arrastrar dependencias. No toca umbrales, sizing, límites ni la política
de la IA: solo decide si un texto ya armado sale o se queda en el log.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger("telegram_filtro")

ENTRADA = "entrada"
SALIDA = "salida"
CRITICO = "critico"
INFO = "info"

CATEGORIAS_QUE_SALEN = frozenset({ENTRADA, SALIDA, CRITICO})

ENV_FLAG = "TELEGRAM_SOLO_ENTRADAS"
ENV_ARCHIVO = "MOMENTUM_TELEGRAM_FLAG_FILE"
ARCHIVO_DEFAULT = "/etc/momentum/telegram_solo_entradas"

_APAGADO = {"0", "false", "no", "off"}
_PRENDIDO = {"1", "true", "si", "sí", "yes", "on"}


def _interpretar(valor: str | None) -> bool | None:
    if valor is None:
        return None
    v = valor.strip().lower()
    if v in _APAGADO:
        return False
    if v in _PRENDIDO:
        return True
    return None


def _valor_archivo() -> bool | None:
    ruta = os.environ.get(ENV_ARCHIVO) or ARCHIVO_DEFAULT
    try:
        with open(ruta, encoding="utf-8") as f:
            primero = f.readline()
    except OSError:
        return None
    return _interpretar(primero)


def solo_entradas() -> bool:
    """True si el filtro está activo (default). Nunca lanza."""
    try:
        archivo = _valor_archivo()
        if archivo is not None:
            return archivo
        env = _interpretar(os.environ.get(ENV_FLAG))
        if env is not None:
            return env
    except Exception:  # pragma: no cover - cinturón: ante la duda, filtro activo
        pass
    return True


def permitido(categoria: str | None) -> bool:
    """¿Este aviso sale a Telegram? Con el filtro apagado, todo sale."""
    if not solo_entradas():
        return True
    return (categoria or INFO) in CATEGORIAS_QUE_SALEN


def silenciar(texto: str, categoria: str | None, origen: str = "") -> bool:
    """True si el aviso NO debe salir (y ya quedó en el log). El texto
    de un aviso no lleva secretos (lo armamos nosotros), pero se corta
    para que un resumen largo no inunde el journal."""
    if permitido(categoria):
        return False
    corto = " ".join(str(texto or "").split())
    if len(corto) > 300:
        corto = corto[:299] + "…"
    log.info(
        "telegram silenciado (%s=1, categoria=%s%s): %s",
        ENV_FLAG, categoria or INFO, f", origen={origen}" if origen else "", corto,
    )
    return True
