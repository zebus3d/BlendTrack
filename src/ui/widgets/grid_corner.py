"""Los mandos de la esquina de la rejilla: los efectos y sus ajustes.

Son el hermano de los botones de la esquina de la galería (estilo, láser,
silencio), pero para la vista de rejilla. En la esquina de abajo a la derecha,
encima del listado, hay:

- **FX**: enciende o apaga la inclinación 3D de las tarjetas
  (``services.settings.card_tilt``). Apagado, se queda solo él y pegado a la
  esquina.
- **El engranaje**: despliega un panel pequeño con los ajustes del efecto
  (fuerza de la rotación, intensidad y color de la luz, e intensidad del halo).
  Va plegado por defecto para no ensuciar la vista; el estado se recuerda.
- **El panel**: aparece justo encima de la esquina, alineado a la derecha, y se
  cierra al pulsar fuera. Solo existe mientras el efecto está encendido.
  Los deslizadores vuelven a su valor de fábrica con **Ctrl+clic**, **Supr** o
  **Retroceso** (el que esté bajo el ratón).

Vive en su propio módulo y no en ``build_lists`` por dos motivos: se coloca solo
(escucha el tamaño de su padre con un filtro de eventos) y así la lista, que ya
roza el tope de líneas, no crece de más.

**El listado le reserva abajo una franja** (``CORNER_RESERVED``, que
``build_lists`` usa como margen del grid): sin ella el grupo flotaría sobre los
botones de la última fila de tarjetas y los taparía.
"""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QButtonGroup,
                               QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QSlider, QWidget)

from i18n import tr
from services.settings import (TILT_GLOW_DEFAULT, TILT_GLOW_MAX,
                               TILT_GLOW_MIN, TILT_LIGHT_COLOR_DEFAULT,
                               TILT_LIGHT_DEFAULT, TILT_LIGHT_MAX,
                               TILT_LIGHT_MIN, TILT_SAT_DEFAULT, TILT_SAT_MAX,
                               TILT_SAT_MIN, TILT_STRENGTH_DEFAULT,
                               TILT_STRENGTH_MAX, TILT_STRENGTH_MIN)
from ui import icons
from ui import theme as t
from ui.fonts import icon_font
from ui.widgets.layouts import repolish
from ui.widgets.shell import CORNER_BUTTON_MARGIN, CORNER_BUTTON_SIZE

# La franja que hay que dejar libre abajo para que el grupo no tape la última
# fila de tarjetas: su alto más su margen.
CORNER_RESERVED = CORNER_BUTTON_SIZE + CORNER_BUTTON_MARGIN
# Cuánto se aparta del borde derecho. La barra de desplazamiento ocupa los
# últimos ~12 px, así que con 4 el grupo queda casi pegado a la esquina.
SCROLLBAR_INSET = 4
# Separación entre los botones de la esquina y entre el panel y ellos.
CONTROL_GAP = 6
PANEL_GAP = 8
# Aire alrededor del grupo para que el halo de FX no se recorte contra los
# bordes de su contenedor: el resplandor se sale del botón (26 px) y sin este
# margen quedaba cortado por arriba y por abajo.
GLOW_MARGIN = 16

# El halo de FX: difuminado y su intensidad en cada estado. El apagado es más
# intenso para que el botón siga viéndose cuando no hay animación que lo
# destaque.
GLOW_BLUR = 16
GLOW_ON = 150
GLOW_OFF = 215

# Ancho del deslizador y de su valor. La etiqueta no lleva ancho fijo: la
# columna de la rejilla se ajusta sola a la etiqueta más larga de cada idioma.
PANEL_SLIDER_WIDTH = 118
PANEL_VALUE_WIDTH = 36
SWATCH_SIZE = 18

# Los tonos que se pueden elegir para la luz (nombre de i18n, hex). Van
# saturados a propósito: sobre el gris de la tarjeta y con poca intensidad, un
# tono pastel se pierde y la luz parece blanca.
LIGHT_TONES = (
    ("Warm", "#ffe1a8"),
    ("White", "#ffffff"),
    ("Cool", "#b3d4ff"),
    ("Amber", "#ffbf5e"),
    ("Violet", "#c2a8ff"),
)


def saturate(colour: QColor, percent: int) -> QColor:
    """Sube (o baja) la saturación de un color según un porcentaje.

    A 100 % es el color tal cual. El blanco y los grises no tienen tono (la
    saturación no los toca): un ``#ffffff`` se queda blanco aunque el ajuste
    suba, porque no hay color que intensificar.
    """
    hue, sat, val, alpha = colour.getHsv()
    if hue < 0 or percent == 100:
        return QColor(colour)
    sat = max(0, min(255, round(sat * percent / 100.0)))
    return QColor.fromHsv(hue, sat, val, alpha)


def apply_tilt_card(card, settings) -> None:
    """Reparte a una tarjeta los ajustes del efecto (rotación, luz y halo).

    La fuerza, la luz y el halo van como fracciones (1.0 = fábrica) porque es lo
    que multiplican ``tilt.MAX_TILT``, ``GLARE`` y ``HALO_ALPHA``. La tarjeta los
    lee al inclinarse (``ui/widgets/tilt.py``), así que cambiarlos no rehace la
    rejilla. El color de la luz se guarda ya saturado.
    """
    card.tilt_strength = settings.tilt_strength / 100.0
    card.tilt_light = settings.tilt_light / 100.0
    card.tilt_glow = settings.tilt_glow / 100.0
    card.tilt_light_color = saturate(QColor(settings.tilt_light_color),
                                     settings.tilt_light_sat)


def apply_tilt_options(grids, settings) -> None:
    """Lo mismo para todas las tarjetas ya construidas de las dos rejillas."""
    for grid in grids:
        for index in range(grid.count()):
            card = grid.itemAt(index).widget()
            if card is not None:
                apply_tilt_card(card, settings)


def prepare_grid_card(card, settings, glow_colour, tick) -> None:
    """Monta lo común de una tarjeta de rejilla (efectos, "tic" y halo).

    La inclinación con sus ajustes, la sombra del texto (solo si el efecto va a
    pintar su brillo encima), el "tic" al entrar (``tick``) y el color del halo.
    """
    card.tilt_enabled = settings.card_tilt
    card.set_text_shadow(card.tilt_enabled)
    apply_tilt_card(card, settings)
    card.hover_tick = tick
    card.glow_color = glow_colour


class GridCornerButton(QPushButton):
    """El botón FX: enciende o apaga la inclinación de las tarjetas."""

    def __init__(self, parent, on: bool):
        super().__init__(parent)
        self.setObjectName("CornerButton")
        self.setCheckable(True)
        self.setFixedSize(CORNER_BUTTON_SIZE, CORNER_BUTTON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        # Las letras "FX" y su estado: el QSS pinta el color según ``active`` y
        # ``fx`` (ver ``qss.py``).
        self.setProperty("fx", "true")
        self._glow = QGraphicsDropShadowEffect(self)
        self._glow.setOffset(0, 0)
        self._glow.setBlurRadius(GLOW_BLUR)
        self.setGraphicsEffect(self._glow)
        self.set_on(on)

    def set_on(self, on: bool) -> None:
        """Pone el botón en su estado (encendido/apagado) sin emitir señales."""
        on = bool(on)
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self.setText("FX")
        self.setProperty("active", "true" if on else "false")
        repolish(self)
        self.setToolTip(tr("Turn off the card effects") if on
                        else tr("Turn on the card effects"))
        self._paint_glow(on)

    def _paint_glow(self, on: bool) -> None:
        """El halo del botón: acento encendido, claro e intenso apagado."""
        colour = QColor(t.ACCENT if on else t.TEXT)
        colour.setAlpha(GLOW_ON if on else GLOW_OFF)
        self._glow.setColor(colour)


class GridCornerGear(QPushButton):
    """El botón del engranaje: despliega el panel de ajustes del efecto."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("CornerButton")
        self.setCheckable(True)
        self.setFont(icon_font())
        self.setFixedSize(CORNER_BUTTON_SIZE, CORNER_BUTTON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        # Los glifos de la rejilla van un punto más grandes que los de la
        # galería (ver ``qss.py``): así miden como el "FX" de al lado.
        self.setProperty("cornerIcon", "true")
        self.setText(icons.SETTINGS)
        self.set_tooltip(False)

    def set_tooltip(self, open_: bool) -> None:
        self.setToolTip(tr("Hide the effect settings") if open_
                        else tr("Effect settings"))


class GridCornerMute(QPushButton):
    """El botón de silencio: enciende o apaga los sonidos de la interfaz."""

    def __init__(self, parent, muted: bool):
        super().__init__(parent)
        self.setObjectName("CornerButton")
        self.setCheckable(True)
        self.setFont(icon_font())
        self.setFixedSize(CORNER_BUTTON_SIZE, CORNER_BUTTON_SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setProperty("cornerIcon", "true")
        self.set_muted(muted)

    def set_muted(self, muted: bool) -> None:
        """Pone el botón en su estado sin avisar a nadie."""
        self.blockSignals(True)
        self.setChecked(bool(muted))
        self.blockSignals(False)
        self.setText(icons.SOUND_OFF if muted else icons.SOUND_ON)
        self.setToolTip(tr("Turn the interface sounds back on") if muted
                        else tr("Mute the interface sounds"))


class DefaultSlider(QSlider):
    """Deslizador que vuelve a su valor de fábrica con Ctrl+clic o Supr/Retroceso.

    El Ctrl+clic vale en todo el deslizador, no solo en la bolita: la diana es
    el widget entero (``minimumHeight``), que es lo que cuesta atinar.
    """

    def __init__(self, parent, default: int):
        super().__init__(Qt.Horizontal, parent)
        self._default = int(default)
        # Diana de 24 px (WCAG 2.5.8): sin esto el widget mide lo que el estilo
        # (unos 15 px) y hay que apuntar a la ranura con precisión.
        self.setMinimumHeight(24)
        self.setFocusPolicy(Qt.StrongFocus)

    def reset(self) -> None:
        self.setValue(self._default)

    def mousePressEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            self.reset()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Backspace, Qt.Key_Delete):
            self.reset()
            event.accept()
            return
        super().keyPressEvent(event)


class TiltSettingsPanel(QWidget):
    """El panel de ajustes del efecto: rotación, luz, color y halo."""

    # (nombre del ajuste, valor): lo guarda y aplica la ventana.
    changed = Signal(str, int)
    # Color de la luz (hex ``#rrggbb``).
    color_changed = Signal(str)

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setObjectName("TiltPanel")
        # Es una subclase de QWidget: sin esto no pinta el fondo del QSS.
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Sombra suave, como los popups de Blender, para que flote sobre las
        # tarjetas en vez de parecer pegado a ellas.
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 190))
        self.setGraphicsEffect(shadow)
        self._hovered = None
        # Una rejilla, no filas apiladas: así la etiqueta se alinea en una
        # columna que se ajusta sola a la más larga (sin ancho fijo que recorte
        # "Saturación"), y no hay ``QWidget`` de fila que herede el fondo global.
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 8, 10, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        self.rotation = self._row(
            grid, 0, "Rotation", "tilt_strength", settings.tilt_strength,
            TILT_STRENGTH_MIN, TILT_STRENGTH_MAX, TILT_STRENGTH_DEFAULT,
            "How far the card leans towards the mouse.")
        self.light = self._row(
            grid, 1, "Light", "tilt_light", settings.tilt_light,
            TILT_LIGHT_MIN, TILT_LIGHT_MAX, TILT_LIGHT_DEFAULT,
            "Brightness of the reflection that follows the mouse.")
        self.glow = self._row(
            grid, 2, "Glow", "tilt_glow", settings.tilt_glow,
            TILT_GLOW_MIN, TILT_GLOW_MAX, TILT_GLOW_DEFAULT,
            "Brightness of the coloured halo around the card.")
        self._tone_row(grid, 3, settings.tilt_light_color)
        self.saturation = self._row(
            grid, 4, "Saturation", "tilt_light_sat", settings.tilt_light_sat,
            TILT_SAT_MIN, TILT_SAT_MAX, TILT_SAT_DEFAULT,
            "Vividness of the light colour.")
        self.adjustSize()

    def _row(self, grid, row, label, name, value, low, high, default, tooltip):
        """Una fila etiqueta + deslizador + valor. Devuelve el deslizador."""
        caption = QLabel(tr(label))
        caption.setToolTip(tr(tooltip))
        slider = DefaultSlider(self, default)
        slider.setRange(low, high)
        slider.setValue(int(value))
        slider.setFixedWidth(PANEL_SLIDER_WIDTH)
        slider.setToolTip(tr(tooltip))
        amount = QLabel(f"{int(value)}%")
        amount.setObjectName("Muted")
        amount.setFixedWidth(PANEL_VALUE_WIDTH)
        amount.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider.valueChanged.connect(lambda v, l=amount: l.setText(f"{v}%"))
        slider.valueChanged.connect(lambda v, n=name: self.changed.emit(n, v))
        slider.installEventFilter(self)
        grid.addWidget(caption, row, 0)
        grid.addWidget(slider, row, 1)
        grid.addWidget(amount, row, 2)
        return slider

    def _tone_row(self, grid, row, current: str) -> None:
        """Fila del color de la luz: un círculo por tono, excluyentes."""
        caption = QLabel(tr("Tone"))
        caption.setToolTip(tr("Colour of the light that follows the mouse."))
        box = QHBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)
        group = QButtonGroup(self)
        group.setExclusive(True)
        current = (current or "").lower()
        self._default_tone = None
        for name, colour in LIGHT_TONES:
            dot = QPushButton(self)
            dot.setCheckable(True)
            dot.setFixedSize(SWATCH_SIZE, SWATCH_SIZE)
            dot.setCursor(Qt.PointingHandCursor)
            dot.setFocusPolicy(Qt.NoFocus)
            dot.setToolTip(tr(name))
            dot.setStyleSheet(
                f"QPushButton {{ background-color: {colour};"
                f" border-radius: {SWATCH_SIZE // 2}px;"
                f" border: 2px solid transparent; padding: 0; }}"
                f"QPushButton:checked {{ border-color: {t.TEXT}; }}")
            dot.setChecked(colour == current)
            dot.toggled.connect(
                lambda on, c=colour: on and self.color_changed.emit(c))
            dot.installEventFilter(self)
            group.addButton(dot)
            box.addWidget(dot)
            if colour == TILT_LIGHT_COLOR_DEFAULT:
                self._default_tone = dot
        box.addStretch()
        grid.addWidget(caption, row, 0)
        grid.addLayout(box, row, 1, 1, 2)

    def _select_default_tone(self) -> None:
        if self._default_tone is not None:
            self._default_tone.setChecked(True)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Enter:
            # Lo que está bajo el ratón: lo que resetean Supr/Retroceso.
            self._hovered = watched
        elif event.type() == QEvent.Leave and self._hovered is watched:
            self._hovered = None
        elif (event.type() == QEvent.MouseButtonPress
                and event.modifiers() & Qt.ControlModifier
                and isinstance(watched, QPushButton)):
            # Ctrl+clic en una muestra de color: vuelve al tono de fábrica.
            self._select_default_tone()
            return True
        return super().eventFilter(watched, event)

    def reset_hovered(self) -> bool:
        """Devuelve a fábrica el control bajo el ratón (deslizador o tono)."""
        if self._hovered is None:
            return False
        if isinstance(self._hovered, QSlider):
            self._hovered.reset()
        else:
            self._select_default_tone()
        return True


class GridCorner(QWidget):
    """La esquina: FX, el engranaje y su panel de ajustes."""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.setObjectName("CornerControls")
        # True en la rejilla (FX + engranaje + silencio); False en la lista,
        # donde solo tiene sentido el silencio (no hay efecto que encender).
        self._grid = True
        lay = QHBoxLayout(self)
        # El margen deja sitio al halo de FX, que se sale del botón.
        lay.setContentsMargins(GLOW_MARGIN, GLOW_MARGIN, GLOW_MARGIN, GLOW_MARGIN)
        lay.setSpacing(CONTROL_GAP)
        self.button = GridCornerButton(self, settings.card_tilt)
        self.gear = GridCornerGear(self)
        # El silencio va a la izquierda del grupo: es de otra cosa (los sonidos,
        # no el efecto) y así FX y su engranaje siguen pegados a la esquina.
        self.mute = GridCornerMute(self, not settings.ui_sounds)
        lay.addWidget(self.mute)
        lay.addWidget(self.button)
        lay.addWidget(self.gear)
        # El panel es hermano del grupo (hijo de la misma página), no suyo: así
        # flota por encima sin que lo recorte el layout de la fila.
        self.panel = TiltSettingsPanel(parent, settings)
        self.panel.hide()
        self.button.toggled.connect(self._on_fx)
        self.gear.toggled.connect(self._on_gear)
        self.mute.toggled.connect(self._on_mute)
        # Estado inicial sin avisar a nadie (la ventana lo guardará cuando el
        # usuario toque de verdad). El engranaje recuerda si el panel estaba
        # abierto aunque el efecto esté apagado: al encenderlo, se reabre.
        open_ = bool(settings.tilt_panel_open)
        self.gear.blockSignals(True)
        self.gear.setChecked(open_)
        self.gear.blockSignals(False)
        self.gear.set_tooltip(open_)
        self.gear.setVisible(settings.card_tilt)
        parent.installEventFilter(self)
        self._resize_and_place()

    # ------------------------------------------------------------- eventos
    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Resize:
            self.place()
        elif (event.type() == QEvent.MouseButtonPress
                and self.panel.isVisible()
                and not self._hits_controls(event.globalPosition().toPoint())):
            # Un clic fuera cierra el panel (como cualquier desplegable).
            self.gear.setChecked(False)
        elif (event.type() == QEvent.KeyPress and self.panel.isVisible()
                and event.key() in (Qt.Key_Backspace, Qt.Key_Delete)
                and not isinstance(QApplication.focusWidget(),
                                   (QLineEdit, QAbstractSpinBox))
                and self.panel.reset_hovered()):
            # Retroceso/Supr devuelven a fábrica el deslizador bajo el ratón,
            # pero no si el foco está en un campo de texto (el buscador).
            return True
        return super().eventFilter(watched, event)

    def _hits_controls(self, global_pos) -> bool:
        for widget in (self.panel, self.button, self.gear):
            if widget.isVisible() and widget.rect().contains(
                    widget.mapFromGlobal(global_pos)):
                return True
        return False

    # -------------------------------------------------------------- estado
    def set_on(self, on: bool) -> None:
        """Enciende o apaga el efecto sin avisar (lo pidió la ventana)."""
        self.button.set_on(on)
        self._sync_controls()

    def set_shown(self, show: bool, grid: bool = True) -> None:
        """Enseña u oculta el grupo y lo coloca.

        En la rejilla va entero (FX + engranaje + silencio); en la lista solo el
        silencio, porque no hay efecto de tarjeta que encender.
        """
        self._grid = bool(grid)
        self.button.setVisible(self._grid)
        self.gear.setVisible(self._grid and self.button.isChecked())
        self.setVisible(bool(show))
        if show:
            self._resize_and_place()
            self.raise_()
            self._set_panel(self._grid and self.gear.isChecked())
        else:
            self._set_panel(False)

    def _on_fx(self, _on: bool) -> None:
        self._sync_controls()

    def _on_gear(self, open_: bool) -> None:
        self.gear.set_tooltip(open_)
        self._set_panel(open_)

    def set_muted(self, muted: bool) -> None:
        """Pone el botón de silencio en su estado sin avisar (lo pidió la ventana)."""
        self.mute.set_muted(muted)

    def _on_mute(self, muted: bool) -> None:
        self.mute.set_muted(muted)

    def _sync_controls(self) -> None:
        """Ajusta el engranaje y el panel al estado de FX.

        Al esconder el engranaje hay que reencajar el grupo: si no, FX se queda
        donde estaba y deja un hueco a la derecha que ahora es suyo. El
        engranaje **no** se desmarca al apagar el efecto: recuerda si el panel
        estaba abierto, para reabrirlo al volver a encenderlo.
        """
        on = self.button.isChecked() and self._grid
        if self.gear.isVisible() != on:
            self.gear.setVisible(on)
        self._resize_and_place()
        self._set_panel(self.gear.isChecked())

    def _set_panel(self, open_: bool) -> None:
        visible = (bool(open_) and self._grid and self.button.isChecked()
                   and self.isVisible())
        self.panel.setVisible(visible)
        app = QApplication.instance()
        if visible:
            app.installEventFilter(self)
            self._resize_and_place()
            self.panel.raise_()
        else:
            app.removeEventFilter(self)

    # ----------------------------------------------------------- geometría
    def _resize_and_place(self) -> None:
        # El grupo mide distinto con y sin el engranaje: se reajusta antes de
        # colocarlo para que no baile pegado a la derecha.
        if self.layout() is not None:
            self.layout().activate()
        self.adjustSize()
        self.place()

    def place(self) -> None:
        """Pega el grupo (y su panel) a la esquina de abajo a la derecha."""
        parent = self.parentWidget()
        if parent is None:
            return
        # Esquina de la zona de contenido, descontando el halo del contenedor.
        right = parent.width() - CORNER_BUTTON_MARGIN - SCROLLBAR_INSET
        bottom = parent.height() - CORNER_BUTTON_MARGIN
        self.move(right - (self.width() - GLOW_MARGIN),
                  bottom - (self.height() - GLOW_MARGIN))
        if self.panel.isVisible():
            self.panel.adjustSize()
            self.panel.move(right - self.panel.width(),
                            self.y() + GLOW_MARGIN - self.panel.height()
                            - PANEL_GAP)
