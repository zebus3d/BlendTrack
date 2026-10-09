"""Tests del núcleo de ``services.downloader``: lo que pasa con los bytes.

Las invariantes que promete AGENTS.md viven en ``Downloader._run`` y hasta
ahora no las comprobaba nadie (0 % de cobertura): se baja a un ``.part`` que
solo se renombra al final, una suma SHA-256 distinta rechaza el fichero, al
cancelar no queda nada a medias y el fichero final lleva el bit de ejecución.

La red no se toca: se sustituye ``_connect`` por una respuesta falsa que
entrega unos bytes conocidos.
"""

import hashlib
import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from services import downloader
from services.downloader import Downloader

DATA = b"blender" * 50_000          # ~350 KB: varios trozos de lectura
GOOD_SHA = hashlib.sha256(DATA).hexdigest()


class _FakeResponse(io.BytesIO):
    """Lo mínimo de ``urlopen`` que usa el descargador: cabeceras y read()."""

    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


class DownloaderRunTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def _download(self, sha=None, response=None, cancel_after=None):
        """Descarga ``DATA`` y devuelve ``(ruta_final | None, error | None)``.

        ``cancel_after`` pide cancelar al recibir ese progreso (en bytes), que
        es como lo haría el botón del pie a mitad de descarga.
        """
        loader = Downloader()
        result = {"path": None, "error": None}
        finished = threading.Event()

        def on_progress(done, total):
            if cancel_after is not None and done >= cancel_after:
                loader.cancel()

        def on_done(path):
            result["path"] = path
            finished.set()

        def on_error(message):
            result["error"] = message
            finished.set()

        fake = response if response is not None else _FakeResponse(DATA)
        with mock.patch.object(loader, "_connect", return_value=fake):
            loader.start("https://example/f.tar.xz", str(self.folder),
                         "f.tar.xz", sha, on_progress=on_progress,
                         on_done=on_done, on_error=on_error)
            self.assertTrue(finished.wait(10), "la descarga no terminó")
        return result["path"], result["error"]

    def test_baja_completa_y_renombra_el_part(self):
        path, error = self._download(sha=GOOD_SHA)
        self.assertIsNone(error)
        self.assertEqual(Path(path).read_bytes(), DATA)
        # Del .part no queda rastro: solo se renombra cuando está entero.
        self.assertFalse((self.folder / "f.tar.xz.part").exists())

    def test_una_suma_distinta_rechaza_el_fichero(self):
        path, error = self._download(sha="0" * 64)
        self.assertIsNone(path)
        self.assertEqual(error, "checksum")
        # Lo que no cuadra no se queda con el nombre bueno.
        self.assertFalse((self.folder / "f.tar.xz").exists())

    def test_cancelar_no_deja_nada_a_medias(self):
        path, error = self._download(sha=GOOD_SHA,
                                     cancel_after=downloader.CHUNK_SIZE)
        self.assertIsNone(path)
        self.assertEqual(error, "cancelled")
        self.assertEqual(list(self.folder.iterdir()), [])

    @unittest.skipIf(os.name == "nt", "el bit de ejecución es de POSIX")
    def test_el_fichero_final_es_ejecutable(self):
        # Si el auto-update no puede reemplazar el AppImage, el usuario abre a
        # mano la copia descargada: sin +x, "Permiso denegado".
        path, _ = self._download()
        self.assertTrue(os.access(path, os.X_OK))

    def test_un_fallo_de_red_a_mitad_lo_avisa(self):
        class _Broken(_FakeResponse):
            def read(self, size=-1):
                raise OSError("connection reset")

        path, error = self._download(response=_Broken(DATA))
        self.assertIsNone(path)
        self.assertIn("connection reset", error)
        self.assertFalse((self.folder / "f.tar.xz").exists())


if __name__ == "__main__":
    unittest.main()
