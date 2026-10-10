"""Etiquetas que recortan el texto en vez de ensanchar su columna.

``QLabel`` pide como ancho mínimo el texto completo. En una tarjeta eso es una
trampa: el nombre de una carpeta instalada
(``blender-5.3.0-alpha+main.1fd06ddba680-linux.x86_64-release``) mide 483 px, así
que su tarjeta pedía 507 px mientras las de al lado se quedaban en 249 y la
columna de la rejilla salía más ancha que las demás. En modo lista, el meta con
la ruta pedía 666 px y la tarjeta entera 974 px en una ventana de 900.
"""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QLineEdit, QSizePolicy

from i18n import tr

# La sombra del texto: un negro suave desplazado un píxel, para que el título y
# el meta de una tarjeta de rejilla se lean sobre el brillo del efecto (ver
# ``tilt.py``). No es decorativa: sin ella, el reflejo que sigue al ratón lava
# las letras claras del centro de la tarjeta.
TEXT_SHADOW = QColor(0, 0, 0, 160)
TEXT_SHADOW_OFFSET = (1, 1)


class ElidedLabel(QLabel):
    """``QLabel`` que pinta el texto recortado con ``…`` cuando no cabe.

    - El texto completo sigue disponible en ``text()`` y en el **tooltip**, que
      solo se pone mientras se está recortando de verdad. Como el tooltip va en
      la propia etiqueta, aparece al pasar el ratón por encima del texto.
    - ``minimumSizeHint().width()`` es 0 y la política de tamaño es ``Ignored``:
      la etiqueta nunca pide más sitio del que hay, así que no deforma la
      rejilla.
    - El recorte se decide al pintar, con el ancho del momento: ``ElideMiddle``
      para nombres de fichero (conserva el principio y el final, que es lo que
      identifica: ``blender-5.3.0-alpha+main…x86_64-release``) y ``ElideRight``
      para textos donde lo importante va delante.
    """

    # Por defecto a la derecha, que es lo prudente para un texto cualquiera.
    _mode = Qt.ElideRight

    def __init__(self, text: str = "", mode=Qt.ElideRight, parent=None):
        super().__init__(text, parent)
        self._mode = mode
        self._shadow = False
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._update_tooltip()

    def set_text_shadow(self, on: bool) -> None:
        """Pinta una sombra negra suave bajo el texto (para leerlo sobre el brillo)."""
        if bool(on) != self._shadow:
            self._shadow = bool(on)
            self.update()

    def minimumSizeHint(self) -> QSize:
        """Alto el de siempre, ancho 0: el texto no manda sobre el layout."""
        hint = super().minimumSizeHint()
        return QSize(0, hint.height())

    def displayed_text(self) -> str:
        """Lo que se está viendo ahora mismo (recortado, si hace falta)."""
        return self.fontMetrics().elidedText(self.text(), self._mode,
                                             self.contentsRect().width())

    def is_elided(self) -> bool:
        """True si el texto no cabe y se está mostrando recortado."""
        return self.displayed_text() != self.text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Cambia el ancho (o el zoom, que rehace la tarjeta): el tooltip deja de
        # tener sentido en cuanto el nombre se ve entero.
        self._update_tooltip()

    def setText(self, text: str) -> None:  # noqa: N802 (API de Qt)
        super().setText(text)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        self.setToolTip(self.text() if self.is_elided() else "")

    def paintEvent(self, event):
        """Se pinta con el estilo, no con un ``QPainter`` a mano.

        Así siguen mandando el color del QSS (``Title``, ``Muted``, ``Info``...),
        también el de sus propiedades dinámicas (el ``[dim]`` de las versiones
        que no tienes), que es exactamente lo que hace un ``QLabel`` normal.
        """
        painter = QPainter(self)
        if self._shadow:
            # La sombra primero, un píxel abajo a la derecha; el texto encima la
            # tapa salvo por ese filo, que es lo que da el relieve.
            painter.setPen(TEXT_SHADOW)
            dx, dy = TEXT_SHADOW_OFFSET
            painter.drawText(self.contentsRect().translated(dx, dy),
                             int(self.alignment()), self.displayed_text())
        self.style().drawItemText(
            painter, self.contentsRect(), int(self.alignment()),
            self.palette(), self.isEnabled(), self.displayed_text(),
            self.foregroundRole())
        painter.end()


class _InlineEditor(QLineEdit):
    """Campo que se usa para renombrar; Escape avisa en vez de confirmar."""

    escaped = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.escaped.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class EditableLabel(ElidedLabel):
    """``ElidedLabel`` que se edita con doble clic (renombrar una instalada).

    Al confirmar (Enter, o perder el foco) emite ``renamed`` con el texto nuevo;
    con Escape se cancela. Aquí **no** se toca el texto original: quien escucha
    decide si el cambio vale (validación, disco) y refresca la tarjeta. Se usa
    solo en las tarjetas instaladas; el resto de etiquetas no son editables.
    """

    renamed = Signal(str)

    def __init__(self, text: str = "", mode=Qt.ElideRight, parent=None):
        # Antes de ``super().__init__``: el constructor base ya llama a
        # ``_update_tooltip``, que los consulta.
        self._editable = True
        self._reason = ""
        super().__init__(text, mode, parent)
        self._editor = None

    def set_editable(self, editable: bool, reason: str = "") -> None:
        """Permite o no renombrar; ``reason`` dice por qué no (en el tooltip).

        Una instalada en una carpeta de solo lectura no se puede renombrar: el
        doble clic no hace nada y el tooltip lo explica en vez de prometer
        "doble clic para renombrar".
        """
        self._editable = editable
        self._reason = reason
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        """Texto completo (si se recorta) + la pista de que se puede renombrar."""
        if self._editable:
            hint = tr("Double-click to rename")
        else:
            hint = self._reason
        full = self.text() if self.is_elided() else ""
        self.setToolTip("\n".join(part for part in (full, hint) if part))

    def mouseDoubleClickEvent(self, event):
        if self._editable:
            self.start_editing()
        event.accept()

    def start_editing(self) -> None:
        """Cambia la etiqueta por un campo de texto con el nombre actual."""
        if self._editor is not None or not self._editable:
            return
        editor = _InlineEditor(self)
        editor.setObjectName("InlineEdit")
        editor.setText(self.text())
        editor.setGeometry(self.rect())
        editor.selectAll()
        editor.show()
        editor.setFocus()
        editor.editingFinished.connect(self._commit)
        editor.escaped.connect(self._cancel)
        self._editor = editor

    def _commit(self) -> None:
        editor, self._editor = self._editor, None
        if editor is None:
            return
        name = editor.text().strip()
        editor.deleteLater()
        if name and name != self.text():
            self.renamed.emit(name)

    def _cancel(self) -> None:
        editor, self._editor = self._editor, None
        if editor is not None:
            editor.deleteLater()
