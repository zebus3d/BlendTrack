"""Acceso a la API de compilaciones de Blender.

Blender publica un JSON con todas las compilaciones (estables por rama y
alfa de ``main``). Es mucho más fiable que hacer scraping del HTML, que es
lo que hacían los primeros prototipos.

Endpoint: https://builder.blender.org/download/daily/?format=json&v=2

Solo usamos la biblioteca estándar (urllib) para no añadir dependencias
extra al empaquetado.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, fields

from model.build import FORK_BFORARTISTS, FORK_UPBGE, Build
from services.downloader import log
from services import channels, forks as forks_service, tls
from services.settings import cache_dir, write_json_atomic

API_URL = "https://builder.blender.org/download/daily/?format=json&v=2"
# Las ramas experimentales (sección "Branch" del builder) publican en su
# propio listado, el mismo que Blender Launcher usa para "experimental". Solo
# aparece contenido cuando el equipo de Blender abre ramas de funciones nuevas;
# la mayor parte del tiempo está vacío (y entonces la app lo explica en la
# tienda).
EXPERIMENTAL_URL = "https://builder.blender.org/download/experimental/?format=json&v=2"
CACHE_MAX_AGE = 3600  # una hora de validez para el caché en disco

# Extensiones descargables que nos interesan (descartamos .sha256, .msi, etc.).
VALID_EXTENSIONS = {"xz", "zip", "dmg", "tar", "gz", "bz2"}

# Preferimos un formato de archivo por plataforma: tar.xz en Linux, zip portable
# en Windows y dmg en macOS.
PREFERRED_EXTENSION = {"linux": "xz", "windows": "zip", "darwin": "dmg"}

USER_AGENT = "BlenderManager/0.2 (+https://github.com/zebus3d/BlenderManager)"


def cache_path():
    """Ruta del caché del listado de compilaciones."""
    return cache_dir() / "builds.json"


def _fetch_json(url: str, timeout: int = 20, etag: str | None = None):
    """Descarga un JSON y devuelve ``(datos, etag)``.

    Con ``etag`` manda ``If-None-Match``; si el servidor responde 304 (no ha
    cambiado), ``datos`` es ``None`` y se reutiliza la caché. Así, refrescar no
    vuelve a bajarse un listado que ya tenemos.
    """
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    # El contexto va explicito: el OpenSSL del binario no encuentra las CAs en
    # distros que no son Debian (ver services/tls.py).
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=tls.ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data, response.headers.get("ETag")
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return None, etag
        raise


def normalize_arch(value: str) -> str:
    """Unifica el nombre de la arquitectura.

    Blender publica la misma arquitectura con dos nombres según la plataforma
    (``amd64`` en Windows, ``x86_64`` en Linux y macOS) y la barra de filtros de
    la aplicación solo ofrece ``x86_64``/``arm64``. Sin esto, elegir Windows
    dejaba la tienda **vacía**: había 8 builds, pero ninguna coincidía con el
    nombre que pedía el filtro.
    """
    text = (value or "").strip().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(text, text)


def _to_build(entry: dict, experimental: bool = False) -> Build:
    """Convierte una entrada del JSON en un objeto Build."""
    return Build(
        version=str(entry.get("version") or ""),
        branch=str(entry.get("branch") or ""),
        risk=str(entry.get("risk_id") or ""),
        platform=str(entry.get("platform") or ""),
        arch=normalize_arch(entry.get("architecture")),
        url=str(entry.get("url") or ""),
        filename=str(entry.get("file_name") or ""),
        size=int(entry.get("file_size") or 0),
        checksum=entry.get("checksum"),
        mtime=int(entry.get("file_mtime") or 0),
        build_hash=str(entry.get("hash") or ""),
        experimental=experimental,
    )


def _fetch_builds_from(url: str, timeout: int, experimental: bool,
                       etag: str | None = None):
    """Descarga un listado y se queda con las extensiones que sabemos abrir.

    Devuelve ``(builds, etag)``; ``builds`` es ``None`` si el servidor dijo 304
    (no ha cambiado), para que quien llame reutilice la caché.
    """
    entries, new_etag = _fetch_json(url, timeout=timeout, etag=etag)
    if entries is None:
        return None, etag
    builds = []
    for entry in entries:
        if entry.get("file_extension") not in VALID_EXTENSIONS:
            continue
        builds.append(_to_build(entry, experimental=experimental))
    return builds, new_etag


def fetch_builds(timeout: int = 20, etags: dict | None = None,
                 cached=None, forks=()):
    """Descarga el listado completo: diarias + experimentales + forks.

    ``etags``/``cached`` son de la última vez: si un listado responde 304, se
    reutilizan las builds de la caché en vez de volver a bajarlo. El listado
    experimental y **cada fork** van en su propio try: un fallo de cualquiera de
    ellos no debe impedir que se vean las compilaciones normales. Devuelve
    ``(builds, etags)``.

    ``forks`` son los identificadores activados (``settings.enabled_forks()``);
    con la lista vacía no se hace ninguna petición extra.
    """
    etags = etags or {}
    cached = list(cached or [])
    new_etags = {}

    daily, daily_etag = _fetch_builds_from(API_URL, timeout, experimental=False,
                                           etag=etags.get(API_URL))
    if daily is None:
        daily = [build for build in cached if not build.experimental
                 and not build.fork]
    if daily_etag:
        new_etags[API_URL] = daily_etag
    builds = list(daily)

    try:
        experimental, exp_etag = _fetch_builds_from(
            EXPERIMENTAL_URL, timeout, experimental=True,
            etag=etags.get(EXPERIMENTAL_URL))
        if experimental is None:
            experimental = [build for build in cached
                            if build.experimental and not build.fork]
        if exp_etag:
            new_etags[EXPERIMENTAL_URL] = exp_etag
        builds += experimental
    except Exception as error:
        log(f"experimental builds unavailable: {error}")

    for fork in forks or ():
        try:
            # ``forks.fetch`` ya trae su propia caché y, si falla la red, cae a
            # lo guardado; un fork desconocido devuelve [].
            builds += forks_service.fetch(fork, timeout=timeout)
        except Exception as error:
            log(f"{fork} builds unavailable: {error}")
    return builds, new_etags


def save_cache(builds, etags: dict | None = None) -> None:
    """Guarda el listado en disco, para poder arrancar sin red.

    Los ``etags`` se guardan con él: en el siguiente refresco se mandan como
    ``If-None-Match`` y, si nada cambió, el servidor contesta 304.
    """
    payload = {
        "saved_at": int(time.time()),
        "builds": [asdict(build) for build in builds],
        "etags": etags or {},
    }
    write_json_atomic(cache_path(), payload)


def load_etags() -> dict:
    """ETags del último listado guardado (por URL), o ``{}``."""
    path = cache_path()
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    etags = payload.get("etags")
    return etags if isinstance(etags, dict) else {}


def load_cache(max_age=CACHE_MAX_AGE):
    """Lee el caché en disco. Con ``max_age=None`` lo acepta aunque esté caducado."""
    path = cache_path()
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if max_age is not None and time.time() - payload.get("saved_at", 0) > max_age:
        return []
    # Un caché escrito por una versión anterior puede traer campos que ya no
    # existen (o faltarle alguno nuevo): nos quedamos solo con los que conoce
    # el modelo, en vez de descartar la entrada entera.
    known = {field.name for field in fields(Build)}
    builds = []
    for item in payload.get("builds", []):
        # Un caché de antes del arreglo trae "amd64": se normaliza al leerlo.
        if "arch" in item:
            item["arch"] = normalize_arch(item["arch"])
        try:
            builds.append(Build(**{key: value for key, value in item.items() if key in known}))
        except TypeError:
            continue
    return builds


def _without_disabled_forks(builds, forks) -> list:
    """Quita las versiones de los forks que no están activados.

    El caché se escribió con los forks que hubiera encendidos entonces. Si el
    usuario apaga Bforartists y el listado sale del caché (está fresco, o no
    hay red), sus versiones seguirían apareciendo en "Todas" sin que nadie las
    haya pedido.
    """
    enabled = set(forks or ())
    return [build for build in builds if not build.fork or build.fork in enabled]


def get_builds(force: bool = False, forks=()) -> list:
    """Listado de compilaciones: usa caché y, si falla la red, cae al caché antiguo.

    ``forks`` son los forks activados; las versiones de los demás no se
    devuelven nunca, vengan de la red o del caché.
    """
    if not force:
        cached = _without_disabled_forks(load_cache(), forks)
        if cached:
            return cached
    # La caché (aunque esté caducada) sirve para dos cosas: reutilizar las
    # builds de un listado que responda 304 y tener algo que enseñar sin red.
    stale = load_cache(max_age=None)
    try:
        builds, etags = fetch_builds(etags=load_etags(), cached=stale,
                                     forks=forks)
    except Exception as error:
        # Sin conexión: mejor mostrar algo (aunque esté caducado) que nada.
        # Se apunta el motivo: sin él, "no veo versiones nuevas" no se puede
        # distinguir de "no hay versiones nuevas" mirando el log.
        log(f"builds listing unavailable, using the cache: {error}")
        return _without_disabled_forks(stale, forks)
    # Guardar va aparte de descargar: si el disco está lleno o la carpeta de
    # caché no deja escribir, el listado recién bajado sigue siendo bueno y
    # tirarlo para enseñar el viejo sería castigar al usuario por el disco.
    try:
        save_cache(builds, etags)
    except OSError as error:
        log(f"could not save the builds cache: {error}")
    return builds


def available_for(builds, platform: str, arch: str):
    """Filtra por plataforma y arquitectura, quedándose con un archivo por versión."""
    preferred = PREFERRED_EXTENSION.get(platform)
    filtered = [build for build in builds if build.platform == platform and build.arch == arch]
    # La preferencia de formato (tar.xz en Linux, zip en Windows, dmg en macOS)
    # se aplica **por fork**: UPBGE en Linux solo publica .tar.gz, y si se
    # aplicara al conjunto entero, la presencia de un .tar.xz de Blender dejaría
    # fuera todas sus versiones. Un fork que no tenga el formato preferido se
    # queda con lo que publique.
    if preferred:
        kept = []
        for fork in {build.fork for build in filtered}:
            group = [build for build in filtered if build.fork == fork]
            matching = [build for build in group
                        if build.filename.endswith("." + preferred)]
            kept.extend(matching or group)
        filtered = kept
    # Puede haber varias entradas de la misma versión/rama, y Blender y un fork
    # pueden compartir número (Bforartists 5.2.0 y Blender 5.2.0): el fork
    # entra en la clave o se pisarían. Nos quedamos con la más reciente.
    best = {}
    for build in filtered:
        key = (build.fork, build.version, build.branch, build.risk)
        current = best.get(key)
        if current is None or build.mtime > current.mtime:
            best[key] = build
    return sorted(best.values(), key=lambda build: build.sort_key, reverse=True)


def filter_builds(builds, channel: str, search: str = "", favorites=()):
    """Aplica el filtro de canal y la búsqueda a las compilaciones de la tienda.

    "Todas" (*all*) enseña **todo**: Blender estable, LTS, diarias, ramas
    experimentales y los forks (Bforartists, UPBGE), que es lo que promete el
    nombre. El resto de canales son excluyentes y estrictos: "experimental" solo
    las ramas de Blender, y cada fork el suyo (sus versiones no se mezclan con
    las de Blender ni entre sí). Así quien quiere Blender no se topa con otros
    programas en las pestañas concretas, pero en "Todas" ve el catálogo entero.

    ``favorites`` es la lista de claves marcadas por el usuario
    (``model.build.favorite_key``). Con el canal "favorites" se muestran solo
    esas, sin excluir las experimentales ni los forks: ahí manda lo que haya
    marcado.
    """
    fork_channel = next((fork for fork, build_type in channels.FORK_TYPES.items()
                         if build_type == channel), None)
    if channel == "favorites":
        marked = set(favorites or ())
        selected = [build for build in builds if build.favorite_key in marked]
    elif channel == "all":
        # "Todas" es literalmente todas: también las experimentales y los forks.
        selected = list(builds)
    elif fork_channel:
        selected = [build for build in builds if build.fork == fork_channel]
    elif channel == "experimental":
        selected = [build for build in builds
                    if build.experimental and not build.fork]
    else:
        selected = [build for build in builds
                    if not build.fork and not build.experimental]
        # La clasificación vive en ``services.channels`` porque de ella depende
        # también a qué carpeta se descarga cada compilación: si aquí dijera
        # una cosa y allí otra, una build podría salir en la pestaña "Diarias"
        # y aterrizar en la carpeta de las LTS.
        if channel in (channels.TYPE_LTS, channels.TYPE_STABLE,
                       channels.TYPE_DAILY):
            selected = [build for build in selected
                        if channels.type_of_build(build) == channel]
        elif channel == "lts_stable":
            # LTS y estables a la vez (todo lo estable).
            selected = [build for build in selected
                        if channels.type_of_build(build) in (
                            channels.TYPE_LTS, channels.TYPE_STABLE)]
    text = (search or "").strip().lower()
    if text:
        selected = [
            build for build in selected
            if text in build.version.lower() or text in build.branch.lower()
        ]
    return selected


# Notas de versión de Blender. Cada serie tiene su propia página:
# https://developer.blender.org/docs/release_notes/4.2/
RELEASE_NOTES_URL = "https://developer.blender.org/docs/release_notes/"
# La serie más antigua que tiene página propia publicada.
OLDEST_RELEASE_NOTES = (2, 79)


def release_notes_url(version: str, fork: str = "", notes_url: str = "") -> str:
    """Devuelve la URL de las notas de versión de una compilación.

    Los forks traen su propia página: Bforartists publica sus notas en su web
    y UPBGE en la release de GitHub (que la fuente ya nos da en
    ``notes_url``). Para Blender las páginas van por serie (mayor.menor), así
    que de "4.2.1" o de "5.2.0-alpha" nos quedamos con "4.2" y "5.2". Si la
    versión no se entiende o es anterior a las notas publicadas, abrimos el
    índice general.
    """
    if notes_url:
        return notes_url
    if fork == FORK_BFORARTISTS:
        return "https://www.bforartists.de/download/"
    if fork == FORK_UPBGE:
        return "https://upbge.org/#/download"
    match = re.search(r"(\d+)\.(\d+)", version or "")
    if not match:
        return RELEASE_NOTES_URL
    series = (int(match.group(1)), int(match.group(2)))
    if series < OLDEST_RELEASE_NOTES:
        return RELEASE_NOTES_URL
    return f"{RELEASE_NOTES_URL}{series[0]}.{series[1]}/"
