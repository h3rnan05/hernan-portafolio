"""Noticias de Alpaca (Benzinga) EN SOMBRA frente a Yahoo (2026-10-01).

POR QUÉ. Precios y velas ya salen del feed SIP de Alpaca. Las noticias
siguen en Yahoo, y son una consulta por acción: eso es lo que impide
escanear el universo entero sin que Yahoo nos bloquee. El plan de datos
de Alpaca trae su propio feed de noticias (`/v1beta1/news`, Benzinga).
Antes de que decida algo hay que saber si detecta los mismos
catalizadores, porque cambiar la fuente cambia qué entra al embudo.

QUÉ HACE. En cada escaneo pide en LOTE las noticias de los símbolos que
pasaron los filtros de universo (pocas llamadas, no una por acción),
les aplica el MISMO `detectar_catalizador` y el MISMO `ancla_ok` que a
Yahoo, y anota una línea por acción en
`$MOMENTUM_ESTADO_DIR/sombra_noticias/<fecha>.jsonl`: cuántos titulares
trajo cada fuente y qué catalizador habría salido de cada una.

QUÉ NO HACE. No cambia ningún candidato, ningún catalizador ni ninguna
decisión: el hunter sigue decidiendo con Yahoo. Cualquier fallo (sin
llaves, red, 429 tras los reintentos) se anota como `error` en el
resumen de la corrida y el escaneo sigue igual. Solo lee el host de
DATOS; nunca el de trading.

Encendida por defecto. `MOMENTUM_NOTICIAS_SOMBRA=0` la apaga.

Limitación conocida: Benzinga es UNA fuente. Un "rumor" pide
`fuentes_minimas_rumor` fuentes distintas, así que por Alpaca casi nunca
se confirma uno. Eso no es un error de la comparación: es lo que pasaría
si se cambiara la fuente, y por eso se mide.

`python -m momentum_hunter.catalysts.sombra_noticias --desde AAAA-MM-DD`
imprime el resumen acumulado.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from momentum_hunter.catalysts.ancla import ancla_ok
from momentum_hunter.catalysts.detector import Titular, detectar_catalizador
from momentum_hunter.config import MomentumConfig
from momentum_hunter.rutas_estado import resolver

log = logging.getLogger("momentum_hunter.sombra_noticias")

ENV_ACTIVA = "MOMENTUM_NOTICIAS_SOMBRA"
RELATIVO = "sombra_noticias"
RUTA_NOTICIAS = "/v1beta1/news"
LOTE = 50              # símbolos por pedido
LIMITE_PAGINA = 50     # máximo que acepta el endpoint
MAX_PAGINAS_LOTE = 20  # tope de paginación por lote: un lote truncado se marca, no se rellena
TEXTO_MAX = 160


def activa() -> bool:
    return os.environ.get(ENV_ACTIVA, "1").strip() != "0"


def directorio() -> Path:
    return resolver(RELATIVO, es_dir=True)


def _titular(articulo: dict) -> Titular | None:
    texto = articulo.get("headline")
    if not isinstance(texto, str) or not texto.strip():
        return None
    fuente = articulo.get("source")
    fuente = fuente.strip() if isinstance(fuente, str) and fuente.strip() else "desconocida"
    fecha = articulo.get("created_at")
    fecha = fecha if isinstance(fecha, str) and fecha else None
    url = articulo.get("url")
    link = url if isinstance(url, str) and url.startswith(("https://", "http://")) else None
    return Titular(texto.strip(), fuente, fecha, link)


class NoticiasAlpaca:
    """Titulares por símbolo, precargados en lote. `cliente` es cualquier
    objeto con `_get(path, params) -> dict` (el `AlpacaProvider` del
    hunter: llaves del entorno, reintentos y backoff). Un símbolo sin
    artículos queda con lista vacía; uno cuyo lote falló queda AUSENTE
    (no se sabe, no es "sin noticias")."""

    def __init__(self, cliente=None) -> None:
        if cliente is None:
            from momentum_hunter.data.alpaca_datos import AlpacaProvider
            cliente = AlpacaProvider(feed="sip")
        self._cliente = cliente
        self._por_ticker: dict[str, list[Titular]] = {}
        self.errores: Counter = Counter()
        self.pedidos = 0

    def precargar(self, tickers: list[str], desde: datetime, hasta: datetime) -> None:
        limpios = list(dict.fromkeys(t for t in tickers if isinstance(t, str) and t.strip()))
        for i in range(0, len(limpios), LOTE):
            lote = limpios[i:i + LOTE]
            try:
                articulos = self._lote(lote, desde, hasta)
            except Exception as ex:  # noqa: BLE001 -- la sombra nunca tumba el escaneo
                codigo = getattr(ex, "codigo", None) or type(ex).__name__
                self.errores[str(codigo)] += 1
                log.warning("sombra noticias: lote de %d símbolos sin respuesta (%s)", len(lote), codigo)
                continue
            pedidos = set(lote)
            for t in lote:
                self._por_ticker[t] = []
            for art in articulos:
                tit = _titular(art)
                simbolos = art.get("symbols")
                if tit is None or not isinstance(simbolos, list):
                    continue
                for s in simbolos:
                    if s in pedidos:
                        self._por_ticker[s].append(tit)

    def _lote(self, lote: list[str], desde: datetime, hasta: datetime) -> list[dict]:
        params = {
            "symbols": ",".join(lote),
            "start": desde.astimezone(UTC).isoformat(timespec="seconds"),
            "end": hasta.astimezone(UTC).isoformat(timespec="seconds"),
            "limit": LIMITE_PAGINA,
            "sort": "desc",
            "include_content": "false",
        }
        out: list[dict] = []
        token = None
        for _ in range(MAX_PAGINAS_LOTE):
            q = dict(params)
            if token:
                q["page_token"] = token
            self.pedidos += 1
            cuerpo = self._cliente._get(RUTA_NOTICIAS, q)
            noticias = cuerpo.get("news") if isinstance(cuerpo, dict) else None
            if not isinstance(noticias, list):
                raise ValueError("cuerpo")
            out.extend(n for n in noticias if isinstance(n, dict))
            token = cuerpo.get("next_page_token")
            if not token:
                return out
        # Más de MAX_PAGINAS_LOTE páginas: el lote queda truncado. Se usa
        # lo que llegó (lo más reciente primero) y se cuenta aparte.
        self.errores["truncado"] += 1
        return out

    def tiene(self, ticker: str) -> bool:
        return ticker in self._por_ticker

    def titulares(self, ticker: str) -> list[Titular]:
        return list(self._por_ticker.get(ticker, []))


class Sombra:
    """Una por escaneo. `anotar` no hace red: compara contra lo
    precargado. `cerrar` escribe el JSONL (salvo dry-run)."""

    def __init__(self, cfg: MomentumConfig, ahora: datetime, fuente: NoticiasAlpaca | None = None) -> None:
        self.cfg = cfg
        self.ahora = ahora
        self.fuente = fuente if fuente is not None else NoticiasAlpaca()
        self.filas: list[dict] = []

    def precargar(self, tickers: list[str]) -> None:
        # +1 día de margen: la ventana del detector tolera un día de
        # desfase horario (`dentro_de_ventana`).
        desde = self.ahora - timedelta(days=self.cfg.dias_ventana_catalizador + 1)
        try:
            self.fuente.precargar(tickers, desde, self.ahora)
        except Exception as ex:  # noqa: BLE001
            self.fuente.errores[type(ex).__name__] += 1

    def anotar(self, ticker: str, nombre: str | None, titulares_yahoo: list[Titular],
               catalizador_yahoo) -> None:
        try:
            fila = {"ticker": ticker, "n_yahoo": len(titulares_yahoo),
                    "cat_yahoo": getattr(catalizador_yahoo, "tipo", None)}
            if not self.fuente.tiene(ticker):
                fila.update(n_alpaca=None, cat_alpaca=None, sin_respuesta=True)
            else:
                tits = self.fuente.titulares(ticker)
                cat = detectar_catalizador(tits, self.cfg, hoy=self.ahora.date())
                if cat is not None and not ancla_ok(ticker, nombre, cat.titular)[0]:
                    cat = None
                fila.update(n_alpaca=len(tits), cat_alpaca=cat.tipo if cat else None,
                            titular_alpaca=cat.titular[:TEXTO_MAX] if cat else None)
            self.filas.append(fila)
        except Exception as ex:  # noqa: BLE001
            self.fuente.errores[f"anotar:{type(ex).__name__}"] += 1

    def resumen(self) -> dict:
        comparadas = [f for f in self.filas if not f.get("sin_respuesta")]
        y = sum(1 for f in comparadas if f["cat_yahoo"])
        a = sum(1 for f in comparadas if f["cat_alpaca"])
        ambos = sum(1 for f in comparadas if f["cat_yahoo"] and f["cat_alpaca"])
        return {
            "tipo": "resumen", "ts": self.ahora.isoformat(timespec="seconds"),
            "acciones": len(self.filas), "comparadas": len(comparadas),
            "con_noticia_yahoo": sum(1 for f in comparadas if f["n_yahoo"]),
            "con_noticia_alpaca": sum(1 for f in comparadas if f["n_alpaca"]),
            "catalizador_yahoo": y, "catalizador_alpaca": a, "ambos": ambos,
            "solo_yahoo": y - ambos, "solo_alpaca": a - ambos,
            "mismo_tipo": sum(1 for f in comparadas
                              if f["cat_yahoo"] and f["cat_yahoo"] == f["cat_alpaca"]),
            "pedidos": self.fuente.pedidos, "errores": dict(self.fuente.errores),
        }

    def cerrar(self, persistir: bool = True) -> dict:
        res = self.resumen()
        log.info("sombra noticias: %s", json.dumps(res, ensure_ascii=False))
        if not persistir:
            return res
        try:
            d = directorio()
            d.mkdir(parents=True, exist_ok=True)
            ruta = d / f"{self.ahora.date().isoformat()}.jsonl"
            with ruta.open("a", encoding="utf-8") as fh:
                for f in self.filas:
                    fh.write(json.dumps({"ts": res["ts"], **f}, ensure_ascii=False) + "\n")
                fh.write(json.dumps(res, ensure_ascii=False) + "\n")
        except OSError as ex:
            log.warning("sombra noticias: no se pudo escribir (%s)", type(ex).__name__)
        return res


def nueva(cfg: MomentumConfig, ahora: datetime) -> Sombra | None:
    """La sombra de esta corrida, o None si está apagada o no se pudo
    armar. Nunca lanza."""
    if not activa():
        return None
    try:
        return Sombra(cfg, ahora)
    except Exception as ex:  # noqa: BLE001
        log.warning("sombra noticias: no se pudo armar (%s)", type(ex).__name__)
        return None


def acumular(desde: date, hasta: date, d: Path | None = None) -> dict:
    d = d or directorio()
    total: Counter = Counter()
    corridas = 0
    for ruta in sorted(d.glob("*.jsonl")):
        try:
            dia = date.fromisoformat(ruta.stem)
        except ValueError:
            continue
        if not desde <= dia <= hasta:
            continue
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(linea)
            except ValueError:
                continue
            if r.get("tipo") != "resumen":
                continue
            corridas += 1
            for k in ("comparadas", "catalizador_yahoo", "catalizador_alpaca", "ambos",
                      "solo_yahoo", "solo_alpaca", "mismo_tipo", "pedidos"):
                v = r.get(k)
                if isinstance(v, int):
                    total[k] += v
    return {"corridas": corridas, **dict(total)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Resumen de la sombra de noticias (Alpaca vs Yahoo).")
    hoy = datetime.now(UTC).date()
    p.add_argument("--desde", default=(hoy - timedelta(days=6)).isoformat())
    p.add_argument("--hasta", default=hoy.isoformat())
    a = p.parse_args(argv)
    r = acumular(date.fromisoformat(a.desde), date.fromisoformat(a.hasta))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
