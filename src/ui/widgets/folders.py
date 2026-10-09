"""Una fila de la biblioteca de carpetas (Ajustes ▸ Carpetas).

Cada fila es una carpeta: dónde está, qué tipos de compilación se descargan en
ella y si la aplicación puede escribir ahí. La lógica (quién se queda con qué,
dónde va cada descarga) vive en ``services.channels`` y ``services.settings``;
aquí solo está el dibujo y las señales.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from i18n import tr
from services import channels
from ui import icons
from ui.fonts import icon_font
from ui.widgets.buttons import CardButton, CheckPill
from ui.widgets.labels import ElidedLabel

# Etiqueta de cada tipo. Las cuatro de Blender ya existían traducidas para las
# pestañas de canal, así que se reutilizan tal cual (mismo nombre en los dos
# sitios: lo que el usuario ve en la pestaña "LTS" es lo que va a parar a la
# carpeta "LTS"). Los forks usan su nombre propio, que no se traduce.
TYPE_LABELS = {
    channels.TYPE_LTS: "LTS",
    channels.TYPE_STABLE: "Stable",
    channels.TYPE_DAILY: "Daily",
    channels.TYPE_EXPERIMENTAL: "Experimental",
    channels.TYPE_BFORARTISTS: "Bforartists",
    channels.TYPE_UPBGE: "UPBGE",
}

# Tooltips de las casillas. Cada uno dice qué son esas compilaciones y qué
# pasa al marcarlo, en varias líneas: un tooltip de una sola línea larga se
# sale de la pantalla y se vuelve ilegible.
TYPE_TOOLTIPS = {
    channels.TYPE_LTS: (
        "LTS: versions with two years of support, for work that has to keep\n"
        "opening years from now.\n"
        "Tick this and every LTS you download lands in this folder.\n"
        "Only one folder can take them."),
    channels.TYPE_STABLE: (
        "Stable: the normal releases of Blender, the ones most people use.\n"
        "Tick this and every stable release you download lands in this folder.\n"
        "Only one folder can take them."),
    channels.TYPE_DAILY: (
        "Daily: versions built every day from the branch in development.\n"
        "They bring the newest features and they can break; they also pile up\n"
        "fast, so many people keep them on a big drive.\n"
        "Only one folder can take them."),
    channels.TYPE_EXPERIMENTAL: (
        "Experimental: branches with features that are not in any release yet.\n"
        "Blender rarely publishes them, so this is usually empty.\n"
        "Only one folder can take them."),
    channels.TYPE_BFORARTISTS: (
        "Bforartists: a fork of Blender with a redesigned interface.\n"
        "Tick this and every Bforartists you download lands in this folder.\n"
        "Only one folder can take them."),
    channels.TYPE_UPBGE: (
        "UPBGE: the fork that keeps the Blender game engine alive.\n"
        "Tick this and every UPBGE you download lands in this folder.\n"
        "Only one folder can take them."),
}

WRITABLE_TIP = (
    "The lock is open: this folder can receive downloads, and the app may\n"
    "delete or rename the versions inside it.\n"
    "Click to close it.")
READ_ONLY_TIP = (
    "The lock is closed: the app never writes anything here.\n"
    "The versions inside are still listed, launched and used to migrate\n"
    "add-ons, but nothing is downloaded, deleted or renamed in this folder.\n"
    "Click to open it.")

# Cuántas filas se ven antes de que aparezca el scroll. Con 64 px por fila,
# cuatro es lo que cabe en la tarjeta con la ventana en su alto mínimo (540).
MAX_VISIBLE_ROWS = 4


class WriteToggle(CardButton):
    """El candado de una carpeta: abierto se escribe, cerrado no.

    Es un solo botón con dos estados (como ``StarButton``) y no dos controles,
    porque es una sola decisión. El cerrado se pinta en ámbar desde el QSS
    (propiedad dinámica ``writable``) para que se vea sin abrir el tooltip.
    """

    def __init__(self, writable: bool = True, parent=None):
        super().__init__("", "neutral", parent=parent)
        self.setCheckable(True)
        self.setFont(icon_font())    # 14 px, por QSS (``[writable]``)
        self.setFixedWidth(46)
        self.setChecked(writable)
        self._refresh()
        self.toggled.connect(lambda _value: self._refresh())

    def _refresh(self) -> None:
        writable = self.isChecked()
        self.setText(icons.LOCK_OPEN if writable else icons.LOCK)
        self.setToolTip(tr(WRITABLE_TIP if writable else READ_ONLY_TIP))
        # La propiedad la lee el QSS; hay que repintar a mano al cambiarla.
        self.setProperty("writable", "true" if writable else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class FolderRow(QFrame):
    """Fila de una carpeta: ruta, casillas de tipo, candado y papelera.

    Dos líneas y alto fijo. La ruta va en un ``ElidedLabel``: un ``QLabel``
    pelado pide como ancho mínimo el texto entero, y una ruta larga deformaría
    la tarjeta (es el fallo que ya llevó una tarjeta a 974 px en una ventana de
    900).
    """

    HEIGHT = 64

    type_toggled = Signal(str, str, bool)   # ruta, tipo, marcado
    writable_toggled = Signal(str, bool)    # ruta, escribible
    remove_requested = Signal(str)          # ruta

    def __init__(self, folder, zebra: bool = False, missing: bool = False,
                 types=None, parent=None):
        super().__init__(parent)
        self.setObjectName("FolderRow")
        # Sin esto el QSS no pinta el fondo de una **subclase** de QWidget
        # (la lección que costó el gris de MigrateView).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("zebra", "true" if zebra else "false")
        self.setProperty("missing", "true" if missing else "false")
        self.setFixedHeight(self.HEIGHT)
        self.path = folder.path

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 4, 8, 4)
        outer.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(8)
        glyph = QLabel(icons.FOLDER)
        glyph.setFont(icon_font())
        glyph.setObjectName("Muted")
        glyph.setFixedWidth(18)
        top.addWidget(glyph)

        self.path_label = ElidedLabel(folder.path, Qt.ElideMiddle)
        top.addWidget(self.path_label, 1)

        if missing:
            warning = QLabel(tr("Not found"))
            warning.setObjectName("Warning")
            warning.setToolTip(tr(
                "This folder is not reachable right now.\n"
                "It usually means the drive is unplugged or the network share\n"
                "is down. It is kept in the list so you do not lose its\n"
                "settings; its versions come back when the folder does."))
            top.addWidget(warning)

        self.write_toggle = WriteToggle(folder.writable)
        self.write_toggle.toggled.connect(
            lambda value: self.writable_toggled.emit(self.path, value))
        top.addWidget(self.write_toggle)

        remove = CardButton(icons.DELETE, variant="danger", tooltip=tr(
            "Stop using this folder.\n"
            "Nothing is deleted from disk: its Blender versions simply stop\n"
            "being listed, and you can add the folder again whenever you want.\n"
            "Any build types it was taking will need a new folder."))
        remove.setFont(icon_font())
        remove.setFixedWidth(46)
        remove.clicked.connect(lambda: self.remove_requested.emit(self.path))
        top.addWidget(remove)
        outer.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        bottom.setContentsMargins(26, 0, 0, 0)
        self.checks = {}
        # ``types`` son los tipos activos: los de Blender siempre y los de los
        # forks solo si el usuario los encendió. Sin eso, la carpeta pediría
        # una decisión sobre algo que no se puede descargar.
        for build_type in (types or channels.BUILD_TYPES):
            check = CheckPill(tr(TYPE_LABELS[build_type]))
            check.setToolTip(tr(TYPE_TOOLTIPS[build_type]))
            check.setChecked(build_type in folder.types)
            # Cerrar el candado apaga y deshabilita las casillas: no se puede
            # recibir una descarga donde la aplicación no escribe.
            check.setEnabled(folder.writable)
            check.toggled.connect(
                lambda value, key=build_type: self.type_toggled.emit(
                    self.path, key, value))
            self.checks[build_type] = check
            bottom.addWidget(check)
        bottom.addStretch()
        outer.addLayout(bottom)

    def set_folder(self, folder) -> None:
        """Repinta la fila con el estado nuevo, sin reemitir señales.

        Se actualiza la fila en vez de reconstruir la lista entera porque estos
        cambios nacen de los propios controles de la fila: destruirla desde su
        propia señal es la forma segura de que Qt se queje.
        """
        self.path = folder.path
        self.path_label.setText(folder.path)
        self.write_toggle.blockSignals(True)
        self.write_toggle.setChecked(folder.writable)
        self.write_toggle.blockSignals(False)
        for build_type, check in self.checks.items():
            check.blockSignals(True)
            check.setChecked(build_type in folder.types)
            check.setEnabled(folder.writable)
            check.blockSignals(False)
