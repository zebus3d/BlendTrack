"""Preparación común de los tests.

Aquí solo se hace lo que tiene que pasar una vez para toda la suite: poner
``src/`` en el ``sys.path`` y silenciar el registro en fichero.
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _apartar_log() -> None:
    """Que los tests NO escriban en el log real del usuario.

    ``services.downloader.log`` es la herramienta de diagnóstico de la
    aplicación (``~/.cache/blendtrack/blendtrack.log``) y acababa con
    basura de cada ejecución de los tests: rutas ``/tmp/tmpXXXX``, un
    ``git pull failed: boom`` de un test que simula un fallo... Así, cuando de
    verdad hay que mirar el log para ayudar a alguien, no se sabe qué es real.

    Antes se sustituía el nombre ``log`` módulo a módulo (cada uno hace
    ``from services.downloader import log``) y se escapaban los que no
    estaban en la lista (forks, sources, macos_dmg...). Ahora se cambia
    **adónde** escribe: ``log`` busca la carpeta con ``cache_dir()`` al
    llamarse, así que basta con apuntar ese nombre a un temporal.
    """
    import atexit
    import shutil
    import tempfile

    from services import downloader

    carpeta = Path(tempfile.mkdtemp(prefix="bm-tests-log-"))
    atexit.register(shutil.rmtree, carpeta, ignore_errors=True)
    downloader.cache_dir = lambda: carpeta


_apartar_log()
