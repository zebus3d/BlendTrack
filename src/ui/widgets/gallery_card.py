"""La tarjeta grande de la galería: splash, datos de la versión y acciones.

En la galería la tarjeta es la protagonista, así que tiene más que contar que
en la rejilla. De arriba abajo, por orden de importancia:

1. El **splash** de la versión (``services.splash``), a lo ancho y apagándose
   hacia el cuerpo de la tarjeta. Sin splash (forks, versiones 2.x, sin red),
   un degradado del color de la versión con su logo.
2. El título y su línea de datos (canal, tamaño, rama, arquitectura).
3. Un bloque de información en pares etiqueta/valor (fecha, fichero, build...).
4. Las acciones al pie, donde se buscan.

**No repite las acciones**: dentro va la tarjeta de rejilla de siempre
(``GridBuildCard`` o ``GridInstalledCard``), con sus botones, su menú y sus
señales ya conectadas por ``build_lists``. Aquí solo se le quita el logo (el
splash ocupa su sitio), se le hace el fondo transparente y se le mete el bloque
de información antes de los botones. Así un cambio en las acciones de las
tarjetas llega solo a la galería.
"""

from datetime import datetime

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from i18n import tr
from ui import theme as t
from ui.widgets.cards import _HoverCard, card_shadow
from ui.widgets.gallery_art import enhance
from ui.widgets.labels import ElidedLabel

# Alto máximo del arte respecto al ancho de la tarjeta. El splash de Blender es
# apaisado (1000×500), pero la tarjeta es vertical, como una carta: el arte se
# recorta al centro ("cover") hasta ser cuadrado, y con los datos debajo la
# carta queda en ~1:1,55 (``gallery.CARD_RATIO``). Si la ventana no da para
# tanto, encoge (``fit``).
SPLASH_RATIO = 1.0

# Por debajo de este alto el splash ya no se lee (una franja de 40 px de una
# ilustración es ruido): se esconde y queda la tarjeta sin él.
SPLASH_MIN = 56

RADIUS = 12   # el de ``QFrame#Card`` en el QSS

# Dónde empieza la ilustración en un splash de Blender (fracción del ancho):
# a la izquierda van el logotipo y el número de versión.
ART_LEFT = 0.46


class _Splash(QWidget):
    """El splash a lo ancho, recortado para cubrir su hueco ("cover").

    Las esquinas de arriba van redondeadas como la tarjeta y la parte de abajo
    se funde con el color de la tarjeta, para que la ilustración no corte el
    texto con una raya.
    """

    def __init__(self, color: str, logo: str, base: str, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._color = QColor(color)
        self._base = QColor(base)
        self._logo = QPixmap(logo)

    def set_pixmap(self, path: str) -> None:
        # Con un punto más de saturación y contraste (``enhance``), para que
        # el arte de la carta destaque. Se hace una vez, al cargarlo.
        pix = QPixmap(path) if path else QPixmap()
        self._pixmap = enhance(pix) if not pix.isNull() else pix
        self.update()

    def has_image(self) -> bool:
        return not self._pixmap.isNull()

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
        rect = QRectF(self.rect())
        clip = QPainterPath()
        # Redondeado arriba y recto abajo: se dibuja con radio en todas y se le
        # suma un rectángulo que tapa las dos esquinas de abajo. Con la regla
        # *winding*: con la de por defecto (par-impar) la zona donde se solapan
        # las dos figuras se volvía un agujero y el arte se cortaba a la mitad.
        clip.setFillRule(Qt.WindingFill)
        clip.addRoundedRect(rect, RADIUS, RADIUS)
        clip.addRect(QRectF(0, rect.height() / 2, rect.width(), rect.height() / 2))
        painter.setClipPath(clip.simplified())
        if self.has_image():
            pix = self._pixmap
            # Solo la parte del arte: los splash de Blender llevan el logotipo
            # y la versión a la izquierda, y recortado en una carta vertical
            # el logotipo salía partido. De ``ART_LEFT`` a la derecha queda la
            # ilustración, que es lo que distingue una versión de otra.
            source = QRectF(pix.width() * ART_LEFT, 0,
                            pix.width() * (1 - ART_LEFT), pix.height())
            scale = max(rect.width() / source.width(),
                        rect.height() / source.height())
            w, h = source.width() * scale, source.height() * scale
            painter.drawPixmap(QRectF((rect.width() - w) / 2, (rect.height() - h) / 2,
                                      w, h), pix, source)
        else:
            # Sin splash: el color de la versión y su logo, para que la tarjeta
            # no tenga un agujero gris arriba.
            fill = QLinearGradient(0, 0, rect.width(), rect.height())
            top = QColor(self._color)
            top.setAlphaF(0.55)
            fill.setColorAt(0.0, top)
            fill.setColorAt(1.0, self._base)
            painter.fillRect(rect, fill)
            if not self._logo.isNull():
                side = min(rect.height() * 0.55, 72)
                painter.setOpacity(0.85)
                painter.drawPixmap(QRectF((rect.width() - side) / 2,
                                          (rect.height() - side) / 2 - 6, side, side),
                                   self._logo, QRectF(self._logo.rect()))
                painter.setOpacity(1.0)
        # El fundido ocupa solo el último cuarto: más arriba se comía la mitad
        # de la ilustración y parecía un hueco vacío.
        fade = QLinearGradient(0, rect.height() * 0.75, 0, rect.height())
        clear = QColor(self._base)
        clear.setAlpha(0)
        fade.setColorAt(0.0, clear)
        fade.setColorAt(1.0, self._base)
        painter.fillRect(rect, fade)


def _info_block(rows) -> QWidget:
    """Los datos de la versión en pares etiqueta/valor.

    Etiqueta en ``Muted`` y valor en texto normal (los dos pasan AA sobre la
    tarjeta). El valor es un ``ElidedLabel``: una ruta larga no puede ensanchar
    la tarjeta, se recorta por el medio y deja el texto entero en el tooltip.
    """
    box = QWidget()
    box.setObjectName("GalleryInfo")
    grid = QGridLayout(box)
    grid.setContentsMargins(4, 4, 4, 0)
    grid.setHorizontalSpacing(12)
    grid.setVerticalSpacing(4)
    for row, (label, value) in enumerate(rows):
        key = QLabel(label)
        key.setObjectName("Muted")
        grid.addWidget(key, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
        text = ElidedLabel(value, Qt.ElideMiddle)
        text.setObjectName("GalleryValue")
        grid.addWidget(text, row, 1)
    grid.setColumnStretch(1, 1)
    return box


def build_rows(build) -> list:
    """Qué se cuenta de una versión de la tienda."""
    rows = []
    if build.mtime:
        rows.append((tr("Build date"),
                     datetime.fromtimestamp(build.mtime).strftime("%Y-%m-%d %H:%M")))
    rows.append((tr("File"), build.filename))
    if build.build_hash:
        rows.append((tr("Build"), build.build_hash[:12]))
    return rows


def installed_rows(entry) -> list:
    """Qué se cuenta de una versión instalada."""
    rows = [(tr("Folder"), str(entry.path))]
    downloaded = getattr(entry, "downloaded", 0)
    if downloaded:
        rows.append((tr("Downloaded"),
                     datetime.fromtimestamp(downloaded).strftime("%Y-%m-%d %H:%M")))
    if entry.branch:
        rows.append((tr("Branch"), entry.branch))
    if entry.build_hash:
        rows.append((tr("Build"), entry.build_hash[:12]))
    return rows


class GalleryCard(_HoverCard, QFrame):
    """La tarjeta grande de la galería; envuelve una tarjeta de rejilla.

    ``inner`` es la tarjeta de siempre, ya con sus señales conectadas.
    ``primary_button`` se reexpone para que la galería lo pulse con Intro.
    """

    def __init__(self, inner: QFrame, rows, color: str, logo: str,
                 installed: bool, parent=None):
        super().__init__(parent)
        self.inner = inner
        self.primary_button = getattr(inner, "primary_button", None)
        self.setObjectName("Card")
        self.setProperty("installed", "true" if installed else "false")
        self.setAttribute(Qt.WA_Hover, True)
        card_shadow(self)
        base = t.SURFACE if installed else t.CARD_DIM

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.splash = _Splash(color, logo, base)
        lay.addWidget(self.splash)

        # La tarjeta de dentro deja de ser una tarjeta: sin fondo, sin sombra y
        # sin logo (el splash ocupa su sitio). Su alto fijo de rejilla también
        # sobra: aquí manda el contenido.
        inner.setProperty("embedded", "true")
        inner.tilt_enabled = False
        inner.setGraphicsEffect(None)
        inner.setMinimumHeight(0)
        inner.setMaximumHeight(16777215)
        inner_lay = inner.layout()
        logo_item = inner_lay.itemAt(0)
        if logo_item is not None and logo_item.widget() is not None:
            logo_item.widget().hide()
        # El bloque va antes del estirable y de la fila de botones (los dos
        # últimos elementos), para que las acciones queden al pie.
        self.info = _info_block(rows)
        inner_lay.insertWidget(max(0, inner_lay.count() - 2), self.info)
        lay.addWidget(inner, 1)

    def set_splash(self, path: str) -> None:
        self.splash.set_pixmap(path)

    def content_height(self) -> int:
        """Lo que mide la tarjeta sin splash."""
        self.inner.layout().activate()
        return self.inner.sizeHint().height()

    def fit(self, width: int, budget: int, splash: bool = True) -> int:
        """Ajusta la tarjeta a ``width`` y a un alto disponible; devuelve su alto.

        El splash se encoge primero (hasta ``SPLASH_MIN``, y por debajo se
        esconde): los datos y los botones tienen que caber siempre.
        """
        content = self.content_height()
        room = min(int(width * SPLASH_RATIO), budget - content) if splash else 0
        if room >= SPLASH_MIN:
            self.splash.setFixedHeight(room)
            self.splash.show()
        else:
            room = 0
            self.splash.hide()
        height = content + room
        self.setFixedSize(width, height)
        return height
