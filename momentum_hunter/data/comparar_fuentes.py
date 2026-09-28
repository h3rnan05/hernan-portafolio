"""Compara, en solo lectura, la vela que vería el hunter en cada fuente.

El VWAP es el de `factors.intradia.vwap_real` (sesión regular, precio
típico por volumen), no el `vw` por vela del feed: es el número con el
que el bot decide, y mezclar la otra definición no diría si las fuentes
coinciden. Un campo ausente queda en None; no se rellena con cero.

`nota_ratio` es un aviso del script (15 % de diferencia), no un umbral
del bot. No cambia sizing, stops ni filtros.

La sección de subasta, si se pasa, es lo que el feed SIP sumó a las
velas de minuto (el cruce no viene en la vela cruda). El VWAP y el
volumen de sesión de la tabla ya incluyen ese pliegue. Un '-' es un
dato que no vino; no es un cruce de cero acciones.
"""

from __future__ import annotations

from datetime import UTC, datetime

from momentum_hunter.factors.intradia import barras_de_hoy, es_sesion_regular, vwap_real
from momentum_hunter.models import Barras, BarraIntradia

# Por encima de esto el script lo marca. No lo lee el hunter.
UMBRAL_AVISO_RATIO = 0.15


def _edad_s(ultima: str | None, ahora: datetime | None) -> float | None:
    if ultima is None or ahora is None:
        return None
    try:
        dt = datetime.fromisoformat(ultima)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return round((ahora.astimezone(UTC) - dt.astimezone(UTC)).total_seconds(), 1)


def resumen_intradia(bi: BarraIntradia | None, ahora: datetime | None = None) -> dict:
    """Última vela de HOY, su volumen, el volumen de la sesión regular
    (el que pesa el VWAP) y el VWAP. None si no hay vela usable."""
    vacio = {
        "ultima_vela": None, "edad_s": None, "vol_ultima": None,
        "vol_sesion": None, "vwap": None,
    }
    if bi is None or not bi.timestamps:
        return vacio
    hoy = barras_de_hoy(bi)
    if not hoy.timestamps or not hoy.volume:
        return vacio
    vols = [
        v for t, v in zip(hoy.timestamps, hoy.volume, strict=True) if es_sesion_regular(t)
    ]
    return {
        "ultima_vela": hoy.timestamps[-1],
        "edad_s": _edad_s(hoy.timestamps[-1], ahora),
        "vol_ultima": hoy.volume[-1],
        "vol_sesion": sum(vols) if vols else None,
        "vwap": vwap_real(hoy),
    }


def resumen_diario(b: Barras | None) -> dict:
    """Volumen de la última sesión y promedio de 20. El promedio solo
    si hay 20: uno calculado con menos no es el número del filtro."""
    if b is None or not b.volume:
        return {"vol_ultima": None, "vol_promedio_20": None, "n": 0}
    n = len(b.volume)
    return {
        "vol_ultima": b.volume[-1],
        "vol_promedio_20": (sum(b.volume[-20:]) / 20) if n >= 20 else None,
        "n": n,
    }


def nota_ratio(alpaca: float | None, yahoo: float | None, umbral: float = UMBRAL_AVISO_RATIO) -> str | None:
    """`alpaca/yahoo` si se apartan más del umbral. None si falta un
    lado o el de Yahoo es 0 (dividir por un volumen ausente no es una
    diferencia)."""
    if alpaca is None or yahoo is None or yahoo == 0:
        return None
    ratio = alpaca / yahoo
    if abs(ratio - 1.0) <= umbral:
        return None
    return f"{ratio:.2f}x"


def _fmt(valor, decimales: int = 2) -> str:
    if valor is None:
        return "-"
    if isinstance(valor, float):
        return f"{valor:.{decimales}f}"
    return str(valor)


def _fmt_lado_subasta(lado: dict | None) -> str:
    """Precio x volumen del cruce plegado. Sin size no se imprime un 0."""
    if not lado:
        return "-"
    precio = lado.get("precio")
    volumen = lado.get("volumen")
    if precio is None and volumen is None:
        return "-"
    texto = f"{_fmt(precio, 4)} x {_fmt(volumen, 0)}"
    if volumen is None:
        if lado.get("sin_size"):
            texto += " (sin size, no se plegó)"
        else:
            texto += " (fuera de la sesión que mira el VWAP, no se plegó)"
    minuto = lado.get("minuto")
    if minuto:
        texto += f" @ {minuto}"
    return texto


def formatear(
    simbolos: list[str],
    intradía_alpaca: dict[str, BarraIntradia],
    intradía_yahoo: dict[str, BarraIntradia],
    diarias_alpaca: dict[str, Barras],
    diarias_yahoo: dict[str, Barras],
    snapshots: dict[str, dict] | None = None,
    ahora: datetime | None = None,
    aportes_subasta: dict[str, list] | None = None,
) -> str:
    """Texto para el operador. No decide nada."""
    snapshots = snapshots or {}
    lineas = [
        "ticker  fuente   ultima_vela                edad_s  vol_ultima  vol_sesion      vwap  "
        "vol_dia  vol_prom20  trade",
    ]
    avisos = []
    for t in simbolos:
        ra = resumen_intradia(intradía_alpaca.get(t), ahora)
        ry = resumen_intradia(intradía_yahoo.get(t), ahora)
        da = resumen_diario(diarias_alpaca.get(t))
        dy = resumen_diario(diarias_yahoo.get(t))
        trade = (snapshots.get(t) or {}).get("precio")
        for nombre, r, d, extra in (
            ("alpaca", ra, da, _fmt(trade)),
            ("yahoo", ry, dy, "-"),
        ):
            lineas.append(
                f"{t:<8}{nombre:<8}{r['ultima_vela'] or '-':<27}{_fmt(r['edad_s'], 0):>7}"
                f"{_fmt(r['vol_ultima'], 0):>12}{_fmt(r['vol_sesion'], 0):>12}"
                f"{_fmt(r['vwap'], 4):>10}{_fmt(d['vol_ultima'], 0):>9}"
                f"{_fmt(d['vol_promedio_20'], 0):>12}{extra:>8}"
            )
        for etiqueta, a, y in (
            ("vol_sesion", ra["vol_sesion"], ry["vol_sesion"]),
            ("vwap", ra["vwap"], ry["vwap"]),
            ("vol_prom20", da["vol_promedio_20"], dy["vol_promedio_20"]),
        ):
            nota = nota_ratio(a, y)
            if nota:
                avisos.append(f"{t} {etiqueta}: feed/yahoo = {nota}")
    if avisos:
        lineas.append("")
        lineas.append("avisos (no son umbrales del bot; 15% es solo para mirar):")
        lineas.extend(f"  {a}" for a in avisos)
    else:
        lineas.append("")
        lineas.append("sin avisos de diferencia >15% en los campos que ambos devolvieron.")
    if aportes_subasta is not None:
        # El volumen de acá es el que YA está dentro de vol_sesion y del
        # VWAP de la fila alpaca. Sirve para ver cuánto del día fue el
        # cruce y no el continuo.
        lineas.append("")
        lineas.append(
            "subasta plegada en las velas del feed "
            "(apertura en su minuto; cierre de las 16:00 ET en el minuto anterior):"
        )
        for t in simbolos:
            dias = aportes_subasta.get(t) or []
            if not dias:
                lineas.append(f"  {t}: -")
                continue
            for dia in dias:
                lineas.append(
                    f"  {t} {dia.get('dia') or '-'}: "
                    f"apertura {_fmt_lado_subasta(dia.get('apertura'))}; "
                    f"cierre {_fmt_lado_subasta(dia.get('cierre'))}"
                )
        lineas.append(
            "Ese volumen ya entra en vol_sesion y en el vwap de alpaca. "
            "Un '-' es un cruce que no vino; no es cero."
        )
    lineas.append(
        "El volumen 0 o ausente no se muestra como cero inventado: un '-' es un dato que faltó."
    )
    return "\n".join(lineas)
