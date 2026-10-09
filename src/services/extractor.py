"""Extracción segura de los archivos comprimidos de Blender.

Soportamos .tar.xz / .tar.gz / .tar.bz2 (Linux y macOS) y .zip (Windows)
con la biblioteca estándar. Ninguna entrada puede salirse de la carpeta
destino (ataque "zip slip"): en los tar lo impide el filtro ``data`` de
``tarfile`` y en los zip lo comprobamos nosotros antes de extraer.
"""

import tarfile
import zipfile
from pathlib import Path

ARCHIVE_SUFFIXES = (".tar.xz", ".tar.gz", ".tar.bz2", ".tgz", ".tbz2", ".zip")


def is_archive(path) -> bool:
    """True si el fichero parece un comprimido que sabemos abrir."""
    name = str(path).lower()
    return name.endswith(".zip") or any(name.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


def _within(base: Path, target: Path) -> bool:
    """Comprueba que ``target`` está dentro de ``base``."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_extract_tar(archive: tarfile.TarFile, dest: Path) -> None:
    """Extrae un tar con el filtro ``data`` de la biblioteca estándar.

    El filtro ``data`` (PEP 706) ya hace todo lo que hace falta y lo hace
    bien: rechaza rutas absolutas, rutas con ``..`` que salgan del destino y
    enlaces simbólicos **o duros** que apunten fuera, y además quita bits
    peligrosos (setuid) y no crea dispositivos. Antes había una comprobación
    propia delante que sobraba por dos motivos:

    * llamaba a ``getmembers()``, que lee el archivo entero para listarlo; un
      flujo ``xz`` no se puede rebobinar, así que el ``.tar.xz`` de Blender
      (unos 300 MB) se descomprimía **dos veces**;
    * y resolvía mal los enlaces duros: su ``linkname`` es relativo a la raíz
      del tar, no a la carpeta del miembro, así que dejaba pasar alguno que
      salía fuera (lo paraba el filtro, que era quien de verdad protegía).

    Se pide ``data`` explícitamente porque Python 3.12/3.13 usan
    ``fully_trusted`` por defecto y 3.14 ya ``data``: así el comportamiento no
    cambia según con qué Python se empaquete. El error del filtro se traduce a
    ``ValueError``, el mismo que da el zip, para que quien llame vea un único
    tipo de "archivo peligroso".

    Si el miembro peligroso no es el primero, los anteriores ya se habrán
    escrito (dentro del destino, que es lo que importa): es el precio de leer
    de una sola pasada.
    """
    try:
        archive.extractall(dest, filter="data")
    except tarfile.FilterError as error:
        raise ValueError(f"unsafe member in archive: {error}") from error


def _safe_extract_zip(archive: zipfile.ZipFile, dest: Path) -> None:
    for member in archive.infolist():
        target = dest / member.filename
        if not _within(dest, target):
            raise ValueError("unsafe path in archive: " + member.filename)
    archive.extractall(dest)


def extract(archive_path, dest_folder) -> Path:
    """Extrae el archivo y devuelve la carpeta creada (o la destino si hubo varias)."""
    archive = Path(archive_path)
    dest = Path(dest_folder).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    before = {entry.name for entry in dest.iterdir() if entry.is_dir()}

    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            _safe_extract_zip(zf, dest)
    else:
        # "r:*" detecta automáticamente la compresión (xz, gz, bz2...).
        with tarfile.open(archive, "r:*") as tf:
            _safe_extract_tar(tf, dest)

    after = {entry.name for entry in dest.iterdir() if entry.is_dir()}
    created = after - before
    # Si solo apareció una carpeta nueva, es la de Blender.
    if len(created) == 1:
        return dest / created.pop()
    return dest
