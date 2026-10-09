"""Lo que comparten todas las peticiones HTTP de la aplicación.

De momento es una sola cosa: **cómo nos presentamos** (``User-Agent``). Hace
falta mandar uno propio porque varios servidores rechazan el de ``urllib``
(``Python-urllib/3.x``): ``download.blender.org`` contesta 403 y la API de
GitHub exige uno. Antes había cuatro copias de la cadena repartidas por
``api``, ``downloader``, ``forks`` y ``sources``, cada una con su versión
(alguna, una ``0.2`` de hace mucho). Una sola, con la versión real de la
aplicación, deja ver en los registros de los servidores qué versión pregunta.

``services/updater.py`` tiene la suya propia, ya construida igual.

No importa Qt.
"""

from version import __version__

PROJECT_URL = "https://github.com/zebus3d/BlenderManager"

USER_AGENT = f"BlenderManager/{__version__} (+{PROJECT_URL})"
