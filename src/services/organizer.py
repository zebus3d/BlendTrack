"""Mover instalaciones de una carpeta de la biblioteca a otra.

Cuando el usuario cambia qué tipos recibe una carpeta, las versiones que ya
estaban dentro pueden dejar de encajar. Este módulo hace dos cosas bien
separadas: **decidir** qué habría que mover (funciones puras, sin disco) y
**mover**, que es la operación más destructiva que tiene la aplicación —una
build son cientos de MB y a menudo hay que cruzar de disco.

**La invariante del módulo, que no se puede romper: el origen no se borra hasta
que el destino está completo y en su sitio.** Todo lo demás de ``move_build``
(comprobar espacio, mirar si Blender está abierto, copiar a un temporal) existe
para sostenerla.

No importa Qt: el trabajo pesado lo lanza la interfaz en un hilo y se entera por
los callbacks ``on_progress``/``should_cancel``, igual que con las descargas.
"""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from services import blender_runner, channels

# Sufijo de la carpeta temporal donde se copia antes de dejarla en su sitio.
# El temporal se llama ``.<nombre>.moving``: lo que lo esconde de un escaneo a
# medio camino es el **punto delante**, porque ``installed._scan_root`` se salta
# las carpetas ocultas. El sufijo solo no bastaba: ``blender-5.2.1.moving``
# sigue casando con ``VERSION_RE`` y salía en Instaladas como una versión más,
# a medio copiar.
MOVING_SUFFIX = ".moving"


def temporary_path(target_root: Path, name: str) -> Path:
    """Carpeta temporal donde se copia ``name`` antes de dejarla en su sitio."""
    return Path(target_root) / f".{name}{MOVING_SUFFIX}"

# Margen sobre el tamaño de la build al comprobar el espacio libre. Copiar deja
# picos (metadatos, bloques a medias) y quedarse sin sitio a mitad es el peor
# final posible.
SPACE_MARGIN = 1.05


@dataclass(frozen=True)
class Move:
    """Una instalación que habría que llevar de una carpeta a otra."""

    entry: object
    source_root: Path
    target_root: Path
    build_type: str


class OrganizerError(Exception):
    """Fallo al mover, con un código en vez de un texto.

    El código ("not_writable", "exists", "no_space", "running", "io") lo
    traduce la interfaz, igual que con ``installed.rename_failure``: aquí no se
    puede llamar a ``tr()`` porque esta capa no sabe de idiomas.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def folder_size(path) -> int:
    """Bytes que ocupa una carpeta, ignorando lo que no se pueda leer."""
    total = 0
    for root, _dirs, files in os.walk(str(path), onerror=lambda _e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def plan_reorg(entries, folders) -> list:
    """Instalaciones que no están en la carpeta que les corresponde.

    Devuelve solo movimientos que **se pueden hacer**: si el tipo de una build
    no lo recibe ninguna carpeta, no se propone nada (no hay adónde llevarla).

    Dos reglas de las que no se sale:

    * de una carpeta sin tipos marcados no se saca nada. Está en la lista
      precisamente para mirar dentro sin tocarla;
    * de una carpeta con el candado cerrado, tampoco. "Solo lectura" significa
      que la aplicación no escribe nada ahí, y sacar una carpeta es escribir.
    """
    moves = []
    by_path = {channels.normalize_path(folder.path): folder
               for folder in folders or ()}
    for entry in entries or ():
        root = getattr(entry, "root", None)
        source = by_path.get(channels.normalize_path(root)) if root else None
        if source is None or not source.writable or not source.types:
            continue
        build_type = channels.type_of_installed(entry)
        if build_type in source.types:
            continue
        target = channels.owner_of(folders, build_type)
        if target is None:
            continue
        if channels.normalize_path(target.path) == channels.normalize_path(root):
            continue
        moves.append(Move(entry=entry, source_root=Path(source.path),
                          target_root=Path(target.path),
                          build_type=build_type))
    return moves


def misplaced(entries, folders, root) -> list:
    """Como ``plan_reorg`` pero solo para una carpeta.

    ``folders`` tiene que ser la biblioteca **tal y como quedaría** tras el
    cambio: es lo que permite enseñar "esto es lo que dejaría de encajar" antes
    de aplicarlo.
    """
    key = channels.normalize_path(root)
    return [move for move in plan_reorg(entries, folders)
            if channels.normalize_path(move.source_root) == key]


def _probe_writable(folder: Path) -> None:
    """Crea la carpeta y comprueba que deja escribir, o lanza."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".blendermanager-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        raise OrganizerError("not_writable", str(error)) from error


def _same_volume(source: Path, target: Path) -> bool:
    """True si las dos rutas viven en el mismo sistema de archivos."""
    try:
        return os.stat(source).st_dev == os.stat(target).st_dev
    except OSError:
        return False


def _copy_tree(source: Path, temporary: Path, total: int,
               on_progress, should_cancel) -> None:
    """Copia el árbol a ``temporary`` contando bytes, o lanza al cancelar."""
    copied = 0

    def copy_file(src, dst, *, follow_symlinks=True):
        nonlocal copied
        if should_cancel is not None and should_cancel():
            raise OrganizerError("cancelled")
        shutil.copy2(src, dst, follow_symlinks=follow_symlinks)
        copied += os.path.getsize(dst) if os.path.isfile(dst) else 0
        if on_progress is not None:
            on_progress(min(copied, total), total)

    shutil.copytree(source, temporary, symlinks=True, copy_function=copy_file)


def move_build(move: Move, on_progress=None, should_cancel=None) -> Path:
    """Lleva una instalación a su carpeta nueva y devuelve la ruta resultante.

    El orden de las comprobaciones no es casual; cada una evita un final malo:

    1. que el destino deje escribir (si no, se sabe antes de copiar nada);
    2. que no haya ya una carpeta con ese nombre. **No se añaden sufijos en
       silencio**: dos "blender-5.2.1" distintos son una decisión del usuario,
       no algo que se resuelva solo;
    3. que quepa, con margen;
    4. que ese Blender no esté abierto (en Windows, además, mover con el .exe
       abierto falla con ``PermissionError``, así que se captura también: la
       comprobación previa siempre puede llegar tarde).

    Y luego, el movimiento en sí. En el mismo volumen es un ``os.replace``:
    instantáneo y atómico. Cruzando discos **no se usa ``shutil.move`` a pelo**,
    porque copia y borra, y si falla a mitad deja el destino incompleto y el
    origen ya tocado. En su lugar se copia a un temporal hermano del destino,
    se renombra encima (mismo sistema de archivos, así que es atómico) y
    **solo entonces** se borra el origen. Ante cualquier fallo se borra el
    temporal y el origen se queda intacto.

    El marcador ``.blendermanager.json`` viaja dentro de la carpeta, así que no
    hay que reescribirlo (a diferencia de ``installed.rename``, donde lo que se
    pierde es el nombre).
    """
    source = Path(move.entry.path)
    target_root = Path(move.target_root)
    target = target_root / source.name

    _probe_writable(target_root)
    if target.exists():
        raise OrganizerError("exists", str(target))

    size = folder_size(source)
    try:
        free = shutil.disk_usage(target_root).free
    except OSError:
        free = None
    if free is not None and free < size * SPACE_MARGIN:
        raise OrganizerError("no_space", str(target_root))

    executable = getattr(move.entry, "executable", None)
    if executable is not None and blender_runner.is_running(executable):
        raise OrganizerError("running", str(source))

    if on_progress is not None:
        on_progress(0, size)

    if _same_volume(source, target_root):
        try:
            os.replace(source, target)
        except PermissionError as error:
            # Windows: la carpeta está en uso (Blender abierto, o el explorador
            # con ella seleccionada). No es un fallo de disco.
            raise OrganizerError("running", str(error)) from error
        except OSError as error:
            raise OrganizerError("io", str(error)) from error
        if on_progress is not None:
            on_progress(size, size)
        return target

    temporary = temporary_path(target_root, source.name)
    shutil.rmtree(temporary, ignore_errors=True)
    try:
        _copy_tree(source, temporary, size, on_progress, should_cancel)
        os.replace(temporary, target)
    except OrganizerError:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    except OSError as error:
        shutil.rmtree(temporary, ignore_errors=True)
        raise OrganizerError("io", str(error)) from error
    # El destino ya está completo y en su sitio: ahora sí se puede soltar el
    # origen. Si esto falla, el usuario tiene la build duplicada, que es
    # molesto pero recuperable; al revés se habría quedado sin ella.
    try:
        shutil.rmtree(source)
    except OSError as error:
        raise OrganizerError("io", str(error)) from error
    if on_progress is not None:
        on_progress(size, size)
    return target
