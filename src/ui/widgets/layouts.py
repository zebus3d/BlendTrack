"""Piezas de layout que se repetían en varias vistas.

Vaciar un layout, montar un área de scroll para una lista y poner una nota
apagada ("no hay nada todavía") se hacían a mano en Recientes, Add-ons,
Migración y la ventana principal, cada una con su pequeña variante. Aquí está
la única versión de cada una, con el porqué de sus detalles.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from ui import theme as t


def clear_layout(layout, keep_stretch: bool = False) -> None:
    """Vacía un layout de widgets de verdad.

    Se **quita del layout y se oculta** además de ``deleteLater()``: el borrado
    diferido no es inmediato, así que sin el ``setParent(None)`` el widget viejo
    seguía ocupando su hueco (y salía duplicado) hasta que el bucle de eventos
    lo recogía. Con ``keep_stretch`` se respeta el hueco elástico del final
    (``addStretch``), que algunas listas usan para empujar sus filas arriba.
    """
    index = layout.count() - 1
    while index >= 0:
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is None and keep_stretch:
            index -= 1
            continue
        layout.takeAt(index)
        if widget is not None:
            widget.setParent(None)
            widget.hide()
            widget.deleteLater()
        index -= 1


def list_scroll(scroll_name: str, body_name: str, spacing: int = 4,
                margins=(0, 0, 0, 0), align_top: bool = True):
    """Área de scroll vertical con un cuerpo en columna: ``(scroll, layout)``.

    Sin marco ni barra horizontal (las filas se recortan con ``ElidedLabel``,
    no se desplazan de lado) y con ``objectName`` en el área y en el cuerpo
    para que el QSS los ponga transparentes: un ``QScrollArea`` pinta su propio
    fondo y cortaría la tarjeta en la que va.
    """
    scroll = QScrollArea()
    scroll.setObjectName(scroll_name)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    body = QWidget()
    body.setObjectName(body_name)
    layout = QVBoxLayout(body)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    if align_top:
        layout.setAlignment(Qt.AlignTop)
    scroll.setWidget(body)
    return scroll, layout


def muted_note(text: str) -> QLabel:
    """Nota apagada y centrada para una lista vacía ("No hay recientes")."""
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignHCenter)
    return label


class CornerGrip(QWidget):
    """Esquinita para estirar el panel, abajo a la derecha.

    Vive flotando sobre la tarjeta (``GripCard.set_grip`` la coloca) y solo
    manda el desplazamiento vertical; el ancho lo fija el layout. La usan las
    listas que se pueden estirar a mano: las de Migración y la de Carpetas.

    El widget mide 24×24 aunque las rayitas ocupen menos: es la diana mínima
    de WCAG 2.5.8 (con 14×14 había que apuntar con precisión de cirujano). El
    dibujo va en la esquina de abajo a la derecha, con ``INSET`` de aire para
    no pisar el borde redondeado de la tarjeta.
    """

    SIZE = 24
    INSET = 4

    def __init__(self, on_resize, tooltip: str = "", parent=None):
        super().__init__(parent)
        self._on_resize = on_resize
        self._last = None
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.SizeFDiagCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def paintEvent(self, event) -> None:
        # Tres rayitas diagonales, como el grip clásico; se encienden al pasar
        # el ratón para que se vea que se puede arrastrar.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(t.ACCENT if self.underMouse() else t.MUTED)
        painter.setPen(QPen(color, 1.4, Qt.SolidLine, Qt.RoundCap))
        edge = self.SIZE - 1 - self.INSET
        for offset in (4, 7, 10):
            painter.drawLine(edge - offset, edge, edge, edge - offset)
        painter.end()

    def enterEvent(self, event) -> None:
        # ``paintEvent`` mira ``underMouse()``, pero Qt no repinta solo al
        # entrar o salir el ratón: sin ``update()`` el realce no se veía.
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:
        self._last = event.globalPosition().y()

    def mouseMoveEvent(self, event) -> None:
        if self._last is None:
            return
        # Posición **global**: la esquinita se mueve con el panel al crecer, así
        # que la local daría un salto en cada fotograma.
        current = event.globalPosition().y()
        self._on_resize(int(current - self._last))
        self._last = current

    def mouseReleaseEvent(self, event) -> None:
        self._last = None


class FittedList:
    """Lista con alto fijo a su contenido (hasta un tope) y estirable con un asa.

    Va **fijo** a propósito. Dejándoselo negociar al layout, cuando la ventana
    se queda corta Qt reparte a la baja y encoge la lista hasta su mínimo (y
    con ella la tarjeta): quedaba media fila cortada justo encima de los
    botones, que parecían metidos dentro del listado. Con el alto fijo la
    tarjeta mide lo que tiene que medir y lo que no cabe lo resuelve el scroll
    de la página.

    El suelo son ``floor_rows`` filas enteras, medidas sobre una fila real (no
    una constante) para que siga valiendo si cambia la fuente; sin filas vale
    ``fallback``. ``window_height`` es una función que da el alto de la ventana:
    el asa no deja estirar la lista más allá de ella.

    ``cap`` es el tope del alto automático: un número fijo (Migración) o una
    **función** que mire el hueco libre actual (Carpetas, que crece con la
    pestaña). ``max_height``, si se pasa, limita además hasta dónde puede
    estirar el usuario; sin él, el tope del asa es ``window_height - 200``.
    """

    def __init__(self, scroll, layout, cap, floor_rows: int,
                 fallback: int, window_height, max_height=None):
        self.scroll = scroll
        self.layout = layout
        self.cap = cap
        self.floor_rows = floor_rows
        self.fallback = fallback
        self._window_height = window_height
        self._max_height = max_height
        # Alto elegido por el usuario con el asa; ``None`` es "el del contenido".
        self.chosen = None

    def _cap_height(self) -> int:
        """El tope del alto automático, resolviendo si ``cap`` es función."""
        return int(self.cap()) if callable(self.cap) else int(self.cap)

    def content_height(self) -> int:
        """Alto que pediría el contenido, sin topes.

        Se suman las filas una a una en vez de preguntar al cuerpo: su
        ``sizeHint`` es 0 hasta que el layout se asienta (un ciclo de eventos
        después de llenar la lista), así que medirlo ahí daba siempre el suelo
        y el panel abría aplastado. Las filas sí saben lo que miden en cuanto
        existen.
        """
        margins = self.layout.contentsMargins()
        total = margins.top() + margins.bottom()
        count = self.layout.count()
        for index in range(count):
            item = self.layout.itemAt(index)
            widget = item.widget()
            total += (widget.sizeHint().height() if widget is not None
                      else item.sizeHint().height())
        return total + self.layout.spacing() * max(0, count - 1)

    def floor(self) -> int:
        """Suelo: ``floor_rows`` filas enteras, medidas sobre la primera real."""
        for index in range(self.layout.count()):
            widget = self.layout.itemAt(index).widget()
            if widget is None:      # el hueco final (``addStretch``)
                continue
            widget.ensurePolished()
            row = widget.sizeHint().height() + self.layout.spacing()
            return max(self.fallback, self.floor_rows * row)
        return self.fallback

    def fit(self) -> None:
        """Fija el alto: el elegido con el asa o el del contenido, sobre el suelo."""
        height = self.chosen or min(self.content_height(), self._cap_height())
        self.scroll.setFixedHeight(max(self.floor(), int(height)))

    def resize(self, delta: int) -> None:
        """Arrastró el asa: el alto nuevo queda entre el suelo y su tope."""
        minimum = self.floor()
        maximum = (self._max_height() if self._max_height is not None
                   else self._window_height() - 200)
        maximum = max(minimum, maximum)
        wanted = self.scroll.height() + delta
        self.chosen = max(minimum, min(int(wanted), maximum))
        self.scroll.setFixedHeight(self.chosen)
