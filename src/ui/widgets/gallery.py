"""Vista de galería: las versiones pasan en fila, como cartas, con una al frente.

Es el tercer modo de las listas, junto a la rejilla y la lista. Tiene cuatro
estilos, que se eligen desde la propia galería (el botón de capas de la
esquina, o las teclas 1-4) y se recuerdan como el modo de vista:

- ``perspective``: las laterales giran como las paredes de un pasillo, con
  reflejo en el suelo (el Cover Flow de Apple).
- ``shelf``: planas; la central grande con el halo de acento y las demás
  pequeñas y apagadas (como la Switch).
- ``carousel``: las tarjetas giran sobre un cilindro, en perspectiva.
- ``showcase``: fila abajo, la seleccionada con contorno blanco y su título, y
  detrás un fondo grande que cambia con la versión (como la Steam Deck).

**Cómo se pinta.** Qt Widgets no gira widgets en 3D: un widget es siempre un
rectángulo plano. Lo que gira son **imágenes** de las tarjetas (``grab()``),
pintadas con un ``QTransform`` con perspectiva (``rotate(…, Qt.YAxis)``). Pero
una imagen no tiene botones, así que la tarjeta del centro, cuando la galería
está quieta, es **el widget de verdad**, colocado justo donde se pinta su
imagen: se puede descargar, lanzar, marcar como favorita o abrir su menú igual
que en la rejilla. Al empezar a moverse se vuelve a capturar (puede haber
cambiado: una estrella marcada, un "Instalada" nuevo) y se esconde.

Por eso la central mide lo mismo en los cuatro estilos (escala 1, sin giro): el
widget no se puede escalar, y si la imagen midiera otra cosa, al pararse la
tarjeta daría un salto. Pintar 13 imágenes transformadas cuesta ~2,5 ms por
fotograma (medido en el prototipo), así que va sobrado.
"""

import math
import threading
from dataclasses import dataclass

from PySide6.QtCore import (QEasingCurve, QPointF, QRectF, Qt, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QBrush, QColor, QLinearGradient, QPainter,
                           QPen, QPixmap, QPolygonF,
                           QRadialGradient, QTransform)
from PySide6.QtWidgets import (QWidget)

from i18n import tr
from services.settings import GALLERY_STYLES as STYLES
from ui import theme as t
from ui.widgets.gallery_controls import HINT_RESERVE, GalleryControlsMixin
from ui.widgets.gallery_art import (CARD_RADIUS, GLOW_PAD, blurred_logo,
                                    dithered_focus, glow, greyscale,
                                    noise_tile, reflection,
                                    rounded)

# La carta es **vertical**, como una de Magic: alto = ancho × ``CARD_RATIO``,
# con el arte arriba y los datos debajo. Su tamaño sale del hueco de la ventana
# (``_card_size``): el ancho se calcula a partir del alto disponible, para que
# al redimensionar siga pareciendo una carta y no un rectángulo achaparrado,
# entre un mínimo (por debajo, los datos no caben) y un máximo (por encima, las
# laterales no tendrían sitio). El zoom con el que se construye es el
# completo: aquí la carta es la protagonista.
CARD_RATIO = 1.55
CARD_MIN_WIDTH = 205
CARD_MAX_WIDTH = 360
# Como mucho esta parte del ancho de la galería: en una ventana estrecha y
# alta, la carta no puede comerse a las laterales.
CARD_MAX_SHARE = 0.40
CARD_ZOOM = 1.0

# Parte del alto de la galería que puede ocupar una tarjeta (lo demás es aire
# arriba y el reflejo abajo).
CARD_HEIGHT_SHARE = 0.86


# Duraciones: un paso con flecha o rueda, y lo que tarda en asentarse al soltar
# un arrastre.
STEP_MS = 380
SETTLE_MS = 300

# Cuánto se apaga una tarjeta por cada puesto que se aleja del centro: pierde
# color (``DESATURATE``) y luz (``DARKEN``), con un tope para que las del fondo
# se sigan leyendo. Así la central manda sin tener que agrandarla más.
DESATURATE = 0.45
DARKEN = 0.16
DARKEN_MAX = 0.55

# Aire entre el techo de la galería y la carta central. Sin él, al encoger la
# ventana la carta tocaba el borde de arriba (y el halo se cortaba en seco).
TOP_MARGIN = 22

# Margen bajo la fila (``showcase``) y alto de la línea de atajos de teclado.
BOTTOM_MARGIN = 44
HINT_HEIGHT = 24

# El halo de la carta central: cuánto se extiende fuera de ella, su opacidad
# en reposo y el destello al llegar al centro (sube a 1 y vuelve en FLASH_MS).
GLOW_REST = 0.75
FLASH_MS = 520

# Giro de las laterales en el estilo ``perspective`` y cuánto encogen: más
# pequeñas, la central destaca y no parecen apelotonadas contra ella.
PERSPECTIVE_ANGLE = 38
SIDE_SHRINK = 0.26

# Resolución de la imagen de las laterales respecto a la de la central.
SIDE_RES = 0.5
# Las laterales se guardan ya apagadas por niveles de distancia (cada
# ``BAKE_STEP`` puestos), hasta ``BAKE_FAR``: más lejos ya no cambian.
BAKE_STEP = 0.2
BAKE_FAR = 3.6

# Cuántas tarjetas a cada lado se pintan. Más allá ya no se ven (se salen de la
# ventana o se funden con el fondo) y solo costarían tiempo.
VISIBLE = 6

# El reborde de la carta central: el color de su halo con este tanto de
# blanco (0..1). Es el filo más brillante de la luz.
RIM_WHITE = 0.45


@dataclass
class GalleryItem:
    """Una tarjeta de la galería y lo que el estilo ``showcase`` dice de ella.

    ``key`` identifica la versión entre reconstrucciones (filtrar, marcar un
    favorito...): con ella la galería sigue en la misma tarjeta en vez de
    volver a la primera.
    """

    card: QWidget
    key: str
    title: str
    subtitle: str
    color: str
    logo: str
    # Serie del splash (``services.splash.series_for``) y su fichero, si ya
    # está bajado. Llega después, en un hilo: ver ``set_splash``.
    series: str = ""
    splash: str = ""


class GalleryView(GalleryControlsMixin, QWidget):
    """Las tarjetas en fila, con una al frente; se recorren de lado.

    Flechas, Inicio/Fin, rueda del ratón, arrastrar, o clic en una lateral para
    traerla al centro; Intro pulsa el botón principal de la central (Descargar
    o Lanzar). ``current_changed`` avisa del índice de la que queda delante.
    """

    current_changed = Signal(int)
    # El fondo con dithering llega desde un hilo: (ancho, alto), imagen.
    _focus_ready = Signal(object, object)
    # Una carta acaba de pasar por el centro (al animar o al arrastrar): es el
    # momento del "tic". Si se saltan tres de golpe, llegan tres.
    passed = Signal()
    # Una carta nueva se ha quedado en el centro: el "clac" de selección.
    settled = Signal()
    # El botón de silencio de la esquina: True = silenciar.
    mute_toggled = Signal(bool)
    # El botón del puntero láser, a su lado: True = láser.
    laser_toggled = Signal(bool)
    # Se pide otro estilo (el botón de la esquina o las teclas 1-4). Lo
    # aplica quien lo guarda en los ajustes, para las dos galerías a la vez.
    style_requested = Signal(str)

    def __init__(self, style: str = "perspective", parent=None):
        super().__init__(parent)
        self.setObjectName("Gallery")
        self.setFocusPolicy(Qt.StrongFocus)
        self.style_name = style if style in STYLES else STYLES[0]
        self.items: list[GalleryItem] = []
        self.card_width = CARD_MIN_WIDTH
        self._images: dict[int, tuple[QPixmap, QPixmap, QPixmap]] = {}
        self._images_pending = False
        self._shades: dict[tuple, QPixmap] = {}
        self._bakes: dict[tuple, QPixmap] = {}
        self._logos: dict[str, QPixmap] = {}
        self._backdrops: dict[tuple, QPixmap] = {}
        self._glows: dict[tuple, QPixmap] = {}
        self._focus = None          # ((ancho, alto), QPixmap) ya calculado
        self._focus_busy = False    # hay un hilo calculándolo
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.setInterval(250)
        self._focus_timer.timeout.connect(self._compute_focus)
        self._focus_ready.connect(self._on_focus_ready)
        self._build_controls()
        self._flash = 0.0
        self._flashed = None
        self._flash_anim = QVariantAnimation(self)
        self._flash_anim.setDuration(FLASH_MS)
        self._flash_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._flash_anim.setStartValue(1.0)
        self._flash_anim.setEndValue(0.0)
        self._flash_anim.valueChanged.connect(self._on_flash)
        self._fitted = None
        self.pos = 0.0
        self.target = 0
        self._nearest = 0
        self._drag = None
        self._hits = []
        self._anim = QVariantAnimation(self)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_step)
        self._anim.finished.connect(self._settle)

    # ------------------------------------------------------------- contenido
    def set_style(self, style: str) -> None:
        if style in STYLES and style != self.style_name:
            self.style_name = style
            self._style_tooltip()
            # ``showcase`` lleva el splash de fondo y no en la tarjeta: cambia
            # el alto de todas.
            self._fit_all()
            self._settle()

    def set_items(self, items: list) -> None:
        """Cambia las tarjetas, siguiendo en la misma versión si sigue ahí.

        Las tarjetas viejas se sueltan con ``setParent(None)`` antes de
        ``deleteLater``: sin eso se pintan un fotograma más encima de las nuevas
        (el mismo parpadeo que en la rejilla).
        """
        current = (self.items[self.target].key
                   if 0 <= self.target < len(self.items) else None)
        for item in self.items:
            item.card.setParent(None)
            item.card.deleteLater()
        self.items = list(items)
        self._images.clear()
        self._bakes.clear()
        for item in self.items:
            item.card.setParent(self)
            item.card.hide()
        self._card_buttons_cursor()
        self._fit_all()
        keys = [item.key for item in self.items]
        index = keys.index(current) if current in keys else 0
        self._anim.stop()
        self.pos = float(index)
        self.target = index
        self._nearest = index
        self._settle()

    def set_splash(self, series: str, path: str) -> None:
        """Llega el splash de una serie (bajado en un hilo): a sus tarjetas."""
        for index, item in enumerate(self.items):
            if item.series == series and item.splash != path:
                item.splash = path
                if hasattr(item.card, "set_splash"):
                    item.card.set_splash(path)
                self._images.pop(index, None)
                self._drop_bakes(index)
        self.update()

    def _room(self) -> float:
        """El alto en el que caben las cartas: sin el aire de arriba ni los atajos."""
        return max(0.0, self.height() - HINT_HEIGHT - TOP_MARGIN)

    def _budget(self) -> int:
        return int(self._room() * CARD_HEIGHT_SHARE)

    def _card_size(self) -> tuple:
        """Ancho y alto máximo de una carta para el hueco de ahora.

        El ancho sale del alto disponible (alto / ``CARD_RATIO``), así la carta
        conserva su proporción vertical al redimensionar la ventana. En
        ``showcase`` la fila va abajo y sin arte (el splash está de fondo), así
        que su carta es más baja y se calcula con algo menos de hueco.
        """
        budget = self._budget()
        if self.style_name == "showcase":
            budget = int(budget * 0.62)
        width = round(budget / CARD_RATIO)
        width = min(width, int(self.width() * CARD_MAX_SHARE))
        width = max(CARD_MIN_WIDTH, min(CARD_MAX_WIDTH, width))
        return width, min(budget, round(width * CARD_RATIO))

    def _fit_key(self) -> tuple:
        return (*self._card_size(), self.style_name != "showcase")

    def _fit_all(self) -> None:
        """Da a cada tarjeta su tamaño para el hueco y el estilo de ahora.

        Las capturas dejan de valer (la tarjeta ha cambiado de tamaño), así
        que se tiran; se rehacen al pintar.
        """
        width, height = self._card_size()
        splash = self.style_name != "showcase"
        self.card_width = width
        for item in self.items:
            if hasattr(item.card, "fit"):
                item.card.fit(width, height, splash=splash)
            else:
                item.card.resize(width, self._card_height(item.card))
        self._fitted = self._fit_key()
        self._images.clear()
        self._bakes.clear()

    @staticmethod
    def _card_height(card: QWidget) -> int:
        """Alto de una tarjeta sin ``fit``, con un poco de aire para el logo."""
        card.ensurePolished()
        if card.layout() is not None:
            card.layout().activate()
        return max(card.minimumSizeHint().height(), card.sizeHint().height()) + 24

    def _image(self, index: int):
        """La captura de una tarjeta, su reflejo y su gris, si ya está hecha.

        **Nunca se captura aquí**: esto se llama desde ``paintEvent``, y hacer
        ``grab()`` de una tarjeta (que lleva su ``QGraphicsDropShadowEffect``)
        en mitad de un pintado deja dos *painters* sobre el mismo dispositivo:
        "A paint device can only be painted by one painter at a time" y, en un
        escritorio de verdad, violación de segmento (con el plugin *offscreen*
        de los tests no saltaba). Si falta, se pide para después
        (``_request_images``) y esa tarjeta espera un fotograma.
        """
        found = self._images.get(index)
        if found is None:
            self._request_images()
        return found

    def _capture(self, index: int) -> None:
        """Captura una tarjeta (fuera de cualquier pintado) y la guarda.

        Además de la imagen a tamaño completo (la central), guarda una a
        ``SIDE_RES`` para las laterales: se ven pequeñas y apagadas, y escalar
        en perspectiva por CPU cuesta según los píxeles de origen. Era la mitad
        de cada fotograma (medido: 5,6 de 11,4 ms).
        """
        pix = rounded(self.items[index].card.grab(), CARD_RADIUS)
        small = pix.scaled(round(pix.width() * SIDE_RES), round(pix.height() * SIDE_RES),
                           Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        # El reflejo y el gris se calculan ANTES de rebajar su
        # ``devicePixelRatio``: con un ratio de 0,5, Qt pinta en unidades
        # lógicas del doble de tamaño y el fundido del reflejo solo cubría la
        # mitad de la imagen (se veía la carta entera reflejada, sin apagar).
        small_mirror, small_grey = reflection(small), greyscale(small)
        # Con el ratio rebajado, se dibujan al mismo tamaño lógico que la
        # grande: el código que las pinta no cambia.
        for image in (small, small_mirror, small_grey):
            image.setDevicePixelRatio(pix.devicePixelRatio() * SIDE_RES)
        self._drop_bakes(index)
        self._images[index] = (pix, reflection(pix), greyscale(pix),
                               small, small_mirror, small_grey)

    def _request_images(self) -> None:
        if not self._images_pending:
            self._images_pending = True
            QTimer.singleShot(0, self, self._prepare_images)

    def _prepare_images(self) -> None:
        """Captura las tarjetas que se ven y aún no tienen imagen.

        Desaturar y hacer el reflejo costaría mucho en cada fotograma, así que
        se hace una vez por tarjeta y se guarda.
        """
        self._images_pending = False
        changed = False
        for index in range(len(self.items)):
            if abs(index - self.pos) <= VISIBLE + 1 and index not in self._images:
                self._capture(index)
                changed = True
        if changed:
            self.update()

    def _logo(self, path: str) -> QPixmap:
        if path not in self._logos:
            self._logos[path] = blurred_logo(path, 520)
        return self._logos[path]

    # ------------------------------------------------------------ movimiento
    def go(self, index: int, ms: int = STEP_MS) -> None:
        """Lleva la tarjeta ``index`` al centro, animando el recorrido."""
        if not self.items:
            return
        index = max(0, min(len(self.items) - 1, round(index)))
        self._begin_motion()
        self.target = index
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(self.pos)
        self._anim.setEndValue(float(index))
        self._anim.start()
        self.current_changed.emit(index)

    def _on_step(self, value) -> None:
        self.pos = float(value)
        self._check_passed()
        self.update()

    def _check_passed(self) -> None:
        nearest = round(self.pos)
        if nearest != self._nearest:
            self._nearest = nearest
            self.passed.emit()

    def _begin_motion(self) -> None:
        """Antes de moverse: la central vuelve a ser una imagen.

        Se recaptura justo ahora porque la tarjeta pudo cambiar mientras estaba
        quieta (una estrella marcada, el hover del ratón...).
        """
        centre = self._centre_card()
        if centre is not None and centre.isVisible():
            # Se captura AQUÍ, con la tarjeta aún visible y fuera de cualquier
            # pintado (ver ``_image``), y luego se esconde.
            self._capture(self.target)
            centre.hide()

    def _centre_card(self):
        if 0 <= self.target < len(self.items):
            return self.items[self.target].card
        return None

    def _settle(self) -> None:
        """Quieta: la tarjeta del centro pasa a ser el widget de verdad."""
        self.update()
        if self._drag is not None or self._anim.state() == QVariantAnimation.Running:
            return
        for index, item in enumerate(self.items):
            if index != self.target:
                item.card.hide()
        centre = self._centre_card()
        if centre is None:
            return
        transform = self._place(0.0, self.card_width, centre.height())[0]
        centre.move(transform.mapRect(
            QRectF(0, 0, self.card_width, centre.height())).topLeft().toPoint())
        centre.show()
        centre.raise_()
        for button in self._corner_buttons():
            button.raise_()
        # El destello solo al llegar una carta nueva al centro, no en cada
        # reajuste (cambiar el tamaño de la ventana o el estilo).
        key = self.items[self.target].key
        if key != self._flashed:
            self._flashed = key
            self._flash_anim.stop()
            self._flash_anim.start()
            self.settled.emit()

    def _on_flash(self, value) -> None:
        self._flash = float(value)
        self.update()
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_corner_buttons()
        if self._fitted != self._fit_key():
            self._fit_all()
        self._settle()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        self._drag = (event.position().x(), self.pos, False)

    def mouseMoveEvent(self, event):
        if self._drag is None:
            return
        x0, pos0, moved = self._drag
        dx = event.position().x() - x0
        if not moved and abs(dx) > 4:
            moved = True
            self._anim.stop()
            self._begin_motion()
        if moved:
            # El arrastre se deja pasar un poco de los extremos (40 % de
            # tarjeta) y al soltar vuelve: así se nota que no hay más.
            self.pos = max(-0.4, min(len(self.items) - 0.6,
                                     pos0 - dx / self._spacing()))
            self._check_passed()
            self.update()
        self._drag = (x0, pos0, moved)

    def mouseReleaseEvent(self, event):
        if self._drag is None:
            return super().mouseReleaseEvent(event)
        moved = self._drag[2]
        self._drag = None
        if moved:
            self.go(self.pos, SETTLE_MS)
            return
        # Un clic sin arrastre trae al centro la tarjeta pinchada (la que
        # quede encima, por eso se recorre al revés del orden de pintado).
        for index, polygon in reversed(self._hits):
            if polygon.containsPoint(event.position(), Qt.OddEvenFill):
                if index != self.target:
                    self.go(index)
                return

    def _spacing(self) -> float:
        """Píxeles de arrastre que equivalen a pasar una tarjeta."""
        return {"perspective": self.card_width * 0.40, "shelf": self.card_width * 0.85,
                "carousel": self.card_width * 0.55,
                "showcase": self.card_width * 0.80}[self.style_name]

    # ------------------------------------------------------------- geometría
    def _row_y(self, h: float) -> float:
        """Altura del centro de la fila: abajo en ``showcase``, centrada si no."""
        if self.style_name == "showcase":
            return self.height() - h / 2 - BOTTOM_MARGIN
        # Desde debajo del aire de arriba: como la carta mide como mucho el
        # 86 % del hueco y su centro va al 44 %, su borde de arriba nunca
        # queda por encima de ``TOP_MARGIN``, por baja que sea la ventana.
        return TOP_MARGIN + self._room() * 0.44

    def _place(self, d: float, w: float, h: float):
        """Dónde y cómo va la tarjeta a distancia ``d`` del centro.

        Devuelve el ``QTransform`` (de coordenadas de la tarjeta a las de la
        galería), su profundidad (para pintar de atrás adelante) y su opacidad.
        En ``d == 0`` todos los estilos dejan la tarjeta plana y a escala 1:
        ahí es donde se coloca el widget de verdad.
        """
        cx, cy = self.width() / 2, self._row_y(h)
        transform = QTransform()
        a = max(-1.0, min(1.0, d))     # el primer paso, hacia la central
        rest = d - a                   # lo que queda más allá
        style = self.style_name
        if style == "perspective":
            # El primer paso deja aire entre la central y la vecina (el halo
            # necesita sitio); más allá van a paso fijo, solapándose un poco.
            x = a * w * 0.92 + rest * w * 0.40
            scale = 1.0 - SIDE_SHRINK * abs(a)
            transform.translate(cx + x, cy)
            # Las de la derecha giran hacia la izquierda y viceversa: miran
            # al centro, como las paredes de un pasillo. 38° y no los ~60° del
            # Cover Flow original: más girada, la carta se leía como una raya.
            transform.rotate(a * PERSPECTIVE_ANGLE, Qt.YAxis)
            transform.scale(scale, scale)
            depth = abs(d)
            alpha = 1.0 - 0.13 * max(0.0, abs(d) - 1.0)
        elif style == "shelf":
            scale = 1.0 - 0.34 * abs(a)
            x = a * w * 0.98 + rest * (w * 0.66 + 18)
            transform.translate(cx + x, cy)
            transform.scale(scale, scale)
            depth = abs(d)
            alpha = 1.0 - 0.45 * abs(a)
        elif style == "carousel":
            theta = d * math.radians(34)
            radius = w * 1.85
            sink = radius * (1 - math.cos(theta))
            scale = 1100 / (1100 + sink * 2.6)
            transform.translate(cx + radius * math.sin(theta), cy - sink * 0.10)
            # Gira menos que el propio cilindro: si cada carta mirara del todo
            # hacia fuera, las de los lados quedarían casi de canto.
            transform.rotate(math.degrees(theta) * -0.6, Qt.YAxis)
            transform.scale(scale, scale)
            depth = sink
            alpha = min(1.0, math.cos(theta) * 1.2)
        else:  # showcase
            scale = 1.0 - 0.18 * abs(a)
            x = a * (w * 0.96 + 22) + rest * (w * 0.82 + 18)
            transform.translate(cx + x, cy)
            transform.scale(scale, scale)
            depth = abs(d)
            alpha = 1.0 - 0.25 * abs(a)
        transform.translate(-w / 2, -h / 2)
        return transform, depth, max(0.0, alpha)

    # ---------------------------------------------------------------- pintar
    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            # Se cierra pase lo que pase: una excepción con el painter abierto
            # dejaba la ventana entera sin poder pintar ("one painter at a
            # time", "endPaint() called with active painter") y acababa en
            # violación de segmento.
            painter.end()

    def _paint(self, painter) -> None:
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(t.BG))
        if self.style_name == "showcase":
            # El escaparate no lleva foco: su fondo es el splash, y el velo que
            # lo funde acaba en el gris liso (sobre el foco dejaba una franja).
            self._paint_backdrop(painter)
            painter.fillRect(self.rect(), QBrush(noise_tile()))
        else:
            self._paint_glow(painter)
        centre_live = self._centre_card() is not None and self._centre_card().isVisible()
        order = []
        for index in range(len(self.items)):
            d = index - self.pos
            if abs(d) > VISIBLE:
                continue
            h = self.items[index].card.height()
            transform, depth, alpha = self._place(d, self.card_width, h)
            order.append((depth, index, transform, alpha, h, d))
        order.sort(key=lambda entry: -entry[0])
        self._hits = []
        for _, index, transform, alpha, h, d in order:
            if alpha <= 0.01:
                continue
            live = centre_live and index == self.target
            painter.setTransform(transform)
            if index == self.target:
                self._paint_centre_glow(painter, d, h)
            painter.setOpacity(alpha)
            images = self._image(index)
            if images is None:
                continue   # se está preparando: llega en el siguiente ciclo
            near = abs(d) < 0.9
            pix, mirror, _grey = images[:3] if near else images[3:]
            reflected = self.style_name in ("perspective", "carousel") and abs(d) <= 3
            # Las que caen fuera de la ventana no se pintan: con 13 cartas en
            # perspectiva era casi la mitad del tiempo de cada fotograma.
            reach = h * 2 + 6 if reflected else h
            if not transform.mapRect(QRectF(0, 0, self.card_width, reach)).intersects(
                    QRectF(self.rect())):
                continue
            # Lejos del centro, la tarjeta pierde luz (ver DARKEN; el color lo
            # pierde en ``_baked``). El reflejo se apaga igual.
            dark = min(DARKEN_MAX, abs(d) * DARKEN)
            if reflected:
                # El reflejo solo en las cercanas: más allá apenas se ve.
                painter.setOpacity(alpha * (1.0 - dark))
                painter.drawPixmap(QPointF(0, h + 6), mirror)
                painter.setOpacity(alpha)
            self._paint_selection(painter, d, h)
            if not live:
                painter.drawPixmap(QPointF(0, 0), self._baked(index, d, near))
            self._hits.append((index, transform.map(QPolygonF(QRectF(0, 0, self.card_width, h)))))
        painter.resetTransform()
        painter.setOpacity(1.0)
        if self.style_name == "showcase":
            self._paint_caption(painter)
        self._paint_hint(painter)

    def _paint_centre_glow(self, painter: QPainter, d: float, h: float) -> None:
        """El halo de la central, que se apaga según se aparta del centro."""
        strength = max(0.0, 1.0 - abs(d) * 1.5)
        if strength <= 0.0:
            return
        item = self.items[self.target]
        key = (self.card_width, int(h), item.color)
        if key not in self._glows:
            if len(self._glows) > 8:
                self._glows.clear()
            self._glows[key] = glow(self.card_width, int(h), item.color)
        halo = self._glows[key]
        painter.save()
        # Con el destello, el halo además crece un poco desde el centro.
        grow = 1.0 + 0.08 * self._flash
        painter.translate(self.card_width / 2, h / 2)
        painter.scale(grow, grow)
        painter.translate(-self.card_width / 2, -h / 2)
        painter.setOpacity(strength * min(1.0, GLOW_REST + (1 - GLOW_REST) * self._flash))
        # Aditivo: suma luz a lo que hay debajo en vez de taparlo, así que sobre
        # el fondo oscuro y las vecinas se lee como brillo y no como una mancha.
        painter.setCompositionMode(QPainter.CompositionMode_Plus)
        painter.drawPixmap(QPointF(-GLOW_PAD, -GLOW_PAD), halo)
        # El reborde: 1 px del color del halo con algo de blanco, justo por
        # fuera de la carta (por dentro lo taparía el widget de verdad).
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        tint = QColor(item.color)
        rim = QColor(round(tint.red() + (255 - tint.red()) * RIM_WHITE),
                     round(tint.green() + (255 - tint.green()) * RIM_WHITE),
                     round(tint.blue() + (255 - tint.blue()) * RIM_WHITE))
        rim.setAlphaF(strength)
        painter.setOpacity(1.0)
        painter.setPen(QPen(rim, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(-0.5, -0.5, self.card_width + 1, h + 1),
                                CARD_RADIUS + 0.5, CARD_RADIUS + 0.5)
        painter.restore()

    def _drop_bakes(self, index: int) -> None:
        """Olvida las versiones apagadas de una carta (su captura cambió)."""
        for key in [k for k in self._bakes if k[0] == index]:
            del self._bakes[key]

    def _baked(self, index: int, d: float, full: bool = False) -> QPixmap:
        """La lateral ya apagada (gris + color + velo), en una sola imagen.

        Antes se pintaban tres capas en perspectiva por carta, y en
        perspectiva lo que cuesta es la superficie pintada: tres veces la
        misma. Aquí se mezclan una vez, sin transformar (barato), y se guarda
        por nivel de distancia (``BAKE_STEP``: un 3 % de brillo por nivel, que
        en movimiento no se ve). Se pinta en una imagen aparte, no en la
        galería: dentro de un ``paintEvent`` eso sí se puede. ``full`` es para
        las dos más cercanas al centro: a resolución completa, que se ven
        grandes.
        """
        level = round(min(abs(d), BAKE_FAR) / BAKE_STEP)
        key = (index, level, full)
        if key not in self._bakes:
            images = self._images[index]
            small, grey = (images[0], images[2]) if full else (images[3], images[5])
            fade = min(1.0, level * BAKE_STEP * DESATURATE)
            dark = min(DARKEN_MAX, level * BAKE_STEP * DARKEN)
            baked = QPixmap(small.size())
            baked.setDevicePixelRatio(small.devicePixelRatio())
            baked.fill(Qt.transparent)
            mix = QPainter(baked)
            try:
                self._paint_card(mix, small, grey, self._shade(small), fade, dark, 1.0)
            finally:
                mix.end()
            self._bakes[key] = baked
        return self._bakes[key]

    def _shade(self, pix: QPixmap) -> QPixmap:
        """La silueta negra de una carta, para oscurecerla (una por tamaño).

        Es una imagen y no un ``drawRoundedRect``: rellenar una forma con
        antialias en perspectiva en cada fotograma era de lo más caro.
        """
        key = (pix.width(), pix.height())
        if key not in self._shades:
            ratio = pix.devicePixelRatio()
            shade = QPixmap(pix.size())
            shade.setDevicePixelRatio(ratio)
            shade.fill(Qt.transparent)
            shape = QPainter(shade)
            shape.setRenderHint(QPainter.Antialiasing)
            shape.setPen(Qt.NoPen)
            shape.setBrush(QColor(0, 0, 0))
            shape.drawRoundedRect(QRectF(0, 0, pix.width() / ratio,
                                         pix.height() / ratio),
                                  CARD_RADIUS, CARD_RADIUS)
            shape.end()
            self._shades[key] = shade
        return self._shades[key]

    @staticmethod
    def _paint_card(painter: QPainter, pix: QPixmap, grey: QPixmap, shade: QPixmap,
                    fade: float, dark: float, alpha: float) -> None:
        """Una tarjeta lateral, apagada según lo lejos que esté.

        Primero la gris y encima la de color con opacidad ``1 - fade``: el
        resultado es la mezcla de las dos (si ya está gris del todo, la de
        color ni se pinta). El oscurecido es su silueta negra encima.
        """
        if fade >= 0.999:
            painter.drawPixmap(QPointF(0, 0), grey)
        else:
            if fade > 0.0:
                painter.drawPixmap(QPointF(0, 0), grey)
                painter.setOpacity(alpha * (1.0 - fade))
            painter.drawPixmap(QPointF(0, 0), pix)
        if dark > 0.0:
            painter.setOpacity(alpha * dark)
            painter.drawPixmap(QPointF(0, 0), shade)
        painter.setOpacity(alpha)

    def _paint_hint(self, painter: QPainter) -> None:
        """La línea de atajos al pie: cómo se mueve uno por la galería."""
        font = self.font()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor(t.MUTED))
        # Se deja el hueco de los botones de la esquina a los DOS lados, para
        # que la línea siga centrada; si no cabe, se recorta con "…" en vez de
        # pasar por debajo de los botones.
        reserve = HINT_RESERVE
        rect = QRectF(reserve, self.height() - HINT_HEIGHT - 4,
                      max(0, self.width() - 2 * reserve), HINT_HEIGHT)
        text = painter.fontMetrics().elidedText(tr(
            "← →  move   ·   ↑  launch   ·   Enter  download or launch   ·   "
            "1–4  style   ·   Home / End  first / last   ·   wheel or drag  scroll"),
            Qt.ElideRight, int(rect.width()))
        painter.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, text)

    def _paint_glow(self, painter: QPainter) -> None:
        """Un foco suave del color de acento detrás de la central.

        Lo bueno es la versión con *dithering* (``dithered_focus``), que se
        calcula en un hilo para el tamaño actual. La primera vez se pide en el
        acto; al redimensionar, se espera a que el tamaño se quede quieto y
        mientras tanto se pinta **la anterior estirada** (no se nota, y no
        tiene anillos). Solo la primera vez, sin ninguna hecha, sale el
        degradado de Qt un momento.
        """
        size = (self.width(), self.height())
        if self._focus is not None and self._focus[0] == size:
            painter.drawPixmap(0, 0, self._focus[1])
            return
        # Se calcula cuando el tamaño lleva un rato quieto, y uno a la vez:
        # arrastrando el borde de la ventana, cada tamaño intermedio lanzaba
        # su hilo, y todos peleaban por el GIL con la interfaz.
        if not self._focus_busy:
            if self._focus is None:
                QTimer.singleShot(0, self, self._compute_focus)
            else:
                self._focus_timer.start()
        if self._focus is not None:
            painter.drawPixmap(self.rect(), self._focus[1])
            return
        centre = QPointF(self.width() / 2, self.height() * 0.46)
        glow = QRadialGradient(centre, self.width() * 0.45)
        accent = QColor(t.ACCENT)
        accent.setAlpha(55)
        glow.setColorAt(0.0, accent)
        accent.setAlpha(0)
        glow.setColorAt(1.0, accent)
        painter.fillRect(self.rect(), glow)

    def _compute_focus(self) -> None:
        size = (self.width(), self.height())
        if self._focus_busy or size[0] <= 0 or size[1] <= 0:
            return
        if self._focus is not None and self._focus[0] == size:
            return
        self._focus_busy = True

        def work():
            image = dithered_focus(size[0], size[1], t.BG, t.ACCENT)
            try:
                self._focus_ready.emit(size, image)
            except RuntimeError:
                pass   # la galería se destruyó mientras se calculaba

        threading.Thread(target=work, daemon=True).start()

    def _on_focus_ready(self, size, image) -> None:
        self._focus_busy = False
        if size == (self.width(), self.height()):
            self._focus = (size, QPixmap.fromImage(image))
        self.update()   # si el tamaño cambió mientras tanto, se pedirá otro

    def _paint_selection(self, painter: QPainter, d: float, h: float) -> None:
        """El marco de la seleccionada: halo de acento o contorno blanco.

        Se apaga según se aleja del centro (``d``), así que al pasar de una a
        otra el marco "viaja" en vez de saltar.
        """
        strength = 1.0 - abs(d) * 2
        if strength <= 0 or self.style_name not in ("shelf", "showcase"):
            return
        if self.style_name == "shelf":
            color, width, grow = QColor(t.ACCENT), 3, 4
        else:
            color, width, grow = QColor(t.FOCUS_RING), 3, 1.5
        color.setAlphaF(strength)
        painter.setPen(QPen(color, width))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(-grow, -grow, self.card_width + 2 * grow,
                                       h + 2 * grow),
                                CARD_RADIUS + grow, CARD_RADIUS + grow)

    def _backdrop_height(self) -> float:
        h = self.items[self.target].card.height() if self.items else 200
        return max(0.0, self._row_y(h) - h / 2 - 12)

    def _paint_backdrop(self, painter: QPainter) -> None:
        """El fondo de ``showcase``: color y logo de la seleccionada.

        Al pasar de una tarjeta a otra, los fondos de las dos se funden según
        lo que falte para llegar, y el logo se desliza un poco (paralaje).
        """
        if not self.items:
            return
        height = self._backdrop_height()
        painter.save()
        painter.setClipRect(QRectF(0, 0, self.width(), height))
        low = math.floor(self.pos)
        k = self.pos - low
        for index, weight in ((low, 1 - k), (low + 1, k)):
            if weight <= 0.01 or not 0 <= index < len(self.items):
                continue
            item = self.items[index]
            shift = (index - self.pos) * 140
            if item.splash:
                # Con splash, el fondo es la ilustración de la versión (como el
                # Big Picture de Steam), deslizándose un poco con el paso.
                art = self._backdrop(item.splash, self.width() + 160, height)
                painter.setOpacity(0.85 * weight)
                painter.drawPixmap(QPointF(-80 + shift * 0.6, 0), art)
                painter.setOpacity(1.0)
                continue
            tint = QColor(item.color)
            tint.setAlphaF(0.6 * weight)
            fade = QLinearGradient(0, 0, self.width(), height)
            fade.setColorAt(0.0, tint)
            clear = QColor(tint)
            clear.setAlpha(0)
            fade.setColorAt(1.0, clear)
            painter.fillRect(QRectF(0, 0, self.width(), height), fade)
            logo = self._logo(item.logo)
            if not logo.isNull():
                painter.setOpacity(0.35 * weight)
                painter.drawPixmap(QPointF(self.width() * 0.58 + shift,
                                           height * 0.5 - logo.height() * 0.55),
                                   logo)
                painter.setOpacity(1.0)
        veil = QLinearGradient(0, height * 0.35, 0, height)
        base = QColor(t.BG)
        base.setAlpha(0)
        veil.setColorAt(0.0, base)
        base.setAlpha(255)
        veil.setColorAt(1.0, base)
        painter.fillRect(QRectF(0, 0, self.width(), height), veil)
        painter.restore()

    def _backdrop(self, path: str, width: float, height: float) -> QPixmap:
        """El splash escalado para cubrir el fondo, guardado por tamaño."""
        key = (path, int(width), int(height))
        if key not in self._backdrops:
            if len(self._backdrops) > 24:
                self._backdrops.clear()
            source = QPixmap(path)
            self._backdrops[key] = (source.scaled(
                int(width), int(height), Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation) if not source.isNull() else QPixmap())
        return self._backdrops[key]

    def _paint_caption(self, painter: QPainter) -> None:
        """Título y detalle de la seleccionada, encima de la fila."""
        if not self.items:
            return
        item = self.items[max(0, min(len(self.items) - 1, round(self.pos)))]
        bottom = self._backdrop_height()
        font = self.font()
        font.setPixelSize(20)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(t.TEXT_SEL))
        painter.drawText(QRectF(0, bottom - 58, self.width(), 28),
                         Qt.AlignHCenter | Qt.AlignVCenter, item.title)
        font.setPixelSize(13)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(t.MUTED))
        painter.drawText(QRectF(0, bottom - 28, self.width(), 20),
                         Qt.AlignHCenter | Qt.AlignVCenter, item.subtitle)
