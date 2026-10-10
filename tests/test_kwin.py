import unittest
from unittest import mock

from services import kwin


class KwinRuleTests(unittest.TestCase):
    """La regla de KWin que recuerda la posición de la ventana (KDE Wayland)."""

    def test_no_aplica_fuera_de_wayland(self):
        with mock.patch.object(kwin.detector, "session_is_wayland",
                               return_value=False):
            self.assertFalse(kwin.available())

    def test_no_aplica_fuera_de_kde(self):
        env = {"XDG_CURRENT_DESKTOP": "GNOME"}
        with mock.patch.object(kwin.detector, "session_is_wayland",
                               return_value=True), \
                mock.patch.object(kwin.opener, "clean_env", return_value=env), \
                mock.patch.object(kwin.shutil, "which",
                                  return_value="/usr/bin/tool"):
            self.assertFalse(kwin.available())

    def test_disponible_en_kde_wayland_con_herramientas(self):
        env = {"XDG_CURRENT_DESKTOP": "KDE"}
        with mock.patch.object(kwin.detector, "session_is_wayland",
                               return_value=True), \
                mock.patch.object(kwin.opener, "clean_env", return_value=env), \
                mock.patch.object(kwin.shutil, "which",
                                  return_value="/usr/bin/tool"):
            self.assertTrue(kwin.available())

    def test_sin_herramientas_no_aplica(self):
        env = {"XDG_CURRENT_DESKTOP": "KDE"}
        with mock.patch.object(kwin.detector, "session_is_wayland",
                               return_value=True), \
                mock.patch.object(kwin.opener, "clean_env", return_value=env), \
                mock.patch.object(kwin.shutil, "which", return_value=None):
            self.assertFalse(kwin.available())

    def test_crea_la_regla_de_recordar_posicion(self):
        with mock.patch.object(kwin, "available", return_value=True), \
                mock.patch.object(kwin, "_read", return_value=""), \
                mock.patch.object(kwin, "_write") as write, \
                mock.patch.object(kwin, "_run") as run:
            self.assertTrue(kwin.remember_position_rule())
        escrito = {call.args[0]: call.args[1] for call in write.call_args_list}
        self.assertEqual(escrito["wmclass"], kwin.WINDOW_CLASS)
        self.assertEqual(escrito["wmclassmatch"], kwin.CLASS_MATCH_SUBSTRING)
        self.assertEqual(escrito["wmclasscomplete"], "false")
        self.assertEqual(escrito["positionrule"], kwin.POSITION_RULE_REMEMBER)
        self.assertEqual(escrito["rules"], "1")
        run.assert_called_once_with(
            ["qdbus6", "org.kde.KWin", "/KWin", "reconfigure"])

    def test_si_la_regla_ya_esta_no_la_toca(self):
        def leer(key, group):
            if key == "rules":
                return "1"
            return kwin.WINDOW_CLASS if key == "wmclass" else ""

        with mock.patch.object(kwin, "available", return_value=True), \
                mock.patch.object(kwin, "_read", side_effect=leer), \
                mock.patch.object(kwin, "_write") as write, \
                mock.patch.object(kwin, "_run") as run:
            self.assertTrue(kwin.remember_position_rule())
        write.assert_not_called()
        run.assert_not_called()

    def test_un_fallo_al_escribir_no_tumba_nada(self):
        with mock.patch.object(kwin, "available", return_value=True), \
                mock.patch.object(kwin, "_read", side_effect=OSError("boom")):
            self.assertFalse(kwin.remember_position_rule())


if __name__ == "__main__":
    unittest.main()
