"""Tarjetas de versiones: la tienda y las versiones instaladas.

Cada tarjeta existe en versión lista (una fila) y rejilla (icono grande). En
vez de llamar directamente a la ventana, emiten señales y la ventana las
conecta. Aquí viven también ``settings_card``/``GripCard``, la tarjeta de
Ajustes, Migración y Add-ons.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from i18n import tr
from model.build import FORK_BLENDER
from paths import ASSETS_DIR
from ui import icons
from ui import theme as t
from ui.fonts import device_ratio, glyph_icon, icon_font
from ui.widgets.buttons import CardButton, IconLinkButton, StarButton
from ui.widgets.labels import EditableLabel, ElidedLabel
from ui.widgets import motion, tilt
from ui.widgets.layouts import repolish

# Un logo por fork. El de Blender está en el centro; Bforartists y UPBGE traen
# el suyo, porque son programas distintos y la tarjeta tiene que decir cuál es
# antes de leer el texto.
_LOGOS = {
    FORK_BLENDER: ASSETS_DIR / "images" / "blender_logo.png",
    "bforartists": ASSETS_DIR / "images" / "bforartists_logo.png",
    "upbge": ASSETS_DIR / "images" / "upbge_logo.png",
}



def logo_path(fork: str):
    """Ruta del logo de un fork (el de Blender si no se conoce)."""
    return _LOGOS.get(fork, _LOGOS[FORK_BLENDER])


# Los PNG se cargan una vez por fork y las versiones escaladas/atenuadas se
# guardan por (fork, tamaño, atenuado). Antes cada tarjeta hacía
# ``QPixmap(_LOGO)`` al construirse: al mover el zoom se reconstruye la rejilla
# entera cada pocos ticks, así que eran cientos de lecturas y decodificaciones
# del mismo PNG por segundo (y en Windows cada lectura pasa por el antivirus).
_logo_sources: dict = {}
_logo_cache: dict = {}


def _logo_source_pixmap(fork: str) -> QPixmap:
    """El PNG del fork cargado una sola vez."""
    fork = fork or FORK_BLENDER
    if fork not in _logo_sources:
        _logo_sources[fork] = QPixmap(str(_LOGOS.get(fork, _LOGOS[FORK_BLENDER])))
    return _logo_sources[fork]


def _logo_pixmap(size: int, dim: bool, fork: str = FORK_BLENDER) -> QPixmap:
    """Logo del fork escalado a ``size`` (y atenuado si ``dim``), desde la caché.

    Se escala a los píxeles **físicos** (``size × device_ratio``) y se marca
    con ese ratio: escalado a ``size`` a secas, en una pantalla HiDPI Qt lo
    estiraba al doble y el logo salía borroso.
    """
    ratio = device_ratio()
    key = (fork or FORK_BLENDER, size, dim, ratio)
    cached = _logo_cache.get(key)
    if cached is not None:
        return cached
    pix = _logo_source_pixmap(fork)
    if pix.isNull():
        return pix
    physical = round(size * ratio)
    scaled = pix.scaled(physical, physical, Qt.KeepAspectRatio,
                        Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    if dim:
        scaled = _with_opacity(scaled, 0.32)
    _logo_cache[key] = scaled
    return scaled

# Ancho mínimo de un botón que solo lleva un icono (la papelera de la rejilla):
# con el padding corto del QSS ([iconOnly]) el glifo de 13 px necesita ~21 px.
MIN_ICON_BUTTON_WIDTH = 26


def _icon_only(button) -> None:
    """Marca un CardButton que solo lleva un icono.

    El QSS le quita el padding lateral (``[iconOnly="true"]``): con el normal,
    a zoom bajo el ancho fijo se queda por debajo del padding y la papelera
    salía como un recuadro rojo vacío. Vale con poner la propiedad antes de que
    el widget se muestre (igual que ``zebra`` o ``installed``): el primer
    *polish* ya la lee.
    """
    button.setProperty("iconOnly", "true")


def _launch_icon():
    """Icono de "play" verde como QIcon.

    En Kivy el icono se pintaba dentro del texto del botón (mezclando fuentes en
    el markup); en Qt hay que pasarlo como QIcon.
    """
    return _action_icon(icons.LAUNCH, t.LAUNCH_ICON)


def _download_icon():
    """Icono blanco de descarga (flecha hacia abajo), como el original."""
    return _action_icon(icons.DOWNLOAD, t.TEXT_SEL)


def _action_icon(glyph: str, color: str):
    """Glifo de 12 px como ``QIcon``, para ir junto al texto de un botón."""
    return glyph_icon(glyph, 12, color)


def _drop_shadow(widget, blur: float, offset: float, alpha: int):
    """Sombra negra suave bajo un widget (el ``drop-shadow`` de CSS, en Qt).

    Único sitio que construye el efecto: la del logo y la de la tarjeta solo
    se diferencian en cuánto difumina y cuánto se desplaza.
    """
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(offset, offset)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return effect


def logo_shadow(widget, size: int):
    """Sombra negra suave bajo el logo: el `drop-shadow` de CSS, versión Qt.

    Qt repinta el widget a un pixmap y lo difumina (efecto de software), así que
    se paga al pintar. Medido: con los logos de las tarjetas (40-120 px) la
    rejilla completa sube ~1 ms, nada al lado de lo que cuesta reconstruirla.

    El radio y el desplazamiento escalan con el tamaño para que la sombra no se
    coma el icono cuando el zoom es bajo ni quede ridícula cuando es alto. Va
    desplazada abajo a la derecha y con poco contraste: solo busca despegar el
    logo del fondo, no dibujar un contorno marcado.
    """
    offset = max(1.0, size * 0.04)
    return _drop_shadow(widget, blur=max(3.0, size * 0.14), offset=offset,
                        alpha=120)


def card_shadow(widget) -> None:
    """Sombra sutil de la tarjeta entera, para despegarla del fondo.

    Misma dirección y contraste que la del logo (abajo a la derecha y leve),
    pero con desplazamiento fijo y no proporcional al zoom: la tarjeta es mucho
    más grande que el icono, así que unos pocos píxeles ya se leen como relieve
    aunque la ampliación suba y no acaban manchando el borde.

    OJO: instalar un ``QGraphicsDropShadowEffect`` obliga a pintar la tarjeta a
    un pixmap y difuminarlo en cada repintado (incluido el hover), así que se
    paga. Medido al pintar 60 tarjetas de rejilla de golpe: ~36 ms sin sombra
    frente a ~63 ms con ella. En uso normal solo se repinta la tarjeta que está
    bajo el ratón, así que es medio milisegundo, pero conviene saberlo antes de
    añadir más efectos.
    """
    rest = motion.SHADOW_REST
    _drop_shadow(widget, blur=rest["blur"], offset=rest["offset"],
                 alpha=rest["color"].alpha())


def _favorite_star(marked: bool, on_toggle) -> StarButton:
    """Estrella de favorito de una tarjeta (misma diana que la "i": 24x24)."""
    star = StarButton(marked, tooltip_on=tr("Remove from favorites"),
                      tooltip_off=tr("Mark as favorite"))
    star.setFont(icon_font())
    star.toggled.connect(on_toggle)
    return star


def _info_button(item, signal) -> IconLinkButton:
    """La "i" de las notas de la versión (misma diana que la estrella: 24x24).

    Emite el objeto entero (``Build`` o ``InstalledBuild``) y no solo la
    versión: las notas de un fork no son las de Blender y la ventana necesita
    saber de quién son (y, en UPBGE, la URL exacta de su release).
    """
    info = IconLinkButton(icons.INFO, tr("Read the release notes for this version"))
    info.setFont(icon_font())
    info.clicked.connect(lambda: signal.emit(item))
    return info


def _console_button(checked: bool, entry, signal) -> CardButton:
    """Botón para lanzar con consola, **por versión**.

    Cada tarjeta recuerda lo suyo (lo elige ``MainWindow`` con la entrada), así
    que encenderlo aquí no cambia las demás. Encendido va en azul.
    """
    button = CardButton(
        icons.TERMINAL, variant="accent" if checked else "neutral",
        tooltip=tr("Launch this version with the console visible: Python "
                   "output and script errors."))
    button.setCheckable(True)
    button.setChecked(checked)
    _icon_only(button)
    button.setFont(icon_font())
    button.setFixedWidth(46)
    button.toggled.connect(lambda on: signal.emit(entry, on))
    button.toggled.connect(
        lambda on: button.set_variant("accent" if on else "neutral"))
    return button


def _with_opacity(pix: QPixmap, opacity: float) -> QPixmap:
    """Devuelve el pixmap con esa opacidad.

    En Qt el ``opacity`` del QSS **no hace nada** sobre un QLabel con pixmap
    (comprobado: el canal alfa sale igual), así que el atenuado de las
    versiones que no tienes (que en la versión Kivy sí se veía) hay que
    pintarlo aquí.
    """
    result = QPixmap(pix.size())
    # Mismo ratio que el original: si no, el resultado se pintaría al doble de
    # tamaño en una pantalla HiDPI.
    result.setDevicePixelRatio(pix.devicePixelRatio())
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setOpacity(opacity)
    painter.drawPixmap(0, 0, pix)
    painter.end()
    return result


def _natural_width(label: ElidedLabel) -> ElidedLabel:
    """Tope de ancho en lo que mide su texto entero, para usarla con estirado.

    Un ``ElidedLabel`` no pide ancho, así que en una fila con ``addStretch``
    se quedaba a 0 px. Con factor de estirado y este tope ocupa lo que mide su
    texto (y lo que tenga detrás va pegado); si no cabe, se recorta con "…".
    """
    label.ensurePolished()
    label.setMaximumWidth(
        label.fontMetrics().horizontalAdvance(label.text()) + 6)
    return label


def _dim_unless(label, installed: bool) -> None:
    """Atenúa el título de una versión que no tienes (regla ``[dim]`` del QSS).

    Solo el título, y a un gris opaco que sigue pasando 4,5:1 (``TEXT_DIM``):
    lo que dice "no la tienes" es sobre todo el logo apagado. La propiedad se
    pone antes de que el widget se pula por primera vez, así que no hace falta
    repolir.
    """
    label.setProperty("dim", "false" if installed else "true")


def _read_only_reason() -> str:
    """Por qué una instalada de una carpeta con candado no se borra ni renombra."""
    return tr("This version lives in a folder with the lock closed.\n"
              "You can launch it and use it to migrate add-ons, but the app\n"
              "will not delete or rename it.")


def _protect(title, delete, read_only: bool) -> None:
    """Apaga renombrar y borrar si la carpeta es de solo lectura.

    Lanzar y migrar siguen funcionando (AGENTS.md: "solo lectura significa que
    la app no escribe NADA ahí"). Los controles se apagan en vez de ocultarse:
    así la tarjeta mide y se ve igual que las demás, y el tooltip dice por qué.
    """
    if not read_only:
        return
    title.set_editable(False, _read_only_reason())
    delete.setEnabled(False)
    delete.setToolTip(_read_only_reason())


def _logo_label(size: int, dim: bool, fork: str = FORK_BLENDER) -> QLabel:
    label = QLabel()
    pix = _logo_pixmap(size, dim, fork)
    if not pix.isNull():
        label.setPixmap(pix)
    logo_shadow(label, size)
    return label


class _HoverCard:
    """Resalta la tarjeta al pasar el ratón.

    No basta con ``QFrame#Card:hover`` en el QSS: cuando el ratón está encima de
    un hijo (una etiqueta o un botón), el padre puede no recibir el estado
    hover. Aquí lo marcamos a mano con una propiedad dinámica que el QSS lee.

    Además la tarjeta "se levanta": su sombra crece y se vuelve un halo del
    color de acento (``motion.lift_shadow``). El fondo lo cambia el QSS de
    golpe, pero el halo entra y sale con suavidad.
    """

    def enterEvent(self, event):
        super().enterEvent(event)
        self._set_hover(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._set_hover(False)

    # Las tarjetas de rejilla se inclinan en 3D hacia el ratón (``tilt``);
    # las de lista no (son filas a todo lo ancho) ni la que va dentro de la
    # carta grande de la galería.
    tilt_enabled = False

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", "true" if on else "false")
        repolish(self)
        if on:
            # El "tic" al entrar en una tarjeta de rejilla. La tarjeta no sabe
            # de sonidos: la ventana le deja aquí un callback (``hover_tick``)
            # que comprueba si los sonidos están encendidos. Las de lista no
            # lo llevan y no suenan.
            tick = getattr(self, "hover_tick", None)
            if tick is not None:
                tick()
        if self.tilt_enabled:
            # La capa inclinada lleva su propio halo, que se inclina con ella.
            # El plano de la sombra (``lift_shadow``) sobraba: eran dos halos,
            # y el de debajo, apagándose con su propia animación, dejaba restos
            # en las tarjetas de al lado.
            tilt.tilt(self, on)
        else:
            motion.lift_shadow(self, on)

    def hideEvent(self, event):
        # Al cambiar de vista con el ratón encima no llega ``leaveEvent``: sin
        # esto, la inclinación se quedaba a medias y escuchando el ratón.
        super().hideEvent(event)
        if self.tilt_enabled:
            tilt.tilt(self, False)

    def set_text_shadow(self, on: bool) -> None:
        """Sombra negra bajo el texto de la tarjeta.

        La pide la rejilla cuando el efecto (que pinta un brillo encima) va a
        lavar las letras; las tarjetas de lista no tienen ``_shadow_labels`` y
        no hacen nada.
        """
        for label in getattr(self, "_shadow_labels", ()):
            label.set_text_shadow(on)


class BaseBuildCard(_HoverCard, QFrame):
    """Base común de las tarjetas de compilaciones."""

    action_clicked = Signal(object)   # build
    notes_clicked = Signal(object)    # build
    favorite_toggled = Signal(object, bool)   # build, marcada
    console_toggled = Signal(object, bool)    # build, con consola

    def __init__(self, build, installed: bool, zebra: bool, console=None,
                 parent=None):
        super().__init__(parent)
        self.build = build
        self.installed = installed
        # ``console`` solo llega para las versiones que ya están instaladas
        # (la tienda las lanza igual que Local): ``None`` es "sin botón".
        self.console = console
        self.setObjectName("Card")
        self.setProperty("zebra", "true" if zebra else "false")
        self.setProperty("installed", "true" if installed else "false")
        self.setAttribute(Qt.WA_Hover, True)
        card_shadow(self)

        self.fork = getattr(build, "fork", FORK_BLENDER) or FORK_BLENDER
        # Nombre visible del programa ("Blender", "Bforartists", "UPBGE"): el
        # título y el meta lo usan para que una Bforartists 5.2 no se confunda
        # con un Blender 5.2.
        self.program = build.fork_name
        self.title_text = f"{self.program} {build.version}"
        self.version = build.version
        if self.fork or build.experimental:
            # Los forks y las experimentales no tienen los canales de Blender:
            # la insignia enseña de dónde vienen (el fork, o la rama).
            self.channel_text = self.program if self.fork else build.branch
            self.is_lts = False
        else:
            if build.is_lts:
                channel = "LTS"
            elif build.risk in ("alpha", "daily", "beta"):
                channel = {"alpha": "Alpha", "daily": "Daily", "beta": "Beta"}[build.risk]
            else:
                channel = "Stable"
            self.channel_text = tr(channel)
            self.is_lts = build.is_lts

        details = [build.human_size]
        if not build.experimental and not self.fork:
            details.append(build.branch)
        details.append(build.arch)
        self.meta_text = "  ·  ".join(details)

    def _badge(self) -> ElidedLabel:
        """Insignia del canal ("LTS", "Alfa"... o la rama de una experimental).

        Una rama experimental puede tener un nombre larguísimo: con un
        ``QLabel`` pedía su ancho entero y deformaba la fila. Se usa con
        ``_natural_width`` (ver ``BuildCard``).
        """
        label = ElidedLabel(self.channel_text, Qt.ElideRight)
        label.setObjectName("Warning" if self.is_lts else "Info")
        return _natural_width(label)

    def _info(self) -> IconLinkButton:
        return _info_button(self.build, self.notes_clicked)

    def _star(self, marked: bool) -> StarButton:
        return _favorite_star(
            marked, lambda on: self.favorite_toggled.emit(self.build, on))

    def _console(self):
        """Botón de consola de una versión instalada, o ``None`` si no toca.

        La clave de la consola es la serie (``favorite_key``), igual que en
        Local: encenderla aquí se ve allí, y al revés.
        """
        if self.console is None:
            return None
        return _console_button(self.console, self.build, self.console_toggled)


class BuildCard(BaseBuildCard):
    """Tarjeta en modo lista (una fila por versión)."""

    def __init__(self, build, installed: bool, zebra: bool,
                 marked: bool = False, console=None, parent=None):
        super().__init__(build, installed, zebra, console=console,
                         parent=parent)
        self.setFixedHeight(68)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 9, 12, 9)
        lay.setSpacing(12)
        lay.addWidget(_logo_label(44, dim=not installed, fork=self.fork))

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(10)
        title = ElidedLabel(self.title_text, Qt.ElideMiddle)
        title.setObjectName("Title")
        _dim_unless(title, installed)
        # ``ElidedLabel`` no pide ancho: en esta fila con ``addStretch`` se
        # quedaba a 0 px y en modo lista solo se veía la insignia ("LTS",
        # "Alfa"), sin la versión. Con factor de estirado **y** tope en su
        # ancho natural ocupa lo que mide su texto y la insignia va pegada.
        # La insignia, igual: si no caben las dos, se recortan las dos.
        top.addWidget(_natural_width(title), 1)
        top.addWidget(self._badge(), 1)
        top.addStretch()
        text_col.addLayout(top)
        meta = ElidedLabel(self.meta_text, Qt.ElideRight)
        meta.setObjectName("Muted")
        text_col.addWidget(meta)
        lay.addLayout(text_col, 1)

        lay.addWidget(self._star(marked))
        lay.addWidget(self._info())
        console = self._console()
        if console is not None:
            lay.addWidget(console)

        action = CardButton(
            tr("Launch") if installed else tr("Download"),
            variant="dark" if installed else "accent",
            tooltip=tr("Launch this installed version") if installed
            else tr("Download and install this version"),
        )
        # El icono depende de la accion: flecha verde para lanzar (si ya la
        # tienes) y flecha blanca hacia abajo para descargar. En Kivy se pintaba
        # dentro del texto; en Qt hace falta un QIcon.
        action.setIcon(_launch_icon() if installed else _download_icon())
        action.clicked.connect(lambda: self.action_clicked.emit(self.build))
        lay.addWidget(action)


def _grid_height(zoom: float) -> int:
    """Alto de una tarjeta de rejilla para un ``zoom`` dado.

    No es ``196 * zoom``: las etiquetas (título, meta, "Instalada"...) NO
    escalan con el zoom, así que con poca ampliación se recortaban. Medido:
    el contenido mide ~104·zoom + 90 px, y dejamos ~4 px de holgura para que
    nunca se corte (para verlo más grande está el zoom). Lo vigila
    ``test_el_zoom_minimo_no_recorta_ni_solapa_el_contenido``, que a varios
    niveles comprueba que el layout cabe en este alto.

    Lo usan las dos rejillas (tienda e instaladas): son la misma estructura
    (logo, título, meta, fila de etiqueta y fila de botones) y tienen que medir
    igual al cambiar de pestaña. La fila de la etiqueta se reserva siempre
    aunque esté vacía, que es lo que fija este alto.
    """
    return int(104 * zoom + 94)


class GridBuildCard(BaseBuildCard):
    """Tarjeta en modo rejilla (icono grande y botón debajo)."""

    # Cuando el efecto está apagado no hay inclinación que destaque la tarjeta:
    # su halo al pasar el ratón sube un poco más de intensidad (``lift_shadow``).
    glow_boost = 1.3

    def __init__(self, build, installed: bool, zebra: bool, zoom: float = 1.0,
                 marked: bool = False, console=None, parent=None):
        super().__init__(build, installed, zebra, console=console,
                         parent=parent)
        # La fila de la insignia ("Instalada") se reserva SIEMPRE, aunque la
        # compilación no esté instalada: en el Kivy original la etiqueta existía
        # con el texto vacío y así todas las tarjetas de la tienda medían lo
        # mismo. Al ahorrársela a las descargables, estas salían 22 px más bajas
        # que las instaladas y la rejilla quedaba desigual.
        self.setFixedHeight(_grid_height(zoom))
        self.tilt_enabled = True
        lay = QVBoxLayout(self)
        m = int(12 * zoom)
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(int(5 * zoom))

        logo = _logo_label(int(60 * zoom), dim=not installed, fork=self.fork)
        logo.setAlignment(Qt.AlignHCenter)
        lay.addWidget(logo)

        title = ElidedLabel(self.title_text, Qt.ElideMiddle)
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignHCenter)
        _dim_unless(title, installed)
        lay.addWidget(title)

        sub = ElidedLabel(f"{self.channel_text}  ·  {self.meta_text}",
                          Qt.ElideRight)
        sub.setObjectName("Warning" if self.is_lts else "Info")
        sub.setAlignment(Qt.AlignHCenter)
        lay.addWidget(sub)
        # Las etiquetas que llevan sombra cuando el efecto está encendido.
        self._shadow_labels = [title, sub]

        tag = QLabel(tr("Local build") if installed else "")
        tag.setObjectName("Success")
        tag.setAlignment(Qt.AlignHCenter)
        lay.addWidget(tag)

        lay.addStretch()

        row = QHBoxLayout()
        row.setSpacing(int(5 * zoom))
        row.addStretch()
        row.addWidget(self._star(marked))
        row.addWidget(self._info())
        console = self._console()
        if console is not None:
            console.setFixedWidth(max(int(42 * zoom), MIN_ICON_BUTTON_WIDTH))
            row.addWidget(console)
        action = CardButton(
            tr("Launch") if installed else tr("Download"),
            variant="dark" if installed else "accent",
            tooltip=tr("Launch this installed version") if installed
            else tr("Download and install this version"),
        )
        action.setIcon(_launch_icon() if installed else _download_icon())
        action.clicked.connect(lambda: self.action_clicked.emit(self.build))
        # El botón principal (Descargar o Lanzar): la galería lo pulsa con Intro.
        self.primary_button = action
        row.addWidget(action)
        row.addStretch()
        lay.addLayout(row)


class InstalledCard(_HoverCard, QFrame):
    """Versión ya instalada (lanzar / desinstalar) en modo lista."""

    launch_clicked = Signal(object)   # entry
    delete_clicked = Signal(object)   # entry
    notes_clicked = Signal(object)    # entry
    favorite_toggled = Signal(object, bool)   # entry, marcada
    update_clicked = Signal(object, object)   # entry, build nueva
    rename_requested = Signal(object, str)    # entry, nombre nuevo
    console_toggled = Signal(object, bool)   # entry, lanzar con consola

    def __init__(self, entry, zebra: bool, marked: bool = False, parent=None,
                 update=None, read_only: bool = False,
                 console: bool | None = None):
        super().__init__(parent)
        self.entry = entry
        self.fork = getattr(entry, "fork", FORK_BLENDER) or FORK_BLENDER
        self.program = entry.fork_name
        self.setObjectName("Card")
        self.setProperty("zebra", "true" if zebra else "false")
        self.setProperty("installed", "true")
        self.setAttribute(Qt.WA_Hover, True)
        card_shadow(self)
        # Mismas medidas que ``BuildCard`` (la fila de la tienda): al cambiar de
        # pestaña las tarjetas no pueden medir distinto.
        self.setFixedHeight(68)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 9, 12, 9)
        lay.setSpacing(12)
        lay.addWidget(_logo_label(44, dim=False, fork=self.fork))

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        title = EditableLabel(entry.name, Qt.ElideMiddle)
        title.setObjectName("Title")
        title.renamed.connect(
            lambda name: self.rename_requested.emit(self.entry, name))
        text_col.addWidget(title)
        # El "solo lectura" se cuela en la línea que YA existe, no en una fila
        # nueva: la tarjeta tiene que seguir midiendo 68 px como las de la
        # tienda (hay un test que lo vigila).
        detail = f"{self.program} {entry.version}   ·   {entry.path}"
        if read_only:
            detail += "   ·   " + tr("Read-only")
        meta = ElidedLabel(detail, Qt.ElideRight)
        meta.setObjectName("Muted")
        if read_only:
            meta.setToolTip(_read_only_reason())
        text_col.addWidget(meta)
        lay.addLayout(text_col, 1)

        # Estrella e "i" van juntas y en ese orden en las cuatro tarjetas
        # (tienda e instaladas, lista y rejilla): así no bailan al cambiar de
        # pestaña. Detrás, el grupo de acciones.
        lay.addWidget(_favorite_star(
            marked, lambda on: self.favorite_toggled.emit(entry, on)))
        lay.addWidget(_info_button(entry, self.notes_clicked))

        if update is not None:
            update_btn = CardButton(
                tr("Update to {version}", version=update.version),
                variant="accent",
                tooltip=tr("Download and install this version"),
            )
            update_btn.clicked.connect(
                lambda: self.update_clicked.emit(entry, update))
            lay.addWidget(update_btn)

        if console is not None:
            lay.addWidget(_console_button(console, entry, self.console_toggled))

        launch = CardButton(tr("Launch"), variant="dark",
                            tooltip=tr("Launch this installed version"))
        launch.setIcon(_launch_icon())
        launch.clicked.connect(lambda: self.launch_clicked.emit(entry))
        lay.addWidget(launch)

        delete = CardButton(icons.DELETE, variant="danger",
                            tooltip=tr("Remove this installed version"))
        _icon_only(delete)
        delete.setFont(icon_font())
        delete.setFixedWidth(46)
        delete.clicked.connect(lambda: self.delete_clicked.emit(entry))
        lay.addWidget(delete)
        _protect(title, delete, read_only)


class GridInstalledCard(_HoverCard, QFrame):
    """Versión instalada en cuadrícula (icono grande y botones debajo)."""

    # Como en la tienda: sin efecto, el halo del ratón sube un poco más.
    glow_boost = 1.3

    launch_clicked = Signal(object)
    delete_clicked = Signal(object)
    notes_clicked = Signal(object)
    favorite_toggled = Signal(object, bool)   # entry, marcada
    update_clicked = Signal(object, object)   # entry, build nueva
    rename_requested = Signal(object, str)    # entry, nombre nuevo
    console_toggled = Signal(object, bool)   # entry, lanzar con consola

    def __init__(self, entry, zebra: bool, zoom: float = 1.0,
                 marked: bool = False, parent=None, update=None,
                 console: bool | None = None, read_only: bool = False):
        super().__init__(parent)
        self.entry = entry
        self.fork = getattr(entry, "fork", FORK_BLENDER) or FORK_BLENDER
        self.program = entry.fork_name
        self.setObjectName("Card")
        self.setProperty("zebra", "true" if zebra else "false")
        self.setProperty("installed", "true")
        self.setAttribute(Qt.WA_Hover, True)
        card_shadow(self)
        # Misma estructura y medidas que ``GridBuildCard``: al cambiar de
        # pestaña las tarjetas no pueden bailar de tamaño ni el logo cambiar de
        # tamaño. La fila de la etiqueta se reserva siempre (aquí lleva el
        # aviso de actualización, o va vacía).
        self.setFixedHeight(_grid_height(zoom))
        self.tilt_enabled = True

        lay = QVBoxLayout(self)
        m = int(12 * zoom)
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(int(5 * zoom))

        logo = _logo_label(int(60 * zoom), dim=False, fork=self.fork)
        logo.setAlignment(Qt.AlignHCenter)
        lay.addWidget(logo)

        title = EditableLabel(entry.name, Qt.ElideMiddle)
        title.setObjectName("Title")
        title.setAlignment(Qt.AlignHCenter)
        title.renamed.connect(
            lambda name: self.rename_requested.emit(self.entry, name))
        lay.addWidget(title)

        detail = f"{self.program} {entry.version}"
        if read_only:
            # Como en la lista: en la línea que ya existe, no en una nueva
            # (las tarjetas de la rejilla tienen que medir todas lo mismo).
            detail += "  ·  " + tr("Read-only")
        meta = ElidedLabel(detail, Qt.ElideRight)
        meta.setObjectName("Muted")
        meta.setAlignment(Qt.AlignHCenter)
        if read_only:
            meta.setToolTip(_read_only_reason())
        lay.addWidget(meta)

        # El aviso va como texto elidido (no pide ancho y no ensancha la
        # columna); la acción de descarga es el botón de la fila de abajo.
        hint_text = (tr("Update to {version}", version=update.version)
                     if update is not None else "")
        hint = ElidedLabel(hint_text, Qt.ElideRight)
        hint.setObjectName("Info")
        hint.setAlignment(Qt.AlignHCenter)
        lay.addWidget(hint)
        # Las etiquetas que llevan sombra cuando el efecto está encendido.
        self._shadow_labels = [title, meta, hint]

        lay.addStretch()

        row = QHBoxLayout()
        row.setSpacing(int(5 * zoom))
        row.addWidget(_favorite_star(
            marked, lambda on: self.favorite_toggled.emit(entry, on)))
        row.addWidget(_info_button(entry, self.notes_clicked))
        if update is not None:
            # En rejilla el aviso va sin texto: un botón ancho pediría más
            # ancho mínimo y ensancharía su columna (justo lo que arreglamos
            # con ElidedLabel). El texto va en el tooltip.
            update_btn = CardButton(
                "", variant="accent",
                tooltip=tr("Update to {version}", version=update.version))
            update_btn.setIcon(_download_icon())
            _icon_only(update_btn)
            update_btn.setFixedWidth(max(int(42 * zoom),
                                         MIN_ICON_BUTTON_WIDTH))
            update_btn.clicked.connect(
                lambda: self.update_clicked.emit(entry, update))
            row.addWidget(update_btn)
        if console is not None:
            console_btn = _console_button(console, entry, self.console_toggled)
            console_btn.setFixedWidth(max(int(42 * zoom),
                                          MIN_ICON_BUTTON_WIDTH))
            row.addWidget(console_btn)
        launch = CardButton(tr("Launch"), variant="dark",
                            tooltip=tr("Launch this installed version"))
        launch.setIcon(_launch_icon())
        launch.clicked.connect(lambda: self.launch_clicked.emit(entry))
        # Como en la tienda: el que pulsa Intro en la galería.
        self.primary_button = launch
        row.addWidget(launch, 1)
        delete = CardButton(icons.DELETE, variant="danger",
                            tooltip=tr("Remove this installed version"))
        _icon_only(delete)
        # El ancho acompaña al zoom, pero nunca por debajo de lo que necesita el
        # glifo: a 0.6 salía un recuadro rojo vacío (el icono no cabía). El
        # tamaño del icono no se puede escalar con setFont: el `font-size` del
        # QSS global (13 px) pisa lo que ponga el widget, y da igual, porque el
        # resto del texto de la tarjeta tampoco escala.
        delete.setFont(icon_font())
        delete.setFixedWidth(max(int(42 * zoom), MIN_ICON_BUTTON_WIDTH))
        delete.clicked.connect(lambda: self.delete_clicked.emit(entry))
        row.addWidget(delete)
        lay.addLayout(row)
        _protect(title, delete, read_only)


class GripCard(QFrame):
    """Tarjeta de ajustes que coloca su asa en la esquina del panel.

    El asa iba como un widget más de la fila de botones, así que quedaba a los
    márgenes de la tarjeta (16 px a la derecha, 14 abajo): se leía como un
    botón pequeño al lado de "Aplicar" y no como la esquina del panel gris, que
    es donde todo el mundo va a buscar el redimensionado. Un layout no puede
    sacar un hijo de sus márgenes, así que el asa se pone **flotando** sobre la
    tarjeta y se recoloca en cada ``resizeEvent``. A cambio, ``set_grip``
    reserva abajo la altura del asa para que ningún botón quede debajo.

    La tarjeta no fija su propio alto: lo hereda de la lista de dentro, que sí
    lo tiene fijo (``layouts.FittedList``). Así estirar la esquina mueve el
    panel gris entero sin que haya dos sitios decidiendo el mismo alto.
    """

    # Separación del asa respecto a los bordes de la tarjeta. Es 0 porque el
    # aire para no pisar el borde redondeado ya va dentro del asa
    # (``CornerGrip.INSET``): así su diana de 24 px llega hasta la esquina.
    GRIP_MARGIN = 0
    # Hueco mínimo entre el contenido y el asa, para que ningún botón quede
    # pegado a las rayitas.
    GRIP_GAP = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        # Sin esto, una subclase de QFrame puede no pintar el fondo del QSS.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._grip = None

    def set_grip(self, grip) -> None:
        """Adopta el asa y la saca de los márgenes del layout."""
        self._grip = grip
        grip.setParent(self)
        grip.raise_()
        lay = self.layout()
        if lay is not None:
            margins = lay.contentsMargins()
            # El contenido termina por encima del asa: si no, el botón de
            # acento pasaría justo por debajo de las rayitas.
            lay.setContentsMargins(
                margins.left(), margins.top(), margins.right(),
                max(margins.bottom(),
                    grip.height() + self.GRIP_MARGIN + self.GRIP_GAP))
        self._place_grip()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_grip()

    def _place_grip(self) -> None:
        if self._grip is None:
            return
        self._grip.move(
            self.width() - self._grip.width() - self.GRIP_MARGIN,
            self.height() - self._grip.height() - self.GRIP_MARGIN)


def settings_card(title: str = "", spacing: int = 8,
                  margins=(16, 14, 16, 14)) -> tuple:
    """Tarjeta con el aspecto de Ajustes y su layout vertical: ``(card, layout)``.

    Es el ``QFrame#SettingsCard`` que usan Ajustes, Migración y el gestor de
    add-ons (título apagado, bordes redondeados, sombra, mismos márgenes).
    Antes cada vista tenía la suya y ya iban distintas (una sin sombra, otra
    con otro espaciado): un solo sitio evita que se separen con el tiempo.
    Devuelve un ``GripCard`` para que quien quiera pueda colgarle un asa.
    """
    card = GripCard()
    card.setObjectName("SettingsCard")
    # Sombra abajo a la derecha (la misma que las tarjetas de la tienda): las
    # tarjetas van sobre el fondo oscuro y así se despegan de él.
    card_shadow(card)
    lay = QVBoxLayout(card)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    if title:
        label = QLabel(tr(title))
        label.setObjectName("Muted")
        lay.addWidget(label)
    return card, lay
