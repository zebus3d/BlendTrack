"""Guardados de la configuración de una versión (los "valores de fábrica").

Restablecer una versión no borra nada: aparta su carpeta ``config`` a un
guardado con fecha (``set_config_aside``) y deja que Blender la recree. Desde
ahí se puede listar lo guardado, ver qué trae y volver a ponerlo
(``restore_snapshot``), que **reemplaza** la config, no la funde.

Es un módulo de ``services/``: no importa Qt. Leer qué **ajustes** cambia un
guardado es cosa de ``blender_prefs`` (arranca Blender para preguntárselo).
"""

import re
import shutil
from datetime import datetime
from pathlib import Path

from services.blender_config import (STARTUP_FILE, USERPREF_FILE,
                                     BlenderConfig)

# Carpeta, junto a la config de una versión, donde se guardan las instantáneas
# de ``config`` antes de resetear. El nombre lleva fecha y hora para que se
# puedan tener varias (resetear a fábrica y volver a intentarlo es normal).
SNAPSHOT_DIR = ".blendermanager-snapshots"
SNAPSHOT_PREFIX = "config"

# Ficheros de texto de ``config`` que no son ajustes pero sí datos del usuario:
# los ficheros recientes y los marcadores del explorador de archivos. Una
# config sin preferencias guardadas puede tenerlos, y no se tiran.
RECENT_FILES = "recent-files.txt"
BOOKMARKS_FILE = "bookmarks.txt"


def set_config_aside(target: BlenderConfig, label: str = "",
                    keep: int = 0) -> Path | None:
    """Aparta la carpeta ``config`` de una versión y devuelve dónde quedó.

    Es lo que hace a mano quien quiere probar una versión "de fábrica": renombrar
    ``config`` y dejar que Blender la recree. Aquí se guarda además en una
    carpeta de instantáneas para poder **volver a ponerla** después. No borra
    la config: si no existe, devuelve ``None``. ``keep`` > 0 poda las
    instantáneas más viejas (ver ``prune_snapshots``).
    """
    source = target.config_dir
    if not source.exists():
        return None
    snapshots = target.root / SNAPSHOT_DIR
    snapshots.mkdir(parents=True, exist_ok=True)
    name = f"{SNAPSHOT_PREFIX}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    if label:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")
        if clean:
            name += f"-{clean}"
    destination = snapshots / name
    counter = 1
    while destination.exists():
        destination = snapshots / f"{name}-{counter}"
        counter += 1
    shutil.move(str(source), str(destination))
    if keep > 0:
        prune_snapshots(target, keep)
    return destination

def all_snapshots(target: BlenderConfig) -> list:
    """Todas las instantáneas de ``config`` de esa versión, vacías incluidas.

    Es la lista cruda (para borrar o para saber si hay algo), de la más nueva a
    la más vieja. Lo que se ofrece restaurar es ``snapshots_with_settings``.
    """
    folder = target.root / SNAPSHOT_DIR
    if not folder.is_dir():
        return []
    return sorted((item for item in folder.iterdir()
                   if item.is_dir() and item.name.startswith(SNAPSHOT_PREFIX)),
                  key=lambda item: item.name, reverse=True)

def _has_settings(snapshot: Path) -> bool:
    """True si guarda preferencias de verdad (``userpref`` o ``startup``).

    No basta con que la carpeta tenga algo: una instantánea con solo
    ``platform_support.txt`` o los recientes no tiene ajustes que restaurar y no
    debe contar como "lo más reciente" (el botón de restaurar no recuperaría
    nada).
    """
    snapshot = Path(snapshot)
    return any((snapshot / name).is_file()
               for name in (USERPREF_FILE, STARTUP_FILE))

def _has_user_data(config_dir: Path) -> bool:
    """True si esa carpeta guarda algo del usuario: ajustes, recientes o marcadores.

    Es más amplio que ``_has_settings`` a propósito. Para decidir qué guardado
    se *ofrece restaurar* solo cuentan los ajustes; para decidir si una config
    se puede *tirar* cuentan también los recientes y los marcadores, que el
    usuario echaría de menos. Lo que Blender regenera solo
    (``platform_support.txt``) no cuenta para ninguna de las dos cosas.
    """
    config_dir = Path(config_dir)
    return _has_settings(config_dir) or any(
        (config_dir / name).is_file() for name in (RECENT_FILES, BOOKMARKS_FILE))


def snapshots_with_settings(target: BlenderConfig) -> list:
    """Instantáneas con ajustes de verdad, de la más nueva a la más vieja.

    Las vacías se descartan: al restaurar se aparta la config que hubiera, y si
    no había nada queda una carpeta sin ficheros. Si esa contase como "lo más
    reciente", el botón de restaurar no recuperaría los ajustes de verdad (le
    pasó al usuario: su config real quedaba tapada por una vacía).
    """
    return [item for item in all_snapshots(target) if _has_settings(item)]

def snapshot_date(snapshot) -> str:
    """Fecha legible de una instantánea (``2026-09-18 14:21``), o ``""``.

    El nombre es ``config-AAAAMMDD-HHMMSS[-etiqueta]``. La interfaz enseña la
    fecha, no el nombre de la carpeta: «config-20260918-152704-factory» no dice
    nada a quien lo lee.
    """
    match = re.match(r"config-(\d{8})-(\d{6})", Path(snapshot).name)
    if not match:
        return ""
    try:
        moment = datetime.strptime(match.group(1) + match.group(2),
                                   "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return moment.strftime("%Y-%m-%d %H:%M")

def snapshot_label(snapshot) -> str:
    """Etiqueta con la que se guardó (``v5.2.0``, ``factory`` o ``""``).

    Es lo que distingue un guardado del usuario (los ajustes que se apartaron al
    restablecer) de una config limpia que se aparcó al restaurar.
    """
    match = re.match(r"config-\d{8}-\d{6}(?:-(.*))?$", Path(snapshot).name)
    return (match.group(1) or "") if match else ""

def snapshot_details(snapshot) -> dict:
    """Resumen barato de una instantánea (sin arrancar Blender).

    Cuenta los ficheros y sus tamaños, y las líneas de los que son texto
    (marcadores y recientes). ``has_userpref`` es lo que de verdad importa:
    sin él no hay preferencias que restaurar.
    """
    snapshot = Path(snapshot)
    files = {}
    if snapshot.is_dir():
        for child in snapshot.iterdir():
            try:
                if child.is_file():
                    files[child.name] = child.stat().st_size
            except OSError:
                continue

    def lines(name: str) -> int:
        try:
            text = (snapshot / name).read_text(encoding="utf-8",
                                               errors="ignore")
        except OSError:
            return 0
        return sum(1 for line in text.splitlines() if line.strip())

    return {
        "files": files,
        "total": sum(files.values()),
        "has_userpref": "userpref.blend" in files,
        "has_startup": "startup.blend" in files,
        "bookmarks": lines("bookmarks.txt"),
        "recent": lines("recent-files.txt"),
    }

def restore_snapshot(target: BlenderConfig, snapshot,
                     keep: int = 0) -> Path | None:
    """Pone una instantánea en el sitio de ``config`` (reemplazándola), sin consumirla.

    Antes se **movía** (el guardado desaparecía). Eso dejaba al usuario sin
    segunda oportunidad: si algo pisaba la config después (un Blender de la
    misma serie abierto, por ejemplo), los ajustes se perdían para siempre. Se
    copia y el guardado se queda hasta que el usuario lo borre a mano.

    La config que había se aparta como ``factory`` para poder deshacer, pero
    solo si tenía algo del usuario (``_has_user_data``): aparcar una carpeta
    que Blender regenera sola solo añade ruido. Antes bastaba con que no
    tuviera ``userpref``/``startup`` para borrarla, y con ella se iban los
    ficheros recientes y los marcadores que el usuario acumuló mientras probaba
    la versión de fábrica. Devuelve ese aparte (o ``None``). ``keep`` > 0 poda
    las más viejas sin tocar la que se acaba de restaurar.
    """
    snapshot = Path(snapshot)
    if not snapshot.is_dir():
        return None
    aside = None
    if _has_user_data(target.config_dir):
        aside = set_config_aside(target, label="factory")
    elif target.config_dir.exists():
        # Sin nada del usuario no merece guardado, pero tampoco puede quedarse:
        # restaurar es **reemplazar** la config, no fundir el guardado con lo
        # que hubiera.
        shutil.rmtree(target.config_dir)
    target.config_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot, target.config_dir, symlinks=True)
    if keep > 0:
        prune_snapshots(target, keep, protect=snapshot)
    return aside

def delete_snapshot(snapshot) -> bool:
    """Borra una instantánea (el usuario decidió que no la quiere)."""
    snapshot = Path(snapshot)
    if not snapshot.is_dir():
        return False
    try:
        shutil.rmtree(snapshot)
    except OSError:
        return False
    return True

def prune_snapshots(target: BlenderConfig, keep: int, protect=None) -> list:
    """Deja ``keep`` instantáneas y borra el resto, **las vacías primero**.

    ``keep`` <= 0 no borra nada. ``protect`` (una ruta) nunca se borra: es la
    que se acaba de restaurar. Nunca se toca la más nueva tampoco: el guardado
    que el usuario acaba de hacer tiene que seguir ahí aunque algo falle.

    El orden de sacrificio importa. Cada restauración aparta la config que
    hubiera, y muchas veces es una casi vacía (solo recientes) o una de
    fábrica. Podando solo por fecha, unas cuantas idas y venidas llenaban el
    cupo con esas y se borraba el guardado **con los ajustes de verdad** del
    usuario, que era el más viejo. Ahora las que tienen ajustes van delante y,
    dentro de cada grupo, las más nuevas.
    """
    if keep <= 0:
        return []
    snapshots = all_snapshots(target)      # de la más nueva a la más vieja
    if not snapshots:
        return []
    protected = Path(protect) if protect is not None else None
    # ``sorted`` es estable: dentro de cada grupo se respeta el orden por
    # fecha que ya trae ``all_snapshots``.
    ranked = sorted(snapshots, key=lambda item: not _has_settings(item))
    kept = set(ranked[:keep]) | {snapshots[0]}
    removed = []
    for snapshot in snapshots:
        if snapshot in kept or snapshot == protected:
            continue
        if delete_snapshot(snapshot):
            removed.append(snapshot)
    return removed
