"""La ventana principal: su cáscara y las listas de versiones.

Aquí viven la cabecera, la barra lateral, el pie, la navegación entre vistas
y el ciclo de vida de la ventana (bandeja, cerrar, tamaño). Lo demás está
repartido por responsabilidad en módulos vecinos, que se montan como
**mixins** de esta clase:

* ``build_lists.py`` — las dos listas de versiones (Nube y Local).
* ``settings_view.py`` — la pantalla de Ajustes y sus controles.
* ``folder_library.py`` — la biblioteca de carpetas (dónde vive cada versión).
* ``downloads.py`` — descargar, instalar, lanzar y borrar versiones de Blender.
* ``updates.py`` — actualizar BlenderManager.
* ``shell.py`` — las tablas y los widgets que comparten todos.

Son mixins y no objetos independientes porque todos trabajan sobre el estado
de **esta misma ventana** (``self.settings``, el estado, las listas): meterlos
en otro objeto solo movería el acoplo de sitio. Lo que se gana es que cada
fichero se pueda leer entero; lo que de verdad no depende de la interfaz ya
está en ``services/``.
"""

from PySide6.QtCore import QEvent, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame,
                               QHBoxLayout, QLabel,
                               QLineEdit, QProgressBar, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

import i18n
from i18n import tr
from paths import ASSETS_DIR
from services import detector, updater
from services import installed as installed_service
from services import opener
from services import settings as settings_service
from services.downloader import Downloader
from services.launcher import Launcher
from ui import icons
from ui import theme as t
from ui.fonts import glyph_icon, icon_font
from ui.widgets.addons import AddonsView
from ui.widgets.build_lists import BuildListsMixin
from ui.widgets.buttons import CardButton, IconFlatButton, SideButton
from ui.widgets.busy import BusyBar
from ui.widgets.cards import logo_shadow
from ui.widgets.donate import DonationPopup, paypal_url
from ui.widgets.downloads import DownloadFlowMixin
from ui.widgets.folder_library import FolderLibraryMixin
from ui.widgets.grid_keys import GridKeysMixin
from ui.widgets import motion
from ui.widgets.migrate import MigrateView
from ui.widgets.recent import RecentView
from ui.widgets.settings_view import SettingsViewMixin
from ui.widgets.shell import (ARCH_LABELS, DEFAULT_WINDOW_HEIGHT,
                              DEFAULT_WINDOW_WIDTH, EXPERIMENTAL_VIEWS,
                              MAX_ZOOM, MIN_WINDOW_HEIGHT, MIN_WINDOW_WIDTH,
                              MIN_ZOOM, PLATFORM_LABELS, PLATFORMS,
                              SECTION_TITLES, ZoomSlider)
# Estas dos no se usan aquí: se reexportan porque son de la ventana aunque
# vivan en ``shell`` (que existe solo para romper la circularidad), y hay
# tests que las piden aquí, que es donde se espera encontrarlas.
from ui.widgets.shell import CHANNELS, ZOOM_STEP  # noqa: F401
from ui.widgets.tray import TrayIcon
from ui.widgets.updates import UpdateFlowMixin
from ui.widgets.view_keys import ViewKeysMixin

# El enlace del botón de donación del pie (PayPal.Me). Abre en el navegador
# por defecto y con el entorno limpio (``services.opener``). Sin importe en la
# URL: así el donante elige la cantidad libremente (con un importe prefijado,
# PayPal.Me la fija y no deja cambiarla).
SUPPORT_URL = "https://paypal.me/zebus3d"


class MainWindow(BuildListsMixin, SettingsViewMixin, FolderLibraryMixin,
                 DownloadFlowMixin, UpdateFlowMixin, ViewKeysMixin,
                 GridKeysMixin, QWidget):
    """Pantalla principal: cabecera, filtros, listas y pie con progreso/zoom."""

    # Señales para marshar el resultado de los hilos al hilo de la interfaz.
    # (QTimer.singleShot desde un hilo de Python NO es fiable para esto.)
    builds_loaded = Signal(int, object, object)   # petición, forks, builds
    download_error = Signal(str)
    extract_done = Signal(str)
    dmg_manual = Signal(str)   # macOS: no se pudo instalar, se revela a mano
    update_result = Signal(str, object, bool)
    update_applied = Signal(str)
    update_manual = Signal()   # descargada, pero hay que instalarla a mano
    source_chosen = Signal(int, object, object)   # petición, build, Source
    source_update_done = Signal(bool, str)
    release_notes_result = Signal(bool)   # abierta o no
    folder_move_progress = Signal(int, int, str)   # hechas, total, nombre
    folder_move_done = Signal(int, int, str)       # movidas, fallidas, error
    splash_ready = Signal(str, str)   # serie, fichero (galería)

    # Índice de la pestaña "Carpetas" dentro de Ajustes (va la segunda, justo
    # después de Descargas, porque es lo que más se toca de las dos).
    FOLDERS_TAB = 1

    # Ancho MÍNIMO que necesita una tarjeta de rejilla para no recortarse
    # (medido: 227 px con el logo, las etiquetas y el botón). Si se piden más
    # columnas que las que caben a ese ancho, la última se sale del viewport.
    MIN_CARD_WIDTH = 230

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{tr('Blender Manager')} {updater.app_version()}")
        # El mínimo sale de ``shell`` (una sola definición): antes estaba a mano
        # aquí (880x540) y pisaba el de allí, así que bajar ``MIN_WINDOW_*`` no
        # dejaba encoger la ventana.
        self.setMinimumSize(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)

        self.settings = settings_service.Settings.load()
        i18n.set_language(self.settings.language)
        self.system = detector.detect()
        self.current_version = updater.app_version()

        detected_platform = PLATFORM_LABELS.get(self.system.os_name, "GNU/Linux")
        detected_arch = "x86_64" if self.system.arch in ("amd64", "x86_64") else self.system.arch
        self.platform_label = (
            self.settings.platform if self.settings.platform in PLATFORMS else detected_platform
        )
        self.arch_label = (
            self.settings.arch if self.settings.arch in ARCH_LABELS else detected_arch
        )
        # Las carpetas NO se copian a atributos de la ventana: la lista de
        # ``settings.folders`` es la única fuente de verdad y las filas la leen
        # y la escriben directamente. Los espejos de antes (``dest_folder``,
        # ``lts_folder``...) eran cinco sitios donde el mismo dato podía
        # quedarse viejo.
        self.folder_rows = {}
        self._move_dialog = None
        self._move_cancel = None
        self.launch_args = self.settings.launch_args
        self.launch_env = self.settings.launch_env
        self.delete_archive = bool(self.settings.delete_archive)
        # Bandeja del sistema: dos decisiones independientes (cerrar y
        # minimizar). ``_tray`` se crea perezosamente (solo si hace falta).
        self.close_to_tray = bool(self.settings.close_to_tray)
        self.minimize_to_tray = bool(self.settings.minimize_to_tray)
        self.start_minimized = bool(self.settings.start_minimized)
        self._tray = None
        # Salidas de verdad (actualizar la app o reiniciar tras un git pull):
        # esas no pueden acabar escondidas en la bandeja.
        self._force_quit = False
        self.auto_update = bool(self.settings.auto_update)
        self.periodic_update = bool(self.settings.periodic_update)
        self.layout_mode = (self.settings.layout_mode
                            if self.settings.layout_mode in ("grid", "list", "gallery")
                            else "grid")
        try:
            self.zoom = min(MAX_ZOOM, max(MIN_ZOOM, float(self.settings.zoom)))
        except (TypeError, ValueError):
            self.zoom = settings_service.DEFAULT_ZOOM
        # Zoom al que vuelven Ctrl+0 y el Ctrl+clic del slider. Se acota aquí
        # (como ``zoom``) para que un settings.json editado a mano no deje la
        # rejilla en un valor imposible.
        try:
            self.settings.reset_zoom = min(
                MAX_ZOOM, max(MIN_ZOOM, float(self.settings.reset_zoom)))
        except (TypeError, ValueError):
            self.settings.reset_zoom = settings_service.DEFAULT_ZOOM

        self.view = "store"
        # A dónde vuelve el botón de ajustes al pulsarlo por segunda vez.
        self._previous_view = "store"
        # El filtro que estaba puesto la última vez (Favoritos incluido).
        self.channel = self.settings.channel
        self.search = ""

        self.downloader = Downloader()
        self.update_downloader = Downloader()
        # Elección de fuente en curso (ver ``downloads.install_build``): el
        # número de la última petición y el de la que se espera, o None.
        self._source_request = 0
        self._pending_source = None
        self.launcher = Launcher()

        self.builds = []
        # Forks con los que se cargó el listado en memoria (ver
        # ``build_lists._on_builds_loaded``). Sirve para saber si, al encender o
        # apagar un fork, lo que hay ya no vale y hay que volver a pedirlo.
        self._builds_forks = []
        # Número de la última petición del listado (ver ``refresh``).
        self._refresh_request = 0
        self.installed = installed_service.scan_folders(self.settings.library_roots(),
                                                        self.platform)
        self.view = "installed" if self.installed else "store"
        # Actualizaciones de Blender detectadas para lo que ya tienes instalado:
        # un parche de la misma serie (botón en la tarjeta) o una serie nueva
        # (diálogo). Se recalculan al cargar compilaciones y al reescanear.
        self.updates_by_path = {}
        self.series_updates = []
        # Series ya ofrecidas en esta sesión: el diálogo no se repite cada vez
        # que se refresca el listado.
        self._series_offered = set()
        # Instalada que hay que borrar cuando acabe de extraerse la nueva
        # (cuando el usuario eligió "Reemplazar").
        self._replace_entry = None

        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self._set_status(tr("Ready")))
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._apply_search)
        self._pending_search = ""
        # El zoom no reconstruye en cada tick del slider (eso era lo que hacia
        # parpadear la rejilla), pero tampoco espera a que sueltes: refresca
        # como mucho cada 80 ms y, cuando el slider lleva 250 ms quieto, para y
        # guarda. Medido: reconstruir la tienda entera cuesta ~7-25 ms.
        self._zoom_tick = QTimer(self)
        self._zoom_tick.setInterval(80)
        self._zoom_tick.timeout.connect(self._rebuild_zoom_views)
        self._zoom_settle = QTimer(self)
        self._zoom_settle.setSingleShot(True)
        self._zoom_settle.setInterval(250)
        self._zoom_settle.timeout.connect(self._commit_zoom)
        self._update_checking = False
        self._auto_checked = False
        self._source_dialog = None
        # Versión de la app de la que ya se avisó en esta sesión (para que el
        # chequeo periódico no repita el diálogo cada X minutos).
        self._offered_update_tag = ""
        # Chequeo periódico de la propia aplicación (BlenderManager).
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._periodic_update_check)
        self._apply_update_timer()

        # Cooldown del reescaneo al recuperar el foco: cambiar de ventana no
        # puede lanzar un escaneo de disco cada vez.
        self._focus_refresh = QTimer(self)
        self._focus_refresh.setSingleShot(True)
        self._focus_refresh.setInterval(2000)

        # Conectamos las señales de los hilos ANTES de montar la UI.
        self.builds_loaded.connect(self._on_refresh_result)
        self.download_error.connect(self._on_download_error)
        self.extract_done.connect(self._on_extract_done)
        self.dmg_manual.connect(self._on_dmg_manual)
        self.update_result.connect(self._on_update_result)
        self.update_applied.connect(self._on_update_applied)
        self.update_manual.connect(self._on_update_manual)
        self.source_update_done.connect(self._on_source_update_done)
        self.release_notes_result.connect(self._on_release_notes_result)
        self.folder_move_progress.connect(self._on_folder_move_progress)
        self.folder_move_done.connect(self._on_folder_move_done)
        self.splash_ready.connect(self._on_splash_ready)
        self._splash_pending = set()
        self.source_chosen.connect(self._on_source_chosen)

        self._build_ui()
        # Los temporizadores llevan ``self`` de contexto: si la ventana se
        # destruye antes de que disparen (pasa en los tests, que crean y
        # borran ventanas a docenas), Qt no llama al callback en vez de
        # hacerlo sobre un objeto ya borrado.
        QTimer.singleShot(100, self, lambda: self.refresh(force=False))
        # Quien venga de una versión anterior merece enterarse de que ahora
        # puede repartir sus Blender por varias carpetas. Se enseña una sola
        # vez y **después** de que la ventana esté montada.
        if not self.settings.folders_hint_shown:
            QTimer.singleShot(400, self, self._show_folders_hint)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())
        # El stack principal tiene las tres zonas: las listas (Local y Nube),
        # Migración y Ajustes. Las listas llevan dentro su fila de filtros.
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_lists_view())
        self.migrate_view = MigrateView()
        self.migrate_view.set_system(self.system.os_name, self.system.arch)
        self.migrate_view.status_message.connect(self._show_message)
        self.migrate_view.snapshot_keep_changed.connect(self.set_snapshot_keep)
        self.migrate_view.set_snapshot_keep(self.settings.snapshot_keep)
        self.stack.addWidget(self.migrate_view)
        self.recent_view = RecentView(open_file=self.launch_installed)
        self.recent_view.set_system(self.system.os_name)
        self.recent_view.status_message.connect(self._show_message)
        self.stack.addWidget(self.recent_view)
        self.addons_view = AddonsView()
        self.addons_view.set_system(self.system.os_name, self.system.arch)
        self.addons_view.status_message.connect(self._show_message)
        self.stack.addWidget(self.addons_view)
        self.stack.addWidget(self._build_settings_view())
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        root.addWidget(self._build_footer())
        self._set_view(self.view)
        # La vista de instaladas no se rellena sola: hay que poblarla al arrancar
        # (si no, al abrir en esa pestaña se vería vacía hasta el primer refresco).
        self._rebuild_installed()
        self.migrate_view.set_installed(self.installed)
        self.recent_view.set_installed(self.installed)
        self.addons_view.set_installed(self.installed)
        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        """Atajos del zoom. Ctrl+= es el mismo '+' sin pulsar Shift, y muchos
        teclados no mandan 'Ctrl++' al soltar; registramos los dos."""
        bindings = {
            "Ctrl++": self.zoom_in,
            "Ctrl+=": self.zoom_in,
            "Ctrl+-": self.zoom_out,
            "Ctrl+0": self.reset_zoom,
        }
        for keys, slot in bindings.items():
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
        # Esc cancela la descarga en curso, igual que el botón Cancelar. Solo
        # está activo mientras hay una descarga: así no se traga el Escape de
        # otros sitios (cancelar el renombrado de una tarjeta, cerrar un panel)
        # cuando no hay nada que parar.
        self.cancel_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.cancel_shortcut.setEnabled(False)
        self.cancel_shortcut.activated.connect(self.cancel_download)
        # Alt+1..9: pasa a la pestaña N de la pantalla actual (los canales de
        # Local/Nube, las pestañas de Ajustes y las de Migración). Los números
        # **sin** Alt cambian de sección (ver ``ViewKeysMixin``); con Alt, de
        # pestaña dentro de la pantalla. Cuenta solo las pestañas visibles: los
        # canales de los forks apagados no ocupan número.
        for number in range(1, 10):
            shortcut = QShortcut(QKeySequence(f"Alt+{number}"), self)
            shortcut.activated.connect(lambda n=number: self._select_tab(n))

    def _tab_bar_for_view(self):
        """La barra de pestañas de la pantalla actual, o ``None`` si no tiene."""
        if self.view in ("store", "installed"):
            return self.channel_tabs
        if self.view == "settings":
            return self.settings_tabs
        if self.view == "migrate":
            return self.migrate_view.tabs
        return None

    def _select_tab(self, number: int) -> None:
        """Selecciona la pestaña ``number`` (1..N) de la pantalla actual.

        Cuenta solo las visibles: las pestañas de los forks se esconden si el
        fork está apagado y no deben ocupar un número.
        """
        tabs = self._tab_bar_for_view()
        if tabs is None:
            return
        visible = [index for index in range(tabs.count())
                   if tabs.isTabVisible(index)]
        if 1 <= number <= len(visible):
            tabs.setCurrentIndex(visible[number - 1])

    def _build_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("Chrome")
        header.setFixedHeight(72)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 10, 16, 8)
        lay.setSpacing(14)

        logo = QLabel()
        pix = QPixmap(str(ASSETS_DIR / "images" / "app_icon.png"))
        if not pix.isNull():
            logo.setPixmap(pix.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo_shadow(logo, 52)
        logo.setToolTip(tr("Blender Manager"))
        lay.addWidget(logo)

        # Dos líneas: la marca (grande) y la sección actual (un poco más
        # pequeña), para saber siempre dónde estás. La barra de título de la
        # ventana sigue llevando la marca + versión.
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.app_label = QLabel(tr("Blender Manager"))
        self.app_label.setObjectName("HeaderTitle")
        # La misma sombra suave que el logo (y las tarjetas): despega el texto
        # del gris de la cabecera.
        logo_shadow(self.app_label, 19)
        titles.addWidget(self.app_label)
        self.section_label = QLabel("")
        self.section_label.setObjectName("HeaderSection")
        logo_shadow(self.section_label, 17)
        titles.addWidget(self.section_label)
        lay.addLayout(titles)
        lay.addStretch()

        # El buscador va en la cabecera (a la derecha, como en cualquier app);
        # el botón de refrescar vive con los filtros, que es lo que refresca.
        self.header_tools = QWidget()
        self.header_tools.setObjectName("HeaderTools")  # fondo transparente (QSS)
        # Sin esto el bloque se estira y reparte el hueco que sobra. Es el mismo
        # caso que el zoom del pie.
        self.header_tools.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        tools = QHBoxLayout(self.header_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.setSpacing(10)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchField")
        self.search_input.setPlaceholderText(tr("Search..."))
        self.search_input.setToolTip(tr(
            "Filter by version, branch or file name as you type."))
        self.search_input.setFixedWidth(240)
        # El buscador NO toma el foco solo: ni al abrir la ventana (Qt lo deja
        # en el primer control) ni con Tab. Así los dígitos no se quedan en el
        # campo y los atajos de sección (1-6) funcionan; se escribe en él al
        # hacer clic, que es cuando el usuario quiere buscar de verdad.
        self.search_input.setFocusPolicy(Qt.ClickFocus)
        self.search_input.textChanged.connect(self._on_search_text)
        # Icono de lupa dentro del campo, como en la versión original.
        self.search_input.addAction(
            glyph_icon(icons.SEARCH, 14, t.MUTED), QLineEdit.LeadingPosition)
        tools.addWidget(self.search_input)
        lay.addWidget(self.header_tools, 0, Qt.AlignRight)
        return header

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(74)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(11, 14, 11, 14)
        lay.setSpacing(10)
        self.side_group = QButtonGroup(sidebar)
        self.side_group.setExclusive(True)
        self.side_buttons = {}
        for key, glyph, tip, hotkey in (
            ("installed", icons.INSTALLED, tr(
                "Show the versions you already have on this computer."), 1),
            ("store", icons.STORE, tr(
                "Show the versions you can download from the cloud."), 2),
            ("recent", icons.CLOCK, tr(
                "Show the .blend files you opened recently, by Blender "
                "version."), 3),
            ("addons", icons.PUZZLE, tr(
                "Manage the add-ons and extensions of an installed version "
                "without opening Blender."), 4),
        ):
            # Con padre desde el principio: un widget sin padre al que se le
            # hace ``setVisible(True)`` antes de entrar en el layout se enseña
            # como una **ventana suelta** (se veía un cuadradito en el centro de
            # la pantalla al arrancar, que desaparecía al colocarse el botón).
            btn = SideButton(glyph, f"{tip}  [{hotkey}]", parent=sidebar)
            btn.setFont(icon_font())
            self.side_group.addButton(btn)
            btn.clicked.connect(lambda _=False, k=key: self.set_view(k))
            lay.addWidget(btn)
            # El gestor de add-ons es nuevo: oculto hasta activar las opciones
            # experimentales (Ajustes > Avanzado), como Migración.
            if key == "addons":
                btn.setVisible(self.settings.experimental_features)
            self.side_buttons[key] = btn
        lay.addStretch()
        # Migración va abajo (encima de Ajustes): es una herramienta puntual, no
        # una pestaña de uso diario como la tienda o las instaladas.
        migrate_btn = SideButton(icons.MIGRATE, tr(
            "Copy add-ons, extensions and preferences from one Blender version "
            "to another.") + "  [5]", parent=sidebar)
        migrate_btn.setFont(icon_font())
        self.side_group.addButton(migrate_btn)
        migrate_btn.clicked.connect(lambda: self.set_view("migrate"))
        lay.addWidget(migrate_btn)
        self.side_buttons["migrate"] = migrate_btn
        settings_btn = SideButton(icons.SETTINGS, tr("Settings.") + "  [6]")
        settings_btn.setFont(icon_font())
        self.side_group.addButton(settings_btn)
        settings_btn.clicked.connect(lambda: self.set_view("settings"))
        lay.addWidget(settings_btn)
        self.side_buttons["settings"] = settings_btn
        return sidebar

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("Chrome")
        footer.setFixedHeight(40)
        lay = QHBoxLayout(footer)
        # Margen derecho corto: el icono de donación queda pegado a la esquina
        # (con 16 quedaba un hueco grande a su derecha).
        lay.setContentsMargins(16, 4, 4, 4)
        lay.setSpacing(12)
        self.status_label = QLabel(tr("Ready"))
        self.status_label.setObjectName("Muted")
        lay.addWidget(self.status_label)

        # Barra de "esto está trabajando" para lo que no tiene porcentaje: pedir
        # el listado a internet (una descarga sí lo tiene, y usa la de abajo).
        # Va pegada al texto de estado, en el hueco que queda a su derecha.
        self.busy_bar = BusyBar()
        self.busy_bar.setFixedWidth(120)
        self.busy_bar.setVisible(False)
        self.busy_bar.setToolTip(tr("Loading the list of versions..."))
        lay.addWidget(self.busy_bar)

        # Progreso + porcentaje + cancelar van en su PROPIO contenedor con
        # stretch, y no sueltos en el pie: un widget OCULTO no aporta su stretch
        # al layout, así que si el progreso iba suelto, al ocultarse el zoom se
        # quedaba a la izquierda y el pie se descolocaba. Con el contenedor
        # (siempre visible) el zoom queda pegado a la derecha en los dos casos.
        self.progress_box = QWidget()
        self.progress_box.setObjectName("HeaderTools")  # fondo transparente
        progress_lay = QHBoxLayout(self.progress_box)
        progress_lay.setContentsMargins(0, 0, 0, 0)
        progress_lay.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(12)
        self.progress.setValue(0)
        self.progress.setToolTip(tr("Download progress."))
        # Solo se muestra mientras hay una descarga en curso.
        self.progress.setVisible(False)
        progress_lay.addWidget(self.progress, 1)
        self.percent = QLabel("")
        self.percent.setToolTip(tr("Download progress."))
        self.percent.setVisible(False)
        progress_lay.addWidget(self.percent)
        self.cancel_btn = CardButton(tr("Cancel"),
                                     tooltip=tr("Stop the download running now (Esc)."))
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setVisible(False)
        progress_lay.addWidget(self.cancel_btn)
        lay.addWidget(self.progress_box, 1)

        # Zoom: slider + porcentaje, juntos y pegados al borde derecho.
        self.zoom_box = QWidget()
        self.zoom_box.setObjectName("HeaderTools")  # fondo transparente (QSS)
        self.zoom_box.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        zoom_lay = QHBoxLayout(self.zoom_box)
        zoom_lay.setContentsMargins(0, 0, 0, 0)
        zoom_lay.setSpacing(6)
        self.zoom_slider = ZoomSlider(Qt.Horizontal)
        self.zoom_slider.setRange(int(MIN_ZOOM * 100), int(MAX_ZOOM * 100))
        self.zoom_slider.setValue(int(self.zoom * 100))
        self.zoom_slider.setFixedWidth(130)
        self.zoom_slider.valueChanged.connect(lambda v: self.set_zoom(v / 100.0))
        self.zoom_slider.reset_requested.connect(self.reset_zoom)
        self.zoom_slider.setToolTip(tr(
            "Size of the cards in the grid.\n"
            "Ctrl + and Ctrl - change it, Ctrl+0 resets it."))
        zoom_lay.addWidget(self.zoom_slider)
        self.zoom_label = QLabel()
        self.zoom_label.setObjectName("Muted")
        self.zoom_label.setToolTip(tr("Current zoom."))
        # Ancho fijo para que el pie no "baile" al pasar de 100 % a 180 %.
        self.zoom_label.setFixedWidth(40)
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._update_zoom_label()
        zoom_lay.addWidget(self.zoom_label)
        lay.addWidget(self.zoom_box, 0, Qt.AlignRight)

        # Donación: un icono pequeño pegado a la esquina de abajo a la derecha.
        # Abre un diálogo para elegir el importe y luego PayPal en el navegador
        # por defecto (con el entorno limpio).
        self.donate_btn = IconFlatButton(icons.DONATE, tr(
            "Support Blender Manager with a PayPal donation."))
        self.donate_btn.setFont(icon_font())
        self.donate_btn.clicked.connect(self._offer_donation)
        lay.addWidget(self.donate_btn, 0, Qt.AlignRight)
        return footer

    def _offer_donation(self) -> None:
        """Abre el popup para elegir el importe de la donación.

        El importe va en la URL de PayPal.Me (``/5``) y ahí queda fijo, así que
        lo elige el usuario en el popup: no se le impone. Si ya estaba abierto,
        el botón lo cierra.
        """
        popup = getattr(self, "_donation", None)
        if popup is not None and popup.isVisible():
            popup.close()
            return
        self._donation = DonationPopup(self, self.donate_btn,
                                       self._open_donation)
        self._donation.popup()

    @staticmethod
    def _open_donation(amount: float) -> None:
        opener.open_url(paypal_url(SUPPORT_URL, amount))

    # -------------------------------------------------------------- estado
    def _set_status(self, text: str, timeout: int = 0) -> None:
        self.status_label.setText(text)
        if timeout:
            self._status_timer.start(timeout * 1000)
        else:
            self._status_timer.stop()

    def _show_message(self, message: str, timeout: int = 4) -> None:
        self._set_status(message, timeout)

    def _set_downloading(self, active: bool) -> None:
        self.progress.setVisible(active)
        self.percent.setVisible(active)
        self.cancel_btn.setVisible(active)
        # El atajo de Escape va con el botón: solo cancela mientras hay una
        # descarga (ver ``_install_shortcuts``).
        shortcut = getattr(self, "cancel_shortcut", None)
        if shortcut is not None:
            shortcut.setEnabled(active)

    @property
    def platform(self) -> str:
        """Plataforma de destino elegida en la barra de filtros."""
        return PLATFORMS.get(self.platform_label, "linux")

    @property
    def arch(self) -> str:
        """Arquitectura de destino, tal y como la comparan las compilaciones.

        La barra ofrece ``x86_64``/``arm64`` y ``api.normalize_arch`` guarda las
        compilaciones con esos mismos nombres (Blender llama ``amd64`` a la de
        Windows), así que aquí se devuelve la etiqueta tal cual. Antes se
        traducía a ``amd64`` para Windows y la tienda salía **vacía**: las
        builds ya venían normalizadas a ``x86_64`` y no coincidían con el filtro.
        """
        return self.arch_label

    def set_view(self, view: str) -> None:
        """Cambia entre la tienda, las instaladas y los ajustes.

        El botón de ajustes funciona como un interruptor (igual que en la
        versión Kivy): la primera vez entra y la segunda vuelve a la vista en la
        que estabas. Entre tienda e instaladas no hay interruptor, el clic
        cambia de pestaña y ya.
        """
        if (view in EXPERIMENTAL_VIEWS
                and not self.settings.experimental_features):
            # Esas vistas están ocultas (opciones experimentales apagadas): su
            # botón no se ve, pero cualquier llamada debe quedar sin efecto.
            view = "installed" if self.installed else "store"
        if view == "settings" and self.view == "settings":
            # Segundo clic en ajustes: volvemos a donde estábamos.
            view = self._previous_view
        elif view != "settings":
            # Recordamos la última vista que no eran ajustes.
            self._previous_view = view
        # Ajustes baja desde arriba al entrar y sube al salir (como en la
        # versión Kivy y en las preferencias de Blender 2.4x); el resto de
        # cambios de vista es instantáneo. Se captura cómo estaba el stack
        # **antes** de cambiar de página: es la imagen que se va.
        sliding = (self.isVisible()
                   and (view == "settings") != (self.view == "settings"))
        before = self.stack.grab() if sliding else None
        self.view = view
        self._set_view(view)
        if before is not None:
            motion.slide_stack(self.stack, before, down=view == "settings")
        if view == "installed":
            self.refresh_installed()
        elif view == "store":
            # El zoom en vivo solo reconstruye la vista visible, así que la
            # tienda puede haberse quedado con el tamaño viejo.
            self._rebuild_store()
            # Si se encendió o apagó un fork con Ajustes delante, el listado en
            # memoria ya no vale (le faltan sus builds o le sobran): se vuelve a
            # pedir al entrar en la nube. Sin esto, cambiar el interruptor no
            # tenía efecto visible hasta reiniciar la app.
            if self._builds_forks != self.settings.enabled_forks():
                self.refresh(force=True)
        elif view == "migrate":
            # Las instaladas pueden haber cambiado desde la última vez.
            self.migrate_view.set_installed(self.installed)
        elif view == "recent":
            # Los recientes se leen al entrar: Blender puede haber abierto
            # ficheros desde la última vez.
            self.recent_view.set_installed(self.installed)
        elif view == "addons":
            # Los addons se leen al entrar (arranca Blender): no tiene sentido
            # hacerlo al abrir la app si el usuario no va a mirarlos.
            self.addons_view.set_installed(self.installed)
            self.addons_view.read()

    def _set_view(self, view: str) -> None:
        # Tienda e Instaladas comparten la zona de listas (con sus filtros); lo
        # que cambia entre ellas es la página del sub-stack.
        if view in ("store", "installed"):
            self.stack.setCurrentIndex(0)
            self.list_stack.setCurrentIndex(0 if view == "store" else 1)
            show_tools = True
        else:
            self.stack.setCurrentIndex(
                {"migrate": 1, "recent": 2, "addons": 3,
                 "settings": 4}.get(view, 0))
            show_tools = False
        for key, btn in self.side_buttons.items():
            btn.setChecked(key == view)
        self.section_label.setText(tr(SECTION_TITLES.get(view, "")))
        # El buscador y el zoom del pie solo aplican a las listas; en Migración,
        # Recientes y Ajustes se ocultan (no arrastran salto: van en la cabecera
        # y el pie).
        if not show_tools and self.focusWidget() is self.search_input:
            # Al esconder el buscador, Qt pasa el foco al siguiente control
            # (el primer botón lateral) y su aro de foco parece una selección.
            # Se deja en la zona de contenido **antes** de esconderlo.
            self.stack.setFocus(Qt.OtherFocusReason)
        self.header_tools.setVisible(show_tools)
        self.zoom_box.setVisible(show_tools and self.layout_mode == "grid")
        self._update_refresh_tooltip()
        # El botón de los efectos de tarjeta (esquina de la rejilla) solo se
        # enseña en las listas y en modo rejilla.
        self._sync_grid_corner()

    def _update_refresh_tooltip(self) -> None:
        """El botón de refrescar explica qué va a refrescar en esta vista."""
        if self.view == "installed":
            text = tr("Look for the installed Blender versions again.\n"
                      "Use it if you added or removed one outside this app.")
        else:
            text = tr("Read the Blender versions available to download again.\n"
                      "Use it if something looks out of date.")
        self.refresh_btn.setToolTip(text)

    def refresh_current(self) -> None:
        """Refresca la lista que está a la vista: instaladas o nube."""
        if self.view == "installed":
            self.refresh_installed()
        else:
            self.refresh(force=True)

    def showEvent(self, event):
        """Deja el icono en la bandeja mientras la ventana esté a la vista.

        El icono vive siempre que la app corre, así que se muestra también al
        enseñar la ventana por primera vez (la app arrancada normal, la
        restaurada desde la bandeja o tras un ``git pull``). Si el escritorio
        no tiene bandeja no se crea nada.
        """
        super().showEvent(event)
        self._ensure_tray()

    def resizeEvent(self, event):
        """Refluye la rejilla al cambiar el ancho (recalcula columnas)."""
        super().resizeEvent(event)
        if not hasattr(self, "_resize_timer"):
            self._resize_timer = QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._reflow)
        self._resize_timer.start(200)

    def changeEvent(self, event):
        """Minimizar a la bandeja, si el usuario lo pidió.

        El evento llega **antes** de que el gestor de ventanas termine de
        minimizar, así que el ocultado se difiere al siguiente ciclo: esconder
        la ventana en el acto hacía que algunos escritorios volvieran a
        mostrarla. Si la bandeja no está disponible no se hace nada: minimizar
        sin un icono al que volver dejaría la app inaccesible.
        """
        super().changeEvent(event)
        # ``getattr`` por si un cambio de estado llega durante el propio
        # ``__init__`` de QWidget, antes de que existan los atributos.
        if (event.type() == QEvent.WindowStateChange and self.isMinimized()
                and getattr(self, "minimize_to_tray", False)
                and not getattr(self, "_force_quit", False)
                and TrayIcon.available()):
            QTimer.singleShot(0, self, self._hide_to_tray)
        if (event.type() == QEvent.ActivationChange
                and getattr(self, "_focus_refresh", None) is not None
                and self.isActiveWindow()):
            self._refresh_on_focus()

    def _refresh_on_focus(self) -> None:
        """Reescanea las instaladas al recuperar el foco, con un cooldown.

        Blender puede haber instalado o renombrado algo fuera de la app. No se
        repite si se acaba de hacer (``_focus_refresh``) ni mientras hay una
        descarga o una actualización en curso: sería pisarla.
        """
        if (self.downloader.running or self.update_downloader.running
                or self._focus_refresh.isActive()):
            return
        self._focus_refresh.start()
        self.refresh_installed()

    # ------------------------------------------------------------- bandeja
    def _ensure_tray(self) -> TrayIcon:
        """Crea el icono de la bandeja la primera vez que se necesita.

        El objeto se reutiliza entre ocultados: crear y destruir el icono en
        cada uno era el camino a más de un fantasma en la bandeja.
        """
        if self._tray is None and TrayIcon.available():
            self._tray = TrayIcon(self)
            self._tray.restore_requested.connect(self._restore_from_tray)
            self._tray.quit_requested.connect(self._quit_from_tray)
            # El icono está siempre visible mientras la app corre: con la
            # ventana abierta y escondida. Así el usuario lo tiene a mano para
            # volver o salir de verdad, y queda claro que la app sigue viva.
            self._tray.show()
        return self._tray

    def _hide_to_tray(self) -> None:
        """Oculta la ventana, dejando el icono en la bandeja.

        El aviso de "sigue en la bandeja" se enseña **una sola vez** (y se
        recuerda en los ajustes): sin él, esconder la ventana es indistinguible
        de que la app se haya cerrado.
        """
        tray = self._ensure_tray()
        if tray is None:
            # Sin bandeja, esconder la ventana dejaría la app inaccesible.
            return
        self.hide()
        if not self.settings.tray_hint_shown:
            tray.notify(
                tr("Still running in the system tray"),
                tr("BlenderManager keeps running in the tray. Click its icon to "
                   "bring the window back."))
            self.settings.tray_hint_shown = True
            self.settings.save()

    def _restore_from_tray(self) -> None:
        """Vuelve a mostrar la ventana (el icono sigue en la bandeja)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_from_tray(self) -> None:
        """Salir de verdad desde el menú de la bandeja."""
        self._quit_app()

    def _quit_app(self) -> None:
        """Cierra la ventana saltándose el "cerrar a la bandeja".

        Lo usan el menú de la bandeja y los reinicios internos (tras aplicar una
        actualización o un ``git pull``), que sin esto se quedarían escondidos
        en la bandeja en vez de terminar.
        """
        self._force_quit = True
        if self._tray is not None:
            self._tray.hide()
        self.close()
        QApplication.quit()

    def closeEvent(self, event):
        """Vuelca el zoom pendiente y la geometría de la ventana.

        Si el usuario activó "cerrar a la bandeja", la X **no** cierra: se
        ignora el evento y la ventana se oculta (los ajustes ya se han guardado
        antes, en ``save_window_state``). Solo se hace si la bandeja existe; si
        no, cerrar cierra.
        """
        self.save_window_state()
        if (self.close_to_tray and not self._force_quit
                and TrayIcon.available()):
            event.ignore()
            self._hide_to_tray()
            return
        # Cierra de verdad: el icono ya no tiene a qué volver, así que se
        # retira. Sin esto quedaría un fantasma en la bandeja tras cerrar.
        if self._tray is not None:
            self._tray.hide()
        super().closeEvent(event)

    def save_window_state(self) -> None:
        """Guarda el zoom pendiente, el tamaño y la posición de la ventana.

        El zoom va con retardo, así que si se cierra mientras el slider se mueve
        el guardado aún no ha corrido. El tamaño y la **posición** se guardan
        al cerrar (y no en cada ``resizeEvent``/arrastre) para no escribir el
        JSON a cada tirón; la próxima vez ``main.py`` arranca con estas medidas
        y en el mismo monitor.

        Lo llama también el auto-update **antes** de relanzar: el proceso nuevo
        arranca mientras este sigue vivo y lee ``settings.json`` enseguida, así
        que si el tamaño esperaba al ``closeEvent`` (un segundo después) el
        nuevo abría con el de la última vez que se cerró la app de verdad y, al
        cerrarse, lo escribía encima del bueno.
        """
        self._zoom_settle.stop()
        self._zoom_tick.stop()
        geometry = self._normal_geometry()
        changed = False
        if self.zoom != self.settings.zoom:
            self.settings.zoom = self.zoom
            changed = True
        if (geometry.width(), geometry.height()) != (self.settings.window_width,
                                                      self.settings.window_height):
            self.settings.window_width = geometry.width()
            self.settings.window_height = geometry.height()
            changed = True
        # En Wayland el compositor decide dónde va la ventana y no deja ni leer
        # su posición: guardar (0, 0) sería basura que, en una sesión X11, la
        # abriría pegada a la esquina. Así que ahí no se guarda.
        if self._window_can_be_placed() and (
                not self.settings.window_pos_saved
                or (geometry.x(), geometry.y()) != (self.settings.window_x,
                                                    self.settings.window_y)):
            self.settings.window_x = geometry.x()
            self.settings.window_y = geometry.y()
            self.settings.window_pos_saved = True
            changed = True
        if changed:
            self.settings.save()

    def _normal_geometry(self):
        """Geometría con la que reabrir: la normal, no la de maximizada.

        Si se cierra maximizada (o a pantalla completa) se guarda la posición y
        el tamaño "normales" anteriores, para no arrancar siempre maximizada sin
        estarlo de verdad ni perder el monitor en el que estaba.
        """
        if self.isMaximized() or self.isFullScreen():
            return self.normalGeometry()
        return self.geometry()

    def restore_window_geometry(self) -> None:
        """Reabre la ventana donde el usuario la dejó la última vez.

        La posición se guarda al cerrar (``closeEvent``); aquí se restaura, y
        con ella el monitor. Si la pantalla ya no está o la posición quedó fuera
        de todo monitor (cambio de resolución, monitor desconectado), se centra
        en vez de abrir en un sitio inalcanzable.

        En **Wayland** no se hace nada: el compositor decide dónde colocar la
        ventana y el cliente no puede moverla (``move()`` no tiene efecto). Ahí
        la posición "recordada" es cosa de una regla de ventana de KWin, no de
        la aplicación.
        """
        if not self._window_can_be_placed():
            return
        if self._saved_position_is_visible():
            self.move(self.settings.window_x, self.settings.window_y)
        else:
            self.center_on_screen()

    @staticmethod
    def _window_can_be_placed() -> bool:
        """True si la aplicación puede leer y fijar la posición de la ventana.

        En Wayland no puede (el compositor manda); en X11, Windows y macOS sí.
        Se mira el **backend de Qt** (``platformName``) y no la variable de
        entorno: los tests corren con el plugin *offscreen*, donde sí se puede
        mover, aunque la sesión sea Wayland.
        """
        return not QApplication.platformName().startswith("wayland")

    def _saved_position_is_visible(self) -> bool:
        """True si la posición guardada deja la ventana en algún monitor.

        Se comprueba un trozo de la barra de título (esquina superior
        izquierda), no el rectángulo entero: una ventana que sobresale un poco
        del borde sigue siendo arrastrable y debe respetarse.
        """
        if not self.settings.window_pos_saved:
            return False
        anchor = QRect(self.settings.window_x, self.settings.window_y,
                       min(self.width(), 200), min(self.height(), 60))
        return any(screen.availableGeometry().intersects(anchor)
                   for screen in QApplication.screens())

    def center_on_screen(self) -> None:
        """Centra la ventana en el monitor, sea cual sea su tamaño.

        Se centra el **marco** (``frameGeometry``), no solo el área de cliente,
        para que quede bien con la barra de título y los bordes del gestor de
        ventanas.
        """
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())

    def reset_window_size(self) -> None:
        """Devuelve la ventana al tamaño por defecto y la centra.

        Se pone a ``0`` en los ajustes para que, al volver a abrir, se use otra
        vez el tamaño de fábrica (es lo mismo que hace ``main.py``). La posición
        también se olvida: "restablecer" devuelve la ventana centrada, no a la
        última esquina.
        """
        self.settings.window_width = 0
        self.settings.window_height = 0
        self.settings.window_x = 0
        self.settings.window_y = 0
        self.settings.window_pos_saved = False
        self.settings.save()
        self.showNormal()
        self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        self.center_on_screen()

    def set_snapshot_keep(self, value: int) -> None:
        """Recuerda cuántas copias guardadas se conservan por versión."""
        self.settings.snapshot_keep = int(value)
        self.settings.save()


