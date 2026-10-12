"""Dónde guarda Blender la configuración de cada versión.

``~/.config/blender/5.2/`` en Linux, ``%APPDATA%`` en Windows,
``~/Library/Application Support`` en macOS, y las variables de entorno con las
que el usuario puede moverlo de sitio. De aquí salen las rutas que usan los
demás módulos:

* ``blender_addons`` — migrar add-ons y ficheros de preferencias.
* ``blender_snapshots`` — los guardados de "valores de fábrica".
* ``blender_prefs`` — las preferencias clave a clave (preguntándoselo a Blender).

Es un módulo de ``services/``: no importa Qt.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from model.build import (FORK_BFORARTISTS, FORK_BLENDER, FORK_UPBGE,
                         fork_series, version_tuple)

# Variables de entorno con las que Blender permite reubicar sus carpetas. Son
# la forma estándar de decirle a Blender "usa esta config" y hay que respetarlas
# o la migración acabaría escribiendo donde no toca.
RESOURCES_ENV = "BLENDER_USER_RESOURCES"
CONFIG_ENV = "BLENDER_USER_CONFIG"
SCRIPTS_ENV = "BLENDER_USER_SCRIPTS"
EXTENSIONS_ENV = "BLENDER_USER_EXTENSIONS"

# Subcarpetas dentro de la carpeta de una versión.
CONFIG_SUBDIR = "config"
SCRIPTS_SUBDIR = "scripts"
EXTENSIONS_SUBDIR = "extensions"
LEGACY_ADDONS_SUBDIR = "addons"
# Repositorio local por defecto: ahí van las extensiones instaladas a mano
# ("Install from Disk"). Las que vienen de extensions.blender.org caen en
# ``blender_org``, así que se escanean todos los repositorios.
LOCAL_REPO = "user_default"
MANIFEST_NAME = "blender_manifest.toml"


# Marcador donde anotamos qué copiamos, para poder deshacer más adelante.
MIGRATION_MARKER = ".blendtrack-migration.json"
# Sufijo de las copias de seguridad. **Solo se conserva una por destino** (ver
# ``blender_addons.park_existing``), no un histórico con fecha.
BACKUP_SUFFIX = ".blendtrack-bak"
# Sufijo de la copia a medio hacer: se copia primero aquí y, solo cuando está
# completa, se renombra a su sitio (ver ``blender_addons.replace_with_copy``).
STAGING_SUFFIX = ".blendtrack-tmp"

# Estados de compatibilidad de un addon. El texto lo pone la interfaz.
OK = "ok"            # compatible
WARN = "warn"        # se puede copiar, pero conviene avisar
BLOCKED = "blocked"  # no se debe copiar: no es compatible

# Motivos (claves de i18n). ``""`` cuando todo está bien.
REASON_REQUIRES_NEWER = "requires_newer"   # el addon pide un Blender más nuevo
REASON_TOO_NEW = "too_new"                 # el addon no soporta esta versión aún
REASON_PLATFORM = "platform"               # no está publicado para este sistema
REASON_WHEEL_ABI = "wheel_abi"             # sus wheels no son para este Python
REASON_UNKNOWN_VERSION = "unknown_version"  # el addon no declara versión mínima
# Deducciones a partir de los ficheros del addon (``services.addon_checks``):
# siempre avisos, nunca bloqueos.
REASON_PRE_280 = "pre_280"           # declara un Blender de antes de 2.80
REASON_REMOVED_API = "removed_api"   # importa un módulo que Blender ya no trae
REASON_NATIVE_ABI = "native_abi"     # trae compilados para otro Python


# Versión de Python que embebe cada serie de Blender. Solo se usa para avisar
# de wheels incompatibles; si la serie es más vieja que todas las de la tabla,
# no se avisa (mejor no dar un falso positivo que inventar). Las de 4.5, 5.0,
# 5.1 y 5.2 están comprobadas arrancando cada build y preguntándole
# ``sys.version_info``; las demás salen de las notas de versión de Blender.
#
# Es una tabla, y no una pregunta al Blender de destino, a propósito: el plan
# de migración se recalcula cada vez que se cambia de versión en los
# desplegables, y arrancar un Blender en cada cambio son segundos de espera
# para un dato que solo cambia una vez cada pocos años.
_PYTHON_BY_SERIES = {
    (3, 6): "3.10",
    # 4.0 sigue con 3.10; el salto a 3.11 llegó en 4.1 (con la plataforma VFX
    # de 2024). La fila decía (4, 0): "3.11" y avisaba al revés para 4.0.
    (4, 1): "3.11",
    (4, 2): "3.11",
    (4, 5): "3.11",
    (5, 0): "3.11",
    (5, 1): "3.13",
    (5, 2): "3.13",
    (5, 3): "3.13",
}


def python_for_version(version: str) -> str:
    """Versión de Python probable de una build, o "" si no la conocemos.

    Se busca la última serie conocida que no supere la versión pedida, así que
    para una serie **más nueva que todas las de la tabla** esto *extrapola*:
    devuelve el Python de la última conocida. Mientras Blender no cambie de
    Python acierta, y el día que lo cambie el aviso de wheels de esa serie será
    erróneo hasta que se añada la fila (una línea; ver ``_PYTHON_BY_SERIES``
    para por qué es una tabla).
    """
    target = version_tuple(version)
    if not target:
        return ""
    best = None
    for series, python in _PYTHON_BY_SERIES.items():
        if series <= target and (best is None or series > best[0]):
            best = (series, python)
    return best[1] if best else ""


# --------------------------------------------------------------- ubicaciones

# Carpeta de configuración de cada programa en cada sistema: ``(Linux, macOS,
# Windows)``. En Linux cuelga de ``$XDG_CONFIG_HOME`` (``~/.config``), en macOS
# de ``~/Library/Application Support`` y en Windows de ``%APPDATA%``, donde los
# tres usan dos niveles (organización y programa). Comprobado en el
# ``getUserDir`` de cada uno: Bforartists y UPBGE escriben en su propio sitio,
# no en el de Blender, aunque sean Blender por dentro.
_CONFIG_FOLDERS = {
    FORK_BLENDER: ("blender", "Blender", ("Blender Foundation", "Blender")),
    FORK_BFORARTISTS: ("bforartists", "Bforartists",
                       ("Bforartists", "Bforartists")),
    FORK_UPBGE: ("upbge", "UPBGE", ("UPBGE", "Blender")),
}


def config_base(platform: str, env: dict | None = None,
                fork: str = FORK_BLENDER) -> Path:
    """Carpeta que contiene una subcarpeta por versión de Blender (o del fork).

    Para Blender: ``~/.config/blender``, ``%APPDATA%\\Blender
    Foundation\\Blender`` y ``~/Library/Application Support/Blender``. Los
    nombres de los forks están en ``_CONFIG_FOLDERS``; un fork desconocido se
    trata como Blender.
    """
    env = os.environ if env is None else env
    linux, macos, windows = _CONFIG_FOLDERS.get(fork,
                                                _CONFIG_FOLDERS[FORK_BLENDER])
    if platform == "windows":
        appdata = env.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(appdata).joinpath(*windows)
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / macos
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / linux


@dataclass
class BlenderConfig:
    """Carpetas de configuración de una versión concreta de Blender.

    Se guardan las tres rutas ya resueltas (y no solo ``root``) porque las
    variables ``BLENDER_USER_CONFIG/_SCRIPTS/_EXTENSIONS`` pueden descolgarlas
    a sitios distintos.
    """

    version: str
    root: Path
    platform: str
    config_dir: Path
    scripts_dir: Path
    extensions_dir: Path
    # Fork del que es esta configuración ('' = Blender). Se usa sobre todo para
    # saber a quién pertenece al migrar entre versiones.
    fork: str = FORK_BLENDER

    @property
    def addons_dir(self) -> Path:
        """Carpeta de los addons legacy (``scripts/addons``)."""
        return self.scripts_dir / LEGACY_ADDONS_SUBDIR

    def exists(self) -> bool:
        """True si la carpeta de esa versión ya existe."""
        return self.root.is_dir()


def config_for(version: str, platform: str, env: dict | None = None,
               fork: str = FORK_BLENDER) -> BlenderConfig:
    """Ubica la carpeta de una versión, respetando los ``BLENDER_USER_*``.

    Blender usa ``mayor.menor`` como nombre de carpeta (``4.5`` para 4.5.13),
    así que se normaliza la versión antes de construir la ruta. Los forks usan
    su propia base (``~/.config/bforartists``, ``~/.config/upbge``) y, en el
    caso de UPBGE, la serie de Blender a la que equivale su versión (0.53 ->
    5.3, ver ``model.build.fork_series``). Cuando el usuario fija una carpeta
    con una variable de entorno, esa ruta sustituye a la subcarpeta
    correspondiente (no se le añade la versión).

    ``BLENDER_USER_RESOURCES`` reemplaza la carpeta de la versión entera; si
    está definida, ``config``/``scripts``/``extensions`` cuelgan de ella.
    """
    env = os.environ if env is None else env
    minor = fork_series(fork, version) or version
    resources = (env.get(RESOURCES_ENV) or "").strip()
    root = Path(resources).expanduser() if resources else \
        config_base(platform, env, fork) / minor

    def _override(name: str, fallback: Path) -> Path:
        value = (env.get(name) or "").strip()
        return Path(value).expanduser() if value else fallback

    return BlenderConfig(
        version=minor,
        root=root,
        platform=platform,
        config_dir=_override(CONFIG_ENV, root / CONFIG_SUBDIR),
        scripts_dir=_override(SCRIPTS_ENV, root / SCRIPTS_SUBDIR),
        extensions_dir=_override(EXTENSIONS_ENV, root / EXTENSIONS_SUBDIR),
        fork=fork,
    )


# ------------------------------------------------------------ preferencias

# Ficheros de la carpeta ``config`` que forman las preferencias del usuario.
# ``userpref.blend`` es lo que Blender llama "preferences" (keymap, temas,
# addons habilitados...); ``startup.blend`` es la escena/UI por defecto, que es
# un ``.blend`` completo y por eso merece un aviso aparte.
USERPREF_FILE = "userpref.blend"
STARTUP_FILE = "startup.blend"

# Ficheros que se pueden copiar como "preferencias": (clave, fichero). El
# ``userpref`` lleva ajustes, tema, keymap y addons activos; el ``startup`` es
# la escena y la disposición por defecto (la interfaz avisa de que pisa más).
PREFERENCE_FILES = (
    ("userpref", USERPREF_FILE),
    ("startup", STARTUP_FILE),
)
