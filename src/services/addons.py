"""Gestión de los addons y extensiones de una versión instalada de Blender.

Lista lo que hay en las carpetas de usuario (``scripts/addons`` y
``extensions/<repo>``) con su estado —activado, tipo, si es un enlace de
desarrollo—, y permite activar/desactivar sin abrir Blender, instalar desde
``.zip``/``.py``, enlazar una carpeta de desarrollo (symlink) y borrar.

Es la versión "de diario" de lo que hace la migración: allí se copian addons de
una versión a otra; aquí se gestionan los de una versión concreta.

Es un módulo de ``services/``: **no importa Qt**. Lo que habla con Blender
(activar/desactivar) vive en ``blender_runner``; leer manifiestos y ``bl_info``,
en ``blender_config``.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from services import blender_addons as baddons
from services import blender_config as bc
from services import blender_runner, extractor
# Los tipos de addon se definen junto a ``Addon``; se reexportan porque la
# interfaz los usa como ``services.addons.LEGACY``/``EXTENSION``.
from services.blender_addons import EXTENSION, LEGACY


class AddonError(Exception):
    """Fallo al gestionar un addon.

    ``reason`` es una clave corta para que la interfaz elija el mensaje (y la
    traduzca); ``detail`` es el texto del sistema, para el tooltip o el log.
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass
class AddonState:
    """Un addon con lo que la interfaz necesita para pintarlo."""

    addon: baddons.Addon
    enabled: bool = False
    linked: bool = False

    @property
    def kind(self) -> str:
        """``"extension"`` o ``"legacy"``: de eso depende dónde vive y cómo se activa."""
        return self.addon.kind

    @property
    def name(self) -> str:
        """Nombre legible que declara el addon (el que se enseña en la lista)."""
        return self.addon.name

    @property
    def module(self) -> str:
        """Módulo con el que Blender lo conoce (``bl_ext.<repo>.<id>`` o el suyo)."""
        return self.addon.module


def list_addons(entry, platform: str, env=None) -> list:
    """Addons de esa versión con su estado (activado, tipo, enlace).

    El estado *activado* se pregunta a Blender una sola vez (``enabled_addons``)
    y se cruza por ``addon_id_of``: el módulo puede llevar repositorio
    (``bl_ext.<repo>.<id>``) y no coincidir literalmente con el de la carpeta.
    """
    config = bc.config_for(entry.version, platform, env,
                          fork=getattr(entry, "fork", ""))
    enabled = set()
    executable = getattr(entry, "executable", None)
    if executable:
        enabled = {baddons.addon_id_of(name)
                   for name in blender_runner.enabled_addons(executable)}
    states = []
    for addon in baddons.addons_in(config):
        states.append(AddonState(
            addon=addon,
            enabled=baddons.addon_id_of(addon.module) in enabled,
            linked=addon.path.is_symlink(),
        ))
    return states


def set_enabled(entry, module: str, enabled: bool) -> dict:
    """Activa o desactiva un addon sin abrir Blender."""
    executable = getattr(entry, "executable", None)
    if not executable:
        raise AddonError("no_executable")
    result = blender_runner.set_addons(
        executable,
        enable=[module] if enabled else [],
        disable=[] if enabled else [module])
    if result.get("errors"):
        detail = "; ".join(str(item.get("error") or "")
                           for item in result["errors"])
        raise AddonError("failed", detail)
    return result


def install(entry, source, platform: str, env=None) -> dict:
    """Instala un addon desde un ``.zip`` o un ``.py``.

    Un ``.py`` suelto va a ``scripts/addons``. Un ``.zip`` se extrae y se mira
    qué trae: si hay ``blender_manifest.toml`` es una extensión y va al
    repositorio local (``extensions/user_default/<id>``); si hay ``__init__.py``
    (o un ``.py`` suelto) es un addon legacy y va a ``scripts/addons``. Lo que
    ya hubiera en el destino se aparta como copia de seguridad.
    """
    source = Path(source)
    if not source.is_file():
        raise AddonError("missing_file", str(source))
    config = bc.config_for(entry.version, platform, env,
                          fork=getattr(entry, "fork", ""))
    suffix = source.suffix.lower()

    if suffix == ".py":
        destination = config.addons_dir / source.name
        _install_copy(source, destination)
        return {"kind": LEGACY, "module": source.stem,
                "destination": destination}

    if suffix != ".zip":
        raise AddonError("unsupported", suffix)

    with tempfile.TemporaryDirectory(prefix="blendtrack-addon-") as tmp:
        try:
            extracted = Path(extractor.extract(source, Path(tmp)))
        except Exception as error:  # noqa: BLE001 - zip roto, truncado, sin permisos...
            raise AddonError("broken_archive", str(error)) from error
        kind, root = _find_addon_root(extracted)
        if kind == EXTENSION:
            manifest = baddons.read_manifest(root)
            addon_id = str(manifest.get("id") or root.name)
            destination = config.extensions_dir / bc.LOCAL_REPO / addon_id
            _install_copy(root, destination)
            return {"kind": EXTENSION,
                    "module": f"bl_ext.{bc.LOCAL_REPO}.{addon_id}",
                    "destination": destination}
        # Legacy: si no hay __init__.py es un addon de un solo fichero.
        if not (root / "__init__.py").is_file():
            single = next(iter(root.glob("*.py")), None)
            if single is not None:
                destination = config.addons_dir / single.name
                _install_copy(single, destination)
                return {"kind": LEGACY, "module": single.stem,
                        "destination": destination}
        destination = config.addons_dir / root.name
        _install_copy(root, destination)
        return {"kind": LEGACY, "module": root.name,
                "destination": destination}


def link(entry, folder, platform: str, env=None) -> dict:
    """Enlaza una carpeta de desarrollo en su sitio (symlink).

    El addon sigue viviendo en la carpeta del proyecto y Blender lo carga desde
    ahí. En Windows los symlinks de carpeta necesitan permisos (o el modo
    desarrollador); si falla, se avisa con el motivo.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise AddonError("missing_folder", str(folder))
    config = bc.config_for(entry.version, platform, env,
                          fork=getattr(entry, "fork", ""))
    if (folder / bc.MANIFEST_NAME).is_file():
        manifest = baddons.read_manifest(folder)
        addon_id = str(manifest.get("id") or folder.name)
        destination = config.extensions_dir / bc.LOCAL_REPO / addon_id
        module = f"bl_ext.{bc.LOCAL_REPO}.{addon_id}"
    elif (folder / "__init__.py").is_file() or any(folder.glob("*.py")):
        destination = config.addons_dir / folder.name
        module = folder.name
    else:
        raise AddonError("no_addon", str(folder))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        backup = baddons.park_existing(destination)
    except OSError as error:
        raise AddonError("symlink_failed", str(error)) from error
    try:
        destination.symlink_to(folder.resolve(), target_is_directory=True)
    except OSError as error:
        # Sin enlace, lo que había vuelve a su sitio: un enlace fallido (en
        # Windows, típico por falta de permiso) no puede costarle al usuario el
        # addon que ya tenía instalado.
        if backup is not None:
            try:
                backup.rename(destination)
            except OSError:
                pass
        raise AddonError("symlink_failed", str(error)) from error
    return {"kind": EXTENSION if module.startswith("bl_ext.") else LEGACY,
            "module": module, "destination": destination}


def remove(entry, addon: baddons.Addon, platform: str, env=None) -> None:
    """Borra los ficheros de un addon (desactivándolo antes si hace falta).

    Si es un enlace de desarrollo, borra el enlace y deja la carpeta original
    intacta (``delete_path`` respeta los symlinks).

    Si no se puede borrar (sin permiso, un fichero abierto en Windows) lanza
    ``AddonError``: antes el fallo se perdía por el camino y la interfaz decía
    "borrado" con el addon todavía en su sitio. Se usa la clave
    ``copy_failed`` porque su mensaje ("no se pudo escribir en la carpeta de
    add-ons de Blender") es justo lo que pasa, y es una de las que la interfaz
    ya sabe contar.
    """
    executable = getattr(entry, "executable", None)
    if executable:
        try:
            blender_runner.set_addons(executable, disable=[addon.module])
        except Exception:  # noqa: BLE001 - borrar no debe fallar por desactivar
            pass
    try:
        baddons.delete_path(addon.path)
    except OSError as error:
        raise AddonError("copy_failed", str(error)) from error


def _find_addon_root(folder: Path):
    """``(tipo, raíz)`` del addon dentro de ``folder``.

    Un zip puede traer la carpeta del addon directamente o los ficheros
    sueltos; se busca el manifiesto (extensión) o el ``__init__.py``/``.py``
    (legacy), también un nivel más abajo.
    """
    candidates = [folder] + [child for child in folder.iterdir()
                             if child.is_dir()]
    for candidate in candidates:
        if (candidate / bc.MANIFEST_NAME).is_file():
            return EXTENSION, candidate
    for candidate in candidates:
        if (candidate / "__init__.py").is_file() or any(candidate.glob("*.py")):
            return LEGACY, candidate
    raise AddonError("no_addon", str(folder))


def _install_copy(source: Path, destination: Path) -> None:
    """Copia un addon (carpeta o ``.py`` suelto) a su sitio, apartando lo que hubiera.

    La copia la hace ``blender_addons.replace_with_copy``: primero a un temporal
    y solo al final se aparta lo viejo, así que un fallo a mitad no deja el
    destino a medias. Los fallos del sistema (sin permiso, disco lleno, ruta
    imposible) se envuelven en ``AddonError``: la interfaz solo sabe contar
    esos, y una excepción cruda mataba su hilo sin decir nada y dejaba la vista
    bloqueada.
    """
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        baddons.replace_with_copy(source, destination)
    except OSError as error:
        raise AddonError("copy_failed", str(error)) from error
