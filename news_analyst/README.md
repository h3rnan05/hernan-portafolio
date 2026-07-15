# News Analyst — "Why Should I Care?"

El agente #9 del AIOS (AI Analyst, ver `ROADMAP.md`) aplicado a noticias.
Nace de una crítica directa: reenviar titulares crudos ("Warren Buffett...
IBM... Nasdaq...") no aporta nada que Yahoo Finance no dé gratis. Este
módulo filtra el ruido y explica **solo** lo que de verdad toca tu
shortlist — el resto ni se muestra.

## Cómo funciona

1. **Cruce determinístico** (`matching.py`, cero LLM, cero red): ¿el
   titular menciona el nombre o el ticker de alguna empresa de
   `screener/shortlist_hoy.json`? Nombre primero (más específico, menos
   falsos positivos), ticker como respaldo solo para tickers de 3+ letras
   (tickers de 1-2 letras como "C" o "F" son palabras/siglas comunes en
   inglés — matchearlos solos generaría demasiado ruido).
2. **Explicación con LLM** (`explicador.py`), *solo* para lo que sí
   matcheó, con tope de `MAX_EXPLICACIONES` por corrida (control de costo).
   El LLM recibe el titular + el contexto de por qué el modelo
   cuantitativo eligió esa empresa (posición en la shortlist, score,
   sector, factores más altos) y responde con una explicación en lenguaje
   llano, un tono (positivo/negativo/neutral/incierto) y un nivel de
   importancia 1-5.
3. **Formato** (`formato.py`): un mensaje por corrida a Telegram. Si nada
   de la shortlist salió en las noticias, lo dice explícitamente — "sin
   noticias relevantes hoy" es una salida válida, no silencio.

## Principio #3 del AIOS: el LLM explica, nunca decide

Esto es exactamente el punto de fricción que existe hoy en
`telegram_bot/idea_evaluator.py` y el explorador de `wizards_bot.py`
(ver la deuda de arquitectura en `ROADMAP.md`) — así que este módulo se
construyó con las defensas puestas desde el día uno, no como parche
después:

- El prompt (`explicador.SYSTEM_PROMPT`) prohíbe explícitamente sugerir
  comprar/vender/mantener/aumentar/reducir.
- **Defensa adicional en código**: si la respuesta del LLM de todas
  formas contiene palabras como "compra", "vende", "recomiendo", la
  explicación se descarta ENTERA antes de llegar al usuario
  (`explicador._PALABRAS_PROHIBIDAS`). Mismo patrón de belt-and-suspenders
  que `telegram_bot/idea_evaluator.py`.
- El LLM solo puede usar el titular + el contexto de la shortlist que se
  le da explícitamente — nunca inventa cifras ni eventos externos (regla
  2 del prompt).
- "Tono" describe el titular para el negocio, nunca una predicción de
  precio ni una señal de trading.

## Por qué el cruce contra el portafolio real quedó fuera de esta parte

El pedido original también pedía cruzar contra "el portafolio", con
ejemplos tipo "Apple es la posición #2 de nuestro portafolio". Hoy
`wizards_bot.py` opera un universo de 10 ETFs (ver `UNIVERSO` en ese
archivo) — no tiene sentido cruzar una noticia de Apple contra un
portafolio que no puede tener acciones individuales. El cruce contra
`shortlist_hoy.json` (que sí es de acciones individuales) cubre el caso
real de uso de hoy. Cuando exista un portafolio real de acciones
individuales (Decision Engine + Execution Engine), este módulo se extiende
para cruzar también contra posiciones abiertas — la arquitectura
(`EntradaShortlist`, `Mencion`) ya está pensada para que ese segundo cruce
sea un `detectar_mencion()` más, no un rediseño.

## Costo y control

Solo se llama al LLM por los titulares que **ya pasaron** el filtro
determinístico — nunca por los que no mencionan nada de la shortlist. Tope
adicional de `MAX_EXPLICACIONES` (default 5) por corrida. Sin
`ANTHROPIC_API_KEY`, el módulo sigue funcionando: lista las menciones
detectadas sin explicación en vez de fallar.

## Uso

```bash
python -m news_analyst.run
```

Requiere que `screener/shortlist_hoy.json` ya exista (lo genera
`screener.run`, que corre antes en el cron diario).

## Despliegue

`.github/workflows/news_analyst.yml` — **solo `workflow_dispatch` por
ahora, sin cron**. Se agrega un horario fijo una vez verificada la calidad
y el costo con corridas manuales.

## Integración pendiente

Standalone, no conectado a `wizards_bot.py` ni a ningún otro módulo del
AIOS — es un canal de información adicional, no toca ninguna decisión de
trading.
