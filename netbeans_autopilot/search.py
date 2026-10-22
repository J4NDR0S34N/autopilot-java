"""Búsqueda de código, estado del proyecto y creación de proyectos nuevos."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import fileio, java_scan, projects
from .build import Builder
from .security import AutopilotError, Guard

TEXT_EXT = (".java", ".xml", ".properties", ".sql", ".txt", ".md", ".json", ".fxml", ".gradle", ".kts",
            ".css", ".html", ".csv", ".yml", ".yaml", ".form", ".mf")


def search(guard: Guard, project: str, query: str, mode: str = "text", glob: str | None = None,
           max_results: int = 50, case_sensitive: bool = False) -> dict:
    pdir = guard.project_dir(project)
    if not isinstance(query, str) or not query.strip():
        raise AutopilotError("Consulta vacía.")
    if mode not in ("text", "regex", "symbol", "references"):
        raise AutopilotError("mode debe ser text, regex, symbol o references.")
    flags = 0 if case_sensitive else re.I
    if mode == "regex":
        try:
            pat = re.compile(query, flags)
        except re.error as e:
            raise AutopilotError(f"Expresión regular no válida: {e}") from e
    elif mode in ("symbol", "references"):
        if not re.match(r"^[\w$]+$", query):
            raise AutopilotError("Para symbol/references usa un identificador Java (letras, dígitos, _ $).")
        pat = re.compile(rf"\b{re.escape(query)}\b")
    else:
        pat = re.compile(re.escape(query), flags)
    max_results = max(1, min(int(max_results), 300))
    hits, files_scanned = [], 0
    exts = TEXT_EXT if mode in ("text", "regex") else (".java",)
    for f in projects._walk(pdir, exts):
        rel = f.relative_to(pdir).as_posix()
        if glob and not Path(rel).match(glob):
            continue
        try:
            guard.check_readable(project, rel)
            raw = f.read_bytes()
            if len(raw) > guard.cfg.max_read_bytes:
                continue
            text = fileio.decode(raw)[0]
        except Exception:  # noqa: BLE001 - archivos ilegibles se omiten
            continue
        files_scanned += 1
        if mode in ("symbol", "references"):
            clean = java_scan.strip_noise(text)
            s = java_scan.scan(text)
            defs = {(t["line"]) for t in s["types"] if t["name"] == query} | \
                   {(m["line"]) for m in s["methods"] if m["name"] == query}
            for i, line in enumerate(clean.splitlines(), 1):
                if pat.search(line):
                    is_def = i in defs
                    if mode == "symbol" and not is_def:
                        continue
                    hits.append({"file": rel, "line": i, "definition": is_def,
                                 "text": text.splitlines()[i - 1].strip()[:200]})
        else:
            shown, _ = fileio.redact(text)
            for i, line in enumerate(shown.splitlines(), 1):
                if pat.search(line):
                    hits.append({"file": rel, "line": i, "text": line.strip()[:200]})
        if len(hits) >= max_results:
            break
    return {"project": project, "query": query, "mode": mode, "files_scanned": files_scanned,
            "match_count": min(len(hits), max_results), "truncated": len(hits) >= max_results,
            "matches": hits[:max_results]}


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def status(guard: Guard, store: fileio.ChangeStore, builder: Builder, project: str) -> dict:
    pdir = guard.project_dir(project)
    out: dict = {"project": project, "build": projects.detect_build(pdir)["primary"]}
    top = _git(["rev-parse", "--show-toplevel"], pdir)
    if top and guard._inside(Path(top.strip()).resolve()):
        porcelain = _git(["status", "--porcelain", "--", "."], pdir) or ""
        out["git"] = {"repository": True,
                      "branch": (_git(["rev-parse", "--abbrev-ref", "HEAD"], pdir) or "").strip(),
                      "changes": [l for l in porcelain.splitlines()][:100]}
    else:
        out["git"] = {"repository": False,
                      "note": "Sin Git: los cambios hechos por Claude se registran y se pueden revertir "
                              "(get_change_summary / revert_project_changes)."}
    last_by_path: dict[str, dict] = {}
    for e in store.entries():
        if e.get("project") == project and e.get("action") in ("create", "modify", "delete", "revert"):
            last_by_path[e["path"]] = e
    outside = []
    for rel, e in last_by_path.items():
        real = pdir / rel
        cur = fileio.sha256_bytes(real.read_bytes()) if real.is_file() else None
        expected = None if e["action"] in ("delete",) else e.get("sha_after")
        if e["action"] == "revert":
            continue
        if cur != expected:
            outside.append({"path": rel, "reason": "modificado fuera de las herramientas después del último cambio registrado"
                            if cur else "eliminado fuera de las herramientas"})
    out["modified_outside_tools"] = outside
    out["tool_changes_total"] = len(last_by_path)
    for kind, label in (("build", "last_build"), ("tests", "last_tests"), ("run", "last_run")):
        d = builder.load_last(project, kind)
        if d:
            out[label] = {"success": d.get("success", d.get("exit_code") == 0 if kind == "run" else None),
                          "finished": d.get("finished"), "exit_code": d.get("exit_code")}
    newest = sorted(projects._walk(pdir, (".java",)), key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    out["recently_modified_sources"] = [p.relative_to(pdir).as_posix() for p in newest]
    return out


POM_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>{group}</groupId>
    <artifactId>{name}</artifactId>
    <version>1.0-SNAPSHOT</version>
    <packaging>jar</packaging>
    <properties>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <maven.compiler.release>{release}</maven.compiler.release>
        <exec.mainClass>{group}.Main</exec.mainClass>
    </properties>
</project>
"""

MAIN_TEMPLATE = """package {group};

public class Main {{

    public static void main(String[] args) {{
        System.out.println("Hola desde {name}");
    }}
}}
"""


def create_project(guard: Guard, store: fileio.ChangeStore, name: str, release: int | None = None) -> dict:
    cfg = guard.cfg
    if not re.match(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$", name or ""):
        raise AutopilotError("Nombre de proyecto no válido (letras, dígitos, _ y -; empieza por letra).")
    target = guard.resolve(name)
    if target.exists():
        raise AutopilotError(f"Ya existe '{name}' en la carpeta raíz; no se sobrescribe.")
    rel = release or cfg.java_feature() or 17
    group = re.sub(r"[^a-z0-9]", "", name.lower()) or "app"
    if group[0].isdigit():
        group = "p" + group
    files = {"pom.xml": POM_TEMPLATE.format(group=group, name=name, release=rel),
             f"src/main/java/{group}/Main.java": MAIN_TEMPLATE.format(group=group, name=name)}
    for relp, content in files.items():
        p = target / relp
        data = content.encode("utf-8")
        fileio.write_atomic(p, data)
        store.append({"id": f"new-{name}-{relp}", "project": name, "path": relp, "action": "create",
                      "sha_before": None, "sha_after": fileio.sha256_bytes(data), "backup": None,
                      "note": "create_project"})
    return {"project": name, "created": list(files), "java_release": rel,
            "note": "Proyecto Maven compatible con NetBeans (Archivo > Abrir proyecto)."}
