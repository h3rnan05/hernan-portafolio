"""P3. Backtest del hunter sobre el histórico SIP de 1 minuto (desde 2016).

Extiende B3 (`historia.py`): B3 baja las velas a un almacén local; este
módulo las recorre minuto a minuto con la lógica ACTUAL del hunter y
simula la entrada paper. Es offline: no escribe la watchlist, no manda
Telegram, no importa el ejecutor y no conoce el host de trading.

QUÉ SE REUTILIZA TAL CUAL (no hay copia que se desactualice):
  - filtros de universo: `run._clasificar_banda_de_universo`,
    `run._excede_techo_de_tamano`, ETF/SPAC/CEF de la metadata;
  - catalizador: `detector.detectar_catalizador` + `ancla_ok`, con los
    titulares publicados ANTES del minuto evaluado (sin mirar el futuro);
  - score diario: `momentum.calcular` + `scoring.puntuar`;
  - shortlist: `alerts.candidatos_para_etapa_intradia`;
  - evaluación intradía: `run._construir_candidato_intradia`;
  - competencia y abogado del diablo: `run.seleccionar_y_auditar`;
  - máquina de estados: `watchlist.agregar_nuevas`, `marcar_triggered`,
    `run._evaluar_no_disparada` (MISSED / INVALIDATED),
    `run._resolver_vencidas_antes_de_evaluar` y `expirar_vencidas`;
  - "queda sesión": `run._hay_tiempo`.

EL RELOJ. `_construir_candidato_intradia` mide los minutos desde el
catalizador con el reloj de pared. Aquí se fija al minuto simulado
(`_reloj_simulado`) solo durante el backtest. No se toca `run.py`
porque #199, #201 y #202 lo están editando a la vez; un parámetro
`ahora` explícito queda como mejora pendiente.

LO QUE NO SE PUEDE REPRODUCIR -- sale en cada informe, no se maquilla:
  - Noticias: Benzinga (`/v1beta1/news`), no las de Yahoo que usa
    producción. Con una sola fuente, un "rumor" nunca se confirma.
  - Metadata (float, ETF, bolsa): la ACTUAL de Yahoo, no la de esa
    fecha. Sin metadata el símbolo se descarta, igual que en producción.
  - Universo: se asume que el escaneo vio al símbolo en CADA ranura de
    30 min. Producción rota ~1.000 tickers por ranura, así que esto es
    un techo de señales, no un conteo esperado.
  - Memoria: `historial=[]`. La memoria de producción son alertas que
    en esa fecha todavía no existían.
  - IA: no se simula. Se asume que aprueba todo con fracción 1,0, así
    que las entradas simuladas son el techo de lo que el ejecutor
    colocaría, no una predicción de lo que haría.
  - Ejecución: fill de la orden límite en la primera vela que toca el
    precio; si stop y objetivo caen en la misma vela, se asume el stop
    (lo conservador). Sin slippage más allá de eso.

Tamaños y tiempos de la simulación = los del ejecutor hoy (una prueba
lo verifica): riesgo $100, tope 15 % del efectivo, 5 posiciones,
15 min para llenar, liquidación 10 min antes del cierre, no entrar con
menos de 30 min de sesión. Latencia señal→orden: 2 min (la mediana
medida el 23/9 fue 2,4 min).

USO (en el VPS o en local, con claves de DATOS):
  python -m shadow_alpaca.historia descargar --simbolos X,Y --desde 2024-01-01 \
      --timeframes 1Day,1Min --almacen /var/lib/momentum/shadow_alpaca/almacen
  python -m shadow_alpaca.backtest noticias --simbolos X,Y --desde 2024-01-01 --almacen ...
  python -m shadow_alpaca.backtest metadata --simbolos X,Y --almacen ...
  python -m shadow_alpaca.backtest correr --simbolos X,Y --desde 2024-03-01 \
      --hasta 2024-03-31 --almacen ... --salida /var/lib/momentum/shadow_alpaca/backtest
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum_hunter import run as run_mod
from momentum_hunter import watchlist
from momentum_hunter.alerts import CandidatoDiario, candidatos_para_etapa_intradia
from momentum_hunter.catalysts.ancla import ancla_ok
from momentum_hunter.catalysts.detector import Titular, detectar_catalizador
from momentum_hunter.config import CONFIG, MomentumConfig
from momentum_hunter.factors import momentum as mom
from momentum_hunter.models import Barras, BarraIntradia, Metadata
from momentum_hunter.scoring import puntuar

from shadow_alpaca.cliente import ClienteDatos, ErrorDatos
from shadow_alpaca.historia import cargar_almacen
from shadow_alpaca.jsonl_log import exigir_directorio_aislado

log = logging.getLogger("shadow_alpaca.backtest")

NY = ZoneInfo("America/New_York")
MTY = ZoneInfo("America/Monterrey")
APERTURA = time(9, 30)
CIERRE = time(16, 0)
# Ranuras del escaneo del VPS (:01 y :31), llevadas a hora de Nueva
# York para que 2016-2025 (con y sin horario de verano) se lean igual.
PRIMERA_RANURA = time(9, 1)
ULTIMA_RANURA = time(15, 31)
DIAS_VENTANA_INTRADIA = 5          # el `periodo="5d"` del hunter
MESES_POR_PEDIDO_NOTICIAS = 1


@dataclass(frozen=True)
class ParametrosEjecucion:
    """Los números del ejecutor paper. No se importan de
    `momentum_paper_trader` a propósito (la sombra no puede engancharse
    al ejecutor); `test_backtest` verifica que coincidan."""

    riesgo_dolares: float = 100.0
    pct_efectivo_por_posicion: float = 0.15
    efectivo: float = 5_000.0
    maximo_posiciones: int = 5
    minutos_para_llenar: float = 15.0
    minutos_antes_del_cierre: int = 10
    minutos_minimos_para_entrar: float = 30.0
    minimo_acciones: int = 1
    latencia_minutos: float = 2.0


# --------------------------------------------------------------- datos


@dataclass
class Vela:
    t: datetime   # inicio del minuto (o del día), UTC
    o: float
    h: float
    l: float   # noqa: E741 -- mismo nombre que el feed
    c: float
    v: float


def _velas(crudas: list[dict]) -> list[Vela]:
    out: list[Vela] = []
    for b in crudas:
        try:
            t = datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00")).astimezone(UTC)
            out.append(Vela(t, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), float(b["v"])))
        except (KeyError, TypeError, ValueError):
            continue   # B3 ya descartó las incompletas; esto es cinturón
    out.sort(key=lambda v: v.t)
    return out


def _dia_ny(t: datetime) -> date:
    return t.astimezone(NY).date()


def _en_ny(dia: date, hora: time) -> datetime:
    return datetime.combine(dia, hora, tzinfo=NY).astimezone(UTC)


@dataclass
class DatosSimbolo:
    ticker: str
    minutos: list[Vela]
    diarias: list[Vela]
    titulares: list[Titular]
    meta: Metadata | None
    por_dia: dict[date, list[Vela]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for v in self.minutos:
            self.por_dia.setdefault(_dia_ny(v.t), []).append(v)


def _titular(d: dict) -> Titular | None:
    texto, fuente = d.get("texto"), d.get("fuente")
    if not isinstance(texto, str) or not isinstance(fuente, str):
        return None
    fecha = d.get("fecha")
    return Titular(texto, fuente, fecha if isinstance(fecha, str) else None)


def cargar_simbolo(almacen: Path, ticker: str) -> DatosSimbolo:
    dir_sim = almacen / ticker
    titulares: list[Titular] = []
    ruta_n = dir_sim / "noticias.jsonl"
    if ruta_n.exists():
        for linea in ruta_n.read_text(encoding="utf-8").splitlines():
            try:
                t = _titular(json.loads(linea))
            except json.JSONDecodeError:
                continue
            if t is not None:
                titulares.append(t)
    meta = None
    ruta_m = dir_sim / "metadata.json"
    if ruta_m.exists():
        try:
            crudo = json.loads(ruta_m.read_text(encoding="utf-8"))
            campos = {k: v for k, v in crudo.get("metadata", {}).items() if k in Metadata.__dataclass_fields__}
            meta = Metadata(**campos)
        except (json.JSONDecodeError, TypeError, AttributeError):
            meta = None
    return DatosSimbolo(
        ticker=ticker,
        minutos=_velas(cargar_almacen(almacen, ticker, "1Min")),
        diarias=_velas(cargar_almacen(almacen, ticker, "1Day")),
        titulares=titulares,
        meta=meta,
    )


def _barras_diarias(d: DatosSimbolo, dia: date, hasta: datetime) -> Barras | None:
    """Diarias anteriores a `dia` + la vela de HOY armada con los minutos
    de sesión regular ya cerrados a `hasta` (como la barra en vivo que
    ve producción durante el día). Épocas en texto: contrato de `Barras`."""
    previas = [v for v in d.diarias if _dia_ny(v.t) < dia]
    fechas = [str(int(v.t.timestamp())) for v in previas]
    o, c, h, lo, vol = ([v.o for v in previas], [v.c for v in previas], [v.h for v in previas],
                        [v.l for v in previas], [v.v for v in previas])
    regulares = [v for v in d.por_dia.get(dia, [])
                 if _en_ny(dia, APERTURA) <= v.t < _en_ny(dia, CIERRE)
                 and v.t + timedelta(minutes=1) <= hasta]
    if regulares:
        fechas.append(str(int(_en_ny(dia, time(0, 0)).timestamp())))
        o.append(regulares[0].o)
        c.append(regulares[-1].c)
        h.append(max(v.h for v in regulares))
        lo.append(min(v.l for v in regulares))
        vol.append(sum(v.v for v in regulares))
    if not fechas:
        return None
    return Barras(d.ticker, fechas, o, c, h, lo, vol)


def _bi_hasta(d: DatosSimbolo, dia: date, momento: datetime) -> BarraIntradia | None:
    """Velas de 1 minuto de las últimas 5 sesiones, solo las que ya
    CERRARON en `momento` (una vela empieza en t y cierra en t+1 min)."""
    dias = sorted(x for x in d.por_dia if x <= dia)[-DIAS_VENTANA_INTRADIA:]
    velas = [v for x in dias for v in d.por_dia[x] if v.t + timedelta(minutes=1) <= momento]
    if not velas or _dia_ny(velas[-1].t) != dia:
        return None
    return BarraIntradia(
        d.ticker, [v.t.isoformat(timespec="seconds") for v in velas],
        [v.o for v in velas], [v.c for v in velas], [v.h for v in velas],
        [v.l for v in velas], [v.v for v in velas],
    )


# ---------------------------------------------------------------- reloj


@contextmanager
def _reloj_simulado(reloj: dict) -> Iterator[None]:
    """`minutos_desde_catalizador` sin `ahora` usa el reloj de pared;
    aquí se contesta con el minuto simulado. Solo dura el backtest."""
    original = run_mod.minutos_desde_catalizador

    def _simulado(catalizador, ahora=None):
        return original(catalizador, ahora or reloj["ahora"])

    run_mod.minutos_desde_catalizador = _simulado
    try:
        yield
    finally:
        run_mod.minutos_desde_catalizador = original


# ------------------------------------------------------------ simulación


@dataclass
class Senal:
    ticker: str
    momento: str
    patron: str | None
    entrada: float | None
    stop: float | None
    objetivo: float | None
    score: float | None
    catalizador: str | None


@dataclass
class Operacion:
    ticker: str
    senal: str
    estado: str                      # "cerrada" | "sin_llenar" | "no_entra"
    motivo: str | None = None
    cantidad: int | None = None
    orden_ts: str | None = None
    llenado_ts: str | None = None
    precio_llenado: float | None = None
    salida_ts: str | None = None
    precio_salida: float | None = None
    salida_por: str | None = None    # "stop" | "objetivo" | "cierre"
    pnl: float | None = None
    r: float | None = None


@dataclass
class InformeDia:
    fecha: str
    candidatos: dict[str, str] = field(default_factory=dict)     # ticker -> catalizador
    descartes: dict[str, str] = field(default_factory=dict)      # ticker -> motivo
    senales: list[Senal] = field(default_factory=list)
    operaciones: list[Operacion] = field(default_factory=list)
    transiciones: dict[str, str] = field(default_factory=dict)   # ticker -> estado final


def _candidato_diario(
    d: DatosSimbolo, dia: date, momento: datetime, cfg: MomentumConfig,
) -> tuple[CandidatoDiario | None, str | None]:
    """(candidato, motivo del descarte). Mismos filtros que la etapa 1."""
    b = _barras_diarias(d, dia, momento)
    if b is None or len(b) < 20:
        return None, "sin_historia_diaria"
    banda, motivo = run_mod._clasificar_banda_de_universo(b, cfg)
    if banda is None:
        return None, f"universo:{motivo or 'fuera'}"
    meta = d.meta
    if meta is None:
        return None, "sin_metadata"
    if meta.es_etf or (cfg.excluir_spac and meta.es_spac) or (cfg.excluir_cef and meta.es_cef):
        return None, "etf_spac_cef"
    es_large = banda == "large"
    if not es_large and run_mod._excede_techo_de_tamano(meta, b, cfg):
        return None, "market_cap"
    vol_prom = run_mod._volumen_promedio(b)
    if meta.es_adr and (vol_prom is None or vol_prom < cfg.liquidez_minima_adr):
        return None, "adr_iliquido"
    publicados = [t for t in d.titulares if t.fecha and _antes_de(t.fecha, momento)]
    catalizador = detectar_catalizador(publicados, cfg, hoy=dia)
    if catalizador is not None:
        ok, _ = ancla_ok(d.ticker, meta.nombre, catalizador.titular)
        if not ok:
            catalizador = None
    if catalizador is None:
        return None, "sin_catalizador"
    factores = mom.calcular(b)
    puntuacion = puntuar(d.ticker, b.close[-1], vol_prom, factores, catalizador, meta, cfg)
    return CandidatoDiario(
        ticker=d.ticker, nombre=meta.nombre, precio=b.close[-1], volumen_promedio=vol_prom,
        factores=factores, catalizador=catalizador, meta=meta, puntuacion=puntuacion,
        es_large_cap=es_large,
    ), None


def _antes_de(fecha_iso: str, momento: datetime) -> bool:
    """Un titular sin hora no se puede ubicar antes o después del minuto:
    se descarta (no se adivina la hora, y no se mira el futuro)."""
    if "T" not in fecha_iso:
        return False
    try:
        t = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        return False
    return t <= momento


def _cierre_previo(d: DatosSimbolo, dia: date) -> float | None:
    previas = [v for v in d.diarias if _dia_ny(v.t) < dia]
    return previas[-1].c if previas else None


def simular_dia(
    dia: date, datos: list[DatosSimbolo], cfg: MomentumConfig = CONFIG,
    params: ParametrosEjecucion | None = None,
) -> InformeDia:
    params = params or ParametrosEjecucion()
    informe = InformeDia(fecha=dia.isoformat())
    por_ticker = {d.ticker: d for d in datos if dia in d.por_dia}
    if not por_ticker:
        return informe
    entradas: list = []
    reloj = {"ahora": _en_ny(dia, PRIMERA_RANURA)}
    ranuras = set()
    t = _en_ny(dia, PRIMERA_RANURA)
    while t <= _en_ny(dia, ULTIMA_RANURA):
        ranuras.add(t)
        t += timedelta(minutes=30)

    with _reloj_simulado(reloj):
        momento = _en_ny(dia, PRIMERA_RANURA)
        fin = _en_ny(dia, CIERRE)
        while momento <= fin:
            reloj["ahora"] = momento
            if momento in ranuras:
                diarios = []
                for d in por_ticker.values():
                    c, motivo = _candidato_diario(d, dia, momento, cfg)
                    if c is not None:
                        diarios.append(c)
                        informe.candidatos.setdefault(d.ticker, c.catalizador.titular)
                        informe.descartes.pop(d.ticker, None)
                    elif d.ticker not in informe.candidatos:
                        informe.descartes[d.ticker] = motivo or "?"
                shortlist = candidatos_para_etapa_intradia(diarios, cfg)
                entradas = watchlist.agregar_nuevas(entradas, shortlist, momento)
            run_mod._resolver_vencidas_antes_de_evaluar(entradas, cfg, momento)
            candidatos = []
            vigiladas = {e.ticker: e for e in watchlist.activas(entradas)}
            for e in vigiladas.values():
                d = por_ticker.get(e.ticker)
                bi = _bi_hasta(d, dia, momento) if d else None
                if bi is None:
                    continue
                c = run_mod._construir_candidato_intradia(
                    e.ticker, e.nombre, watchlist.catalizador_de(e), watchlist.meta_de(e),
                    e.es_large_cap, e.atr_diario, e.score_base, _cierre_previo(d, dia), bi, cfg,
                    gap_pct_fallback=e.gap_pct_congelado)
                if c is None:
                    continue
                if momento in ranuras and e.gap_pct_congelado is None and c.factores.gap_pct is not None:
                    e.gap_pct_congelado = c.factores.gap_pct
                candidatos.append(c)
            if candidatos:
                hora = momento.hour + momento.minute / 60.0
                oportunidades, _, _ = run_mod.seleccionar_y_auditar(
                    candidatos, cfg, historial=[], hora_utc=hora, n_universo=0)
                elegidas = {o.ticker: o for o in oportunidades}
                for c in candidatos:
                    e = vigiladas[c.ticker]
                    if c.ticker in elegidas and run_mod._hay_tiempo(cfg, momento, c.ticker):
                        o = elegidas[c.ticker]
                        marca = momento.isoformat(timespec="seconds")
                        watchlist.marcar_triggered(
                            e, c.bi_hoy.timestamps[-1], marca, marca, momento,
                            velas_desde_ruptura=getattr(c.factores, "velas_desde_ruptura", None))
                        watchlist.actualizar_niveles(e, o.entrada, o.stop, o.objetivo,
                                                     o.zona_entrada_baja, momento)
                        informe.senales.append(Senal(
                            c.ticker, marca, c.resultado.patron, o.entrada, o.stop, o.objetivo,
                            c.resultado.score_ajustado,
                            c.catalizador.tipo if c.catalizador else None))
                    elif c.ticker not in elegidas:
                        run_mod._evaluar_no_disparada(e, c, cfg, momento)
            watchlist.expirar_vencidas(entradas, cfg.minutos_maximos_en_watching, momento)
            momento += timedelta(minutes=1)

    informe.transiciones = {e.ticker: e.estado for e in entradas}
    informe.operaciones = simular_ejecucion(informe.senales, por_ticker, dia, params)
    return informe


# ------------------------------------------------------------- ejecución


def _tamano(entrada: float, stop: float, p: ParametrosEjecucion) -> int:
    """Igual que el ejecutor: riesgo ÷ distancia al stop, hacia abajo, y
    el tope de concentración sobre el efectivo (nunca margen)."""
    if entrada <= 0 or entrada - stop <= 0:
        return 0
    por_riesgo = int(p.riesgo_dolares // (entrada - stop))
    por_concentracion = int((p.efectivo * p.pct_efectivo_por_posicion) // entrada)
    cantidad = min(por_riesgo, por_concentracion)
    return cantidad if cantidad >= p.minimo_acciones else 0


def simular_ejecucion(
    senales: list[Senal], por_ticker: dict[str, DatosSimbolo], dia: date, p: ParametrosEjecucion,
) -> list[Operacion]:
    liquidacion = _en_ny(dia, CIERRE) - timedelta(minutes=p.minutos_antes_del_cierre)
    abiertas: list[tuple[datetime, datetime]] = []   # (llenado, salida)
    out: list[Operacion] = []
    for s in sorted(senales, key=lambda x: x.momento):
        op = Operacion(ticker=s.ticker, senal=s.momento, estado="no_entra")
        out.append(op)
        if s.entrada is None or s.stop is None or s.objetivo is None:
            op.motivo = "niveles_incompletos"
            continue
        orden = datetime.fromisoformat(s.momento) + timedelta(minutes=p.latencia_minutos)
        op.orden_ts = orden.isoformat(timespec="seconds")
        if (_en_ny(dia, CIERRE) - orden).total_seconds() / 60.0 < p.minutos_minimos_para_entrar:
            op.motivo = "cierre_cercano"
            continue
        if sum(1 for ll, sa in abiertas if ll <= orden < sa) >= p.maximo_posiciones:
            op.motivo = "maximo_posiciones"
            continue
        cantidad = _tamano(s.entrada, s.stop, p)
        if cantidad == 0:
            op.motivo = "tamano_cero"
            continue
        op.cantidad = cantidad
        velas = [v for v in por_ticker[s.ticker].por_dia.get(dia, []) if v.t >= orden]
        limite = orden + timedelta(minutes=p.minutos_para_llenar)
        llenado = next((v for v in velas if v.t < limite and v.t < liquidacion and v.l <= s.entrada), None)
        if llenado is None:
            op.estado, op.motivo = "sin_llenar", f"{p.minutos_para_llenar:.0f}_min"
            continue
        precio = min(s.entrada, llenado.o)
        op.llenado_ts, op.precio_llenado = llenado.t.isoformat(timespec="seconds"), round(precio, 4)
        salida_v, salida_p, por = None, None, None
        for v in velas:
            if v.t < llenado.t:
                continue
            if v.t >= liquidacion:
                salida_v, salida_p, por = v, v.o, "cierre"
                break
            if v.l <= s.stop:
                salida_v, salida_p, por = v, min(s.stop, v.o) if v.t > llenado.t else s.stop, "stop"
                break
            if v.t > llenado.t and v.h >= s.objetivo:
                salida_v, salida_p, por = v, max(s.objetivo, v.o), "objetivo"
                break
        if salida_v is None:
            previas = [v for v in velas if v.t < liquidacion]
            if not previas:
                op.estado, op.motivo = "sin_llenar", "sin_velas"
                continue
            salida_v, salida_p, por = previas[-1], previas[-1].c, "cierre"
        op.estado = "cerrada"
        op.salida_ts, op.precio_salida, op.salida_por = (
            salida_v.t.isoformat(timespec="seconds"), round(salida_p, 4), por)
        op.pnl = round((salida_p - precio) * cantidad, 2)
        op.r = round((salida_p - precio) / (s.entrada - s.stop), 3)
        abiertas.append((llenado.t, salida_v.t))
    return out


# --------------------------------------------------------------- informe


LIMITACIONES = (
    "Noticias Benzinga (no Yahoo); metadata actual (no la de esa fecha); se asume el símbolo "
    "escaneado en cada ranura (techo); memoria vacía; IA no simulada (se asume que aprueba todo); "
    "si stop y objetivo caen en la misma vela, se toma el stop."
)


def _hora(iso: str | None) -> str:
    if not iso:
        return "—"
    t = datetime.fromisoformat(iso)
    return f"{t.astimezone(UTC):%H:%M} UTC / {t.astimezone(MTY):%H:%M} MTY"


def formatear_dia(inf: InformeDia) -> str:
    lineas = [f"# Backtest {inf.fecha}", "", f"_Limitaciones: {LIMITACIONES}_", ""]
    lineas.append(f"## Candidatos ({len(inf.candidatos)})")
    for t, tit in sorted(inf.candidatos.items()):
        lineas.append(f"- {t}: {tit}")
    if inf.descartes:
        lineas.append("")
        lineas.append("Descartados: " + ", ".join(f"{t} ({m})" for t, m in sorted(inf.descartes.items())))
    lineas += ["", f"## Señales ({len(inf.senales)})"]
    for s in inf.senales:
        lineas.append(f"- {_hora(s.momento)} {s.ticker} {s.patron or '?'} · entrada {s.entrada} · "
                      f"stop {s.stop} · objetivo {s.objetivo} · score {s.score}")
    lineas += ["", "## Entradas simuladas y resultado"]
    total = 0.0
    for o in inf.operaciones:
        if o.estado == "cerrada":
            total += o.pnl or 0.0
            lineas.append(f"- {o.ticker}: {o.cantidad} acc a {o.precio_llenado} ({_hora(o.llenado_ts)}) → "
                          f"{o.precio_salida} por {o.salida_por} ({_hora(o.salida_ts)}) · P&L {o.pnl} · {o.r} R")
        else:
            lineas.append(f"- {o.ticker}: {o.estado} ({o.motivo})")
    if any(o.estado == "cerrada" for o in inf.operaciones):
        lineas.append(f"\nP&L del día (simulado): {round(total, 2)}")
    return "\n".join(lineas) + "\n"


def _resumen(informes: list[InformeDia]) -> dict:
    ops = [o for i in informes for o in i.operaciones if o.estado == "cerrada"]
    rs = [o.r for o in ops if o.r is not None]
    return {
        "dias": len(informes),
        "dias_con_candidatos": sum(1 for i in informes if i.candidatos),
        "senales": sum(len(i.senales) for i in informes),
        "operaciones_cerradas": len(ops),
        "ganadoras": sum(1 for o in ops if (o.pnl or 0) > 0),
        "pnl_total": round(sum(o.pnl or 0 for o in ops), 2),
        "r_promedio": round(sum(rs) / len(rs), 3) if rs else None,
        "limitaciones": LIMITACIONES,
    }


def correr(
    simbolos: list[str], desde: date, hasta: date, almacen: Path, salida: Path,
    cfg: MomentumConfig = CONFIG, params: ParametrosEjecucion | None = None,
) -> dict:
    exigir_directorio_aislado(salida)
    salida.mkdir(parents=True, exist_ok=True)
    datos = [cargar_simbolo(almacen, s) for s in simbolos]
    dias = sorted({x for d in datos for x in d.por_dia if desde <= x <= hasta})
    informes: list[InformeDia] = []
    for dia in dias:
        inf = simular_dia(dia, datos, cfg, params)
        informes.append(inf)
        (salida / f"{dia.isoformat()}.json").write_text(
            json.dumps(asdict(inf), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (salida / f"{dia.isoformat()}.md").write_text(formatear_dia(inf), encoding="utf-8")
    resumen = _resumen(informes)
    (salida / "resumen.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    return resumen


# ---------------------------------------------------- insumos del almacén


def _meses(desde: date, hasta: date) -> list[tuple[date, date]]:
    out, cursor = [], desde
    while cursor <= hasta:
        siguiente = (cursor.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((cursor, min(hasta, siguiente - timedelta(days=1))))
        cursor = siguiente
    return out


def descargar_noticias(cliente: ClienteDatos, simbolo: str, desde: date, hasta: date, almacen: Path) -> int:
    """Titulares históricos (Benzinga) al almacén, un mes por pedido. Un
    mes que falla corta la descarga: una historia con huecos haría pasar
    "no hubo catalizador" por "no se pidió"."""
    from shadow_alpaca.noticias import pedir_alpaca

    dir_sim = exigir_directorio_aislado(almacen) / simbolo
    dir_sim.mkdir(parents=True, exist_ok=True)
    vistos: set[tuple] = set()
    filas: list[dict] = []
    for ini, fin in _meses(desde, hasta):
        por, fallidos, motivos, _ = pedir_alpaca(
            cliente, [simbolo], datetime.combine(ini, time(0), tzinfo=UTC),
            datetime.combine(fin + timedelta(days=1), time(0), tzinfo=UTC))
        if simbolo in fallidos:
            raise ErrorDatos(motivos.get(simbolo, "noticias"))
        for t in por.get(simbolo, []):
            clave = (t.texto, t.fecha)
            if clave not in vistos:
                vistos.add(clave)
                filas.append({"texto": t.texto, "fuente": t.fuente, "fecha": t.fecha})
    filas.sort(key=lambda f: f["fecha"] or "")
    ruta = dir_sim / "noticias.jsonl"
    ruta.write_text("".join(json.dumps(f, ensure_ascii=False) + "\n" for f in filas), encoding="utf-8")
    return len(filas)


def guardar_metadata(proveedor, simbolos: list[str], almacen: Path) -> int:
    """Metadata ACTUAL (Yahoo) por símbolo, con la fecha de captura. Un
    símbolo sin metadata no se escribe: el backtest lo descarta."""
    meta = proveedor.metadata(simbolos)
    capturada = datetime.now(UTC).isoformat(timespec="seconds")
    n = 0
    for s in simbolos:
        m = meta.get(s)
        if m is None:
            continue
        dir_sim = exigir_directorio_aislado(almacen) / s
        dir_sim.mkdir(parents=True, exist_ok=True)
        (dir_sim / "metadata.json").write_text(json.dumps(
            {"capturada": capturada, "nota": "metadata actual, no la de la fecha simulada",
             "metadata": asdict(m)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        n += 1
    return n


# -------------------------------------------------------------------- CLI


def _fecha(texto: str) -> date:
    return date.fromisoformat(texto)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest offline del hunter sobre velas SIP de 1 minuto.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    comun = argparse.ArgumentParser(add_help=False)
    comun.add_argument("--simbolos", required=True)
    comun.add_argument("--almacen", type=Path, required=True)
    p_n = sub.add_parser("noticias", parents=[comun])
    p_n.add_argument("--desde", required=True)
    p_n.add_argument("--hasta", default=None)
    sub.add_parser("metadata", parents=[comun])
    p_c = sub.add_parser("correr", parents=[comun])
    p_c.add_argument("--desde", required=True)
    p_c.add_argument("--hasta", required=True)
    p_c.add_argument("--salida", type=Path, required=True)
    p_c.add_argument("--efectivo", type=float, default=ParametrosEjecucion.efectivo)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    simbolos = [s.strip().upper() for s in args.simbolos.split(",") if s.strip()]
    try:
        exigir_directorio_aislado(args.almacen)
    except ValueError:
        log.warning("backtest: el almacén cae dentro de un paquete operativo")
        return 2
    if args.cmd == "noticias":
        cliente = ClienteDatos()
        hasta = _fecha(args.hasta) if args.hasta else datetime.now(UTC).date()
        for s in simbolos:
            try:
                n = descargar_noticias(cliente, s, _fecha(args.desde), hasta, args.almacen)
            except ErrorDatos as ex:
                log.warning("backtest: noticias de %s fallaron (%s)", s, ex.codigo)
                return 2
            log.info("backtest: %s titulares=%d", s, n)
        return 0
    if args.cmd == "metadata":
        from momentum_hunter.data.provider import YahooProvider
        n = guardar_metadata(YahooProvider(), simbolos, args.almacen)
        log.info("backtest: metadata de %d/%d símbolos", n, len(simbolos))
        return 0
    try:
        exigir_directorio_aislado(args.salida)
    except ValueError:
        log.warning("backtest: la salida cae dentro de un paquete operativo")
        return 2
    resumen = correr(simbolos, _fecha(args.desde), _fecha(args.hasta), args.almacen, args.salida,
                     params=ParametrosEjecucion(efectivo=args.efectivo))
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
