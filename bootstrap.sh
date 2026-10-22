#!/usr/bin/env sh
# Instalador de una linea para Linux y macOS:
#   curl -fsSL https://raw.githubusercontent.com/J4NDR0S34N/autopilot-java/main/bootstrap.sh | sh
# Comprueba git y python3, descarga NetBeans Autopilot en ~/autopilot-java y lanza el asistente.
set -e
REPO_URL="https://github.com/J4NDR0S34N/autopilot-java.git"
DEST="$HOME/autopilot-java"

need() {
  command -v "$1" >/dev/null 2>&1 && return 0
  echo "Falta '$1'. Instalalo y repite:"
  if command -v apt-get >/dev/null 2>&1; then echo "  sudo apt-get install -y $2"
  elif command -v brew >/dev/null 2>&1; then echo "  brew install $2"
  elif command -v dnf >/dev/null 2>&1; then echo "  sudo dnf install -y $2"
  else echo "  (usa el gestor de paquetes de tu sistema)"; fi
  exit 1
}
need git git
need python3 python3
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' || { echo "Se necesita Python 3.10 o superior."; exit 1; }
python3 -c 'import venv, ensurepip' 2>/dev/null || { echo "Falta el modulo venv: sudo apt-get install -y python3-venv"; exit 1; }

if [ -d "$DEST/.git" ]; then
  echo "Actualizando $DEST ..."
  git -C "$DEST" pull --ff-only
else
  echo "Descargando en $DEST ..."
  git clone "$REPO_URL" "$DEST"
fi
cd "$DEST"
# `curl | sh` ocupa la entrada estandar: reconecta el teclado para las preguntas del asistente.
if [ -r /dev/tty ]; then exec python3 autopilot.py setup </dev/tty; else exec python3 autopilot.py setup -y; fi
