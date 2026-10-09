"""Widgets básicos reutilizables (versión PySide6).

El aspecto vive en ``ui/qss.py``; aquí está solo el comportamiento y los
puntos de enganche (``objectName`` y propiedades dinámicas) que el QSS usa. Un
botón nuevo se estiliza dándole un ``objectName`` y añadiendo su regla al QSS,
nunca con ``setStyleSheet`` en el código.

Todos los botones de aquí toman el foco **solo con el teclado**
(``Qt.TabFocus``, ver ``_keyboard_focus``): el QSS les pinta un aro mientras
tienen el foco, y con el foco por clic ese aro se quedaría encendido en el
último botón pulsado, que es justo lo que la web evita con ``:focus-visible``.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QPushButton

from ui import icons
from ui import theme as t


def _keyboard_focus(widget) -> None:
    """El widget toma el foco con Tab, no al hacer clic (ver el docstring)."""
    widget.setFocusPolicy(Qt.TabFocus)


class Pill(QPushButton):
    """Botón con forma de pastilla para filtros y selector de vista."""

    def __init__(self, text: str = "", tooltip: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Pill")
        _keyboard_focus(self)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


class SideButton(QPushButton):
    """Botón cuadrado de la barra lateral (tienda, instaladas, ajustes)."""

    def __init__(self, glyph: str = "", tooltip: str = "", parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("SideButton")
        _keyboard_focus(self)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


class CardButton(QPushButton):
    """Botón de acción dentro de las tarjetas (descargar, lanzar, borrar...).

    ``variant`` selecciona el color de relleno desde el QSS:
    ``neutral`` (gris), ``accent`` (azul primario) o ``danger`` (rojo).
    """

    def __init__(self, text: str = "", variant: str = "neutral",
                 tooltip: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("CardButton")
        _keyboard_focus(self)
        self.setProperty("variant", variant)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def set_variant(self, variant: str) -> None:
        """Cambia el color del botón y lo repinta."""
        self.setProperty("variant", variant)
        self.style().unpolish(self)
        self.style().polish(self)


class IconLinkButton(QPushButton):
    """Icono de información de una tarjeta (abre las notas de la versión).

    Diana de 24x24 (mínimo AA, WCAG 2.5.8): en la era Kivy medía 22 px.
    """

    def __init__(self, glyph: str, tooltip: str = "", parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("IconLink")
        _keyboard_focus(self)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


class StarButton(QPushButton):
    """Estrella de favorito (marca y desmarca).

    El glifo es el mismo en los dos estados; lo que cambia es el color, que lo
    pone el QSS según ``:checked`` (no tenemos la variante de contorno de la
    fuente: solo se empaqueta la sólida).
    """

    def __init__(self, marked: bool = False, tooltip_on: str = "",
                 tooltip_off: str = "", parent=None):
        super().__init__(icons.STAR, parent)
        self.setObjectName("StarButton")
        _keyboard_focus(self)
        self.setCheckable(True)
        self.setChecked(bool(marked))
        self.setCursor(Qt.PointingHandCursor)
        self._tooltip_on = tooltip_on
        self._tooltip_off = tooltip_off
        self._update_tooltip()
        self.toggled.connect(lambda _: self._update_tooltip())

    def _update_tooltip(self) -> None:
        self.setToolTip(self._tooltip_on if self.isChecked() else self._tooltip_off)


class IconFlatButton(QPushButton):
    """Botón de icono plano sin fondo (refrescar, etc.)."""

    def __init__(self, glyph: str, tooltip: str = "", parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("IconFlat")
        _keyboard_focus(self)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


class SwitchPill(QPushButton):
    """Interruptor de sí/no con forma de *toggle* (pista + bolita).

    Qt no trae un interruptor y un ``QCheckBox`` con ``::indicator`` no deja
    mover la bolita ni animarla desde el QSS, así que se pinta a mano. Sigue
    siendo un botón *checkable*: emite ``toggled`` y responde a clic y a teclado
    (Tab + Espacio) igual que antes, solo que ya no muestra "Sí"/"No" — el
    estado se ve por la posición de la bolita y el color de la pista.
    """

    WIDTH = 46
    HEIGHT = 24
    MARGIN = 3

    def __init__(self, checked: bool = False, tooltip: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Switch")
        _keyboard_focus(self)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setChecked(checked)
        if tooltip:
            self.setToolTip(tooltip)

    def _track_color(self) -> QColor:
        """Color de la pista según estado y hover."""
        if not self.isEnabled():
            # Apagado pero visible: no puede parecer que está encendido.
            return QColor(t.FIELD)
        if self.isChecked():
            return QColor(t.ACCENT_DARK if self.underMouse() else t.ACCENT)
        return QColor(t.BUTTON if self.underMouse() else t.SURFACE_ALT)

    def _edge_color(self) -> str:
        """Borde de la pista: lo que dibuja el interruptor sin encender.

        Sin encender, la pista gris (SURFACE_ALT) apenas se distingue de la
        tarjeta (1,2:1), y un control tiene que destacar 3:1 de su fondo (WCAG
        1.4.11). El borde en MUTED lo dibuja con 4,5:1 sin cambiar el relleno.
        """
        if self.isEnabled() and not self.isChecked():
            return t.MUTED
        return t.BORDER

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2
        painter.setPen(QPen(QColor(self._edge_color()), 1))
        painter.setBrush(self._track_color())
        painter.drawRoundedRect(rect, radius, radius)

        diameter = self.HEIGHT - 2 * self.MARGIN
        offset = (rect.width() - self.MARGIN - diameter) if self.isChecked() \
            else self.MARGIN
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t.TEXT_SEL if self.isEnabled() else t.MUTED))
        painter.drawEllipse(QRectF(rect.x() + offset, rect.y() + self.MARGIN,
                                   diameter, diameter))

        if self.hasFocus():
            # Aro de foco para quien navega con el teclado. En FOCUS_RING y no
            # en ACCENT: encendido, la pista ya es ACCENT y el aro no se veía.
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(t.FOCUS_RING), 2))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1),
                                    radius - 1, radius - 1)
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class CheckPill(QCheckBox):
    """Casilla de verificación con la palomita pintada a mano.

    Se probó a hacerlo con QSS (``::indicator`` + un SVG en ``image``), pero la
    regla global ``QWidget { background-color: ... }`` hereda sobre el indicador
    y Qt **descarta el image** (comprobado: 0 px de palomita con el mismo data
    URI que, aislado, sí pinta). Es el mismo problema que con ``SwitchPill``, así
    que se pinta con ``QPainter``: cuadro redondeado y, al marcar, un trazo de
    check con el color del texto seleccionado.

    Sigue siendo un ``QCheckBox`` normal: emite ``toggled``, responde a clic y a
    teclado (Tab + Espacio) y lleva el texto al lado.
    """

    BOX = 16          # lado del cuadro
    GAP = 8           # separación entre el cuadro y el texto
    RADIUS = 4

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        _keyboard_focus(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        box = self._box_rect()

        if self.isChecked() and self.isEnabled():
            fill = QColor(t.ACCENT_DARK if self.underMouse() else t.ACCENT)
            edge = fill
        elif not self.isEnabled():
            fill, edge = QColor(t.FIELD), QColor(t.BORDER)
        else:
            # Sin marcar, el borde es lo único que dibuja la casilla: en BORDER
            # se quedaba en 1,2:1 sobre la tarjeta y la casilla "desaparecía".
            # En MUTED pasa los 3:1 de un control (WCAG 1.4.11).
            fill = QColor(t.FIELD)
            edge = QColor(t.ACCENT if self.underMouse() else t.MUTED)
        painter.setPen(QPen(edge, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(box, self.RADIUS, self.RADIUS)

        if self.isChecked():
            self._draw_check(painter, box)

        if self.hasFocus():
            self._draw_focus(painter, box)

        # El texto (y solo el texto) con el color del QSS. ``QCheckBox`` no tiene
        # ``alignment()``, así que se pinta a la izquierda y centrado a mano.
        if self.text():
            text_rect = self.rect().adjusted(
                int(box.right()) + self.GAP, 0, 0, 0)
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(text_rect,
                             Qt.AlignLeft | Qt.AlignVCenter, self.text())
        painter.end()

    def _draw_focus(self, painter: QPainter, box: QRectF) -> None:
        """Aro de foco de teclado (como el de ``SwitchPill``).

        Con texto, el aro rodea el texto (la casilla va pegada al borde
        izquierdo y no deja sitio fuera); sin texto, es el propio cuadro el que
        se pinta con el borde del foco. Sin esto, quien navega con Tab no sabe
        en qué casilla está.
        """
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(t.FOCUS_RING), 1.5))
        if self.text():
            ring = QRectF(self.rect()).adjusted(
                box.right() + self.GAP / 2, 0.75, -0.75, -0.75)
            painter.drawRoundedRect(ring, self.RADIUS, self.RADIUS)
        else:
            painter.drawRoundedRect(box, self.RADIUS, self.RADIUS)

    def _box_rect(self) -> QRectF:
        """Cuadro centrado verticalmente, a la izquierda."""
        top = (self.height() - self.BOX) / 2
        return QRectF(0.5, top + 0.5, self.BOX - 1, self.BOX - 1)

    def _draw_check(self, painter: QPainter, box: QRectF) -> None:
        """Trazo de la palomita, dentro del cuadro."""
        painter.setPen(QPen(QColor(t.TEXT_SEL), 2.2, Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        left, top, width, height = (box.x(), box.y(), box.width(), box.height())
        points = [
            (left + width * 0.28, top + height * 0.52),
            (left + width * 0.44, top + height * 0.70),
            (left + width * 0.74, top + height * 0.30),
        ]
        for index in range(len(points) - 1):
            painter.drawLine(QRectF(points[index][0], points[index][1], 0, 0)
                             .topLeft(),
                             QRectF(points[index + 1][0], points[index + 1][1],
                                    0, 0).topLeft())

    def sizeHint(self):
        """Ancho = cuadro + hueco + texto, para que no se solape con la palomita."""
        hint = super().sizeHint()
        hint.setWidth(hint.width() + self.GAP)
        return hint
