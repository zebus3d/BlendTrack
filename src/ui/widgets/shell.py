"""Piezas que comparten la ventana principal y sus partes.

``MainWindow`` está repartida en varios módulos por responsabilidad (Ajustes,
biblioteca de carpetas, descargas, actualizaciones). Todos necesitan las
mismas tablas y un par de widgets, y viven aquí y no en ``main_window``
porque si no cada parte tendría que importar la ventana y la ventana a cada
parte: importación circular.
"""

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from ui import theme as t


PLATFORMS = {"GNU/Linux": "linux", "Windows": "windows", "macOS": "darwin"}
PLATFORM_LABELS = {value: key for key, value in PLATFORMS.items()}
ARCH_LABELS = ["x86_64", "arm64"]
LANGUAGE_IDS = {
    "auto": "Automatic",
    "en": "English",
    "es": "Spanish",
    "zh": "Simplified Chinese",
    "ru": "Russian",
    "ja": "Japanese",
    "pt_BR": "Portuguese (Brazil)",
}
# El mínimo (40 %) se midió: a ese nivel el contenido de una tarjeta de rejilla
# (logo, textos y fila de botones) sigue cabiendo en el alto fijo con 6+ px de
# holgura, así que caben más filas sin que nada se recorte ni se solape. Por
# debajo, el texto (que NO escala con el zoom) empieza a apretarse.
MIN_ZOOM, MAX_ZOOM = 0.4, 1.8

# Alto de la fila de filtros (la que lleva las pestañas de canal). Vive en el
# tema porque Ajustes y Migración lo usan para dejar sus pestañas a la misma
# altura que las de canal.
FILTERS_HEIGHT = t.FILTERS_HEIGHT
# Alto de los controles de la fila de filtros (pastillas de vista y combos):
# el mismo que los tags de canal, para que la fila quede a ras.
FILTER_CONTROL_HEIGHT = t.CONTROL_HEIGHT

# La esquina de abajo a la derecha (galería y rejilla): el botón que la ocupa
# mide y se separa del borde igual en las dos, para que no baile al cambiar de
# vista. Los comparten ``gallery_controls`` y ``build_lists``.
CORNER_BUTTON_SIZE = 26
CORNER_BUTTON_MARGIN = 8

# Vistas que solo se ven con las opciones experimentales (Ajustes > Avanzado).
# Recientes y Migración salieron de aquí cuando quedaron probadas (Migración
# tiene la batería de tests de UI más grande de la app y feedback de usuarios
# reales); el gestor de add-ons sigue en desarrollo (arranca Blender para leer
# el estado).
EXPERIMENTAL_VIEWS = ("addons",)
# Cuánto sube/baja el zoom con Ctrl +/-. El slider va en pasos de 1 %.
ZOOM_STEP = 0.1

# --- Tamaño de la ventana ---
# Son cuatro números con dos significados distintos, por eso van uno por uno y
# no en un par de tuplas: "de fábrica" es con lo que se abre la app la primera
# vez (y a lo que vuelve el botón "Restablecer" de Ajustes), y "mínimo" es el
# tamaño más pequeño al que el usuario puede encogerla sin que la interfaz se
# recorte hasta ser inservible.

# Ancho de fábrica. Con los dos forks activos hay ocho pestañas de canal
# (Todas, LTS, Estable, Diarias, Experimentales, Bforartists, UPBGE, Favoritos)
# más el botón de refrescar, los de vista (rejilla/lista/galería) y los combos
# de plataforma y arquitectura. Si no basta, Qt **recorta** la última contra el
# refrescar ("Bforartists" salía como "Bfor...") y parece que el fork no existe;
# por eso la barra lleva botones de scroll. Lo vigila
# ``test_todas_las_pestanas_de_canal_son_alcanzables``.
# Subió de 940 a 1000 al añadir el botón de vista de galería: con el tercer
# botón, en español "Favoritos" quedaba cortado (la barra necesitaba 532 px y
# tenía 495). Lo vigila ``test_el_ancho_de_fabrica_no_corta_la_ultima_pestana``.
DEFAULT_WINDOW_WIDTH = 1000

# Alto de fábrica. Deja entrar **tres filas** de tarjetas de rejilla al zoom
# de fábrica (50 %) con 3 columnas (separaciones y márgenes incluidos). Lleva 35
# px de más sobre los 655 que hacían falta para eso: es la franja que se reserva
# abajo en la rejilla para el botón de efectos de tarjeta (la esquina de abajo a
# la derecha), y sin ellos la tercera fila quedaría cortada.
# Lo vigila ``test_el_tamano_por_defecto_muestra_tres_filas_al_zoom_de_fabrica``.
DEFAULT_WINDOW_HEIGHT = 690

# Ancho mínimo. Por debajo, la fila de filtros y las tarjetas se aprietan de más
# y la interfaz empieza a recortar contenido. Las pestañas de canal que no caben
# a este ancho se alcanzan con los botones de scroll de la propia barra.
MIN_WINDOW_WIDTH = 700

# Alto mínimo. El suelo del caso más alto de la app (la pestaña de Ajustes más
# larga) sin que el contenido se recorte.
MIN_WINDOW_HEIGHT = 440

# Canales de la barra de filtros, en el orden en que se enseñan. Son
# excluyentes (solo se ve uno a la vez), así que van en una barra de pestañas
# y no en pastillas sueltas. Aquí solo viven las **etiquetas**: las claves son
# las de ``services.channels.CHANNELS`` y un test vigila que no se separen.
CHANNELS = (
    ("all", "All"),
    ("lts", "LTS"),
    ("stable", "Stable"),
    ("daily", "Daily"),
    ("experimental", "Experimental"),
    ("bforartists", "Bforartists"),
    ("upbge", "UPBGE"),
    ("favorites", "Favorites"),
)

# Título que se enseña en la cabecera según la vista. La cabecera lleva arriba
# "Blender Manager" y debajo esto, para saber en qué sección se está.
SECTION_TITLES = {
    "installed": "Local",
    "store": "Cloud",
    "recent": "Recent files",
    "addons": "Add-ons",
    "migrate": "Migration",
    "settings": "Settings",
}

# Tooltips de los filtros de canal. Se explican para quien no sabe qué es una
# LTS o una compilación diaria; las claves de i18n son estos textos en inglés.
# Los saltos de línea (\n) se ven en el tooltip, así que pueden ser varias
# líneas.
CHANNEL_TOOLTIPS = {
    "all": "Show everything, forks included: stable, LTS, daily and alpha.",
    "lts": "LTS = Long Term Support.\nVersions maintained for years and the "
           "most stable.\nRecommended for everyday work.",
    "stable": "Stable versions that are not LTS.\nThey are the latest official "
              "releases, supported until the next one.",
    "daily": "Daily and alpha builds with the newest changes.\nThey can fail: "
             "for testing, not for work.",
    "experimental": "Branches with new features still in development.\nThey are "
                    "not ready for production and the list is usually empty.",
    "bforartists": "Bforartists is a fork of Blender with a redesigned, "
                   "easier-to-read interface.\nIts versions live here, on their "
                   "own tab (All shows them too, mixed with Blender's).\n"
                   "Turn it on in Settings > Downloads.",
    "upbge": "UPBGE is the fork that keeps the Blender game engine alive.\n"
             "Its versions live here, on their own tab (All shows them too, "
             "mixed with Blender's).\nTurn it on in Settings > Downloads.",
    "favorites": "Only the versions you marked with the star.",
}


def write_problem(folder) -> str:
    """Motivo por el que no se puede escribir en ``folder``, o "" si sí se puede.

    Crea la carpeta si falta (que es lo que hará la descarga igualmente) y
    escribe y borra un fichero de prueba. Existe por un caso real: con la
    carpeta de las LTS en otro disco, si no había permiso de escritura la
    descarga terminaba en un genérico "Fallo en la descarga" y no había forma
    de saber que era eso. Mejor decirlo antes de bajar cientos de MB.
    """
    path = Path(folder).expanduser()
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".blendermanager-write-test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        return str(error)
    return ""



class WorkerBridge(QObject):
    """Reenvía callbacks de hilos de descarga al hilo de la interfaz.

    Tocar widgets desde un hilo que no es el de la interfaz cuelga Qt: el
    hilo emite estas señales y Qt las entrega en el hilo bueno.
    """

    progress = Signal(int, int)
    done = Signal(str)
    error = Signal(str)


class ZoomSlider(QSlider):
    """Slider del zoom que vuelve al valor por defecto con ``Ctrl`` + clic.

    ``QSlider`` no distingue el clic normal del clic con modificadores, así que
    hay que mirarlo en ``mousePressEvent``.

    Y en la ranura, ``QSlider`` da un ``pageStep`` (10 % de zoom, medido) en vez
    de llevar el tirador al punto pulsado. Aquí se mapea la coordenada a un valor
    —el tirador queda justo bajo el cursor, y sigue ahí mientras se arrastra—,
    que es lo que se espera de un deslizador. El dibujo sigue en ``qss.py``.
    """

    reset_requested = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # El tirador mide 16 px de alto, pero la diana la marca el widget: con
        # los 15 px por defecto queda por debajo del mínimo de 24 px (WCAG 2.5.8).
        # La fila de la cabecera ya mide 32 px, así que subirlo no mueve nada.
        self.setMinimumHeight(24)
        self._drag_active = False

    def _sub_rect(self, sub_control):
        """Rectángulo de una parte del slider (ranura o tirador) ya maquetado."""
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        return self.style().subControlRect(
            QStyle.CC_Slider, option, sub_control, self)

    def _value_at(self, x):
        """Valor cuyo tirador queda centrado en la coordenada ``x``."""
        groove = self._sub_rect(QStyle.SC_SliderGroove)
        handle = self._sub_rect(QStyle.SC_SliderHandle)
        span = groove.width() - handle.width()
        if span <= 0:
            return self.value()
        value = QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(),
            x - groove.left() - handle.width() // 2, span)
        return min(self.maximum(), max(self.minimum(), value))

    def mousePressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.reset_requested.emit()
            event.accept()
            return
        self._drag_active = True
        self.setSliderDown(True)
        self.setValue(self._value_at(event.position().toPoint().x()))
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._drag_active:
            super().mouseMoveEvent(event)
            return
        self.setValue(self._value_at(event.position().toPoint().x()))
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_active:
            self._drag_active = False
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)
