"""Tests del punto de entrada (``src/main.py``) que no necesitan ventana."""

import sys
import threading
import unittest
from unittest import mock

import main


class ThreadExceptionHookTests(unittest.TestCase):
    def setUp(self):
        previous = threading.excepthook
        self.addCleanup(setattr, threading, "excepthook", previous)

    def test_un_fallo_en_un_hilo_queda_en_el_log(self):
        """Lo que revienta un hilo no pasa por ``sys.excepthook``: se perdía."""
        main._install_thread_exception_hook()

        def trabajo():
            raise ValueError("zip roto")

        with mock.patch.object(main.downloader, "log") as log, \
                mock.patch.object(threading, "__excepthook__"):
            hilo = threading.Thread(target=trabajo, name="descarga")
            hilo.start()
            hilo.join()
        texto = log.call_args.args[0]
        self.assertIn("thread descarga", texto)
        self.assertIn("ValueError: zip roto", texto)

    def test_systemexit_en_un_hilo_no_es_un_error(self):
        main._install_thread_exception_hook()
        with mock.patch.object(main.downloader, "log") as log, \
                mock.patch.object(threading, "__excepthook__"):
            hilo = threading.Thread(target=sys.exit)
            hilo.start()
            hilo.join()
        log.assert_not_called()


class SmokeTests(unittest.TestCase):
    """El smoke distingue un fallo del binario de una caída del servidor.

    Un 5xx o el HTML de error de ``builder.blender.org`` no pueden bloquear una
    publicación: el binario está bien y el fallo es ajeno. Pero un problema de
    red de verdad (sin DNS, timeout, TLS) sí tiene que fallar, que es justo lo
    que el smoke se puso a comprobar (tapó un binario sin HTTPS durante días).
    """

    def test_servidor_caido_no_bloquea(self):
        import json as _json

        # El caso real: builder.blender.org devolvió un fatal de PHP (200, HTML)
        # y ``json.loads`` reventó con JSONDecodeError.
        try:
            _json.loads("<br />Fatal error: Call to undefined method "
                        "BuildsRenderer::getFileNameURL()")
        except Exception as error:
            fallo = error
        with mock.patch.object(main.api, "fetch_builds", side_effect=fallo):
            self.assertEqual(main.smoke(), main.SMOKE_SERVER_DOWN)

    def test_error_500_no_bloquea(self):
        import urllib.error

        error = urllib.error.HTTPError("http://x", 500, "push", {}, None)
        with mock.patch.object(main.api, "fetch_builds", side_effect=error):
            self.assertEqual(main.smoke(), main.SMOKE_SERVER_DOWN)

    def test_fallo_de_red_si_bloquea(self):
        import urllib.error

        for error in (urllib.error.URLError("getaddrinfo failed"),
                      TimeoutError("timed out"),
                      urllib.error.HTTPError("http://x", 404, "nf", {}, None)):
            with self.subTest(error=error), \
                    mock.patch.object(main.api, "fetch_builds",
                                      side_effect=error):
                self.assertEqual(main.smoke(), main.SMOKE_BINARY_BROKEN)

    def test_listado_vacio_si_bloquea(self):
        with mock.patch.object(main.api, "fetch_builds", return_value=([], {})):
            self.assertEqual(main.smoke(), main.SMOKE_BINARY_BROKEN)

    def test_si_va_bien_sale_cero(self):
        from model.build import Build

        build = Build(version="5.2.1", branch="v52", risk="stable",
                      platform="linux", arch="x86_64", url="u",
                      filename="f.tar.xz", size=1, mtime=1)
        with mock.patch.object(main.api, "fetch_builds",
                               return_value=([build], {})):
            self.assertEqual(main.smoke(), main.SMOKE_OK)


class ArgumentsTests(unittest.TestCase):
    def test_debug_activa_el_volcado_de_fallos(self):
        """``--debug`` existía y no hacía nada; ahora deja la pila en el log."""
        with mock.patch.object(sys, "argv", ["main.py", "--debug"]), \
                mock.patch.object(main, "_enable_crash_log") as crash, \
                mock.patch.object(main, "run_ui", return_value=0), \
                self.assertRaises(SystemExit):
            main.main()
        crash.assert_called_once_with()

    def test_el_volcado_va_al_fichero_de_log(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("services.settings.cache_dir", return_value=Path(tmp)), \
                mock.patch.object(main.faulthandler, "enable") as enable:
            main._enable_crash_log()
            try:
                destino = enable.call_args.kwargs["file"]
                self.assertEqual(Path(destino.name).parent, Path(tmp))
            finally:
                main._crash_log.close()
                main._crash_log = None

    def test_la_ayuda_va_entera_en_ingles(self):
        ayuda = main._parser().format_help()
        self.assertIn("Internal: apply an update", ayuda)
        self.assertNotIn("Uso interno", ayuda)


if __name__ == "__main__":
    unittest.main()
