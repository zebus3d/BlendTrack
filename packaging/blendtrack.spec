# -*- mode: python ; coding: utf-8 -*-
"""Especificación de PyInstaller para empaquetar BlendTrack.

En Linux y macOS se genera un paquete "one-folder" (o un bundle `.app`), porque
es más rápido de arrancar y más fácil de depurar que `--onefile`.

En Windows, en cambio, se genera un **único `.exe`** (`--onefile`): todo va
embebido y PyInstaller lo descomprime en `%TEMP%` al arrancar. Se hace así porque
el reparto es un `.zip` que la gente baja a mano y el error clásico es
descomprimir solo el `.exe`, borrar la carpeta `_internal` y encontrarse con
``Failed to load Python DLL ... python312.dll``. Con un solo fichero no hay nada
que perder. El precio es un arranque más lento (descomprime ~100 MB cada vez) y
algo más de ruido con los antivirus heurísticos.

Uso:
    pyinstaller --clean --noconfirm packaging/blendtrack.spec \
        --distpath dist --workpath build
"""

import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
SRC = ROOT / "src"

# La versión vive en src/version.py; el CI la reescribe desde el tag antes de
# compilar (packaging/inject_version.py) y genera el recurso de versión de
# Windows a partir de ella.
_version_match = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (SRC / "version.py").read_text(encoding="utf-8"),
)
APP_VERSION = _version_match.group(1) if _version_match else "0.0.0"
VERSION_INFO = ROOT / "packaging" / "version_info.txt"

# Iconos del .exe (Windows) y del .app (macOS). Los genera
# `packaging/make_icons.py` a partir de src/assets/images/app_icon.png y se
# dejan en el repo para no depender de Qt antes de empaquetar. El AppImage no
# usa estos: ver build_appimage.sh (.DirIcon + .desktop).
ICONS = ROOT / "packaging" / "icons"
ICON_ICO = ICONS / "blendtrack.ico"
ICON_ICNS = ICONS / "blendtrack.icns"

block_cipher = None

datas = [
    (str(SRC / "assets"), "assets"),
    # Las traducciones son ficheros, no código: sin esto el binario
    # saldría siempre en inglés (``i18n`` las busca en RESOURCE_DIR).
    (str(SRC / "locale"), "locale"),
]

# Módulos de Qt que NO usamos. PySide6-Essentials trae muchos; excluirlos evita
# que PyInstaller arrastre ~100 MB de más (WebEngine, QML, Multimedia...).
# Solo usamos QtCore, QtGui y QtWidgets.
QT_EXCLUDES = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtPositioning", "PySide6.QtSerialPort", "PySide6.QtSensors",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech", "PySide6.QtHttpServer",
    # OJO: NO excluir shiboken6 ni shiboken6.Shiboken. PySide6 los necesita
    # para arrancar ("No module named 'shiboken6.Shiboken'").
]

a = Analysis(
    [str(SRC / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=QT_EXCLUDES,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Opciones comunes del ejecutable (recurso de versión e icono solo aplican en
# Windows; en el resto de plataformas PyInstaller las ignora).
_EXE_OPTIONS = dict(
    name="BlendTrack",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX puede romper librerías nativas (Qt sobre todo), así que lo desactivamos.
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(VERSION_INFO) if VERSION_INFO.is_file() else None,
    icon=str(ICON_ICO) if ICON_ICO.is_file() else None,
)

if sys.platform == "win32":
    # Onefile: Python, Qt y todas las DLL van dentro del .exe. Un solo fichero
    # que no se puede romper borrando `_internal` (ver la cabecera del spec).
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **_EXE_OPTIONS)
else:
    # One-folder: el .exe y la carpeta `_internal` al lado.
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **_EXE_OPTIONS)
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="BlendTrack",
    )

    if sys.platform == "darwin":
        app = BUNDLE(
            coll,
            name="BlendTrack.app",
            icon=str(ICON_ICNS) if ICON_ICNS.is_file() else None,
            bundle_identifier="org.zebus3d.blendtrack",
            info_plist={
                "NSHighResolutionCapable": True,
                "CFBundleShortVersionString": APP_VERSION,
                "CFBundleVersion": APP_VERSION,
            },
        )
