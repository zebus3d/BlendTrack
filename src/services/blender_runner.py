"""Ejecuta un Blender instalado en segundo plano (modo ``--background``).

Hay cosas que solo entiende el propio Blender: el estado *habilitado* de un
addon vive dentro de ``userpref.blend`` (un ``.blend`` binario) y las
extensiones se registran con un espacio de nombres que solo Blender conoce
(``bl_ext.<repo>.<id>``). En vez de tocar esos ficheros a mano, se arranca el
Blender **destino** con un guion, se piden los cambios y se guardan.

Es un módulo de ``services/``: no importa Qt. El proceso se lanza siempre con
``clean_env()`` (regla del proyecto: el ``LD_LIBRARY_PATH`` que mete PyInstaller
rompe los programas externos) y con un tiempo máximo, porque un addon que se
cuelga al registrarse no puede colgar al gestor.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from services.opener import clean_env

# La salida del guion se busca por este prefijo: Blender escribe mucha cosa en
# la consola y no queremos confundir un mensaje suyo con el resultado.
RESULT_MARKER = "BLENDTRACK_RESULT="

# Tope por defecto para arrancar Blender. Habilitar unos cuantos addons tarda
# segundos; si pasa de aquí, algo va mal (addon colgado) y se mata.
DEFAULT_TIMEOUT = 180


def with_constants(script: str, **constants) -> str:
    """Antepone al guion una línea ``NOMBRE = valor`` por cada constante.

    Los guiones que corren dentro de Blender son texto: no pueden importar
    nada de la app. Antes repetían a mano los marcadores
    (``"BLENDTRACK_RESULT="``) y los mensajes de error estables, y bastaba
    con cambiar uno de los dos lados para que la app dejara de entender a
    Blender sin que fallase nada visible. Así cada constante vive en un solo
    sitio (aquí, en Python) y el guion la recibe ya definida. ``repr`` da un
    literal de Python válido para cadenas y números.
    """
    header = "".join(f"{name} = {value!r}\n" for name, value in constants.items())
    return header + script

# Guion que activa/desactiva los módulos que llegan por entorno y guarda las
# preferencias. Los módulos van por variable de entorno (no interpolados en el
# código) para no pelearse con comillas ni con el límite de longitud de los
# argumentos. ``addon_utils`` ya rechaza las extensiones incompatibles (Blender
# las marca en su propio chequeo), y ese error se recoge por addon.
_ADDON_SCRIPT = with_constants(r'''
import addon_utils
import bpy
import json
import os

payload = json.loads(os.environ.get("BLENDTRACK_ADDONS", "{}"))
enable = set(payload.get("enable") or [])
disable = set(payload.get("disable") or [])
enabled = []
disabled = []
errors = []
known = {mod.__name__: mod for mod in addon_utils.modules()}

for name in sorted(enable):
    if name not in known:
        errors.append({"module": name, "error": "unknown add-on"})
        continue
    try:
        addon_utils.enable(name, default_set=True)
        enabled.append(name)
    except Exception as exc:
        errors.append({"module": name, "error": str(exc)})

for name in sorted(disable):
    if name not in known:
        errors.append({"module": name, "error": "unknown add-on"})
        continue
    try:
        addon_utils.disable(name, default_set=True)
        disabled.append(name)
    except Exception as exc:
        errors.append({"module": name, "error": str(exc)})

# `save_userpref` NO crea la carpeta de configuración: en segundo plano hay que
# asegurarla o el guardado falla en silencio y los addons no quedan activados
# (comprobado arrancando Blender 5.2 con BLENDER_USER_CONFIG a una ruta nueva).
try:
    config = bpy.utils.user_resource("CONFIG")
    if config:
        os.makedirs(config, exist_ok=True)
    bpy.context.preferences.use_preferences_save = True
    bpy.ops.wm.save_userpref()
except Exception as exc:
    errors.append({"module": "", "error": "save_userpref: " + str(exc)})

print(RESULT_MARKER + json.dumps(
    {"enabled": enabled, "disabled": disabled, "errors": errors}))
''', RESULT_MARKER=RESULT_MARKER)

def run_blender(executable, args, extra_env=None, timeout=DEFAULT_TIMEOUT):
    """Arranca Blender y devuelve ``(returncode, stdout, stderr)``.

    Es la única puerta para lanzar un Blender en segundo plano: la usan también
    ``blender_prefs`` y ``blender_style``, y así todos heredan lo mismo
    (entorno limpio, entrada cerrada, tiempo máximo, carpeta de config
    asegurada). ``returncode`` es ``None`` si no se pudo lanzar o si agotó el
    tiempo (en ese caso se mata el proceso); el motivo viaja en ``stderr``.
    """
    environment = clean_env()
    if extra_env:
        environment.update(extra_env)
    # ``save_userpref`` no crea la carpeta de config (comprobado en 5.2: en
    # ``--background`` falla en silencio). Tras un reset a fábrica esa carpeta
    # puede no existir, así que la aseguramos antes de arrancar: da igual que
    # Blender la use después, solo tiene que estar.
    _ensure_config_dir(environment)
    command = [str(executable)] + list(args)
    try:
        # ``stdin=DEVNULL``: sin él, Blender hereda la entrada de la app y un
        # addon que llame a ``input()`` al registrarse se queda esperando una
        # tecla que nadie va a pulsar, hasta agotar el tiempo. Con la entrada
        # cerrada, ``input()`` falla al momento con ``EOFError`` y ese addon
        # se anota como error, como cualquier otro fallo al registrarse.
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=environment,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        return None, "", f"not found: {error}"
    except PermissionError as error:
        return None, "", f"not executable: {error}"
    except subprocess.TimeoutExpired:
        return None, "", f"timeout after {timeout}s"
    except OSError as error:
        return None, "", str(error)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _ensure_config_dir(environment: dict) -> None:
    """Crea la carpeta de configuración del Blender que se va a lanzar.

    Busca en el entorno la ubicación que Blender va a usar (``BLENDER_USER_CONFIG``)
    y, si no está definida, no hace nada: el caso normal (config del usuario) ya
    existe. Solo importa para el flujo de reset a fábrica, donde puede faltar.
    """
    path = (environment.get("BLENDER_USER_CONFIG") or "").strip()
    if not path:
        return
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass


def parse_marker(output: str, marker: str):
    """Última línea JSON con ``marker`` del volcado de Blender, o None.

    Blender escribe mucho en la consola (avisos de addons, de la GPU...); el
    guion imprime su resultado en una sola línea que empieza por ``marker`` y
    aquí se busca esa. Se queda con la **última** por si un addon imprimiera
    algo parecido antes.
    """
    payload = None
    for line in (output or "").splitlines():
        index = line.find(marker)
        if index == -1:
            continue
        try:
            payload = json.loads(line[index + len(marker):])
        except json.JSONDecodeError:
            continue
    return payload


def set_addons(executable, enable=None, disable=None,
               timeout=DEFAULT_TIMEOUT) -> dict:
    """Activa y/o desactiva esos módulos en el Blender destino y guarda.

    Devuelve ``{"ok", "enabled", "disabled", "errors", "log"}``. Nunca lanza:
    si Blender no está o se cuelga, el motivo viaja en ``errors`` para que la
    interfaz lo cuente. Los addons ya están copiados/instalados, así que un
    fallo aquí solo significa "aparecen en la lista, pero no como se pidió".
    """
    wanted = {
        "enable": [name for name in (enable or []) if name],
        "disable": [name for name in (disable or []) if name],
    }
    result = {"ok": False, "enabled": [], "disabled": [], "errors": [],
              "log": ""}
    if not executable or not (wanted["enable"] or wanted["disable"]):
        result["errors"].append({"module": "", "error": "no Blender to run"})
        return result
    code, out, err = run_blender(
        executable,
        ["--background", "--python-expr", _ADDON_SCRIPT],
        extra_env={"BLENDTRACK_ADDONS": json.dumps(wanted)},
        timeout=timeout,
    )
    result["log"] = (err or "")[-4000:]
    if code is None:
        result["errors"].append({"module": "", "error": err})
        return result
    payload = parse_marker(out, RESULT_MARKER)
    if payload is None:
        result["errors"].append(
            {"module": "", "error": "no result from Blender (exit "
                                    f"{code})"})
        return result
    result["enabled"] = list(payload.get("enabled") or [])
    result["disabled"] = list(payload.get("disabled") or [])
    result["errors"] = list(payload.get("errors") or [])
    result["ok"] = code == 0
    return result


def enable_addons(executable, modules, timeout=DEFAULT_TIMEOUT) -> dict:
    """Habilita esos módulos (envoltorio de ``set_addons`` para Migración)."""
    return set_addons(executable, enable=modules, timeout=timeout)


# Devuelve los módulos de addon **habilitados** en esa build. Se pregunta a
# Blender en vez de leer ``userpref.blend``: el estado "enabled" es un dato suyo
# y el fichero es binario. Se usa para copiar los addons con el mismo estado que
# tenían en origen (activado/desactivado), que es lo que espera el usuario.
_ENABLED_SCRIPT = with_constants(r'''
import addon_utils
import json

names = []
for mod in addon_utils.modules():
    try:
        if addon_utils.check(mod.__name__)[1]:
            names.append(mod.__name__)
    except Exception:
        continue

print(RESULT_MARKER + json.dumps({"enabled": names}))
''', RESULT_MARKER=RESULT_MARKER)


def enabled_addons(executable, timeout=DEFAULT_TIMEOUT) -> list:
    """Módulos de addon habilitados en esa build (o ``[]`` si falló).

    Incluye los legacy (``mi_addon``) y las extensiones con su namespace real
    (``bl_ext.<repo>.<id>``), que es como los conoce Blender. Nunca lanza: si no
    se puede preguntar, se devuelve una lista vacía y quien copie decide.
    """
    if not executable:
        return []
    code, out, _ = run_blender(executable, ["--background", "--python-expr",
                                     _ENABLED_SCRIPT], timeout=timeout)
    if code is None:
        return []
    payload = parse_marker(out, RESULT_MARKER)
    if not isinstance(payload, dict):
        return []
    return [str(name) for name in (payload.get("enabled") or [])]


# Nombres del ejecutable de cualquier Blender que gestionamos. UPBGE se llama
# igual que Blender; Bforartists tiene el suyo. En macOS el binario del bundle
# va con mayúscula (``Blender.app/Contents/MacOS/Blender``), por eso se compara
# en minúsculas.
EXECUTABLE_NAMES = ("blender", "blender.exe", "bforartists", "bforartists.exe")


def _looks_like_blender(executable: Path, target: Path | None) -> bool:
    """True si esa ruta es ese Blender (o cualquiera, si ``target`` es None)."""
    if target is not None:
        try:
            return executable.resolve() == target
        except OSError:
            return False
    return executable.name.lower() in EXECUTABLE_NAMES


def _matching_pids(processes, target: Path | None) -> list:
    """PIDs de ``processes`` (pares ``(pid, ruta del ejecutable)``) que encajan.

    Es la parte pura de ``running_blenders``: cada sistema saca la lista de
    procesos a su manera y aquí se decide cuáles son el Blender buscado.
    """
    return [pid for pid, path in processes
            if _looks_like_blender(Path(path), target)]


def _linux_processes():
    """``(pid, ejecutable)`` de cada proceso, leyendo ``/proc/<pid>/exe``.

    El enlace apunta al binario de verdad, así que acierta aunque el proceso
    se haya lanzado con otro nombre o a través de un enlace.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            yield int(entry.name), os.readlink(entry / "exe")
        except OSError:
            continue


def _macos_processes():
    """``(pid, ejecutable)`` de cada proceso, con ``ps``.

    En macOS, la columna ``comm`` de ``ps`` trae la **ruta completa** del
    binario (``/Applications/Blender.app/Contents/MacOS/Blender``). Antes se
    usaba ``pgrep -x Blender``, que solo veía el nombre: no reconocía
    Bforartists y, si se preguntaba por una versión concreta, contestaba por
    cualquier Blender abierto.
    """
    try:
        completed = subprocess.run(["ps", "-axo", "pid=,comm="],
                                   capture_output=True, text=True, timeout=5,
                                   stdin=subprocess.DEVNULL, env=clean_env())
    except (OSError, subprocess.SubprocessError):
        return []
    processes = []
    for line in completed.stdout.splitlines():
        pid, _, path = line.strip().partition(" ")
        if pid.isdigit() and path.strip():
            processes.append((int(pid), path.strip()))
    return processes


def running_blenders(executable=None) -> list:
    """PIDs de los Blender abiertos, si se pueden averiguar.

    * **Linux**: se mira a qué apunta ``/proc/<pid>/exe`` (robusto aunque el
      binario se llame distinto).
    * **macOS**: la ruta del binario que da ``ps``.
    * **Windows**: no se intenta (``tasklist`` no da la ruta con fiabilidad);
      se devuelve ``[]`` y quien escribe preferencias avisa igual.

    Sin ``executable`` vale cualquier Blender, Bforartists o UPBGE (ver
    ``EXECUTABLE_NAMES``); con él, solo ese binario.

    Escribir ``userpref.blend`` con Blender abierto es perder el cambio al
    cerrarlo, así que conviene comprobarlo antes de tocar nada.
    """
    target = None
    if executable:
        try:
            target = Path(executable).resolve()
        except OSError:
            target = None
    if sys.platform.startswith("linux"):
        return _matching_pids(_linux_processes(), target)
    if sys.platform == "darwin":
        return _matching_pids(_macos_processes(), target)
    return []


def is_running(executable=None) -> bool:
    """True si hay un Blender abierto (de esa build, o cualquiera)."""
    return bool(running_blenders(executable))
