"""Glifos de la fuente de iconos (Font Awesome Free).

Cada constante es un carácter Unicode de la zona privada de la fuente. Se
pintan como el texto de un widget que lleva la fuente de iconos:
``label.setText(icons.STORE)`` + ``label.setFont(fonts.icon_font())``; o como
``QIcon`` con ``fonts.glyph_icon`` cuando van junto a texto normal. Su tamaño
lo pone el QSS (ver ``fonts.icon_font``).
"""

STORE = "\uf0ed"      # nube con flecha de descarga
INSTALLED = "\uf108"  # ordenador de sobremesa
SETTINGS = "\uf013"
LAUNCH = "\uf04b"
DELETE = "\uf1f8"
REFRESH = "\uf021"
SEARCH = "\uf002"
FOLDER = "\uf07b"
GRID = "\uf00a"
LIST = "\uf0ca"
INFO = "\uf129"     # "i" suelta (el círculo azul lo pinta el QSS)
STAR = "\uf005"     # estrella de favorito
DOWNLOAD = "\uf019"  # flecha hacia abajo (descargar)
MIGRATE = "\uf362"  # flechas izquierda-derecha (migrar entre versiones)
ARROW_RIGHT = "\uf061"  # flecha hacia la derecha (sentido de la migración)
LOCK = "\uf023"        # candado cerrado (la app no escribe ahí)
LOCK_OPEN = "\uf3c1"   # candado abierto (se puede descargar ahi)
CLOCK = "\uf017"       # reloj (ficheros recientes)
ELLIPSIS = "\uf141"    # tres puntos (más opciones)
PUZZLE = "\uf12e"      # pieza de puzzle (addons)
TERMINAL = "\uf120"    # terminal (lanzar con consola)
