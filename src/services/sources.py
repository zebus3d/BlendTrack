"""Fuentes de descarga: el CDN de Blender y, si existe, el release oficial.

El listado de Blender (``builder.blender.org``) da **una** URL por compilación, y
apunta al CDN (``cdn.builder.blender.org``, servido por CDN77). Para las
versiones **estables** existe además el tarball oficial del release en
``download.blender.org`` (detrás de Cloudflare).

Medido desde Argentina con la misma versión (4.4.3, Linux x64), 3 pasadas de
8 MB::

    buildbot por CDN77 (lo que usa la app)   3,57 / 3,02 / 3,84 MB/s
    release oficial por Cloudflare          20,21 / 20,45 / 20,00 MB/s

O sea **~5,6 veces más rápido**. Los espejos "oficiales" que Blender agradece en
su web no sirven para esto: son más lentos que Cloudflare (0,2 a 3,5 MB/s) y el
``mirror.blender.org`` que redirige "al más cercano" manda a EE. UU. desde aquí.
Y las **diarias, alfas y experimentales no tienen alternativa**: no están en
ningún espejo (404 en todos), solo en el CDN.

Por eso no hay una lista de espejos que mantener ni un *reflector*: hay **dos**
candidatas, ambas de Blender, y se elige la que va más rápida midiendo ~1 MB de
cada una. Si la medición falla, se sigue usando el CDN, que es lo de siempre.
"""

import http.client
import time
import urllib.error
import urllib.request
from typing import NamedTuple

from model.build import minor_of
from services import forks, net, tls
from services.downloader import log

RELEASE_BASE = "https://download.blender.org/release"

# Nombre y extensión que usa Blender en los releases oficiales para cada
# combinación. Lo que no esté aquí no tiene release que ofrecer.
RELEASE_NAMES = {
    ("linux", "x86_64"): ("linux-x64", "tar.xz"),
    ("linux", "arm64"): ("linux-arm64", "tar.xz"),
    ("windows", "x86_64"): ("windows-x64", "zip"),
    ("windows", "arm64"): ("windows-arm64", "zip"),
    ("darwin", "x86_64"): ("macos-x64", "dmg"),
    ("darwin", "arm64"): ("macos-arm64", "dmg"),
}


# Para elegir fuente: 4 MB. Con 1 MB pesaba demasiado el arranque de la conexión
# (DNS + TLS) y la medida salía dominada por la latencia, no por la velocidad.
PROBE_BYTES = 4 * 1024 * 1024
PROBE_TIMEOUT = 15


class Source(NamedTuple):
    """Una fuente de descarga con su checksum y una etiqueta para el log."""

    label: str
    url: str
    checksum: str | None = None
    # Cabeceras extra que exige la fuente (el WebDAV público de Bforartists,
    # por ejemplo). El resto van con el User-Agent de siempre.
    headers: dict | None = None


def release_url(build) -> str | None:
    """URL del release oficial de esa versión, o ``None`` si no hay.

    Solo las estables: las diarias y las alfas se compilan al vuelo y nunca se
    publican como release. Y solo **Blender oficial**: Bforartists y UPBGE
    comparten el número de versión (5.2.0 es 5.2.0 en los dos), pero
    ``download.blender.org`` únicamente guarda el Blender de Blender. Sin esta
    comprobación, una Bforartists estable pasaba por el release oficial, la
    sonda veía Cloudflare mucho más rápido que su NextCloud y acababa bajando
    el Blender oficial con el nombre de Bforartists (el checksum cuadraba
    porque era el del release de Blender).
    """
    if getattr(build, "fork", ""):
        return None
    if build.risk != "stable":
        return None
    names = RELEASE_NAMES.get((build.platform, build.arch))
    if not names:
        return None
    name, extension = names
    return (f"{RELEASE_BASE}/Blender{minor_of(build.version)}/"
            f"blender-{build.version}-{name}.{extension}")


def release_checksum(build, timeout: int = 15) -> str | None:
    """SHA-256 del release oficial, leído del ``.sha256`` de esa versión.

    Blender publica un fichero por versión con el hash de cada uno de sus
    ficheros (el del buildbot que da la API no sirve aquí: es otro artefacto).
    """
    url = release_url(build)
    if not url:
        return None
    # El .sha256 vive en la misma carpeta que el fichero: se saca de su URL
    # en vez de volver a montarla (dos copias de la ruta acaban divergiendo).
    folder, name = url.rsplit("/", 1)
    sha_url = f"{folder}/blender-{build.version}.sha256"
    request = urllib.request.Request(sha_url,
                                      headers={"User-Agent": net.USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=tls.ssl_context()) as response:
            text = response.read().decode("utf-8", "replace")
    except Exception as error:
        log(f"release checksum unavailable: {error}")
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*").strip() == name:
            return parts[0]
    return None


def _speed(url: str, timeout: int = PROBE_TIMEOUT) -> float | None:
    """Bytes por segundo bajando un trozo, o ``None`` si no se puede medir.

    Se corta a ``PROBE_BYTES``: si el servidor ignora el ``Range`` seguimos
    leyendo solo eso, no el fichero entero.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": net.USER_AGENT,
                      "Range": f"bytes=0-{PROBE_BYTES - 1}"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=tls.ssl_context()) as response:
            read_bytes = len(response.read(PROBE_BYTES))
    except (urllib.error.URLError, OSError, ValueError,
            http.client.HTTPException) as error:
        # ``HTTPException`` va aparte porque no es un ``OSError``: es lo que
        # lanza ``http.client`` con una respuesta mal formada o cortada
        # (``BadStatusLine``, ``IncompleteRead``). Una sonda fallida solo
        # significa "no se pudo medir", y entonces se usa el CDN.
        log(f"source probe failed ({url}): {error}")
        return None
    elapsed = time.monotonic() - started
    if read_bytes <= 0 or elapsed <= 0:
        return None
    return read_bytes / elapsed


def candidates(build) -> list[Source]:
    """Las fuentes posibles, la del CDN primero (es la de siempre)."""
    options = [Source(cdn_label(), build.url, build.checksum,
                      forks.download_headers(build.url))]
    url = release_url(build)
    if url:
        options.append(Source("Blender release (Cloudflare)", url, None))
    return options


def cdn_label() -> str:
    """Etiqueta de la fuente del CDN (el listado de Blender)."""
    return "Blender CDN"


def choose(build) -> Source:
    """Devuelve la fuente a usar, midiendo cuál de las candidatas va más rápida.

    Ante cualquier duda (una sola candidata, o la medición falla) se usa el CDN,
    que es lo que hacía la aplicación antes de esto: así lo peor que puede pasar
    es que la descarga vaya como siempre.
    """
    options = candidates(build)
    if len(options) == 1:
        return options[0]

    measured = []
    for option in options:
        speed = _speed(option.url)
        if speed:
            measured.append((speed, option))
    if not measured:
        return options[0]

    speed, chosen = max(measured, key=lambda pair: pair[0])
    if chosen.label == cdn_label():
        return chosen
    # El release se verifica con su propio .sha256 (el de la API es del fichero
    # del buildbot, que es otro artefacto).
    chosen = chosen._replace(checksum=release_checksum(build))
    if not chosen.checksum:
        log("release source has no checksum; falling back to the CDN")
        return options[0]
    log(f"fastest source: {chosen.label} ({speed / 1048576:.1f} MB/s)")
    return chosen
