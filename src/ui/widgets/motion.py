"""Animaciones de la interfaz: el movimiento que el QSS no sabe hacer.

El QSS de Qt no tiene ``transition``: un ``:hover`` cambia de golpe, y por eso
las apps Qt suelen parecer rígidas. Aquí viven los tres efectos de la app, todos
con el marco de animación de Qt (``QVariantAnimation``) y todos **cortos**
(150-300 ms), para que se noten sin hacer esperar:

- **Ajustes baja desde arriba** (``slide_stack``), como la versión Kivy y como
  las preferencias de Blender 2.4x, que se sacaban arrastrando la cabecera
  hacia abajo. Al salir, vuelve a subir.
- **La tarjeta "se levanta"** bajo el ratón (``lift_shadow``): su sombra crece
  y se vuelve un halo del color de acento.
- **La estrella hace "pop"** al marcar un favorito (lo pinta ``StarButton``
  con ``star_pop_scale`` y ``star_burst``).

Qt pinta por CPU (motor *raster*), y eso manda en cómo se anima. Nada de
animar efectos sobre la rejilla entera: ``card_shadow`` midió que la sombra ya
cuesta ~27 ms al pintar 60 tarjetas. Cada animación toca **un solo widget** (la
tarjeta bajo el ratón, la estrella pulsada) o pinta dos imágenes ya hechas (el
deslizamiento), que es lo más barato que hay.
"""

import math

from PySide6.QtCore import QEasingCurve, QPoint, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from ui import theme as t

# Duración del deslizamiento de Ajustes. Kivy tardaba 400 ms y se hacía largo
# al entrar y salir varias veces seguidas; 280 sigue leyéndose como "baja".
SLIDE_MS = 280

# Duración del "levantarse" de una tarjeta. Al pasar el ratón por encima de
# varias seguidas, más de ~200 ms deja una estela de halos encendidos.
LIFT_MS = 160

# Duración del "pop" de la estrella.
POP_MS = 420

# La sombra de una tarjeta en reposo (``cards.card_shadow``) y levantada. En
# reposo es negra, leve y desplazada abajo a la derecha; levantada se centra
# (desplazamiento 0) y toma el color de acento, así que se lee como un halo
# azul alrededor de la tarjeta. El radio no pasa de 18: la rejilla deja 10 px
# entre tarjetas y un halo más ancho lo taparía la tarjeta de al lado.
SHADOW_REST = {"blur": 10.0, "offset": 2.0, "color": QColor(0, 0, 0, 110)}
SHADOW_LIFT = {"blur": 18.0, "offset": 0.0, "color": QColor(t.ACCENT)}
SHADOW_LIFT["color"].setAlpha(200)


def _lerp(a: float, b: float, k: float) -> float:
    return a + (b - a) * k


def _animation(owner, ms: int, curve, on_value, on_done=None) -> QVariantAnimation:
    """Una animación de 0 a 1 que llama a ``on_value`` en cada fotograma.

    Va emparentada a ``owner``: si el widget se destruye (al reconstruir la
    rejilla, por ejemplo), la animación muere con él y no llama a nadie.
    """
    anim = QVariantAnimation(owner)
    anim.setDuration(ms)
    anim.setEasingCurve(curve)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.valueChanged.connect(on_value)
    if on_done is not None:
        anim.finished.connect(on_done)
    return anim


# --------------------------------------------------------------- tarjetas
def _apply_shadow(effect: QGraphicsDropShadowEffect, k: float,
                  colour: str | None = None, boost: float = 1.0) -> None:
    """Pone la sombra en el punto ``k`` (0 = reposo, 1 = levantada).

    ``colour`` es el del halo levantado: el de la versión (``glow_color`` de la
    tarjeta: naranja LTS, azul estable, violeta en pruebas). ``boost`` (> 1)
    sube la intensidad y el radio del halo: lo usan las tarjetas de rejilla
    cuando el efecto está apagado, para que el realce no se pierda.
    """
    rest, lift = SHADOW_REST, SHADOW_LIFT
    effect.setBlurRadius(_lerp(rest["blur"], lift["blur"] * boost, k))
    offset = _lerp(rest["offset"], lift["offset"], k)
    effect.setOffset(offset, offset)
    target = QColor(colour) if colour else QColor(lift["color"])
    target.setAlpha(min(255, round(lift["color"].alpha() * boost)))
    a = rest["color"]
    effect.setColor(QColor(round(_lerp(a.red(), target.red(), k)),
                           round(_lerp(a.green(), target.green(), k)),
                           round(_lerp(a.blue(), target.blue(), k)),
                           round(_lerp(a.alpha(), target.alpha(), k))))


def lift_shadow(widget: QWidget, on: bool) -> None:
    """Levanta (o posa) la tarjeta animando su sombra.

    Si el ratón sale a mitad de la subida, la bajada empieza **desde donde
    iba**, no desde arriba del todo: por eso se guarda el punto actual en
    ``_lift``. Sin una ``QGraphicsDropShadowEffect`` instalada no hace nada.
    """
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsDropShadowEffect):
        return
    anim = getattr(widget, "_lift_anim", None)
    if anim is None:
        widget._lift = 0.0

        def step(value):
            widget._lift = float(value)
            _apply_shadow(effect, widget._lift,
                          getattr(widget, "glow_color", None),
                          getattr(widget, "glow_boost", 1.0))

        anim = QVariantAnimation(widget)
        anim.setDuration(LIFT_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(step)
        widget._lift_anim = anim
    anim.stop()
    anim.setStartValue(widget._lift)
    anim.setEndValue(1.0 if on else 0.0)
    anim.start()


# -------------------------------------------------------------- estrella
def star_pop_scale(k: float) -> float:
    """Escala de la estrella en el punto ``k`` del pop (empieza y acaba en 1).

    Crece rápido hasta ~1,45, se pasa un poco por debajo de 1 al volver y se
    asienta: el rebote es lo que hace que parezca que "salta".
    """
    if k <= 0.0 or k >= 1.0:
        return 1.0
    return 1.0 + 0.45 * math.sin(math.pi * k * 1.25) * (1.0 - k)


def star_burst(painter: QPainter, center, k: float, color: QColor) -> None:
    """Las chispas del pop: seis puntos que salen del centro y se apagan.

    Todo cabe en la diana de 24×24 de la estrella (el radio llega a 11 px):
    un widget no puede pintar fuera de su rectángulo. Salen ya desde el borde
    del glifo (8 px): más adentro, la estrella agrandada las tapa.
    """
    if k <= 0.0 or k >= 1.0:
        return
    radius = 8.0 + 3.0 * k
    dot = QColor(color)
    dot.setAlphaF(max(0.0, 1.0 - k) * 0.9)
    painter.setPen(Qt.NoPen)
    painter.setBrush(dot)
    size = 2.2 * (1.0 - 0.5 * k)
    for i in range(6):
        angle = math.radians(i * 60 - 90)
        painter.drawEllipse(
            QPoint(round(center.x() + radius * math.cos(angle)),
                   round(center.y() + radius * math.sin(angle))),
            size, size)


def play_pop(widget: QWidget) -> None:
    """Arranca (o reinicia) el pop de ``widget``, que lee ``_pop`` al pintar."""
    anim = getattr(widget, "_pop_anim", None)
    if anim is None:
        def step(value):
            widget._pop = float(value)
            widget.update()

        def done():
            widget._pop = 0.0
            widget.update()

        anim = _animation(widget, POP_MS, QEasingCurve.Linear, step, done)
        widget._pop_anim = anim
    anim.stop()
    anim.start()


# ------------------------------------------------------- deslizar páginas
class _SlideOverlay(QWidget):
    """Lo que se ve encima del stack mientras dura el deslizamiento.

    Pinta dos imágenes fijas (la página que se va y la que llega) y las mueve.
    Es mucho más barato que mover las páginas de verdad: la de Ajustes tiene
    decenas de controles y moverla obligaría a repintarlos todos en cada
    fotograma. Debajo, la página nueva ya es la actual desde el primer momento,
    así que nada del estado de la app espera a la animación.
    """

    def __init__(self, stack: QWidget, before, after, down: bool):
        super().__init__(stack)
        self._before = before
        self._after = after
        self._down = down
        self._k = 0.0
        self.setGeometry(stack.rect())
        # Las dos imágenes cubren siempre el alto entero, así que no hace falta
        # que Qt borre el fondo antes de cada fotograma. Y al estar encima, el
        # overlay se queda los clics de esos 280 ms: no llegan a ciegas a la
        # página de debajo, que el usuario aún no ve en ese sitio.
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.show()
        self.raise_()

    def set_progress(self, k: float) -> None:
        self._k = k
        self.update()

    def paintEvent(self, event):
        h = self.height()
        # Al bajar, la nueva entra por arriba empujando a la vieja hacia abajo;
        # al subir, al revés. Es la ``SlideTransition`` de Kivy.
        sign = 1 if self._down else -1
        old_y = round(sign * h * self._k)
        new_y = round(-sign * h * (1.0 - self._k))
        painter = QPainter(self)
        try:
            painter.drawPixmap(0, old_y, self._before)
            painter.drawPixmap(0, new_y, self._after)
        finally:
            painter.end()   # cerrado siempre (ver ``tilt.TiltOverlay``)


def slide_stack(stack, before, down: bool) -> None:
    """Desliza el ``stack`` de la imagen ``before`` a su página actual.

    Se llama **después** de cambiar de página, con una captura de cómo estaba
    justo antes (``stack.grab()``). Si había un deslizamiento a medias se
    sustituye: la captura ``before`` ya lo incluye en el punto en que iba, así
    que el cambio no salta.
    """
    previous = getattr(stack, "_slide", None)
    if previous is not None:
        previous[0].stop()
        previous[1].deleteLater()
    page = stack.currentWidget()
    # La página nueva puede no tener aún el tamaño del stack (QStackedWidget
    # deja las páginas ocultas con medidas viejas): se ajusta antes de
    # capturarla, o la imagen saldría con el tamaño de la última vez.
    page.resize(stack.size())
    if page.layout() is not None:
        page.layout().activate()
    overlay = _SlideOverlay(stack, before, page.grab(), down)

    def done():
        overlay.hide()
        overlay.deleteLater()
        stack._slide = None

    anim = _animation(overlay, SLIDE_MS, QEasingCurve.OutCubic,
                      overlay.set_progress, done)
    stack._slide = (anim, overlay)
    anim.start()
