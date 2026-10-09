"""Traducciones de la interfaz.

Las **claves son las cadenas en inglés**: si falta una traducción se enseña el
texto original en vez de romperse. El idioma se detecta de las variables de
entorno del sistema (LANG, LC_ALL...) salvo que el usuario lo fije en Ajustes.

Los textos viven en ``src/locale/<idioma>.json``, no aquí: con dos idiomas el
diccionario en Python ya pasaba de mil líneas, y en JSON se puede mandar una
traducción sin tocar código. Se cargan una vez, al primer uso.
"""

import json
import locale
import os

from paths import RESOURCE_DIR

SUPPORTED = ("en", "es", "zh", "ru", "ja", "pt_BR")
DEFAULT = "en"

# Carpeta con los ``<idioma>.json``. Va por ``RESOURCE_DIR`` para que también
# la encuentre el binario empaquetado (PyInstaller la copia ahí).
LOCALE_DIR = RESOURCE_DIR / "locale"

# Caché por idioma: leer el JSON en cada ``tr()`` sería absurdo.
_TRANSLATIONS = {}


def _load(language: str) -> dict:
    """Diccionario de ese idioma, del disco y una sola vez.

    El inglés no tiene fichero: es la clave. Si el JSON falta o está roto se
    devuelve vacío y la interfaz sale en inglés, que es preferible a no
    arrancar.
    """
    if language in _TRANSLATIONS:
        return _TRANSLATIONS[language]
    table = {}
    if language != DEFAULT:
        try:
            table = json.loads((LOCALE_DIR / f"{language}.json")
                               .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            table = {}
    _TRANSLATIONS[language] = table
    return table


# Idioma en uso. Arranca en inglés (las claves) hasta que ``set_language`` lo
# fije con lo que diga el sistema o los ajustes.
_current = DEFAULT


# Regiones donde el chino se escribe en caracteres **tradicionales**. Nuestro
# ``zh`` es chino simplificado (el de China continental), y enseñárselo a quien
# pidió tradicional es como dar un texto en otra ortografía: se entiende a
# medias y se nota que no es lo suyo. Mientras no haya un ``zh_TW``, esas
# regiones se quedan en inglés, que es lo que hace también Blender cuando le
# falta un idioma.
_TRADITIONAL_CHINESE_REGIONS = {"TW", "HK", "MO"}


def _supported_code(value: str) -> str | None:
    """Código de ``SUPPORTED`` para un locale (``pt_BR.UTF-8``), o ``None``.

    Primero se prueba el idioma **con su región** (``pt_BR``) y luego solo el
    idioma (``es`` para ``es_MX``). En ese orden porque hay idiomas que solo
    tenemos en una variante regional: ``pt_BR.UTF-8`` buscaba ``pt``, que no
    existe, y la interfaz salía en inglés a un usuario brasileño.

    Acepta las formas que se ven por ahí: con codificación (``.UTF-8``), con
    modificador (``@euro``), con guion en vez de guion bajo (``pt-BR``, la de
    macOS) y con escritura (``zh_Hant_TW``).
    """
    tag = value.split(":")[0].split(".")[0].split("@")[0].replace("-", "_")
    parts = [part for part in tag.split("_") if part]
    if not parts:
        return None
    language = parts[0].lower()
    region = parts[-1].upper() if len(parts) > 1 else ""
    by_lower = {code.lower(): code for code in SUPPORTED}
    if region and f"{language}_{region}".lower() in by_lower:
        return by_lower[f"{language}_{region}".lower()]
    traditional = language == "zh" and (
        region in _TRADITIONAL_CHINESE_REGIONS
        or any(part.lower() == "hant" for part in parts))
    if traditional:
        return None
    return by_lower.get(language)


def detect_language() -> str:
    """Idioma según las variables de entorno (LC_ALL, LC_MESSAGES, LANG).

    Se miran en el orden en que manda POSIX (la primera definida gana) y, si
    ninguna da un idioma que tengamos, el locale del sistema (en Windows no hay
    ``LANG``). Si nada encaja, inglés.
    """
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            code = _supported_code(value)
            if code:
                return code
    try:
        detected = locale.getlocale()[0]
    except (ValueError, TypeError):
        detected = None
    if detected:
        code = _supported_code(detected)
        if code:
            return code
    return DEFAULT


def set_language(language) -> None:
    """Fija el idioma de la interfaz: 'auto' o un código de ``SUPPORTED``."""
    global _current
    if language in (None, "", "auto"):
        _current = detect_language()
    else:
        _current = language if language in SUPPORTED else DEFAULT


def get_language() -> str:
    """Idioma que se está usando ahora mismo."""
    return _current


def tr(text, **kwargs) -> str:
    """Traduce una cadena (las claves van en inglés).

    Si falta la traducción devuelve la clave tal cual, para que la
    interfaz no se rompa por un texto sin traducir.
    """
    translated = _load(_current).get(text, text)
    if kwargs:
        try:
            return translated.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            # Una traducción con una llave suelta ("Versión {") no puede tumbar
            # la construcción de la interfaz: devolvemos el texto sin sustituir.
            return translated
    return translated
