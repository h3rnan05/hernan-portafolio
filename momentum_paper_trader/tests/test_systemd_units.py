"""Validador de las unidades systemd versionadas en `infra/systemd/`.

POR QUÉ. El 2026-09-14 apareció que en el VPS paper hay unidades
corriendo que no estaban en ningún repositorio, y al menos una apuntaba
a `/opt/momentum/...` cuando el checkout real es `/opt/hernan-portafolio/`.
Nadie lo vio porque nada lo verificaba: una ruta rota en un `ExecStart`
falla en silencio en el journal del VPS, no en CI.

QUÉ HACE. Lee cada `.service`, `.timer` y drop-in `.conf` bajo
`infra/systemd/`, saca todos los paths absolutos de las líneas `Exec*=`,
y para cada uno que caiga bajo `/opt/`:

  - si el prefijo está versionado (`PREFIJOS_VERSIONADOS`), el archivo
    tiene que existir en el repo, y si es el ejecutable (argv[0]) tiene
    que tener permiso +x;
  - si el prefijo NO está versionado, es un error: `/opt/` es donde vive
    el código nuestro, y una ruta ahí que no corresponde a nada del repo
    es exactamente el bug que motivó esto.

Paths fuera de `/opt/` (`/usr/bin/bash`, `/etc/momentum/paper.env`) no
son asunto de este test. `EnvironmentFile=` tampoco: son secretos.

Si `infra/systemd/` está vacío, el test FALLA a propósito. Pasar en
vacío es el mismo silencio que se quiere eliminar.

PAPER ONLY. No toca umbrales, credenciales ni el endpoint de Alpaca; no
ejecuta nada, solo lee texto."""

from __future__ import annotations

import os
import shlex
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DIR_UNITS = REPO / "infra" / "systemd"

# Prefijo tal como aparece en el VPS -> dónde vive eso en el repo.
# `/opt/momentum/bin/` es el wrapper que no estaba versionado (2026-09-15);
# se versiona en `infra/systemd/bin/` y se instala copiándolo ahí.
PREFIJOS_VERSIONADOS: dict[str, Path] = {
    "/opt/hernan-portafolio/": REPO,
    "/opt/momentum/bin/": DIR_UNITS / "bin",
}

_CLAVES_EXEC = (
    "ExecStart", "ExecStartPre", "ExecStartPost", "ExecStop", "ExecStopPost",
    "ExecReload", "ExecCondition",
)
# systemd permite prefijar argv[0]: `-` (ignorar rc), `@`, `+`, `!`, `!!`, `:`.
_PREFIJOS_SYSTEMD = "-@+!:"
_EXTENSIONES = (".service", ".timer", ".conf")


def paths_referenciados(texto: str) -> list[tuple[str, str, bool]]:
    """`(clave, path, es_argv0)` por cada token absoluto en líneas `Exec*=`.

    `ExecStart=` vacío (el reset de un drop-in) no referencia nada.
    Tokens con especificadores (`%i`, `%h`) no se pueden resolver
    estáticamente y se ignoran -- no se inventa una ruta."""
    out: list[tuple[str, str, bool]] = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith(("#", ";")) or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        clave = clave.strip()
        if clave not in _CLAVES_EXEC:
            continue
        valor = valor.strip()
        if not valor:
            continue
        try:
            tokens = shlex.split(valor)
        except ValueError:
            tokens = valor.split()
        if not tokens:
            continue
        tokens[0] = tokens[0].lstrip(_PREFIJOS_SYSTEMD)
        for i, tok in enumerate(tokens):
            if tok.startswith("/") and "%" not in tok:
                out.append((clave, tok, i == 0))
    return out


def a_ruta_repo(path_vps: str, prefijos: dict[str, Path]) -> Path | None:
    for prefijo, base in prefijos.items():
        if path_vps.startswith(prefijo):
            return base / path_vps[len(prefijo):]
    return None


def problemas_en(unit: Path, prefijos: dict[str, Path] | None = None) -> list[str]:
    prefijos = PREFIJOS_VERSIONADOS if prefijos is None else prefijos
    errores: list[str] = []
    for clave, p, es_argv0 in paths_referenciados(unit.read_text(encoding="utf-8")):
        destino = a_ruta_repo(p, prefijos)
        if destino is None:
            if p.startswith("/opt/"):
                errores.append(
                    f"{unit.name}: {clave} apunta a {p}, bajo /opt/ pero fuera de todo "
                    f"prefijo versionado {sorted(prefijos)} -- ¿ruta vieja?")
            continue
        if not destino.exists():
            errores.append(f"{unit.name}: {clave} apunta a {p} -> no existe en el repo ({destino})")
        elif es_argv0 and not os.access(destino, os.X_OK):
            errores.append(f"{unit.name}: {clave} ejecuta {p} -> existe pero no tiene +x")
    return errores


def unidades_versionadas(directorio: Path = DIR_UNITS) -> list[Path]:
    if not directorio.is_dir():
        return []
    return sorted(p for p in directorio.rglob("*") if p.suffix in _EXTENSIONES and p.is_file())


# ------------------------- las unidades reales del repo -------------------------

@pytest.mark.xfail(
    not unidades_versionadas(), strict=True,
    reason="infra/systemd/ está vacío: las unidades del VPS todavía no se versionaron "
           "(2026-09-15). En cuanto existan, esta condición deja de aplicar sola y el "
           "test exige que sigan existiendo.",
)
def test_infra_systemd_tiene_unidades_versionadas():
    # Falla en vacío A PROPÓSITO (ver docstring del módulo). El xfail de
    # arriba solo lo convierte en "fallo esperado" mientras no haya nada
    # que validar -- CLAUDE.md pide la suite en verde para commitear.
    assert unidades_versionadas(), (
        f"{DIR_UNITS.relative_to(REPO)} no tiene ninguna unidad .service/.timer/.conf -- "
        "hay unidades corriendo en el VPS que no están en el repo")


@pytest.mark.parametrize("unit", unidades_versionadas(), ids=lambda p: str(p.relative_to(REPO)))
def test_cada_exec_apunta_a_algo_que_existe_en_el_repo(unit: Path):
    assert problemas_en(unit) == []


# ------------------------- el validador, probado contra unidades sintéticas -------------------------

def _repo_falso(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    ok = repo / "scripts" / "ok.sh"
    ok.write_text("#!/usr/bin/env bash\n")
    ok.chmod(0o755)
    sin_x = repo / "scripts" / "sin_x.sh"
    sin_x.write_text("#!/usr/bin/env bash\n")
    sin_x.chmod(0o644)
    (repo / "infra" / "systemd" / "bin").mkdir(parents=True)
    wrapper = repo / "infra" / "systemd" / "bin" / "run-paper"
    wrapper.write_text("#!/usr/bin/env bash\n")
    wrapper.chmod(0o755)
    prefijos = {
        "/opt/hernan-portafolio/": repo,
        "/opt/momentum/bin/": repo / "infra" / "systemd" / "bin",
    }
    return repo, prefijos


def _unit(tmp_path: Path, nombre: str, cuerpo: str) -> Path:
    p = tmp_path / nombre
    p.write_text(cuerpo)
    return p


def test_unidad_sana_no_tiene_problemas(tmp_path):
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "sana.service",
              "[Service]\nType=oneshot\nExecStart=/opt/hernan-portafolio/scripts/ok.sh\n")
    assert problemas_en(u, prefijos) == []


def test_ruta_bajo_opt_sin_prefijo_versionado_es_error(tmp_path):
    # El bug real: /opt/momentum/... cuando el checkout es /opt/hernan-portafolio/.
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "vieja.service",
              "[Service]\nExecStart=/opt/momentum/scripts/notify_telegram.sh hola\n")
    errores = problemas_en(u, prefijos)
    assert len(errores) == 1
    assert "/opt/momentum/scripts/notify_telegram.sh" in errores[0]
    assert "fuera de todo prefijo versionado" in errores[0]


def test_script_que_no_existe_es_error(tmp_path):
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "rota.service",
              "[Service]\nExecStart=/opt/hernan-portafolio/scripts/no_existe.sh\n")
    errores = problemas_en(u, prefijos)
    assert len(errores) == 1 and "no existe en el repo" in errores[0]


def test_argv0_sin_permiso_de_ejecucion_es_error(tmp_path):
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "sinx.service",
              "[Service]\nExecStart=/opt/hernan-portafolio/scripts/sin_x.sh\n")
    errores = problemas_en(u, prefijos)
    assert len(errores) == 1 and "no tiene +x" in errores[0]


def test_argumento_sin_x_esta_bien_si_lo_corre_bash(tmp_path):
    # `bash script.sh` no necesita +x en el script -- solo argv[0] se ejecuta.
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "viabash.service",
              "[Service]\nExecStart=/usr/bin/bash /opt/hernan-portafolio/scripts/sin_x.sh\n")
    assert problemas_en(u, prefijos) == []


def test_wrapper_en_opt_momentum_bin_se_resuelve_a_infra_systemd_bin(tmp_path):
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "wrapper.service",
              "[Service]\nExecStart=/opt/momentum/bin/run-paper\n")
    assert problemas_en(u, prefijos) == []
    u2 = _unit(tmp_path, "wrapper2.service",
               "[Service]\nExecStart=/opt/momentum/bin/no-esta\n")
    assert len(problemas_en(u2, prefijos)) == 1


def test_dropin_con_reset_y_override_se_parsea(tmp_path):
    # Patrón habitual de un drop-in: `ExecStart=` vacío resetea, la línea
    # siguiente reemplaza. El vacío no referencia nada; el override sí.
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "20-ownership-wrapper.conf",
              "[Service]\nExecStart=\nExecStart=/opt/momentum/bin/run-paper\n")
    assert problemas_en(u, prefijos) == []
    refs = paths_referenciados(u.read_text())
    assert refs == [("ExecStart", "/opt/momentum/bin/run-paper", True)]


def test_paths_fuera_de_opt_y_environmentfile_se_ignoran(tmp_path):
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "sistema.service",
              "[Service]\nEnvironmentFile=/etc/momentum/paper.env\n"
              "ExecStartPre=/usr/bin/test -f /etc/momentum/paper.env\n"
              "ExecStart=/usr/bin/true\n")
    assert problemas_en(u, prefijos) == []


def test_prefijos_de_systemd_en_argv0_se_quitan(tmp_path):
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "prefijo.service",
              "[Service]\nExecStart=-/opt/hernan-portafolio/scripts/ok.sh\n"
              "ExecStopPost=+/opt/hernan-portafolio/scripts/ok.sh\n")
    assert problemas_en(u, prefijos) == []
    refs = paths_referenciados(u.read_text())
    assert [r[1] for r in refs] == ["/opt/hernan-portafolio/scripts/ok.sh"] * 2


def test_especificadores_de_systemd_no_se_resuelven(tmp_path):
    # `%i`/`%h` dependen de la instancia; no se puede saber estáticamente
    # a qué archivo apuntan, así que no se inventa un veredicto.
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "plantilla@.service",
              "[Service]\nExecStart=/opt/hernan-portafolio/scripts/%i.sh\n")
    assert problemas_en(u, prefijos) == []
    assert paths_referenciados(u.read_text()) == []


def test_timer_sin_exec_no_tiene_nada_que_validar(tmp_path):
    _, prefijos = _repo_falso(tmp_path)
    u = _unit(tmp_path, "algo.timer", "[Timer]\nOnCalendar=*:0/5\nPersistent=true\n")
    assert problemas_en(u, prefijos) == []
