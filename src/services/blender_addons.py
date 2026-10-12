"""Migrar add-ons y ficheros de preferencias de una versión de Blender a otra.

Qué add-ons tiene una versión (``addons_in``), si cada uno es compatible con la
de destino (``compat_report``, mirando versión mínima, plataforma y los wheels
que trae, más lo que ``addon_checks`` deduce de sus ficheros), dónde caería (``destination_for``) y la copia en sí, con respaldo de
lo que pise y un marcador para poder deshacer.

Dónde vive la configuración de cada versión está en ``blender_config``; los
guardados de "valores de fábrica", en ``blender_snapshots``. Es un módulo de
``services/``: no importa Qt.
"""

import ast
import glob
import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from model.build import version_tuple
from services import addon_checks
from services.blender_config import (BACKUP_SUFFIX, BLOCKED, LOCAL_REPO,
                                     MANIFEST_NAME, MIGRATION_MARKER, OK,
                                     PREFERENCE_FILES, REASON_NATIVE_ABI,
                                     REASON_PLATFORM, REASON_PRE_280,
                                     REASON_REMOVED_API, REASON_REQUIRES_NEWER,
                                     REASON_TOO_NEW, REASON_UNKNOWN_VERSION,
                                     REASON_WHEEL_ABI, STAGING_SUFFIX, WARN,
                                     BlenderConfig)

# Archivos/carpetas del árbol de extensiones que no son repositorios.
_SKIP_EXTENSION_ENTRIES = {".cache", ".local", ".blender_ext", "__pycache__"}

# Wheels con extensión compilada: el nombre lleva la etiqueta de Python
# (``cp311``/``pp311``); compararla evita copiar un addon cuyas dependencias no
# van a cargar. Es heurística: la comprobación fina la hace Blender al arrancar.
_WHEEL_TAG_RE = re.compile(r"(?:cp|pp)(\d+)", re.IGNORECASE)

_ARCH_TOKENS = {
    "x86_64": ("x64",),
    "amd64": ("x64",),
    "arm64": ("arm64", "aarch64"),
}

_OS_TOKENS = {"linux": "linux", "windows": "windows", "darwin": "macos"}


# -------------------------------------------------------------------- addons

# Los dos tipos de addon (``Addon.kind``). La interfaz los usa para agrupar la
# lista, así que se nombran aquí una sola vez y ``services.addons`` los reexporta.
LEGACY = "legacy"        # ``bl_info`` en ``scripts/addons`` (el sistema de siempre)
EXTENSION = "extension"  # ``blender_manifest.toml`` en ``extensions/<repo>`` (4.2+)


@dataclass
class Addon:
    """Un addon instalado en una versión de Blender.

    ``kind`` distingue una extensión (``blender_manifest.toml``, Blender 4.2+)
    de un addon legacy (``bl_info``). ``module`` es el nombre con el que Blender
    lo habilita: el nombre de la carpeta para los legacy y
    ``bl_ext.<repo>.<id>`` para las extensiones.
    """

    kind: str
    module: str
    name: str
    version: str
    min_version: str
    max_version: str
    path: Path
    repo: str = ""
    platforms: tuple = ()
    wheels: tuple = ()
    # Lo que ``addon_checks.scan`` encontró en sus ficheros: módulos compilados
    # con etiqueta de Python y módulos de Blender ya retirados que importa.
    natives: tuple = ()
    removed_imports: tuple = ()


def read_manifest(folder: Path) -> dict:
    """Lee ``blender_manifest.toml``; si falta o está roto, ``{}``."""
    path = Path(folder) / MANIFEST_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def read_bl_info(init_py: Path) -> dict:
    """Extrae el ``bl_info`` de un addon legacy sin ejecutarlo.

    El diccionario es una asignación literal en la mayoría de addons, así que
    se lee con ``ast.literal_eval`` (no se importa el módulo: importarlo
    ejecutaría código del addon, que es justo lo que queremos evitar). Si no se
    puede evaluar (se construye dinámicamente), cae a una búsqueda por regex.
    """
    try:
        source = init_py.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "bl_info" not in names:
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            break
        return value if isinstance(value, dict) else {}
    # Plan B: un addon que arma el bl_info por partes. Se busca un "blender":
    # con una tupla de números.
    match = re.search(r'"blender"\s*:\s*\(([^)]*)\)', source)
    if match:
        numbers = re.findall(r"\d+", match.group(1))
        if numbers:
            return {"blender": [int(n) for n in numbers]}
    return {}


def _tuple_text(value) -> str:
    """Convierte ``(4, 5, 0)`` o ``"4.5.0"`` en el texto ``"4.5.0"``."""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return ".".join(str(part) for part in value)
    return str(value)


def addons_in(config: BlenderConfig) -> list[Addon]:
    """Todos los addons y extensiones instalados en una versión.

    No toca red ni ejecuta nada del addon: solo lee ficheros de metadatos.
    """
    addons = _legacy_addons(config) + _extensions(config)
    for addon in addons:
        addon.natives, addon.removed_imports = addon_checks.scan(addon.path)
    addons.sort(key=lambda item: (item.kind, item.name.lower(), item.module))
    return addons


def _legacy_addons(config: BlenderConfig) -> list[Addon]:
    """Addons legacy: ``scripts/addons/<mod>/__init__.py`` (o ``<mod>.py``)."""
    folder = config.addons_dir
    if not folder.is_dir():
        return []
    result = []
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_dir():
            init = entry / "__init__.py"
            if not init.is_file():
                continue
            module = entry.name
        elif entry.suffix == ".py":
            init = entry
            module = entry.stem
        else:
            continue
        info = read_bl_info(init)
        result.append(Addon(
            kind=LEGACY,
            module=module,
            name=str(info.get("name") or module),
            version=_tuple_text(info.get("version")),
            min_version=_tuple_text(info.get("blender")),
            max_version="",
            path=entry,
        ))
    return result


def _extension_repos(config: BlenderConfig) -> dict:
    """Repositorios de extensiones: ``extensions/<repo>/`` -> carpeta.

    El repositorio local de las extensiones instaladas a mano es
    ``user_default``; las bajadas de extensions.blender.org van a
    ``blender_org``. Se aceptan todos los que tengan pinta de repositorio.
    """
    root = config.extensions_dir
    repos = {}
    if not root.is_dir():
        return repos
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name not in _SKIP_EXTENSION_ENTRIES:
            repos[entry.name] = entry
    return repos


def _extensions(config: BlenderConfig) -> list[Addon]:
    """Extensiones con ``blender_manifest.toml`` en cada repositorio."""
    result = []
    for repo, folder in _extension_repos(config).items():
        for entry in sorted(folder.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            manifest = read_manifest(entry)
            if not manifest:
                continue
            addon_id = str(manifest.get("id") or entry.name)
            result.append(Addon(
                kind=EXTENSION,
                module=f"bl_ext.{repo}.{addon_id}",
                name=str(manifest.get("name") or addon_id),
                version=str(manifest.get("version") or ""),
                min_version=str(manifest.get("blender_version_min") or ""),
                max_version=str(manifest.get("blender_version_max") or ""),
                path=entry,
                repo=repo,
                platforms=tuple(manifest.get("platforms") or ()),
                wheels=tuple(_wheels_of(entry, manifest)),
            ))
    return result


def _wheels_of(folder: Path, manifest: dict) -> list[str]:
    """Nombres de los wheels de una extensión (los declara o están en disco)."""
    names = [str(item) for item in (manifest.get("wheels") or [])]
    if names:
        return names
    wheels_dir = Path(folder) / "wheels"
    if wheels_dir.is_dir():
        names = [item.name for item in sorted(wheels_dir.glob("*.whl"))]
    return names


# ----------------------------------------------------------- compatibilidad


def _platform_ok(platforms, target_platform: str, target_arch: str) -> bool:
    """True si el addon declara soporte para este sistema.

    En el manifest las plataformas son ``<os>-<arch>`` (``linux-x64``,
    ``macos-arm64``...); se admite ``aarch64`` como sinónimo de ``arm64``.
    """
    if not platforms:
        return True
    os_token = _OS_TOKENS.get(target_platform, target_platform)
    arch_tokens = _ARCH_TOKENS.get(target_arch, (target_arch,) if target_arch else ())
    for entry in platforms:
        text = str(entry or "").lower()
        if "-" not in text:
            continue
        os_part, _, arch_part = text.partition("-")
        if os_part != os_token:
            continue
        if not arch_tokens or arch_part in arch_tokens:
            return True
    return False


def _python_tag(raw: str) -> str:
    """Normaliza ``"3.13"`` o ``"311"`` a ``"313"`` (mayor + menor a 2 dígitos)."""
    digits = re.sub(r"[^\d]", "", raw or "")
    if len(digits) < 2:
        return ""
    # ``cp311`` -> mayor 3, menor 11; ``cp39`` -> mayor 3, menor 9.
    major, minor = digits[0], digits[1:]
    return f"{major}{int(minor):02d}"


def _wheel_python_tag(wheel_name: str) -> str:
    """Etiqueta de Python de un wheel, o "" si es de Python puro.

    Un wheel se llama ``nombre-version-[build-]pytag-abitag-plattag.whl``. Se
    respeta la ABI cuando la hay: ``abi3`` (ABI estable) vale para cualquier
    Python 3, y ``cp3XX`` manda sobre la etiqueta de Python. Si la ABI no dice
    nada (``none``), vale la etiqueta de Python.
    """
    parts = str(wheel_name).split("-")
    if len(parts) < 3:
        return ""
    python_tag, abi_tag = parts[-3], parts[-2]
    if abi_tag.lower().startswith("abi"):
        # ABI estable (``abi3``): vale para cualquier Python 3, no hay conflicto.
        return ""
    for candidate in (abi_tag, python_tag):
        match = _WHEEL_TAG_RE.fullmatch(candidate or "")
        if match:
            return _python_tag(match.group(1))
    return ""


def _wheel_package(wheel_name: str) -> str:
    """Nombre del paquete de un wheel: lo que va antes del primer guion.

    Un wheel se llama ``paquete-version-pytag-abitag-plattag.whl`` y en el
    manifiesto viene con su ruta (``./wheels/pillow-11.1.0-...``), así que
    primero nos quedamos con el nombre del fichero.
    """
    base = str(wheel_name).replace("\\", "/").split("/")[-1]
    return base.split("-")[0].lower()


def wheel_problem(wheels, target_python: str) -> str:
    """Paquete cuyas dependencias no sirven para ``target_python``, o "".

    **Se mira paquete a paquete, no wheel a wheel**, y este es el motivo: una
    extensión bien empaquetada trae *un wheel por plataforma y por versión de
    Python*, y Blender instala el que le toca al arrancar. MatPlus, por
    ejemplo, declara Pillow en ``cp311`` (para Blender 4.2/5.0) y en ``cp313``
    (para 5.1+). Avisar en cuanto **uno** no encajaba marcaba esas extensiones
    como dudosas siempre, dijeras la versión de destino que dijeras: el aviso
    saltaba igual apuntando a 5.2 que a 5.3, que usan el mismo Python 3.13, y
    por eso parecía que comparaba los dos Blender entre sí. Solo hay problema
    de verdad cuando un paquete trae wheels compilados y **ninguno** vale.

    Los de Python puro (``py3-none-any``) y los de ABI estable (``abi3``) se
    ignoran: valen para cualquier Python 3. La plataforma la valida Blender al
    arrancar; aquí solo se mira la etiqueta de Python/ABI del nombre.
    """
    want = _python_tag(target_python)
    if not want:
        return ""
    # Por paquete: las etiquetas de sus wheels compilados (los puros no cuentan).
    tags_by_package = {}
    for name in wheels:
        tag = _wheel_python_tag(name)
        if not tag:
            continue
        tags_by_package.setdefault(_wheel_package(name), set()).add(tag)
    for package, tags in tags_by_package.items():
        if want not in tags:
            return package
    return ""


def compat_report(addon: Addon, target_version: str, target_platform: str = "",
                  target_arch: str = "", target_python: str = "") -> tuple:
    """Estado de un addon frente a la versión destino.

    Devuelve ``(status, reason)`` con ``status`` en ``OK``/``WARN``/``BLOCKED``
    y ``reason`` una de las claves ``REASON_*`` (o ``""``). Es pura: no toca el
    disco, para poder probarla con addons de mentira.
    """
    target = version_tuple(target_version)
    minimum = version_tuple(addon.min_version)
    if addon.min_version and minimum and target and target < minimum:
        return BLOCKED, REASON_REQUIRES_NEWER
    maximum = version_tuple(addon.max_version)
    if addon.max_version and maximum and target and target >= maximum:
        return BLOCKED, REASON_TOO_NEW
    if addon.platforms and not _platform_ok(
            addon.platforms, target_platform, target_arch):
        return BLOCKED, REASON_PLATFORM
    # Lo deducido de los ficheros va antes que los wheels: un addon de antes
    # de 2.80 o que importa ``bgl`` falla al activarse, pase lo que pase con
    # sus dependencias.
    if addon.kind == LEGACY and addon_checks.predates_api_break(minimum, target):
        return WARN, REASON_PRE_280
    if addon_checks.removed_module(addon.removed_imports, target):
        return WARN, REASON_REMOVED_API
    if addon.natives and target_python and addon_checks.native_problem(
            addon.natives, target_python):
        return WARN, REASON_NATIVE_ABI
    if addon.wheels and target_python and wheel_problem(
            addon.wheels, target_python):
        return WARN, REASON_WHEEL_ABI
    if not addon.min_version:
        # Sin versión mínima no hay forma de saberlo: se copia, pero se avisa.
        return WARN, REASON_UNKNOWN_VERSION
    return OK, ""


# ------------------------------------------------------------- plan y copia


@dataclass
class AddonPlan:
    """Qué haríamos con un addon en la migración."""

    addon: Addon
    status: str
    reason: str
    destination: Path
    selected: bool = True
    # True si en la versión origen estaba **activado**. Se copia con el mismo
    # estado que tenía (no se activa por el simple hecho de migrar): si allí
    # estaba apagado, se queda apagado, que es lo que el usuario espera.
    was_enabled: bool = False
    # Dato suelto que acompaña al motivo, para que la interfaz pueda ser
    # concreta en vez de genérica: el paquete con ``REASON_WHEEL_ABI``, el
    # fichero compilado con ``REASON_NATIVE_ABI`` y el módulo retirado con
    # ``REASON_REMOVED_API``. En los wheels lleva el **nombre del paquete**: decir "numpy no trae una
    # compilación para Python 3.13" se puede accionar; "sus dependencias son de
    # otro Python" no.
    detail: str = ""
    # Python que embebe la versión de destino ("3.13"), para poder nombrarlo en
    # el aviso. Vacío si no se conoce esa serie (ver ``python_for_version``).
    target_python: str = ""

    @property
    def blocked(self) -> bool:
        """True si no se debe copiar (incompatible)."""
        return self.status == BLOCKED

    @property
    def enable_module(self) -> str:
        """Módulo que Blender destino tiene que habilitar.

        Las extensiones se copian al repositorio local, así que su módulo en
        destino es ``bl_ext.user_default.<id>`` aunque en origen fuera otro.
        """
        if self.addon.kind == EXTENSION:
            return f"bl_ext.{LOCAL_REPO}.{addon_id_of(self.addon.module)}"
        return self.addon.module


def addon_id_of(module: str) -> str:
    """Id de un addon, sin repositorio ni prefijo.

    ``bl_ext.user_default.MatPlus`` -> ``MatPlus``; ``mi_addon`` -> ``mi_addon``.
    Es la clave para comparar el estado activado entre versiones, porque el
    módulo cambia (el repositorio de origen puede no ser el de destino).
    """
    return (module or "").rsplit(".", 1)[-1]


def destination_for(addon: Addon, target: BlenderConfig) -> Path:
    """Dónde quedaría el addon copiado en la versión destino.

    Las extensiones se instalan en el repositorio local (``user_default``),
    aunque en origen vinieran de otro; así no hay que dar de alta repositorios
    remotos solo para copiar una carpeta.
    """
    if addon.kind == EXTENSION:
        return target.extensions_dir / LOCAL_REPO / addon_id_of(addon.module)
    return target.addons_dir / addon.path.name


def plan_migration(source: BlenderConfig, target: BlenderConfig,
                   target_platform: str = "", target_arch: str = "",
                   target_python: str = "", enabled_ids=()) -> list[AddonPlan]:
    """Lista de addons de ``source`` y qué pasaría al llevarlos a ``target``.

    ``enabled_ids`` son los ids (``addon_id_of``) que estaban **activados** en
    origen; con eso se marca ``was_enabled`` y la interfaz sabe cuáles hay que
    activar en destino (solo esos). Si se deja vacío, se asume que ninguno lo
    estaba, que es lo prudente.
    """
    enabled = set(enabled_ids or ())
    plans = []
    for addon in addons_in(source):
        status, reason = compat_report(addon, target.version, target_platform,
                                       target_arch, target_python)
        detail = _reason_detail(addon, reason, target.version, target_python)
        plans.append(AddonPlan(
            addon=addon,
            status=status,
            reason=reason,
            detail=detail,
            target_python=target_python,
            destination=destination_for(addon, target),
            # Los incompatibles llegan sin marcar: nunca se copian sin querer.
            selected=status != BLOCKED,
            was_enabled=addon_id_of(addon.module) in enabled,
        ))
    return plans


def _reason_detail(addon: Addon, reason: str, target_version: str,
                   target_python: str) -> str:
    """El dato concreto que nombra el aviso (paquete, fichero o módulo)."""
    if reason == REASON_WHEEL_ABI:
        return wheel_problem(addon.wheels, target_python)
    if reason == REASON_NATIVE_ABI:
        return addon_checks.native_problem(addon.natives, target_python)
    if reason == REASON_REMOVED_API:
        return addon_checks.removed_module(addon.removed_imports,
                                           version_tuple(target_version))
    return ""


@dataclass
class MigrationResult:
    """Resumen de una migración aplicada (o simulada)."""

    copied: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    backed_up: list = field(default_factory=list)
    failed: list = field(default_factory=list)   # (plan, mensaje)
    dry_run: bool = False
    marker: Path | None = None
    modules: list = field(default_factory=list)


def delete_path(path: Path) -> None:
    """Borra un fichero, un enlace o una carpeta entera; si no está, no hace nada.

    Un enlace simbólico se borra **él**, no lo que apunta: así quitar un addon
    de desarrollo enlazado deja intacta la carpeta del proyecto.

    Si no se puede borrar (sin permiso, un fichero abierto en Windows...) lanza
    ``OSError``. Antes se tragaba el error y quien llamaba daba por hecho el
    borrado: el gestor de addons decía "borrado" con el addon todavía en su
    sitio. Para limpiar restos, donde fallar no importa, está ``_discard``.
    """
    path = Path(path)
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _discard(path: Path) -> None:
    """Borra un resto (un temporal, una copia apartada) sin quejarse si falla.

    Se usa al recoger después de un error, donde un segundo fallo no puede
    tapar el primero, que es el que hay que contar.
    """
    try:
        delete_path(path)
    except OSError:
        pass


def copy_entry(source: Path, destination: Path) -> None:
    """Copia un addon, sea una carpeta o un addon legacy de un solo ``.py``.

    Un addon legacy puede ser un fichero suelto (``scripts/addons/mi_addon.py``)
    y ``shutil.copytree`` con un fichero revienta con ``NotADirectoryError``:
    las carpetas van con ``copytree`` y los ficheros con ``copy2`` (que, como
    ``copytree``, conserva las fechas).

    En las carpetas, ``symlinks=True`` como el propio ``preferences.copy_prev``
    de Blender: los enlaces de dentro (muy típicos en desarrollo) tienen que
    seguir apuntando a su sitio, no duplicarse.
    """
    source = Path(source)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination)


def park_existing(destination: Path) -> Path | None:
    """Aparta lo que ya hay en ``destination`` a su copia de seguridad.

    **Una sola copia por destino, reutilizable**: los usuarios migran más de una
    vez (y a veces en la dirección contraria), y con un sufijo por fecha se
    acumulaba un ``.bak`` por intento sin que nadie los viera. Se reutiliza el
    mismo nombre y se pisa. Cuando lo que hay en destino es una copia nuestra
    de una migración anterior, no se llega a llamar a esto (pisaría el
    original): ver ``_copy_with_undo``.

    Devuelve la ruta del respaldo, o ``None`` si no había nada que apartar. Un
    enlace simbólico roto no "existe" para ``Path.exists`` pero sí estorba al
    copiar encima: se quita sin más (no hay nada que respaldar).
    """
    if destination.is_symlink() and not destination.exists():
        delete_path(destination)
        return None
    if not destination.exists():
        return None
    # Quita un sufijo .bak ya existente, para no respaldar el respaldo.
    base = destination.name.split(BACKUP_SUFFIX, 1)[0]
    # Limpia restos de intentos anteriores (y de versiones viejas de la app,
    # que sí ponían fecha) antes de reutilizar el nombre. El nombre se escapa:
    # un addon llamado ``addon[1]`` es un patrón de glob que casa con
    # ``addon1``, y sin escapar se borraba el respaldo de otro addon.
    for old in destination.parent.glob(f"{glob.escape(base)}{BACKUP_SUFFIX}*"):
        if old != destination:
            delete_path(old)
    backup = destination.with_name(f"{base}{BACKUP_SUFFIX}")
    destination.rename(backup)
    return backup


def replace_with_copy(source: Path, destination: Path,
                      discard_existing: bool = False) -> Path | None:
    """Pone una copia de ``source`` en ``destination`` sin dejarlo a medias.

    El orden es lo que importa. Antes se apartaba primero lo que hubiera y
    luego se copiaba: si la copia fallaba a mitad (disco lleno, un fichero sin
    permiso de lectura), el destino quedaba con media copia y el original
    escondido en el ``.bak``. Ahora:

    1. Se copia a un **temporal hermano** (misma carpeta, así que el paso 3 es
       un simple renombrado y no una copia entre discos). Empieza por punto
       para que ni Blender ni ``addons_in`` lo tomen por un addon.
    2. Solo si la copia terminó, se aparta lo que hubiera (``park_existing``).
    3. Se renombra el temporal a su sitio con ``os.replace``.

    Si falla el paso 1, el destino no se ha tocado; si falla el 3, se devuelve
    el respaldo a su sitio. En los dos casos se relanza el ``OSError`` para que
    quien llama lo cuente. Devuelve la ruta del respaldo, o ``None``.

    ``discard_existing`` es para cuando lo que hay en destino es **una copia
    nuestra** de una migración anterior: no se respalda (pisaría el ``.bak``
    con el original del usuario, ver ``_copy_with_undo``), se tira en cuanto la
    copia nueva está en su sitio.
    """
    destination = Path(destination)
    staging = destination.with_name(f".{destination.name}{STAGING_SUFFIX}")
    # Restos de un intento anterior que se cortó (la app se cerró a medias).
    delete_path(staging)
    try:
        copy_entry(source, staging)
    except OSError:
        _discard(staging)
        raise
    backup = displaced = None
    try:
        if not discard_existing:
            backup = park_existing(destination)
        elif destination.exists() or destination.is_symlink():
            # Nuestra copia vieja se aparta (no se borra todavía) por si el
            # paso siguiente falla y hay que devolverla.
            displaced = destination.with_name(
                f".{destination.name}{STAGING_SUFFIX}-old")
            delete_path(displaced)
            destination.rename(displaced)
        os.replace(staging, destination)
    except OSError:
        previous = backup or displaced
        if previous is not None:
            try:
                previous.rename(destination)
            except OSError:
                # Lo de antes sigue entero donde se apartó: no se ha perdido,
                # solo no se pudo devolver a su sitio. El error que importa es
                # el de arriba, que es el que se relanza.
                pass
        _discard(staging)
        raise
    if displaced is not None:
        # La copia nueva ya está en su sitio; la vieja era nuestra y sobra. Si
        # no se puede borrar, se queda oculta (empieza por punto) sin estorbar.
        _discard(displaced)
    return backup


def _copies_in_marker(target: BlenderConfig, key: str) -> dict:
    """Destinos que copió la última migración (``key``), con su respaldo.

    Devuelve ``{ruta destino: respaldo o None}``. Es lo que permite reconocer,
    al migrar otra vez, que lo que hay en destino lo puso BlendTrack. Los
    marcadores de versiones anteriores de la app anotaban dos entradas por copia
    (una con respaldo y otra sin él): se queda la que lo tiene.
    """
    payload = read_migration_marker(target)
    if payload.get("version") != target.version:
        return {}
    copies = {}
    for action in payload.get(key) or []:
        path = action.get("path")
        if not path:
            continue
        backup = action.get("backup")
        backup = Path(backup) if backup and Path(backup).exists() else None
        copies[path] = copies.get(path) or backup
    return copies


def _copy_with_undo(source: Path, destination: Path, previous: dict,
                    backed_up: list) -> dict:
    """Copia ``source`` a ``destination`` y devuelve su entrada del marcador.

    **Migrar dos veces no puede perder el original.** El respaldo es uno por
    destino; si la segunda migración apartaba lo que había, apartaba *nuestra
    propia copia de la primera* y pisaba con ella el ``.bak``, que era el único
    sitio donde seguía el addon (o las preferencias) que el usuario tenía antes
    de migrar. Así que, si el marcador dice que ese destino lo copiamos
    nosotros (``previous``, de ``_copies_in_marker``), la copia vieja se tira
    sin respaldarla y se hereda el respaldo original: deshacer vuelve al estado
    de antes de BlendTrack, no a una copia intermedia.

    El precio, a sabiendas: si el usuario retocó a mano esa copia entre una
    migración y otra, el retoque se pierde. Es una copia del origen, que sigue
    intacto, y perder el original era mucho peor (no había forma de recuperarlo).

    Con respaldo, deshacer lo devuelve a su sitio; sin él (el destino no
    existía), deshacer borra la copia. Los respaldos hechos ahora se anotan en
    ``backed_up``, que es lo que enseña el resumen de la interfaz.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    ours = str(destination) in previous
    backup = replace_with_copy(source, destination, discard_existing=ours)
    if ours:
        backup = previous[str(destination)]
    elif backup is not None:
        backed_up.append(backup)
    if backup is None:
        return {"path": str(destination)}
    return {"path": str(destination), "backup": str(backup)}


def _write_marker(target: BlenderConfig, actions: list, key: str = "actions") -> Path:
    """Anota en la carpeta destino qué se copió (para poder deshacer).

    ``key`` separa lo que copió la migración de addons (``actions``) de lo que
    copió la de preferencias (``preferences``): el deshacer lee las dos listas.
    """
    path = target.root / MIGRATION_MARKER
    # Se conserva lo que ya anotase la otra migración (addons y luego
    # preferencias, o al revés): antes se sobrescribía el fichero entero y el
    # "deshacer" de la primera se perdía en silencio.
    payload = read_migration_marker(target)
    if payload.get("version") != target.version:
        payload = {}
    payload.update({
        "version": target.version,
        "date": datetime.now().isoformat(timespec="seconds"),
        key: actions,
    })
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass
    return path


def apply_migration(plans, target: BlenderConfig,
                    dry_run: bool = False) -> MigrationResult:
    """Copia los addons marcados y devuelve el resumen.

    Es conservador a propósito: lo que ya exista en destino **no se borra**,
    se aparta como ``.blendtrack-bak`` (por si el usuario tenía su propia
    versión de ese addon), y solo cuando la copia nueva ya está completa (ver
    ``replace_with_copy``). Con ``dry_run`` no toca el disco.
    """
    result = MigrationResult(dry_run=dry_run)
    actions = []
    previous = _copies_in_marker(target, "actions")
    for plan in plans:
        if not plan.selected or plan.blocked:
            result.skipped.append(plan)
            continue
        if dry_run:
            result.copied.append(plan)
            continue
        try:
            actions.append(_copy_with_undo(plan.addon.path,
                                           Path(plan.destination), previous,
                                           result.backed_up))
            result.copied.append(plan)
            result.modules.append(plan.enable_module)
        except OSError as error:
            result.failed.append((plan, str(error)))
    if not dry_run and actions:
        result.marker = _write_marker(target, actions)
    return result


@dataclass
class UndoResult:
    """Resumen de deshacer la última migración sobre un destino."""

    restored: list = field(default_factory=list)   # (destino, backup)
    removed: list = field(default_factory=list)    # rutas borradas
    failed: list = field(default_factory=list)     # (ruta, mensaje)
    marker_found: bool = False
    dry_run: bool = False


def read_migration_marker(target: BlenderConfig) -> dict:
    """Lee el marcador de la última migración de esa versión, o ``{}``."""
    path = target.root / MIGRATION_MARKER
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _undoable(target: BlenderConfig, raw) -> Path | None:
    """La ruta del marcador si deshacer puede tocarla, o ``None``.

    ``Path("")`` no es falso: es ``Path(".")``, el directorio actual. Con una
    entrada vacía en el marcador, deshacer habría borrado **la carpeta desde la
    que corre la app**. Y un marcador editado a mano (o corrupto) podría
    apuntar a cualquier sitio del disco. Por eso se exige que la ruta esté
    *dentro* (y no sea la raíz) de alguna de las carpetas donde copia la
    migración: ``config``, ``scripts`` o ``extensions`` de esa versión.

    Se compara con ``os.path.abspath`` y no con ``resolve``: un addon enlazado
    es un symlink que apunta fuera (a la carpeta del proyecto) y lo que se
    deshace es el enlace, no su destino.
    """
    if not raw or not isinstance(raw, str):
        return None
    path = Path(os.path.abspath(raw))
    for root in (target.config_dir, target.scripts_dir, target.extensions_dir):
        base = Path(os.path.abspath(root))
        if path != base and path.is_relative_to(base):
            return path
    return None


def undo_migration(target: BlenderConfig, dry_run: bool = False) -> UndoResult:
    """Deshace la última migración: revierte lo que se guardase como backup.

    Es deliberadamente conservador: solo toca lo que el marcador dice que hemos
    copiado nosotros. Lo que no tenía backup (porque el addon no existía antes)
    se **borra**, que es la única forma de que el estado vuelva atrás; si el
    usuario hubiera puesto ahí algo después, se perdería, así que la interfaz
    avisa antes de llamar a esto.

    Al terminar se elimina el marcador, de modo que un segundo "deshacer" no
    restaura nada (evita el clásico doble-clic que deshace de más).

    El marcador es un JSON en disco y no se cree a ciegas: deshacer **borra**
    carpetas enteras, así que solo se toca lo que cae dentro de las carpetas
    de esa versión donde copia la migración (ver ``_undoable``). Una entrada
    vacía o que apunte fuera se salta.
    """
    result = UndoResult(dry_run=dry_run)
    payload = read_migration_marker(target)
    actions = (payload.get("actions") or []) + (payload.get("preferences") or [])
    if not actions:
        return result
    result.marker_found = True
    if dry_run:
        return result
    # En orden inverso: lo último copiado, lo primero en deshacerse.
    for action in reversed(actions):
        destination = _undoable(target, action.get("path"))
        raw_backup = action.get("backup")
        backup = _undoable(target, raw_backup)
        if destination is None or (raw_backup and backup is None):
            continue
        try:
            if backup is not None and backup.exists():
                delete_path(destination)
                backup.rename(destination)
                result.restored.append((destination, backup))
            elif destination.exists():
                delete_path(destination)
                result.removed.append(destination)
        except OSError as error:
            result.failed.append((destination, str(error)))
    if not result.failed:
        try:
            (target.root / MIGRATION_MARKER).unlink()
        except OSError:
            pass
    return result


def summary_counts(plans) -> dict:
    """Cuenta addons por estado, para el resumen de la interfaz."""
    counts = {OK: 0, WARN: 0, BLOCKED: 0}
    for plan in plans:
        counts[plan.status] = counts.get(plan.status, 0) + 1
    return counts


@dataclass
class PreferenceItem:
    """Un fichero de preferencias y qué pasaría al migrarlo."""

    key: str
    filename: str
    source: Path
    destination: Path
    exists: bool          # en origen
    overwrites: bool      # ya hay uno en destino
    selected: bool = True

    @property
    def safe(self) -> bool:
        """True si se puede copiar (está en origen)."""
        return self.exists


def preference_plan(source: BlenderConfig, target: BlenderConfig) -> list:
    """Qué ficheros de preferencias se podrían copiar y con qué riesgo.

    No toca el disco más allá de comprobar existencia: la decisión de copiar
    (y el aviso de que pisa lo que ya hubiera) es del usuario.
    """
    items = []
    for key, filename in PREFERENCE_FILES:
        origin = source.config_dir / filename
        destination = target.config_dir / filename
        items.append(PreferenceItem(
            key=key,
            filename=filename,
            source=origin,
            destination=destination,
            exists=origin.is_file(),
            overwrites=destination.exists(),
            # ``startup.blend`` no llega marcado: es un ``.blend`` entero y
            # sobrescribir la escena por defecto es más agresivo que el resto.
            selected=origin.is_file() and key != "startup",
        ))
    return items


@dataclass
class PreferenceResult:
    """Resumen de una migración de preferencias (aplicada o simulada)."""

    copied: list = field(default_factory=list)
    backed_up: list = field(default_factory=list)
    failed: list = field(default_factory=list)   # (item, mensaje)
    dry_run: bool = False
    marker: Path | None = None


def copy_preference_files(items, target: BlenderConfig,
                          dry_run: bool = False) -> PreferenceResult:
    """Copia los ficheros de preferencias marcados, con copia de seguridad.

    ``shutil.copy2`` (no ``copy``): conserva los tiempos del fichero, que es lo
    que Blender usa para decidir si las preferencias están al día. Lo que ya
    exista **no se borra**: se aparta como ``.blendtrack-bak``.
    """
    result = PreferenceResult(dry_run=dry_run)
    actions = []
    previous = _copies_in_marker(target, "preferences")
    for item in items:
        if not item.selected or not item.safe:
            continue
        if dry_run:
            result.copied.append(item)
            continue
        try:
            actions.append(_copy_with_undo(item.source, Path(item.destination),
                                           previous, result.backed_up))
            result.copied.append(item)
        except OSError as error:
            result.failed.append((item, str(error)))
    if not dry_run and actions:
        result.marker = _write_marker(target, actions, key="preferences")
    return result
