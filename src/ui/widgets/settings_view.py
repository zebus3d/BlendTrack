"""La pantalla de Ajustes de la ventana principal.

Son siete pestañas de tarjetas (descargas, carpetas, interfaz, lanzamiento,
sistema, actualizaciones y avanzado) y los manejadores de sus controles. Vive
aparte porque es medio millar de líneas que solo se tocan al añadir un ajuste,
y mezcladas con el resto de la ventana no dejaban ver lo demás.

Es un **mixin** de ``MainWindow``, no una vista independiente: sus controles
escriben directamente en ``self.settings`` y muchos repintan las listas de la
propia ventana, así que separarlos en otro objeto solo cambiaría el acoplo de
sitio (habría que pasarle la ventana entera). El reparto es por
responsabilidad, no por independencia; lo que gana es que cada fichero se
pueda leer entero.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from i18n import tr
from services import autostart, detector
from services import settings as settings_service
from ui import theme as t
from ui.widgets.buttons import CardButton, ChevronCombo, SwitchPill
from ui.widgets.cards import settings_card
from ui.widgets.dialogs import show_error
from ui.widgets.labels import ElidedLabel
from ui.widgets.shell import (EXPERIMENTAL_VIEWS,
                              LANGUAGE_IDS, MAX_ZOOM,
                              MIN_ZOOM, ZoomSlider)
from ui.widgets.tray import TrayIcon


class _GrowingPlainTextEdit(QPlainTextEdit):
    """``QPlainTextEdit`` que crece con su contenido, de una a ``max_lines``.

    El campo de variables de entorno vacío medía dos filas fijas y parecía un
    hueco: aquí empieza a la altura de una línea y se estira al añadir
    variables, hasta ``max_lines`` (a partir de ahí aparece la barra de scroll).
    """

    def __init__(self, text: str = "", max_lines: int = 4, parent=None):
        super().__init__(text, parent)
        self._max_lines = max_lines
        self._fitted = 0
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _visual_lines(self) -> int:
        """Líneas que se ven, no párrafos: una larga que se parte ocupa dos."""
        lines = 0
        block = self.document().begin()
        while block.isValid():
            layout = block.layout()
            lines += max(1, layout.lineCount()) if layout is not None else 1
            block = block.next()
        return max(1, lines)

    def _fit(self) -> None:
        # Altura = texto + relleno del QSS (márgenes de contenido) + margen
        # interno del documento, con un píxel de redondeo. Se mide en vez de
        # calcularse a ojo para que aguante un cambio de fuente.
        margins = self.contentsMargins()
        height = (self.fontMetrics().lineSpacing()
                  * min(self._visual_lines(), self._max_lines)
                  + margins.top() + margins.bottom()
                  + int(self.document().documentMargin()) * 2 + 1)
        if height != self._fitted:
            self._fitted = height
            self.setFixedHeight(height)


class SettingsViewMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    def _build_settings_view(self) -> QWidget:
        # Un tema por pestaña: con tantas opciones, las dos columnas ya no
        # cabían a la altura mínima de la ventana. Las tarjetas y el fondo gris
        # son los mismos que en Migración (objectName `SettingsPage`/`SettingsCard`).
        page = QWidget()
        page.setObjectName("SettingsPage")
        # Sin título: las pestañas van pegadas al borde izquierdo y a la misma
        # altura que las de canal (Local y Nube). Como el QTabWidget dibuja su
        # barra arriba del todo, se baja lo que mida de menos para que las dos
        # filas de pestañas terminen a la misma altura.
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tabs = QTabWidget()
        tabs.setObjectName("SettingsTabs")
        self.settings_tabs = tabs
        tabs.addTab(self._settings_tab(self._settings_downloads_card()),
                    tr("Downloads"))
        # Las carpetas van en su propia pestaña y no dentro de "Descargas":
        # es una lista que crece, y ``_settings_tab`` no lleva scroll, así que
        # no cabría junto a los demás controles con la ventana en su mínimo.
        tabs.addTab(self._settings_tab(self._settings_folders_card()),
                    tr("Folders"))
        tabs.addTab(self._settings_tab(self._settings_interface_card()),
                    tr("Interface"))
        tabs.addTab(self._settings_tab(self._settings_launch_card()),
                    tr("Launch options"))
        tabs.addTab(self._settings_tab(self._settings_system_card()),
                    tr("System"))
        tabs.addTab(self._settings_tab(self._settings_updates_card()),
                    tr("Updates"))
        # Avanzado va al final: son opciones que la mayoría no toca.
        tabs.addTab(self._settings_tab(self._settings_advanced_card()),
                    tr("Advanced"))
        # Qué hay en cada pestaña, sin tener que entrar a mirarlas.
        for index, tip in enumerate((
                tr("Where downloads go and how they are unpacked."),
                tr("The folders where your Blender versions live, and what "
                   "kind of version each one receives."),
                tr("Language, zoom and how the window behaves."),
                tr("Arguments and console for every Blender you launch."),
                tr("Tray icon, autostart and other system integration."),
                tr("Updates of BlenderManager itself."),
                tr("Options still in development."))):
            tabs.setTabToolTip(index, tip)
        # Al entrar en una pestaña, sus listas se ajustan al hueco que tienen:
        # una página oculta no mide igual que recién enseñada.
        tabs.currentChanged.connect(self._on_settings_tab_changed)
        # Las pestañas se alinean por **arriba** con la barra lateral y con los
        # tags de canal (``TABS_TOP``).
        outer.setContentsMargins(0, t.TABS_TOP, 0, 0)
        outer.addWidget(tabs, 1)
        return page

    def _settings_tab(self, card: QFrame) -> QWidget:
        """Página de una pestaña de Ajustes: una sola tarjeta con sus márgenes.

        Cada tema cabe de sobra en su pestaña, así que no lleva scroll (como las
        de Migración); el mínimo de la ventana lo fija la pestaña más alta.
        """
        page = QWidget()
        # El contenido va oscuro (como las listas) y la tira de las pestañas se
        # queda el gris de panel; de ahí que la página tenga su propio objectName.
        page.setObjectName("SettingsTabPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)
        lay.addWidget(card)
        lay.addStretch()
        return page

    def _settings_downloads_card(self) -> QFrame:
        card, lay = settings_card("Downloads", spacing=10)

        # Resumen de a dónde va lo que se descargue ahora mismo. La lista de
        # carpetas vive en su pestaña; aquí solo se dice el resultado, que es
        # lo que interesa desde "Descargas".
        self.dest_summary = ElidedLabel("", Qt.ElideMiddle)
        self.dest_summary.setObjectName("Muted")
        lay.addWidget(self.dest_summary)

        row = QHBoxLayout()
        row.addStretch()
        folders_btn = CardButton(tr("Folders"), tooltip=tr(
            "Choose which folders your Blender versions live in, and which "
            "ones receive each kind of version."))
        folders_btn.clicked.connect(
            lambda: self.settings_tabs.setCurrentIndex(self.FOLDERS_TAB))
        row.addWidget(folders_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr("Delete archive after extraction")))
        row2.addStretch()
        self.archive_switch = SwitchPill(
            self.delete_archive,
            tooltip=tr("Delete the downloaded .zip/.tar.xz after extracting it.\n"
                       "Saves disk space; you can download it again if you need it."))
        self.archive_switch.toggled.connect(self._on_archive_toggled)
        row2.addWidget(self.archive_switch)
        lay.addLayout(row2)

        # Forks de Blender: otros programas que la app puede descargar y
        # lanzar igual que Blender. Van apagados por defecto (son otras apps) y
        # al encenderlos aparecen su pestaña, su tipo de carpeta y su fuente.
        lay.addSpacing(8)
        forks_title = QLabel(tr("Other Blender-based apps"))
        forks_title.setObjectName("Muted")
        lay.addWidget(forks_title)

        row_bfa = QHBoxLayout()
        row_bfa.addWidget(QLabel(tr("Bforartists")))
        row_bfa.addStretch()
        self.bforartists_switch = SwitchPill(
            self.settings.enable_bforartists,
            tooltip=tr("Show Bforartists versions in the cloud.\n"
                       "It is a fork of Blender with a redesigned interface."))
        self.bforartists_switch.toggled.connect(self._on_bforartists_toggled)
        row_bfa.addWidget(self.bforartists_switch)
        lay.addLayout(row_bfa)

        row_upbge = QHBoxLayout()
        row_upbge.addWidget(QLabel(tr("UPBGE")))
        row_upbge.addStretch()
        self.upbge_switch = SwitchPill(
            self.settings.enable_upbge,
            tooltip=tr("Show UPBGE versions in the cloud.\n"
                       "It is the fork that keeps the Blender game engine "
                       "alive."))
        self.upbge_switch.toggled.connect(self._on_upbge_toggled)
        row_upbge.addWidget(self.upbge_switch)
        lay.addLayout(row_upbge)

        forks_hint = QLabel(tr(
            "Bforartists and UPBGE are separate programs based on Blender.\n"
            "When you turn one on, its tab appears in the cloud and it can be "
            "downloaded, installed and launched like any other version.\n"
            "Its files go to its own folder (Settings > Folders)."))
        forks_hint.setObjectName("Muted")
        forks_hint.setWordWrap(True)
        lay.addWidget(forks_hint)

        # Tope de versiones que se enseñan de cada fork. Bforartists acumula
        # 30 y pico versiones y cada una cuesta una petición WebDAV; con las
        # últimas basta para el uso normal, y quien las quiera todas pone
        # "Todas". No afecta a Blender, solo a los forks.
        row_limit = QHBoxLayout()
        row_limit.addWidget(QLabel(tr("Versions per fork")))
        row_limit.addStretch()
        self.fork_limit_combo = ChevronCombo()
        for limit in settings_service.FORK_VERSION_LIMITS:
            self.fork_limit_combo.addItem(
                tr("All") if limit == 0 else tr("Last {count}", count=limit),
                limit)
        index = self.fork_limit_combo.findData(self.settings.fork_version_limit)
        self.fork_limit_combo.setCurrentIndex(index if index >= 0 else 0)
        self.fork_limit_combo.setToolTip(tr(
            "How many of the newest Bforartists and UPBGE versions are shown "
            "in the cloud.\nFewer versions load the tab faster; the files are "
            "the same."))
        self.fork_limit_combo.currentIndexChanged.connect(
            self._on_fork_limit_changed)
        row_limit.addWidget(self.fork_limit_combo)
        lay.addLayout(row_limit)

        self._refresh_dest_summary()
        return card

    def _settings_interface_card(self) -> QFrame:
        card, lay = settings_card("Interface", spacing=10)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel(tr("Language")))
        row3.addStretch()
        self.language_combo = ChevronCombo()
        # El identificador va como **dato** de cada elemento, no en el texto: el
        # texto se traduce (y cambia según el idioma activo), así que buscar por
        # la etiqueta era frágil y con más idiomas dejaba el ajuste en "auto".
        for lang_id, english in LANGUAGE_IDS.items():
            self.language_combo.addItem(tr(english), lang_id)
        index = self.language_combo.findData(self.settings.language)
        self.language_combo.setCurrentIndex(index if index >= 0 else 0)
        self.language_combo.setToolTip(tr(
            "Language of the interface.\nIt changes when you restart the app."))
        self.language_combo.currentIndexChanged.connect(
            self._on_language_changed)
        row3.addWidget(self.language_combo)
        lay.addLayout(row3)

        row_tilt = QHBoxLayout()
        row_tilt.addWidget(QLabel(tr("Tilt the cards under the mouse")))
        row_tilt.addStretch()
        self.tilt_switch = SwitchPill(
            self.settings.card_tilt,
            tooltip=tr("In the grid, the card under the mouse leans towards it "
                       "in 3D and rises above the others.\nTurn it off if you "
                       "prefer the cards to stay still."))
        self.tilt_switch.toggled.connect(self.set_card_tilt)
        row_tilt.addWidget(self.tilt_switch)
        lay.addLayout(row_tilt)

        row_sound = QHBoxLayout()
        row_sound.addWidget(QLabel(tr("Interface sounds")))
        row_sound.addStretch()
        self.sounds_switch = SwitchPill(
            self.settings.ui_sounds,
            tooltip=tr("A soft click each time a card passes through the "
                       "centre of the gallery."))
        self.sounds_switch.toggled.connect(self.set_ui_sounds)
        row_sound.addWidget(self.sounds_switch)
        lay.addLayout(row_sound)

        # Destino del "restablecer" (Ctrl+0 / Ctrl+clic en el slider del pie).
        # Es una preferencia, no el zoom actual: moverlo aquí NO cambia la
        # rejilla; solo decide a qué tamaño vuelve el reset. Por eso el slider
        # del pie sigue persistiendo lo que el usuario dejara la última sesión.
        row4 = QHBoxLayout()
        row4.addWidget(QLabel(tr("Reset zoom")))
        row4.addStretch()
        self.reset_zoom_slider = ZoomSlider(Qt.Horizontal)
        self.reset_zoom_slider.setRange(int(MIN_ZOOM * 100), int(MAX_ZOOM * 100))
        self.reset_zoom_slider.setValue(round(self.settings.reset_zoom * 100))
        self.reset_zoom_slider.setFixedWidth(130)
        self.reset_zoom_slider.setToolTip(
            tr("Zoom the grid returns to (Ctrl+0 or Ctrl+click on the slider)"))
        self.reset_zoom_slider.valueChanged.connect(self._on_reset_zoom_changed)
        self.reset_zoom_slider.sliderReleased.connect(self._save_reset_zoom)
        self.reset_zoom_slider.reset_requested.connect(self._factory_reset_zoom)
        row4.addWidget(self.reset_zoom_slider)
        self.reset_zoom_label = QLabel(f"{round(self.settings.reset_zoom * 100)} %")
        self.reset_zoom_label.setObjectName("Muted")
        self.reset_zoom_label.setToolTip(
            tr("Size the grid returns to when you reset the zoom."))
        self.reset_zoom_label.setFixedWidth(40)
        self.reset_zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row4.addWidget(self.reset_zoom_label)
        lay.addLayout(row4)

        # Tamaño de la ventana: vuelve al de fábrica y se centra. Útil si una
        # sesión la dejó enorme o en una esquina.
        row5 = QHBoxLayout()
        row5.addWidget(QLabel(tr("Window size")))
        row5.addStretch()
        reset_window = CardButton(
            tr("Reset"), tooltip=tr("Return the window to its default size and "
                                    "center it on the screen."))
        reset_window.clicked.connect(self.reset_window_size)
        row5.addWidget(reset_window)
        lay.addLayout(row5)
        return card

    def _settings_system_card(self) -> QFrame:
        card, lay = settings_card("System", spacing=10)

        # Bandeja del sistema: dos decisiones independientes (cerrar y
        # minimizar). Si el escritorio no la soporta se deshabilitan, porque
        # activarlas escondería la ventana sin un icono al que volver.
        tray_available = TrayIcon.available()
        tray_unavailable = tr("The system tray is not available on this desktop.")
        row_tray_close = QHBoxLayout()
        row_tray_close.addWidget(QLabel(tr("Close to the system tray")))
        row_tray_close.addStretch()
        self.close_tray_switch = SwitchPill(self.close_to_tray, tooltip=tr(
            "Keep BlenderManager running in the system tray when you close the "
            "window.\nClick the tray icon to open it again."))
        self.close_tray_switch.toggled.connect(self._on_close_to_tray_toggled)
        row_tray_close.addWidget(self.close_tray_switch)
        lay.addLayout(row_tray_close)

        minimize_tip = tr("Hide the window in the system tray when you minimize "
                          "it.\nClick the tray icon to bring it back.")
        row_tray_min = QHBoxLayout()
        row_tray_min.addWidget(QLabel(tr("Minimize to the system tray")))
        row_tray_min.addStretch()
        self.minimize_tray_switch = SwitchPill(self.minimize_to_tray,
                                               tooltip=minimize_tip)
        self.minimize_tray_switch.toggled.connect(
            self._on_minimize_to_tray_toggled)
        row_tray_min.addWidget(self.minimize_tray_switch)
        lay.addLayout(row_tray_min)
        if not tray_available:
            for switch in (self.close_tray_switch, self.minimize_tray_switch):
                switch.setEnabled(False)
                switch.setToolTip(tray_unavailable)
        elif not detector.minimize_to_tray_supported():
            # En Wayland no hay forma de enterarse de que se ha minimizado, así
            # que la opción ni se ofrece.
            self.minimize_tray_switch.setEnabled(False)
            self.minimize_tray_switch.setToolTip(tr(
                "Minimizing to the tray is not available on this desktop."))

        # Autoarranque con la sesión y arranque oculto. OJO: el autoarranque no
        # es un ajuste nuestro, vive en el sistema (XDG autostart, registro de
        # Windows, LaunchAgent), así que su estado se lee del sistema y no del
        # settings.json; lo que sí guardamos es si debe empezar en la bandeja.
        self.autostart_switch = SwitchPill(autostart.is_enabled(), tooltip=tr(
            "Open BlenderManager automatically when you sign in to your "
            "computer."))
        self.autostart_switch.toggled.connect(self._on_autostart_toggled)
        row_autostart = QHBoxLayout()
        row_autostart.addWidget(QLabel(tr("Start automatically at login")))
        row_autostart.addStretch()
        row_autostart.addWidget(self.autostart_switch)
        lay.addLayout(row_autostart)

        self.start_minimized_switch = SwitchPill(
            self.start_minimized,
            tooltip=tr("Start hidden in the system tray.\nRecommended if it "
                       "opens automatically at login."))
        self.start_minimized_switch.toggled.connect(
            self._on_start_minimized_toggled)
        row_start_min = QHBoxLayout()
        row_start_min.addWidget(QLabel(tr("Start minimized in the system tray")))
        row_start_min.addStretch()
        row_start_min.addWidget(self.start_minimized_switch)
        lay.addLayout(row_start_min)
        if not autostart.supported():
            self.autostart_switch.setEnabled(False)
            self.autostart_switch.setToolTip(
                tr("Automatic startup is not available on this system."))
        if not tray_available:
            # Sin bandeja no hay dónde arrancar oculta: se enseñaría la ventana
            # igual, así que no se ofrece un ajuste que no haría nada.
            self.start_minimized_switch.setEnabled(False)
            self.start_minimized_switch.setToolTip(tray_unavailable)
        return card

    def _settings_launch_card(self) -> QFrame:
        card, lay = settings_card("Launch options", spacing=10)

        # Lanzar con consola: se puede alternar también desde cada tarjeta
        # instalada; aquí queda el ajuste (el mismo) para dejarlo fijo. Es una
        # opción nueva, así que va tras las experimentales.
        self.console_row = QWidget()
        console_lay = QHBoxLayout(self.console_row)
        console_lay.setContentsMargins(0, 0, 0, 0)
        console_lay.addWidget(QLabel(tr("Launch with console")))
        console_lay.addStretch()
        self.console_switch = SwitchPill(
            self.settings.launch_console,
            tooltip=tr("Launch with the console visible: Python output and "
                       "script errors."))
        self.console_switch.toggled.connect(self._on_console_default_toggled)
        console_lay.addWidget(self.console_switch)
        # Primero al layout y luego la visibilidad (ver ``_build_sidebar``).
        lay.addWidget(self.console_row)
        self.console_row.setVisible(self.settings.experimental_features)

        lay.addWidget(QLabel(tr("Launch arguments")))
        self.args_input = QLineEdit(self.launch_args)
        self.args_input.setPlaceholderText(
            tr("Example: --background --python script.py"))
        self.args_input.setToolTip(tr(
            "Extra arguments Blender receives when you launch it.\n"
            "Example: --background to start without the interface."))
        self.args_input.textChanged.connect(self._on_args_changed)
        lay.addWidget(self.args_input)

        # Variables de entorno: una ``CLAVE=VALOR`` por línea. Es un campo
        # aparte de los argumentos porque el apaño típico (desactivar el método
        # de entrada en Linux) no es un argumento sino entorno.
        lay.addWidget(QLabel(tr("Environment variables")))
        self.env_input = _GrowingPlainTextEdit(self.launch_env)
        self.env_input.setPlaceholderText(
            tr("Example: XMODIFIERS=@im=none"))
        self.env_input.setToolTip(tr(
            "Extra environment variables Blender receives when you launch it, "
            "one KEY=VALUE per line.\n"
            "Example: XMODIFIERS=@im=none to disable input methods on Linux."))
        self.env_input.textChanged.connect(self._on_env_changed)
        lay.addWidget(self.env_input)
        return card

    def _settings_updates_card(self) -> QFrame:
        card, lay = settings_card("Updates", spacing=10)

        # Dos ajustes independientes: comprobar al arrancar, y comprobar cada X
        # rato. Apagar el primero NO apaga el segundo.
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Check for updates on startup")))
        row.addStretch()
        self.update_switch = SwitchPill(
            self.auto_update, tooltip=tr(
                "Check for new BlenderManager versions when the app starts.\n"
                "It only downloads one when you accept; checking is cheap."))
        self.update_switch.toggled.connect(self.set_auto_update)
        row.addWidget(self.update_switch)
        lay.addLayout(row)

        # Cada cuánto se comprueba sola (el "y luego se me olvida abrirlo").
        row_periodic = QHBoxLayout()
        row_periodic.addWidget(QLabel(tr("Check for updates periodically")))
        row_periodic.addStretch()
        self.periodic_switch = SwitchPill(self.periodic_update, tooltip=tr(
            "Look for new versions of BlenderManager every so often,\n"
            "even if the startup check is off."))
        self.periodic_switch.toggled.connect(self.set_periodic_update)
        row_periodic.addWidget(self.periodic_switch)
        lay.addLayout(row_periodic)

        row_interval = QHBoxLayout()
        row_interval.addWidget(QLabel(tr("Check for updates every")))
        row_interval.addStretch()
        self.update_interval_combo = ChevronCombo()
        for minutes in settings_service.UPDATE_INTERVALS:
            self.update_interval_combo.addItem(self._interval_text(minutes), minutes)
        index = self.update_interval_combo.findData(
            self.settings.update_interval_min)
        self.update_interval_combo.setCurrentIndex(index if index >= 0 else 0)
        self.update_interval_combo.setToolTip(tr(
            "How often BlenderManager looks for its own updates.\n"
            "It only downloads one when you accept; checking is cheap."))
        self.update_interval_combo.setEnabled(self.periodic_update)
        self.update_interval_combo.currentIndexChanged.connect(
            self._on_update_interval_changed)
        row_interval.addWidget(self.update_interval_combo)
        lay.addLayout(row_interval)

        row2 = QHBoxLayout()
        version_label = QLabel(tr("Version {version}", version=self.current_version))
        version_label.setToolTip(
            tr("Version of BlenderManager you are using right now."))
        row2.addWidget(version_label)
        row2.addStretch()
        check = CardButton(tr("Check now"), tooltip=tr("Check for updates now"))
        check.clicked.connect(lambda: self.check_updates(manual=True))
        row2.addWidget(check)
        lay.addLayout(row2)

        # Series de Blender silenciadas con "Nunca" en su aviso de actualización:
        # la fila solo se ve si hay alguna, para poder reactivarlas.
        self.muted_series_row = QWidget()
        muted_lay = QHBoxLayout(self.muted_series_row)
        muted_lay.setContentsMargins(0, 0, 0, 0)
        muted_lay.setSpacing(8)
        self.muted_series_label = QLabel("")
        self.muted_series_label.setObjectName("Muted")
        muted_lay.addWidget(self.muted_series_label)
        muted_lay.addStretch()
        reset_series = CardButton(tr("Reactivate"),
                                  tooltip=tr("Blender series you silenced with Never"))
        reset_series.clicked.connect(self.reset_blender_series)
        muted_lay.addWidget(reset_series)
        lay.addWidget(self.muted_series_row)
        self._update_blender_series_controls()
        return card

    def _update_blender_series_controls(self) -> None:
        """Enseña (u oculta) la fila de series de Blender silenciadas."""
        if not hasattr(self, "muted_series_row"):
            return
        series = self.settings.ignored_blender_series
        self.muted_series_row.setVisible(bool(series))
        if series:
            self.muted_series_label.setText(
                tr("Silenced: {series}", series=", ".join(series)))

    def _settings_advanced_card(self) -> QFrame:
        """Opciones experimentales: las vistas de ``EXPERIMENTAL_VIEWS``
        (hoy, Add-ons) y lanzar Blender con su consola."""
        card, lay = settings_card("Advanced", spacing=10)
        explanation = tr(
            "Show the experimental features: the Add-ons view and launching "
            "Blender with its console. They are still in development and may "
            "change.")

        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Experimental options")))
        row.addStretch()
        self.experimental_switch = SwitchPill(
            self.settings.experimental_features, tooltip=explanation)
        self.experimental_switch.toggled.connect(self._on_experimental_toggled)
        row.addWidget(self.experimental_switch)
        lay.addLayout(row)

        # El aviso va debajo, en apagado: el interruptor por sí solo no dice
        # que lo que se activa está a medias.
        hint = QLabel(explanation)
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return card

    def _on_experimental_toggled(self, value: bool) -> None:
        """Enseña u oculta las vistas nuevas, y sale de ellas si se apaga."""
        self.settings.experimental_features = value
        self.settings.save()
        for key in EXPERIMENTAL_VIEWS:
            self.side_buttons[key].setVisible(value)
        if hasattr(self, "console_row"):
            self.console_row.setVisible(value)
        # El botón de consola de las tarjetas depende de lo mismo.
        self._rebuild_installed()
        self._rebuild_store()
        if not value and self.view in EXPERIMENTAL_VIEWS:
            self.set_view("installed" if self.installed else "store")

    # ------------------------------------------------------- auto-guardado
    def _on_archive_toggled(self, value: bool) -> None:
        self.delete_archive = value
        self.settings.delete_archive = value
        self.settings.save()

    def _on_bforartists_toggled(self, value: bool) -> None:
        """Enciende/apaga Bforartists y repinta pestañas y casillas."""
        self.settings.enable_bforartists = value
        self.settings.save()
        self.apply_enabled_forks()
        self._rebuild_folder_rows()

    def _on_upbge_toggled(self, value: bool) -> None:
        """Enciende/apaga UPBGE y repinta pestañas y casillas."""
        self.settings.enable_upbge = value
        self.settings.save()
        self.apply_enabled_forks()
        self._rebuild_folder_rows()

    def _on_fork_limit_changed(self, index: int) -> None:
        """Guarda el tope de versiones de los forks y repinta la tienda.

        No hay que volver a pedir el listado: el recorte se aplica al filtrar,
        así que basta con reconstruir la lista que ya está en memoria.
        """
        limit = self.fork_limit_combo.itemData(index)
        if limit is None:
            return
        self.settings.fork_version_limit = limit
        self.settings.save()
        self._rebuild_store()

    def _on_close_to_tray_toggled(self, value: bool) -> None:
        self.close_to_tray = value
        self.settings.close_to_tray = value
        self.settings.save()

    def _on_minimize_to_tray_toggled(self, value: bool) -> None:
        self.minimize_to_tray = value
        self.settings.minimize_to_tray = value
        self.settings.save()

    def _on_start_minimized_toggled(self, value: bool) -> None:
        self.start_minimized = value
        self.settings.start_minimized = value
        self.settings.save()

    def _on_autostart_toggled(self, value: bool) -> None:
        """Registra (o quita) el autoarranque en el propio sistema.

        No se guarda en ``settings.json``: el estado vive en el sistema, así
        que si falla se relee y se deja el interruptor como estaba (si no, la
        interfaz diría una cosa y el sistema otra).
        """
        ok = autostart.enable() if value else autostart.disable()
        if ok:
            return
        show_error(self, tr("Automatic startup"),
                   tr("Could not change the automatic startup."))
        self.autostart_switch.blockSignals(True)
        self.autostart_switch.setChecked(autostart.is_enabled())
        self.autostart_switch.blockSignals(False)

    def _on_args_changed(self, text: str) -> None:
        self.launch_args = text
        self.settings.launch_args = text
        self.settings.save()

    def _on_env_changed(self) -> None:
        """Guarda las variables de entorno al editarlas (auto-guardado)."""
        text = self.env_input.toPlainText()
        self.launch_env = text
        self.settings.launch_env = text
        self.settings.save()

    def _on_language_changed(self, index: int) -> None:
        """Guarda el idioma elegido (el identificador viene como dato del combo)."""
        lang_id = self.language_combo.itemData(index)
        if not lang_id:
            return
        self.settings.language = lang_id
        self.settings.save()

    def _on_reset_zoom_changed(self, percent: int) -> None:
        """Mueve el destino del reset: etiqueta en vivo, guardado al soltar.

        No escribimos el JSON en cada píxel del arrastre (como con el slider
        del pie): aquí no hay nada que reconstruir, así que basta con esperar a
        que el usuario suelte el tirador (o use el teclado).
        """
        self.settings.reset_zoom = min(MAX_ZOOM, max(MIN_ZOOM, percent / 100.0))
        self.reset_zoom_label.setText(f"{percent} %")
        if not self.reset_zoom_slider.isSliderDown():
            self._save_reset_zoom()

    def _save_reset_zoom(self) -> None:
        self.settings.save()

    def _factory_reset_zoom(self) -> None:
        """Ctrl+clic en el slider del ajuste: vuelve al valor de fábrica."""
        self.reset_zoom_slider.setValue(round(settings_service.DEFAULT_ZOOM * 100))

    def _on_console_default_toggled(self, value: bool) -> None:
        """Cambió el valor por defecto (Ajustes > Launch)."""
        self.settings.launch_console = value
        self.settings.save()
        # Las dos listas enseñan el botón (la tienda, en las ya instaladas).
        self._rebuild_installed()
        self._rebuild_store()
