"""Respuestas grabadas: cómo se guardan y cómo las sirven las pruebas.

Formato de un archivo en `fuentes/tests/respuestas/<fuente>/<nombre>.json`:

    {"url": "...", "params": {...}, "status": 200,
     "headers": {"Content-Type": "..."}, "texto": "<cuerpo crudo>",
     "ficticio": true, "nota": "..."}

`ficticio: true` marca las respuestas armadas a mano con el formato
público y emisores inventados (el entorno de desarrollo no llegaba a la
fuente). `python -m fuentes grabar <fuente> ...` en el VPS escribe la
real al lado, con `ficticio: false`, para compararlas y reemplazar.

`TransporteGrabado` es el `transport` de `fuentes.http.Cliente` en las
pruebas: sirve por URL (+ params) y levanta si se pide algo que no está
grabado, para que ninguna prueba salga a la red sin querer.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode

import requests

from fuentes.http import Cliente

DIR_RESPUESTAS = Path(__file__).resolve().parent / "tests" / "respuestas"


def ruta_de(fuente: str, nombre: str) -> Path:
    return DIR_RESPUESTAS / fuente / f"{nombre}.json"


def cargar(fuente: str, nombre: str) -> dict:
    return json.loads(ruta_de(fuente, nombre).read_text(encoding="utf-8"))


def guardar(fuente: str, nombre: str, url: str, params: dict | None, status: int, headers: dict,
            texto: str, ficticio: bool, nota: str = "", directorio: Path | None = None) -> Path:
    base = directorio if directorio is not None else DIR_RESPUESTAS
    ruta = base / fuente / f"{nombre}.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    reg = {"url": url, "params": params or {}, "status": status,
           "headers": {k: v for k, v in headers.items() if k.lower() in ("content-type", "date", "last-modified")},
           "texto": texto, "ficticio": ficticio, "nota": nota}
    ruta.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    return ruta


class _RespuestaFalsa:
    def __init__(self, reg: dict) -> None:
        self.status_code = reg["status"]
        self.headers = dict(reg.get("headers") or {})
        self.text = reg["texto"]

    def json(self):
        return json.loads(self.text)


class TransporteGrabado:
    """`transport` para las pruebas. `registros` es {url_con_query: dict}.
    La clave es la URL más `?`+params ordenados; sin params, la URL sola.
    `fallar` fuerza una excepción de red en esas URLs (pruebas de fail-closed)."""

    def __init__(self, registros: dict[str, dict] | None = None, fallar: set[str] | None = None) -> None:
        self.registros = dict(registros or {})
        self.fallar = set(fallar or ())
        self.pedidos: list[tuple[str, dict]] = []

    @staticmethod
    def clave(url: str, params: dict | None) -> str:
        if params:
            return url + "?" + urlencode(sorted(params.items()), doseq=True)
        return url

    def agregar(self, url: str, params: dict | None, reg: dict) -> None:
        self.registros[self.clave(url, params)] = reg

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.pedidos.append((url, dict(params or {})))
        k = self.clave(url, params)
        if k in self.fallar:
            raise requests.ConnectionError("simulado")
        if k not in self.registros:
            raise AssertionError(f"pedido no grabado: {k}")
        return _RespuestaFalsa(self.registros[k])


def grabar_get(cliente: Cliente, fuente: str, nombre: str, url: str, params: dict | None = None,
               nota: str = "", directorio: Path | None = None) -> tuple[Path, str]:
    """Hace el GET real y lo guarda con `ficticio: false`."""
    r = cliente.get(url, params)
    ruta = guardar(fuente, nombre, url, params, r.status, r.headers, r.texto, ficticio=False, nota=nota,
                   directorio=directorio)
    return ruta, r.texto


def transporte_desde(fuente: str, nombres: list[str], fallar: set[str] | None = None) -> TransporteGrabado:
    """Un transporte con las respuestas grabadas `nombres` de `fuente`."""
    t = TransporteGrabado(fallar=fallar)
    for n in nombres:
        reg = cargar(fuente, n)
        t.agregar(reg["url"], reg.get("params") or None, reg)
    return t
