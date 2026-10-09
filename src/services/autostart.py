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
DESKTOP_FILE = "blendermanager.desktop"
MACOS_LABEL = "com.blendermanager"
WINDOWS_VALUE = "BlenderManager"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APPROVED_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run")

NAME = "Blender Manager"
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
        "Icon=blendermanager\n"
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


def _launch_agent_path() -> Path:
    return _launch_agents_dir() / f"{MACOS_LABEL}.plist"


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
    return True


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
        if _run_launchctl(["bootstrap", domain, str(path)]):
            return True
        # Si no se pudo cargar, no dejamos el plist: el estado que enseña la
        # interfaz (existencia del fichero) tiene que corresponderse con lo que
        # hará el sistema al iniciar sesión.
        log("autostart: launchctl bootstrap falló")
        try:
            path.unlink()
        except OSError:
            pass
        return False
    _run_launchctl(["bootout", domain, str(path)])
    try:
        if path.exists():
            path.unlink()
    except OSError as error:
        log(f"autostart: no se pudo borrar {path}: {error}")
        return False
    return True


# ----------------------------------------------------------- Windows
def _windows_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, WINDOWS_VALUE)
    except OSError:
        return False
    # Windows puede haberla desactivado desde el Administrador de tareas sin
    # borrar el valor de Run: entonces hay una entrada en StartupApproved.
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            STARTUP_APPROVED_KEY) as key:
            data, _ = winreg.QueryValueEx(key, WINDOWS_VALUE)
        if data and data[0] & 1:
            return False
    except OSError:
        pass
    return True


def _windows_apply(enable: bool) -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enable:
                # ``list2cmdline`` entrecomilla la ruta si lleva espacios y
                # respeta los argumentos (sin comillas, una ruta con espacios
                # dejaría que otro programa se colara en el arranque).
                winreg.SetValueEx(
                    key, WINDOWS_VALUE, 0, winreg.REG_SZ,
                    subprocess.list2cmdline(_exec_command()))
            else:
                try:
                    winreg.DeleteValue(key, WINDOWS_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as error:
        log(f"autostart: no se pudo {'activar' if enable else 'desactivar'}: "
            f"{error}")
        return False
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
