"""Lanzamiento de Blender como proceso externo.

Blender se ejecuta en un proceso aparte y **desacoplado** del gestor:
en Linux/macOS se inicia en su propia sesión (``start_new_session``) y en
Windows en un grupo de procesos propio. Así, aunque cierres Blender Manager,
los Blender que hayas lanzado desde él siguen abiertos.

Guardamos las referencias a los procesos lanzados solo para poder descartar
los que ya han terminado y no acumularlas indefinidamente.
"""

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from services.opener import clean_env

# Nombre de variable de entorno válido: letras, dígitos y ``_``, sin empezar por
# dígito. Se valida porque el texto viene de un campo de Ajustes (o de un
# ``settings.json`` editado a mano) y no queremos colar un nombre imposible en
# el proceso de Blender.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

# Prefijo de shell que la gente copia de la terminal ("export FOO=bar",
# "env FOO=bar"). No es parte del nombre, así que se quita antes de mirarlo.
_SHELL_PREFIX = re.compile(r"^(?:export|env)\s+")


def parse_env(text: str) -> dict:
    """Convierte las líneas ``CLAVE=VALOR`` de Ajustes en un mapa de entorno.

    Una variable por línea; las líneas en blanco y las que empiezan por ``#``
    (comentarios) se ignoran, igual que las que no traen ``=`` o cuyo nombre no
    es un identificador válido. El valor sí puede llevar espacios: es todo lo
    que va tras el primer ``=`` (recortado de espacios en los extremos).

    Se admite un ``export `` o ``env `` delante porque es lo que sale al copiar
    el apaño de una terminal (``export XMODIFIERS=@im=none``): sin quitarlo, el
    nombre sería "export XMODIFIERS" y la variable se perdería en silencio.
    """
    result = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        line = _SHELL_PREFIX.sub("", line, count=1)
        name, _, value = line.partition("=")
        name = name.strip()
        if _ENV_NAME.match(name):
            result[name] = value.strip()
    return result


# Emuladores de terminal que sabemos abrir en Linux, por orden de preferencia.
# El segundo elemento es cómo se le pasa el comando: unos usan ``-e`` y otros
# ``--`` (gnome-terminal) o nada (kitty lo ejecuta tal cual).
_TERMINALS = (
    ("x-terminal-emulator", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("konsole", ["-e"]),
    ("xfce4-terminal", ["-e"]),
    ("mate-terminal", ["-e"]),
    ("alacritty", ["-e"]),
    ("kitty", []),
    ("foot", ["-e"]),
    ("xterm", ["-e"]),
)


def _applescript_string(text: str) -> str:
    """Texto entre comillas para AppleScript (escapa ``\\`` y ``"``)."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def console_command(command) -> list | None:
    """Comando que abre Blender con consola visible, o ``None`` si no se puede.

    Linux: busca un emulador de terminal en el ``PATH``. macOS: Terminal, vía
    ``osascript``. Windows no necesita comando aparte (se usa
    ``CREATE_NEW_CONSOLE`` al crear el proceso), así que devuelve ``None``.
    """
    if sys.platform.startswith("win"):
        return None
    if sys.platform == "darwin":
        if not shutil.which("osascript"):
            return None
        script = ('tell application "Terminal" to do script '
                  + _applescript_string(shlex.join(command)))
        return ["osascript", "-e", script]
    for name, prefix in _TERMINALS:
        path = shutil.which(name)
        if path:
            return [path, *prefix, *command]
    return None


def terminal_available() -> bool:
    """True si se puede lanzar con consola en este equipo."""
    if sys.platform.startswith("win"):
        return True   # CREATE_NEW_CONSOLE no necesita nada más
    return console_command(["true"]) is not None


class Launcher:
    """Lanza Blender como proceso aparte.

    Así las instancias que abre siguen vivas cuando se cierra
    el gestor.
    """
    def __init__(self):
        self._processes = []

    def launch(self, executable, args=None, cwd=None, console=False,
               env=None) -> subprocess.Popen:
        """Ejecuta esa versión instalada y devuelve el proceso lanzado.

        ``console=True`` abre Blender con su consola visible (salida de Python y
        errores de scripts). Si no hay terminal disponible, lanza normal: es un
        extra, no algo que deba impedir abrir Blender.

        ``env`` son variables de entorno extra para Blender (Ajustes > Launch,
        p. ej. ``XMODIFIERS=@im=none``). Se aplican **encima** del entorno limpio
        que devuelve ``clean_env``: así el apaño del usuario no reintroduce la
        contaminación del binario empaquetado.
        """
        executable = Path(executable)
        command = [str(executable)]
        command.extend(args or [])

        # Entorno limpio: sin el LD_LIBRARY_PATH que PyInstaller mete para el
        # AppImage, o Blender cargaría las librerías del gestor en vez de las
        # suyas (mismo fallo que al abrir el navegador).
        launch_env = clean_env()
        if env:
            launch_env.update(env)
        kwargs = {"cwd": str(cwd or executable.parent), "env": launch_env}

        if console and sys.platform.startswith("win"):
            # Consola nueva de Windows. NO se redirige la salida: si se manda a
            # DEVNULL, Blender escribiría en NUL y la consola saldría vacía.
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_CONSOLE
                | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        elif console:
            wrapped = console_command(command)
            if wrapped is not None:
                command = wrapped
            # El emulador de terminal se descarta igual: su ventana enseña la
            # salida del hijo, que corre en su propia sesión.
            kwargs.update(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)
        else:
            # Desacoplado del gestor y sin terminal asociado.
            kwargs.update(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                # Nueva sesión: no recibe el SIGHUP del terminal ni del gestor.
                kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **kwargs)
        self._processes.append(process)
        self._processes = [item for item in self._processes if item.poll() is None]
        return process
