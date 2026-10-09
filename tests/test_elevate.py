"""Tests de la elevación en Windows (``services.elevate``).

Se pueden correr en cualquier sistema: se mockean ``ctypes`` (el
``ShellExecuteW`` que dispara el UAC) y ``subprocess`` (``icacls``).
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import elevate


class ElevateAccountTests(unittest.TestCase):
    def test_el_permiso_es_para_quien_lo_pidio(self):
        """El proceso elevado puede ser otra cuenta (la del administrador).

        Por eso la cuenta viaja desde el proceso sin privilegios.
        """
        env = {"USERNAME": "ana", "USERDOMAIN": "CASA"}
        with mock.patch.dict(elevate.os.environ, env), \
                mock.patch.object(elevate, "available", return_value=True), \
                mock.patch.object(elevate, "ctypes") as ctypes:
            ctypes.windll.shell32.ShellExecuteW.return_value = 42
            self.assertTrue(elevate.relaunch_elevated(["--grant-access", "C:\\x"]))
        parameters = ctypes.windll.shell32.ShellExecuteW.call_args.args[3]
        self.assertIn("--for-user CASA\\ana", parameters)

    def test_grant_write_usa_la_cuenta_recibida(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(elevate.os.environ, {"USERNAME": "admin"}), \
                mock.patch.object(elevate.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            self.assertTrue(elevate.grant_write(Path(tmp) / "x", "CASA\\ana"))
        self.assertIn("CASA\\ana:(OI)(CI)M", run.call_args.args[0])

    def test_una_cuenta_con_sintaxis_de_icacls_no_cuela(self):
        """``ana:(F)`` daría control total: se ignora y se usa la del proceso."""
        self.assertFalse(elevate.valid_account("ana:(F)"))
        self.assertFalse(elevate.valid_account(""))
        self.assertTrue(elevate.valid_account("CASA\\Ana López"))
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(elevate.os.environ, {"USERNAME": "ana"}), \
                mock.patch.object(elevate.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            elevate.grant_write(Path(tmp) / "x", "ana:(F)")
        self.assertIn("ana:(OI)(CI)M", run.call_args.args[0])

    def test_en_modo_fuente_el_main_va_con_ruta_absoluta(self):
        """El proceso elevado arranca en System32: una ruta relativa no sirve."""
        with mock.patch.object(elevate.sys, "argv", ["src/main.py"]):
            comando = elevate._command(["--grant-access", "C:\\x"])
        self.assertTrue(Path(comando[1]).is_absolute())
        self.assertTrue(Path(comando[1]).is_file())


if __name__ == "__main__":
    unittest.main()
