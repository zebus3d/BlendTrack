"""Las flechas mueven la tarjeta activa de la rejilla o la fila de la lista.

En modo rejilla, arriba/abajo/izquierda/derecha pasan el resalte (el mismo que
levanta el ratón al pasar por encima de una tarjeta) a la vecina de la fila o
la columna; en modo lista, arriba y abajo mueven la fila (izquierda y derecha no
tienen vecina con una sola columna). No es el foco de teclado de Qt —las
tarjetas no son focusables—: es el "hover" movido a mano, para recorrer la
lista sin soltar el teclado. El punto de partida es la **tarjeta activa**, la
que dejó el último movimiento de flechas o el ratón al pasar por encima.

**El ratón quieto no manda.** Al moverse por teclado, ``ensureWidgetVisible``
desplaza la rejilla y el cursor, que no se ha movido, acaba encima de otra
tarjeta: su ``enterEvent`` le quitaba el resalte a la que había elegido el
teclado. Por eso, tras una flecha se **desarma** el ratón (se ignoran sus
``Enter``/``Leave``) hasta que haya un ``MouseMove`` de verdad; entonces el
ratón vuelve a mandar y el resalte pasa a la tarjeta que tenga debajo.

Es un **mixin** de ``MainWindow``: necesita la rejilla que está delante, cuántas
columnas tiene y su scroll. El filtro de eventos se pone solo mientras la
rejilla está a la vista (como el de la galería), para no pasar por Python cada
evento de la aplicación. Fuera de la rejilla las flechas no hacen nada: no
cambian de sección (el botón lateral las ignora, ver ``SideButton``).
"""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (QAbstractSlider, QAbstractSpinBox, QApplication,
                               QComboBox, QLineEdit, QWidget)

# Las cuatro flechas que mueve la rejilla.
ARROWS = (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down)


class GridKeysMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    def _sync_grid_keys(self) -> None:
        """Pone o quita el filtro de flechas según se vea la lista o no.

        Vale para la rejilla (cuatro direcciones) y para la lista (arriba y
        abajo; izquierda y derecha no tienen vecina con una sola columna).
        """
        if not hasattr(self, "_grid_active"):
            self._grid_active = {"store": None, "installed": None}
        on = (self.view in ("store", "installed")
              and self.layout_mode in ("grid", "list"))
        if on == getattr(self, "_grid_keys_on", False):
            return
        app = QApplication.instance()
        if on:
            self._grid_hover_armed = True
            app.installEventFilter(self)
        else:
            app.removeEventFilter(self)
            self._clear_grid_hover()
        self._grid_keys_on = on

    def eventFilter(self, watched, event):
        """Los eventos de la rejilla, estén donde estén el foco y el ratón.

        Primero el arma del ratón (ver el docstring del módulo): tras una flecha
        se ignoran sus ``Enter``/``Leave`` y se vuelven a atender al primer
        ``MouseMove``. Después, las flechas: se respeta a quien de verdad las
        necesita (buscador, desplegables, campos numéricos y deslizadores, el
        del zoom por ejemplo) y a los diálogos y desplegables abiertos.
        """
        if getattr(self, "_grid_keys_on", False):
            handled = self._grid_mouse_events(watched, event)
            if handled is not None:
                return handled
        if (event.type() != QEvent.KeyPress or event.key() not in ARROWS
                or not getattr(self, "_grid_keys_on", False)
                or not self.isVisible()
                or QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None
                or not self.window().isActiveWindow()):
            return False
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QComboBox, QAbstractSpinBox,
                              QAbstractSlider)):
            return False
        self._move_grid_active(event.key())
        return True

    def _grid_mouse_events(self, watched, event):
        """El arma del ratón. Devuelve ``True``/``False`` si consume el evento.

        ``None`` es "no es cosa mía": que siga el camino normal (las flechas).
        Cuando el ratón manda (armado), entrar en una tarjeta apunta cuál es la
        activa: así la siguiente flecha sigue desde donde dejó el ratón.
        """
        if event.type() in (QEvent.MouseMove, QEvent.MouseButtonPress):
            if not self._grid_hover_armed:
                self._grid_hover_armed = True
                self._apply_mouse_hover(event.globalPosition().toPoint())
            return False
        card = (watched if isinstance(watched, QWidget)
                and hasattr(watched, "set_hover") else None)
        if event.type() == QEvent.Enter:
            if not self._grid_hover_armed:
                # El ratón no se ha movido desde la última flecha: no le quita
                # el resalte a la tarjeta activa (la rejilla se desplazó).
                return card is not None
            if card is not None:
                self._remember_hover(card)
            return None
        if event.type() == QEvent.Leave and not self._grid_hover_armed:
            return card is not None
        return None

    def _remember_hover(self, card) -> None:
        """Apunta como activa la tarjeta que tiene el ratón encima."""
        kind = "installed" if self.view == "installed" else "store"
        grid, _scroll = self._grid_for(kind)
        for index, (_row, _col, widget) in enumerate(self._entries_of(grid)):
            if widget is card:
                self._grid_active[kind] = index
                return

    def _grid_for(self, kind: str):
        if kind == "installed":
            return self.installed_grid, self.installed_scroll
        return self.store_grid, self.store_scroll

    @staticmethod
    def _entries_of(grid) -> list:
        """Las tarjetas de una rejilla, ordenadas por (fila, columna).

        Se leen del propio ``QGridLayout`` en vez de guardarlas al construirlo:
        así no hay que repetir el dato y no puede quedarse viejo cuando la
        rejilla se rehace (al filtrar, al cambiar el zoom...). Las tarjetas son
        las que saben resaltarse (``set_hover``); los avisos de "no hay
        versiones" se saltan.
        """
        entries = []
        for index in range(grid.count()):
            widget = grid.itemAt(index).widget()
            if widget is None or not hasattr(widget, "set_hover"):
                continue
            row, col, _rs, _cs = grid.getItemPosition(index)
            entries.append((row, col, widget))
        entries.sort(key=lambda entry: (entry[0], entry[1]))
        return entries

    def _move_grid_active(self, key_pressed) -> None:
        """Pasa el resalte a la tarjeta vecina de la que está activa.

        La activa es la última que tocó el teclado **o el ratón**: si el cursor
        está sobre una tarjeta y se pulsa una flecha, se sigue desde ahí. Si no
        hay ninguna activa todavía, la primera pulsación enciende la primera.
        """
        self._grid_hover_armed = False
        kind = "installed" if self.view == "installed" else "store"
        grid, scroll = self._grid_for(kind)
        entries = self._entries_of(grid)
        if not entries:
            return
        active = self._grid_active.get(kind)
        if active is None or not 0 <= active < len(entries):
            self._apply_grid_hover(kind, scroll, entries, 0)
            return
        row, col = entries[active][0], entries[active][1]
        target = self._grid_neighbour(entries, row, col, key_pressed)
        if target is not None:
            self._apply_grid_hover(kind, scroll, entries, target)

    @staticmethod
    def _grid_neighbour(entries, row: int, col: int, key_pressed):
        """Índice de la tarjeta vecina en esa dirección, o ``None`` si no hay.

        Se busca por la celda de la rejilla (misma fila y columna ±1), no por
        el orden de la lista: en la última columna, la de la derecha es la
        primera de la fila siguiente, y eso no es "la vecina de al lado".
        """
        step = {Qt.Key_Left: (0, -1), Qt.Key_Right: (0, 1),
                Qt.Key_Up: (-1, 0), Qt.Key_Down: (1, 0)}[key_pressed]
        want = (row + step[0], col + step[1])
        for index, (r, c, _widget) in enumerate(entries):
            if (r, c) == want:
                return index
        return None

    def _apply_grid_hover(self, kind: str, scroll, entries, index: int) -> None:
        previous = self._grid_active.get(kind)
        if previous is not None and 0 <= previous < len(entries):
            entries[previous][2].set_hover(False)
        self._grid_active[kind] = index
        card = entries[index][2]
        # El centro de la tarjeta como "ratón": se eleva y brilla, pero no gira
        # hacia ningún lado (no hay cursor al que mirar).
        card.set_hover(True, card.mapToGlobal(card.rect().center()))
        scroll.ensureWidgetVisible(card, 24, 24)

    def _unhover_active(self) -> None:
        """Quita el resalte de la tarjeta activa, sin olvidar cuál era."""
        for kind in ("store", "installed"):
            active = self._grid_active.get(kind)
            if active is None:
                continue
            grid, _scroll = self._grid_for(kind)
            entries = self._entries_of(grid)
            if 0 <= active < len(entries):
                entries[active][2].set_hover(False)

    def _clear_grid_hover(self) -> None:
        """Quita el resalte y olvida la tarjeta activa (al salir de la rejilla)."""
        self._unhover_active()
        for kind in ("store", "installed"):
            self._grid_active[kind] = None

    def _apply_mouse_hover(self, global_pos) -> None:
        """El ratón vuelve a mandar: pasa el resalte a la tarjeta de debajo.

        Sin esto, la tarjeta que el cursor ya tenía debajo no volvía a
        resaltarse al mover el ratón (su ``Enter`` se ignoró mientras estaba
        desarmado y Qt no lo repite). Se recuerda su índice para que la
        siguiente flecha siga desde ahí.
        """
        kind = "installed" if self.view == "installed" else "store"
        grid, _scroll = self._grid_for(kind)
        entries = self._entries_of(grid)
        card = self._hover_card_at(global_pos)
        self._unhover_active()
        self._grid_active[kind] = None
        for index, (_row, _col, widget) in enumerate(entries):
            if widget is card:
                self._grid_active[kind] = index
                break
        if card is not None:
            card.set_hover(True)

    @staticmethod
    def _hover_card_at(global_pos):
        """La tarjeta bajo el cursor (o ``None``), subiendo por los hijos."""
        widget = QApplication.widgetAt(global_pos)
        while widget is not None:
            if hasattr(widget, "set_hover"):
                return widget
            widget = widget.parentWidget()
        return None
