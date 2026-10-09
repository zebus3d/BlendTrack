"""Diálogos con el aspecto de la aplicación (versión PySide6).

Sustituyen a ``ui/widgets/dialogs.py`` + ``views/dialogs.kv``. Qt trae
``QMessageBox`` y ``QDialog``, pero con el estilo claro/nativo del escritorio
desentonan con el tema oscuro; aquí heredamos de ``QDialog`` y los montamos con
nuestros propios botones (``CardButton``), que ya llevan el QSS de la app.
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from ui.widgets.buttons import CardButton


class AppDialog(QDialog):
    """Diálogo base: fondo SURFACE, título, cuerpo y fila de botones.

    El ancho es fijo (440 por defecto) para que todos los diálogos se vean
    iguales, pero el **alto** lo pone el contenido: un mensaje largo tiene que
    caber entero (ver ``resizeEvent``).
    """

    # Tope de ancho: un nombre de carpeta muy largo no puede sacar la ventana
    # fuera de la pantalla; a partir de aquí el texto se reparte en más líneas.
    MAX_WIDTH = 720

    def __init__(self, parent=None, title: str = "", message: str = "",
                 width: int = 440):
        super().__init__(parent)
        self.setWindowTitle(title or tr("Blender Downloads Manager"))
        self.setModal(True)
        self.setMinimumWidth(width)
        self.setMaximumWidth(self.MAX_WIDTH)
        # Etiquetas con texto que puede partirse en varias líneas: hay que
        # ajustarles el alto (ver resizeEvent).
        self._wrapped = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(12)

        if title:
            heading = QLabel(title)
            heading.setObjectName("DialogTitle")
            heading.setWordWrap(True)
            lay.addWidget(heading)
            self._wrapped.append(heading)

        # El cuerpo existe **siempre**, aunque nazca vacío (y entonces oculto):
        # hay diálogos que se crean sin mensaje y lo van contando después
        # (``ProgressDialog.set_text``, "Moviendo X..."). Si solo se creaba con
        # texto, ese ``set_text`` no tenía dónde escribir y no se veía nada.
        self.body_label = QLabel(message)
        self.body_label.setWordWrap(True)
        self.body_label.setVisible(bool(message))
        lay.addWidget(self.body_label)
        self._wrapped.append(self.body_label)

        self._buttons = QHBoxLayout()
        self._buttons.setSpacing(8)
        self._buttons.addStretch()
        lay.addLayout(self._buttons)

    def resizeEvent(self, event):
        """Ajusta el alto de las etiquetas que parten el texto en varias líneas.

        Qt calcula el alto de un ``QLabel`` con ``wordWrap`` **antes** de que el
        estilo aplique la fuente y con un ancho que luego cambia, así que el
        mensaje se cortaba: medido con el nombre largo de una instalada, la
        etiqueta se quedaba en 90 px cuando necesitaba 108 y se comía media
        primera línea y la última de la ruta. Aquí el diálogo ya conoce su ancho,
        así que se recalcula; y si se redimensiona, se vuelve a ajustar.
        """
        super().resizeEvent(event)
        for label in self._wrapped:
            height = label.heightForWidth(label.width())
            if height and label.minimumHeight() != height:
                label.setMinimumHeight(height)

    def add_button(self, text: str, variant: str = "neutral",
                   on_click=None, tooltip: str = "") -> CardButton:
        """Añade un botón a la fila inferior y lo devuelve."""
        button = CardButton(text, variant=variant, tooltip=tooltip)
        if on_click is not None:
            button.clicked.connect(on_click)
        self._buttons.addWidget(button)
        # El texto de un botón no se puede partir en líneas, así que no se puede
        # recortar: si la fila pide más ancho que el mínimo actual (y que el
        # tope, pensado para el texto del mensaje, que sí se ajusta), se sube el
        # ancho del diálogo. Sin esto, con muchos botones, Qt los encogía por
        # debajo de su texto y salía cortado ("Descargar como copia" ->
        # "argar como").
        needed = self._buttons.sizeHint().width() + 36  # márgenes izq+der
        if needed > self.minimumWidth():
            self.setMinimumWidth(needed)
        if needed > self.maximumWidth():
            self.setMaximumWidth(needed)
        return button


def confirm(parent, title: str, message: str, accept_text: str | None = None,
            danger: bool = False) -> bool:
    """Diálogo de confirmación. Devuelve True si el usuario acepta.

    ``accept_text`` es el texto del botón que continúa; por convención las
    acciones destructivas lo dicen con el verbo ("Desinstalar"), no con un
    "Aceptar" genérico. Con ``danger`` el botón va en rojo y avisa de que no
    hay vuelta atrás.
    """
    dialog = AppDialog(parent, title, message)
    dialog.add_button(tr("Cancel"), on_click=dialog.reject,
                      tooltip=tr("Do nothing."))
    dialog.add_button(accept_text or tr("Accept"),
                      variant="danger" if danger else "accent",
                      on_click=dialog.accept,
                      tooltip=tr("This cannot be undone.") if danger
                      else tr("Go ahead."))
    return dialog.exec() == QDialog.Accepted


def _notice(parent, title: str, message: str) -> None:
    """Aviso bloqueante con un solo botón de cerrar (base de error e info)."""
    dialog = AppDialog(parent, title, message)
    dialog.add_button(tr("Close"), variant="accent", on_click=dialog.accept,
                      tooltip=tr("Close this message."))
    dialog.exec()


def show_error(parent, title: str, message: str) -> None:
    """Aviso de que algo falló.

    Mismo diálogo que ``show_info``: el nombre dice la intención en quien lo
    llama; el aspecto lo distingue el título, no un icono (el tema oscuro no
    los usa).
    """
    _notice(parent, title, message)


def show_info(parent, title: str, message: str) -> None:
    """Aviso informativo (resultado de una operación)."""
    _notice(parent, title, message)


class ProgressDialog(AppDialog):
    """Diálogo con barra de progreso propia (descarga de actualizaciones)."""

    def __init__(self, parent=None, title: str = "", message: str = "",
                 primary_text: str | None = None,
                 secondary_text: str | None = None,
                 primary_tooltip: str = "",
                 secondary_tooltip: str = ""):
        """``primary_text``/``secondary_text`` cambian los botones.

        Por defecto son los de la actualización de la app, que es para lo que
        nació este diálogo. Pasar ``""`` como ``primary_text`` **no crea** el
        botón principal: lo usa el progreso de mover carpetas, donde la única
        acción posible mientras corre es cancelar.
        """
        super().__init__(parent, title, message)

        from PySide6.QtWidgets import QProgressBar

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(10)
        # Este se inserta antes de la fila de botones (que ya existe al final).
        layout = self.layout()
        layout.insertWidget(layout.count() - 1, self.progress)
        primary = tr("Update") if primary_text is None else primary_text
        secondary = tr("Later") if secondary_text is None else secondary_text
        self._primary = (self.add_button(
            primary, variant="accent",
            tooltip=primary_tooltip or tr("Go ahead.")) if primary else None)
        self._secondary = (self.add_button(
            secondary, tooltip=secondary_tooltip or tr("Do nothing."))
            if secondary else None)
        if self._secondary is not None:
            self._secondary.clicked.connect(self.reject)

    def set_progress(self, value: int) -> None:
        """Mueve la barra de progreso (0-100)."""
        self.progress.setValue(max(0, min(100, value)))

    def set_text(self, text: str) -> None:
        """Cambia el mensaje del diálogo, para ir contando qué pasa."""
        self.body_label.setText(text)
        self.body_label.setVisible(bool(text))


def update_available(parent, tag: str, on_update, on_skip=None) -> bool:
    """Diálogo de actualización disponible (de BlenderManager).

    ``on_update`` se llama si acepta; ``on_skip`` si elige no volver a avisar de
    esa versión concreta. El aviso automático la silencia; "Buscar ahora" la
    sigue mostrando. Devuelve True si se ha aceptado.
    """
    # En macOS no hay reemplazo en caliente del .app: prometer que se instala y
    # se reinicia sola sería mentir (``updater.apply`` solo revela el zip).
    if sys.platform == "darwin":
        note = tr("It will be downloaded. You will have to install it manually.")
    else:
        note = tr("It will be installed and the app will restart automatically.")
    message = (tr("A new version is available: {version}", version=tag)
               + "\n\n" + note)
    dialog = AppDialog(parent, tr("Update available"), message)
    choice = {"skip": False}
    dialog.add_button(tr("Later"), on_click=dialog.reject,
                      tooltip=tr("Ask me again another time."))
    if on_skip is not None:
        dialog.add_button(
            tr("Skip this version"),
            on_click=lambda: (choice.update(skip=True), dialog.reject()),
            tooltip=tr('Do not offer this exact version again.\n'
                       '"Check now" still shows it.'))
    dialog.add_button(tr("Update"), variant="accent", on_click=dialog.accept,
                      tooltip=tr("Download and install it now.\n"
                                 "The app restarts by itself."))
    accepted = dialog.exec() == QDialog.Accepted
    if accepted:
        on_update()
    elif choice["skip"] and on_skip is not None:
        on_skip()
    return accepted
