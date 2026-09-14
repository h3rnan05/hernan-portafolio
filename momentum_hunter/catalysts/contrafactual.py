"""Contrafactual SHADOW: PRE vs Tanda 1 (producción intacta en este PR).

El expand de producción es el #121. Acá se miden números y se deja
la lista escrita. `run.py` no importa este módulo.

Uso: python -m momentum_hunter.catalysts.contrafactual
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from momentum_hunter.audit import DIR_AUDITORIA
from momentum_hunter.catalysts.ancla import ancla_ok
from momentum_hunter.catalysts.detector import CATALYST_KEYWORDS, clasificar_titular
from momentum_hunter.catalysts.shadow import (
    CATALYST_KEYWORDS_PRE_TANDA1,
    WAVE1_VERSION,
    clasificar_sombra,
    cargar_frases_propuestas,
    keywords_propuestos,
)
from momentum_hunter.telemetria import DIR_TELEMETRIA

DIR_CATALYSTS = Path(__file__).resolve().parent
DIR_MUESTRAS = DIR_CATALYSTS / "muestras"
PATH_YAHOO = DIR_MUESTRAS / "yahoo_titulares_2026-09-14.json"
PATH_CURADAS = DIR_MUESTRAS / "wave1_curadas.json"
PATH_UNIVERSO = Path(__file__).resolve().parent.parent / "universo_cache.json"
PATH_WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.json"
PATH_REPORTE = DIR_CATALYSTS / "WAVE1_CONTRAFACTUAL.md"

# El dueño lo dijo en unidades del embudo: mediana histórica ~3 cats
# por escaneo, ~40 = demasiado suelto. No se recalibra acá.
MEDIANA_HISTORICA_REF = 3
UMBRAL_DEMASIADO_SUELTO = 40

# Medición del Buscador (dueño, 2026-09-14) sobre Tanda 1.
# No es este snapshot: se cita como referencia, no se recompute.
BUSCADOR_N_TICKERS = 50
BUSCADOR_TICKERS_PRE = 5
BUSCADOR_TICKERS_POST = 7
BUSCADOR_N_TITULOS = 230
BUSCADOR_TITULOS_PRE = 55
BUSCADOR_TITULOS_POST = 57


@dataclass
class Fila:
    ticker: str
    titular: str
    origen: str
    fuente: str | None = None
    fecha: str | None = None
    nombre: str | None = None


@dataclass
class ResultadoCorpus:
    nombre: str
    n_titulares: int
    n_tickers: int
    cats_titular_actual: int
    cats_titular_propuesto: int
    cats_ticker_actual: int
    cats_ticker_propuesto: int
    cats_ticker_ancla_actual: int
    cats_ticker_ancla_propuesto: int
    nuevos: list[tuple[str, str, str, str]] = field(default_factory=list)
    bloqueados_ancla: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    cambios_tipo: list[tuple[str, str, str, str, str]] = field(default_factory=list)


def _nombres_universo(path: Path = PATH_UNIVERSO) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for s in data.get("simbolos") or []:
        t = s.get("ticker")
        if t:
            out[t] = s.get("nombre") or ""
    return out


def cargar_auditoria(directorio: Path = DIR_AUDITORIA) -> list[Fila]:
    """Titulares que YA calificaron -- control: el delta nuevo debe ser
    ~0. Un delta grande acá significaría que re-clasificamos lo ya
    visto, no que encontramos huecos."""
    filas: list[Fila] = []
    vistos: set[tuple[str, str]] = set()
    for path in sorted(directorio.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for corrida in data.get("corridas") or []:
            for cand in corrida.get("candidatos") or []:
                cat = cand.get("catalizador") or {}
                titular = cat.get("titular")
                ticker = cand.get("ticker") or ""
                if not titular or not ticker:
                    continue
                clave = (ticker, titular)
                if clave in vistos:
                    continue
                vistos.add(clave)
                filas.append(Fila(
                    ticker=ticker,
                    titular=titular,
                    origen="auditoria",
                    fuente=cat.get("fuente"),
                    fecha=cat.get("fecha"),
                    nombre=cand.get("nombre"),
                ))
    return filas


def cargar_json_titulares(path: Path, origen_default: str) -> list[Fila]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data["titulares"] if isinstance(data, dict) and "titulares" in data else data
    filas: list[Fila] = []
    for item in items:
        filas.append(Fila(
            ticker=item.get("ticker") or "?",
            titular=item["titular"],
            origen=item.get("origen") or origen_default,
            fuente=item.get("fuente"),
            fecha=item.get("fecha"),
            nombre=item.get("nombre"),
        ))
    return filas


def _tickers_watchlist(path: Path = PATH_WATCHLIST) -> set[str]:
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {e["ticker"] for e in data.get("entradas") or [] if e.get("ticker")}


def mediana_historica_cats(directorio: Path = DIR_TELEMETRIA) -> tuple[float | None, int]:
    """embudo.con_catalizador por escaneo -- la unidad que el dueño
    comparó con ~3 y ~40. None si no hay telemetría."""
    vals: list[int] = []
    for path in sorted(directorio.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for corrida in data.get("corridas") or []:
            if corrida.get("modo") not in (None, "escaneo"):
                continue
            emb = (corrida.get("embudo") or {}).get("con_catalizador") or {}
            if isinstance(emb, dict):
                vals.append(sum(int(v) for v in emb.values()))
    if not vals:
        return None, 0
    return float(statistics.median(vals)), len(vals)


def _ticker_tiene_cat(
    filas: list[Fila],
    keywords: dict[str, tuple[str, ...]],
    usar_ancla: bool,
) -> bool:
    for f in filas:
        m = clasificar_sombra(f.titular, keywords)
        if m is None:
            continue
        if usar_ancla:
            ok, _motivo = ancla_ok(f.ticker, f.nombre, f.titular)
            if not ok:
                continue
        return True
    return False


def evaluar_corpus(
    nombre: str,
    filas: list[Fila],
    actuales: dict[str, tuple[str, ...]],
    propuestos: dict[str, tuple[str, ...]],
) -> ResultadoCorpus:
    por_ticker: dict[str, list[Fila]] = defaultdict(list)
    nuevos: list[tuple[str, str, str, str]] = []
    bloqueados: list[tuple[str, str, str, str, str]] = []
    cambios: list[tuple[str, str, str, str, str]] = []
    n_act = n_prop = 0
    vistos_titular: set[tuple[str, str]] = set()

    for f in filas:
        por_ticker[f.ticker].append(f)
        clave = (f.ticker, f.titular)
        if clave in vistos_titular:
            continue
        vistos_titular.add(clave)
        a = clasificar_sombra(f.titular, actuales)
        p = clasificar_sombra(f.titular, propuestos)
        if a is not None:
            n_act += 1
        if p is not None:
            n_prop += 1
        if p is not None and a is None:
            nuevos.append((f.ticker, f.titular, p.tipo, p.frase))
            ok, motivo = ancla_ok(f.ticker, f.nombre, f.titular)
            if not ok:
                bloqueados.append((f.ticker, f.titular, p.tipo, p.frase, motivo))
        if a is not None and p is not None and a.tipo != p.tipo:
            cambios.append((f.ticker, f.titular, a.tipo, p.tipo, p.frase))

    tickers = list(por_ticker.values())
    return ResultadoCorpus(
        nombre=nombre,
        n_titulares=len(vistos_titular),
        n_tickers=len(por_ticker),
        cats_titular_actual=n_act,
        cats_titular_propuesto=n_prop,
        cats_ticker_actual=sum(1 for ts in tickers if _ticker_tiene_cat(ts, actuales, False)),
        cats_ticker_propuesto=sum(1 for ts in tickers if _ticker_tiene_cat(ts, propuestos, False)),
        cats_ticker_ancla_actual=sum(1 for ts in tickers if _ticker_tiene_cat(ts, actuales, True)),
        cats_ticker_ancla_propuesto=sum(1 for ts in tickers if _ticker_tiene_cat(ts, propuestos, True)),
        nuevos=nuevos,
        bloqueados_ancla=bloqueados,
        cambios_tipo=cambios,
    )


def _md_tabla_nuevos(filas: list[tuple[str, str, str, str]]) -> str:
    if not filas:
        return "_(ninguno)_\n"
    lineas = ["| ticker | tipo | frase | titular |", "|---|---|---|---|"]
    for ticker, titular, tipo, frase in filas:
        tit = titular.replace("|", "\\|")
        lineas.append(f"| {ticker} | {tipo} | `{frase}` | {tit} |")
    return "\n".join(lineas) + "\n"


def _seccion_corpus(r: ResultadoCorpus) -> str:
    d_tit = r.cats_titular_propuesto - r.cats_titular_actual
    d_tic = r.cats_ticker_propuesto - r.cats_ticker_actual
    d_ancla = r.cats_ticker_ancla_propuesto - r.cats_ticker_ancla_actual
    partes = [
        f"### {r.nombre}",
        "",
        f"- Titulares únicos: **{r.n_titulares}** · tickers: **{r.n_tickers}**",
        f"- Cats (titular, solo keyword): actual **{r.cats_titular_actual}** → propuesto **{r.cats_titular_propuesto}** (delta {d_tit:+d})",
        f"- Cats (ticker, solo keyword): actual **{r.cats_ticker_actual}** → propuesto **{r.cats_ticker_propuesto}** (delta {d_tic:+d})",
        f"- Cats (ticker, keyword + ancla #118): actual **{r.cats_ticker_ancla_actual}** → propuesto **{r.cats_ticker_ancla_propuesto}** (delta {d_ancla:+d})",
        "",
        "Nuevos matches (ticker|titular|tipo|frase):",
        "",
        _md_tabla_nuevos(r.nuevos),
    ]
    if r.bloqueados_ancla:
        partes += [
            "",
            "De esos nuevos, el ancla (#118, **sin cambiar**) bloquearía:",
            "",
        ]
        lineas = ["| ticker | motivo | tipo | frase | titular |", "|---|---|---|---|---|"]
        for ticker, titular, tipo, frase, motivo in r.bloqueados_ancla:
            tit = titular.replace("|", "\\|")
            lineas.append(f"| {ticker} | {motivo} | {tipo} | `{frase}` | {tit} |")
        partes.append("\n".join(lineas) + "\n")
    if r.cambios_tipo:
        partes += [
            "",
            "Cambios de tipo (prioridad: buyback gana a earnings si ambas frases están):",
            "",
        ]
        lineas = ["| ticker | actual | propuesto | frase nueva | titular |", "|---|---|---|---|---|"]
        for ticker, titular, a, p, frase in r.cambios_tipo:
            tit = titular.replace("|", "\\|")
            lineas.append(f"| {ticker} | {a} | {p} | `{frase}` | {tit} |")
        partes.append("\n".join(lineas) + "\n")
    return "\n".join(partes)


def _flag_suelto(
    yahoo: ResultadoCorpus,
    mediana: float | None,
) -> tuple[bool, str]:
    """La alarma del dueño es cats de EMBUDO por escaneo (~3 vs ~40),
    no 'cuántos tickers de este sample matchean'. Un sample de 93
    tickers con noticia puede tener 40 matches y eso NO es 40
    cats/corrida. Se extrapola el ratio ticker+ancla sobre la mediana
    histórica. La medición autoritativa del dueño es la del Buscador."""
    propuesto = yahoo.cats_ticker_ancla_propuesto
    actual = yahoo.cats_ticker_ancla_actual
    mediana_ref = mediana if mediana is not None else float(MEDIANA_HISTORICA_REF)
    if actual > 0:
        ratio = propuesto / actual
        extra = mediana_ref * ratio
        if extra >= UMBRAL_DEMASIADO_SUELTO:
            return True, (
                f"FLAG: Yahoo+ancla {actual}→{propuesto} (×{ratio:.2f}). "
                f"Sobre mediana {mediana_ref:.1f} eso extrapolado es ~{extra:.0f} "
                f"≥ {UMBRAL_DEMASIADO_SUELTO}."
            )
        return False, (
            f"Sin FLAG de '~{UMBRAL_DEMASIADO_SUELTO}' en unidades de embudo. "
            f"Yahoo+ancla {actual}→{propuesto} (×{ratio:.2f}); "
            f"extrapolado sobre mediana {mediana_ref:.1f}: ~{extra:.1f} cats/escaneo. "
            f"El sample tuvo {propuesto} tickers con match — no confundir con "
            f"{UMBRAL_DEMASIADO_SUELTO} cats/corrida. "
            f"Referencia Buscador: {BUSCADOR_TICKERS_PRE}→{BUSCADOR_TICKERS_POST} "
            f"tickers, {BUSCADOR_TITULOS_PRE}→{BUSCADOR_TITULOS_POST} títulos, "
            f"alarma no disparó."
        )
    return False, "Sin FLAG: no hay cats PRE en el snapshot Yahoo para extrapolar."


def render_reporte(
    auditoria: ResultadoCorpus,
    yahoo: ResultadoCorpus,
    yahoo_wl: ResultadoCorpus | None,
    yahoo_uni: ResultadoCorpus | None,
    curadas: ResultadoCorpus,
    mediana: float | None,
    n_escaneos: int,
    n_frases: int,
) -> str:
    flag, flag_txt = _flag_suelto(yahoo, mediana)
    mediana_txt = (
        f"{mediana:.1f} (n={n_escaneos} escaneos en telemetría)"
        if mediana is not None else
        f"sin telemetría local; referencia del dueño ~{MEDIANA_HISTORICA_REF}"
    )
    hoy = date.today().isoformat()
    filas_tabla = [
        "| corpus | titulares | tickers | cats titular actual | cats titular propuesto | delta | cats ticker+ancla actual | cats ticker+ancla propuesto |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| auditoria | {auditoria.n_titulares} | {auditoria.n_tickers} | {auditoria.cats_titular_actual} | {auditoria.cats_titular_propuesto} | {auditoria.cats_titular_propuesto - auditoria.cats_titular_actual:+d} | {auditoria.cats_ticker_ancla_actual} | {auditoria.cats_ticker_ancla_propuesto} |",
        f"| yahoo snapshot 2026-09-14 | {yahoo.n_titulares} | {yahoo.n_tickers} | {yahoo.cats_titular_actual} | {yahoo.cats_titular_propuesto} | {yahoo.cats_titular_propuesto - yahoo.cats_titular_actual:+d} | {yahoo.cats_ticker_ancla_actual} | {yahoo.cats_ticker_ancla_propuesto} |",
    ]
    if yahoo_wl is not None:
        filas_tabla.append(
            f"| yahoo · solo watchlist | {yahoo_wl.n_titulares} | {yahoo_wl.n_tickers} | {yahoo_wl.cats_titular_actual} | {yahoo_wl.cats_titular_propuesto} | {yahoo_wl.cats_titular_propuesto - yahoo_wl.cats_titular_actual:+d} | {yahoo_wl.cats_ticker_ancla_actual} | {yahoo_wl.cats_ticker_ancla_propuesto} |"
        )
    if yahoo_uni is not None:
        filas_tabla.append(
            f"| yahoo · solo universo (no watchlist) | {yahoo_uni.n_titulares} | {yahoo_uni.n_tickers} | {yahoo_uni.cats_titular_actual} | {yahoo_uni.cats_titular_propuesto} | {yahoo_uni.cats_titular_propuesto - yahoo_uni.cats_titular_actual:+d} | {yahoo_uni.cats_ticker_ancla_actual} | {yahoo_uni.cats_ticker_ancla_propuesto} |"
        )
    filas_tabla.append(
        f"| curadas (near-miss + trampas) | {curadas.n_titulares} | {curadas.n_tickers} | {curadas.cats_titular_actual} | {curadas.cats_titular_propuesto} | {curadas.cats_titular_propuesto - curadas.cats_titular_actual:+d} | {curadas.cats_ticker_ancla_actual} | {curadas.cats_ticker_ancla_propuesto} |"
    )
    nota_uni = ""
    if yahoo_uni is not None and yahoo_uni.cats_ticker_ancla_actual > 0:
        ru = yahoo_uni.cats_ticker_ancla_propuesto / yahoo_uni.cats_ticker_ancla_actual
        med = mediana if mediana is not None else float(MEDIANA_HISTORICA_REF)
        nota_uni = (
            f" Rebanada universo (sin watchlist): ancla "
            f"{yahoo_uni.cats_ticker_ancla_actual}→{yahoo_uni.cats_ticker_ancla_propuesto} "
            f"(×{ru:.2f}), extrapolado ~{med * ru:.1f}. n chico; no es ~40."
        )
    return "\n".join([
        "# Contrafactual Tanda 1 — PRE vs producción",
        "",
        f"Generado {hoy}. Versión de frases: `{WAVE1_VERSION}`. "
        "Paper only. Este PR es **shadow**: `CATALYST_KEYWORDS` de "
        "producción no cambia. El expand Tanda 1 de producción es #121. "
        "Este reporte compara PRE vs PRE∪Tanda1.",
        "",
        "## Referencia Buscador (dueño, 2026-09-14)",
        "",
        f"- {BUSCADOR_N_TICKERS} tickers: **{BUSCADOR_TICKERS_PRE}→{BUSCADOR_TICKERS_POST}**",
        f"- ~{BUSCADOR_N_TITULOS} títulos: **{BUSCADOR_TITULOS_PRE}→{BUSCADOR_TITULOS_POST}**",
        f"- Alarma ~{UMBRAL_DEMASIADO_SUELTO}: **no disparó**.",
        "",
        "## Resumen (este repo, snapshot Yahoo 2026-09-14 + auditoría)",
        "",
        f"- Frases Tanda 1 en el markdown: **{n_frases}** "
        "(solo `buybacks`/`share buybacks`/`stock buybacks` + "
        "`upbeat qN` + `qN earnings`).",
        f"- Mediana histórica `embudo.con_catalizador`: **{mediana_txt}**. "
        f"El dueño marcó ~{UMBRAL_DEMASIADO_SUELTO} como demasiado suelto.",
        f"- **{flag_txt}**{nota_uni}",
        "",
        *filas_tabla,
        "",
        "Auditoría = titulares que **ya** calificaron (control). Yahoo = "
        "snapshot de `yfinance` el 2026-09-14 sobre 116 tickers (watchlist + "
        "rebanada del universo); 93 trajeron noticia. La watchlist ya venía "
        "con catalizador: el delta útil está en la rebanada de universo. "
        "Curadas = huecos y trampas escritos a mano para las pruebas. "
        "Este dump de 887 títulos salta más que el Buscador (55→57) "
        "porque `qN earnings` pega templates Zacks ('Q2 Earnings Call "
        "Highlights'). El dueño midió 50 tickers / ~230 títulos y dio OK.",
        "",
        "## Números por corpus",
        "",
        _seccion_corpus(auditoria),
        "",
        _seccion_corpus(yahoo),
        "",
        _seccion_corpus(curadas),
        "",
        "## Qué se midió y qué no",
        "",
        "- El ancla se aplicó como **filtro posterior**, igual que en "
        f"`run.py`. No se modificó (`flag demasiado suelto={flag}` se "
        "calcula sobre ticker+ancla del snapshot Yahoo mixto).",
        "- No se re-corrió el embudo completo (universo → operables → "
        "noticias). Un escaneo de ~1000 tickers no cabe en este "
        "contrafactual offline.",
        "- `clasificar_titular` de producción (sin Tanda 1 en este PR) "
        "sigue viendo catalizador en los titulares de auditoría.",
        "",
        "## Qué no se tocó",
        "",
        "- `CATALYST_KEYWORDS` en `detector.py` (expand = #121)",
        "- `ancla.py` (ALIASES, GENERIC, la regla)",
        "- IA≥7, ATR, umbrales, universo",
        "- Tanda 2 (`reports qN`, `beats` pelado, `q1:`)",
        "- paper endpoint",
        "",
    ])


def comprobar_produccion_intacta(filas_auditoria: list[Fila]) -> None:
    """Sanity: el clasificador de producción, con SUS keywords, sigue
    viendo catalizador en los titulares de auditoría. Si esto falla,
    alguien tocó `CATALYST_KEYWORDS`. No usa la ventana de días --
    esos titulares ya son viejos y `detectar_catalizador` los
    descartaría por fecha, no por wording."""
    for f in filas_auditoria[:25]:
        assert clasificar_titular(f.titular) is not None, f.titular


def construir_reporte(
    dir_auditoria: Path = DIR_AUDITORIA,
    path_yahoo: Path = PATH_YAHOO,
    path_curadas: Path = PATH_CURADAS,
) -> str:
    actuales = CATALYST_KEYWORDS_PRE_TANDA1
    extras = cargar_frases_propuestas()
    propuestos = keywords_propuestos(actuales, extras)
    nombres = _nombres_universo()

    aud = cargar_auditoria(dir_auditoria)
    for f in aud:
        if f.nombre is None:
            f.nombre = nombres.get(f.ticker)
    yahoo_filas = cargar_json_titulares(path_yahoo, "yahoo") if path_yahoo.exists() else []
    for f in yahoo_filas:
        f.nombre = f.nombre or nombres.get(f.ticker)
    curadas = cargar_json_titulares(path_curadas, "curada") if path_curadas.exists() else []
    for f in curadas:
        f.nombre = f.nombre or nombres.get(f.ticker)

    r_aud = evaluar_corpus("auditoria (ya calificados)", aud, actuales, propuestos)
    r_yahoo = evaluar_corpus("yahoo snapshot 2026-09-14", yahoo_filas, actuales, propuestos)
    wl = _tickers_watchlist()
    r_wl = evaluar_corpus(
        "yahoo · solo watchlist",
        [f for f in yahoo_filas if f.ticker in wl],
        actuales, propuestos,
    )
    r_uni = evaluar_corpus(
        "yahoo · solo universo (no watchlist)",
        [f for f in yahoo_filas if f.ticker not in wl],
        actuales, propuestos,
    )
    r_cur = evaluar_corpus("curadas near-miss + trampas", curadas, actuales, propuestos)
    mediana, n_esc = mediana_historica_cats()
    n_frases = sum(len(v) for v in extras.values())
    if aud:
        comprobar_produccion_intacta(aud)
    return render_reporte(r_aud, r_yahoo, r_wl, r_uni, r_cur, mediana, n_esc, n_frases)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Contrafactual Tanda 1 (PRE vs producción)")
    parser.add_argument(
        "--salida",
        type=Path,
        default=PATH_REPORTE,
        help="Markdown del reporte (default: catalysts/WAVE1_CONTRAFACTUAL.md)",
    )
    args = parser.parse_args(argv)
    texto = construir_reporte()
    args.salida.write_text(texto, encoding="utf-8")
    print(texto)
    print(f"\nescrito {args.salida}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
