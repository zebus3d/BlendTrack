"""Pestaña de preferencias: presets, fichero entero y ajustes uno a uno.

Tres formas de llevarse los ajustes, de la más fina a la más gruesa:

* **En detalle**: cada clave que el usuario cambió respecto a fábrica, con su
  nombre y su descripción (las de Blender), para marcar solo lo que interese.
* **Tema y mapa de teclas**: se dejan como presets con nombre en el destino,
  sin pisar nada.
* **Fichero de preferencias**: copiar ``userpref.blend`` entero (y, si se
  marca, el ``startup.blend``), que sí pisa lo que hubiera.

Es un **mixin** de ``MigrateView``. La lectura y la escritura de claves están
en ``services/blender_prefs.py``.
"""

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from i18n import tr
from model.build import minor_of
from services import blender_addons as baddons
from services import blender_snapshots as bsnap
from services import blender_prefs as bprefs
from services import blender_runner
from services import blender_style as bstyle
from ui.widgets.buttons import CardButton, CheckPill
from ui.widgets.cards import settings_card
from ui.widgets import dialogs
from ui.widgets.labels import ElidedLabel
from ui.widgets.layouts import (CornerGrip, FittedList, clear_layout,
                                muted_note)
from ui.widgets.migrate.common import (DETAIL_MIN_HEIGHT, DETAIL_MIN_ROWS,
                                       DETAIL_SCROLL_HEIGHT, _accent_button,
                                       _entry_info, _entry_key,
                                       _entry_program,
                                       _failure_block, _make_scroll)


class _PrefRow(QWidget):
    """Fila de una preferencia: nombre, valor y la descripción de Blender.

    El nombre es el que Blender enseña en Preferencias (``Preference.name``)
    y la descripción, su tooltip: así cada fila explica qué es ese ajuste sin
    mantener textos a mano. La ruta RNA completa va en el tooltip, que es lo
    que permite comprobarlo en Blender sin adivinar.

    Con ``checkable`` la fila lleva casilla (lista para migrar); sin ella es
    solo lectura (el detalle de un guardado). La descripción va en
    ``ElidedLabel`` **con factor de estirado**: sin él, en un ``QHBoxLayout``
    se queda a 0 px (ver AGENTS.md).

    Cada fila es un widget suelto, así que por sí solas no forman columnas: el
    nombre y el valor se fijan al mismo ancho en todas (``align_columns``) y
    las descripciones quedan alineadas a la izquierda, como otra columna.
    """

    # Topes de las dos primeras columnas: un nombre o un valor larguísimo se
    # recorta con "…" en vez de empujar las descripciones fuera de la vista.
    NAME_MAX = 260
    VALUE_MAX = 180

    def __init__(self, pref, checkable: bool = True, environment: bool = False,
                 parent=None):
        super().__init__(parent)
        self.pref = pref
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        if checkable:
            self.check = CheckPill(pref.label)
            self.check.setChecked(pref.selected)
            self.check.toggled.connect(
                lambda checked: setattr(pref, "selected", checked))
            self.name = self.check
        else:
            self.check = None
            self.name = ElidedLabel(pref.label, Qt.ElideRight)
        lay.addWidget(self.name)
        self.value = ElidedLabel(pref.display_value, Qt.ElideRight)
        self.value.setObjectName("Info")
        lay.addWidget(self.value)
        # Apagada y la única que se estira: es texto de apoyo, no el dato.
        self.description = ElidedLabel(pref.description, Qt.ElideRight)
        self.description.setObjectName("Muted")
        lay.addWidget(self.description, 1)

        lines = []
        if pref.description:
            lines.append(pref.description)
        lines.append(tr("Blender path: {path}", path=pref.path))
        if environment:
            lines.append(tr(
                "This depends on your computer, not on your settings. Leave "
                "it off unless it is the same machine."))
        tooltip = "\n\n".join(lines)
        self.setToolTip(tooltip)
        if self.check is not None:
            self.check.setToolTip(tooltip)

    def set_checked(self, checked: bool) -> None:
        """Marca o desmarca la casilla (si la fila la tiene)."""
        if self.check is not None:
            self.check.setChecked(checked)

    def natural_widths(self) -> tuple:
        """Ancho que piden el nombre y el valor con su texto entero.

        Se pule antes de medir: el valor va en negrita por QSS y, sin el
        estilo aplicado, las métricas son las de la fuente normal (más
        estrecha) y "OPTIX" salía recortado a "OP…".
        """
        self.name.ensurePolished()
        self.value.ensurePolished()
        if self.check is not None:
            name = self.check.sizeHint().width()
        else:
            name = self.name.fontMetrics().horizontalAdvance(self.pref.label)
        value = self.value.fontMetrics().horizontalAdvance(self.pref.display_value)
        # Un par de píxeles de aire: la elisión salta con el ancho justo.
        return name + 2, value + 4

    @staticmethod
    def align_columns(rows) -> None:
        """Da a todas las filas el mismo ancho de nombre y de valor.

        Se mide el más ancho de cada columna (con tope) y se fija en todas:
        es lo que hace que las descripciones empiecen en la misma vertical.
        """
        rows = list(rows)
        if not rows:
            return
        widths = [row.natural_widths() for row in rows]
        name = min(_PrefRow.NAME_MAX, max(w[0] for w in widths))
        value = min(_PrefRow.VALUE_MAX, max(w[1] for w in widths))
        for row in rows:
            for widget, width in ((row.name, name), (row.value, value)):
                # ``ElidedLabel`` tiene política ``Ignored`` (no pide ancho);
                # para que respete un ancho fijo hay que cambiársela también.
                widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
                widget.setFixedWidth(width)


class PrefsTabMixin:
    """Parte de ``MigrateView``; ver el docstring del módulo."""

    def _build_preferences_page(self) -> QWidget:
        """Pestaña de preferencias: detalle fino, tema/keymap y ficheros.

        El detalle va arriba porque es lo recomendado (ajustes sueltos, sin
        pisar todo); el fichero completo es el atajo que reemplaza el
        ``userpref.blend`` entero, y por eso queda abajo del todo. El tema y el
        keymap van en medio: son colecciones que el detalle no puede tocar y no
        obligan a pisar todo como el fichero entero.
        """
        page, lay = self._new_page()
        lay.addWidget(self._build_detail_prefs())
        # Tema/keymap y fichero completo van **en dos columnas**: son tarjetas
        # cortas y en vertical, con la lista de ajustes, no cabían sin comerse
        # el alto de la ventana.
        side = QHBoxLayout()
        side.setSpacing(16)
        side.addWidget(self._build_style(), 1)
        side.addWidget(self._build_preferences_file(), 1)
        lay.addLayout(side)
        lay.addStretch()
        return self._scrollable(page)

    def _build_style(self) -> QFrame:
        """Tarjeta para llevar el tema y el mapa de teclas como presets.

        No copia el ``userpref`` entero: deja en el destino un preset con
        nombre (``BlenderManager <serie>``) que el usuario puede volver a
        elegir, sin perder lo que tuviera.
        """
        card, lay = settings_card("Theme and keymap")
        hint = QLabel(tr(
            "Copy your theme and key map as named presets, without replacing "
            "the whole preferences file. They arrive as \"BlenderManager\" and "
            "you can pick them again in Blender's preferences."))
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.style_checks = {}
        rows = QVBoxLayout()
        rows.setSpacing(4)
        for key, label, tip in (
            ("theme", "Theme",
             "The colours and sizes of the interface."),
            ("keymap", "Key map", "Your keyboard shortcuts."),
        ):
            check = CheckPill(tr(label))
            check.setChecked(True)
            check.setToolTip(tr(tip))
            rows.addWidget(check)
            self.style_checks[key] = check
        lay.addLayout(rows)

        self.style_status = QLabel("")
        self.style_status.setToolTip(tr(
            "Result of copying the theme and the key map."))
        self.style_status.setWordWrap(True)
        lay.addWidget(self.style_status)

        row = QHBoxLayout()
        row.addStretch()
        self.style_btn = _accent_button(
            tr("Apply theme and key map"),
            tr("Export them from the source version and install them in the "
               "destination as named presets."),
            self.apply_style)
        row.addWidget(self.style_btn)
        lay.addLayout(row)
        return card

    def apply_style(self) -> None:
        """Exporta tema/keymap del origen y los instala en el destino."""
        source_exe, _ = _entry_info(self.source_entry)
        target_exe, target_version = _entry_info(self.target_entry)
        if (not source_exe or not Path(source_exe).is_file()
                or not target_exe or not Path(target_exe).is_file()):
            self.style_status.setText(
                tr("Both versions need an executable to do this."))
            return
        theme = self.style_checks["theme"].isChecked()
        keymap = self.style_checks["keymap"].isChecked()
        if not theme and not keymap:
            self.status_message.emit(tr("Nothing selected"))
            return
        if self._blocked_by_running():
            return
        _, source_version = _entry_info(self.source_entry)
        name = f"{bstyle.STYLE_PREFIX} {minor_of(source_version)}"
        self.style_status.setText(tr("Applying theme and key map..."))
        ticket = self._style_job.start(target_version)

        def worker():
            result = bstyle.copy_style(source_exe, target_exe, name,
                                       theme=theme, keymap=keymap)
            self.style_done.emit({"result": result, "version": target_version,
                                  "ticket": ticket})

        threading.Thread(target=worker, daemon=True).start()

    def _on_style_done(self, payload) -> None:
        if not self._style_job.finish(payload):
            return
        result = payload.get("result") or {}
        applied = result.get("applied") or []
        errors = result.get("errors") or []
        names = {"theme": tr("theme"), "keymap": tr("key map")}
        if applied and not errors:
            done = ", ".join(names.get(item, item) for item in applied)
            self.style_status.setText(
                tr("Applied {items}.", items=done))
            self.status_message.emit(tr("Theme and key map applied."))
            return
        lines = []
        if applied:
            lines.append(tr("Applied {items}.",
                            items=", ".join(names.get(i, i) for i in applied)))
        lines.extend(_failure_block(tr("These parts could not be applied:"),
                                    errors)[1:])
        if not lines:
            lines.append(tr("Nothing was applied."))
        self.style_status.setText("\n".join(lines))
        self.status_message.emit(tr("Theme and key map could not be applied."))

    def _build_preferences_file(self) -> QFrame:
        """Tarjeta para migrar las preferencias (ficheros de ``config``).

        Va aparte del tablero porque es otra cosa: aquí no hay addons ni
        manifiestos, son ficheros de Blender y el usuario decide si los pisa.
        """
        card, lay = settings_card("Preferences file")
        hint = QLabel(tr(
            "Copy the preferences file of the source version. It replaces the "
            "current one (a backup is kept)."))
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.pref_checks = {}
        self.pref_rows = QVBoxLayout()
        self.pref_rows.setSpacing(4)
        lay.addLayout(self.pref_rows)

        warning = QLabel(tr(
            "The startup file replaces your default scene and interface. "
            "Leave it off unless you know what it does."))
        warning.setObjectName("Warning")
        warning.setWordWrap(True)
        lay.addWidget(warning)

        self.pref_warning = QLabel("")
        self.pref_warning.setObjectName("Danger")
        self.pref_warning.setWordWrap(True)
        self.pref_warning.setVisible(False)
        lay.addWidget(self.pref_warning)

        self.copy_prefs_btn = _accent_button(
            tr("Copy preferences"), tr("Copy the selected preference files."),
            self.copy_preference_files)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self._undo_button())
        row.addWidget(self.copy_prefs_btn)
        lay.addLayout(row)
        return card

    def _fill_preference_files(self) -> None:
        """Rellena las casillas de preferencias según el origen/destino."""
        clear_layout(self.pref_rows)
        self.pref_checks = {}
        self.pref_items = []
        if self.source_cfg is None or self.target_cfg is None:
            self.copy_prefs_btn.setEnabled(False)
            return
        self.pref_items = baddons.preference_plan(self.source_cfg, self.target_cfg)
        none = True
        for item in self.pref_items:
            if not item.exists:
                continue
            none = False
            check = CheckPill(item.filename)
            check.setChecked(item.selected)
            check.setEnabled(True)
            check.setToolTip(tr("{name} of the source version:\n{path}",
                                name=item.filename, path=item.source))
            # Lo que ya hay en destino se pisaría: se avisa en el propio texto.
            label = item.filename
            if item.overwrites:
                label += "  ·  " + tr("(replaces the current one)")
            check.setText(label)
            check.toggled.connect(
                lambda checked, it=item: setattr(it, "selected", checked))
            self.pref_rows.addWidget(check)
            self.pref_checks[item.key] = check
        if none:
            self.pref_rows.addWidget(
                muted_note(tr("No preferences found in the source version.")))
        self.copy_prefs_btn.setEnabled(not none)

    def copy_preference_files(self) -> None:
        """Copia los ficheros de preferencias marcados.

        Antes de tocar ``userpref.blend`` hay que asegurarse de que el Blender
        destino no está abierto: al salir, reescribiría el fichero y se perdería
        lo que acabamos de poner (y peor: podría guardar un estado mezclado).
        """
        if self._blocked_by_running():
            return
        if not any(item.selected and item.safe for item in self.pref_items):
            self.status_message.emit(tr("Nothing selected"))
            return
        result = baddons.copy_preference_files(self.pref_items, self.target_cfg)
        self._finish_copy(
            result, tr("Copied {count} preference files.",
                       count=len(result.copied)),
            tr("Some files could not be copied:"),
            lambda item: item.filename)

    def _build_detail_prefs(self) -> QFrame:
        """Tarjeta de preferencias selectivas (una a una, comparadas con fábrica).

        Es la parte "fina": en vez de copiar el ``userpref.blend`` entero,
        pregunta al Blender origen qué cambió respecto a los valores de fábrica
        y deja marcar cada cambio. La lectura es **automática** al elegir la
        versión de origen (arranca Blender una vez); el botón solo sirve para
        reintentarla si algo falló.
        """
        card, lay = settings_card("Preferences in detail")
        hint = QLabel(tr(
            "Pick individual settings changed from Blender's defaults. They are "
            "read automatically from the source version (it starts once, it may "
            "take a moment)."))
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.detail_status = QLabel("")
        self.detail_status.setToolTip(tr(
            "How the reading went. If something could not be read, the "
            "details show up here."))
        self.detail_status.setWordWrap(True)
        lay.addWidget(self.detail_status)

        # Las claves van en su propia área de scroll con alto tope (ver
        # ``DETAIL_SCROLL_HEIGHT``). Los botones quedan **fuera**, como en la
        # biblioteca de carpetas: así no hay que bajar hasta el final de una
        # lista larguísima para pulsarlos.
        self.detail_scroll = _make_scroll("DetailPrefs", DETAIL_SCROLL_HEIGHT,
                                          DETAIL_MIN_HEIGHT)
        # El scroll y su contenido transparentes: el fondo lo pone la tarjeta,
        # y un QScrollArea pinta el suyo por defecto (se vería un rectángulo).
        body = QWidget()
        body.setObjectName("DetailPrefsBody")
        self.detail_rows = QVBoxLayout(body)
        self.detail_rows.setContentsMargins(0, 0, 0, 0)
        self.detail_rows.setSpacing(4)
        self.detail_scroll.setWidget(body)
        # +1: la primera fila siempre es un título de sección.
        self.detail_list = FittedList(
            self.detail_scroll, self.detail_rows, DETAIL_SCROLL_HEIGHT,
            DETAIL_MIN_ROWS + 1, DETAIL_MIN_HEIGHT, self.height)
        self.detail_scroll.setVisible(False)
        lay.addWidget(self.detail_scroll)

        # La casilla de addons va en **su propia línea**: su texto es una frase
        # larga (y en español todavía más) que, metida en la fila de botones,
        # fijaba el ancho mínimo de la tarjeta por encima del de la ventana y el
        # panel se salía por la derecha. En su línea, el mínimo es el del botón
        # más ancho y la tarjeta encoge con la ventana sin recortar el texto.
        # Las preferencias de un addon solo existen si el addon está activado
        # en el destino: con esto se activa en el mismo arranque en que se
        # escriben. Solo se ve cuando hay ajustes de addons en la lista.
        self.enable_addons_check = CheckPill(
            tr("Enable the add-ons these settings need"))
        self.enable_addons_check.setChecked(True)
        self.enable_addons_check.setToolTip(tr(
            "An add-on's settings can only be written if the add-on is enabled "
            "in the destination. Tick this to enable them there first; the "
            "add-on must already be installed in the destination (copy it from "
            "the Add-ons tab if it is not)."))
        self.enable_addons_check.setVisible(False)
        lay.addWidget(self.enable_addons_check)

        # Fila de acciones, con el mismo orden que el tablero de Add-ons:
        # marcar a la izquierda y aplicar a la derecha. "Read again" reintenta
        # la lectura, así que va con ellas.
        row = QHBoxLayout()
        self.detail_load_btn = CardButton(
            tr("Read again"),
            tooltip=tr("Read the settings from the source version again."))
        self.detail_load_btn.clicked.connect(lambda: self._read_source_blender(force=True))
        row.addWidget(self.detail_load_btn)
        row.addStretch()
        self.detail_select_all = CardButton(
            tr("Select all"),
            tooltip=tr("Tick every setting listed below."))
        self.detail_select_all.clicked.connect(
            lambda: self._select_detail(True))
        self.detail_select_none = CardButton(
            tr("Select none"),
            tooltip=tr("Untick them all, to migrate no setting."))
        self.detail_select_none.clicked.connect(
            lambda: self._select_detail(False))
        row.addWidget(self.detail_select_all)
        row.addWidget(self.detail_select_none)
        self.detail_apply_btn = _accent_button(
            tr("Apply to destination"),
            tr("Write the selected settings in the destination version."),
            self.write_detail_prefs)
        row.addWidget(self.detail_apply_btn)
        lay.addLayout(row)
        # Asa en la esquina inferior derecha de la **tarjeta** (no de la fila).
        self.detail_grip = CornerGrip(
            self._resize_detail,
            tooltip=tr("Drag the corner to make the settings list taller or "
                       "shorter."))
        card.set_grip(self.detail_grip)
        self.detail_card = card
        self._show_detail_buttons(False)
        return card

    def _show_detail_buttons(self, loaded: bool) -> None:
        for widget in (self.detail_select_all, self.detail_select_none,
                       self.detail_apply_btn):
            widget.setEnabled(loaded)
        self.detail_load_btn.setEnabled(True)

    def _clear_detail_rows(self) -> None:
        self.detail_checks = []
        clear_layout(self.detail_rows)
        # Sin filas no se enseña el área: un hueco vacío con su barra (y su
        # esquinita) quedaría raro cuando aún no se ha leído nada.
        self.detail_scroll.setVisible(False)

    def _resize_detail(self, delta: int) -> None:
        """Arrastró el asa de la lista de claves (ver ``FittedList``)."""
        self.detail_list.resize(delta)

    def _source_usable(self) -> bool:
        """True si el origen tiene un ejecutable real con el que preguntarle."""
        executable, _ = _entry_info(self.source_entry)
        return bool(executable) and Path(executable).is_file()

    def _read_source_blender(self, force: bool = False) -> None:
        """Lee el estado del origen (addons activos + preferencias) en un hilo.

        Se llama **sola** al cambiar de versión (el usuario no tiene que pulsar
        nada): solo arranca Blender una vez por origen y, si ya se leyó esa
        misma versión, no repite el trabajo. ``force`` rehace la lectura.

        Se hace todo en un arranque para que cambiar de versión cueste un solo
        Blender, no dos. Si ya hay una lectura en marcha no se lanza otra: al
        llegar, ``_on_prefs_loaded`` ve si sigue siendo del origen elegido y,
        si no, vuelve a llamar aquí para leer el nuevo.
        """
        if self._prefs_read.running or self.source_entry is None:
            return
        key = _entry_key(self.source_entry)
        if not force and key == self._source_read_for:
            return
        if not self._source_usable():
            self.detail_status.setText(
                tr("The source version has no executable to read."))
            self._show_detail_buttons(False)
            self._clear_detail_rows()
            return
        executable, version = _entry_info(self.source_entry)
        ticket = self._prefs_read.start(key)
        self.detail_load_btn.setEnabled(False)
        # Se vacía lo que hubiera: mientras se lee, una lista de la lectura
        # anterior haría creer que esos siguen siendo los ajustes de ahora.
        self.detail_prefs = []
        self._clear_detail_rows()
        self.detail_status.setText(tr("Reading settings..."))

        def worker():
            try:
                enabled = blender_runner.enabled_addons(executable)
                user = bprefs.read_preferences(executable)
                factory = bprefs.read_preferences(executable, factory=True)
            except Exception as error:  # noqa: BLE001 - se enseña el motivo
                self.source_read.emit({"version": version, "error": str(error),
                                       "ticket": ticket})
                self.prefs_loaded.emit({"error": str(error), "ticket": ticket})
                return
            self.source_read.emit({"version": version, "enabled": enabled,
                                   "ticket": ticket})
            # ``user`` y ``factory`` son ``PreferenceDump``: valores, nombres y
            # descripciones, y el motivo si alguno no se pudo leer.
            self.prefs_loaded.emit({"user": user, "factory": factory,
                                    "ticket": ticket})

        threading.Thread(target=worker, daemon=True).start()

    def _reading_current_source(self, payload) -> bool:
        """True si ``payload`` es de la lectura en marcha **y** del origen actual.

        Si el usuario cambió de origen mientras se leía, lo leído es de la
        versión anterior y no puede pintarse sobre la nueva.
        """
        return (self._prefs_read.owns(payload)
                and self._prefs_read.key == _entry_key(self.source_entry))

    def _on_source_read(self, payload) -> None:
        """Aplica el estado activado/desactivado del origen al plan.

        Llega antes que ``prefs_loaded`` (las dos las emite el mismo hilo); el
        trabajo se da por cerrado en ``_on_prefs_loaded``, no aquí.
        """
        if not self._reading_current_source(payload):
            return
        if payload.get("enabled") is not None:
            self._source_read_for = self._prefs_read.key
            self.source_enabled = {baddons.addon_id_of(name)
                                   for name in payload["enabled"]}
        # Recalcula el plan con el estado real (marca ``was_enabled``).
        if self.source_cfg is not None and self.target_cfg is not None:
            self._rebuild_plan()
        self._update_detail_status()

    def _update_detail_status(self) -> None:
        """Resume en una línea qué se ha leído del origen: ajustes y addons."""
        active = sum(1 for plan in self.plans if plan.was_enabled)
        text = tr(
            "{changed} settings changed from Blender's defaults · {active} "
            "add-ons enabled in the source.",
            changed=len(self.detail_prefs), active=active)
        skipped = self._detail_skipped
        if skipped:
            # Propiedades que el volcado no pudo leer: el detalle está en el
            # tooltip del estado (lo pone ``_on_prefs_loaded``).
            text += " " + tr("{count} could not be read (hover for details).",
                             count=skipped)
        self.detail_status.setText(text)

    def _on_prefs_loaded(self, payload) -> None:
        """Pinta los ajustes leídos del origen (o el motivo de no poder)."""
        read_for = self._prefs_read.key
        if not self._prefs_read.finish(payload):
            return
        self.detail_load_btn.setEnabled(True)
        if read_for != _entry_key(self.source_entry):
            # Era del origen anterior: se tira y se lee el que hay ahora.
            self.detail_status.setText("")
            self._read_source_blender()
            return
        user, factory = payload.get("user"), payload.get("factory")
        error = payload.get("error")
        if not error and (user is None or not user.ok):
            error = user.error if user is not None else "no dump"
        if not error and not factory.ok:
            error = factory.error
        if error:
            self.detail_prefs = []
            self._clear_detail_rows()
            # El motivo real va en el tooltip: "no se pudo leer" a secas no
            # dice si es que Blender no arranca, tardó demasiado o qué.
            self.detail_status.setText(tr("Could not read the settings."))
            self.detail_status.setToolTip(str(error))
            self._show_detail_buttons(False)
            return
        self.detail_status.setToolTip(
            "\n".join(f"{item.get('path')}: {item.get('reason')}"
                      for item in user.skipped[:40]))
        self._detail_skipped = len(user.skipped)
        changed = bprefs.diff(user.values, factory.values, meta=user.meta)
        env = bprefs.environment_preferences(user.values, factory.values,
                                             meta=user.meta)
        self.detail_prefs = changed + env
        if not self.detail_prefs:
            # Decir solo "no has cambiado nada" despista cuando el motivo es
            # que la propia aplicación restableció esa versión: el usuario sabe
            # que SÍ tenía ajustes y cree que el detector falla. Si hay una
            # instantánea, se dice de dónde viene y cómo recuperarlos. Y la
            # lista se **vacía**: sin esto quedaban las casillas de la lectura
            # anterior y parecía que aún detectaba aquellos ajustes.
            self._clear_detail_rows()
            self.detail_status.setText(self._no_changes_message())
            self._show_detail_buttons(False)
            return
        self._fill_detail_rows()
        self._show_detail_buttons(True)
        self._update_detail_status()

    def _no_changes_message(self) -> str:
        """Por qué no hay nada que copiar, con la causa cuando la sabemos.

        Si hay instantáneas con ajustes es que la app restableció esa versión
        (o el usuario lo hizo desde aquí) y sus ajustes están aparte. Se dice
        **de qué versión** son y dónde se recuperan: la pestaña "Valores de
        fábrica" tiene su propio selector, así que hay que decir que se elija
        esa versión allí, o el usuario la abre con otra y no encuentra nada
        (fue su confusión).
        """
        base = tr("You have no settings changed from Blender's defaults.")
        config = self.source_cfg
        snapshots = bsnap.snapshots_with_settings(config) if config is not None else []
        if not snapshots:
            return base
        _, version = _entry_info(self.source_entry)
        return base + " " + tr(
            "This app has the settings of {program} {version} saved aside from "
            "a previous reset. To put them back, open the \"Factory settings\" "
            "tab and pick {program} {version} there.",
            program=_entry_program(self.source_entry), version=version)

    def _fill_detail_rows(self) -> None:
        self._clear_detail_rows()
        self.detail_checks = []
        self.detail_scroll.setVisible(True)
        for section, items in bprefs.group_by_section(self.detail_prefs):
            header = QLabel(tr(bprefs.section_label(section)))
            header.setObjectName("Muted")
            self.detail_rows.addWidget(header)
            for pref in items:
                row = _PrefRow(pref, environment=bprefs.is_environment(pref.path))
                self.detail_rows.addWidget(row)
                self.detail_checks.append(row)
        _PrefRow.align_columns(self.detail_checks)
        # La casilla de activar addons solo tiene sentido si hay ajustes de
        # addons en la lista.
        self.enable_addons_check.setVisible(
            any(pref.section == "addons" for pref in self.detail_prefs))
        # Alto: el que el usuario haya elegido con el asa o, si no, el del
        # contenido; nunca por debajo de las filas enteras que exige el suelo.
        self.detail_list.fit()
        # Con una lista nueva, al principio (si no, hereda la posición anterior).
        self.detail_scroll.verticalScrollBar().setValue(0)

    def _select_detail(self, checked: bool) -> None:
        for pref in self.detail_prefs:
            pref.selected = checked
        for row in self.detail_checks:
            row.set_checked(checked)

    def write_detail_prefs(self) -> None:
        """Aplica las claves marcadas en el Blender destino."""
        executable, version = _entry_info(self.target_entry)
        if not executable or not Path(executable).is_file():
            self.detail_status.setText(
                tr("The destination version has no executable to write."))
            return
        selected = [p for p in self.detail_prefs if p.selected]
        if not selected:
            self.status_message.emit(tr("Nothing selected"))
            return
        if self._blocked_by_running():
            return
        self.detail_status.setText(tr("Applying settings..."))
        ticket = self._prefs_write.start(version)
        program = _entry_program(self.target_entry)

        enable_addons = (self.enable_addons_check.isVisible()
                         and self.enable_addons_check.isChecked())

        def worker():
            result = bprefs.write_preferences(executable, selected,
                                              enable_addons=enable_addons)
            self.prefs_applied.emit({"result": result, "version": version,
                                     "program": program, "ticket": ticket})

        threading.Thread(target=worker, daemon=True).start()

    def _on_prefs_applied(self, payload) -> None:
        if not self._prefs_write.finish(payload):
            return
        result = payload.get("result") or {}
        version = payload.get("version") or ""
        program = payload.get("program") or "Blender"
        applied = result.get("applied") or []
        errors = result.get("errors") or []
        enabled = [item["module"] for item in result.get("addons_enabled") or []
                   if item.get("enabled")]
        lines = [tr("Applied {count} settings to {program} {version}.",
                    count=len(applied), program=program, version=version)]
        if not applied:
            lines = [tr("No settings were applied.")]
        if enabled:
            lines.append(tr("Enabled add-ons: {names}", names=", ".join(enabled)))
        # Dos motivos distintos, dos listas: "el addon no está activado" tiene
        # arreglo (copiarlo desde Add-ons); "ya no existe" no.
        not_enabled = [item for item in errors
                       if item.get("error") == bprefs.ADDON_NOT_ENABLED]
        missing = [item for item in errors if item not in not_enabled]
        lines.extend(_failure_block(
            tr("These need their add-on enabled in {program} {version} "
               "first (copy it from the Add-ons tab):", program=program,
               version=version),
            (item.get("path") for item in not_enabled)))
        lines.extend(_failure_block(
            tr("These settings no longer exist in this version:"),
            (item.get("path") or "Blender" for item in missing)))
        dialogs.show_info(self, tr("Settings applied"), "\n".join(lines))
        self.status_message.emit(tr("Settings applied"))
        self.detail_status.setText(tr(
            "Applied {count} settings to {program} {version}.",
            count=len(applied), program=program, version=version))
