"""Prepara el envío de un falso positivo de antivirus a Microsoft (WDSI).

Windows Defender marca de vez en cuando un `.exe` de PyInstaller **sin firmar**
como si fuera un virus (típico ``Trojan:Win32/Wacatac.B!ml``). Es un falso
positivo, y el problema es del empaquetado, no del código. La única vía gratuita
y sin registro de que Microsoft lo saque de las definiciones es **enviarle la
muestra** al portal WDSI marcándola como *Clean (false positive)*; suele tardar
uno o dos días.

Este script calcula el SHA-256 (lo que identifica la muestra), imprime los datos
que pide el formulario y abre el portal. Subir el fichero y enviarlo hay que
hacerlo a mano: el portal no tiene API.

Uso:
    python packaging/report_false_positive.py dist/BlendTrack.exe
    python packaging/report_false_positive.py BlendTrack-windows-x86_64.zip

Sin argumentos usa ``dist/BlendTrack.exe`` (el que deja PyInstaller).
"""

import hashlib
import sys
import webbrowser
from pathlib import Path

WDSI_URL = "https://www.microsoft.com/en-us/wdsi/filesubmission"
DEFAULT_TARGET = Path("dist") / "BlendTrack.exe"


def sha256(path: Path) -> str:
    """SHA-256 del fichero, leído a trozos para no cargarlo entero en memoria."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv) -> int:
    target = Path(argv[1]).expanduser() if len(argv) > 1 else DEFAULT_TARGET
    if not target.is_file():
        print(f"No existe {target}.")
        print("Compila el binario o pásale la ruta del .exe (o del .zip).")
        return 1

    print(f"Fichero : {target}")
    print(f"Tamaño  : {target.stat().st_size} bytes")
    print(f"SHA-256 : {sha256(target)}")
    print()
    print("En el portal (se abre en el navegador):")
    print("  1. Sube este fichero.")
    print("  2. Elige 'Clean (false positive)'.")
    print("  3. Si te pide el nombre de la detección, cópialo del aviso de")
    print("     Defender (p. ej. Trojan:Win32/Wacatac.B!ml).")
    print(f"  {WDSI_URL}")
    try:
        webbrowser.open(WDSI_URL)
    except Exception:  # sin navegador (servidor, CI): al menos queda la URL
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
