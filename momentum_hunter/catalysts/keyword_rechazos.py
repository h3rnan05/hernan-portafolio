"""Observación de rechazos en la etapa KEYWORD -- no decide nada.

POR QUÉ. Corrida del lunes 2026-09-14: 424 titulares → 0 catalizadores.
Los titulares no se persistían, así que no se pudo auditar si el cuello
era `sin_keyword`, ventana o rumor sin fuentes. El filtro de ancla
(#118) es POST-keyword y no cubre este hueco: si `detectar_catalizador`
ya devolvió None, el ancla ni se evalúa.

NUNCA cambia `detectar_catalizador`, `clasificar_titular`,
`CATALYST_KEYWORDS`, umbrales ni el filtro de ancla. Relee los mismos
predicados para explicar un None, nada más. Las frases almost-miss /
shadow-CF van en otro PR: acá no se inventa un match ni se anota
un "casi". Si esta explicación falla, el pipeline sigue -- registrar
no es un requisito para operar."""

from __future__ import annotations

from datetime import date

from momentum_hunter.catalysts.detector import (
    Titular,
    clasificar_titular,
    dentro_de_ventana,
)
from momentum_hunter.config import MomentumConfig

MOTIVO_SIN_KEYWORD = "sin_keyword"
MOTIVO_FUERA_VENTANA = "fuera_ventana"
MOTIVO_RUMOR_SIN_FUENTES = "rumor_sin_fuentes"


def _nota_fuera_ventana(fecha: str | None, hoy: date, dias: int) -> str | None:
    """Cuántos días tiene el titular vs la ventana -- no es un almost-
    miss de keywords, es el dato que ya usó `dentro_de_ventana`."""
    if not fecha:
        return None
    try:
        f = date.fromisoformat(fecha[:10])
    except ValueError:
        return None
    return f"hace_{(hoy - f).days}d_ventana_{dias}"


def explicar_rechazos_keyword(
    ticker: str,
    titulares: list[Titular],
    cfg: MomentumConfig,
    hoy: date | None = None,
) -> list[dict]:
    """Un dict por titular que NO confirmó catalizador.

    Motivos (el primero que aplica, el mismo orden que el detector):
      1. `fuera_ventana` -- la fecha cae fuera de `dias_ventana_catalizador`
      2. `sin_keyword` -- `clasificar_titular` devolvió None
      3. `rumor_sin_fuentes` -- es rumor y no hay suficientes fuentes
         distintas en la ventana

    Un titular vigente con keyword no-rumor no aparece: ese caso es
    precisamente el que `detectar_catalizador` confirmaría. Llamar
    esto cuando el detector ya devolvió un Catalizador no tiene
    sentido -- el hook en `run.py` no lo hace.

    `hoy` default = `date.today()`, igual que el detector, para que
    el motivo coincida con el None que se está explicando."""
    if not ticker or not titulares:
        return []
    hoy = hoy or date.today()
    dias = cfg.dias_ventana_catalizador

    vigentes = [t for t in titulares if dentro_de_ventana(t.fecha, hoy, dias)]
    rumores = [t for t in vigentes if clasificar_titular(t.texto) == "rumor"]
    n_fuentes_rumor = len({t.fuente for t in rumores})
    rumor_confirmado = n_fuentes_rumor >= cfg.fuentes_minimas_rumor

    out: list[dict] = []
    for t in titulares:
        if not dentro_de_ventana(t.fecha, hoy, dias):
            muestra = {
                "ticker": ticker,
                "titular": t.texto,
                "motivo": MOTIVO_FUERA_VENTANA,
            }
            nota = _nota_fuera_ventana(t.fecha, hoy, dias)
            if nota:
                muestra["nota"] = nota
            out.append(muestra)
            continue
        tipo = clasificar_titular(t.texto)
        if tipo is None:
            out.append({
                "ticker": ticker,
                "titular": t.texto,
                "motivo": MOTIVO_SIN_KEYWORD,
            })
            continue
        if tipo == "rumor" and not rumor_confirmado:
            out.append({
                "ticker": ticker,
                "titular": t.texto,
                "motivo": MOTIVO_RUMOR_SIN_FUENTES,
                "nota": (
                    f"fuentes={n_fuentes_rumor}"
                    f" minimo={cfg.fuentes_minimas_rumor}"
                ),
            })
    return out
