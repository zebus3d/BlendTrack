"""Genera las capturas de documentación (docs/img/*.png) desde la app Qt.

Uso:  QT_QPA_PLATFORM=offscreen .venv/bin/python packaging/capture_docs.py

Las capturas del README son 1280x880, en español y con la barra de filtros
visible. El alto es holgado a propósito: con 624 px la última fila de tarjetas
de la tienda salía cortada por abajo (la rejilla tiene más filas que la lista).
Se hacen con el plugin offscreen de Qt, así que no hace falta servidor gráfico
(a diferencia del Kivy antiguo, que necesitaba ventana real).

El README las referencia con **rutas relativas** (`docs/img/...`), no con un
CDN: al estar en el mismo repositorio, GitHub sirve siempre la versión del
commit actual. Se probó antes con jsdelivr y no vale para esto: cachea `@main`
hasta 12 h e ignora los parámetros (`?v=`), así que seguía enseñando capturas
viejas aunque el PNG del repo estuviera actualizado.
"""

import sys
import tempfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "img"
WIDTH, HEIGHT = 1280, 880

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _prepare_folder_library(window) -> None:
    """Monta dos carpetas para que Ajustes ▸ Carpetas enseñe la biblioteca.

    La pestaña solo enseña la lista de carpetas con **más de una**; con una sola
    muestra el campo de siempre. Para la captura se parte en dos la carpeta por
    defecto: la de siempre se queda con LTS y Stable, y una subcarpeta
    ``Diarias`` con Daily y Experimental. La subcarpeta se crea vacía si falta y
    se deja ahí; los ajustes de verdad no se tocan (la captura usa una config
    temporal).
    """
    from services import channels
    from services.settings import Folder, default_destination

    base = Path(window.settings.folders[0].path) if window.settings.folders \
        else default_destination()
    second = base / "Diarias"
    second.mkdir(parents=True, exist_ok=True)
    window.settings.folders = [
        Folder(path=str(base),
               types=[channels.TYPE_LTS, channels.TYPE_STABLE], writable=True),
        Folder(path=str(second),
               types=[channels.TYPE_DAILY, channels.TYPE_EXPERIMENTAL],
               writable=True),
    ]
    window._rebuild_folder_rows()


def _next_release_tag() -> str:
    """La versión que ofrecería el aviso: la minor siguiente al último tag.

    ``src/version.py`` vale "0.0.0" en el repositorio (el CI la cuece al
    compilar), así que se mira el último tag de git; sin git, uno fijo.
    """
    import subprocess

    try:
        last = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"], capture_output=True,
            text=True, check=True, cwd=SRC.parent).stdout.strip()
        major, minor = last.lstrip("v").split(".")[:2]
        return f"v{major}.{int(minor) + 1}.0"
    except (OSError, subprocess.CalledProcessError, ValueError):
        return "v2.0.0"


def main() -> None:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import i18n
    from services import settings as settings_service
    from ui import fonts, qss
    from ui.widgets import MainWindow
    from ui.widgets.dialogs import update_dialog

    # Los ajustes van a un directorio temporal, y ojo con esto: el script fija
    # el zoom en memoria para que las capturas salgan siempre iguales, y sin
    # aislar la config ese zoom acababa escrito en el settings.json del usuario.
    # Lo vuelca closeEvent al salir, que es lo correcto en la app (flush del
    # zoom pendiente) pero aquí no debe tocar nada del usuario.
    with tempfile.TemporaryDirectory() as temp:
        settings_service.config_dir = lambda: Path(temp)

        app = QApplication([])
        i18n.set_language("es")
        fonts.load()
        app.setStyleSheet(qss.build_qss())

        OUT.mkdir(parents=True, exist_ok=True)
        window = MainWindow()
        window.resize(WIDTH, HEIGHT)
        # El zoom se fija en memoria para que la captura sea siempre igual,
        # independientemente de lo que tenga guardado el usuario. Hay que
        # reflejarlo también en el slider y la etiqueta, o la captura sale con
        # las tarjetas a un tamaño y el "NN %" de otro.
        window.zoom = 1.0
        window.zoom_slider.blockSignals(True)
        window.zoom_slider.setValue(100)
        window.zoom_slider.blockSignals(False)
        window._update_zoom_label()
        # El aviso de salto de serie es un diálogo modal: si salta en mitad de
        # la captura la bloquea y las fotos no se guardan. Aquí no interesa.
        window._offer_series_update = lambda: None
        window.show()

        def capture_store_grid():
            window.set_layout_mode("grid")
            window.set_view("store")
            # Una marcada como favorita, para que la captura enseñe la estrella
            # en ámbar (se marca la primera que haya, así no depende de una
            # versión concreta).
            visible = window._filtered()
            if visible:
                window.settings.favorites = [visible[0].favorite_key]
                window._rebuild_store()
            QTimer.singleShot(600, save_store_grid)

        def save_store_grid():
            window.grab().save(str(OUT / "store_grid.png"))
            print("guardada store_grid.png")
            QTimer.singleShot(300, capture_installed_grid)

        def capture_installed_grid():
            window.set_layout_mode("grid")
            window.set_view("installed")
            QTimer.singleShot(600, save_installed_grid)

        def save_installed_grid():
            window.grab().save(str(OUT / "installed_grid.png"))
            print("guardada installed_grid.png")
            QTimer.singleShot(300, capture_migrate)

        def capture_migrate():
            # El tablero de migración necesita saber qué versiones hay
            # instaladas para ofrecerlas como origen.
            window.set_view("migrate")
            QTimer.singleShot(700, save_migrate)

        def save_migrate():
            window.grab().save(str(OUT / "migrate.png"))
            print("guardada migrate.png")
            QTimer.singleShot(300, capture_settings)

        def capture_settings():
            window.set_view("settings")
            # La pestaña "Carpetas" es la que enseña la biblioteca por tipos.
            window.settings_tabs.setCurrentIndex(1)
            _prepare_folder_library(window)
            QTimer.singleShot(600, save_settings)

        def save_settings():
            window.grab().save(str(OUT / "settings.png"))
            print("guardada settings.png")
            QTimer.singleShot(300, capture_update)

        def capture_update():
            # El diálogo de verdad (``dialogs.update_dialog``), sin abrirlo con
            # ``exec``: una copia a mano se quedaba atrás cuando cambiaba.
            dialog, _ = update_dialog(window, _next_release_tag())
            dialog.show()

            def save_dialog():
                dialog.grab().save(str(OUT / "update_dialog.png"))
                print("guardada update_dialog.png")
                app.quit()

            QTimer.singleShot(600, save_dialog)

        QTimer.singleShot(8000, capture_store_grid)
        app.exec()


if __name__ == "__main__":
    main()
