"""Control de rutas y reglas de protección. Toda E/S de archivos pasa por Guard."""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path, PurePosixPath

from .config import Config


class AutopilotError(Exception):
    """Error esperado: se muestra tal cual a Claude como error de la herramienta."""


class SecurityError(AutopilotError):
    pass


_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}

WRITE_EXTENSIONS = {".java", ".xml", ".properties", ".form", ".sql", ".txt", ".md", ".json",
                    ".gradle", ".kts", ".fxml", ".css", ".html", ".csv", ".yml", ".yaml",
                    ".mf", ".gitignore", ".iml", ".policy"}
READ_DENY_NAMES = re.compile(r"(?i)^(\.env.*|id_rsa.*|id_ed25519.*|.*\.(pem|key|p12|pfx|kdbx|jks|keystore)|"
                             r"credentials.*|.*\.sqlite-journal)$")
# Directorios de salida/IDE donde nunca se escribe (primer nivel del proyecto).
DENY_WRITE_TOP = {"target", "build", "dist", "out", ".autopilot", "nbproject/private", ".idea"}
DELETE_DENY_NAMES = {"pom.xml", "build.xml", "build.gradle", "build.gradle.kts",
                     "settings.gradle", "settings.gradle.kts", "nbactions.xml"}


class Guard:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = cfg.root

    # ---------- resolución de rutas ----------
    def _inside(self, p: Path) -> bool:
        a, r = os.path.normcase(str(p)), os.path.normcase(str(self.root))
        return a == r or a.startswith(r.rstrip(os.sep) + os.sep)

    def resolve(self, path: str) -> Path:
        """Devuelve la ruta real (sin enlaces/uniones) garantizando que queda dentro de la raíz."""
        if not isinstance(path, str) or not path.strip():
            raise SecurityError("Ruta vacía.")
        if "\x00" in path:
            raise SecurityError("Ruta con caracteres no válidos.")
        raw = path.strip()
        if raw.startswith(("\\\\", "//")):
            raise SecurityError("Rutas UNC/de dispositivo no permitidas.")
        p = Path(raw)
        body = raw[2:] if re.match(r"^[A-Za-z]:", raw) else raw  # quita unidad de rutas absolutas
        if ":" in body:
            raise SecurityError("Rutas con ':' (p. ej. flujos NTFS alternativos) no permitidas.")
        for comp in re.split(r"[\\/]+", body):
            if comp in ("", ".", ".."):
                continue
            if comp.endswith((".", " ")):
                raise SecurityError(f"Componente de ruta no válido en Windows: {comp!r}")
            if comp.split(".")[0].lower() in _RESERVED:
                raise SecurityError(f"Nombre reservado de Windows: {comp!r}")
        cand = p if p.is_absolute() else self.root / p
        real = Path(os.path.realpath(cand))
        if not self._inside(real):
            raise SecurityError(f"Ruta fuera de la carpeta autorizada: {path!r}")
        return real

    def project_dir(self, project: str) -> Path:
        if (not isinstance(project, str) or not project or re.search(r"[\\/:\x00]", project)
                or project in (".", "..") or project.startswith(".")):
            raise SecurityError(f"Nombre de proyecto no válido: {project!r}")
        d = self.resolve(project)
        if d.parent != self.root and os.path.normcase(str(d.parent)) != os.path.normcase(str(self.root)):
            raise SecurityError("El proyecto debe ser una carpeta directamente dentro de la raíz.")
        if not d.is_dir():
            raise AutopilotError(f"El proyecto '{project}' no existe en la raíz autorizada.")
        return d

    def file_path(self, project: str, rel: str) -> tuple[Path, Path, str]:
        """(carpeta_proyecto, ruta_real_archivo, ruta_relativa_posix). Impide salir del proyecto."""
        pdir = self.project_dir(project)
        if not isinstance(rel, str) or not rel.strip():
            raise SecurityError("Ruta de archivo vacía.")
        if re.match(r"^([A-Za-z]:|[\\/])", rel.strip()):
            # Se admite ruta absoluta solo si cae dentro del proyecto.
            real = self.resolve(rel)
        else:
            real = self.resolve(f"{project}/{rel}")
        try:
            relp = real.relative_to(pdir)
        except ValueError:
            if os.path.normcase(str(real)).startswith(os.path.normcase(str(pdir)) + os.sep):
                relp = Path(os.path.relpath(real, pdir))
            else:
                raise SecurityError(f"La ruta sale del proyecto '{project}': {rel!r}") from None
        return pdir, real, PurePosixPath(*relp.parts).as_posix() if relp.parts else "."

    # ---------- reglas de lectura / escritura ----------
    def check_readable(self, project: str, rel: str) -> None:
        name = PurePosixPath(rel).name
        low = rel.lower()
        if READ_DENY_NAMES.match(name):
            raise SecurityError(f"Lectura denegada (archivo potencialmente sensible): {name}")
        if low.startswith("nbproject/private") or "/.git/" in "/" + low or low.startswith(".git/"):
            raise SecurityError(f"Lectura denegada (carpeta privada/de control de versiones): {rel}")

    def check_writable(self, project: str, rel: str, op: str) -> None:
        low = rel.lower()
        if project in self.cfg.read_only_projects:
            raise SecurityError(f"El proyecto '{project}' está marcado como solo lectura.")
        if rel in (".", ""):
            raise SecurityError("No se puede modificar la raíz del proyecto.")
        if ".git" in low.split("/"):
            raise SecurityError("No se permite escribir dentro de .git.")
        for top in DENY_WRITE_TOP:
            if low == top or low.startswith(top + "/"):
                raise SecurityError(f"Carpeta protegida (salida de compilación/privada): {top}")
        for pat in self.cfg.protected_globs:
            if fnmatch.fnmatch(f"{project}/{rel}".lower(), pat.lower()):
                raise SecurityError(f"Archivo protegido por configuración ({pat}): {project}/{rel}")
        ext = PurePosixPath(rel).suffix.lower()
        name = PurePosixPath(rel).name.lower()
        if ext not in WRITE_EXTENSIONS and name not in {".gitignore"}:
            raise SecurityError(f"Extensión no permitida para escritura: {ext or name!r}")
        if ext == ".form" and not self.cfg.allow_form_edit:
            raise SecurityError(
                "Los .form del diseñador de NetBeans son de solo lectura (allow_form_edit=false). "
                "Edítalos con el diseñador, o crea la interfaz Swing por código.")
        if op == "delete":
            if name in DELETE_DENY_NAMES or low.startswith("nbproject/") or ext == ".jar":
                raise SecurityError(f"No se permite eliminar archivos de configuración/bibliotecas: {rel}")
