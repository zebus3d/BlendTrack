"""Preferencias de Blender por clave, para migrarlas de forma selectiva.

``userpref.blend`` es un ``.blend`` binario: no se interpreta a mano. Lo que
hace este módulo es **preguntarle al propio Blender** (en ``--background``) por
sus preferencias y traerlas como un diccionario plano ``{"view.ui_scale": 1.25}``.
Así:

* volcar las preferencias del usuario y las de fábrica (``--factory-startup``),
* **diferenciarlas** para quedarse solo con lo que el usuario cambió de verdad
  (y no con las 280 claves, la mayoría por defecto),
* y escribir solo las claves marcadas en la versión destino, avisando de las
  que ya no existen allí (Blender renombra/mueve cosas entre versiones).

Cada clave viene con su **nombre y descripción** tal y como los declara el RNA
de Blender (la etiqueta y el tooltip que se ven en Preferencias): así la
interfaz explica qué es cada ajuste sin mantener textos a mano.

La ruta de cada clave es una ruta RNA (``seccion.propiedad.subpropiedad``). No
es API estable entre versiones: esa es justo la razón de que escribir vaya
**clave a clave** y se reporten las que fallan, en vez de copiar de golpe.

Es un módulo de ``services/``: no importa Qt. La parte que habla con Blender
está en ``blender_runner``; aquí solo vive el guion, el filtrado y los tipos.
"""

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from services import blender_runner

# Valores que no son una preferencia "del usuario" sino del equipo: el diff
# contra fábrica los detectaría como cambio, pero migrarlos no tiene sentido
# (o es directamente peligroso: mover `gpu_backend` de un equipo a otro puede
# dejar a la otra build sin arrancar). Se ofrecen aparte y desmarcados.
ENVIRONMENT_KEYS = (
    "system.gpu_backend",
    "system.gpu_backend_os",
    "system.audio_device",
    "system.audio_channels",
    "system.audio_sample_rate",
    "system.memory_cache_limit",
    "filepaths.font_directory",
    "filepaths.texture_directory",
    "filepaths.render_output_directory",
    "filepaths.render_cache_directory",
    "filepaths.temp_directory",
    "filepaths.asset_libraries",
)

# Claves que ni siquiera se ofrecen: no son ajustes del usuario ni del equipo,
# son estado interno de Blender (la pestaña de preferencias que estaba abierta,
# si hay cambios sin guardar, la versión que escribió el fichero).
IGNORED_KEYS = (
    # Ojo con la ruta: ``use_preferences_save`` cuelga de ``preferences``, no
    # de ``preferences.system``, así que la clave NO lleva sección. Estuvo
    # escrita como ``system.use_preferences_save`` y por tanto no ignoraba
    # nada: comprobado contra Blender 5.2.2, donde la clave real del volcado
    # es ``use_preferences_save``.
    "use_preferences_save",
    "active_section",
    "is_dirty",
    "version",
)

# Secciones que se pueden ofrecer al usuario, en el orden de las pestañas de
# Preferencias de Blender. Temas, estilos de texto y atajos completos viven en
# colecciones que este volcado no recorre: van por presets (``blender_style``);
# de ``keymap`` solo llega cuál es el preset activo, y la etiqueta lo dice.
SECTIONS = (
    ("view", "Interface"),
    ("edit", "Editing"),
    ("inputs", "Input"),
    ("keymap", "Keymap (active preset)"),
    ("system", "System"),
    ("filepaths", "File Paths"),
    ("extensions", "Get Extensions"),
    ("experimental", "Experimental"),
    ("apps", "App templates"),
    # Preferencias de los addons activos (p. ej. el dispositivo de Cycles).
    ("addons", "Add-ons"),
)

# Error estable con el que el guion de escritura señala una preferencia de un
# addon que no está activado en el destino: la interfaz lo agrupa y explica.
ADDON_NOT_ENABLED = "add-on not enabled in the destination"

# Versión del formato del volcado. Un volcado sin ``format`` es del esquema
# viejo (solo el diccionario de valores) y se admite igual.
DUMP_FORMAT = 2

# Prefijo de la línea con el volcado en la salida de Blender (ver
# ``blender_runner.parse_marker``). Escribir usa el ``RESULT_MARKER`` común.
DUMP_MARKER = "BLENDTRACK_DUMP="

# --- Guion que corre dentro de Blender ------------------------------------
#
# Recorre el árbol de preferencias por las propiedades RNA y vuelca las hojas
# con su nombre y descripción. Se salta colecciones (addons, temas, asset
# libraries) porque no son escalares y se migran de otra forma (los addons
# tienen su propia vista), y las de solo lectura de hoja. Los POINTER se
# recorren aunque sean de solo lectura: por ahí se baja (``view``, ``edit``...),
# no se copian como valor.
#
# Cada propiedad va en su propio ``try``: una que falle se anota en ``skipped``
# y las demás siguen. Antes un solo POINTER a ``None`` tumbaba el volcado
# entero y la interfaz decía "no se pudieron leer los ajustes" sin motivo.
# Y el marcador se imprime SIEMPRE, aunque falle todo, con el error dentro.

_DUMP_SCRIPT = blender_runner.with_constants(r'''
import bpy
import json

VALUES = {}
META = {}
SKIPPED = []
ADDONS = []
SKIP = object()


def scalar(kind, value):
    """Valor JSON del tipo RNA ``kind``.

    Los float se redondean: Blender devuelve 0.10000000149 para un 0.1, y sin
    redondear dos lecturas de una preferencia sin tocar podían salir distintas.
    """
    if kind == "FLOAT":
        return round(float(value), 6)
    if kind == "INT":
        return int(value)
    if kind == "BOOLEAN":
        return bool(value)
    return str(value)


def is_array(prop):
    return bool(getattr(prop, "is_array", False))


def is_flag(prop):
    return prop.type == "ENUM" and bool(getattr(prop, "is_enum_flag", False))


def serialise(prop, value):
    """Valor JSON de esa propiedad, o SKIP si no es de un tipo que sepamos."""
    if is_flag(prop):
        # Un enum de varios valores llega como ``set``, y ``str()`` sobre un
        # set NO tiene orden estable entre procesos (Python aleatoriza el hash
        # de las cadenas): se ordena para que el diff no lo dé por cambiado.
        return sorted(str(item) for item in value)
    if prop.type == "ENUM":
        return str(value)
    if is_array(prop):
        # Colores, pares de floats...: antes se descartaban sin dejar rastro.
        return [scalar(prop.type, item) for item in value]
    if prop.type in ("BOOLEAN", "INT", "FLOAT", "STRING"):
        return scalar(prop.type, value)
    return SKIP


def default_of(prop):
    """Valor de fábrica de la propiedad, en la misma forma que ``serialise``."""
    if is_flag(prop):
        return sorted(str(item) for item in prop.default_flag)
    if prop.type == "ENUM":
        return str(prop.default)
    if is_array(prop):
        return [scalar(prop.type, item) for item in prop.default_array]
    return scalar(prop.type, prop.default)


def is_default(prop, value):
    """True si ``value`` (ya serializado) es el de fábrica de esa propiedad.

    Se usa para las preferencias de addons: como un addon que solo está
    activado en la config del usuario no sale en el volcado de fábrica, se
    compara contra el valor por defecto que declara el propio RNA.
    """
    try:
        return default_of(prop) == value
    except Exception:
        return False


def describe(prop):
    """Nombre, descripción y tipo: lo que la interfaz enseña por cada clave."""
    meta = {
        "name": prop.name or "",
        "description": prop.description or "",
        "type": prop.type,
        "subtype": getattr(prop, "subtype", "NONE") or "NONE",
    }
    if is_array(prop):
        meta["is_array"] = True
    if is_flag(prop):
        meta["enum_flag"] = True
    return meta


def walk(obj, prefix, depth=0, only_changed=False):
    if obj is None or depth > 5:
        return
    for prop in obj.bl_rna.properties:
        path = prefix + prop.identifier
        try:
            if prop.identifier in ("rna_type", "bl_idname") or prop.type == "COLLECTION":
                continue
            value = getattr(obj, prop.identifier)
            if prop.type == "POINTER":
                walk(value, path + ".", depth + 1, only_changed)
                continue
            if prop.is_readonly:
                continue
            out = serialise(prop, value)
            if out is SKIP:
                SKIPPED.append({"path": path, "reason": "unsupported type " + prop.type})
                continue
            if only_changed and is_default(prop, out):
                continue
            VALUES[path] = out
            META[path] = describe(prop)
        except Exception as exc:
            SKIPPED.append({"path": path, "reason": str(exc)})


payload = {"format": DUMP_FORMAT, "values": VALUES, "meta": META,
           "skipped": SKIPPED, "addons": ADDONS}
try:
    walk(bpy.context.preferences, "")
    # Preferencias de los addons ACTIVOS. No son atributos de ``preferences``
    # (viven en la colección ``addons``), así que el recorrido de arriba no
    # las ve: de ahí salían sin detectar el dispositivo de Cycles (OPTIX/CUDA/
    # HIP) o las opciones de glTF. Los módulos de extensión llevan puntos en
    # el nombre (``bl_ext.<repo>.<id>``); quien las escribe resuelve el módulo
    # por el prefijo más largo de ``addons``.
    for addon in bpy.context.preferences.addons:
        ADDONS.append(addon.module)
        try:
            addon_prefs = addon.preferences
        except Exception:
            continue
        # ``only_changed``: sin el volcado de fábrica con el que comparar, se
        # descartan las claves que ya valen lo que su propio RNA dice.
        walk(addon_prefs, "addons." + addon.module + ".", 0, True)
except Exception as exc:
    payload["error"] = str(exc)

print(DUMP_MARKER + json.dumps(payload))
''', DUMP_MARKER=DUMP_MARKER, DUMP_FORMAT=DUMP_FORMAT)

# --- Guion que escribe las claves elegidas ---------------------------------
#
# Recibe pares ``[ruta, valor]`` en un fichero JSON cuya ruta llega por
# variable de entorno. Escribe cada uno por su cuenta: si una clave ya no existe
# en esta versión (o cambió de tipo), se anota el error y se sigue con las
# demás. Solo guarda si algo se escribió.
#
# Los pares van en un fichero y no en la variable misma porque en Windows una
# variable de entorno no puede pasar de 32 767 caracteres: unos cientos de
# claves con sus descripciones de ruta (colores, listas) ya se acercan, y
# pasarse no da un error claro, el proceso simplemente no arranca.
#
# Las preferencias de un addon solo existen si el addon está activado. Con
# ``BLENDTRACK_ENABLE_ADDONS=1`` se intenta activar antes (en este mismo
# arranque, un solo ``save_userpref``); si no se puede, la clave falla con
# ``ADDON_NOT_ENABLED`` y la interfaz explica que hay que copiar el addon.

_APPLY_SCRIPT = blender_runner.with_constants(r'''
import bpy
import json
import os

pairs_file = os.environ.get("BLENDTRACK_PREFS_FILE", "")
pairs = []
if pairs_file:
    with open(pairs_file, encoding="utf-8") as handle:
        pairs = json.load(handle)
enable_addons = os.environ.get("BLENDTRACK_ENABLE_ADDONS") == "1"
applied = []
errors = []
addons_enabled = []
prefs = bpy.context.preferences

# Igual que al habilitar addons: `save_userpref` no crea la carpeta de config,
# y sin ella el guardado falla en silencio en `--background`.
try:
    config = bpy.utils.user_resource("CONFIG")
    if config:
        os.makedirs(config, exist_ok=True)
    prefs.use_preferences_save = True
except Exception:
    pass


def longest_prefix(rest, names):
    """El nombre de módulo más largo que sea prefijo de ``rest``."""
    matches = [name for name in names if rest.startswith(name + ".")]
    return max(matches, key=len) if matches else None


def ensure_addon(rest):
    """Módulo del addon de ``rest`` si está activado (o se acaba de activar)."""
    module = longest_prefix(rest, list(prefs.addons.keys()))
    if module is not None:
        return module
    if not enable_addons:
        return None
    import addon_utils
    module = longest_prefix(rest, [m.__name__ for m in addon_utils.modules()])
    if module is None:
        return None
    try:
        addon_utils.enable(module, default_set=True)
    except Exception as exc:
        addons_enabled.append({"module": module, "enabled": False,
                               "error": str(exc)})
        return None
    if module not in prefs.addons.keys():
        addons_enabled.append({"module": module, "enabled": False,
                               "error": "enable failed"})
        return None
    addons_enabled.append({"module": module, "enabled": True})
    return module


def resolve(path):
    """``(objeto RNA, error)`` de la ruta: el objeto que tiene la hoja.

    ``addons.<módulo>.<clave>`` no es una cadena de atributos (``addons`` es una
    colección), así que se entra por ella. El módulo puede llevar puntos
    (extensiones), y se elige el que sea prefijo más largo de la ruta.
    """
    if path.startswith("addons."):
        rest = path[len("addons."):]
        module = ensure_addon(rest)
        if module is None:
            return None, ADDON_NOT_ENABLED
        addon = prefs.addons.get(module)
        obj = addon.preferences if addon is not None else None
        if obj is None:
            return None, ADDON_NOT_ENABLED
        head = rest[len(module) + 1:].rpartition(".")[0]
    else:
        obj = prefs
        head = path.rpartition(".")[0]
    for part in head.split(".") if head else []:
        obj = getattr(obj, part)
    return obj, ""


for path, value in pairs:
    try:
        leaf = path.rpartition(".")[2]
        obj, error = resolve(path)
        if obj is None:
            errors.append({"path": path, "error": error or "unknown property"})
            continue
        prop = obj.bl_rna.properties.get(leaf)
        if prop is None:
            errors.append({"path": path, "error": "unknown property"})
            continue
        if prop.type == "ENUM":
            setattr(obj, leaf, set(value) if isinstance(value, list) else str(value))
        elif getattr(prop, "is_array", False):
            cast = {"BOOLEAN": bool, "INT": int, "FLOAT": float}.get(prop.type)
            setattr(obj, leaf, tuple(cast(item) if cast else item for item in value))
        elif prop.type == "BOOLEAN":
            setattr(obj, leaf, bool(value))
        elif prop.type in ("INT", "FLOAT"):
            setattr(obj, leaf, float(value) if prop.type == "FLOAT" else int(value))
        else:
            setattr(obj, leaf, value)
        applied.append(path)
    except Exception as exc:
        errors.append({"path": path, "error": str(exc)})

if applied:
    try:
        bpy.ops.wm.save_userpref()
    except Exception as exc:
        errors.append({"path": "", "error": "save_userpref: " + str(exc)})

print(RESULT_MARKER + json.dumps(
    {"applied": applied, "errors": errors, "addons_enabled": addons_enabled}))
''', RESULT_MARKER=blender_runner.RESULT_MARKER,
    ADDON_NOT_ENABLED=ADDON_NOT_ENABLED)


@dataclass
class Preference:
    """Una preferencia, ya filtrada (el usuario la cambió).

    ``name`` y ``description`` son los del RNA de Blender (la etiqueta y el
    tooltip de Preferencias); pueden faltar en volcados viejos, y entonces la
    interfaz enseña el identificador.
    """

    path: str
    value: object
    selected: bool = True
    name: str = ""
    description: str = ""
    type: str = ""
    subtype: str = ""

    @property
    def section(self) -> str:
        """Sección de la ruta RNA (``view.ui_scale`` -> ``view``)."""
        return (self.path or "").split(".", 1)[0]

    @property
    def identifier(self) -> str:
        """Último tramo de la ruta (``ui_scale``): el nombre en el RNA."""
        return self.path.rsplit(".", 1)[-1]

    @property
    def label(self) -> str:
        """Nombre legible: el de Blender si lo hay, si no el identificador."""
        return self.name or self.identifier

    @property
    def display_value(self) -> str:
        """El valor como texto corto para una fila (listas y floats legibles)."""
        return format_value(self.value)


@dataclass
class PreferenceDump:
    """Lo que devuelve un volcado: valores, sus metadatos y cómo fue la cosa.

    ``ok`` distingue "Blender contestó" de "no se pudo leer" (sin ejecutable,
    tiempo agotado, salida sin marcador): antes un volcado vacío y uno fallido
    eran el mismo ``{}`` y la interfaz no sabía qué decir.
    """

    values: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    skipped: list = field(default_factory=list)
    addons: list = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def format_value(value) -> str:
    """Texto corto de un valor: listas separadas por comas, floats sin ruido."""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, tuple)):
        return ", ".join(format_value(item) for item in value)
    return str(value)


def is_environment(key: str) -> bool:
    """True si la clave depende del equipo y no conviene migrarla."""
    return any(key == blocked or key.startswith(blocked + ".")
               for blocked in ENVIRONMENT_KEYS)


def is_ignored(key: str) -> bool:
    """True si la clave es estado interno de Blender y no se ofrece nunca."""
    return any(key == blocked or key.startswith(blocked + ".")
               for blocked in IGNORED_KEYS)


def is_serialisable(value) -> bool:
    """True si el valor se puede escribir de vuelta: escalar o lista de escalares."""
    if isinstance(value, (bool, int, float, str)):
        return True
    return (isinstance(value, list)
            and all(isinstance(item, (bool, int, float, str)) for item in value))


def changed(user: dict, factory: dict, meta=None) -> list:
    """Todas las claves que el usuario cambió respecto a fábrica.

    Es la base de ``diff`` y de ``environment_preferences``; aquí se devuelven
    **todas** (equipo incluido) porque para resumir "cuánto tiene este
    guardado" cuenta todo lo que el usuario tocó, esté donde esté.

    Una clave que falte en fábrica **normalmente** no es un cambio del usuario,
    es una propiedad que esa versión no tiene, y se descarta. La excepción son
    las de addons: el volcado de fábrica no trae las preferencias de un addon
    que solo está activado en la config del usuario (p. ej. una extensión
    instalada a mano), así que sus claves faltarían siempre. Ahí el addon
    activado es justamente lo que las hace suyas, y se ofrecen.

    ``meta`` (el del volcado del usuario) rellena nombre y descripción.
    """
    meta = meta or {}
    result = []
    for path, value in user.items():
        if not is_serialisable(value) or is_ignored(path):
            continue
        if path in factory:
            if factory[path] == value:
                continue
        elif not path.startswith("addons."):
            continue
        info = meta.get(path) or {}
        result.append(Preference(
            path=path, value=value,
            name=info.get("name", ""), description=info.get("description", ""),
            type=info.get("type", ""), subtype=info.get("subtype", "")))
    result.sort(key=lambda item: item.path)
    return result


def diff(user: dict, factory: dict, meta=None) -> list:
    """Claves que el usuario cambió y que son suyas (no del equipo).

    Es lo que se ofrece migrar de verdad: los cambios que dependen del hardware
    o de rutas van aparte, en ``environment_preferences``.
    """
    return [item for item in changed(user, factory, meta)
            if not is_environment(item.path)]


def environment_preferences(user: dict, factory: dict, meta=None) -> list:
    """Cambios que son del equipo (GPU, audio, rutas): se enseñan aparte.

    No se seleccionan por defecto, pero se listan para que el usuario sepa que
    existen y decida (sobre todo para migrar a la **misma** máquina).
    """
    items = [item for item in changed(user, factory, meta)
             if is_environment(item.path)]
    for item in items:
        item.selected = False
    return items


def group_by_section(preferences) -> list:
    """Agrupa las preferencias por sección, conservando el orden de ``SECTIONS``."""
    order = {key: index for index, (key, _) in enumerate(SECTIONS)}
    groups = {}
    for item in preferences:
        groups.setdefault(item.section, []).append(item)
    grouped = []
    for section in sorted(groups, key=lambda key: (order.get(key, 99), key)):
        grouped.append((section, groups[section]))
    return grouped


def section_label(section: str) -> str:
    """Etiqueta (clave i18n) de una sección; la propia clave si no la conocemos."""
    return dict(SECTIONS).get(section, section)


def _dump_from_payload(payload) -> PreferenceDump:
    """``PreferenceDump`` a partir del JSON del marcador (formato 2 o viejo)."""
    if not isinstance(payload, dict):
        return PreferenceDump(error="no dump in Blender's output")
    if "format" not in payload:
        # Esquema viejo: el diccionario entero eran los valores.
        return PreferenceDump(values=dict(payload))
    return PreferenceDump(
        values=dict(payload.get("values") or {}),
        meta=dict(payload.get("meta") or {}),
        skipped=list(payload.get("skipped") or []),
        addons=list(payload.get("addons") or []),
        error=str(payload.get("error") or ""),
    )


def read_preferences(executable, factory: bool = False, timeout: int = 180,
                     config_dir=None) -> PreferenceDump:
    """Pide a Blender su volcado de preferencias (de fábrica o del usuario).

    ``config_dir`` apunta a una carpeta de configuración concreta
    (``BLENDER_USER_CONFIG``): es lo que permite leer un guardado sin tocar la
    config viva. Nunca lanza: si no se pudo (sin ejecutable, timeout, salida
    rara), el motivo viaja en ``PreferenceDump.error`` y la interfaz lo enseña.
    """
    if not executable:
        return PreferenceDump(error="no Blender to run")
    args = ["--background"]
    if factory:
        args.append("--factory-startup")
    args += ["--python-expr", _DUMP_SCRIPT]
    extra_env = None
    if config_dir:
        extra_env = {"BLENDER_USER_CONFIG": str(config_dir)}
    code, out, err = blender_runner.run_blender(executable, args,
                                                extra_env=extra_env,
                                                timeout=timeout)
    if code is None:
        return PreferenceDump(error=err or "could not run Blender")
    payload = blender_runner.parse_marker(out, DUMP_MARKER)
    if payload is None:
        tail = (err or "").strip()[-400:]
        if code != 0:
            return PreferenceDump(error=f"Blender exited with {code}: {tail}")
        return PreferenceDump(error="no dump in Blender's output")
    return _dump_from_payload(payload)


def snapshot_preferences(executable, snapshot, timeout: int = 180) -> PreferenceDump:
    """Preferencias guardadas en una instantánea, sin tocarla.

    Se copia a un temporal y se arranca Blender con esa config: un guardado se
    abre **en una copia**, nunca en su sitio (Blender escribe cosas suyas al
    arrancar y no queremos que un guardado cambie por leerlo).
    """
    snapshot = Path(snapshot)
    if not executable:
        return PreferenceDump(error="no Blender to run")
    if not snapshot.is_dir():
        return PreferenceDump(error="snapshot folder not found")
    with tempfile.TemporaryDirectory(
            prefix="blendtrack-snapshot-") as tmp:
        config = Path(tmp) / "config"
        try:
            shutil.copytree(snapshot, config)
        except OSError as error:
            return PreferenceDump(error=str(error))
        return read_preferences(executable, config_dir=config, timeout=timeout)


def write_preferences(executable, preferences, timeout: int = 180,
                      enable_addons: bool = False) -> dict:
    """Escribe esas claves (``Preference`` o pares ``(ruta, valor)``) en Blender.

    **Escribe en la configuración REAL de la versión de ``executable``**, que
    es justo lo que se quiere al migrar (el destino es esa versión) y no tiene
    equivalente de ``config_dir``: Blender resuelve su propia carpeta. Para
    probar esto sin tocar la config del usuario hay que apuntar
    ``BLENDER_USER_CONFIG`` a un temporal **en el entorno del proceso**, que
    ``clean_env`` hereda. Aprendido a la mala: una prueba de ida y vuelta sin
    esa variable reescribió las preferencias reales de un Blender 5.2.

    Devuelve ``{"applied", "errors", "addons_enabled", "log"}``. Nunca lanza:
    si Blender no está o se cuelga, el motivo viaja en ``errors``. Con
    ``enable_addons`` intenta activar antes los addons cuyas preferencias se
    escriben (ver el guion).
    """
    pairs = []
    for item in preferences or []:
        if isinstance(item, Preference):
            pairs.append([item.path, item.value])
        else:
            pairs.append([item[0], item[1]])
    result = {"applied": [], "errors": [], "addons_enabled": [], "log": ""}
    if not pairs or not executable:
        result["errors"].append({"path": "", "error": "no Blender to run"})
        return result
    # Los pares van en un fichero temporal (ver el comentario del guion): el
    # ``with`` lo borra en cuanto Blender termina.
    with tempfile.TemporaryDirectory(prefix="blendtrack-prefs-") as tmp:
        pairs_file = Path(tmp) / "preferences.json"
        pairs_file.write_text(json.dumps(pairs), encoding="utf-8")
        code, out, err = blender_runner.run_blender(
            executable,
            ["--background", "--python-expr", _APPLY_SCRIPT],
            extra_env={"BLENDTRACK_PREFS_FILE": str(pairs_file),
                       "BLENDTRACK_ENABLE_ADDONS":
                           "1" if enable_addons else "0"},
            timeout=timeout,
        )
    result["log"] = (err or "")[-4000:]
    if code is None:
        result["errors"].append({"path": "", "error": err})
        return result
    payload = blender_runner.parse_marker(out, blender_runner.RESULT_MARKER)
    if payload is None:
        result["errors"].append(
            {"path": "", "error": f"no result from Blender (exit {code})"})
        return result
    result["applied"] = list(payload.get("applied") or [])
    result["errors"] = list(payload.get("errors") or [])
    result["addons_enabled"] = list(payload.get("addons_enabled") or [])
    return result
