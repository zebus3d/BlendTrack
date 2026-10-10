"""La tarjeta de la rejilla se inclina en 3D siguiendo al ratón.

Es el efecto de las tarjetas "holográficas" de la web (``perspective`` +
``rotateX``/``rotateY`` en CSS): la tarjeta mira hacia el cursor, se eleva un
poco y un brillo recorre su superficie siguiendo la mano.

**Qt Widgets no puede inclinar un widget**: un widget es siempre un rectángulo
plano. Así que encima de la tarjeta se pone una capa (``TiltOverlay``) que:

- tapa la tarjeta con el color del fondo y pinta encima su **imagen**
  (``grab()``) con un ``QTransform`` en perspectiva;
- es **transparente al ratón** (``WA_TransparentForMouseEvents``): los clics y
  el hover llegan a la tarjeta de verdad, que sigue debajo, viva. Sus botones
  funcionan como siempre;
- vuelve a capturar la tarjeta al pasar el ratón de un control a otro, para
  que se vea el resaltado del botón que tiene debajo.

No se hace invisible la tarjeta con un ``QGraphicsOpacityEffect``: Qt aplica el
efecto también al capturarla y la imagen salía negra (medido). Y la tarjeta ya
lleva su sombra, que es el halo azul de ``motion.lift_shadow``: se queda, y
hace de luz detrás de la tarjeta inclinada.

Solo trabaja una tarjeta a la vez (la que está bajo el ratón) y solo mientras
se mueve: cuando llega a su sitio, el temporizador se para.
"""

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QImage, QPainter, QPainterPath,
                           QPixmap,
                           QRadialGradient, QTransform)
from PySide6.QtWidgets import QApplication, QWidget

from shiboken6 import isValid

from ui import theme as t
from ui.widgets import motion
from ui.widgets.gallery_art import soft_blur

# Inclinación máxima (grados) en cada eje, con el ratón en el borde. Con 9°
# apenas se notaba: la perspectiva de Qt mira desde 1024 px, y en una tarjeta
# de 250 px el escorzo de 9° es de unos pocos píxeles.
MAX_TILT = 18
# Cuánto se eleva (escala) la tarjeta bajo el ratón.
LIFT = 1.08
# Lo que se sale la capa de la tarjeta por cada lado: al inclinarse y crecer,
# las esquinas pasan del rectángulo original.
MARGIN = 44
# El halo azul va en su propia imagen, más justa que la capa: se dibuja con la
# misma transformación que la tarjeta (crecida e inclinada) y, si ocupara todo
# el margen, su borde desenfocado se salía de la capa y Qt lo cortaba con una
# raya recta (se veía sobre la tarjeta de abajo, a zoom pequeño sobre todo).
# Con ``HALO_PAD`` la cola del desenfoque llega a cero antes del borde de su
# imagen, y la imagen transformada cabe en ``MARGIN``.
HALO_PAD = 24
HALO_GROW = 4
# Cada fotograma se acerca esta parte de lo que falta (un muelle suave: la
# tarjeta persigue al ratón en vez de pegarse a él).
FOLLOW = 0.22
FRAME_MS = 16
# El brillo que sigue al ratón: su opacidad máxima y su radio (fracción del
# ancho de la tarjeta).
GLARE = 0.22
GLARE_RADIUS = 0.75
RADIUS = 12   # el de ``QFrame#Card`` en el QSS
# La sombra de "elevación": cuánto baja respecto a la tarjeta y su opacidad,
# las dos al subir del todo. Es lo que hace que se lea como que sube en Z por
# encima de las vecinas, y no solo como que crece.
SHADOW_DROP = 14
SHADOW_ALPHA = 0.55
# El halo azul que acompaña a la tarjeta inclinada (opacidad al subir del todo).
HALO_ALPHA = 0.9


class TiltOverlay(QWidget):
    """La tarjeta inclinada, pintada encima de la de verdad."""

    def __init__(self, card: QWidget):
        super().__init__(card.parentWidget())
        self.card = card
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Opaca: pinta ella misma lo que tiene detrás (``_backdrop``), así Qt
        # no repinta en cada fotograma las tarjetas de debajo con sus sombras
        # desenfocadas. Medido: 9,3 ms por fotograma sin esto.
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self._backdrop = QPixmap()
        self._opaque = True
        self._under = None
        self._pixmap = card.grab()
        self._hover = False
        self._now = [0.0, 0.0, 1.0, 0.0]      # giro X, giro Y, escala, brillo
        self._goal = [0.0, 0.0, 1.0, 0.0]
        self._glare_at = QPointF(0.5, 0.5)
        self._frame = QTimer(self)
        self._frame.setInterval(FRAME_MS)
        self._frame.timeout.connect(self._step)
        # La rejilla se reconstruye a menudo (al filtrar, al recuperar el foco
        # se reescanean las instaladas) y la tarjeta que se inclinaba se borra.
        # La capa tiene que enterarse YA: un fotograma más tocando la tarjeta
        # borrada lanzaba una excepción dentro de ``paintEvent``.
        card.destroyed.connect(self._card_gone)
        self._follow_card()

    def _card_gone(self, *_args) -> None:
        self.card = None
        self._frame.stop()
        QApplication.instance().removeEventFilter(self)
        self.hide()
        self.deleteLater()

    def _alive(self) -> bool:
        return self.card is not None and isValid(self.card)

    # -------------------------------------------------------------- estado
    def hover(self, on: bool) -> None:
        """El ratón entra (se inclina y eleva) o sale (vuelve a su sitio)."""
        if not self._alive():
            return
        self._hover = on
        app = QApplication.instance()
        if on:
            app.installEventFilter(self)
            self._grab()
            # Si venía de volver a su sitio (modo transparente), su foto de
            # fondo ya es vieja: se rehace.
            if not self.isVisible() or not self._opaque:
                self._snapshot()
                # Y otra vez cuando acaben las animaciones de las vecinas (el
                # halo de la que acaba de soltar el ratón aún se apaga): si no,
                # ese brillo se quedaba pegado en la foto mientras dure esta.
                QTimer.singleShot(motion.LIFT_MS + 60, self, self._refresh_backdrop)
            self._set_opaque(True)
            self._aim(QCursor.pos())
            self.show()
            self.raise_()
        else:
            app.removeEventFilter(self)
            self._goal = [0.0, 0.0, 1.0, 0.0]
            # De vuelta a su sitio, transparente: las vecinas cambian (la que
            # recibe ahora el ratón se resalta) y una foto fija de ellas salía
            # como un artefacto. Es el modo caro, pero dura lo que la vuelta.
            self._set_opaque(False)
        self._run()

    def _refresh_backdrop(self) -> None:
        if self._alive() and self._opaque and self.isVisible():
            self._snapshot()
            self.update()

    def _set_opaque(self, on: bool) -> None:
        self._opaque = on
        self.setAttribute(Qt.WA_OpaquePaintEvent, on)

    def finish(self) -> None:
        """Termina en el acto: la tarjeta a su sitio y la capa fuera.

        Lo pide la tarjeta vecina al recibir el ratón (``tilt``): dos capas
        solapándose, cada una con su foto de fondo, se pisaban.
        """
        self._hover = False
        QApplication.instance().removeEventFilter(self)
        self._frame.stop()
        self._now = [0.0, 0.0, 1.0, 0.0]
        self._goal = [0.0, 0.0, 1.0, 0.0]
        self.hide()

    def _run(self) -> None:
        """Pone en marcha la animación, si no lo está ya.

        **Nunca ``start()`` a secas**: el ratón manda movimientos mucho más a
        menudo que cada ``FRAME_MS``, y ``start()`` sobre un temporizador en
        marcha lo reinicia. Así no llegaba a vencer nunca mientras el ratón se
        movía y la tarjeta solo se ponía al día al pararlo, a saltos (medido en
        Wayland: un paso en 3 s de movimiento). Parecía que "le costaba".
        """
        if not self._frame.isActive():
            self._frame.start()

    def _snapshot(self) -> None:
        """Una foto de lo que hay detrás de la capa (las vecinas), una vez.

        Se hace con la capa escondida, para que no salga en su propia foto.
        Si la tarjeta se mueve (la rejilla se recoloca), se repite.
        """
        parent = self.parentWidget()
        if parent is None:
            return
        # Sin ninguna capa inclinada delante, tampoco la de la tarjeta vecina
        # que aún vuelve a su sitio: si no, se quedaba "impresa" en la foto y
        # se veía una tarjeta fantasma.
        others = [w for w in parent.findChildren(TiltOverlay)
                  if w.isVisible() and w is not self]
        shown = self.isVisible()
        for widget in [self, *others]:
            widget.hide()
        self._follow_card(snapshot=False)
        self._backdrop = parent.grab(self.geometry())
        for widget in others:
            widget.show()
        if shown:
            self.show()
            self.raise_()

    def eventFilter(self, watched, event):
        # El ratón sobre la tarjeta llega a sus hijos, no a ella: se sigue
        # desde aquí, mirando la posición global del cursor.
        if event.type() == QEvent.MouseMove:
            self._aim(QCursor.pos())
            # Se recaptura solo al pasar de un control a otro de la tarjeta (el
            # botón que se ilumina), no cada poco: capturar re-renderiza la
            # tarjeta con su sombra.
            if self._alive():
                under = self.card.childAt(self.card.mapFromGlobal(QCursor.pos()))
                if under is not self._under:
                    self._under = under
                    QTimer.singleShot(0, self, self._grab)
        elif event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            QTimer.singleShot(0, self, self._grab)   # el botón pulsado, a la vista
        return False

    def _aim(self, global_pos) -> None:
        if not self._alive():
            return
        local = self.card.mapFromGlobal(global_pos)
        w, h = max(1, self.card.width()), max(1, self.card.height())
        x = min(1.0, max(0.0, local.x() / w))
        y = min(1.0, max(0.0, local.y() / h))
        # La tarjeta mira hacia el ratón: con el cursor a la derecha, su lado
        # derecho se hunde (giro en Y) y con el cursor arriba, el de arriba.
        self._goal = [(y - 0.5) * 2 * MAX_TILT, -(x - 0.5) * 2 * MAX_TILT,
                      LIFT, 1.0]
        self._glare_at = QPointF(x, y)
        self._run()

    def _halo(self, w: int, h: int) -> QPixmap:
        """La silueta de la tarjeta en el color de su versión, desenfocada."""
        colour = getattr(self.card, "glow_color", None) or t.ACCENT
        key = (w, h, colour)
        if getattr(self, "_halo_key", None) != key:
            image = QImage(w + 2 * HALO_PAD, h + 2 * HALO_PAD,
                           QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            shape = QPainter(image)
            shape.setRenderHint(QPainter.Antialiasing)
            shape.setPen(Qt.NoPen)
            shape.setBrush(QColor(colour))
            g = HALO_GROW
            shape.drawRoundedRect(QRectF(HALO_PAD - g, HALO_PAD - g, w + 2 * g,
                                         h + 2 * g), RADIUS + g, RADIUS + g)
            shape.end()
            self._halo_pixmap = QPixmap.fromImage(soft_blur(image, 3))
            self._halo_key = key
        return self._halo_pixmap

    def _shadow(self, w: int, h: int) -> QPixmap:
        """La silueta negra y desenfocada de la tarjeta, del tamaño de la capa.

        Se hace una vez por tamaño. El desenfoque cabe dentro del margen de la
        capa: si se saliera, se cortaría en seco en su borde.
        """
        key = (w, h)
        if getattr(self, "_shadow_key", None) != key:
            image = QImage(w + 2 * MARGIN, h + 2 * MARGIN,
                           QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            shape = QPainter(image)
            shape.setRenderHint(QPainter.Antialiasing)
            shape.setPen(Qt.NoPen)
            shape.setBrush(QColor(0, 0, 0))
            shape.drawRoundedRect(QRectF(MARGIN + 6, MARGIN + 4, w - 12, h - 4),
                                  RADIUS, RADIUS)
            shape.end()
            self._shadow_pixmap = QPixmap.fromImage(soft_blur(image, 3))
            self._shadow_key = key
        return self._shadow_pixmap

    def _grab(self) -> None:
        if self._alive():
            self._pixmap = self.card.grab()
            self.update()

    def _follow_card(self, snapshot: bool = True) -> None:
        if not self._alive():
            return
        target = self.card.geometry().adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        if target != self.geometry():
            self.setGeometry(target)
            if snapshot:
                self._snapshot()

    def _step(self) -> None:
        if not self._alive():
            self._frame.stop()
            return
        done = True
        for i in range(4):
            gap = self._goal[i] - self._now[i]
            if abs(gap) > 0.002:
                self._now[i] += gap * FOLLOW
                done = False
            else:
                self._now[i] = self._goal[i]
        self._follow_card()
        self.update()
        if done:
            self._frame.stop()
            if not self._hover:
                # De vuelta en su sitio y sin ratón: la tarjeta real ya se
                # ve igual, la capa sobra.
                self.hide()

    # -------------------------------------------------------------- pintar
    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            self._paint(painter)
        finally:
            # Se cierra pase lo que pase: una excepción con el painter abierto
            # dejaba la ventana entera sin poder pintar ("one painter at a
            # time", "endPaint() called with active painter") y acababa en
            # violación de segmento.
            painter.end()

    def _paint(self, painter) -> None:
        if not self._alive():
            return
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        if self._opaque:
            painter.drawPixmap(0, 0, self._backdrop)
        w, h = self.card.width(), self.card.height()
        card_rect = QRectF(MARGIN, MARGIN, w, h)
        # Tapa la tarjeta de verdad: por las esquinas que deja libres la
        # inclinada se vería la de debajo, plana, y saldrían dos.
        # Con la forma redondeada de la tarjeta y no con un cuadrado: en las
        # esquinas, el cuadrado se comía el halo y se veían picos.
        cover = QPainterPath()
        cover.addRoundedRect(card_rect, RADIUS, RADIUS)
        painter.fillPath(cover, QColor(t.BG))
        rot_x, rot_y, scale, glare = self._now
        # La sombra de elevación, antes que la tarjeta: más abajo y más oscura
        # cuanto más sube (``scale`` va de 1 a ``LIFT``).
        height = max(0.0, min(1.0, (scale - 1.0) / (LIFT - 1.0)))
        if height > 0.01:
            shadow = self._shadow(w, h)
            painter.setOpacity(SHADOW_ALPHA * height)
            painter.drawPixmap(QPointF(0, SHADOW_DROP * height), shadow)
            painter.setOpacity(1.0)
        transform = QTransform()
        transform.translate(MARGIN + w / 2, MARGIN + h / 2)
        transform.rotate(rot_x, Qt.XAxis)
        transform.rotate(rot_y, Qt.YAxis)
        transform.scale(scale, scale)
        transform.translate(-w / 2, -h / 2)
        painter.setTransform(transform)
        if height > 0.01:
            # El halo, inclinado con la tarjeta y sumando luz: el de la tarjeta
            # de debajo se quedaba plano y medio tapado.
            painter.setOpacity(HALO_ALPHA * height)
            painter.setCompositionMode(QPainter.CompositionMode_Plus)
            painter.drawPixmap(QPointF(-HALO_PAD, -HALO_PAD), self._halo(w, h))
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.setOpacity(1.0)
        painter.drawPixmap(QPointF(0, 0), self._pixmap)
        if glare > 0.01:
            # El brillo: una luz blanca en el punto que mira al ratón, solo
            # dentro de la silueta de la tarjeta y sumando luz (``Plus``).
            shape = QPainterPath()
            shape.addRoundedRect(QRectF(0, 0, w, h), RADIUS, RADIUS)
            painter.setClipPath(shape)
            spot = QPointF(self._glare_at.x() * w, self._glare_at.y() * h)
            light = QRadialGradient(spot, w * GLARE_RADIUS)
            light.setColorAt(0.0, QColor(255, 255, 255, round(255 * GLARE * glare)))
            light.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setCompositionMode(QPainter.CompositionMode_Plus)
            painter.fillRect(QRectF(0, 0, w, h), light)


def tilt(card: QWidget, on: bool) -> None:
    """Empieza o termina la inclinación de ``card`` (una capa por tarjeta)."""
    overlay = getattr(card, "_tilt", None)
    if on and card.parentWidget() is not None:
        # Una sola capa a la vez: la de la tarjeta de la que viene el ratón,
        # si aún vuelve a su sitio, termina ya (ver ``TiltOverlay.finish``).
        for other in card.parentWidget().findChildren(TiltOverlay):
            if other is not overlay and other.isVisible() and isValid(other):
                other.finish()
    if overlay is None:
        if not on or card.parentWidget() is None:
            return
        overlay = TiltOverlay(card)
        card._tilt = overlay
    overlay.hover(on)
