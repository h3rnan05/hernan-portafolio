"""Stream SIP en sombra: parseo, reconexión, huecos, comparador,
fallback a REST y el flag. Nada de esto abre un socket real ni llama
a un endpoint de órdenes."""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from momentum_hunter.data import fuente, sip_stream as sip
from momentum_hunter.data import sip_stream_comparar as cmp
from momentum_hunter.data.fuente import ProveedorConRespaldo
from momentum_hunter.models import BarraIntradia

REPO = Path(__file__).resolve().parents[2]
DIA = "2026-09-28"
CLAVE = "K" * 24
SECRETO = "S" * 24
SILENCIO = object()
AHORA = datetime(2026, 9, 28, 14, 32, tzinfo=UTC)


def _bin(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _frame(*msgs) -> bytes:
    return _bin(list(msgs))


AUTH_OK = {"T": "success", "msg": "authenticated"}
CONN = {"T": "success", "msg": "connected"}
SUB_OK = {"T": "subscription", "trades": [], "quotes": [], "bars": ["NTLA"], "updatedBars": ["NTLA"]}
ERR_AUTH = {"T": "error", "code": 402, "msg": "auth failed"}


def _barra_msg(**cambios) -> dict:
    base = {
        "T": "b", "S": "NTLA", "o": 12.8, "h": 12.9, "l": 12.7, "c": 12.85,
        "v": 1000, "t": "2026-09-28T14:31:00Z", "n": 12, "vw": 12.82,
    }
    base.update(cambios)
    return base


def _universo(simbolos=("NTLA",)):
    mapa = {s: s for s in simbolos}
    return sip.Universo(
        watchlist="leida", posiciones="leida", simbolos=list(simbolos), mapa=mapa,
    )


class _Socket:
    def __init__(self, pasos):
        self.pasos = list(pasos)
        self.enviados = []
        self.cerrado = False

    async def enviar(self, texto: str) -> None:
        self.enviados.append(json.loads(texto))

    async def recibir(self):
        if not self.pasos:
            await asyncio.Future()
        paso = self.pasos.pop(0)
        if paso is SILENCIO:
            await asyncio.Future()
        if isinstance(paso, type) and issubclass(paso, BaseException):
            raise paso()
        if isinstance(paso, BaseException):
            raise paso
        return paso

    async def cerrar(self) -> None:
        self.cerrado = True


class _Fabrica:
    def __init__(self, sockets):
        self.sockets = list(sockets)
        self.urls = []

    async def __call__(self, url):
        self.urls.append(url)
        if not self.sockets:
            raise AssertionError("no había otra conexión preparada")
        return self.sockets.pop(0)


def _correr(tmp_path, fabrica, *, parada=None, al_anotar=None, **kw):
    delays = []

    async def dormir(segundos):
        delays.append(segundos)

    async def cuerpo():
        return await sip.correr(
            directorio=tmp_path / "sip",
            api_key=kw.get("api_key", CLAVE),
            api_secret=kw.get("api_secret", SECRETO),
            conector=fabrica,
            dormir=dormir,
            parada=parada if parada is not None else asyncio.Event(),
            vigilancia=kw.get("vigilancia"),
            ahora=kw.get("ahora", lambda: AHORA),
            modo=kw.get("modo", "sombra"),
            universo_fn=kw.get("universo_fn", lambda: _universo()),
            espera_recepcion=kw.get("espera_recepcion", 30),
            espera_auth=kw.get("espera_auth", 5),
            max_fallos_auth=kw.get("max_fallos_auth", 3),
            al_anotar=al_anotar,
            repo=REPO,
        )

    rc = asyncio.run(asyncio.wait_for(cuerpo(), timeout=2))
    return rc, delays, tmp_path / "sip"


# ------------------------- url y flag -------------------------

def test_la_url_es_el_sip_de_datos_y_no_se_puede_cambiar():
    assert sip.URL_STREAM_SIP == "wss://stream.data.alpaca.markets/v2/sip"
    assert sip.afirmar_url_sip(sip.URL_STREAM_SIP) == sip.URL_STREAM_SIP
    fuente_txt = Path(sip.__file__).read_text(encoding="utf-8")
    assert "https://api.alpaca.markets" not in fuente_txt
    assert "paper-api.alpaca.markets" not in fuente_txt
    assert "/v2/orders" not in fuente_txt
    assert "/v2/positions" not in fuente_txt
    assert sip.CONEXIONES_MAX == 1


@pytest.mark.parametrize("url", [
    "wss://stream.data.alpaca.markets/v2/iex",
    "wss://stream.data.alpaca.markets/v2/sip?feed=sip",
    "wss://api.alpaca.markets/stream",
    "wss://paper-api.alpaca.markets/stream",
    "https://data.alpaca.markets/v2/sip",
])
def test_otra_url_no_conecta(url):
    llamadas = []

    async def conector(u):
        llamadas.append(u)

    with pytest.raises(sip.UrlNoEsSip) as ei:
        asyncio.run(sip.correr(url=url, api_key=CLAVE, api_secret=SECRETO, conector=conector))
    assert SECRETO not in str(ei.value)
    assert llamadas == []


def test_la_cli_no_acepta_cambiar_el_host():
    with pytest.raises(SystemExit) as ei:
        sip.main(["--url", "wss://stream.data.alpaca.markets/v2/iex"])
    assert ei.value.code == 2


def test_flag_default_es_sombra_y_un_texto_raro_no_enciende_primario(monkeypatch):
    monkeypatch.delenv(sip.ENV_MODO, raising=False)
    assert sip.modo_configurado() == "sombra"
    assert sip.modo_configurado("primario") == "primario"
    assert sip.modo_configurado("0") == "0"
    assert sip.modo_configurado("OFF") == "0"
    assert sip.modo_configurado("si") == "sombra"
    assert sip.modo_configurado("") == "sombra"


def test_flag_cero_no_abre_el_socket(tmp_path):
    llamadas = []

    async def conector(url):
        llamadas.append(url)

    rc, _d, raiz = _correr(tmp_path, _Fabrica([]), modo="0", conector=conector)
    assert rc == 0
    assert llamadas == []
    assert not (raiz / "estado.json").exists()


def test_sin_credenciales_no_conecta(tmp_path):
    llamadas = []

    async def conector(url):
        llamadas.append(url)

    rc, _d, _raiz = _correr(tmp_path, _Fabrica([]), api_key="", api_secret="", conector=conector)
    assert rc == 1
    assert llamadas == []


def test_el_almacen_dentro_del_repo_no_se_crea(tmp_path):
    llamadas = []

    async def conector(url):
        llamadas.append(url)

    destino = tmp_path / "adentro"

    async def cuerpo():
        return await sip.correr(
            directorio=destino, api_key=CLAVE, api_secret=SECRETO,
            conector=conector, modo="sombra", repo=tmp_path,
            universo_fn=lambda: _universo(),
        )

    rc = asyncio.run(cuerpo())
    assert rc == 1
    assert llamadas == []
    assert not destino.exists()


def test_el_directorio_default_usa_la_misma_raiz_de_estado(monkeypatch, tmp_path):
    monkeypatch.delenv(sip.ENV_ESTADO, raising=False)
    assert sip.directorio_stream() == sip.ESTADO_DEFAULT / "sip_stream"
    assert sip.ESTADO_DEFAULT == Path("/var/lib/momentum/estado")
    monkeypatch.setenv(sip.ENV_ESTADO, str(tmp_path))
    assert sip.directorio_stream() == tmp_path / "sip_stream"


def test_watchlist_y_revisiones_salen_del_estado_no_del_checkout(monkeypatch, tmp_path):
    """#200 ya movió esos archivos. Si el destino no existe, no se
    cae al JSON del repo: ausente no es una watchlist vacía leída de git."""
    raiz = tmp_path / "estado"
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(raiz))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "0")
    wl, rev, _overlay = sip.rutas_universo()
    assert wl == raiz / "momentum_hunter" / "watchlist.json"
    assert rev == raiz / "momentum_paper_trader" / "revisiones.json"
    assert not wl.is_file() and not rev.is_file()
    u = sip.cargar_universo(watchlist_path=wl, revisiones_path=rev, overlay_path=None)
    assert u.watchlist == "ausente"
    assert u.posiciones == "ausente"
    assert u.simbolos == []


# ------------------------- parseo -------------------------

def test_un_frame_de_barra_conserva_ohlcv():
    msgs = sip.decodificar_frame(_frame(_barra_msg()))
    assert msgs is not None and len(msgs) == 1
    barra = sip.barra_de_mensaje(msgs[0], "2026-09-28T14:32:01.000+00:00")
    assert barra["S"] == "NTLA"
    assert barra["t"] == "2026-09-28T14:31:00+00:00"
    assert barra["o"] == 12.8 and barra["h"] == 12.9
    assert barra["l"] == 12.7 and barra["c"] == 12.85
    assert barra["v"] == 1000
    assert barra["correccion"] is False


def test_una_correccion_es_la_misma_vela():
    msg = _barra_msg(T="u", v=1100, c=12.86)
    barra = sip.barra_de_mensaje(msg, "2026-09-28T14:32:30.000+00:00")
    assert barra["correccion"] is True
    assert barra["t"] == "2026-09-28T14:31:00+00:00"
    assert barra["v"] == 1100


def test_volumen_ausente_no_es_cero():
    msg = _barra_msg()
    del msg["v"]
    assert sip.barra_de_mensaje(msg, "t") is None
    msg["v"] = None
    assert sip.barra_de_mensaje(msg, "t") is None
    msg["v"] = True
    assert sip.barra_de_mensaje(msg, "t") is None
    # Un cero que el feed sí mandó se conserva.
    cero = sip.barra_de_mensaje(_barra_msg(v=0), "t")
    assert cero is not None and cero["v"] == 0


def test_un_frame_que_no_es_lista_no_se_inventa():
    assert sip.decodificar_frame(_bin({"T": "b", "S": "NTLA"})) is None
    assert sip.decodificar_frame(b"\x80\x01") is None
    assert sip.decodificar_frame(_bin(["no-objeto"])) is None
    assert sip.decodificar_frame(_frame({"T": "b"})) == [{"T": "b"}]
    assert sip.barra_de_mensaje({"T": "b"}, "t") is None


def test_la_suscripcion_no_pide_trades_ni_comodin():
    msg = sip.mensaje_subscribe(["NTLA", "AAPL"])
    assert msg["action"] == "subscribe"
    assert msg["bars"] == ["NTLA", "AAPL"]
    assert "trades" not in msg and "quotes" not in msg
    assert "*" not in msg["bars"]
    assert sip.cazar_wildcard({"T": "subscription", "bars": ["*"]}) is True
    assert sip.hunters_confirmados({"T": "subscription", "bars": ["*"]}, {"NTLA": "NTLA"}) is None
    ok = sip.hunters_confirmados(SUB_OK, {"NTLA": "NTLA"})
    assert ok == ["NTLA"]


def test_el_mensaje_de_auth_no_viaja_al_log(caplog, tmp_path):
    sock = _Socket([_frame(ERR_AUTH)])
    with caplog.at_level("DEBUG", logger="momentum_hunter.data.sip_stream"):
        rc, delays, raiz = _correr(
            tmp_path, _Fabrica([sock, _Socket([_frame(ERR_AUTH)])]),
            max_fallos_auth=2,
        )
    assert rc == 1
    assert delays == [1.0]
    assert sock.enviados[0]["action"] == "auth"
    assert all(m["action"] != "subscribe" for m in sock.enviados)
    assert SECRETO not in caplog.text and CLAVE not in caplog.text
    texto = (raiz / "eventos" / f"{DIA}.jsonl").read_text(encoding="utf-8")
    assert SECRETO not in texto and CLAVE not in texto
    assert "auth failed" not in texto
    assert "402" in texto


# ------------------------- símbolos -------------------------

def test_prioriza_posicion_luego_triggered_y_respeta_el_tope():
    filas = [("T1", "triggered")] + [(f"W{i}", "watching") for i in range(5)]
    filas.append(("BYE", "expired"))
    sus, fuera = sip.elegir_simbolos(filas, ["POS"], tope=2)
    assert sus == ["POS", "T1"]
    assert fuera[0] == "W0"
    assert "BYE" not in sus and "BYE" not in fuera


def test_posicion_ausente_no_aporta_tickers_y_no_es_cero(tmp_path):
    wl = tmp_path / "watchlist.json"
    wl.write_text(json.dumps({"entradas": [{
        "ticker": "Ntla", "nombre": "x", "estado": "watching",
        "creado_en": "2026-09-28T14:00:00+00:00",
        "actualizado_en": "2026-09-28T14:00:00+00:00",
        "transiciones": [],
    }]}), encoding="utf-8")
    rev = tmp_path / "no-esta.json"
    u = sip.cargar_universo(watchlist_path=wl, revisiones_path=rev, overlay_path=None)
    assert u.watchlist == "leida"
    assert u.posiciones == "ausente"
    assert u.simbolos == ["NTLA"]
    assert u.posiciones != "leida"


def test_un_resultado_ausente_no_es_posicion_abierta():
    estado, tickers = sip._tickers_abiertos({
        "revisiones": [
            {"ticker": "NTLA", "entro": True},
            {"ticker": "POS", "entro": True, "resultado": "abierta"},
            {"ticker": "X", "entro": False, "resultado": "abierta"},
        ],
    })
    assert estado == "leida"
    assert tickers == ["POS"]


def test_un_libro_ilegible_no_es_cero_posiciones(tmp_path):
    wl = tmp_path / "watchlist.json"
    wl.write_text("{", encoding="utf-8")
    rev = tmp_path / "revisiones.json"
    rev.write_text("{", encoding="utf-8")
    u = sip.cargar_universo(watchlist_path=wl, revisiones_path=rev, overlay_path=None)
    assert u.watchlist == "ilegible"
    assert u.posiciones == "ilegible"
    assert u.simbolos == []


def test_el_guion_sin_traduccion_no_se_suscribe():
    mapa, sin = sip.traducir(["BH-A", "ETI-P", "NTLA"])
    assert mapa["BH-A"] == "BH.A"
    assert mapa["NTLA"] == "NTLA"
    assert sin == ["ETI-P"]


# ------------------------- reconexión, latido, huecos -------------------------

def test_reconecta_con_backoff_y_vuelve_a_suscribir_barras(tmp_path):
    s1 = _Socket([_frame(CONN, AUTH_OK), _frame(SUB_OK), sip.ConexionCerrada])
    s2 = _Socket([_frame(AUTH_OK), _frame(SUB_OK), _frame(_barra_msg())])
    parada = asyncio.Event()

    def al_anotar(reg):
        if reg.get("tipo") == "barra":
            parada.set()

    rc, delays, raiz = _correr(
        tmp_path, _Fabrica([s1, s2]), parada=parada, al_anotar=al_anotar,
    )
    assert rc == 0
    assert delays == [1.0]
    assert s1.urls if False else s1.cerrado and s2.cerrado
    for sock in (s1, s2):
        assert [m["action"] for m in sock.enviados] == ["auth", "subscribe"]
        assert sock.enviados[1]["bars"] == ["NTLA"]
        assert "trades" not in sock.enviados[1]
    lineas = []
    for path in (raiz / "eventos").glob("*.jsonl"):
        lineas += [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    barras = [
        json.loads(l) for l in (raiz / "barras" / f"{DIA}.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert barras[0]["v"] == 1000
    assert any(l.get("tipo") == "hueco" and l.get("motivo") == "reconexion" for l in lineas)
    assert SECRETO not in (raiz / "barras" / f"{DIA}.jsonl").read_text(encoding="utf-8")


def test_un_silencio_escribe_heartbeat_y_no_inventa_velas(tmp_path):
    sock = _Socket([_frame(AUTH_OK), _frame(SUB_OK), SILENCIO])
    parada = asyncio.Event()

    def al_anotar(reg):
        if reg.get("tipo") == "heartbeat":
            parada.set()

    rc, _d, raiz = _correr(
        tmp_path, _Fabrica([sock]), parada=parada, al_anotar=al_anotar,
        vigilancia=sip.Vigilancia(heartbeat_s=0), espera_recepcion=0.05,
    )
    assert rc == 0
    eventos = [
        json.loads(l) for l in (raiz / "eventos" / f"{DIA}.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(l.get("tipo") == "heartbeat" and l.get("conectado") is True for l in eventos)
    assert not (raiz / "barras").exists() or not any((raiz / "barras").glob("*.jsonl"))


def test_un_salto_de_minutos_no_rellena_con_volumen_cero():
    v = sip.Vigilancia()
    t0 = datetime(2026, 9, 28, 14, 31, tzinfo=UTC)
    assert v.salto("NTLA", t0) is None
    salto = v.salto("NTLA", t0 + timedelta(minutes=3))
    assert salto["tipo"] == "salto"
    assert salto["salto_s"] == 180
    assert "v" not in salto


def test_un_segundo_proceso_no_abre_otra_conexion(tmp_path):
    primero = sip.Almacen.abrir(tmp_path / "sip", repo=REPO)
    llamadas = []

    async def conector(url):
        llamadas.append(url)

    try:
        rc, _d, _raiz = _correr(tmp_path, _Fabrica([]), conector=conector)
    finally:
        primero.cerrar()
    assert rc == 1
    assert llamadas == []


def test_estado_ilegible_no_se_reescribe_ni_conecta(tmp_path):
    raiz = tmp_path / "sip"
    raiz.mkdir()
    (raiz / "estado.json").write_text("{", encoding="utf-8")
    llamadas = []

    async def conector(url):
        llamadas.append(url)

    rc, _d, _ = _correr(tmp_path, _Fabrica([]), conector=conector)
    assert rc == 1
    assert llamadas == []
    assert (raiz / "estado.json").read_text(encoding="utf-8") == "{"


def test_la_espera_crece_y_tiene_tope():
    assert sip.espera_de_reintento(1) == 1
    assert sip.espera_de_reintento(2) == 2
    assert sip.espera_de_reintento(3) == 4
    assert sip.espera_de_reintento(8) == 30


# ------------------------- el camino de decisión no lo usa en sombra -------------------------

def _modulos(path: Path) -> set[str]:
    arbol = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            mods.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            mods.add(nodo.module)
    return mods


def test_el_listener_no_importa_el_ejecutor():
    mods = _modulos(Path(sip.__file__))
    prohibidos = {
        "momentum_paper_trader.executor",
        "momentum_paper_trader.cierre",
        "momentum_paper_trader.seguimiento",
        "momentum_paper_trader.alpaca_client",
        "momentum_paper_trader.run",
        "momentum_paper_trader.ia_decision",
    }
    assert mods.isdisjoint(prohibidos)
    texto = Path(sip.__file__).read_text(encoding="utf-8")
    for palabra in ("colocar_orden", "cerrar_posicion", "buying_power"):
        assert palabra not in texto


def test_run_y_el_ejecutor_no_importan_el_stream():
    for path in (
        REPO / "momentum_hunter" / "run.py",
        REPO / "momentum_hunter" / "report.py",
        REPO / "momentum_hunter" / "evaluator.py",
        REPO / "momentum_paper_trader" / "executor.py",
        REPO / "momentum_paper_trader" / "run.py",
        REPO / "momentum_paper_trader" / "vigia.py",
        REPO / "momentum_paper_trader" / "ia_decision.py",
    ):
        mods = _modulos(path)
        assert "momentum_hunter.data.sip_stream" not in mods, path.name
        texto = path.read_text(encoding="utf-8")
        assert "wss://stream.data.alpaca.markets" not in texto


def test_en_sombra_no_se_lee_el_almacen(monkeypatch):
    monkeypatch.delenv(sip.ENV_MODO, raising=False)

    def boom(*_a, **_k):
        raise AssertionError("sombra no debía abrir el almacén")

    monkeypatch.setattr(sip, "leer_estado", boom)
    assert sip.barras_si_cubren(["NTLA"], "1m", "5d") is None


def _estado_fresco(ahora: datetime) -> dict:
    return {
        "version": 1,
        "conectado": True,
        "conectado_desde": (ahora - timedelta(days=6)).isoformat(),
        "ultimo_heartbeat_en": ahora.isoformat(),
        "suscritos": ["NTLA"],
        "fuera_de_tope": [],
        "watchlist": "leida",
        "posiciones": "leida",
        "hueco_abierto": False,
        "huecos": [],
    }


def _escribir_almacen(raiz: Path, ahora: datetime, *, estado=None, barras=None, mala=False):
    (raiz / "barras").mkdir(parents=True, exist_ok=True)
    (raiz / "estado.json").write_text(
        json.dumps(estado if estado is not None else _estado_fresco(ahora)),
        encoding="utf-8",
    )
    lineas = []
    for i, vela in enumerate(barras or []):
        lineas.append(json.dumps(vela))
    if mala:
        lineas.append(json.dumps({
            "tipo": "barra", "S": "NTLA", "t": "2026-09-28T14:00:00+00:00",
            "o": 1, "h": 1, "l": 1, "c": 1, "v": None,
        }))
    dia = ahora.astimezone(sip._NY).date().isoformat()
    if lineas:
        (raiz / "barras" / f"{dia}.jsonl").write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _velas(ahora: datetime, n=6) -> list[dict]:
    out = []
    for i in range(n):
        t = (ahora - timedelta(minutes=n - i)).replace(second=0, microsecond=0)
        out.append({
            "tipo": "barra", "S": "NTLA", "t": t.isoformat(timespec="seconds"),
            "o": 10.0 + i, "h": 11.0 + i, "l": 9.0 + i, "c": 10.5 + i, "v": 100 + i,
            "recibido_en": (t + timedelta(seconds=61)).isoformat(),
        })
    return out


class _Primario:
    def __init__(self):
        self.llamadas = []
        self.fallidos = []

    def barras(self, tickers, dias=280):
        return {}

    def barras_intradia(self, tickers, intervalo="1m", periodo="5d"):
        self.llamadas.append(list(tickers))
        ts = [f"2026-09-28T14:{m:02d}:00+00:00" for m in range(6)]
        px = [10.0] * 6
        vol = [50.0] * 6
        return {"NTLA": BarraIntradia("NTLA", ts, px, px, px, px, vol)}


class _Yahoo:
    def barras(self, *a, **k):
        return {}

    def barras_intradia(self, *a, **k):
        return {}

    def metadata(self, tickers):
        return {}


def test_primario_viejo_o_con_hueco_cae_al_rest(monkeypatch, tmp_path):
    ahora = datetime.now(UTC)
    raiz = tmp_path / "estado" / "sip_stream"
    estado = _estado_fresco(ahora)
    estado["conectado"] = False
    _escribir_almacen(raiz, ahora, estado=estado, barras=_velas(ahora))
    monkeypatch.setenv(sip.ENV_MODO, "primario")
    monkeypatch.setenv(sip.ENV_ESTADO, str(tmp_path / "estado"))
    primario = _Primario()
    prov = ProveedorConRespaldo(primario, _Yahoo(), feed="sip")
    out = prov.barras_intradia(["NTLA"])
    assert primario.llamadas == [["NTLA"]]
    assert out["NTLA"].volume[0] == 50.0

    estado["conectado"] = True
    estado["hueco_abierto"] = True
    _escribir_almacen(raiz, ahora, estado=estado, barras=_velas(ahora))
    primario.llamadas.clear()
    prov.barras_intradia(["NTLA"])
    assert primario.llamadas == [["NTLA"]]


def test_primario_fresco_sirve_el_almacen_y_no_pisa_un_volumen(monkeypatch, tmp_path):
    ahora = datetime.now(UTC)
    raiz = tmp_path / "estado" / "sip_stream"
    velas = _velas(ahora)
    _escribir_almacen(raiz, ahora, barras=velas)
    monkeypatch.setenv(sip.ENV_MODO, "primario")
    monkeypatch.setenv(sip.ENV_ESTADO, str(tmp_path / "estado"))
    primario = _Primario()
    prov = ProveedorConRespaldo(primario, _Yahoo(), feed="sip")
    out = prov.barras_intradia(["NTLA"])
    assert primario.llamadas == []
    assert out["NTLA"].volume[-1] == velas[-1]["v"]
    assert out["NTLA"].close[-1] == velas[-1]["c"]


def test_una_serie_corta_no_reemplaza_al_rest(monkeypatch, tmp_path):
    ahora = datetime.now(UTC)
    raiz = tmp_path / "estado" / "sip_stream"
    _escribir_almacen(raiz, ahora, barras=_velas(ahora, n=3))
    monkeypatch.setenv(sip.ENV_MODO, "primario")
    monkeypatch.setenv(sip.ENV_ESTADO, str(tmp_path / "estado"))
    primario = _Primario()
    prov = ProveedorConRespaldo(primario, _Yahoo(), feed="sip")
    prov.barras_intradia(["NTLA"])
    assert primario.llamadas == [["NTLA"]]


def test_una_linea_sin_volumen_tira_el_pedido_al_rest(monkeypatch, tmp_path):
    ahora = datetime.now(UTC)
    raiz = tmp_path / "estado" / "sip_stream"
    _escribir_almacen(raiz, ahora, barras=_velas(ahora), mala=True)
    monkeypatch.setenv(sip.ENV_MODO, "primario")
    monkeypatch.setenv(sip.ENV_ESTADO, str(tmp_path / "estado"))
    primario = _Primario()
    prov = ProveedorConRespaldo(primario, _Yahoo(), feed="sip")
    prov.barras_intradia(["NTLA"])
    assert primario.llamadas == [["NTLA"]]


def test_primario_no_toca_yahoo_ni_iex(monkeypatch):
    monkeypatch.delenv("MOMENTUM_DATA_PROVIDER", raising=False)
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    monkeypatch.setenv(sip.ENV_MODO, "primario")

    def boom(*_a, **_k):
        raise AssertionError("no debía consultar el stream")

    monkeypatch.setattr(sip, "barras_si_cubren", boom)
    yahoo = fuente.proveedor_configurado(construir_yahoo=_Yahoo)
    yahoo.barras_intradia(["NTLA"])
    iex = ProveedorConRespaldo(_Primario(), _Yahoo(), feed="iex")
    iex.barras_intradia(["NTLA"])


def test_sombra_aunque_el_almacen_este_fresco_va_al_rest(monkeypatch, tmp_path):
    ahora = datetime.now(UTC)
    raiz = tmp_path / "estado" / "sip_stream"
    _escribir_almacen(raiz, ahora, barras=_velas(ahora))
    monkeypatch.setenv(sip.ENV_MODO, "sombra")
    monkeypatch.setenv(sip.ENV_ESTADO, str(tmp_path / "estado"))
    primario = _Primario()
    prov = ProveedorConRespaldo(primario, _Yahoo(), feed="sip")
    prov.barras_intradia(["NTLA"])
    assert primario.llamadas == [["NTLA"]]


# ------------------------- comparador -------------------------

def _fila(simbolo="NTLA", minuto="2026-09-28T14:31:00+00:00", **cambios):
    base = {
        "S": simbolo, "t": minuto, "o": 12.8, "h": 12.9, "l": 12.7, "c": 12.85, "v": 1000,
        "recibido_en": "2026-09-28T14:32:01+00:00",
    }
    base.update(cambios)
    return base


def test_ohlcv_dentro_de_tolerancia_no_es_discrepancia():
    stream = [_fila(c=12.855)]
    rest = [_fila(c=12.85)]
    inf = cmp.comparar(DIA, stream=stream, rest=rest)
    assert inf.discrepancias == []
    assert inf.solo_rest == [] and inf.solo_stream == []
    assert inf.cobertura["ratio"] == 1.0
    assert inf.lags[0]["lag_ms"] == 1000.0
    assert cmp.codigo_salida(inf) == 0


def test_un_centavo_de_mas_en_un_precio_grande_es_discrepancia():
    inf = cmp.comparar(DIA, stream=[_fila(c=12.87)], rest=[_fila(c=12.85)])
    assert len(inf.discrepancias) == 1
    assert inf.discrepancias[0]["campo"] == "c"
    assert cmp.codigo_salida(inf) == 1


def test_bajo_un_dolar_la_tolerancia_es_de_cuatro_decimales():
    assert cmp.precio_dentro(0.5000, 0.5001) is True
    assert cmp.precio_dentro(0.5000, 0.5003) is False
    assert cmp.precio_dentro(10.0, 10.009) is True
    assert cmp.precio_dentro(10.0, 10.02) is False


def test_volumen_distinto_y_volumen_ausente_no_es_cero():
    inf = cmp.comparar(DIA, stream=[_fila(v=1001)], rest=[_fila(v=1000)])
    assert inf.discrepancias[0]["campo"] == "v"
    inf2 = cmp.comparar(DIA, stream=[_fila(v=None)], rest=[_fila(v=1000)])
    assert inf2.discrepancias == []
    assert inf2.ausentes[0]["campo"] == "v"
    assert inf2.ausentes[0]["stream"] is None
    assert inf2.ausentes[0]["stream"] != 0
    assert cmp.codigo_salida(inf2) == 1


def test_un_minuto_que_solo_vio_el_rest_baja_la_cobertura():
    rest = [_fila(), _fila(minuto="2026-09-28T14:32:00+00:00")]
    inf = cmp.comparar(DIA, stream=[_fila()], rest=rest)
    assert inf.solo_rest == [{"S": "NTLA", "t": "2026-09-28T14:32:00+00:00"}]
    assert inf.cobertura["ratio"] == 0.5
    assert inf.cobertura["minutos_rest"] == 2


def test_sin_rest_el_ratio_no_es_cero():
    inf = cmp.comparar(DIA, stream=[_fila()], rest=None)
    assert inf.cobertura["ratio"] is None
    assert inf.cobertura["ratio"] != 0
    assert inf.solo_rest == []
    assert inf.n_barras_rest is None
    assert cmp.codigo_salida(inf) == 2


def test_sin_reloj_de_llegada_el_lag_no_es_cero():
    inf = cmp.comparar(DIA, stream=[_fila(recibido_en=None)], rest=[_fila()])
    assert inf.lags[0]["lag_ms"] is None
    assert inf.lags[0]["lag_ms"] != 0


def test_el_jsonl_del_dia_se_compara_y_deja_telemetria(tmp_path):
    raiz = tmp_path / "sip"
    (raiz / "barras").mkdir(parents=True)
    (raiz / "eventos").mkdir()
    (raiz / "barras" / f"{DIA}.jsonl").write_text(
        json.dumps({"tipo": "barra", **_fila()}) + "\n", encoding="utf-8",
    )
    (raiz / "eventos" / f"{DIA}.jsonl").write_text(
        json.dumps({"tipo": "hueco", "motivo": "reconexion", "recibido_en": "2026-09-28T14:00:00+00:00"}) + "\n",
        encoding="utf-8",
    )
    inf = cmp.comparar_dia(DIA, raiz, rest=[_fila(c=13.0)])
    assert inf.huecos[0]["motivo"] == "reconexion"
    assert inf.discrepancias
    rc_inf = cmp.codigo_salida(inf)
    assert rc_inf == 1
    inf_vacio = cmp.comparar_dia(DIA, raiz, rest=None)
    assert inf_vacio.fuentes["rest"] == "ausente"
    assert cmp.codigo_salida(inf_vacio) == 2


# ------------------------- unidad -------------------------

def test_la_unidad_es_un_proceso_en_sombra_que_no_elige_el_host():
    texto = (REPO / "infra" / "systemd" / "momentum-sip-stream.service").read_text(encoding="utf-8")
    assert "Type=simple" in texto
    assert "Restart=on-failure" in texto
    assert "User=momentum" in texto
    assert "EnvironmentFile=/etc/momentum/paper.env" in texto
    assert "run_sip_stream.sh" in texto
    assert "wss://" not in texto
    assert "enable --now momentum-sip-stream.service" in texto
    wrapper = (REPO / "infra" / "systemd" / "bin" / "run_sip_stream.sh").read_text(encoding="utf-8")
    assert "MOMENTUM_SIP_STREAM=sombra" in wrapper
    assert "MOMENTUM_ESTADO_DIR:-/var/lib/momentum/estado" in wrapper
    assert "momentum_hunter.data.sip_stream" in wrapper
    assert "wss://" not in wrapper
