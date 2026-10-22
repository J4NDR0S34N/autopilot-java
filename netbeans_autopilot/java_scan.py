"""Análisis ligero de código Java por expresiones regulares (sin dependencias)."""
from __future__ import annotations

import re


def strip_noise(src: str) -> str:
    """Sustituye comentarios y literales por espacios conservando saltos de línea y posiciones."""
    out: list[str] = []
    i, n = 0, len(src)
    blank = lambda s: re.sub(r"[^\n]", " ", s)  # noqa: E731
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(blank(src[i:j]))
            i = j
        elif src.startswith('"""', i):
            j = src.find('"""', i + 3)
            j = n if j < 0 else j + 3
            out.append(blank(src[i:j]))
            i = j
        elif src[i] in "\"'":
            q = src[i]
            j = i + 1
            while j < n and src[j] != q and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(q + " " * max(0, j - i - 2) + (q if j - i >= 2 else ""))
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


PACKAGE_RE = re.compile(r"(?m)^\s*package\s+([\w.]+)\s*;")
IMPORT_RE = re.compile(r"(?m)^\s*import\s+(?:static\s+)?([\w.*]+)\s*;")
TYPE_RE = re.compile(
    r"(?m)^[ \t]*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|protected|private|abstract|static|final|sealed|non-sealed|strictfp)\s+)*"
    r"(class|interface|enum|record|@interface)\s+(\w+)(?:\s*<[^{]*?>)?(?:\s*\([^)]*\))?"
    r"(?:\s+extends\s+([\w.<>, ?]+?))?(?:\s+implements\s+([\w.<>, ?]+?))?\s*(?:permits\s+[\w., ]+)?\{")
METHOD_RE = re.compile(
    r"(?m)^[ \t]*(?:@\w+(?:\([^)]*\))?\s+)*((?:(?:public|protected|private|static|final|abstract|synchronized|native|default)\s+)*)"
    r"(?:<[^>]+>\s+)?([\w<>\[\],.? ]+?)\s+(\w+)\s*\(([^;{)]*)\)\s*(?:throws\s+[\w., ]+)?\s*\{")
MAIN_RE = re.compile(r"public\s+static\s+void\s+main\s*\(\s*(?:final\s+)?String\s*(?:\[\s*\]\s*\w+|\.\.\.\s*\w+|\w+\s*\[\s*\])")
_NOT_METHODS = {"if", "for", "while", "switch", "catch", "synchronized", "return", "new", "else", "try"}


def scan(src: str) -> dict:
    clean = strip_noise(src)
    pkg = PACKAGE_RE.search(clean)
    imports = IMPORT_RE.findall(clean)
    types = []
    for m in TYPE_RE.finditer(clean):
        types.append({"kind": m.group(1), "name": m.group(2),
                      "extends": (m.group(3) or "").strip() or None,
                      "implements": (m.group(4) or "").strip() or None,
                      "line": clean.count("\n", 0, m.start()) + 1})
    methods = []
    for m in METHOD_RE.finditer(clean):
        name = m.group(3)
        ret = m.group(2).strip()
        if name in _NOT_METHODS or ret in _NOT_METHODS or ret.split()[-1:] == ["new"]:
            continue
        methods.append({"name": name, "returns": ret, "params": " ".join(m.group(4).split()),
                        "line": clean.count("\n", 0, m.start()) + 1})
    return {
        "package": pkg.group(1) if pkg else "",
        "imports": imports,
        "types": types,
        "methods": methods,
        "has_main": bool(MAIN_RE.search(clean)),
        "balanced": balanced(clean),
    }


def balanced(clean: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for ch in clean:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack.pop() != pairs[ch]:
                return False
    return not stack


# ---------- código generado por el diseñador de NetBeans ----------
def generated_lines(text: str) -> list[str]:
    """Líneas protegidas: bloques GEN-BEGIN/GEN-END, 'Generated Code' y marcadores GEN-FIRST/GEN-LAST."""
    prot: list[str] = []
    inside = False
    for line in text.splitlines():
        s = line.strip()
        if not inside and ("//GEN-BEGIN:" in s or ("<editor-fold" in s and "Generated Code" in s)):
            inside = True
        if inside:
            prot.append(line)
            if "//GEN-END:" in s or "</editor-fold>" in s:
                inside = False
            continue
        if "//GEN-FIRST:" in s or "//GEN-LAST:" in s:
            prot.append(line)
    return prot
