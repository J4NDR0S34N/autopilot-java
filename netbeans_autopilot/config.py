"""Configuración y descubrimiento de herramientas (JDK, Maven, Ant, Gradle).

Nada de rutas fijas: la raíz de proyectos sale de config.json o de variables de entorno.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""


class ConfigError(Exception):
    pass


@dataclass
class Config:
    root: Path
    data_dir: Path
    java_home: Path | None = None
    maven_cmd: Path | None = None
    ant_cmd: Path | None = None
    gradle_cmd: Path | None = None
    protected_globs: list[str] = field(default_factory=list)
    read_only_projects: list[str] = field(default_factory=list)
    allow_network: bool = True
    allow_form_edit: bool = False
    max_read_bytes: int = 1_000_000
    max_write_bytes: int = 1_000_000
    build_timeout: int = 300
    max_run_timeout: int = 120
    _java_feature: int | None = field(default=None, repr=False)

    @property
    def javac(self) -> Path | None:
        return self.java_home / "bin" / f"javac{EXE}" if self.java_home else None

    @property
    def java(self) -> Path | None:
        return self.java_home / "bin" / f"java{EXE}" if self.java_home else None

    def java_feature(self) -> int | None:
        """Versión principal del JDK (p. ej. 26), obtenida de `javac -version`."""
        if self._java_feature is None and self.javac and self.javac.exists():
            try:
                out = subprocess.run([str(self.javac), "-version"], capture_output=True,
                                     text=True, timeout=20)
                m = re.search(r"javac\s+(\d+)", out.stdout + out.stderr)
                if m:
                    self._java_feature = int(m.group(1))
            except (OSError, subprocess.SubprocessError):
                pass
        return self._java_feature


def _version_key(p: str):
    return [int(x) for x in re.findall(r"\d+", Path(p).name)] or [0]


def _find_java_home(configured: str | None) -> Path | None:
    cands: list[str] = []
    if configured:
        cands.append(configured)
    if os.environ.get("JAVA_HOME"):
        cands.append(os.environ["JAVA_HOME"])
    patterns = ((r"C:\Program Files\Java\jdk-*", r"C:\Program Files\Eclipse Adoptium\jdk-*",
                 r"C:\Program Files\Microsoft\jdk-*") if IS_WIN else
                ("/usr/lib/jvm/*", "/usr/local/lib/jvm/*", "/opt/homebrew/opt/openjdk*/libexec/openjdk.jdk/Contents/Home",
                 "/Library/Java/JavaVirtualMachines/*/Contents/Home"))
    # Si hay `javac` en el PATH se deduce su JAVA_HOME (cubre sdkman, asdf, instalaciones a mano...).
    javac = shutil.which("javac")
    if javac:
        cands.append(str(Path(os.path.realpath(javac)).parent.parent))
    for pattern in patterns:
        cands += sorted(glob.glob(pattern), key=_version_key, reverse=True)
    for c in cands:
        if (Path(c) / "bin" / f"javac{EXE}").exists():
            return Path(c)
    return None


def _find_tool(configured: str | None, which_names: list[str], patterns: list[str]) -> Path | None:
    if configured and Path(configured).exists():
        return Path(configured)
    for name in which_names:
        found = shutil.which(name)
        if found:
            return Path(found)
    for pattern in patterns:
        hits = sorted(glob.glob(pattern), reverse=True)
        if hits:
            return Path(hits[0])
    return None


def load_config() -> Config:
    cfg_path = Path(os.environ.get("NB_AUTOPILOT_CONFIG", REPO / "config.json"))
    raw: dict = {}
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8-sig"))   # tolera BOM de PowerShell
        except (OSError, ValueError) as e:
            raise ConfigError(f"No se pudo leer {cfg_path}: {e}") from e

    root_str = os.environ.get("NB_AUTOPILOT_ROOT") or raw.get("root")
    if not root_str:
        raise ConfigError(
            "Falta la carpeta raíz de proyectos. Defínela en config.json ('root') "
            "o en la variable de entorno NB_AUTOPILOT_ROOT.")
    root = Path(os.path.realpath(root_str))
    if not root.is_dir():
        raise ConfigError(f"La carpeta raíz no existe o no es un directorio: {root}")

    data_str = os.environ.get("NB_AUTOPILOT_DATA") or raw.get("data_dir") or str(REPO / "data")
    data_dir = Path(os.path.realpath(data_str))
    # La carpeta de datos no puede estar dentro de la raíz (evita auto-modificarse y ensuciar proyectos).
    a, r = os.path.normcase(str(data_dir)), os.path.normcase(str(root))
    if a == r or a.startswith(r.rstrip(os.sep) + os.sep):
        raise ConfigError("data_dir no puede estar dentro de la carpeta raíz de proyectos.")
    data_dir.mkdir(parents=True, exist_ok=True)

    nb = r"C:\Program Files*\Apache NetBeans*"
    return Config(
        root=root,
        data_dir=data_dir,
        java_home=_find_java_home(raw.get("java_home")),
        maven_cmd=_find_tool(raw.get("maven_cmd"), ["mvn.cmd", "mvn"], [nb + r"\java\maven\bin\mvn.cmd"]),
        ant_cmd=_find_tool(raw.get("ant_cmd"), ["ant.bat", "ant.cmd", "ant"],
                           [nb + r"\extide\ant\bin\ant.bat"]),
        gradle_cmd=_find_tool(raw.get("gradle_cmd"), ["gradle.bat", "gradle"], []),
        protected_globs=list(raw.get("protected_globs", [])),
        read_only_projects=list(raw.get("read_only_projects", [])),
        allow_network=bool(raw.get("allow_network", True)),
        allow_form_edit=bool(raw.get("allow_form_edit", False)),
        max_read_bytes=int(raw.get("max_read_bytes", 1_000_000)),
        max_write_bytes=int(raw.get("max_write_bytes", 1_000_000)),
        build_timeout=int(raw.get("build_timeout", 300)),
        max_run_timeout=int(raw.get("max_run_timeout", 120)),
    )
