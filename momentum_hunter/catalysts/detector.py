"""Detección de catalizadores -- Prompt 4: "Nunca enviar una alerta
únicamente porque el gráfico se vea bien. Debe existir al menos un
catalizador." 100% determinístico por keywords sobre titulares reales,
cero LLM (mismo principio de `news_analyst/matching.py`: o el titular
menciona el catalizador, o no -- eso se puede verificar con texto plano).

Independiente de `news_analyst/` a propósito: ese módulo cruza titulares
contra la shortlist del S&P 500 y usa un LLM para explicar el "Why
Should I Care?" de una empresa grande. Este detector solo CLASIFICA el
tipo de catalizador (earnings/FDA/contrato/...) para decidir si existe
uno verificable -- nunca explica nada con lenguaje natural.

Regla de rumores (Prompt 4: "únicamente si aparecen en múltiples fuentes
confiables"): un rumor solo se confirma si aparece en
`cfg.fuentes_minimas_rumor` fuentes DISTINTAS dentro de la ventana de
`cfg.dias_ventana_catalizador` días. El resto de tipos de catalizador se
confirman con un solo titular -- son, por naturaleza, anuncios
verificables (un comunicado de la FDA no necesita una segunda fuente).

`Titular.fecha` guarda el timestamp COMPLETO cuando la fuente lo da (no
solo la fecha) -- lo necesita `minutos_desde_catalizador` para el "hace
X minutos" del Early Opportunity Engine (Prompt 2/5). `dentro_de_ventana`
sigue comparando solo por fecha (`fecha[:10]`), así que esto no cambia
ningún comportamiento de la ventana de días."""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime

from momentum_hunter.config import MomentumConfig
from momentum_hunter.models import Catalizador

log = logging.getLogger(__name__)

# Orden de prioridad cuando un titular (o varios, el mismo día) califican
# para más de un tipo -- los catalizadores estructuralmente más fuertes
# (aprobación regulatoria, adquisición) van primero. Fijo y documentado,
# nunca ajustado por ticker.
ORDEN_PRIORIDAD: tuple[str, ...] = (
    "fda", "adquisicion", "contrato", "regulatorio", "guidance",
    "nuevo_cliente", "patente", "buyback", "insider_buying",
    "upgrade_analista", "earnings", "rumor",
)

CATALYST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fda": (
        "fda approval", "fda clearance", "fda grants", "breakthrough therapy",
        "phase 3 results", "phase 2 results", "clinical trial results", "fda approves",
        # ALKS 2026-09-21 salió sin_keyword con "Phase 1b Results": esa frase
        # no contiene "phase 2 results" ni "phase 3 results", y "phase 1
        # results" tampoco (la "b" corta el substring). "phase 1" cubre
        # "phase 1b" por substring; "phase 1b" queda escrito para auditar
        # el hueco sin inferirlo. "proof of concept" es la misma frase sin
        # guiones. Limitación honesta: "phase 1" también entra en "phase 10"
        # y en "inicia phase 1" (phase 2/3 solo entran como results). No se
        # agrega "phase i": es substring de "phase ii" y "phase iii", y el
        # titular del 22-sep ("Phase I ADHD Study") sigue sin matchear.
        "phase 1b", "phase 1", "proof-of-concept", "proof of concept",
    ),
    "adquisicion": (
        "to acquire", "acquisition of", "merger agreement", "to be acquired",
        "definitive agreement to merge", "agrees to acquire", "buyout offer",
    ),
    "contrato": (
        "awarded contract", "signs contract", "wins contract", "purchase order",
        "multi-year agreement", "awarded a contract",
    ),
    "regulatorio": (
        "regulatory approval", "sec approval", "license granted", "granted approval by",
    ),
    "guidance": (
        "raises guidance", "raises forecast", "issues guidance", "updates guidance",
        "raises full-year", "cuts guidance",
    ),
    "nuevo_cliente": (
        "signs agreement with", "partnership with", "strategic partnership",
        "new customer", "expands partnership",
        # LFMD 2026-09-21 salió sin_keyword con "Secures AT&T Partnership":
        # el nombre queda entre "secures" y "partnership", y el matcher es
        # substring contiguo, así que "partnership with" / "strategic
        # partnership" / "signs agreement with" no están. "collaboration"
        # cubre "strategic collaboration" por el mismo motivo. Limitación:
        # cualquier titular con esas palabras cuenta, no solo el anuncio
        # de un acuerdo. No entra "teams up" ni el resto de #121.
        "partnership", "collaboration",
    ),
    "patente": (
        "patent granted", "patent issued", "awarded patent", "uspto",
    ),
    "buyback": (
        "share buyback", "repurchase program", "stock buyback", "buyback program",
    ),
    "insider_buying": (
        "insider buying", "director buys", "ceo buys shares", "form 4 filing",
        "insider purchase",
    ),
    "upgrade_analista": (
        "upgrades to buy", "initiates coverage", "price target raised",
        "upgraded to overweight", "upgraded to outperform",
    ),
    "earnings": (
        "quarterly results", "earnings results", "beats estimates", "misses estimates",
        "q1 results", "q2 results", "q3 results", "q4 results", "reports revenue of",
    ),
    "rumor": (
        "reportedly", "sources say", "according to sources", "is said to be",
    ),
}


@dataclass(frozen=True)
class Titular:
    texto: str
    fuente: str
    fecha: str | None = None  # ISO yyyy-mm-dd, best-effort
    # Link del artículo, tal como vino en la MISMA respuesta (yfinance o
    # RSS). Solo lo usa el registro de auditoría `noticias_leidas`: no
    # entra en la igualdad (`compare=False`) ni en ninguna decisión, así
    # que dos titulares iguales con o sin link siguen siendo iguales.
    link: str | None = field(default=None, compare=False)


def _link_http(valor: object) -> str | None:
    """Un link solo si es http(s). Cualquier otra cosa (vacío, otro
    esquema, un dict) queda como None: no se inventa ni se repara."""
    if isinstance(valor, str):
        v = valor.strip()
        if v.startswith(("https://", "http://")):
            return v
    return None


def clasificar_titular(texto: str) -> str | None:
    """Primer tipo (en orden de prioridad) cuyas keywords aparecen en el
    titular, o None si no coincide con ningún catalizador conocido."""
    bajo = texto.lower()
    for tipo in ORDEN_PRIORIDAD:
        if any(kw in bajo for kw in CATALYST_KEYWORDS[tipo]):
            return tipo
    return None


def dentro_de_ventana(fecha: str | None, hoy: date, dias: int) -> bool:
    """Sin fecha (algunas fuentes no la dan) se asume vigente -- mejor no
    descartar de más un catalizador real que sí lo es. `+1` de margen
    tolera desfases de huso horario entre el timestamp de la fuente y la
    corrida del bot. Pública (2026-08-11) -- `watchlist.py` la reutiliza
    para decidir si un catalizador ya congelado envejeció fuera de la
    ventana (transición a INVALIDATED), en vez de reinventar la misma
    regla dos veces."""
    if not fecha:
        return True
    try:
        f = date.fromisoformat(fecha[:10])
    except ValueError:
        return True
    delta = (hoy - f).days
    return -1 <= delta <= dias


def detectar_catalizador(
    titulares: list[Titular], cfg: MomentumConfig, hoy: date | None = None,
) -> Catalizador | None:
    """Punto de entrada único. Devuelve el catalizador CONFIRMADO de
    mayor prioridad, o None si nada calificó -- ese None es la señal para
    que el pipeline descarte el ticker por completo (Prompt 4: "si no
    existe un catalizador verificable, descartar la acción"), sin
    importar qué tan bien se vea el gráfico."""
    hoy = hoy or date.today()
    vigentes = [t for t in titulares if dentro_de_ventana(t.fecha, hoy, cfg.dias_ventana_catalizador)]

    por_tipo: dict[str, list[Titular]] = {}
    for t in vigentes:
        tipo = clasificar_titular(t.texto)
        if tipo:
            por_tipo.setdefault(tipo, []).append(t)

    for tipo in ORDEN_PRIORIDAD:
        candidatos = por_tipo.get(tipo)
        if not candidatos:
            continue
        fuentes = {c.fuente for c in candidatos}
        if tipo == "rumor" and len(fuentes) < cfg.fuentes_minimas_rumor:
            continue  # no confirmado -- se descarta en silencio, se sigue buscando otro tipo
        principal = candidatos[0]
        adicionales = tuple(sorted(fuentes - {principal.fuente}))
        return Catalizador(
            tipo=tipo, titular=principal.texto, fuente=principal.fuente,
            fecha=principal.fecha, confirmado=True, fuentes_adicionales=adicionales,
        )
    return None


class NewsProvider(ABC):
    """Misma abstracción de inyección de dependencia que `DataProvider`
    (`momentum_hunter/data/provider.py`) -- el detector nunca conoce a
    Yahoo directamente."""

    @abstractmethod
    def titulares(self, ticker: str) -> list[Titular]:
        """Titulares recientes de un ticker. Lista vacía si falla o no hay."""


# Respaldo de noticias (2026-09-29). Ese día el endpoint que usa
# yfinance 1.7.0 para `.news` (POST finance.yahoo.com/xhr/ncp) respondía
# HTTP 500 `{"message":"Internal Server Error"}` a todo el mundo, desde
# el VPS y desde otra red. yfinance se traga ese 500 y devuelve `[]` sin
# excepción: el hunter corrió 4 escaneos con titulares_total=0 sin que
# nada lo contara como error. El RSS por ticker de Yahoo seguía en 200.
YAHOO_RSS_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
YAHOO_RSS_TIMEOUT_S = 10
# `0` apaga el respaldo sin redeploy (rollback: editar el env y listo).
ENV_RSS_FALLBACK = "MOMENTUM_NOTICIAS_RSS_FALLBACK"
# El RSS no trae el medio. No se inventa: todos sus titulares llevan la
# misma fuente, así un rumor nunca junta dos fuentes distintas con el RSS
# solo (la regla de rumores queda igual o más estricta, nunca más laxa).
FUENTE_RSS = "desconocida"


def rss_fallback_activo() -> bool:
    return os.environ.get(ENV_RSS_FALLBACK, "1").strip() != "0"


def parsear_rss_yahoo(xml_texto: str) -> list[Titular]:
    """Cuerpo del RSS `headline?s=TICKER` → titulares, mismo formato que
    `YahooNewsProvider._parsear`: fecha ISO completa en UTC. Un XML
    ilegible lanza `ET.ParseError` (el caller lo cuenta como error de la
    fuente, no como 'no había noticias')."""
    texto = xml_texto.lstrip("\ufeff").strip()
    raiz = ET.fromstring(texto)
    out: list[Titular] = []
    for item in raiz.iter("item"):
        titulo = (item.findtext("title") or "").strip()
        if not titulo:
            continue
        fecha = None
        crudo = (item.findtext("pubDate") or "").strip()
        if crudo:
            try:
                momento = parsedate_to_datetime(crudo)
            except (TypeError, ValueError, IndexError):
                momento = None
            if momento is not None:
                if momento.tzinfo is None:
                    momento = momento.replace(tzinfo=UTC)
                fecha = momento.astimezone(UTC).isoformat(timespec="seconds")
        out.append(Titular(titulo, FUENTE_RSS, fecha, _link_http(item.findtext("link"))))
    return out


def titulares_rss_yahoo(ticker: str, timeout: float = YAHOO_RSS_TIMEOUT_S) -> list[Titular]:
    """GET al RSS por ticker de Yahoo. Lanza ante HTTP != 200, red o XML
    roto: el caller decide cómo contarlo."""
    import requests

    r = requests.get(
        YAHOO_RSS_URL,
        params={"s": ticker, "region": "US", "lang": "en-US"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    r.raise_for_status()
    return parsear_rss_yahoo(r.text)


class YahooNewsProvider(NewsProvider):
    """Vía `yfinance`. Best-effort: si yfinance no está instalado o la
    llamada falla, devuelve lista vacía (el pipeline entonces no
    encuentra catalizador y descarta el ticker -- nunca inventa uno).

    Respaldo (2026-09-29): si yfinance no dio ningún titular (vacío o
    excepción), se pide el RSS por ticker de Yahoo. Cuando yfinance sí
    trae titulares, el resultado es idéntico al de siempre. Apagable con
    `MOMENTUM_NOTICIAS_RSS_FALLBACK=0`."""

    def __init__(self, metricas=None, rss=None) -> None:
        """`metricas` opcional (`telemetria.Metricas`): si viene, se
        registra CADA fallo de la fuente de noticias en vez de que
        desaparezca en el `except` de abajo. Sin él, el comportamiento
        es idéntico al de siempre -- ninguna llamada existente se rompe.

        `rss` inyectable para pruebas (callable ticker -> titulares);
        por defecto `titulares_rss_yahoo`."""
        self._metricas = metricas
        self._rss = rss if rss is not None else titulares_rss_yahoo
        # Cómo terminó la última lectura de cada ticker, SOLO para el
        # registro de auditoría (`noticias_leidas`): "yfinance", "rss",
        # "vacio" (la fuente respondió sin titulares) o "error" (la
        # fuente que tenía la última palabra falló). No cambia lo que
        # devuelve `titulares` ni ninguna decisión. Limitación: si
        # yfinance se traga un 500 y devuelve [] y el RSS también viene
        # vacío, queda "vacio" -- desde aquí no se puede distinguir.
        self.estado: dict[str, str] = {}

    def _registrar(self, origen: str, ex: BaseException) -> None:
        if self._metricas is not None:
            self._metricas.registrar_error(origen, ex)

    def titulares(self, ticker: str) -> list[Titular]:
        items: list = []
        yf_fallo = False
        try:
            import yfinance as yf
            items = yf.Ticker(ticker).news or []
        except Exception as ex:
            # Un ticker que falla nunca tumba la corrida. Pero el fallo
            # queda CONTADO: hasta el 2026-08-24 desaparecía en silencio,
            # y "no había catalizadores" era indistinguible de "la fuente
            # estaba caída". Ver `telemetria.py`.
            self._registrar("noticias", ex)
            items = []
            yf_fallo = True
        out = []
        for item in items:
            t = self._parsear(item)
            if t is not None:
                out.append(t)
        if out:
            self.estado[ticker] = "yfinance"
            return out
        if not rss_fallback_activo():
            self.estado[ticker] = "error" if yf_fallo else "vacio"
            return out
        try:
            rss = self._rss(ticker)
        except Exception as ex:  # noqa: BLE001 -- el respaldo nunca tumba la corrida
            self._registrar("noticias_rss", ex)
            self.estado[ticker] = "error"
            return []
        self.estado[ticker] = "rss" if rss else "vacio"
        return rss

    @staticmethod
    def _parsear(item: dict) -> Titular | None:
        # yfinance cambió el formato de /news en algún momento a anidar
        # los campos bajo "content" -- se soportan ambos formatos.
        contenido = item.get("content", item)
        titulo = contenido.get("title")
        if not titulo:
            return None
        proveedor = contenido.get("provider")
        fuente = (
            (proveedor.get("displayName") if isinstance(proveedor, dict) else None)
            or item.get("publisher") or "desconocida"
        )
        fecha = None
        pub = contenido.get("pubDate") or item.get("providerPublishTime")
        if isinstance(pub, str):
            fecha = pub  # timestamp completo (ISO), no solo la fecha
        elif isinstance(pub, int | float):
            try:
                fecha = datetime.fromtimestamp(pub, tz=UTC).isoformat(timespec="seconds")
            except (OSError, OverflowError, ValueError):
                fecha = None
        # Formato anidado: `canonicalUrl.url` o `clickThroughUrl.url`;
        # plano: `link`. Viene en la misma respuesta: cero llamadas extra.
        link = None
        for clave in ("canonicalUrl", "clickThroughUrl"):
            v = contenido.get(clave)
            link = _link_http(v.get("url") if isinstance(v, dict) else v)
            if link:
                break
        link = link or _link_http(item.get("link"))
        return Titular(titulo, fuente, fecha, link)


def minutos_desde_catalizador(catalizador: Catalizador | None, ahora: datetime | None = None) -> float | None:
    """Minutos entre `catalizador.fecha` y `ahora` -- None si no hay
    catalizador o si la fuente solo dio una fecha sin hora (no se puede
    inventar la precisión que la fuente no dio)."""
    if catalizador is None or catalizador.fecha is None or "T" not in catalizador.fecha:
        return None
    try:
        momento = datetime.fromisoformat(catalizador.fecha)
    except ValueError:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=UTC)
    ahora = ahora or datetime.now(UTC)
    return (ahora - momento).total_seconds() / 60.0
