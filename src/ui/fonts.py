"""Carga de la fuente de iconos (Font Awesome Free) para Qt.

Sustituye a ``LabelBase.register`` de Kivy. Se carga una sola vez y se
devuelve un ``QFont`` con la familia resultante para pintar los glifos.

Aquí vive también la **familia del texto** (``ui.theme.FONT_FAMILY``): se aplica
como fuente de la aplicación, **nunca** como regla ``font-family`` del QSS. Un
``font-family`` global pisa el ``setFont`` de cada widget y dejaría a los iconos
sin su glifo (la razón por la que los iconos "no salían").
"""

from PySide6.QtGui import QFont, QFontDatabase

from paths import ASSETS_DIR

_ICON_TTF = ASSETS_DIR / "fonts" / "fa-solid-900.ttf"
_family: str | None = None


def load() -> None:
    """Registra la fuente de iconos (idempotente). Llamar tras crear QApplication."""
    global _family
    if _family is not None:
        return
    if not _ICON_TTF.is_file():
        return
    font_id = QFontDatabase.addApplicationFont(str(_ICON_TTF))
    if font_id == -1:
        return
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        _family = families[0]


def icon_font(size: int | None = None) -> QFont:
    """Devuelve un ``QFont`` de la fuente de iconos (o la general si falló).

    ``size`` (en píxeles) solo vale para **pintar a mano** con ``QPainter``
    (``glyph_icon``). En un widget, ``setFont(icon_font(20))`` deja la familia
    pero **no el tamaño**: la regla global ``QWidget { font-size: 13px; }`` del
    QSS gana a lo que fije el widget (medido: pedir 16 o 28 devolvía 13). El
    tamaño de un glifo en un widget se pone en ``ui/qss.py`` por
    ``objectName``, como ``#StarButton`` o ``#SideButton``.
    """
    if _family is None:
        load()
    font = QFont(_family) if _family else QFontDatabase.systemFont(
        QFontDatabase.GeneralFont)
    if size:
        font.setPixelSize(size)
    return font


def apply_ui_font(app) -> None:
    """Fija la familia del texto de la aplicación (con su cadena de preferencia).

    Se hace con ``QApplication.setFont`` y **no** con el QSS a propósito: una
    regla ``font-family`` global gana al ``setFont`` de cada widget, así que los
    iconos (que se pintan con la fuente de Font Awesome puesta a mano) perdían
    su glifo. Como fuente de la aplicación, en cambio, Qt la usa de base y
    respeta lo que cada widget fije por encima.

    ``theme.FONT_FAMILY`` es una lista separada por comas (``Inter, Noto Sans,
    ...``); Qt se queda con la primera familia instalada y resuelve con su
    *fallback* los glifos que le falten (CJK, cirílico).
    """
    from ui import theme as t

    families = [name.strip() for name in t.FONT_FAMILY.split(",") if name.strip()]
    font = app.font()
    font.setFamilies(families)
    app.setFont(font)


def glyph_icon(glyph: str, size: int = 16, color: str = "#989898"):
    """Renderiza un glifo de la fuente de iconos como ``QIcon``.

    Útil para meter iconos dentro de un ``QLineEdit`` (``addAction``), donde no
    se puede usar directamente la fuente.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setFont(icon_font(size))
    painter.setPen(QColor(color))
    painter.drawText(pix.rect(), Qt.AlignCenter, glyph)
    painter.end()
    return QIcon(pix)
