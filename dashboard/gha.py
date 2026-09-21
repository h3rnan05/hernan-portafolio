"""Última corrida exitosa del hunter en GitHub Actions, para el panel.

POR QUÉ. El estado "Hunter" del panel salía de la hora de la watchlist.
Pero una corrida que termina bien con 0 candidatos no cambia la watchlist,
no commitea nada, y el panel la daba por no ocurrida ("Revisar"). La única
fuente que sabe si el hunter corrió es GitHub Actions.

CÓMO. GET a la API pública de Actions (el repo es público: sin token,
sin escribir nada). Un GET cada minuto agotaría el límite anónimo de 60
peticiones por hora, así que la respuesta se cachea en disco (TTL
configurable, 5 min por defecto: el hunter corre como mucho cada 30) y
ante un 403/429 se deja de pedir un rato y se sirve la copia vieja
marcada como vieja. Sin respuesta y sin copia: "Sin datos". Nunca se
inventa una hora.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

API = "https://api.github.com"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "momentum-dashboard (solo lectura)"}
ARCHIVO = "gha_hunter.json"
ARCHIVO_PAUSA = "gha_pausa.json"
CAMPOS = ("numero", "terminada", "evento", "url")


class LimiteDePeticiones(Exception):
    """GitHub respondió 403/429: límite anónimo agotado."""


def parsear_corrida(cuerpo) -> dict | None:
    """La corrida exitosa más reciente de la respuesta de
    /actions/workflows/{workflow}/runs, o None si no hay ninguna o le
    falta un campo (un dato incompleto no es una corrida)."""
    if not isinstance(cuerpo, dict) or not isinstance(cuerpo.get("workflow_runs"), list):
        return None
    for run in cuerpo["workflow_runs"]:
        if not isinstance(run, dict) or run.get("conclusion") != "success":
            continue
        corrida = {"numero": run.get("run_number"), "terminada": run.get("updated_at"),
                   "evento": run.get("event"), "url": run.get("html_url")}
        if isinstance(corrida["numero"], int) and isinstance(corrida["terminada"], str) and corrida["terminada"]:
            return corrida
    return None


def fuente_github(repo: str, workflow: str) -> dict | None:
    """Una sola petición, sin token y sin reintentos."""
    r = requests.get(f"{API}/repos/{repo}/actions/workflows/{workflow}/runs",
                     params={"status": "success", "per_page": 1, "exclude_pull_requests": "true"},
                     headers=HEADERS, timeout=10)
    if r.status_code in (403, 429):
        raise LimiteDePeticiones(f"GitHub respondió {r.status_code}")
    r.raise_for_status()
    return parsear_corrida(r.json())


def _leer_json(ruta: Path) -> dict | None:
    try:
        crudo = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return crudo if isinstance(crudo, dict) else None


def _escribir_json(ruta: Path, datos: dict) -> None:
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_name(ruta.name + ".tmp")
        temporal.write_text(json.dumps(datos), encoding="utf-8")
        os.replace(temporal, ruta)
    except OSError:
        pass


def _fecha(valor) -> datetime | None:
    try:
        momento = datetime.fromisoformat(valor) if isinstance(valor, str) else None
    except ValueError:
        return None
    return momento if momento is not None and momento.tzinfo is not None else None


def _corrida_valida(corrida) -> bool:
    return (isinstance(corrida, dict) and isinstance(corrida.get("numero"), int)
            and isinstance(corrida.get("terminada"), str) and bool(corrida["terminada"]))


def _leer_cache(ruta: Path) -> tuple[dict | None, datetime | None]:
    crudo = _leer_json(ruta)
    if crudo is None:
        return None, None
    momento, corrida = _fecha(crudo.get("obtenido")), crudo.get("corrida")
    if momento is None or not _corrida_valida(corrida):
        return None, None
    return corrida, momento


def pausa_hasta(cache_dir: Path) -> datetime | None:
    crudo = _leer_json(cache_dir / ARCHIVO_PAUSA)
    return _fecha(crudo.get("hasta")) if crudo else None


def _pausar(cache_dir: Path, ahora: datetime, segundos: float, motivo: str) -> datetime:
    hasta = ahora + timedelta(seconds=segundos)
    _escribir_json(cache_dir / ARCHIVO_PAUSA, {"hasta": hasta.isoformat(timespec="seconds"),
                                              "desde": ahora.isoformat(timespec="seconds"),
                                              "motivo": motivo})
    return hasta


def _con_cache(corrida_cache, obtenido_cache, ahora, ttl_seg, error) -> dict:
    if corrida_cache is None:
        return {"corrida": None, "obtenido": None, "origen": None, "error": error}
    vigente = (ahora - obtenido_cache).total_seconds() <= ttl_seg
    return {"corrida": corrida_cache, "obtenido": obtenido_cache,
            "origen": "cache" if vigente else "cache vencida", "error": error}


def obtener(repo: str, workflow: str, ahora: datetime, cache_dir: Path, ttl_seg: float,
            fuente=fuente_github, pausa_seg: float = 900.0) -> dict:
    """{"corrida": {"numero", "terminada", "evento", "url"} | None,
        "obtenido": datetime | None,
        "origen": "github" | "cache" | "cache vencida" | None, "error": str | None}

    Mismo contrato que dashboard.velas.obtener: caché vigente → no se
    pregunta; pausa activa → se sirve lo que haya, marcado; si la API
    falla y había copia, se devuelve la copia CON el error."""
    ruta = cache_dir / ARCHIVO
    corrida_cache, obtenido_cache = _leer_cache(ruta)
    if corrida_cache is not None and (ahora - obtenido_cache).total_seconds() <= ttl_seg:
        return _con_cache(corrida_cache, obtenido_cache, ahora, ttl_seg, None)

    hasta = pausa_hasta(cache_dir)
    if hasta is not None and ahora < hasta:
        return _con_cache(corrida_cache, obtenido_cache, ahora, ttl_seg,
                          f"GitHub limitó peticiones: sin preguntar hasta las {hasta:%H:%M} UTC")
    try:
        corrida = fuente(repo, workflow)
    except LimiteDePeticiones:
        hasta = _pausar(cache_dir, ahora, pausa_seg, "limite")
        return _con_cache(corrida_cache, obtenido_cache, ahora, ttl_seg,
                          f"GitHub limitó peticiones: sin preguntar hasta las {hasta:%H:%M} UTC")
    except Exception as exc:  # red: nunca tumba el panel
        return _con_cache(corrida_cache, obtenido_cache, ahora, ttl_seg,
                          f"GitHub Actions no respondió ({type(exc).__name__})")
    if _corrida_valida(corrida):
        _escribir_json(ruta, {"obtenido": ahora.isoformat(timespec="seconds"), "corrida": corrida})
        return {"corrida": corrida, "obtenido": ahora, "origen": "github", "error": None}
    return _con_cache(corrida_cache, obtenido_cache, ahora, ttl_seg,
                      "GitHub Actions no reporta ninguna corrida exitosa")
