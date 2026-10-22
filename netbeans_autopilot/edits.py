"""Operaciones de edición: crear, modificar, eliminar y revertir con copias de seguridad y registro."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from . import fileio, java_scan
from .security import AutopilotError, Guard, SecurityError

MAX_OPS = 50


class Editor:
    def __init__(self, guard: Guard, store: fileio.ChangeStore):
        self.guard, self.store, self.cfg = guard, store, guard.cfg

    # ---------- lectura ----------
    def read(self, project: str, rel: str, start_line: int = 1, max_lines: int = 400) -> dict:
        pdir, real, relp = self.guard.file_path(project, rel)
        self.guard.check_readable(project, relp)
        if not real.is_file():
            raise AutopilotError(f"No existe el archivo: {project}/{relp}")
        if real.stat().st_size > self.cfg.max_read_bytes:
            raise AutopilotError(f"Archivo demasiado grande (> {self.cfg.max_read_bytes} bytes).")
        raw = real.read_bytes()
        text, enc = fileio.decode(raw)
        text, redactions = fileio.redact(text)
        lines = text.splitlines()
        start = max(1, int(start_line))
        chunk = lines[start - 1:start - 1 + max(1, min(int(max_lines), 2000))]
        out = {"project": project, "path": relp, "encoding": enc, "sha256": fileio.sha256_bytes(raw),
               "total_lines": len(lines), "start_line": start, "end_line": start - 1 + len(chunk),
               "truncated": start - 1 + len(chunk) < len(lines), "content": "\n".join(chunk),
               "designer_generated_regions": bool(java_scan.generated_lines(text)) if relp.endswith(".java") else False}
        if redactions:
            out["redactions"] = redactions
            out["note"] = "Se ocultaron valores que parecen contraseñas/tokens; no edites esas líneas."
        return out

    # ---------- planificación (sin tocar disco) ----------
    def _load(self, project: str, rel: str):
        pdir, real, relp = self.guard.file_path(project, rel)
        exists = real.is_file()
        if real.exists() and not exists:
            raise AutopilotError(f"No es un archivo: {relp}")
        if exists:
            raw = real.read_bytes()
            text, enc = fileio.decode(raw)
            return real, relp, raw, text, enc
        return real, relp, None, None, "utf-8"

    def _plan(self, project: str, ops: list[dict]) -> list[dict]:
        if not ops:
            raise AutopilotError("No hay operaciones.")
        if len(ops) > MAX_OPS:
            raise AutopilotError(f"Demasiadas operaciones ({len(ops)} > {MAX_OPS}).")
        state: dict[str, dict] = {}   # relp -> {real, orig_raw, orig_text, text, enc, deleted}
        steps = []
        for i, op in enumerate(ops, 1):
            kind = op.get("op")
            if kind not in ("create", "edit", "delete"):
                raise AutopilotError(f"Operación {i}: 'op' debe ser create, edit o delete.")
            rel = op.get("path")
            real, relp, raw, text, enc = self._load(project, rel)
            self.guard.check_writable(project, relp, kind)
            key = relp.lower()
            cur = state.get(key)
            if cur is None:
                cur = state[key] = {"real": real, "rel": relp, "orig_raw": raw, "orig_text": text,
                                    "text": text, "enc": enc, "deleted": False, "existed": raw is not None}
            exists_now = cur["text"] is not None and not cur["deleted"]
            if kind == "create":
                content = op.get("content")
                if not isinstance(content, str):
                    raise AutopilotError(f"Operación {i}: falta 'content'.")
                if exists_now and not op.get("overwrite"):
                    raise AutopilotError(
                        f"Operación {i}: {relp} ya existe. Usa edit, o overwrite=true si es intencionado.")
                if len(content.encode("utf-8")) > self.cfg.max_write_bytes:
                    raise AutopilotError(f"Operación {i}: contenido demasiado grande.")
                if exists_now:
                    self._check_generated(relp, cur["text"], content)
                cur["text"], cur["deleted"] = content, False
            elif kind == "edit":
                if not exists_now:
                    raise AutopilotError(f"Operación {i}: {relp} no existe (usa create).")
                exp = op.get("expected_sha256")
                if exp and cur["orig_raw"] is not None and fileio.sha256_bytes(cur["orig_raw"]) != exp:
                    raise AutopilotError(
                        f"Operación {i}: {relp} cambió desde que lo leíste (¿editado en NetBeans?). Vuelve a leerlo.")
                old, new = op.get("old_string"), op.get("new_string")
                if not isinstance(old, str) or not isinstance(new, str) or old == "":
                    raise AutopilotError(f"Operación {i}: edit necesita old_string (no vacío) y new_string.")
                text = cur["text"]
                if "\r\n" in text and "\r\n" not in old:
                    old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
                n = text.count(old)
                if n == 0:
                    raise AutopilotError(f"Operación {i}: old_string no aparece en {relp}.")
                if n > 1 and not op.get("replace_all"):
                    raise AutopilotError(
                        f"Operación {i}: old_string aparece {n} veces en {relp}; añade contexto o usa replace_all.")
                newtext = text.replace(old, new) if op.get("replace_all") else text.replace(old, new, 1)
                self._check_generated(relp, text, newtext)
                if len(newtext.encode("utf-8")) > self.cfg.max_write_bytes:
                    raise AutopilotError(f"Operación {i}: resultado demasiado grande.")
                cur["text"] = newtext
            else:  # delete
                if not exists_now:
                    raise AutopilotError(f"Operación {i}: {relp} no existe.")
                if op.get("confirm") is not True:
                    raise AutopilotError(f"Operación {i}: eliminar requiere confirm=true (se guarda copia).")
                cur["deleted"] = True
            steps.append((kind, relp))
        return [dict(v) for v in state.values()]

    def _check_generated(self, relp: str, old: str, new: str) -> None:
        if relp.lower().endswith(".java") and java_scan.generated_lines(old) != java_scan.generated_lines(new):
            raise SecurityError(
                f"El cambio altera código generado por el diseñador de NetBeans en {relp} "
                "(bloques GEN-BEGIN/GEN-END o GEN-FIRST/GEN-LAST). Modifica la interfaz con el diseñador, "
                "o escribe solo en el código fuera de esas regiones.")

    # ---------- aplicación ----------
    def apply(self, project: str, ops: list[dict], note: str = "") -> dict:
        plan = self._plan(project, ops)
        batch = uuid.uuid4().hex[:10]
        applied: list[dict] = []
        results = []
        try:
            for n, item in enumerate(plan):
                real: Path = item["real"]
                changed = (item["deleted"] and item["existed"]) or (
                    not item["deleted"] and (item["text"] != item["orig_text"] or not item["existed"]))
                if not changed:
                    continue
                cid = f"{batch}-{n}"
                bak = self.store.backup(cid, item["orig_raw"]) if item["existed"] else None
                rec = {"id": cid, "batch": batch, "path": item["rel"], "backup": bak, "real": real,
                       "existed": item["existed"]}
                if item["deleted"]:
                    real.unlink()
                    action, sha_after = "delete", None
                else:
                    data = fileio.encode(item["text"], item["enc"])
                    fileio.write_atomic(real, data)
                    action = "modify" if item["existed"] else "create"
                    sha_after = fileio.sha256_bytes(data)
                rec["action"] = action
                applied.append(rec)
                sha_before = fileio.sha256_bytes(item["orig_raw"]) if item["existed"] else None
                self.store.append({"id": cid, "batch": batch, "project": project, "path": item["rel"],
                                   "action": action, "sha_before": sha_before, "sha_after": sha_after,
                                   "backup": bak, "note": note})
                results.append({"path": item["rel"], "action": action, "change_id": cid,
                                "sha256": sha_after, "backup": bak})
        except Exception:
            for rec in reversed(applied):  # reversión atómica del lote
                if rec["backup"]:
                    fileio.write_atomic(rec["real"], self.store.read_backup(rec["backup"]))
                else:
                    rec["real"].unlink(missing_ok=True)
                self.store.append({"id": rec["id"] + "-rollback", "batch": batch, "project": project,
                                   "path": rec["path"], "action": "rollback"})
            raise
        return {"project": project, "batch": batch, "changes": results,
                "unchanged": len(plan) - len(results)}

    # ---------- reversión ----------
    def revert(self, project: str, batch: str | None = None, change_id: str | None = None,
               force: bool = False) -> dict:
        entries = [e for e in self.store.entries()
                   if e.get("project") == project and e.get("action") in ("create", "modify", "delete")]
        if batch:
            sel = [e for e in entries if e.get("batch") == batch]
        elif change_id:
            sel = [e for e in entries if e.get("id") == change_id]
        else:
            raise AutopilotError("Indica batch o change_id.")
        if not sel:
            raise AutopilotError("No se encontraron cambios con ese identificador en este proyecto.")
        reverted_ids = {e["id"].removesuffix("-revert") for e in self.store.entries()
                        if e.get("action") == "revert"}
        sel = [e for e in sel if e["id"] not in reverted_ids]
        if not sel:
            raise AutopilotError("Esos cambios ya fueron revertidos.")
        done = []
        for e in reversed(sel):
            _, real, relp = self.guard.file_path(project, e["path"])
            self.guard.check_writable(project, relp, "modify")
            cur_sha = fileio.sha256_bytes(real.read_bytes()) if real.is_file() else None
            if cur_sha != e.get("sha_after") and not force:
                raise AutopilotError(
                    f"{relp} fue modificado después de este cambio; revertirlo perdería esos cambios "
                    "(usa force=true si es lo que quieres).")
            if e["action"] == "create":
                real.unlink(missing_ok=True)
            else:
                fileio.write_atomic(real, self.store.read_backup(e["backup"]))
            self.store.append({"id": e["id"] + "-revert", "project": project, "path": e["path"],
                               "action": "revert"})
            done.append({"path": e["path"], "restored_from": e["action"]})
        return {"project": project, "reverted": done}

    # ---------- resumen ----------
    def summary(self, project: str | None, scope: str = "session") -> dict:
        entries = [e for e in self.store.entries()
                   if e.get("action") in ("create", "modify", "delete", "revert", "rollback")]
        if project:
            entries = [e for e in entries if e.get("project") == project]
        if scope == "session":
            entries = [e for e in entries if e.get("session") == fileio.SESSION_ID]
        by: dict[str, dict] = {}
        for e in entries:
            key = f"{e.get('project')}/{e.get('path')}"
            by.setdefault(key, {"project": e.get("project"), "path": e.get("path"), "actions": []})
            by[key]["actions"].append(e["action"])
        created = [k for k, v in by.items() if v["actions"][0] == "create" and v["actions"][-1] != "delete"]
        deleted = [k for k, v in by.items() if v["actions"][-1] == "delete"]
        modified = [k for k in by if k not in created and k not in deleted]
        return {"scope": scope, "session": fileio.SESSION_ID, "created": created, "modified": modified,
                "deleted": deleted, "batches": sorted({e["batch"] for e in entries if e.get("batch")}),
                "total_operations": len(entries)}

    # ---------- validación ----------
    def validate(self, project: str, paths: list[str] | None = None) -> dict:
        pdir = self.guard.project_dir(project)
        if not paths:
            logged = [e for e in self.store.entries()
                      if e.get("project") == project and e.get("session") == fileio.SESSION_ID
                      and e.get("action") in ("create", "modify")]
            paths = sorted({e["path"] for e in logged})
        issues, checked = [], []
        for p in paths:
            try:
                _, real, relp = self.guard.file_path(project, p)
            except SecurityError as e:
                issues.append({"path": p, "severity": "error", "message": f"Fuera del ámbito autorizado: {e}"})
                continue
            if not real.is_file():
                issues.append({"path": relp, "severity": "error", "message": "El archivo no existe."})
                continue
            raw = real.read_bytes()
            ext = real.suffix.lower()
            checked.append(relp)
            try:
                text, _ = fileio.decode(raw)
            except AutopilotError as e:
                issues.append({"path": relp, "severity": "error", "message": str(e)})
                continue
            if ext in (".xml", ".form", ".fxml"):
                try:
                    fileio.parse_xml_safe(raw)
                except Exception as e:  # noqa: BLE001
                    issues.append({"path": relp, "severity": "error", "message": f"XML no válido: {e}"})
            elif ext == ".json":
                try:
                    json.loads(text)
                except ValueError as e:
                    issues.append({"path": relp, "severity": "error", "message": f"JSON no válido: {e}"})
            elif ext == ".java":
                s = java_scan.scan(text)
                if not s["balanced"]:
                    issues.append({"path": relp, "severity": "error",
                                   "message": "Llaves/paréntesis/corchetes desbalanceados."})
                issues += self._java_package_check(pdir, relp, s)
                issues += self._designer_check(project, relp, text)
        # clases duplicadas en el proyecto
        from .projects import scan_java_files, detect_build
        seen: dict[str, list[str]] = {}
        for jf in scan_java_files(pdir, detect_build(pdir)):
            for t in jf["types"]:
                if "." not in t["name"] and Path(jf["path"]).stem == t["name"]:
                    seen.setdefault(f"{jf['package']}.{t['name']}", []).append(jf["path"])
        for fq, files in seen.items():
            if len(files) > 1 and any(f in checked for f in files):
                issues.append({"path": files[0], "severity": "error",
                               "message": f"Clase duplicada {fq} en: {', '.join(files)}"})
        errors = [i for i in issues if i["severity"] == "error"]
        return {"project": project, "valid": not errors, "files_checked": checked, "issues": issues,
                "note": "Validación estática; para comprobar que compila usa compile_project."}

    def _java_package_check(self, pdir: Path, relp: str, s: dict) -> list[dict]:
        for root in ("src/main/java/", "src/test/java/", "src/"):
            if relp.startswith(root):
                sub = relp[len(root):].rsplit("/", 1)
                expected = sub[0].replace("/", ".") if len(sub) == 2 else ""
                if s["package"] != expected:
                    return [{"path": relp, "severity": "warning",
                             "message": f"El paquete declarado '{s['package']}' no coincide con la carpeta '{expected}'."}]
                break
        return []

    def _designer_check(self, project: str, relp: str, text: str) -> list[dict]:
        """Compara las regiones generadas con la copia más antigua guardada de esta sesión."""
        firsts = [e for e in self.store.entries() if e.get("project") == project and e.get("path") == relp
                  and e.get("session") == fileio.SESSION_ID and e.get("backup")]
        if not firsts:
            return []
        orig = fileio.decode(self.store.read_backup(firsts[0]["backup"]))[0]
        if java_scan.generated_lines(orig) != java_scan.generated_lines(text):
            return [{"path": relp, "severity": "error",
                     "message": "Se alteró código generado por el diseñador de NetBeans."}]
        return []
