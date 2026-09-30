"""Fuentes externas para el buscador, PRIMERO como columnas del backtest v2.

Regla del proyecto (2026-09-28): ninguna fuente entra al hunter hasta
que, como columna del backtest v2, mejore la expectativa y el dueño lo
apruebe. Este paquete es la antesala: cada fuente sabe pedir su dato,
guardarlo en caché fuera de git, y responder "qué se sabía de este
símbolo en este minuto". El enriquecedor (`fuentes/enriquecer.py`, paso
3) pega esas respuestas al lado de cada señal del backtest; NO toca
`shadow_alpaca/backtest_v2/motor.py`.

Invariantes que todas las fuentes cumplen (las prueba `tests/test_base.py`):

  - Fail-closed: sin credencial, sin respuesta, con cuerpo ilegible o
    con la zona horaria en duda, la columna vale `FALTANTE`, nunca
    "no hay evento". `FALTANTE` es un valor explícito, distinto de
    None, de 0 y de False, y se serializa como la cadena "FALTANTE".
  - "No hay evento" solo se afirma cuando la fuente respondió completa
    para ese símbolo y esa ventana.
  - Caché en disco fuera del repo (`FUENTES_CACHE_DIR`, default
    `/var/lib/momentum/fuentes`). Un directorio dentro del checkout se
    rechaza.
  - Límites de cada API respetados con un `Limitador` propio por fuente.
  - Pruebas sin red: respuestas grabadas en `fuentes/tests/respuestas/`.
    Cuando el entorno de desarrollo no llega a la fuente, las respuestas
    son del formato público con emisores FICTICIOS, y cada fuente deja
    un comando `python -m fuentes grabar <fuente> ...` para grabar las
    reales desde el VPS y compararlas.
  - Este paquete no importa `momentum_paper_trader` ni conoce el host de
    trading; `momentum_hunter` no importa este paquete (hasta que una
    fuente se apruebe, y entonces será un PR aparte).

Horas: se guardan en UTC (aware). Al imprimir, `tiempo.fmt_utc_mty`
muestra UTC y Monterrey (UTC−6, sin horario de verano).
"""

from __future__ import annotations

from fuentes.comun import FALTANTE, ErrorFuente, Faltante, es_faltante

__all__ = ["FALTANTE", "ErrorFuente", "Faltante", "es_faltante"]
