"""Configuración del Momentum Opportunity Hunter -- un solo lugar para
tocar universo, pesos y umbrales de alerta (mismo principio que
screener/config.py).

Deliberadamente NO comparte config con `screener/`: ese motor está
calibrado para el S&P 500 con una pregunta de VALORACIÓN ("¿es una buena
empresa?"). Este vive en un universo mayormente small-cap/low float
(ver `market_cap_max`), más una banda large-cap complementaria (ver
"Modo large-cap" más abajo) -- pero SIEMPRE con la misma pregunta de
momentum ("¿puede moverse fuerte ahora?"), nunca de valoración. Cero
fundamentales, cero pesos, cero umbrales compartidos con el screener.
Ver momentum_hunter/README.md.

Nota sobre `market_cap_max`: el pedido original decía "menor a 2
billones" -- en el uso real de un Opportunity Hunter de small/micro caps
eso solo tiene sentido como 2,000 millones de dólares (small-cap, no
mega-cap), así que el default es 2e9 USD. Queda como parámetro explícito
por si se quisiera ajustar.

Modo large-cap (pedido 2026-08-07, después de ver a Airbnb (ABNB) subir
17% en un día): `incluir_large_cap` abre una SEGUNDA banda de universo,
COMPLEMENTARIA a la de arriba, no un reemplazo -- todo lo que quede por
encima de `precio_max`/`market_cap_max` (sin techo superior) entra por
esta banda en vez de quedar descartado. La pregunta de fondo sigue
siendo la misma ("¿puede moverse fuerte ahora?"), pero el mecanismo es
distinto: una empresa grande no tiene el desequilibrio de oferta/demanda
de un float bajo (evaluator.py pregunta 3 -- ver `es_large_cap` ahí), así
que la pregunta que reemplaza esa señal es un catalizador confirmado que
YA está moviendo el precio con un gap real (los mismos patrones
`gap_and_go`/`opening_range_breakout` de `classification.py`, sin
umbrales nuevos: un gap del 8%+ en una mega-cap ya es un evento inusual,
el mismo umbral que usa small-cap resulta apropiadamente exigente aquí).
Honestidad explícita (ver conversación con el dueño del producto): esto
NO predice un movimiento antes de que exista ninguna señal pública --
eso no es alcanzable para una acción con cobertura total de Wall Street.
Lo que sí hace es avisar en el instante en que el catalizador + el gap
premarket ya son detectables, antes de que abra el mercado regular.

Pivote 2026-07-26 (pedido explícito: "quiero que piense como un trader
de momentum, no como un screener"): la etapa 2 del pipeline (`run.py`)
ya no evalúa con barras diarias -- pide velas intradía SOLO para los
`max_candidatos_intradia` mejores candidatos de la etapa 1 (evitar pedir
esto para miles de tickers, ver `data/provider.py`). `umbral_rvol_intradia`
es una lectura de "¿está entrando dinero AHORA?" (vela actual vs. las
últimas 5), distinta de `rvol_minimo_alerta` (que compara contra el
promedio de 20 DÍAS -- sigue existiendo como filtro grueso de la etapa 1).
`extension_maxima_pct`/`velas_maximas_desde_patron` son los umbrales
duros del Early Opportunity Engine (`early_opportunity.py`) para decidir
"temprano" vs. "tarde" -- un veredicto que puede bajar una alerta aunque
el score compuesto sea alto (Prompt 2)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MomentumConfig:
    # --- Universo ---
    bolsas: tuple[str, ...] = ("NYSE", "NASDAQ", "AMEX")
    precio_min: float = 0.75
    precio_max: float = 20.0
    market_cap_max: float = 2_000_000_000.0   # 2,000 millones USD (ver docstring)
    volumen_promedio_min: float = 300_000.0   # acciones/día, promedio 20 sesiones
    excluir_etf: bool = True
    excluir_spac: bool = True
    excluir_cef: bool = True                  # closed-end funds
    liquidez_minima_adr: float = 300_000.0    # ADRs con menos volumen que esto se excluyen

    # --- Modo large-cap (ver docstring del módulo) -- banda complementaria,
    # no reemplaza precio_min/precio_max/market_cap_max de arriba. ---
    incluir_large_cap: bool = True
    volumen_promedio_min_large_cap: float = 1_000_000.0  # empresas grandes trafican mucho más

    # --- Pesos del score compuesto (deben sumar 1.0) ---
    # Nunca incluye fundamentales de valoración (P/E, ROE, dividendos) --
    # esas señales son del Investment Analyst (screener/), no de este bot.
    pesos: dict[str, float] = field(default_factory=lambda: {
        "momentum":    0.40,
        "catalizador": 0.25,
        "liquidez":    0.20,
        "riesgo":      0.15,
    })

    # --- Umbrales de alerta (Prompt 7: calidad antes que cantidad) ---
    # Historia de este número, porque es el que más veces nos ha
    # engañado:
    #
    # 85,0 -- INALCANZABLE. Auditadas 3.161 candidatas reales, el
    #   `score_base` más alto jamás producido fue 81,2. Y como
    #   `score_ajustado = score_base - penalizaciones` (solo restan), el
    #   final nunca podía superar al base. El bot era aritméticamente
    #   incapaz de alertar: 0 de 3.161. No era selectividad.
    #
    # 60,0 -- calibrado el 2026-08-21 con `replay.py`, pero sobre datos
    #   que eran 3.155/3.161 large-cap (el sesgo de universo que se
    #   corrigió ese mismo día). Se marcó PROVISIONAL a propósito.
    #
    # 55,0 -- ESTE. Primera calibración con el universo ya corregido y
    #   con `riesgo_definido` guardado. El 2026-08-24, primer día de
    #   operación real, la mejor candidata (TRV) sacó 59,7 y se quedó a
    #   0,3 del umbral -- habiendo pasado LAS CUATRO condiciones
    #   obligatorias (patrón, temprano, riesgo definido, dinero
    #   entrando) con CERO penalizaciones.
    #
    # El argumento para bajar no es "queremos que opere". Es que la
    # selectividad real la hacen esas cuatro condiciones booleanas, no
    # este número: son las que descartan el 99% de las candidatas. El
    # score encima de ellas es un segundo filtro cuyo poder predictivo
    # NADIE HA MEDIDO NUNCA -- no existe evidencia de que una candidata
    # de 65 funcione mejor que una de 58 (ver `stats.py`: hacen falta
    # alertas resueltas, y todavía no hay ninguna). Rechazar la
    # candidata más limpia del día con una vara sin evidencia detrás es
    # estar mal calibrado, no ser exigente.
    #
    # Sigue siendo revisable: cuando `outcomes.py` tenga resultados
    # medidos, se podrá comprobar por primera vez si el score predice
    # algo. Si no predice nada, la conclusión correcta será quitarlo del
    # criterio de `accionable`, no moverlo otra vez.
    #
    #   python -m momentum_hunter.replay        <- re-medir con esto
    score_minimo_alerta: float = 55.0
    rvol_minimo_alerta: float = 4.0           # volumen actual / promedio 20 sesiones (filtro grueso, etapa 1)
    requiere_catalizador_confirmado: bool = True
    # Techo de seguridad, no un objetivo -- la selectividad real la hace
    # el evaluator (5 preguntas secuenciales) y el veredicto de "temprano"
    # del Early Opportunity Engine. Bajado de 5 a 3 (pedido explícito,
    # 2026-07-26: "prefiero una sola oportunidad excelente que veinte
    # promedio").
    limite_diario_alertas: int = 3
    # Principio 4 (pedido 2026-07-27): "cada alerta debe competir contra
    # todas las demás... si hoy solamente pudiera abrir UNA operación,
    # ¿sería esta? Si la respuesta es no, no quiero recibir la alerta."
    # Con True (default), por corrida solo se alerta LA MEJOR candidata
    # que además sobreviva al abogado del diablo (skeptic.py); las demás
    # accionables aparecen en el radar como subcampeonas, con su motivo.
    # limite_diario_alertas queda como techo cuando esto se apague.
    solo_la_mejor: bool = True

    # --- Catalizadores ---
    fuentes_minimas_rumor: int = 2            # un rumor solo cuenta si aparece en >=N fuentes
    dias_ventana_catalizador: int = 3         # noticia debe ser de los últimos N días

    # --- Etapa 2: datos intradía (solo sobre los mejores candidatos) ---
    intervalo_intradia: str = "1m"
    periodo_intradia: str = "5d"               # margen para tener el cierre de ayer disponible
    max_candidatos_intradia: int = 50          # tope duro -- ver data/provider.py
    umbral_rvol_intradia: float = 3.0          # "¿está entrando dinero AHORA?" (Prompt 4, pregunta 2)
    minutos_rango_apertura: int = 5            # ventana del Opening Range Breakout

    # --- Early Opportunity Engine (Prompt 2) ---
    extension_maxima_pct: float = 0.12         # >12% lejos de VWAP/EMA9 -> "ya vamos tarde"
    velas_maximas_desde_patron: int = 8        # más de 8 velas desde la ruptura -> "ya vamos tarde"

    # --- Detector de entradas (2026-08-10, ver "Fase 1" en README) ---
    # Veto duro que antes no existía: un candidato sin stop definido (o
    # con riesgo/recompensa pobre) podía volverse accionable igual --
    # esto lo bloquea explícitamente, no solo lo penaliza.
    riesgo_recompensa_minimo: float = 1.5
    # Ancho de la zona de entrada sobre el nivel de ruptura del patrón
    # (mismo nivel que ya usa `report._nivel_invalidacion`) -- 1.5% es
    # editorial, documentado como tal (mismo espíritu que `_VENTANA_MINUTOS`
    # en report.py): ancho a propósito para "recién rompió", no una
    # tolerancia infinita para perseguir el precio.
    tolerancia_zona_entrada_pct: float = 0.015

    # --- State Engine / watchlist persistida (2026-08-11, "Fase 2" --
    # ver watchlist.py y README). Cuánto tiempo puede quedarse un ticker
    # en WATCHING sin resolver (TRIGGERED/INVALIDATED/MISSED) antes de
    # expirar solo -- una candidata que lleva horas sin confirmar nada
    # ya no es la misma oportunidad que la detectó. ---
    minutos_maximos_en_watching: int = 120
    # Corrección 2026-08-11 (revisión de PR): el veredicto "tarde" es
    # una lectura del instante, no un hecho permanente -- se exige verla
    # en N chequeos SEGUIDOS antes de comprometerse a MISSED (terminal),
    # para no apagar la vigilancia continua por un solo dato ruidoso
    # (ver `EntradaWatchlist.tarde_consecutivas`).
    verificaciones_tarde_para_missed: int = 2

    # --- Aprendizaje / tracking ---
    horizontes_seguimiento: tuple[int, ...] = (1, 3, 5, 10)  # días hábiles de seguimiento

    def validar(self) -> None:
        total = sum(self.pesos.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Los pesos suman {total:.3f}, deben sumar 1.0")
        if self.precio_min <= 0 or self.precio_max <= self.precio_min:
            raise ValueError("precio_min debe ser > 0 y menor que precio_max")
        if not (0.0 <= self.score_minimo_alerta <= 100.0):
            raise ValueError("score_minimo_alerta debe estar en [0, 100]")
        if self.limite_diario_alertas < 1:
            raise ValueError("limite_diario_alertas debe ser >= 1")
        if self.max_candidatos_intradia < 1:
            raise ValueError("max_candidatos_intradia debe ser >= 1")
        if self.extension_maxima_pct <= 0:
            raise ValueError("extension_maxima_pct debe ser > 0")
        if self.velas_maximas_desde_patron < 1:
            raise ValueError("velas_maximas_desde_patron debe ser >= 1")
        if self.volumen_promedio_min_large_cap <= 0:
            raise ValueError("volumen_promedio_min_large_cap debe ser > 0")
        if self.riesgo_recompensa_minimo <= 0:
            raise ValueError("riesgo_recompensa_minimo debe ser > 0")
        if self.tolerancia_zona_entrada_pct <= 0:
            raise ValueError("tolerancia_zona_entrada_pct debe ser > 0")
        if self.minutos_maximos_en_watching < 1:
            raise ValueError("minutos_maximos_en_watching debe ser >= 1")
        if self.verificaciones_tarde_para_missed < 1:
            raise ValueError("verificaciones_tarde_para_missed debe ser >= 1")


CONFIG = MomentumConfig()
