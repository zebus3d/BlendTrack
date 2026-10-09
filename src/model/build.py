"""Modelos de datos: compilaciones disponibles e instaladas.

Se usan dataclasses sencillas para que el resto de la aplicación trabaje con
objetos claros en lugar de diccionarios sueltos.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Versiones de Blender con soporte de larga duración (Long Term Support).
# Las LTS reciben mantenimiento durante varios años, así que merece la pena
# destacarlas en la tienda.
LTS_MINORS = {"2.83", "2.93", "3.3", "3.6", "4.2", "4.5", "5.2"}

# Forks de Blender que la aplicación sabe descargar y gestionar. Se guardan en
# ``Build.fork``/``InstalledBuild.fork`` con estos identificadores; el texto
# visible lo pone la interfaz (``fork_label``). La cadena vacía es Blender.
FORK_BLENDER = ""
FORK_BFORARTISTS = "bforartists"
FORK_UPBGE = "upbge"
FORK_LABELS = {
    FORK_BLENDER: "Blender",
    FORK_BFORARTISTS: "Bforartists",
    FORK_UPBGE: "UPBGE",
}


def fork_label(fork: str) -> str:
    """Nombre visible de un fork ('' -> 'Blender')."""
    return FORK_LABELS.get(fork or "", fork or "Blender")


def version_tuple(version: str):
    """Convierte '5.2.1' en (5, 2, 1) para poder ordenar y comparar versiones."""
    parts = re.findall(r"\d+", version or "")
    return tuple(int(part) for part in parts) if parts else (0,)


def minor_of(version: str) -> str:
    """Devuelve la rama menor: '5.2.1' -> '5.2' (la clave de las LTS)."""
    parts = (version or "").split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else (version or "")


def upbge_series(version: str) -> str:
    """Serie de Blender que corresponde a una versión de UPBGE.

    UPBGE numera con su propio esquema, pero por dentro es un Blender: la
    0.53 es en realidad un Blender 5.3 y guarda su configuración en la carpeta
    ``5.3`` (comprobado en el ``getUserDir`` de su código). La regla es mover
    el punto un dígito: ``0.53`` -> ``5.3``, ``0.40`` -> ``4.0``, ``0.36`` ->
    ``3.6``. Si la versión no tiene la forma esperada se devuelve su serie tal
    cual, para no inventar una carpeta.
    """
    match = re.match(r"0\.(\d+)", (version or "").strip())
    if not match:
        return minor_of(version)
    digits = match.group(1)
    return f"{digits[0]}.{digits[1:]}" if len(digits) >= 2 else digits


def fork_series(fork: str, version: str) -> str:
    """Serie de configuración de un fork, o la de Blender para el resto.

    Es la carpeta que usan Migración, add-ons y snapshots: cada fork la
    escribe con su propio nombre y, en el caso de UPBGE, con la serie de
    Blender a la que equivale su versión.
    """
    if fork == FORK_UPBGE:
        return upbge_series(version)
    return minor_of(version)


def human_size(size) -> str:
    """Convierte bytes a un texto legible (por ejemplo, '359.8 MB')."""
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def favorite_key(branch: str, version: str) -> str:
    """Clave con la que se guarda un favorito: rama + versión.

    Se guarda la **serie**, no la compilación exacta: las diarias cambian de
    hash cada día y un favorito atado al hash desaparecería al día siguiente.

    Tampoco se incluye plataforma ni arquitectura, a propósito: las versiones
    instaladas no las guardan, y así marcar 4.5.5 en la tienda marca también la
    que ya tienes instalada (y al revés).
    """
    return f"{branch or ''}|{version or ''}"


@dataclass
class Build:
    """Una compilación concreta publicada en los servidores de Blender."""

    version: str
    branch: str
    risk: str
    platform: str
    arch: str
    url: str
    filename: str
    size: int = 0
    checksum: Optional[str] = None
    mtime: int = 0
    # Hash corto del commit con el que se compiló (lo da la API en "hash").
    # Es lo único que distingue dos compilaciones diarias del mismo día a día:
    # todas las alfa de 'main' comparten número de versión.
    build_hash: str = ""
    # True si la compilación viene de una rama experimental (la sección
    # "Branch" del builder, ramas con funciones nuevas que aún no están en una
    # versión oficial). Se muestra con su propio filtro "Experimental".
    experimental: bool = False
    # Fork del que sale la compilación: vacío para Blender oficial,
    # "bforartists" o "upbge" para los otros. Es lo que decide en qué pestaña
    # se ve, a qué tipo de carpeta va y qué configuración lee al migrar. Antes
    # de este campo no había forma de distinguir "Bforartists 5.2.0" de
    # "Blender 5.2.0": comparten número de versión.
    fork: str = FORK_BLENDER
    # Página de notas de versión propia del fork, si la fuente la conoce (el
    # release de GitHub de UPBGE, por ejemplo). Para Blender se calcula por
    # serie (``api.release_notes_url``).
    notes_url: str = ""

    @property
    def is_fork(self) -> bool:
        """True si no es Blender oficial."""
        return bool(self.fork)

    @property
    def fork_name(self) -> str:
        """Nombre visible del fork ('Blender', 'Bforartists', 'UPBGE')."""
        return fork_label(self.fork)

    @property
    def is_lts(self) -> bool:
        """True si es una versión LTS.

        Solo las estables pueden serlo (tabla ``LTS_MINORS``).
        """
        return self.risk == "stable" and minor_of(self.version) in LTS_MINORS

    @property
    def sort_key(self):
        # Orden por versión y, a igualdad, por fecha de compilación.
        """Clave de orden: por versión y, a igualdad, por fecha."""
        return (version_tuple(self.version), self.mtime)

    @property
    def human_size(self) -> str:
        """Tamaño legible para el usuario (por ejemplo '359.8 MB')."""
        return human_size(self.size)

    @property
    def favorite_key(self) -> str:
        """Serie a la que pertenece, para los favoritos (ver ``favorite_key``)."""
        return favorite_key(self.branch, self.version)


@dataclass
class InstalledBuild:
    """Una versión ya descargada y extraída en la carpeta destino."""

    name: str
    path: Path
    version: str
    executable: Optional[Path] = None
    # Hash de la compilación, leído del marcador que dejamos al instalar.
    # Las carpetas instaladas antes de que existiera el marcador lo traen
    # vacío, y entonces se compara solo por versión (como se hacía siempre).
    build_hash: str = ""
    # Rama de la que salió la compilación, también leída del marcador. Sirve
    # para reconocer las ramas experimentales ("geometry-nodes", etc.). Vacío
    # en instalaciones antiguas, que se tratan como normales.
    branch: str = ""
    # "Riesgo" que dio la API al instalarla ("stable", "alpha", "candidate"),
    # también del marcador. Es la respuesta buena a "¿qué tipo es esto?": sin
    # él hay que adivinar por la rama y el nombre (ver ``services.channels``).
    risk: str = ""
    # Carpeta de la biblioteca de la que salió esta instalación. Con ella se
    # sabe si vive en una carpeta con el candado cerrado y si encaja con lo que
    # esa carpeta recibe. ``None`` en un escaneo suelto.
    # OJO: es un ``Path``, que no es serializable a JSON. Hoy no se cachean las
    # instaladas (el caché de ``api`` es solo de ``Build``); si algún día se
    # hace, hay que convertirlo a texto.
    root: Optional[Path] = None
    # Fork del que salió la instalación ('' = Blender). Se reconoce por el
    # nombre de la carpeta y por el marcador; decide el ejecutable que hay que
    # buscar y en qué carpeta de configuración vive la versión.
    fork: str = FORK_BLENDER

    @property
    def fork_name(self) -> str:
        """Nombre visible del fork ('Blender', 'Bforartists', 'UPBGE')."""
        return fork_label(self.fork)

    @property
    def can_launch(self) -> bool:
        """True si encontramos el ejecutable de esta versión."""
        return self.executable is not None

    @property
    def favorite_key(self) -> str:
        """La misma clave que la de la tienda, para compartir los favoritos."""
        return favorite_key(self.branch, self.version)
