"""La biblioteca de carpetas: dónde vive cada versión de Blender.

La tarjeta de Ajustes > Carpetas, con su lista de filas (``folders.py``), el
alta y baja de carpetas, las casillas de qué tipo recibe cada una, el candado
de solo lectura y el diálogo que ofrece mover lo que ya hay cuando el reparto
cambia.

Es un **mixin** de ``MainWindow`` por el mismo motivo que ``settings_view``:
toca ``self.settings`` y repinta las listas. Lo que decide **a qué carpeta va
cada descarga** está en ``services/channels.py``, que es puro y testeado; aquí
solo está la interfaz de esa decisión.
"""

import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from services import channels, elevate, organizer
from services import settings as settings_service
from services.downloader import log as download_log
from ui.widgets.buttons import CardButton
from ui.widgets.cards import settings_card
from ui.widgets.dialogs import (AppDialog, ProgressDialog, confirm, show_error,
                                show_info)
from ui.widgets.folders import MAX_VISIBLE_ROWS, TYPE_LABELS, FolderRow
from ui.widgets.labels import ElidedLabel
from ui.widgets.layouts import (CornerGrip, FittedList, clear_layout,
                                list_scroll)
from ui.widgets.shell import write_problem


class FolderLibraryMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    # ------------------------------------------------- biblioteca de carpetas
    def _settings_folders_card(self) -> QFrame:
        """Tarjeta con la biblioteca de carpetas.

        Tiene dos caras y **no hay un interruptor que las cambie**: con una
        sola carpeta que se queda con todo se ve el campo de siempre (la app no
        estrena concepto a quien no lo ha pedido), y en cuanto hay dos aparece
        la lista. Es un estado real —cuántas carpetas hay— y no una preferencia
        guardada que pudiera desincronizarse de las carpetas de verdad.
        """
        card, lay = settings_card("Folders", spacing=10)

        self.folders_hint = ElidedLabel("", Qt.ElideRight)
        self.folders_hint.setObjectName("Muted")
        lay.addWidget(self.folders_hint)

        # Aviso de tipos sin carpeta. Es la contrapartida de no tener cadena de
        # reservas: si nadie recibe las diarias hay que decirlo AQUÍ, y no
        # cuando el usuario le dé a descargar una.
        self.folders_warning = ElidedLabel("", Qt.ElideRight)
        self.folders_warning.setObjectName("Warning")
        lay.addWidget(self.folders_warning)

        # --- modo simple: una carpeta, como siempre
        self.simple_box = QWidget()
        self.simple_box.setObjectName("FormRow")
        simple = QVBoxLayout(self.simple_box)
        simple.setContentsMargins(0, 0, 0, 0)
        simple.setSpacing(8)
        row = QHBoxLayout()
        self.dest_input = QLineEdit()
        self.dest_input.setToolTip(tr(
            "Folder where the Blender versions you download are stored.\n"
            "Each version goes in its own subfolder."))
        self.dest_input.editingFinished.connect(self._on_simple_folder_changed)
        row.addWidget(self.dest_input, 1)
        browse = CardButton(tr("Browse..."), tooltip=tr("Choose destination folder"))
        browse.clicked.connect(self.browse_dest)
        row.addWidget(browse)
        simple.addLayout(row)
        split_row = QHBoxLayout()
        split_row.addStretch()
        self.split_btn = CardButton(tr("Separate by channel..."), variant="accent",
                                    tooltip=tr(
            "Add a second folder and choose what goes in each one: for example "
            "the LTS versions on a fast drive and the rest on a big one."))
        self.split_btn.clicked.connect(self.add_folder)
        split_row.addWidget(self.split_btn)
        simple.addLayout(split_row)
        lay.addWidget(self.simple_box)

        # --- modo ramificado: la lista de carpetas
        #
        # La lista **crece con su contenido** en vez de nacer con cuatro filas
        # fijas: antes se quedaba con el alto que le daba Qt (media fila
        # cortada) mientras sobraba sitio debajo. ``FittedList`` la fija al
        # contenido y ``_folder_list_cap`` le dice cuánto cabe de verdad en la
        # pestaña, así que si hay hueco se estira y si no, sale la barra.
        self.folder_list, self.folder_body = list_scroll(
            "FolderList", "FolderListBody", spacing=6, align_top=False)
        self.folder_body.addStretch()
        lay.addWidget(self.folder_list)
        self.folder_fit = FittedList(
            self.folder_list, self.folder_body, self._folder_list_cap, 1,
            MAX_VISIBLE_ROWS * FolderRow.HEIGHT + (MAX_VISIBLE_ROWS - 1) * 6,
            self.height, max_height=self._folder_list_cap)

        add_row = QHBoxLayout()
        add_row.addStretch()
        # Vive FUERA del scroll a propósito: con muchas carpetas seguiría a mano
        # en vez de haber que bajar hasta el final para encontrarlo.
        self.add_folder_btn = CardButton(tr("Add folder"), variant="accent",
                                         tooltip=tr(
            "Add a folder where you already have Blender versions, or where "
            "you want to download them."))
        self.add_folder_btn.clicked.connect(self.add_folder)
        add_row.addWidget(self.add_folder_btn)
        lay.addLayout(add_row)

        # Asa en la esquina inferior derecha de la tarjeta: permite bajar el
        # alto a mano (y entonces sí aparece la barra de scroll) sin tener que
        # encoger la ventana. Por defecto la lista se ajusta sola al hueco.
        self.folder_grip = CornerGrip(
            self._resize_folder_list,
            tooltip=tr("Drag the corner to make the list of folders taller "
                       "or shorter."))
        card.set_grip(self.folder_grip)

        self._rebuild_folder_rows()
        return card

    def _folder_list_cap(self) -> int:
        """Alto que le cabe a la lista de carpetas dentro de su pestaña.

        Se mide desde la página (``SettingsTabPage``) hacia abajo descontando
        lo que ocupa el resto de la tarjeta (título, avisos y el botón de
        añadir). Al ser una función y no un número, se recalcula en cada
        ``fit``: si el usuario agranda la ventana, la lista usa el hueco nuevo
        sin tocar nada.
        """
        page = self.folder_list
        while page is not None and page.objectName() != "SettingsTabPage":
            page = page.parentWidget()
        card = self.folder_list.parentWidget()
        fallback = MAX_VISIBLE_ROWS * FolderRow.HEIGHT + (MAX_VISIBLE_ROWS - 1) * 6
        if page is None or card is None:
            return fallback
        margins = page.layout().contentsMargins()
        available = page.height() - margins.top() - margins.bottom()
        lay = card.layout()
        if lay is None:
            return max(0, available)
        edges = lay.contentsMargins()
        overhead = edges.top() + edges.bottom()
        visible = 0
        for index in range(lay.count()):
            item = lay.itemAt(index)
            widget = item.widget()
            if widget is self.folder_list:
                continue
            if widget is not None:
                if not widget.isVisibleTo(card):
                    continue
                overhead += widget.sizeHint().height()
            else:
                overhead += item.sizeHint().height()
            visible += 1
        overhead += lay.spacing() * max(0, visible - 1)
        return max(0, available - overhead)

    def _on_settings_tab_changed(self, index: int) -> None:
        """Al abrir Carpetas, la lista se mide con la pestaña ya a su tamaño."""
        if index == self.FOLDERS_TAB:
            self._fit_folder_list()

    def _fit_folder_list(self) -> None:
        """Ajusta la lista al hueco actual (y respeta el alto elegido a mano)."""
        fit = getattr(self, "folder_fit", None)
        if fit is None:
            return
        cap = self._folder_list_cap()
        # Si el usuario estiró la lista y ahora la ventana es más baja, el alto
        # elegido ya no cabe: se recorta al hueco en vez de desbordar.
        if fit.chosen is not None and fit.chosen > cap:
            fit.chosen = max(fit.floor(), cap)
        fit.fit()

    def _resize_folder_list(self, delta: int) -> None:
        """Arrastró el asa de la lista de carpetas (ver ``FittedList``)."""
        self.folder_fit.resize(delta)

    def _branched(self) -> bool:
        """True si hay que enseñar la lista en vez del campo de siempre."""
        folders = self.settings.folders
        if len(folders) != 1:
            return True
        return sorted(folders[0].types) != sorted(channels.BUILD_TYPES)

    def _rebuild_folder_rows(self) -> None:
        """Repinta la tarjeta entera (altas, bajas y cambio de modo)."""
        branched = self._branched()
        self.simple_box.setVisible(not branched)
        self.folder_list.setVisible(branched)
        self.add_folder_btn.setVisible(branched)
        self.folder_grip.setVisible(branched)
        self.folders_hint.setText(tr(
            "Each download goes to the folder that takes it. "
            "Locked folders are only scanned.") if branched
            else tr("Blender versions you download are stored here, each one "
                    "in its own subfolder."))

        if not branched:
            self.dest_input.blockSignals(True)
            self.dest_input.setText(self.settings.folders[0].path)
            self.dest_input.blockSignals(False)

        clear_layout(self.folder_body, keep_stretch=True)
        self.folder_rows = {}
        for index, folder in enumerate(self.settings.folders):
            row = FolderRow(folder, zebra=bool(index % 2),
                            missing=not self._folder_exists(folder.path),
                            types=self.settings.active_build_types())
            row.type_toggled.connect(self._on_folder_type_toggled)
            row.writable_toggled.connect(self._on_folder_writable_toggled)
            row.remove_requested.connect(self._on_folder_remove_requested)
            self.folder_body.insertWidget(self.folder_body.count() - 1, row)
            self.folder_rows[channels.normalize_path(folder.path)] = row
        # El aviso de tipos huérfanos ocupa alto, así que se decide antes de
        # medir el hueco que le queda a la lista.
        self._refresh_folder_warning()
        self._fit_folder_list()
        self._refresh_dest_summary()

    @staticmethod
    def _folder_exists(path: str) -> bool:
        """``is_dir`` tolerante: una unidad desconectada no es un error."""
        try:
            return Path(path).expanduser().is_dir()
        except OSError:
            return False

    def _refresh_folder_warning(self) -> None:
        # Solo se avisa de los tipos activos: los forks apagados no se pueden
        # descargar, así que no pueden quedar huérfanos todavía.
        orphans = channels.orphan_types(self.settings.folders,
                                        self.settings.active_build_types())
        if not orphans:
            self.folders_warning.setText("")
            self.folders_warning.setVisible(False)
            return
        names = ", ".join(tr(TYPE_LABELS[key]) for key in orphans)
        self.folders_warning.setText(
            tr("No folder for: {types}. Those builds cannot be downloaded "
               "until you tick one.", types=names))
        self.folders_warning.setVisible(True)

    def _refresh_dest_summary(self) -> None:
        if not hasattr(self, "dest_summary"):
            return
        folder = channels.owner_of(self.settings.folders, channels.TYPE_STABLE)
        folder = folder or next((f for f in self.settings.folders if f.types), None)
        self.dest_summary.setText(
            tr("New downloads go to: {folder}", folder=folder.path) if folder
            else tr("No folder is set to receive downloads."))

    def _show_folders_hint(self) -> None:
        """Presenta la biblioteca de carpetas a quien actualiza la app.

        Lo primero que dice es lo que más tranquiliza: que no hay que tocar
        nada y que no se ha movido ningún archivo. Lo segundo, para qué sirve.
        """
        self.settings.folders_hint_shown = True
        self.settings.save()
        folder = self.settings.folders[0].path if self.settings.folders else ""
        dialog = AppDialog(
            self, tr("Your Blender folders"),
            tr("Nothing has changed on disk and you do not have to set up "
               "anything: your versions are still in {folder} and keep working "
               "exactly as before.", folder=folder)
            + "\n\n"
            + tr("What is new is that you can now add more folders and choose "
                 "what goes in each one: for example the LTS versions on a "
                 "fast SSD and the daily builds on a big drive. You can also "
                 "point the app at folders where you already had Blender, and "
                 "lock them so nothing is ever written there."))
        choice = {"open": False}
        dialog.add_button(tr("Not now"), on_click=dialog.reject,
                          tooltip=tr("Close this message. You can open\n"
                                     "Settings > Folders whenever you want."))
        dialog.add_button(
            tr("Show me"), variant="accent",
            on_click=lambda: (choice.update(open=True), dialog.accept()),
            tooltip=tr("Opens Settings > Folders, where you add folders and\n"
                       "tick what each one receives."))
        dialog.exec()
        if choice["open"]:
            self.set_view("settings")
            self.settings_tabs.setCurrentIndex(self.FOLDERS_TAB)

    # ------------------------------------------------------- reorganizar
    def _offer_reorg(self, root: str) -> None:
        """Si algo dejó de encajar en esa carpeta, ofrece moverlo.

        Se llama **después** de guardar el cambio: lo que se decide aquí es
        solo qué hacer con lo que ya estaba dentro, no si el cambio se aplica.
        Quedarse quieto es una respuesta válida y es la que se ofrece primero.
        """
        moves = organizer.misplaced(self.installed, self.settings.folders, root)
        if not moves:
            return
        counts = {}
        for move in moves:
            counts[move.build_type] = counts.get(move.build_type, 0) + 1
        detail = "\n".join(
            f"    · {tr(TYPE_LABELS[key])}: {value}"
            for key, value in counts.items())
        message = (root + "\n\n"
                   + tr("There are {count} versions here that this folder no "
                        "longer takes:", count=len(moves))
                   + "\n" + detail + "\n\n"
                   + tr("You can move them to the folder that takes them, or "
                        "leave them where they are."))
        dialog = AppDialog(self, tr("Folder contents"), message)
        choice = {"move": False}
        dialog.add_button(tr("Leave them here"), on_click=dialog.reject,
                          tooltip=tr(
            "Nothing is moved.\n"
            "Those versions stay in this folder and keep showing up in Local.\n"
            "Only new downloads follow the boxes you just ticked."))
        dialog.add_button(
            tr("Move them"), variant="accent",
            on_click=lambda: (choice.update(move=True), dialog.accept()),
            tooltip=tr(
                "Each version is moved to the folder that takes its kind.\n"
                "Nothing is deleted: a version is only removed from here once "
                "the copy is complete.\n"
                "With big folders on another drive this takes a while."))
        dialog.exec()
        if choice["move"]:
            self._start_move(moves)

    def _start_move(self, moves) -> None:
        """Mueve las versiones en un hilo, con barra de progreso.

        El diálogo es **modal** a propósito: mover carpetas por debajo de la
        lista de instaladas mientras se usa es pedir que el usuario lance algo
        que ya no está donde dice la tarjeta.
        """
        self._move_cancel = threading.Event()
        self._move_dialog = ProgressDialog(
            self, tr("Moving versions"), "",
            primary_text="", secondary_text=tr("Cancel"))
        self._move_dialog.set_text(
            tr("Moving {name} ({done} of {total})",
               name=moves[0].entry.name, done=1, total=len(moves)))
        # Cancelar (o Esc) cierra el diálogo; sin esto el traslado seguía en
        # segundo plano, ya sin el modal que impide tocar la lista mientras.
        # ``move_build`` mira la marca entre fichero y fichero y deja el origen
        # intacto si no llegó a completar la copia.
        self._move_dialog.rejected.connect(self._move_cancel.set)

        def worker():
            moved = failed = 0
            last = ""
            for index, move in enumerate(moves):
                if self._move_cancel.is_set():
                    break
                self.folder_move_progress.emit(index, len(moves),
                                               move.entry.name)
                try:
                    organizer.move_build(
                        move, should_cancel=self._move_cancel.is_set)
                    moved += 1
                except organizer.OrganizerError as error:
                    if error.code == "cancelled":
                        break
                    # Que una build esté abierta no puede abortar las otras.
                    download_log(f"move failed ({move.entry.name}): {error}")
                    failed += 1
                    last = str(error)
            self.folder_move_done.emit(moved, failed, last)

        threading.Thread(target=worker, daemon=True).start()
        self._move_dialog.exec()

    def _on_folder_move_progress(self, done: int, total: int, name: str) -> None:
        if self._move_dialog is None:
            return
        self._move_dialog.set_progress(int(done * 100 / total) if total else 0)
        self._move_dialog.set_text(tr("Moving {name} ({done} of {total})",
                                      name=name, done=done + 1, total=total))

    def _on_folder_move_done(self, moved: int, failed: int, error: str) -> None:
        if self._move_dialog is not None:
            self._move_dialog.accept()
            self._move_dialog = None
        self.refresh_installed()
        if failed:
            show_error(self, tr("Moving versions"),
                       tr("{failed} versions could not be moved.", failed=failed)
                       + ("\n\n" + error if error else ""))
        elif moved:
            self._show_message(tr("{moved} versions moved", moved=moved), 6)

    def _save_folders(self) -> None:
        """Guarda y refresca todo lo que depende de las carpetas."""
        self.settings.save()
        self.refresh_installed()
        self._rebuild_store()
        self._refresh_folder_warning()
        self._refresh_dest_summary()

    def _choose_folder(self, current: str, title: str) -> str:
        """Abre el diálogo de carpetas del sistema y devuelve la elegida (o "")."""
        return QFileDialog.getExistingDirectory(self, title,
                                                current or str(Path.home()))

    def browse_dest(self) -> None:
        """Pide la carpeta de descargas con el diálogo del sistema (modo simple)."""
        current = self.settings.folders[0].path if self.settings.folders else ""
        folder = self._choose_folder(current, tr("Choose destination folder"))
        if folder:
            self.dest_input.setText(folder)
            self._on_simple_folder_changed()

    def _on_simple_folder_changed(self) -> None:
        """Cambia la ruta de la única carpeta (modo simple)."""
        text = self.dest_input.text().strip()
        if not text or not self.settings.folders:
            return
        if channels.normalize_path(text) == channels.normalize_path(
                self.settings.folders[0].path):
            return
        self.settings.folders[0].path = text
        self._save_folders()

    # -------------------------------------------- biblioteca de carpetas
    def add_folder(self) -> None:
        """Da de alta una carpeta nueva en la biblioteca.

        Propone marcarle los tipos que **nadie** recibe todavía: es lo que hace
        que el primer clic en "Separar por canales" deje algo útil sin tener
        que explicar nada. Si ya están todos repartidos entra sin tipos (solo
        se escanea), que es lo menos invasivo.
        """
        folder = self._choose_folder("", tr("Choose a folder with Blender versions"))
        if not folder:
            return
        problem = self._folder_problem(folder)
        if problem == "duplicate":
            show_info(self, tr("Folders"),
                      tr("That folder is already in the list."))
            return

        writable = not write_problem(folder)
        types = (channels.orphan_types(self.settings.folders,
                                       self.settings.active_build_types())
                 if writable else [])
        self.settings.folders.append(
            settings_service.Folder(path=folder, types=types, writable=writable))
        self._save_folders()
        self._rebuild_folder_rows()
        if not writable:
            self._show_message(tr(
                "Added as read-only: that folder does not allow writing."), 6)

    def _folder_problem(self, candidate: str) -> str:
        """Por qué no se puede añadir esa carpeta, o "" si sí se puede.

        Lo único que se rechaza es **la misma carpeta dos veces**. Anidar sí se
        permite, y es una petición razonable: tener ``Blender 3D`` de raíz y
        ``Blender 3D/Experimentales`` dentro es de las formas más naturales de
        organizarse. No duplica nada porque ``installed._scan_root`` solo mira
        los hijos **directos** de cada raíz y descarta los que no parecen una
        build, así que la carpeta hija no se cuenta dos veces (lo fija
        ``test_una_subcarpeta_no_duplica_las_versiones``).

        Se compara con ``resolve()`` y no por texto: si no, en Windows
        ``C:\\Blender`` y ``c:\\blender`` entrarían como dos carpetas
        distintas y todas sus versiones saldrían duplicadas en Local.
        """
        def resolved(path):
            try:
                return Path(path).expanduser().resolve()
            except OSError:
                return Path(path).expanduser()

        target = resolved(candidate)
        for folder in self.settings.folders:
            if resolved(folder.path) == target:
                return "duplicate"
        return ""

    def _on_folder_type_toggled(self, path: str, build_type: str,
                                marked: bool) -> None:
        """Marca o desmarca un tipo, respetando que solo tenga un dueño."""
        folder = self.settings.folder_for(path)
        if folder is None:
            return
        previous = (channels.owner_of(self.settings.folders, build_type)
                    if marked else None)
        if marked:
            for other in self.settings.folders:
                if build_type in other.types and other is not folder:
                    other.types = [item for item in other.types
                                   if item != build_type]
            if build_type not in folder.types:
                folder.types = [item for item in channels.BUILD_TYPES
                                if item in folder.types or item == build_type]
        else:
            folder.types = [item for item in folder.types if item != build_type]
        self._save_folders()
        # Solo se repintan las filas afectadas: reconstruir la lista entera
        # desde la señal de una de sus casillas destruiría el widget que la
        # acaba de emitir.
        for target in {path, previous.path if previous else None}:
            row = self.folder_rows.get(channels.normalize_path(target or ""))
            entry = self.settings.folder_for(target or "")
            if row is not None and entry is not None:
                row.set_folder(entry)
        if previous is not None:
            self._show_message(tr(
                "{type} versions now go to {folder}",
                type=tr(TYPE_LABELS[build_type]), folder=folder.path), 6)
        # Al desmarcar, lo que ya estaba dentro puede dejar de encajar; al
        # marcar, es la carpeta que pierde el tipo la que se queda con builds
        # que ya no recibe.
        self._offer_reorg(previous.path if previous is not None else path)

    def _on_folder_writable_toggled(self, path: str, writable: bool) -> None:
        """Abre o cierra el candado de una carpeta.

        Cerrarlo apaga sus casillas: no se puede recibir una descarga donde la
        aplicación no escribe (es la invariante de ``settings.Folder``).
        """
        folder = self.settings.folder_for(path)
        if folder is None:
            return
        folder.writable = writable
        if not writable:
            folder.types = []
        elif not folder.types:
            # Al abrir el candado se le devuelven los tipos que no tenga nadie.
            # Antes se quedaba vacía, así que cerrar y volver a abrir dejaba la
            # carpeta sin recibir nada --y si era la única, la aplicación sin
            # sitio donde descargar-- sin que el usuario hubiera tocado ninguna
            # casilla. Es la misma regla que al añadir una carpeta nueva.
            folder.types = channels.orphan_types(
                self.settings.folders, self.settings.active_build_types())
        self._save_folders()
        row = self.folder_rows.get(channels.normalize_path(path))
        if row is not None:
            row.set_folder(folder)
        if not writable:
            # Cerrar el candado deja la carpeta sin recibir nada: lo de dentro
            # puede tener ahora otra casa. Pero de aquí no se saca nada (lo
            # impide ``organizer.plan_reorg``), así que no saldrá diálogo.
            self._offer_reorg(path)

    def _on_folder_remove_requested(self, path: str) -> None:
        """Quita una carpeta de la lista (sin tocar el disco)."""
        folder = self.settings.folder_for(path)
        if folder is None:
            return
        if not confirm(self, tr("Remove folder"),
                       tr("Remove {folder} from the list?", folder=path)
                       + "\n\n"
                       + tr("Nothing is deleted from disk: the versions there "
                            "just stop being shown."),
                       accept_text=tr("Remove")):
            return
        self.settings.folders = [item for item in self.settings.folders
                                 if item is not folder]
        self._save_folders()
        self._rebuild_folder_rows()

    def _destination_for(self, build) -> str:
        """Carpeta donde va esta compilación, o "" si nadie recibe su tipo.

        Se delega en ``Settings.destination_for`` para que la regla viva con
        los ajustes (y se pueda probar sin montar la ventana).
        """
        return self.settings.destination_for(build)

    def _no_folder_for(self, build_type: str) -> None:
        """Avisa de que nadie recibe ese tipo y lleva a arreglarlo."""
        dialog = AppDialog(
            self, tr("Download"),
            tr("No folder is set to receive {type} builds.",
               type=tr(TYPE_LABELS[build_type]))
            + "\n\n"
            + tr("Open Settings and tick {type} on the folder where you want "
                 "them.", type=tr(TYPE_LABELS[build_type])))
        choice = {"open": False}
        dialog.add_button(tr("Close"), on_click=dialog.reject,
                          tooltip=tr("Close this message and download nothing."))
        dialog.add_button(
            tr("Open folder settings"), variant="accent",
            on_click=lambda: (choice.update(open=True), dialog.accept()),
            tooltip=tr("Takes you to Settings > Folders, where you choose "
                       "which folder\nreceives each kind of version."))
        dialog.exec()
        if choice["open"]:
            self.set_view("settings")
            self.settings_tabs.setCurrentIndex(self.FOLDERS_TAB)

    def _ask_other_folder(self, build, destination: str, problem: str) -> bool:
        """Ofrece arreglar la carpeta cuando no se puede escribir en ella.

        Devuelve True si al final se puede escribir ahí (porque el usuario ha
        elegido otra carpeta o ha dado permiso de administrador), para que la
        descarga se reintente sola. La carpeta nueva se da de alta con **el
        tipo de esta compilación**, así que el arreglo dura: antes se adivinaba
        cuál era la carpeta afectada comparando cadenas de rutas y, de paso, se
        cambiaba la carpeta global por defecto por un fallo puntual.
        """
        message = (tr("Could not write to the destination folder:")
                   + "\n\n" + destination + "\n\n" + problem
                   + "\n\n" + tr("Windows protects folders like Program Files. "
                                 "Pick a folder you can write to, such as "
                                 "Documents or another drive."))
        dialog = AppDialog(self, tr("Download failed"), message)
        choice = {"other": False, "admin": False}
        dialog.add_button(tr("Close"), on_click=dialog.reject,
                          tooltip=tr("Give up on this download for now."))
        dialog.add_button(
            tr("Choose another folder"), variant="accent",
            on_click=lambda: (choice.update(other=True), dialog.accept()),
            tooltip=tr("Pick a folder you can write to. It is added to your\n"
                       "folders with this kind of version ticked, so the fix "
                       "lasts."))
        if elevate.available():
            dialog.add_button(
                tr("Grant permission (admin)"),
                on_click=lambda: (choice.update(admin=True), dialog.accept()),
                tooltip=tr("Asks Windows for permission to write in that "
                           "folder.\nYou will see the system's own prompt."))
        dialog.exec()

        if choice["admin"]:
            # Windows: pedir el UAC una vez para dar permiso de escritura sobre
            # esta carpeta al usuario, en vez de cambiar de sitio.
            if self._grant_permission(destination):
                return True
            show_error(self, tr("Download failed"),
                       tr("The folder still could not be made writable."))
            return False
        if not choice["other"]:
            return False
        build_type = channels.type_of_build(build)
        folder = self._choose_folder(destination, tr("Choose destination folder"))
        if not folder:
            return False
        existing = self.settings.folder_for(folder)
        if existing is None:
            existing = settings_service.Folder(path=folder, types=[],
                                               writable=True)
            self.settings.folders.append(existing)
        existing.writable = True
        # El tipo cambia de dueño: solo puede estar en una carpeta.
        for other in self.settings.folders:
            if other is not existing:
                other.types = [item for item in other.types if item != build_type]
        existing.types = [item for item in channels.BUILD_TYPES
                          if item in existing.types or item == build_type]
        self._save_folders()
        self._rebuild_folder_rows()
        return True

    def _grant_permission(self, destination: str) -> bool:
        """Pide el UAC y espera a poder escribir en ``destination``.

        Lanza este mismo binario elevado con ``--grant-access`` (ver
        ``services.elevate``). El proceso elevado es rápido, así que se espera
        un poco comprobando si la carpeta ya deja escribir; mientras, se
        procesan eventos para que la ventana no se quede congelada.
        """
        if not elevate.available():
            return False
        if not elevate.relaunch_elevated(["--grant-access", destination]):
            # El usuario ha cancelado el UAC.
            self._show_message(tr("The permission request was cancelled."), 5)
            return False
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not write_problem(destination):
                return True
            QApplication.processEvents()
            time.sleep(0.2)
        return False
