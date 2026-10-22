"""Compilación, ejecución y pruebas. Solo se ejecutan comandos de una lista cerrada, nunca texto del proyecto."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import diagnostics, fileio, projects, runner
from .config import Config
from .security import AutopilotError, Guard

MAIN_CLASS_RE = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")
ARG_RE = re.compile(r"^[\w.,:=@%+\-/\\ ]{0,200}$")
RESOLVE_FAIL = re.compile(r"(?i)(offline mode|Could not resolve dependencies|Cannot access|Could not transfer|"
                          r"Unknown host|Failed to read artifact descriptor|Plugin .* or one of its dependencies)")
CMD_SAFE_PATH = re.compile(r"^[^&|<>^%!\"'`;]+$")


class Builder:
    def __init__(self, cfg: Config, guard: Guard):
        self.cfg, self.guard = cfg, guard
        self.runs = cfg.data_dir / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)

    # ---------- utilidades ----------
    def _save(self, project: str, kind: str, data: dict) -> None:
        (self.runs / f"{project}.{kind}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")

    def load_last(self, project: str, kind: str) -> dict | None:
        p = self.runs / f"{project}.{kind}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def _need_java(self) -> None:
        if not self.cfg.java_home:
            raise AutopilotError("No se encontró un JDK (defina java_home en config.json o JAVA_HOME).")

    def _sources(self, pdir: Path) -> list[Path]:
        return sorted(projects._walk(pdir, (".java",)))

    def classes_dir(self, pdir: Path, build: dict) -> Path:
        p = build["primary"]
        if p == "maven":
            return pdir / "target" / "classes"
        if p == "gradle":
            return pdir / "build" / "classes" / "java" / "main"
        if p == "ant":
            return pdir / "build" / "classes"
        return pdir / "out" / "classes"

    # ---------- detección ----------
    def detect(self, project: str) -> dict:
        pdir = self.guard.project_dir(project)
        b = projects.detect_build(pdir)
        tools = {"maven": str(self.cfg.maven_cmd) if self.cfg.maven_cmd else None,
                 "ant": str(self.cfg.ant_cmd) if self.cfg.ant_cmd else None,
                 "gradle": str(self.cfg.gradle_cmd) if self.cfg.gradle_cmd else None,
                 "javac": str(self.cfg.javac) if self.cfg.javac else None}
        usable = {"maven": bool(self.cfg.maven_cmd), "ant": bool(self.cfg.ant_cmd),
                  "gradle": bool(self.cfg.gradle_cmd), "plain-java": bool(self.cfg.javac)}.get(b["primary"], False)
        notes = []
        if b["wrappers"]:
            notes.append("Hay mvnw/gradlew pero no se ejecutan (son scripts del proyecto); se usa la herramienta instalada.")
        if len(b["systems"]) > 1:
            notes.append(f"Hay varios sistemas ({', '.join(b['systems'])}); se usa {b['primary']}.")
        return {"project": project, **b, "tools": tools, "tool_available": usable, "notes": notes,
                "classes_dir": self.classes_dir(pdir, b).relative_to(pdir).as_posix() if b["primary"] else None}

    # ---------- compilación ----------
    def _build_argv(self, pdir: Path, b: dict, goal: str) -> tuple[list[str], str]:
        sysm = b["primary"]
        if sysm == "maven":
            if not self.cfg.maven_cmd:
                raise AutopilotError("Maven no encontrado (ni en PATH ni en la instalación de NetBeans).")
            g = {"compile": "test-compile", "test": "test"}[goal]
            return [str(self.cfg.maven_cmd), "-B", "-ntp", "-Dstyle.color=never", g], "maven"
        if sysm == "ant":
            if not self.cfg.ant_cmd:
                raise AutopilotError("Ant no encontrado (ni en PATH ni en la instalación de NetBeans).")
            return [str(self.cfg.ant_cmd), "-noinput", "-f", "build.xml", goal], "ant"
        if sysm == "gradle":
            if not self.cfg.gradle_cmd:
                raise AutopilotError("Gradle no está instalado y los wrappers (gradlew) no se ejecutan por seguridad.")
            tasks = ["compileJava", "compileTestJava"] if goal == "compile" else ["test"]
            return [str(self.cfg.gradle_cmd), "--console=plain", "--no-daemon", *tasks], "gradle"
        raise AutopilotError("Proyecto sin sistema de construcción reconocido.")

    def compile(self, project: str) -> dict:
        self._need_java()
        pdir = self.guard.project_dir(project)
        b = projects.detect_build(pdir)
        if not b["primary"]:
            raise AutopilotError("No hay código Java ni archivos de construcción en el proyecto.")
        attempts = []
        if b["primary"] == "plain-java":
            res = self._compile_plain(pdir)
            attempts.append(res)
        else:
            argv, system = self._build_argv(pdir, b, "compile")
            if system == "maven" and self.cfg.allow_network:
                res = runner.run_command(argv[:1] + ["-o"] + argv[1:], pdir, self.cfg.build_timeout, self.cfg.java_home)
                attempts.append({**res, "mode": "offline"})
                if res["exit_code"] != 0 and RESOLVE_FAIL.search(res["stdout"] + res["stderr"]):
                    res = runner.run_command(argv, pdir, self.cfg.build_timeout, self.cfg.java_home)
                    attempts.append({**res, "mode": "online (reintento por dependencias)"})
            elif system == "maven":
                res = runner.run_command(argv[:1] + ["-o"] + argv[1:], pdir, self.cfg.build_timeout, self.cfg.java_home)
                attempts.append({**res, "mode": "offline"})
            else:
                res = runner.run_command(argv, pdir, self.cfg.build_timeout, self.cfg.java_home)
                attempts.append(res)
        final = attempts[-1]
        text = final["stdout"] + "\n" + final["stderr"]
        diag = diagnostics.parse_build_errors(text, pdir)
        ok = final["exit_code"] == 0 and not final["timed_out"]
        out = {"project": project, "build_system": b["primary"], "success": ok,
               "exit_code": final["exit_code"], "timed_out": final["timed_out"],
               "duration_s": final["duration_s"], "command": " ".join(final["argv"]),
               "attempts": len(attempts), "diagnostics": diag,
               "output": text.strip()[-8000:] if not ok else text.strip()[-1500:],
               "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self._save(project, "build", out)
        return out

    def _compile_plain(self, pdir: Path) -> dict:
        srcs = self._sources(pdir)
        if not srcs:
            raise AutopilotError("No hay archivos .java que compilar.")
        outdir = pdir / "out" / "classes"
        outdir.mkdir(parents=True, exist_ok=True)
        argv = [str(self.cfg.javac), "-encoding", "UTF-8", "-proc:none", "-d", str(outdir),
                *[str(s) for s in srcs]]
        return runner.run_command(argv, pdir, self.cfg.build_timeout, self.cfg.java_home)

    def errors(self, project: str, rerun: bool = False) -> dict:
        self.guard.project_dir(project)
        last = None if rerun else self.load_last(project, "build")
        if last is None:
            last = self.compile(project)
        d = last["diagnostics"]
        return {"project": project, "build_success": last["success"], "from_build_at": last["finished"],
                "error_count": d["error_count"], "errors": d["errors"], "other_errors": d["other_errors"],
                "warning_count": d["warning_count"],
                "by_file": _group(d["errors"])}

    # ---------- classpath y ejecución ----------
    def _classpath(self, pdir: Path, b: dict) -> tuple[list[str], str]:
        classes = self.classes_dir(pdir, b)
        cp = [str(classes)]
        src = "classes"
        if b["primary"] == "maven":
            jars, src = self._maven_jars(pdir)
            cp += jars
        elif b["primary"] == "ant":
            ai = projects.ant_info(pdir)
            props = projects.parse_properties(pdir / "nbproject" / "project.properties")
            raw = ai["javac_classpath_raw"] or ""
            for tok in re.findall(r"\$\{([^}]+)\}", raw):
                val = props.get(tok)
                if val:
                    val = val.replace("\\\\", "\\")
                    cp.append(val if Path(val).is_absolute() else str(pdir / val))
            src = "project.properties"
        return cp, src

    def _maven_jars(self, pdir: Path) -> tuple[list[str], str]:
        out_file = self.cfg.data_dir / "tmp" / f"cp-{pdir.name}.txt"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.unlink(missing_ok=True)
        if self.cfg.maven_cmd and CMD_SAFE_PATH.match(str(out_file)):
            argv = [str(self.cfg.maven_cmd), "-o", "-B", "-q", "dependency:build-classpath",
                    f"-Dmdep.outputFile={out_file}"]
            res = runner.run_command(argv, pdir, self.cfg.build_timeout, self.cfg.java_home)
            if res["exit_code"] == 0 and out_file.exists():
                txt = out_file.read_text(encoding="utf-8").strip()
                return [j for j in txt.split(os.pathsep) if j], "mvn dependency:build-classpath"
        # Alternativa: dependencias directas con versión explícita en ~/.m2
        jars = []
        pom = projects.parse_pom(pdir / "pom.xml")
        m2 = Path.home() / ".m2" / "repository"
        for d in pom.get("dependencies", []):
            if d.get("groupId") and d.get("artifactId") and d.get("version") and "$" not in d["version"]:
                j = m2 / d["groupId"].replace(".", "/") / d["artifactId"] / d["version"] / \
                    f"{d['artifactId']}-{d['version']}.jar"
                if j.exists():
                    jars.append(str(j))
        return jars, "~/.m2 (solo dependencias directas)"

    def _main_candidates(self, pdir: Path, b: dict) -> list[str]:
        out = []
        if b["primary"] == "ant":
            mc = projects.ant_info(pdir).get("main_class")
            if mc:
                out.append(mc)
        elif b["primary"] == "maven":
            props = projects.parse_pom(pdir / "pom.xml").get("properties", {})
            for k in ("exec.mainClass", "mainClass", "main.class"):
                if props.get(k):
                    out.append(props[k])
        if out:
            return out
        for jf in projects.scan_java_files(pdir, b):
            if jf["has_main"]:
                stem = Path(jf["path"]).stem
                if "test" in jf["path"].lower().split("/")[:3] and stem.lower().endswith("test"):
                    continue
                out.append(f"{jf['package']}.{stem}" if jf["package"] else stem)
        return out

    def _stale(self, pdir: Path, classes: Path) -> bool:
        if not classes.is_dir():
            return True
        newest_class = max((p.stat().st_mtime for p in classes.rglob("*.class")), default=0)
        newest_src = max((p.stat().st_mtime for p in self._sources(pdir)), default=0)
        return newest_src > newest_class

    def run(self, project: str, main_class: str | None = None, args: list[str] | None = None,
            stdin_text: str | None = None, timeout: int = 30, headless: bool = False,
            auto_compile: bool = True) -> dict:
        self._need_java()
        pdir = self.guard.project_dir(project)
        b = projects.detect_build(pdir)
        if not b["primary"]:
            raise AutopilotError("No hay nada que ejecutar en este proyecto.")
        args = args or []
        if len(args) > 20 or any((not isinstance(a, str)) or not ARG_RE.match(a) for a in args):
            raise AutopilotError("Argumentos no válidos (máx. 20; solo letras, dígitos y . , : = @ % + - / \\ espacio).")
        if main_class is not None and not MAIN_CLASS_RE.match(main_class):
            raise AutopilotError(f"Nombre de clase principal no válido: {main_class!r}")
        timeout = max(1, min(int(timeout), self.cfg.max_run_timeout))
        if main_class is None:
            cands = self._main_candidates(pdir, b)
            if len(cands) == 1:
                main_class = cands[0]
            elif not cands:
                raise AutopilotError("No se encontró ninguna clase con main(String[]).")
            else:
                raise AutopilotError("Hay varias clases principales; indica main_class: " + ", ".join(cands))
        classes = self.classes_dir(pdir, b)
        compiled = None
        if auto_compile and self._stale(pdir, classes):
            compiled = self.compile(project)
            if not compiled["success"]:
                return {"project": project, "ran": False, "reason": "La compilación falló; no se ejecutó.",
                        "build": {"errors": compiled["diagnostics"]["errors"],
                                  "other_errors": compiled["diagnostics"]["other_errors"]}}
        cp, cp_src = self._classpath(pdir, b)
        argv = [str(self.cfg.java), "-Dfile.encoding=UTF-8", "-Dstdout.encoding=UTF-8", "-Dstderr.encoding=UTF-8"]
        if headless:
            argv.append("-Djava.awt.headless=true")
        argv += ["-cp", os.pathsep.join(cp), main_class, *args]
        res = runner.run_command(argv, pdir, timeout, self.cfg.java_home, stdin_text=stdin_text)
        out = {"project": project, "ran": True, "main_class": main_class, "exit_code": res["exit_code"],
               "timed_out": res["timed_out"], "duration_s": res["duration_s"], "stdout": res["stdout"],
               "stderr": res["stderr"], "classpath_source": cp_src, "compiled_first": bool(compiled),
               "finished": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "command": " ".join(argv[:1] + ["..."] + argv[-(1 + len(args)):])}
        if res["timed_out"]:
            out["note"] = (f"Se detuvo tras {timeout}s. Es normal en aplicaciones gráficas (Swing) o que esperan "
                           "entrada: no indica por sí solo un fallo.")
        self._save(project, "run", out)
        return out

    # ---------- pruebas ----------
    def tests(self, project: str, timeout: int | None = None) -> dict:
        self._need_java()
        pdir = self.guard.project_dir(project)
        b = projects.detect_build(pdir)
        if not b["primary"]:
            raise AutopilotError("Proyecto sin código ni sistema de construcción.")
        if b["primary"] == "plain-java":
            return self._tests_plain(project, pdir, b)
        argv, system = self._build_argv(pdir, b, "test")
        if system == "maven":  # primero sin red; se reintenta en línea solo si falla la resolución
            argv = argv[:1] + ["-o"] + argv[1:]
        res = runner.run_command(argv, pdir, timeout or self.cfg.build_timeout, self.cfg.java_home)
        if (system == "maven" and self.cfg.allow_network and res["exit_code"] != 0
                and RESOLVE_FAIL.search(res["stdout"] + res["stderr"])):
            res = runner.run_command([a for a in argv if a != "-o"], pdir, timeout or self.cfg.build_timeout,
                                     self.cfg.java_home)
        text = res["stdout"] + "\n" + res["stderr"]
        parsed = diagnostics.parse_test_results(text)
        comp = diagnostics.parse_build_errors(text, pdir)
        out = {"project": project, "build_system": system, "success": res["exit_code"] == 0,
               "exit_code": res["exit_code"], "timed_out": res["timed_out"], "duration_s": res["duration_s"],
               "results": parsed, "compile_errors": comp["errors"][:20],
               "output_tail": text.strip()[-4000:]}
        if not parsed["parsed"] and out["success"]:
            out["note"] = "El comando terminó bien pero no se encontraron pruebas (¿hay src/test/java?)."
        self._save(project, "tests", out)
        return out

    def _tests_plain(self, project: str, pdir: Path, b: dict) -> dict:
        comp = self.compile(project)
        if not comp["success"]:
            return {"project": project, "success": False, "reason": "No compila.",
                    "compile_errors": comp["diagnostics"]["errors"]}
        classes = self.classes_dir(pdir, b)
        tests = []
        for jf in projects.scan_java_files(pdir, b):
            stem = Path(jf["path"]).stem
            if jf["has_main"] and re.search(r"(Test|Tests)$", stem):
                tests.append(f"{jf['package']}.{stem}" if jf["package"] else stem)
        if not tests:
            return {"project": project, "success": True, "tests": [],
                    "note": "No hay clases de prueba (*Test con main) en este proyecto sin Maven/Ant."}
        results = []
        for t in tests:
            r = runner.run_command([str(self.cfg.java), "-Dstdout.encoding=UTF-8", "-cp", str(classes), t], pdir,
                                   60, self.cfg.java_home)
            results.append({"class": t, "exit_code": r["exit_code"], "passed": r["exit_code"] == 0,
                            "output": (r["stdout"] + r["stderr"]).strip()[-3000:]})
        out = {"project": project, "build_system": "plain-java", "success": all(r["passed"] for r in results),
               "tests": results}
        self._save(project, "tests", out)
        return out

    # ---------- registros de ejecución ----------
    def logs(self, project: str, source: str = "last_run", path: str | None = None, tail: int = 60) -> dict:
        pdir = self.guard.project_dir(project)
        if source == "file":
            if not path:
                raise AutopilotError("source='file' requiere 'path'.")
            _, real, relp = self.guard.file_path(project, path)
            self.guard.check_readable(project, relp)
            if not real.is_file():
                raise AutopilotError(f"No existe: {relp}")
            if real.stat().st_size > self.cfg.max_read_bytes:
                raise AutopilotError("Registro demasiado grande.")
            text = fileio.decode(real.read_bytes())[0]
            text, _n = fileio.redact(text)
            meta = {"source": relp}
        else:
            kind = {"last_run": "run", "last_build": "build", "last_tests": "tests"}.get(source)
            if not kind:
                raise AutopilotError("source debe ser last_run, last_build, last_tests o file.")
            last = self.load_last(project, kind)
            if not last:
                raise AutopilotError(f"No hay un resultado de '{kind}' guardado para este proyecto.")
            text = (last.get("stdout", "") + "\n" + last.get("stderr", "")) if kind == "run" else \
                (last.get("output") or last.get("output_tail") or "")
            meta = {"source": source, "exit_code": last.get("exit_code"), "timed_out": last.get("timed_out"),
                    "finished": last.get("finished")}
        pfiles = {Path(p).name: p.relative_to(pdir).as_posix() for p in self._sources(pdir)}
        excs = diagnostics.parse_exceptions(text, pfiles)
        lines = text.strip().splitlines()
        return {"project": project, **meta, "exception_count": len(excs), "exceptions": excs,
                "tail": "\n".join(lines[-max(1, min(tail, 400)):])}


def _group(errors: list[dict]) -> dict:
    g: dict[str, list[str]] = {}
    for e in errors:
        g.setdefault(e["file"], []).append(f"l.{e['line']}: {e['message']}")
    return g
