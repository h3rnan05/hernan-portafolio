"""Bandera `MOMENTUM_ESTRATEGIA` y carga estricta de `config/estrategia_v2.yaml`.

QUÉ ESTRATEGIA CORRE (`estado()`):

  bandera   YAML válido          YAML inválido o ausente
  -------   ------------------   -----------------------------------------
  v1        v1 + sombra v2 (*)   v1 sin sombra; se avisa. La v1 no depende
                                 de este archivo y no se entera.
  v2        v2                   BLOQUEADO: ni v1 ni v2 abren entradas.
                                 Correr la v1 cuando se pidió la v2 sería
                                 cambiar de estrategia en silencio.

  (*) si `sombra.activa_con_v1` es true.

Un valor de bandera desconocido cuenta como v1 (el estado conocido) y se
avisa. Sin la variable: v1.

POR QUÉ TAN ESTRICTO. Cada parámetro de la v2 vive en el YAML y en
ningún otro lado. Si un campo falta, sobra (una errata como
`rvol_mim`) o tiene un tipo o rango imposible, el archivo entero se
rechaza: un default silencioso sería un umbral que nadie aprobó. Los
chequeos de rango de abajo (un porcentaje entre 0 y 1, un mínimo menor
que su máximo) no son parámetros de estrategia: son la frontera entre
un número posible y una errata.

PyYAML se importa recién al cargar. El VPS no instala dependencias al
desplegar (solo `git pull`): si falta, la v2 queda sin configuración
(`sin_pyyaml`) y la v1 sigue igual.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

log = logging.getLogger("estrategia_v2")

ENV_BANDERA = "MOMENTUM_ESTRATEGIA"
ENV_RUTA = "MOMENTUM_ESTRATEGIA_V2_CONFIG"
RUTA_DEFAULT = Path(__file__).resolve().parents[1] / "config" / "estrategia_v2.yaml"
V1, V2, BLOQUEADO = "v1", "v2", "bloqueado"
VERSION_SOPORTADA = 1


class ConfigV2Invalida(Exception):
    """`codigo` corto para el log; `detalle` dice qué campo."""

    def __init__(self, codigo: str, detalle: str = "") -> None:
        self.codigo, self.detalle = codigo, detalle
        super().__init__(f"{codigo}: {detalle}" if detalle else codigo)


# ------------------------------------------------------------------ modelo


@dataclass(frozen=True)
class Universo:
    exchanges: tuple[str, ...]
    excluir_otc: bool
    excluir_etf: bool
    excluir_spac: bool
    precio_min: float
    precio_max: float
    float_min: float
    float_max: float
    float_faltante: str
    minutos_sin_halt: int
    accion_corporativa_hoy: str


@dataclass(frozen=True)
class Catalizador:
    fuente: str
    horas_maximas: float
    modelo: str
    prompt_version: int
    niveles: dict[int, tuple[str, ...]]
    niveles_operables: tuple[int, ...]
    direcciones_operables: tuple[str, ...]
    veto: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class Senal:
    zona_horaria: str
    gap_min_pct: float
    rvol_min: float
    rvol_dias: int
    precio_sobre_vwap: bool
    rango_apertura_inicio: time
    rango_apertura_fin: time
    vela_ruptura_vol_min_x: float
    spread_max_pct: float
    indice_referencia: str
    indice_sobre_vwap: bool
    ventana_inicio: time
    ventana_fin: time
    minutos_maximos_niveles: float


@dataclass(frozen=True)
class Riesgo:
    riesgo_pct_equity: float
    tope_posicion_pct_equity: float
    stop_origen: str
    stop_min_pct: float
    stop_max_pct: float
    stop_si_excede_max: str
    objetivo_r: float
    breakeven_en_r: float
    stop_tiempo_minutos: float
    stop_tiempo_r_minimo: float
    max_posiciones: int
    max_entradas_dia: int
    freno_diario_pct: float
    freno_semanal_pct: float
    tamano_por_nivel: dict[int, float]
    solo_largos: bool
    ia_decide_entradas: bool
    cierre_fin_de_dia: str


@dataclass(frozen=True)
class Sombra:
    activa_con_v1: bool
    directorio: Path


@dataclass(frozen=True)
class ConfigV2:
    version: int
    universo: Universo
    catalizador: Catalizador
    senal: Senal
    riesgo: Riesgo
    sombra: Sombra
    ruta: Path


@dataclass(frozen=True)
class EstadoV2:
    """Qué corre en este proceso. `config` es None si no se pudo cargar;
    `error` dice por qué (código corto)."""

    bandera: str
    modo: str               # v1 | v2 | bloqueado
    sombra: bool
    config: ConfigV2 | None
    error: str | None


# -------------------------------------------------------------- lectores


class _Seccion:
    """Lee una sección exigiendo exactamente sus claves."""

    def __init__(self, nombre: str, crudo: object, claves: tuple[str, ...]) -> None:
        if not isinstance(crudo, dict):
            raise ConfigV2Invalida("seccion", nombre)
        faltan = [k for k in claves if k not in crudo]
        sobran = [k for k in crudo if k not in claves]
        if faltan:
            raise ConfigV2Invalida("falta_campo", f"{nombre}.{faltan[0]}")
        if sobran:
            raise ConfigV2Invalida("campo_desconocido", f"{nombre}.{sobran[0]}")
        self.nombre, self.d = nombre, crudo

    def _mal(self, clave: str, que: str) -> ConfigV2Invalida:
        return ConfigV2Invalida("valor_invalido", f"{self.nombre}.{clave}: {que}")

    def booleano(self, k: str) -> bool:
        v = self.d[k]
        if not isinstance(v, bool):
            raise self._mal(k, "se esperaba true/false")
        return v

    def numero(self, k: str, *, minimo=None, maximo=None, abierto_min=False, entero=False) -> float:
        v = self.d[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise self._mal(k, "se esperaba un número")
        if entero and not isinstance(v, int):
            raise self._mal(k, "se esperaba un entero")
        if minimo is not None and (v <= minimo if abierto_min else v < minimo):
            raise self._mal(k, "fuera de rango")
        if maximo is not None and v > maximo:
            raise self._mal(k, "fuera de rango")
        return v

    def fraccion(self, k: str) -> float:
        """Un porcentaje escrito como fracción: 0 < v < 1."""
        v = self.numero(k, minimo=0, abierto_min=True)
        if v >= 1:
            raise self._mal(k, "se esperaba una fracción (0,1)")
        return float(v)

    def perdida(self, k: str) -> float:
        """Un freno: fracción negativa, -1 < v < 0."""
        v = self.numero(k, maximo=0)
        if v == 0 or v <= -1:
            raise self._mal(k, "se esperaba una fracción negativa (-1,0)")
        return float(v)

    def texto(self, k: str, opciones: tuple[str, ...] | None = None) -> str:
        v = self.d[k]
        if not isinstance(v, str) or not v.strip():
            raise self._mal(k, "se esperaba texto")
        if opciones is not None and v not in opciones:
            raise self._mal(k, f"debe ser uno de {opciones}")
        return v.strip()

    def textos(self, k: str) -> tuple[str, ...]:
        v = self.d[k]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
            raise self._mal(k, "se esperaba una lista de textos no vacía")
        return tuple(x.strip() for x in v)

    def hora(self, k: str) -> time:
        # Sin comillas, YAML 1.1 lee 09:30 como un número en base 60: por
        # eso solo se acepta texto.
        v = self.d[k]
        if isinstance(v, str):
            try:
                return time.fromisoformat(v.strip())
            except ValueError:
                pass
        raise self._mal(k, "se esperaba \"HH:MM\" entre comillas")

    def seccion(self, k: str, claves: tuple[str, ...]) -> _Seccion:
        return _Seccion(f"{self.nombre}.{k}", self.d[k], claves)


def _niveles(sec: _Seccion) -> dict[int, tuple[str, ...]]:
    crudo = sec.d["niveles"]
    if not isinstance(crudo, dict) or set(crudo) != {0, 1, 2}:
        raise ConfigV2Invalida("valor_invalido", "catalizador.niveles: exactamente 0, 1 y 2")
    out = {}
    for nivel, lista in crudo.items():
        if not isinstance(lista, list) or not lista or not all(isinstance(x, str) and x.strip() for x in lista):
            raise ConfigV2Invalida("valor_invalido", f"catalizador.niveles.{nivel}")
        out[nivel] = tuple(x.strip() for x in lista)
    return out


def _veto(sec: _Seccion) -> dict[str, tuple[str, ...]]:
    crudo = sec.d["veto"]
    if not isinstance(crudo, dict) or not crudo:
        raise ConfigV2Invalida("valor_invalido", "catalizador.veto")
    out = {}
    for categoria, lista in crudo.items():
        if (not isinstance(categoria, str) or not isinstance(lista, list) or not lista
                or not all(isinstance(x, str) and x.strip() for x in lista)):
            raise ConfigV2Invalida("valor_invalido", f"catalizador.veto.{categoria}")
        # En minúsculas: el filtro compara contra titular + resumen en minúsculas.
        out[categoria] = tuple(x.strip().lower() for x in lista)
    return out


def _tamano_por_nivel(sec: _Seccion, operables: tuple[int, ...]) -> dict[int, float]:
    crudo = sec.d["tamano_por_nivel"]
    if not isinstance(crudo, dict) or set(crudo) != set(operables):
        raise ConfigV2Invalida("valor_invalido",
                               "riesgo.tamano_por_nivel: una fracción por cada nivel operable")
    out = {}
    for nivel, v in crudo.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
            raise ConfigV2Invalida("valor_invalido", f"riesgo.tamano_por_nivel.{nivel}: (0,1]")
        out[nivel] = float(v)
    return out


def interpretar(crudo: object, ruta: Path) -> ConfigV2:
    raiz = _Seccion("raiz", crudo, ("version", "universo", "catalizador", "senal", "riesgo", "sombra"))
    version = raiz.numero("version", entero=True)
    if version != VERSION_SOPORTADA:
        raise ConfigV2Invalida("version", str(version))

    u = raiz.seccion("universo", (
        "exchanges", "excluir_otc", "excluir_etf", "excluir_spac", "precio_min", "precio_max",
        "float_min", "float_max", "float_faltante", "minutos_sin_halt", "accion_corporativa_hoy"))
    universo = Universo(
        exchanges=tuple(x.upper() for x in u.textos("exchanges")),
        excluir_otc=u.booleano("excluir_otc"), excluir_etf=u.booleano("excluir_etf"),
        excluir_spac=u.booleano("excluir_spac"),
        precio_min=float(u.numero("precio_min", minimo=0, abierto_min=True)),
        precio_max=float(u.numero("precio_max", minimo=0, abierto_min=True)),
        float_min=float(u.numero("float_min", minimo=0, abierto_min=True)),
        float_max=float(u.numero("float_max", minimo=0, abierto_min=True)),
        # Un float faltante nunca se trata como cero: la única opción es excluir.
        float_faltante=u.texto("float_faltante", ("excluir",)),
        minutos_sin_halt=int(u.numero("minutos_sin_halt", minimo=0, abierto_min=True, entero=True)),
        accion_corporativa_hoy=u.texto("accion_corporativa_hoy", ("bloquear",)),
    )
    if universo.precio_min >= universo.precio_max:
        raise ConfigV2Invalida("valor_invalido", "universo.precio_min >= precio_max")
    if universo.float_min >= universo.float_max:
        raise ConfigV2Invalida("valor_invalido", "universo.float_min >= float_max")

    c = raiz.seccion("catalizador", (
        "fuente", "horas_maximas", "modelo", "prompt_version", "niveles", "niveles_operables",
        "direcciones_operables", "veto"))
    operables_crudo = c.d["niveles_operables"]
    if (not isinstance(operables_crudo, list) or not operables_crudo
            or not all(isinstance(n, int) and not isinstance(n, bool) and n in (1, 2) for n in operables_crudo)):
        raise ConfigV2Invalida("valor_invalido", "catalizador.niveles_operables: subconjunto de [1, 2]")
    catalizador = Catalizador(
        fuente=c.texto("fuente", ("benzinga",)),
        horas_maximas=float(c.numero("horas_maximas", minimo=0, abierto_min=True)),
        modelo=c.texto("modelo"),
        prompt_version=int(c.numero("prompt_version", minimo=1, entero=True)),
        niveles=_niveles(c),
        niveles_operables=tuple(sorted(set(operables_crudo))),
        direcciones_operables=c.textos("direcciones_operables"),
        veto=_veto(c),
    )

    s = raiz.seccion("senal", (
        "zona_horaria", "gap_min_pct", "rvol_min", "rvol_dias", "precio_sobre_vwap",
        "rango_apertura_inicio", "rango_apertura_fin", "vela_ruptura_vol_min_x", "spread_max_pct",
        "indice_referencia", "indice_sobre_vwap", "ventana_inicio", "ventana_fin",
        "minutos_maximos_niveles"))
    zona = s.texto("zona_horaria")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(zona)
    except Exception:
        raise ConfigV2Invalida("valor_invalido", "senal.zona_horaria") from None
    senal = Senal(
        zona_horaria=zona,
        gap_min_pct=s.fraccion("gap_min_pct"),
        rvol_min=float(s.numero("rvol_min", minimo=0, abierto_min=True)),
        rvol_dias=int(s.numero("rvol_dias", minimo=1, entero=True)),
        precio_sobre_vwap=s.booleano("precio_sobre_vwap"),
        rango_apertura_inicio=s.hora("rango_apertura_inicio"),
        rango_apertura_fin=s.hora("rango_apertura_fin"),
        vela_ruptura_vol_min_x=float(s.numero("vela_ruptura_vol_min_x", minimo=0, abierto_min=True)),
        spread_max_pct=s.fraccion("spread_max_pct"),
        indice_referencia=s.texto("indice_referencia").upper(),
        indice_sobre_vwap=s.booleano("indice_sobre_vwap"),
        ventana_inicio=s.hora("ventana_inicio"),
        ventana_fin=s.hora("ventana_fin"),
        minutos_maximos_niveles=float(s.numero("minutos_maximos_niveles", minimo=0, abierto_min=True)),
    )
    if not senal.rango_apertura_inicio < senal.rango_apertura_fin <= senal.ventana_inicio < senal.ventana_fin:
        raise ConfigV2Invalida("valor_invalido",
                               "senal: rango_apertura_inicio < rango_apertura_fin <= ventana_inicio < ventana_fin")

    r = raiz.seccion("riesgo", (
        "riesgo_pct_equity", "tope_posicion_pct_equity", "stop_origen", "stop_min_pct", "stop_max_pct",
        "stop_si_excede_max", "objetivo_r", "breakeven_en_r", "stop_tiempo_minutos",
        "stop_tiempo_r_minimo", "max_posiciones", "max_entradas_dia", "freno_diario_pct",
        "freno_semanal_pct", "tamano_por_nivel", "solo_largos", "ia_decide_entradas",
        "cierre_fin_de_dia"))
    riesgo = Riesgo(
        riesgo_pct_equity=r.fraccion("riesgo_pct_equity"),
        tope_posicion_pct_equity=r.fraccion("tope_posicion_pct_equity"),
        stop_origen=r.texto("stop_origen", ("minimo_rango_apertura",)),
        stop_min_pct=r.fraccion("stop_min_pct"),
        stop_max_pct=r.fraccion("stop_max_pct"),
        stop_si_excede_max=r.texto("stop_si_excede_max", ("no_entrar",)),
        objetivo_r=float(r.numero("objetivo_r", minimo=0, abierto_min=True)),
        breakeven_en_r=float(r.numero("breakeven_en_r", minimo=0, abierto_min=True)),
        stop_tiempo_minutos=float(r.numero("stop_tiempo_minutos", minimo=0, abierto_min=True)),
        stop_tiempo_r_minimo=float(r.numero("stop_tiempo_r_minimo", minimo=0, abierto_min=True)),
        max_posiciones=int(r.numero("max_posiciones", minimo=1, entero=True)),
        max_entradas_dia=int(r.numero("max_entradas_dia", minimo=1, entero=True)),
        freno_diario_pct=r.perdida("freno_diario_pct"),
        freno_semanal_pct=r.perdida("freno_semanal_pct"),
        tamano_por_nivel=_tamano_por_nivel(r, catalizador.niveles_operables),
        solo_largos=r.booleano("solo_largos"),
        ia_decide_entradas=r.booleano("ia_decide_entradas"),
        cierre_fin_de_dia=r.texto("cierre_fin_de_dia", ("como_v1",)),
    )
    if riesgo.stop_min_pct >= riesgo.stop_max_pct:
        raise ConfigV2Invalida("valor_invalido", "riesgo.stop_min_pct >= stop_max_pct")
    if riesgo.riesgo_pct_equity > riesgo.tope_posicion_pct_equity:
        raise ConfigV2Invalida("valor_invalido", "riesgo.riesgo_pct_equity > tope_posicion_pct_equity")
    if riesgo.freno_semanal_pct > riesgo.freno_diario_pct:
        raise ConfigV2Invalida("valor_invalido", "riesgo.freno_semanal_pct menos estricto que el diario")
    if riesgo.max_entradas_dia < riesgo.max_posiciones:
        raise ConfigV2Invalida("valor_invalido", "riesgo.max_entradas_dia < max_posiciones")
    if not riesgo.solo_largos:
        # La v2 no tiene lógica de cortos: aceptarlo sería prometer algo
        # que el código no hace.
        raise ConfigV2Invalida("valor_invalido", "riesgo.solo_largos: la v2 solo opera largos")
    if riesgo.ia_decide_entradas:
        raise ConfigV2Invalida("valor_invalido", "riesgo.ia_decide_entradas: la IA solo clasifica")

    so = raiz.seccion("sombra", ("activa_con_v1", "directorio"))
    directorio = Path(so.texto("directorio"))
    if not directorio.is_absolute():
        raise ConfigV2Invalida("valor_invalido", "sombra.directorio: ruta absoluta, fuera de git")
    raiz_repo = RUTA_DEFAULT.parents[1].resolve()
    if directorio.resolve() == raiz_repo or raiz_repo in directorio.resolve().parents:
        raise ConfigV2Invalida("valor_invalido", "sombra.directorio: dentro del repo (debe estar fuera de git)")
    sombra = Sombra(activa_con_v1=so.booleano("activa_con_v1"), directorio=directorio)

    return ConfigV2(version, universo, catalizador, senal, riesgo, sombra, ruta)


# ----------------------------------------------------------------- carga


def ruta() -> Path:
    crudo = os.environ.get(ENV_RUTA, "").strip()
    return Path(crudo) if crudo else RUTA_DEFAULT


def cargar(ruta_yaml: Path | None = None) -> ConfigV2:
    """Levanta `ConfigV2Invalida` ante cualquier problema; nunca devuelve
    una configuración a medias."""
    ruta_yaml = ruta_yaml or ruta()
    try:
        import yaml
    except ImportError:
        raise ConfigV2Invalida("sin_pyyaml") from None
    try:
        texto = ruta_yaml.read_text(encoding="utf-8")
    except OSError:
        raise ConfigV2Invalida("sin_archivo", str(ruta_yaml.name)) from None
    try:
        crudo = yaml.safe_load(texto)
    except yaml.YAMLError:
        raise ConfigV2Invalida("yaml_ilegible", str(ruta_yaml.name)) from None
    return interpretar(crudo, ruta_yaml)


def estrategia_pedida() -> str:
    crudo = os.environ.get(ENV_BANDERA, "").strip().lower()
    if crudo in ("", V1):
        return V1
    if crudo == V2:
        return V2
    log.warning("estrategia: %s=%r no es v1 ni v2 -- se usa v1", ENV_BANDERA, crudo)
    return V1


def estado(ruta_yaml: Path | None = None) -> EstadoV2:
    """Qué corre en este proceso. Nunca levanta."""
    bandera = estrategia_pedida()
    try:
        cfg = cargar(ruta_yaml)
    except ConfigV2Invalida as ex:
        if bandera == V2:
            log.error("estrategia: se pidió v2 y la configuración no sirve (%s) -- BLOQUEADO, "
                      "no se abren entradas", ex)
            return EstadoV2(bandera, BLOQUEADO, False, None, ex.codigo)
        log.warning("estrategia: v1 sin sombra v2 (configuración v2 no válida: %s)", ex)
        return EstadoV2(bandera, V1, False, None, ex.codigo)
    if bandera == V2:
        return EstadoV2(bandera, V2, False, cfg, None)
    return EstadoV2(bandera, V1, cfg.sombra.activa_con_v1, cfg, None)
