"""Servidor MCP (stdio) de NetBeans Autopilot.

Importante: en transporte stdio NUNCA se escribe en stdout salvo el protocolo; los logs van a stderr/archivo.
"""
import functools
import logging
import sys
from typing import Any, Optional

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, edits, fileio, projects, search
from .build import Builder
from .config import Config, load_config
from .security import AutopilotError, Guard

log = logging.getLogger("netbeans_autopilot")

INSTRUCTIONS = """\
Herramientas para trabajar sobre proyectos Java de Apache NetBeans dentro de una carpeta raíz autorizada.
Flujo recomendado: list_projects -> inspect_project -> (read_project_file / search_project_code) ->
apply_project_changes o edit_project_file -> validate_project_changes -> compile_project ->
run_project / run_project_tests -> get_build_errors / inspect_runtime_logs -> get_change_summary.
Todas las rutas son relativas al proyecto. No se pueden modificar carpetas de salida (target, build, dist),
nbproject/private, .git ni el código generado por el diseñador de NetBeans (GEN-BEGIN/GEN-END).
Los resultados de compilación y ejecución son salida real de los comandos; nunca afirmes que algo funciona
si la herramienta no lo ha comprobado.
"""


def _setup_logging(cfg: Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    fh = logging.FileHandler(cfg.data_dir / "server.log", encoding="utf-8")
    for h in (sh, fh):
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)
    log.propagate = False


def create_server(cfg: Optional[Config] = None) -> FastMCP:
    cfg = cfg or load_config()
    _setup_logging(cfg)
    guard = Guard(cfg)
    store = fileio.ChangeStore(cfg.data_dir)
    editor = edits.Editor(guard, store)
    builder = Builder(cfg, guard)
    mcp = FastMCP("netbeans-autopilot", instructions=INSTRUCTIONS)

    def tool(read_only: bool, destructive: bool = False):
        ann = ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive if not read_only else False,
                              idempotentHint=read_only, openWorldHint=False)

        def deco(fn):
            @functools.wraps(fn)
            async def wrapper(*a, **kw):
                log.info("tool %s %s", fn.__name__, {k: (v if len(str(v)) < 80 else str(v)[:77] + "...")
                                                      for k, v in kw.items() if k != "content"})
                try:
                    return await anyio.to_thread.run_sync(functools.partial(fn, *a, **kw))
                except AutopilotError as e:
                    log.warning("tool %s rechazada: %s", fn.__name__, e)
                    raise ToolError(str(e)) from None
                except Exception as e:  # noqa: BLE001
                    log.exception("fallo interno en %s", fn.__name__)
                    raise ToolError(f"Error interno en {fn.__name__}: {type(e).__name__}: {e}") from None

            return mcp.tool(annotations=ann)(wrapper)

        return deco

    # ---------------------------------------------------------------- inspección
    @tool(read_only=True)
    def get_environment() -> dict[str, Any]:
        """Muestra la raíz autorizada y las herramientas detectadas (JDK, Maven, Ant, Gradle)."""
        return {"version": __version__, "root": str(cfg.root), "java_home": str(cfg.java_home or ""),
                "java_feature_version": cfg.java_feature(), "maven": str(cfg.maven_cmd or ""),
                "ant": str(cfg.ant_cmd or ""), "gradle": str(cfg.gradle_cmd or "(no instalado)"),
                "read_only_projects": cfg.read_only_projects, "protected_globs": cfg.protected_globs,
                "allow_network": cfg.allow_network, "session": fileio.SESSION_ID}

    @tool(read_only=True)
    def list_projects() -> dict[str, Any]:
        """Enumera los proyectos de la carpeta autorizada y su sistema de construcción (Maven, Ant, Gradle...)."""
        return projects.list_projects(cfg)

    @tool(read_only=True)
    def inspect_project(project: str) -> dict[str, Any]:
        """Analiza un proyecto: estructura, paquetes y clases, puntos de entrada, formularios Swing/.form,
        dependencias, hints de base de datos, versión de Java requerida y avisos."""
        return projects.inspect_project(cfg, guard, project)

    @tool(read_only=True)
    def read_project_file(project: str, path: str, start_line: int = 1, max_lines: int = 400) -> dict[str, Any]:
        """Lee un archivo de texto del proyecto (ruta relativa al proyecto). Devuelve contenido, sha256 y
        rango de líneas. Los valores que parecen contraseñas/tokens se ocultan."""
        return editor.read(project, path, start_line, max_lines)

    @tool(read_only=True)
    def search_project_code(project: str, query: str, mode: str = "text", glob: Optional[str] = None,
                            max_results: int = 50, case_sensitive: bool = False) -> dict[str, Any]:
        """Busca en el proyecto. mode: text (literal), regex, symbol (definiciones de clase/método) o
        references (usos de un identificador). glob opcional, p. ej. 'src/**/*.java'."""
        return search.search(guard, project, query, mode, glob, max_results, case_sensitive)

    @tool(read_only=True)
    def get_project_status(project: str) -> dict[str, Any]:
        """Estado del proyecto: cambios en Git (si existe), archivos modificados fuera de estas herramientas
        tras el último cambio registrado, y resultado de la última compilación/ejecución/pruebas."""
        return search.status(guard, store, builder, project)

    # ---------------------------------------------------------------- edición
    @tool(read_only=False)
    def create_project(name: str, java_release: Optional[int] = None) -> dict[str, Any]:
        """Crea un proyecto Maven mínimo (pom.xml + clase Main) dentro de la raíz, abrible en NetBeans.
        No sobrescribe proyectos existentes."""
        return search.create_project(guard, store, name, java_release)

    @tool(read_only=False)
    def create_project_file(project: str, path: str, content: str, overwrite: bool = False) -> dict[str, Any]:
        """Crea un archivo nuevo (UTF-8). Falla si ya existe salvo overwrite=true. Hace copia de seguridad
        y registra el cambio."""
        return editor.apply(project, [{"op": "create", "path": path, "content": content, "overwrite": overwrite}])

    @tool(read_only=False)
    def edit_project_file(project: str, path: str, old_string: str, new_string: str, replace_all: bool = False,
                          expected_sha256: Optional[str] = None) -> dict[str, Any]:
        """Sustituye texto exacto en un archivo existente. old_string debe ser único (o usa replace_all).
        Con expected_sha256 (de read_project_file) se detectan cambios hechos entre tanto en NetBeans.
        Rechaza cualquier cambio sobre código generado por el diseñador."""
        return editor.apply(project, [{"op": "edit", "path": path, "old_string": old_string,
                                       "new_string": new_string, "replace_all": replace_all,
                                       "expected_sha256": expected_sha256}])

    @tool(read_only=False, destructive=True)
    def apply_project_changes(project: str, changes: list[dict[str, Any]], note: str = "") -> dict[str, Any]:
        """Aplica varios cambios de forma atómica (todo o nada, con reversión automática si algo falla).
        Cada cambio: {"op":"create","path","content","overwrite"?} | {"op":"edit","path","old_string",
        "new_string","replace_all"?,"expected_sha256"?} | {"op":"delete","path","confirm":true}."""
        return editor.apply(project, changes, note)

    @tool(read_only=True)
    def validate_project_changes(project: str, paths: Optional[list[str]] = None) -> dict[str, Any]:
        """Valida los archivos modificados en esta sesión (o los indicados): dentro del ámbito autorizado,
        XML/JSON bien formados, llaves balanceadas, paquete = carpeta, clases duplicadas y que no se haya
        tocado código del diseñador. No sustituye a compile_project."""
        return editor.validate(project, paths)

    @tool(read_only=False, destructive=True)
    def revert_project_changes(project: str, batch: Optional[str] = None, change_id: Optional[str] = None,
                               force: bool = False) -> dict[str, Any]:
        """Revierte un lote (batch) o un cambio concreto restaurando la copia de seguridad. Se niega si el
        archivo cambió después, salvo force=true."""
        return editor.revert(project, batch, change_id, force)

    @tool(read_only=True)
    def get_change_summary(project: Optional[str] = None, scope: str = "session") -> dict[str, Any]:
        """Archivos creados, modificados y eliminados por las herramientas. scope: session (por defecto) o all."""
        return editor.summary(project, scope)

    # ---------------------------------------------------------------- construcción y ejecución
    @tool(read_only=True)
    def detect_build_system(project: str) -> dict[str, Any]:
        """Detecta Maven/Gradle/Ant/Java simple, si es proyecto NetBeans y si hay herramienta disponible."""
        return builder.detect(project)

    @tool(read_only=False)
    def compile_project(project: str) -> dict[str, Any]:
        """Compila con el sistema del proyecto (Maven test-compile, Ant compile, Gradle o javac) y devuelve
        el resultado real con errores estructurados."""
        return builder.compile(project)

    @tool(read_only=False)
    def run_project(project: str, main_class: Optional[str] = None, args: Optional[list[str]] = None,
                    stdin_text: Optional[str] = None, timeout_seconds: int = 30,
                    headless: bool = False) -> dict[str, Any]:
        """Compila si hace falta y ejecuta la clase principal con límite de tiempo. stdin_text se envía como
        entrada estándar. Las apps gráficas se detienen al agotar el tiempo (timed_out=true)."""
        return builder.run(project, main_class, args, stdin_text, timeout_seconds, headless)

    @tool(read_only=False)
    def run_project_tests(project: str) -> dict[str, Any]:
        """Ejecuta las pruebas (Maven test / Ant test / clases *Test en proyectos simples) y resume resultados."""
        return builder.tests(project)

    @tool(read_only=False)
    def get_build_errors(project: str, rerun: bool = False) -> dict[str, Any]:
        """Errores de compilación organizados por archivo y línea (de la última compilación, o recompilando
        con rerun=true)."""
        return builder.errors(project, rerun)

    @tool(read_only=True)
    def inspect_runtime_logs(project: str, source: str = "last_run", path: Optional[str] = None,
                             tail_lines: int = 60) -> dict[str, Any]:
        """Analiza excepciones y trazas de la última ejecución (source=last_run|last_build|last_tests) o de un
        archivo de registro del proyecto (source=file, path=...). Devuelve causa, línea de origen y pistas."""
        return builder.logs(project, source, path, tail_lines)

    return mcp
