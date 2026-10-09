"""Piezas que comparten las tres pestañas de Migración.

Los textos de estado de un addon, el asa para estirar los paneles, el área de
lista con su lienzo hundido y los bloques de los diálogos de resultado. Viven
aquí porque las usan las tres pestañas; lo que solo usa una está en su módulo.
"""

from pathlib import Path

from PySide6.QtWidgets import QScrollArea

from i18n import tr
from model.build import fork_label, fork_series, version_tuple
from services import blender_config as bc
from services import blender_prefs as bprefs
from services import blender_runner
from services.downloader import log as download_log
from ui import icons
from ui import theme as t
from ui.fonts import glyph_icon
from ui.widgets import dialogs
from ui.widgets.buttons import CardButton
from ui.widgets.layouts import list_scroll, repolish


# Alto fijo de una fila: las dos columnas tienen que cuadrar una con otra.
ROW_HEIGHT = 48

# Alto máximo del área con las claves cambiadas. Un Blender puede tener cientos
# de ajustes distintos de fábrica: sin tope, la tarjeta crece sin fin, empuja
# los botones fuera de la pantalla y no se puede leer nada. Con esto caben unas
# ocho filas y el resto se ve con la barra de scroll, con los botones siempre a
# la vista.
DETAIL_SCROLL_HEIGHT = 260

# Alto máximo de la lista de guardados (mismo motivo: caben unos cuatro y a
# partir de ahí se baja con la barra).
SNAPSHOT_SCROLL_HEIGHT = 280

# Alto de los paneles con lista: el del contenido, con estos topes. El usuario
# los estira desde la esquinita de la tarjeta (``layouts.CornerGrip``).
DETAIL_MIN_HEIGHT = 60
SNAPSHOT_MIN_HEIGHT = 60

# Cuántas filas enteras tienen que caber como mínimo en la lista de claves
# (más el título de sección). Es un suelo flojo a propósito: lo que hacía que
# los botones pareciesen metidos dentro del listado no era el tamaño, sino que
# el layout encogía la lista por debajo de su mínimo cuando la ventana se
# quedaba corta (lo arregla ``FittedList``). Se deja el suelo solo para que
# encoger del todo siga enseñando una lista y no una rendija.
DETAIL_MIN_ROWS = 2

# Lo mismo para la lista de guardados. Ahí una fila es una tarjeta entera (con
# su fecha, su resumen y sus botones), así que con una basta: media tarjeta
# cortada contra el borde del área se lee como un fallo de pintado.
SNAPSHOT_MIN_ROWS = 1

# objectName del QSS y texto del estado de compatibilidad.
_STATUS = {
    bc.OK: ("Success", "Compatible"),
    bc.WARN: ("Warning", "Review"),
    bc.BLOCKED: ("Danger", "Not compatible"),
}


class LatestJob:
    """Un trabajo en segundo plano del que solo vale la **última** respuesta.

    Las pestañas de Migración arrancan Blender en un hilo y el resultado vuelve
    por una señal. Mientras tanto el usuario puede cambiar de versión, o lanzar
    otra operación que responde por otra señal. Con una sola bandera booleana
    compartida, la primera respuesta en llegar "gastaba" la bandera de la otra
    y una de las dos se perdía (la lista se quedaba en "Leyendo ajustes..." para
    siempre), y un resultado de la versión anterior se pintaba sobre la nueva.

    Aquí cada operación lleva su propio objeto, y cada arranque, un número de
    **ticket** que el hilo devuelve en su ``payload``:

    * ``start(key)`` abre el trabajo y da el ticket. ``key`` dice *para qué* se
      lanzó (la versión, por ejemplo) y se guarda en ``self.key``.
    * ``finish(payload)`` es True solo si esa respuesta es la del último
      ``start`` y el trabajo seguía abierto; entonces lo cierra. Lo demás (un
      ticket viejo, o una señal emitida desde fuera sin ticket) se ignora.
      ``owns(payload)`` hace la misma comprobación sin cerrarlo, para un hilo
      que responde en dos señales.
    * ``invalidate()`` deja el trabajo en marcha pero avisa de que lo que
      traiga ya no sirve (``key`` pasa a ``None``): quien lo recibe compara
      ``key`` con lo que hay ahora y, si no cuadra, lo relanza.

    No se lanza un segundo hilo mientras hay uno en marcha (``running``): cada
    uno arranca Blender, y cambiar el desplegable cinco veces seguidas no debe
    dejar cinco Blenders trabajando a la vez.
    """

    def __init__(self):
        self._ticket = 0
        self.running = False
        self.key = None

    def start(self, key=None) -> int:
        """Abre el trabajo para ``key`` y devuelve el ticket que lo identifica."""
        self._ticket += 1
        self.running = True
        self.key = key
        return self._ticket

    def owns(self, payload) -> bool:
        """True si ``payload`` es la respuesta del trabajo en marcha."""
        return self.running and payload.get("ticket") == self._ticket

    def finish(self, payload) -> bool:
        """True (y cierra el trabajo) si ``payload`` es la respuesta esperada."""
        if not self.owns(payload):
            return False
        self.running = False
        return True

    def invalidate(self) -> None:
        """Lo que está en marcha ya no vale: su ``key`` no cuadrará con nada."""
        self.key = None


def newest_per_series(installed) -> list:
    """Una instalada por ``(fork, serie)``, de la más nueva a la más vieja.

    Las versiones de una misma serie comparten carpeta de configuración (dos
    diarias de 5.3 escriben en ``~/.config/blender/5.3``), así que en los
    selectores de Migración y de Add-ons basta con una por serie: la más
    nueva. El **fork** entra en la clave porque Bforartists 4.5 y Blender 4.5
    tienen cada uno su propia carpeta (y UPBGE usa la serie de Blender a la que
    equivale, ``fork_series``): juntarlos dejaba sin poder elegir uno de los dos.

    Se ordena aquí en vez de fiarse del orden de entrada: de ahí salen los
    valores por defecto de los desplegables.
    """
    seen = set()
    choices = []
    for entry in sorted(installed or [],
                        key=lambda item: version_tuple(
                            getattr(item, "version", "") or ""),
                        reverse=True):
        fork = getattr(entry, "fork", "") or ""
        series = fork_series(fork, getattr(entry, "version", "") or "")
        if not series or (fork, series) in seen:
            continue
        seen.add((fork, series))
        choices.append(entry)
    return choices


def choice_label(entry) -> str:
    """Texto de una instalada en un selector: la versión y, si es un fork, cuál.

    El nombre de la carpeta no aporta nada (repetía el número y la ruta ya va
    debajo), pero el del fork sí: sin él, "4.5.0" podía ser Bforartists o
    Blender.
    """
    version = getattr(entry, "version", "") or getattr(entry, "name", "")
    fork = getattr(entry, "fork", "") or ""
    return f"{fork_label(fork)} {version}" if fork else version


def blender_is_running(executable=None) -> bool:
    """``blender_runner.is_running`` sin que un fallo tumbe la interfaz.

    Si no se puede averiguar (``/proc`` ilegible, ``pgrep`` que falla) se
    responde **False** y se deja constancia en el registro: bloquear para
    siempre una acción porque la detección falló sería peor que dejarla
    pasar, y en Windows la detección ni existe (devuelve siempre False). Lo
    que no puede pasar es que el fallo sea invisible: sin la línea del log,
    "no me avisó de que Blender estaba abierto" no tendría explicación.
    """
    try:
        return blender_runner.is_running(executable)
    except Exception as error:  # noqa: BLE001 - ver el docstring
        download_log(f"could not tell whether Blender is running: {error}")
        return False


def blocked_by_running(parent) -> bool:
    """True si hay **cualquier** Blender abierto (y lo avisa); bloquea la acción.

    Es la comprobación de todo lo que escribe en la configuración de Blender
    (Migración y el gestor de Add-ons). No se mira solo la versión de destino:
    dos builds de la misma serie (5.2.0 y 5.2.2) comparten carpeta, así que un
    Blender abierto que no es el elegido también pisaría el cambio al cerrarse
    (Blender guarda sus preferencias al salir). Como no hay forma fiable de
    saber qué serie tiene abierta cada proceso, se pide cerrarlos todos.
    """
    if not blender_is_running(None):
        return False
    dialogs.show_info(parent, tr("Blender is running"), tr(
        "Close every Blender window before continuing: Blender saves its "
        "preferences when it quits and would overwrite the changes."))
    return True


def _entry_program(entry) -> str:
    """Nombre del programa de una instalada: "Blender", "Bforartists" o "UPBGE".

    Los textos dicen "{program} {version}" y no "Blender {version}": con los
    forks, "Blender 4.5.0" podía ser en realidad una Bforartists.
    """
    return fork_label(getattr(entry, "fork", "") or "")


def _entry_key(entry) -> tuple:
    """Identidad de una instalada para la migración: ``(fork, versión)``.

    La versión sola no basta: Bforartists 4.5.0 y Blender 4.5.0 tienen el mismo
    número y cada uno su propia carpeta de configuración.
    """
    if entry is None:
        return ("", "")
    return (getattr(entry, "fork", "") or "", getattr(entry, "version", "") or "")


def _reason_text(plan) -> str:
    """Frase que explica el motivo del estado (vacía si todo está bien)."""
    addon = plan.addon
    if plan.reason == bc.REASON_REQUIRES_NEWER:
        return tr("It needs Blender {version} or newer.",
                  version=addon.min_version)
    if plan.reason == bc.REASON_TOO_NEW:
        return tr("It does not support Blender {version} yet.",
                  version=addon.max_version)
    if plan.reason == bc.REASON_PLATFORM:
        return tr("It is not published for this system.")
    if plan.reason == bc.REASON_WHEEL_ABI:
        # Se nombra el paquete culpable y el Python del destino: el texto
        # genérico de antes ("sus dependencias son de otro Python") se leía
        # como si comparásemos las dos versiones de Blender entre sí.
        if not plan.detail:
            # Sin el nombre del paquete (un plan armado a mano) se dice lo
            # mismo en genérico, pero sin fingir que sabemos cuál falla.
            return tr("One of its dependencies has no build for the Python of "
                      "the destination version.")
        if plan.target_python:
            return tr("{package} has no build for Python {python}, which the "
                      "destination version uses.",
                      package=plan.detail, python=plan.target_python)
        return tr("{package} has no build for the Python of the destination "
                  "version.", package=plan.detail)
    if plan.reason == bc.REASON_UNKNOWN_VERSION:
        return tr("It does not state a minimum version.")
    return ""


# Qué hacer con un addon marcado "Review" (el motivo solo no basta: el usuario
# necesita saber cómo comprobarlo). Se elige la recomendación según la causa.
def _review_advice(plan) -> str:
    """Pasos concretos para revisar un addon que no se puede dar por seguro."""
    if plan.reason == bc.REASON_WHEEL_ABI:
        return tr(
            "Copy it and enable it in the destination version. If it fails to "
            "load, ask its author for a build that includes that dependency "
            "for this Python.")
    if plan.reason == bc.REASON_UNKNOWN_VERSION:
        return tr(
            "The add-on does not say which Blender it works with. Copy it and "
            "test its panel or operators in the destination version; if they "
            "fail, leave it disabled.")
    # Aviso sin causa concreta (no debería pasar, pero mejor dar algo que nada).
    return tr("Copy it and test it in the destination version before relying "
              "on it.")


def _status_tooltip(plan) -> str:
    """Tooltip del estado de una fila: motivo y, si toca, qué hacer.

    Sirve para las tres insignias: la verde dice que no hay nada que mirar, la
    amarilla por qué hay que revisarlo y cómo, y la roja por qué no se copia.
    """
    if plan.status == bc.OK:
        return tr("It works with the destination version; it will be copied and "
                  "kept as it is.")
    reason = _reason_text(plan)
    if plan.status == bc.WARN:
        advice = _review_advice(plan)
        return f"{reason}\n\n{advice}" if reason else advice
    # BLOCKED: el motivo de por qué no se puede copiar (ya viene en el texto).
    return reason


def _meta_text(addon) -> str:
    """Línea secundaria de una fila (versión, mínimo declarado y tipo)."""
    parts = []
    if addon.version:
        parts.append(f"v{addon.version}")
    if addon.min_version:
        parts.append(f"min {addon.min_version}")
    parts.append(tr("Extension") if addon.kind == "extension"
                 else tr("Legacy add-on"))
    return "  ·  ".join(parts)


def _destination_text(plan) -> str:
    """Carpeta donde quedará el addon (``user_default/<id>``, ``addons/<x>``).

    Sale de ``plan.destination``, que ya decidió ``baddons.destination_for`` (el
    mismo que usa la copia de verdad): así lo que se enseña y donde se escribe
    no pueden discrepar. Se enseñan los dos últimos tramos, que es lo que
    distingue una extensión de un addon clásico.
    """
    if plan.blocked:
        return tr("(not copied)")
    return "/".join(Path(plan.destination).parts[-2:])


def _accent_button(text: str, tooltip: str, on_click) -> CardButton:
    """Botón primario con la flecha de la migración como icono.

    El glifo va como ``QIcon``: el texto del botón usa la fuente general, así
    que pegar el carácter de la flecha ahí salía como un recuadro.
    """
    button = CardButton(text, variant="accent", tooltip=tooltip)
    # El glifo, en el blanco del texto sobre acento.
    button.setIcon(glyph_icon(icons.ARROW_RIGHT, 13, t.TEXT_SEL))
    button.clicked.connect(on_click)
    return button


def _entry_info(entry) -> tuple:
    """``(executable, version)`` de una instalada, aguantando ``None``.

    Las instaladas de verdad (``InstalledBuild``) siempre traen los dos campos,
    pero esto se llama también con entradas de tests y con el destino vacío, y
    un ``AttributeError`` aquí tumbaría la interfaz entera.
    """
    if entry is None:
        return None, ""
    return getattr(entry, "executable", None), getattr(entry, "version", "")


def _failure_block(title: str, lines, limit: int = 10) -> list:
    """Bloque "título + viñetas" de un diálogo de resultado, recortado.

    Como mucho ``limit`` líneas: un fallo masivo no puede convertir el diálogo
    en un muro de texto. Todos los resúmenes de Migración recortan igual.
    """
    lines = list(lines)
    if not lines:
        return []
    block = ["", title]
    block.extend(f"· {line}" for line in lines[:limit])
    if len(lines) > limit:
        block.append(tr("… and {count} more.", count=len(lines) - limit))
    return block


def _report_lines(summary: str, backed_up: bool, failed, failure_title: str,
                  failure_label) -> list:
    """Monta las líneas del diálogo de resultado de una copia.

    ``failed`` es una lista de ``(elemento, mensaje)`` y ``failure_label``
    extrae de cada elemento su nombre legible. Se listan como mucho diez para
    que un fallo masivo no convierta el diálogo en un muro de texto.
    """
    if not summary:
        return [tr("Nothing was copied.")]
    lines = [summary]
    if backed_up:
        lines.append(tr("What was replaced was kept next to it as a backup."))
    lines.extend(_failure_block(
        failure_title, (f"{failure_label(item)}: {message}"
                        for item, message in failed)))
    return lines


def _section_names(paths) -> list:
    """Nombres legibles de las secciones a las que pertenecen esas rutas RNA."""
    order = {key: index for index, (key, _) in enumerate(bprefs.SECTIONS)}
    positions = {}
    for path in paths:
        key = (path or "").split(".", 1)[0]
        positions.setdefault(key, order.get(key, 99))
    ordered = sorted(positions, key=lambda key: (positions[key], key))
    return [tr(bprefs.section_label(key)) for key in ordered]


def _make_scroll(object_name: str, cap: int, minimum: int) -> QScrollArea:
    """Área de lista, con su alto de partida entre ``minimum`` y ``cap``.

    El alto definitivo lo fija ``FittedList.fit`` cuando hay filas:
    se pone **fijo** a propósito (ver allí el porqué), así que estos dos valores
    solo valen mientras el área está vacía.
    """
    scroll, _ = list_scroll(object_name, object_name + "Body", align_top=False)
    scroll.setMinimumHeight(minimum)
    scroll.setMaximumHeight(cap)
    _sunken_when_scrolling(scroll)
    return scroll


def _sunken_when_scrolling(scroll: QScrollArea) -> None:
    """Hunde el fondo del lienzo mientras la lista no quepa entera.

    Con barra de scroll, el área tiene que leerse como una ventana a algo más
    largo; con el mismo gris de la tarjeta la barra parecía salir de la nada y
    no se veía dónde empieza y acaba lo que se desplaza. El color lo pone el
    QSS (propiedad dinámica ``scrolling``); aquí solo se sigue el rango de la
    barra, que es lo que cambia al llenar la lista, al estirarla con el asa y
    al encoger la ventana, así que no hay que enganchar los tres casos.
    """
    bar = scroll.verticalScrollBar()

    def sync(*_args) -> None:
        value = "true" if bar.maximum() > 0 else "false"
        for widget in (scroll, scroll.widget()):
            if widget is None or widget.property("scrolling") == value:
                continue
            widget.setProperty("scrolling", value)
            repolish(widget)

    bar.rangeChanged.connect(sync)
    sync()
