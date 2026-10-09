"""Pestaña de add-ons: el tablero origen → destino y la copia.

Una fila por addon a cada lado (qué es, si es compatible, dónde va a caer),
la copia con su respaldo y el deshacer, y la activación en destino de los que
estaban activados en origen.

Es un **mixin** de ``MigrateView``: comparte con las otras pestañas el par
origen/destino y el aviso de "Blender abierto". El trabajo de verdad (leer
manifiestos, decidir compatibilidad, copiar) está en
``services/blender_config.py``, que no depende de Qt.
"""

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QVBoxLayout,
                               QWidget)

from i18n import tr
from services import blender_config as bc
from services import blender_addons as baddons
from services import blender_runner
from ui import icons
from ui.fonts import icon_font
from ui.widgets.buttons import CardButton, CheckPill
from ui.widgets.cards import settings_card
from ui.widgets import dialogs
from ui.widgets.labels import ElidedLabel
from ui.widgets.layouts import clear_layout, muted_note
from ui.widgets.migrate.common import (ROW_HEIGHT, _STATUS,
                                           _accent_button, _destination_text,
                                           _entry_info, _entry_program,
                                           _failure_block,
                                           _meta_text, _report_lines,
                                           _status_tooltip)


class _BoardRow(QFrame):
    """Base de las filas del tablero.

    Las dos columnas tienen que cuadrar una con otra (misma altura, mismo rayado
    cebra), así que ese preámbulo está aquí una sola vez: si una fila creciera
    sin la otra, al cambiar de versión el tablero quedaría desalineado.
    """

    def __init__(self, plan, zebra: bool, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.setObjectName("AddonRow")
        self.setProperty("zebra", "true" if zebra else "false")
        self.setFixedHeight(ROW_HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)
        self.row_layout = lay

class _SourceRow(_BoardRow):
    """Fila del origen: casilla, nombre, versión y veredicto."""

    changed = Signal()

    def __init__(self, plan, zebra: bool, parent=None):
        super().__init__(plan, zebra, parent)
        lay = self.row_layout

        self.check = CheckPill()
        self.check.setChecked(plan.selected)
        # Lo que no es compatible no se puede marcar: nunca se copia sin querer.
        self.check.setEnabled(not plan.blocked)
        self.check.setToolTip(
            _status_tooltip(plan) if plan.blocked
            else tr("Tick to include this add-on in the copy."))
        self.check.toggled.connect(self._on_toggled)
        lay.addWidget(self.check)

        text = QVBoxLayout()
        text.setSpacing(1)
        name = ElidedLabel(plan.addon.name, Qt.ElideRight)
        name.setObjectName("Title")
        text.addWidget(name)
        meta = ElidedLabel(_meta_text(plan.addon), Qt.ElideRight)
        meta.setObjectName("Muted")
        text.addWidget(meta)
        lay.addLayout(text, 1)

        obj, label = _STATUS.get(plan.status, ("Muted", "Review"))
        status = QLabel(tr(label))
        status.setObjectName(obj)
        # El tooltip va en el distintivo (donde pone "Review") y en la fila: al
        # pasar el ratón por el texto amarillo se explica qué hay que revisar y
        # cómo. Es lo que pide cualquiera que vea "Review" y no sepa qué hacer.
        tooltip = _status_tooltip(plan)
        if tooltip:
            status.setToolTip(tooltip)
            self.setToolTip(tooltip)
        lay.addWidget(status)

    def _on_toggled(self, checked: bool) -> None:
        self.plan.selected = checked
        self.changed.emit()

    def set_checked(self, checked: bool) -> None:
        """Marca o desmarca respetando las bloqueadas."""
        self.check.setChecked(bool(checked) and not self.plan.blocked)

class _DestRow(_BoardRow):
    """Fila del destino: dónde caerá ese addon."""

    def __init__(self, plan, zebra: bool, parent=None):
        super().__init__(plan, zebra, parent)
        label = ElidedLabel(_destination_text(plan), Qt.ElideMiddle)
        label.setObjectName("Muted" if plan.blocked else "Info")
        if plan.blocked:
            tip = tr("This add-on is not copied, so nothing changes here.")
        else:
            tip = tr("Where it lands on the destination side: {path}",
                     path=plan.destination)
        label.setToolTip(tip)
        self.setToolTip(tip)
        self.row_layout.addWidget(label, 1)


class AddonsTabMixin:
    """Parte de ``MigrateView``; ver el docstring del módulo."""

    def _build_addons_tab(self) -> QWidget:
        """Pestaña de addons: tablero, resumen y explicación, en ese orden.

        El resumen y la explicación van **debajo** del tablero y **dentro** de
        la pestaña: así no comparten altura con las otras pestañas y no hacen
        saltar la interfaz al cambiar de una a otra (que es lo que pasaba
        teniéndolos en la tarjeta de versiones).
        """
        page, lay = self._new_page()

        # El tablero es un layout DIRECTAMENTE en la página (sin un QWidget que
        # lo envuelva): un contenedor ajustado al tamaño de las tarjetas
        # **recorta su sombra** (QGraphicsDropShadowEffect no puede pintar fuera
        # del padre). Con la página grande de padre, la sombra cae en el hueco y
        # se ve. Además así las tarjetas no se estiran a lo alto: miden lo que
        # miden sus filas.
        board_lay = QHBoxLayout()
        board_lay.setContentsMargins(0, 0, 0, 0)
        board_lay.setSpacing(14)
        self.left_card, self.left_title, self.left_rows = self._board_column(
            tr("Source"),
            tr("Add-ons installed in the version you are copying from. Tick "
               "the ones you want in the destination version."))
        self.right_card, self.right_title, self.right_rows = self._board_column(
            tr("Destination"),
            tr("Where each add-on lands in the destination version. Add-ons "
               "that were enabled in the source are enabled here too."))
        board_lay.addWidget(self.left_card, 1)
        board_lay.addLayout(self._arrow_column(), 0)
        board_lay.addWidget(self.right_card, 1)
        lay.addLayout(board_lay)

        # Los botones van pegados a las tarjetas (debajo), y el resumen y la
        # explicación quedan debajo de ellos: primero actuar, luego el detalle.
        actions = QHBoxLayout()
        self.select_all_btn = CardButton(
            tr("Select all"),
            tooltip=tr("Tick every add-on that can be copied. The ones marked "
                       "\"Not compatible\" cannot be ticked."))
        self.select_all_btn.clicked.connect(lambda: self._select_all(True))
        self.select_none_btn = CardButton(
            tr("Select none"),
            tooltip=tr("Untick them all, to copy nothing."))
        self.select_none_btn.clicked.connect(lambda: self._select_all(False))
        actions.addWidget(self.select_all_btn)
        actions.addWidget(self.select_none_btn)
        actions.addStretch()
        self.copy_btn = _accent_button(
            tr("Copy selected"),
            tr("Copy the ticked add-ons to the destination version and enable "
               "the ones that were enabled in the source."),
            self.apply)
        actions.addWidget(self.copy_btn)
        lay.addLayout(actions)

        # El resumen y la explicación, en color de texto normal: son datos y
        # ayuda que hay que leer, y en Muted sobre el gris quedaban apagados.
        self.summary = QLabel("")
        self.summary.setToolTip(tr(
            "How many add-ons were found, how many are compatible, how many "
            "you should review and how many cannot be copied."))
        lay.addWidget(self.summary)

        for text in (
            "Copy the add-ons (and extensions) of one installed version to "
            "another, checking first whether they are compatible.",
            # No hay interruptor de "activar tras copiar": el estado se imita
            # del origen (lo activado se activa, lo apagado se queda apagado).
            "Add-ons keep the state they had in the source: the ones that were "
            "enabled there are enabled here too.",
        ):
            label = QLabel(tr(text))
            label.setWordWrap(True)
            lay.addWidget(label)

        # El estirón al final deja la página más alta que las tarjetas: es el
        # hueco donde cae su sombra (y evita que se estiren a lo alto).
        lay.addStretch()
        # La página va dentro de un scroll, como Preferences y Factory: sin él,
        # con la ventana baja el layout aprieta las tarjetas por debajo de su
        # mínimo y los textos de Origen y Destino se solapan y se recortan. Con
        # el scroll, las tarjetas miden lo que miden y lo que no cabe se baja.
        return self._scrollable(page)

    def _board_column(self, title: str, tooltip: str = ""):
        """Una columna del tablero: título y hueco de filas.

        Comparte el ``SettingsCard`` con las demás tarjetas, pero con menos
        margen: el tablero va a dos columnas y con los 16 px de las tarjetas
        de texto las filas se quedaban estrechas.
        """
        card, lay = settings_card(margins=(12, 10, 12, 10))
        label = QLabel(title)
        label.setObjectName("Muted")
        if tooltip:
            label.setToolTip(tooltip)
        lay.addWidget(label)
        rows = QVBoxLayout()
        rows.setSpacing(4)
        lay.addLayout(rows)
        lay.addStretch()
        return card, label, rows

    def _arrow_column(self):
        """Columna central: la flecha (un solo sentido) del tablero."""
        column = QVBoxLayout()
        column.addStretch()
        arrow = QLabel(icons.ARROW_RIGHT)
        arrow.setObjectName("BoardArrow")    # el tamaño lo pone el QSS
        arrow.setFont(icon_font())
        arrow.setAlignment(Qt.AlignHCenter)
        arrow.setToolTip(tr("The ticked add-ons are copied from left to right. "
                            "Nothing is moved: the source version stays as it "
                            "is."))
        column.addWidget(arrow)
        column.addStretch()
        return column

    def _fill_board(self) -> None:
        """Pinta el tablero con el plan actual (o el mensaje de vacío)."""
        self._clear_rows()
        if not self.plans:
            self.summary.setText(tr("No add-ons to migrate"))
            self.left_rows.addWidget(
                muted_note(tr("No add-ons found in this version")))
            self.right_rows.addWidget(muted_note(tr(
                "Install some add-ons in the source version first.")))
            self._set_controls_enabled(False)
            return
        for index, plan in enumerate(self.plans):
            zebra = bool(index % 2)
            row = _SourceRow(plan, zebra)
            row.changed.connect(self._update_summary)
            self.left_rows.addWidget(row)
            self._rows.append(row)
            self.right_rows.addWidget(_DestRow(plan, zebra))
        self._set_controls_enabled(True)
        self._update_summary()

    def _clear_rows(self) -> None:
        """Vacía las dos columnas del tablero."""
        self._rows = []
        for layout in (self.left_rows, self.right_rows):
            clear_layout(layout)

    def _select_all(self, checked: bool) -> None:
        for row in self._rows:
            row.set_checked(checked)
        self._update_summary()

    def _update_summary(self) -> None:
        counts = baddons.summary_counts(self.plans)
        self.summary.setText(tr(
            "{total} add-ons · {ok} compatible · {warn} to review · "
            "{blocked} not compatible",
            total=len(self.plans), ok=counts[bc.OK], warn=counts[bc.WARN],
            blocked=counts[bc.BLOCKED]))

    def _rebuild_plan(self) -> None:
        """Recalcula el plan de addons con el estado activado del origen."""
        python = bc.python_for_version(self.target_entry.version)
        self.plans = baddons.plan_migration(
            self.source_cfg, self.target_cfg, self.platform, self.arch, python,
            enabled_ids=self.source_enabled)
        self._fill_board()

    # --------------------------------------------------------------- copiar
    def apply(self) -> None:
        """Copia los addons marcados y los activa como estaban en origen.

        El estado se **imita**, no se fuerza: los addons que en origen estaban
        activados se activan en destino, y los que allí estaban apagados se
        quedan apagados. Así una copia se comporta igual que el original sin
        que el usuario tenga que repasar la lista de addons.

        Antes de copiar se comprueba que no haya ningún Blender abierto: antes
        se miraba **después**, así que los ficheros ya estaban copiados cuando
        aparecía el aviso de "cierra Blender" y la activación no se hacía. La
        copia va en un hilo (un addon con sus *wheels* puede pesar cientos de
        MB y la ventana se quedaba congelada) y termina en ``_on_addons_copied``.
        """
        selected = [plan for plan in self.plans
                    if plan.selected and not plan.blocked]
        if not selected:
            self.status_message.emit(tr("Nothing selected"))
            return
        if self._blocked_by_running():
            return
        # Lo que el hilo necesita se toma **ahora**: si el usuario cambia de
        # versión mientras copia, la respuesta sigue siendo de esta copia.
        plans, target_cfg = list(self.plans), self.target_cfg
        executable, version = _entry_info(self.target_entry)
        program = _entry_program(self.target_entry)
        ticket = self._copy_job.start(version)
        self._set_controls_enabled(False)
        self.status_message.emit(tr("Copying the add-ons..."))

        def worker():
            payload = {"executable": executable, "version": version,
                       "program": program, "ticket": ticket}
            try:
                payload["result"] = baddons.apply_migration(plans, target_cfg)
            except Exception as error:  # noqa: BLE001 - se enseña el motivo
                payload["error"] = str(error)
            self.addons_copied.emit(payload)

        threading.Thread(target=worker, daemon=True).start()

    def _on_addons_copied(self, payload) -> None:
        """Fin de la copia: informe y activación de los que estaban activos."""
        if not self._copy_job.finish(payload):
            return
        self._set_controls_enabled(bool(self.plans))
        if payload.get("error"):
            dialogs.show_error(self, tr("Could not finish"), payload["error"])
            return
        result = payload["result"]
        self._finish_copy(
            result, tr("Copied {count} add-ons.", count=len(result.copied)),
            tr("Some add-ons could not be copied:"),
            lambda plan: plan.addon.name)

        # Solo se activan los que estaban activos en origen.
        executable, version = payload["executable"], payload["version"]
        modules = [plan.enable_module for plan in result.copied
                   if plan.was_enabled]
        if not (executable and modules and Path(executable).is_file()):
            return
        # Se vuelve a mirar: mientras se copiaba, el usuario pudo abrir Blender.
        if self._blocked_by_running():
            return
        self._start_activation(executable, modules, version,
                               payload["program"])

    def undo(self) -> None:
        """Revierte la última migración sobre el destino."""
        if self.target_cfg is None or self.target_entry is None:
            return
        _, version = _entry_info(self.target_entry)
        if not dialogs.confirm(
                self, tr("Undo last migration"),
                tr("This puts back what the last migration replaced and removes "
                   "what it copied from {program} {version}.\n\nAdd-ons you "
                   "have changed since then will be lost.",
                   program=_entry_program(self.target_entry), version=version),
                accept_text=tr("Undo"), danger=True):
            return
        result = baddons.undo_migration(self.target_cfg)
        lines = []
        if result.restored:
            lines.append(tr("Restored {count} items to their previous state.",
                            count=len(result.restored)))
        if result.removed:
            lines.append(tr("Removed {count} items that were copied.",
                            count=len(result.removed)))
        if not lines:
            lines = [tr("There was nothing to undo.")]
        lines.extend(_failure_block(
            tr("Some items could not be restored:"),
            (f"{Path(path).name}: {message}" for path, message in result.failed)))
        dialogs.show_info(self, tr("Migration undone"), "\n".join(lines))
        self.status_message.emit(tr("Migration undone"))
        self._fill_from_installed()

    def _undo_button(self) -> CardButton:
        """Botón para revertir la última migración (si la hay)."""
        self.undo_btn = CardButton(
            tr("Undo last migration"),
            tooltip=tr("Put back what the last migration replaced and remove "
                       "what it copied."))
        self.undo_btn.clicked.connect(self.undo)
        self.undo_btn.setVisible(False)
        return self.undo_btn

    def _refresh_undo(self) -> None:
        """Enseña el botón de deshacer solo si el destino tiene marcador."""
        if self.target_cfg is None or not hasattr(self, "undo_btn"):
            return
        self.undo_btn.setVisible(
            bool(baddons.read_migration_marker(self.target_cfg)))

    def _start_activation(self, executable, modules, version,
                          program: str = "Blender") -> None:
        """Habilita los addons copiados en un hilo (Blender tarda en arrancar)."""
        self.status_message.emit(tr("Enabling the add-ons in Blender..."))
        ticket = self._activation_job.start(version)

        def worker():
            result = blender_runner.enable_addons(executable, modules)
            self.activation_done.emit({"result": result, "version": version,
                                       "program": program, "ticket": ticket})

        threading.Thread(target=worker, daemon=True).start()

    def _on_activation_done(self, payload) -> None:
        if not self._activation_job.finish(payload):
            return
        result = payload.get("result") or {}
        version = payload.get("version") or ""
        lines = []
        enabled = result.get("enabled") or []
        if enabled:
            lines.append(tr("Enabled {count} add-ons in {program} {version}.",
                            count=len(enabled), version=version,
                            program=payload.get("program") or "Blender"))
        else:
            lines.append(tr("The add-ons were copied but not enabled. You can "
                            "enable them in Blender's preferences."))
        lines.extend(_failure_block(
            tr("Could not enable some add-ons:"),
            (f"{item.get('module') or 'Blender'}: {item.get('error', '')}"
             for item in result.get("errors") or [])))
        dialogs.show_info(self, tr("Migration complete"), "\n".join(lines))
        self.status_message.emit(tr("Migration complete"))

    def _finish_copy(self, result, summary: str, failure_title: str,
                     failure_label) -> None:
        """Cierre común de una copia (addons o ficheros): diálogo, estado, deshacer.

        ``summary`` es la frase "Copiados N…"; si no se copió nada, el diálogo
        lo dice en su lugar. Se acaba de escribir en destino, así que el botón
        de deshacer se refresca aquí.
        """
        lines = _report_lines(summary if result.copied else "",
                              bool(result.backed_up), result.failed,
                              failure_title, failure_label)
        dialogs.show_info(self, tr("Migration complete"), "\n".join(lines))
        self.status_message.emit(summary)
        self._refresh_undo()
