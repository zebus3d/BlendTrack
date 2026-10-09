"""Lo que se deduce de un addon mirando sus ficheros, sin ejecutarlo.

``blender_addons.compat_report`` decide con lo que el addon **declara**
(versión mínima y máxima, plataformas, wheels). Eso no basta para tres casos
que salían "compatibles" y luego no cargaban:

- **Módulos compilados sueltos** (``.so``/``.pyd`` dentro de la carpeta del
  addon, no en ``wheels/``). Llevan el Python en el nombre
  (``mi_mod.cpython-311-x86_64-linux-gnu.so``, ``mi_mod.cp311-win_amd64.pyd``)
  y solo cargan en ese Python: de Blender 5.0 (3.11) a 5.1 (3.13) dejan de
  importarse.
- **Módulos de Blender que ya no existen.** ``bgl`` se quitó en Blender 5.0
  (comprobado arrancando 4.5.14, 5.0.0 y 5.2.2: ``import bgl`` funciona en la
  primera y da ``ModuleNotFoundError`` en las otras dos). Un addon que lo
  importa al cargar ni siquiera se activa.
- **Addons de antes de 2.80**, que fue la ruptura grande de la API de Python
  (``bl_info["blender"] < (2, 80)``): en una versión actual casi nunca
  funcionan.

Todo esto son **deducciones** (un ``import bgl`` puede estar protegido por un
``try``; un compilado puede ser opcional), así que la migración los convierte en
avisos para revisar, nunca en bloqueos. Es un módulo de ``services/``: no
importa Qt.
"""

import os
import re
from pathlib import Path

# Módulos de Python que Blender ha dejado de incluir y desde qué versión.
# Añadir uno es una línea; comprobarlo, arrancar la versión y hacer el import.
REMOVED_MODULES = {
    "bgl": (5, 0),
}

# Antes de 2.80 la API de Python era otra (capas, ``scene.objects.link``,
# ``bpy.types.INFO_MT_*``...). Un addon que declara una versión anterior no se
# actualizó desde entonces.
API_BREAK_280 = (2, 80)

# Carpetas que no son código del addon: ``wheels`` lo instala Blender (y lo
# vigila ``blender_addons.wheel_problem``), las demás son basura de herramientas.
_SKIP_DIRS = {"wheels", "__pycache__", ".git", ".svn", ".hg", ".venv", "venv"}

# Python de un módulo compilado, en el nombre: ``.cpython-311-...`` (Linux y
# macOS) o ``.cp311-...`` (Windows). ``.abi3.so`` es la ABI estable y vale para
# cualquier Python 3, así que no casa a propósito.
_NATIVE_TAG_RE = re.compile(r"\.(?:cpython-|cp)(\d)(\d+)[-.]", re.IGNORECASE)
_NATIVE_SUFFIXES = (".so", ".pyd")

# Tope de ficheros que se leen por addon: el plan se recalcula al cambiar de
# versión en los desplegables y un addon con miles de .py (librerías
# vendorizadas) no puede congelar la interfaz.
_MAX_PY_FILES = 2000


def _walk(path: Path):
    """Ficheros del addon (la propia ruta si es un addon de un solo ``.py``)."""
    path = Path(path)
    if path.is_file():
        yield path
        return
    if not path.is_dir():
        return
    for root, dirs, files in os.walk(path):
        dirs[:] = [name for name in dirs if name not in _SKIP_DIRS]
        for name in files:
            yield Path(root) / name


def _import_pattern(module: str) -> re.Pattern:
    """``import bgl``, ``import os, bgl``, ``from bgl import ...``, al principio
    de una línea (con sangría: dentro de una función también cuenta)."""
    name = re.escape(module)
    return re.compile(
        rf"^[ \t]*(?:from[ \t]+{name}\b|import[ \t]+(?:[\w.]+[ \t]*,[ \t]*)*{name}\b)",
        re.MULTILINE)


_IMPORT_PATTERNS = {module: _import_pattern(module) for module in REMOVED_MODULES}

# ``scan`` por carpeta: ``ruta -> (mtime_ns, resultado)``. El plan se recalcula
# cada vez que se cambia de versión en los desplegables, y leer los .py de
# addons grandes cuesta (medido: MatPlus trae 706 y casi 6000 ficheros). La
# fecha de la carpeta cambia cuando se instala, se borra o se reemplaza el
# addon, que es lo que mueve el resultado.
_CACHE = {}


def scan(path: Path) -> tuple:
    """Como ``_scan``, pero recordando el resultado mientras la carpeta no cambie."""
    path = Path(path)
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return (), ()
    cached = _CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    result = _scan(path)
    _CACHE[path] = (stamp, result)
    return result


def _scan(path: Path) -> tuple:
    """``(natives, removed_imports)`` de un addon.

    ``natives`` son los nombres de los módulos compilados **con** etiqueta de
    Python (los que no la llevan no dicen nada y se ignoran); ``removed_imports``,
    los módulos de ``REMOVED_MODULES`` que importa alguno de sus ``.py``. Solo
    lee ficheros; un error de lectura se salta.
    """
    natives = []
    found = set()
    py_files = 0
    for file in _walk(path):
        suffix = file.suffix.lower()
        if suffix in _NATIVE_SUFFIXES and _NATIVE_TAG_RE.search(file.name):
            natives.append(file.name)
        elif (suffix == ".py" and py_files < _MAX_PY_FILES
                and len(found) < len(REMOVED_MODULES)):
            py_files += 1
            try:
                data = file.read_bytes()
            except OSError:
                continue
            # Primero se busca el nombre en bruto: casi ningún fichero lo
            # contiene y así solo se decodifica y pasa la regex a esos pocos
            # (medido: de 230 a ~60 ms con los addons de un equipo real).
            for module, pattern in _IMPORT_PATTERNS.items():
                if (module not in found and module.encode() in data
                        and pattern.search(data.decode("utf-8", "replace"))):
                    found.add(module)
    return tuple(sorted(natives)), tuple(sorted(found))


def removed_module(imports, target: tuple) -> str:
    """Primer módulo importado que ya no existe en ``target``, o ""."""
    if not target:
        return ""
    for module in sorted(imports):
        since = REMOVED_MODULES.get(module)
        if since and target >= since:
            return module
    return ""


def removed_since(module: str) -> str:
    """Versión en la que Blender quitó ``module`` (``"5.0"``), o ""."""
    since = REMOVED_MODULES.get(module)
    return ".".join(str(part) for part in since) if since else ""


def predates_api_break(minimum: tuple, target: tuple) -> bool:
    """True si el addon declara un Blender anterior a 2.80 y el destino no lo es."""
    return bool(minimum and target and minimum < API_BREAK_280 <= target)


def _native_python(file_name: str) -> str:
    """``"313"`` para ``x.cpython-313-x86_64-linux-gnu.so``; "" si no lleva."""
    match = _NATIVE_TAG_RE.search(file_name)
    if not match:
        return ""
    return f"{match.group(1)}{int(match.group(2)):02d}"


def native_problem(natives, target_python: str) -> str:
    """Módulo compilado que no carga en ``target_python``, o "".

    Igual que con los wheels, se mira **módulo a módulo**: un addon puede traer
    ``core.cpython-311-…so`` y ``core.cpython-313-…so`` para servir a varias
    versiones de Blender, y Python carga el suyo. Solo hay problema si un
    módulo trae compilados y ninguno es del Python de destino. Devuelve el
    nombre del fichero, que es lo que el usuario puede buscar en la carpeta.
    """
    digits = re.sub(r"\D", "", target_python or "")
    if len(digits) < 2:
        return ""
    want = f"{digits[0]}{int(digits[1:]):02d}"
    by_module = {}
    for name in natives:
        tag = _native_python(name)
        if tag:
            by_module.setdefault(name.split(".")[0].lower(), []).append((tag, name))
    for module in sorted(by_module):
        variants = by_module[module]
        if all(tag != want for tag, _ in variants):
            return sorted(name for _, name in variants)[0]
    return ""
