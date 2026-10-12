#!/usr/bin/env bash
# Compila el binario portable para la plataforma actual.
#
# Uso:  packaging/build.sh [--appimage]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

python3 -m pip install -r requirements-build.txt
pyinstaller --clean --noconfirm packaging/blendtrack.spec --distpath dist --workpath build

if [ "${1:-}" = "--appimage" ]; then
    packaging/build_appimage.sh dist
fi

echo "Binario listo en dist/BlendTrack"
