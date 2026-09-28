"""Clasificación de catalizadores con la IA para el backtest v2.

La lógica (prompt, validación estricta, veto) es la de
`estrategia_v2.catalizador`; aquí solo está la llamada al modelo, la
caché y la auditoría:

  - Caché por (id de noticia, modelo, versión de prompt): una noticia se
    clasifica una sola vez aunque aparezca en varios días o corridas.
  - Auditoría: cada llamada deja una línea JSONL con el prompt completo,
    la respuesta cruda, la clasificación interpretada, el modelo y la hora
    (UTC). Fuera del repo, en el almacén.
  - Tope de llamadas: pasado el tope, la noticia queda "sin clasificar"
    (no operable) y se cuenta. No se adivina un nivel.
  - Un error de la API (red, 429, 5xx) deja la noticia sin clasificar y
    NO se cachea: la próxima corrida la reintenta.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from estrategia_v2 import catalizador as cat
from estrategia_v2.config import ConfigV2

from shadow_alpaca.jsonl_log import append_linea, exigir_directorio_aislado

log = logging.getLogger("shadow_alpaca.backtest_v2.clasificador")

MAX_TOKENS_RESPUESTA = 200


def llamada_anthropic(modelo: str):
    """Función (system, user) -> texto con el SDK de Anthropic. La clave
    sale del entorno (`ANTHROPIC_API_KEY`); nunca se registra."""
    import anthropic

    cliente = anthropic.Anthropic()

    def _llamar(system: str, user: str) -> str:
        r = cliente.messages.create(model=modelo, max_tokens=MAX_TOKENS_RESPUESTA, system=system,
                                    messages=[{"role": "user", "content": user}])
        return "".join(getattr(b, "text", "") for b in r.content)
    return _llamar


class Clasificador:
    def __init__(self, almacen: Path, llamar=None, max_llamadas: int | None = None) -> None:
        base = exigir_directorio_aislado(almacen) / "v2" / "clasificaciones"
        base.mkdir(parents=True, exist_ok=True)
        self.ruta_cache = base / "cache.jsonl"
        self.ruta_auditoria = base / "auditoria.jsonl"
        self.llamar = llamar
        self.max_llamadas = max_llamadas
        self.llamadas = 0
        self.cache: dict[str, dict] = {}
        if self.ruta_cache.exists():
            for linea in self.ruta_cache.read_text(encoding="utf-8").splitlines():
                try:
                    fila = json.loads(linea)
                    self.cache[fila["clave"]] = fila["clasificacion"]
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue

    def clasificar(self, noticia, cfg: ConfigV2, res=None) -> cat.Clasificacion:
        k = cfg.catalizador
        clave = f"{noticia.id}|{k.modelo}|{k.prompt_version}"
        if clave in self.cache:
            return cat.Clasificacion(**self.cache[clave])
        if self.llamar is None:
            self._contar(res, "sin_clasificador")
            return cat.Clasificacion(False, motivo="sin_clasificador")
        if self.max_llamadas is not None and self.llamadas >= self.max_llamadas:
            self._contar(res, "tope_de_llamadas")
            return cat.Clasificacion(False, motivo="tope_de_llamadas")
        system, user = cat.prompts(noticia.titular, noticia.resumen, k)
        self.llamadas += 1
        try:
            crudo = self.llamar(system, user)
        except Exception as ex:   # noqa: BLE001 -- el TIPO, nunca el texto
            log.warning("clasificador: la llamada falló (%s)", type(ex).__name__)
            self._contar(res, "error_api")
            return cat.Clasificacion(False, motivo=f"error_api:{type(ex).__name__}")
        c = cat.interpretar(crudo)
        dato = {"valida": c.valida, "nivel": c.nivel, "tipo": c.tipo, "direccion": c.direccion,
                "confianza": c.confianza, "motivo": c.motivo}
        self.cache[clave] = dato
        append_linea(self.ruta_cache, {"clave": clave, "clasificacion": dato})
        append_linea(self.ruta_auditoria, {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"), "noticia_id": noticia.id,
            "modelo": k.modelo, "prompt_version": k.prompt_version, "system": system, "user": user,
            "respuesta": crudo, "clasificacion": dato})
        self._contar(res, f"nivel_{c.nivel}" if c.valida else f"invalida:{c.motivo}")
        return c

    @staticmethod
    def _contar(res, clave: str) -> None:
        if res is not None:
            res.clasificaciones[clave] += 1
