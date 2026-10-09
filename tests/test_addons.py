"""Tests del gestor de addons (``services/addons``).

Todo es disco y mocks: se montan configs y zips de mentira y se comprueba
dónde queda cada cosa. No se arranca Blender.
"""

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from model.build import InstalledBuild
from services import addons as ap


def _entry(tmp, version="5.3.0"):
    return InstalledBuild(name="blender", path=Path(tmp) / "blender",
                          version=version,
                          executable=Path(tmp) / "blender" / "blender")


def _env(tmp):
    return {"XDG_CONFIG_HOME": str(tmp)}


def _config_dir(tmp, series="5.3"):
    return Path(tmp) / "blender" / series


class ListAddonsTest(unittest.TestCase):
    def test_lista_legacy_y_extension_con_su_estado(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _config_dir(tmp)
            legacy = root / "scripts" / "addons" / "mi_addon"
            legacy.mkdir(parents=True)
            (legacy / "__init__.py").write_text(
                'bl_info = {"name": "Mi Addon", "version": (1, 0), '
                '"blender": (5, 0, 0)}\n', encoding="utf-8")
            extension = root / "extensions" / "user_default" / "otro"
            extension.mkdir(parents=True)
            (extension / "blender_manifest.toml").write_text(
                'id = "otro"\nname = "Otro"\nversion = "1.0.0"\n'
                'blender_version_min = "5.0.0"\n', encoding="utf-8")
            with mock.patch.object(ap.blender_runner, "enabled_addons",
                                   return_value=["mi_addon"]):
                states = ap.list_addons(_entry(tmp), "linux", _env(tmp))
            by_module = {state.module: state for state in states}
            self.assertTrue(by_module["mi_addon"].enabled)
            self.assertFalse(by_module["bl_ext.user_default.otro"].enabled)
            self.assertFalse(by_module["mi_addon"].linked)


class InstallAddonTest(unittest.TestCase):
    def _zip(self, path, files):
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in files.items():
                archive.writestr(name, content)

    def test_instala_un_py_suelto(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "mi_addon.py"
            source.write_text("# addon", encoding="utf-8")
            result = ap.install(_entry(tmp), source, "linux", _env(tmp))
            self.assertEqual(result["module"], "mi_addon")
            destination = _config_dir(tmp) / "scripts" / "addons" / "mi_addon.py"
            self.assertTrue(destination.is_file())

    def test_instala_un_zip_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon.zip"
            self._zip(source, {"mi_addon/__init__.py": "# addon"})
            result = ap.install(_entry(tmp), source, "linux", _env(tmp))
            self.assertEqual(result["kind"], ap.LEGACY)
            destination = (_config_dir(tmp) / "scripts" / "addons" / "mi_addon"
                           / "__init__.py")
            self.assertTrue(destination.is_file())

    def test_instala_un_zip_de_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "ext.zip"
            self._zip(source, {
                "otro/blender_manifest.toml":
                    'id = "otro"\nname = "Otro"\nversion = "1.0.0"\n'
                    'blender_version_min = "5.0.0"\n'})
            result = ap.install(_entry(tmp), source, "linux", _env(tmp))
            self.assertEqual(result["kind"], ap.EXTENSION)
            self.assertEqual(result["module"], "bl_ext.user_default.otro")
            destination = (_config_dir(tmp) / "extensions" / "user_default"
                           / "otro" / "blender_manifest.toml")
            self.assertTrue(destination.is_file())

    def test_lo_que_pisa_se_guarda_como_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _config_dir(tmp)
            existing = root / "scripts" / "addons" / "mi_addon"
            existing.mkdir(parents=True)
            (existing / "__init__.py").write_text("viejo", encoding="utf-8")
            source = Path(tmp) / "addon.zip"
            self._zip(source, {"mi_addon/__init__.py": "nuevo"})
            ap.install(_entry(tmp), source, "linux", _env(tmp))
            backup = root / "scripts" / "addons" / (
                "mi_addon" + ap.bc.BACKUP_SUFFIX)
            self.assertTrue(backup.is_dir())
            self.assertEqual((existing / "__init__.py").read_text(
                encoding="utf-8"), "nuevo")

    def test_un_zip_sin_addon_se_rechaza(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cosas.zip"
            self._zip(source, {"leeme.txt": "nada"})
            with self.assertRaises(ap.AddonError):
                ap.install(_entry(tmp), source, "linux", _env(tmp))


class LinkAddonTest(unittest.TestCase):
    def test_un_zip_corrupto_se_cuenta_como_error_del_gestor(self):
        """Un fallo del sistema no puede escaparse como excepción cruda.

        La vista solo sabe contar ``AddonError``; sus hilos morían sin emitir
        nada y dejaban el gestor bloqueado (ni instalar, ni enlazar, ni
        borrar) hasta reiniciar la app. Un .zip a medio bajar bastaba.
        """
        with tempfile.TemporaryDirectory() as tmp:
            entry = _entry(tmp)
            malo = Path(tmp) / "corrupto.zip"
            malo.write_bytes(b"PK\x03\x04 no soy un zip")
            with self.assertRaises(ap.AddonError) as caso:
                ap.install(entry, malo, "linux")
            self.assertEqual(caso.exception.reason, "broken_archive")
            self.assertTrue(caso.exception.detail)

    def test_si_no_se_puede_escribir_lo_dice(self):
        """Sin permiso en el destino, ``copy_failed`` y no un PermissionError."""
        with tempfile.TemporaryDirectory() as tmp:
            entry = _entry(tmp)
            suelto = Path(tmp) / "mi_addon.py"
            suelto.write_text("bl_info = {}\n", encoding="utf-8")
            with mock.patch("services.blender_addons.shutil.copy2",
                            side_effect=PermissionError("sin permiso")):
                with self.assertRaises(ap.AddonError) as caso:
                    ap.install(entry, suelto, "linux")
            self.assertEqual(caso.exception.reason, "copy_failed")

    def test_enlaza_y_desvincula_sin_tocar_el_origen(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp) / "proyecto" / "mi_addon"
            dev.mkdir(parents=True)
            (dev / "__init__.py").write_text("# dev", encoding="utf-8")
            result = ap.link(_entry(tmp), dev, "linux", _env(tmp))
            self.assertTrue(result["destination"].is_symlink())
            # Borrar el addon quita el enlace, no la carpeta del proyecto.
            addon = ap.baddons.Addon(kind=ap.LEGACY, module="mi_addon",
                                name="Mi Addon", version="", min_version="",
                                max_version="", path=result["destination"])
            ap.remove(_entry(tmp), addon, "linux", _env(tmp))
            self.assertFalse(result["destination"].exists())
            self.assertTrue(dev.is_dir())


    def test_si_el_enlace_falla_vuelve_el_addon_que_habia(self):
        """Un enlace que no se pudo crear no se lleva el addon instalado."""
        with tempfile.TemporaryDirectory() as tmp:
            dev = Path(tmp) / "proyecto" / "mi_addon"
            dev.mkdir(parents=True)
            (dev / "__init__.py").write_text("# dev", encoding="utf-8")
            instalado = _config_dir(tmp) / "scripts" / "addons" / "mi_addon"
            instalado.mkdir(parents=True)
            (instalado / "__init__.py").write_text("# mio", encoding="utf-8")
            with mock.patch.object(Path, "symlink_to",
                                   side_effect=OSError("sin permiso")):
                with self.assertRaises(ap.AddonError) as caso:
                    ap.link(_entry(tmp), dev, "linux", _env(tmp))
            self.assertEqual(caso.exception.reason, "symlink_failed")
            self.assertEqual((instalado / "__init__.py").read_text(), "# mio")

    def test_si_no_se_puede_borrar_lo_dice(self):
        """Un borrado que falla no puede contarse como hecho."""
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = _config_dir(tmp) / "scripts" / "addons" / "mi_addon"
            carpeta.mkdir(parents=True)
            (carpeta / "__init__.py").write_text("# mio", encoding="utf-8")
            addon = ap.baddons.Addon(kind=ap.LEGACY, module="mi_addon",
                                     name="Mi Addon", version="", min_version="",
                                     max_version="", path=carpeta)
            entry = _entry(tmp)
            entry.executable = None
            with mock.patch.object(ap.baddons.shutil, "rmtree",
                                   side_effect=PermissionError("sin permiso")):
                with self.assertRaises(ap.AddonError) as caso:
                    ap.remove(entry, addon, "linux", _env(tmp))
            self.assertEqual(caso.exception.reason, "copy_failed")
            self.assertTrue(carpeta.is_dir())

class SetEnabledTest(unittest.TestCase):
    def test_activa_y_desactiva(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = _entry(tmp)
            with mock.patch.object(ap.blender_runner, "set_addons",
                                   return_value={"errors": []}) as runner:
                ap.set_enabled(entry, "mi_addon", True)
                self.assertEqual(runner.call_args[1]["enable"], ["mi_addon"])
                ap.set_enabled(entry, "mi_addon", False)
                self.assertEqual(runner.call_args[1]["disable"], ["mi_addon"])

    def test_sin_ejecutable_avisa(self):
        entry = InstalledBuild(name="x", path=Path("/tmp/x"), version="5.3.0")
        with self.assertRaises(ap.AddonError):
            ap.set_enabled(entry, "mi_addon", True)


if __name__ == "__main__":
    unittest.main()
