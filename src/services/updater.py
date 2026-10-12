"""Comprobación e instalación de actualizaciones desde GitHub Releases.

La aplicación consulta la última release publicada, compara su versión con
``version.__version__`` y, si hay una nueva, descarga el binario de la
plataforma actual y lo instala.

Solo usamos la biblioteca estándar (urllib). La API de GitHub exige un
``User-Agent`` y permite 60 peticiones por hora sin autenticar; usamos ETag
para que las comprobaciones repetidas devuelvan 304 (que no consumen cuota).

Aplicar la actualización es distinto en cada plataforma:

* **Linux AppImage**: se reemplaza el propio archivo ``$APPIMAGE`` y se relanza.
* **Windows**: no se puede sobrescribir un .exe en ejecución, así que se
  extrae la release en un directorio temporal y se relanza el binario *nuevo*
  con ``--apply-update``; ese proceso espera a que salga el actual, copia los
  ficheros y arranca la versión nueva.
* **macOS**: se extrae el zip y se lanza un helper (como Sparkle) que espera a
  que la app se cierre, mueve el bundle viejo, copia el nuevo con ``ditto``,
  quita la cuarentena y relanza. Si no se puede (sin permiso en el directorio del
  bundle, bundle no localizable), se cae a revelar la descarga y avisar.
* **Cualquier otro caso**: solo se avisa y se revela la descarga.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import version
from paths import APP_DIR
from services import macos_dmg, opener, tls
from services.downloader import log
from services.extractor import extract
from services.settings import cache_dir, write_json_atomic

REPO = "zebus3d/BlendTrack"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
USER_AGENT = f"BlendTrack/{version.__version__} (+https://github.com/{REPO})"

# Un asset por plataforma. El nombre debe coincidir con lo que sube el CI.
ASSET_NAMES = {
    "linux": "BlendTrack-x86_64.AppImage",
    "windows": "BlendTrack-windows-x86_64.zip",
    "darwin": "BlendTrack-macos.zip",
}
CHECKSUM_NAME = "checksums.txt"
EXE_NAME = "BlendTrack.exe" if sys.platform.startswith("win") else "BlendTrack"

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200

#: Marca que este proceso es un relanzamiento tras aplicar una actualización.
#: El binario nuevo arranca **antes** de que el viejo termine de cerrarse, así
#: que no debe pedirle el turno por el socket de instancia única: le pediría al
#: viejo que salga al frente, el viejo se cerraría y la app se quedaría sin
#: ninguna instancia. Con esta marca el proceso nuevo toma el relevo directo.
RELAUNCH_ENV = "BLENDTRACK_RELAUNCH"


def _relaunch_env() -> dict:
    """Entorno para relanzar la app, con la marca de relevo y sin el
    ``LD_LIBRARY_PATH`` del bundle (ver ``opener.clean_env``)."""
    env = opener.clean_env()
    env[RELAUNCH_ENV] = "1"
    return env


def updates_dir() -> Path:
    """Carpeta donde se descargan y preparan las actualizaciones."""
    return cache_dir() / "updates"


# --- Comprobación de versión ------------------------------------------------

def _version_key(text: str):
    """Convierte 'v1.2.3' en (1, 2, 3) para poder comparar.

    No usa ``model.build.version_tuple`` a propósito, aunque se parezcan: aquí
    las versiones son **tags de la app**, y en modo fuente llegan como el
    describe del checkout (``1.2.0-19-g24a0b43``). ``version_tuple`` saca todos
    los grupos de dígitos del texto, así que de ese describe sacaría
    ``(1, 2, 0, 19, 24, 0, 43)`` —los dígitos del hash incluidos— y la
    comparación dejaría de significar nada. Aquí se toma el primer número de
    cada tramo separado por puntos y se para ahí. Comparar mal estas versiones
    es justo lo que provoca el bucle infinito de actualización que avisa
    AGENTS.md, así que se quedan separadas.
    """
    key = []
    for chunk in (text or "").strip().lstrip("vV").split("."):
        digits = re.findall(r"\d+", chunk)
        key.append(int(digits[0]) if digits else 0)
    return tuple(key)


def is_newer(current: str, latest: str) -> bool:
    """True si ``latest`` es más nueva que ``current``."""
    if not latest:
        return False
    return _version_key(latest) > _version_key(current)


def asset_for(system) -> str:
    """Nombre del asset que corresponde a esta plataforma (o None)."""
    os_name = getattr(system, "os_name", system) or ""
    return ASSET_NAMES.get(os_name)


# --- Modo fuente (git) ------------------------------------------------------
#
# Al correr desde el código (``python3 src/main.py``) no hay binario que
# reemplazar: lo equivalente a actualizarse es un ``git pull``. Lo hacemos solo
# sobre un checkout limpio, para no pisar cambios locales sin guardar.

def source_root():
    """Raíz del checkout git, o None si no aplica (empaquetado o sin .git).

    Solo miramos el ``.git`` de la propia carpeta del proyecto (``APP_DIR``); no
    subimos por los directorios padre para no confundir este repo con otro que
    lo contenga (un monorepo, por ejemplo) y acabar haciendo pull de aquel.
    """
    if getattr(sys, "frozen", False):
        return None
    root = Path(APP_DIR)
    return root if (root / ".git").exists() else None


def source_tag() -> str:
    """Último tag del checkout (p. ej. ``v1.1.10``), o ``''`` si no se puede."""
    root = source_root()
    if root is None:
        return ""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "describe", "--tags", "--abbrev=0"],
            stderr=subprocess.DEVNULL, timeout=10)
    except Exception as error:
        log(f"git describe failed: {error}")
        return ""
    return output.decode("utf-8", "ignore").strip()


def source_describe() -> str:
    """Describe del checkout (p. ej. ``v1.2.0-19-g24a0b43``), o ``''``.

    A diferencia de ``source_tag``, esto incluye los commits que el checkout va
    por delante del último tag. Es la versión que de verdad se está ejecutando:
    en una rama de desarrollo el tag se queda atrás y decir "1.2.0" engaña.
    """
    root = source_root()
    if root is None:
        return ""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "describe", "--tags", "--always",
             "--abbrev=7"],
            stderr=subprocess.DEVNULL, timeout=10)
    except Exception as error:
        log(f"git describe failed: {error}")
        return ""
    return output.decode("utf-8", "ignore").strip()


def app_version() -> str:
    """Versión que se muestra en la app.

    Si el CI inyectó una versión, esa. En modo fuente ``version.py`` vale
    ``0.0.0``, así que usamos el describe del checkout: en main limpio sale el
    tag (``1.2.0``) y en una rama por delante, los commits de más
    (``1.2.0-19-g24a0b43``).
    """
    if version.__version__ != "0.0.0":
        return version.__version__
    described = source_describe()
    if described:
        return described.lstrip("vV")
    tag = source_tag()
    return tag.lstrip("vV") if tag else version.__version__


def source_update(timeout: int = 120):
    """Actualiza el checkout con ``git pull --ff-only``.

    Devuelve ``(ok, motivo)``. Si hay cambios locales sin confirmar no toca
    nada: preferimos no pisar el trabajo del usuario. ``motivo`` es ``"ok"`` si
    llegó algo nuevo, ``"up-to-date"`` si el pull no movió HEAD, ``"dirty"`` o
    ``"failed"``.
    """
    root = source_root()
    if root is None:
        return False, "no-git"
    try:
        status = subprocess.check_output(
            ["git", "-C", str(root), "status", "--porcelain"],
            stderr=subprocess.DEVNULL, timeout=20).decode("utf-8", "ignore").strip()
    except Exception as error:
        log(f"git status failed: {error}")
        return False, "failed"
    if status:
        return False, "dirty"
    before = _git_head(root)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=timeout,
            env=opener.clean_env())
    except Exception as error:
        log(f"git pull failed: {error}")
        return False, "failed"
    if result.returncode != 0:
        log(f"git pull failed: {result.stderr.strip()}")
        return False, "failed"
    after = _git_head(root)
    if before and after and before == after:
        return True, "up-to-date"
    return True, "ok"


def _git_head(root: Path) -> str:
    """SHA de HEAD del checkout (o ``''`` si no se puede leer)."""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=20)
    except Exception as error:
        log(f"git rev-parse failed: {error}")
        return ""
    return output.decode("utf-8", "ignore").strip()


def relaunch_source() -> bool:
    """Relanza la app en modo fuente para usar el código recién descargado."""
    if source_root() is None:
        return False
    # ``clean_env`` en todos los relanzamientos: el proceso nuevo heredaría el
    # LD_LIBRARY_PATH del bootloader viejo y, peor, lo guardaría como el
    # "original" que después pasa a Blender y al navegador (ver opener).
    kwargs = {"env": _relaunch_env()}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, *sys.argv], **kwargs)
    except OSError as error:
        log(f"source relaunch failed: {error}")
        return False
    return True


def _parse_release(payload: dict):
    """Extrae (tag, assets) del JSON de una release de GitHub."""
    tag = str(payload.get("tag_name") or "")
    assets = []
    for item in payload.get("assets") or []:
        assets.append({
            "name": str(item.get("name") or ""),
            "url": str(item.get("browser_download_url") or ""),
        })
    return tag, assets


def _release_cache():
    base = updates_dir()
    return base / "release.json", base / "release.etag"


def latest_release(force: bool = False, timeout: int = 15):
    """Última release como (tag, assets), o None si no se puede consultar.

    Si ya hay una respuesta cacheada, envía su ETag; cuando GitHub responde 304
    reutilizamos el JSON guardado (y esa petición no gasta cuota).
    """
    json_path, etag_path = _release_cache()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    etag = ""
    if etag_path.is_file():
        try:
            etag = etag_path.read_text(encoding="utf-8").strip()
        except OSError:
            etag = ""
    if etag and not force:
        headers["If-None-Match"] = etag

    request = urllib.request.Request(API_URL, headers=headers)
    new_etag = etag
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                     context=tls.ssl_context()) as response:
            payload = json.loads(response.read().decode("utf-8"))
            new_etag = response.headers.get("ETag") or etag
    except urllib.error.HTTPError as error:
        if error.code == 304 and json_path.is_file():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        else:
            log(f"update check failed: {error}")
            return None
    except Exception as error:
        log(f"update check failed: {error}")
        return None

    try:
        write_json_atomic(json_path, payload)
        if new_etag:
            etag_path.write_text(new_etag, encoding="utf-8")
    except OSError:
        pass
    return _parse_release(payload)


def checksum_for(assets, filename: str, timeout: int = 15):
    """SHA-256 de ``filename`` según checksums.txt de la release (o None)."""
    url = next((a["url"] for a in assets if a["name"] == CHECKSUM_NAME), None)
    if not url:
        log(f"update: la release no trae {CHECKSUM_NAME}; se descarga sin verificar")
        return None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                     context=tls.ssl_context()) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception as error:
        log(f"checksum fetch failed: {error}")
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and Path(parts[1].lstrip("*")).name == filename:
            return parts[0]
    return None


# --- Aplicar la actualización ----------------------------------------------

def _open_fallback(path=None) -> None:
    """Abre la descarga o la página de releases para actualizar a mano.

    Va por ``services.opener`` para que funcione dentro del AppImage: si
    lanzáramos ``xdg-open`` con el entorno del binario, heredaría el
    ``LD_LIBRARY_PATH`` de PyInstaller y el gestor de ficheros/navegador no
    arrancaría (ver el docstring de ``opener``).
    """
    try:
        if path and Path(path).exists() and not sys.platform.startswith("win"):
            # Mejor dejar al usuario delante del archivo que acaba de bajar
            # que en la página de releases, donde tendría que bajarlo otra vez.
            opener.reveal(path)
            return
        opener.open_url(RELEASES_URL)
    except Exception as error:
        log(f"open fallback failed: {error}")


def open_releases() -> None:
    """Abre la página de releases para actualizar a mano."""
    _open_fallback()


def _apply_appimage(archive: Path) -> bool:
    """Reemplaza la AppImage en disco y relanza esa versión nueva.

    Devuelve True solo si el reemplazo y el relanzamiento han ido bien. Ante
    cualquier fallo preferimos devolver False (y que la UI avise al usuario)
    antes que cerrar la app y dejarle sin binario funcionando.
    """
    raw = os.environ.get("APPIMAGE", "")
    if not raw:
        # Lanzada desde fuera de una AppImage (p. ej. el binario extraído a
        # mano, o un launcher que no propaga APPIMAGE). Sin esa variable no
        # sabemos qué fichero reemplazar; que la UI ofrezca el .AppImage
        # descargado para abrirlo a mano.
        log("appimage update: $APPIMAGE no definido, no hay nada que reemplazar")
        _make_executable(archive)
        return False
    target = Path(raw).resolve()
    if not target.is_file():
        log(f"appimage update: {target} no es un fichero (existe={target.exists()})")
        _make_executable(archive)
        return False
    try:
        tmp = target.parent / (target.name + ".new")
        shutil.copy2(archive, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)
    except OSError as error:
        log(f"appimage update failed ({target}): {error}")
        # Aseguramos que la copia descargada se puede abrir a mano si el
        # self-replace no ha funcionado (permiso de escritura en el dir,
        # sistema de ficheros read-only, AppImage en una unidad montada...).
        _make_executable(archive)
        return False
    try:
        subprocess.Popen([str(target)], start_new_session=True, close_fds=True,
                         env=_relaunch_env())
    except OSError as error:
        log(f"appimage relaunch failed: {error}")
        _make_executable(archive)
        return False
    return True


def _make_executable(path) -> None:
    """Bit +x al fichero descargado, para poder abrirlo a mano si hace falta."""
    try:
        candidate = Path(path)
        if candidate.is_file():
            candidate.chmod(candidate.stat().st_mode | 0o111)
    except OSError as error:
        log(f"chmod failed for {path}: {error}")


def _apply_windows(archive: Path) -> bool:
    """Extrae la release y lanza el binario nuevo en modo --apply-update."""
    app_dir = Path(sys.executable).resolve().parent
    staging = updates_dir() / f"staging-{int(time.time())}"
    try:
        folder = extract(archive, staging)
    except Exception as error:
        log(f"windows update extract failed: {error}")
        shutil.rmtree(staging, ignore_errors=True)
        return False
    helper = folder / EXE_NAME
    if not helper.is_file():
        candidates = list(folder.rglob(EXE_NAME))
        if not candidates:
            log("windows update helper not found")
            shutil.rmtree(staging, ignore_errors=True)
            return False
        helper = candidates[0]
    try:
        subprocess.Popen(
            [str(helper), "--apply-update", str(app_dir), str(os.getpid())],
            creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
            close_fds=True, env=opener.clean_env(),
        )
    except OSError as error:
        log(f"windows update launch failed: {error}")
        return False
    return True


# --- macOS ---------------------------------------------------------------

def _app_bundle(executable) -> Path | None:
    """Bundle ``.app`` del ejecutable en marcha, o ``None``.

    El binario va en ``<bundle>.app/Contents/MacOS/BlendTrack``, así que el
    bundle es el tercer padre. Si no encaja (modo fuente, layout raro), se
    devuelve ``None`` y se cae al plan B.
    """
    exe = Path(executable).resolve()
    if exe.parent.name != "MacOS" or len(exe.parents) <= 2:
        return None
    bundle = exe.parents[2]
    return bundle if bundle.suffix == ".app" else None


def _apply_macos(archive: Path) -> bool:
    """Reemplaza el ``.app`` con un helper que espera a que la app se cierre.

    No se puede pisar el bundle en marcha de forma fiable, así que se hace como
    Sparkle: se extrae el zip y se deja un pequeño script que **espera** a que
    este proceso muera, mueve el bundle viejo, copia el nuevo con ``ditto``,
    quita la cuarentena y relanza. Devuelve True para que la interfaz cierre la
    app (el helper hace el resto).

    Ante cualquier duda (no encuentro el bundle, no hay permiso de escritura,
    el zip no trae el .app) se cae al plan B: revelar el fichero y no cerrar.
    """
    bundle = _app_bundle(sys.executable)
    if bundle is None:
        log("macos update: no encuentro el bundle .app")
        _open_fallback(archive)
        return False
    if not os.access(bundle.parent, os.W_OK):
        # /Applications sin permiso (o app translocada por Gatekeeper): no nos
        # arriesgamos a dejarle sin app; que la instale a mano.
        log(f"macos update: sin permiso de escritura en {bundle.parent}")
        _open_fallback(archive)
        return False

    staging = updates_dir() / f"macos-{int(time.time())}"
    try:
        # Con ditto, NO con zipfile: Python pierde los bits de ejecución y los
        # enlaces del bundle, y el .app extraído no arranca. Es justo el fallo
        # que reportó un usuario de Mac ("reemplaza pero no abre").
        macos_dmg.extract_zip(archive, staging)
    except Exception as error:
        log(f"macos update extract failed: {error}")
        shutil.rmtree(staging, ignore_errors=True)
        _open_fallback(archive)
        return False

    candidates = list(staging.rglob("*.app"))
    if not candidates or not candidates[0].is_dir():
        log("macos update: el zip no trae ningún .app")
        shutil.rmtree(staging, ignore_errors=True)
        _open_fallback(archive)
        return False
    new_app = candidates[0]

    # El script espera, mueve y copia. Las rutas van entre comillas y los
    # comandos con ruta absoluta (el entorno del binario es mínimo). Si el
    # ditto falla, restaura el bundle viejo para no dejarle sin app.
    script = staging / "apply-update.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'PID={os.getpid()}\n'
        f'BUNDLE="{bundle}"\n'
        f'NEW="{new_app}"\n'
        f'EXE="$NEW/Contents/MacOS/{EXE_NAME}"\n'
        'while kill -0 "$PID" 2>/dev/null; do sleep 0.5; done\n'
        '/bin/sleep 1\n'
        # El bundle nuevo tiene que traer el ejecutable con permisos. Si no
        # (p. ej. se extrajo perdiendo los bits), no se toca nada y se relanza
        # el viejo. Este es el fallo real que reportó un usuario de Mac:
        # "reemplaza pero no abre" al extraer el zip con zipfile.
        'if [ ! -x "$EXE" ]; then /usr/bin/open "$BUNDLE"; exit 1; fi\n'
        '/bin/rm -rf "$BUNDLE.old"\n'
        '/bin/mv "$BUNDLE" "$BUNDLE.old" || { /usr/bin/open "$BUNDLE"; exit 1; }\n'
        '/usr/bin/ditto "$NEW" "$BUNDLE" '
        '|| { /bin/mv "$BUNDLE.old" "$BUNDLE"; /usr/bin/open "$BUNDLE"; exit 1; }\n'
        # -cr: quita TODOS los atributos (cuarentena y provenance); solo con la
        # cuarentena a veces Gatekeeper sigue quejandose.
        '/usr/bin/xattr -cr "$BUNDLE" 2>/dev/null\n'
        # Si el nuevo bundle no llega a abrir, se deja el viejo en su sitio: el
        # .old no se borra hasta que el nuevo arranca.
        '/usr/bin/open "$BUNDLE" '
        '|| { /bin/rm -rf "$BUNDLE"; /bin/mv "$BUNDLE.old" "$BUNDLE"; '
        '/usr/bin/open "$BUNDLE"; exit 1; }\n'
        '/bin/rm -rf "$BUNDLE.old"\n',
        encoding="utf-8")
    try:
        script.chmod(0o755)
        subprocess.Popen(
            ["/bin/sh", str(script)],
            env=opener.clean_env(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True,
        )
    except OSError as error:
        log(f"macos update helper launch failed: {error}")
        shutil.rmtree(staging, ignore_errors=True)
        _open_fallback(archive)
        return False
    log(f"macos update: helper lanzado para reemplazar {bundle}")
    return True


def apply(path) -> bool:
    """Instala la actualización descargada.

    Devuelve True si hay que cerrar la aplicación para completarla (ya se ha
    lanzado la versión nueva o el proceso que la aplicará).
    """
    archive = Path(path)
    if not getattr(sys, "frozen", False):
        # En modo fuente no hay nada que reemplazar: avisamos.
        _open_fallback(archive)
        return False
    if os.environ.get("APPIMAGE"):
        return _apply_appimage(archive)
    if sys.platform.startswith("win"):
        return _apply_windows(archive)
    if sys.platform == "darwin":
        return _apply_macos(archive)
    _open_fallback(archive)
    return False


# --- Modo --apply-update (Windows) -----------------------------------------

def _pid_alive(pid: int) -> bool:
    if sys.platform.startswith("win"):
        try:
            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception:
            return False
        return str(pid) in output.decode("utf-8", "ignore")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_for_exit(pid: int, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.5)


def _copy_tree(source: Path, target: Path) -> None:
    for root, _dirs, files in os.walk(source):
        relative = Path(root).relative_to(source)
        (target / relative).mkdir(parents=True, exist_ok=True)
        for name in files:
            try:
                shutil.copy2(Path(root) / name, target / relative / name)
            except OSError as error:
                log(f"update copy failed: {error}")


def apply_update(app_dir, pid) -> None:
    """Espera al proceso antiguo, copia los ficheros nuevos y relanza.

    Se ejecuta desde el binario *nuevo* (extraído en staging), por lo que este
    proceso puede sobrescribir sin problema al .exe antiguo, que ya no corre.
    """
    target = Path(app_dir)
    source = Path(sys.executable).resolve().parent
    try:
        # Este proceso no tiene ventana ni consola: un PID mal formado no
        # puede abortar la actualización en silencio, así que lo registramos
        # y copiamos igualmente (la app antigua ya se está cerrando).
        _wait_for_exit(int(pid))
    except (TypeError, ValueError):
        log(f"update: invalid pid ({pid!r}), copying without waiting")
    _copy_tree(source, target)
    exe = target / EXE_NAME
    try:
        subprocess.Popen([str(exe)], close_fds=True, env=_relaunch_env())
    except OSError as error:
        log(f"update relaunch failed: {error}")


def cleanup_staging() -> None:
    """Borra los restos de actualizaciones ya aplicadas.

    Son los directorios de staging de Windows y los binarios descargados
    (una AppImage ronda los 60 MB), que hasta ahora se quedaban en el caché
    para siempre. El JSON y el ETag de la release sí se conservan: son
    diminutos y evitan gastar cuota de la API de GitHub.
    """
    base = updates_dir()
    if not base.is_dir():
        return
    keep = {"release.json", "release.etag"}
    for entry in base.iterdir():
        if entry.name in keep:
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass


def cleanup_partials(dest_folder) -> None:
    """Borra descargas de Blender interrumpidas (.part) de la carpeta destino."""
    try:
        folder = Path(dest_folder).expanduser()
        if not folder.is_dir():
            return
        for entry in folder.glob("*.part"):
            try:
                entry.unlink()
            except OSError:
                pass
    except OSError:
        pass
