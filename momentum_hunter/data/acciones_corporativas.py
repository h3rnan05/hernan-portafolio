"""Acciones corporativas (splits, dividendos, fusiones...) del feed de
datos de Alpaca -- `GET /v1/corporate-actions`, host de DATOS, solo
lectura. Este módulo no conoce la cuenta ni el host de trading.

POR QUÉ EXISTE. Un split, un dividendo o una fusión mueven el precio sin
que nadie haya comprado ni vendido nada. Para el hunter eso es veneno:
un reverse split 1:10 sin ajustar es un "+900 % con gap" y un máximo de
52 semanas roto, o sea exactamente la foto de una ruptura. Hasta hoy el
sistema confiaba en que Yahoo o el feed devolvieran la historia ya
ajustada y no lo verificaba nunca.

DOS REGLAS, LAS DOS DETERMINISTAS:

1. Día de la acción = día sin señal. Si un símbolo tiene una acción
   corporativa con fecha efectiva HOY (ex-date o effective date, en la
   fecha de Nueva York), el hunter no lo dispara ni le refresca niveles.
   Sin niveles frescos el ejecutor no puede operarlo (su tope de 15 min
   hace el resto), así que esta guardia no necesita tocar al ejecutor.
   Es literal a propósito: también bloquea un dividendo trimestral chico.
   Distinguir "dividendo que importa" de "dividendo que no" exigiría un
   umbral nuevo que hoy nadie puede calibrar.

2. Historia verificada, no supuesta. Para cada split / dividendo en
   acciones dentro de la ventana de las barras diarias, se mira el salto
   entre el cierre anterior a la ex-date y la apertura de la ex-date:
     - si se parece a 1, la serie ya viene ajustada: no se toca;
     - si se parece al factor del split, la serie viene cruda: se ajusta
       (precio / factor, volumen x factor antes de la ex-date);
     - si no se parece claramente a ninguno de los dos, no se adivina:
       el símbolo queda bloqueado para disparar ese día.

FAIL-CLOSED. Sin credenciales, sin respuesta, con una página ilegible o
con un registro sin fecha legible: no se dispara. Un símbolo sin fecha
legible se bloquea él solo; un fallo del pedido bloquea a todos. Un
registro sin ningún campo de símbolo no se puede atribuir a nadie: se
cuenta y se registra, no se inventa a quién pertenece.

VENTANA DEL PEDIDO. No está documentado con certeza qué fecha filtra
`start`/`end` en este endpoint (un dividendo en efectivo suele tener
`process_date` = día de pago, semanas después de la ex-date). Por eso la
guardia pide un rango ancho alrededor de hoy (-7 / +75 días) y filtra
localmente por las fechas que sí mueven el precio. Pedir de más cuesta
un par de registros; pedir de menos dejaría pasar la ex-date de hoy.

MODO (`MOMENTUM_CORP_ACTIONS`):
  - `observar` (default mientras no haya una sesión limpia): se consulta
    todo igual y se registra QUÉ se habría bloqueado o ajustado, pero no
    se bloquea ni se toca ninguna serie. Es el comportamiento anterior
    más el registro.
  - `enforce`: bloquea y ajusta como se describe arriba. Se pasa a este
    modo a mano, después de una sesión limpia en `observar`
    (`python -m momentum_hunter.data.acciones_corporativas --resumen`).
  - `off`: ni consulta ni registra (emergencia), con WARNING por corrida.
  Un valor desconocido se trata como `enforce` (fail-closed) y se avisa:
  una errata no debe apagar una protección en silencio.

Registro fuera de git: `MOMENTUM_ACCIONES_CORP_LOG` (JSONL). Sin la
variable se usa `/var/lib/momentum/acciones_corporativas.jsonl` si ese
directorio existe (el VPS); si no, solo queda el log de la corrida.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momentum_hunter.data.alpaca_datos import AlpacaProvider, ErrorDatosAlpaca
from momentum_hunter.models import Barras

log = logging.getLogger("momentum_hunter.data.acciones_corporativas")

RUTA = "/v1/corporate-actions"
LIMITE_PAGINA = 1000
NY = ZoneInfo("America/New_York")

ENV_MODO = "MOMENTUM_CORP_ACTIONS"
MODO_OBSERVAR = "observar"
MODO_ENFORCE = "enforce"
MODO_APAGADO = "off"
MODO_DEFAULT = MODO_OBSERVAR
ENV_LOG = "MOMENTUM_ACCIONES_CORP_LOG"
LOG_DEFAULT = Path("/var/lib/momentum/acciones_corporativas.jsonl")

# Rango del pedido de la guardia -- ver "VENTANA DEL PEDIDO" arriba.
DIAS_ATRAS_GUARDIA = 7
DIAS_ADELANTE_GUARDIA = 75

# Tipos que cambian la cantidad de acciones y, por lo tanto, la escala
# del precio. Son los únicos que se verifican contra las barras. El
# `unit_split` (unidad -> acción + warrant) no tiene un factor de precio
# limpio: no se ajusta, y si cae en la ventana de hoy la guardia lo
# bloquea como cualquier otra acción.
TIPOS_ESCALA = ("forward_split", "reverse_split", "stock_dividend")

# Cuánto tiene que parecerse el salto observado a una de las dos
# hipótesis (ya ajustada / cruda) para aceptarla: menos de un cuarto de
# la distancia logarítmica entre ellas. Para un split 2:1 eso es "salto
# entre 0,84x y 1,19x = ajustada" y "entre 1,68x y 2,38x = cruda"; lo
# que quede en medio no se adivina. No es un umbral de trading: no
# cambia qué se considera una oportunidad, solo cuándo la serie de
# precios es confiable. Es razonamiento, no calibración (no hay datos
# para calibrarlo), y está anotado así.
FRACCION_CONFIANZA = 0.25


# ---------------------------------------------------------------- modelo


@dataclass(frozen=True)
class AccionCorporativa:
    """Un registro del feed, normalizado. `simbolos` son todos los
    campos `*symbol` del registro (una fusión afecta al comprador y al
    comprado). `factor` = acciones nuevas por acción vieja, solo para
    `TIPOS_ESCALA` y solo si el feed trajo los dos números; None no es 1."""

    tipo: str
    simbolos: tuple[str, ...]
    ex_date: date | None
    effective_date: date | None
    process_date: date | None
    factor: float | None = None

    def fecha_efectiva(self) -> date | None:
        """La fecha en que el PRECIO cambia de escala o de dueño: ex-date
        si existe, si no effective date. `process_date` solo cuando el
        tipo no trae ninguna de las otras (cambio de nombre, baja): en un
        dividendo en efectivo el process_date es el pago, que no mueve
        el precio."""
        return self.ex_date or self.effective_date or (
            self.process_date if self.ex_date is None and self.effective_date is None else None)


@dataclass
class Guardia:
    """Veredicto de una corrida. `disponible=False` bloquea TODO: no se
    pudo saber, y no saber no es "no hay"."""

    fecha: date
    disponible: bool
    codigo: str | None = None
    apagada: bool = False
    # Modo `observar`: `motivo` dice qué bloquearía, `bloquea` nunca bloquea.
    observando: bool = False
    hoy: dict[str, tuple[str, ...]] = field(default_factory=dict)
    sin_fecha: set[str] = field(default_factory=set)
    sin_verificar: dict[str, str] = field(default_factory=dict)
    ajustados: dict[str, str] = field(default_factory=dict)
    sin_simbolo: int = 0

    def motivo(self, ticker: str) -> str | None:
        """None = puede disparar. Un string = por qué no. Siempre un
        código corto, nunca el cuerpo de una respuesta."""
        if self.apagada:
            return None
        if not self.disponible:
            return f"acciones_corporativas_no_disponibles:{self.codigo or 'desconocido'}"
        clave = normalizar(ticker)
        if clave in self.hoy:
            return "accion_corporativa_hoy:" + ",".join(self.hoy[clave])
        if clave in self.sin_fecha:
            return "accion_corporativa_sin_fecha_legible"
        if clave in self.sin_verificar:
            return "split_sin_verificar:" + self.sin_verificar[clave]
        return None

    def bloquea(self, ticker: str) -> str | None:
        """Lo que el caller debe respetar: el motivo en `enforce`, None
        en `observar` (el motivo igual se registra, ver `run.py`)."""
        return None if self.observando else self.motivo(ticker)


def normalizar(ticker: str) -> str:
    """Una sola forma de clave para los dos estilos de clase: el
    universo escribe `BRK-B` (Yahoo) y el feed `BRK.B`."""
    return str(ticker).strip().upper().replace(".", "-").replace("/", "-")


def modo() -> str:
    crudo = os.environ.get(ENV_MODO, "").strip().lower()
    if not crudo:
        return MODO_DEFAULT
    if crudo in (MODO_OBSERVAR, MODO_ENFORCE, MODO_APAGADO):
        return crudo
    log.warning("acciones corporativas: %s=%r no es observar/enforce/off -- se aplica enforce",
                ENV_MODO, crudo)
    return MODO_ENFORCE


def hoy_ny(ahora: datetime | None = None) -> date:
    ahora = ahora or datetime.now(UTC)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=UTC)
    return ahora.astimezone(NY).date()


# --------------------------------------------------------------- parseo


def _fecha(valor: object) -> date | None:
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        return date.fromisoformat(valor.strip()[:10])
    except ValueError:
        return None


def _positivo(valor: object) -> float | None:
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        v = float(valor)
    elif isinstance(valor, str):
        try:
            v = float(valor.strip())
        except ValueError:
            return None
    else:
        return None
    return v if math.isfinite(v) and v > 0 else None


def _factor(tipo: str, crudo: dict) -> float | None:
    if tipo in ("forward_split", "reverse_split"):
        nuevo, viejo = _positivo(crudo.get("new_rate")), _positivo(crudo.get("old_rate"))
        return nuevo / viejo if nuevo is not None and viejo is not None else None
    if tipo == "stock_dividend":
        # `rate` = acciones nuevas por acción vieja (0,05 = 5 %).
        tasa = _positivo(crudo.get("rate"))
        return 1.0 + tasa if tasa is not None else None
    return None


def _tipo_de_grupo(grupo: str) -> str:
    """`forward_splits` -> `forward_split`. El feed agrupa en plural."""
    return grupo[:-1] if grupo.endswith("s") else grupo


def parsear_pagina(cuerpo: object) -> tuple[list[AccionCorporativa], int]:
    """(acciones, registros_sin_simbolo). Un cuerpo sin la forma esperada
    es un error de datos, no "cero acciones": levanta."""
    if not isinstance(cuerpo, dict):
        raise ErrorDatosAlpaca("cuerpo")
    grupos = cuerpo.get("corporate_actions")
    if grupos is None:
        # Página vacía legítima: el feed omite la clave cuando no hay nada.
        return [], 0
    if not isinstance(grupos, dict):
        raise ErrorDatosAlpaca("cuerpo")
    acciones: list[AccionCorporativa] = []
    sin_simbolo = 0
    for grupo, registros in grupos.items():
        if registros is None:
            continue
        if not isinstance(registros, list):
            raise ErrorDatosAlpaca("cuerpo")
        tipo = _tipo_de_grupo(str(grupo))
        for crudo in registros:
            if not isinstance(crudo, dict):
                sin_simbolo += 1
                continue
            simbolos = tuple(sorted({
                normalizar(v) for k, v in crudo.items()
                if k.endswith("symbol") and isinstance(v, str) and v.strip()
            }))
            if not simbolos:
                sin_simbolo += 1
                continue
            acciones.append(AccionCorporativa(
                tipo=tipo,
                simbolos=simbolos,
                ex_date=_fecha(crudo.get("ex_date")),
                effective_date=_fecha(crudo.get("effective_date")),
                process_date=_fecha(crudo.get("process_date")),
                factor=_factor(tipo, crudo),
            ))
    return acciones, sin_simbolo


# --------------------------------------------------------------- pedido


class ClienteAcciones:
    """Transporte: reutiliza el `_get`/`_paginas` de `AlpacaProvider`
    (mismas claves de solo lectura, mismos reintentos, mismo manejo de
    429/5xx y el mismo tope de páginas). Se inyecta en pruebas."""

    def __init__(self, transporte: AlpacaProvider | None = None) -> None:
        self._t = transporte or AlpacaProvider(feed="sip")
        self.pedidos = 0

    def pedir(
        self, desde: date, hasta: date, simbolos: list[str] | None = None,
        tipos: tuple[str, ...] | None = None,
    ) -> tuple[list[AccionCorporativa], int]:
        params: dict = {
            "start": desde.isoformat(),
            "end": hasta.isoformat(),
            "limit": LIMITE_PAGINA,
            "sort": "asc",
        }
        if simbolos:
            # El feed usa punto para la clase (`BRK.B`).
            params["symbols"] = ",".join(sorted({normalizar(s).replace("-", ".") for s in simbolos}))
        if tipos:
            params["types"] = ",".join(tipos)
        paginas = self._t._paginas(RUTA, params)
        self.pedidos += len(paginas)
        acciones: list[AccionCorporativa] = []
        sin_simbolo = 0
        for p in paginas:
            a, s = parsear_pagina(p)
            acciones.extend(a)
            sin_simbolo += s
        return acciones, sin_simbolo


# --------------------------------------------------------------- guardia


def _aplicar_hoy(guardia: Guardia, acciones: list[AccionCorporativa], interes: set[str]) -> None:
    hoy: dict[str, set[str]] = {}
    for a in acciones:
        tocados = [s for s in a.simbolos if s in interes] if interes else list(a.simbolos)
        if not tocados:
            continue
        efectiva = a.fecha_efectiva()
        if efectiva is None:
            # Sin fecha legible no se sabe si es hoy: fail-closed por
            # símbolo, no por corrida.
            guardia.sin_fecha.update(tocados)
            continue
        if efectiva == guardia.fecha:
            for s in tocados:
                hoy.setdefault(s, set()).add(a.tipo)
    for s, tipos in hoy.items():
        guardia.hoy[s] = tuple(sorted(tipos))


def _heredar(guardia: Guardia, base: Guardia | None) -> Guardia:
    """Lo que ya se supo de la historia (escaneo) no se pierde al
    consultar el día: un símbolo sin verificar sigue bloqueado y una
    historia caída sigue bloqueando todo."""
    if base is None:
        return guardia
    guardia.sin_verificar.update(base.sin_verificar)
    guardia.ajustados.update(base.ajustados)
    if not base.disponible and guardia.disponible:
        guardia.disponible, guardia.codigo = False, base.codigo
    return guardia


def consultar(
    tickers: list[str], ahora: datetime | None = None, cliente: ClienteAcciones | None = None,
    base: Guardia | None = None,
) -> Guardia:
    """Guardia del día para `tickers`. Nunca levanta: cualquier fallo
    vuelve como `disponible=False` (que bloquea todo). `base` es lo que
    dejó `verificar_historia` en el escaneo."""
    fecha = hoy_ny(ahora)
    m = modo()
    if m == MODO_APAGADO:
        log.warning("acciones corporativas: APAGADAS por %s=off -- no se verifica nada", ENV_MODO)
        return Guardia(fecha=fecha, disponible=True, apagada=True)
    guardia = _consultar(tickers, fecha, cliente, base)
    guardia.observando = m == MODO_OBSERVAR
    if not guardia.disponible:
        registrar("consulta_fallida", "*", guardia.codigo or "desconocido", ahora)
    return guardia


def _consultar(
    tickers: list[str], fecha: date, cliente: ClienteAcciones | None, base: Guardia | None,
) -> Guardia:
    interes = {normalizar(t) for t in tickers if isinstance(t, str) and t.strip()}
    if not interes:
        return _heredar(Guardia(fecha=fecha, disponible=True), base)
    try:
        cliente = cliente or ClienteAcciones()
        acciones, sin_simbolo = cliente.pedir(
            fecha - timedelta(days=DIAS_ATRAS_GUARDIA),
            fecha + timedelta(days=DIAS_ADELANTE_GUARDIA),
            simbolos=sorted(interes),
        )
    except ErrorDatosAlpaca as ex:
        log.warning("acciones corporativas: no se pudieron consultar (%s) -- no se dispara nada", ex.codigo)
        return _heredar(Guardia(fecha=fecha, disponible=False, codigo=ex.codigo), base)
    except Exception as ex:   # noqa: BLE001 -- fail-closed, se registra el TIPO
        log.warning("acciones corporativas: fallo inesperado (%s) -- no se dispara nada", type(ex).__name__)
        return _heredar(Guardia(fecha=fecha, disponible=False, codigo=type(ex).__name__), base)
    guardia = _heredar(Guardia(fecha=fecha, disponible=True, sin_simbolo=sin_simbolo), base)
    _aplicar_hoy(guardia, acciones, interes)
    if sin_simbolo:
        log.warning("acciones corporativas: %d registro(s) sin símbolo -- no se atribuyen a nadie", sin_simbolo)
    for s, tipos in sorted(guardia.hoy.items()):
        log.info("acciones corporativas: %s tiene %s con fecha hoy (%s) -- motivo de bloqueo",
                 s, "/".join(tipos), fecha.isoformat())
    return guardia


# ---------------------------------------------------- verificar y ajustar


def fecha_de_barra(valor: object) -> date | None:
    """Día de sesión de una barra diaria. El contrato de `Barras.fechas`
    es epoch en segundos como texto (Yahoo y el feed; `run._cierre_
    anterior` hace `int(...)`), no ISO. Se lee en la fecha de Nueva
    York: la vela diaria del feed está sellada a las 00:00 ET (04:00
    UTC) y la de Yahoo a la apertura; las dos caen en su propio día.
    Un ISO también se acepta (pruebas, datos viejos). Ilegible -> None."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    texto = valor.strip()
    if texto.isdigit():
        try:
            return datetime.fromtimestamp(int(texto), tz=UTC).astimezone(NY).date()
        except (OverflowError, OSError, ValueError):
            return None
    return _fecha(texto)


def _fechas_de(b: Barras) -> list[date | None]:
    return [fecha_de_barra(f) for f in b.fechas]


def verificar_escala(b: Barras, accion: AccionCorporativa) -> str:
    """'ajustada' | 'cruda' | 'ambigua' | 'fuera_de_rango' | 'sin_factor'.
    Compara el cierre previo a la ex-date con la apertura de la ex-date."""
    if accion.factor is None:
        return "sin_factor"
    ex = accion.ex_date or accion.effective_date
    if ex is None or not b.fechas:
        return "fuera_de_rango"
    fechas = _fechas_de(b)
    if any(f is None for f in fechas):
        # Una fecha ilegible impide saber de qué lado del split cae cada
        # vela: no se ajusta a ciegas.
        return "ambigua"
    i = next((k for k, f in enumerate(fechas) if f >= ex), None)
    if i is None or i == 0:
        # La ex-date cae antes de la primera vela o después de la última:
        # no hay salto que medir dentro de esta serie.
        return "fuera_de_rango"
    previo, apertura = b.close[i - 1], b.open[i]
    if not previo or not apertura or previo <= 0 or apertura <= 0:
        return "ambigua"
    salto = math.log(previo / apertura)
    distancia = abs(math.log(accion.factor))
    if distancia == 0:
        return "ajustada"
    margen = FRACCION_CONFIANZA * distancia
    if abs(salto) <= margen:
        return "ajustada"
    if abs(salto - math.log(accion.factor)) <= margen:
        return "cruda"
    return "ambigua"


def ajustar(b: Barras, accion: AccionCorporativa) -> Barras:
    """Lleva la parte previa a la ex-date a la escala de hoy. Solo se
    llama con un veredicto 'cruda'."""
    ex = accion.ex_date or accion.effective_date
    f = accion.factor
    antes = [d is not None and d < ex for d in _fechas_de(b)]
    return replace(
        b,
        open=[v / f if a else v for v, a in zip(b.open, antes, strict=True)],
        close=[v / f if a else v for v, a in zip(b.close, antes, strict=True)],
        high=[v / f if a else v for v, a in zip(b.high, antes, strict=True)],
        low=[v / f if a else v for v, a in zip(b.low, antes, strict=True)],
        volume=[v * f if a else v for v, a in zip(b.volume, antes, strict=True)],
    )


def verificar_historia(
    barras: dict[str, Barras], ahora: datetime | None = None,
    cliente: ClienteAcciones | None = None,
) -> tuple[dict[str, Barras], Guardia]:
    """Pide los cambios de escala de todo el mercado en la ventana de
    `barras` (un par de páginas: no hace falta la lista de símbolos),
    verifica cada serie afectada y devuelve (barras con las crudas ya
    ajustadas, guardia parcial con qué se ajustó y qué no se pudo
    verificar). Si el pedido falla, la guardia sale no disponible."""
    fecha = hoy_ny(ahora)
    m = modo()
    guardia = Guardia(fecha=fecha, disponible=True, observando=m == MODO_OBSERVAR)
    if m == MODO_APAGADO:
        guardia.apagada = True
        return barras, guardia
    primeras = [fecha_de_barra(b.fechas[0]) for b in barras.values() if b.fechas]
    legibles = [d for d in primeras if d is not None]
    if not legibles:
        return barras, guardia
    # Una serie con la primera fecha ilegible queda "ambigua" en
    # `verificar_escala` si tiene un split; no tumba a las demás.
    desde = min(legibles)
    try:
        cliente = cliente or ClienteAcciones()
        acciones, _ = cliente.pedir(desde, fecha + timedelta(days=DIAS_ATRAS_GUARDIA), tipos=TIPOS_ESCALA)
    except ErrorDatosAlpaca as ex:
        log.warning("acciones corporativas: historia de splits no disponible (%s) -- no se dispara nada",
                    ex.codigo)
        guardia.disponible, guardia.codigo = False, f"historia:{ex.codigo}"
        registrar("consulta_fallida", "*", guardia.codigo, ahora)
        return barras, guardia
    except Exception as ex:   # noqa: BLE001
        log.warning("acciones corporativas: fallo inesperado en historia (%s)", type(ex).__name__)
        guardia.disponible, guardia.codigo = False, f"historia:{type(ex).__name__}"
        registrar("consulta_fallida", "*", guardia.codigo, ahora)
        return barras, guardia

    por_simbolo: dict[str, list[AccionCorporativa]] = {}
    for a in acciones:
        for s in a.simbolos:
            por_simbolo.setdefault(s, []).append(a)
    out = dict(barras)
    for ticker, b in barras.items():
        propias = por_simbolo.get(normalizar(ticker))
        if not propias:
            continue
        # Se verifican todas contra la serie ORIGINAL: ajustar una no
        # cambia el salto de otra (las dos mitades se escalan igual).
        veredictos = [(a, verificar_escala(b, a)) for a in propias]
        fechas = _fechas_de(b)
        primera, ultima = (fechas[0], fechas[-1]) if fechas else (None, None)

        def _en_rango(a: AccionCorporativa) -> bool:
            ex = a.ex_date or a.effective_date
            if ex is None:
                return False
            if primera is None or ultima is None:
                return True   # serie ilegible: no se puede descartar
            return primera < ex <= ultima

        dudosas = [(a, v) for a, v in veredictos
                   if v in ("ambigua", "sin_factor") and _en_rango(a)]
        if dudosas:
            a, v = dudosas[0]
            guardia.sin_verificar[normalizar(ticker)] = f"{a.tipo}:{v}"
            registrar("observacion_sin_verificar" if guardia.observando else "sin_verificar",
                      ticker, f"{a.tipo}:{v}", ahora)
            log.warning("acciones corporativas: %s tiene %s del %s y la serie no se puede verificar (%s) "
                        "-- motivo de bloqueo hoy", ticker, a.tipo, (a.ex_date or a.effective_date), v)
            continue
        nueva = b
        for a, v in veredictos:
            if v == "cruda":
                nueva = ajustar(nueva, a)
                guardia.ajustados[normalizar(ticker)] = f"{a.tipo}:{a.factor:g}"
                registrar("observacion_ajuste" if guardia.observando else "ajuste",
                          ticker, f"{a.tipo}:{a.factor:g}", ahora)
                log.warning("acciones corporativas: %s venía SIN ajustar por %s (x%g, ex %s) -- %s",
                            ticker, a.tipo, a.factor, a.ex_date or a.effective_date,
                            "se AJUSTARÍA (observación)" if guardia.observando else "ajustada")
        if not guardia.observando:
            out[ticker] = nueva
    return out, guardia


# ---------------------------------------------------------------- registro


def _ruta_log() -> Path | None:
    crudo = os.environ.get(ENV_LOG, "").strip()
    if crudo:
        return Path(crudo)
    return LOG_DEFAULT if LOG_DEFAULT.parent.is_dir() else None


def registrar(evento: str, ticker: str, motivo: str, ahora: datetime | None = None) -> None:
    """Una línea JSONL fuera de git por bloqueo o ajuste. Nunca tumba la
    corrida: el registro es un extra, el bloqueo ya ocurrió."""
    ruta = _ruta_log()
    if ruta is None:
        return
    fila = {
        "ts": (ahora or datetime.now(UTC)).isoformat(timespec="seconds"),
        "evento": evento,
        "ticker": ticker,
        "motivo": motivo,
    }
    try:
        with ruta.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(fila, ensure_ascii=False) + "\n")
    except OSError as ex:
        log.debug("acciones corporativas: no se pudo escribir el registro (%s)", type(ex).__name__)


# ----------------------------------------------------- ¿sesión limpia?


def resumen_sesion(fecha_ny: str, ruta: Path | None = None) -> dict[str, dict[str, int]]:
    """evento -> {ticker: veces} de una fecha de Nueva York, leído del
    JSONL. Es lo que se mira antes de pasar de `observar` a `enforce`."""
    ruta = ruta or _ruta_log()
    out: dict[str, dict[str, int]] = {}
    if ruta is None or not ruta.exists():
        return out
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            fila = json.loads(linea)
            ts = datetime.fromisoformat(fila["ts"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if hoy_ny(ts).isoformat() != fecha_ny:
            continue
        por = out.setdefault(str(fila.get("evento")), {})
        por[str(fila.get("ticker"))] = por.get(str(fila.get("ticker")), 0) + 1
    return out


def formatear_resumen(resumen: dict[str, dict[str, int]], fecha_ny: str) -> str:
    """"Limpia" = ninguna consulta fallida y ningún bloqueo u observación
    que el dueño no pueda explicar con una acción corporativa real. Lo
    primero lo dice este texto; lo segundo lo decide una persona."""
    if not resumen:
        return (f"{fecha_ny}: sin registros (¿corrió la guardia? ¿existe el JSONL?). "
                "Sin registros no hay evidencia de sesión limpia.")
    lineas = [f"Acciones corporativas, {fecha_ny}:"]
    for evento, por in sorted(resumen.items()):
        detalle = ", ".join(f"{t}×{n}" for t, n in sorted(por.items()))
        lineas.append(f"  {evento}: {sum(por.values())} ({detalle})")
    fallidas = sum(resumen.get("consulta_fallida", {}).values())
    lineas.append("  consultas fallidas: " + ("NINGUNA" if not fallidas else
                                              f"{fallidas} -- en enforce, esos minutos no habrían disparado"))
    return "\n".join(lineas)


# ------------------------------------------------------------ verificación


def _probar(argv: list[str] | None = None) -> int:
    """`python -m momentum_hunter.data.acciones_corporativas AAPL NVDA`

    Para correr UNA vez en el VPS antes de confiar en la guardia: hace
    los dos pedidos reales (guardia del día y splits del último año) e
    imprime conteos, sin tocar la watchlist. Si algún parámetro del
    endpoint no fuera el que este módulo supone, sale aquí como
    `no disponible (http_400)`, y no como un día entero sin disparos."""
    import sys
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "--resumen":
        fecha = args[1] if len(args) > 1 else hoy_ny().isoformat()
        print(formatear_resumen(resumen_sesion(fecha), fecha))
        return 0
    tickers = args or ["AAPL"]
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ahora = datetime.now(UTC)
    guardia = consultar(tickers, ahora)
    print(f"fecha NY: {guardia.fecha}  disponible: {guardia.disponible}  código: {guardia.codigo}")
    for t in tickers:
        print(f"  {t}: {guardia.motivo(t) or 'puede disparar'}")
    cliente = ClienteAcciones()
    try:
        acciones, sin_simbolo = cliente.pedir(
            hoy_ny(ahora) - timedelta(days=365), hoy_ny(ahora), tipos=TIPOS_ESCALA)
    except ErrorDatosAlpaca as ex:
        print(f"historia de splits: NO disponible ({ex.codigo})")
        return 1
    por_tipo: dict[str, int] = {}
    for a in acciones:
        por_tipo[a.tipo] = por_tipo.get(a.tipo, 0) + 1
    print(f"historia de splits (365 d, todo el mercado): {len(acciones)} en {cliente.pedidos} página(s) "
          f"{por_tipo}  sin símbolo: {sin_simbolo}")
    return 0 if guardia.disponible else 1


if __name__ == "__main__":
    raise SystemExit(_probar())
