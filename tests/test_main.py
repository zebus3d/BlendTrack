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
