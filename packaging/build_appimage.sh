#!/usr/bin/env bash
# Construye un AppImage a partir del paquete "one-folder" de PyInstaller.
#
# Uso:  packaging/build_appimage.sh [directorio_dist]
#
# Requisitos: haber ejecutado antes PyInstaller (ver packaging/build.sh) y
# tener wget. appimagetool se descarga automáticamente.
set -euo pipefail

DIST="${1:-dist}"
PACKAGING_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="BlendTrack"
APPDIR="${DIST}/${APP_NAME}.AppDir"

if [ ! -d "${DIST}/${APP_NAME}" ]; then
    echo "No existe ${DIST}/${APP_NAME}. Ejecuta antes PyInstaller." >&2
    exit 1
fi

rm -rf "${APPDIR}"
mkdir -p "${APPDIR}/usr/bin"
cp -r "${DIST}/${APP_NAME}/." "${APPDIR}/usr/bin/"

cp "${PACKAGING_DIR}/blendtrack.desktop" "${APPDIR}/blendtrack.desktop"
cp "${PACKAGING_DIR}/../src/assets/images/app_icon.png" "${APPDIR}/blendtrack.png"
# El .DirIcon es lo que ensena el gestor de ficheros para el AppImage suelto.
# appimagetool ya lo crea (como symlink) a partir del Icon= del .desktop, pero
# lo dejamos explicito y como fichero de verdad: es la pieza que se mira cuando
# alguien dice "el AppImage sale sin icono".
cp "${APPDIR}/blendtrack.png" "${APPDIR}/.DirIcon"

cat > "${APPDIR}/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/BlendTrack" "$@"
EOF
chmod +x "${APPDIR}/AppRun"

TOOL="${DIST}/appimagetool-x86_64.AppImage"
if [ ! -f "${TOOL}" ]; then
    echo "Descargando appimagetool..."
    wget -q -O "${TOOL}" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "${TOOL}"
fi

OUTPUT="${DIST}/${APP_NAME}-x86_64.AppImage"
# APPIMAGE_EXTRACT_AND_RUN evita depender de FUSE (habitual en contenedores/CI).
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "${TOOL}" "${APPDIR}" "${OUTPUT}"
echo "AppImage creado en ${OUTPUT}"
