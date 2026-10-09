"""Vista de migración de addons entre versiones de Blender.

El núcleo (``services/blender_config``) hace el trabajo: localiza las carpetas,
lee la versión mínima de cada addon y decide si es compatible. Aquí solo se
enseña el informe, se dejan marcar los que se quieren copiar y se lanza la
copia. La parte de habilitarlos en la versión destino la ejecuta Blender en
segundo plano (``services/blender_runner``), porque el estado *habilitado*
vive dentro de ``userpref.blend``.

La pantalla sigue la misma guía que Ajustes: pestañas arriba (a la altura de
las de canal), contenido debajo sobre el fondo oscuro y tarjetas un escalón por
encima. La tarjeta "desde → hacia" es común a las tres pestañas y se mueve a la
que esté abierta.

Dentro de Add-ons, la disposición es la de un tablero de transferencia: el **origen** a la
izquierda (con casilla, versión y veredicto), el **destino** a la derecha (con
la carpeta donde caerá cada addon) y una flecha de un solo sentido en medio.
Las dos columnas viven en la **misma área de scroll**, así que las filas quedan
siempre alineadas sin sincronizar nada.

No es una pestaña de compilaciones, así que la barra de filtros y el buscador
se ocultan (lo decide ``MainWindow._set_view``).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QScrollArea,
                               QSizePolicy, QTabWidget, QVBoxLayout, QWidget)

from i18n import tr
from model.build import fork_label
from services import blender_config as bc
from services import blender_runner
from ui import icons
from ui import theme as t
from ui.fonts import icon_font
from ui.widgets.cards import settings_card
from ui.widgets import dialogs
from ui.widgets.layouts import list_scroll
from ui.widgets.migrate.addons_tab import AddonsTabMixin
from ui.widgets.migrate.common import (LatestJob, _entry_info, _entry_key,
                                       choice_label, newest_per_series)
from ui.widgets.migrate.factory_tab import FactoryTabMixin
from ui.widgets.migrate.prefs_tab import PrefsTabMixin


class MigrateView(AddonsTabMixin, PrefsTabMixin, FactoryTabMixin,
                  QWidget):
    """Pantalla para copiar addons de una versión instalada a otra."""

    status_message = Signal(str)
    activation_done = Signal(object)
    prefs_loaded = Signal(object)      # {"user", "factory", "error"}
    prefs_applied = Signal(object)     # {"result", "version"}
    source_read = Signal(object)       # {"version", "enabled", "error"}
    snapshots_analyzed = Signal(object)  # {"version", "results", "live"}
    snapshot_keep_changed = Signal(int)  # cuántas copias guardadas conservar
    style_done = Signal(object)          # {"result", "version"}

    def __init__(self, parent=None):
        super().__init__(parent)
        # objectName para el QSS (fondo gris unificado y tarjetas por encima).
        self.setObjectName("MigrateView")
        # Una SUBCLASE de QWidget no pinta el fondo que le pone el QSS salvo que
        # se active esto. Sin ello, el gris de la vista no se dibujaba y la zona
        # de la fila de pestañas a la derecha de las solapas quedaba con el
        # oscuro de detrás.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.platform = ""
        self.arch = ""
        self.installed = []
        self._choices = []
        self.source_cfg = None
        self.target_cfg = None
        self.source_entry = None
        self.target_entry = None
        self.plans = []
        self.pref_items = []
        self._rows = []
        # Un ``LatestJob`` por cada trabajo en segundo plano, **uno por
        # operación**: leer el origen y escribir ajustes pueden estar en marcha
        # a la vez, y con una bandera compartida la respuesta de una se comía la
        # de la otra. Además las señales son públicas: un evento diferido podría
        # emitirlas sin que nadie espere, y sin el ticket se abriría un diálogo
        # modal de la nada (le pasó al suite con ``QTest.qWait``).
        self._activation_job = LatestJob()
        self._prefs_read = LatestJob()
        self._prefs_write = LatestJob()
        self._style_job = LatestJob()
        # Lectura automática del origen: guarda para qué ``(fork, versión)`` se
        # hizo, para no repetir el arranque de Blender mientras no cambie.
        self._source_read_for = None
        self.source_enabled = set()
        self.detail_prefs = []
        self.detail_checks = []
        # Alto elegido a mano con el asa (None = el del contenido, con tope).
        # Gestor de guardados: filas por instantánea, análisis cacheado y
        # bandera de "ya se está analizando" (un arranque de Blender por fila).
        self._snapshot_widgets = {}
        self._snapshot_job = LatestJob()
        self._forget_analysis()
        self._factory_live_count = None
        # Cuántas copias guardadas se conservan (lo fija MainWindow desde los
        # ajustes; aquí se usa al crear o restaurar una).
        self.snapshot_keep = 5
        self._build_ui()
        self.activation_done.connect(self._on_activation_done)
        self.prefs_loaded.connect(self._on_prefs_loaded)
        self.prefs_applied.connect(self._on_prefs_applied)
        self.source_read.connect(self._on_source_read)
        self.snapshots_analyzed.connect(self._on_snapshots_analyzed)
        self.style_done.connect(self._on_style_done)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        """Estructura de la pantalla, calcada de Ajustes.

        Las **pestañas van arriba del todo** (a la misma altura que las de
        canal de Tienda/Instaladas) y el contenido debajo, sobre el mismo fondo
        oscuro que las listas; las tarjetas quedan un escalón por encima. Antes
        había una cabecera propia y toda la vista iba del gris de panel: era la
        única pantalla con el fondo claro.

        La tarjeta **origen → destino** es común a Add-ons y Preferences, así que
        hay una sola y se mueve a la pestaña visible (``_show_header``). La
        pestaña de fábrica **no migra nada**, así que lleva su propia tarjeta
        con un solo desplegable: allí un "Desde" y un "Hacia" hacían creer que
        la operación usaba los dos, cuando solo actúa sobre uno.

        * **Add-ons**: el tablero y el botón de copiar lo seleccionado.
        * **Preferences**: primero lo fino (ajustes uno a uno) y luego los
          ficheros completos.
        * **Factory settings**: una versión, que se restablece o se recupera.
        """
        root = QVBoxLayout(self)
        # Sin margen: las pestañas van **a sangre** (el canvas llega hasta la
        # barra lateral, el borde derecho y la barra de estado). El padding va
        # dentro de cada pestaña, no aquí.
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = self._build_header()
        self.factory_header = self._build_factory_header()

        self.tabs = QTabWidget()
        self.tabs.setObjectName("MigrateTabs")
        self.tabs.addTab(self._build_addons_tab(), tr("Add-ons"))
        self.tabs.addTab(self._build_preferences_page(), tr("User prefs"))
        self.tabs.addTab(self._build_factory_page(), tr("Factory settings"))
        # "User prefs" y "Factory settings" suenan parecido: el tooltip dice
        # cuál copia de una versión a otra y cuál deja una versión limpia.
        for index, tip in enumerate((
                tr("Copy add-ons and extensions from one version to another."),
                tr("Copy settings: one by one, as theme and key map presets, "
                   "or the whole preferences file."),
                tr("Start a version as if it were freshly installed, keeping "
                   "your current settings saved aside."))):
            self.tabs.setTabToolTip(index, tip)
        # Las pestañas se alinean por **arriba** con la barra lateral y con los
        # tags de canal (``TABS_TOP``): las tres filas no miden lo mismo, así
        # que alinear por abajo las dejaba a distinta altura.
        root.setContentsMargins(0, t.TABS_TOP, 0, 0)
        root.addWidget(self.tabs, 1)

        self.tabs.currentChanged.connect(self._show_header)
        self._show_header(self.tabs.currentIndex())

        self._set_controls_enabled(False)

    def _build_header(self) -> QWidget:
        """Bloque común a las tres pestañas: versiones y aviso de Blender abierto.

        Es **un solo widget** que se reparenta a la pestaña visible: con una
        copia por pestaña habría tres pares de desplegables que mantener en
        sincronía, y el origen/destino es uno para toda la pantalla.
        """
        header = QWidget()
        header.setObjectName("MigrateHeader")
        lay = QVBoxLayout(header)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        lay.addWidget(self._build_version_bar())

        self.warning = QLabel("")
        self.warning.setObjectName("Danger")
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        lay.addWidget(self.warning)
        return header



    def _show_header(self, index: int) -> None:
        """Mueve a la pestaña visible la tarjeta de versiones que le toca.

        Add-ons y Preferences comparten la barra origen → destino; fábrica usa su
        propio selector de una versión (``_build_factory_header``). La que no se
        usa se desparenta y se oculta, para que no quede por debajo.
        """
        page = self._page_of(self.tabs.widget(index))
        is_factory = page is self.factory_page
        header = self.factory_header if is_factory else self.header
        other = self.header if is_factory else self.factory_header
        # Sacar la que no toca (``removeWidget`` no la desparenta, así que hay
        # que insertarla de nuevo al volver: no basta con mirar el padre).
        old_other = other.parentWidget()
        if old_other is not None and old_other.layout() is not None:
            old_other.layout().removeWidget(other)
        other.hide()
        if page is not None:
            old = header.parentWidget()
            if old is not None and old.layout() is not None:
                old.layout().removeWidget(header)
            # ``insertWidget`` reparenta: la tarjeta pasa a ser hija de la
            # página nueva y se dibuja arriba del todo, encima del contenido.
            page.layout().insertWidget(0, header)
        header.show()
        if is_factory:
            self._fill_factory()
            self._check_running(self._factory_entry(), self.factory_warning)

    def _new_page(self) -> tuple:
        """Página de una pestaña con los márgenes de Ajustes (``(page, lay)``).

        El hueco de arriba lo ocupa la tarjeta de versiones, que se inserta en
        la posición 0 al abrir la pestaña.
        """
        page = QWidget()
        page.setObjectName("MigratePage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(16)
        return page, lay

    @staticmethod
    def _scrollable(page: QWidget) -> QScrollArea:
        """Mete la página en su propio scroll vertical.

        Las tarjetas con asa crecen a voluntad del usuario, y sin scroll el
        layout no tenía dónde poner lo que sobraba: las de abajo se quedaban en
        su sitio y la que crecía se metía por detrás. Con esto, estirar una
        empuja a las demás hacia abajo y la página se desplaza.
        """
        scroll, _ = list_scroll("MigrateScroll", "MigratePageBody")
        scroll.setWidget(page)
        return scroll

    def _page_of(self, host) -> QWidget:
        """Página real de una pestaña (las que van en scroll son el de dentro)."""
        return host.widget() if isinstance(host, QScrollArea) else host

    def _build_version_bar(self) -> QFrame:
        """Tarjeta superior: de qué versión a qué versión (común a las pestañas).

        Va en una tarjeta como el resto: sobre el fondo oscuro de la ventana, un
        desplegable —que también es oscuro— no se distingue. Cada columna lleva
        su etiqueta y, debajo, la ruta real de esa config: es lo que deja claro
        *dónde* se va a escribir, sobre todo con las LTS en otro disco o una
        config movida con ``BLENDER_USER_CONFIG``.
        """
        card, lay = settings_card()
        card.setToolTip(tr(
            "Pick a source and a destination version here, then use the tabs "
            "to copy add-ons, individual settings or the whole preferences "
            "file."))
        bar = QHBoxLayout()
        bar.setSpacing(10)

        def column(title: str, tip: str, path_tip: str):
            # Etiqueta y ruta en el color de texto normal (como los rótulos de
            # las opciones en Ajustes): en Muted sobre la tarjeta clara se
            # quedaban en 3,7:1 y costaba leerlas.
            column = QVBoxLayout()
            column.setSpacing(4)
            label = QLabel(title)
            label.setToolTip(tip)
            column.addWidget(label)
            combo = self._version_combo(tip)
            column.addWidget(combo)
            path = QLabel("")
            path.setWordWrap(True)
            path.setToolTip(path_tip)
            column.addWidget(path)
            return column, combo, path

        left, self.source_combo, self.source_path_label = column(
            tr("From"), tr("Version you are copying the add-ons and settings "
                           "from."),
            tr("Folder with the settings and add-ons of the source version. It "
               "is only read, never written."))
        bar.addLayout(left, 1)
        arrow = QLabel(icons.ARROW_RIGHT)
        arrow.setFont(icon_font(16))
        arrow.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        arrow.setToolTip(tr("The add-ons and settings selected on the left are "
                            "copied to the version on the right."))
        bar.addWidget(arrow)
        right, self.target_combo, self.target_path_label = column(
            tr("To"), tr("Version you are copying the add-ons and settings to."),
            tr("Folder with the settings and add-ons of the destination "
               "version. This is where the copy writes; what exists there is "
               "kept as a backup."))
        bar.addLayout(right, 1)
        lay.addLayout(bar)
        return card







    def _version_combo(self, tooltip: str) -> QComboBox:
        """Desplegable de versión instalada (en la barra superior)."""
        combo = QComboBox()
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        combo.setMinimumWidth(150)
        combo.setToolTip(tooltip)
        combo.currentIndexChanged.connect(lambda _: self._fill_from_installed())
        return combo








































    def set_system(self, platform: str, arch: str) -> None:
        """Fija el SO/arquitectura de este equipo (para la compatibilidad)."""
        self.platform = platform or ""
        self.arch = arch or ""

    # -------------------------------------------------------------- origen
    def set_installed(self, installed) -> None:
        """Recibe las versiones instaladas y rellena los desplegables.

        Una opción por ``(fork, serie)``, de la más nueva a la más vieja (ver
        ``newest_per_series``): de ahí sale el valor por defecto de los combos.
        """
        self.installed = list(installed or [])
        # Lo elegido se busca por ``(fork, versión)`` **antes** de cambiar la
        # lista: el índice de antes ya no vale con la lista nueva.
        keep = {combo: _entry_key(self._selected(combo))
                for combo in (self.source_combo, self.target_combo,
                              self.factory_combo)}
        self._choices = newest_per_series(self.installed)

        # Al reordenar, el desplegable mantiene la versión que tuviera elegida.
        self._fill_combo(self.source_combo, keep[self.source_combo])
        self._fill_combo(self.target_combo, keep[self.target_combo])
        # La pestaña de fábrica tiene su propio selector de una versión: se
        # mantiene lo elegido y, la primera vez, arranca en la más nueva (igual
        # que el destino de la migración).
        self._fill_combo(self.factory_combo, keep[self.factory_combo],
                         label=choice_label)
        if self._choices and self.factory_combo.currentIndex() < 0:
            self.factory_combo.setCurrentIndex(0)
        # Por defecto (solo la primera vez): de la **penúltima** a la **última**,
        # que es el caso normal de "acabo de instalarme la nueva y quiero
        # traerme lo de la anterior".
        if len(self._choices) >= 2:
            if self.source_combo.currentIndex() < 0:
                self.source_combo.setCurrentIndex(1)
            if self.target_combo.currentIndex() < 0:
                self.target_combo.setCurrentIndex(0)
        self._fill_from_installed()

    def _fill_combo(self, combo, keep: tuple, label=None) -> None:
        """Rellena un desplegable con ``self._choices`` y conserva ``keep``.

        ``keep`` es el ``(fork, versión)`` que estaba elegido. ``label`` decide
        el texto de cada opción (por defecto, programa, versión y carpeta; el
        selector de fábrica usa ``choice_label``, sin la carpeta).
        """
        label = label or (lambda entry: f"{fork_label(entry.fork)} "
                                        f"{entry.version}  ·  {entry.name}")
        combo.blockSignals(True)
        combo.clear()
        for entry in self._choices:
            combo.addItem(label(entry))
        index = next((i for i, entry in enumerate(self._choices)
                      if _entry_key(entry) == keep), -1)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _selected(self, combo):
        index = combo.currentIndex()
        if 0 <= index < len(self._choices):
            return self._choices[index]
        return None

    # ------------------------------------------------------------- informe
    def _fill_from_installed(self) -> None:
        self._clear_rows()
        self.plans = []
        self.source_cfg = None
        self.target_cfg = None
        self.source_entry = None
        self.target_entry = None
        self._set_warning("")
        self.source_path_label.setText("")
        self.target_path_label.setText("")

        source = self._selected(self.source_combo)
        target = self._selected(self.target_combo)
        if len(self._choices) < 2 or source is None or target is None:
            self.summary.setText(tr("You need at least two installed Blender "
                                    "versions."))
            self._set_controls_enabled(False)
            self._fill_preference_files()
            return
        if _entry_key(source) == _entry_key(target):
            self.summary.setText(tr("Source and destination must be different."))
            self._set_controls_enabled(False)
            self._fill_preference_files()
            return

        self.source_entry = source
        self.target_entry = target
        self.source_cfg = bc.config_for(source.version, self.platform,
                                        fork=getattr(source, "fork", ""))
        self.target_cfg = bc.config_for(target.version, self.platform,
                                        fork=getattr(target, "fork", ""))
        self._fill_preference_files()
        self._refresh_undo()
        self._fill_factory()
        self.source_path_label.setText(str(self.source_cfg.root))
        self.target_path_label.setText(str(self.target_cfg.root))
        self._rebuild_plan()
        self._check_running(target)
        self._refresh_undo()
        # Lectura automática del origen (addons activos + preferencias). Si esa
        # versión ya se leyó, no vuelve a arrancar Blender.
        self._read_source_blender()


    def _check_running(self, entry, warning=None) -> None:
        """Avisa (en la tarjeta) si ese Blender está abierto.

        Escribir sus preferencias con Blender abierto es perder el cambio al
        salir. Es un aviso suave en la propia vista; para las acciones que van a
        escribir de verdad se llama a ``_blocked_by_running``, que impide
        continuar. ``warning`` elige la etiqueta (la de migración o la de
        fábrica), porque cada pestaña tiene la suya.
        """
        warning = warning if warning is not None else self.warning
        executable, version = _entry_info(entry)
        try:
            running = blender_runner.is_running(executable)
        except Exception:  # noqa: BLE001 - el aviso no puede tumbar la vista
            running = False
        self._put_warning(warning,
                          self._running_message(version) if running else "")

    @staticmethod
    def _running_message(version: str) -> str:
        return tr("Close Blender {version} before migrating: it would "
                  "overwrite the changes when it quits.", version=version)

    def _blocked_by_running(self, entry=None) -> bool:
        """True si hay **cualquier** Blender abierto (y avisa); bloquea la acción.

        No se comprueba solo el destino: dos builds de la misma serie (5.2.0 y
        5.2.2) comparten carpeta de configuración, así que un Blender abierto
        que no es el elegido también pisaría el cambio al cerrarse. Como no hay
        forma fiable de saber qué serie tiene abierta cada proceso (en Windows
        ni se detectan), se cierra todo.
        """
        try:
            running = blender_runner.is_running(None)
        except Exception:  # noqa: BLE001 - el chequeo no puede tumbar la acción
            running = False
        if not running:
            return False
        dialogs.show_info(self, tr("Blender is running"), tr(
            "Close every Blender window before continuing: Blender saves its "
            "preferences when it quits and would overwrite the changes."))
        return True




    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.copy_btn, self.select_all_btn,
                       self.select_none_btn):
            widget.setEnabled(enabled)

    def _set_warning(self, text: str) -> None:
        self._put_warning(self.warning, text)

    @staticmethod
    def _put_warning(label, text: str) -> None:
        """Pinta el aviso en la etiqueta que le pasen (cada pestaña tiene una)."""
        label.setText(text)
        label.setVisible(bool(text))






