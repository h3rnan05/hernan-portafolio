"""Diario automático de aprendizaje -- refinamiento "Head Trader"
(2026-07-27), punto 9: "cada operación debe generar automáticamente una
página de aprendizaje... no solo ganó/perdió, sino por qué funcionó,
por qué falló, qué hubiera hecho un trader profesional, qué puedo
aprender para la siguiente."

Cuando `outcomes.py` termina de medir una alerta (resuelta a todos los
horizontes), este módulo escribe una página markdown en
`momentum_hunter/diario/` -- una por alerta, generada UNA sola vez
(`AlertaRegistrada.diario_escrito`).

HONESTIDAD SOBRE QUÉ ES ESTO: cada sección es una plantilla determinista
rellenada con los números REALES medidos (retornos por horizonte, mejor
y peor excursión vs. objetivo y stop, patrón, hora, RVOL de entrada) --
el mismo tipo de "explicación por reglas fijas" que usa el resto del
sistema, nunca el juicio inventado de un modelo. Lo que la página puede
decir con verdad, lo dice; lo que requeriría releer el mercado de ese
día (noticias posteriores, contexto macro), lo declara fuera de alcance
en la propia página. Así el diario enseña sin que el sistema modifique
jamás sus propias reglas (Principio 8: el que decide cambios sigue
siendo el humano, con estas páginas como evidencia)."""

from __future__ import annotations

import logging
from pathlib import Path

from momentum_hunter.tracker import AlertaRegistrada

log = logging.getLogger("momentum_hunter.diario")

DIR_DIARIO = Path(__file__).resolve().parent / "diario"
HORIZONTE_VEREDICTO = "3d"   # mismo horizonte de referencia que memoria.py


def _pct(x: float | None) -> str:
    return f"{x:+.1%}" if x is not None else "no medido"


def _toco_objetivo(a: AlertaRegistrada) -> bool | None:
    if a.objetivo1 is None or a.precio_maximo_pct is None or a.precio_entrada <= 0:
        return None
    return a.precio_maximo_pct >= (a.objetivo1 - a.precio_entrada) / a.precio_entrada


def _toco_stop(a: AlertaRegistrada) -> bool | None:
    if a.stop is None or a.precio_minimo_pct is None or a.precio_entrada <= 0:
        return None
    return a.precio_minimo_pct <= (a.stop - a.precio_entrada) / a.precio_entrada


def _veredicto(a: AlertaRegistrada) -> str:
    r = a.resultados_pct.get(HORIZONTE_VEREDICTO)
    if r is None:
        return "sin veredicto"
    return "funcionó" if r > 0 else "falló"


def _leccion(a: AlertaRegistrada, toco_obj: bool | None, toco_stop: bool | None) -> list[str]:
    """Las secciones interpretativas -- cada frase es una regla fija
    sobre los números medidos, elegida por el caso que REALMENTE ocurrió."""
    lineas: list[str] = []
    if toco_obj and toco_stop:
        lineas.append(
            "**Qué hubiera hecho un trader profesional:** el precio tocó tanto el objetivo "
            "como el stop dentro de la ventana medida. Con velas diarias no sé cuál tocó "
            "primero -- la lección conservadora es que este trade exigía gestión activa: "
            "tomar parcial en el objetivo y nunca aflojar el stop."
        )
    elif toco_obj:
        lineas.append(
            "**Qué hubiera hecho un trader profesional:** el precio llegó al objetivo. "
            "La jugada era tomar al menos una parte de la ganancia ahí, en vez de esperar más."
        )
    elif toco_stop:
        lineas.append(
            "**Qué hubiera hecho un trader profesional:** el precio tocó el stop. La jugada "
            "correcta era salir ahí sin discutir -- el stop existe exactamente para este caso, "
            "y respetarlo es lo que mantiene pequeñas las pérdidas."
        )
    else:
        lineas.append(
            "**Qué hubiera hecho un trader profesional:** el precio no llegó ni al objetivo "
            "ni al stop en la ventana medida -- un trade que no resolvió. La lección es de "
            "paciencia o de salida por tiempo: si la tesis era de días y no se movió, el "
            "capital estaba mejor en otra parte."
        )

    r = a.resultados_pct.get(HORIZONTE_VEREDICTO)
    if r is not None:
        if r > 0:
            lineas.append(
                f"**Por qué funcionó (datos, no narrativa):** la configuración registrada fue "
                f"{a.clasificacion or 'sin patrón'} a las {a.hora_utc}h UTC"
                + (f" con RVOL {a.rvol:.1f}x" if a.rvol is not None else "")
                + f". El retorno a {HORIZONTE_VEREDICTO} fue {r:+.1%}. Cuando se acumulen más "
                "casos como este, stats.py dirá si esta combinación se repite o fue suerte."
            )
        else:
            lineas.append(
                f"**Por qué falló (datos, no narrativa):** la configuración registrada fue "
                f"{a.clasificacion or 'sin patrón'} a las {a.hora_utc}h UTC"
                + (f" con RVOL {a.rvol:.1f}x" if a.rvol is not None else "")
                + f". El retorno a {HORIZONTE_VEREDICTO} fue {r:+.1%}. Qué noticia o contexto lo "
                "revirtió queda fuera de lo que puedo medir hoy -- lo declaro en vez de inventarlo."
            )

    lineas.append(
        "**Qué aprender para la siguiente:** este caso ya cuenta en la memoria del sistema "
        "(stats.py agrupa por patrón, hora, catalizador, float, gap y RVOL). Ningún umbral "
        "cambia solo por esta página: cuando un grupo acumule evidencia, el ajuste se propone "
        "y lo decide el humano."
    )
    return lineas


def pagina(a: AlertaRegistrada) -> str:
    toco_obj = _toco_objetivo(a)
    toco_stp = _toco_stop(a)
    lineas = [
        f"# {a.ticker} -- {a.fecha[:10]} ({_veredicto(a)})",
        "",
        f"- Alerta enviada: {a.fecha}",
        f"- Configuración: {a.clasificacion or 'sin patrón'}"
        + (f", catalizador: {a.catalizador_tipo}" if a.catalizador_tipo else ""),
        f"- Entrada ${a.precio_entrada:,.2f} | stop "
        + (f"${a.stop:,.2f}" if a.stop is not None else "no definido")
        + " | objetivo "
        + (f"${a.objetivo1:,.2f}" if a.objetivo1 is not None else "no definido"),
        "",
        "## Qué ocurrió realmente",
        "",
        f"- Retornos: 1d {_pct(a.resultados_pct.get('1d'))} | 3d {_pct(a.resultados_pct.get('3d'))} "
        f"| 5d {_pct(a.resultados_pct.get('5d'))} | 10d {_pct(a.resultados_pct.get('10d'))}",
        f"- Mejor momento: {_pct(a.precio_maximo_pct)} | peor momento: {_pct(a.precio_minimo_pct)}",
        "- ¿Tocó el objetivo?: "
        + ("sí" if toco_obj else "no" if toco_obj is not None else "no se pudo medir"),
        "- ¿Tocó el stop?: "
        + ("sí" if toco_stp else "no" if toco_stp is not None else "no se pudo medir"),
        "",
        "## Lección",
        "",
    ]
    lineas += [linea + "\n" for linea in _leccion(a, toco_obj, toco_stp)]
    return "\n".join(lineas)


def escribir_nuevas(alertas: list[AlertaRegistrada], dir_diario: Path = DIR_DIARIO) -> list[Path]:
    """Escribe la página de cada alerta resuelta que todavía no tiene
    una (marcándola con `diario_escrito` -- quien llama persiste con
    `tracker.guardar`). Devuelve las rutas escritas."""
    rutas: list[Path] = []
    pendientes = [a for a in alertas if a.resuelta and not a.diario_escrito]
    if not pendientes:
        return rutas
    dir_diario.mkdir(parents=True, exist_ok=True)
    for a in pendientes:
        path = dir_diario / f"{a.fecha[:10]}-{a.ticker}-{a.id}.md"
        path.write_text(pagina(a))
        a.diario_escrito = True
        rutas.append(path)
        log.info("página de diario escrita: %s", path.name)
    return rutas
