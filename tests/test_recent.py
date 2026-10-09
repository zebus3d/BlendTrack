"""Tests de los ficheros recientes (``services/recent``).

Todo es disco: se montan configs de mentira con su ``recent-files.txt`` y se
comprueba qué se lee y cómo se agrupa. No se arranca Blender.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from model.build import InstalledBuild
from services import blender_config as bc
from services import recent as rp


def _entry(version, root):
    return InstalledBuild(
        name=f"blender-{version}",
        path=Path(root) / f"blender-{version}",
        version=version,
        executable=Path(root) / f"blender-{version}" / "blender",
    )


class RecentServiceTest(unittest.TestCase):
    def test_lee_todos_y_marca_los_que_ya_no_estan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = bc.BlenderConfig("5.2", base, "linux", base / "config",
                                      base / "scripts", base / "extensions")
            config.config_dir.mkdir(parents=True)
            good = base / "a.blend"
            good.write_text("", encoding="utf-8")
            (config.config_dir / rp.RECENT_FILE).write_text(
                f"{good}\n/base/que/no/existe.blend\n//relativo.blend\n\n",
                encoding="utf-8")
            files = rp.recent_files(config)
            # El que existe y el que no: los dos, y solo el segundo marcado.
            self.assertEqual([f.path for f in files],
                             [good, Path("/base/que/no/existe.blend")])
            self.assertEqual([f.missing for f in files], [False, True])

    def test_una_ruta_sin_permiso_cuenta_como_desaparecida(self):
        """En Python 3.12, ``is_file()`` **lanza** ``PermissionError`` si no se
        puede recorrer la ruta (un disco de otro usuario, un montaje FUSE).
        Un reciente así no puede tumbar la pestaña entera."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = bc.BlenderConfig("5.2", base, "linux", base / "config",
                                      base / "scripts", base / "extensions")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / rp.RECENT_FILE).write_text(
                "/media/ajeno/escena.blend\n", encoding="utf-8")
            with mock.patch.object(Path, "is_file",
                                   side_effect=PermissionError("sin permiso")):
                files = rp.recent_files(config)
            self.assertEqual([f.missing for f in files], [True])

    def test_sin_fichero_no_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            config = bc.BlenderConfig("5.2", base, "linux", base / "config",
                                      base / "scripts", base / "extensions")
            self.assertEqual(rp.recent_files(config), [])

    def test_agrupa_por_serie_y_usa_la_mas_nueva(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for series, name in (("5.2", "a.blend"), ("4.5", "b.blend")):
                folder = base / "blender" / series / "config"
                folder.mkdir(parents=True)
                recent = base / name
                recent.write_text("", encoding="utf-8")
                (folder / rp.RECENT_FILE).write_text(str(recent) + "\n",
                                                     encoding="utf-8")
            env = {"XDG_CONFIG_HOME": str(base)}
            installed = [_entry("5.2.2", base), _entry("5.2.0", base),
                         _entry("4.5.14", base)]
            groups = rp.grouped(installed, "linux", env)
            self.assertEqual([group.series for group in groups], ["5.2", "4.5"])
            self.assertEqual(groups[0].version, "5.2.2")
            self.assertEqual([f.path for f in groups[0].files],
                             [base / "a.blend"])

    def test_series_sin_recientes_no_aparecen(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / "blender" / "5.2" / "config"
            folder.mkdir(parents=True)
            env = {"XDG_CONFIG_HOME": str(base)}
            groups = rp.grouped([_entry("5.2.2", base)], "linux", env)
            self.assertEqual(groups, [])

    def test_upbge_usa_la_serie_de_blender(self):
        """UPBGE 0.53 guarda su config en la carpeta 5.3, no en la 0.53."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            folder = base / "upbge" / "5.3" / "config"
            folder.mkdir(parents=True)
            recent = base / "up.blend"
            recent.write_text("", encoding="utf-8")
            (folder / rp.RECENT_FILE).write_text(str(recent) + "\n",
                                                 encoding="utf-8")
            entry = InstalledBuild(
                name="upbge-0.53", path=base / "upbge-0.53", version="0.53",
                fork="upbge")
            groups = rp.grouped([entry], "linux", {"XDG_CONFIG_HOME": str(base)})
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].series, "5.3")
            self.assertEqual(groups[0].fork, "upbge")

    def test_blender_y_bforartists_de_la_misma_serie_no_se_mezclan(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("blender", "bforartists"):
                folder = base / name / "5.2" / "config"
                folder.mkdir(parents=True)
                recent = base / f"{name}.blend"
                recent.write_text("", encoding="utf-8")
                (folder / rp.RECENT_FILE).write_text(str(recent) + "\n",
                                                     encoding="utf-8")
            env = {"XDG_CONFIG_HOME": str(base)}
            plain = _entry("5.2.2", base)
            bfa = InstalledBuild(name="Bforartists-5.2.0", path=base / "bfa",
                                 version="5.2.0", fork="bforartists")
            groups = rp.grouped([plain, bfa], "linux", env)
            self.assertEqual({(g.fork, g.series) for g in groups},
                             {("", "5.2"), ("bforartists", "5.2")})


if __name__ == "__main__":
    unittest.main()
