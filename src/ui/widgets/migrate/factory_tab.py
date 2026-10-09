"""Pestaña de valores de fábrica: arrancar limpio sin perder los ajustes.

Restablecer una versión aparta su configuración en un guardado con fecha, y
desde aquí se ve qué trae cada guardado, se restaura o se borra. No migra
nada: es la operación inversa, dejar una versión como recién instalada.

Es un **mixin** de ``MigrateView``. Apartar, listar y restaurar guardados está
en ``services/blender_config.py``; leer qué ajustes trae cada uno, en
``services/blender_prefs.py`` (arranca Blender, así que va en un hilo).
"""

import threading
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QSizePolicy, QVBoxLayout, QWidget)

from i18n import tr
from model.build import fork_label, human_size
from services import blender_config as bc
from services import blender_snapshots as bsnap
from services import blender_prefs as bprefs
from ui.widgets.buttons import CardButton
from ui.widgets.cards import settings_card
from ui.widgets import dialogs
from ui.widgets.layouts import (CornerGrip, FittedList, clear_layout,
                                list_scroll)
from ui.widgets.migrate.common import (SNAPSHOT_MIN_HEIGHT,
                                       SNAPSHOT_MIN_ROWS,
                                       SNAPSHOT_SCROLL_HEIGHT, _entry_info,
                                       _entry_key, _make_scroll,
                                       _section_names)
from ui.widgets.migrate.prefs_tab import _PrefRow


def _snapshot_origin(snapshot) -> str:
    """Qué es ese guardado, en una frase (según la etiqueta del nombre).

    ``factory`` es la config limpia que se aparcó al restaurar; ``vX.Y.Z`` son
    los ajustes del usuario que se apartaron al restablecer esa versión. Sin la
    frase, dos carpetas con fechas distintas no dicen cuál es cuál.
    """
    label = bsnap.snapshot_label(snapshot)
    if label == "factory":
        return tr("Clean settings replaced by a restore")
    if label.startswith("v"):
        return tr("Your settings saved when resetting Blender {version}",
                  version=label[1:])
    return tr("Saved settings")

def _snapshot_files_text(details) -> str:
    """Qué trae el guardado, además de su userpref (barato, sin arrancar nada)."""
    parts = []
    if details.get("has_userpref"):
        parts.append(tr("preferences"))
    if details.get("has_startup"):
        parts.append(tr("startup"))
    if details.get("bookmarks"):
        parts.append(tr("bookmarks ({count})", count=details["bookmarks"]))
    if details.get("recent"):
        parts.append(tr("recent files ({count})", count=details["recent"]))
    if details.get("total"):
        parts.append(human_size(int(details["total"])))
    return "  ·  ".join(parts) or tr("Empty")

class _SnapshotRow(QFrame):
    """Fila del gestor de guardados: qué es, qué trae y qué hacer con él.

    Cada acción va en su botón (ver, restaurar, borrar), así que no hay que
    seleccionar una y luego buscar el botón: es un gestor, no una lista de
    opciones.
    """

    def __init__(self, snapshot, details, on_restore, on_delete, on_details,
                 parent=None):
        super().__init__(parent)
        self.snapshot = Path(snapshot)
        # Preferencias leídas de este guardado (o None mientras se analiza).
        self.analysis = None
        self.setObjectName("SnapshotRow")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        date = bsnap.snapshot_date(self.snapshot) or self.snapshot.name
        origin = _snapshot_origin(self.snapshot)
        title = QLabel(f"{date}  ·  {origin}")
        title.setToolTip(tr("Saved on {date}. {origin}", date=date,
                            origin=origin))
        title_row.addWidget(title)
        self.badge = QLabel("")
        self.badge.setObjectName("Info")
        title_row.addWidget(self.badge)
        title_row.addStretch()
        info.addLayout(title_row)

        files = QLabel(_snapshot_files_text(details))
        files.setObjectName("Muted")
        files.setWordWrap(True)
        info.addWidget(files)

        self.analysis_label = QLabel("")
        self.analysis_label.setObjectName("Muted")
        self.analysis_label.setWordWrap(True)
        info.addWidget(self.analysis_label)
        lay.addLayout(info, 1)

        self.details_btn = CardButton(
            tr("View settings"),
            tooltip=tr("See the settings this saved copy changes from "
                       "Blender's defaults."))
        self.details_btn.clicked.connect(lambda: on_details(self.snapshot))
        self.details_btn.setEnabled(False)
        lay.addWidget(self.details_btn)

        self.restore_btn = CardButton(
            tr("Restore"), variant="accent",
            tooltip=tr("Put these settings back in Blender."))
        self.restore_btn.clicked.connect(lambda: on_restore(self.snapshot))
        self.restore_btn.setEnabled(bool(details.get("has_userpref")))
        lay.addWidget(self.restore_btn)

        delete = CardButton(tr("Delete"), variant="danger",
                            tooltip=tr("Delete this saved copy for good."))
        delete.clicked.connect(lambda: on_delete(self.snapshot))
        lay.addWidget(delete)

    def add_badge(self, text: str) -> None:
        """Añade una insignia ("más reciente", "más completo") a la fila."""
        current = self.badge.text()
        self.badge.setText(f"{current} · {text}" if current else text)

    def set_unreadable(self) -> None:
        """No se pudo leer ese guardado (Blender falló o no está)."""
        self.analysis = None
        self.analysis_label.setText(
            tr("Could not read this copy's settings."))

    def set_analysis(self, preferences) -> None:
        """Pinta el resumen del análisis (o "Analizando…" si es ``None``)."""
        self.analysis = preferences
        if preferences is None:
            self.analysis_label.setText(tr("Analyzing its settings..."))
            return
        self.details_btn.setEnabled(True)
        if not preferences:
            self.analysis_label.setText(
                tr("No settings changed from Blender's defaults"))
            return
        text = tr("{count} settings changed from Blender's defaults",
                  count=len(preferences))
        sections = _section_names([pref.path for pref in preferences])
        if sections:
            text += "  ·  " + " · ".join(sections)
        self.analysis_label.setText(text)

def _show_snapshot_details(parent, snapshot, preferences) -> None:
    """Diálogo con los ajustes que cambia ese guardado, uno por línea.

    En un scroll: un guardado puede traer decenas de claves y el diálogo no
    puede crecer hasta salirse de la pantalla.
    """
    date = bsnap.snapshot_date(snapshot) or Path(snapshot).name
    dialog = dialogs.AppDialog(parent, tr("Saved settings of {date}", date=date),
                       tr("These are the settings this copy changes from "
                          "Blender's defaults."))
    scroll, body_lay = list_scroll("SnapshotDetails", "SnapshotDetailsBody",
                                   align_top=False)
    scroll.setMaximumHeight(360)
    if not preferences:
        empty = QLabel(tr("No settings changed from Blender's defaults"))
        empty.setObjectName("Muted")
        body_lay.addWidget(empty)
    else:
        rows = []
        for section, items in bprefs.group_by_section(preferences):
            head = QLabel(tr(bprefs.section_label(section)))
            head.setObjectName("Muted")
            body_lay.addWidget(head)
            for pref in items:
                rows.append(_PrefRow(pref, checkable=False))
                body_lay.addWidget(rows[-1])
        _PrefRow.align_columns(rows)
    body_lay.addStretch()
    layout = dialog.layout()
    layout.insertWidget(layout.count() - 1, scroll)
    dialog.add_button(tr("Close"), variant="accent", on_click=dialog.accept,
                      tooltip=tr("Close this message."))
    dialog.exec()


class FactoryTabMixin:
    """Parte de ``MigrateView``; ver el docstring del módulo."""

    def _build_factory_header(self) -> QWidget:
        """Cabecera de la pestaña de fábrica: una sola versión.

        No es la barra origen → destino: en fábrica no hay copia, solo una
        versión que se restablece (o cuyos ajustes guardados se recuperan). Con
        dos desplegables parecía que la operación usaba el "Hacia" y el "Desde"
        a la vez, y el usuario no sabía cuál mandaba.
        """
        header = QWidget()
        header.setObjectName("MigrateHeader")
        lay = QVBoxLayout(header)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        card, card_lay = settings_card()
        card.setToolTip(tr(
            "Pick the version whose settings you want to reset or put back."))
        column = QVBoxLayout()
        column.setSpacing(4)
        label = QLabel(tr("Version"))
        label.setToolTip(tr("The version whose settings are reset or restored."))
        column.addWidget(label)
        self.factory_combo = QComboBox()
        self.factory_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.factory_combo.setMinimumWidth(150)
        self.factory_combo.setToolTip(tr(
            "The version whose settings are reset or restored."))
        self.factory_combo.currentIndexChanged.connect(
            lambda _: self._on_factory_version())
        column.addWidget(self.factory_combo)
        self.factory_path_label = QLabel("")
        self.factory_path_label.setWordWrap(True)
        self.factory_path_label.setToolTip(tr(
            "Folder with the settings of this version. This is the one that "
            "gets reset or put back."))
        column.addWidget(self.factory_path_label)
        card_lay.addLayout(column)
        lay.addWidget(card)

        self.factory_warning = QLabel("")
        self.factory_warning.setObjectName("Danger")
        self.factory_warning.setWordWrap(True)
        self.factory_warning.setVisible(False)
        lay.addWidget(self.factory_warning)
        return header

    def _on_factory_version(self) -> None:
        """Cambió la versión de la pestaña de fábrica: refresca su estado.

        Se tira el análisis y el resumen porque son de otra versión: los
        guardados que se enseñan ahora son otros.
        """
        self._forget_analysis()
        self._factory_live_count = None
        self._fill_factory()
        self._check_running(self._factory_entry(), self.factory_warning)

    def _build_factory_page(self) -> QWidget:
        """Pestaña de valores de fábrica: reset, recuperar y borrar.

        Va en su propia pestaña porque es una operación distinta (no migra
        nada; deja la versión destino limpia) y así no se mezcla con la
        migración de ajustes. El reset es reversible desde aquí mismo.
        """
        page, lay = self._new_page()
        lay.addWidget(self._build_factory_card())
        lay.addStretch()
        # ``factory_page`` es la página de dentro: es quien recibe la cabecera.
        self.factory_page = page
        return self._scrollable(page)

    def _build_factory_card(self) -> QFrame:
        """Tarjeta-gestor de los ajustes guardados de esa versión.

        Cada guardado es una fila con su fecha, de qué es, qué trae y qué
        ajustes cambia (eso último se lee en segundo plano), con botones para
        verlos, restaurarlo o borrarlo. El reset sigue siendo una acción aparte.
        """
        # Sin título dentro: la pestaña ya se llama "Factory settings". El
        # texto se rellena en ``_fill_factory`` porque nombra la versión
        # destino, que el usuario puede cambiar en la barra de arriba.
        card, lay = settings_card()
        self.factory_hint = QLabel("")
        self.factory_hint.setWordWrap(True)
        lay.addWidget(self.factory_hint)

        # Para qué sirve esto. Sin decirlo, "restablecer" suena a botón
        # destructivo que nadie toca; y es justo lo contrario: la forma más
        # rápida de saber si un problema es de Blender o de tu configuración.
        why = QLabel(tr(
            "Useful when something misbehaves and you want to find out why: "
            "if the problem disappears on a clean Blender, it comes from your "
            "settings or add-ons, not from Blender itself. From there you put "
            "your settings back and enable things one at a time until it "
            "breaks again. It is also the fair way to report a bug, and a way "
            "to record a tutorial with the interface everyone else sees. "
            "Nothing is lost: your settings are saved aside and go back with "
            "one click."))
        why.setWordWrap(True)
        why.setObjectName("Muted")
        lay.addWidget(why)

        # Resumen: cuántos guardados hay y en qué estado está la config viva.
        self.factory_status = QLabel("")
        self.factory_status.setToolTip(tr(
            "How many saved copies this version has and what state its "
            "live settings are in."))
        self.factory_status.setWordWrap(True)
        lay.addWidget(self.factory_status)

        # La lista, en scroll: puede haber muchos guardados.
        self.snapshot_scroll = _make_scroll("SnapshotList",
                                            SNAPSHOT_SCROLL_HEIGHT,
                                            SNAPSHOT_MIN_HEIGHT)
        body = QWidget()
        body.setObjectName("SnapshotListBody")
        self.snapshot_rows = QVBoxLayout(body)
        self.snapshot_rows.setContentsMargins(0, 0, 0, 0)
        self.snapshot_rows.setSpacing(6)
        self.snapshot_rows.addStretch()
        self.snapshot_scroll.setWidget(body)
        self.snapshot_list = FittedList(
            self.snapshot_scroll, self.snapshot_rows, SNAPSHOT_SCROLL_HEIGHT,
            SNAPSHOT_MIN_ROWS, SNAPSHOT_MIN_HEIGHT, self.height)
        self.snapshot_scroll.setVisible(False)
        lay.addWidget(self.snapshot_scroll)

        self.factory_empty = QLabel(tr(
            "No saved settings. Resetting will keep nothing to go back to."))
        self.factory_empty.setObjectName("Muted")
        self.factory_empty.setWordWrap(True)
        lay.addWidget(self.factory_empty)

        row = QHBoxLayout()
        reset = CardButton(tr("Reset to factory settings"),
                           tooltip=tr("Save the current settings aside and "
                                      "start clean."))
        reset.clicked.connect(self.reset_to_factory)
        row.addWidget(reset)
        row.addStretch()
        self.delete_all_btn = CardButton(
            tr("Delete all saved settings"), variant="danger",
            tooltip=tr("Delete every saved copy of this version for good."))
        self.delete_all_btn.clicked.connect(self.delete_all_snapshots)
        row.addWidget(self.delete_all_btn)
        lay.addLayout(row)

        # Retención: cuántas copias se conservan por versión. Va aquí (y no en
        # Ajustes) porque es justo donde se ven y se borran.
        keep_row = QHBoxLayout()
        keep_label = QLabel(tr("Keep at most"))
        keep_label.setToolTip(tr(
            "How many saved copies to keep per version. The oldest are deleted "
            "when a new one is saved."))
        keep_row.addWidget(keep_label)
        keep_row.addStretch()
        self.snapshot_keep_combo = QComboBox()
        for value, label in ((3, "3"), (5, "5"), (10, "10"), (0, tr("All"))):
            self.snapshot_keep_combo.addItem(label, value)
        index = self.snapshot_keep_combo.findData(self.snapshot_keep)
        self.snapshot_keep_combo.setCurrentIndex(index if index >= 0 else 1)
        self.snapshot_keep_combo.setToolTip(tr(
            "How many saved copies to keep per version. The oldest are deleted "
            "when a new one is saved."))
        self.snapshot_keep_combo.currentIndexChanged.connect(
            self._on_snapshot_keep_changed)
        keep_row.addWidget(self.snapshot_keep_combo)
        lay.addLayout(keep_row)
        # Asa en la esquina inferior derecha de la **tarjeta** (no de la fila).
        self.snapshot_grip = CornerGrip(
            self._resize_snapshots,
            tooltip=tr("Drag the corner to make the saved copies list taller "
                       "or shorter."))
        card.set_grip(self.snapshot_grip)
        self.snapshot_card = card

        self._fill_factory()
        return card

    def set_snapshot_keep(self, value: int) -> None:
        """Fija cuántas copias se conservan (lo llama MainWindow con el ajuste)."""
        self.snapshot_keep = int(value)
        if hasattr(self, "snapshot_keep_combo"):
            index = self.snapshot_keep_combo.findData(self.snapshot_keep)
            if index >= 0:
                self.snapshot_keep_combo.blockSignals(True)
                self.snapshot_keep_combo.setCurrentIndex(index)
                self.snapshot_keep_combo.blockSignals(False)

    def _on_snapshot_keep_changed(self, index: int) -> None:
        """El usuario cambió la retención: se guarda y se poda ya.

        Bajar el tope **borra** guardados en el acto, y sin vuelta atrás. Un
        desplegable no se lee como un botón de borrar, así que antes se
        pregunta diciendo cuántos se van; si dice que no, el desplegable
        vuelve a lo que tenía y no cambia nada.
        """
        value = self.snapshot_keep_combo.itemData(index)
        if value is None:
            return
        keep = int(value)
        config = self._factory_config()
        doomed = (max(0, len(bsnap.all_snapshots(config)) - keep)
                  if config is not None and keep > 0 else 0)
        if doomed and not self._confirm_prune(keep, doomed):
            self.set_snapshot_keep(self.snapshot_keep)
            return
        self.snapshot_keep = keep
        self.snapshot_keep_changed.emit(self.snapshot_keep)
        if doomed:
            bsnap.prune_snapshots(config, keep)
            self._forget_analysis()
        self._fill_factory()

    def _confirm_prune(self, keep: int, doomed: int) -> bool:
        """Pregunta antes de borrar los ``doomed`` guardados que sobran."""
        entry = self._factory_entry()
        return dialogs.confirm(
            self, tr("Delete older saved settings"),
            tr("Keep only the {keep} newest saved copies of {program} "
               "{version}? The {count} older ones are deleted for good.",
               keep=keep, count=doomed,
               program=fork_label(getattr(entry, "fork", "")),
               version=getattr(entry, "version", "")),
            accept_text=tr("Delete"), danger=True)

    def _forget_analysis(self) -> None:
        """Tira el análisis de los guardados (es de otra versión o ya no vale).

        ``_analyzed_for`` a ``None`` es lo que hace que
        ``_maybe_analyze_snapshots`` vuelva a arrancar Blender la próxima vez
        que se abra la pestaña. Si hay un análisis en marcha, se invalida: al
        llegar, ``_on_snapshots_analyzed`` lo tira y lanza el que toca.
        """
        self._analysis = {}
        self._analyzed_for = None
        self._snapshot_job.invalidate()

    def _factory_entry(self):
        """Instalada elegida en la pestaña de fábrica (su propio selector)."""
        return self._selected(self.factory_combo)

    def _factory_config(self):
        """Config de la versión que se restablece/recupera en esa pestaña."""
        entry = self._factory_entry()
        if entry is None:
            return None
        return bc.config_for(entry.version, self.platform,
                             fork=getattr(entry, "fork", ""))

    def _clear_snapshot_rows(self) -> None:
        """Vacía la lista de guardados (deja el hueco del final)."""
        self._snapshot_widgets = {}
        clear_layout(self.snapshot_rows, keep_stretch=True)

    def _fill_snapshot_rows(self, snapshots) -> None:
        """Crea una fila por guardado y le vuelca el análisis ya cacheado."""
        self._snapshot_widgets = {}
        for snapshot in snapshots:
            row = _SnapshotRow(
                snapshot, bsnap.snapshot_details(snapshot),
                on_restore=self.restore_factory_snapshot,
                on_delete=self.delete_snapshot,
                on_details=self.show_snapshot_details)
            cached = self._analysis.get(str(snapshot))
            if cached is not None:
                row.set_analysis(cached)
            # Antes del hueco final (el ``addStretch`` del layout).
            self.snapshot_rows.insertWidget(self.snapshot_rows.count() - 1,
                                            row)
            self._snapshot_widgets[snapshot] = row
        if snapshots:
            self._snapshot_widgets[snapshots[0]].add_badge(tr("Most recent"))

    def _factory_status_text(self, count: int) -> str:
        """Resumen: cuántos guardados hay y en qué estado está la config viva."""
        text = tr("Saved copies: {count}.", count=count)
        if self._factory_live_count is None:
            return text
        if self._factory_live_count:
            return text + " " + tr(
                "Right now this Blender has {count} settings changed from its "
                "defaults.", count=self._factory_live_count)
        return text + " " + tr(
            "Right now this Blender is at its defaults; restoring a copy "
            "brings your settings back.")

    def _fill_factory(self) -> None:
        """Repinta la tarjeta de fábrica: estado, lista de guardados y análisis."""
        if not hasattr(self, "factory_status"):
            return
        config = self._factory_config()
        self._clear_snapshot_rows()
        if config is None:
            self.factory_hint.setText("")
            self.factory_status.setText("")
            self.factory_path_label.setText("")
            self.factory_empty.setVisible(False)
            self.snapshot_scroll.setVisible(False)
            self.delete_all_btn.setEnabled(False)
            return
        _, version = _entry_info(self._factory_entry())
        self.factory_path_label.setText(str(config.root))
        self.factory_hint.setText(tr(
            "Start Blender {version} as if it were freshly installed. Its "
            "current settings are saved aside and can be put back.",
            version=version))
        snapshots = bsnap.snapshots_with_settings(config)
        self.delete_all_btn.setEnabled(bool(bsnap.all_snapshots(config)))
        if not snapshots:
            self.factory_status.setText("")
            self.factory_empty.setVisible(True)
            self.snapshot_scroll.setVisible(False)
            self._forget_analysis()
            self._factory_live_count = None
            return
        self.factory_empty.setVisible(False)
        self._fill_snapshot_rows(snapshots)
        self.snapshot_scroll.setVisible(True)
        self.snapshot_list.fit()
        self.factory_status.setText(self._factory_status_text(len(snapshots)))
        self._maybe_analyze_snapshots()

    def _maybe_analyze_snapshots(self) -> None:
        """Lee en segundo plano qué ajustes cambia cada guardado.

        Solo cuando la pestaña está abierta (no en cada ``_reload``) y una vez
        por versión: cada guardado cuesta un arranque de Blender, así que no se
        repite mientras no cambie la versión ni los guardados.
        """
        if not hasattr(self, "tabs"):
            return      # aún se está montando la vista
        if self._page_of(self.tabs.currentWidget()) is not self.factory_page:
            return
        if self._snapshot_job.running or self._analyzed_for:
            return
        entry = self._factory_entry()
        executable, version = _entry_info(entry)
        config = self._factory_config()
        if config is None or not executable or not Path(executable).is_file():
            return
        snapshots = list(bsnap.snapshots_with_settings(config))
        if not snapshots:
            return
        self._analyzed_for = _entry_key(entry)
        ticket = self._snapshot_job.start(self._analyzed_for)
        # Las filas sin análisis dicen que se está leyendo: en blanco parecía
        # que el guardado no traía nada.
        for row in self._snapshot_widgets.values():
            if row.analysis is None:
                row.set_analysis(None)

        def worker():
            payload = {"version": version, "results": {}, "live": None,
                       "ticket": ticket}
            try:
                factory = bprefs.read_preferences(executable, factory=True,
                                                  timeout=120)
                live = bprefs.read_preferences(executable, timeout=120)
                if not factory.ok or not live.ok:
                    payload["failed"] = True
                else:
                    payload["live"] = len(bprefs.changed(live.values,
                                                         factory.values))
                    for snapshot in snapshots:
                        user = bprefs.snapshot_preferences(
                            executable, snapshot, timeout=120)
                        # Un guardado ilegible marca solo su fila.
                        payload["results"][str(snapshot)] = (
                            bprefs.changed(user.values, factory.values,
                                           meta=user.meta)
                            if user.ok else None)
            except Exception:  # noqa: BLE001 - un fallo no puede tumbar la vista
                payload["failed"] = True
            self.snapshots_analyzed.emit(payload)

        threading.Thread(target=worker, daemon=True).start()

    def _on_snapshots_analyzed(self, payload) -> None:
        """Vuelca en cada fila lo que cambia su guardado.

        Si el análisis era de otra versión (o se tiró mientras corría), se
        descarta y se lanza el de la versión elegida ahora: sin eso, sus filas
        se quedaban en "Analyzing..." para siempre.
        """
        analyzed = self._snapshot_job.key
        if not self._snapshot_job.finish(payload):
            return
        if analyzed != _entry_key(self._factory_entry()):
            self._analyzed_for = None
            self._maybe_analyze_snapshots()
            return
        results = payload.get("results") or {}
        best = None
        best_count = 0
        for snapshot, row in self._snapshot_widgets.items():
            preferences = results.get(str(snapshot))
            if preferences is None:
                # Sin resultado: o falló todo, o ese guardado no se pudo leer
                # (``None`` explícito). Solo se queda "analizando" si aún no
                # le ha tocado.
                if payload.get("failed") or str(snapshot) in results:
                    row.set_unreadable()
                else:
                    row.set_analysis(None)
                continue
            self._analysis[str(snapshot)] = preferences
            row.set_analysis(preferences)
            if len(preferences) > best_count:
                best, best_count = snapshot, len(preferences)
        if best is not None:
            self._snapshot_widgets[best].add_badge(tr("Most complete"))
        live = payload.get("live")
        if live is not None:
            self._factory_live_count = live
            self.factory_status.setText(
                self._factory_status_text(len(self._snapshot_widgets)))

    def show_snapshot_details(self, snapshot) -> None:
        """Abre el diálogo con los ajustes que cambia ese guardado."""
        row = self._snapshot_widgets.get(Path(snapshot))
        preferences = row.analysis if row is not None else None
        if preferences is None:
            dialogs.show_info(self, tr("Saved settings"),
                      tr("Still reading this copy. Try again in a moment."))
            return
        _show_snapshot_details(self, snapshot, preferences)

    def delete_snapshot(self, snapshot) -> None:
        """Borra un guardado concreto (irreversible)."""
        snapshot = Path(snapshot)
        date = bsnap.snapshot_date(snapshot) or snapshot.name
        if not dialogs.confirm(
                self, tr("Delete saved settings"),
                tr("Delete the settings saved on {date} for good? You will not "
                   "be able to restore them.", date=date),
                accept_text=tr("Delete"), danger=True):
            return
        # ``delete_snapshot`` no lanza: devuelve False si no pudo (un fichero
        # abierto en Windows, sin permiso...). Anunciar "borrado" en ese caso
        # dejaba al usuario creyendo que el guardado ya no estaba.
        if not bsnap.delete_snapshot(snapshot):
            dialogs.show_error(self, tr("Could not finish"), tr(
                "Could not delete the settings saved on {date}. Close any "
                "program that is using that folder and try again:\n{path}",
                date=date, path=str(snapshot)))
            self._fill_factory()
            return
        self._analysis.pop(str(snapshot), None)
        self.status_message.emit(tr("Saved settings deleted."))
        self._fill_factory()

    def delete_all_snapshots(self) -> None:
        """Borra todos los guardados de esa versión (irreversible)."""
        config = self._factory_config()
        if config is None:
            return
        # ``all_snapshots`` (no ``snapshots_with_settings``): se borra también lo vacío,
        # que si no quedaría ahí sin forma de limpiarlo desde la interfaz.
        snapshots = bsnap.all_snapshots(config)
        if not snapshots:
            return
        if not dialogs.confirm(
                self, tr("Delete saved settings"),
                tr("Delete all saved settings of this version for good? You "
                   "will not be able to restore them."),
                accept_text=tr("Delete"), danger=True):
            return
        failed = [snapshot for snapshot in snapshots
                  if not bsnap.delete_snapshot(snapshot)]
        self._forget_analysis()
        self._fill_factory()
        if failed:
            dialogs.show_error(self, tr("Could not finish"), tr(
                "{count} saved copies could not be deleted. Close any program "
                "that is using them and try again.", count=len(failed)))
            return
        self.status_message.emit(tr("Saved settings deleted."))

    def reset_to_factory(self) -> None:
        """Aparta la config de esa versión (instantánea) para dejarla limpia."""
        entry = self._factory_entry()
        config = self._factory_config()
        if config is None:
            return
        if self._blocked_by_running(entry):
            return
        _, version = _entry_info(entry)
        if not dialogs.confirm(
                self, tr("Reset to factory settings"),
                tr("Blender {version} will start clean on next launch.\n\nYour "
                   "settings are saved aside, so you can put them back from "
                   "this same screen.", version=version),
                accept_text=tr("Reset"), danger=True):
            return
        try:
            snapshot = bsnap.set_config_aside(config, label=f"v{version}",
                                              keep=self.snapshot_keep)
        except OSError as error:
            # Apartar es mover una carpeta: puede fallar (un fichero abierto,
            # sin permiso). Sin el diálogo, la excepción se perdía en el slot
            # y parecía que el botón no hacía nada.
            dialogs.show_error(self, tr("Could not finish"), tr(
                "Could not save the current settings aside, so nothing was "
                "reset:\n{error}", error=str(error)))
            self._fill_factory()
            return
        if snapshot is None:
            self.factory_status.setText(tr(
                "This version has no settings yet."))
            return
        self._forget_analysis()
        self.status_message.emit(tr("Settings saved aside and reset."))
        dialogs.show_info(self, tr("Reset to factory settings"),
                  tr("Your settings were saved. Blender {version} will start "
                     "clean the next time you open it.", version=version))
        self._fill_factory()

    def restore_factory_snapshot(self, snapshot=None) -> None:
        """Copia un guardado a su sitio (el más reciente si no se dice cuál)."""
        entry = self._factory_entry()
        config = self._factory_config()
        if config is None:
            return
        snapshots = bsnap.snapshots_with_settings(config)
        if not snapshots:
            return
        target = Path(snapshot) if snapshot is not None else snapshots[0]
        if target not in snapshots:
            return
        if self._blocked_by_running(entry):
            return
        _, version = _entry_info(entry)
        date = bsnap.snapshot_date(target) or target.name
        if not dialogs.confirm(
                self, tr("Restore settings"),
                tr("Put back in Blender {version} the settings saved on "
                   "{date}?\n\nWhat it has now is saved aside, so this can be "
                   "undone.", version=version, date=date),
                accept_text=tr("Restore")):
            return
        try:
            bsnap.restore_snapshot(config, target, keep=self.snapshot_keep)
        except OSError as error:
            dialogs.show_error(self, tr("Could not finish"), tr(
                "Could not put the saved settings back:\n{error}",
                error=str(error)))
            self._forget_analysis()
            self._fill_factory()
            return
        self._forget_analysis()
        self.status_message.emit(tr("Settings restored."))
        self._fill_factory()

    def _resize_snapshots(self, delta: int) -> None:
        """Arrastró el asa de la lista de guardados."""
        self.snapshot_list.resize(delta)
