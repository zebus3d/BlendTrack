"""Widgets básicos reutilizables (versión PySide6).

El aspecto vive en ``ui/qss.py``; aquí está solo el comportamiento y los
puntos de enganche (``objectName`` y propiedades dinámicas) que el QSS usa. Un
botón nuevo se estiliza dándole un ``objectName`` y añadiendo su regla al QSS,
nunca con ``setStyleSheet`` en el código.

Todos los botones de aquí toman el foco **solo con el teclado**
(``Qt.TabFocus``, ver ``_keyboard_focus``) y pintan su aro de foco solo cuando
ese foco llegó con un Tab de verdad (``_FocusRing``): es el ``:focus-visible``
de la web. Con ``:focus`` a secas el aro salía sin tocar el teclado, porque Qt
también mueve el foco por su cuenta (al abrir la ventana o al esconder el
control que lo tenía) y lo dejaba en "Local" o en un interruptor de Ajustes.
"""

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QPushButton, QStyle,
                               QStyleOptionButton, QStylePainter, QToolTip)

from ui import icons
from ui import theme as t
from ui.fonts import icon_font
from ui.widgets import motion
from ui.widgets.layouts import repolish


def _keyboard_focus(widget) -> None:
    """El widget toma el foco con Tab, no al hacer clic (ver el docstring)."""
    widget.setFocusPolicy(Qt.TabFocus)


class _FocusRing:
    """Marca ``focusRing`` mientras el widget tiene un foco *de teclado*.

    Qt pone ``WA_KeyboardFocusChange`` en la ventana solo cuando el foco cambia
    por una tecla (Tab, Mayús+Tab, un atajo) y lo quita con el primer clic. El
    foco que Qt reparte solo (al mostrar la ventana, al esconder el control
    enfocado) llega sin esa marca, así que no enciende el aro. El QSS usa
    ``[focusRing="true"]`` y los que se pintan a mano, ``focus_ring()``.
    """

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._set_focus_ring(
            self.window().testAttribute(Qt.WA_KeyboardFocusChange))

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._set_focus_ring(False)

    def focus_ring(self) -> bool:
        """True si hay que pintar el aro de foco ahora."""
        return self.hasFocus() and bool(self.property("focusRing"))

    def _set_focus_ring(self, on: bool) -> None:
        if bool(self.property("focusRing")) != on:
            self.setProperty("focusRing", on)
            repolish(self)
        self.update()


class Pill(_FocusRing, QPushButton):
    """Botón con forma de pastilla para filtros y selector de vista."""

    def __init__(self, text: str = "", tooltip: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("Pill")
        _keyboard_focus(self)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


class SideButton(_FocusRing, QPushButton):
    """Botón cuadrado de la barra lateral (tienda, instaladas, ajustes)."""

    def __init__(self, glyph: str = "", tooltip: str = "", parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("SideButton")
        _keyboard_focus(self)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def keyPressEvent(self, event):
        """Las flechas no cambian de sección: se dejan pasar sin hacer nada.

        Por defecto, un ``QPushButton`` en un ``QButtonGroup`` mueve el foco con
        las flechas y **pulsa** el botón vecino, así que bajar con la flecha
        cambiaba de sección (y pintaba su aro de foco). Las flechas son para
        recorrer las tarjetas de la rejilla (``grid_keys``), no la barra.
        """
        if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            event.ignore()
            return
        super().keyPressEvent(event)


class CardButton(_FocusRing, QPushButton):
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
        repolish(self)


class IconLinkButton(_FocusRing, QPushButton):
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


class StarButton(_FocusRing, QPushButton):
    """Estrella de favorito (marca y desmarca).

    El glifo es el mismo en los dos estados; lo que cambia es el color, que lo
    pone el QSS según ``:checked`` (no tenemos la variante de contorno de la
    fuente: solo se empaqueta la sólida).

    Al marcarla hace "pop": la estrella salta, rebota y suelta unas chispas
    (``motion.play_pop``). Mientras dura, el glifo se pinta aquí a mano, porque
    el QSS fija el tamaño de la fuente (``font-size`` pisa ``setFont``) y no se
    puede agrandar de otra forma; fuera del pop pinta Qt como siempre.
    """

    _pop = 0.0

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
        # Solo al marcar: desmarcar no merece celebración.
        self.toggled.connect(lambda on: on and motion.play_pop(self))

    def _update_tooltip(self) -> None:
        self.setToolTip(self._tooltip_on if self.isChecked() else self._tooltip_off)

    def paintEvent(self, event):
        if not 0.0 < self._pop < 1.0:
            super().paintEvent(event)
            return
        # El botón (fondo y aro de foco) lo sigue pintando el estilo, pero sin
        # texto: el glifo va encima, escalado alrededor del centro.
        painter = QStylePainter(self)
        try:
            self._paint_pop(painter)
        finally:
            # Se cierra pase lo que pase: una excepción con el painter abierto
            # dejaba la ventana entera sin poder pintar ("one painter at a
            # time", "endPaint() called with active painter") y acababa en
            # violación de segmento.
            painter.end()

    def _paint_pop(self, painter) -> None:
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter.drawControl(QStyle.CE_PushButton, option)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # El color del QSS (``:checked`` → ámbar) no llega a la paleta del
        # widget: con ``palette()`` salía gris. Solo se pinta aquí al marcar,
        # así que es el de ``QPushButton#StarButton:checked``.
        color = QColor(t.WARNING)
        center = QRectF(self.rect()).center()
        scale = motion.star_pop_scale(self._pop)
        painter.save()
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        painter.setPen(color)
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        painter.restore()
        # Las chispas van encima: debajo, el glifo agrandado las tapaba.
        motion.star_burst(painter, center, self._pop, color)


class IconFlatButton(_FocusRing, QPushButton):
    """Botón de icono plano sin fondo (refrescar, etc.)."""

    def __init__(self, glyph: str, tooltip: str = "", parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("IconFlat")
        _keyboard_focus(self)
        self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)


class SwitchPill(_FocusRing, QPushButton):
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

        if self.focus_ring():
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


class CheckPill(_FocusRing, QCheckBox):
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

        if self.focus_ring():
            self._draw_focus(painter, box)

        # El texto (y solo el texto) con el color del QSS. ``QCheckBox`` no tiene
        # ``alignment()``, así que se pinta a la izquierda y centrado a mano.
        # Recortado con "…" si no cabe: en las filas de "Preferencias en
        # detalle" la columna tiene ancho fijo y el texto se salía por encima
        # del valor de al lado.
        if self.text():
            painter.setPen(self.palette().color(self.foregroundRole()))
            painter.drawText(self._text_rect(), Qt.AlignLeft | Qt.AlignVCenter,
                             self.displayed_text())
        painter.end()

    def _text_rect(self):
        """Hueco del texto: a la derecha del cuadro y su separación."""
        return self.rect().adjusted(
            int(self._box_rect().right()) + self.GAP, 0, 0, 0)

    def displayed_text(self) -> str:
        """El texto tal como se pinta ahora (con "…" si no cabe)."""
        return self.fontMetrics().elidedText(self.text(), Qt.ElideRight,
                                             self._text_rect().width())

    def event(self, event):
        """Con el texto recortado, el tooltip empieza por el texto entero.

        No se sustituye el tooltip que tuviera (suele explicar para qué es la
        casilla): se le pone delante lo que no se ve, como hace ``ElidedLabel``.
        """
        if (event.type() == QEvent.ToolTip and self.text()
                and self.displayed_text() != self.text()):
            tip = self.toolTip()
            QToolTip.showText(event.globalPos(),
                              f"{self.text()}\n\n{tip}" if tip else self.text(),
                              self)
            return True
        return super().event(event)

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


class ChevronCombo(QComboBox):
    """Desplegable con una flecha hacia abajo pintada a la derecha.

    Qt **deja de dibujar la flecha** del ``QComboBox`` en cuanto el
    ``::drop-down`` va estilado por QSS (y el truco del triángulo con bordes
    sale un bloque relleno, medido), así que el control parecía un campo de
    texto y no se sabía que se podía desplegar. Se pinta el glifo de chevron con
    la fuente de iconos: nítido en HiDPI y del color del tema.
    """

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setFont(icon_font(12))
            painter.setPen(QColor(t.MUTED))
            # Un poco a la izquierda del borde: dentro del hueco del
            # desplegable (``::drop-down``, 18 px) y sin pegarse al filo.
            painter.drawText(self.rect().adjusted(0, 0, -7, 0),
                             Qt.AlignRight | Qt.AlignVCenter,
                             icons.CHEVRON_DOWN)
        finally:
            painter.end()
