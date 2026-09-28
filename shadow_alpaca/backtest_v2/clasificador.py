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
  - Una respuesta ilegible (vacía, cortada, JSON roto, claves de más o
    de menos, valor fuera de rango) se REINTENTA UNA VEZ con el mismo
    pedido más la causa (`catalizador.prompt_correccion`). Cada respuesta
    inválida, del primer o del segundo intento, queda en
    `invalidas.jsonl` con su causa y la respuesta cruda. Una noticia que
    sigue inválida tras el reintento NO se cachea (la próxima corrida la
    vuelve a pedir), y las entradas inválidas de una caché vieja se
    ignoran al cargar. El backtest del 2026-09-28 perdió el 15 % de las
    clasificaciones así: 73 en bloques de código y 81 vacías o cortadas.
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

MAX_TOKENS_RESPUESTA = 300   # 200 cortó respuestas con `tipo` largo


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
        self.ruta_invalidas = base / "invalidas.jsonl"
        self.reintentos = 0
        self.invalidas = 0
        self.llamar = llamar
        self.max_llamadas = max_llamadas
        self.llamadas = 0
        self.cache: dict[str, dict] = {}
        if self.ruta_cache.exists():
            for linea in self.ruta_cache.read_text(encoding="utf-8").splitlines():
                try:
                    fila = json.loads(linea)
                    if not fila["clasificacion"].get("valida"):
                        continue   # una inválida cacheada sería una noticia perdida para siempre
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
        c, crudo = self._llamar_y_leer(system, user, noticia, k, res, intento=1)
        if c is None:
            return cat.Clasificacion(False, motivo="error_api")
        if not c.valida:
            if self.max_llamadas is not None and self.llamadas >= self.max_llamadas:
                self._contar(res, "invalida_sin_reintento:tope_de_llamadas")
                return c
            self.reintentos += 1
            self._contar(res, "reintento")
            c2, crudo2 = self._llamar_y_leer(system, cat.prompt_correccion(user, c.motivo), noticia, k, res, intento=2)
            if c2 is None:
                return cat.Clasificacion(False, motivo="error_api")
            c, crudo = c2, crudo2
        dato = {"valida": c.valida, "nivel": c.nivel, "tipo": c.tipo, "direccion": c.direccion,
                "confianza": c.confianza, "motivo": c.motivo}
        if c.valida:
            self.cache[clave] = dato
            append_linea(self.ruta_cache, {"clave": clave, "clasificacion": dato})
            self._contar(res, f"nivel_{c.nivel}")
        else:
            self._contar(res, f"invalida_tras_reintento:{c.motivo}")
        return c

    def _llamar_y_leer(self, system: str, user: str, noticia, k, res, intento: int):
        """(clasificación, respuesta cruda) o (None, None) si la API falló.
        Audita cada llamada; registra cada respuesta inválida con su causa."""
        self.llamadas += 1
        try:
            crudo = self.llamar(system, user)
        except Exception as ex:   # noqa: BLE001 -- el TIPO, nunca el texto
            log.warning("clasificador: la llamada falló (%s)", type(ex).__name__)
            self._contar(res, "error_api")
            return None, None
        c = cat.interpretar(crudo)
        dato = {"valida": c.valida, "nivel": c.nivel, "tipo": c.tipo, "direccion": c.direccion,
                "confianza": c.confianza, "motivo": c.motivo}
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        append_linea(self.ruta_auditoria, {
            "ts": ts, "noticia_id": noticia.id, "intento": intento,
            "modelo": k.modelo, "prompt_version": k.prompt_version, "system": system, "user": user,
            "respuesta": crudo, "clasificacion": dato})
        if not c.valida:
            self.invalidas += 1
            append_linea(self.ruta_invalidas, {
                "ts": ts, "noticia_id": noticia.id, "intento": intento, "causa": c.motivo,
                "largo": len(crudo or ""), "respuesta": crudo})
            self._contar(res, f"invalida_intento_{intento}:{c.motivo}")
        return c, crudo

    @staticmethod
    def _contar(res, clave: str) -> None:
        if res is not None:
            res.clasificaciones[clave] += 1
