"""El color de la luz de cada versión y lo que se le deja a una tarjeta.

Lo comparten la rejilla, la lista y la galería: el color dice lo mismo en las
tres (naranja las LTS, azul las estables, violeta las que están en pruebas; cada
fork con el suyo), y las tarjetas lo leen al resaltarse con el ratón o con el
teclado (``ui/widgets/motion.py`` y ``ui/widgets/tilt.py``). Estaba dentro de
``build_lists``; salió aquí cuando ese módulo llegó al tope de líneas.
"""

from services import channels
from ui import theme as t


def glow_color(item, build_type: str) -> str:
    """El color de la luz de una versión: halo, brillo y fondo de galería.

    Naranja las LTS (el mismo con el que la tarjeta escribe "LTS"), azul las
    estables y violeta las que están en pruebas: diarias, alfas, beta y
    experimentales. Los forks llevan su propio color (turquesa Bforartists,
    magenta UPBGE), para distinguirlos de un vistazo. La misma regla en la
    rejilla, la lista y la galería, para que el color diga lo mismo en las tres.
    """
    if build_type == channels.TYPE_BFORARTISTS:
        return t.FORK_BFORARTISTS
    if build_type == channels.TYPE_UPBGE:
        return t.FORK_UPBGE
    if build_type == channels.TYPE_LTS:
        return t.WARNING
    if build_type == channels.TYPE_STABLE:
        return t.ACCENT
    if build_type in (channels.TYPE_DAILY, channels.TYPE_EXPERIMENTAL):
        return t.TESTING
    risk = getattr(item, "risk", "") or "stable"
    return t.ACCENT if risk == "stable" else t.TESTING


def prepare_list_card(card, glow_colour: str, tick) -> None:
    """Deja una fila de lista lista para resaltarse, como una tarjeta de rejilla.

    El halo de color por tipo de versión (``glow_color``) y el "tic" al pasar
    por encima (``hover_tick``). Son los mismos datos que usa la rejilla
    (``prepare_grid_card``); así la lista, que antes no tenía ningún efecto, se
    resalta igual.
    """
    card.glow_color = glow_colour
    card.hover_tick = tick
