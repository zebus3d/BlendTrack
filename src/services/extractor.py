"""Extracción segura de los archivos comprimidos de Blender.

Soportamos .tar.xz / .tar.gz / .tar.bz2 (Linux y macOS) y .zip (Windows)
con la biblioteca estándar. Ninguna entrada puede salirse de la carpeta
destino (ataque "zip slip"): en los tar lo impide el filtro ``data`` de
``tarfile`` y en los zip lo comprobamos nosotros antes de extraer.

Extraer una versión de Blender tarda: el ``.tar.xz`` de Linux son más de
1 GB descomprimidos en un solo hilo, y el ``.zip`` de Windows son miles de
ficheros que el antivirus revisa uno a uno. Por eso ``extract`` avisa del
progreso y se puede cancelar: sin eso, la barra se quedaba clavada en el 100 %
de la descarga durante minutos y el usuario creía que la app se había colgado.
"""

import shutil
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ARCHIVE_SUFFIXES = (".tar.xz", ".tar.gz", ".tar.bz2", ".tgz", ".tbz2", ".zip")


def is_archive(path) -> bool:
    """True si el fichero parece un comprimido que sabemos abrir."""
    name = str(path).lower()
    return name.endswith(".zip") or any(name.endswith(suffix) for suffix in ARCHIVE_SUFFIXES)


class ExtractCancelled(Exception):
    """El usuario canceló a mitad de extracción (lo extraído ya se ha borrado)."""


class _Progress:
    """Reparte el progreso y comprueba la cancelación entre miembro y miembro.

    Un Blender trae decenas de miles de ficheros; avisar por cada uno serían
    decenas de miles de señales hacia la interfaz para mover una barra de 100
    posiciones. Solo se avisa cuando cambia el porcentaje entero.
    """

    def __init__(self, total: int, on_progress=None, cancel=None):
        self.total = max(total, 1)
        self.on_progress = on_progress
        self.cancel = cancel
        self._last = -1

    def step(self, done: int) -> None:
        """Avisa de ``done`` (si cambió el porcentaje) o corta si cancelaron."""
        if self.cancel is not None and self.cancel.is_set():
            raise ExtractCancelled()
        percent = min(done * 100 // self.total, 100)
        if self.on_progress and percent != self._last:
            self._last = percent
            self.on_progress(min(done, self.total), self.total)


def _top_of(name: str) -> str:
    """Primer nivel de un nombre del archivo (``a/b/c`` -> ``a``; ``""`` si no hay)."""
    parts = [part for part in
             PurePosixPath(name.replace("\\", "/").lstrip("/")).parts
             if part != "."]
    return parts[0] if parts else ""


def _within(base: Path, target: Path) -> bool:
    """Comprueba que ``target`` está dentro de ``base``."""
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _safe_extract_tar(archive: tarfile.TarFile, dest: Path, raw,
                      progress: _Progress, created: list[Path]) -> list[str]:
    """Extrae un tar con el filtro ``data`` y devuelve los nombres extraídos.

    El filtro ``data`` (PEP 706) ya hace todo lo que hace falta y lo hace
    bien: rechaza rutas con ``..`` que salgan del destino y enlaces
    simbólicos **o duros** que apunten fuera, a las rutas absolutas les quita
    la barra inicial (así caen dentro del destino), y además quita bits
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

    El progreso sale de lo leído del fichero **comprimido** (``raw.tell()``):
    es lo único cuyo total se conoce sin recorrer el archivo entero. Y cada
    carpeta de primer nivel que aún no existía se apunta en ``created`` antes
    de extraer nada en ella, para poder borrarla si se cancela o falla.
    """
    names = []

    def members():
        for member in archive:
            progress.step(raw.tell())
            names.append(member.name)
            top = _top_of(member.name)
            if top and (dest / top) not in created and not (dest / top).exists():
                created.append(dest / top)
            yield member

    try:
        archive.extractall(dest, members=members(), filter="data")
    except tarfile.FilterError as error:
        raise ValueError(f"unsafe member in archive: {error}") from error
    return names


def _safe_extract_zip(archive: zipfile.ZipFile, dest: Path,
                      progress: _Progress, created: list[Path]) -> list[str]:
    """Extrae un zip tras comprobar que nada sale del destino ("zip slip").

    Aquí sí se valida antes: el índice de un zip está al final del fichero y
    listarlo no obliga a descomprimir nada. Se extrae miembro a miembro (no con
    ``extractall``) para poder dar progreso y atender una cancelación; el
    progreso son los bytes comprimidos ya procesados. Devuelve los nombres.
    """
    infos = archive.infolist()
    names = [info.filename for info in infos]
    for name in names:
        if not _within(dest, dest / name):
            raise ValueError("unsafe path in archive: " + name)
    for top in sorted({_top_of(name) for name in names} - {""}):
        if not (dest / top).exists():
            created.append(dest / top)
    progress.total = max(sum(info.compress_size for info in infos), 1)
    done = 0
    for info in infos:
        progress.step(done)
        archive.extract(info, dest)
        done += info.compress_size
    progress.step(done)
    return names


def _top_level(names) -> set[str]:
    """Nombres de primer nivel de un archivo (``a/b/c`` -> ``a``).

    Se ignoran ``./`` y la barra inicial, que algunos empaquetadores ponen
    delante de todo y que el filtro ``data`` también quita al extraer.
    """
    return {top for top in map(_top_of, names) if top}


def _remove(paths) -> None:
    """Borra lo que creó una extracción a medias (y nada más).

    Solo llegan aquí las carpetas de primer nivel que **no existían** antes de
    empezar: si se reinstalaba encima de una versión, esa no se toca. Sin esta
    limpieza, una extracción cancelada dejaba una carpeta de Blender incompleta
    que luego aparecía como instalada.
    """
    for path in paths:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def extract(archive_path, dest_folder, on_progress=None, cancel=None) -> Path:
    """Extrae el archivo y devuelve la carpeta que contiene lo extraído.

    Las versiones de Blender traen todo dentro de una única carpeta
    (``blender-5.2.1-linux-x64/...``): entonces se devuelve esa carpeta. Si el
    archivo trae varias cosas sueltas en la raíz, se devuelve el destino.

    La carpeta se deduce **de los nombres del propio archivo**, no comparando
    el destino antes y después: así se acierta también cuando esa carpeta ya
    existía (reinstalar encima). Con la comparación, una carpeta que "no era
    nueva" hacía devolver el destino, y quien llama escribía el marcador de la
    instalación en la carpeta padre en vez de en la de Blender.

    ``on_progress(hecho, total)`` se llama desde este mismo hilo según avanza.
    ``cancel`` es cualquier objeto con ``is_set()`` (un ``threading.Event``):
    si se enciende, se borra lo que se llevaba extraído y se lanza
    ``ExtractCancelled``. Lo mismo se borra si la extracción falla (disco
    lleno, archivo dañado): una carpeta a medias no sirve para nada.
    """
    archive = Path(archive_path)
    dest = Path(dest_folder).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    progress = _Progress(archive.stat().st_size, on_progress, cancel)
    created: list[Path] = []

    try:
        if archive.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive) as zf:
                names = _safe_extract_zip(zf, dest, progress, created)
        else:
            # El fichero lo abrimos nosotros para poder preguntarle cuánto se
            # ha leído (el progreso). "r:*" detecta la compresión (xz, gz...).
            with open(archive, "rb") as raw, \
                    tarfile.open(fileobj=raw, mode="r:*") as tf:
                names = _safe_extract_tar(tf, dest, raw, progress, created)
                progress.step(progress.total)
    except BaseException:
        _remove(created)
        raise

    tops = _top_level(names)
    if len(tops) == 1:
        folder = dest / tops.pop()
        if folder.is_dir():
            return folder
    return dest
