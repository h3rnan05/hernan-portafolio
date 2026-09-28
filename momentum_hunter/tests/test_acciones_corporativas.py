"""Pruebas de la guardia de acciones corporativas: sin red (transporte
falso), sin credenciales reales y sin escribir fuera de `tmp_path`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from momentum_hunter import run as run_mod
from momentum_hunter import watchlist
from momentum_hunter.data import acciones_corporativas as ac
from momentum_hunter.data.alpaca_datos import ErrorDatosAlpaca
from momentum_hunter.models import Barras
from momentum_hunter.tests.test_run_watchlist import (
    AHORA,
    CFG,
    _candidato_diario,
    _candidato_intradia,
    _FakeProviderIntradia,
    _parchear_efectos_secundarios,
    _preparar_watchlist,
    _triggered_con_niveles,
)

HOY = date(2026, 9, 28)
# 15:00 UTC = 11:00 en Nueva York = 09:00 en Monterrey (UTC-6).
MEDIODIA = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


class _Transporte:
    """Hace de `AlpacaProvider` para `ClienteAcciones`: devuelve páginas
    fijas y guarda los parámetros pedidos."""

    def __init__(self, paginas=None, error=None):
        self.paginas = paginas if paginas is not None else [{}]
        self.error = error
        self.pedidos: list[tuple[str, dict]] = []

    def _paginas(self, ruta, params):
        self.pedidos.append((ruta, dict(params)))
        if self.error is not None:
            raise self.error
        return self.paginas


def _cliente(grupos=None, error=None):
    return ac.ClienteAcciones(_Transporte([{"corporate_actions": grupos or {}}], error=error))


@pytest.fixture(autouse=True)
def _enforce(monkeypatch):
    # Casi todas las pruebas miden el bloqueo real; las de observación
    # cambian el modo explícitamente.
    monkeypatch.setenv(ac.ENV_MODO, ac.MODO_ENFORCE)


# ------------------------------------------------------------------ parseo


def test_parsea_splits_con_su_factor():
    acciones, sin_simbolo = ac.parsear_pagina({"corporate_actions": {
        "forward_splits": [{"symbol": "NVDA", "new_rate": 10, "old_rate": 1, "ex_date": "2026-09-28"}],
        "reverse_splits": [{"symbol": "MULN", "new_rate": 1, "old_rate": 25, "ex_date": "2026-09-28"}],
        "stock_dividends": [{"symbol": "ABC", "rate": 0.05, "ex_date": "2026-09-28"}],
    }})
    por = {a.simbolos[0]: a for a in acciones}
    assert sin_simbolo == 0
    assert por["NVDA"].tipo == "forward_split" and por["NVDA"].factor == pytest.approx(10.0)
    assert por["MULN"].tipo == "reverse_split" and por["MULN"].factor == pytest.approx(0.04)
    assert por["ABC"].factor == pytest.approx(1.05)


def test_un_split_sin_una_de_las_tasas_no_inventa_factor():
    acciones, _ = ac.parsear_pagina({"corporate_actions": {
        "forward_splits": [{"symbol": "X", "new_rate": 2, "ex_date": "2026-09-28"}]}})
    assert acciones[0].factor is None   # None no es 1


def test_una_fusion_afecta_a_los_dos_simbolos():
    acciones, _ = ac.parsear_pagina({"corporate_actions": {
        "stock_mergers": [{"acquirer_symbol": "BIG", "acquiree_symbol": "SMOL",
                           "effective_date": "2026-09-28"}]}})
    assert acciones[0].simbolos == ("BIG", "SMOL")
    assert acciones[0].fecha_efectiva() == HOY


def test_registro_sin_simbolo_se_cuenta_no_se_atribuye():
    acciones, sin_simbolo = ac.parsear_pagina({"corporate_actions": {
        "cash_dividends": [{"rate": 0.2, "ex_date": "2026-09-28"}, "basura"]}})
    assert acciones == [] and sin_simbolo == 2


def test_cuerpo_sin_forma_es_error_no_cero_acciones():
    with pytest.raises(ErrorDatosAlpaca):
        ac.parsear_pagina({"corporate_actions": ["no", "es", "dict"]})
    with pytest.raises(ErrorDatosAlpaca):
        ac.parsear_pagina(["nada"])
    assert ac.parsear_pagina({}) == ([], 0)   # página vacía legítima


def test_el_dividendo_en_efectivo_se_fecha_por_ex_date_no_por_pago():
    a = ac.AccionCorporativa("cash_dividend", ("KO",), ex_date=date(2026, 9, 1),
                             effective_date=None, process_date=HOY)
    assert a.fecha_efectiva() == date(2026, 9, 1)


def test_normalizar_une_los_dos_estilos_de_clase():
    assert ac.normalizar("brk.b") == ac.normalizar("BRK-B") == "BRK-B"


def test_hoy_es_la_fecha_de_nueva_york():
    # 02:00 UTC del 29/9 = 22:00 del 28/9 en Nueva York.
    assert ac.hoy_ny(datetime(2026, 9, 29, 2, 0, tzinfo=UTC)) == HOY


# ----------------------------------------------------------------- guardia


def test_bloquea_el_simbolo_con_ex_date_hoy_y_no_a_los_demas():
    g = ac.consultar(["KO", "RKLB"], MEDIODIA, _cliente({
        "cash_dividends": [{"symbol": "KO", "rate": 0.51, "ex_date": "2026-09-28",
                            "process_date": "2026-10-15"}]}))
    assert g.motivo("KO") == "accion_corporativa_hoy:cash_dividend"
    assert g.motivo("RKLB") is None


def test_ex_date_de_otro_dia_no_bloquea():
    g = ac.consultar(["KO"], MEDIODIA, _cliente({
        "cash_dividends": [{"symbol": "KO", "rate": 0.51, "ex_date": "2026-09-01",
                            "process_date": "2026-09-28"}],
        "forward_splits": [{"symbol": "KO", "new_rate": 2, "old_rate": 1, "ex_date": "2026-09-29"}]}))
    assert g.motivo("KO") is None


def test_un_registro_sin_fecha_legible_bloquea_solo_ese_simbolo():
    g = ac.consultar(["KO", "RKLB"], MEDIODIA, _cliente({
        "reverse_splits": [{"symbol": "KO", "new_rate": 1, "old_rate": 10, "ex_date": "pronto"}]}))
    assert g.motivo("KO") == "accion_corporativa_sin_fecha_legible"
    assert g.motivo("RKLB") is None


def test_clase_con_punto_en_el_feed_bloquea_el_ticker_con_guion():
    transporte = _Transporte([{"corporate_actions": {
        "cash_dividends": [{"symbol": "BRK.B", "rate": 1, "ex_date": "2026-09-28"}]}}])
    g = ac.consultar(["BRK-B"], MEDIODIA, ac.ClienteAcciones(transporte))
    assert g.motivo("BRK-B") is not None
    ruta, params = transporte.pedidos[0]
    assert ruta == "/v1/corporate-actions"
    assert params["symbols"] == "BRK.B"
    # Rango ancho alrededor de hoy: la ex-date de hoy puede tener su
    # process_date semanas después.
    assert params["start"] == (HOY - timedelta(days=ac.DIAS_ATRAS_GUARDIA)).isoformat()
    assert params["end"] == (HOY + timedelta(days=ac.DIAS_ADELANTE_GUARDIA)).isoformat()


@pytest.mark.parametrize("error", [ErrorDatosAlpaca("sin_credenciales"), RuntimeError("x")])
def test_si_no_se_puede_consultar_bloquea_todo(error):
    g = ac.consultar(["RKLB"], MEDIODIA, _cliente(error=error))
    assert not g.disponible
    assert g.motivo("RKLB").startswith("acciones_corporativas_no_disponibles:")
    assert g.motivo("CUALQUIERA") is not None


def test_el_codigo_del_fallo_es_una_etiqueta_no_el_texto():
    g = ac.consultar(["RKLB"], MEDIODIA, _cliente(error=RuntimeError("https://k:secreto@host")))
    assert "secreto" not in g.motivo("RKLB")


def test_apagada_por_variable_no_bloquea_ni_pide_nada(monkeypatch):
    monkeypatch.setenv(ac.ENV_MODO, "off")
    transporte = _Transporte(error=ErrorDatosAlpaca("red"))
    g = ac.consultar(["KO"], MEDIODIA, ac.ClienteAcciones(transporte))
    assert g.motivo("KO") is None and transporte.pedidos == []


def test_la_guardia_del_dia_hereda_lo_que_dejo_la_historia():
    base = ac.Guardia(fecha=HOY, disponible=True, sin_verificar={"ZZZ": "reverse_split:ambigua"})
    g = ac.consultar(["ZZZ"], MEDIODIA, _cliente(), base=base)
    assert g.motivo("ZZZ") == "split_sin_verificar:reverse_split:ambigua"
    caida = ac.Guardia(fecha=HOY, disponible=False, codigo="historia:red")
    assert not ac.consultar(["ZZZ"], MEDIODIA, _cliente(), base=caida).disponible


# ------------------------------------------------ verificar y ajustar historia


def _epoch_diario(d: date) -> str:
    """Mismo sello que la vela diaria del feed: 00:00 ET (04:00 UTC)."""
    return str(int(datetime(d.year, d.month, d.day, 4, 0, tzinfo=UTC).timestamp()))


def _serie(ticker, cierres, aperturas=None):
    # Contrato real de `Barras.fechas`: epoch en texto, no ISO.
    n = len(cierres)
    fechas = [_epoch_diario(date(2026, 9, 1) + timedelta(days=i)) for i in range(n)]
    aperturas = aperturas or cierres
    return Barras(ticker, fechas, list(aperturas), list(cierres),
                  [c * 1.01 for c in cierres], [c * 0.99 for c in cierres], [1000.0] * n)


def _reverse(simbolo="MULN", ex="2026-09-04", viejo=10, nuevo=1):
    return {"reverse_splits": [{"symbol": simbolo, "new_rate": nuevo, "old_rate": viejo, "ex_date": ex}]}


def test_serie_ya_ajustada_no_se_toca():
    b = _serie("MULN", [5.0, 5.1, 5.0, 5.2, 5.3])
    out, g = ac.verificar_historia({"MULN": b}, MEDIODIA, _cliente(_reverse()))
    assert out["MULN"] is b
    assert g.motivo("MULN") is None and g.ajustados == {}


def test_serie_cruda_de_un_reverse_split_se_ajusta():
    # 1:10 sin ajustar: 0,50 -> 5,00 el día de la ex-date. Parece una
    # ruptura de +900 %; no lo es.
    b = _serie("MULN", [0.50, 0.51, 0.50, 5.0, 5.1])
    out, g = ac.verificar_historia({"MULN": b}, MEDIODIA, _cliente(_reverse()))
    ajustada = out["MULN"]
    assert ajustada.close[:3] == pytest.approx([5.0, 5.1, 5.0])
    assert ajustada.close[3:] == [5.0, 5.1]
    assert ajustada.volume[0] == pytest.approx(100.0)   # 1000 acciones viejas = 100 nuevas
    assert g.motivo("MULN") is None
    assert g.ajustados["MULN"].startswith("reverse_split")


def test_salto_que_no_se_parece_a_ninguna_hipotesis_bloquea_el_simbolo():
    # 2:1 con un salto de 1,45x: ni ajustada (1x) ni cruda (2x) con claridad.
    b = _serie("NVDA", [100.0, 101.0, 100.0, 69.0, 70.0])
    out, g = ac.verificar_historia({"NVDA": b}, MEDIODIA, _cliente({"forward_splits": [
        {"symbol": "NVDA", "new_rate": 2, "old_rate": 1, "ex_date": "2026-09-04"}]}))
    assert out["NVDA"] is b
    assert g.motivo("NVDA") == "split_sin_verificar:forward_split:ambigua"


def test_split_fuera_de_la_ventana_de_la_serie_no_bloquea():
    b = _serie("MULN", [5.0, 5.1, 5.0])
    out, g = ac.verificar_historia({"MULN": b}, MEDIODIA, _cliente(_reverse(ex="2025-01-10")))
    assert g.motivo("MULN") is None


def test_historia_caida_bloquea_todo():
    out, g = ac.verificar_historia({"X": _serie("X", [1.0, 1.0])}, MEDIODIA,
                                   _cliente(error=ErrorDatosAlpaca("http_500")))
    assert not g.disponible and g.motivo("X") == "acciones_corporativas_no_disponibles:historia:http_500"


def test_historia_pide_solo_cambios_de_escala_de_todo_el_mercado():
    transporte = _Transporte([{}])
    ac.verificar_historia({"X": _serie("X", [1.0, 1.0])}, MEDIODIA, ac.ClienteAcciones(transporte))
    _, params = transporte.pedidos[0]
    assert "symbols" not in params
    assert params["types"] == "forward_split,reverse_split,stock_dividend"
    assert params["start"] == "2026-09-01"


# ------------------------------------------------------ integración con run


def _bloquear(monkeypatch, bloqueados):
    def _guardia(tickers, ahora, base=None):
        return ac.Guardia(fecha=ac.hoy_ny(ahora), disponible=True,
                          hoy={t: ("cash_dividend",) for t in bloqueados})
    monkeypatch.setattr(run_mod, "_guardia_corporativa", _guardia)


def test_rechequeo_no_dispara_un_simbolo_con_accion_hoy(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    enviados, _, _ = _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia",
                        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))
    _bloquear(monkeypatch, {"RKLB"})

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_WATCHING   # sigue vigilada, no descartada
    assert enviados == []


def test_si_la_mejor_esta_bloqueada_compite_la_siguiente(monkeypatch, tmp_path):
    e_a = watchlist.desde_candidato_diario(_candidato_diario("MEJOR"), AHORA)
    e_b = watchlist.desde_candidato_diario(_candidato_diario("SEGUNDA"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e_a, e_b])
    _parchear_efectos_secundarios(monkeypatch)
    candidatos = {
        "MEJOR": _candidato_intradia("MEJOR", accionable=True, score=99.0),
        "SEGUNDA": _candidato_intradia("SEGUNDA", accionable=True, score=90.0),
    }
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia", lambda ticker, *a, **kw: candidatos[ticker])
    _bloquear(monkeypatch, {"MEJOR"})

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"MEJOR", "SEGUNDA"}), dry_run=False, ahora=AHORA)

    recargadas = {r.ticker: r for r in watchlist.cargar(path)}
    assert recargadas["MEJOR"].estado == watchlist.ESTADO_WATCHING
    assert recargadas["SEGUNDA"].estado == watchlist.ESTADO_TRIGGERED


def test_guardia_caida_no_deja_disparar_nada(monkeypatch, tmp_path):
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia",
                        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))
    monkeypatch.setattr(run_mod, "_guardia_corporativa",
                        lambda tickers, ahora, base=None: ac.Guardia(
                            fecha=ac.hoy_ny(ahora), disponible=False, codigo="red"))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_WATCHING


def test_una_triggered_con_accion_hoy_no_refresca_niveles(monkeypatch, tmp_path):
    # Sin refresco los niveles envejecen y el ejecutor los rechaza por
    # rancios: así la guardia llega hasta la orden.
    e = _triggered_con_niveles()
    ts_viejo = e.ultimos_niveles_ts
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia",
                        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))
    _bloquear(monkeypatch, {"RKLB"})

    run_mod.revisar_watchlist(
        CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA + timedelta(minutes=20))

    assert watchlist.cargar(path)[0].ultimos_niveles_ts == ts_viejo


def test_el_bloqueo_accionable_queda_en_el_registro_fuera_de_git(monkeypatch, tmp_path):
    ruta = tmp_path / "acciones.jsonl"
    monkeypatch.setenv(ac.ENV_LOG, str(ruta))
    g = ac.Guardia(fecha=HOY, disponible=True, hoy={"RKLB": ("forward_split",)})
    quedan = run_mod._sin_bloqueo_corporativo(
        [_candidato_intradia("RKLB", accionable=True), _candidato_intradia("OTRA", accionable=True)],
        g, MEDIODIA)
    assert [c.ticker for c in quedan] == ["OTRA"]
    lineas = ruta.read_text().splitlines()
    assert len(lineas) == 1 and '"bloqueo_disparo"' in lineas[0] and "forward_split" in lineas[0]


def test_fecha_de_barra_lee_el_epoch_de_los_dos_proveedores():
    # Feed: 04:00 UTC; Yahoo: 13:30 UTC. Los dos son el 4 de septiembre.
    assert ac.fecha_de_barra(_epoch_diario(date(2026, 9, 4))) == date(2026, 9, 4)
    yahoo = str(int(datetime(2026, 9, 4, 13, 30, tzinfo=UTC).timestamp()))
    assert ac.fecha_de_barra(yahoo) == date(2026, 9, 4)
    assert ac.fecha_de_barra("2026-09-04") == date(2026, 9, 4)
    assert ac.fecha_de_barra("mañana") is None


def test_historia_con_epoch_real_no_bloquea_por_fecha_ilegible():
    # Regresión: con fechas epoch la primera versión leía "fecha ilegible"
    # y, por fail-closed, bloqueaba TODOS los disparos.
    out, g = ac.verificar_historia({"X": _serie("X", [1.0, 1.0, 1.0])}, MEDIODIA, _cliente())
    assert g.disponible and g.motivo("X") is None


def test_una_serie_con_fecha_ilegible_y_split_en_la_ventana_no_se_ajusta_a_ciegas():
    b = _serie("MULN", [0.50, 0.51, 0.50, 5.0, 5.1])
    b.fechas[2] = "basura"
    out, g = ac.verificar_historia({"MULN": b}, MEDIODIA, _cliente(_reverse()))
    assert out["MULN"] is b
    assert g.motivo("MULN") == "split_sin_verificar:reverse_split:ambigua"


# ------------------------------------------------------------ modo observar


def test_el_modo_por_defecto_es_observar(monkeypatch):
    monkeypatch.delenv(ac.ENV_MODO, raising=False)
    assert ac.modo() == ac.MODO_OBSERVAR


def test_un_modo_desconocido_se_trata_como_enforce(monkeypatch):
    monkeypatch.setenv(ac.ENV_MODO, "enforse")
    assert ac.modo() == ac.MODO_ENFORCE


def test_observar_dice_el_motivo_pero_no_bloquea(monkeypatch):
    monkeypatch.setenv(ac.ENV_MODO, "observar")
    g = ac.consultar(["KO"], MEDIODIA, _cliente({
        "cash_dividends": [{"symbol": "KO", "rate": 0.51, "ex_date": "2026-09-28"}]}))
    assert g.observando
    assert g.motivo("KO") == "accion_corporativa_hoy:cash_dividend"
    assert g.bloquea("KO") is None


def test_observar_con_consulta_caida_registra_y_no_bloquea(monkeypatch, tmp_path):
    monkeypatch.setenv(ac.ENV_MODO, "observar")
    monkeypatch.setenv(ac.ENV_LOG, str(tmp_path / "ac.jsonl"))
    g = ac.consultar(["KO"], MEDIODIA, _cliente(error=ErrorDatosAlpaca("red")))
    assert g.bloquea("KO") is None and g.motivo("KO") is not None
    assert '"consulta_fallida"' in (tmp_path / "ac.jsonl").read_text()


def test_observar_no_ajusta_la_serie_pero_registra_que_la_ajustaria(monkeypatch, tmp_path):
    monkeypatch.setenv(ac.ENV_MODO, "observar")
    monkeypatch.setenv(ac.ENV_LOG, str(tmp_path / "ac.jsonl"))
    b = _serie("MULN", [0.50, 0.51, 0.50, 5.0, 5.1])
    out, g = ac.verificar_historia({"MULN": b}, MEDIODIA, _cliente(_reverse()))
    assert out["MULN"] is b
    assert "observacion_ajuste" in (tmp_path / "ac.jsonl").read_text()


def test_rechequeo_en_observar_dispara_igual_y_registra(monkeypatch, tmp_path):
    ruta = tmp_path / "ac.jsonl"
    monkeypatch.setenv(ac.ENV_LOG, str(ruta))
    e = watchlist.desde_candidato_diario(_candidato_diario("RKLB"), AHORA)
    path = _preparar_watchlist(monkeypatch, tmp_path, [e])
    _parchear_efectos_secundarios(monkeypatch)
    monkeypatch.setattr(run_mod, "_construir_candidato_intradia",
                        lambda ticker, *a, **kw: _candidato_intradia(ticker, accionable=True))
    monkeypatch.setattr(run_mod, "_guardia_corporativa", lambda tickers, ahora, base=None: ac.Guardia(
        fecha=ac.hoy_ny(ahora), disponible=True, observando=True, hoy={"RKLB": ("forward_split",)}))

    run_mod.revisar_watchlist(CFG, _FakeProviderIntradia({"RKLB"}), dry_run=False, ahora=AHORA)

    assert watchlist.cargar(path)[0].estado == watchlist.ESTADO_TRIGGERED
    assert '"observacion_disparo"' in ruta.read_text()


def test_resumen_de_sesion_cuenta_por_evento_y_dice_si_hubo_fallas(tmp_path):
    ruta = tmp_path / "ac.jsonl"
    filas = [
        {"ts": "2026-09-28T14:00:00+00:00", "evento": "observacion_disparo", "ticker": "KO", "motivo": "x"},
        {"ts": "2026-09-28T14:01:00+00:00", "evento": "observacion_disparo", "ticker": "KO", "motivo": "x"},
        {"ts": "2026-09-29T02:00:00+00:00", "evento": "consulta_fallida", "ticker": "*", "motivo": "red"},
    ]
    ruta.write_text("".join(__import__("json").dumps(f) + "\n" for f in filas))
    r = ac.resumen_sesion("2026-09-28", ruta)
    # 02:00 UTC del 29 = 22:00 del 28 en Nueva York: cuenta para el 28.
    assert r == {"observacion_disparo": {"KO": 2}, "consulta_fallida": {"*": 1}}
    texto = ac.formatear_resumen(r, "2026-09-28")
    assert "KO×2" in texto and "consultas fallidas: 1" in texto
    assert "Sin registros" in ac.formatear_resumen({}, "2026-09-28")
