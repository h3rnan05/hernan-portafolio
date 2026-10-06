"""Fixtures de suite: calendario de sesión + estado fuera del repo.

1) Calendario: sin archivo, el código se niega a abrir entradas. Casi
   todas las pruebas históricas asumen un día hábil normal (9:30–16:00
   America/New_York). Esta fixture se lo da, una vez por sesión de
   pytest. Las pruebas del calendario real apuntan
   `MOMENTUM_CALENDARIO_PATH` a otro archivo.

2) Estado: `RutaEstado` resuelve `MOMENTUM_ESTADO_DIR` en cada uso. Sin
   esto, un test que toca el default copiaría la telemetría versionada
   o intentaría crear `/var/lib/momentum/estado`.

3) Telegram (2026-10-06): las pruebas históricas verifican QUÉ arma y
   manda cada módulo, con el comportamiento anterior al filtro
   TELEGRAM_SOLO_ENTRADAS. Corren con el filtro APAGADO (=0, que es
   justamente "flag=0 restaura lo de antes") y con el archivo de
   interruptor apuntando a un tmp que no existe, para que un
   `/etc/momentum/telegram_solo_entradas` del host no se cuele. El
   default de producción (activo) lo prueban
   `momentum_paper_trader/tests/test_telegram_solo_entradas.py`.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest


def _documento_normal(desde: date, hasta: date) -> dict:
    dias = {}
    cursor = desde
    un_dia = timedelta(days=1)
    while cursor <= hasta:
        if cursor.weekday() < 5:
            dias[cursor.isoformat()] = {"open": "09:30", "close": "16:00"}
        cursor += un_dia
    # Las pruebas intradía históricas fabrican velas el 25 y el 26 de
    # julio de 2026 (sábado y domingo) y las tratan como sesión de
    # verano. Esos dos días se anotan a mano. El resto de los fines de
    # semana queda fuera: si se rellenaran, las pruebas de "cerrado el
    # sábado" dejarían de ver un día conocido sin fila.
    for excepcional in (date(2026, 7, 25), date(2026, 7, 26)):
        dias[excepcional.isoformat()] = {"open": "09:30", "close": "16:00"}
    return {
        "fetched_at": "2026-01-01T12:00:00+00:00",
        "rango": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
        "clock": {"is_open": False},
        "dias": dias,
    }


@pytest.fixture(scope="session")
def _archivo_calendario_normal(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("calendario") / "normal.json"
    path.write_text(
        json.dumps(_documento_normal(date(2024, 1, 1), date(2028, 12, 31))),
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _calendario_normal_en_el_entorno(monkeypatch, _archivo_calendario_normal):
    monkeypatch.setenv("MOMENTUM_CALENDARIO_PATH", str(_archivo_calendario_normal))


@pytest.fixture(autouse=True)
def _estado_fuera_del_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMENTUM_ESTADO_DIR", str(tmp_path / "estado"))
    monkeypatch.setenv("MOMENTUM_ESTADO_MIGRAR", "0")


@pytest.fixture(autouse=True)
def _telegram_comportamiento_historico(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_SOLO_ENTRADAS", "0")
    monkeypatch.setenv("MOMENTUM_TELEGRAM_FLAG_FILE", str(tmp_path / "sin_interruptor_telegram"))
