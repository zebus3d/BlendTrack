"""Descargar e instalar una versión de Blender, de principio a fin.

Elegir la fuente más rápida, bajar con progreso y SHA-256, extraer (o montar
el ``.dmg`` en macOS), avisar de que hay una versión nueva de una serie y
gestionar los fallos de escritura. Es el camino largo de la aplicación y por
eso vive en su propio fichero.

Es un **mixin** de ``MainWindow``: el progreso, el estado y los diálogos son
los de la ventana. El trabajo de verdad (red, checksum, extracción) ya está
en ``services/`` y no depende de Qt.
"""

import shlex
import shutil
import threading
from pathlib import Path

from i18n import tr
from model.build import minor_of
from services import api, channels, launcher, macos_dmg, opener, sources
from services import installed as installed_service
from services.downloader import log as download_log
from services.extractor import extract, is_archive
from ui.widgets.dialogs import AppDialog, confirm, show_error
from ui.widgets.shell import WorkerBridge, write_problem


class DownloadFlowMixin:
    """Parte de ``MainWindow``; ver el docstring del módulo."""

    # ----------------------------------------------------- descarga / instalación
    def install_build(self, build) -> None:
        """Descarga e instala una compilación (progreso y SHA-256)."""
        installed = installed_service.find_installed(self.installed, build)
        if installed is not None:
            self.launch_installed(installed)
            return
        if self._download_busy():
            self._show_message(tr("A download is already in progress"), 4)
            return
        self._set_downloading(True)
        self.progress.setValue(0)
        # Primero se elige de qué fuente bajar (el CDN o el release oficial). Es
        # una medición de red, así que va en un hilo y vuelve por señal: en el
        # hilo de la interfaz congelaría la ventana un par de segundos.
        self._set_status(tr("Choosing the fastest source..."))
        # Cada elección lleva su número. Si el usuario cancela mientras se mide
        # (o la respuesta llega tarde), ``_on_source_chosen`` ve que el número
        # ya no es el que se espera y la descarta, en vez de arrancar una
        # descarga que nadie ha pedido.
        self._source_request += 1
        self._pending_source = self._source_request
        request = self._source_request

        def worker():
            self.source_chosen.emit(request, build, sources.choose(build))

        threading.Thread(target=worker, daemon=True).start()

    def _download_busy(self) -> bool:
        """True si hay una descarga de Blender en marcha o eligiendo fuente.

        ``downloader.running`` solo se enciende cuando empieza a bajar; mientras
        se mide qué fuente es más rápida (unos segundos) vale False, y dos clics
        seguidos colaban dos descargas a la vez.
        """
        return self.downloader.running or self._pending_source is not None

    def _on_source_chosen(self, request: int, build, source) -> None:
        """Llega la fuente elegida: arranca la descarga si sigue pedida."""
        if request != self._pending_source:
            return
        self._pending_source = None
        self._start_download(build, source)

    def _start_download(self, build, source) -> None:
        """Arranca la descarga desde la fuente ya elegida."""
        target = self._destination_for(build)
        if not target:
            # Nadie ha marcado ese tipo en ninguna carpeta. Es el precio de no
            # tener cadena de reservas, y es el precio bueno: mejor decirlo que
            # dejarle la build en una carpeta que no eligió.
            self._set_downloading(False)
            self._no_folder_for(channels.type_of_build(build))
            return
        destination = str(Path(target).expanduser())
        # La carpeta puede no dejar escribir (la de las LTS suele estar en otro
        # disco y a veces es una de sistema). Comprobarlo ahora evita bajar
        # cientos de MB para nada y, sobre todo, explica el motivo.
        problem = write_problem(destination)
        if problem:
            # En vez de rendirnos, ofrecemos elegir otra carpeta y reintentar:
            # carpetas como "C:\Program Files" solo dejan escribir a un
            # administrador, y eso el usuario no lo puede cambiar desde aquí.
            if self._ask_other_folder(build, destination, problem):
                self._start_download(build, source)
                return
            self._set_downloading(False)
            self._set_status(tr("Download failed"), 6)
            return
        self._set_status(tr("Downloading..."))
        download_log(f"downloading {build.filename} from {source.label}")
        # Un puente **propio** de esta descarga, capturado por las lambdas. Antes
        # se guardaba en ``self._bridge``, que también usaba la actualización de
        # la app: si se actualizaba la app con un Blender bajando, al acabar el
        # Blender se "aplicaba" su .tar.xz como si fuera la actualización.
        bridge = WorkerBridge()
        bridge.progress.connect(self._set_progress)
        bridge.done.connect(lambda path: self._on_download_done(path, build))
        bridge.error.connect(self._on_download_error)
        self.downloader.start(
            source.url, destination,
            build.filename, source.checksum,
            on_progress=lambda done, total: bridge.progress.emit(done, total),
            on_done=lambda path: bridge.done.emit(str(path)),
            on_error=lambda msg: bridge.error.emit(msg),
            headers=source.headers,
        )

    def _set_progress(self, downloaded: int, total: int) -> None:
        value = int(downloaded * 100 / total) if total else 0
        self.progress.setValue(value)
        self.percent.setText(f"{value} %")

    def cancel_download(self) -> None:
        """Cancela lo que esté en curso: la de Blender o la de la propia app.

        El botón del pie es el mismo para las dos, así que cancela ambas. Si
        aún se estaba eligiendo la fuente, no hay hilo que parar: se olvida la
        petición (``_on_source_chosen`` la descartará) y se recoge la barra.
        """
        self.downloader.cancel()
        self.update_downloader.cancel()
        if self._pending_source is not None:
            self._pending_source = None
            self._set_downloading(False)
        # Si la descarga era un "Reemplazar", ya no se borra nada.
        self._replace_entry = None
        self._set_status(tr("Cancelled"))

    def _on_download_done(self, path: str, build) -> None:
        archive = Path(path)
        # El .dmg de macOS no es un archivo comprimido, pero sí se puede
        # montar y copiar el Blender.app a la carpeta destino: así la build se
        # puede lanzar desde la app como cualquier otra (antes se dejaba el
        # fichero y había que instalarla a mano, sin forma de abrirla luego).
        if (not is_archive(archive)
                and archive.suffix.lower() == ".dmg"
                and self.system.os_name == "darwin"
                and macos_dmg.available()):
            self._install_dmg(archive, build)
            return
        if not is_archive(archive):
            # Otro fichero que no sabemos abrir (o un .dmg bajado desde otro
            # sistema para un Mac): se deja donde está y se avisa, en vez de
            # fingir un fallo de descarga.
            self._on_dmg_manual(str(archive))
            return
        self._set_status(tr("Extracting..."))
        self._set_downloading(True)

        # El archivo ya está bajado, así que se extrae **junto a él**: es la
        # carpeta que recibió la descarga. Preguntar otra vez por el destino
        # daría "" si el usuario acaba de quitar esa carpeta de la lista, y
        # ``Path("")`` es el directorio actual: extraeríamos dentro de la app.
        destination = archive.parent

        def worker():
            try:
                target = extract(archive, destination)
                # El nombre de la carpeta extraída no lleva el hash de la
                # compilación, así que lo anotamos nosotros: es lo único que
                # distingue dos diarias de la misma versión bajadas en días
                # distintos (ver ``services.installed``). Si no apareció una
                # carpeta nueva (target == destino) no hay dónde anotarlo.
                if Path(target) != destination:
                    installed_service.write_marker(target, build)
                # Borrar el archivo comprimido solo si el usuario lo pidió.
                if self.delete_archive:
                    try:
                        archive.unlink()
                    except OSError:
                        pass
            except Exception as error:
                download_log(f"extract failed: {error}")
                self.download_error.emit(str(error))
                return
            self.extract_done.emit(str(target))

        threading.Thread(target=worker, daemon=True).start()

    def _install_dmg(self, archive: Path, build) -> None:
        """Monta el .dmg e instala el ``Blender.app`` en un hilo de trabajo.

        Montar y copiar un bundle son operaciones lentas, así que van fuera del
        hilo de la interfaz. El resultado vuelve por ``extract_done`` (el mismo
        camino que una extracción normal, así el "Reemplazar" y el refresco
        funcionan igual) o por ``dmg_manual`` si no se pudo.
        """
        self._set_status(tr("Installing..."))
        self._set_downloading(True)
        # Igual que al extraer: el .dmg ya está en la carpeta que lo recibió.
        destination = archive.parent

        def worker():
            try:
                target = macos_dmg.install(archive, destination,
                                           build.version, build.arch,
                                           fork=build.fork)
                installed_service.write_marker(target, build)
                if self.delete_archive:
                    try:
                        archive.unlink()
                    except OSError:
                        pass
            except Exception as error:
                download_log(f"dmg install failed: {error}")
                self.dmg_manual.emit(str(archive))
                return
            self.extract_done.emit(str(target))

        threading.Thread(target=worker, daemon=True).start()

    def _on_dmg_manual(self, path: str) -> None:
        """Plan B de macOS (o fichero que no sabemos abrir): revelar y avisar.

        La descarga ha ido bien, así que no es un "Download failed": se deja el
        fichero donde está y se explica que hay que instalarlo a mano.
        """
        archive = Path(path)
        # No hubo extracción, así que no hay "Reemplazar" que completar: sin
        # esto la carpeta vieja se borraría en la siguiente extracción.
        self._replace_entry = None
        self._set_downloading(False)
        self.progress.setValue(0)
        self.percent.setText("")
        opener.reveal(archive)
        self._show_message(
            tr("Downloaded to {folder}", folder=archive.parent)
            + "  ·  "
            + tr("Open it to install Blender manually."),
            10,
        )

    def _on_extract_done(self, target: str) -> None:
        self._set_downloading(False)
        self.progress.setValue(0)
        self.percent.setText("")
        self._set_status(tr("Ready"), 3)
        self.refresh_installed()
        # "Reemplazar": ahora que la nueva está extraída, se borra la vieja.
        # Si el borrado falla, la nueva queda igualmente instalada y se avisa.
        if self._replace_entry is not None:
            old, self._replace_entry = self._replace_entry, None
            if self._is_read_only(old):
                # El candado pudo cerrarse mientras se descargaba: la nueva
                # se queda instalada y la vieja, intacta.
                self._rebuild_store()
                return
            try:
                shutil.rmtree(old.path)
            except OSError as error:
                download_log(f"replace failed ({old.path}): {error}")
                show_error(self, tr("Uninstall"), str(error))
            else:
                self._show_message(tr("Replaced {name}", name=old.name), 5)
                self.refresh_installed()
        self._rebuild_store()

    # ---------------------------------------------------- updates de Blender
    def offer_blender_update(self, entry, build) -> None:
        """Pregunta si reemplazar la instalada o bajar la nueva como copia.

        Es el mismo diálogo para el parche de la misma serie (botón de la
        tarjeta) y para el salto de serie (aviso al cargar compilaciones): en
        los dos casos la decisión es la misma. "Nunca" silencia esa serie de
        Blender (ver ``mute_blender_series``).
        """
        message = (
            tr("You have Blender {current} installed. Blender {new} is available.",
               current=entry.version, new=build.version)
            + "\n\n"
            + tr("Replace the installed version or download the new one as a copy?")
        )
        dialog = AppDialog(self, tr("Update available"), message)
        choice = {"replace": None, "never": False}
        dialog.add_button(tr("Later"), on_click=dialog.reject,
                          tooltip=tr("Ask me again another time."))
        dialog.add_button(
            tr("Download as copy"),
            on_click=lambda: (choice.update(replace=False), dialog.accept()),
            tooltip=tr("Keep the version you have and add the new one next to "
                       "it."))
        # En una carpeta con el candado cerrado no se borra nada (AGENTS.md:
        # "solo lectura" no admite excepciones), así que solo cabe la copia.
        if not self._is_read_only(entry):
            dialog.add_button(
                tr("Replace"), variant="accent",
                on_click=lambda: (choice.update(replace=True), dialog.accept()),
                tooltip=tr("Delete the installed version and put the new one "
                           "in its place."))
        dialog.add_button(
            tr("Never"),
            on_click=lambda: (choice.update(never=True), dialog.reject()),
            tooltip=tr("Never offer updates for this Blender series again.\n"
                       "You can undo it in Settings."))
        dialog.exec()
        if choice["never"]:
            self.mute_blender_series(entry)
            return
        if choice["replace"] is not None:
            self._start_blender_update(entry, build, choice["replace"])

    def mute_blender_series(self, entry) -> None:
        """No volver a ofrecer actualizar esta serie de Blender instalada.

        Se guarda la serie (mayor.menor) y se recalcula: desaparecen tanto el
        botón de parche de la tarjeta como el aviso de salto de serie. Se puede
        reactivar desde Ajustes.
        """
        series = minor_of(entry.version)
        if series and series not in self.settings.ignored_blender_series:
            self.settings.ignored_blender_series = [
                *self.settings.ignored_blender_series, series]
            self.settings.save()
        self._recompute_updates()
        self._rebuild_installed()
        self._update_blender_series_controls()
        self._show_message(
            tr("You will not be reminded about Blender {series} updates.",
               series=series), 8)

    def reset_blender_series(self) -> None:
        """Vuelve a avisar de las series de Blender silenciadas con "Nunca"."""
        if not self.settings.ignored_blender_series:
            return
        self.settings.ignored_blender_series = []
        self.settings.save()
        self._recompute_updates()
        self._rebuild_installed()
        self._update_blender_series_controls()

    def _start_blender_update(self, entry, build, replace: bool) -> None:
        """Descarga la build nueva; si ``replace``, borra la vieja al extraer."""
        if self.downloader.running:
            self._show_message(tr("A download is already in progress"), 4)
            return
        if installed_service.find_installed(self.installed, build) is not None:
            # Ya la tienes (quizá la bajaste antes como copia): no hay parche
            # que aplicar ni nada que reemplazar.
            self._show_message(tr("This version is already installed"), 4)
            return
        self._replace_entry = entry if replace else None
        self.install_build(build)

    def _offer_series_update(self) -> None:
        """Ofrece el salto de serie una vez por sesión y por serie."""
        for update in self.series_updates:
            key = update.build.favorite_key
            if key in self._series_offered:
                continue
            self._series_offered.add(key)
            self.offer_blender_update(update.entry, update.build)
            return

    def _on_download_error(self, message: str) -> None:
        self._set_downloading(False)
        self.progress.setValue(0)
        self.percent.setText("")
        # Si falló el "Reemplazar", la instalada vieja se queda como estaba.
        self._replace_entry = None
        if message == "cancelled":
            # Cancelar no es un fallo: el estado ya lo puso ``cancel_download``.
            self._set_status(tr("Cancelled"), 5)
            return
        # El motivo real, a la vista: antes solo se veía un "Fallo en la
        # descarga" genérico y no había forma de saber si era red, checksum o
        # permisos de la carpeta (justo el caso de las LTS en Windows).
        download_log(f"download failed: {message}")
        reason = tr("Checksum error") if message == "checksum" else message
        self._set_status(tr("Download failed"), 8)
        show_error(self, tr("Download failed"), reason)

    def launch_installed(self, entry, blend_file=None,
                         console: bool | None = None) -> bool:
        """Abre una versión instalada (Blender sigue vivo al cerrar el gestor).

        Es el **único** camino para lanzar Blender desde la interfaz: aplica
        los argumentos de Ajustes > Launch y la consola de esa versión. Con
        ``blend_file`` abre ese fichero (lo usa Recientes); con ``console``
        se fuerza la consola para esta vez sin tocar lo guardado. Devuelve si
        arrancó.
        """
        executable = getattr(entry, "executable", None)
        if executable is None:
            return False
        try:
            args = shlex.split(self.launch_args or "")
            if blend_file is not None:
                args.append(str(blend_file))
            if console is None:
                console = self._console_state(entry)
            if console and not launcher.terminal_available():
                self._set_status(tr("No terminal found; launching without "
                                    "console."), 6)
            self.launcher.launch(executable, args=args, console=console,
                                 env=launcher.parse_env(self.launch_env))
        except Exception as error:
            download_log(f"launch failed: {error}")
            self._set_status(tr("Could not launch Blender: {error}",
                                error=error), 8)
            return False
        return True

    @staticmethod
    def _console_key(entry) -> str:
        """Clave con la que se recuerda la consola de una versión.

        La serie (``favorite_key``) si la tiene, para que la tienda y la
        instalada compartan elección; si no, la ruta de la instalación.
        """
        return getattr(entry, "favorite_key", "") or str(
            getattr(entry, "path", ""))

    def _console_state(self, entry) -> bool:
        """Si esa versión concreta se lanza con consola.

        El botón de cada tarjeta manda sobre esa versión; el ajuste de
        Ajustes > Launch es el valor por defecto de las que no tienen elección.
        """
        key = self._console_key(entry)
        overrides = self.settings.launch_console_overrides
        return bool(overrides.get(key, self.settings.launch_console))

    def set_console_for(self, entry, value: bool) -> None:
        """Recuerda la consola de **esa** versión y repinta las tarjetas.

        No es un ajuste global: encenderlo en una tarjeta no toca las demás.
        """
        key = self._console_key(entry)
        if not key:
            return
        self.settings.launch_console_overrides[key] = bool(value)
        self.settings.save()
        # Las dos listas enseñan el botón (la tienda, en las ya instaladas).
        self._rebuild_installed()
        self._rebuild_store()

    def _launch_with_console(self, entry) -> None:
        """Lanza esa versión con consola sin cambiar el ajuste guardado."""
        self.launch_installed(entry, console=True)

    def _is_read_only(self, entry) -> bool:
        """True si esa instalación vive en una carpeta con el candado cerrado."""
        folder = self.settings.folder_for(getattr(entry, "root", None))
        return folder is not None and not folder.writable

    @staticmethod
    def _read_only_message() -> str:
        return (tr("This version is in a read-only folder.")
                + "\n\n"
                + tr("Open the lock in Settings > Folders, or use your file "
                     "manager."))

    def rename_installed(self, entry, new_name: str) -> None:
        """Renombra la carpeta de una instalación (doble clic en el nombre).

        Cambia la carpeta **real** en disco y vuelve a escanear. Si el nombre no
        vale o el disco no deja (en Windows, Blender abierto desde esa carpeta la
        bloquea), se avisa con el motivo y se deja el nombre viejo.
        """
        if self._is_read_only(entry):
            show_error(self, tr("Rename folder"), self._read_only_message())
            return
        reason = installed_service.rename_failure(entry.path, new_name)
        if reason:
            show_error(self, tr("Rename folder"), self._rename_error(reason))
            return
        try:
            installed_service.rename(entry.path, new_name, entry.version,
                                     entry.branch, entry.build_hash, entry.fork)
        except OSError as error:
            download_log(f"rename failed ({entry.path}): {error}")
            show_error(self, tr("Rename folder"), str(error))
            return
        self._show_message(tr("Renamed to {name}", name=new_name), 5)
        self.refresh_installed()

    @staticmethod
    def _rename_error(reason: str) -> str:
        """Traduce el código de ``installed.rename_failure`` a un texto."""
        return {
            "empty": tr("The name cannot be empty."),
            "invalid": tr('The name cannot contain \\ / : * ? " < > |.'),
            "same": tr("That is already the name."),
            "exists": tr("There is already a folder with that name."),
        }.get(reason, tr("The name is not valid."))

    def delete_installed(self, entry) -> None:
        """Borra una versión instalada, con confirmación.

        La confirmación dice **qué** se borra (con nombre y ruta) y el botón usa
        el verbo, no un "Aceptar" genérico. Ojo con dos cosas que se rompieron
        al portar desde Kivy: la ruta va convertida a texto (concatenar un
        ``Path`` a un ``str`` lanza ``TypeError``, y al saltar antes de crear el
        diálogo no se veía nada) y **nada de ``ignore_errors``**: si el borrado
        falla hay que decir por qué, no callarse.
        """
        if self._is_read_only(entry):
            # "Solo lectura" no admite excepciones: si se pudiera borrar pero
            # no instalar, el candado no significaría nada.
            show_error(self, tr("Uninstall"), self._read_only_message())
            return
        if not confirm(self, tr("Uninstall"),
                       tr("Delete {name}?", name=entry.name)
                       + "\n\n" + tr("This will remove the folder permanently.")
                       + "\n\n" + str(entry.path),
                       accept_text=tr("Uninstall"), danger=True):
            return
        try:
            shutil.rmtree(entry.path)
        except OSError as error:
            download_log(f"uninstall failed ({entry.path}): {error}")
            show_error(self, tr("Uninstall"), str(error))
            return
        self._show_message(tr("Deleted {name}", name=entry.name))
        self.refresh_installed()

    # -------------------------------------------------------------- acciones
    def open_release_notes(self, item) -> None:
        """Abre en el navegador las notas de esa versión.

        Acepta la compilación entera (lo que emiten las tarjetas) o una versión
        suelta. La URL la construye ``api.release_notes_url``, que para Blender
        va por serie (de "5.2.1" sale .../release_notes/5.2/) y para los forks
        usa su propia página (la release de GitHub en UPBGE). El port la había
        sustituido por una URL a mano que no existe, y por eso el icono dejó de
        abrir nada útil.

        ``opener.open_url`` puede tardar (arranca el navegador), así que corre
        en un hilo y el resultado vuelve por señal.
        """
        version = getattr(item, "version", item)
        fork = getattr(item, "fork", "")
        notes_url = getattr(item, "notes_url", "")
        url = api.release_notes_url(version, fork, notes_url)
        self._show_message(tr("Opening the release notes..."))

        def worker():
            try:
                opened = opener.open_url(url)
            except Exception:
                opened = False
            self.release_notes_result.emit(bool(opened))

        threading.Thread(target=worker, daemon=True).start()

    def _on_release_notes_result(self, opened: bool) -> None:
        if not opened:
            self._show_message(tr("Could not open the browser"))
