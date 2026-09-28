"""Sombra de trade_updates: parseo, reconexión, rechazo de un host que
no es paper, y el comparador. Nada de esto coloca una orden ni habla
con Alpaca: el socket es falso y el REST está mockeado."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from momentum_paper_trader import alpaca_client, trade_updates_comparar as cmp
from momentum_paper_trader import trade_updates_sombra as sombra

REPO = Path(__file__).resolve().parents[2]
DIA = "2026-09-28"
CLAVE = "K" * 24
SECRETO = "S" * 24
SILENCIO = object()


def _bin(obj) -> bytes:
    return json.dumps(obj).encode("utf-8")


AUTH_OK = {"stream": "authorization", "data": {"status": "authorized", "action": "authenticate"}}
LISTEN_OK = {"stream": "listening", "data": {"streams": ["trade_updates"]}}
UNAUTH = {"stream": "authorization", "data": {"status": "unauthorized", "action": "authenticate"}}


def _fill(order_id="abc", status="filled", filled_qty="58", price="12.88", event="fill") -> dict:
    return {
        "stream": "trade_updates",
        "data": {
            "event": event,
            "price": price,
            "timestamp": "2026-09-28T14:00:01.5Z",
            "qty": "10",
            "order": {
                "id": order_id,
                "client_order_id": "momentum-NTLA-1",
                "symbol": "NTLA",
                "qty": "58",
                "filled_qty": filled_qty,
                "status": status,
            },
        },
    }


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
    jsonl = tmp_path / "trade_updates.jsonl"
    estado = tmp_path / "trade_updates_estado.json"
    delays = []

    async def dormir(segundos):
        delays.append(segundos)

    async def cuerpo():
        return await sombra.correr(
            jsonl=jsonl,
            estado=estado,
            api_key=kw.get("api_key", CLAVE),
            api_secret=kw.get("api_secret", SECRETO),
            conector=fabrica,
            dormir=dormir,
            parada=parada if parada is not None else asyncio.Event(),
            vigilancia=kw.get("vigilancia"),
            ahora=kw.get("ahora", lambda: datetime(2026, 9, 28, 14, 0, tzinfo=UTC)),
            espera_recepcion=kw.get("espera_recepcion", 30),
            espera_auth=kw.get("espera_auth", 5),
            max_fallos_auth=kw.get("max_fallos_auth", 3),
            al_anotar=al_anotar,
        )

    rc = asyncio.run(cuerpo())
    return rc, delays, jsonl, estado


# ------------------------- url paper -------------------------

def test_la_constante_es_el_stream_paper():
    assert sombra.URL_STREAM_PAPER == "wss://paper-api.alpaca.markets/stream"
    assert sombra.afirmar_url_paper(sombra.URL_STREAM_PAPER) == sombra.URL_STREAM_PAPER
    fuente = Path(sombra.__file__).read_text(encoding="utf-8")
    assert "wss://api.alpaca.markets" not in fuente
    assert "https://api.alpaca.markets" not in fuente


def test_una_url_que_no_es_paper_no_conecta():
    llamadas = []

    async def conector(url):
        llamadas.append(url)
        raise AssertionError("no debía abrir el socket")

    with pytest.raises(sombra.UrlNoEsPaper):
        asyncio.run(sombra.correr(
            url="wss://api.alpaca.markets/stream",
            api_key=CLAVE, api_secret=SECRETO, conector=conector,
        ))
    assert llamadas == []


def test_la_url_rechazada_no_se_repite_si_trae_un_secreto():
    secreto = "super-secret-value"
    url = f"wss://user:{secreto}@paper-api.alpaca.markets/stream"
    with pytest.raises(sombra.UrlNoEsPaper) as ei:
        sombra.afirmar_url_paper(url)
    assert secreto not in str(ei.value)
    with pytest.raises(sombra.UrlNoEsPaper):
        sombra.afirmar_url_paper("https://paper-api.alpaca.markets/v2")
    with pytest.raises(sombra.UrlNoEsPaper):
        sombra.afirmar_url_paper("wss://paper-api.alpaca.markets/v2")


def test_la_cli_no_acepta_cambiar_el_stream():
    with pytest.raises(SystemExit) as ei:
        sombra.main(["--url", "wss://api.alpaca.markets/stream"])
    assert ei.value.code == 2


def test_sin_credenciales_no_conecta(tmp_path):
    llamadas = []

    async def conector(url):
        llamadas.append(url)
        raise AssertionError("no debía conectar")

    rc = asyncio.run(sombra.correr(
        jsonl=tmp_path / "t.jsonl", estado=tmp_path / "e.json",
        api_key="", api_secret="", conector=conector,
    ))
    assert rc == 1
    assert llamadas == []


# ------------------------- frames -------------------------

@pytest.mark.parametrize("nombre", [
    "new", "fill", "partial_fill", "canceled", "expired",
    "rejected", "replaced", "pending_new",
])
def test_un_frame_binario_conserva_el_evento(nombre):
    msg = {
        "stream": "trade_updates",
        "data": {
            "event": nombre,
            "timestamp": "2026-09-28T14:00:01Z",
            "order": {"id": "abc", "client_order_id": "c1", "symbol": "NTLA", "qty": "58"},
        },
    }
    ev = sombra.evento_de_mensaje(
        sombra.decodificar_frame(_bin(msg)), "2026-09-28T14:00:02+00:00",
    )
    assert ev is not None
    assert ev["event"] == nombre
    assert ev["order_id"] == "abc"
    assert ev["client_order_id"] == "c1"
    assert ev["symbol"] == "NTLA"
    assert ev["qty"] == "58"
    assert ev["filled_qty"] is None
    assert ev["price"] is None
    assert ev["filled_qty"] != 0 and ev["price"] != 0


def test_el_parcial_no_confunde_qty_de_la_orden_con_la_del_evento():
    msg = _fill(event="partial_fill", status="partially_filled", filled_qty="10", price="12.50")
    msg["data"]["qty"] = "10"
    msg["data"]["order"]["qty"] = "58"
    crudo = _bin(msg)
    assert isinstance(crudo, bytes)
    ev = sombra.evento_de_mensaje(sombra.decodificar_frame(crudo), "2026-09-28T14:00:02+00:00")
    assert ev["qty"] == "58"
    assert ev["event_qty"] == "10"
    assert ev["filled_qty"] == "10"
    assert ev["price"] == "12.50"
    assert ev["timestamp"] == "2026-09-28T14:00:01.5Z"


def test_un_frame_de_texto_tambien_se_lee():
    ev = sombra.evento_de_mensaje(
        sombra.decodificar_frame(json.dumps(_fill())), "2026-09-28T14:00:02+00:00",
    )
    assert ev["event"] == "fill"
    assert ev["order_id"] == "abc"


def test_un_binario_que_no_es_json_no_se_inventa():
    assert sombra.decodificar_frame(b"\x80\x01\x02") is None
    assert sombra.decodificar_frame(_bin(["no", "objeto"])) is None
    assert sombra.evento_de_mensaje({"stream": "trade_updates", "data": {}}, "t") is None
    assert sombra.evento_de_mensaje({"stream": "otro", "data": {"event": "fill"}}, "t") is None


def test_el_estado_no_pisa_un_precio_visto_con_el_null_de_un_cancel(tmp_path):
    esc = sombra.Escritor.abrir(tmp_path / "t.jsonl", tmp_path / "e.json", secretos=(CLAVE, SECRETO))
    lleno = sombra.evento_de_mensaje(sombra.decodificar_frame(_bin(_fill())), "2026-09-28T14:00:02+00:00")
    esc.anotar(lleno)
    cancel = {
        "tipo": "trade_update",
        "event": "canceled",
        "order_id": "abc",
        "symbol": "NTLA",
        "status": "canceled",
        "qty": "58",
        "filled_qty": None,
        "price": None,
        "timestamp": "2026-09-28T14:05:00Z",
        "recibido_en": "2026-09-28T14:05:00.100+00:00",
    }
    esc.anotar(cancel)
    esc.cerrar()
    estado = json.loads((tmp_path / "e.json").read_text(encoding="utf-8"))
    orden = estado["ordenes"]["abc"]
    assert orden["event"] == "canceled"
    assert orden["status"] == "canceled"
    assert orden["filled_qty"] == "58"
    assert orden["price"] == "12.88"
    assert estado["version"] == 1


def test_un_frame_no_guarda_el_secreto(tmp_path):
    esc = sombra.Escritor.abrir(tmp_path / "t.jsonl", tmp_path / "e.json", secretos=(CLAVE, SECRETO))
    esc.anotar({
        "tipo": "trade_update", "event": "fill", "order_id": "abc",
        "symbol": f"NTLA-{SECRETO}", "status": "filled",
    })
    esc.cerrar()
    texto = (tmp_path / "t.jsonl").read_text(encoding="utf-8")
    assert SECRETO not in texto and CLAVE not in texto
    assert "***" in texto


def test_el_estado_ilegible_no_se_reescribe_ni_conecta(tmp_path):
    estado = tmp_path / "e.json"
    estado.write_text("{", encoding="utf-8")
    llamadas = []

    async def conector(url):
        llamadas.append(url)
        raise AssertionError("no debía conectar")

    rc = asyncio.run(sombra.correr(
        jsonl=tmp_path / "t.jsonl", estado=estado,
        api_key=CLAVE, api_secret=SECRETO, conector=conector,
    ))
    assert rc == 1
    assert llamadas == []
    assert estado.read_text(encoding="utf-8") == "{"


def test_un_segundo_proceso_no_abre_el_mismo_log(tmp_path):
    a = sombra.Escritor.abrir(tmp_path / "t.jsonl", tmp_path / "e.json")
    try:
        with pytest.raises(sombra.YaCorre):
            sombra.Escritor.abrir(tmp_path / "t.jsonl", tmp_path / "e.json")
    finally:
        a.cerrar()
    b = sombra.Escritor.abrir(tmp_path / "t.jsonl", tmp_path / "e.json")
    b.cerrar()


def test_la_espera_crece_y_tiene_tope():
    assert sombra.espera_de_reintento(1) == 1
    assert sombra.espera_de_reintento(2) == 2
    assert sombra.espera_de_reintento(3) == 4
    assert sombra.espera_de_reintento(6) == 30
    assert sombra.espera_de_reintento(20) == 30


# ------------------------- reconexión y auth -------------------------

def test_reconecta_con_backoff_y_vuelve_a_pedir_listen(tmp_path):
    s1 = _Socket([_bin(AUTH_OK), _bin(LISTEN_OK), sombra.ConexionCerrada])
    s2 = _Socket([_bin(AUTH_OK), _bin(LISTEN_OK), _bin(_fill())])
    parada = asyncio.Event()

    def al_anotar(reg):
        if reg.get("event") == "fill":
            parada.set()

    rc, delays, jsonl, _estado = _correr(
        tmp_path, _Fabrica([s1, s2]), parada=parada, al_anotar=al_anotar,
    )
    assert rc == 0
    assert delays == [1.0]
    for sock in (s1, s2):
        assert [m["action"] for m in sock.enviados] == ["auth", "listen"]
        assert sock.enviados[1]["data"]["streams"] == ["trade_updates"]
        assert sock.cerrado
    lineas = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines()]
    fills = [l for l in lineas if l.get("event") == "fill"]
    assert len(fills) == 1
    assert fills[0]["order_id"] == "abc"
    assert any(l.get("tipo") == "hueco" and l.get("motivo") == "reconexion" for l in lineas)
    texto = jsonl.read_text(encoding="utf-8")
    assert SECRETO not in texto and CLAVE not in texto


def test_auth_rechazada_no_escucha_y_no_loguea_el_secreto(caplog, tmp_path):
    s1 = _Socket([_bin(UNAUTH)])
    s2 = _Socket([_bin(UNAUTH)])
    with caplog.at_level(logging.DEBUG, logger="momentum_paper_trader.trade_updates_sombra"):
        rc, delays, jsonl, _estado = _correr(
            tmp_path, _Fabrica([s1, s2]), max_fallos_auth=2,
        )
    assert rc == 1
    assert delays == [1.0]
    assert s1.enviados[0]["action"] == "auth"
    assert all(m["action"] != "listen" for m in s1.enviados)
    assert all(m["action"] != "listen" for m in s2.enviados)
    assert SECRETO not in caplog.text and CLAVE not in caplog.text
    texto = jsonl.read_text(encoding="utf-8")
    assert SECRETO not in texto and CLAVE not in texto
    assert "unauthorized" in texto


def test_un_silencio_escribe_heartbeat(tmp_path):
    sock = _Socket([_bin(AUTH_OK), _bin(LISTEN_OK), SILENCIO])
    parada = asyncio.Event()

    def al_anotar(reg):
        if reg.get("tipo") == "heartbeat":
            parada.set()

    rc, _delays, jsonl, _estado = _correr(
        tmp_path, _Fabrica([sock]), parada=parada, al_anotar=al_anotar,
        vigilancia=sombra.Vigilancia(heartbeat_s=0, hueco_s=10_000),
        espera_recepcion=0.05,
    )
    assert rc == 0
    lineas = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines()]
    beats = [l for l in lineas if l.get("tipo") == "heartbeat"]
    assert beats
    assert beats[0]["conectado"] is True
    assert isinstance(beats[0]["silencio_s"], (int, float))


def test_la_vigilancia_anota_un_solo_hueco_hasta_que_vuelve_un_frame():
    t0 = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)
    v = sombra.Vigilancia(heartbeat_s=60, hueco_s=120)
    assert v.revisar(t0) == []
    assert [r["tipo"] for r in v.revisar(t0 + timedelta(seconds=60))] == ["heartbeat"]
    tipos = [r["tipo"] for r in v.revisar(t0 + timedelta(seconds=120))]
    assert tipos == ["heartbeat", "hueco"]
    assert [r["tipo"] for r in v.revisar(t0 + timedelta(seconds=180))] == ["heartbeat"]
    cerrados = v.recibido(t0 + timedelta(seconds=200))
    assert cerrados[0]["tipo"] == "hueco_cerrado"
    assert cerrados[0]["silencio_s"] == 200


# ------------------------- el camino de órdenes no lee esto -------------------------

def test_el_camino_de_ordenes_no_importa_la_sombra():
    raiz = Path(__file__).resolve().parents[1]
    for nombre in (
        "cierre.py", "seguimiento.py", "executor.py", "reconciliacion.py",
        "run.py", "vigia.py", "ia_decision.py",
    ):
        texto = (raiz / nombre).read_text(encoding="utf-8")
        assert "trade_updates_sombra" not in texto, nombre
        assert "trade_updates_comparar" not in texto, nombre
        assert "trade_updates.jsonl" not in texto, nombre
        assert "wss://" not in texto, nombre


def _modulos(path: Path) -> set[str]:
    arbol = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            mods.update(a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            mods.add(nodo.module)
    return mods


def test_el_listener_no_importa_el_ejecutor_ni_el_cierre():
    mods = _modulos(Path(sombra.__file__))
    prohibidos = {
        "momentum_paper_trader.executor",
        "momentum_paper_trader.cierre",
        "momentum_paper_trader.seguimiento",
        "momentum_paper_trader.reconciliacion",
        "momentum_paper_trader.ia_decision",
        "momentum_paper_trader.run",
        "momentum_paper_trader.vigia",
        "momentum_paper_trader.alpaca_client",
    }
    assert mods.isdisjoint(prohibidos)
    texto = Path(sombra.__file__).read_text(encoding="utf-8")
    for palabra in ("colocar_orden", "cerrar_posicion", "vender_a_mercado", "buying_power"):
        assert palabra not in texto


def test_el_comparador_no_importa_el_cierre_ni_coloca():
    mods = _modulos(Path(cmp.__file__))
    for prohibido in (
        "momentum_paper_trader.executor",
        "momentum_paper_trader.cierre",
        "momentum_paper_trader.seguimiento",
        "momentum_paper_trader.reconciliacion",
        "momentum_paper_trader.ia_decision",
    ):
        assert prohibido not in mods
    texto = Path(cmp.__file__).read_text(encoding="utf-8")
    for palabra in ("colocar_orden", "cerrar_posicion", "vender_a_mercado"):
        assert palabra not in texto


# ------------------------- comparador -------------------------

def _orden_rest(status="filled", filled_qty="58"):
    return {
        "id": "abc",
        "client_order_id": "momentum-NTLA-1",
        "symbol": "NTLA",
        "qty": "58",
        "filled_qty": filled_qty,
        "status": status,
        "submitted_at": "2026-09-28T14:00:00Z",
        "filled_at": "2026-09-28T14:00:01Z",
    }


def _evento_sombra(**cambios):
    base = {
        "tipo": "trade_update",
        "event": "fill",
        "order_id": "abc",
        "client_order_id": "momentum-NTLA-1",
        "symbol": "NTLA",
        "qty": "58",
        "filled_qty": "58",
        "price": "12.88",
        "status": "filled",
        "timestamp": "2026-09-28T14:00:01Z",
        "recibido_en": "2026-09-28T14:00:01+00:00",
    }
    base.update(cambios)
    return base


def test_detecta_un_evento_que_el_rest_vio_y_el_stream_no():
    inf = cmp.comparar(
        DIA,
        sombra=[],
        ordenes_rest=[_orden_rest()],
        revisiones=None,
        eventos=None,
    )
    assert inf.n_eventos_sombra == 0
    ids = [e["order_id"] for e in inf.eventos_ausentes]
    assert "abc" in ids
    assert any(e["motivo"] == "orden_rest_sin_evento_en_el_stream" for e in inf.eventos_ausentes)
    assert cmp.codigo_salida(inf) == 1


def test_detecta_status_distinto():
    inf = cmp.comparar(
        DIA,
        sombra=[_evento_sombra()],
        ordenes_rest=[_orden_rest(status="new")],
        revisiones=[],
        eventos=[],
    )
    assert len(inf.estados_distintos) == 1
    fila = inf.estados_distintos[0]
    assert fila["status_sombra"] == "filled"
    assert fila["status_rest"] == "new"
    assert fila["motivo"] == "status_distinto"
    assert cmp.codigo_salida(inf) == 1


def test_la_latencia_es_el_reloj_rest_menos_el_del_stream():
    inf = cmp.comparar(
        DIA,
        sombra=[_evento_sombra(event="new", status="new", price=None, filled_qty="0")],
        ordenes_rest=[_orden_rest(status="new", filled_qty="0")],
        eventos=[{
            "ts": "2026-09-28T14:00:03+00:00",
            "tipo": "orden",
            "estado": "enviada",
            "order_id": "abc",
            "ticker": "NTLA",
        }],
        revisiones=[],
    )
    assert inf.eventos_ausentes == []
    assert inf.estados_distintos == []
    assert len(inf.latencias) == 1
    assert inf.latencias[0]["latencia_ms"] == 2000.0
    assert inf.latencias[0]["reloj_rest"] == "events.jsonl"
    assert cmp.codigo_salida(inf) == 0


def test_sin_reloj_de_deteccion_la_latencia_no_es_cero():
    inf = cmp.comparar(
        DIA,
        sombra=[_evento_sombra()],
        revisiones=[{
            "ticker": "NTLA",
            "creado_en": "x",
            "entro": True,
            "timestamp": "2026-09-28T13:59:00+00:00",
            "order_id": "abc",
            "resultado": "abierta",
        }],
        eventos=[],
        ordenes_rest=None,
    )
    assert inf.estados_distintos == []
    assert inf.eventos_ausentes == []
    assert len(inf.latencias) == 1
    assert inf.latencias[0]["latencia_ms"] is None
    assert inf.latencias[0]["latencia_ms"] != 0
    assert inf.latencias[0]["motivo"] == "sin_reloj_de_deteccion_rest"
    assert cmp.codigo_salida(inf) == 0


def test_filled_qty_ausente_no_se_trata_como_cero():
    inf = cmp.comparar(
        DIA,
        sombra=[_evento_sombra(filled_qty=None, status="new", event="new", price=None)],
        ordenes_rest=[_orden_rest(status="new", filled_qty="0")],
        revisiones=[],
        eventos=[],
    )
    assert inf.datos_ausentes
    fila = inf.datos_ausentes[0]
    assert fila["campo"] == "filled_qty"
    assert fila["sombra"] is None
    assert fila["rest"] == "0"
    assert fila["sombra"] != 0
    assert inf.datos_distintos == []
    assert cmp.codigo_salida(inf) == 1


def test_seguimiento_no_ejecutada_contra_un_fill_es_estado_distinto():
    inf = cmp.comparar(
        DIA,
        sombra=[_evento_sombra()],
        revisiones=[{
            "ticker": "NTLA",
            "timestamp": "2026-09-28T13:50:00+00:00",
            "order_id": "abc",
            "entro": True,
            "resultado": "no_ejecutada",
        }],
        eventos=[],
        ordenes_rest=None,
    )
    assert any(e["motivo"] == "seguimiento_sin_ejecutar_y_stream_con_fill" for e in inf.estados_distintos)


def test_una_fuente_ausente_no_es_cero_eventos():
    inf = cmp.comparar(DIA, sombra=None, ordenes_rest=[_orden_rest()])
    assert inf.n_eventos_sombra is None
    assert inf.fuentes["sombra"] == "ausente"
    assert inf.eventos_ausentes == []
    assert cmp.codigo_salida(inf) == 2


def test_pagina_llena_no_acusa_al_stream_de_una_orden_que_el_rest_no_trajo():
    inf = cmp.comparar(
        DIA,
        sombra=[_evento_sombra(order_id="solo-stream")],
        ordenes_rest=[],
        ordenes_truncadas=True,
        revisiones=[],
        eventos=[],
    )
    assert not any(e.get("lado") == "rest" for e in inf.eventos_ausentes)


def test_la_cli_informa_la_discrepancia(tmp_path, capsys):
    sombra_path = tmp_path / "s.jsonl"
    sombra_path.write_text("", encoding="utf-8")
    rest = tmp_path / "ordenes.json"
    rest.write_text(json.dumps([_orden_rest()]), encoding="utf-8")
    ausente = tmp_path / "no-esta.json"
    rc = cmp.main([
        "--dia", DIA,
        "--sombra", str(sombra_path),
        "--estado", str(ausente),
        "--revisiones", str(ausente),
        "--eventos", str(ausente),
        "--ordenes-rest", str(rest),
    ])
    assert rc == 1
    informe = json.loads(capsys.readouterr().out)
    assert informe["n_eventos_sombra"] == 0
    assert any(e["order_id"] == "abc" for e in informe["eventos_ausentes"])


# ------------------------- GET de solo lectura -------------------------

def test_ordenes_del_dia_es_get_paper_status_all(monkeypatch):
    llamadas = []

    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"id": "abc", "status": "filled"}]

    def fake_get(url, params=None, headers=None, timeout=None):
        llamadas.append((url, params))
        return _R()

    monkeypatch.setattr(alpaca_client.requests, "get", fake_get)
    out = alpaca_client.AlpacaPaperClient("k", "s").ordenes_del_dia(
        "2026-09-21T00:00:00-04:00", "2026-09-29T00:00:00-04:00",
    )
    assert out[0]["id"] == "abc"
    assert llamadas[0][0] == "https://paper-api.alpaca.markets/v2/orders"
    assert llamadas[0][1]["status"] == "all"
    assert llamadas[0][1]["after"] == "2026-09-21T00:00:00-04:00"
    assert "api.alpaca.markets" not in llamadas[0][0] or llamadas[0][0].startswith("https://paper-api")


def test_ordenes_del_dia_no_convierte_un_cuerpo_raro_en_lista_vacia(monkeypatch):
    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"oops": True}

    monkeypatch.setattr(alpaca_client.requests, "get", lambda *a, **k: _R())
    with pytest.raises(ValueError):
        alpaca_client.AlpacaPaperClient("k", "s").ordenes_del_dia("a", "b")


def test_sin_ventana_no_pide_status_all(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no debía llamar")

    monkeypatch.setattr(alpaca_client.requests, "get", boom)
    with pytest.raises(ValueError):
        alpaca_client.AlpacaPaperClient("k", "s").ordenes_del_dia("  ", "b")


# ------------------------- unidad systemd -------------------------

def test_la_unidad_es_un_proceso_paper_que_no_elige_el_host():
    texto = (REPO / "deploy" / "momentum-trade-updates-sombra.service").read_text(encoding="utf-8")
    assert "Type=simple" in texto
    assert "Restart=always" in texto
    assert "User=momentum" in texto
    assert "EnvironmentFile=/etc/momentum/paper.env" in texto
    assert "momentum_paper_trader.trade_updates_sombra" in texto
    assert "wss://" not in texto
    assert "api.alpaca.markets" not in texto or "paper-api.alpaca.markets" in texto
    assert "MOMENTUM_TRADE_UPDATES_JSONL=/var/lib/momentum/trade_updates.jsonl" in texto
    assert "--url" not in texto
    assert "enable --now momentum-trade-updates-sombra.service" in texto
