"""Detección de proyectos, sistemas de construcción e inspección de estructura."""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from . import fileio, java_scan
from .config import Config
from .security import AutopilotError, Guard

SKIP_DIRS = {"target", "build", "dist", "out", ".git", ".idea", ".autopilot", "node_modules", ".gradle"}
MAX_JAVA_FILES = 600


def detect_build(pdir: Path) -> dict:
    systems = []
    if (pdir / "pom.xml").is_file():
        systems.append("maven")
    if any((pdir / n).is_file() for n in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")):
        systems.append("gradle")
    if (pdir / "build.xml").is_file():
        systems.append("ant")
    netbeans = (pdir / "nbproject").is_dir()
    has_java = any(True for _ in _walk(pdir, (".java",), limit=1))
    primary = systems[0] if systems else ("plain-java" if has_java else None)
    return {"systems": systems, "primary": primary, "netbeans_project": netbeans,
            "has_java_sources": has_java,
            "wrappers": [n for n in ("mvnw.cmd", "gradlew.bat") if (pdir / n).is_file()]}


def _walk(pdir: Path, exts: tuple[str, ...], limit: int | None = None):
    n = 0
    for dirpath, dirnames, filenames in os.walk(pdir):
        rel = Path(dirpath).relative_to(pdir).as_posix()
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and
                       not (rel == "nbproject" and d == "private")]
        for f in filenames:
            if f.lower().endswith(exts):
                yield Path(dirpath) / f
                n += 1
                if limit and n >= limit:
                    return


def list_projects(cfg: Config) -> dict:
    projects, loose = [], []
    for child in sorted(cfg.root.iterdir(), key=lambda p: p.name.lower()):
        if child.name.startswith("."):
            continue
        if child.is_dir():
            b = detect_build(child)
            projects.append({
                "name": child.name, "build_system": b["primary"], "all_build_files": b["systems"],
                "netbeans_project": b["netbeans_project"], "java": b["has_java_sources"],
                "read_only": child.name in cfg.read_only_projects,
            })
        elif child.suffix.lower() == ".java":
            loose.append(child.name)
    return {"root": str(cfg.root), "count": len(projects), "projects": projects,
            "loose_java_files_in_root": loose,
            "note": "Los .java sueltos de la raíz no son un proyecto; no se modifican con las herramientas de proyecto."}


# ---------- pom / ant ----------
def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def parse_pom(pom: Path) -> dict:
    try:
        root = fileio.parse_xml_safe(pom.read_bytes())
    except (ET.ParseError, ValueError) as e:
        return {"error": f"pom.xml mal formado: {e}"}
    info: dict = {"dependencies": [], "properties": {}}

    def child(el, name):
        for c in el:
            if _strip_ns(c.tag) == name:
                return c
        return None

    for name in ("groupId", "artifactId", "version", "packaging"):
        c = child(root, name)
        if c is not None and c.text:
            info[name] = c.text.strip()
    props = child(root, "properties")
    if props is not None:
        info["properties"] = {_strip_ns(p.tag): (p.text or "").strip() for p in props}
    deps = child(root, "dependencies")
    if deps is not None:
        for d in deps:
            if _strip_ns(d.tag) != "dependency":
                continue
            info["dependencies"].append({
                n: (child(d, n).text or "").strip() if child(d, n) is not None else None
                for n in ("groupId", "artifactId", "version", "scope")})
    p = info["properties"]
    rel = p.get("maven.compiler.release") or p.get("maven.compiler.source") or p.get("maven.compiler.target")
    info["java_release"] = int(rel) if rel and rel.isdigit() else None
    return info


def parse_properties(path: Path) -> dict:
    out: dict = {}
    if not path.is_file():
        return out
    text, _ = fileio.decode(path.read_bytes())
    text = re.sub(r"\\\r?\n\s*", "", text)  # continuaciones de línea
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def ant_info(pdir: Path) -> dict:
    props = parse_properties(pdir / "nbproject" / "project.properties")
    libs = []
    for k, v in props.items():
        if k.startswith("file.reference."):
            libs.append({"name": k[len("file.reference."):], "path": v.replace("\\\\", "\\"),
                         "exists": Path(v.replace("\\\\", "\\")).exists()})
    rel = props.get("javac.source")
    return {"properties_found": bool(props), "main_class": props.get("main.class"),
            "src_dir": props.get("src.dir", "src"), "test_dir": props.get("test.src.dir"),
            "java_release": int(rel) if rel and rel.isdigit() else None,
            "libraries": libs, "javac_classpath_raw": props.get("javac.classpath")}


# ---------- inspección ----------
def source_roots(pdir: Path, build: dict) -> list[Path]:
    roots: list[Path] = []
    if "maven" in build["systems"] or "gradle" in build["systems"]:
        roots += [pdir / "src/main/java", pdir / "src/test/java"]
    if "ant" in build["systems"]:
        ai = ant_info(pdir)
        roots += [pdir / ai["src_dir"]] + ([pdir / ai["test_dir"]] if ai["test_dir"] else [])
    if not roots:
        roots = [pdir / "src"] if (pdir / "src").is_dir() else [pdir]
    return [r for r in dict.fromkeys(roots) if r.is_dir()]


def scan_java_files(pdir: Path, build: dict) -> list[dict]:
    files = []
    for f in _walk(pdir, (".java",)):
        if len(files) >= MAX_JAVA_FILES:
            break
        try:
            text, _ = fileio.decode(f.read_bytes())
        except (OSError, AutopilotError):
            continue
        s = java_scan.scan(text)
        files.append({"path": f.relative_to(pdir).as_posix(), "text_len": len(text), **s})
    return files


def inspect_project(cfg: Config, guard: Guard, project: str) -> dict:
    pdir = guard.project_dir(project)
    build = detect_build(pdir)
    java_files = scan_java_files(pdir, build)
    roots = source_roots(pdir, build)

    packages: dict[str, list[str]] = defaultdict(list)
    entry_points, swing, db_hints, duplicates = [], [], set(), []
    fqcn_seen: dict[str, list[str]] = defaultdict(list)
    forms = []
    for jf in java_files:
        pkg = jf["package"]
        for t in jf["types"]:
            packages[pkg or "(paquete por defecto)"].append(t["name"])
            fqcn_seen[f"{pkg}.{t['name']}" if pkg else t["name"]].append(jf["path"])
            if t["extends"] and re.search(r"\b(JFrame|JDialog|JPanel|JInternalFrame|JApplet)\b", t["extends"]):
                swing.append({"class": t["name"], "extends": t["extends"], "file": jf["path"]})
        if jf["has_main"]:
            cls = Path(jf["path"]).stem
            entry_points.append({"class": f"{pkg}.{cls}" if pkg else cls, "file": jf["path"]})
        for imp in jf["imports"]:
            if imp.startswith(("java.sql", "javax.sql")):
                db_hints.add("JDBC (java.sql)")
            elif imp.startswith(("javax.persistence", "jakarta.persistence")):
                db_hints.add("JPA")
            elif imp.startswith("com.mysql") or imp.startswith("org.mariadb"):
                db_hints.add("Driver MySQL/MariaDB")
            elif imp.startswith("com.objectdb"):
                db_hints.add("ObjectDB")
    for name, paths in fqcn_seen.items():
        if len(paths) > 1:
            duplicates.append({"class": name, "files": paths})
    for f in _walk(pdir, (".form",)):
        rel = f.relative_to(pdir).as_posix()
        forms.append({"form": rel, "java": rel[:-5] + ".java", "java_exists": f.with_suffix(".java").exists()})

    resources = []
    for r in (pdir / "src/main/resources", pdir / "src/test/resources"):
        if r.is_dir():
            resources += [p.relative_to(pdir).as_posix() for p in r.rglob("*") if p.is_file()][:50]

    deps, libs, java_req = [], [], None
    if "maven" in build["systems"]:
        pom = parse_pom(pdir / "pom.xml")
        deps = pom.get("dependencies", [])
        java_req = pom.get("java_release")
        for d in deps:
            if (d.get("groupId") or "").startswith(("mysql", "com.mysql", "org.mariadb")):
                db_hints.add("Driver MySQL/MariaDB (dependencia Maven)")
    ant = None
    if "ant" in build["systems"]:
        ant = ant_info(pdir)
        java_req = java_req or ant["java_release"]
        libs = ant["libraries"]
        if ant["main_class"]:
            entry_points.append({"class": ant["main_class"], "file": "nbproject/project.properties (main.class)"})
        if any("objectdb" in l["name"].lower() for l in libs):
            db_hints.add("ObjectDB")

    entry_points = list({e["class"]: e for e in reversed(entry_points)}.values())[::-1]
    warnings = []
    installed = cfg.java_feature()
    if java_req and installed and java_req > installed:
        warnings.append(f"El proyecto exige Java {java_req} pero el JDK instalado es {installed}.")
    for l in libs:
        if not l["exists"]:
            warnings.append(f"Biblioteca externa no encontrada en disco: {l['path']}")
    if duplicates:
        warnings.append("Hay clases duplicadas (mismo nombre completo en varios archivos).")
    for jf in java_files:
        if not jf["balanced"]:
            warnings.append(f"Llaves/paréntesis desbalanceados en {jf['path']}")

    important = [n for n in ("pom.xml", "build.xml", "build.gradle", "nbactions.xml", "nbproject/project.properties",
                             "nbproject/project.xml", "README.md", "README.txt") if (pdir / n).is_file()]
    important += [e["file"] for e in entry_points if "(" not in e["file"]]
    counts = Counter(p.suffix.lower() for p in _walk(pdir, (".java", ".form", ".xml", ".properties", ".sql", ".fxml", ".jar")))
    return {
        "project": project, "path": str(pdir), "build": build,
        "java_version_required": java_req, "java_version_installed": installed,
        "source_roots": [r.relative_to(pdir).as_posix() or "." for r in source_roots(pdir, build)],
        "resources": resources,
        "packages": {k: sorted(v) for k, v in sorted(packages.items())},
        "java_file_count": len(java_files), "file_counts": dict(counts),
        "entry_points": entry_points,
        "swing_components": swing, "netbeans_forms": forms,
        "database_hints": sorted(db_hints),
        "maven_dependencies": deps, "ant": ant,
        "important_files": list(dict.fromkeys(important)),
        "duplicate_classes": duplicates, "warnings": warnings,
        "truncated": len(java_files) >= MAX_JAVA_FILES,
    }
