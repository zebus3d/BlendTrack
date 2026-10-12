"""Tests de la instancia única (``ui.single_instance``).

Se usa un nombre de socket propio en cada test: con el de verdad, el test
hablaría con la BlendTrack que el usuario tenga abierta.
"""

import os
import stat
import sys
import unittest
import uuid
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    from ui import single_instance
    HAVE_QT = True
except ImportError:  # pragma: no cover - sin PySide6 no hay nada que probar
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _instance(self):
        name = f"blendtrack-test-{uuid.uuid4().hex[:12]}"
        with mock.patch.object(single_instance, "socket_name", return_value=name):
            instance = single_instance.SingleInstance()
        self.addCleanup(instance.close)
        return instance

    @unittest.skipIf(sys.platform.startswith("win"), "en Windows es un named pipe")
    def test_el_socket_solo_lo_abre_su_usuario(self):
        """Sin ``UserAccessOption`` el socket nacía con la ``umask``."""
        instance = self._instance()
        previous = os.umask(0o002)   # grupo escribible, como en Fedora/Ubuntu
        try:
            self.assertTrue(instance.listen(lambda: None))
        finally:
            os.umask(previous)
        path = instance._server.fullServerName()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        self.assertEqual(mode & 0o077, 0, oct(mode))

    def test_una_segunda_apertura_avisa_a_la_primera(self):
        primera = self._instance()
        avisos = []
        self.assertTrue(primera.listen(lambda: avisos.append(1)))
        segunda = single_instance.SingleInstance()
        segunda._name = primera._name
        self.assertTrue(segunda.notify_existing())
        for _ in range(50):
            self.app.processEvents()
            if avisos:
                break
        self.assertEqual(avisos, [1])


if __name__ == "__main__":
    unittest.main()
