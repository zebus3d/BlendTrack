"""Stylesheet global de la aplicación (sustituye a los ``views/*.kv``).

En Kivy el aspecto vivía repartido en cuatro archivos ``.kv`` con instrucciones
de canvas. En Qt todo eso se expresa con QSS: ``border-radius`` para las
esquinas redondeadas, ``qlineargradient`` para el degradado superior de los
botones y los pseudo-estados ``:hover``/``:pressed``/``:checked``/``:disabled``.

Los widgets se distinguen por ``objectName`` (o por una propiedad dinámica para
las variantes de color): el código solo pone el nombre y aquí se decide el
aspecto.
"""

from ui import theme as t


def _lighten(color: str, amount: float) -> str:
    """Mezcla ``color`` (#RRGGBB) hacia blanco, para el degradado."""
    value = color.lstrip("#")
    channels = [int(value[index:index + 2], 16) for index in (0, 2, 4)]
    mixed = [round(channel + (255 - channel) * amount) for channel in channels]
    return "#%02X%02X%02X" % tuple(mixed)


# Cuánto blanco lleva arriba el degradado de los botones de color.
VOLUME = 0.09


def gradient_top(color: str) -> str:
    """El extremo más claro del degradado de un botón: donde peor se lee el texto.

    Es el color contra el que hay que medir el contraste del texto de un botón
    con volumen; medirlo contra el color base engaña (daba 4,6:1 donde arriba
    había 3,56:1).
    """
    return _lighten(color, VOLUME)


def _volume(color: str) -> str:
    """Degradado vertical que da volumen a un botón, sobre su color base.

    Es el ``GRADIENT_TOP`` de la versión Kivy: una capa blanca del 9 % arriba
    que se desvanece hacia abajo. En QSS no se pueden apilar dos fondos, así que
    el 9 % de blanco se mezcla en el color del extremo superior.
    """
    return ("qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {gradient_top(color)}, stop:1 {color})")


# Alto de la caja de contenido de una pestaña para que el total sea
# ``CONTROL_HEIGHT`` (el de las pastillas): padding vertical 4+4 y el borde,
# que en las pestañas "abiertas" (Migración/Ajustes, sin borde inferior) es de
# 1 px y en las "cerradas" (canales) de 2.
TAB_CONTENT_OPEN = t.CONTROL_HEIGHT - 8 - 1
TAB_CONTENT_CLOSED = t.CONTROL_HEIGHT - 8 - 2


def build_qss() -> str:
    """Devuelve el stylesheet completo, con los tokens del tema interpolados."""
    return f"""
    QWidget {{
        background-color: {t.BG};
        color: {t.TEXT};
        font-size: {t.FONT_SIZE}px;
    }}
    QLabel {{ background: transparent; }}
    /* El contenedor de refresco+buscador NO debe pintar su fondo: si no,
       aparece un recuadro más oscuro dentro de la cabecera. */
    QWidget#HeaderTools {{ background: transparent; }}
    /* Filas que solo agrupan un campo y su botón: sin fondo, o el QWidget
       pinta el BG oscuro y aparece una banda dentro de la tarjeta clara. */
    QWidget#FormRow {{ background: transparent; }}
    QToolTip {{
        background-color: {t.BG};
        color: {t.TEXT};
        border: 1px solid {t.SURFACE_ALT};
        padding: 5px 9px;
    }}

    /* --- Pastillas (filtros de canal, selector de vista) --- */
    QPushButton#Pill {{
        background-color: {t.FILTER};
        border: 1px solid {t.EDGE};
        border-radius: 6px;
        padding: 4px 9px;
        color: {t.TEXT};
        font-weight: bold;
    }}
    QPushButton#Pill:hover {{ background-color: {t.ACCENT_DARK}; color: {t.TEXT_SEL}; }}
    QPushButton#Pill:checked {{ background-color: {t.ACCENT_SELECTED}; color: {t.TEXT_SEL}; }}
    QPushButton#Pill:pressed {{
        background-color: {t.ACCENT_BTN_PRESSED}; color: {t.TEXT_SEL};
    }}
    QPushButton#Pill:disabled {{ color: {t.TEXT_DISABLED}; }}

    /* --- Pestañas (canal, migración y ajustes) ---
       Mismo aspecto en las tres: oscuras, compactas y con el mismo color de
       texto. Las de canal van en un QTabBar suelto (sin páginas debajo); las
       otras, dentro de un QTabWidget que sí tiene panel.
       El QTabBar es un QWidget pelado y, si no, pinta el BG del tema (más
       oscuro) y tapa el gris de la fila. */
    QTabBar#ChannelTabs,
    QTabWidget#MigrateTabs QTabBar,
    QTabWidget#SettingsTabs QTabBar {{ background: transparent; }}
    QTabWidget#MigrateTabs QTabBar::tab,
    QTabWidget#SettingsTabs QTabBar::tab {{
        background-color: {t.FILTER};
        border: 1px solid {t.EDGE};
        /* Redondeadas arriba y rectas abajo: se leen como pestañas y no como
           botones. */
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 4px 12px;
        margin-right: 4px;
        /* Mismo alto que las pastillas de filtro (CONTROL_HEIGHT). En QSS el
           alto es el de la caja de contenido: al total le restamos el padding
           (4+4) y el borde de arriba (abajo no hay). */
        min-height: {TAB_CONTENT_OPEN}px; max-height: {TAB_CONTENT_OPEN}px;
        color: {t.TEXT};
        font-weight: bold;
    }}
    /* Los canales van como **pastillas**, no como pestañas pegadas al lienzo:
       son excluyentes (filtran la lista de debajo) y así no parecen parte del
       contenido. */
    QTabBar#ChannelTabs::tab {{
        background-color: {t.FILTER};
        border: 1px solid {t.EDGE};
        /* Un poco cuadradas (no píldoras perfectas) y algo más arriba, para
           que no queden a ras del contenido de debajo. */
        border-radius: 6px;
        padding: 4px 14px;
        /* Alto explícito (ver arriba): aquí hay borde arriba y abajo. */
        min-height: {TAB_CONTENT_CLOSED}px; max-height: {TAB_CONTENT_CLOSED}px;
        /* Pegados arriba (a la altura del botón de Local) y con aire debajo,
           para no quedar a ras del contenido. */
        margin: 8px 6px 16px 0;
        color: {t.TEXT};
        font-weight: bold;
    }}
    QTabBar#ChannelTabs::tab:hover,
    QTabWidget#MigrateTabs QTabBar::tab:hover,
    QTabWidget#SettingsTabs QTabBar::tab:hover {{ background-color: {t.ACCENT_DARK}; color: {t.TEXT_SEL}; }}
    QTabBar#ChannelTabs::tab:selected,
    QTabWidget#MigrateTabs QTabBar::tab:selected,
    QTabWidget#SettingsTabs QTabBar::tab:selected {{ background-color: {t.ACCENT_SELECTED}; color: {t.TEXT_SEL}; }}

    /* --- Barra lateral --- */
    QPushButton#SideButton {{
        background-color: {t.BUTTON};
        border: 1px solid {t.EDGE};
        border-radius: 8px;
        min-width: 50px; max-width: 50px;
        min-height: 50px; max-height: 50px;
        color: {t.TEXT};
        /* El glifo, a 20 px. El tamaño de un icono en un widget va aquí y no en
           ``setFont(icon_font(20))``: la regla global de ``QWidget`` pisaría el
           tamaño (ver ``fonts.icon_font``). */
        font-size: 20px;
    }}
    QPushButton#SideButton:hover {{ background-color: {t.ACCENT_DARK}; color: {t.TEXT_SEL}; }}
    QPushButton#SideButton:checked {{ background-color: {t.ACCENT_SELECTED}; color: {t.TEXT_SEL}; }}
    QPushButton#SideButton:pressed {{
        background-color: {t.ACCENT_BTN_PRESSED}; color: {t.TEXT_SEL};
    }}

    /* --- Botón de tarjeta / acción (relleno + degradado + borde) ---
       El degradado (blanco 9 % arriba) solo va en los botones de color, como en
       Kivy: en los grises enturbia el texto. "dark" es el gris oscuro de las
       pestañas de Blender, que es el que usa Lanzar. */
    QPushButton#CardButton {{
        background-color: {t.BUTTON};
        border: 1px solid {t.EDGE};
        border-radius: 5px;
        padding: 6px 14px;
        color: {t.TEXT};
        font-weight: bold;
    }}
    QPushButton#CardButton:hover {{ background-color: {t.BUTTON_HOVER}; }}
    QPushButton#CardButton:pressed {{
        background-color: {t.ACCENT_DARK};
        color: {t.TEXT_SEL};
    }}
    QPushButton#CardButton:disabled {{ color: {t.TEXT_DISABLED}; }}
    QPushButton#CardButton[variant="accent"] {{
        background: {_volume(t.ACCENT_BTN)};
    }}
    QPushButton#CardButton[variant="accent"]:hover {{
        background: {_volume(t.ACCENT_BTN_HOVER)};
        color: {t.TEXT_SEL};
    }}
    QPushButton#CardButton[variant="accent"]:pressed {{
        background: {_volume(t.ACCENT_BTN_PRESSED)};
        color: {t.TEXT_SEL};
    }}
    QPushButton#CardButton[variant="dark"] {{
        background: {_volume(t.FILTER)};
    }}
    QPushButton#CardButton[variant="dark"]:hover {{
        background: {_volume(t.LAUNCH_HOVER)};
        border: 1px solid {t.ACCENT};
        color: {t.TEXT_SEL};
    }}
    QPushButton#CardButton[variant="dark"]:pressed {{
        background: {_volume(t.ACCENT_BTN_PRESSED)};
        border: 1px solid {t.ACCENT};
        color: {t.TEXT_SEL};
    }}
    QPushButton#CardButton[variant="danger"] {{
        background: {_volume(t.DANGER_BTN)};
    }}
    QPushButton#CardButton[variant="danger"]:hover {{
        background: {_volume(t.DANGER_BTN_HOVER)};
        border: 1px solid {t.DANGER_EDGE};
        color: {t.TEXT_SEL};
    }}
    QPushButton#CardButton[variant="danger"]:pressed {{
        background: {_volume(t.DANGER_DARK)};
        border: 1px solid {t.DANGER_EDGE};
        color: {t.TEXT_SEL};
    }}
    /* La papelera es un glifo, así que en el realce vale teñirlo de rojo (3:1).
       El selector se acota a [iconOnly] para no tocar el texto de los botones
       rojos de los diálogos, que sí necesita 4,5:1 y por eso va en blanco. */
    QPushButton#CardButton[variant="danger"][iconOnly="true"]:hover,
    QPushButton#CardButton[variant="danger"][iconOnly="true"]:pressed {{
        color: {t.DANGER_ICON};
    }}
    /* Botón que solo lleva un icono (papelera): con el padding normal (14 px por
       lado) el glifo no cabe cuando la rejilla va pequeña y queda el recuadro
       rojo vacío. Con 4 px sobra sitio incluso a zoom 0.6. */
    QPushButton#CardButton[iconOnly="true"] {{ padding: 6px 4px; }}
    /* El botón de consola (gris, solo icono) se realza **oscureciéndose**, al
       revés que los botones grises con texto: un glifo suelto que se aclara se
       lee como si cambiara de color, no como un realce. Se acota a [iconOnly]
       para no tocar el resto de botones neutros. */
    QPushButton#CardButton[variant="neutral"][iconOnly="true"]:hover {{
        background-color: {t.SURFACE_ALT};
    }}

    /* --- Botón de info (círculo azul con la "i"), diana 24x24 (WCAG 2.5.8) --- */
    QPushButton#IconLink {{
        background-color: {t.INFO_DISC};
        /* Reborde blanco fino que separa el disco azul del fondo oscuro de la
           tarjeta. En QSS el borde se suma al ancho, así que el contenido baja
           a 20 px para que la diana siga midiendo 24x24 (como la estrella); la
           "i" (13 px) sigue cabiendo de sobra. */
        border: 2px solid {t.TEXT_SEL};
        border-radius: 12px;
        min-width: 20px; max-width: 20px;
        min-height: 20px; max-height: 20px;
        color: {t.TEXT_SEL};
    }}
    QPushButton#IconLink:hover {{ background-color: {t.ACCENT}; }}
    QPushButton#IconLink:pressed {{ background-color: {t.ACCENT_BTN_PRESSED}; }}

    /* --- Estrella de favorito (misma diana de 24x24 que la "i") ---
       Se usa el mismo glifo en los dos estados: apagado cuando no está marcada
       y en ámbar cuando sí (solo empaquetamos la fuente sólida de iconos). */
    QPushButton#StarButton {{
        background: transparent;
        /* Borde transparente fijo: el aro de foco solo cambia su color, así
           que no mueve nada. El contenido baja a 22 para que la diana siga
           midiendo 24x24. */
        border: 1px solid transparent;
        border-radius: 4px;
        color: {t.ICON_MUTED};
        /* El glifo de la estrella aprovecha menos su caja que la "i", así que a
           13 px (el tamaño global) se veía más pequeña que el disco de info.
           Medido con tightBoundingRect: 16 px le da 18x17 px de tinta, a la par
           del disco azul de 20 px de la "i". */
        font-size: 16px;
        min-width: 22px; max-width: 22px;
        min-height: 22px; max-height: 22px;
    }}
    /* Al pasar por encima ya se enciende en ámbar: adelanta lo que va a pasar
       al pulsar (en gris claro parecía un icono cualquiera al que se le sube
       el brillo). */
    QPushButton#StarButton:hover {{ color: {t.WARNING}; }}
    QPushButton#StarButton:checked {{ color: {t.WARNING}; }}
    /* Al pulsar, el glifo baja un poco: se "hunde" como un botón. */
    QPushButton#StarButton:pressed {{ padding-top: 2px; }}

    /* --- Botón de icono plano (refrescar, etc.) --- */
    QPushButton#IconFlat {{
        background: transparent;
        /* Borde transparente fijo (ver la estrella): 30 + 2 = diana de 32. */
        border: 1px solid transparent;
        color: {t.TEXT};
        /* Glifo a 16 px dentro de la diana de 32 (refrescar, "más opciones"). */
        font-size: 16px;
        border-radius: 6px;
        min-width: 30px; max-width: 30px;
        min-height: 30px; max-height: 30px;
    }}
    QPushButton#IconFlat:hover {{ background-color: {t.SURFACE_ALT}; }}
    QPushButton#IconFlat:pressed {{ background-color: {t.FIELD}; }}
    QPushButton#IconFlat:disabled {{ color: {t.TEXT_DISABLED}; }}

    /* --- Tarjetas ---
       OJO con el orden: en QSS, `#Card[installed="true"]` y `#Card:hover`
       tienen la MISMA especificidad, así que gana la regla que va más abajo.
       Por eso el hover va al FINAL y repite todas las combinaciones: si no,
       las tarjetas instaladas (que llevan [installed="true"]) nunca se
       resaltaban al pasar el ratón. */
    QFrame#Card {{
        background-color: {t.CARD_DIM};
        border-radius: 12px;
        border: none;
    }}
    QFrame#Card[zebra="true"] {{ background-color: {t.CARD_DIM_ALT}; }}
    QFrame#Card[installed="true"] {{ background-color: {t.SURFACE}; }}
    QFrame#Card[installed="true"][zebra="true"] {{ background-color: {t.ROW_ALT}; }}
    QFrame#Card:hover,
    QFrame#Card[zebra="true"]:hover,
    QFrame#Card[installed="true"]:hover,
    QFrame#Card[installed="true"][zebra="true"]:hover {{
        background-color: {t.SURFACE_ALT};
    }}
    /* La propiedad la pone ``_HoverCard`` (enter/leave): cubre el caso de que el
       ratón esté sobre un hijo y el padre no reciba el estado hover. */
    QFrame#Card[hover="true"] {{ background-color: {t.SURFACE_ALT}; }}
    /* La tarjeta de rejilla que va dentro de la grande de la galería deja de
       ser tarjeta: el fondo (y su hover) lo pone la de fuera. Va después de
       los hover para ganarles (misma especificidad, gana la última). */
    QFrame#Card[embedded="true"],
    QFrame#Card[embedded="true"][hover="true"],
    QFrame#Card[embedded="true"]:hover {{ background: transparent; }}
    QWidget#GalleryInfo {{ background: transparent; }}
    /* Los botones de la esquina (galería y rejilla): pequeños, redondos y a
       medio fundir con el fondo, para que no compitan con las cartas. */
    QPushButton#CornerButton {{
        background-color: rgba(0, 0, 0, 110);
        color: {t.MUTED};
        border: 1px solid {t.BORDER};
        border-radius: 13px;
        font-size: 12px;
        padding: 0;
    }}
    QPushButton#CornerButton:hover {{
        background-color: {t.SURFACE_ALT};
        color: {t.TEXT};
    }}
    /* El botón de los efectos de la rejilla lleva las letras "FX": más pequeñas
       y en negrita, y el color cambia según esté encendido o apagado. */
    QPushButton#CornerButton[fx="true"] {{
        font-size: 10px;
        font-weight: 700;
    }}
    /* Los glifos de la esquina de la rejilla (engranaje y sonido) un punto más
       grandes, para que midan como el "FX" de al lado. La galería se queda a 12. */
    QPushButton#CornerButton[cornerIcon="true"] {{ font-size: 13px; }}
    QPushButton#CornerButton[fx="true"][active="true"] {{
        color: {t.ACCENT};
        border-color: {t.ACCENT};
    }}
    QPushButton#CornerButton[fx="true"][active="false"] {{
        color: {t.TEXT};
        border-color: {t.TEXT_DIM};
    }}
    /* El panel de ajustes del efecto de la rejilla (rotación, luz y halo):
       un popup flotante como los de Blender. Los colores son los de su tema por
       defecto (``wcol_menu_back``: fondo #181818, borde #242424), con un punto
       de transparencia para que se lea como popup sobre las tarjetas. */
    QWidget#TiltPanel {{
        background-color: rgba(24, 24, 24, 235);
        border: 1px solid #242424;
        border-radius: 8px;
    }}
    QLabel#GalleryValue {{ color: {t.TEXT}; }}

    /* --- Paneles / barras --- */
    QFrame#Chrome {{ background-color: {t.CHROME}; border: none; }}
    QFrame#Sidebar {{ background-color: {t.SURFACE}; border: none; }}
    QFrame#SettingsCard {{
        background-color: {t.SURFACE};
        border-radius: 10px;
        border: 1px solid {t.EDGE};
    }}
    /* Filas de addons (el tablero de Migración y el gestor de Add-ons). Van
       DENTRO de una tarjeta SURFACE, así que bajan un escalón (CARD_DIM): con
       el mismo gris se perderían. El cebreado solo lo usa el tablero. */
    QFrame#AddonRow {{
        background-color: {t.CARD_DIM};
        border-radius: 8px;
        border: 1px solid {t.EDGE};
    }}
    QFrame#AddonRow[zebra="true"] {{ background-color: {t.CARD_DIM_ALT}; }}

    /* --- Biblioteca de carpetas (Ajustes > Carpetas) ---
       Las filas viven DENTRO de una tarjeta SURFACE, así que bajan un escalón
       igual que las de add-ons de Migración; si no, no se distinguirían del
       fondo de la tarjeta. El scroll y su contenido van transparentes: un
       QScrollArea pinta el BG por su cuenta y cortaría la tarjeta. */
    QFrame#FolderRow {{
        background-color: {t.CARD_DIM};
        border-radius: 8px;
        border: 1px solid {t.EDGE};
    }}
    QFrame#FolderRow[zebra="true"] {{ background-color: {t.CARD_DIM_ALT}; }}
    QFrame#FolderRow[missing="true"] {{ border: 1px solid {t.WARNING}; }}
    QScrollArea#FolderList {{ background: transparent; border: none; }}
    QScrollArea#FolderList > QWidget > QWidget {{ background: transparent; }}
    QWidget#FolderListBody {{ background: transparent; }}
    /* Lo mismo para la lista de claves cambiadas de Migración > Preferencias:
       vive dentro de una tarjeta SURFACE y el scroll no puede cortarla. */
    QScrollArea#DetailPrefs {{ background: transparent; border: none; }}
    QScrollArea#DetailPrefs > QWidget > QWidget {{ background: transparent; }}
    QWidget#DetailPrefsBody {{ background: transparent; }}
    /* Cuando la lista NO cabe entera y sale la barra, el lienzo baja al fondo
       oscuro de las listas: así se ve de dónde a dónde llega lo que se
       desplaza (con el gris de la tarjeta, la barra parecía salir de la nada).
       La propiedad la pone ``_sunken_when_scrolling`` siguiendo el rango de la
       barra. Van los dos selectores porque el cuerpo lleva id propio y gana al
       del área. */
    QScrollArea#DetailPrefs[scrolling="true"] {{ background-color: {t.BG}; }}
    QWidget#DetailPrefsBody[scrolling="true"] {{ background-color: {t.BG}; }}
    /* Gestor de guardados (Migración > Valores de fábrica): cada guardado es
       una fila, como las de add-ons, y el scroll va transparente para no cortar
       la tarjeta. */
    QFrame#SnapshotRow {{
        background-color: {t.CARD_DIM};
        border-radius: 8px;
        border: 1px solid {t.EDGE};
    }}
    QScrollArea#SnapshotList {{ background: transparent; border: none; }}
    QScrollArea#SnapshotList > QWidget > QWidget {{ background: transparent; }}
    QWidget#SnapshotListBody {{ background: transparent; }}
    /* Mismo lienzo hundido que en "Preferencias en detalle" cuando hay barra. */
    QScrollArea#SnapshotList[scrolling="true"] {{ background-color: {t.BG}; }}
    QWidget#SnapshotListBody[scrolling="true"] {{ background-color: {t.BG}; }}
    QWidget#SnapshotDetailsBody {{ background: transparent; }}

    /* --- Recientes: como las listas (fondo BG) con filas SURFACE encima --- */
    QWidget#RecentView {{ background-color: {t.BG}; }}
    QFrame#RecentRow {{
        background-color: {t.SURFACE};
        border-radius: 8px;
        border: 1px solid {t.EDGE};
    }}
    QFrame#RecentRow:hover {{ background-color: {t.SURFACE_ALT}; }}
    /* Fichero que ya no está en su ruta: apagado y sin realce (no se puede
       abrir). Va después del :hover para ganarle con la misma especificidad. */
    QFrame#RecentRow[missing="true"],
    QFrame#RecentRow[missing="true"]:hover {{
        background-color: {t.CARD_DIM_ALT};
        color: {t.MUTED};
    }}
    QScrollArea#RecentScroll {{ background: transparent; border: none; }}
    QScrollArea#RecentScroll > QWidget > QWidget {{ background: transparent; }}
    QWidget#RecentBody {{ background: transparent; }}

    /* --- Add-ons: tarjetas con filas dentro (fondo BG, filas CARD_DIM) --- */
    QWidget#AddonsView {{ background-color: {t.BG}; }}
    QScrollArea#AddonsScroll {{ background: transparent; border: none; }}
    QScrollArea#AddonsScroll > QWidget > QWidget {{ background: transparent; }}
    QWidget#AddonsBody {{ background: transparent; }}
    /* El candado cerrado, en ámbar: que "aquí no se escribe" se vea de un
       vistazo y no haya que pasar el ratón por encima para enterarse. */
    QPushButton#CardButton[writable="false"] {{
        color: {t.WARNING};
    }}
    /* El candado, a 14 px: un poco mayor que el texto para leerse de un
       vistazo, sin salirse del botón de 46 px. */
    QPushButton#CardButton[writable="true"],
    QPushButton#CardButton[writable="false"] {{ font-size: 14px; }}

    /* --- Casilla de verificación (migración) ---
       El cuadro y la palomita los pinta ``CheckPill`` con QPainter: por QSS no
       se puede (la regla global de ``QWidget`` hereda sobre el ``::indicator``
       y Qt descarta su ``image``). Aquí solo se deja el color del texto. */
    QCheckBox {{ color: {t.TEXT}; spacing: 8px; }}

    /* --- Migración: mismo esquema de capas que las listas y Ajustes ---
       La vista va en SURFACE, pero de ella solo se ve la **fila de detrás de
       las pestañas**: el panel lo tapa todo lo demás y va oscuro (BG), como el
       fondo de Local y Nube. Encima, las tarjetas en SURFACE con su sombra.
       Antes toda la vista iba en SURFACE y era la única pantalla con el fondo
       gris claro de arriba abajo. */
    QWidget#MigrateView {{ background-color: {t.SURFACE}; }}
    /* La tarjeta de versiones va dentro de la pestaña: su contenedor es un
       QWidget pelado, así que se pinta el fondo oscuro de la página. */
    QWidget#MigrateHeader {{ background-color: {t.BG}; }}
    QTabWidget#MigrateTabs, QTabWidget#SettingsTabs {{ background-color: {t.SURFACE}; }}

    /* El pane es la única parte del QTabWidget que pinta fondo; las páginas se
       pintan el suyo (un QWidget pelado no siempre deja pasar el del pane).
       El borde de arriba es la línea que separa las solapas del contenido. */
    QTabWidget#MigrateTabs::pane, QTabWidget#SettingsTabs::pane {{
        border: none;
        border-top: 1px solid {t.SURFACE_ALT};
        background-color: {t.BG};
        top: -1px;
    }}
    QWidget#MigratePage {{ background-color: {t.BG}; }}
    /* Las páginas de Migración que llevan tarjetas estirables van dentro de un
       scroll: al agrandar una, las de abajo bajan en vez de quedarse debajo.
       Transparente para que se vea el fondo de la página. */
    QScrollArea#MigrateScroll {{ background: transparent; border: none; }}
    /* Las tarjetas se quedan con el SURFACE de `QFrame#SettingsCard` (el mismo
       gris que las tarjetas de las listas): ahora que la página va oscura, no
       hace falta subirlas un escalón más. */

    /* --- Ajustes: igual que Migración ---
       La página exterior y la tira de pestañas van en SURFACE; el panel de cada
       pestaña (``SettingsTabPage``) baja a BG, como el fondo de Local y Nube, y
       las tarjetas se quedan en SURFACE con la sombra que pone
       `_settings_card`. */
    QWidget#SettingsPage {{ background-color: {t.SURFACE}; }}
    QWidget#SettingsTabPage {{ background-color: {t.BG}; }}

    /* --- Campos de texto --- */
    QLineEdit {{
        background-color: {t.FIELD};
        /* Borde transparente fijo y un píxel menos de padding: al recibir el
           foco solo cambia el color del borde, y el campo mide lo mismo. */
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 5px 11px;
        color: {t.TEXT};
        selection-background-color: {t.ACCENT_SELECTED};
        selection-color: {t.TEXT_SEL};
        /* El color del texto de ejemplo va por aquí: ``QLineEdit::placeholder``
           no existe en Qt Style Sheets y la regla se ignoraba en silencio. */
        placeholder-text-color: {t.PLACEHOLDER};
    }}
    /* Campo multilínea (variables de entorno): mismo aspecto que un QLineEdit,
       pero con el texto alineado arriba. */
    QPlainTextEdit {{
        background-color: {t.FIELD};
        border: 1px solid transparent;
        border-radius: 8px;
        padding: 5px 11px;
        color: {t.TEXT};
        selection-background-color: {t.ACCENT_SELECTED};
        selection-color: {t.TEXT_SEL};
        placeholder-text-color: {t.PLACEHOLDER};
    }}
    /* Campo de renombrado en línea (doble clic en una instalada): compacto y
       con borde de acento, para que se note que se está editando. */
    QLineEdit#InlineEdit {{
        background-color: {t.FIELD};
        border: 1px solid {t.ACCENT};
        border-radius: 4px;
        padding: 0 4px;
    }}
    /* Importe de la donación: mismo aspecto que un QLineEdit, pero con borde
       visible para que se lea como un campo editable sobre el popup oscuro. */
    QDoubleSpinBox#DonationAmount {{
        background-color: {t.FIELD};
        border: 1px solid {t.BORDER};
        border-radius: 8px;
        padding: 5px 11px;
        color: {t.TEXT};
        selection-background-color: {t.ACCENT_SELECTED};
        selection-color: {t.TEXT_SEL};
    }}
    QDoubleSpinBox#DonationAmount:hover {{ border-color: {t.MUTED}; }}
    QDoubleSpinBox#DonationAmount:focus {{ border-color: {t.ACCENT}; }}

    /* --- Desplegables --- */
    QComboBox {{
        background-color: {t.FILTER};
        border: 1px solid {t.EDGE};
        border-radius: 5px;
        padding: 4px 8px;
        color: {t.TEXT};
        min-height: 24px;
    }}
    /* Los combos de la fila de filtros van a la altura de los tags de canal. */
    QComboBox#FilterCombo {{
        padding: 3px 8px;
        min-height: 20px;
    }}
    QComboBox:hover {{ background-color: {t.ACCENT_DARK}; color: {t.TEXT_SEL}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background-color: {t.SURFACE};
        color: {t.TEXT};
        selection-background-color: {t.ACCENT_SELECTED};
        selection-color: {t.TEXT_SEL};
        border: 1px solid {t.SURFACE_ALT};
        outline: none;
    }}

    /* --- Interruptor Sí/No --- */
    /* --- Interruptor (toggle) ---
       Se pinta a mano en SwitchPill.paintEvent: aquí solo se quita el fondo, el
       borde y el padding que pondría el estilo. */
    QPushButton#Switch {{
        background: transparent;
        border: none;
        padding: 0;
    }}

    /* --- Barra de progreso --- */
    QProgressBar {{
        background-color: {t.FIELD};
        border-radius: 4px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background-color: {t.ACCENT}; border-radius: 4px; }}

    /* --- Slider de zoom (sin recuadro: el fondo va transparente) --- */
    QSlider {{
        background: transparent;
        border: none;
    }}
    QSlider::groove:horizontal {{
        background: {t.FIELD};
        height: 6px;
        border-radius: 3px;
    }}
    QSlider::sub-page:horizontal {{ background: {t.ACCENT}; border-radius: 3px; }}
    QSlider::add-page:horizontal {{ background: {t.FIELD}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        background: {t.BUTTON};
        border: none;
        width: 16px; height: 16px;
        margin: -5px 0;
        border-radius: 8px;
    }}
    QSlider::handle:horizontal:hover {{ background: {t.ACCENT}; }}
    QSlider::handle:horizontal:pressed {{ background: {t.ACCENT_DARK}; }}
    /* En el popup de ajustes del efecto (fondo casi negro) el carril del
       deslizador no se veía: FIELD (#171717) y el fondo del popup (#181818) son
       casi el mismo color. Se aclara solo para ese popup. */
    QWidget#TiltPanel QSlider::groove:horizontal,
    QWidget#TiltPanel QSlider::add-page:horizontal {{
        background: {t.SURFACE_ALT};
    }}

    /* --- Etiquetas con color --- */
    QLabel#Muted {{ color: {t.MUTED}; }}
    QLabel#Warning {{ color: {t.WARNING}; font-weight: bold; }}
    QLabel#Info {{ color: {t.INFO_TEXT}; font-weight: bold; }}
    QLabel#Success {{ color: {t.SUCCESS_TEXT}; font-weight: bold; }}
    QLabel#Danger {{ color: {t.DANGER_EDGE}; font-weight: bold; }}
    QLabel#Title {{ font-size: 16px; font-weight: bold; }}
    /* Versión que no tienes (tienda): título y detalles atenuados. Va por
       propiedad dinámica y no con ``setStyleSheet`` en la tarjeta: así el
       color sale de un token y se ve aquí junto al resto. */
    QLabel#Title[dim="true"] {{ color: {t.TEXT_DIM}; }}
    QLabel#HeaderTitle {{ font-size: 19px; font-weight: bold; }}
    /* La sección actual: un poco más pequeña que la marca, en negrita y azul
       claro (``INFO_TEXT``, 5,9:1 sobre el gris de la cabecera). */
    QLabel#HeaderSection {{
        font-size: 17px;
        font-weight: bold;
        color: {t.INFO_TEXT};
    }}
    QLabel#FieldLabel {{ color: {t.MUTED}; font-size: 12px; }}
    /* Flechas de Migración: la de la barra "desde → hacia" acompaña al texto
       (16 px) y la del tablero separa dos columnas, así que va grande. */
    QLabel#FlowArrow {{ font-size: 16px; }}
    QLabel#BoardArrow {{ font-size: 28px; }}

    /* --- Separadores (filas de ajustes) --- */
    QFrame#RowSeparator {{ background-color: {t.SEPARATOR}; max-height: 1px; }}

    /* --- Scroll --- */
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{
        background: {t.ACCENT}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
    QScrollBar::handle:horizontal {{
        background: {t.ACCENT}; border-radius: 4px; min-width: 24px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* --- Diálogos --- */
    QDialog {{ background-color: {t.SURFACE}; }}
    QDialog QLabel#DialogTitle {{ color: {t.TEXT}; font-size: 15px; font-weight: bold; }}

    /* --- Foco de teclado ---
       Quien navega con Tab tiene que ver dónde está, pero solo él: el aro va
       con ``[focusRing="true"]`` y no con ``:focus``, porque Qt también reparte
       el foco solo (al abrir la ventana o al esconder el buscador) y con
       ``:focus`` el botón "Local" y los interruptores salían recuadrados en
       blanco sin haber tocado el teclado. La propiedad la pone ``_FocusRing``
       (``buttons.py``) solo si el foco llegó con una tecla: el equivalente al
       ``:focus-visible`` de la web. Va al final para ganar a los estados
       normales; con el ratón encima, manda el realce del hover. */
    QPushButton#Pill[focusRing="true"],
    QPushButton#SideButton[focusRing="true"],
    QPushButton#CardButton[focusRing="true"],
    QPushButton#IconFlat[focusRing="true"],
    QPushButton#StarButton[focusRing="true"] {{
        border: 1px solid {t.FOCUS_RING};
    }}
    /* La "i" ya lleva un reborde blanco: con el foco, ese reborde pasa a
       ámbar, que se distingue del blanco y del disco azul. */
    QPushButton#IconLink[focusRing="true"] {{ border-color: {t.WARNING}; }}
    QComboBox:focus,
    QLineEdit:focus,
    QPlainTextEdit:focus {{ border: 1px solid {t.ACCENT}; }}

    /* --- Menú de la bandeja del sistema --- */
    /* Sin esto el QMenu sale con el estilo claro del escritorio y desentona
       con el tema oscuro (mismo motivo por el que los diálogos son propios). */
    QMenu#TrayMenu, QMenu#CardMenu {{
        background-color: {t.SURFACE};
        color: {t.TEXT};
        border: 1px solid {t.BORDER};
        padding: 4px;
    }}
    QMenu#TrayMenu::item, QMenu#CardMenu::item {{
        padding: 6px 18px;
        border-radius: 4px;
    }}
    QMenu#TrayMenu::item:selected, QMenu#CardMenu::item:selected {{
        background-color: {t.ACCENT_SELECTED};
        color: {t.TEXT_SEL};
    }}
    QMenu#TrayMenu::separator, QMenu#CardMenu::separator {{
        height: 1px;
        background: {t.BORDER};
        margin: 4px 8px;
    }}
    """
