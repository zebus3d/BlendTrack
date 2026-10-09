"""Qué tipo de compilación es cada cosa, y a qué carpeta le toca ir.

Antes esta pregunta se respondía en cuatro sitios a la vez: las constantes de
``settings`` y de ``ui.widgets.main_window``, las ramas de ``api.filter_builds``
y unas heurísticas paralelas en ``installed`` (``is_experimental``,
``_is_stable_install``). Mientras solo servían para pintar una pestaña se podía
vivir con ello; desde que el usuario puede decir "las LTS a este disco y las
diarias a aquel", el tipo de una compilación es un **dato del que depende dónde
se escribe en disco**, y tener cuatro definiciones distintas de "esto es una
diaria" deja de ser un problema estético.

Este módulo es la única definición. No importa ``settings`` ni ``installed``
(ellos importan de aquí), así que no hay ciclos, y no toca disco ni Qt: todo son
funciones puras que se prueban con datos de mentira.
"""

import os
import re
from pathlib import Path

from typing import TYPE_CHECKING

from model.build import FORK_BFORARTISTS, FORK_UPBGE, LTS_MINORS, minor_of

if TYPE_CHECKING:
    # Solo para las anotaciones: ``settings`` importa este módulo, así que
    # importarlo de verdad aquí sería un ciclo. ``TYPE_CHECKING`` es falso al
    # ejecutar y verdadero para los comprobadores de tipos.
    from services.settings import Folder

# Los tipos que puede recibir una carpeta. Son los canales de Blender menos los
# dos que no describen una compilación: "all" (no filtra) y "favorites" (es
# transversal, lo marca el usuario a mano). Los dos últimos son forks: aunque
# compartan número de versión con Blender, van a su propia carpeta y se ven en
# su propia pestaña.
TYPE_LTS = "lts"
TYPE_STABLE = "stable"
TYPE_DAILY = "daily"
TYPE_EXPERIMENTAL = "experimental"
TYPE_BFORARTISTS = "bforartists"
TYPE_UPBGE = "upbge"
BUILD_TYPES = (TYPE_LTS, TYPE_STABLE, TYPE_DAILY, TYPE_EXPERIMENTAL,
               TYPE_BFORARTISTS, TYPE_UPBGE)

# Tipos que solo existen para los forks. La interfaz los enseña únicamente
# cuando el fork está activado (si no, ofrecería casillas para algo que no se
# puede descargar).
FORK_TYPES = {FORK_BFORARTISTS: TYPE_BFORARTISTS, FORK_UPBGE: TYPE_UPBGE}

# Canales de la barra de pestañas. Viven aquí para que no se dupliquen; las
# etiquetas traducidas son cosa de la interfaz.
CHANNELS = ("all", "lts", "stable", "daily", "experimental", "bforartists",
            "upbge", "favorites")

# Las ramas de versión de Blender se llaman ``v`` + dígitos (``v45``, ``v52``).
# Basta con mirar si empieza por "v" no: ``vulkan`` o ``vse-rework`` son ramas
# de funciones nuevas que también empiezan así.
RELEASE_BRANCH_RE = re.compile(r"v\d+")


def type_of_build(build) -> str:
    """Tipo de una compilación de la tienda.

    El orden de las preguntas importa y es el mismo que tenía
    ``api.filter_builds``: un fork es su propio tipo aunque se llame como una
    LTS (Bforartists 5.2.0 es Bforartists, no la LTS de Blender 5.2), una rama
    experimental es experimental aunque su número de versión sea el de una LTS,
    y una alfa de una serie LTS es una diaria, no una LTS. Es decir, manda de
    dónde viene la compilación por encima de cómo se llama.
    """
    fork_type = FORK_TYPES.get(getattr(build, "fork", ""))
    if fork_type:
        return fork_type
    if getattr(build, "experimental", False):
        return TYPE_EXPERIMENTAL
    if getattr(build, "risk", "") != "stable":
        return TYPE_DAILY
    if getattr(build, "is_lts", False):
        return TYPE_LTS
    return TYPE_STABLE


def type_from_marker(branch: str, version: str, name: str = "",
                     risk: str = "", fork: str = "") -> str:
    """Tipo de una instalación, a partir de lo que sepamos de ella.

    ``risk`` es el que anotamos en el marcador al instalarla, y cuando está es
    la respuesta buena: lo dio la propia API de Blender. Solo cuando falta
    —carpetas que no bajó la app, o instaladas antes de que existiera el
    marcador— hay que adivinar, y entonces se mira la rama y el nombre:

    * las ramas normales se llaman ``main`` (diarias) o ``v45``/``v52``
      (estables); cualquier otro nombre es una rama de funciones nuevas;
    * sin rama anotada nos queda el nombre de la carpeta, donde las diarias
      llevan ``alpha``, ``beta`` o ``daily``.

    ``fork`` manda sobre todo lo demás: una carpeta de Bforartists o UPBGE es
    de ese fork aunque su ``risk`` sea ``stable`` y su versión sea la de una
    LTS, porque va a su propia carpeta.

    Sin nada de eso se da por estable, que es lo que era antes de existir el
    marcador y lo menos sorprendente.
    """
    fork_type = FORK_TYPES.get(fork)
    if fork_type:
        return fork_type
    branch = (branch or "").strip()
    if (branch and branch != "main"
            and not RELEASE_BRANCH_RE.fullmatch(branch)):
        return TYPE_EXPERIMENTAL
    if risk and risk != "stable":
        return TYPE_DAILY
    if branch == "main":
        return TYPE_DAILY
    if any(token in (name or "").lower() for token in ("alpha", "beta", "daily")):
        return TYPE_DAILY
    if minor_of(version or "") in LTS_MINORS:
        return TYPE_LTS
    return TYPE_STABLE


def type_of_installed(entry) -> str:
    """Tipo de un ``InstalledBuild`` (envoltorio de ``type_from_marker``)."""
    return type_from_marker(
        getattr(entry, "branch", ""),
        getattr(entry, "version", ""),
        getattr(entry, "name", ""),
        getattr(entry, "risk", ""),
        getattr(entry, "fork", ""),
    )


def normalize_path(path) -> str:
    """Forma canónica de una ruta, para comparar dos carpetas.

    ``expanduser`` porque el usuario puede escribir ``~/Blender``, y
    ``normcase`` porque en Windows ``C:\\Blender`` y ``c:\\blender`` son la
    misma carpeta. **No** se usa ``resolve()`` aquí a propósito: resolver toca
    el disco y falla con enlaces rotos o unidades desconectadas, y esto tiene
    que funcionar sobre una configuración guardada aunque el disco no esté
    puesto. Para deduplicar lo que ya existe en disco está ``scan_folders``.
    """
    if not path:
        return ""
    return os.path.normcase(str(Path(str(path)).expanduser()))


def owner_of(folders, build_type: str) -> "Folder | None":
    """Carpeta que se queda con ese tipo, o ``None`` si no lo tiene nadie.

    Cada tipo tiene **un único dueño**: la interfaz, al marcar una casilla, se
    la quita a la carpeta que la tuviera. Por eso aquí basta con devolver la
    primera que encaje, y por eso el destino de una descarga nunca es ambiguo.
    """
    for folder in folders or ():
        if folder.takes(build_type):
            return folder
    return None


def resolve_destination(folders, build_type: str) -> str:
    """Carpeta donde se instala ese tipo, o ``""`` si nadie lo recibe.

    Un solo escalón, y es deliberado. Con casillas, "quién recibe esto" es un
    dato que el usuario ha puesto a mano, no algo que haya que adivinar: si
    nadie ha marcado *Diarias*, lo correcto es decirlo y que lo arregle, no
    colar la diaria en la primera carpeta que pille. La cadena de reservas que
    haría falta con un desplegable (rol exacto → comodín → la primera que
    valga) es justo lo que esto se ahorra.
    """
    owner = owner_of(folders, build_type)
    return owner.path if owner is not None else ""


def orphan_types(folders, types=None) -> list:
    """Tipos que no recibe ninguna carpeta, en el orden de ``types``.

    La interfaz los avisa **antes** de que el usuario intente descargar uno:
    es la contrapartida de no tener cadena de reservas. ``types`` permite
    mirar solo los tipos activos (los de los forks apagados no deben salir
    como huérfanos: nadie puede descargarlos todavía).
    """
    candidate = BUILD_TYPES if types is None else types
    return [build_type for build_type in candidate
            if owner_of(folders, build_type) is None]


def active_types(forks=()) -> tuple:
    """Tipos de carpeta que tienen sentido con esos forks activados.

    Los de Blender están siempre; los de un fork solo cuando el usuario lo
    enciende en Ajustes. La usa la biblioteca de carpetas para no ofrecer una
    casilla de algo que no se puede descargar.
    """
    enabled = set(forks or ())
    return tuple(build_type for build_type in BUILD_TYPES
                 if build_type not in FORK_TYPES.values()
                 or build_type in {FORK_TYPES[f] for f in enabled
                                   if f in FORK_TYPES})
