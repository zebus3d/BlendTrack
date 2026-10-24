"""Actualizar **BlendTrack** (no Blender): comprobar, bajar y aplicar.

La comprobación periódica, el diálogo de versión disponible, el camino del
binario (AppImage, Windows, macOS) y el del modo fuente (``git pull`` y
reinicio). Lo de Blender está en ``downloads.py``; esto es la app misma.

Es un **mixin** de ``MainWindow``: necesita su barra de progreso, sus diálogos
y poder cerrarla para reiniciar. La lógica sin interfaz está en
``services/updater.py``.
"""

import sys
import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from i18n import tr
from services import updater
from services.downloader import log as download_log
from ui.widgets.dialogs import AppDialog, update_available
from ui.widgets.shell import WorkerBridge, download_reason


class UpdateFlowMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    # El puente de la descarga de la actualización (ver ``_do_update``).
    _update_bridge = None

    def set_auto_update(self, active: bool) -> None:
        """Guarda si hay que comprobar actualizaciones al arrancar.

        Es independiente del chequeo periódico: apagarlo no lo toca.
        """
        self.auto_update = active
        self.settings.auto_update = active
        self.settings.save()

    def set_periodic_update(self, active: bool) -> None:
        """Apaga/enciende el chequeo periódico (independiente del de arranque)."""
        self.periodic_update = active
        self.settings.periodic_update = active
        self.settings.save()
        if hasattr(self, "update_interval_combo"):
            self.update_interval_combo.setEnabled(active)
        self._apply_update_timer()

    def _apply_update_timer(self) -> None:
        """(Re)programa el chequeo periódico de la app según los ajustes."""
        minutes = self.settings.update_interval_min
        if self.periodic_update and minutes > 0:
            self._update_timer.start(minutes * 60 * 1000)
        else:
            self._update_timer.stop()

    def _periodic_update_check(self) -> None:
        """Chequeo automático de la app cada ``update_interval_min`` minutos.

        No interrumpe: si hay una descarga en curso o un diálogo abierto se salta
        esta vuelta, y si ya se avisó de una versión en esta sesión tampoco la
        repite (darle a "Más tarde" no puede sacar el aviso cada X minutos).
        """
        if self.downloader.running or self.update_downloader.running:
            return
        if QApplication.activeModalWidget() is not None:
            return
        if self._offered_update_tag:
            return
        self.check_updates(manual=False)

    @staticmethod
    def _interval_text(minutes: int) -> str:
        """Etiqueta del intervalo (el valor guardado van en minutos enteros)."""
        if minutes <= 0:
            return tr("Never")
        if minutes == 1:
            return tr("Every minute")
        if minutes < 60:
            return tr("{count} minutes", count=minutes)
        if minutes == 60:
            return tr("Every hour")
        return tr("Every {count} hours", count=minutes // 60)

    def _on_update_interval_changed(self, index: int) -> None:
        minutes = self.update_interval_combo.itemData(index)
        if minutes is None:
            return
        self.settings.update_interval_min = int(minutes)
        self.settings.save()
        self._apply_update_timer()

    # ------------------------------------------------------------- updates
    def check_updates(self, manual: bool = False) -> None:
        # Modo fuente: lo que corre es el checkout y compararlo con una release
        # no dice nada (una rama de desarrollo va por delante del último tag).
        # Al arrancar no avisamos; si el usuario lo pide a mano, ofrecemos
        # ``git pull``, que es la actualización de verdad. Sin ``.git`` no hay
        # nada que actualizar: tampoco avisamos.
        """Comprueba si hay versión nueva, en un hilo aparte."""
        if not getattr(sys, "frozen", False) and not manual:
            return
        if self._update_checking:
            return
        self._update_checking = True

        def worker():
            tag, assets = updater.latest_release(force=manual) or ("", [])
            self.update_result.emit(tag, assets, manual)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_result(self, tag: str, assets, manual: bool) -> None:
        self._update_checking = False
        if not getattr(sys, "frozen", False):
            if updater.source_root() is not None:
                # Checkout git: sin binario que reemplazar, git pull + reinicio.
                self._show_source_update(tag)
            elif manual and tag:
                # Sin git no hay nada que aplicar: al menos, la release abierta.
                self._show_message(
                    tr("A new version is available: {version}", version=tag), 8)
                updater.open_releases()
            return
        if not tag:
            # La comprobación falló (sin red, TLS, cuota de la API...). Decir
            # aquí "ya tienes la última versión" confunde: es justo lo que hizo
            # pensar que no había actualización cuando sí la había.
            if manual:
                self._show_message(tr("Update check failed"), 5)
            return
        asset_name = updater.asset_for(self.system)
        asset = next((a for a in assets if a["name"] == asset_name), None)
        if asset is None:
            if manual:
                self._show_message(tr("No update for this platform"), 5)
            return
        nueva = updater.is_newer(self.current_version, tag)
        # Queda en el log qué se comparó: si alguien dice "no me avisa", se ve
        # en un segundo si es que iba al día o si la comprobación no llegó.
        download_log(f"update check: instalada={self.current_version} "
                     f"ultima={tag} hay_nueva={nueva}")
        if nueva:
            # El usuario puede haber pedido no volver a saber de esta versión:
            # los chequeos automáticos la callan (el manual la muestra igual).
            silenced = not manual and tag == self.settings.skipped_version
            # Un aviso por versión y sesión: el chequeo periódico comprueba a
            # menudo, pero no puede sacar el diálogo una y otra vez si le diste
            # a "Más tarde". Si sale una versión aún más nueva, sí se avisa.
            if not silenced and (manual or tag != self._offered_update_tag):
                self._offered_update_tag = tag
                self._show_update_available(tag, asset, assets)
        elif manual:
            self._show_message(tr("You already have the latest version ({version}).",
                                 version=self.current_version), 6)

    def _show_source_update(self, tag: str) -> None:
        """Actualización de un checkout en modo fuente: ``git pull`` + reinicio."""
        if not tag:
            self._show_message(tr("Update check failed"), 5)
            return
        if not updater.is_newer(self.current_version, tag):
            self._show_message(tr("You already have the latest version ({version}).",
                                 version=self.current_version), 6)
            return
        message = (tr("A new version is available: {version}", version=tag)
                   + "\n\n"
                   + tr("Running from source: the app will run git pull and restart."))
        dialog = AppDialog(self, tr("Update available"), message)
        later = dialog.add_button(tr("Later"), on_click=dialog.reject,
                                  tooltip=tr("Ask me again another time."))
        update = dialog.add_button(
            tr("Update"), variant="accent",
            tooltip=tr("Run git pull and restart the app."))
        update.clicked.connect(lambda: self._do_source_update(dialog, update, later))
        dialog.exec()

    def _do_source_update(self, dialog, update_btn, later_btn) -> None:
        self._source_dialog = dialog
        update_btn.setEnabled(False)
        later_btn.setEnabled(False)
        dialog.body_label.setText(tr("Updating..."))

        def worker():
            ok, reason = updater.source_update()
            self.source_update_done.emit(ok, reason)

        threading.Thread(target=worker, daemon=True).start()

    def _on_source_update_done(self, ok: bool, reason: str) -> None:
        dialog = getattr(self, "_source_dialog", None)
        if not ok or reason == "up-to-date":
            if not ok and reason == "dirty":
                text = tr("You have local changes. Commit or stash them and try again.")
            elif not ok:
                text = tr("Could not update. Run git pull manually.")
            else:
                text = tr("You already have the latest version.")
            if dialog is not None:
                dialog.body_label.setText(text)
                for button in dialog.findChildren(QPushButton):
                    button.setEnabled(True)
            return
        if dialog is not None:
            dialog.body_label.setText(tr("Restarting..."))
        self._set_status(tr("Restarting..."))
        # El diálogo es modal: hay que cerrarlo para que ``exec()`` devuelva y
        # el cierre de la ventana llegue a terminar el bucle de eventos.
        QTimer.singleShot(800, self, self._restart_from_source)

    def _restart_from_source(self) -> None:
        dialog = getattr(self, "_source_dialog", None)
        # Lo mismo que en ``_apply_update``: el proceso nuevo lee los ajustes
        # antes de que este termine de cerrarse.
        self.save_window_state()
        if not updater.relaunch_source():
            if dialog is not None:
                dialog.body_label.setText(tr("Update downloaded. Restart the app."))
                for button in dialog.findChildren(QPushButton):
                    button.setEnabled(True)
            return
        if dialog is not None:
            dialog.accept()
        # Reiniciar es salir de verdad: no puede quedarse en la bandeja.
        self._quit_app()

    def _show_update_available(self, tag: str, asset, assets) -> None:
        """Ofrece la versión ``tag``; ``assets`` hace falta para su checksum."""
        update_available(self, tag, lambda: self._do_update(asset, assets),
                         on_skip=lambda: self.skip_update_version(tag))

    def skip_update_version(self, tag: str) -> None:
        """No volver a avisar de esta versión en los chequeos automáticos.

        El chequeo manual ("Buscar ahora") la muestra igual, y una versión más
        nueva sí se ofrece: se guarda el tag exacto.
        """
        self.settings.skipped_version = tag
        self.settings.save()
        self._show_message(
            tr("You will not be reminded about {version}.", version=tag), 8)

    def _do_update(self, asset, assets) -> None:
        """Baja la versión nueva de la app comprobando su SHA-256.

        Lo que se descarga aquí se **ejecuta** después, así que no se instala
        nada que no cuadre con el ``checksums.txt`` de la misma release. Si la
        release no lo trae (o no se puede leer), se rechaza la actualización y
        se ofrece la página de releases: mejor un paso a mano que ejecutar un
        binario sin verificar. El checksum se pide en un hilo porque es una
        petición de red.
        """
        self._set_status(tr("Downloading..."))
        self._set_downloading(True)
        # Guardado en la ventana por lo mismo que ``_download_bridge``
        # (``downloads.py``): si solo lo sujeta el hilo, muere con él y la señal
        # ``done`` se pierde por el camino.
        bridge = self._update_bridge = WorkerBridge()
        bridge.progress.connect(self._set_progress)
        bridge.done.connect(self._apply_update)
        # El error de la actualización no es el de una descarga de Blender: aquí
        # hay que ofrecer la salida manual, no un "Download failed" a secas.
        bridge.error.connect(self._on_update_download_error)

        def worker():
            checksum = updater.checksum_for(assets, asset["name"])
            if not checksum:
                download_log(f"update: sin checksum para {asset['name']}; "
                             "no se instala")
                bridge.error.emit("checksum")
                return
            self.update_downloader.start(
                asset["url"], str(updater.updates_dir()), asset["name"],
                checksum,
                on_progress=lambda done, total: bridge.progress.emit(done, total),
                on_done=lambda path: bridge.done.emit(str(path)),
                on_error=lambda msg: bridge.error.emit(msg),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_download_error(self, message: str) -> None:
        """No se pudo bajar la actualización: ofrecer instalarla a mano.

        Un "Download failed" genérico deja al usuario sin salida. En macOS hay
        que sustituir el ``.app`` a mano de todos modos, y si lo que falla es el
        CDN de GitHub (handshake TLS, red que lo bloquea...) el navegador sigue
        siendo una vía. En vez de rendirnos, abrimos la página de releases.
        """
        self._set_downloading(False)
        self.progress.setValue(0)
        self.percent.setText("")
        if message == "cancelled":
            self._set_status(tr("Cancelled"), 5)
            return
        download_log(f"update download failed: {message}")
        reason = download_reason(message)
        self._set_status(tr("Download failed"), 8)
        dialog = AppDialog(
            self, tr("Download failed"),
            tr("Could not download the update automatically:")
            + "\n\n" + reason + "\n\n"
            + tr("You can download it from the releases page and install it manually."))
        dialog.add_button(tr("Close"), on_click=dialog.reject)
        dialog.add_button(
            tr("Open the releases page"), variant="accent",
            on_click=lambda: (dialog.accept(), updater.open_releases()))
        dialog.exec()

    def _apply_update(self, path: str) -> None:
        self._set_downloading(False)
        # ``apply`` arranca ya la versión nueva, y esta no se cierra hasta un
        # segundo después: el tamaño de la ventana tiene que estar en disco
        # antes, o la nueva abre con uno viejo (ver ``save_window_state``).
        self.save_window_state()

        def worker():
            quit_app = updater.apply(path)
            if quit_app:
                self.update_applied.emit(path)
            else:
                # macOS (y cualquier caso en el que no haya reemplazo en
                # caliente): ``apply`` ya reveló el fichero; solo falta decirlo.
                self.update_manual.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_manual(self) -> None:
        self._show_message(tr("Update downloaded. Install it manually."), 8)

    def _on_update_applied(self, path: str) -> None:
        self._set_status(tr("Restarting to install the update..."))
        # Salida de verdad: no puede acabar escondida en la bandeja.
        QTimer.singleShot(1000, self, self._quit_app)
