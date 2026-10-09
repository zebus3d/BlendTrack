"""Ficheros ``.blend`` abiertos recientemente, por versión de Blender.

Blender guarda esa lista en ``<config>/recent-files.txt``: una ruta absoluta por
línea, la más reciente primero. Es texto plano, así que se puede leer sin
arrancar Blender. Aquí solo se localiza y se agrupa por serie; abrir el fichero,
revelarlo o lanzarlo es cosa de la interfaz.

Es un módulo de ``services/``: no importa Qt.
"""

from dataclasses import dataclass, field
from pathlib import Path

from model.build import FORK_BLENDER, fork_series
from services import blender_config as bc

RECENT_FILE = "recent-files.txt"


@dataclass
class RecentFile:
    """Un ``.blend`` de la lista de recientes.

    ``missing`` marca los que ya no están en esa ruta (movidos o borrados). Se
    devuelven igualmente: si desaparecieran sin más, el usuario no sabría si
    Blender no tiene recientes o si sus ficheros han cambiado de sitio.
    """

    path: Path
    missing: bool = False


@dataclass
class RecentGroup:
    """Los recientes de una serie de Blender (o de un fork).

    ``version`` es la más nueva instalada de esa serie (las versiones de una
    serie comparten carpeta de configuración, así que la lista es la misma).
    ``fork`` distingue a quién pertenecen: una serie 5.2 de Bforartists y otra
    de Blender son programas distintos y no comparten recientes.
    """

    series: str
    version: str
    files: list = field(default_factory=list)   # de ``RecentFile``
    fork: str = FORK_BLENDER


def _is_file(path: Path) -> bool:
    """``Path.is_file`` que no lanza: un error del disco es "no está".

    En Python 3.12 ``is_file()`` **lanza** ``PermissionError`` cuando falta
    permiso para recorrer la ruta (solo se traga "no existe"). La lista de
    recientes puede apuntar a un disco de otro usuario o a un montaje FUSE que
    no deja ni mirar, y un solo fichero así tumbaba la pestaña entera. Es la
    misma idea que ``installed._is_file``.
    """
    try:
        return path.is_file()
    except OSError:
        return False


def recent_files(config) -> list:
    """``RecentFile`` de esa config, el más nuevo primero.

    Se saltan las líneas vacías y las relativas al fichero (``//``). Las rutas
    que ya no existen se devuelven marcadas con ``missing`` (ver
    ``RecentFile``), no se ocultan.
    """
    path = Path(config.config_dir) / RECENT_FILE
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    files = []
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("//"):
            continue
        candidate = Path(entry)
        files.append(RecentFile(candidate, missing=not _is_file(candidate)))
    return files


def grouped(installed, platform: str, env=None) -> list:
    """Recientes agrupados por serie, de la más nueva a la más vieja.

    De cada serie se usa la versión más nueva instalada y se lee su config una
    sola vez. Solo se devuelven las series con algún fichero reciente.
    """
    seen = set()
    groups = []
    for entry in installed or []:
        fork = getattr(entry, "fork", FORK_BLENDER) or FORK_BLENDER
        series = fork_series(fork, getattr(entry, "version", "") or "")
        key = (fork, series)
        if not series or key in seen:
            continue
        seen.add(key)
        config = bc.config_for(entry.version, platform, env, fork=fork)
        files = recent_files(config)
        if files:
            groups.append(RecentGroup(series=series, version=entry.version,
                                      files=files, fork=fork))
    return groups
