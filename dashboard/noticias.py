"""Página "Noticias leídas" (noticias.html): auditoría de solo lectura.

Muestra qué titulares leyó el escaneo del hunter en su última corrida y
por qué cada acción pasó o no el filtro de catalizador. La fuente es
`momentum_hunter/noticias_leidas.json` en el directorio de estado (lo
escribe `momentum_hunter.noticias_leidas` al terminar la lectura de
noticias). No es un feed ni es en vivo: cambia cuando termina un escaneo.

Reglas:
  - Solo lectura: no pide nada a Yahoo ni a nadie; lee un archivo local.
  - Un dato que falta se muestra "sin dato", nunca como 0 ni como
    "sin catalizador".
  - Archivo ausente, vacío o corrupto: la página dice eso y el panel
    principal sigue igual (`build_dashboard.main` la genera aparte).
  - Solo links http(s): cualquier otro esquema se muestra como texto.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from momentum_hunter.noticias_leidas import (
    CON_CATALIZADOR,
    ERROR_LECTURA,
    MOTIVOS_CASI,
    SIN_CATALIZADOR,
    SIN_DATO,
    SIN_NOTICIAS,
)
from momentum_hunter.catalysts.keyword_rechazos import MOTIVO_SIN_KEYWORD

NOMBRE = "noticias.html"
SIN = "sin dato"

RESULTADOS = {
    CON_CATALIZADOR: "Con catalizador",
    SIN_CATALIZADOR: "Sin catalizador",
    SIN_NOTICIAS: "Sin noticias",
    ERROR_LECTURA: "Error al leer",
    SIN_DATO: SIN,
}
MOTIVOS = {
    "sin_ancla": "keyword, pero el titular no nombra a la empresa (ancla)",
    "fuera_ventana": "keyword, pero fuera de la ventana de días",
    "rumor_sin_fuentes": "rumor sin suficientes fuentes distintas",
    MOTIVO_SIN_KEYWORD: "ninguna keyword coincidió",
}
# Orden de la lista: lo que pasó, lo que casi pasa, lo que no tuvo keyword...
ORDEN = {"con": 0, "casi": 1, "sinkw": 2, SIN_NOTICIAS: 3, ERROR_LECTURA: 4}


def esc(v) -> str:
    return escape("" if v is None else str(v), quote=True)


def cargar(ruta: Path) -> tuple[list[dict], str | None]:
    """(corridas, problema). Nunca lanza: un archivo ausente, vacío o
    ilegible devuelve [] y la razón para mostrarla."""
    try:
        texto = Path(ruta).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], "Todavía no hay registro: el escaneo no ha terminado ninguna lectura de noticias con esta versión."
    except OSError as ex:
        return [], f"No se pudo leer el registro ({type(ex).__name__})."
    if not texto.strip():
        return [], "El registro está vacío."
    try:
        datos = json.loads(texto)
    except ValueError:
        return [], "El registro está corrupto (JSON ilegible)."
    corridas = datos.get("corridas") if isinstance(datos, dict) else None
    if not isinstance(corridas, list):
        return [], "El registro no tiene el formato esperado."
    corridas = [c for c in corridas if isinstance(c, dict)]
    if not corridas:
        return [], "El registro no tiene ninguna corrida."
    return corridas, None


def _momento(texto) -> datetime | None:
    if not isinstance(texto, str) or not texto.strip():
        return None
    try:
        dt = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def _hora(dt: datetime | None, tz, con_fecha: bool = True) -> str:
    if dt is None:
        return SIN
    u = dt.astimezone(timezone.utc)
    txt = f"{u:%Y-%m-%d %H:%M} UTC" if con_fecha else f"{u:%H:%M} UTC"
    if str(tz) != "UTC":
        txt += f" ({dt.astimezone(tz):%H:%M} {tz})"
    return txt


def hace(ahora: datetime, dt: datetime | None) -> str:
    if dt is None:
        return SIN
    minutos = int((ahora - dt).total_seconds() // 60)
    if minutos < 1:
        return "hace menos de 1 min"
    if minutos < 60:
        return f"hace {minutos} min"
    h, m = divmod(minutos, 60)
    return f"hace {h} h {m} min"


def grupo(fila: dict) -> str:
    """Clave de filtro: "con", "casi", "sinkw", o el resultado tal cual."""
    r, m = fila.get("resultado"), fila.get("motivo")
    if r == CON_CATALIZADOR:
        return "con"
    if r == SIN_CATALIZADOR and m in MOTIVOS_CASI:
        return "casi"
    if r == SIN_CATALIZADOR and m == MOTIVO_SIN_KEYWORD:
        return "sinkw"
    return r if r in (SIN_NOTICIAS, ERROR_LECTURA) else SIN_DATO


def casi_pasan(acciones: list[dict]) -> list[dict]:
    return [a for a in acciones if isinstance(a, dict) and grupo(a) == "casi"]


def sin_keyword(acciones: list[dict]) -> list[dict]:
    return [a for a in acciones if isinstance(a, dict) and grupo(a) == "sinkw"]


def _link(url) -> str:
    if isinstance(url, str) and url.strip().startswith(("https://", "http://")):
        return f'<a href="{esc(url.strip())}" target="_blank" rel="noopener noreferrer">abrir</a>'
    return f'<span class="gris">{"sin link" if url is None else "link no válido"}</span>'


def _keyword(fila: dict) -> str:
    if "keyword" not in fila:
        return SIN
    kw = fila.get("keyword")
    if kw:
        tipo = fila.get("tipo")
        return f"{kw} ({tipo})" if tipo else str(kw)
    return "ninguna" if fila.get("resultado") in (CON_CATALIZADOR, SIN_CATALIZADOR) else "—"


def _n(fila: dict) -> str:
    n = fila.get("n_noticias")
    return str(n) if isinstance(n, int) and not isinstance(n, bool) else SIN


def _noticias_html(fila: dict, tz) -> str:
    noticias = fila.get("noticias")
    if not isinstance(noticias, list):
        return f'<p class="vacio">Detalle: {SIN}.</p>'
    if not noticias:
        return '<p class="vacio">Sin titulares.</p>'
    items = []
    for n in noticias:
        if not isinstance(n, dict):
            continue
        kw = n.get("keyword")
        kw_txt = (f"{kw} ({n.get('tipo')})" if kw and n.get("tipo") else (kw or "ninguna")) if "keyword" in n else SIN
        motivo = n.get("motivo")
        items.append(
            "<li>"
            f'<div class="tit">{esc(n.get("titular") or SIN)}</div>'
            f'<div class="meta">{esc(n.get("fuente") or SIN)} · {esc(_hora(_momento(n.get("publicada")), tz))}'
            f' · {_link(n.get("link"))}</div>'
            f'<div class="meta">keyword: <b>{esc(kw_txt)}</b>'
            f'{" · " + esc(MOTIVOS.get(motivo, motivo)) if motivo else ""}</div>'
            "</li>")
    total = fila.get("n_noticias")
    recorte = ""
    if isinstance(total, int) and total > len(items):
        recorte = f'<p class="gris">Se muestran {len(items)} de {total} titulares.</p>'
    return f'<ol class="noticias">{"".join(items)}</ol>{recorte}'


def _fila_html(fila: dict, tz) -> str:
    g = grupo(fila)
    resultado = RESULTADOS.get(fila.get("resultado"), SIN)
    motivo = fila.get("motivo")
    detalle = f' <span class="gris">· {esc(MOTIVOS.get(motivo, motivo))}</span>' if motivo else ""
    return (
        f'<details class="acc g-{esc(g)}">'
        f'<summary><span class="tk">{esc(fila.get("ticker") or SIN)}</span>'
        f'<span class="res r-{esc(g)}">{esc(resultado)}</span>'
        f'<span class="kw">{esc(_keyword(fila))}</span>'
        f'<span class="n">{esc(_n(fila))} <span class="gris">noticias</span></span></summary>'
        f'<div class="cuerpo"><p class="mono">{esc(resultado)}{detalle}</p>{_noticias_html(fila, tz)}</div>'
        "</details>")


def render(corridas: list[dict], problema: str | None, ahora: datetime, tz) -> str:
    from dashboard.build_dashboard import CSS  # mismo tema y variables que el panel

    if corridas:
        c = corridas[0]
        inicio = _momento(c.get("corrida_ts"))
        escrito = _momento(c.get("escrito_ts"))
        referencia = escrito or inicio
        acciones = [a for a in c.get("acciones") or [] if isinstance(a, dict)] \
            if isinstance(c.get("acciones"), list) else []
        acciones.sort(key=lambda a: (ORDEN.get(grupo(a), 9), str(a.get("ticker") or "")))
        n_casi, n_sinkw = len(casi_pasan(acciones)), len(sin_keyword(acciones))
        n_con = sum(1 for a in acciones if grupo(a) == "con")
        cabecera = (
            f'<div class="corrida"><div><span class="mono">Última corrida del escaneo</span>'
            f'<b>{esc(_hora(inicio, tz))}</b></div>'
            f'<div><span class="mono">Registro escrito</span><b>{esc(_hora(escrito, tz))}</b></div>'
            f'<div><span class="mono">Antigüedad</span><b id="hace" data-ts="{int(referencia.timestamp()) if referencia else ""}">'
            f'{esc(hace(ahora, referencia))}</b></div>'
            f'<div><span class="mono">Origen</span><b>{esc(c.get("fuente") or SIN)}</b></div></div>')
        filas = "".join(_fila_html(a, tz) for a in acciones) or '<p class="vacio">La corrida no anotó ninguna acción.</p>'
        cuerpo = f"""<section class="filtrable" aria-label="Acciones de la corrida">
<input type="radio" name="f" id="f-todas" checked><label for="f-todas">Todas ({len(acciones)})</label>
<input type="radio" name="f" id="f-casi"><label for="f-casi">Casi pasan ({n_casi})</label>
<input type="radio" name="f" id="f-sinkw"><label for="f-sinkw">Sin keyword ({n_sinkw})</label>
<p class="ayuda mono">Con catalizador: {n_con}. <b>Casi pasan</b>: hubo keyword pero la frenó el ancla, la ventana de días o la regla de rumores.
<b>Sin keyword</b>: tenía noticias y ninguna coincidió (sirve para buscar keywords que faltan).</p>
<div class="lista">{filas}</div></section>"""
        previas = ""
        if len(corridas) > 1:
            items = []
            for p in corridas[1:]:
                acc = [a for a in p.get("acciones") or [] if isinstance(a, dict)] if isinstance(p.get("acciones"), list) else None
                resumen = (f"{len(acc)} acciones · {sum(1 for a in acc if grupo(a) == 'con')} con catalizador · "
                           f"{len(casi_pasan(acc))} casi pasan") if acc is not None else SIN
                items.append(f"<li>{esc(_hora(_momento(p.get('corrida_ts')), tz))} · {esc(resumen)}</li>")
            previas = f'<section class="panel"><h2>Corridas anteriores (resumen)</h2><ul class="mono">{"".join(items)}</ul></section>'
    else:
        cabecera = f'<div class="corrida"><div><span class="mono">Última corrida</span><b>{SIN}</b></div></div>'
        cuerpo = ""
        previas = ""

    aviso = f'<section class="problemas" role="alert"><b>Sin registro.</b> {esc(problema)}</section>' if problema else ""
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Noticias leídas</title>
<script>try{{var _t=localStorage.getItem("tema");if(_t==="dark"||_t==="light")document.documentElement.dataset.theme=_t;}}catch(_e){{}}</script>
<style>{CSS}
.corrida{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:12px 0}}
.corrida div{{background:var(--papel);border:1px solid var(--linea);border-radius:8px;padding:10px;display:flex;flex-direction:column;gap:4px}}
.nolive{{background:var(--duda-bg);border:1px solid var(--linea);border-radius:8px;padding:10px;margin:10px 0}}
.filtrable{{display:block;min-width:0}}
input[name=f]{{position:absolute;opacity:0}}
input[name=f]+label{{display:inline-block;margin:4px 6px 4px 0;padding:6px 12px;border:1px solid var(--linea);border-radius:999px;cursor:pointer;background:var(--papel)}}
input[name=f]:checked+label{{background:var(--tinta);color:var(--papel)}}
input[name=f]:focus-visible+label{{outline:2px solid var(--acento)}}
#f-casi:checked~.lista .acc:not(.g-casi),#f-sinkw:checked~.lista .acc:not(.g-sinkw){{display:none}}
.acc{{background:var(--papel);border:1px solid var(--linea);border-radius:8px;margin:6px 0}}
.acc summary{{display:grid;grid-template-columns:5.5em 10em 1fr auto;gap:8px;align-items:center;padding:10px;cursor:pointer}}
.acc .tk{{font-weight:700;font-family:var(--mono)}}
.acc .kw{{font-family:var(--mono);font-size:.85em;overflow-wrap:anywhere}}
.res{{font-size:.85em;padding:2px 8px;border-radius:999px;background:var(--duda-bg);justify-self:start}}
.r-con{{background:var(--ok-bg);color:var(--ok-fg)}} .r-casi{{background:var(--mal-bg);color:var(--mal-fg)}}
.acc .cuerpo{{padding:0 10px 10px}}
ol.noticias{{padding-left:1.2em;margin:6px 0}} ol.noticias li{{margin:8px 0}}
.tit{{font-weight:500;overflow-wrap:anywhere}} .meta{{font-size:.85em;color:var(--gris);overflow-wrap:anywhere}}
.gris{{color:var(--gris)}} .ayuda{{font-size:.85em;color:var(--gris)}}
.volver{{color:var(--acento)}}
@media (max-width:640px){{.acc summary{{grid-template-columns:1fr auto;}} .acc .kw{{grid-column:1/-1}} .acc .n{{grid-row:1;grid-column:2}}}}
</style>
</head>
<body>
<main>
<header>
  <div><h1>Noticias leídas</h1><div class="sub"><a class="volver" href="index.html">← Panel</a> · auditoría del filtro de catalizador · solo lectura</div></div>
</header>
<div class="nolive"><b>No es en vivo.</b> Es el registro de la última corrida del escaneo del hunter (cada ~30 min en sesión, en el VPS).
Esta página solo lee ese archivo; el ejecutor no lo usa: <span class="mono">watchlist.json</span> sigue siendo el único canal.</div>
{aviso}
{cabecera}
{cuerpo}
{previas}
<section class="panel ayuda">
<p><b>Qué acciones aparecen:</b> solo aquellas a las que el escaneo les leyó noticias, es decir, las que ya pasaron precio/volumen y el tamaño. Las corridas de respaldo de GitHub Actions no escriben aquí.</p>
<p><b>Limitación conocida:</b> si Yahoo responde un error 500 que yfinance se traga (devuelve una lista vacía) y el RSS de respaldo también viene vacío, la acción aparece como “Sin noticias” aunque en realidad la fuente falló. Desde el hunter no se puede distinguir.</p>
<p><b>“sin dato”</b> significa que el registro no trae ese campo; nunca es un cero ni un “sin catalizador”.</p>
</section>
</main>
<script>(function(){{var e=document.getElementById("hace");if(!e||!e.dataset.ts)return;
function p(){{var m=Math.floor((Date.now()/1000-Number(e.dataset.ts))/60);
e.textContent=m<1?"hace menos de 1 min":(m<60?"hace "+m+" min":"hace "+Math.floor(m/60)+" h "+(m%60)+" min");}}
p();setInterval(p,30000);}})();</script>
</body>
</html>"""


def escribir(html_texto: str, salida: Path) -> Path:
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / NOMBRE
    temporal = salida / f".{NOMBRE}.tmp"
    temporal.write_text(html_texto, encoding="utf-8")
    os.replace(temporal, destino)
    return destino


def generar(ruta: Path, salida: Path, ahora: datetime, tz) -> Path:
    corridas, problema = cargar(ruta)
    return escribir(render(corridas, problema, ahora, tz), salida)
