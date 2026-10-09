"""Pedir permisos de administrador en Windows (el diálogo UAC).

En Windows, carpetas como ``C:\\Program Files`` solo las puede escribir un
administrador. Si el usuario elige una de esas (típicamente para las versiones
LTS), la descarga falla con "Permission denied" y no hay forma de escribir ahí
sin elevación.

Aquí se lanza **este mismo binario** con el verbo ``runas`` de Windows (lo que
dispara el UAC) y el argumento interno ``--grant-access CARPETA``. Ese segundo
proceso, ya elevado, crea la carpeta y le concede permiso de escritura al
usuario con ``icacls``; después termina. Así se pide permiso **una sola vez** y
la app sigue corriendo sin privilegios: no se relanza toda la aplicación como
administrador, que dejaría los ficheros a nombre de admin y luego no se podrían
borrar sin volver a elevarlos.

Solo se usa en Windows; en el resto de sistemas ``available()`` es False y ni se
ofrece.
"""

import ctypes
import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

from services.downloader import log

# ShellExecuteW devuelve un HINSTANCE: los valores <= 32 son error. Si el
# usuario rechaza el UAC devuelve 5 (``SE_ERR_ACCESSDENIED``). El 1223
# (``ERROR_CANCELLED``) que se ve citado a veces es otra cosa: lo da
# ``GetLastError`` con ``ShellExecuteEx``, no el retorno de ``ShellExecuteW``.
SHELLEXECUTE_MIN_OK = 32

# Argumento con el que el proceso sin privilegios le dice al elevado **para
# quién** es el permiso (ver ``current_account``).
FOR_USER_ARG = "--for-user"

# Lo que puede llevar una cuenta de Windows (``DOMINIO\usuario``). Se valida
# porque acaba dentro de la orden de ``icacls`` (``cuenta:(OI)(CI)M``): unos
# dos puntos o unos paréntesis en el nombre cambiarían qué permiso se concede.
# Windows tampoco los admite en un nombre de usuario, así que no se pierde nada.
_ACCOUNT_RE = re.compile(r'^[^\x00-\x1f:()*?"<>|,;=+\[\]/]{1,256}$')


def available() -> bool:
    """True si se puede pedir elevación (solo Windows)."""
    return sys.platform.startswith("win")


def current_account() -> str:
    """La cuenta que está usando la app (``DOMINIO\\usuario``), o "".

    Se calcula **antes** de elevar, en el proceso del usuario, y viaja al
    proceso elevado como argumento. Leer ``USERNAME`` ya elevado no sirve:
    si el usuario no es administrador, el UAC le pide la contraseña de otra
    cuenta que sí lo es, el proceso elevado corre como **esa** cuenta, y el
    permiso se le daba al administrador en vez de al usuario que lo pidió.
    """
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN") or ""
    return f"{domain}\\{user}" if domain and user else (user or "")


def valid_account(account: str) -> bool:
    """True si ``account`` es un nombre de cuenta que se puede pasar a ``icacls``."""
    return bool(account) and bool(_ACCOUNT_RE.match(account))


def _command(args) -> list:
    """Comando para relanzar esta app con ``args``.

    Empaquetada es el propio ejecutable; en modo fuente, el intérprete con el
    ``main.py`` por delante (para que en Windows también se pueda probar sin
    compilar). La ruta del ``main.py`` se saca de este fichero y es absoluta:
    ``sys.argv[0]`` puede ser relativa (``src/main.py``), y el proceso elevado
    arranca en ``C:\\Windows\\System32``, donde esa ruta no lleva a ninguna parte.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    return [sys.executable, str(main_py), *args]


def relaunch_elevated(args) -> bool:
    """Relanza la app con permisos de administrador (UAC).

    Añade ``--for-user`` con la cuenta actual (ver ``current_account``) para
    que el proceso elevado sepa a quién darle el permiso. Devuelve True si el
    usuario ha aceptado. No espera a que el proceso elevado termine (es
    rápido): quien llama decide cómo esperar, porque solo él sabe qué
    comprobar.
    """
    if not available():
        return False
    args = list(args)
    account = current_account()
    if valid_account(account):
        args += [FOR_USER_ARG, account]
    command = _command(args)
    parameters = subprocess.list2cmdline(command[1:])
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", command[0], parameters, None, 1)
    except Exception as error:
        log(f"elevation failed: {error}")
        return False
    if result <= SHELLEXECUTE_MIN_OK:
        log(f"elevation not accepted (code {result})")
        return False
    return True


def grant_write(folder, account: str | None = None) -> bool:
    """Crea ``folder`` y da permiso de **modificación** a ``account``.

    Se ejecuta **dentro** del proceso elevado (``--grant-access``). ``M`` en
    ``icacls`` es *Modify*: leer, escribir, crear y borrar, que es lo que la
    app normal (sin privilegios) necesita para descargar, extraer y borrar de
    esa carpeta sin volver a preguntar. No es control total (``F``): no deja
    cambiar permisos ni dueño, y no hace falta. ``(OI)(CI)`` lo hereda todo lo
    que se cree dentro.

    ``account`` es la cuenta que pidió el permiso (llega con ``--for-user``).
    Si falta o no es válida se usa la del proceso, que acierta en el caso
    normal (el usuario es administrador de su equipo).
    """
    path = Path(folder)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        log(f"grant-access: no se pudo crear {path}: {error}")
        return False
    if not account or not valid_account(account):
        if account:
            log(f"grant-access: cuenta no válida {account!r}; se usa la actual")
        account = os.environ.get("USERNAME") or getpass.getuser()
    try:
        result = subprocess.run(
            ["icacls", str(path), "/grant", f"{account}:(OI)(CI)M"],
            capture_output=True, text=True, timeout=60)
    except Exception as error:
        log(f"grant-access: icacls falló: {error}")
        return False
    if result.returncode != 0:
        log(f"grant-access: icacls devolvió {result.returncode}: "
            f"{result.stdout.strip()} {result.stderr.strip()}")
        return False
    return True
