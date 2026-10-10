"""Los mandos de la galería: botones de la esquina, puntero láser y teclado.

Es una parte de ``GalleryView`` (un *mixin*, como las de la ventana): trabaja
sobre el estado de la misma vista y no tiene sentido como objeto aparte. Se
separó de ``gallery.py`` cuando pasó del tope de 1000 líneas, por
responsabilidad: allí, colocar, mover y pintar las cartas; aquí, lo que el
usuario toca para manejarlas.

- Los tres botones de la esquina: estilo, puntero láser y silencio.
- El puntero láser, con su profundidad (más grande cerca, en escorzo sobre una
  carta girada, aplanado sobre el suelo) y su moteado animado.
- El teclado: ← → (pasar), ↑ (lanzar), Inicio/Fin, Intro y 1-4, estén donde
  estén el foco y el ratón (``eventFilter``).
"""

import math
import random

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QAbstractButton, QAbstractSpinBox, QApplication,
                               QComboBox, QLineEdit, QPushButton, QWidget)

from i18n import tr
from services.settings import GALLERY_STYLES as STYLES
from ui import icons
from ui.fonts import icon_font
from ui.widgets.gallery_art import LASER_FRAMES, laser_cursor
from ui.widgets.shell import CORNER_BUTTON_MARGIN, CORNER_BUTTON_SIZE

# Cada cuánto cambia el moteado del puntero láser (~12 por segundo: más rápido
# se ve como un parpadeo nervioso, más lento como un salto).
LASER_MS = 85

# La profundidad del láser: su tamaño sobre la carta central (la más cercana) y
# su (tamaño, escorzo) sobre el fondo, que es lo más lejano.
LASER_NEAR = 1.25
LASER_BACKGROUND = (0.8, 1.0, 1.0)
# Cuánto queda el horizonte del suelo por debajo de la mitad de la galería.
# Justo en la mitad, el suelo parecía llegar demasiado lejos.
FLOOR_DROP = 20

# Lo que dura un paso con la rueda del ratón: algo menos que con las flechas,
# porque la rueda se gira varias muescas seguidas.
WHEEL_MS = 320

# Los botones de la esquina: lado y separación del borde. El tamaño y el margen
# son los mismos que los de la rejilla (``shell``), para que el botón no baile
# al cambiar de vista.
MUTE_SIZE = CORNER_BUTTON_SIZE
MUTE_MARGIN = CORNER_BUTTON_MARGIN

# Lo que ocupan los tres botones de la esquina: la línea de atajos lo deja
# libre a los dos lados para no pasar por debajo y seguir centrada.
HINT_RESERVE = 3 * MUTE_SIZE + MUTE_MARGIN + 20

# Las teclas que maneja la galería (ver ``eventFilter``).
GALLERY_KEYS = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Home, Qt.Key_End,
                Qt.Key_Return, Qt.Key_Enter,
                Qt.Key_1, Qt.Key_2, Qt.Key_3, Qt.Key_4)

# Nombre visible de cada estilo (el texto es la clave de i18n), para el
# tooltip del botón de estilo de la esquina.
STYLE_LABELS = {
    "perspective": "Perspective",
    "shelf": "Shelf",
    "carousel": "Carousel",
    "showcase": "Showcase",
}


def polygon_area(polygon) -> float:
    """Área de un polígono (fórmula del cordón): lo que ocupa una carta en pantalla."""
    total = 0.0
    count = polygon.count()
    for i in range(count):
        a, b = polygon.at(i), polygon.at((i + 1) % count)
        total += a.x() * b.y() - b.x() * a.y()
    return total / 2


class GalleryControlsMixin:
    """Parte de ``GalleryView``; ver el docstring del módulo."""

    def _build_controls(self) -> None:
        # El puntero láser, con su moteado animado mientras el ratón está
        # encima (``enterEvent``/``leaveEvent``): fuera de la galería no gasta.
        self._laser_key = LASER_BACKGROUND
        self._laser_frame = 0
        self._laser_rng = random.Random()
        self._laser_timer = QTimer(self)
        self._laser_timer.setInterval(LASER_MS)
        self._laser_timer.timeout.connect(self._next_laser_frame)
        self.setCursor(self._laser_now())
        # El silencio, pequeño y fijo en la esquina de abajo a la derecha
        # (se recoloca en ``resizeEvent``). Lleva su propio cursor: el láser es
        # para las cartas, y sobre un botón se espera la manita.
        self.mute_button = QPushButton(self)
        self.mute_button.setObjectName("CornerButton")
        self.mute_button.setCheckable(True)
        self.mute_button.setFont(icon_font())
        self.mute_button.setFixedSize(MUTE_SIZE, MUTE_SIZE)
        self.mute_button.setCursor(Qt.PointingHandCursor)
        self.mute_button.setFocusPolicy(Qt.NoFocus)
        self.mute_button.toggled.connect(self._on_mute)
        self.set_muted(False)
        self.laser_button = QPushButton(self)
        self.laser_button.setObjectName("CornerButton")
        self.laser_button.setCheckable(True)
        self.laser_button.setFont(icon_font())
        self.laser_button.setFixedSize(MUTE_SIZE, MUTE_SIZE)
        self.laser_button.setCursor(Qt.PointingHandCursor)
        self.laser_button.setFocusPolicy(Qt.NoFocus)
        self.laser_button.toggled.connect(self._on_laser)
        self._laser_on = True
        self.set_laser(True)
        # El estilo se elige aquí mismo, como el modo de vista: cada pulsación
        # pasa al siguiente (y se recuerda el último, en los ajustes).
        self.style_button = QPushButton(icons.GALLERY_STYLE, self)
        self.style_button.setObjectName("CornerButton")
        self.style_button.setFont(icon_font())
        self.style_button.setFixedSize(MUTE_SIZE, MUTE_SIZE)
        self.style_button.setCursor(Qt.PointingHandCursor)
        self.style_button.setFocusPolicy(Qt.NoFocus)
        self.style_button.clicked.connect(
            lambda: self.style_requested.emit(self._next_style()))
        self._style_tooltip()
        # Las flechas pasan de carta aunque el foco esté en otro sitio (el
        # botón que se acaba de pulsar, la barra de canales...): ver
        # ``eventFilter``. El filtro se pone al enseñarse y se quita al
        # esconderse: si no, cada evento de la aplicación pasaría por Python.

    def _place_corner_buttons(self) -> None:
        """Los tres botones en la esquina de abajo a la derecha, encima de todo."""
        y = self.height() - MUTE_SIZE - MUTE_MARGIN
        for n, button in enumerate(reversed(self._corner_buttons())):
            button.move(self.width() - (n + 1) * MUTE_SIZE - MUTE_MARGIN - 6 * n, y)
            button.raise_()

    def _next_style(self) -> str:
        return STYLES[(STYLES.index(self.style_name) + 1) % len(STYLES)]

    def _style_tooltip(self) -> None:
        self.style_button.setToolTip(tr(
            "Gallery style: {name}\nClick for {next} (or press 1, 2, 3 or 4).",
            name=tr(STYLE_LABELS[self.style_name]),
            next=tr(STYLE_LABELS[self._next_style()])))

    def _corner_buttons(self) -> tuple:
        return (self.style_button, self.laser_button, self.mute_button)

    def set_muted(self, muted: bool) -> None:
        """Pone el botón de silencio en su estado, sin avisar a nadie."""
        self.mute_button.blockSignals(True)
        self.mute_button.setChecked(muted)
        self.mute_button.blockSignals(False)
        self._paint_mute(muted)

    def _on_mute(self, muted: bool) -> None:
        self._paint_mute(muted)
        self.mute_toggled.emit(muted)

    def set_laser(self, on: bool) -> None:
        """Enciende o apaga el puntero láser, sin avisar a nadie."""
        self.laser_button.blockSignals(True)
        self.laser_button.setChecked(on)
        self.laser_button.blockSignals(False)
        self._apply_laser(on)

    def _on_laser(self, on: bool) -> None:
        self._apply_laser(on)
        self.laser_toggled.emit(on)

    def _card_buttons_cursor(self) -> None:
        """Los botones de las cartas, con el láser o con la manita.

        Cada botón trae su propio cursor de manita, que manda sobre el de la
        galería: sobre "Lanzar" o la estrella el láser desaparecía. Con el
        láser encendido se les quita (heredan el de la galería) y con él
        apagado se les devuelve. Que se pueden pulsar lo sigue diciendo su
        resaltado al pasar por encima.
        """
        for item in self.items:
            for button in item.card.findChildren(QAbstractButton):
                if self._laser_on:
                    button.unsetCursor()
                else:
                    button.setCursor(Qt.PointingHandCursor)

    def _apply_laser(self, on: bool) -> None:
        self._laser_on = on
        self._card_buttons_cursor()
        self.laser_button.setText(icons.LASER_ON if on else icons.LASER_OFF)
        self.laser_button.setToolTip(tr("Turn off the laser pointer") if on
                                     else tr("Turn on the laser pointer"))
        if on:
            self.setCursor(self._laser_now())
            if self.underMouse():
                self._laser_timer.start()
        else:
            self._laser_timer.stop()
            self.unsetCursor()

    def _paint_mute(self, muted: bool) -> None:
        self.mute_button.setText(icons.SOUND_OFF if muted else icons.SOUND_ON)
        self.mute_button.setToolTip(tr("Turn the gallery sounds back on")
                                    if muted else tr("Mute the gallery sounds"))

    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)
        # Al entrar en la galería, si el foco estaba en un buscador vacío, pasa
        # a la galería: es lo que se va a manejar.
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit) and not focus.text():
            self.setFocus(Qt.OtherFocusReason)

    def hideEvent(self, event):
        super().hideEvent(event)
        QApplication.instance().removeEventFilter(self)
        self._laser_timer.stop()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._laser_on:
            self._laser_timer.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._laser_timer.stop()

    def _laser_now(self) -> QCursor:
        """El puntero de este fotograma, hecho al vuelo.

        La forma sigue un ciclo suave (``LASER_FRAMES`` pasos), pero el grano
        se sortea **nuevo en cada fotograma**: con un bucle de fotogramas ya
        hechos, el mismo moteado volvía cada 1,7 s y se reconocía el patrón.
        Fabricar un puntero cuesta menos de un milisegundo, así que hacerlo
        cada ``LASER_MS`` no se nota.
        """
        scale, squash, flatten = self._laser_key
        return laser_cursor(self._laser_frame / LASER_FRAMES,
                            seed=self._laser_rng.getrandbits(32), scale=scale,
                            squash=squash, flatten=flatten)

    def _laser_depth(self, point) -> tuple:
        """Tamaño y escorzo del punto láser sobre lo que hay bajo ``point``.

        Sale de la forma con que se ve la carta en pantalla: su área frente a
        la de la carta plana da lo cerca que está (más grande, más cerca) y la
        proporción ancho/alto frente a la plana, lo girada que va. Bajo la
        fila de cartas está el suelo (donde van los reflejos): un plano visto
        en ángulo, así que el punto se aplana en vertical, y más cuanto más al
        fondo (cuanto más cerca de las cartas), donde además se ve más pequeño.
        Fuera de todo eso, el fondo: lo más lejano. Se redondea a décimas para
        no fabricar un juego de fotogramas por cada píxel que se mueve el ratón.
        """
        for index, polygon in reversed(self._hits):
            if not polygon.containsPoint(point, Qt.OddEvenFill):
                continue
            h = self.items[index].card.height()
            box = polygon.boundingRect()
            area = abs(polygon_area(polygon))
            near = math.sqrt(area / max(1.0, self.card_width * h))
            squash = min(1.0, (box.width() / max(1.0, box.height()))
                         / (self.card_width / max(1.0, h)))
            scale = LASER_NEAR * near
            return (round(scale, 1), round(max(0.5, squash), 1), 1.0)
        floor = self._floor_top()
        if floor is not None and point.y() > floor:
            # 0 en el horizonte (al pie de las cartas), 1 en el borde de abajo.
            k = min(1.0, (point.y() - floor) / max(1.0, self.height() - floor))
            return (round(0.7 + 0.5 * k, 1), 1.0, round(0.35 + 0.35 * k, 1))
        return LASER_BACKGROUND

    def _floor_top(self):
        """Dónde empieza el suelo (el horizonte), o ``None`` si no hay suelo.

        Llega casi hasta la mitad de la galería (``FLOOR_DROP`` por debajo),
        aunque las cartas
        bajen más: el suelo sigue por detrás de ellas y asoma entre las
        laterales. Las cartas se miran antes (``_laser_depth``), así que encima
        de una manda la carta. Solo hay suelo en los estilos con reflejo; en
        los planos, lo de debajo de las cartas es fondo como el resto.
        """
        if self.style_name not in ("perspective", "carousel") or not self.items:
            return None
        h = self.items[self.target].card.height()
        return min(self._row_y(h) + h / 2, self.height() / 2 + FLOOR_DROP)

    def _aim_laser(self, point) -> None:
        if not self._laser_on:
            return
        key = self._laser_depth(point)
        if key != self._laser_key:
            self._laser_key = key
            self.setCursor(self._laser_now())

    def _next_laser_frame(self) -> None:
        self._laser_frame = (self._laser_frame + 1) % LASER_FRAMES
        self.setCursor(self._laser_now())

    def eventFilter(self, watched, event):
        """Las teclas de la galería, estén donde estén el foco y el ratón.

        Sin esto las flechas solo funcionaban tras pinchar en la galería: el
        foco se lo quedaba el botón de modo recién pulsado o la barra de
        canales, que usa las flechas para cambiar de pestaña. Se respeta a
        quien de verdad las necesita: el buscador, los desplegables y
        cualquier diálogo abierto. Intro sobre un botón de la propia carta
        pulsa ese botón, no el principal.
        """
        if (event.type() == QEvent.MouseMove and isinstance(watched, QWidget)
                and (watched is self or self.isAncestorOf(watched))):
            # El ratón sobre la carta central llega a sus hijos, no aquí: se
            # recoge en el filtro para que el láser sepa sobre qué está.
            self._aim_laser(self.mapFromGlobal(event.globalPosition().toPoint()))
            return False
        if (event.type() != QEvent.KeyPress or not self.isVisible()
                or event.key() not in GALLERY_KEYS
                or QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None
                or not self.window().isActiveWindow()):
            return False
        focus = QApplication.focusWidget()
        # El buscador vacío no las necesita (no hay texto por el que mover el
        # cursor), y es justo donde está el foco al arrancar la app sin que el
        # usuario lo haya tocado: dejárselas a él era que las flechas "no
        # hicieran nada". Con texto escrito, sí son suyas.
        if isinstance(focus, QLineEdit) and not focus.text():
            focus = None
        if isinstance(focus, (QLineEdit, QComboBox, QAbstractSpinBox)):
            return False
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and isinstance(focus, QAbstractButton) and self.isAncestorOf(focus)):
            return False
        self.keyPressEvent(event)
        return True

    def _press_primary(self) -> None:
        button = getattr(self._centre_card(), "primary_button", None)
        if button is not None and button.isEnabled():
            button.click()

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Left:
            self.go(self.target - 1)
        elif key == Qt.Key_Right:
            self.go(self.target + 1)
        elif key == Qt.Key_Home:
            self.go(0)
        elif key == Qt.Key_End:
            self.go(len(self.items) - 1)
        elif Qt.Key_1 <= key <= Qt.Key_4:
            self.style_requested.emit(STYLES[key - Qt.Key_1])
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            # La acción principal de la central: Descargar o Lanzar.
            self._press_primary()
        elif key == Qt.Key_Up:
            # ↑ lanza, y solo eso: en una versión de la nube no hace nada. Una
            # flecha se toca sin querer, y no debe empezar una descarga de
            # 300 MB (para eso está Intro).
            centre = self._centre_card()
            if centre is not None and centre.property("installed") == "true":
                self._press_primary()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta:
            self.go(self.target + (-1 if delta > 0 else 1), WHEEL_MS)
