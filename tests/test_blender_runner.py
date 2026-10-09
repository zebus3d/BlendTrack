"""Tests del lanzador de Blender headless (``services.blender_runner``).

Nunca se arranca un Blender de verdad: se mockea ``subprocess.run``. Aquí solo
se comprueba que se interpretan bien sus salidas y sus fallos.
"""

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from services import blender_runner


def _completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=["blender"], returncode=code,
                                       stdout=stdout, stderr=stderr)


class PythonVersionTest(unittest.TestCase):
    def test_parsea_la_version(self):
        with mock.patch.object(blender_runner.subprocess, "run",
                               return_value=_completed("BLENDERMANAGER_PY=3.13\n")):
            self.assertEqual(blender_runner.python_version("/bin/blender"), "3.13")

    def test_sin_ejecutable_no_lanza_nada(self):
        with mock.patch.object(blender_runner.subprocess, "run") as run:
            self.assertEqual(blender_runner.python_version(""), "")
            run.assert_not_called()

    def test_sin_marcador_devuelve_vacio(self):
        with mock.patch.object(blender_runner.subprocess, "run",
                               return_value=_completed("ruido\n")):
            self.assertEqual(blender_runner.python_version("/bin/blender"), "")


class EnableAddonsTest(unittest.TestCase):
    def test_parsea_el_resultado(self):
        output = ('BLENDERMANAGER_RESULT={"enabled": ["foo"], "errors": []}\n')
        with mock.patch.object(blender_runner.subprocess, "run",
                               return_value=_completed(output)):
            result = blender_runner.enable_addons("/bin/blender", ["foo"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["enabled"], ["foo"])
        self.assertEqual(result["errors"], [])

    def test_timeout_lo_cuenta_como_error(self):
        with mock.patch.object(
                blender_runner.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("blender", 180)):
            result = blender_runner.enable_addons("/bin/blender", ["foo"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])

    def test_sin_modulos_no_arranca(self):
        with mock.patch.object(blender_runner.subprocess, "run") as run:
            result = blender_runner.enable_addons("/bin/blender", [])
            run.assert_not_called()
        self.assertFalse(result["ok"])

    def test_salida_sin_marcador(self):
        with mock.patch.object(blender_runner.subprocess, "run",
                               return_value=_completed("ruido\n")):
            result = blender_runner.enable_addons("/bin/blender", ["foo"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["errors"])


class RunTest(unittest.TestCase):
    def test_blender_no_hereda_la_entrada_de_la_app(self):
        """Un addon con ``input()`` no puede dejar a Blender esperando."""
        with mock.patch.object(blender_runner.subprocess, "run",
                               return_value=_completed("")) as run:
            blender_runner.enable_addons("/bin/blender", ["foo"])
        self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)


class RunningBlendersTest(unittest.TestCase):
    def test_reconoce_el_ejecutable(self):
        self.assertTrue(
            blender_runner._looks_like_blender(Path("/x/blender"), None))
        self.assertTrue(
            blender_runner._looks_like_blender(Path("/x/Blender.exe"), None))
        self.assertFalse(
            blender_runner._looks_like_blender(Path("/x/python3"), None))
        self.assertTrue(blender_runner._looks_like_blender(
            Path("/x/blender"), Path("/x/blender")))

    def test_reconoce_bforartists(self):
        """Sin ejecutable concreto, Bforartists también es "un Blender abierto"."""
        for name in ("bforartists", "Bforartists", "bforartists.exe"):
            self.assertTrue(
                blender_runner._looks_like_blender(Path("/x") / name, None), name)

    def test_en_macos_mira_la_ruta_y_no_solo_el_nombre(self):
        """``pgrep -x Blender`` contestaba por cualquier Blender abierto."""
        salida = ("  101 /Applications/Blender 4.5.app/Contents/MacOS/Blender\n"
                  "  202 /Apps/Bforartists.app/Contents/MacOS/Bforartists\n"
                  "  303 /usr/sbin/cfprefsd\n")
        with mock.patch.object(blender_runner.sys, "platform", "darwin"), \
                mock.patch.object(blender_runner.subprocess, "run",
                                  return_value=_completed(salida)):
            self.assertEqual(blender_runner.running_blenders(), [101, 202])
            mia = "/Apps/Bforartists.app/Contents/MacOS/Bforartists"
            with mock.patch.object(blender_runner.Path, "resolve",
                                   lambda self: self):
                self.assertEqual(blender_runner.running_blenders(mia), [202])

    def test_running_devuelve_una_lista(self):
        # No se afirma que esté vacía: el usuario puede tener Blender abierto.
        self.assertIsInstance(blender_runner.running_blenders(), list)


if __name__ == "__main__":
    unittest.main()
