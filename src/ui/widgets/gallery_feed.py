"""Lo que la ventana le da a la galería: sus cartas, sus splashes y su estilo.

La galería (``gallery.py``) pinta lo que le pasan; aquí se decide qué le
pasan. Cada tarjeta de rejilla se envuelve en la carta grande con el splash de
su serie (de la caché, del ejecutable instalado o de la red, en un hilo), y se
reparten a las dos galerías el estilo, el láser y el "tic"/"clac" al pasar de
carta.

Es un **mixin** de ``MainWindow`` a través de ``BuildListsMixin``, que hereda
de él: las rejillas piden aquí la carta grande de cada tarjeta, y el estado
(ajustes, instaladas, las dos galerías) es de la ventana.
"""

import threading

from services import sound
from services import splash as splash_service
from ui.widgets import gallery
from ui.widgets.cards import logo_path
from ui.widgets.gallery_card import GalleryCard


class GalleryFeedMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    def _gallery_tick(self) -> None:
        """El "tic" de pasar de carta, si el usuario no lo ha apagado."""
        if self.settings.ui_sounds:
            sound.play_tick()

    def _gallery_lock(self) -> None:
        """El "clac" de una carta que se queda en el centro."""
        if self.settings.ui_sounds:
            sound.play_lock()

    def set_laser_pointer(self, on: bool) -> None:
        """El puntero láser de las galerías, encendido o no (y recordado)."""
        self.settings.laser_pointer = bool(on)
        self.settings.save()
        self.store_gallery.set_laser(bool(on))
        self.installed_gallery.set_laser(bool(on))

    def set_gallery_style(self, style: str) -> None:
        """Cambia el estilo de las dos galerías y lo recuerda.

        Se pide desde la propia galería (el botón de la esquina o las teclas
        1-4): es una forma de ver, como rejilla/lista/galería, no un ajuste.
        """
        if style not in gallery.STYLES:
            return
        self.settings.gallery_style = style
        self.settings.save()
        self.store_gallery.set_style(style)
        self.installed_gallery.set_style(style)

    def _gallery_item(self, card, item, key: str, subtitle: str,
                      build_type: str, rows, installed: bool):
        """Envuelve una tarjeta de rejilla en la grande de la galería.

        ``build_type`` es el de ``services.channels`` (``type_of_build`` en la
        tienda, ``type_of_installed`` en Local): el color de la versión sale de
        ahí, naranja en las LTS y el acento en el resto, como en las tarjetas.
        El splash se pone si ya está en la caché; si no, se pide en un hilo y
        llega por ``splash_ready``.
        """
        color = self._glow_color(item, build_type)
        logo = str(logo_path(item.fork))
        series = splash_service.series_for(item.version, item.branch or "",
                                           getattr(item, "risk", "") or "",
                                           item.fork)
        cached = splash_service.cached(series)
        big = GalleryCard(card, rows, color, logo, installed)
        big.glow_color = color
        if cached is not None:
            big.set_splash(str(cached))
        elif series and (splash_service.needs_fetch(series)
                         or self._splash_executable(series)):
            self._fetch_splash(series)
        return gallery.GalleryItem(
            card=big, key=key, title=f"{item.fork_name} {item.version}",
            subtitle=subtitle, color=color, logo=logo, series=series,
            splash=str(cached) if cached is not None else "")

    def _splash_executable(self, series: str):
        """El ejecutable de alguna instalación de esa serie, si la hay.

        Si la versión (o otra de su serie, que comparten splash) está
        instalada, el splash se saca de su binario y no hace falta la red.
        """
        for entry in self.installed:
            if entry.executable and splash_service.series_for(
                    entry.version, entry.branch or "", entry.risk or "",
                    entry.fork) == series:
                return entry.executable
        return None

    def _fetch_splash(self, series: str) -> None:
        """Consigue el splash de una serie en un hilo (una vez por serie):
        del ejecutable instalado si lo hay, y si no, de la red."""
        if series in self._splash_pending:
            return
        self._splash_pending.add(series)
        executable = self._splash_executable(series)

        def worker():
            path = (splash_service.extract_from(series, executable)
                    if executable else None)
            if path is None and splash_service.needs_fetch(series):
                path = splash_service.fetch(series)
            if path is None:
                return
            try:
                self.splash_ready.emit(series, str(path))
            except RuntimeError:
                # La ventana se cerró mientras se bajaba: ya no hay a quién
                # avisar, y el fichero queda en la caché para la próxima vez.
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _on_splash_ready(self, series: str, path: str) -> None:
        self._splash_pending.discard(series)
        self.store_gallery.set_splash(series, path)
        self.installed_gallery.set_splash(series, path)
