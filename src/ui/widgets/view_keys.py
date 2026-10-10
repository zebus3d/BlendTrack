"""Las teclas de número saltan de sección (Local, Nube, Recientes...).

Es un **mixin** de ``MainWindow``: los atajos no dependen de ningún widget, sino
de la ventana entera, así que el manejador es suyo (``keyPressEvent``).

Por qué no van como ``QShortcut``: un atajo tiene prioridad sobre el control con
el foco, así que le robaría los dígitos al buscador. Aquí solo llegan las teclas
que ningún control aceptó —el ``QLineEdit`` se queda las suyas—, y por eso se
puede seguir escribiendo "4.2" en el buscador. Para que los dígitos no se queden
en él nada más abrir, el buscador **no toma el foco solo** (`Qt.ClickFocus`):
solo cuando el usuario hace clic.

Con la **M** se silencia o se devuelve el sonido (el mismo interruptor del botón
de altavoz) en las listas. Los atajos valen en todas las vistas, galería
incluida (que ya no usa los números para el estilo: eso está en su botón de la
esquina).
"""

from PySide6.QtCore import Qt


class ViewKeysMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    # Teclas de número: atajos a las secciones, en el orden de la barra lateral.
    VIEW_KEYS = {
        Qt.Key_1: "installed", Qt.Key_2: "store", Qt.Key_3: "recent",
        Qt.Key_4: "addons", Qt.Key_5: "migrate", Qt.Key_6: "settings",
    }

    def keyPressEvent(self, event):
        """Las teclas de número saltan de sección (ver ``VIEW_KEYS``), y la M
        silencia o devuelve el sonido en las listas.

        Se hace aquí y no con ``QShortcut`` a propósito: un atajo tiene
        prioridad sobre el control con el foco y le robaría las teclas al
        buscador. Este método solo recibe las teclas que ningún control aceptó
        (el ``QLineEdit`` se queda las suyas), así que escribir "4.2" o una "m"
        en el buscador sigue funcionando. Valen en todas las vistas, galería
        incluida.
        """
        target = self.VIEW_KEYS.get(event.key())
        blocked = Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier
        if not (event.modifiers() & blocked):
            if target is not None:
                self.set_view(target)
                return
            if event.key() == Qt.Key_M and self.view in ("store", "installed"):
                self.set_ui_sounds(not self.settings.ui_sounds)
                return
        super().keyPressEvent(event)
