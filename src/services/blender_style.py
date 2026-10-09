"""Tema y mapa de teclas como presets con nombre, sin copiar el ``userpref``.

Migrar el ``userpref.blend`` entero pisa toda la configuración del destino. El
tema y el mapa de teclas se pueden llevar **aparte**, como presets con nombre,
usando los propios exportadores de Blender:

* **Tema**: en origen ``wm.interface_theme_preset_add`` escribe un ``.xml`` en
  ``scripts/presets/interface_theme/``; en destino
  ``preferences.theme_install`` lo copia a esa carpeta y lo aplica.
* **Mapa de teclas**: ``preferences.keyconfig_export`` /
  ``keyconfig_import`` (que copia el ``.py`` y lo activa).

Así el destino se queda con un preset ``BlenderManager <serie>`` que puede
volver a elegir, en vez de perder lo suyo. Es el patrón de Blender Launcher V2
y Blenderbase.

Es un módulo de ``services/``: **no importa Qt**. Lo que arranca Blender vive en
``blender_runner``.
"""

import tempfile
from pathlib import Path

from services import blender_runner

RESULT_MARKER = "BLENDERMANAGER_RESULT="

# Prefijo de los presets que dejamos en el destino ("BlenderManager 5.2").
STYLE_PREFIX = "BlenderManager"

# Nombre del preset temporal que se crea en el origen para copiar el tema. Se
# quita en cuanto se copia, para no dejar rastro en la versión de origen.
_TMP_THEME = "BlenderManager_export_tmp"

# --- Guion que exporta tema y keymap (corre en el Blender ORIGEN) ----------

_EXPORT_SCRIPT = r'''
import bpy
import json
import os
import shutil

out_dir = os.environ["BLENDERMANAGER_STYLE_DIR"]
tmp_name = os.environ["BLENDERMANAGER_STYLE_TMP"]
theme_path = ""
keymap_path = ""
errors = []

# Tema: se añade el tema actual como preset (escribe un .xml en la carpeta de
# presets del usuario), se copia fuera y se quita el temporal. El ``finally``
# es lo que garantiza que no quede rastro en la versión de origen: si la copia
# fallaba (disco lleno, carpeta sin permiso), el preset temporal se quedaba
# para siempre en la lista de temas del usuario.
try:
    bpy.ops.wm.interface_theme_preset_add(name=tmp_name)
    try:
        root = bpy.utils.user_resource("SCRIPTS")
        for base, _dirs, files in os.walk(root):
            for name in files:
                if name == tmp_name + ".xml":
                    # La ruta solo se anuncia cuando la copia ha terminado: una
                    # copia cortada no puede pasar por un tema exportado.
                    copied = os.path.join(out_dir, "theme.xml")
                    shutil.copyfile(os.path.join(base, name), copied)
                    theme_path = copied
    finally:
        bpy.ops.wm.interface_theme_preset_remove(name=tmp_name)
except Exception as exc:
    errors.append("theme: " + str(exc))

# Mapa de teclas: el exportador escribe un .py con los cambios del usuario.
try:
    keymap_path = os.path.join(out_dir, "keymap.py")
    bpy.ops.preferences.keyconfig_export(filepath=keymap_path)
except Exception as exc:
    errors.append("keymap: " + str(exc))

print("BLENDERMANAGER_RESULT=" + json.dumps(
    {"theme": theme_path, "keymap": keymap_path, "errors": errors}))
'''

# --- Guion que instala los presets (corre en el Blender DESTINO) -----------

_IMPORT_SCRIPT = r'''
import bpy
import json
import os
import shutil

name = os.environ["BLENDERMANAGER_STYLE_NAME"]
theme = os.environ.get("BLENDERMANAGER_STYLE_THEME", "")
keymap = os.environ.get("BLENDERMANAGER_STYLE_KEYMAP", "")
applied = []
errors = []


def install(source, extension):
    """Copia el preset con el nombre final y devuelve la ruta."""
    target = os.path.join(os.path.dirname(source), name + extension)
    if os.path.abspath(target) != os.path.abspath(source):
        shutil.copyfile(source, target)
    return target


if theme and os.path.isfile(theme):
    try:
        bpy.ops.preferences.theme_install(filepath=install(theme, ".xml"))
        applied.append("theme")
    except Exception as exc:
        errors.append("theme: " + str(exc))

if keymap and os.path.isfile(keymap):
    try:
        bpy.ops.preferences.keyconfig_import(filepath=install(keymap, ".py"))
        applied.append("keymap")
    except Exception as exc:
        errors.append("keymap: " + str(exc))

try:
    bpy.ops.wm.save_userpref()
except Exception as exc:
    errors.append("save: " + str(exc))

print("BLENDERMANAGER_RESULT=" + json.dumps({"applied": applied, "errors": errors}))
'''


def export_style(executable, dest_dir, timeout: int = 180) -> dict:
    """Exporta el tema y el mapa de teclas activos a ``dest_dir``.

    Deja ``theme.xml`` y ``keymap.py`` en esa carpeta (lo que se pueda). Nunca
    lanza: si algo falla, el motivo viaja en ``errors`` y la ruta queda vacía.
    """
    result = {"theme": "", "keymap": "", "errors": []}
    if not executable:
        result["errors"].append("no Blender to run")
        return result
    code, out, _ = blender_runner._run(
        executable,
        ["--background", "--python-expr", _EXPORT_SCRIPT],
        extra_env={
            "BLENDERMANAGER_STYLE_DIR": str(dest_dir),
            "BLENDERMANAGER_STYLE_TMP": _TMP_THEME,
        },
        timeout=timeout,
    )
    if code is None:
        result["errors"].append("Blender did not run")
        return result
    payload = blender_runner._parse_marker(out, RESULT_MARKER)
    if not isinstance(payload, dict):
        result["errors"].append(f"no result from Blender (exit {code})")
        return result
    result["theme"] = str(payload.get("theme") or "")
    result["keymap"] = str(payload.get("keymap") or "")
    result["errors"] = list(payload.get("errors") or [])
    return result


def import_style(executable, name: str, theme_path: str = "",
                 keymap_path: str = "", timeout: int = 180) -> dict:
    """Instala y aplica esos presets en el Blender destino.

    Los renombra a ``name`` (``<name>.xml`` / ``<name>.py``): ese nombre es el
    que aparece en las preferencias de Blender. Nunca lanza.
    """
    result = {"applied": [], "errors": []}
    if not executable:
        result["errors"].append("no Blender to run")
        return result
    code, out, _ = blender_runner._run(
        executable,
        ["--background", "--python-expr", _IMPORT_SCRIPT],
        extra_env={
            "BLENDERMANAGER_STYLE_NAME": name,
            "BLENDERMANAGER_STYLE_THEME": theme_path or "",
            "BLENDERMANAGER_STYLE_KEYMAP": keymap_path or "",
        },
        timeout=timeout,
    )
    if code is None:
        result["errors"].append("Blender did not run")
        return result
    payload = blender_runner._parse_marker(out, RESULT_MARKER)
    if not isinstance(payload, dict):
        result["errors"].append(f"no result from Blender (exit {code})")
        return result
    result["applied"] = list(payload.get("applied") or [])
    result["errors"] = list(payload.get("errors") or [])
    return result


def copy_style(source_executable, target_executable, name: str,
               theme: bool = True, keymap: bool = True,
               timeout: int = 180) -> dict:
    """Lleva tema y/o mapa de teclas del origen al destino.

    Exporta en un temporal y lo instala en el destino. Devuelve
    ``{"applied", "errors"}``: ``applied`` trae "theme" y/o "keymap" según lo
    que se pudo poner.
    """
    result = {"applied": [], "errors": []}
    with tempfile.TemporaryDirectory(prefix="blendermanager-style-") as tmp:
        exported = export_style(source_executable, tmp, timeout=timeout)
        for error in exported.get("errors") or []:
            result["errors"].append(error)
        installed = import_style(
            target_executable, name,
            theme_path=exported.get("theme") if theme else "",
            keymap_path=exported.get("keymap") if keymap else "",
            timeout=timeout)
        result["applied"] = list(installed.get("applied") or [])
        result["errors"] += list(installed.get("errors") or [])
    return result

