"""La versión exacta que lleva dentro un Blender de Windows.

Las builds que descarga la app traen el número en el nombre de la carpeta
(``blender-5.2.3-windows-x64``) y de ahí sale todo. Pero el **instalador
oficial** de blender.org crea ``C:\\Program Files\\Blender Foundation\\Blender
5.2``: sin el parche. Una 5.2.3 instalada con el instalador se leía como
``5.2``, así que la app le ofrecía "actualizar" justo a la versión que ya tenía
(reportado por una usuaria con su 5.2.3 LTS).

El número de verdad está en el recurso de versión del ``blender.exe``, que es
exactamente el que enseña el Explorador en *Propiedades → Detalles*. Se lee de
ahí con ``seek``: unos pocos kilobytes, sin cargar el fichero entero (pesa
cientos de megabytes) y sin ejecutar nada (``blender --version`` tardaría un
segundo por cada versión, y el escaneo corre en el hilo de la interfaz).

El formato del PE está documentado por Microsoft: cabecera DOS (``MZ``) que
apunta a la de PE, tabla de secciones, y dentro de la sección de recursos un
árbol (tipo → nombre → idioma) que acaba en el bloque de datos. Aquí solo se
busca el tipo 16 (``RT_VERSION``) y, dentro, la firma de ``VS_FIXEDFILEINFO``,
que lleva los cuatro números.

En macOS y Linux no hace falta: el ``.tar.xz`` y el ``.zip`` siempre traen el
parche en el nombre, y las versiones que instala la app también. Solo el
instalador de Windows lo pierde.
"""

import struct

# 0xFEEF04BD en little-endian: la firma que abre ``VS_FIXEDFILEINFO``. Buscarla
# es más corto (y más difícil de romper) que recorrer la estructura completa
# del recurso, que además lleva los textos de la pestaña Detalles.
_FIXED_SIGNATURE = b"\xbd\x04\xef\xfe"

# Tipo de recurso 16: el bloque de versión.
_RT_VERSION = 16

# El bit alto de un destino en el árbol de recursos dice "esto es otro
# directorio"; sin él, apunta a un bloque de datos.
_SUBDIRECTORY = 0x80000000


def file_version(path) -> str:
    """Versión que declara un ``.exe`` (``"5.2.3"``), o ``""`` si no se puede.

    Nunca lanza: un ejecutable raro, un fichero cortado o una ruta sin permiso
    son simplemente "aquí no hay número que leer", y el escaneo se queda con lo
    que dijera el nombre de la carpeta.
    """
    try:
        with open(path, "rb") as handle:
            return _file_version(handle)
    except (OSError, struct.error):
        return ""


def _file_version(handle) -> str:
    """El recorrido del PE, con el fichero abierto."""
    dos = handle.read(64)
    if len(dos) < 64 or dos[:2] != b"MZ":
        return ""
    # La cabecera DOS guarda en 0x3C dónde empieza la de PE.
    handle.seek(_u32(dos, 0x3C))
    header = handle.read(24)
    if len(header) < 24 or header[:4] != b"PE\0\0":
        return ""
    sections = _u16(header, 6)
    optional_size = _u16(header, 20)
    optional = handle.read(optional_size)
    if len(optional) < 2:
        return ""
    # La tabla de directorios de datos empieza después de la cabecera estándar
    # y los campos propios de cada formato: 96 en PE32 y 112 en PE32+.
    magic = _u16(optional, 0)
    if magic == 0x10B:
        directories = 96
    elif magic == 0x20B:
        directories = 112
    else:
        return ""
    resource_entry = directories + 2 * 8  # el recurso es la entrada número 2
    if len(optional) < resource_entry + 8:
        return ""
    resource_rva = _u32(optional, resource_entry)
    resource_size = _u32(optional, resource_entry + 4)
    if not resource_rva or not resource_size:
        return ""

    table = handle.read(40 * sections)
    resource_offset = _offset_of(table, resource_rva)
    if resource_offset is None:
        return ""

    resources = _ResourceReader(handle, resource_offset)
    directory = resources.entry(0, _RT_VERSION)
    if directory is None or not directory & _SUBDIRECTORY:
        return ""
    name = resources.entry(directory & ~_SUBDIRECTORY)
    if name is None or not name & _SUBDIRECTORY:
        return ""
    # El último escalón no es otro directorio, sino el bloque de datos.
    data = resources.entry(name & ~_SUBDIRECTORY)
    if data is None or data & _SUBDIRECTORY:
        return ""
    block = resources.read(data, 16)
    if len(block) < 16:
        return ""
    version_rva = _u32(block, 0)
    version_size = _u32(block, 4)
    data_offset = _offset_of(table, version_rva)
    if data_offset is None or not version_size:
        return ""
    handle.seek(data_offset)
    return _fixed_version(handle.read(min(version_size, 4096)))


class _ResourceReader:
    """Las lecturas del árbol de recursos, relativas al principio de la sección.

    Se pide solo lo que hace falta (cabeceras de 16 bytes y entradas de 8) en
    vez de cargar la sección entera: en un ``.exe`` grande los iconos ocupan
    cientos de kilobytes y aquí se leen unos pocos. Cargarla con un tope fijo
    era además un fallo esperando a pasar: con muchos recursos el árbol pasa
    del tope y la versión se quedaba en blanco.
    """

    def __init__(self, handle, offset: int):
        self.handle = handle
        self.offset = offset

    def read(self, relative: int, size: int) -> bytes:
        if relative < 0 or size <= 0:
            return b""
        self.handle.seek(self.offset + relative)
        return self.handle.read(size)

    def entry(self, directory: int, wanted: int | None = None):
        """Primera entrada de un directorio de recursos, o la del id ``wanted``.

        Cada directorio es una cabecera de 16 bytes (con cuántas entradas tienen
        nombre y cuántas id) y luego entradas de 8: el identificador y el
        destino.
        """
        header = self.read(directory, 16)
        if len(header) < 16:
            return None
        count = _u16(header, 12) + _u16(header, 14)
        if count > 4096:
            # Un fichero corrupto no puede hacernos leer media sección.
            return None
        entries = self.read(directory + 16, 8 * count)
        for index in range(count):
            base = index * 8
            if base + 8 > len(entries):
                return None
            identifier = _u32(entries, base)
            if wanted is not None:
                if identifier == wanted:
                    return _u32(entries, base + 4)
                continue
            if identifier & _SUBDIRECTORY:
                # Un recurso con nombre en vez de con id: no es el que buscamos.
                continue
            return _u32(entries, base + 4)
        return None


def _offset_of(table: bytes, rva: int):
    """Convierte una RVA en un desplazamiento del fichero.

    Las direcciones del PE son relativas a cómo se cargaría en memoria; la
    tabla de secciones dice en qué punto del fichero está cada una.
    """
    for index in range(0, len(table) - 39, 40):
        virtual_size = _u32(table, index + 8)
        virtual_address = _u32(table, index + 12)
        raw_size = _u32(table, index + 16)
        raw_pointer = _u32(table, index + 20)
        # ``VirtualSize`` y ``SizeOfRawData`` no siempre coinciden (el último
        # trozo de una sección se rellena): vale la más grande de las dos.
        span = max(virtual_size, raw_size)
        if virtual_address <= rva < virtual_address + span:
            return raw_pointer + (rva - virtual_address)
    return None


def _fixed_version(info: bytes) -> str:
    """Los números de ``VS_FIXEDFILEINFO``, como ``5.2.3``.

    Son dos enteros de 32 bits partidos en dos mitades cada uno. Blender deja
    la cuarta siempre en cero (``BLEN_VER_RC_4=0`` en su CMake), así que se
    devuelven los tres primeros: el tercero es el parche de verdad y hay que
    conservarlo aunque valga cero (``5.2.0``), o la versión no emparejaría con
    la de la tienda.
    """
    index = info.find(_FIXED_SIGNATURE)
    if index < 0 or index + 16 > len(info):
        return ""
    high = _u32(info, index + 8)
    low = _u32(info, index + 12)
    parts = (high >> 16, high & 0xFFFF, low >> 16)
    if not any(parts):
        return ""
    return ".".join(str(part) for part in parts)


def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]
