"""Extracción segura de los archivos comprimidos de Blender.

Soportamos .tar.xz / .tar.gz / .tar.bz2 (Linux y macOS) y .zip (Windows)
con la biblioteca estándar. Ninguna entrada puede salirse de la carpeta
destino (ataque "zip slip"): en los tar lo impide el filtro ``data`` de
``tarfile`` y en los zip lo comprobamos nosotros antes de extraer.
"""

import tarfile
import zipfile
from pathlib import Path, PurePosixPath

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


def _safe_extract_tar(archive: tarfile.TarFile, dest: Path) -> list[str]:
    """Extrae un tar con el filtro ``data`` y devuelve los nombres extraídos.

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

    Los nombres se apuntan **mientras** se extrae (``extractall`` acepta
    cualquier iterable de miembros), no con un listado previo, por lo mismo:
    pedirlos antes costaría otra descompresión entera.
    """
    names = []

    def members():
        for member in archive:
            names.append(member.name)
            yield member

    try:
        archive.extractall(dest, members=members(), filter="data")
    except tarfile.FilterError as error:
        raise ValueError(f"unsafe member in archive: {error}") from error
    return names


def _safe_extract_zip(archive: zipfile.ZipFile, dest: Path) -> list[str]:
    """Extrae un zip tras comprobar que nada sale del destino ("zip slip").

    Aquí sí se valida antes: el índice de un zip está al final del fichero y
    listarlo no obliga a descomprimir nada. Devuelve los nombres extraídos.
    """
    names = archive.namelist()
    for name in names:
        if not _within(dest, dest / name):
            raise ValueError("unsafe path in archive: " + name)
    archive.extractall(dest)
    return names


def _top_level(names) -> set[str]:
    """Nombres de primer nivel de un archivo (``a/b/c`` -> ``a``).

    Se ignoran ``./`` y la barra inicial, que algunos empaquetadores ponen
    delante de todo y que el filtro ``data`` también quita al extraer.
    """
    tops = set()
    for name in names:
        parts = [part for part in
                 PurePosixPath(name.replace("\\", "/").lstrip("/")).parts
                 if part != "."]
        if parts:
            tops.add(parts[0])
    return tops


def extract(archive_path, dest_folder) -> Path:
    """Extrae el archivo y devuelve la carpeta que contiene lo extraído.

    Las versiones de Blender traen todo dentro de una única carpeta
    (``blender-5.2.1-linux-x64/...``): entonces se devuelve esa carpeta. Si el
    archivo trae varias cosas sueltas en la raíz, se devuelve el destino.

    La carpeta se deduce **de los nombres del propio archivo**, no comparando
    el destino antes y después: así se acierta también cuando esa carpeta ya
    existía (reinstalar encima). Con la comparación, una carpeta que "no era
    nueva" hacía devolver el destino, y quien llama escribía el marcador de la
    instalación en la carpeta padre en vez de en la de Blender.
    """
    archive = Path(archive_path)
    dest = Path(dest_folder).expanduser()
    dest.mkdir(parents=True, exist_ok=True)

    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as zf:
            names = _safe_extract_zip(zf, dest)
    else:
        # "r:*" detecta automáticamente la compresión (xz, gz, bz2...).
        with tarfile.open(archive, "r:*") as tf:
            names = _safe_extract_tar(tf, dest)

    tops = _top_level(names)
    if len(tops) == 1:
        folder = dest / tops.pop()
        if folder.is_dir():
            return folder
    return dest
