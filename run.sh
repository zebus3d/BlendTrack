#!/bin/sh
# Lanza BlendTrack en desarrollo usando el venv del proyecto.
#
#   ./run.sh                 -> abre la interfaz
#   ./run.sh --smoke         -> lista builds por consola
#   ./run.sh --screenshot X  -> captura y sale
#
# Si falta el venv, lo crea e instala las dependencias.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

if [ ! -x .venv/bin/python ]; then
    echo "Creando entorno virtual (.venv)..."
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
fi

# Silencia el aviso del portal de escritorio: Qt intenta registrarse con el
# xdg-desktop-portal usando el App ID ("blendtrack") y, al correr desde el
# código fuente, no hay un blendtrack.desktop instalado en el sistema. Es
# inofensivo (la app funciona igual), pero ensucia el arranque. En el AppImage
# el .desktop va dentro (packaging/blendtrack.desktop).
QT_LOGGING_RULES="qt.qpa.services=false${QT_LOGGING_RULES:+;$QT_LOGGING_RULES}"
export QT_LOGGING_RULES

exec .venv/bin/python src/main.py "$@"
