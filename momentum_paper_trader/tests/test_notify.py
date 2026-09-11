"""Formato y política anti-spam de las alertas paper -- sin red.

No se llama a Telegram ni a Alpaca. Se prueba el HTML, el escape, qué
eventos merecen aviso y qué payload se le pasaría al sender compartido."""

from __future__ import annotations

from momentum_paper_trader import notify
from momentum_paper_trader.run import _avisar_falla


def test_llenada_es_corta_y_lleva_campos_clave():
    texto = notify.formatear_llenada(
        ticker="NTLA", signal_id="2026-09-08T14:12:00+00:00",
        cantidad=58, precio_lleno=12.88, precio_limite=12.90,
        stop=12.77, objetivo=13.10,
    )
    assert texto.startswith("🧪 [PAPER] <b>LLENADA</b>")
    assert "<b>NTLA</b>" in texto
    assert "<code>2026-09-08T14:12:00+00:00</code>" in texto
    assert "58 acc @ $12.88" in texto
    assert "lím. $12.90" in texto
    assert "Stop $12.77" in texto
    assert "Obj $13.10" in texto
    assert "Cuenta de práctica" not in texto
    assert texto.count("\n") <= 8


def test_llenada_no_repite_el_limite_si_coincide_con_el_fill():
    texto = notify.formatear_llenada(
        ticker="RKLB", cantidad=65, precio_lleno=78.40, precio_limite=78.40,
        stop=76.90, objetivo=82.50,
    )
    assert "lím." not in texto
    assert "$78.40" in texto


def test_llenada_omite_campos_ausentes_no_inventa():
    texto = notify.formatear_llenada(ticker="S", precio_lleno=4.20)
    assert "<b>S</b>" in texto
    assert "$4.20" in texto
    assert "acc" not in texto
    assert "Stop" not in texto
    assert "Señal:" not in texto
    assert "?" not in texto
    assert "None" not in texto


def test_cerrada_objetivo_con_pnl():
    texto = notify.formatear_cerrada(
        ticker="RKLB", motivo=notify.MOTIVO_OBJETIVO,
        signal_id="sig-1", cantidad=65,
        precio_entrada=78.40, precio_salida=82.50, pnl=266.50,
    )
    assert texto.startswith("🧪 [PAPER] <b>CERRADA</b>")
    assert "objetivo" in texto
    assert "$78.40 → $82.50" in texto
    assert "P&L +$266.50" in texto


def test_cerrada_stop_con_perdida():
    texto = notify.formatear_cerrada(
        ticker="NTLA", motivo=notify.MOTIVO_STOP,
        cantidad=58, precio_entrada=12.88, precio_salida=12.77, pnl=-6.38,
    )
    assert "stop" in texto
    assert "P&L -$6.38" in texto


def test_cierre_dia_solo_lista_cerradas_y_suma_pl():
    texto = notify.formatear_cierre_dia([
        ({"symbol": "AAA", "qty": "10", "unrealized_pl": "100.00"}, "tesis agotada"),
        ({"symbol": "BBB", "qty": "5", "unrealized_pl": "-40.00"}, "se rompió"),
    ])
    assert "CERRADA" in texto
    assert "fin de día" in texto
    assert "+$100.00" in texto and "-$40.00" in texto
    assert "P&L del día +$60.00" in texto
    assert "tesis agotada" in texto


def test_cierre_dia_vacio_no_inventa_resumen():
    assert notify.formatear_cierre_dia([]) == ""


def test_error_no_incluye_texto_crudo_de_excepcion():
    texto = notify.formatear_error(tipo="TimeoutError")
    assert texto.startswith("🧪 [PAPER] <b>ERROR</b>")
    assert "TimeoutError" in texto
    assert "Se reintenta" in texto
    assert "https://" not in texto


def test_html_se_escapa_en_ticker_razon_y_tipo():
    texto = notify.formatear_cerrada(
        ticker="AA<BB>", motivo="stop",
        razon='subió por "news" & <script>',
    )
    assert "AA&lt;BB&gt;" in texto
    assert "&amp;" in texto
    assert "<script>" not in texto
    assert "AA<BB>" not in texto


def test_razon_larga_se_corta():
    texto = notify.formatear_cerrada(
        ticker="X", motivo="objetivo", razon="palabra " * 80,
    )
    assert "…" in texto
    assert len(texto) < 400


def test_debe_avisar_solo_fill_cierre_error():
    assert notify.debe_avisar("abierta") is True
    assert notify.debe_avisar("objetivo") is True
    assert notify.debe_avisar("stop") is True
    assert notify.debe_avisar("cerrada") is True
    assert notify.debe_avisar("no_ejecutada") is False
    assert notify.debe_avisar(None) is False
    assert notify.debe_avisar("WATCHING") is False
    assert notify.debe_avisar("TRIGGERED") is False


def test_enviar_vacio_no_toca_telegram(monkeypatch):
    llamadas = []
    monkeypatch.setattr(notify, "enviar_telegram", lambda *a, **k: llamadas.append((a, k)))
    notify.enviar("")
    assert llamadas == []


def test_enviar_usa_html_y_no_silencia_un_trade(monkeypatch):
    llamadas = []

    def _fake(texto, parse_mode=None, disable_notification=False):
        llamadas.append({
            "texto": texto, "parse_mode": parse_mode,
            "silent": disable_notification,
        })

    monkeypatch.setattr(notify, "enviar_telegram", _fake)
    notify.enviar("🧪 [PAPER] <b>LLENADA</b>")
    assert len(llamadas) == 1
    assert llamadas[0]["parse_mode"] == "HTML"
    assert llamadas[0]["silent"] is False


def test_sender_compartido_acepta_html_y_silent(monkeypatch):
    """El helper de hunter no cambia su default (texto plano); sí acepta
    parse_mode para el paper trader. Sin red: se mockea requests.post."""
    posts = []

    class _Resp:
        pass

    monkeypatch.setenv("MOMENTUM_TELEGRAM_BOT_TOKEN", "token-falso")
    monkeypatch.setenv("MOMENTUM_TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(
        "momentum_hunter.run.requests.post",
        lambda url, json, timeout: posts.append(json) or _Resp(),
    )
    from momentum_hunter.run import enviar_telegram
    enviar_telegram("hola", parse_mode="HTML", disable_notification=True)
    assert posts == [{
        "chat_id": "1",
        "text": "hola",
        "parse_mode": "HTML",
        "disable_notification": True,
    }]


def test_sender_sin_flags_sigue_siendo_texto_plano(monkeypatch):
    posts = []
    monkeypatch.setenv("MOMENTUM_TELEGRAM_BOT_TOKEN", "token-falso")
    monkeypatch.setenv("MOMENTUM_TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(
        "momentum_hunter.run.requests.post",
        lambda url, json, timeout: posts.append(json),
    )
    from momentum_hunter.run import enviar_telegram
    enviar_telegram("alerta hunter")
    assert posts == [{"chat_id": "1", "text": "alerta hunter"}]


def test_avisar_falla_solo_manda_el_tipo(monkeypatch):
    enviados = []
    monkeypatch.setattr("momentum_paper_trader.notify.enviar", enviados.append)
    _avisar_falla(RuntimeError("https://user:pass@alpaca.markets/v2"))
    assert len(enviados) == 1
    assert "ERROR" in enviados[0]
    assert "RuntimeError" in enviados[0]
    assert "user:pass" not in enviados[0]
    assert "alpaca.markets" not in enviados[0]
