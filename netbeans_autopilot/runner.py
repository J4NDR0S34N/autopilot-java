"""Ejecución controlada de procesos: lista de argumentos (sin shell), entorno reducido, límites de tiempo."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

SAFE_ENV_KEYS = ["PATH", "SystemRoot", "windir", "ComSpec", "PATHEXT", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
                 "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "ProgramFiles", "ProgramFiles(x86)", "ProgramData",
                 "ProgramW6432", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "USERNAME", "COMPUTERNAME",
                 "OS", "SystemDrive"]
MAX_OUTPUT = 60_000


def decode_best(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", errors="replace")


def truncate(text: str, limit: int = MAX_OUTPUT) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    head, tail = limit // 3, limit - limit // 3
    return text[:head] + f"\n... [{len(text) - limit} caracteres omitidos] ...\n" + text[-tail:], True


def safe_env(java_home: Path | None, extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    if java_home:
        env["JAVA_HOME"] = str(java_home)
        env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    if extra:
        env.update(extra)
    return env


def _kill_tree(pid: int) -> None:
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=20)


def run_command(argv: list[str], cwd: Path, timeout: int, java_home: Path | None,
                stdin_text: str | None = None, extra_env: dict | None = None) -> dict:
    t0 = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=safe_env(java_home, extra_env),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW)
    except OSError as e:
        return {"argv": argv, "exit_code": None, "stdout": "", "stderr": f"No se pudo iniciar el proceso: {e}",
                "timed_out": False, "duration_s": 0.0, "truncated": False}
    try:
        out, err = proc.communicate(stdin_text.encode("utf-8") if stdin_text is not None else None,
                                    timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out, err = b"", b""
    so, t1 = truncate(decode_best(out or b""))
    se, t2 = truncate(decode_best(err or b""))
    return {"argv": argv, "exit_code": None if timed_out else proc.returncode, "stdout": so, "stderr": se,
            "timed_out": timed_out, "duration_s": round(time.monotonic() - t0, 2), "truncated": t1 or t2}
