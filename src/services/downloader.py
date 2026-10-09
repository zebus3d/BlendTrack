"""Descarga de archivos en segundo plano con progreso y verificación.

La descarga se hace siempre en un hilo aparte para no congelar la interfaz;
las llamadas de vuelta (progreso, fin, error) las recibe quien nos llame y es
su responsabilidad reenviarlas al hilo de la interfaz (con una señal de Qt; ver
``ui.widgets.shell.WorkerBridge``).
"""

import hashlib
import threading
import urllib.error
import urllib.request
from pathlib import Path

from services import net, tls
from services.settings import cache_dir

CHUNK_SIZE = 1024 * 256  # 256 KiB por lectura

# Tiempo máximo para conectar (incluye el handshake TLS), y reintentos de la
# conexión. El caso real: "urlopen error _ssl.c:993: The handshake operation
# timed out" al bajar el zip de una release en macOS, el mismo asset que otras
# veces sí había funcionado. Es intermitente, así que reintentar lo resuelve; el
# fallo ocurre **antes de descargar**, así que reintentar no cuesta datos.
CONNECT_TIMEOUT = 30
CONNECT_ATTEMPTS = 3
RETRY_DELAY = 3  # segundos entre intentos


def log(message: str) -> None:
    """Registro sencillo a archivo, útil para diagnosticar fallos en otros equipos."""
    try:
        path = cache_dir() / "blendermanager.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except OSError:
        pass


# Códigos de los fallos que no son de red. Llegan a la interfaz como texto por
# ``on_error`` y ella decide qué decir: "cancelled" no es un error que enseñar
# (lo pidió el usuario) y "checksum" se traduce a "Checksum error". Cualquier
# otro texto es el mensaje de la excepción tal cual.
#
# Comparar cadenas es frágil (un error de red que dijera "cancelled" pasaría
# por cancelación), pero es el contrato con ``ui.widgets.downloads`` y
# ``ui.widgets.updates``: cambiarlo a excepciones o a un enum obliga a tocar
# los dos lados a la vez. Por eso los códigos viven aquí con nombre, y la
# interfaz los compara con estos mismos valores.
CANCELLED = "cancelled"
CHECKSUM = "checksum"


class DownloadError(Exception):
    """Fallo propio de la descarga; su texto es uno de los códigos de arriba."""


def _retry_after(error) -> float:
    """Segundos que pide esperar un 429 (``Retry-After``), con tope y respaldo.

    ``Retry-After`` puede venir en segundos; si no está o no es un número, se
    usa el retardo normal. El tope de 60 s evita que un servidor raro deje la
    descarga colgada minutos.
    """
    value = None
    headers = getattr(error, "headers", None)
    if headers is not None:
        value = headers.get("Retry-After")
    if value:
        try:
            return min(float(value), 60.0)
        except (TypeError, ValueError):
            pass
    return float(RETRY_DELAY)


class Downloader:
    """Gestor de una única descarga simultánea, cancelable."""

    def __init__(self):
        self._cancel = threading.Event()
        self._thread = None

    def cancel(self) -> None:
        """Pide a la descarga en curso que se detenga."""
        self._cancel.set()

    @property
    def running(self) -> bool:
        """True mientras haya una descarga en marcha."""
        return self._thread is not None and self._thread.is_alive()

    def start(self, url, dest_folder, filename, expected_sha256=None,
              on_progress=None, on_done=None, on_error=None, headers=None) -> None:
        """Lanza la descarga en un hilo aparte y va avisando por callbacks.

        ``headers`` son cabeceras extra para la petición; las fuentes que piden
        autenticación (el WebDAV público de Bforartists) las usan. No se guardan
        en el log ni en los ajustes.
        """
        if self.running:
            return
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(url, dest_folder, filename, expected_sha256, on_progress,
                  on_done, on_error, headers),
            daemon=True,
        )
        self._thread.start()

    def _connect(self, url, headers=None):
        """Abre la conexión, reintentando los fallos de red transitorios.

        Solo se reintenta la **conexión** (incluye el handshake TLS): si el
        fallo llega a mitad de la descarga, mejor no empezar de cero. Un
        ``HTTPError`` (404, 500...) no se reintenta: no va a cambiar.
        """
        extra = dict(headers or {})
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            try:
                base = {"User-Agent": net.USER_AGENT}
                base.update(extra)
                request = urllib.request.Request(url, headers=base)
                # Con contexto explicito: si no, en Arch no encuentra las CAs y
                # no se puede descargar nada (ver services/tls.py).
                return urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT,
                                              context=tls.ssl_context())
            except urllib.error.HTTPError as error:
                # 429 = "Too Many Requests": el servidor pide esperar, así que
                # sí se reintenta (respetando ``Retry-After`` si lo manda). El
                # resto de errores HTTP (404, 500...) no cambian al reintentar.
                if (error.code != 429 or self._cancel.is_set()
                        or attempt == CONNECT_ATTEMPTS):
                    raise
                wait = _retry_after(error)
                log(f"download got 429 ({url}), retry "
                    f"{attempt}/{CONNECT_ATTEMPTS} in {wait}s")
                if self._wait_or_cancel(wait):
                    raise
            except Exception as error:
                if self._cancel.is_set() or attempt == CONNECT_ATTEMPTS:
                    raise
                log(f"download connect failed ({url}), retry "
                    f"{attempt}/{CONNECT_ATTEMPTS}: {error}")
                if self._wait_or_cancel(RETRY_DELAY):
                    raise

    def _wait_or_cancel(self, seconds: float) -> bool:
        """Espera entre dos intentos; devuelve True si cancelan mientras tanto.

        ``Event.wait`` en vez de ``time.sleep``: duerme igual, pero se
        despierta en cuanto alguien pulsa Cancelar. Con ``sleep`` la descarga
        se quedaba hasta un minuto (el tope de ``Retry-After``) sin hacer caso.
        """
        return self._cancel.wait(seconds)

    def _run(self, url, dest_folder, filename, expected_sha256, on_progress,
             on_done, on_error, headers=None):
        # Descargamos primero a un .part y solo al final renombramos,
        # así una descarga a medias no se confunde con una completa.
        part_path = Path(dest_folder).expanduser() / (filename + ".part")
        try:
            dest = Path(dest_folder).expanduser()
            dest.mkdir(parents=True, exist_ok=True)
            final_path = dest / filename
            # La URL va al log: un fallo de red (handshake TLS, host bloqueado)
            # es indistinguible de otro sin saber a qué host iba.
            log(f"downloading {url} -> {final_path}")
            with self._connect(url, headers) as response:
                total = int(response.headers.get("Content-Length") or 0)
                digest = hashlib.sha256()
                downloaded = 0
                with open(part_path, "wb") as handle:
                    while True:
                        if self._cancel.is_set():
                            raise DownloadError(CANCELLED)
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        if on_progress:
                            on_progress(downloaded, total)
            if self._cancel.is_set():
                raise DownloadError(CANCELLED)
            # Verificación de integridad si la API nos dio la suma SHA-256.
            if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
                raise DownloadError(CHECKSUM)
            if final_path.exists():
                final_path.unlink()
            part_path.replace(final_path)
            # Bit de ejecución: si el fichero es una AppImage (o cualquier
            # binario) el usuario puede querer abrirlo directamente. El
            # updater hace su propio chmod 0755 antes del self-replace, pero
            # si ese replace falla la app muestra "Downloaded to ... Open it
            # to install the new version" y sin +x el doble-clic da
            # "Permiso denegado". En .tar.xz/.zip el bit es inofensivo.
            try:
                final_path.chmod(final_path.stat().st_mode | 0o111)
            except OSError as error:
                log(f"chmod failed for {final_path}: {error}")
            log(f"downloaded {final_path}")
            if on_done:
                on_done(final_path)
        except Exception as error:
            # Un ``.part`` solo sirve mientras se escribe: no se reanudan
            # descargas, así que uno que se quede (cancelada, red caída o un
            # checksum que no cuadra) son cientos de MB de basura. Se borra en
            # **todos** los fallos; antes el del checksum se quedaba en disco.
            try:
                part_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                log(f"could not remove {part_path}: {cleanup_error}")
            cancelled = (isinstance(error, DownloadError)
                         and str(error) == CANCELLED)
            if not cancelled:
                log(f"download error ({url}): {error}")
            if on_error:
                on_error(str(error))
