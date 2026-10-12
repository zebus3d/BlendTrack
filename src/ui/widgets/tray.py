"""Icono de la bandeja del sistema (system tray).

La bandeja no es universal: ``QSystemTrayIcon.isSystemTrayAvailable()`` devuelve
``False`` en escritorios que no la soportan (GNOME sin la extensión AppIndicator,
o el plugin *offscreen* de los tests). Por eso **todo** lo que oculta la ventana
consulta antes ``available()``: esconder la app sin un icono al que volver la
dejaría inaccesible, que es mucho peor que no tener bandeja.

El icono está **siempre visible** mientras la aplicación corre, tenga la ventana
abierta o escondida: así el usuario sabe que sigue ahí y puede volver a ella o
cerrarla de verdad desde el menú. (Antes solo se enseñaba mientras la ventana
estaba oculta; se cambió a petición de los usuarios.) El menú tiene solo dos
acciones —mostrar y salir de verdad— porque todo lo demás (tienda, ajustes...)
ya vive en la ventana.
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from i18n import tr
from paths import ASSETS_DIR


class TrayIcon(QObject):
    """Envuelve ``QSystemTrayIcon`` y su menú, y avisa por señales.

    No conoce la ventana: cuando el usuario pulsa el icono o una acción, emite
    ``restore_requested`` / ``quit_requested`` y es ``MainWindow`` quien decide
    (así el widget se puede probar sin montar toda la interfaz).
    """

    restore_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tray = QSystemTrayIcon(self)
        icon = QIcon(str(ASSETS_DIR / "images" / "app_icon.png"))
        if not icon.isNull():
            self._tray.setIcon(icon)
        self._tray.setToolTip(tr("BlendTrack"))

        # El menú contextual es lo que se ve al pulsar el icono en la mayoría
        # de escritorios. Se estiliza en ``qss.py`` (objectName TrayMenu): el
        # menú nativo claro desentona con el tema oscuro.
        self._menu = QMenu()
        self._menu.setObjectName("TrayMenu")
        # Qt no enseña los tooltips de las acciones si no se le pide.
        self._menu.setToolTipsVisible(True)
        self.show_action = self._menu.addAction(tr("Show"))
        self.show_action.setToolTip(tr("Bring the BlendTrack window back."))
        self.show_action.triggered.connect(lambda: self.restore_requested.emit())
        self._menu.addSeparator()
        self.quit_action = self._menu.addAction(tr("Quit"))
        self.quit_action.setToolTip(tr("Close BlendTrack completely."))
        self.quit_action.triggered.connect(lambda: self.quit_requested.emit())
        self._tray.setContextMenu(self._menu)

        self._tray.activated.connect(self._on_activated)

    @staticmethod
    def available() -> bool:
        """True si este escritorio puede mostrar un icono en la bandeja."""
        return QSystemTrayIcon.isSystemTrayAvailable()

    def is_visible(self) -> bool:
        """True si el icono está en la bandeja."""
        return self._tray.isVisible()

    def show(self) -> None:
        """Pone el icono en la bandeja."""
        self._tray.show()

    def hide(self) -> None:
        """Retira el icono."""
        self._tray.hide()

    def notify(self, title: str, text: str) -> None:
        """Globo/mensaje del icono (solo si el escritorio lo soporta)."""
        if not self._tray.supportsMessages():
            return
        self._tray.showMessage(title, text, QSystemTrayIcon.Information, 5000)

    def _on_activated(self, reason) -> None:
        """Clic o doble clic en el icono restauran la ventana.

        En macOS un clic suele abrir el menú, así que allí la acción fiable es
        la entrada "Mostrar" del propio menú.
        """
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore_requested.emit()
