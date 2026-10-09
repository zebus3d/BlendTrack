"""Paleta de colores y tipografías (versión PySide6).

Los colores imitan el *tema oscuro por defecto* de Blender. La idea de
profundidad es la de Blender:

* el **fondo** de la ventana es el gris más oscuro;
* las **barras** (cabecera, barra lateral y de estado) y los **paneles/tarjetas**
  van un escalón por encima;
* los **botones** neutros son gris oscuro, como las pestañas de Blender;
* los **campos de texto** quedan hundidos (más oscuros que su panel).

Los valores van como cadenas ``#RRGGBB`` (o ``rgba(...)`` los que llevan
transparencia): así sirven igual en el QSS que para pintar a mano, con
``QColor(t.ACCENT)``. Este módulo **no importa Qt**: es solo la paleta, y los
tests de contraste la leen sin montar ninguna ventana.

Cambio respecto a la era Kivy (medido con la fórmula de contraste WCAG):
las constantes ``ACCENT``/``DANGER`` se usan para **bordes e indicadores**
(solo necesitan 3:1), y los **rellenos de botón** usan ``ACCENT_BTN`` /
``DANGER_BTN``, que dejan el texto claro por encima de 4,5:1 (AA).
"""

# --- Jerarquía de grises (de más profundo a más elevado) ---
BG = "#1D1D1D"          # fondo de la ventana (el más oscuro)
FIELD = "#171717"       # campos de texto (hundidos)
BUTTON = "#585858"      # botones neutros
# Botones neutros con texto, al pasar el ratón. Un pelo más oscuro que el
# #6A6A6A de antes: con aquel, TEXT quedaba en 4,33:1 (por debajo de AA).
BUTTON_HOVER = "#666666"
FILTER = "#1D1D1D"      # botones de filtro
SURFACE = "#303030"     # paneles y tarjetas
CHROME = "#303030"      # cabecera, barra lateral y de estado
CARD_DIM = "#262626"    # por descargar (clara; se distingue del BG, pero
                        # queda por debajo de SURFACE, que son las instaladas)
CARD_DIM_ALT = "#202020"  # por descargar (oscura)
ROW_ALT = "#2A2A2A"     # instalada (oscura)
SURFACE_ALT = "#3D3D3D"  # hover / contornos suaves
BORDER = "#3D3D3D"
# Tarjeta **encima de un canvas gris**, un escalón por encima de SURFACE.
# Comparte valor con SURFACE_ALT a propósito: es el mismo escalón de
# profundidad del tema. Hoy no lo usa ninguna vista (Migración y Ajustes
# pasaron a fondo oscuro con tarjetas en SURFACE), pero se queda como parte de
# la escala de grises.
SURFACE_HIGH = "#3D3D3D"

# --- Texto ---
TEXT = "#E6E6E6"
# Texto secundario. Tiene que pasar 4,5:1 también sobre el gris del hover
# (SURFACE_ALT), que es donde peor queda: con el #989898 de antes, una tarjeta
# con el ratón encima dejaba su línea de detalles en 3,77:1.
MUTED = "#A8A8A8"
# Glifo apagado (la estrella sin marcar). Es un icono, no texto: le basta 3:1,
# y se queda en el gris de siempre para que el ámbar del realce siga
# distinguiéndose de él (con MUTED la diferencia bajaba de 1,5:1).
ICON_MUTED = "#989898"
# Texto de ejemplo (placeholder) de los campos. Más apagado que MUTED a
# propósito: tiene que leerse como "esto es una pista", no como lo que hay
# escrito. Sobre FIELD queda en 3,5:1, por encima del 3:1 de un elemento no
# esencial, pero muy por debajo del texto real.
PLACEHOLDER = "#6E6E6E"
TEXT_SEL = "#FFFFFF"

# --- Acentos y estados ---
# Los rellenos con texto encima se miden en su **peor punto**: los botones
# llevan un degradado con un 9 % de blanco arriba (``qss._volume``), así que el
# texto se compara con ese extremo, el más claro. Lo vigila ``ContrastTests``.
#
# Azul de los bordes e indicadores (casilla marcada, interruptor, barras):
# solo necesita 3:1 contra lo que tiene alrededor (3,4:1 sobre SURFACE).
ACCENT = "#5085B1"
# El mismo azul, un punto más oscuro, cuando lleva **texto** encima: pestaña o
# pastilla elegida, elemento resaltado de un menú, texto seleccionado. Con
# blanco, 4,6:1; con ACCENT se quedaba en 3,94:1 (13 px en negrita no cuenta
# como texto grande).
ACCENT_SELECTED = "#487AA3"
ACCENT_DARK = "#3A6890"     # hover de pestañas, pastillas y casillas
ACCENT_BTN = "#325B82"      # relleno de botón primario (TEXT = 4,6:1 arriba)
# Botón primario al pasar el ratón: más claro que el normal y con el texto en
# blanco puro, que es lo que deja aclarar el fondo sin bajar de 4,5:1.
ACCENT_BTN_HOVER = "#3D6C93"
# Pulsado: más oscuro, como un botón que se hunde (antes se aclaraba y el texto
# bajaba a 2,72:1).
ACCENT_BTN_PRESSED = "#2A4D6E"
DANGER = "#B84A4A"          # rojo para bordes/indicadores
DANGER_BTN = "#983A3A"      # relleno del botón borrar (TEXT = 4,6:1 arriba)
DANGER_BTN_HOVER = "#B24545"  # al pasar el ratón (texto blanco, 4,6:1)
DANGER_DARK = "#873232"     # pulsado
DANGER_EDGE = "#F0A0A0"     # borde claro del realce de la papelera: sobre el
                            # rojo vivo (#B84A4A) el borde oscuro no se veía
DANGER_ICON = "#FFC0C0"     # glifo de la papelera al pasar por encima: blanco
                            # teñido de rojo, para que el realce avise de verdad
                            # (sobre DANGER = 3,3:1, suficiente para un icono)
WARNING = "#FFAF23"         # naranja (LTS)
SUCCESS_TEXT = "#6FCF7A"    # verde claro para texto
INFO_TEXT = "#80ACE2"       # azul claro para texto (4,5:1 sobre el hover)
INFO_DISC = "#45729B"       # disco del icono de información

# Título de una versión que **no** tienes (la tienda): más apagado que TEXT
# para que se lea "no instalada", pero opaco y por encima de 4,5:1. Antes era
# TEXT al 60 % de opacidad, y su línea de detalles MUTED al 60 %: 2,8:1. Lo que
# se atenúa de verdad es el logo; los detalles van en MUTED, como en las demás.
TEXT_DIM = "#B4B4B4"
# Texto de un control deshabilitado (WCAG no exige contraste a lo inactivo,
# pero tiene que seguir viéndose que está ahí).
TEXT_DISABLED = "rgba(230,230,230,0.35)"

# Botón "Lanzar": el glifo de "play" en verde y el tinte al pasar el ratón, un
# azul pizarra lo bastante claro para que se note que es pulsable, sin el salto
# del ACCENT a plena saturación.
LAUNCH_ICON = "#22C55E"
LAUNCH_HOVER = "#2B4A66"

# Colores con alfa (para QSS: rgba)
# Borde oscuro y fino de botones, pastillas, tarjetas y filas: separa el
# control de lo que tiene detrás sin dibujar un contorno marcado.
EDGE = "rgba(0,0,0,0.35)"
# Línea entre las filas de una tarjeta de Ajustes.
SEPARATOR = "rgba(0,0,0,0.18)"

# --- Métricas de la interfaz ---
# Alto de la fila de filtros (la de las pestañas de canal). Migración y Ajustes
# lo usan para bajar su tira de pestañas y que las tres filas terminen a la
# misma altura al cambiar de vista.
FILTERS_HEIGHT = 50
# Margen superior de las tiras de pestañas (Migración y Ajustes) para que su
# **borde de arriba** quede a la altura del primer botón de la barra lateral y
# de los tags de canal. Se alinea por arriba, no por abajo: las pestañas y los
# tags no miden lo mismo.
TABS_TOP = 14
# Alto de los controles de la fila de filtros (pastillas de canal, rejilla/lista,
# desplegables) **y** de las pestañas de Migración y Ajustes: todo lo que se
# lee como "una pestaña o un filtro" mide lo mismo, aunque viva en filas
# distintas. Antes cada una salía de su padding y las de Migración/Ajustes
# quedaban 1 px más bajas que las pastillas.
CONTROL_HEIGHT = 28

# --- Tipografía ---
FONT_SIZE = 13
# Cadena de familias, en orden de preferencia. **No se empaqueta ninguna
# fuente**: se pide la que trae cada sistema y Qt resuelve los glifos que falten
# con su propio *fallback* (así el chino, el ruso y el japonés se ven aunque la
# primera familia no los tenga). "Inter" es la que usa Blender desde 3.x y la
# que hace que la app se parezca a él cuando está instalada; detrás van las de
# cada plataforma (Segoe UI en Windows, SF/Roboto en macOS) y, al final, las
# libres que casi cualquier Linux trae (Noto Sans, DejaVu). El último
# "sans-serif" es el comodín del propio Qt.
FONT_FAMILY = ("Inter, Noto Sans, Segoe UI, SF Pro Text, Helvetica Neue, "
               "Roboto, DejaVu Sans, sans-serif")

# --- Fuente de iconos (Font Awesome Free) ---
ICON_FONT_FILE = "fa-solid-900.ttf"


def luminance(token: str) -> float:
    """Luminancia relativa WCAG de un ``#RRGGBB``."""
    token = token.lstrip("#")
    channels = [int(token[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: str, b: str) -> float:
    """Ratio de contraste WCAG entre dos ``#RRGGBB`` (para tests)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)
