"""Arranque automático de la aplicación al iniciar la sesión.

Cada sistema guarda esto a su manera y no hay una API común:

* **Linux**: el estándar freedesktop es un fichero ``.desktop`` en
  ``~/.config/autostart/``. Lo leen GNOME, KDE Plasma, XFCE, Cinnamon, MATE,
  LXQt… y systemd lo convierte en una unidad con
  ``systemd-xdg-autostart-generator``, así que es lo correcto en *todas* las
  distros de escritorio.
* **Windows**: un valor en ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
  (solo el usuario actual, sin administrador). La ruta **tiene que ir
  entrecomillada**: sin comillas, una carpeta con espacios permitiría a otro
  programa colarse en el arranque.
* **macOS**: un *LaunchAgent* en ``~/Library/LaunchAgents/`` y
  ``launchctl bootstrap``. Lo moderno sería ``SMAppService`` (macOS 13+), pero
  es una API de Objective-C/Swift (``ServiceManagement``): desde Python haría
  falta una dependencia más (``pyobjc``) solo para esto, y dejaría fuera los
  macOS anteriores al 13. El LaunchAgent es un ``plist`` que escribimos con la
  biblioteca estándar y apunta al ejecutable de dentro de nuestro ``.app``.

El fichero que deja el autoarranque **solo lanza la aplicación**; que arranque
oculta en la bandeja lo decide el ajuste ``start_minimized`` (así el mismo
comando sirve para el arranque manual y el automático). No importa Qt: es un
módulo de ``services/``.
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from services.downloader import log
from services.opener import clean_env

# Nombre del fichero/clave/etiqueta según el sistema.
DESKTOP_FILE = "blendtrack.desktop"
MACOS_LABEL = "com.blendtrack"
WINDOWS_VALUE = "BlendTrack"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APPROVED_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run")

# El autoarranque que dejó la versión antigua, cuando el proyecto se llamaba
# BlenderManager. Al renombrar (commit 51b88e2) el registro pasó a los nombres
# de arriba, pero el viejo **no se borró**. Quien lo tuviera activado además del
# nuevo arrancaba dos instancias con la sesión, y la segunda sacaba al frente la
# ventana que la primera acababa de esconder ("arrancar minimizado" no servía).
# ``remove_legacy`` limpia ese caso al arrancar; el fichero suelto de la versión
# antigua no se toca (ver su docstring).
LEGACY_DESKTOP_FILE = "blendermanager.desktop"
LEGACY_MACOS_LABEL = "com.blendermanager"
LEGACY_WINDOWS_VALUE = "BlenderManager"

NAME = "BlendTrack"
COMMENT = "Download and manage Blender builds"


def supported() -> bool:
    """True en los sistemas donde sabemos registrar el autoarranque."""
    return sys.platform.startswith("win") or sys.platform == "darwin" \
        or sys.platform.startswith("linux")


def _exec_command() -> list[str]:
    """Comando que hay que ejecutar al iniciar la sesión.

    Empaquetado: el propio binario. En Linux, si corremos dentro de una AppImage
    hay que usar ``$APPIMAGE`` (el ``sys.executable`` apunta a la ruta temporal
    donde está montada y desaparece al cerrarla), igual que hace el auto-update.
    En modo fuente: el intérprete de Python y ``main.py``.
    """
    if getattr(sys, "frozen", False):
        if sys.platform.startswith("linux"):
            appimage = os.environ.get("APPIMAGE")
            if appimage:
                return [appimage]
        return [sys.executable]
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return [sys.executable, str(main_py)]


def _quote_desktop(part: str) -> str:
    """Escribe una parte del ``Exec=`` según la Desktop Entry Specification.

    Una ruta con espacios sin comillas se parte en varios argumentos y el
    autoarranque falla (o, peor, ejecuta otra cosa). La especificación pide
    tres capas, y el orden importa porque quien lee las deshace al revés:

    1. **Códigos de campo**: ``%f``, ``%u``... los sustituye el escritorio, así
       que un ``%`` literal (una carpeta ``100%``) se escribe ``%%``.
    2. **Comillas**: si hay espacios o caracteres reservados, el argumento va
       entre comillas dobles y dentro se escapan ``"``, la comilla invertida,
       ``$`` y ``\\``.
    3. **Escape de cadena**: el valor de la clave es de tipo *string*, cuyo
       escape se aplica *antes* que el de las comillas al leer. Por eso cada
       ``\\`` del paso 2 se vuelve a doblar, y una barra literal acaba
       escrita como cuatro (es el ejemplo que da la propia especificación).
       Lo mismo con un salto de línea o un tabulador, que se escriben ``\\n``
       y ``\\t``.
    """
    part = part.replace("%", "%%")
    reserved = ' \t\n"\'\\><~|&;$*?#()`'
    if not part or any(char in part for char in reserved):
        escaped = (part.replace("\\", "\\\\").replace('"', '\\"')
                   .replace("`", "\\`").replace("$", "\\$"))
        part = f'"{escaped}"'
    return (part.replace("\\", "\\\\").replace("\n", "\\n")
            .replace("\t", "\\t").replace("\r", "\\r"))


def _desktop_entry(command: list[str]) -> str:
    """Contenido del ``.desktop`` de autoarranque (Linux)."""
    exec_line = " ".join(_quote_desktop(part) for part in command)
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        f"Comment={COMMENT}\n"
        f"Exec={exec_line}\n"
        "Icon=blendtrack\n"
        "Terminal=false\n"
        # Clave de GNOME; en KDE/XFCE/Cinnamon es inocua, pero ayuda a que
        # algunos ajustes la muestren como activada.
        "X-GNOME-Autostart-enabled=true\n"
    )


def _launch_agent_bytes(command: list[str]) -> bytes:
    """Contenido del LaunchAgent de macOS (plist generado con ``plistlib``)."""
    return plistlib.dumps({
        "Label": MACOS_LABEL,
        "ProgramArguments": list(command),
        "RunAtLoad": True,
    })


def _autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _desktop_path() -> Path:
    return _autostart_dir() / DESKTOP_FILE


def _legacy_desktop_path() -> Path:
    return _autostart_dir() / LEGACY_DESKTOP_FILE


def _launch_agent_path() -> Path:
    return _launch_agents_dir() / f"{MACOS_LABEL}.plist"


def _legacy_launch_agent_path() -> Path:
    return _launch_agents_dir() / f"{LEGACY_MACOS_LABEL}.plist"


def _run_launchctl(args: list[str]) -> bool:
    """Ejecuta ``launchctl`` con el entorno limpio (regla de ``services/opener``)."""
    try:
        result = subprocess.run(
            ["launchctl", *args], env=clean_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False)
        return result.returncode == 0
    except OSError as error:
        log(f"autostart: launchctl {args} falló: {error}")
        return False


# --------------------------------------------------------------- Linux
def _linux_enabled() -> bool:
    return _desktop_path().is_file()


def _linux_apply(enable: bool) -> bool:
    path = _desktop_path()
    try:
        if enable:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_desktop_entry(_exec_command()), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError as error:
        log(f"autostart: no se pudo {'activar' if enable else 'desactivar'} "
            f"({path}): {error}")
        return False
    # Al activar el nuestro, el de la versión antigua ya sobra: dos entradas
    # arrancarían dos instancias. Al desactivar **no se toca**, porque ese
    # fichero es de otra aplicación (BlenderManager) y desactivar la nuestra no
    # es motivo para cambiar la suya.
    if enable:
        _linux_remove_legacy()
    return True


def _linux_remove_legacy() -> None:
    legacy = _legacy_desktop_path()
    try:
        if legacy.exists():
            legacy.unlink()
    except OSError as error:
        log(f"autostart: no se pudo borrar el autoarranque antiguo "
            f"({legacy}): {error}")


# ------------------------------------------------------------- macOS
def _macos_enabled() -> bool:
    return _launch_agent_path().is_file()


def _macos_apply(enable: bool) -> bool:
    path = _launch_agent_path()
    domain = f"gui/{os.getuid()}"
    if enable:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_launch_agent_bytes(_exec_command()))
        except OSError as error:
            log(f"autostart: no se pudo escribir {path}: {error}")
            return False
        # Puede estar ya cargado de una sesión anterior: se descarga y se
        # vuelve a cargar para que coja el contenido nuevo.
        _run_launchctl(["bootout", domain, str(path)])
        if not _run_launchctl(["bootstrap", domain, str(path)]):
            # Si no se pudo cargar, no dejamos el plist: el estado que enseña la
            # interfaz (existencia del fichero) tiene que corresponderse con lo
            # que hará el sistema al iniciar sesión.
            log("autostart: launchctl bootstrap falló")
            try:
                path.unlink()
            except OSError:
                pass
            return False
        _macos_remove_legacy(domain)
        return True
    _run_launchctl(["bootout", domain, str(path)])
    try:
        if path.exists():
            path.unlink()
    except OSError as error:
        log(f"autostart: no se pudo borrar {path}: {error}")
        return False
    # El agente antiguo no se toca: es de otra aplicación y apagarla a ella no
    # nos autoriza a cambiar su autoarranque.
    return True


def _macos_remove_legacy(domain: str) -> None:
    legacy = _legacy_launch_agent_path()
    _run_launchctl(["bootout", domain, str(legacy)])
    try:
        if legacy.exists():
            legacy.unlink()
    except OSError as error:
        log(f"autostart: no se pudo borrar el autoarranque antiguo "
            f"({legacy}): {error}")


# ----------------------------------------------------------- Windows
def _windows_value_enabled(name: str) -> bool | None:
    """Estado de un valor de ``Run``: True/False, o None si no existe.

    ``False`` es que existe pero Windows lo tiene desactivado desde el
    Administrador de tareas (la entrada de ``StartupApproved``).
    """
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
    except OSError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            STARTUP_APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, name)
        if data and data[0] & 1:
            return False
    except OSError:
        pass
    return True


def _windows_enabled() -> bool:
    return _windows_value_enabled(WINDOWS_VALUE) is True


def _windows_remove_legacy() -> None:
    """Borra el valor ``Run`` antiguo y su marca de ``StartupApproved``.

    La marca es el *tombstone* que deja el Administrador de tareas al
    desactivar una entrada; sin borrarla queda basura en el registro.
    """
    import winreg

    for root, name in ((RUN_KEY, LEGACY_WINDOWS_VALUE),
                       (STARTUP_APPROVED_KEY, LEGACY_WINDOWS_VALUE)):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root, 0,
                                winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            # La clave o el valor no existen: no hay nada que borrar. Pasa,
            # por ejemplo, con ``StartupApproved`` en un perfil donde nadie ha
            # tocado el Administrador de tareas todavía.
            pass
        except OSError as error:
            log(f"autostart: no se pudo borrar el autoarranque antiguo "
                f"({root}\\{name}): {error}")


def _windows_apply(enable: bool) -> bool:
    import winreg

    try:
        if enable:
            # ``CreateKeyEx`` y no ``OpenKey``: en un perfil nuevo la clave
            # ``Run`` puede no existir todavía, y ``OpenKey`` fallaba (lo
            # destapó la prueba contra el registro de Wine).
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                    winreg.KEY_SET_VALUE) as key:
                # ``list2cmdline`` entrecomilla la ruta si lleva espacios y
                # respeta los argumentos (sin comillas, una ruta con espacios
                # dejaría que otro programa se colara en el arranque).
                winreg.SetValueEx(
                    key, WINDOWS_VALUE, 0, winreg.REG_SZ,
                    subprocess.list2cmdline(_exec_command()))
        else:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                try:
                    winreg.DeleteValue(key, WINDOWS_VALUE)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        # Sin clave ``Run`` al desactivar no hay nada que quitar: ya está.
        return not enable
    except OSError as error:
        log(f"autostart: no se pudo {'activar' if enable else 'desactivar'}: "
            f"{error}")
        return False
    # Al activar el nuestro, el valor antiguo ya sobra (dos entradas =
    # dos instancias). Al desactivar no se toca: es de otra aplicación.
    if enable:
        _windows_remove_legacy()
    return True


# ------------------------------------------------------------------ API
def is_enabled() -> bool:
    """True si la aplicación está registrada para arrancar con la sesión."""
    if not supported():
        return False
    try:
        if sys.platform.startswith("win"):
            return _windows_enabled()
        if sys.platform == "darwin":
            return _macos_enabled()
        return _linux_enabled()
    except Exception as error:  # registro ilegible, permisos...
        log(f"autostart: no se pudo consultar el estado: {error}")
        return False


def enable() -> bool:
    """Registra el autoarranque. Devuelve True si se pudo."""
    if not supported():
        return False
    if sys.platform.startswith("win"):
        return _windows_apply(True)
    if sys.platform == "darwin":
        return _macos_apply(True)
    return _linux_apply(True)


def disable() -> bool:
    """Quita el autoarranque. Devuelve True si se pudo."""
    if not supported():
        return False
    if sys.platform.startswith("win"):
        return _windows_apply(False)
    if sys.platform == "darwin":
        return _macos_apply(False)
    return _linux_apply(False)


def remove_legacy() -> None:
    """Borra el autoarranque de BlenderManager **solo si amenaza con duplicar**.

    Al renombrar el proyecto, quien tuviera activado el autoarranque en las dos
    versiones se quedaba con las dos entradas: la sesión arrancaba dos
    instancias y la segunda sacaba al frente la ventana que la primera acababa
    de esconder, así que "arrancar minimizado" no servía.

    Se limpia únicamente cuando el autoarranque de BlendTrack **está activo**,
    que es el caso que produce el doble arranque. Si solo existe el de la
    versión antigua, no se toca: es la configuración de *otra* aplicación y
    apagar la nuestra (o no haberla encendido) no es motivo para cambiarla.
    """
    if not supported():
        return
    if sys.platform.startswith("win"):
        if _windows_value_enabled(WINDOWS_VALUE) is True:
            _windows_remove_legacy()
        return
    if sys.platform == "darwin":
        if _launch_agent_path().is_file():
            _macos_remove_legacy(f"gui/{os.getuid()}")
        return
    if _desktop_path().is_file():
        _linux_remove_legacy()
