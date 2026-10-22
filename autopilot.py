#!/usr/bin/env python3
"""NetBeans Autopilot - asistente de instalación y uso (Windows, Linux y macOS).

Solo usa la biblioteca estándar, así que funciona antes de instalar nada.

    python autopilot.py setup      Asistente: comprueba requisitos, instala, elige carpeta y modelo, registra el cliente
    python autopilot.py doctor     Comprueba qué falta (--fix intenta instalarlo)
    python autopilot.py update     Actualiza el servidor (git pull + dependencias)
    python autopilot.py start      Abre tu cliente de IA dentro de la carpeta de proyectos
    python autopilot.py register   Vuelve a registrar el servidor en un cliente
    python autopilot.py status     Resumen de la configuración actual
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IS_WIN = os.name == "nt"
HOME = Path.home()
SERVER_NAME = "netbeans-autopilot"
CONFIG = HERE / "config.json"
VENV = HERE / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
MIN_PY = (3, 10)

# Modelos que se ofrecen en el asistente (el catálogo cambia: siempre se puede escribir otro).
OPENCODE_MODELS = [
    ("opencode/big-pickle", "Big Pickle - gratis, sin tarjeta (recomendado)"),
    ("opencode/grok-code", "Grok Code - gratis, rápido (si está disponible)"),
]

OK, BAD, WARN = "[OK]", "[X] ", "[!] "


# ------------------------------------------------------------------ utilidades
def say(msg: str = "") -> None:
    print(msg, flush=True)


def ask(prompt: str, default: str = "", assume_yes: bool = False) -> str:
    if assume_yes or not sys.stdin.isatty():
        return default
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return val or default


def confirm(prompt: str, default: bool = True, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return default
    d = "S/n" if default else "s/N"
    val = ask(f"{prompt} ({d})").lower()
    return default if not val else val in ("s", "si", "sí", "y", "yes")


def choose(prompt: str, options: list[tuple[str, str]], default_idx: int = 0,
           assume_yes: bool = False) -> str:
    """Menú numerado; devuelve el valor elegido (o el texto escrito si se elige 'otro')."""
    if assume_yes or not sys.stdin.isatty():
        return options[default_idx][0]
    say(prompt)
    for i, (_, label) in enumerate(options, 1):
        say(f"  {i}) {label}")
    say(f"  {len(options) + 1}) Otro (escribirlo)")
    while True:
        raw = ask("Elige un número", str(default_idx + 1))
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        if raw.isdigit() and int(raw) == len(options) + 1:
            return ask("Escríbelo")
        say("  Opción no válida.")


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, **kw)


def capture(cmd: list[str], timeout: int = 20) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def find_exe(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def find_claude() -> str | None:
    p = find_exe("claude")
    if p:
        return p
    for cand in (HOME / ".local/bin/claude.exe", HOME / ".local/bin/claude", HOME / ".claude/local/claude"):
        if cand.exists():
            return str(cand)
    return None


def read_config() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
        except ValueError:
            return {}
    return {}


def write_config(data: dict) -> None:
    CONFIG.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def server_python() -> str:
    return str(VENV_PY if VENV_PY.exists() else sys.executable)


# ------------------------------------------------------------------ requisitos
class Check:
    def __init__(self, name: str, ok: bool, detail: str = "", fix: str = "", required: bool = True,
                 installer: list[str] | None = None):
        self.name, self.ok, self.detail, self.fix = name, ok, detail, fix
        self.required, self.installer = required, installer


def _installer(winget_id: str, apt: str, brew: str) -> list[str] | None:
    """Comando para instalar un paquete con el gestor del sistema (None si no hay gestor)."""
    if IS_WIN and find_exe("winget"):
        return ["winget", "install", "-e", "--id", winget_id, "--accept-package-agreements",
                "--accept-source-agreements"]
    if platform.system() == "Darwin" and find_exe("brew"):
        return ["brew", "install", brew]
    if find_exe("apt-get"):
        return ["sudo", "apt-get", "install", "-y", apt]
    return None


def check_all() -> list[Check]:
    out: list[Check] = []

    v = sys.version_info
    out.append(Check("Python 3.10+", v >= MIN_PY, f"{v.major}.{v.minor}.{v.micro}",
                     "Instala Python 3.10 o superior desde https://www.python.org/downloads/"))

    git = find_exe("git")
    out.append(Check("Git", bool(git), (capture(["git", "--version"]) or "") if git else "",
                     "Instala Git: https://git-scm.com/downloads  (sirve para actualizar el servidor)",
                     required=False, installer=_installer("Git.Git", "git", "git")))

    javac = find_exe("javac")
    jdk_detail, jdk_ok = "", False
    sys.path.insert(0, str(HERE))
    try:
        from netbeans_autopilot.config import _find_java_home   # misma lógica que usa el servidor
        jh = _find_java_home(read_config().get("java_home"))
        if jh:
            jdk_ok, jdk_detail = True, str(jh)
    except Exception:
        if javac:
            jdk_ok, jdk_detail = True, javac
    out.append(Check("JDK (javac)", jdk_ok, jdk_detail,
                     "Instala un JDK 17+ (p. ej. Temurin: https://adoptium.net) o define java_home en config.json",
                     installer=_installer("EclipseAdoptium.Temurin.21.JDK", "openjdk-21-jdk", "openjdk@21")))

    mvn = find_exe("mvn", "mvn.cmd")
    out.append(Check("Maven (opcional)", bool(mvn), mvn or "",
                     "Opcional: solo para proyectos Maven. NetBeans trae el suyo y se detecta solo.",
                     required=False))

    out.append(Check("Entorno virtual (.venv)", VENV_PY.exists(), str(VENV) if VENV_PY.exists() else "",
                     "Se crea con: python autopilot.py setup"))
    mcp_ver = None
    if VENV_PY.exists():
        mcp_ver = capture([str(VENV_PY), "-c", "import importlib.metadata as m; print(m.version('mcp'))"])
    out.append(Check("Paquete MCP", bool(mcp_ver), f"mcp {mcp_ver}" if mcp_ver else "",
                     "Se instala con: python autopilot.py setup (o update)"))

    root = read_config().get("root")
    root_ok = bool(root) and Path(root).is_dir()
    out.append(Check("Carpeta de proyectos", root_ok, root or "",
                     "Se elige en: python autopilot.py setup"))

    has_client = False
    for label, found in clients_detected().items():
        out.append(Check(f"Cliente IA: {label}", found, "instalado" if found else "",
                         "", required=False))
        has_client = has_client or found
    out.append(Check("Al menos un cliente de IA", has_client, "",
                     "Instala OpenCode (gratis): npm i -g opencode-ai   |   o Claude Code / Gemini CLI",
                     installer=None))
    return out


def clients_detected() -> dict[str, bool]:
    return {"OpenCode": bool(find_exe("opencode")), "Claude Code": bool(find_claude()),
            "Gemini CLI": bool(find_exe("gemini"))}


def print_checks(checks: list[Check]) -> bool:
    all_ok = True
    for c in checks:
        mark = OK if c.ok else (BAD if c.required else WARN)
        line = f" {mark} {c.name}"
        if c.detail:
            line += f"  ({c.detail})"
        say(line)
        if not c.ok:
            if c.required:
                all_ok = False
            if c.fix:
                say(f"       -> {c.fix}")
    return all_ok


def fix_missing(checks: list[Check], assume_yes: bool) -> None:
    """Ofrece instalar con winget/apt/brew lo que falta y tiene instalador conocido."""
    for c in checks:
        if c.ok or not c.installer:
            continue
        say(f"\nFalta {c.name}. Comando: {' '.join(c.installer)}")
        if confirm("¿Instalarlo ahora?", True, assume_yes):
            run(c.installer)
            say("  (si se instaló algo nuevo, cierra y abre la terminal para que lo detecte)")
    if not find_exe("node", "npm") and not find_exe("opencode"):
        say("\nPara instalar OpenCode necesitas Node.js 20+ (https://nodejs.org).")


def ensure_venv(upgrade: bool = False) -> bool:
    if not VENV_PY.exists():
        say("Creando entorno virtual...")
        if run([sys.executable, "-m", "venv", str(VENV)]).returncode:
            say(f" {BAD} No se pudo crear el entorno virtual."
                + ("  En Debian/Ubuntu: sudo apt install python3-venv" if not IS_WIN else ""))
            return False
    say("Instalando dependencias (puede tardar un minuto)...")
    cmd = [str(VENV_PY), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
           "-r", str(HERE / "requirements.txt")]
    if upgrade:
        cmd.insert(5, "--upgrade")
    if run(cmd).returncode:
        say(f" {BAD} Falló la instalación de dependencias (¿sin conexión?).")
        return False
    return True


# ------------------------------------------------------------------ registro en clientes
def server_entry() -> dict:
    return {"command": server_python(), "args": ["-m", "netbeans_autopilot"],
            "env": {"PYTHONPATH": str(HERE)}}


def _load_json(path: Path) -> dict | None:
    """None si el archivo existe pero no es JSON puro (p. ej. JSONC): no se toca."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except ValueError:
        return None


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def opencode_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (HOME / ".config"))
    return base / "opencode" / "opencode.json"


def register_opencode(model: str | None) -> bool:
    path = opencode_config_path()
    data = _load_json(path)
    entry = {"type": "local", "command": [server_python(), "-m", "netbeans_autopilot"],
             "environment": {"PYTHONPATH": str(HERE)}, "enabled": True}
    if data is None:
        say(f" {WARN}{path} tiene comentarios o formato especial; no lo modifico. Añade a mano:")
        say(json.dumps({"mcp": {SERVER_NAME: entry}}, indent=2))
        return False
    data.setdefault("$schema", "https://opencode.ai/config.json")
    data.setdefault("mcp", {})[SERVER_NAME] = entry
    if model:
        data["model"] = model
    _save_json(path, data)
    say(f" {OK} Registrado en OpenCode ({path})" + (f"  modelo: {model}" if model else ""))
    return True


def register_gemini() -> bool:
    path = HOME / ".gemini" / "settings.json"
    data = _load_json(path)
    if data is None:
        say(f" {WARN}{path} no es JSON puro; no lo modifico. Añade a mano:")
        say(json.dumps({"mcpServers": {SERVER_NAME: server_entry()}}, indent=2))
        return False
    data.setdefault("mcpServers", {})[SERVER_NAME] = server_entry()
    _save_json(path, data)
    say(f" {OK} Registrado en Gemini CLI ({path}). Compruébalo con /mcp dentro de gemini.")
    return True


def register_claude() -> bool:
    claude = find_claude()
    if not claude:
        say(f" {WARN}No se encontró 'claude'.")
        return False
    run([claude, "mcp", "remove", SERVER_NAME, "--scope", "user"], capture_output=True)
    r = run([claude, "mcp", "add", SERVER_NAME, "--scope", "user", "-e", f"PYTHONPATH={HERE}",
             "--", server_python(), "-m", "netbeans_autopilot"])
    if r.returncode:
        say(f" {BAD} No se pudo registrar en Claude Code.")
        return False
    say(f" {OK} Registrado en Claude Code. Abre una sesión NUEVA para usarlo.")
    return True


def register_generic() -> bool:
    out = HERE / "mcp.config.generated.json"
    out.write_text(json.dumps({"mcpServers": {SERVER_NAME: server_entry()}}, indent=2) + "\n", encoding="utf-8")
    say(f" {OK} Escrito {out.name}: copia su contenido a la configuración MCP de tu herramienta.")
    return True


CLIENTS = {"opencode": "OpenCode (gratis, recomendado)", "claude": "Claude Code",
           "gemini": "Gemini CLI", "other": "Otro cliente MCP (genera el JSON)"}


def pick_client(requested: str | None, assume_yes: bool) -> str:
    if requested:
        return requested
    det = clients_detected()
    default = "opencode" if det["OpenCode"] else "claude" if det["Claude Code"] else \
        "gemini" if det["Gemini CLI"] else "opencode"
    options = [(k, v + ("  [detectado]" if det.get({"opencode": "OpenCode", "claude": "Claude Code",
                                                    "gemini": "Gemini CLI"}.get(k, ""), False) else ""))
               for k, v in CLIENTS.items()]
    return choose("\n¿Con qué herramienta de IA vas a usarlo?", options,
                  [k for k, _ in options].index(default), assume_yes)


def pick_model(client: str, requested: str | None, assume_yes: bool) -> str | None:
    if requested:
        return requested
    if client != "opencode":
        return None   # Claude y Gemini eligen el modelo con su propia cuenta/comando
    say("\nModelo para OpenCode (los gratuitos cambian con el tiempo; puedes cambiarlo luego con /models).")
    return choose("Elige modelo:", OPENCODE_MODELS, 0, assume_yes) or None


def register(client: str, model: str | None) -> bool:
    return {"opencode": lambda: register_opencode(model), "claude": register_claude,
            "gemini": register_gemini, "other": register_generic}[client]()


def default_projects_dir() -> str:
    for rel in ("Documents/NetBeansProjects", "NetBeansProjects", "IdeaProjects", "eclipse-workspace",
                "Documents/IdeaProjects", "Documents/eclipse-workspace", "Projects", "proyectos"):
        if (HOME / rel).is_dir():
            return str(HOME / rel)
    return str(HOME / "Documents" / "NetBeansProjects")


# ------------------------------------------------------------------ comandos
def cmd_doctor(args) -> int:
    say(f"NetBeans Autopilot - diagnóstico ({platform.system()} {platform.release()})\n")
    checks = check_all()
    ok = print_checks(checks)
    if not ok and args.fix:
        fix_missing(checks, args.yes)
        say("\nVuelvo a comprobar:\n")
        ok = print_checks(check_all())
    say("\n" + ("Todo listo." if ok else "Faltan requisitos. Usa 'python autopilot.py doctor --fix' o 'setup'."))
    return 0 if ok else 1


def cmd_setup(args) -> int:
    say("=== NetBeans Autopilot: asistente de instalación ===\n")
    say("Paso 1/5 - Requisitos")
    checks = [c for c in check_all() if c.name in ("Python 3.10+", "Git", "JDK (javac)", "Maven (opcional)")]
    print_checks(checks)
    if sys.version_info < MIN_PY:
        say("\nPython es demasiado antiguo; instálalo y repite.")
        return 1
    missing = [c for c in checks if not c.ok and c.installer]
    if missing:
        fix_missing(missing, args.yes)

    say("\nPaso 2/5 - Instalación del servidor")
    if not ensure_venv():
        return 1
    say(f" {OK} Entorno listo")

    say("\nPaso 3/5 - Carpeta de proyectos")
    cfg = read_config()
    current = args.root or cfg.get("root") or default_projects_dir()
    root = args.root or ask("Carpeta donde están tus proyectos (el servidor solo podrá tocar esta)",
                            current, args.yes)
    root_path = Path(os.path.expanduser(root)).resolve()
    if not root_path.is_dir():
        if confirm(f"La carpeta {root_path} no existe. ¿Crearla?", True, args.yes):
            root_path.mkdir(parents=True, exist_ok=True)
        else:
            say("Sin carpeta de proyectos no se puede continuar.")
            return 1
    cfg["root"] = str(root_path)
    cfg.setdefault("protected_globs", [])
    cfg.setdefault("read_only_projects", [])
    write_config(cfg)
    say(f" {OK} root = {root_path}")

    say("\nPaso 4/5 - Herramienta de IA y modelo")
    client = pick_client(args.client, args.yes)
    if client == "opencode" and not find_exe("opencode"):
        say(f" {WARN}OpenCode no está instalado. Instálalo con (necesita Node.js 20+):")
        say("       npm install -g opencode-ai")
    model = pick_model(client, args.model, args.yes)
    register(client, model)
    cfg["client"] = client
    if model:
        cfg["model"] = model
    write_config(cfg)

    say("\nPaso 5/5 - Comprobación")
    if not args.skip_tests:
        r = run([str(VENV_PY), "-m", "unittest", "tests.test_acceptance", "tests.test_mcp_protocol"],
                cwd=str(HERE), capture_output=True)
        say(f" {OK if r.returncode == 0 else BAD} Pruebas del servidor"
            + ("" if r.returncode == 0 else "  (ejecuta 'python -m unittest' para ver el detalle)"))
    say("\nListo. Para empezar:\n")
    say("   python autopilot.py start\n")
    say("y dile a la IA, por ejemplo: «Llama a get_environment y list_projects».")
    say("Para actualizar más adelante: python autopilot.py update")
    return 0


def cmd_update(args) -> int:
    if not find_exe("git") or not (HERE / ".git").exists():
        say(f" {BAD} Esta carpeta no es un clon de git; no puedo actualizar solo. Clona de nuevo el repositorio.")
        return 1
    before = capture(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"]) or "?"
    r = run(["git", "-C", str(HERE), "pull", "--ff-only"])
    if r.returncode:
        say(f" {BAD} git pull falló (¿cambios locales o sin conexión?).")
        return 1
    after = capture(["git", "-C", str(HERE), "rev-parse", "--short", "HEAD"]) or "?"
    say(f" {OK} {'Ya estabas al día' if before == after else f'Actualizado {before} -> {after}'}")
    if not ensure_venv(upgrade=True):
        return 1
    cfg = read_config()
    if cfg.get("client"):
        register(cfg["client"], None)   # rutas por si la carpeta se movió; no toca el modelo
    return 0


def cmd_register(args) -> int:
    client = pick_client(args.client, args.yes)
    return 0 if register(client, pick_model(client, args.model, args.yes)) else 1


def cmd_status(args) -> int:
    cfg = read_config()
    say(f"Servidor:   {HERE}")
    say(f"Proyectos:  {cfg.get('root', '(sin configurar)')}")
    say(f"Cliente:    {cfg.get('client', '(sin configurar)')}   Modelo: {cfg.get('model', '-')}")
    if (HERE / ".git").exists():
        say(f"Versión:    {capture(['git', '-C', str(HERE), 'log', '-1', '--format=%h %s']) or '?'}")
    return 0


def cmd_start(args) -> int:
    cfg = read_config()
    root = cfg.get("root")
    if not root or not Path(root).is_dir():
        say("Aún no hay carpeta de proyectos. Ejecuta primero: python autopilot.py setup")
        return 1
    client = args.client or cfg.get("client") or "opencode"
    exe = {"opencode": find_exe("opencode"), "claude": find_claude(), "gemini": find_exe("gemini")}.get(client)
    if not exe:
        say(f"No encuentro '{client}'. Instálalo o elige otro cliente con: python autopilot.py register")
        return 1
    cmd = [exe]
    model = args.model or (cfg.get("model") if client == "opencode" else None)
    if client == "opencode" and model:
        cmd += ["--model", model]
    say(f"Abriendo {client} en {root} ...")
    return run(cmd, cwd=root).returncode


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser(prog="autopilot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        p.add_argument("-y", "--yes", action="store_true", help="no preguntar; usar valores por defecto")
        return p

    add("doctor", cmd_doctor, "comprobar requisitos").add_argument("--fix", action="store_true",
                                                                    help="ofrecer instalar lo que falte")
    s = add("setup", cmd_setup, "asistente de instalación")
    s.add_argument("--root", help="carpeta de proyectos")
    s.add_argument("--client", choices=list(CLIENTS))
    s.add_argument("--model", help="p. ej. opencode/big-pickle")
    s.add_argument("--skip-tests", action="store_true")
    add("update", cmd_update, "actualizar el servidor")
    r = add("register", cmd_register, "registrar en un cliente")
    r.add_argument("--client", choices=list(CLIENTS))
    r.add_argument("--model")
    add("status", cmd_status, "ver configuración")
    st = add("start", cmd_start, "abrir el cliente de IA en tu carpeta de proyectos")
    st.add_argument("--client", choices=["opencode", "claude", "gemini"])
    st.add_argument("--model")

    args = ap.parse_args()
    if not args.cmd:
        # Sin argumentos: asistente si no está configurado, estado si ya lo está.
        args = ap.parse_args(["setup"] if not CONFIG.exists() else ["status"])
    return args.fn(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
