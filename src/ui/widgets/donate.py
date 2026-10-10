"""El popup de donación: elige la cantidad y abre PayPal en el navegador.

Es un popup flotante —el mismo estilo que el panel de ajustes del efecto de la
rejilla (reutiliza su ``objectName`` ``TiltPanel``)— anclado por encima del icono
de donación del pie. Se cierra al pulsar fuera o con Escape.

La cantidad la elige el usuario **aquí** porque en la URL de PayPal.Me un
importe prefijado queda **fijo** (no se puede cambiar en la página): la añadimos
a la URL y abrimos el navegador.
"""

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QDoubleSpinBox,
                               QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
                               QVBoxLayout, QWidget)

from i18n import tr
from ui.widgets.buttons import CardButton

# Separación entre el popup y el icono que lo abre, y respeto a los bordes.
GAP = 8
EDGE = 8
# Cantidades rápidas que se pueden tocar (el campo queda editable igualmente).
PRESETS = (2, 5, 10, 20)


def paypal_url(base: str, amount: float) -> str:
    """La URL de PayPal.Me con el importe (sin ceros de relleno: ``5``, ``1.5``)."""
    value = f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{base}/{value}"


class DonationPopup(QWidget):
    """Pide la cantidad y, al donar, avisa con ella (``on_donate``)."""

    def __init__(self, parent, anchor, on_donate):
        super().__init__(parent)
        self._anchor = anchor
        self._on_donate = on_donate
        # Mismo look que el popup de ajustes del efecto (ver ``qss.py``).
        self.setObjectName("TiltPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 190))
        self.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        heading = QLabel(tr("Support Blender Manager"))
        heading.setObjectName("DialogTitle")
        lay.addWidget(heading)
        message = QLabel(tr(
            "Thank you for supporting the project! Choose how much you would "
            "like to donate; PayPal will open in your browser."))
        message.setObjectName("Muted")
        message.setWordWrap(True)
        message.setFixedWidth(240)
        lay.addWidget(message)

        self.amount = QDoubleSpinBox()
        self.amount.setObjectName("DonationAmount")
        self.amount.setRange(1.0, 1000.0)
        self.amount.setDecimals(2)
        self.amount.setValue(5.0)
        self.amount.setSuffix(" €")
        self.amount.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.amount.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.amount.setToolTip(tr("Amount you want to donate, in euros."))
        lay.addWidget(self.amount)

        presets = QHBoxLayout()
        presets.setSpacing(6)
        for value in PRESETS:
            preset = CardButton(f"{value} €")
            preset.clicked.connect(
                lambda _=False, v=value: self.amount.setValue(v))
            presets.addWidget(preset)
        presets.addStretch()
        lay.addLayout(presets)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = CardButton(tr("Cancel"), tooltip=tr("Do nothing."))
        cancel.clicked.connect(self.close)
        buttons.addWidget(cancel)
        donate = CardButton(tr("Donate"), variant="accent", tooltip=tr(
            "Open PayPal in your browser with this amount."))
        donate.clicked.connect(self._donate)
        buttons.addWidget(donate)
        lay.addLayout(buttons)
        self.adjustSize()

    def _donate(self) -> None:
        self._on_donate(round(self.amount.value(), 2))
        self.close()

    # ----------------------------------------------------------- colocación
    def popup(self) -> None:
        """Coloca el popup encima del icono, lo enseña y escucha para cerrarse."""
        self._place()
        self.show()
        self.raise_()
        self.setFocus()
        QApplication.instance().installEventFilter(self)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None or self._anchor is None:
            return
        anchor = self._anchor.mapTo(parent, QPoint(0, 0))
        x = anchor.x() + self._anchor.width() - self.width()
        x = max(EDGE, min(x, parent.width() - self.width() - EDGE))
        y = max(EDGE, anchor.y() - self.height() - GAP)
        self.move(x, y)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress:
            # Un clic fuera del popup lo cierra (como cualquier desplegable),
            # salvo en el icono que lo abre: eso lo alterna su propio botón.
            point = event.globalPosition().toPoint()
            if (not self.rect().contains(self.mapFromGlobal(point))
                    and not self._hits_anchor(point)):
                self.close()
        elif event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self.close()
        elif event.type() == QEvent.Resize and watched is self.parentWidget():
            self._place()
        return super().eventFilter(watched, event)

    def _hits_anchor(self, global_pos) -> bool:
        if self._anchor is None:
            return False
        return self._anchor.rect().contains(
            self._anchor.mapFromGlobal(global_pos))

    def hideEvent(self, event):
        super().hideEvent(event)
        # Deja de escuchar la aplicación cuando no está: si no, cada evento
        # pasaría por Python aunque el popup esté cerrado.
        QApplication.instance().removeEventFilter(self)
