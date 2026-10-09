"""Detección del sistema operativo, la arquitectura y la sesión gráfica.

El resultado usa los mismos identificadores que la API de Blender:
plataforma 'linux' | 'windows' | 'darwin' y arquitectura 'x86_64' | 'amd64' | 'arm64'.
"""

import os
import platform
from typing import NamedTuple
from collections.abc import Mapping

OS_IDS = {"Linux": "linux", "Windows": "windows", "Darwin": "darwin"}
OS_LABELS = {"linux": "GNU/Linux", "windows": "Windows", "darwin": "macOS"}


class SystemInfo(NamedTuple):
    """Sistema y arquitectura detectados, con los identificadores
    de Blender.
    """
    os_name: str
    arch: str
    label: str


def _arch(os_name: str) -> str:
    """Normaliza la arquitectura de la máquina al nombre que usa Blender."""
    machine = (platform.machine() or "").lower()
    if machine in ("x86_64", "amd64", "x64"):
        # Windows llama 'amd64' a la arquitectura de 64 bits.
        return "amd64" if os_name == "windows" else "x86_64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("i386", "i686", "x86", "i86pc"):
        return "x86"
    return machine


def detect() -> SystemInfo:
    """Detecta el sistema operativo y la arquitectura de este equipo."""
    system_name = platform.system()
    os_name = OS_IDS.get(system_name)
    if os_name is None:
        return SystemInfo("", "", system_name or "unknown")
    return SystemInfo(os_name, _arch(os_name), OS_LABELS[os_name])


def session_is_wayland(env: Mapping[str, str] | None = None) -> bool:
    """True si la sesión gráfica es Wayland (mira el entorno, no la UI)."""
    env = os.environ if env is None else env
    if "wayland" in (env.get("XDG_SESSION_TYPE") or "").lower():
        return True
    return bool(env.get("WAYLAND_DISPLAY"))


def minimize_to_tray_supported(env: Mapping[str, str] | None = None) -> bool:
    """Si se puede detectar el minimizado del sistema en esta sesión.

    En Wayland el compositor **no** comunica al cliente que la ventana está
    minimizada: ese estado no existe en ``xdg-shell``, así que si el botón lo
    dibuja el compositor (KWin, decoraciones del servidor) la app no se entera
    (lo documentan SDL, Electron y KDE). Forzar XWayland para interceptarlo
    obligaba a correr toda la app en X11, que es justo lo que choca con los
    atajos globales del compositor; por eso en Wayland la opción no se ofrece y
    el resto de plataformas sí la soportan.
    """
    return not session_is_wayland(env)
