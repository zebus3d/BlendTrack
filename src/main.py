"""Punto de entrada de Blender Manager (versión PySide6).

Modos de uso:

* ``python3 src/main.py``               -> abre la interfaz.
* ``python3 src/main.py --debug``       -> igual, y si la app revienta (un fallo
  nativo de Qt, por ejemplo) deja la pila de Python en el log.
* ``python3 src/main.py --smoke``       -> lista versiones por consola (sin ventana).
* ``python3 src/main.py --screenshot RUTA.png`` -> arranca, captura y sale.
* ``python3 src/main.py --apply-update APP_DIR PID`` -> uso interno del auto-update.
* ``python3 src/main.py --grant-access CARPETA`` -> uso interno de Windows (UAC).

La interfaz usa Qt Widgets (PySide6), que renderiza con el motor *raster* (CPU)
en vez de exigir OpenGL: eso es lo que permite que un mismo AppImage funcione
en todas las distros, sin depender del Mesa del sistema.

**Los imports de Qt van dentro de las funciones, no aquí arriba**, y es a
propósito: ``--smoke``, ``--apply-update`` y ``--grant-access`` no abren
ventana y no deben cargar Qt. Cargarlo cuesta tiempo y, sobre todo, puede
fallar por motivos que no tienen nada que ver con lo que se pidió (un plugin
``xcb`` que falta en el equipo, una sesión sin pantalla en el CI): el smoke
test del CI comprueba la red y el auto-update tiene que poder reemplazar el
binario aunque la interfaz no arranque. Arriba solo va lo que no toca Qt.
"""

import argparse
import faulthandler
import os
import sys
import threading
import traceback
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Estos imports van después de tocar ``sys.path``: en modo fuente es lo que
# hace que ``services`` y compañía se encuentren desde cualquier carpeta.
import i18n
from services import api, detector, downloader, updater

# Fichero abierto al que ``faulthandler`` escribe con ``--debug``. Tiene que
# seguir abierto mientras dure el proceso: el volcado se hace justo cuando el
# intérprete está cayendo y ya no puede abrir nada.
_crash_log = None


def smoke() -> int:
    """Comprobación rápida sin interfaz: descarga el listado y lo imprime.

    Devuelve 0 si ha podido descargar y 1 si no, para que el CI (o quien lo
    ejecute) note que el binario no tiene red. Antes usaba ``get_builds``, que
    cae al caché de disco cuando falla: imprimía las compilaciones guardadas y
    salía con 0, así que un binario incapaz de hacer HTTPS *parecía* funcionar
    (nos pasó en Arch y lo tapó durante días).
    """
    info = detector.detect()
    try:
        # ``fetch_builds`` devuelve ``(builds, etags)``; aquí solo importan las
        # builds.
        builds, _ = api.fetch_builds()
    except Exception as error:
        print(f"ERROR: no se pudo descargar el listado: {error}", file=sys.stderr)
        return 1
    if not builds:
        print("ERROR: el listado vino vacío", file=sys.stderr)
        return 1
    print("system:", info)
    print("total builds:", len(builds))
    # El filtro usa el nombre normalizado (Windows es "amd64" en la API): si no,
    # en Windows este listado saldría vacío aunque hubiera compilaciones.
    for build in api.available_for(builds, info.os_name,
                                   api.normalize_arch(info.arch))[:12]:
        tag = "LTS" if build.is_lts else build.risk
        print(f"  {build.version:<8} {tag:<7} {build.branch:<5} "
              f"{build.human_size:>10}  {build.filename}")
    return 0


def _clean_previous_session(settings) -> None:
    """Borra los restos de la sesión anterior.

    Dos cosas: el binario que se descargó para actualizar (una AppImage son
    ~73 MB) y las descargas de Blender que se quedaron a medias (``.part``).

    OJO: estas dos llamadas vivían en ``root.py`` en la versión Kivy y se
    perdieron al portar la interfaz, así que el binario de cada actualización se
    quedaba en el caché para siempre. Hay un test que comprueba que se llaman.
    """
    updater.cleanup_staging()
    # Solo en las carpetas donde se descarga: un ``.part`` no puede aparecer en
    # una carpeta que solo se escanea, y en una con el candado cerrado no
    # tenemos permiso para borrar nada (ni queremos).
    for folder in settings.install_folders():
        updater.cleanup_partials(folder.path)


def _log_unhandled(exc_type, exc_value, exc_traceback, where: str) -> None:
    """Escribe en el log de la app el traceback de un fallo no controlado.

    El log (``blendermanager.log``) es lo que un usuario nos puede mandar desde
    su equipo; la consola, en un binario sin terminal, no la ve nadie.
    """
    detail = "".join(traceback.format_exception(exc_type, exc_value,
                                                exc_traceback))
    # ``downloader.log`` y no un ``log`` importado: así se resuelve al
    # llamarlo y los tests pueden sustituirlo en su módulo.
    downloader.log(f"unhandled error ({where}):\n{detail}")


def _install_thread_exception_hook() -> None:
    """Manda al log los fallos que revientan un hilo de trabajo.

    Las descargas, las extracciones y las llamadas a Blender corren en hilos
    (``threading.Thread``). Una excepción que se escapa de un hilo **no** pasa
    por ``sys.excepthook``: Python la imprime por stderr y el hilo muere. En el
    binario nadie ve esa consola, así que el fallo desaparecía sin rastro y la
    interfaz se quedaba esperando un aviso que no iba a llegar. No se enseña un
    diálogo porque desde un hilo no se pueden crear ventanas de Qt; basta con
    que quede en el log. Después se llama al gancho de siempre, para que en
    desarrollo se siga viendo en la consola.
    """
    def hook(args):
        if not issubclass(args.exc_type, SystemExit):
            name = args.thread.name if args.thread is not None else "?"
            _log_unhandled(args.exc_type, args.exc_value, args.exc_traceback,
                           f"thread {name}")
        threading.__excepthook__(args)

    threading.excepthook = hook


def _install_exception_hook() -> None:
    """Deja constancia (y avisa) cuando un fallo no controlado revienta la UI.

    PySide6 se traga las excepciones que saltan **dentro de un slot**: las
    imprime por stderr y sigue, así que desde el menú el usuario solo ve que "no
    pasa nada". Nos pasó con la papelera: reventaba al construir el mensaje del
    diálogo (concatenar un ``Path`` a un ``str``) y no se borraba nada ni se
    decía nada. El ``sys.excepthook`` de Python **sí** se llama en ese caso, así
    que desde aquí lo mandamos al log de la aplicación y lo enseñamos.
    """
    from i18n import tr
    from ui.widgets.dialogs import show_error

    def hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        _log_unhandled(exc_type, exc_value, exc_traceback, "interface")
        # Un diálogo como mucho: si algo falla en bucle, el log ya lo tiene todo
        # y no queremos llenar la pantalla de ventanas.
        if getattr(hook, "showing", False):
            return
        hook.showing = True
        try:
            show_error(None, tr("Unexpected error"),
                       f"{exc_type.__name__}: {exc_value}")
        except Exception:
            pass
        finally:
            hook.showing = False

    sys.excepthook = hook


def _enable_crash_log() -> None:
    """``--debug``: si el proceso revienta, la pila de Python va al log.

    Hay fallos que no son excepciones: un acceso inválido dentro de Qt mata el
    proceso de golpe (``SIGSEGV``) y ningún ``excepthook`` llega a enterarse.
    ``faulthandler`` sí: en ese momento vuelca la pila de cada hilo. Se manda
    al mismo log que lo demás (y no a la consola) porque en el binario de
    Windows no hay consola: ``sys.stderr`` es ``None``.
    """
    global _crash_log
    from services.settings import cache_dir

    try:
        path = cache_dir() / "blendermanager.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        _crash_log = open(path, "a", encoding="utf-8")
    except OSError:
        return
    faulthandler.enable(file=_crash_log, all_threads=True)


def _start_window(window, start_minimized: bool) -> None:
    """Enseña la ventana, o la deja en la bandeja si se pidió arrancar oculto.

    Arrancar oculto (``start_minimized``) es lo que hace útil el autoarranque:
    la aplicación queda a mano sin aparecer al iniciar la sesión. Si no hay
    bandeja se enseña igual, porque esconderla sin un icono al que volver la
    dejaría inaccesible.
    """
    from PySide6.QtCore import QTimer

    from ui.widgets.tray import TrayIcon

    if start_minimized and TrayIcon.available():
        window.restore_window_geometry()
        QTimer.singleShot(0, window._hide_to_tray)
    else:
        window.show()
        window.restore_window_geometry()


def run_ui(screenshot: str | None = None) -> int:
    """Arranca la aplicación Qt."""
    from services import settings as settings_service

    # Los ajustes se leen antes de crear la interfaz: de ahí salen el idioma y
    # las medidas de la ventana.
    settings = settings_service.Settings.load()
    i18n.set_language(settings.language)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui import fonts, qss
    from ui.widgets.main_window import (
        DEFAULT_WINDOW_HEIGHT,
        DEFAULT_WINDOW_WIDTH,
        MIN_WINDOW_HEIGHT,
        MIN_WINDOW_WIDTH,
        MainWindow,
    )

    _install_exception_hook()
    _install_thread_exception_hook()

    app = QApplication(sys.argv)
    app.setApplicationName("BlenderManager")
    app.setApplicationVersion(updater.app_version())
    # En Wayland/KDE el compositor empareja la ventana con el .desktop por este
    # nombre; sin él, la barra de tareas no encuentra el icono.
    app.setDesktopFileName("blendermanager")

    # Una sola instancia por usuario: si ya hay una abierta, se le pide que
    # vuelva al frente y esta se va sin crear ventana. Se salta en los modos que
    # no abren la interfaz normal (``--screenshot``) y cuando nos relanza el
    # auto-update: el proceso nuevo arranca antes de que cierre el viejo, así
    # que pedirle el turno al viejo dejaría la app sin ninguna instancia viva.
    from ui.single_instance import SingleInstance

    single = SingleInstance(app)
    if not screenshot and not os.environ.get(updater.RELAUNCH_ENV):
        if single.notify_existing():
            return 0
    # Se escucha ANTES de construir la ventana: así una segunda apertura muy
    # seguida no se cuela en el hueco entre "no había nadie" y "ya escucho". El
    # callback mira la lista porque la ventana todavía no existe.
    window_ref: list = []
    single.listen(lambda: window_ref and window_ref[0]._restore_from_tray())

    # Icono de la ventana y de la barra de tareas. Sin esto, el marco y el dock
    # enseñan el icono genérico de Qt.
    from PySide6.QtGui import QIcon

    from paths import ASSETS_DIR

    icon = QIcon(str(ASSETS_DIR / "images" / "app_icon.png"))
    if not icon.isNull():
        app.setWindowIcon(icon)

    fonts.load()
    # La familia del texto va como fuente de la aplicación, no en el QSS: una
    # regla global ``font-family`` pisaría el ``setFont`` de los iconos.
    fonts.apply_ui_font(app)
    app.setStyleSheet(qss.build_qss())

    window = MainWindow()
    # A partir de aquí, una segunda apertura ya puede traer esta ventana al
    # frente (el callback de ``single.listen`` mira esta lista).
    window_ref.append(window)
    window.resize(max(MIN_WINDOW_WIDTH, settings.window_width or DEFAULT_WINDOW_WIDTH),
                  max(MIN_WINDOW_HEIGHT, settings.window_height or DEFAULT_WINDOW_HEIGHT))
    _start_window(window, settings.start_minimized)

    # Restos de la sesión anterior, con retardo para no retrasar el arranque.
    QTimer.singleShot(600, lambda: _clean_previous_session(settings))

    if screenshot:
        def grab():
            window.grab().save(screenshot)
            app.quit()

        QTimer.singleShot(6000, grab)
    return app.exec()


def _parser() -> argparse.ArgumentParser:
    """Los argumentos de la línea de órdenes.

    La ayuda va en inglés, como el resto de textos que no pasan por ``tr()``;
    los de uso interno lo dicen para que nadie los use a mano.
    """
    parser = argparse.ArgumentParser(description="Blender Manager")
    parser.add_argument("--smoke", action="store_true",
                        help="List Blender versions without opening the UI")
    parser.add_argument("--debug", action="store_true",
                        help="If the app crashes, write the Python stack to "
                             "the log file")
    parser.add_argument("--screenshot", metavar="PATH",
                        help="Save a screenshot after startup and quit")
    parser.add_argument("--apply-update", nargs=2, metavar=("APP_DIR", "PID"),
                        help="Internal: apply an update that was already "
                             "downloaded")
    parser.add_argument("--grant-access", metavar="FOLDER",
                        help="Internal: create the folder and grant write "
                             "access to it (Windows, through UAC)")
    parser.add_argument("--for-user", metavar="ACCOUNT",
                        help="Internal: account that asked for --grant-access")
    return parser


def main() -> None:
    """Punto de entrada: lee los argumentos y hace lo que toque."""
    args = _parser().parse_args()

    if args.grant_access:
        # Segundo proceso, ya elevado: concede permiso y termina sin abrir la
        # interfaz (lo lanza services.elevate desde la app normal).
        from services import elevate

        sys.exit(0 if elevate.grant_write(args.grant_access,
                                          args.for_user) else 1)

    if args.apply_update:
        updater.apply_update(args.apply_update[0], args.apply_update[1])
        return

    if args.smoke:
        sys.exit(smoke())

    if args.debug:
        _enable_crash_log()
    sys.exit(run_ui(screenshot=args.screenshot))


if __name__ == "__main__":
    main()
