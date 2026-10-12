#!/usr/bin/env python3
"""Inyecta la versión en el código y en el recurso de Windows.

Escribe ``src/version.py`` (que la aplicación importa) y
``packaging/version_info.txt`` (formato VSVersionInfo que usa PyInstaller para
el recurso de versión del .exe). Lo llama el flujo de CI a partir del tag.

Uso:
    python packaging/inject_version.py 1.2.3
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "src" / "version.py"
INFO_FILE = ROOT / "packaging" / "version_info.txt"


def _numbers(version: str):
    """Convierte '1.2.3' en la tupla de 4 enteros que espera el recurso."""
    parts = version.split(".")
    numbers = []
    for part in (parts + ["0", "0", "0", "0"])[:4]:
        digits = re.findall(r"\d+", part)
        numbers.append(int(digits[0]) if digits else 0)
    return tuple(numbers)


def build_version_info(version: str) -> str:
    major, minor, patch, build = _numbers(version)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {build}),
    prodvers=({major}, {minor}, {patch}, {build}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          "040904B0",
          [
            StringStruct("CompanyName", "zebus3d"),
            StringStruct("FileDescription", "Blender Downloads Manager"),
            StringStruct("FileVersion", "{version}"),
            StringStruct("InternalName", "BlendTrack"),
            StringStruct("OriginalFilename", "BlendTrack.exe"),
            StringStruct("ProductName", "BlendTrack"),
            StringStruct("ProductVersion", "{version}")
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct("Translation", [1033, 1200])])
  ]
)
"""


def main() -> None:
    version = (sys.argv[1] if len(sys.argv) > 1 else "0.0.0").lstrip("vV") or "0.0.0"
    VERSION_FILE.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    INFO_FILE.write_text(build_version_info(version), encoding="utf-8")
    print(f"version {version} injected into {VERSION_FILE} and {INFO_FILE}")


if __name__ == "__main__":
    main()
