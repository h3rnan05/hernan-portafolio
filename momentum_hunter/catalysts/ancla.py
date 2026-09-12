"""Filtro de ancla ticker/nombre -- post-keyword, v1.

POR QUÉ. `detectar_catalizador` solo mira si el ticker de Yahoo trajo un
titular con keyword de catalizador. Yahoo mezcla noticias del sector: el
mismo "Director Buys" de GameStop se atribuye a EBAY, RBLX y TTWO; el
deal de Alif Semiconductor aparece en ON aunque el comprador es ADI.
Sin ancla, el buscador vigila un ticker que no está en el titular.

QUÉ HACE. Después del keyword, exige que el titular mencione ESTE
ticker, un alias curado, o un token del nombre legal. Si no, el
catalizador se trata como ausente -- fail-closed, igual que el resto
del pipeline. No cambia umbrales ni la lista de keywords.

Las tablas van acá, versionadas, para poder auditarlas sin tocar la
regla. v1 se midió en contrafactual sobre el corpus etiquetado del
diseño (24/24 FP, 0/37 FN) -- esa cifra es tautológica: el mismo
corpus sirvió para escribir los alias. No es validación out-of-sample.

NUNCA importa bróker ni decide tamaños. Solo lee texto."""

from __future__ import annotations

import re

# Subir esto cuando se toquen las tablas. El filtro en sí no cambia
# de semántica entre versiones -- solo el diccionario.
ANCLA_TABLAS_VERSION = "v1"

# Palabras que no identifican a UNA empresa. "semiconductor" tiene que
# estar: si no, ON (ON Semiconductor) ancla el titular de ADI/Alif.
# "strategy" también: el nombre legal de MSTR es "Strategy Inc" y
# "strategy" aparece en cualquier plan de negocio. No se usa como alias.
GENERIC_TOKENS: frozenset[str] = frozenset({
    "the", "and", "for", "of",
    "inc", "incorporated", "corp", "corporation", "ltd", "limited",
    "llc", "plc", "lp", "llp", "co", "company", "holdings", "holding",
    "group", "class", "series", "ordinary", "common", "shares", "stock",
    "sa", "nv", "ag", "se", "asa", "ab", "oy", "spa", "pbc", "plc",
    "technologies", "technology", "therapeutics", "pharmaceutical",
    "pharmaceuticals", "pharma", "energy", "resources", "industries",
    "industrial", "international", "financial", "services", "systems",
    "solutions", "partners", "capital", "bank", "trust", "healthcare",
    "medical", "biosciences", "bioscience", "sciences", "science",
    "devices", "semiconductor", "semiconductors", "electronics",
    "communications", "communication", "networks", "network",
    "software", "hardware", "media", "entertainment", "management",
    "investment", "investments", "insurance", "properties", "property",
    "realty", "mining", "minerals", "utilities", "utility", "power",
    "electric", "oil", "gas", "stores", "store", "markets", "market",
    "infrastructure", "automotive", "internet", "strategy",
    "laboratories", "laboratory", "labs", "diagnostics", "diagnostic",
    "fitness", "wholesale", "brands", "brand", "interactive",
    "global", "national", "united", "american", "america",
    "aerospace", "aviation", "health", "care",
})

# Forma legal que se tira ANTES de quedarse con tokens de identidad.
# Separado de GENERIC a propósito: un sufijo no es "genérico de
# industria", es ruido del nombre registrado.
LEGAL_SUFFIXES: frozenset[str] = frozenset({
    "inc", "incorporated", "corp", "corporation", "ltd", "limited",
    "llc", "plc", "lp", "llp", "co", "company", "holdings", "holding",
    "group", "the", "and", "sa", "nv", "ag", "se", "asa", "ab", "oy",
    "spa", "pbc", "plc", "class", "series", "ordinary", "common",
    "uk", "us",
})

# Alias de marca / how headlines spell the company. Solo formas
# específicas -- jamás una palabra que viva en GENERIC (por eso MSTR
# no lleva "strategy"). Fácil de versionar: tocar acá, no la regla.
ALIASES: dict[str, tuple[str, ...]] = {
    "ABT": ("abbott",),
    "ADI": ("analog devices",),
    "ON": ("onsemi", "on semiconductor", "on semi"),
    "MSTR": ("microstrategy",),
    "SEI": ("solaris energy", "solaris"),
    "GEHC": ("ge healthcare", "ge health"),
    "CHPT": ("chargepoint",),
    "NTLA": ("intellia",),
    "CEG": ("constellation energy", "constellation"),
    "TTE": ("totalenergies", "total energies"),
    "BMY": ("bristol myers", "bristol-myers"),
    "AZN": ("astrazeneca",),
    "GILD": ("gilead",),
    "BEAM": ("beam therapeutics",),
    "AMAT": ("applied materials",),
    "MRVL": ("marvell",),
    "ALAB": ("astera labs", "astera"),
    "CRCL": ("circle internet",),
    "GE": ("ge aerospace",),
    "BAC": ("bank of america",),
    "T": ("at&t", "att"),
    "BRK-B": ("berkshire hathaway", "berkshire"),
    "BRK.B": ("berkshire hathaway", "berkshire"),
    "PSNYW": ("polestar",),
    "ROIV": ("roivant",),
    "BJDX": ("bluejay",),
    "XPOF": ("xponential",),
    "SUNB": ("sunbelt rentals", "sunbelt"),
    "VOXR": ("vox royalty",),
    "BSP": ("bending spoons",),
    "SHEL": ("shell",),
    "ENB": ("enbridge",),
    "ADBE": ("adobe",),
    "CRM": ("salesforce",),
    "CSCO": ("cisco",),
    "TGT": ("target",),
    "KR": ("kroger",),
    "EBAY": ("ebay",),
    "UBER": ("uber",),
    "AVGO": ("broadcom",),
    "QCOM": ("qualcomm",),
    "NVDA": ("nvidia",),
    "AMD": ("advanced micro devices",),
    "AAPL": ("apple",),
    "MSFT": ("microsoft",),
    "AMZN": ("amazon",),
    "META": ("meta platforms", "facebook"),
    "GOOGL": ("alphabet", "google"),
    "GOOG": ("alphabet", "google"),
    "WFC": ("wells fargo",),
    "JPM": ("jpmorgan", "jp morgan"),
    "ORCL": ("oracle",),
    "INTC": ("intel",),
    "IBM": ("ibm",),
    "NFLX": ("netflix",),
    "TSLA": ("tesla",),
    # Huecos de la tabla v1 del diseño -- tickers del corpus cuya marca
    # no salía de name_tokens (o el nombre Yahoo falta) y hay que
    # anclar por alias. No se toca MSTR: sigue sin "strategy".
    "TTWO": ("take-two", "take two"),
    "RBLX": ("roblox",),
    "WDAY": ("workday",),
    "JEF": ("jefferies",),
    "GMED": ("globus medical", "globus"),
    "GLUE": ("monte rosa",),
    "BSX": ("boston scientific",),
    "BNS": ("scotiabank", "bank of nova scotia"),
    "BRZE": ("braze",),
    "RVTY": ("revvity",),
}


_NO_ALNUM = re.compile(r"[^a-z0-9]+")


def match_token(token: str, titular: str) -> bool:
    """True si `token` aparece en el titular.

    Tickers cortos (len<=3) SIEMPRE van con frontera de palabra: si no,
    ON vive dentro de "acquisition". Los largos pueden ser substring
    ("abbott" dentro de "Abbott's")."""
    if not token or not titular:
        return False
    pieza = token.lower()
    texto = titular.lower()
    if len(pieza) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(pieza)}(?![a-z0-9])", texto) is not None
    return pieza in texto


def name_tokens(nombre: str | None) -> list[str]:
    """Tokens de identidad del nombre legal.

    Tira sufijos societarios, descarta genéricos y piezas de 1-2 letras,
    y se queda con [primero] y -- si hay al menos dos palabras útiles --
    ["primero segundo"]. No se devuelve el resto: "labs" solo no debe
    anclar a Astera Labs un titular de Marvell."""
    if not nombre or not nombre.strip():
        return []
    crudo = _NO_ALNUM.sub(" ", nombre.lower()).split()
    utiles = [
        p for p in crudo
        if len(p) >= 3 and p not in LEGAL_SUFFIXES and p not in GENERIC_TOKENS
    ]
    if not utiles:
        return []
    out = [utiles[0]]
    if len(utiles) >= 2:
        out.append(f"{utiles[0]} {utiles[1]}")
    return out


def ancla_ok(ticker: str, nombre: str | None, titular: str | None) -> tuple[bool, str]:
    """¿Este titular es de ESTE ticker?

    Orden fijo (el primero que gana, para que el motivo sea estable):
      1. titular vacío → False, sin_titular
      2. el ticker (frontera si es corto)
      3. cualquier alias de ALIASES[ticker]
      4. cualquier name_tokens(nombre)
      5. si no → False, sin_ancla
    """
    if titular is None or not str(titular).strip():
        return False, "sin_titular"
    texto = str(titular)
    if ticker and match_token(ticker, texto):
        return True, "ticker"
    for alias in ALIASES.get((ticker or "").upper(), ()):
        if match_token(alias, texto):
            return True, "alias"
    for token in name_tokens(nombre):
        if match_token(token, texto):
            return True, "nombre"
    return False, "sin_ancla"
