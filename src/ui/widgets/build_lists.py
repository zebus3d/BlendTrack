"""Las dos listas de versiones: la Nube y las que hay instaladas.

La fila de filtros (canales, rejilla/lista, plataforma y arquitectura), las dos
rejillas con sus tarjetas, el zoom y el refiltrado. Las dos listas comparten
todo menos de dónde salen las filas, así que van juntas: la de la Nube viene
de ``services.api`` y la Local de ``services.installed``.

Es un **mixin** de ``MainWindow`` (ver el docstring de ``main_window.py``): el
canal, la búsqueda y el zoom son estado de la ventana, y las tarjetas mandan
sus acciones a los otros mixins (descargar, lanzar, borrar).
"""

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QScrollArea, QTabBar, QStackedWidget,
                               QVBoxLayout, QWidget)

from i18n import tr
from model.build import minor_of
from services import api, channels, opener
from services import installed as installed_service
from services.downloader import log as download_log
from ui import icons
from ui.fonts import icon_font
from ui.widgets.buttons import IconFlatButton, Pill
from ui.widgets.cards import (BuildCard, GridBuildCard, GridInstalledCard,
                              InstalledCard, card_shadow)
from ui.widgets.layouts import clear_layout
from ui.widgets.menus import card_menu
from ui.widgets.shell import (ARCH_LABELS, CHANNEL_TOOLTIPS, CHANNELS,
                              FILTER_CONTROL_HEIGHT, FILTERS_HEIGHT, MAX_ZOOM,
                              MIN_ZOOM, PLATFORMS, ZOOM_STEP)


class BuildListsMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    def _new_list_view(self):
        """Scroll + QGridLayout (1 columna en lista, N en rejilla)."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setSpacing(10)
        grid.setAlignment(Qt.AlignTop)
        scroll.setWidget(content)
        return scroll, grid

    def _build_lists_view(self) -> QWidget:
        """Zona de listas (Local y Nube) con su fila de filtros encima.

        Los filtros solo tienen sentido aquí: en Migración y Ajustes no son
        listas de compilaciones. Al vivir con ellas, esas vistas usan el alto
        completo (antes se reservaba la fila de 44 px y se ocultaba su contenido
        para que la interfaz no diera un salto al cambiar de vista).
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.filters = self._build_filters()
        lay.addWidget(self.filters)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self._build_store_view())
        self.list_stack.addWidget(self._build_installed_view())
        lay.addWidget(self.list_stack, 1)
        return page

    def _build_store_view(self):
        self.store_scroll, self.store_grid = self._new_list_view()
        return self.store_scroll

    def _build_installed_view(self):
        self.installed_scroll, self.installed_grid = self._new_list_view()
        return self.installed_scroll

    def _columns_for(self, scroll: QScrollArea, card_width: int) -> int:
        """Número de columnas que caben en el ancho visible (mínimo 1).

        OJO: usamos el ancho del ``QStackedWidget``, no el del ``viewport`` de la
        página. Un ``QStackedWidget`` da a las páginas NO activas un tamaño
        reducido (la de ajustes mide 640 en una ventana de 1060), así que el
        viewport de la tienda miente mientras no está delante.
        """
        width = self.stack.width() if self.stack.width() > 0 else self.width() - 74
        available = width - 28  # margins 14+14
        return max(1, available // max(1, card_width + 10))

    def _grid_columns(self, scroll: QScrollArea, grid: QGridLayout, list_width: int) -> int:
        """En modo lista, 1 columna a todo lo ancho; en rejilla, las que quepan."""
        if self.layout_mode == "list":
            return 1
        # Ancho objetivo de una tarjeta de rejilla escalada por el zoom, pero
        # nunca por debajo del mínimo que necesita el contenido.
        target = max(int(300 * self.zoom), self.MIN_CARD_WIDTH)
        return self._columns_for(scroll, target)

    def _fill_grid(self, grid: QGridLayout, cards: list, columns: int) -> None:
        """Coloca las tarjetas en la rejilla, repartiendo el ancho a partes iguales.

        Al dar a todas las columnas el mismo ``stretch`` y NO fijar un ancho a
        las tarjetas, cada una se estira para llenar su celda: la rejilla se
        adapta al ancho de la ventana en vez de dejar huecos a la derecha.
        """
        clear_layout(grid)
        # Reseteamos stretches de una rejilla anterior con más columnas.
        for col in range(24):
            grid.setColumnStretch(col, 1 if col < columns else 0)
        for index, card in enumerate(cards):
            grid.addWidget(card, index // columns, index % columns)

    def _placeholder(self, text: str, hint: str = "") -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        card_shadow(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(20, 24, 20, 24)
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setAlignment(Qt.AlignCenter)
        lay.addWidget(label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("Muted")
            hint_label.setAlignment(Qt.AlignCenter)
            lay.addWidget(hint_label)
        return frame

    def _reflow(self) -> None:
        if self.layout_mode == "grid":
            self._rebuild_store()
            self._rebuild_installed()
        # La lista de Carpetas crece con la ventana: al cambiar el alto hay que
        # volver a medirle el hueco (el resto de páginas no lleva listas).
        self._fit_folder_list()

    def _rebuild_store(self) -> None:
        clear_layout(self.store_grid)
        builds = self._filtered()
        columns = self._grid_columns(self.store_scroll, self.store_grid, 0)
        if not builds:
            # Cada canal vacío tiene su explicación, en vez del genérico.
            if self.channel == "experimental":
                text, hint = tr("No experimental builds right now"), ""
            elif self.channel == "favorites":
                text = tr("No favorites yet")
                hint = tr("Tap the star on a card to keep it here.")
            elif self.channel in self._fork_channels():
                text = tr("No {name} versions right now",
                          name=self._channel_label(self.channel))
                hint = tr("They come from the project's own download page.")
            else:
                text = tr("No versions found")
                hint = tr("Try clearing the search or another channel filter.")
            self._fill_grid(self.store_grid, [self._placeholder(text, hint)],
                            columns)
            return
        grid = self.layout_mode == "grid"
        cards = []
        for index, build in enumerate(builds):
            # La misma regla que al pulsar (``install_build``): en las diarias
            # también cuenta el hash, o una diaria nueva de la misma versión
            # saldría como "Instalada" y al pulsarla se descargaría.
            entry = installed_service.find_installed(self.installed, build)
            installed = entry is not None
            # La cebra es para la lista (filas contiguas); en rejilla las
            # tarjetas van sueltas sobre el fondo y alternar el gris solo
            # ensucia el conjunto.
            zebra = (not grid) and bool(index % 2)
            marked = build.favorite_key in self.settings.favorites
            # Una versión instalada se lanza desde aquí igual que desde Local,
            # así que lleva el mismo botón de consola (misma clave: la serie).
            console = (self._console_state(entry)
                       if installed and self.settings.experimental_features
                       else None)
            if grid:
                card = GridBuildCard(build, installed, zebra, self.zoom, marked,
                                     console=console)
            else:
                card = BuildCard(build, installed, zebra, marked, console=console)
            card.action_clicked.connect(self.install_build)
            card.notes_clicked.connect(self.open_release_notes)
            card.favorite_toggled.connect(self.set_favorite)
            card.console_toggled.connect(self.set_console_for)
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, b=build, c=card: self._show_store_menu(b, c, pos))
            cards.append(card)
        self._fill_grid(self.store_grid, cards, columns)

    def _rebuild_installed(self) -> None:
        clear_layout(self.installed_grid)
        entries = self._filtered_installed()
        columns = self._grid_columns(self.installed_scroll, self.installed_grid, 0)
        if not entries:
            if self.channel == "favorites":
                text = tr("No favorites yet")
                hint = tr("Tap the star on a card to keep it here.")
            elif self.channel in self._fork_channels():
                text = tr("No {name} versions installed",
                          name=self._channel_label(self.channel))
                hint = tr("Download one from the cloud to see it here.")
            else:
                text = tr("No local versions found")
                hint = tr("Download one from the cloud to see it here.")
            self._fill_grid(self.installed_grid, [self._placeholder(text, hint)],
                            columns)
            return
        grid = self.layout_mode == "grid"
        cards = []
        for index, entry in enumerate(entries):
            # Igual que en la tienda: cebra solo en modo lista.
            zebra = (not grid) and bool(index % 2)
            marked = entry.favorite_key in self.settings.favorites
            update = self.updates_by_path.get(str(entry.path))
            # El botón de consola solo se enseña con las opciones experimentales.
            console = (self._console_state(entry)
                       if self.settings.experimental_features else None)
            if grid:
                card = GridInstalledCard(entry, zebra, self.zoom, marked,
                                         update=update, console=console)
            else:
                card = InstalledCard(entry, zebra, marked, update=update,
                                     read_only=self._is_read_only(entry),
                                     console=console)
            card.launch_clicked.connect(self.launch_installed)
            card.delete_clicked.connect(self.delete_installed)
            card.notes_clicked.connect(self.open_release_notes)
            card.favorite_toggled.connect(self.set_favorite)
            card.update_clicked.connect(self.offer_blender_update)
            card.rename_requested.connect(self.rename_installed)
            card.console_toggled.connect(self.set_console_for)
            card.setContextMenuPolicy(Qt.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, e=entry, c=card: self._show_installed_menu(e, c, pos))
            cards.append(card)
        self._fill_grid(self.installed_grid, cards, columns)

    def _installed_menu(self, entry) -> QMenu:
        """Menú contextual de una tarjeta instalada (sin mostrarlo).

        Se separa de ``_show_installed_menu`` para poder comprobarlo sin abrir
        un menú modal (que en un test se queda esperando).
        """
        menu = card_menu(self)
        launch = menu.addAction(tr("Launch"))
        launch.setToolTip(tr("Launch this installed version"))
        launch.triggered.connect(lambda: self.launch_installed(entry))
        if self.settings.experimental_features:
            console = menu.addAction(tr("Launch with console"))
            console.setToolTip(tr("Launch this version with the console visible: Python "
                            "output and script errors."))
            console.triggered.connect(lambda: self._launch_with_console(entry))
        menu.addSeparator()
        open_folder = menu.addAction(tr("Open folder"))
        open_folder.setToolTip(tr("Open the folder where this version is installed."))
        open_folder.triggered.connect(lambda: opener.open_path(entry.path))
        copy = menu.addAction(tr("Copy path"))
        copy.setToolTip(tr("Copy the path of this version to the clipboard."))
        copy.triggered.connect(
            lambda: QApplication.clipboard().setText(str(entry.path)))
        menu.addSeparator()
        remove = menu.addAction(tr("Uninstall"))
        remove.setToolTip(tr("Remove this installed version"))
        remove.triggered.connect(lambda: self.delete_installed(entry))
        return menu

    def _show_installed_menu(self, entry, card, pos) -> None:
        self._installed_menu(entry).exec(card.mapToGlobal(pos))

    def _store_menu(self, build) -> QMenu:
        """Menú contextual de una tarjeta de la tienda (sin mostrarlo)."""
        menu = card_menu(self)
        install = menu.addAction(tr("Download and install"))
        install.setToolTip(tr("Download and install this version"))
        install.triggered.connect(lambda: self.install_build(build))
        notes = menu.addAction(tr("Release notes"))
        notes.setToolTip(tr("Read the release notes for this version"))
        notes.triggered.connect(lambda: self.open_release_notes(build))
        menu.addSeparator()
        copy = menu.addAction(tr("Copy download link"))
        copy.setToolTip(tr("Copy the download link to the clipboard."))
        copy.triggered.connect(
            lambda: QApplication.clipboard().setText(build.url))
        return menu

    def _show_store_menu(self, build, card, pos) -> None:
        self._store_menu(build).exec(card.mapToGlobal(pos))

    # ------------------------------------------------- forks
    @staticmethod
    def _fork_channels() -> tuple:
        """Canales que son un fork (tienen su propia pestaña, ocultable)."""
        return tuple(channels.FORK_TYPES.values())

    @staticmethod
    def _channel_label(channel: str) -> str:
        """Etiqueta traducida de un canal, para los textos de lista vacía."""
        return tr(next((label for key, label in CHANNELS if key == channel),
                       channel))

    def _update_fork_tabs(self) -> None:
        """Enseña solo las pestañas de los forks que el usuario activó.

        Las pestañas de los forks apagados se ocultan (no se quitan), así los
        índices de ``CHANNELS`` no bailan y el canal guardado sigue apuntando a
        lo mismo cuando el fork está encendido.
        """
        if not hasattr(self, "channel_tabs"):
            return
        enabled = set(self.settings.enabled_forks())
        for index, (key, _label) in enumerate(CHANNELS):
            if key in self._fork_channels():
                self.channel_tabs.setTabVisible(index, key in enabled)

    def apply_enabled_forks(self) -> None:
        """Reacciona al interruptor de un fork (Ajustes > Descargas).

        Enseña u oculta su pestaña, y si el canal puesto era el de un fork que
        se acaba de apagar vuelve a "Todas" (su pestaña desaparece).

        El listado se vuelve a pedir **solo si la nube está a la vista**: si el
        interruptor se cambia desde Ajustes, no tiene sentido bajarlo ahí mismo
        (el usuario no lo vería) y ya se pedirá al volver a la nube; así no se
        descarga dos veces. Lo decide ``MainWindow.set_view("store")`` mirando
        ``_builds_forks``.
        """
        self._update_fork_tabs()
        if (self.channel in self._fork_channels()
                and self.channel not in self.settings.enabled_forks()):
            self.set_channel("all")
        if self.view in ("store", "installed"):
            self.refresh(force=True)

    def refresh_installed(self) -> None:
        """Vuelve a escanear las carpetas y repinta las instaladas."""
        self.installed = installed_service.scan_folders(self.settings.library_roots(),
                                                        self.platform)
        self._recompute_updates()
        self._rebuild_installed()
        self.migrate_view.set_installed(self.installed)
        self.recent_view.set_installed(self.installed)
        self.addons_view.set_installed(self.installed)

    def _recompute_updates(self) -> None:
        """Recalcula qué instaladas tienen parche o serie nueva disponible.

        Las builds se filtran por plataforma/arquitectura (``available_for``)
        para no ofrecer un parche que no es para este sistema. Se guardan por
        ruta para que las tarjetas lo consulten al construirse.
        """
        builds = api.available_for(self.builds, self.platform, self.arch)
        updates = installed_service.available_updates(self.installed, builds)
        # Las series que el usuario silenció con "Nunca" no se ofrecen (ni el
        # parche de la tarjeta ni el aviso de salto de serie).
        muted = set(self.settings.ignored_blender_series)
        if muted:
            updates = [u for u in updates
                       if minor_of(u.entry.version) not in muted]
        self.updates_by_path = {
            str(u.entry.path): u.build for u in updates if u.kind == "patch"}
        self.series_updates = [
            u for u in updates if u.kind == "series"]

    # ------------------------------------------------------------- refresco
    def refresh(self, force: bool = False) -> None:
        """Pide el listado de compilaciones y lo pinta cuando llega."""
        self._set_status(tr("Loading..."))
        # Mientras se pide a internet, la barra del pie dice que sigue en ello
        # (no se sabe cuánto tarda: es indeterminada, no un porcentaje).
        self.busy_bar.setVisible(True)

        def worker():
            try:
                builds = api.get_builds(force=force,
                                        forks=self.settings.enabled_forks())
            except Exception as error:  # red, JSON roto...
                download_log(f"refresh failed: {error}")
                builds = []
            self.builds_loaded.emit(builds)

        threading.Thread(target=worker, daemon=True).start()

    def _on_builds_loaded(self, builds) -> None:
        self.builds = builds
        self.busy_bar.setVisible(False)
        # Con qué forks se cargó este listado. Al cambiar un interruptor de fork
        # hay que volver a pedirlo (el listado anterior no traía sus builds), y
        # así se sabe aunque el cambio se haga con Ajustes delante.
        self._builds_forks = list(self.settings.enabled_forks())
        self._recompute_updates()
        self._rebuild_store()
        # Las instaladas también llevan el aviso de parche, y ese aviso depende
        # del listado que acaba de llegar.
        self._rebuild_installed()
        # La ventana puede no tener todavía su ancho final: refloweamos en
        # cuanto el layout esté asentado para calcular bien las columnas.
        QTimer.singleShot(250, self, self._reflow)
        if self.updates_by_path:
            self._show_message(
                tr("{count} Blender updates available",
                   count=len(self.updates_by_path)), 6)
        else:
            self._set_status(tr("Ready"), 2)
        # El salto de serie (5.2 -> 5.3) se ofrece aparte, en un diálogo.
        QTimer.singleShot(400, self, self._offer_series_update)
        if not self._auto_checked:
            self._auto_checked = True
            if self.auto_update:
                QTimer.singleShot(2000, self, lambda: self.check_updates(manual=False))

    def _filtered(self):
        """Aplica plataforma, arquitectura, canal y búsqueda a las compilaciones.

        Usamos ``api.filter_builds`` (función pura y testeada) en vez de
        reimplementar el filtrado aquí: la primera versión del port lo repetía a
        mano y los filtros de canal no filtraban nada.
        """
        builds = api.available_for(self.builds, self.platform, self.arch)
        # El tope de versiones es solo de la tienda: el aviso de actualización
        # de las instaladas (``available_for`` sin recortar) sigue viendo todo,
        # o una diaria antigua de un fork dejaría de ofrecerse.
        builds = self.settings.limit_forks(builds)
        return api.filter_builds(builds, self.channel, self.search,
                                 self.settings.favorites)

    def _filtered_installed(self):
        """Aplica canal y búsqueda a las versiones instaladas.

        Igual que en la tienda, usamos la función pura y testeada
        (``installed_service.filter_installed``) en vez de reimplementarla: la
        primera versión del port solo miraba la búsqueda y las pastillas de
        canal no filtraban nada en esta pestaña.
        """
        return installed_service.filter_installed(self.installed, self.channel,
                                                  self.search,
                                                  self.settings.favorites)

    # ------------------------------------------------------------- filtros
    def set_channel(self, channel: str) -> None:
        """Cambia de canal: Todas, LTS, Estable, Diarias,
        Experimentales o Favoritos.
        """
        self.channel = channel
        # Se recuerda para la próxima vez que se abra la aplicación.
        self.settings.channel = channel
        self.settings.save()
        # Sincroniza la pestaña: el cambio puede venir de ella misma o de otro
        # sitio (por ejemplo, al restaurar el canal guardado).
        index = next((i for i, (key, _) in enumerate(CHANNELS)
                      if key == channel), 0)
        if self.channel_tabs.currentIndex() != index:
            self.channel_tabs.blockSignals(True)
            self.channel_tabs.setCurrentIndex(index)
            self.channel_tabs.blockSignals(False)
        self._rebuild_store()
        self._rebuild_installed()

    def _on_channel_tab_changed(self, index: int) -> None:
        if 0 <= index < len(CHANNELS):
            self.set_channel(CHANNELS[index][0])

    def _on_search_text(self, text: str) -> None:
        self._pending_search = text
        self._search_timer.start(250)

    def _apply_search(self) -> None:
        self.search = self._pending_search.strip().lower()
        self._rebuild_store()
        self._rebuild_installed()

    def set_layout_mode(self, mode: str) -> None:
        """Cambia entre rejilla y lista y recuerda la elección."""
        self.layout_mode = mode
        self.zoom_box.setVisible(
            self.view not in ("settings", "migrate", "recent", "addons")
            and mode == "grid")
        (self.grid_btn if mode == "grid" else self.list_btn).setChecked(True)
        self.settings.layout_mode = mode
        self.settings.save()
        self._rebuild_store()
        self._rebuild_installed()

    def set_zoom(self, value: float) -> None:
        """Fija el tamaño de la rejilla: la etiqueta va al instante y
        la rejilla se reconstruye cuando el slider se para.
        """
        self.zoom = min(MAX_ZOOM, max(MIN_ZOOM, float(value)))
        self._update_zoom_label()
        # En vivo, pero con tope: si no, se reconstruye en cada pixel de
        # arrastre (parpadeo) y el JSON se escribe decenas de veces por segundo.
        if not self._zoom_tick.isActive():
            self._zoom_tick.start()
        self._zoom_settle.start()

    def _rebuild_zoom_views(self) -> None:
        """Refresco en vivo del zoom: solo la vista que se está viendo.

        Reconstruir la otra pestaña (que no se ve) no aporta nada y, con muchas
        tarjetas, suma a que la interfaz se quede sin responder un momento —en
        Windows eso termina sacando la ventana fantasma de "no responde" encima
        de la app. Al soltar el slider se reconstruyen las dos (``_commit_zoom``).
        """
        if self.view == "installed":
            self._rebuild_installed()
        elif self.view == "store":
            self._rebuild_store()

    def _commit_zoom(self) -> None:
        """El slider lleva quieto: paramos y guardamos el ajuste una sola vez."""
        self._zoom_tick.stop()
        self._rebuild_store()
        self._rebuild_installed()
        self.settings.zoom = self.zoom
        self.settings.save()

    def _zoom_enabled(self) -> bool:
        """El zoom solo pinta algo en rejilla y fuera de ajustes/migración."""
        return (self.view not in ("settings", "migrate", "recent", "addons")
                and self.layout_mode == "grid")

    def _set_zoom_value(self, value: float) -> None:
        """Fija el zoom pasando por el slider, para que UI y valor no se separen.

        El porcentaje es entero (el slider va de 1 en 1), así que redondeamos y
        bloqueamos la señal para no llamar dos veces a ``set_zoom``.
        """
        percent = int(round(min(MAX_ZOOM, max(MIN_ZOOM, float(value))) * 100))
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(percent)
        self.zoom_slider.blockSignals(False)
        self.set_zoom(percent / 100.0)

    def zoom_in(self) -> None:
        """Sube un paso el zoom (Ctrl +)."""
        if self._zoom_enabled():
            self._set_zoom_value(self.zoom + ZOOM_STEP)

    def zoom_out(self) -> None:
        """Baja un paso el zoom (Ctrl -)."""
        if self._zoom_enabled():
            self._set_zoom_value(self.zoom - ZOOM_STEP)

    def reset_zoom(self) -> None:
        """Vuelve al zoom de restablecimiento elegido en los ajustes (Ctrl+0)."""
        if self._zoom_enabled():
            self._set_zoom_value(self.settings.reset_zoom)

    def _update_zoom_label(self) -> None:
        self.zoom_label.setText(f"{round(self.zoom * 100)} %")

    def set_favorite(self, item, marked: bool) -> None:
        """Marca o desmarca una serie como favorita (estrella de una tarjeta).

        Se fija el estado que trae la señal en vez de alternarlo: así la estrella
        y el ajuste no se pueden desincronizar aunque llegue dos veces el evento.
        """
        key = getattr(item, "favorite_key", "")
        if not self.settings.set_favorite(key, marked):
            return
        self.settings.save()
        # En el canal de favoritos la lista cambia (la tarjeta entra o sale);
        # en los demás la rejilla sería la misma, así que no la repintamos para
        # no perder la posición del scroll.
        if self.channel == "favorites":
            self._rebuild_store()
            self._rebuild_installed()

    def set_platform(self, label: str) -> None:
        """Cambia la plataforma de destino y repinta."""
        self.platform_label = label
        self.settings.platform = label
        self.settings.save()
        self._rebuild_store()
        self.refresh_installed()

    def set_arch(self, label: str) -> None:
        """Cambia la arquitectura de destino y repinta."""
        self.arch_label = label
        self.settings.arch = label
        self.settings.save()
        self._rebuild_store()
        # Los avisos de parche de las instaladas se calculan para una
        # arquitectura: sin esto seguían siendo los de la anterior (la
        # plataforma sí recalculaba, vía ``refresh_installed``).
        self._recompute_updates()
        self._rebuild_installed()

    def _build_filters(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("Chrome")
        bar.setFixedHeight(FILTERS_HEIGHT)
        lay = QHBoxLayout(bar)
        # Sin margen a la izquierda (las pestañas van pegadas al borde) ni abajo
        # (tocan el fondo de la fila, como pestañas de verdad).
        lay.setContentsMargins(0, 6, 16, 0)
        lay.setSpacing(6)

        # Los canales son excluyentes, así que van en una barra de pestañas: es
        # lo que espera ver quien elige "solo uno de estos". No lleva páginas
        # (Tienda e Instaladas comparten el canal): al cambiar de pestaña se
        # refiltra la lista de debajo.
        self.channel_tabs = QTabBar()
        self.channel_tabs.setObjectName("ChannelTabs")
        self.channel_tabs.setExpanding(False)
        self.channel_tabs.setDrawBase(False)
        # Con los forks hay hasta ocho pestañas (Todas, LTS, Estable, Diarias,
        # Experimentales, Bforartists, UPBGE, Favoritos) y no caben todas en el
        # ancho mínimo de la ventana. Sin botones de scroll, Qt **recorta** la
        # última ("Bforartists" salía como "Bfor...") y parece que no existe:
        # así se descubrió que no se veían los forks. Con ellos, las que no
        # caben quedan accesibles desplazando, en vez de desaparecer.
        self.channel_tabs.setUsesScrollButtons(True)
        self.channel_tabs.setElideMode(Qt.ElideNone)
        self.channel_tabs.setToolTip(tr(
            "Show only one kind of version at a time.\n"
            "\"All\" mixes them; the rest narrow the list down."))
        for index, (key, label) in enumerate(CHANNELS):
            self.channel_tabs.addTab(tr(label))
            self.channel_tabs.setTabToolTip(index, tr(CHANNEL_TOOLTIPS[key]))
        current = next((i for i, (key, _) in enumerate(CHANNELS)
                        if key == self.channel), 0)
        self.channel_tabs.setCurrentIndex(current)
        self.channel_tabs.currentChanged.connect(self._on_channel_tab_changed)
        # Las pestañas de los forks nacen ocultas si no están activados.
        self._update_fork_tabs()
        # La barra de pestañas ocupa el hueco que le queda (con stretch) en vez
        # de quedarse con su tamaño mínimo: así usa el ancho disponible y, si no
        # cabe todo, enseña sus botones de scroll en lugar de recortar.
        lay.addWidget(self.channel_tabs, 1, Qt.AlignBottom)

        # Refrescar va junto a los controles de la lista (y no en la cabecera):
        # refresca **lo que se ve**, la nube o las instaladas, y el tooltip lo
        # dice según la vista (``_update_refresh_tooltip``).
        self.refresh_btn = IconFlatButton(icons.REFRESH)
        self.refresh_btn.setFont(icon_font(16))
        self.refresh_btn.clicked.connect(self.refresh_current)
        lay.addWidget(self.refresh_btn)

        self.layout_group = QButtonGroup(bar)
        self.layout_group.setExclusive(True)
        self.grid_btn = Pill(icons.GRID, tr("Show the versions as a grid of icons."))
        self.grid_btn.setFont(icon_font(14))
        self.list_btn = Pill(icons.LIST, tr("Show the versions as a list of rows."))
        self.list_btn.setFont(icon_font(14))
        for btn, mode in ((self.grid_btn, "grid"), (self.list_btn, "list")):
            self.layout_group.addButton(btn)
            # Misma altura que los tags de canal: la fila queda a ras.
            btn.setFixedHeight(FILTER_CONTROL_HEIGHT)
            btn.clicked.connect(lambda _=False, m=mode: self.set_layout_mode(m))
            lay.addWidget(btn)
        (self.grid_btn if self.layout_mode == "grid" else self.list_btn).setChecked(True)

        self.platform_combo = QComboBox()
        self.platform_combo.setObjectName("FilterCombo")
        self.platform_combo.addItems(list(PLATFORMS.keys()))
        self.platform_combo.setCurrentText(self.platform_label)
        self.platform_combo.setFixedHeight(FILTER_CONTROL_HEIGHT)
        self.platform_combo.setFixedWidth(104)
        self.platform_combo.setToolTip(tr(
            "System the version is for.\n"
            "Change it to download for another computer (for example, to copy "
            "it on a USB stick)."))
        self.platform_combo.currentTextChanged.connect(self.set_platform)
        lay.addWidget(self.platform_combo)

        self.arch_combo = QComboBox()
        self.arch_combo.setObjectName("FilterCombo")
        self.arch_combo.addItems(ARCH_LABELS)
        self.arch_combo.setCurrentText(self.arch_label)
        self.arch_combo.setFixedHeight(FILTER_CONTROL_HEIGHT)
        self.arch_combo.setFixedWidth(82)
        self.arch_combo.setToolTip(tr(
            "Processor type the version is for.\n"
            "x86_64 is the usual one on most PCs; arm64 is for Apple Silicon "
            "and ARM machines."))
        self.arch_combo.currentTextChanged.connect(self.set_arch)
        lay.addWidget(self.arch_combo)
        return bar
