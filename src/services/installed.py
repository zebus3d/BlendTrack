"""Escaneo de las versiones de Blender ya extraídas en la biblioteca.

La biblioteca son varias carpetas (``settings.Folder``) y en cada una se mira
solo el primer nivel. Las builds oficiales se descomprimen en carpetas con un
nombre del estilo ``blender-4.5.13-linux-x64``. De ahí sacamos la versión y
localizamos el ejecutable, que cambia según la plataforma (y según el fork).

Ese nombre **no** incluye el hash de la compilación, así que dos alfa de
``main`` bajadas en días distintos producen exactamente la misma carpeta. Para
poder distinguirlas dejamos un marcador propio (``MARKER_NAME``) dentro de la
carpeta al instalar, con el hash que nos dio la API.
"""

import json
import re
from collections import namedtuple
from pathlib import Path

from model.build import (FORK_BFORARTISTS, FORK_BLENDER, FORK_UPBGE,
                         InstalledBuild, minor_of, version_tuple)
from services import channels

# Actualización disponible para una versión instalada.
#   kind == "patch":  misma serie con parche más nuevo (5.2.0 -> 5.2.2).
#   kind == "series": serie estable superior (5.2.x -> 5.3.0).
Update = namedtuple("Update", "entry build kind")

# Captura la versión del nombre de la carpeta, por ejemplo 4.5.13. Solo Blender
# oficial: los forks llevan su propio prefijo y se reconocen aparte.
VERSION_RE = re.compile(r"blender[-_ ]?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
# Bforartists extrae una carpeta tipo ``Bforartists-5.2.0-Linux`` y UPBGE
# ``upbge-0.53-alpha-linux-x86_64-...``. Sin esto, una instalación de un fork
# (que no lleva "blender" en el nombre) se ignoraba por completo.
BFA_VERSION_RE = re.compile(r"bforartists[-_ ]?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
UPBGE_VERSION_RE = re.compile(r"upbge[-_ ]?(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)

# Marcador que escribimos al instalar para recordar qué compilación es.
MARKER_NAME = ".blendtrack.json"


def _write_marker_payload(folder, payload: dict) -> None:
    """Escribe el marcador; si no se puede (solo lectura, disco lleno) no pasa nada."""
    try:
        (Path(folder) / MARKER_NAME).write_text(json.dumps(payload, indent=2),
                                                encoding="utf-8")
    except OSError:
        pass


def write_marker(folder, build) -> None:
    """Anota dentro de la carpeta instalada de qué compilación viene.

    Si no se puede escribir (carpeta de solo lectura, disco lleno) no pasa
    nada: sin marcador se compara solo por versión, como antes.
    """
    _write_marker_payload(folder, {
        "version": build.version,
        "risk": build.risk,
        "branch": build.branch,
        "hash": build.build_hash,
        "filename": build.filename,
        "fork": getattr(build, "fork", FORK_BLENDER),
    })


def _is_dir(path) -> bool:
    """``Path.is_dir`` tolerando rutas sin permiso.

    En Python 3.12 ``is_dir()``/``is_file()`` **lanzan** ``PermissionError``
    cuando falta permiso para *recorrer* la ruta (solo ignoran ENOENT/ENOTDIR).
    Al escanear una carpeta amplia (el home, sin ir más lejos) aparece
    ``~/.gvfs``, un montaje FUSE que no deja ni hacer ``stat``: la excepción
    subía hasta la interfaz y salía un "Error inesperado" solo por una carpeta
    ilegible. Para escanear, un error del sistema de archivos es simplemente
    "aquí no hay una instalación de Blender".
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def _is_file(path) -> bool:
    """Igual que ``_is_dir``, pero para ficheros."""
    try:
        return path.is_file()
    except OSError:
        return False


def _folder_mtime(path) -> int:
    """Fecha de modificación de una carpeta, en epoch (0 si no se puede leer).

    La usamos como "fecha de descarga" de una instalación: no guardamos esa
    fecha en el marcador, pero la carpeta se creó al extraer la build, así que
    su ``st_mtime`` es lo más cerca que el sistema de archivos nos deja estar.
    Tolerante a errores como ``_is_dir``: una carpeta ilegible no tiene fecha.
    """
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def read_marker(folder) -> dict:
    """Lee el marcador ``.blendtrack.json`` de una build
    instalada.
    """
    path = Path(folder) / MARKER_NAME
    if not _is_file(path):
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# Caracteres que Windows no admite en un nombre de archivo (y que no queremos
# en ninguna plataforma, por coherencia). Incluye el separador de ruta.
INVALID_NAME_CHARS = '\\/:*?"<>|'


def rename_failure(folder, new_name: str) -> str:
    """Motivo por el que no se puede renombrar esa carpeta, o "" si sí.

    Devuelve un código ("empty", "invalid", "same", "exists") y no un texto: la
    traducción es cosa de la interfaz. La comprobación es pura para poder
    probarla sin tocar el disco.
    """
    name = (new_name or "").strip()
    if not name:
        return "empty"
    if any(char in name for char in INVALID_NAME_CHARS) or name in (".", ".."):
        return "invalid"
    origin = Path(folder)
    if name == origin.name:
        return "same"
    if (origin.parent / name).exists():
        return "exists"
    return ""


def rename(folder, new_name: str, version: str = "", branch: str = "",
           build_hash: str = "", fork: str = "", risk: str = "") -> Path:
    """Renombra la carpeta de una instalación y devuelve la ruta nueva.

    Si la instalación no tenía marcador (de antes de que existiera) se le
    escribe uno con lo que sabemos **antes** de renombrar: si no, al perder el
    nombre la versión, el escaneo ya no la reconocería. La validación del nombre
    va aparte (``rename_failure``); esto solo hace el cambio en disco.

    El nombre no solo lleva la versión: en una carpeta sin marcador, el
    ``alpha`` del nombre es lo único que dice que es una diaria. Por eso el
    marcador guarda también el ``risk``: el que nos pasen o, si no lo sabemos,
    el que se deduce del nombre **viejo** (``channels.type_from_marker``).
    Sin él, una diaria renombrada pasaba a contar como estable y cambiaba de
    pestaña (y de carpeta de destino al reorganizar).
    """
    origin = Path(folder)
    target = origin.with_name((new_name or "").strip())
    if version and not read_marker(origin):
        if not risk:
            guessed = channels.type_from_marker(branch, version, origin.name,
                                                fork=fork)
            risk = "alpha" if guessed == channels.TYPE_DAILY else "stable"
        _write_marker_payload(origin, {
            "version": version,
            "risk": risk,
            "branch": branch,
            "hash": build_hash,
            "fork": fork,
        })
    origin.rename(target)
    return target


# Rutas del ejecutable dentro de la carpeta extraída, por fork. Bforartists usa
# su propio nombre (``bforartists``/``bforartists.exe``/``Bforartists.app``);
# UPBGE, como es un Blender por dentro, usa los mismos que el oficial.
_EXECUTABLES = {
    FORK_BLENDER: {
        "windows": ("blender.exe",),
        "darwin": ("Blender.app/Contents/MacOS/Blender", "blender"),
        "linux": ("blender",),
    },
    FORK_BFORARTISTS: {
        "windows": ("bforartists.exe", "blender.exe"),
        "darwin": ("Bforartists.app/Contents/MacOS/Bforartists",
                   "Blender.app/Contents/MacOS/Blender", "bforartists"),
        "linux": ("bforartists", "blender"),
    },
    FORK_UPBGE: {
        "windows": ("blender.exe",),
        "darwin": ("Blender.app/Contents/MacOS/Blender", "blender"),
        "linux": ("blender",),
    },
}


def _executable_for(directory: Path, platform: str, depth: int = 1,
                    fork: str = FORK_BLENDER):
    """Busca el ejecutable de esa versión dentro de una carpeta.

    ``depth`` limita cuántos niveles descendemos: el ejecutable está en la
    raíz de la carpeta (o un nivel más adentro si la build viene anidada), y
    sin tope acabaríamos recorriendo el árbol entero de Blender —miles de
    archivos— desde el hilo de la interfaz.

    ``fork`` decide los nombres que se prueban: una carpeta de Bforartists no
    lleva ``blender`` como ejecutable, sino ``bforartists``.
    """
    if not _is_dir(directory):
        return None
    options = _EXECUTABLES.get(fork or FORK_BLENDER, _EXECUTABLES[FORK_BLENDER])
    for relative in options.get(platform, options["linux"]):
        candidate = directory / relative
        if _is_file(candidate):
            return candidate
    if depth <= 0:
        return None
    # Algunas builds anidan la carpeta, así que descendemos un nivel.
    try:
        children = sorted(directory.iterdir())
    except OSError:
        return None
    for child in children:
        if _is_dir(child):
            found = _executable_for(child, platform, depth - 1, fork)
            if found is not None:
                return found
    return None


def _fork_and_match(name: str, marker: dict):
    """Fork y versión de una carpeta, por el marcador o por su nombre.

    El marcador manda (lo escribimos nosotros al instalar). Sin él —carpetas
    que no bajó la app, o instaladas antes del marcador— se reconoce el fork
    por el prefijo del nombre y se saca la versión de ahí.
    """
    fork = str(marker.get("fork") or "")
    version = str(marker.get("version") or "")
    match = VERSION_RE.search(name)
    bfa = BFA_VERSION_RE.search(name)
    upbge = UPBGE_VERSION_RE.search(name)
    if not fork:
        if bfa:
            fork = FORK_BFORARTISTS
        elif upbge:
            fork = FORK_UPBGE
    if not version:
        for candidate in (bfa, upbge, match):
            if candidate:
                version = candidate.group(1)
                break
    return fork, version


def _scan_root(root: Path, platform: str):
    """Escanea una sola carpeta y devuelve sus instalaciones sin ordenar."""
    results = []
    if not _is_dir(root):
        return results
    try:
        entries = sorted(root.iterdir())
    except OSError:
        # Sin permiso para listar la carpeta: no hay nada que sacar de aquí.
        return results
    for entry in entries:
        # Las carpetas ocultas nunca son una instalación: ahí van los
        # temporales de la propia aplicación (``.blender-5.2.1.moving``, ver
        # ``organizer.temporary_path``), que a media copia casarían con
        # ``VERSION_RE`` y saldrían como una versión más.
        if entry.name.startswith(".") or not _is_dir(entry):
            continue
        marker = read_marker(entry)
        fork, version = _fork_and_match(entry.name, marker)
        # Sirve si el nombre lleva la versión (lo normal) o si tiene marcador
        # propio: al renombrar una instalación el nombre puede dejar de llevar
        # la versión, pero el marcador sigue diciendo qué es.
        if not version and not marker:
            # Ignoramos carpetas que no son de Blender (archivos temporales, etc.).
            continue
        executable = _executable_for(entry, platform, fork=fork)
        results.append(
            InstalledBuild(
                name=entry.name,
                path=entry,
                version=version,
                executable=executable,
                build_hash=str(marker.get("hash") or ""),
                branch=str(marker.get("branch") or ""),
                # El marcador ya guardaba el "risk" desde el principio, pero
                # hasta ahora se tiraba: con él, saber si algo es una diaria o
                # una LTS deja de ser una heurística sobre el nombre.
                risk=str(marker.get("risk") or ""),
                root=root,
                fork=fork,
                # Fecha de descarga: no la guardamos en ningún sitio, así que
                # se pregunta al sistema de archivos por la fecha de
                # modificación de la carpeta instalada (que se crea al
                # extraer, o sea justo al descargar). Es lo único disponible
                # y es aproximada, pero suficiente para el dato de la tarjeta.
                downloaded=_folder_mtime(entry),
            )
        )
    return results


def scan_folders(folders, platform: str) -> list[InstalledBuild]:
    """Escanea varias carpetas y devuelve todo junto, de más nueva a más antigua.

    Es el escaneo de la biblioteca entera (``Settings.library_roots``): cada
    carpeta puede recibir tipos distintos, y las instaladas se ven todas
    juntas. Si dos rutas apuntan al mismo directorio (o una está repetida) se
    escanea una sola vez, para no listar la misma instalación por duplicado.
    """
    results = []
    seen = set()
    for folder in folders:
        if not folder:
            continue
        root = Path(folder).expanduser()
        try:
            # ``resolve`` puede fallar con enlaces rotos o permisos: en ese caso
            # nos quedamos con la ruta tal cual y listo.
            key = root.resolve()
        except OSError:
            key = root
        if key in seen:
            continue
        seen.add(key)
        results.extend(_scan_root(root, platform))
    results.sort(key=lambda build: version_tuple(build.version), reverse=True)
    return results


def find_installed(installed, build) -> InstalledBuild | None:
    """Devuelve la instalación que corresponde a ``build``, o None.

    Las estables se identifican por versión: 4.5.13 es 4.5.13 y punto. Las
    diarias/alfa, en cambio, comparten número de versión durante meses, así
    que solo cuentan como instaladas si además coincide el hash anotado en su
    marcador. Una carpeta sin marcador (instalada antes de que existiera) se
    compara solo por versión, como se hacía siempre.

    El fork también cuenta: Blender 5.2.0 y Bforartists 5.2.0 comparten número
    de versión, pero no son la misma instalación (ni el mismo programa).
    """
    target = version_tuple(build.version)
    target_fork = getattr(build, "fork", FORK_BLENDER)
    for entry in installed:
        if getattr(entry, "fork", FORK_BLENDER) != target_fork:
            continue
        if version_tuple(entry.version) != target:
            continue
        if build.risk != "stable" and entry.build_hash and build.build_hash:
            if entry.build_hash != build.build_hash:
                continue
        return entry
    return None


def is_experimental(entry) -> bool:
    """True si la instalación viene de una rama experimental.

    La regla está en ``services.channels`` para que sea la misma que decide en
    qué carpeta se instala una experimental; aquí solo queda el envoltorio,
    porque ``available_updates`` y los tests lo llaman por su nombre.
    """
    return channels.type_of_installed(entry) == channels.TYPE_EXPERIMENTAL


def filter_installed(entries, channel: str, search: str = "",
                     favorites=()) -> list:
    """Aplica el filtro de canal y la búsqueda a las versiones instaladas.

    Es el equivalente de ``api.filter_builds`` para la pestaña de instaladas:
    las experimentales solo salen en su canal y el resto de canales las
    excluyen. Clasificar una instalada es más difícil que una de la tienda
    (no tenemos delante la respuesta de la API), así que la regla vive en
    ``services.channels``, que usa el marcador que dejamos al instalar y, solo
    cuando falta, adivina por la rama y el nombre de la carpeta.

    Los favoritos comparten clave con la tienda
    (``model.build.favorite_key``), así que marcar una versión en la tienda la
    marca también aquí.
    """
    if channel == "favorites":
        marked = set(favorites or ())
        selected = [entry for entry in entries if entry.favorite_key in marked]
    elif channel == "all":
        # "Todas" es literalmente todas: también las de fork. Coincide con la
        # tienda, donde "Todas" también mezcla los forks.
        selected = list(entries)
    else:
        fork_channel = next(
            (fork for fork, build_type in channels.FORK_TYPES.items()
             if build_type == channel), None)
        if fork_channel:
            selected = [entry for entry in entries
                        if entry.fork == fork_channel]
        elif channel == "experimental":
            selected = [entry for entry in entries
                        if is_experimental(entry) and not entry.fork]
        else:
            selected = [entry for entry in entries
                        if not entry.fork and not is_experimental(entry)]
            if channel in (channels.TYPE_LTS, channels.TYPE_STABLE,
                           channels.TYPE_DAILY):
                selected = [entry for entry in selected
                            if channels.type_of_installed(entry) == channel]
    text = (search or "").strip().lower()
    if text:
        selected = [
            entry for entry in selected
            if text in entry.name.lower() or text in entry.version.lower()
        ]
    return selected


def _is_stable_install(entry) -> bool:
    """True si la instalación es una versión estable o LTS (no diaria/alfa).

    Lo usa ``available_updates``: solo tiene sentido ofrecer el salto de serie
    a quien va por el canal estable. Envoltorio de ``services.channels`` para
    no tener dos definiciones de "esto es una diaria".
    """
    return channels.type_of_installed(entry) in (channels.TYPE_LTS,
                                                 channels.TYPE_STABLE)


def _minor_tuple(version: str):
    """Serie de una versión como tupla ordenable: '5.2.1' -> (5, 2)."""
    return version_tuple(minor_of(version))


def available_updates(installed, builds) -> list[Update]:
    """Empareja cada instalada con las actualizaciones que le corresponden.

    ``builds`` debe venir ya filtrada por plataforma y arquitectura (es lo que
    hace ``api.available_for``): no tendría sentido ofrecer un parche para otra
    plataforma. Solo se miran instaladas estables y compilaciones ``stable``;
    las diarias/alfa se renumeran solas y aquí serían ruido.

    El aviso es **por serie**, no por cada carpeta: se coge la instalada más
    nueva de cada serie, porque avisar en la 5.2.0 después de bajarte la 5.2.2
    como copia es ruido. Y si esa versión nueva ya está instalada, tampoco se
    ofrece (de ahí ``installed_versions``).

    Devuelve una lista de ``Update(entry, build, kind)`` donde ``kind`` es
    ``"patch"`` (misma serie, número mayor) o ``"series"`` (serie superior). Una
    misma instalada puede salir dos veces si hay parche *y* salto de serie; son
    dos avisos distintos (el botón de la tarjeta y el diálogo de salto).
    """
    stable_builds = [build for build in builds
                     if build.risk == "stable" and not build.fork]
    entries = [entry for entry in installed if _is_stable_install(entry)]
    installed_versions = {version_tuple(entry.version) for entry in entries}

    # La instalada más nueva de cada serie: sobre esa se avisa.
    newest_in_series = {}
    for entry in entries:
        key = _minor_tuple(entry.version)
        current = newest_in_series.get(key)
        if current is None or version_tuple(entry.version) > version_tuple(current.version):
            newest_in_series[key] = entry

    updates = []
    for series_key, entry in newest_in_series.items():
        entry_version = version_tuple(entry.version)
        patch = None
        series = None
        for build in stable_builds:
            build_version = version_tuple(build.version)
            if build_version in installed_versions:
                # Ya la tienes (p. ej. bajada como copia): no hay nada que avisar.
                continue
            build_minor = _minor_tuple(build.version)
            if build_minor == series_key:
                if build_version > entry_version and (
                        patch is None or build.sort_key > patch.sort_key):
                    patch = build
            elif build_minor > series_key:
                if series is None or build.sort_key > series.sort_key:
                    series = build
        if patch is not None:
            updates.append(Update(entry, patch, "patch"))
        if series is not None:
            updates.append(Update(entry, series, "series"))
    return updates
