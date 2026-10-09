"""Tests del autoarranque (``services.autostart``) que no tocan el sistema.

Los de activar y desactivar de verdad (con un ``XDG_CONFIG_HOME`` temporal)
están en ``test_services.py``; aquí va el formato del ``.desktop``.
"""

import unittest

from services import autostart


class DesktopExecQuotingTests(unittest.TestCase):
    """El ``Exec=`` sigue la Desktop Entry Specification al pie de la letra."""

    def test_un_porcentaje_no_es_un_codigo_de_campo(self):
        """``%f`` lo sustituiría el escritorio: un ``%`` literal va doblado."""
        self.assertEqual(autostart._quote_desktop("/apps/100%/BM"),
                         "/apps/100%%/BM")

    def test_una_barra_invertida_se_escribe_cuatro_veces(self):
        """Comillas (``\\\\``) y luego escape de cadena (otra vez doble)."""
        self.assertEqual(autostart._quote_desktop("/a/b\\c"), '"/a/b\\\\\\\\c"')

    def test_comillas_y_dolar_dentro_de_las_comillas(self):
        # Las comillas: ``\"`` y el escape de cadena dobla su barra.
        self.assertEqual(autostart._quote_desktop('/a b/"x"$'),
                         '"/a b/\\\\"x\\\\"\\\\$"')

    def test_una_ruta_normal_se_queda_igual(self):
        self.assertEqual(autostart._quote_desktop("/opt/BlenderManager"),
                         "/opt/BlenderManager")
        self.assertEqual(autostart._quote_desktop("/ruta/con espacios/BM"),
                         '"/ruta/con espacios/BM"')


if __name__ == "__main__":
    unittest.main()
