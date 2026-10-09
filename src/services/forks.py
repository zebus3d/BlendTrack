"""Compilaciones de los forks de Blender: Bforartists y UPBGE.

Blender no es el único: Bforartists (una interfaz retocada) y UPBGE (el motor
de juegos, el Blender Game Engine que salió del oficial en 2.8) publican sus
propias versiones y la gente las usa a diario. Blender Launcher V2 las ofrece
desde hace años, y este módulo trae las mismas dos fuentes, con la biblioteca
estándar (``urllib``): **no se añade ninguna dependencia** al empaquetado.

* **Bforartists** vive en un NextCloud público. Su listado es **WebDAV**:
  un ``PROPFIND`` a la raíz devuelve una carpeta ``Bforartists X.Y.Z`` por
  versión y, dentro, los ficheros por plataforma. El share es público y su
  token va aquí igual que en Blender Launcher, que lo publica en su código.
  Para no repetir 30 y pico peticiones en cada refresco, cada versión se cachea
  por su ``getlastmodified`` y solo se vuelve a pedir la que cambió.
* **UPBGE** publica en GitHub Releases. Es una sola petición JSON, con ``ETag``
  para no gastar cuota cuando nada cambió (GitHub no cuenta los 304). Sus
  builds son casi siempre semanales (``weekly-build-N``, alfa de la rama de
  desarrollo); si algún día publicara releases estables, salen como estables.

Los dos devuelven objetos ``Build`` con ``fork`` relleno, que es lo que usan
``services.channels`` (a qué carpeta van), la interfaz (en qué pestaña salen) y
``services.blender_config`` (qué configuración leen). El fallo de un fork
**nunca** puede tumbar el listado de Blender: quien llama los trata por
separado (ver ``api.fetch_builds``).

No importa Qt.
"""

import base64
import concurrent.futures
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, fields
from datetime import datetime
from email.utils import parsedate_to_datetime

from model.build import FORK_BFORARTISTS, FORK_UPBGE, Build
from services import net, tls
from services.downloader import log
from services.settings import cache_dir, write_json_atomic


# Cuántas peticiones WebDAV se lanzan a la vez al listar Bforartists. Con una
# versión por petición (más de 30), hacerlas en serie tardaba decenas de
# segundos; en paralelo la espera la marca la más lenta, no la suma.
BFA_WORKERS = 6

# --------------------------------------------------------------- Bforartists

BFA_HOST = "cloud.bforartists.de"
BFA_ORIGIN = f"https://{BFA_HOST}"
BFA_WEBDAV_URL = f"{BFA_ORIGIN}/public.php/webdav"
# Share público (sin contraseña, la del usuario va vacía). Es el mismo que usa
# Blender Launcher V2 y está pensado para que lo lea cualquiera.
BFA_SHARE_TOKEN = "JxCjbyt2fFcHjy4"
BFA_AUTH = base64.b64encode(f"{BFA_SHARE_TOKEN}:".encode()).decode()

# ``Bforartists 5.2.0`` -> 5.2.0. El nombre de la carpeta es la versión.
BFA_VERSION_RE = re.compile(r"Bforartists\s+(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)
# Fichero de descarga de cada plataforma. El nombre siempre lleva el prefijo
# ``Bforartists-``, así que esto descarta capturas, manuales y el instalador
# ``Install_Bforartists...exe`` de Windows.
BFA_FILE_RES = {
    "linux": re.compile(r"^Bforartists-.+linux.*\.tar\.xz$", re.IGNORECASE),
    "windows": re.compile(r"^Bforartists-.+windows.*\.zip$", re.IGNORECASE),
    "darwin": re.compile(r"^Bforartists-.+\.dmg$", re.IGNORECASE),
}
# macOS publica un .dmg por arquitectura desde Bforartists 4.5 (Intel y
# Silicon). Sin esta pista, los dos ficheros caerían en la misma versión.
BFA_MAC_ARM = ("silicon", "arm64", "aarch64")
BFA_MAC_INTEL = ("intel", "x86_64", "x64")

DAV = "{DAV:}"


def bfa_cache_path():
    """Fichero donde se guarda el listado de Bforartists."""
    return cache_dir() / "bforartists.json"


def upbge_cache_path():
    """Fichero donde se guarda el listado de UPBGE."""
    return cache_dir() / "upbge.json"


def _known_fields():
    """Nombres de los campos de ``Build``, para reconstruir del caché."""
    return {field.name for field in fields(Build)}


def _propfind(url: str, timeout: int) -> bytes:
    """Hace un ``PROPFIND`` WebDAV y devuelve el XML (Depth 1)."""
    request = urllib.request.Request(
        url, method="PROPFIND",
        headers={
            "User-Agent": net.USER_AGENT,
            "Depth": "1",
            "Authorization": "Basic " + BFA_AUTH,
        })
    with urllib.request.urlopen(request, timeout=timeout,
                                context=tls.ssl_context()) as response:
        return response.read()


def _http_get_json(url: str, timeout: int, etag: str | None = None):
    """Descarga un JSON y devuelve ``(datos, etag)``.

    Con ``etag`` manda ``If-None-Match``: si el servidor contesta 304, devuelve
    ``(None, etag)`` y quien llame reutiliza la caché (y en GitHub además no le
    cuenta la petición contra el límite).
    """
    headers = {"User-Agent": net.USER_AGENT, "Accept": "application/vnd.github+json"}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=tls.ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data, response.headers.get("ETag")
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return None, etag
        raise


def _parse_multistatus(data: bytes) -> list:
    """Convierte el XML de un ``PROPFIND`` en una lista de entradas.

    Cada entrada es ``{href, name, is_dir, modified, size}``. El ``href`` se
    deja **codificado** a propósito: es lo que hay que concatenar al origen para
    pedir o descargar el recurso sin volver a escapar los espacios.
    """
    entries = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        log(f"bforartists webdav: XML ilegible ({error})")
        return entries
    for response in root.findall(f"{DAV}response"):
        href = (response.findtext(f"{DAV}href") or "").strip()
        if not href:
            continue
        prop = response.find(f"{DAV}propstat/{DAV}prop")
        if prop is None:
            continue
        name = urllib.parse.unquote(href.rstrip("/").rsplit("/", 1)[-1])
        modified = 0
        raw_date = prop.findtext(f"{DAV}getlastmodified") or ""
        if raw_date:
            try:
                modified = int(parsedate_to_datetime(raw_date).timestamp())
            except (TypeError, ValueError):
                modified = 0
        try:
            size = int(prop.findtext(f"{DAV}getcontentlength") or 0)
        except (TypeError, ValueError):
            size = 0
        entries.append({
            "href": href,
            "name": name,
            "is_dir": prop.find(f"{DAV}resourcetype/{DAV}collection") is not None,
            "modified": modified,
            "size": size,
        })
    return entries


def _bfa_platform_arch(name: str, platform: str) -> str:
    """Arquitectura de un fichero de Bforartists (solo importa en macOS)."""
    if platform != "darwin":
        return "arm64" if any(k in name.lower() for k in BFA_MAC_ARM) else "x86_64"
    lower = name.lower()
    if any(key in lower for key in BFA_MAC_ARM):
        return "arm64"
    return "x86_64"


def _bfa_builds_from(entries: list, version: str) -> list:
    """Convierte los ficheros de una carpeta de versión en ``Build``.

    Devuelve **todas** las plataformas, no solo la de este equipo: la tienda
    permite descargar para otro sistema (p. ej. copiar en un USB), y ese filtro
    ya lo hace ``api.available_for``.
    """
    builds = []
    for entry in entries:
        if entry["is_dir"]:
            continue
        name = entry["name"]
        for platform, pattern in BFA_FILE_RES.items():
            if not pattern.match(name):
                continue
            builds.append(Build(
                version=version,
                branch=FORK_BFORARTISTS,
                risk="stable",
                platform=platform,
                arch=_bfa_platform_arch(name, platform),
                url=BFA_ORIGIN + entry["href"],
                filename=name,
                size=entry["size"],
                mtime=entry["modified"],
                fork=FORK_BFORARTISTS,
            ))
            break
    return builds


def _load_cache(path):
    """Lee un caché JSON de disco, o ``{}`` si falta o está roto."""
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _builds_from_cache(items) -> list:
    """Reconstruye ``Build`` de los diccionarios guardados, ignorando campos
    que ya no existan (o que falten)."""
    known = _known_fields()
    builds = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        try:
            builds.append(Build(**{key: value for key, value in item.items()
                                   if key in known}))
        except TypeError:
            continue
    return builds


def _bfa_cached_versions(cache: dict) -> dict:
    """Las versiones guardadas en el caché de Bforartists (``{}`` si no hay).

    El caché tiene la forma ``{"versions": {"5.2.0": {"modified": ...,
    "assets": [...]}}}``: se guarda **por versión** para poder reutilizar
    cada una mientras su fecha no cambie.
    """
    versions = cache.get("versions")
    return versions if isinstance(versions, dict) else {}


def _bfa_builds_from_versions(versions: dict) -> list:
    """Aplana las versiones guardadas en una lista de ``Build``.

    Es el único sitio que sabe leer la forma del caché, y lo usan los dos
    caminos: el normal (versiones recién consultadas o reutilizadas) y el de
    emergencia (la raíz no contesta y se sirve todo lo guardado). Antes el de
    emergencia buscaba una clave ``builds`` que este caché nunca tuvo, así que
    sin red la pestaña de Bforartists salía vacía.
    """
    builds = []
    for saved in versions.values():
        if isinstance(saved, dict):
            builds.extend(_builds_from_cache(saved.get("assets")))
    return builds


def fetch_bforartists(timeout: int = 20) -> list:
    """Listado de Bforartists: versiones y ficheros, con caché por fecha.

    Se pide la raíz (una petición) y luego una por versión, **solo de las que
    cambiaron** desde la última vez. Las que no, se sirven del caché en disco.
    Si algo falla, se cae al caché entero antes que dejar la pestaña vacía.
    """
    cache = _load_cache(bfa_cache_path())
    cached = _bfa_cached_versions(cache)
    try:
        root_entries = _parse_multistatus(_propfind(BFA_WEBDAV_URL + "/", timeout))
    except Exception as error:
        log(f"bforartists listing unavailable: {error}")
        return _bfa_builds_from_versions(cached)

    versions = []
    for entry in root_entries:
        if not entry["is_dir"]:
            continue
        match = BFA_VERSION_RE.search(entry["name"])
        if match:
            versions.append((match.group(1), entry))
    if not versions:
        # Una raíz sin ninguna versión no es "Bforartists ya no publica nada":
        # es una respuesta que no es el listado (un 200 con la página de
        # mantenimiento, un portal cautivo...). Si se siguiera, se guardaría
        # un caché vacío encima del bueno y el próximo arranque sin red ya no
        # tendría nada que enseñar.
        log("bforartists listing has no versions; keeping the cache")
        return _bfa_builds_from_versions(cached)

    fresh = {}
    pending = []
    for version, entry in versions:
        saved = cached.get(version)
        if (isinstance(saved, dict) and saved.get("modified") == entry["modified"]
                and entry["modified"]):
            fresh[version] = saved
        else:
            pending.append((version, entry))

    def _one(item):
        version, entry = item
        try:
            data = _propfind(BFA_ORIGIN + entry["href"], timeout)
            builds = _bfa_builds_from(_parse_multistatus(data), version)
        except Exception as error:
            log(f"bforartists {version} unavailable: {error}")
            # La versión que falló se queda con lo que tuviera el caché.
            return version, entry, cached.get(version)
        return version, entry, {
            "modified": entry["modified"],
            "assets": [asdict(build) for build in builds],
        }

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=BFA_WORKERS) as pool:
            for version, entry, saved in pool.map(_one, pending):
                if saved is not None:
                    fresh[version] = saved

    # Se recorre en el orden de la raíz, no en el de los hilos, para que el
    # listado salga siempre igual.
    builds = _bfa_builds_from_versions(
        {version: fresh[version] for version, _entry in versions
         if version in fresh})
    try:
        write_json_atomic(bfa_cache_path(),
                          {"saved_at": int(time.time()), "versions": fresh})
    except OSError as error:
        log(f"could not save bforartists cache: {error}")
    return builds


# --------------------------------------------------------------------- UPBGE

UPBGE_API_URL = "https://api.github.com/repos/UPBGE/upbge/releases?per_page=100"
# Por debajo de 0.30 son releases antiguos del fork (heredados de Blender) que
# no interesan en la tienda.
UPBGE_MINIMUM = (0, 30)
UPBGE_WEEKLY_PREFIX = "weekly-build-"
UPBGE_FILE_RES = {
    "linux": re.compile(r"upbge-.+linux.*\.(tar\.xz|tar\.gz)$", re.IGNORECASE),
    "windows": re.compile(r"upbge-.+windows.*\.zip$", re.IGNORECASE),
    "darwin": re.compile(r"upbge-.+macos.*\.(zip|dmg)$", re.IGNORECASE),
}
UPBGE_VERSION_RE = re.compile(r"upbge-(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)


def _upbge_version(release: dict, asset_name: str) -> str:
    """Versión de una release de UPBGE, del tag o del nombre del fichero."""
    match = UPBGE_VERSION_RE.search(asset_name or "")
    if match:
        return match.group(1)
    tag = str(release.get("tag_name") or "").lstrip("v")
    match = re.match(r"(\d+\.\d+(?:\.\d+)?)", tag)
    return match.group(1) if match else ""


def _upbge_platform(name: str) -> str:
    """Plataforma de un asset de UPBGE a partir de su nombre."""
    lower = name.lower()
    if "windows" in lower:
        return "windows"
    if "macos" in lower or "mac" in lower:
        return "darwin"
    return "linux"


def _upbge_builds_from(release: dict, weekly: bool) -> list:
    """Convierte los assets de una release de UPBGE en ``Build``."""
    builds = []
    release_url = str(release.get("html_url") or "")
    mtime = 0
    raw_date = str(release.get("published_at") or "")
    if raw_date:
        try:
            mtime = int(datetime.fromisoformat(
                raw_date.replace("Z", "+00:00")).timestamp())
        except ValueError:
            mtime = 0
    for asset in release.get("assets", []) or ():
        name = str(asset.get("name") or "")
        url = str(asset.get("browser_download_url") or "")
        if not url:
            continue
        platform = next((key for key, pattern in UPBGE_FILE_RES.items()
                         if pattern.search(name)), None)
        if platform is None:
            continue
        version = _upbge_version(release, name)
        if not version:
            continue
        if not weekly:
            try:
                if tuple(int(part) for part in version.split(".")[:2]) < UPBGE_MINIMUM:
                    continue
            except ValueError:
                pass
        checksum = None
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            checksum = digest.split(":", 1)[1]
        builds.append(Build(
            version=version,
            branch="upbge-weekly" if weekly else "upbge-stable",
            risk="alpha" if weekly else "stable",
            platform=platform,
            arch="arm64" if "arm64" in name.lower() or "aarch64" in name.lower()
            else "x86_64",
            url=url,
            filename=name,
            size=int(asset.get("size") or 0),
            checksum=checksum,
            mtime=mtime,
            experimental=weekly,
            fork=FORK_UPBGE,
            notes_url=release_url,
        ))
    return builds


def _parse_upbge(releases) -> list:
    """Lista de ``Build`` a partir de las releases de la API de GitHub."""
    builds = []
    if not isinstance(releases, list):
        return builds
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if not release.get("published_at"):
            continue
        tag = str(release.get("tag_name") or "")
        weekly = tag.startswith(UPBGE_WEEKLY_PREFIX)
        builds.extend(_upbge_builds_from(release, weekly))
    return builds


def fetch_upbge(timeout: int = 20) -> list:
    """Listado de UPBGE desde GitHub Releases, con ``ETag`` y caché.

    Una sola petición por refresco. Si la respuesta es 304 se reutiliza lo
    guardado; si algo falla, también (mejor enseñar el listado de ayer que
    dejar la pestaña vacía).
    """
    cache = _load_cache(upbge_cache_path())
    cached_builds = _builds_from_cache(cache.get("builds"))
    try:
        releases, etag = _http_get_json(UPBGE_API_URL, timeout,
                                        etag=cache.get("etag"))
    except Exception as error:
        log(f"upbge listing unavailable: {error}")
        return cached_builds

    if releases is None:
        return cached_builds

    builds = _parse_upbge(releases)
    try:
        write_json_atomic(upbge_cache_path(), {
            "saved_at": int(time.time()),
            "etag": etag,
            "builds": [asdict(build) for build in builds],
        })
    except OSError as error:
        log(f"could not save upbge cache: {error}")
    return builds


# Registro de los forks disponibles. La clave es el identificador que viaja en
# ``Build.fork`` y el valor, cómo se llama y cómo se descarga su listado.
FORKS = {
    FORK_BFORARTISTS: {"fetch": fetch_bforartists, "label": "Bforartists"},
    FORK_UPBGE: {"fetch": fetch_upbge, "label": "UPBGE"},
}


def fetch(fork: str, timeout: int = 20) -> list:
    """Listado de un fork por su identificador ([] si no se conoce)."""
    entry = FORKS.get(fork)
    if entry is None:
        return []
    return entry["fetch"](timeout=timeout)


def download_headers(url: str) -> dict:
    """Cabeceras que necesita una descarga según de dónde venga.

    El share de Bforartists es un NextCloud público: el listado y los ficheros
    exigen la misma autenticación Basic (usuario = token, contraseña vacía).
    Sin esto, descargar una versión de Bforartists daba 401. El resto de
    fuentes (CDN de Blender, releases de GitHub) son públicas.
    """
    # Se compara el host ya interpretado, no el principio del texto: con
    # ``startswith`` también pasaban ``https://cloud.bforartists.de.otro.com``
    # o ``https://cloud.bforartists.de@otro.com``, y la autenticación (aunque
    # sea de un share público) acabaría en un servidor ajeno.
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme == "https" and parsed.hostname == BFA_HOST:
        return {"Authorization": "Basic " + BFA_AUTH}
    return {}
