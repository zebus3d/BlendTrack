"""Genera los iconos de empaquetado a partir del PNG de la app.

Uso:  .venv/bin/python packaging/make_icons.py

Escribe en ``packaging/icons/``:

* ``blendtrack.ico``  -> icono del .exe de Windows, a varias resoluciones.
* ``blendtrack.icns`` -> icono del bundle .app de macOS, a varias.

Los ficheros generados se dejan en el repo, para que PyInstaller no necesite Qt
antes de empaquetar. Vuelve a ejecutar esto si cambias
``src/assets/images/app_icon.png``.

El AppImage no usa estos ficheros: appimagetool coge el ``.desktop`` con
``Icon=blendtrack``, el ``blendtrack.png`` del AppDir y el ``.DirIcon``
(lo que ensena el gestor de ficheros), todo desde ``build_appimage.sh``.

Notas de formato (comprobadas, no de oídas):

* Qt sabe escribir .ico y .icns, pero **un solo tamaño por fichero**: al
  encadenar ``write()`` se queda con el primer tamaño y el resto se pierde. Por
  eso aquí se montan los contenedores a mano.
* El .ico se monta concatenando los .ico de un solo tamaño que escribe Qt, que
  llevan los píxeles en formato BMP/DIB (el más compatible: Windows XP en
  adelante). Se podría meter PNG (Vista+), pero BMP no depende de nada.
* El .icns lleva PNG dentro, con el código que le toca a cada tamaño
  (``icp4``=16, ``icp5``=32, ``icp6``=64, ``ic07``=128, ``ic08``=256,
  ``ic09``=512), que es lo que espera macOS.
"""

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src" / "assets" / "images" / "app_icon.png"
OUT = Path(__file__).resolve().parent / "icons"

ICO_SIZES = (16, 32, 48, 64, 128, 256)
ICNS_TYPES = {16: b"icp4", 32: b"icp5", 64: b"icp6",
              128: b"ic07", 256: b"ic08", 512: b"ic09"}
ICNS_SIZES = tuple(sorted(ICNS_TYPES))


def _scaled(image, size):
    from PySide6.QtCore import Qt

    return image.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _png_bytes(image) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _one_ico(image, size) -> bytes:
    """Un .ico de un solo tamaño, escrito por Qt (píxeles en BMP/DIB)."""
    from PySide6.QtCore import QTemporaryFile

    temporary = QTemporaryFile(str(OUT / ".tmp-XXXXXX.ico"))
    temporary.open()
    path = temporary.fileName()
    temporary.close()
    from PySide6.QtGui import QImageWriter

    writer = QImageWriter(path, b"ico")
    if not writer.write(_scaled(image, size)):
        raise RuntimeError(f"no se pudo escribir el ico: {writer.errorString()}")
    data = Path(path).read_bytes()
    Path(path).unlink()
    return data


def build_ico(image) -> bytes:
    """Une los .ico de cada tamaño en uno solo."""
    payloads = []
    for size in ICO_SIZES:
        single = _one_ico(image, size)
        count = struct.unpack_from("<H", single, 4)[0]
        if count != 1:
            raise RuntimeError(f"Qt escribio {count} imagenes en un .ico")
        # Los 8 primeros bytes del directorio son w,h,colores,reservado,planos
        # y bpp; los 8 últimos son tamaño y offset, que hay que recalcular
        # porque van a cambiar al unir los ficheros.
        directory = single[6:14]
        payloads.append((directory, single[22:]))

    header = struct.pack("<HHH", 0, 1, len(payloads))
    offset = 6 + 16 * len(payloads)
    directories = b""
    body = b""
    for directory, payload in payloads:
        directories += directory + struct.pack("<II", len(payload), offset)
        body += payload
        offset += len(payload)
    return header + directories + body


def build_icns(image) -> bytes:
    """Contenedor .icns con un PNG por tamaño y su código correspondiente."""
    body = b""
    for size in ICNS_SIZES:
        payload = _png_bytes(_scaled(image, size))
        body += ICNS_TYPES[size] + struct.pack(">I", 8 + len(payload)) + payload
    return b"icns" + struct.pack(">I", 8 + len(body)) + body


def check_ico(data: bytes) -> str:
    count = struct.unpack_from("<H", data, 4)[0]
    if count != len(ICO_SIZES):
        raise RuntimeError(f"el ico tiene {count} entradas, esperaba {len(ICO_SIZES)}")
    seen = []
    for index in range(count):
        width, height, _c, _r, _p, bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index)
        if offset + size > len(data):
            raise RuntimeError("entrada del ico fuera del fichero")
        # El payload de una entrada BMP empieza por un BITMAPINFOHEADER (40).
        if struct.unpack_from("<I", data, offset)[0] != 40:
            raise RuntimeError("el payload del ico no es un DIB")
        seen.append(f"{width or 256}x{height or 256}@{bpp}bpp")
    return f"{count} entradas: " + ", ".join(seen)


def check_icns(data: bytes) -> str:
    if data[:4] != b"icns":
        raise RuntimeError("falta la magia 'icns'")
    if struct.unpack_from(">I", data, 4)[0] != len(data):
        raise RuntimeError("la longitud del .icns no cuadra")
    offset, seen = 8, []
    while offset < len(data):
        code = data[offset:offset + 4]
        length = struct.unpack_from(">I", data, offset + 4)[0]
        if length < 8 or offset + length > len(data):
            raise RuntimeError(f"entrada {code!r} mal formada")
        if data[offset + 8:offset + 16] == b"\x89PNG\r\n\x1a\n":
            seen.append(f"{code.decode()} (PNG)")
        offset += length
    return f"{len(seen)} entradas: " + ", ".join(seen)


def main() -> int:
    from PySide6.QtGui import QImage

    image = QImage(str(SOURCE))
    if image.isNull():
        print(f"No se pudo leer {SOURCE}", file=sys.stderr)
        return 1
    print(f"origen: {SOURCE.relative_to(ROOT)} ({image.width()}x{image.height()})")

    OUT.mkdir(parents=True, exist_ok=True)

    ico = build_ico(image)
    (OUT / "blendtrack.ico").write_bytes(ico)
    print(f"blendtrack.ico: {check_ico(ico)}")

    icns = build_icns(image)
    (OUT / "blendtrack.icns").write_bytes(icns)
    print(f"blendtrack.icns: {check_icns(icns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
