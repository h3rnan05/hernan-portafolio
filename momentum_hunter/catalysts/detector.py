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
X minutos" del Early Opportunity Engine (Prompt 2/5). `_dentro_de_ventana`
sigue comparando solo por fecha (`fecha[:10]`), así que esto no cambia
ningún comportamiento de la ventana de días."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, date, datetime

from momentum_hunter.config import MomentumConfig
from momentum_hunter.models import Catalizador

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


def clasificar_titular(texto: str) -> str | None:
    """Primer tipo (en orden de prioridad) cuyas keywords aparecen en el
    titular, o None si no coincide con ningún catalizador conocido."""
    bajo = texto.lower()
    for tipo in ORDEN_PRIORIDAD:
        if any(kw in bajo for kw in CATALYST_KEYWORDS[tipo]):
            return tipo
    return None


def _dentro_de_ventana(fecha: str | None, hoy: date, dias: int) -> bool:
    """Sin fecha (algunas fuentes no la dan) se asume vigente -- mejor no
    descartar de más un catalizador real que sí lo es. `+1` de margen
    tolera desfases de huso horario entre el timestamp de la fuente y la
    corrida del bot."""
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
    vigentes = [t for t in titulares if _dentro_de_ventana(t.fecha, hoy, cfg.dias_ventana_catalizador)]

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


class YahooNewsProvider(NewsProvider):
    """Vía `yfinance`. Best-effort: si yfinance no está instalado o la
    llamada falla, devuelve lista vacía (el pipeline entonces no
    encuentra catalizador y descarta el ticker -- nunca inventa uno)."""

    def titulares(self, ticker: str) -> list[Titular]:
        try:
            import yfinance as yf
            items = yf.Ticker(ticker).news or []
        except Exception:
            return []
        out = []
        for item in items:
            t = self._parsear(item)
            if t is not None:
                out.append(t)
        return out

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
        return Titular(titulo, fuente, fecha)


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
