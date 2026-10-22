"""Análisis de salida de compiladores, pruebas y excepciones en tiempo de ejecución."""
from __future__ import annotations

import re
from pathlib import Path

MAVEN_ERR = re.compile(r"^\[(?P<sev>ERROR|WARNING)\]\s+(?P<file>.+?\.java):\[(?P<line>\d+),(?P<col>\d+)\]\s+(?P<msg>.*)$")
JAVAC_ERR = re.compile(r"^(?:\[javac\]\s+)?(?P<file>(?:[A-Za-z]:)?[^:\n]+?\.java):(?P<line>\d+):\s+(?P<sev>error|warning):\s+(?P<msg>.*)$")
DETAIL = re.compile(r"^\s*(symbol|location|required|found|reason)\s*:", re.I)
NOISE = re.compile(r"^\[ERROR\]\s*(->|For more information|Re-run|To see the full|$|Help \d)", re.I)
OTHER_ERR = re.compile(r"(?i)(release version \S+ not supported|invalid (source|target) release|"
                       r"Could not (resolve|transfer)|Cannot access|BUILD FAILED|Unable to locate|"
                       r"no compiler is provided|Failed to execute goal)")


def _rel(path: str, pdir: Path) -> str:
    p = path.strip()
    if re.match(r"^/[A-Za-z]:", p):
        p = p[1:]
    try:
        return Path(p).resolve().relative_to(pdir.resolve()).as_posix()
    except (ValueError, OSError):
        return p.replace("\\", "/")


def parse_build_errors(output: str, pdir: Path) -> dict:
    lines = output.splitlines()
    errors, warnings, other = [], [], []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = MAVEN_ERR.match(ln.strip()) or JAVAC_ERR.match(ln.strip())
        if m:
            item = {"file": _rel(m.group("file"), pdir), "line": int(m.group("line")),
                    "column": int(m.group("col")) if "col" in m.groupdict() and m.groupdict().get("col") else None,
                    "message": m.group("msg").strip(), "details": []}
            sev = m.group("sev").lower()
            j = i + 1
            while j < len(lines) and j <= i + 6:
                nxt = lines[j]
                core = re.sub(r"^\[(ERROR|WARNING|javac)\]\s?", "", nxt.strip(), flags=re.I)
                if MAVEN_ERR.match(nxt.strip()) or JAVAC_ERR.match(nxt.strip()):
                    break
                if DETAIL.match(core):
                    item["details"].append(core.strip())
                elif "source" not in item and j == i + 1 and core.strip() and not core.startswith(("[", "BUILD")):
                    item["source"] = core.rstrip()
                j += 1
            bucket = errors if sev == "error" else warnings
            key = (item["file"], item["line"], item["column"], item["message"])
            if not any((e["file"], e["line"], e["column"], e["message"]) == key for e in bucket):
                bucket.append(item)     # Maven repite cada error en el resumen final
            i = j
            continue
        st = ln.strip()
        if st.startswith(("[ERROR]", "BUILD FAILED", "error:")) and not NOISE.match(st):
            if OTHER_ERR.search(st) and len(other) < 15 and st not in other:
                other.append(st)
        elif OTHER_ERR.search(st) and st.lower().startswith(("error", "javac:", "fatal")) and len(other) < 15:
            other.append(st)
        i += 1
    return {"error_count": len(errors), "errors": errors, "warning_count": len(warnings),
            "warnings": warnings[:20], "other_errors": other}


def parse_test_results(output: str) -> dict:
    summaries = re.findall(r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+),\s*Skipped:\s*(\d+)(?!.*Time elapsed)",
                           output)
    result: dict = {"parsed": bool(summaries)}
    if summaries:
        run, fail, err, skip = map(int, summaries[-1])
        result.update({"tests_run": run, "failures": fail, "errors": err, "skipped": skip,
                       "passed": run - fail - err - skip})
    failing = []
    for m in re.finditer(r"^\[ERROR\]\s{2}(\S+?)(?::(\d+))?\s+(.*)$", output, re.M):
        failing.append({"test": m.group(1), "line": int(m.group(2)) if m.group(2) else None,
                        "message": m.group(3).strip()})
    result["failing"] = failing[:30]
    return result


EXC_HEAD = re.compile(
    r'^(?:Exception in thread "(?P<thread>[^"]+)" )?(?P<type>(?:[\w$]+\.)+[\w$]*(?:Exception|Error|Throwable))(?::\s?(?P<msg>.*))?$')
CAUSE = re.compile(r"^Caused by:\s+(?P<type>(?:[\w$]+\.)+[\w$]*(?:Exception|Error|Throwable))(?::\s?(?P<msg>.*))?$")
FRAME = re.compile(r"^\s+at\s+(?P<frame>.+?)(?:\((?P<file>[\w$]+\.java):(?P<line>\d+)\))?\s*$")
SYSTEM_PREFIXES = ("java.", "javax.", "jdk.", "sun.", "com.sun.")

HINTS = [
    (r"ClassNotFoundException.*(mysql|mariadb|jdbc)|No suitable driver",
     "Falta el conector JDBC en el classpath: añade la dependencia (mysql-connector-j / mariadb-java-client) al proyecto."),
    (r"Communications link failure|Connection refused|CommunicationsException",
     "No se pudo conectar con el servidor de base de datos: ¿está iniciado MySQL/MariaDB? Revisa host, puerto y URL."),
    (r"Access denied for user", "La base de datos rechazó el usuario/contraseña configurados."),
    (r"Unknown database", "La base de datos indicada en la URL no existe todavía: créala con un script SQL."),
    (r"HeadlessException", "Se intentó abrir una ventana sin entorno gráfico (ejecución headless)."),
    (r"UnsupportedClassVersionError", "Las clases se compilaron con una versión de Java más nueva que la que ejecuta."),
    (r"NoClassDefFoundError|ClassNotFoundException", "Falta una clase en el classpath o el proyecto no está compilado."),
    (r"InputMismatchException|NoSuchElementException", "El programa leyó una entrada que no esperaba: revisa stdin_text."),
    (r"NumberFormatException", "Se intentó convertir a número un texto no numérico: valida la entrada."),
    (r"NullPointerException", "Se usó una referencia nula: revisa la línea indicada y la inicialización del objeto."),
    (r"ArrayIndexOutOfBounds|IndexOutOfBounds", "Índice fuera de rango: revisa los límites del bucle/array."),
]


def parse_exceptions(output: str, project_files: dict[str, str] | None = None) -> list[dict]:
    project_files = project_files or {}
    excs: list[dict] = []
    cur: dict | None = None
    target: dict | None = None
    for raw in output.splitlines():
        line = raw.rstrip()
        mc = CAUSE.match(line)
        if mc and cur is not None:
            target = {"type": mc.group("type"), "message": (mc.group("msg") or "").strip(), "frames": []}
            cur["caused_by"].append(target)
            continue
        mh = EXC_HEAD.match(line.strip()) if not line.startswith((" ", "\t")) else None
        if mh:
            cur = {"type": mh.group("type"), "message": (mh.group("msg") or "").strip(),
                   "thread": mh.group("thread"), "frames": [], "caused_by": []}
            excs.append(cur)
            target = cur
            continue
        mf = FRAME.match(line)
        if mf and target is not None:
            target["frames"].append({"frame": mf.group("frame"), "file": mf.group("file"),
                                     "line": int(mf.group("line")) if mf.group("line") else None})
    for e in excs:
        chain = [e] + e["caused_by"]
        origin = None
        for c in reversed(chain):          # la causa raíz suele estar al final
            for fr in c["frames"]:
                if fr["file"] and not fr["frame"].startswith(SYSTEM_PREFIXES):
                    origin = {"frame": fr["frame"], "file": fr["file"], "line": fr["line"],
                              "project_file": project_files.get(fr["file"])}
                    break
            if origin:
                break
        e["origin"] = origin
        blob = " ".join(c["type"] + " " + c["message"] for c in chain)
        e["hints"] = [h for pat, h in HINTS if re.search(pat, blob)]
        for c in chain:
            c["frames"] = c["frames"][:12]
    return excs[:10]
