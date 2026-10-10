"""Las imágenes que pinta la galería: halos, reflejos, fondo y puntero.

Todo lo de aquí **fabrica imágenes** (``QPixmap``/``QImage``/``QCursor``) y no
sabe nada de cartas ni de movimiento: eso es ``gallery.py``. Se separó cuando
la galería se acercó al tope de 1000 líneas, por responsabilidad: la vista
coloca y mueve; esto dibuja una vez lo que la vista pega en cada fotograma.

Casi todo se hace **una vez y se guarda**: desenfocar, desaturar o calcular el
fondo con *dithering* cuesta mucho más que pintar el resultado, y la galería
repinta a 60 fotogramas por segundo mientras se mueve.
"""

import math
import random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QCursor, QImage, QLinearGradient,
                           QPainter, QPainterPath, QPixmap, QRadialGradient)

# Radio de las esquinas: el de ``QFrame#Card`` en el QSS. La captura de un
# widget es rectangular y sin esto las imágenes salían con picos.
CARD_RADIUS = 12

# Cuánto se extiende el halo de la carta central fuera de ella.
GLOW_PAD = 70

# Pasos del ciclo de forma del puntero láser (a ``LASER_MS`` cada uno, dura
# ~1,7 s: lento, para que oscile y no tiemble). El grano, en cambio, es nuevo
# en cada fotograma.
LASER_FRAMES = 20
# Motas del moteado del láser en cada fotograma.
LASER_GRAIN = 28


def rounded(pix: QPixmap, radius: float) -> QPixmap:
    """La captura con las esquinas redondeadas, como se ve la tarjeta."""
    dpr = pix.devicePixelRatio()
    out = QPixmap(pix.size())
    out.setDevicePixelRatio(dpr)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, pix.width() / dpr, pix.height() / dpr),
                        radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pix)
    painter.end()
    return out


def reflection(pix: QPixmap) -> QPixmap:
    """El reflejo en el suelo: la tarjeta invertida, apagándose hacia abajo."""
    image = pix.toImage()
    # ``flipped`` llegó en Qt 6.9 y ``mirrored`` está obsoleto desde entonces.
    image = (image.flipped(Qt.Orientation.Vertical) if hasattr(image, "flipped")
             else image.mirrored(False, True))
    image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    fade = QLinearGradient(0, 0, 0, image.height() * 0.45)
    fade.setColorAt(0.0, QColor(0, 0, 0, 90))
    fade.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.fillRect(image.rect(), fade)
    painter.end()
    out = QPixmap.fromImage(image)
    out.setDevicePixelRatio(pix.devicePixelRatio())
    return out


def greyscale(pix: QPixmap) -> QPixmap:
    """La captura en escala de grises, conservando sus esquinas redondeadas.

    ``Format_Grayscale8`` no tiene canal alfa: las esquinas transparentes
    saldrían negras. Por eso, tras convertir, se recorta con la propia captura
    (``DestinationIn`` se queda con el alfa del original).
    """
    grey = pix.toImage().convertToFormat(QImage.Format_Grayscale8)
    grey = grey.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(grey)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, pix.toImage())
    painter.end()
    out = QPixmap.fromImage(grey)
    out.setDevicePixelRatio(pix.devicePixelRatio())
    return out


_NOISE = []


def noise_tile() -> QPixmap:
    """Un mosaico de grano finísimo para tapar el *banding* de los degradados.

    El foco del fondo baja de alfa 55 a 0 sobre un gris casi negro: en 8 bits
    son muy pocos escalones de color repartidos en cientos de píxeles, y cada
    escalón se ve como un anillo. ``QPainter`` no hace *dithering*, así que se
    hace a mano: puntos blancos y negros con alfa de 0 a 5 (±2 niveles), que el
    ojo no ve como grano pero rompen los bordes de los escalones. Se genera una
    vez (64×64 son 4096 píxeles) y se pinta en mosaico.
    """
    if not _NOISE:
        rng = random.Random(7)
        image = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
        for y in range(64):
            for x in range(64):
                value = 255 if rng.random() < 0.5 else 0
                alpha = rng.randint(0, 5)
                pre = value * alpha // 255
                image.setPixel(x, y, (alpha << 24) | (pre << 16) | (pre << 8) | pre)
        _NOISE.append(QPixmap.fromImage(image))
    return _NOISE[0]


def glow(width: int, height: int, color: str) -> QPixmap:
    """El halo de la carta central: su silueta en color, muy desenfocada.

    Mismo desenfoque barato que el logo del fondo (encoger y volver a ampliar
    suavizando), hecho una vez por tamaño y color. Se pinta **detrás** de la
    carta y por encima de las vecinas: la luz se derrama sobre ellas, que es lo
    que hace que la central parezca encendida.
    """
    pad = GLOW_PAD
    image = QImage(width + 2 * pad, height + 2 * pad,
                   QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(pad - 10, pad - 10, width + 20, height + 20),
                            CARD_RADIUS + 10, CARD_RADIUS + 10)
    painter.end()
    blurred = soft_blur(image, 4)
    # El halo es un degradado ancho y tenue: el mismo grano que el fondo le
    # quita los escalones (``SourceAtop`` solo pinta donde ya hay halo).
    painter = QPainter(blurred)
    painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
    painter.fillRect(blurred.rect(), QBrush(noise_tile()))
    painter.end()
    return QPixmap.fromImage(blurred)


def soft_blur(image: QImage, steps: int) -> QImage:
    """Desenfoque suave y barato: encoger a la mitad ``steps`` veces y volver.

    De un solo salto (encoger ×16 y ampliar ×16) la ampliación bilineal deja
    bloques, que se notaban en las esquinas redondeadas del halo. Encadenando
    mitades y dobles, cada paso suaviza el anterior y el resultado se parece a
    un desenfoque gaussiano.
    """
    sizes = []
    current = image
    for _ in range(steps):
        sizes.append(current.size())
        current = current.scaled(max(1, current.width() // 2),
                                 max(1, current.height() // 2),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    for size in reversed(sizes):
        current = current.scaled(size, Qt.IgnoreAspectRatio,
                                 Qt.SmoothTransformation)
    return current.convertToFormat(QImage.Format_ARGB32_Premultiplied)


# Umbrales de un patrón Bayer 4×4 (0..15): el *ordered dithering* clásico.
_BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))

# El foco de luz del fondo: alfa máximo del acento en el centro y su radio
# (fracción del ancho).
FOCUS_ALPHA = 80 / 255
FOCUS_RADIUS = 0.50

# La viñeta: cuánto se oscurece el fondo en las esquinas (0 = nada) y desde
# qué distancia al centro empieza (0 = centro, 1 = esquina). Junto con el foco,
# da el contraste: claro detrás de la carta y algo más oscuro hacia los bordes.
# Suave a propósito (0,35): con 0,50 las esquinas quedaban demasiado negras.
VIGNETTE = 0.35
VIGNETTE_FROM = 0.30


def dithered_focus(width: int, height: int, base: str, accent: str) -> QImage:
    """El fondo con su foco de luz, calculado píxel a píxel y con *dithering*.

    Pintado con ``QRadialGradient`` salían **anillos**: el foco va de alfa 55 a
    0 sobre un gris casi negro, así que entre el centro y el borde solo caben
    ~32 niveles de color en 8 bits, y cada salto de nivel (cada 12 px) se ve.
    Qt no hace *dithering*: ni al pintar ni al convertir de 16 a 8 bits (se
    probó con ``DiffuseDither``/``OrderedDither`` y sumando un patrón en 16
    bits: lo redondea todo a 8 antes). Así que aquí se calcula el color de cada
    píxel en coma flotante y se redondea con un umbral Bayer distinto según su
    posición: los escalones se convierten en una trama que el ojo promedia.

    Encima va una viñeta que oscurece los bordes, con el mismo redondeo: es
    otro degradado sobre gris oscuro y tendría los mismos anillos. Tarda unas
    décimas, por eso se llama en un hilo (``QImage`` se puede crear fuera del
    hilo de la interfaz).
    """
    b = QColor(base)
    a = QColor(accent)
    base_rgb = (b.red(), b.green(), b.blue())
    delta = (a.red() - b.red(), a.green() - b.green(), a.blue() - b.blue())
    cx, cy = width / 2, height * 0.46
    radius = max(1.0, width * FOCUS_RADIUS)
    # El fondo es simétrico de izquierda a derecha (foco y viñeta centrados en
    # x): se calcula media fila y la otra mitad se copia en espejo. Lo que solo
    # depende de x se calcula una vez por columna, no en cada píxel.
    half_w, half_h = width / 2, height / 2
    columns = (width + 1) // 2
    dx2 = [(x + 0.5 - cx) ** 2 for x in range(columns)]
    vx2 = [((x + 0.5 - half_w) / half_w) ** 2 for x in range(columns)]
    span = 1 - VIGNETTE_FROM
    rows = []
    for y in range(height):
        bayer = _BAYER[y % 4]
        dy2 = (y + 0.5 - cy) ** 2
        vy2 = ((y + 0.5 - half_h) / half_h) ** 2
        blue, green, red = [], [], []
        for x in range(columns):
            k = FOCUS_ALPHA * (1.0 - math.sqrt(dx2[x] + dy2) / radius)
            if k < 0.0:
                k = 0.0
            # Distancia elíptica a la esquina (1 en las esquinas), suavizada.
            e = (math.sqrt(vx2[x] + vy2) * 0.7071 - VIGNETTE_FROM) / span
            shade = 1.0 - VIGNETTE * (e * e * (3 - 2 * e)) if e > 0 else 1.0
            threshold = (bayer[x % 4] + 0.5) / 16
            blue.append(min(255, int((base_rgb[2] + delta[2] * k) * shade + threshold)))
            green.append(min(255, int((base_rgb[1] + delta[1] * k) * shade + threshold)))
            red.append(min(255, int((base_rgb[0] + delta[0] * k) * shade + threshold)))
        mirror = width - columns
        row = bytearray(width * 4)
        # Formato RGB32 en memoria (little endian): azul, verde, rojo, alfa.
        # La asignación con salto (``row[0::4] = ...``) la hace C, no Python.
        row[0::4] = bytes(blue + blue[:mirror][::-1])
        row[1::4] = bytes(green + green[:mirror][::-1])
        row[2::4] = bytes(red + red[:mirror][::-1])
        row[3::4] = b"\xff" * width
        rows.append(bytes(row))
    data = b"".join(rows)
    image = QImage(data, width, height, width * 4, QImage.Format_RGB32)
    return image.copy()   # ``copy`` para que no dependa del buffer de ``data``


def laser_cursor(phase: float = 0.0, seed: int = 0, scale: float = 1.0,
                 squash: float = 1.0, flatten: float = 1.0) -> QCursor:
    """El puntero de la galería: un punto láser rojo, pequeño y muy brillante.

    Hecho como se ve un láser de verdad en una foto: un núcleo casi blanco y
    **elíptico** (quemado de tanta luz), una floración roja muy saturada y un
    resplandor ancho y tenue. ``phase`` (0..1) es el punto de un ciclo suave en
    el que la elipse gira despacio, se estira un poco y la luz respira: los
    fotogramas seguidos se pasan de uno a otro sin saltos (los fabrica la
    galería al vuelo, ``gallery_controls._laser_now``).
    ``seed`` pone un grano tenue, apenas visible, que le da vida.

    ``scale`` y ``squash`` son la profundidad: sobre una carta cercana el punto
    se ve más grande, sobre una lejana más pequeño, y sobre una girada se
    comprime en horizontal (el escorzo), como un láser de verdad. ``flatten``
    lo comprime en vertical: es el suelo, un plano que se ve de canto.

    Se pinta al triple de resolución (``devicePixelRatio`` 3) para que en una
    pantalla HiDPI no salga borroso; el punto caliente es el centro.
    """
    rng = random.Random(seed)
    turn = 2 * math.pi * phase
    angle = 25 + 18 * math.sin(turn)                 # grados
    stretch = 1.18 + 0.07 * math.sin(2 * turn + 0.7)  # elipse del núcleo
    bloom = 1.0 + 0.05 * math.sin(turn + 1.9)         # tamaño de la floración
    breath = 0.94 + 0.06 * math.sin(3 * turn + 0.4)   # intensidad
    # 16 px: unos tres cuartos de lo que medía al principio (22 era grande y
    # 11 se quedaba corto). Núcleo, floración, resplandor y grano van en
    # proporción, para que siga pareciendo luz.
    size, ratio = 16, 3
    # El dibujo se hace siempre en las mismas unidades (16) y se escala entero:
    # el lienzo crece con ``scale`` para que el resplandor no se corte.
    canvas = max(6, round(size * scale))
    image = QImage(canvas * ratio, canvas * ratio, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    def origin():
        painter.resetTransform()
        painter.scale(ratio, ratio)
        painter.translate(canvas / 2, canvas / 2)
        painter.scale(canvas / size * squash, canvas / size * flatten)

    origin()
    painter.setPen(Qt.NoPen)
    centre = QPointF(0, 0)
    # El resplandor ancho, casi redondo.
    halo = QRadialGradient(centre, size / 2 * bloom)
    halo.setColorAt(0.0, QColor(255, 40, 30, round(150 * breath)))
    halo.setColorAt(0.35, QColor(255, 10, 10, round(60 * breath)))
    halo.setColorAt(1.0, QColor(255, 0, 0, 0))
    painter.setBrush(halo)
    painter.drawEllipse(centre, size / 2, size / 2)
    # La floración, en elipse y girada.
    painter.rotate(angle)
    painter.scale(stretch, 1.0)
    flare = QRadialGradient(centre, 3.8 * bloom)
    flare.setColorAt(0.0, QColor(255, 70, 60, round(255 * breath)))
    flare.setColorAt(0.55, QColor(255, 25, 30, round(190 * breath)))
    flare.setColorAt(1.0, QColor(255, 0, 20, 0))
    painter.setBrush(flare)
    painter.drawEllipse(centre, 3.8 * bloom, 3.8 * bloom)
    origin()
    # El grano (el *speckle*): muchas motas finas por todo el resplandor, más
    # densas cerca del núcleo. Cada fotograma las pone en otro sitio (``seed``),
    # así que al pasar los fotogramas el moteado se mueve, como el de un láser
    # de verdad. Con 7 motas tenues no se notaba; con más de ~40, o muy
    # marcadas, ya se lee como chispas en vez de como la textura de la luz.
    for _ in range(LASER_GRAIN):
        a = rng.random() * 2 * math.pi
        # Pegadas a su luz: hasta el borde de la floración, no más allá (sueltas
        # por fuera parecían polvo alrededor del punto, no parte de él).
        r = 1.0 + (rng.random() ** 1.8) * 3.6
        # Todas rojizas, cada una con su luz (de rojo oscuro a rosado claro):
        # las blancas destacaban como chispas sueltas y no como parte del haz.
        grain = QColor.fromHsvF((0.985 + 0.03 * rng.random()) % 1.0,
                                0.45 + 0.5 * rng.random(),
                                0.45 + 0.55 * rng.random())
        grain.setAlphaF(0.25 + 0.35 * rng.random())
        painter.setBrush(grain)
        dot = 0.28 + 0.2 * rng.random()
        painter.drawEllipse(QPointF(r * math.cos(a), r * math.sin(a)), dot, dot)
    # El núcleo blanco, lo último: encima del grano, que si no lo manchaba.
    # Con el mismo giro y estiramiento que la floración.
    painter.rotate(angle)
    painter.scale(stretch, 1.0)
    core = QRadialGradient(centre, 1.75)
    core.setColorAt(0.0, QColor(255, 255, 255))
    core.setColorAt(0.55, QColor(255, 230, 235))
    core.setColorAt(1.0, QColor(255, 120, 130, 0))
    painter.setBrush(core)
    painter.drawEllipse(centre, 1.75, 1.75)
    painter.end()
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return QCursor(pixmap, canvas // 2, canvas // 2)


# El realce del arte de las cartas: +6 % de saturación y +3 % de contraste.
# Lo justo para que la ilustración destaque sin parecer otra (con un 10 % ya
# se notaba de más).
ART_SATURATION = 0.06
ART_CONTRAST = 0.03


def _composed(base: QImage, layer: QImage, mode, opacity: float = 1.0) -> QImage:
    """``base`` con ``layer`` encima en el modo de composición ``mode``."""
    out = base.copy()
    painter = QPainter(out)
    painter.setOpacity(opacity)
    painter.setCompositionMode(mode)
    painter.drawImage(0, 0, layer)
    painter.end()
    return out


def enhance(pix: QPixmap, saturation: float = ART_SATURATION,
            contrast: float = ART_CONTRAST) -> QPixmap:
    """La imagen con un poco más de saturación y de contraste.

    Recorrer en Python el medio millón de píxeles de un splash costaría casi
    un segundo por carta, así que se hace con lo que Qt hace en C.

    **Saturación**: ``color + s·(color − gris)``. Qt no tiene un modo de
    "restar", y el ``Difference`` con opacidad parcial usa una fórmula con un
    ``min()`` que, en los canales por debajo del gris, los acercaba al gris en
    vez de alejarlos (medido: la saturación casi no subía y el contraste
    bajaba). Así que la diferencia se parte en sus dos signos, con capas
    opacas, donde ``Difference`` sí es ``|a − b|``:

    - lo que el canal pasa del gris: ``max(color, gris) − gris``;
    - lo que se queda por debajo: ``gris − min(color, gris)``;
    - y se suma la primera y se resta la segunda, las dos por ``s``.

    **Contraste**: una tabla de 256 valores alrededor del gris medio, aplicada
    a todos los bytes de golpe con ``bytes.translate``. El alfa (255) se queda
    en 255.
    """
    fmt = QImage.Format_ARGB32_Premultiplied
    colour = pix.toImage().convertToFormat(fmt)
    grey = colour.convertToFormat(QImage.Format_Grayscale8).convertToFormat(fmt)
    mode = QPainter.CompositionMode
    above = _composed(_composed(colour, grey, mode.CompositionMode_Lighten), grey,
                      mode.CompositionMode_Difference)
    below = _composed(grey, _composed(colour, grey, mode.CompositionMode_Darken),
                      mode.CompositionMode_Difference)
    black = QImage(colour.size(), fmt)
    black.fill(Qt.black)
    below = _composed(black, below, mode.CompositionMode_SourceOver, saturation)
    out = _composed(colour, above, mode.CompositionMode_Plus, saturation)
    out = _composed(out, below, mode.CompositionMode_Difference)
    lut = bytes(max(0, min(255, round((v - 128) * (1 + contrast) + 128)))
                for v in range(256))
    data = bytes(out.constBits()).translate(lut)
    result = QPixmap.fromImage(QImage(data, out.width(), out.height(),
                                      out.bytesPerLine(), fmt).copy())
    result.setDevicePixelRatio(pix.devicePixelRatio())
    return result


def blurred_logo(path: str, size: int) -> QPixmap:
    """El logo grande y desenfocado del fondo de ``showcase``.

    El desenfoque es el barato: encoger a 1/10 y volver a ampliar suavizando.
    Un ``QGraphicsBlurEffect`` haría lo mismo, pero en cada fotograma.
    """
    source = QPixmap(path)
    if source.isNull():
        return QPixmap()
    small = source.scaled(size // 10, size // 10, Qt.KeepAspectRatio,
                          Qt.SmoothTransformation)
    return small.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
