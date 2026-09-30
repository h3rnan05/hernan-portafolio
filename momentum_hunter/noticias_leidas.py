"""Registro de auditoría: qué noticias leyó el escaneo y por qué cada
acción pasó o no el filtro de catalizador. Solo lo lee el panel
(`dashboard/noticias.py` → `noticias.html`).

POR QUÉ. El embudo dice cuántas acciones murieron en el catalizador,
pero no cuáles ni con qué titulares. Para buscar keywords que faltan
(ALKS "Phase 1b", LFMD "Secures AT&T Partnership") hubo que reconstruir
los titulares a mano.

Lo que NO hace, a propósito:
  - No pide nada: usa los titulares que el escaneo ya tenía en memoria
    (texto, fuente, fecha y link de la MISMA respuesta de Yahoo).
  - No decide nada: no toca `detectar_catalizador`, `CATALYST_KEYWORDS`,
    la ventana, la regla de rumores ni el ancla. Relee los mismos
    predicados para explicar el resultado.
  - No es un canal hacia el ejecutor: `watchlist.json` sigue siendo el
    único. El ejecutor no lee este archivo (hay una prueba).
  - No rompe la corrida: `anotar_seguro` y `escribir` nunca lanzan. Si
    algo falla, queda en el log y el escaneo sigue igual.

Archivo: `momentum_hunter/noticias_leidas.json` en `MOMENTUM_ESTADO_DIR`
(el mismo directorio que la watchlist y la telemetría, fuera de git).
Guarda las últimas `MAX_CORRIDAS` corridas, la más nueva primero, y
como mucho `MAX_NOTICIAS_POR_ACCION` titulares por acción (con el total
real al lado), para que no crezca sin control.

Resultado por acción:
  con_catalizador   el detector confirmó y el ancla lo dejó pasar
  sin_catalizador   había titulares y ninguno terminó en catalizador;
                    `motivo`: sin_ancla | fuera_ventana |
                    rumor_sin_fuentes (hubo keyword pero otra regla la
                    frenó: "casi pasan") o sin_keyword (ninguno coincidió)
  sin_noticias      la fuente respondió sin titulares
  error_lectura     la fuente de noticias falló
  sin_dato          no se sabe cómo terminó la lectura (no se inventa)
Limitación aceptada: si yfinance se traga un HTTP 500 y devuelve [] y el
RSS de respaldo también viene vacío, se ve como `sin_noticias`.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime

from momentum_hunter.catalysts.detector import (
    CATALYST_KEYWORDS,
    ORDEN_PRIORIDAD,
    Titular,
    dentro_de_ventana,
)
from momentum_hunter.catalysts.keyword_rechazos import (
    MOTIVO_FUERA_VENTANA,
    MOTIVO_RUMOR_SIN_FUENTES,
    MOTIVO_SIN_KEYWORD,
    explicar_rechazos_keyword,
)
from momentum_hunter.config import MomentumConfig
from momentum_hunter.rutas_estado import RutaEstado

log = logging.getLogger(__name__)

ARCHIVO = RutaEstado("momentum_hunter/noticias_leidas.json")
VERSION = 1
MAX_CORRIDAS = 3
MAX_NOTICIAS_POR_ACCION = 25

CON_CATALIZADOR = "con_catalizador"
SIN_CATALIZADOR = "sin_catalizador"
SIN_NOTICIAS = "sin_noticias"
ERROR_LECTURA = "error_lectura"
SIN_DATO = "sin_dato"
MOTIVO_SIN_ANCLA = "sin_ancla"
# Hubo keyword y la frenó otra regla: lo que el panel llama "casi pasan".
MOTIVOS_CASI = (MOTIVO_SIN_ANCLA, MOTIVO_FUERA_VENTANA, MOTIVO_RUMOR_SIN_FUENTES)


def keyword_de(texto: str) -> tuple[str, str] | None:
    """(tipo, frase) de la PRIMERA keyword que coincide, con el mismo
    recorrido que `clasificar_titular` (orden de prioridad, substring en
    minúsculas). Solo lee `CATALYST_KEYWORDS`; el tipo siempre coincide
    con el de `clasificar_titular` (hay prueba)."""
    bajo = (texto or "").lower()
    for tipo in ORDEN_PRIORIDAD:
        for kw in CATALYST_KEYWORDS[tipo]:
            if kw in bajo:
                return tipo, kw
    return None


def _noticia(t: Titular, motivo: str | None) -> dict:
    kw = keyword_de(t.texto)
    return {
        "titular": t.texto,
        "fuente": t.fuente,
        "publicada": t.fecha,
        "link": t.link,
        "tipo": kw[0] if kw else None,
        "keyword": kw[1] if kw else None,
        "motivo": motivo,
    }


def clasificar(
    ticker: str, estado_fuente: str | None, titulares: list[Titular],
    catalizador_detector, catalizador_final, cfg: MomentumConfig,
) -> dict:
    """La fila de una acción. `catalizador_detector` es lo que devolvió
    `detectar_catalizador`; `catalizador_final`, lo que quedó después del
    ancla. Pura: no pide nada ni cambia nada."""
    fila: dict = {
        "ticker": ticker,
        "estado_fuente": estado_fuente,
        "resultado": SIN_DATO,
        "motivo": None,
        "tipo": None,
        "keyword": None,
        "n_noticias": len(titulares),
        "noticias": [],
    }
    if not titulares:
        if estado_fuente == "error":
            fila["resultado"] = ERROR_LECTURA
        elif estado_fuente in ("vacio", "yfinance", "rss"):
            fila["resultado"] = SIN_NOTICIAS
        return fila

    motivos: dict[str, str] = {}
    if catalizador_detector is None:
        # Mismo "hoy" por defecto que el detector (date.today()).
        for r in explicar_rechazos_keyword(ticker, titulares, cfg):
            motivos.setdefault(r["titular"], r["motivo"])
    fila["noticias"] = [_noticia(t, motivos.get(t.texto)) for t in titulares[:MAX_NOTICIAS_POR_ACCION]]

    if catalizador_final is not None:
        fila["resultado"] = CON_CATALIZADOR
        principal = catalizador_final.titular
    elif catalizador_detector is not None:
        fila["resultado"] = SIN_CATALIZADOR
        fila["motivo"] = MOTIVO_SIN_ANCLA
        principal = catalizador_detector.titular
    else:
        fila["resultado"] = SIN_CATALIZADOR
        con_kw = [t for t in titulares if keyword_de(t.texto)]
        if not con_kw:
            fila["motivo"] = MOTIVO_SIN_KEYWORD
            return fila
        # Hubo keyword: si alguna estaba en ventana y aun así no confirmó,
        # es un rumor sin fuentes; si no, todas estaban fuera de ventana.
        vigentes = [t for t in con_kw if dentro_de_ventana(t.fecha, date.today(), cfg.dias_ventana_catalizador)]
        fila["motivo"] = MOTIVO_RUMOR_SIN_FUENTES if vigentes else MOTIVO_FUERA_VENTANA
        principal = (vigentes or con_kw)[0].texto
    kw = keyword_de(principal or "")
    if kw:
        fila["tipo"], fila["keyword"] = kw
    return fila


class Registro:
    """Junta las filas de UNA corrida en memoria."""

    def __init__(self, inicio: datetime, fuente: str | None = None) -> None:
        self.corrida: dict = {
            "version": VERSION,
            "corrida_ts": inicio.isoformat(timespec="seconds"),
            "fuente": fuente,
            "acciones": [],
        }

    def anotar_seguro(self, *args, **kwargs) -> None:
        """`clasificar` + guardar la fila. Nunca lanza: registrar no es
        un requisito para escanear."""
        try:
            self.corrida["acciones"].append(clasificar(*args, **kwargs))
        except Exception as ex:  # noqa: BLE001
            log.warning("noticias_leidas: no se pudo anotar (%s)", type(ex).__name__)


def _leer_previas(ruta) -> list[dict]:
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as ex:
        log.warning("noticias_leidas: archivo previo ilegible, se reemplaza (%s)", type(ex).__name__)
        return []
    corridas = datos.get("corridas") if isinstance(datos, dict) else None
    return [c for c in corridas if isinstance(c, dict)] if isinstance(corridas, list) else []


def escribir(registro: Registro, ruta=ARCHIVO, ahora: datetime | None = None,
             max_corridas: int = MAX_CORRIDAS) -> bool:
    """Antepone la corrida y conserva las últimas `max_corridas`.
    Escritura atómica (temporal + replace). Devuelve False si falló;
    nunca lanza."""
    try:
        destino = os.fspath(ruta)
        corrida = dict(registro.corrida)
        if ahora is not None:
            corrida["escrito_ts"] = ahora.isoformat(timespec="seconds")
        corridas = [corrida, *_leer_previas(destino)][:max(1, max_corridas)]
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        temporal = destino + ".tmp"
        with open(temporal, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "corridas": corridas}, f, ensure_ascii=False)
        os.replace(temporal, destino)
        return True
    except Exception as ex:  # noqa: BLE001
        log.warning("noticias_leidas: no se pudo escribir (%s)", type(ex).__name__)
        return False
