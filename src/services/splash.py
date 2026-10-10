"""Las imágenes de arranque (*splash*) de cada versión de Blender.

La vista de galería las enseña de fondo en la tarjeta grande. Blender no las
publica sueltas: viven en su repositorio, una por rama de versión, en
``release/datafiles/splash.png`` (1000×500, ~800 KB). Desde la 3.x el fichero va
por **Git LFS**, así que la URL ``raw/`` devuelve un puntero de texto de 131
bytes; la que sirve el PNG de verdad es la ``media/`` de Gitea:

    https://projects.blender.org/blender/blender/media/branch/
        blender-v5.1-release/release/datafiles/splash.png

Comprobado el 2026-10-09: 4.2 y 5.1 dan el PNG; la 2.83 da 404 (la rama
antigua no está en el repositorio nuevo). Una versión sin splash no es un
error: la tarjeta enseña su fondo de reserva.

**Primero se busca en la propia instalación.** Blender no deja el splash como
fichero suelto: al compilar, sus PNG se meten **dentro del ejecutable** (el
``datatoc`` del código de Blender). Así que, si la versión está instalada, se
saca de ahí (``extract_from``): basta buscar la firma de un PNG cuya cabecera
diga 2:1 de proporción. Medido: 0,2 s sobre el binario de 5.1.2, y salen los
mismos 837 352 bytes que en el repositorio. Vale también para las 2.x, que no
están en el repositorio nuevo, y no necesita red.

Si no está instalada, se baja **una vez por serie** (``5.1``, no ``5.1.2``: todas las de la serie
comparten splash) y se guarda en la caché. Las diarias de ``main`` usan el de
``main``, que cambia con cada ciclo, así que ese se renueva cada semana. Un 404
también se recuerda (``.missing``) para no preguntar en cada arranque.

No importa Qt.
"""

import mmap
import os
import struct
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from model.build import FORK_BLENDER, minor_of
from services import downloader, net, tls
from services.downloader import log

BASE_URL = ("https://projects.blender.org/blender/blender/media/branch/"
            "{branch}/release/datafiles/splash.png")

# Las diarias de ``main`` (y la caché de "no existe") se renuevan pasado este
# tiempo: el splash de main cambia cuando arranca un ciclo nuevo.
REFRESH_SECONDS = 7 * 24 * 3600

# Tope de lo que se acepta: un splash pesa ~800 KB. Algo mucho mayor no es un
# splash (o el servidor ha devuelto otra cosa) y no se guarda.
MAX_BYTES = 6 * 1024 * 1024

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def series_for(version: str, branch: str, risk: str, fork: str = FORK_BLENDER) -> str:
    """La serie cuyo splash corresponde a una versión, o ``""`` si no tiene.

    Los forks no tienen splash en el repositorio de Blender. Las alfas salen de
    ``main``, igual que su splash; las beta y candidatas ya tienen su rama de
    release, como las estables.
    """
    if fork and fork != FORK_BLENDER:
        return ""
    if risk == "alpha" or branch == "main":
        return "main"
    return minor_of(version)


def _branch(series: str) -> str:
    return "main" if series == "main" else f"blender-v{series}-release"


def url_for(series: str) -> str:
    return BASE_URL.format(branch=_branch(series))


def _folder() -> Path:
    # Por ``downloader.cache_dir`` y no por ``settings.cache_dir``: los tests
    # redirigen ese nombre a un temporal (``tests/__init__.py``), y sin eso la
    # suite escribía en la caché real del usuario.
    return downloader.cache_dir() / "splash"


def cached(series: str) -> Path | None:
    """La ruta del splash ya bajado de una serie, o ``None`` si no está."""
    if not series:
        return None
    path = _folder() / f"{series}.png"
    return path if path.is_file() else None


def _fresh(path: Path, series: str) -> bool:
    """True si no hace falta volver a preguntar por este fichero."""
    if not path.exists():
        return False
    if series != "main" and path.suffix == ".png":
        return True
    return time.time() - path.stat().st_mtime < REFRESH_SECONDS


def needs_fetch(series: str) -> bool:
    """True si hay que ir a la red a por el splash de la serie."""
    if not series:
        return False
    folder = _folder()
    return not (_fresh(folder / f"{series}.png", series)
                or _fresh(folder / f"{series}.missing", series))


def _store(series: str, data: bytes) -> Path:
    """Guarda el PNG de una serie en la caché, sin pisar a otro hilo.

    Un ``.part`` por hilo: dos descargas de la misma serie a la vez (dos
    pestañas que lo piden juntas) se pisaban el fichero y una de las dos
    fallaba al renombrar.
    """
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{series}.png"
    partial = folder / f"{series}.{os.getpid()}.{threading.get_ident()}.part"
    partial.write_bytes(data)
    partial.replace(target)
    (folder / f"{series}.missing").unlink(missing_ok=True)
    return target


def find_embedded(data) -> bytes | None:
    """El splash dentro de un binario de Blender (o ``None`` si no aparece).

    Se recorren las firmas de PNG; de cada una se lee la cabecera (``IHDR``:
    ancho y alto) y se queda con la mayor que tenga la proporción del splash
    (2:1 desde la 2.80; algo menos en las 2.7x). Comprobar el ``IHDR`` evita
    los falsos positivos: la firma aparece también en medio de otros datos.
    """
    best = None
    pos = 0
    while True:
        start = data.find(PNG_MAGIC, pos)
        if start < 0:
            break
        pos = start + 8
        if data[start + 12:start + 16] != b"IHDR":
            continue
        width, height = struct.unpack(">II", data[start + 16:start + 24])
        if not (500 <= width <= 4000 and height and 1.7 <= width / height <= 2.1):
            continue
        end = data.find(b"IEND", start)
        if end < 0:
            continue
        if best is None or width > best[0]:
            best = (width, bytes(data[start:end + 8]))
    return best[1] if best else None


def extract_from(series: str, executable) -> Path | None:
    """Saca el splash del ejecutable de una instalación y lo guarda en caché."""
    if not series or not executable:
        return None
    try:
        with open(executable, "rb") as handle, mmap.mmap(
                handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
            png = find_embedded(data)
    except (OSError, ValueError) as error:
        log(f"splash {series}: no se pudo leer {executable}: {error}")
        return None
    if png is None or len(png) > MAX_BYTES:
        return None
    return _store(series, png)


def fetch(series: str, timeout: int = 15) -> Path | None:
    """Baja el splash de una serie a la caché y devuelve su ruta.

    Bloquea (la UI lo llama desde un hilo). Si la serie no tiene splash (404)
    lo deja anotado y devuelve ``None``; si falla la red, devuelve lo que ya
    hubiera en caché, sin anotar nada (se reintentará la próxima vez).
    """
    if not series:
        return None
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    missing = folder / f"{series}.missing"
    request = urllib.request.Request(url_for(series),
                                     headers={"User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=tls.ssl_context()) as response:
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            missing.write_text("", encoding="utf-8")
        log(f"splash {series}: HTTP {error.code}")
        return cached(series)
    except (OSError, ValueError) as error:
        log(f"splash {series}: {error}")
        return cached(series)
    # Un puntero de LFS (texto) o una página de error no son un PNG: no se
    # guardan, para no enseñar un fondo roto.
    if len(data) > MAX_BYTES or not data.startswith(PNG_MAGIC):
        log(f"splash {series}: la respuesta no es un PNG ({len(data)} bytes)")
        missing.write_text("", encoding="utf-8")
        return cached(series)
    return _store(series, data)
