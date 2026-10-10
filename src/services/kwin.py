"""Regla de KWin para que recuerde dónde se deja la ventana (solo KDE Wayland).

En **Wayland** el compositor decide dónde va una ventana y una aplicación no
puede leer ni fijar su posición (``QWidget.move`` no tiene efecto, y ``geometry``
devuelve ``(0, 0)``). Por eso el ``window_x/y`` de los ajustes no sirve ahí: la
posición "recordada" es cosa del compositor, y en KDE se expresa con una **regla
de ventana** en ``~/.config/kwinrulesrc``.

La app crea esa regla ella misma, **una sola vez** y solo cuando de verdad
aplica (KDE + Wayland y con las herramientas de KDE presentes). En cualquier
otro escritorio o en X11 no hace nada. Es idempotente: si la regla ya está, no
la toca.

No importa Qt: habla con KDE por sus herramientas de línea de órdenes
(``kreadconfig6``, ``kwriteconfig6`` y ``qdbus6``), lanzadas con el entorno
limpio (``opener.clean_env``), porque el ``LD_LIBRARY_PATH`` del AppImage
rompería esos binarios.
"""

import subprocess
import shutil

from services import detector, opener
from services.downloader import log

# Nombre de clase con el que KWin ve la ventana: es el ``desktopFileName`` que
# fija ``main.py`` (Qt lo publica como *app id* en Wayland).
WINDOW_CLASS = "blendermanager"
RULE_DESCRIPTION = "BlenderManager: remember window position"
# Valores del enum de KWin (``rules.h``): la posición se **recuerda** al cerrar
# (``Remember`` = 4) y la clase se empareja por **subcadena** (``Substring`` = 2),
# que perdona mayúsculas o sufijos del app id.
POSITION_RULE_REMEMBER = "4"
CLASS_MATCH_SUBSTRING = "2"
TIMEOUT = 5
TOOLS = ("kreadconfig6", "kwriteconfig6", "qdbus6")


def available() -> bool:
    """True si toca crear la regla: KDE + Wayland y con las herramientas a mano."""
    if not detector.session_is_wayland():
        return False
    env = opener.clean_env()
    desktop = (env.get("XDG_CURRENT_DESKTOP", "")
               + env.get("KDE_FULL_SESSION", "")).upper()
    if "KDE" not in desktop and "PLASMA" not in desktop:
        return False
    return all(shutil.which(tool) for tool in TOOLS)


def _run(args) -> str:
    result = subprocess.run(
        args, env=opener.clean_env(), timeout=TIMEOUT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return result.stdout.strip()


def _read(key: str, group: str) -> str:
    return _run(["kreadconfig6", "--file", "kwinrulesrc",
                 "--group", group, "--key", key])


def _write(key: str, value: str, group: str) -> None:
    _run(["kwriteconfig6", "--file", "kwinrulesrc",
          "--group", group, "--key", key, value])


def _rule_ids() -> list:
    raw = _read("rules", "General")
    return [int(part) for part in raw.replace(" ", "").split(",")
            if part.strip().isdigit()]


def remember_position_rule() -> bool:
    """Crea (si falta) la regla de KWin que recuerda la posición.

    Devuelve True si la regla existe al terminar (la acaba de crear o ya
    estaba), y False si no aplica o no se pudo. Así quien llama marca el ajuste
    ``kwin_rule_done`` solo cuando de verdad ha quedado puesta.
    """
    if not available():
        return False
    try:
        ids = _rule_ids()
        for rule_id in ids:
            if _read("wmclass", str(rule_id)).strip() == WINDOW_CLASS:
                return True   # ya estaba
        rule_id = max(ids) + 1 if ids else 1
        _write("Description", RULE_DESCRIPTION, str(rule_id))
        _write("wmclass", WINDOW_CLASS, str(rule_id))
        _write("wmclassmatch", CLASS_MATCH_SUBSTRING, str(rule_id))
        _write("wmclasscomplete", "false", str(rule_id))
        _write("positionrule", POSITION_RULE_REMEMBER, str(rule_id))
        _write("rules", ",".join(str(x) for x in sorted({*ids, rule_id})),
               "General")
        # KWin relee su configuración (y con ella las reglas) al pedírselo.
        _run(["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"])
        log(f"kwin: creada la regla 'recuerda posición' ({rule_id})")
        return True
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        log(f"kwin: no se pudo crear la regla: {error}")
        return False
