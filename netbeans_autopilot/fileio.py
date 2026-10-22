"""Lectura/escritura de texto con detección de codificación, hash y registro de cambios."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .security import AutopilotError

SESSION_ID = uuid.uuid4().hex[:12]


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def decode(raw: bytes) -> tuple[str, str]:
    if b"\x00" in raw[:8192]:
        raise AutopilotError("Archivo binario: no se puede tratar como texto.")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def encode(text: str, enc: str) -> bytes:
    data = text.encode("utf-8" if enc == "utf-8-sig" else enc, errors="replace")
    return b"\xef\xbb\xbf" + data if enc == "utf-8-sig" else data


def parse_xml_safe(raw: bytes):
    """Analiza XML rechazando DOCTYPE/ENTITY (previene XXE y 'billion laughs') sin dependencias extra."""
    import xml.etree.ElementTree as ET
    head = raw.lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise ValueError("XML con DOCTYPE/ENTITY rechazado por seguridad")
    return ET.fromstring(raw)


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


# ---------- redacción de secretos al leer ----------
_SECRET_KV = re.compile(
    r"""(?ix)((?:password|passwd|pwd|secret|token|api[_-]?key)\w*["']?\s*[=:]\s*)("[^"\n]*"|'[^'\n]*'|[^\s;,&"'<>]+)""")
_SECRET_XML = re.compile(r"(?is)(<(?:password|passwd|secret|token)\w*>)([^<]+)(</)")
_JDBC_3ARG = re.compile(r"(DriverManager\.getConnection\(\s*[^,()]+,\s*[^,()]+,\s*)([^)]+?)(\s*\))")


def redact(text: str) -> tuple[str, int]:
    count = 0

    def kv(m):
        nonlocal count
        count += 1
        q = '"' if m.group(2).startswith('"') else ("'" if m.group(2).startswith("'") else "")
        return f"{m.group(1)}{q}***REDACTED***{q}"

    def xml(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}***REDACTED***{m.group(3)}"

    def jdbc(m):
        nonlocal count
        count += 1
        return f"{m.group(1)}\"***REDACTED***\"{m.group(3)}"

    text = _SECRET_KV.sub(kv, text)
    text = _SECRET_XML.sub(xml, text)
    text = _JDBC_3ARG.sub(jdbc, text)
    return text, count


# ---------- registro de cambios y copias de seguridad ----------
class ChangeStore:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.backups = data_dir / "backups"
        self.log = data_dir / "changes.jsonl"
        self.backups.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def backup(self, change_id: str, original: bytes) -> str:
        p = self.backups / f"{change_id}.bak"
        p.write_bytes(original)
        return p.name

    def append(self, entry: dict) -> None:
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": SESSION_ID, **entry}
        with self._lock, self.log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict]:
        if not self.log.exists():
            return []
        out = []
        for line in self.log.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def read_backup(self, name: str) -> bytes:
        return (self.backups / name).read_bytes()
