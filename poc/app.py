"""PoC: ventana PySide6 con el look de BlendTrack, sin OpenGL.

Objetivo de esta prueba (Fase 0 del port):
  1. Reproducir la identidad visual actual con QSS (tema oscuro, esquinas
     redondeadas, gradiente superior, estados hover/pressed/checked).
  2. Cargar la fuente Font Awesome y pintar los mismos glifos.
  3. Demostrar que NO se usa OpenGL: Qt Widgets renderiza con el motor raster.

Si esto abre en Arch/Mesa 26 y en Ubuntu 24.04 sin cargar libGL/libEGL/libGLX,
el port completo es viable.
"""

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QPixmap, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Paleta (idéntica a src/ui/theme.py, con los dos tokens nuevos de relleno)
# ---------------------------------------------------------------------------
COLORS = {
    "BG": "#1D1D1D",
    "FIELD": "#171717",
    "BUTTON": "#585858",
    "FILTER": "#1D1D1D",
    "SURFACE": "#303030",
    "CHROME": "#303030",
    "CARD_DIM": "#1E1E1E",
    "CARD_DIM_ALT": "#161616",
    "ROW_ALT": "#2A2A2A",
    "SURFACE_ALT": "#3D3D3D",
    "BORDER": "#3D3D3D",
    "TEXT": "#E6E6E6",
    "MUTED": "#989898",
    "TEXT_SEL": "#FFFFFF",
    "ACCENT": "#5085B1",
    "ACCENT_DARK": "#3F6F96",
    "ACCENT_BTN": "#356089",  # NUEVO: relleno de botón primario (texto AA)
    "DANGER": "#B84A4A",
    "DANGER_DARK": "#9C3C3C",
    "WARNING": "#FFAF23",
    "SUCCESS_TEXT": "#6FCF7A",
    "INFO_TEXT": "#7AA7E0",
    "INFO_DISC": "#45729B",
}

def _assets_dir() -> Path:
    """Assets en modo fuente o empaquetado con PyInstaller."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "assets"
    return Path(__file__).resolve().parent.parent / "src" / "assets"


ASSETS = _assets_dir()
ICON_TTF = ASSETS / "fonts" / "fa-solid-900.ttf"

# Glifos Font Awesome (los mismos de src/ui/icons.py).
ICONS = {
    "STORE": "\uf0ed",
    "INSTALLED": "\uf108",
    "SETTINGS": "\uf013",
    "LAUNCH": "\uf04b",
    "DELETE": "\uf1f8",
    "REFRESH": "\uf021",
    "SEARCH": "\uf002",
    "FOLDER": "\uf07b",
    "GRID": "\uf00a",
    "LIST": "\uf0ca",
    "INFO": "\uf129",
}


def build_qss() -> str:
    """Stylesheet global equivalente a views/widgets.kv + cards.kv + dialogs.kv."""
    c = COLORS
    # Degradado vertical sutil (blanco 9% arriba -> transparente abajo), el
    # mismo que la textura GRADIENT_TOP de Kivy.
    grad = (
        "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        "stop:0 rgba(255,255,255,0.09), stop:1 rgba(255,255,255,0.0))"
    )
    return f"""
    QWidget {{
        background-color: {c['BG']};
        color: {c['TEXT']};
        font-size: 13px;
    }}
    QLabel {{ background: transparent; }}

    /* --- Barra de filtros (pastillas) --- */
    QPushButton#Pill {{
        background-color: {c['FILTER']};
        border: 1px solid rgba(0,0,0,0.35);
        border-radius: 6px;
        padding: 4px 9px;
        color: {c['TEXT']};
        font-weight: bold;
    }}
    QPushButton#Pill:hover {{ background-color: {c['ACCENT_DARK']}; color: {c['TEXT_SEL']}; }}
    QPushButton#Pill:checked {{ background-color: {c['ACCENT']}; color: {c['TEXT_SEL']}; }}
    QPushButton#Pill:disabled {{ color: rgba(230,230,230,0.35); }}

    /* --- Barra lateral --- */
    QPushButton#SideButton {{
        background-color: {c['BUTTON']};
        border: 1px solid rgba(0,0,0,0.35);
        border-radius: 8px;
        min-width: 50px; max-width: 50px;
        min-height: 50px; max-height: 50px;
        color: {c['TEXT']};
    }}
    QPushButton#SideButton:hover {{ background-color: {c['ACCENT_DARK']}; color: {c['TEXT_SEL']}; }}
    QPushButton#SideButton:checked {{ background-color: {c['ACCENT']}; color: {c['TEXT_SEL']}; }}

    /* --- Botón de tarjeta (relleno + degradado + borde) --- */
    QPushButton#CardButton {{
        background-color: {c['BUTTON']};
        border: 1px solid rgba(0,0,0,0.35);
        border-radius: 5px;
        padding: 6px 14px;
        color: {c['TEXT']};
        font-weight: bold;
    }}
    QPushButton#CardButton:hover {{ background-color: #6A6A6A; }}
    QPushButton#CardButton:pressed {{ background-color: {c['ACCENT_DARK']}; }}
    QPushButton#CardButton[variant="accent"] {{
        background-color: {c['ACCENT_BTN']};
    }}
    QPushButton#CardButton[variant="accent"]:hover {{ background-color: {c['ACCENT_DARK']}; }}
    QPushButton#CardButton[variant="danger"] {{ background-color: {c['DANGER_DARK']}; }}
    QPushButton#CardButton[variant="danger"]:hover {{ background-color: {c['DANGER']}; }}

    /* --- Botón de info (círculo azul con la "i") --- */
    QPushButton#IconLink {{
        background-color: {c['INFO_DISC']};
        border: none;
        border-radius: 12px;
        min-width: 24px; max-width: 24px;
        min-height: 24px; max-height: 24px;
        color: {c['TEXT_SEL']};
    }}
    QPushButton#IconLink:hover {{ background-color: {c['ACCENT']}; }}

    /* --- Tarjetas --- */
    QFrame#Card {{
        background-color: {c['CARD_DIM']};
        border-radius: 12px;
        border: none;
    }}
    QFrame#Card[zebra="true"] {{ background-color: {c['CARD_DIM_ALT']}; }}
    QFrame#Card:hover {{ background-color: {c['SURFACE_ALT']}; }}

    /* --- Spinners --- */
    QComboBox {{
        background-color: {c['FILTER']};
        border: 1px solid rgba(0,0,0,0.35);
        border-radius: 5px;
        padding: 4px 8px;
        color: {c['TEXT']};
        min-height: 24px;
    }}
    QComboBox:hover {{ background-color: {c['ACCENT_DARK']}; }}
    QComboBox::drop-down {{ border: none; width: 16px; }}
    QComboBox QAbstractItemView {{
        background-color: {c['SURFACE']};
        color: {c['TEXT']};
        selection-background-color: {c['ACCENT']};
        border: 1px solid {c['SURFACE_ALT']};
        outline: none;
    }}

    /* --- Barra de progreso --- */
    QProgressBar {{
        background-color: {c['FIELD']};
        border-radius: 4px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{ background-color: {c['ACCENT']}; border-radius: 4px; }}

    /* --- Barras y paneles --- */
    QFrame#Chrome {{ background-color: {c['CHROME']}; border: none; }}
    QFrame#Sidebar {{ background-color: {c['SURFACE']}; border: none; }}

    /* --- Etiquetas de texto --- */
    QLabel#Muted {{ color: {c['MUTED']}; }}
    QLabel#Warning {{ color: {c['WARNING']}; font-weight: bold; }}
    QLabel#Info {{ color: {c['INFO_TEXT']}; font-weight: bold; }}
    QLabel#Success {{ color: {c['SUCCESS_TEXT']}; font-weight: bold; }}
    QLabel#Title {{ font-size: 16px; font-weight: bold; }}
    QLabel#HeaderTitle {{ font-size: 19px; font-weight: bold; }}

    /* --- Scroll --- */
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {c['ACCENT']}; border-radius: 4px; min-height: 24px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


def icon_button(glyph: str, tooltip: str = "") -> QPushButton:
    b = QPushButton(glyph)
    b.setObjectName("IconLink")
    b.setCursor(Qt.PointingHandCursor)
    if tooltip:
        b.setToolTip(tooltip)
    return b


def card(version: str, channel: str, meta: str, zebra: bool, installed: bool,
         icon_font: str) -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setProperty("zebra", "true" if zebra else "false")
    frame.setFixedHeight(78)
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(16, 11, 12, 11)
    lay.setSpacing(12)

    logo = QLabel()
    pix = QPixmap(str(ASSETS / "images" / "blender_logo.png"))
    logo.setPixmap(pix.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    if not installed:
        logo.setStyleSheet("opacity: 0.32;")
    lay.addWidget(logo)

    text_col = QVBoxLayout()
    text_col.setSpacing(3)
    top = QHBoxLayout()
    top.setSpacing(10)
    title = QLabel(f"Blender {version}")
    title.setObjectName("Title")
    if not installed:
        title.setStyleSheet("color: rgba(230,230,230,0.6);")
    top.addWidget(title)
    chan = QLabel(channel)
    chan.setObjectName("Warning" if channel == "LTS" else "Info")
    top.addWidget(chan)
    top.addStretch()
    text_col.addLayout(top)
    meta_label = QLabel(meta)
    meta_label.setObjectName("Muted")
    if not installed:
        meta_label.setStyleSheet("color: rgba(152,152,152,0.6);")
    text_col.addWidget(meta_label)
    lay.addLayout(text_col, 1)

    lay.addWidget(icon_button(ICONS["INFO"], "Read the release notes for this version"))

    action = QPushButton("Launch" if installed else "Download")
    action.setObjectName("CardButton")
    action.setProperty("variant", "neutral" if installed else "accent")
    action.setCursor(Qt.PointingHandCursor)
    if installed:
        action.setFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))
    action.setToolTip("Launch this installed version" if installed else
                      "Download and install this version")
    lay.addWidget(action)

    if installed:
        dele = QPushButton(ICONS["DELETE"])
        dele.setObjectName("CardButton")
        dele.setProperty("variant", "danger")
        dele.setFont(icon_font)
        dele.setCursor(Qt.PointingHandCursor)
        dele.setToolTip("Remove this installed version")
        lay.addWidget(dele)

    return frame


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("BlendTrackPoC")

    icon_font = QFontDatabase.systemFont(QFontDatabase.GeneralFont)
    if ICON_TTF.is_file():
        fid = QFontDatabase.addApplicationFont(str(ICON_TTF))
        if fid != -1:
            fam = QFontDatabase.applicationFontFamilies(fid)
            if fam:
                icon_font = icon_font.__class__(fam[0])
    app.setStyleSheet(build_qss())

    win = QWidget()
    win.setWindowTitle("Blender Downloads Manager (PySide6 PoC)")
    win.resize(1060, 680)
    win.setMinimumSize(880, 540)

    root = QVBoxLayout(win)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(0)

    # --- Cabecera ---
    header = QFrame()
    header.setObjectName("Chrome")
    header.setFixedHeight(72)
    hl = QHBoxLayout(header)
    hl.setContentsMargins(16, 10, 16, 8)
    hl.setSpacing(14)
    logo = QLabel()
    logo.setPixmap(QPixmap(str(ASSETS / "images" / "app_icon.png")).scaled(
        52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    hl.addWidget(logo)
    title = QLabel("Blender Downloads Manager")
    title.setObjectName("HeaderTitle")
    hl.addWidget(title)
    hl.addStretch()

    refresh = QPushButton(ICONS["REFRESH"])
    refresh.setObjectName("SideButton")
    refresh.setFont(icon_font)
    refresh.setToolTip("Refresh the list of builds")
    hl.addWidget(refresh)

    search = QComboBox()
    search.setEditable(True)
    search.setFixedWidth(240)
    search.lineEdit().setPlaceholderText("Search...")
    hl.addWidget(search)
    root.addWidget(header)

    # --- Barra de filtros ---
    filters = QFrame()
    filters.setObjectName("Chrome")
    filters.setFixedHeight(44)
    fl = QHBoxLayout(filters)
    fl.setContentsMargins(16, 6, 16, 6)
    fl.setSpacing(6)
    group = QButtonGroup(filters)
    group.setExclusive(True)
    for name in ("All", "LTS", "Stable", "Daily", "Experimental"):
        b = QPushButton(name)
        b.setObjectName("Pill")
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(f"Filter: {name.lower()}")
        group.addButton(b)
        fl.addWidget(b)
        if name == "All":
            b.setChecked(True)
    fl.addStretch()
    grid = QPushButton(ICONS["GRID"])
    grid.setObjectName("Pill")
    grid.setFont(icon_font)
    grid.setCheckable(True)
    grid.setChecked(True)
    fl.addWidget(grid)
    lst = QPushButton(ICONS["LIST"])
    lst.setObjectName("Pill")
    lst.setFont(icon_font)
    lst.setCheckable(True)
    fl.addWidget(lst)
    platform = QComboBox()
    platform.addItems(["GNU/Linux", "Windows", "macOS"])
    platform.setFixedWidth(104)
    platform.setToolTip("Target operating system")
    fl.addWidget(platform)
    arch = QComboBox()
    arch.addItems(["x86_64", "arm64"])
    arch.setFixedWidth(82)
    arch.setToolTip("Target architecture")
    fl.addWidget(arch)
    root.addWidget(filters)

    # --- Cuerpo: barra lateral + lista ---
    body = QHBoxLayout()
    body.setSpacing(0)
    sidebar = QFrame()
    sidebar.setObjectName("Sidebar")
    sidebar.setFixedWidth(74)
    sl = QVBoxLayout(sidebar)
    sl.setContentsMargins(11, 14, 11, 14)
    sl.setSpacing(10)
    side_group = QButtonGroup(sidebar)
    side_group.setExclusive(True)
    for glyph, tip, checked in (
        (ICONS["INSTALLED"], "Show installed versions", False),
        (ICONS["STORE"], "Show the store", True),
    ):
        b = QPushButton(glyph)
        b.setObjectName("SideButton")
        b.setFont(icon_font)
        b.setCheckable(True)
        b.setChecked(checked)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        side_group.addButton(b)
        sl.addWidget(b)
    sl.addStretch()
    settings_btn = QPushButton(ICONS["SETTINGS"])
    settings_btn.setObjectName("SideButton")
    settings_btn.setFont(icon_font)
    settings_btn.setCheckable(True)
    settings_btn.setToolTip("Open settings")
    settings_btn.setCursor(Qt.PointingHandCursor)
    side_group.addButton(settings_btn)
    sl.addWidget(settings_btn)
    body.addWidget(sidebar)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    cl = QVBoxLayout(content)
    cl.setContentsMargins(14, 14, 14, 14)
    cl.setSpacing(10)
    cl.addWidget(card("5.2.1", "LTS", "359.8 MB  ·  v52  ·  x86_64", False, True, icon_font))
    cl.addWidget(card("5.3.0", "Alpha", "367.8 MB  ·  main  ·  x86_64", True, False, icon_font))
    cl.addWidget(card("5.1.2", "Stable", "371.2 MB  ·  v51  ·  x86_64", False, False, icon_font))
    cl.addWidget(card("4.5.13", "LTS", "355.4 MB  ·  v45  ·  x86_64", True, True, icon_font))
    cl.addStretch()
    scroll.setWidget(content)
    body.addWidget(scroll, 1)
    root.addLayout(body, 1)

    # --- Pie ---
    footer = QFrame()
    footer.setObjectName("Chrome")
    footer.setFixedHeight(40)
    fo = QHBoxLayout(footer)
    fo.setContentsMargins(16, 4, 16, 4)
    fo.setSpacing(12)
    status = QLabel("Ready")
    status.setObjectName("Muted")
    fo.addWidget(status)
    progress = QProgressBar()
    progress.setFixedHeight(12)
    progress.setRange(0, 100)
    progress.setValue(42)
    fo.addWidget(progress, 1)
    pct = QLabel("42 %")
    fo.addWidget(pct)
    cancel = QPushButton("Cancel")
    cancel.setObjectName("CardButton")
    cancel.setCursor(Qt.PointingHandCursor)
    fo.addWidget(cancel)
    root.addWidget(footer)

    win.show()

    # Modo captura para el PoC: guarda el PNG y sale.
    shot = os.environ.get("POC_SCREENSHOT")
    if shot:
        from PySide6.QtCore import QTimer

        def grab():
            win.grab().save(shot)
            app.quit()

        QTimer.singleShot(800, grab)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
