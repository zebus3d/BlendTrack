"""Tests de la instalación del ``.dmg`` de macOS (``services.macos_dmg``).

No hace falta un Mac: se mockean ``subprocess.run`` o ``_run`` (``hdiutil``,
``ditto``, ``xattr``) y se mira qué queda en disco. Los casos de siempre
(montar, copiar, versión del plist) están en ``test_services.py``; aquí van
los de los fallos.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import macos_dmg


class DmgFailureTests(unittest.TestCase):
    def test_una_copia_cortada_no_deja_la_carpeta_a_medias(self):
        """Si ``ditto`` falla, no queda un ``.app`` roto que parezca instalado."""

        def run(command, check=True):
            if command[0].endswith("hdiutil") and "attach" in command:
                mount = Path(command[command.index("-mountpoint") + 1])
                (mount / "Blender.app" / "Contents").mkdir(parents=True)
            elif command[0].endswith("ditto"):
                Path(command[-1]).mkdir(parents=True)
                raise macos_dmg.DmgError("ditto falló (1): disco lleno")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / "b.dmg"
            dmg.write_bytes(b"dmg")
            dest = Path(tmp) / "out"
            with mock.patch.object(macos_dmg, "_run", side_effect=run):
                with self.assertRaises(macos_dmg.DmgError):
                    macos_dmg.install(dmg, dest, "5.2.1", "arm64")
            self.assertEqual(list(dest.iterdir()), [])

    def test_un_comando_colgado_se_corta(self):
        """Todo comando lleva tiempo máximo; agotarlo es un ``DmgError``."""
        with mock.patch.object(
                macos_dmg.subprocess, "run",
                side_effect=subprocess.TimeoutExpired("hdiutil", 1)) as run:
            with self.assertRaises(macos_dmg.DmgError):
                macos_dmg._run(["/usr/bin/hdiutil", "attach", "x.dmg"])
        self.assertEqual(run.call_args.kwargs["timeout"],
                         macos_dmg.COMMAND_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
