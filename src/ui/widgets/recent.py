"""Vista de los ``.blend`` abiertos recientemente, por versión de Blender.

Lee la lista que Blender deja en ``<config>/recent-files.txt`` (ver
``services/recent.py``) y la enseña agrupada por serie. Un clic abre el fichero
en la versión más nueva de su serie; el botón ``⋯`` deja elegir cualquier
versión instalada; el clic derecho revela el fichero en el explorador. Los que
ya no están en su ruta se enseñan apagados, para que se sepa que se movieron.

Abrir un fichero es **lanzar esa versión** con el fichero como argumento, así
que no se lanza desde aquí: se le pide a ``MainWindow`` (``open_file``), que es
quien sabe los argumentos de lanzamiento y si esa versión va con consola. Con
un ``Launcher`` propio, Recientes ignoraba los dos ajustes.

No es una pestaña de versiones, así que la fila de filtros y el buscador se
ocultan (lo decide ``MainWindow._set_view``).
"""

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from model.build import fork_label, fork_series
from services import opener
from services import recent as recent_service
from ui import icons
from ui import theme as t
from ui.fonts import icon_font
from ui.widgets.buttons import IconFlatButton
from ui.widgets.labels import ElidedLabel
from ui.widgets.layouts import clear_layout, list_scroll, muted_note
from ui.widgets.menus import card_menu


class _RecentRow(QFrame):
    """Fila de un fichero reciente: nombre, carpeta y abrir en una versión.

    Si el fichero ya no está (``missing``), la fila se ve apagada y no abre ni
    revela nada: solo cuenta que estuvo ahí.
    """

    def __init__(self, path, versions, on_open, on_reveal, missing=False,
                 parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.missing = missing
        self._versions = list(versions)
        self._on_open = on_open
        self._on_reveal = on_reveal
        self.setObjectName("RecentRow")
        self.setProperty("missing", "true" if missing else "false")
        if not missing:
            self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        lay.setSpacing(8)

        info = QVBoxLayout()
        info.setSpacing(1)
        name = ElidedLabel(self.path.name, Qt.ElideRight)
        if missing:
            # Apagado como la carpeta: el QSS de la fila no llega a la etiqueta
            # (la regla de QLabel tiene su propio color).
            name.setObjectName("Muted")
        info.addWidget(name)
        folder = ElidedLabel(str(self.path.parent), Qt.ElideMiddle)
        folder.setObjectName("Muted")
        info.addWidget(folder)
        lay.addLayout(info, 1)
        # El tooltip va en la fila entera (las etiquetas recortadas ponen el
        # suyo solo cuando no caben).
        self.setToolTip(
            tr("This file is no longer at that path (moved or deleted).")
            if missing else str(self.path))

        self._menu_btn = IconFlatButton(
            icons.ELLIPSIS, tr("Open in another version"))
        # Sin la fuente de iconos el glifo sale como un cuadrado (el tamaño,
        # 16 px, lo pone el QSS de ``#IconFlat``).
        self._menu_btn.setFont(icon_font())
        self._menu_btn.clicked.connect(self._show_versions)
        self._menu_btn.setEnabled(not missing)
        lay.addWidget(self._menu_btn)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        # Solo si se suelta dentro de la fila: arrastrar fuera y soltar no es
        # un clic (antes abría igual).
        inside = self.rect().contains(event.position().toPoint())
        if (event.button() == Qt.LeftButton and inside and self._versions
                and not self.missing):
            self._on_open(self.path, self._versions[0])

    def contextMenuEvent(self, event) -> None:
        if self.missing:
            return
        menu = card_menu(self)
        action = menu.addAction(tr("Open file location"))
        action.setToolTip(tr("Show the file in your file manager."))
        action.triggered.connect(lambda: self._on_reveal(self.path))
        menu.exec(event.globalPos())

    def _show_versions(self) -> None:
        menu = card_menu(self)
        if not self._versions:
            menu.addAction(tr("No installed versions.")).setEnabled(False)
        for entry in self._versions:
            action = menu.addAction(
                tr("Open in {program} {version}",
                   program=fork_label(getattr(entry, "fork", "")),
                   version=entry.version))
            action.setToolTip(tr("Open this file with that version instead of "
                                 "the newest one."))
            action.triggered.connect(
                lambda _=False, e=entry: self._on_open(self.path, e))
        menu.exec(self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height())))


class RecentView(QWidget):
    """Pantalla con los ficheros recientes agrupados por versión de Blender.

    ``open_file(entry, path) -> bool`` es quien lanza de verdad (lo pone
    ``MainWindow``): devuelve si Blender arrancó.
    """

    status_message = Signal(str)

    def __init__(self, open_file=None, parent=None):
        super().__init__(parent)
        # objectName para el QSS (fondo como las listas) y WA_StyledBackground
        # porque una SUBCLASE de QWidget no pinta el fondo del QSS sin él.
        self.setObjectName("RecentView")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.platform = ""
        self.installed = []
        self.open_file = open_file or (lambda entry, path: False)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Fila de cabecera de 44 px (como la de filtros de las listas): así esta
        # vista empieza a la misma altura que las demás al cambiar de pantalla.
        header = QFrame()
        header.setObjectName("Chrome")
        header.setFixedHeight(t.FILTERS_HEIGHT)
        head_lay = QHBoxLayout(header)
        head_lay.setContentsMargins(16, 4, 16, 4)
        title = QLabel(tr("Recent files"))
        head_lay.addWidget(title)
        head_lay.addStretch()
        # Blender reescribe la lista al abrir o guardar, y esta vista solo la
        # lee al entrar: el botón sirve para releerla sin salir y volver.
        self.refresh_btn = IconFlatButton(icons.REFRESH, tr(
            "Read the recent files again.\n"
            "Blender updates the list when you open or save a file."))
        self.refresh_btn.setFont(icon_font())
        self.refresh_btn.clicked.connect(self.refresh)
        head_lay.addWidget(self.refresh_btn)
        root.addWidget(header)

        self.scroll, self.body = list_scroll("RecentScroll", "RecentBody",
                                             spacing=6, margins=(14, 14, 14, 14))
        root.addWidget(self.scroll, 1)

    def set_system(self, platform: str) -> None:
        """Fija el SO de este equipo (de dónde cuelga la config de cada versión)."""
        self.platform = platform or ""

    def set_installed(self, installed) -> None:
        """Recibe las versiones instaladas y repinta la lista."""
        self.installed = list(installed or [])
        self.refresh()

    def refresh(self) -> None:
        """Vuelve a leer los recientes de cada serie y repinta la lista."""
        clear_layout(self.body)
        if not self.installed:
            self.body.addWidget(muted_note(tr("No installed Blender versions.")))
            return
        groups = recent_service.grouped(self.installed, self.platform)
        if not groups:
            self.body.addWidget(muted_note(tr("No recent files yet.")))
            return
        for group in groups:
            # Las versiones de esa serie **y de ese fork**: una serie 5.2 de
            # Bforartists no comparte recientes con la de Blender, aunque el
            # número coincida. Se compara por la serie de configuración
            # (``fork_series``), que es la que agrupó el servicio: en UPBGE
            # 0.53 la serie es 5.3 y ``minor_of`` daría 0.53.
            versions = [entry for entry in self.installed
                        if fork_series(getattr(entry, "fork", "") or "",
                                       getattr(entry, "version", "") or "")
                        == group.series
                        and (getattr(entry, "fork", "") or "") == group.fork]
            header = QLabel(tr("{name} {version}",
                               name=fork_label(group.fork),
                               version=group.version))
            header.setObjectName("Muted")
            self.body.addWidget(header)
            for item in group.files:
                self.body.addWidget(
                    _RecentRow(item.path, versions, self._open, self._reveal,
                               missing=item.missing))

    def _open(self, path, entry) -> None:
        """Abre el fichero en esa versión de Blender (vía ``open_file``)."""
        if entry is None or not getattr(entry, "executable", None):
            self.status_message.emit(tr("That version has no executable."))
            return
        if self.open_file(entry, Path(path)):
            self.status_message.emit(
                tr("Opening {name} in {program} {version}",
                   name=Path(path).name,
                   program=fork_label(getattr(entry, "fork", "")),
                   version=entry.version))

    def _reveal(self, path) -> None:
        if not opener.reveal(path):
            self.status_message.emit(
                tr("This file is no longer at that path (moved or deleted)."))
