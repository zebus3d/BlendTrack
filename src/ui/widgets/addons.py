"""Vista para gestionar los addons y extensiones de una versión instalada.

Lista lo que hay con su estado, activa/desactiva sin abrir Blender, instala
desde un ``.zip``/``.py``, enlaza una carpeta de desarrollo y borra. Todo el
trabajo de disco/Blender vive en ``services/addons``; aquí solo se pinta y se
lanzan las operaciones en un hilo.

No es una pestaña de compilaciones: la fila de filtros y el buscador de la
cabecera se ocultan (lo decide ``MainWindow._set_view``).
"""

import threading
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from model.build import fork_label
from services import addons as addons_service
from services import opener
from services.downloader import log as download_log
from ui import icons
from ui.fonts import icon_font
from ui.widgets.buttons import CardButton, ChevronCombo, IconFlatButton, SwitchPill
from ui.widgets.cards import settings_card
from ui.widgets.dialogs import confirm, show_error
from ui.widgets.labels import ElidedLabel
from ui.widgets.layouts import clear_layout, list_scroll, muted_note
from ui.widgets.menus import card_menu
from ui.widgets.migrate.common import (_entry_key, blocked_by_running,
                                       choice_label, newest_per_series)


# Filtros de tipo de la barra: (clave, etiqueta).
_TYPE_FILTERS = (
    ("", "All types"),
    (addons_service.EXTENSION, "Extensions"),
    (addons_service.LEGACY, "Add-ons (legacy)"),
)


class _AddonRow(QFrame):
    """Fila de un addon: nombre, tipo/versión, interruptor y acciones."""

    def __init__(self, state, on_toggle, on_open, on_delete, parent=None):
        super().__init__(parent)
        self.state = state
        self._on_toggle = on_toggle
        self._on_open = on_open
        self._on_delete = on_delete
        self.setObjectName("AddonRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title = ElidedLabel(state.name, Qt.ElideRight)
        title.setObjectName("Title")
        # El tooltip lleva el id técnico (el módulo con el que Blender lo
        # habilita): es lo que permite reconocerlo sin ambigüedad.
        title.setToolTip(f"{state.name}\n{state.module}")
        # OJO: el título va con factor de estirado. Un ``ElidedLabel`` tiene
        # ancho mínimo 0 y política ``Ignored``: si se añade sin estirar y luego
        # un ``addStretch``, se queda en 0 px y el nombre no se ve.
        title_row.addWidget(title, 1)
        info.addLayout(title_row)
        meta = ElidedLabel(self._meta_text(state), Qt.ElideRight)
        meta.setObjectName("Muted")
        info.addWidget(meta)
        lay.addLayout(info, 1)

        self.switch = SwitchPill(
            state.enabled,
            tooltip=tr("Enable or disable this add-on without opening Blender."))
        self.switch.toggled.connect(lambda on: self._on_toggle(self.state, on))
        lay.addWidget(self.switch)

        self._menu_btn = IconFlatButton(icons.ELLIPSIS,
                                        tr("More options"))
        self._menu_btn.setFont(icon_font())
        self._menu_btn.clicked.connect(self._show_menu_at_button)
        lay.addWidget(self._menu_btn)

    @staticmethod
    def _meta_text(state) -> str:
        kind = tr("Extension") if state.kind == addons_service.EXTENSION \
            else tr("Add-on")
        # La versión es **del addon**, no de Blender: antes salía como
        # "Blender 1.3.0" y parecía la versión de Blender que pedía.
        parts = [f"v{state.addon.version}"] if state.addon.version else []
        parts.append(kind)
        return "  ·  ".join(parts)

    def set_enabled(self, enabled: bool) -> None:
        """Fija el interruptor sin volver a disparar la acción."""
        self.switch.blockSignals(True)
        self.switch.setChecked(enabled)
        self.switch.blockSignals(False)
        self.state.enabled = enabled

    def contextMenuEvent(self, event) -> None:
        self._show_menu(event.globalPos())

    def _show_menu_at_button(self) -> None:
        """Abre el menú justo debajo del botón de los tres puntos.

        Tiene método propio y **no** se conecta ``clicked`` a ``_show_menu``:
        ``clicked`` emite su estado ``checked`` (un bool), que llegaba como
        ``position`` y acababa en ``menu.exec(False)`` -> ``TypeError``. Por
        eso el menú salía con el clic derecho (ahí llega un ``QPoint`` de
        verdad) y fallaba con el botón. ``_show_menu`` exige ahora la posición
        para que no pueda volver a pasar.
        """
        self._show_menu(self._menu_btn.mapToGlobal(
            QPoint(0, self._menu_btn.height())))

    def _show_menu(self, position) -> None:
        """Menú de acciones del addon en esa posición de pantalla."""
        menu = card_menu(self)
        open_action = menu.addAction(tr("Open file location"))
        open_action.setToolTip(tr("Show the file in your file manager."))
        open_action.triggered.connect(lambda: self._on_open(self.state))
        delete_action = menu.addAction(tr("Delete add-on"))
        delete_action.setToolTip(tr("Remove it from this Blender version."))
        delete_action.triggered.connect(lambda: self._on_delete(self.state))
        menu.exec(position)


class AddonsView(QWidget):
    """Pantalla de gestión de addons de la versión instalada elegida."""

    status_message = Signal(str)
    addons_loaded = Signal(object)   # {"version", "addons", "error"}
    action_done = Signal(object)     # {"action", "name", "result", "error"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AddonsView")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.platform = ""
        self.installed = []
        self._choices = []
        self.entry = None
        self.addons = []
        self._rows = []
        self._acting = False
        self._build_ui()
        self.addons_loaded.connect(self._on_addons_loaded)
        self.action_done.connect(self._on_action_done)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        header, header_lay = settings_card()
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Version")))
        self.version_combo = ChevronCombo()
        self.version_combo.setSizePolicy(QSizePolicy.Expanding,
                                         QSizePolicy.Fixed)
        self.version_combo.setMinimumWidth(150)
        self.version_combo.setToolTip(tr(
            "Version whose add-ons you are managing. Versions of the same "
            "series share their add-on folder."))
        self.version_combo.currentIndexChanged.connect(lambda _: self.read())
        row.addWidget(self.version_combo, 1)
        header_lay.addLayout(row)
        self.path_label = QLabel("")
        self.path_label.setObjectName("Muted")
        self.path_label.setWordWrap(True)
        self.path_label.setToolTip(tr(
            "Folder with the settings and add-ons of this version."))
        header_lay.addWidget(self.path_label)
        root.addWidget(header)

        card, lay = settings_card()
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search add-ons..."))
        self.search.setToolTip(tr("Filter the list by name or id."))
        self.search.textChanged.connect(lambda _: self._fill_rows())
        toolbar.addWidget(self.search, 1)
        self.type_combo = ChevronCombo()
        for key, label in _TYPE_FILTERS:
            self.type_combo.addItem(tr(label), key)
        self.type_combo.setToolTip(tr(
            "Show everything, only extensions, or only legacy add-ons."))
        self.type_combo.currentIndexChanged.connect(lambda _: self._fill_rows())
        toolbar.addWidget(self.type_combo)
        lay.addLayout(toolbar)

        actions = QHBoxLayout()
        self.install_btn = CardButton(
            tr("Install add-on"),
            tooltip=tr("Install an add-on or extension from a .zip or .py."))
        self.install_btn.clicked.connect(self.install)
        actions.addWidget(self.install_btn)
        self.link_btn = CardButton(
            tr("Link folder"),
            tooltip=tr("Link a development folder in place, so Blender loads "
                       "the add-on from your project."))
        self.link_btn.clicked.connect(self.link)
        actions.addWidget(self.link_btn)
        actions.addStretch()
        self.reload_btn = CardButton(tr("Read again"),
                                     tooltip=tr("Read the add-ons from Blender "
                                                "again."))
        self.reload_btn.clicked.connect(self.read)
        actions.addWidget(self.reload_btn)
        lay.addLayout(actions)

        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        self.scroll, self.rows = list_scroll("AddonsScroll", "AddonsBody")
        lay.addWidget(self.scroll, 1)
        root.addWidget(card, 1)

        self._set_enabled_controls(False)

    def _set_enabled_controls(self, enabled: bool) -> None:
        for widget in (self.install_btn, self.link_btn, self.reload_btn):
            widget.setEnabled(enabled)

    # -------------------------------------------------------------- origen
    def set_system(self, platform: str, arch: str) -> None:
        self.platform = platform or ""

    def set_installed(self, installed) -> None:
        """Rellena el selector con las versiones instaladas (una por serie).

        Una por ``(fork, serie)`` (``newest_per_series``): las de una misma
        serie comparten carpeta de addons, pero Bforartists y Blender no.
        """
        self.installed = list(installed or [])
        keep = _entry_key(self._selected())
        self._choices = newest_per_series(self.installed)
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        # Solo la versión (y el fork, si no es Blender): la etiqueta de arriba
        # ya dice "Versión" y el nombre de la carpeta repetía el número (misma
        # regla que en el selector de fábrica).
        for entry in self._choices:
            self.version_combo.addItem(choice_label(entry))
        index = next((i for i, entry in enumerate(self._choices)
                      if _entry_key(entry) == keep),
                     0 if self._choices else -1)
        self.version_combo.setCurrentIndex(index)
        self.version_combo.blockSignals(False)
        # No se lee aquí: leer arranca Blender. Lo pide quien entra en la vista
        # (``MainWindow.set_view``), para no lanzarlo al abrir la app.

    def _selected(self):
        index = self.version_combo.currentIndex()
        if 0 <= index < len(self._choices):
            return self._choices[index]
        return None

    # -------------------------------------------------------------- lectura
    def read(self) -> None:
        """Lee los addons de la versión elegida (en un hilo)."""
        entry = self._selected()
        self.entry = entry
        if entry is None:
            self.addons = []
            self.path_label.setText("")
            self._set_enabled_controls(False)
            self.status.setText(tr("No installed Blender versions."))
            self._fill_rows()
            return
        self.path_label.setText(str(getattr(entry, "path", "")))
        self._set_enabled_controls(False)
        self.status.setText(tr("Reading add-ons..."))

        platform = self.platform

        def worker():
            try:
                states = addons_service.list_addons(entry, platform)
            except Exception as error:  # noqa: BLE001
                self.addons_loaded.emit({"version": entry.version,
                                         "error": str(error)})
                return
            self.addons_loaded.emit({"version": entry.version,
                                     "addons": states})

        threading.Thread(target=worker, daemon=True).start()

    def _on_addons_loaded(self, payload) -> None:
        if self.entry is None or payload.get("version") != self.entry.version:
            return
        self._set_enabled_controls(True)
        error = payload.get("error")
        if error:
            # El motivo real va al registro y al tooltip: "no se pudieron
            # leer" a secas no dice si Blender no arranca, tardó o qué.
            download_log(f"addons: could not read {payload.get('version')}: "
                         f"{error}")
            self.addons = []
            self.status.setText(tr("Could not read the add-ons."))
            self.status.setToolTip(str(error))
            self._fill_rows()
            return
        self.addons = list(payload.get("addons") or [])
        self.status.setText(tr("{count} add-ons.", count=len(self.addons)))
        self.status.setToolTip("")
        self._fill_rows()

    def _fill_rows(self) -> None:
        clear_layout(self.rows)
        self._rows = []
        query = self.search.text().strip().lower()
        kind = self.type_combo.currentData()
        shown = [state for state in self.addons
                 if (not kind or state.kind == kind)
                 and (not query or query in state.name.lower()
                      or query in state.module.lower())]
        if not shown:
            self.rows.addWidget(muted_note(tr("No add-ons found.")))
            return
        for state in shown:
            row = _AddonRow(state, self._toggle, self._open, self._delete)
            self.rows.addWidget(row)
            self._rows.append(row)

    # ------------------------------------------------------------- acciones
    def _blocked(self) -> bool:
        """True si hay cualquier Blender abierto (ver ``blocked_by_running``)."""
        return blocked_by_running(self)

    def _toggle(self, state, enabled: bool) -> None:
        """El usuario movió el interruptor de un addon: aplicarlo en un hilo.

        Si no se puede (otra operación en marcha, sin versión, Blender
        abierto), el interruptor **vuelve** a donde estaba: el clic ya lo movió
        en pantalla, y dejarlo así diría que el addon cambió cuando no.
        """
        if self._acting or self.entry is None:
            self._refresh_row(state, not enabled)
            return
        if self._blocked():
            self._refresh_row(state, not enabled)
            return
        self._acting = True
        entry = self.entry

        def worker():
            try:
                addons_service.set_enabled(entry, state.module, enabled)
                self.action_done.emit({"action": "toggle", "name": state.name,
                                       "enabled": enabled, "state": state})
            except Exception as error:  # noqa: BLE001 - ver abajo
                self.action_done.emit({"action": "toggle", "name": state.name,
                                       "error": _reason_of(error)})

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_row(self, state, enabled: bool) -> None:
        for row in self._rows:
            if row.state is state:
                row.set_enabled(enabled)
                return

    def install(self) -> None:
        if self._acting or self.entry is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Install add-on"), "",
            tr("Add-ons (*.zip *.py)"))
        if not path:
            return
        if self._blocked():
            return
        self._acting = True
        entry, platform = self.entry, self.platform

        def worker():
            try:
                result = addons_service.install(entry, path, platform)
                self.action_done.emit({"action": "install",
                                       "name": Path(path).name,
                                       "module": result["module"]})
            except Exception as error:  # noqa: BLE001 - ver abajo
                self.action_done.emit({"action": "install",
                                       "name": Path(path).name,
                                       "error": _reason_of(error)})

        threading.Thread(target=worker, daemon=True).start()

    def link(self) -> None:
        if self._acting or self.entry is None:
            return
        folder = QFileDialog.getExistingDirectory(
            self, tr("Link a development folder"))
        if not folder:
            return
        if self._blocked():
            return
        self._acting = True
        entry, platform = self.entry, self.platform

        def worker():
            try:
                addons_service.link(entry, folder, platform)
                self.action_done.emit({"action": "link",
                                       "name": Path(folder).name})
            except Exception as error:  # noqa: BLE001 - ver abajo
                self.action_done.emit({"action": "link",
                                       "name": Path(folder).name,
                                       "error": _reason_of(error)})

        threading.Thread(target=worker, daemon=True).start()

    def _open(self, state) -> None:
        opener.open_path(state.addon.path)

    def _delete(self, state) -> None:
        if self._acting or self.entry is None:
            return
        if not confirm(self, tr("Delete add-on"),
                       tr("Delete {name}? Its files are removed from "
                          "{program} {version}.", name=state.name,
                          program=fork_label(getattr(self.entry, "fork", "")),
                          version=self.entry.version),
                       accept_text=tr("Delete"), danger=True):
            return
        if self._blocked():
            return
        self._acting = True
        entry = self.entry

        def worker():
            try:
                addons_service.remove(entry, state.addon, self.platform)
                self.action_done.emit({"action": "remove", "name": state.name})
            except Exception as error:  # noqa: BLE001 - ver abajo
                self.action_done.emit({"action": "remove", "name": state.name,
                                       "error": _reason_of(error)})

        threading.Thread(target=worker, daemon=True).start()

    def _on_action_done(self, payload) -> None:
        if not self._acting:
            return
        self._acting = False
        action = payload.get("action")
        name = payload.get("name") or ""
        if payload.get("error"):
            show_error(self, tr("Could not finish"), _error_text(payload))
            self.read()
            return
        if action == "toggle":
            self.status_message.emit(
                tr("Enabled {name}.", name=name) if payload.get("enabled")
                else tr("Disabled {name}.", name=name))
            return
        if action == "install":
            self.status_message.emit(tr("Installed {name}.", name=name))
        elif action == "link":
            self.status_message.emit(tr("Linked {name}.", name=name))
        elif action == "remove":
            self.status_message.emit(tr("Removed {name}.", name=name))
        self.read()


def _reason_of(error) -> str:
    """Clave del fallo para ``_error_text``.

    Los hilos de acción capturan **cualquier** excepción, no solo
    ``AddonError``: si una se escapaba, el hilo moría sin emitir nada,
    ``_acting`` se quedaba en ``True`` y la vista se moría en silencio (ni
    instalar, ni enlazar, ni borrar) hasta reiniciar la app. Un ``.zip``
    corrupto bastaba. Lo que no sea ``AddonError`` se cuenta como fallo
    genérico y el detalle va al registro.
    """
    if isinstance(error, addons_service.AddonError):
        return error.reason
    download_log(f"addons: {type(error).__name__}: {error}")
    return "failed"


def _error_text(payload) -> str:
    """Mensaje legible de un fallo del gestor de addons."""
    reason = payload.get("error")
    name = payload.get("name") or ""
    messages = {
        "no_executable": tr("That version has no executable."),
        "missing_file": tr("That file does not exist."),
        "missing_folder": tr("That folder does not exist."),
        "no_addon": tr("That folder is not an add-on (no manifest or "
                       "__init__.py)."),
        "unsupported": tr("Only .zip and .py files can be installed."),
        "broken_archive": tr("That file is not a valid .zip (it may be "
                             "corrupt or incomplete)."),
        "copy_failed": tr("Could not write to Blender's add-ons folder."),
        "symlink_failed": tr("Could not create the link (on Windows, "
                             "symbolic links need permission)."),
        "failed": tr("Blender could not apply the change."),
    }
    return messages.get(reason, str(reason)) + (f"\n\n{name}" if name else "")
