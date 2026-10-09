"""Tests de la migración de addons entre versiones de Blender.

Todo es disco y funciones puras: no se lanza ningún Blender (eso vive en
``services.blender_runner`` y se prueba con ``subprocess`` mockeado en
``test_blender_runner.py``). Se montan carpetas de configuración de mentira con
addons legacy y extensiones reales (ficheros de texto).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import blender_config as bc
from services import blender_addons as baddons
from services import blender_snapshots as bsnap


def _config(root, version="4.5", platform="linux"):
    """``BlenderConfig`` apuntando a una carpeta temporal."""
    root = Path(root)
    return bc.BlenderConfig(
        version=version,
        root=root,
        platform=platform,
        config_dir=root / "config",
        scripts_dir=root / "scripts",
        extensions_dir=root / "extensions",
    )


def _legacy_addon(config, module, bl_info: str, name=None):
    """Crea ``scripts/addons/<module>/__init__.py``."""
    folder = config.addons_dir / module
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text(bl_info, encoding="utf-8")
    return folder


def _extension(config, repo, addon_id, manifest: str):
    """Crea ``extensions/<repo>/<id>/blender_manifest.toml``."""
    folder = config.extensions_dir / repo / addon_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / bc.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
    return folder


class ConfigPathsTest(unittest.TestCase):
    def test_config_base_por_plataforma(self):
        env = {"APPDATA": "/appdata", "XDG_CONFIG_HOME": "/xdg"}
        self.assertEqual(bc.config_base("linux", env), Path("/xdg/blender"))
        self.assertEqual(bc.config_base("windows", env),
                         Path("/appdata/Blender Foundation/Blender"))
        home = Path.home()
        self.assertEqual(bc.config_base("darwin", env),
                         home / "Library/Application Support/Blender")

    def test_config_for_normaliza_a_serie(self):
        config = bc.config_for("5.2.1", "linux", {"XDG_CONFIG_HOME": "/xdg"})
        self.assertEqual(config.version, "5.2")
        self.assertEqual(config.root, Path("/xdg/blender/5.2"))
        self.assertEqual(config.config_dir, Path("/xdg/blender/5.2/config"))
        self.assertEqual(config.extensions_dir,
                         Path("/xdg/blender/5.2/extensions"))

    def test_config_for_respeta_variables_de_entorno(self):
        env = {
            "BLENDER_USER_CONFIG": "/cfg",
            "BLENDER_USER_SCRIPTS": "/scripts",
            "BLENDER_USER_EXTENSIONS": "/exts",
        }
        config = bc.config_for("4.5", "linux", env)
        self.assertEqual(config.config_dir, Path("/cfg"))
        self.assertEqual(config.scripts_dir, Path("/scripts"))
        self.assertEqual(config.extensions_dir, Path("/exts"))
        # Los addons legacy cuelgan de la carpeta de scripts apuntada.
        self.assertEqual(config.addons_dir, Path("/scripts/addons"))

    def test_config_for_resources_reemplaza_toda_la_version(self):
        config = bc.config_for("4.5", "linux", {"BLENDER_USER_RESOURCES": "/res"})
        self.assertEqual(config.root, Path("/res"))
        self.assertEqual(config.config_dir, Path("/res/config"))

    def test_config_base_de_los_forks_en_cada_plataforma(self):
        """Cada fork escribe su config en su propio sitio (lo dice su origen)."""
        env = {"APPDATA": "/appdata", "XDG_CONFIG_HOME": "/xdg"}
        home = Path.home()
        # Bforartists: ~/.config/bforartists, %APPDATA%\Bforartists\Bforartists,
        # ~/Library/Application Support/Bforartists.
        self.assertEqual(bc.config_base("linux", env, "bforartists"),
                         Path("/xdg/bforartists"))
        self.assertEqual(bc.config_base("windows", env, "bforartists"),
                         Path("/appdata/Bforartists/Bforartists"))
        self.assertEqual(bc.config_base("darwin", env, "bforartists"),
                         home / "Library/Application Support/Bforartists")
        # UPBGE: ~/.config/upbge, %APPDATA%\UPBGE\Blender, ~/.../UPBGE.
        self.assertEqual(bc.config_base("linux", env, "upbge"),
                         Path("/xdg/upbge"))
        self.assertEqual(bc.config_base("windows", env, "upbge"),
                         Path("/appdata/UPBGE/Blender"))
        self.assertEqual(bc.config_base("darwin", env, "upbge"),
                         home / "Library/Application Support/UPBGE")

    def test_config_for_de_upbge_usa_la_serie_de_blender(self):
        """UPBGE 0.53 es un Blender 5.3 y guarda en la carpeta 5.3."""
        env = {"XDG_CONFIG_HOME": "/xdg"}
        config = bc.config_for("0.53", "linux", env, fork="upbge")
        self.assertEqual(config.root, Path("/xdg/upbge/5.3"))
        # La versión que se enseña sigue siendo la de UPBGE.
        self.assertEqual(config.version, "5.3")

    def test_config_for_de_bforartists_usa_su_serie(self):
        env = {"XDG_CONFIG_HOME": "/xdg"}
        config = bc.config_for("5.2.0", "linux", env, fork="bforartists")
        self.assertEqual(config.root, Path("/xdg/bforartists/5.2"))

    def test_python_for_version_conocido_y_desconocido(self):
        self.assertEqual(bc.python_for_version("5.2.1"), "3.13")
        self.assertEqual(bc.python_for_version("4.5.3"), "3.11")
        self.assertEqual(bc.python_for_version("2.79"), "")

    def test_blender_4_0_trae_python_3_10_y_4_1_el_3_11(self):
        self.assertEqual(bc.python_for_version("4.0.2"), "3.10")
        self.assertEqual(bc.python_for_version("4.1.1"), "3.11")


class BlInfoTest(unittest.TestCase):
    def test_lee_bl_info_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            init = Path(tmp) / "__init__.py"
            init.write_text(
                'bl_info = {"name": "Test", "version": (1, 2, 3), '
                '"blender": (4, 5, 0)}\n',
                encoding="utf-8")
            info = baddons.read_bl_info(init)
            self.assertEqual(info["name"], "Test")
            self.assertEqual(info["blender"], (4, 5, 0))

    def test_plan_b_por_regex_si_no_es_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            init = Path(tmp) / "__init__.py"
            init.write_text(
                "import os\n"
                "VERSION = os.environ.get('V', '5.0.0')\n"
                'bl_info = {"name": "Dyn", "blender": (4, 2, 0), '
                '"version": VERSION}\n',
                encoding="utf-8")
            info = baddons.read_bl_info(init)
            # No se puede evaluar (VERSION no es literal), pero el regex lo saca.
            self.assertEqual(info.get("blender"), [4, 2, 0])

    def test_sin_bl_info_devuelve_vacio(self):
        with tempfile.TemporaryDirectory() as tmp:
            init = Path(tmp) / "__init__.py"
            init.write_text("print('hola')\n", encoding="utf-8")
            self.assertEqual(baddons.read_bl_info(init), {})


class AddonsInTest(unittest.TestCase):
    def test_encuentra_legacy_y_extensiones(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(tmp)
            _legacy_addon(config, "old_addon",
                          'bl_info = {"name": "Old", "version": (2, 0), '
                          '"blender": (3, 6, 0)}\n')
            _extension(config, "user_default", "MatPlus",
                       'schema_version = "1.0.0"\nid = "MatPlus"\n'
                       'name = "MatPlus"\nversion = "1.3.0"\n'
                       'blender_version_min = "4.5.0"\n'
                       'tags = ["Paint"]\n')
            addons = baddons.addons_in(config)
            by_module = {addon.module: addon for addon in addons}
            self.assertIn("old_addon", by_module)
            self.assertIn("bl_ext.user_default.MatPlus", by_module)
            self.assertEqual(by_module["old_addon"].min_version, "3.6.0")
            self.assertEqual(
                by_module["bl_ext.user_default.MatPlus"].min_version, "4.5.0")
            self.assertEqual(by_module["old_addon"].kind, "legacy")
            self.assertEqual(by_module["bl_ext.user_default.MatPlus"].kind,
                             "extension")

    def test_repositorio_remoto_tambien_se_escanea(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(tmp)
            _extension(config, "blender_org", "kitsu",
                       'id = "kitsu"\nname = "Kitsu"\nversion = "1.0.0"\n'
                       'blender_version_min = "4.2.0"\n')
            addons = baddons.addons_in(config)
            self.assertEqual([a.module for a in addons],
                             ["bl_ext.blender_org.kitsu"])

    def test_ignora_carpetas_sin_metadatos(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(tmp)
            (config.addons_dir / "no_addon").mkdir(parents=True)
            (config.extensions_dir / ".cache").mkdir(parents=True)
            self.assertEqual(baddons.addons_in(config), [])


class CompatReportTest(unittest.TestCase):
    def _addon(self, **kwargs):
        base = dict(kind="extension", module="bl_ext.user_default.x",
                    name="X", version="1.0.0", min_version="", max_version="",
                    path=Path("/tmp/x"))
        base.update(kwargs)
        return baddons.Addon(**base)

    def test_compatible(self):
        addon = self._addon(min_version="4.2.0")
        self.assertEqual(baddons.compat_report(addon, "5.2.1"), (bc.OK, ""))

    def test_requiere_version_mas_nueva(self):
        addon = self._addon(min_version="5.3.0")
        self.assertEqual(baddons.compat_report(addon, "5.2.1"),
                         (bc.BLOCKED, bc.REASON_REQUIRES_NEWER))

    def test_no_soporta_la_version_destino(self):
        addon = self._addon(min_version="4.2.0", max_version="5.0.0")
        self.assertEqual(baddons.compat_report(addon, "5.2.1"),
                         (bc.BLOCKED, bc.REASON_TOO_NEW))

    def test_plataforma_no_publicada(self):
        addon = self._addon(min_version="4.2.0",
                            platforms=("windows-x64", "macos-arm64"))
        self.assertEqual(baddons.compat_report(addon, "5.2.1", "linux", "x86_64"),
                         (bc.BLOCKED, bc.REASON_PLATFORM))
        self.assertEqual(baddons.compat_report(addon, "5.2.1", "windows", "amd64"),
                         (bc.OK, ""))

    def test_avisa_de_wheels_de_otro_python(self):
        addon = self._addon(min_version="4.2.0",
                            wheels=("numpy-1.26-cp311-cp311-linux_x86_64.whl",))
        self.assertEqual(baddons.compat_report(addon, "5.2.1", "linux", "x86_64",
                                          "3.13"),
                         (bc.WARN, bc.REASON_WHEEL_ABI))

    def test_avisa_si_no_declara_version(self):
        addon = self._addon()
        self.assertEqual(baddons.compat_report(addon, "5.2.1"),
                         (bc.WARN, bc.REASON_UNKNOWN_VERSION))


class WheelTest(unittest.TestCase):
    def test_cp_distinto_choca(self):
        self.assertTrue(baddons.wheel_problem(["a-1-cp311-cp311-linux_x86_64.whl"],
                                          "3.13"))

    def test_cp_igual_no_choca(self):
        self.assertFalse(baddons.wheel_problem(["a-1-cp313-cp313-linux_x86_64.whl"],
                                           "3.13"))

    def test_un_paquete_con_wheels_para_varios_python_no_choca(self):
        """El caso real de MatPlus: Pillow para 3.11 **y** para 3.13.

        Una extensión bien empaquetada trae un wheel por versión de Python y
        Blender instala el que le toca. Mirar wheel a wheel marcaba estas
        extensiones siempre, apuntases a la versión que apuntases.
        """
        wheels = [
            "./wheels/pillow-11.1.0-cp311-cp311-manylinux_2_28_x86_64.whl",
            "./wheels/pillow-11.1.0-cp313-cp313-manylinux_2_28_x86_64.whl",
        ]
        self.assertEqual(baddons.wheel_problem(wheels, "3.13"), "")
        self.assertEqual(baddons.wheel_problem(wheels, "3.11"), "")
        # Y con un Python que no cubre ninguno de los dos, sí avisa.
        self.assertEqual(baddons.wheel_problem(wheels, "3.10"), "pillow")

    def test_solo_avisa_del_paquete_que_falla(self):
        wheels = [
            "./wheels/pillow-11.1.0-cp313-cp313-manylinux_2_28_x86_64.whl",
            "./wheels/numpy-2.2.3-cp311-cp311-manylinux_2_17_x86_64.whl",
            "./wheels/send2trash-1.8-py3-none-any.whl",
        ]
        self.assertEqual(baddons.wheel_problem(wheels, "3.13"), "numpy")

    def test_el_mismo_paquete_en_varias_plataformas_no_choca(self):
        """Un wheel por plataforma, todos del mismo Python: no hay conflicto."""
        wheels = [
            "numpy-2.2.3-cp313-cp313-win_amd64.whl",
            "numpy-2.2.3-cp313-cp313-macosx_11_0_arm64.whl",
            "numpy-2.2.3-cp313-cp313-manylinux_2_17_x86_64.whl",
        ]
        self.assertEqual(baddons.wheel_problem(wheels, "3.13"), "")

    def test_python_puro_y_abi_estable_no_chocan(self):
        self.assertFalse(baddons.wheel_problem(["a-1-py3-none-any.whl"], "3.13"))
        self.assertFalse(baddons.wheel_problem(["a-1-cp39-abi3-linux_x86_64.whl"],
                                           "3.13"))

    def test_sin_python_destino_no_avisa(self):
        self.assertFalse(baddons.wheel_problem(["a-1-cp39-cp39-linux_x86_64.whl"],
                                           ""))

    def test_el_plan_dice_que_paquete_falla(self):
        """El aviso tiene que poder nombrar al culpable y al Python destino."""
        addon = baddons.Addon(
            kind="extension", module="bl_ext.user_default.x", name="X",
            version="1.0.0", min_version="4.2.0", max_version="",
            path=Path("/tmp/x"),
            wheels=("./wheels/numpy-2.2.3-cp311-cp311-linux_x86_64.whl",))
        source = _config("/tmp/origen", version="5.2")
        target = _config("/tmp/destino", version="5.3")
        with mock.patch.object(baddons, "addons_in", return_value=[addon]):
            plan = baddons.plan_migration(source, target, "linux", "x86_64", "3.13")[0]
        self.assertEqual(plan.reason, bc.REASON_WHEEL_ABI)
        self.assertEqual(plan.detail, "numpy")
        self.assertEqual(plan.target_python, "3.13")


class PlanMigrationTest(unittest.TestCase):
    def test_plan_marca_incompatibles_y_destinos(self):
        with tempfile.TemporaryDirectory() as source_tmp, \
                tempfile.TemporaryDirectory() as target_tmp:
            source = _config(source_tmp, "4.5")
            target = _config(target_tmp, "5.3")
            _legacy_addon(source, "viejo",
                          'bl_info = {"name": "Viejo", "version": (1, 0), '
                          '"blender": (3, 6, 0)}\n')
            _legacy_addon(source, "nuevo",
                          'bl_info = {"name": "Nuevo", "version": (1, 0), '
                          '"blender": (6, 0, 0)}\n')
            _extension(source, "user_default", "matplus",
                       'id = "matplus"\nname = "MatPlus"\nversion = "1.0.0"\n'
                       'blender_version_min = "4.5.0"\n')
            plans = baddons.plan_migration(source, target, "linux", "x86_64", "3.13")
            by_module = {plan.addon.module: plan for plan in plans}
            self.assertEqual(by_module["viejo"].status, bc.OK)
            self.assertTrue(by_module["viejo"].selected)
            self.assertEqual(
                by_module["viejo"].destination,
                target.addons_dir / "viejo")
            self.assertEqual(by_module["nuevo"].status, bc.BLOCKED)
            self.assertFalse(by_module["nuevo"].selected)
            self.assertTrue(
                by_module["nuevo"].destination.name == "nuevo")
            # Las extensiones van al repositorio local del destino.
            self.assertEqual(
                by_module["bl_ext.user_default.matplus"].destination,
                target.extensions_dir / "user_default" / "matplus")
            self.assertEqual(
                by_module["bl_ext.user_default.matplus"].enable_module,
                "bl_ext.user_default.matplus")


class ApplyMigrationTest(unittest.TestCase):
    def _plan(self, addon, destination, selected=True):
        status, reason = baddons.compat_report(addon, "5.3")
        return baddons.AddonPlan(addon=addon, status=status, reason=reason,
                            destination=Path(destination), selected=selected)

    def _addon(self, path, module="x"):
        return baddons.Addon(kind="legacy", module=module, name="X", version="1.0",
                        min_version="4.0.0", max_version="", path=Path(path))

    def test_dry_run_no_toca_el_disco(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("x", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            plan = self._plan(self._addon(source), target.addons_dir / "addon")
            result = baddons.apply_migration([plan], target, dry_run=True)
            self.assertTrue(result.dry_run)
            self.assertEqual(len(result.copied), 1)
            self.assertFalse(plan.destination.exists())
            self.assertIsNone(result.marker)

    def test_copia_y_escribe_marcador(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("hola", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            plan = self._plan(self._addon(source), target.addons_dir / "addon")
            result = baddons.apply_migration([plan], target)
            self.assertEqual(len(result.copied), 1)
            self.assertEqual((plan.destination / "a.py").read_text(), "hola")
            self.assertEqual(result.modules, ["x"])
            self.assertTrue(result.marker.is_file())
            payload = json.loads(result.marker.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], "4.5")

    def test_aparta_lo_que_ya_existe(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "addon"
            destination.mkdir(parents=True)
            (destination / "a.py").write_text("viejo", encoding="utf-8")
            plan = self._plan(self._addon(source), destination)
            result = baddons.apply_migration([plan], target)
            self.assertEqual(len(result.backed_up), 1)
            self.assertEqual((destination / "a.py").read_text(), "nuevo")
            self.assertEqual((result.backed_up[0] / "a.py").read_text(), "viejo")

    def test_backup_reutilizable_no_acumula(self):
        """Migrar varias veces no puede dejar un '.bak' por intento.

        Los usuarios migran más de una vez (y a veces al revés), así que hay un
        solo respaldo por destino, no un histórico que llene el disco sin que
        nadie lo vea. Y ese respaldo es **el original** (v1): lo que había en
        destino en la segunda migración era nuestra propia copia (retocada o
        no), que no puede pisarlo.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("v3", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "addon"
            destination.mkdir(parents=True)
            (destination / "a.py").write_text("v1", encoding="utf-8")
            plan = self._plan(self._addon(source), destination)
            baddons.apply_migration([plan], target)   # v1 -> v3 (bak v1)
            (destination / "a.py").write_text("v2", encoding="utf-8")
            baddons.apply_migration([plan], target)   # v2 -> v3 (bak v1)
            backups = [p for p in destination.parent.iterdir()
                       if "blendermanager-bak" in p.name]
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "a.py").read_text(), "v1")

    def test_migrar_dos_veces_no_pierde_el_original(self):
        """El segundo intento no pisa el ``.bak`` con nuestra primera copia.

        Era pérdida de datos: el respaldo es uno por destino, la segunda
        migración apartaba la copia de la primera encima del original y
        deshacer devolvía... la copia.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "addon"
            destination.mkdir(parents=True)
            (destination / "a.py").write_text("original", encoding="utf-8")
            plan = self._plan(self._addon(source), destination)
            baddons.apply_migration([plan], target)
            second = baddons.apply_migration([plan], target)
            self.assertEqual(second.failed, [])
            self.assertEqual((destination / "a.py").read_text(), "nuevo")
            baddons.undo_migration(target)
            self.assertEqual((destination / "a.py").read_text(), "original")

    def test_migrar_dos_veces_sin_original_y_deshacer_lo_quita(self):
        """Si antes no había nada, deshacer tras dos migraciones lo deja vacío."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            plan = self._plan(self._addon(source), target.addons_dir / "addon")
            baddons.apply_migration([plan], target)
            baddons.apply_migration([plan], target)
            baddons.undo_migration(target)
            self.assertEqual(list(target.addons_dir.iterdir()), [])

    def test_migrar_dos_veces_las_preferencias_conserva_las_originales(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = _config(Path(tmp) / "origin")
            target = _config(Path(tmp) / "target")
            origin.config_dir.mkdir(parents=True)
            target.config_dir.mkdir(parents=True)
            (origin.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            (target.config_dir / "userpref.blend").write_bytes(b"ORIGINAL")
            for _ in range(2):
                baddons.copy_preference_files(
                    baddons.preference_plan(origin, target), target)
            baddons.undo_migration(target)
            self.assertEqual((target.config_dir / "userpref.blend").read_bytes(),
                             b"ORIGINAL")

    def test_el_respaldo_no_borra_el_de_otro_addon_por_un_corchete(self):
        """``addon[1]`` no puede tratarse como patrón y casar con ``addon1``."""
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            ajeno = folder / f"addon1{bc.BACKUP_SUFFIX}"
            ajeno.mkdir()
            destination = folder / "addon[1]"
            destination.mkdir()
            backup = baddons.park_existing(destination)
            self.assertTrue(ajeno.is_dir())
            self.assertEqual(backup, folder / f"addon[1]{bc.BACKUP_SUFFIX}")

    def test_undo_restaura_el_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "addon"
            destination.mkdir(parents=True)
            (destination / "a.py").write_text("viejo", encoding="utf-8")
            plan = self._plan(self._addon(source), destination)
            baddons.apply_migration([plan], target)
            self.assertEqual((destination / "a.py").read_text(), "nuevo")
            result = baddons.undo_migration(target)
            self.assertEqual(len(result.restored), 1)
            self.assertEqual((destination / "a.py").read_text(), "viejo")

    def test_deshacer_tras_addons_y_preferencias_revierte_las_dos(self):
        """El marcador acumula: migrar addons y luego ficheros no pierde nada.

        Antes cada migración sobrescribía el marcador entero, así que deshacer
        solo revertía la última y el respaldo de la primera quedaba huérfano.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "addon"
            destination.mkdir(parents=True)
            (destination / "a.py").write_text("viejo", encoding="utf-8")
            baddons.apply_migration([self._plan(self._addon(source), destination)],
                               target)
            # Y después, el fichero de preferencias.
            origin = _config(Path(tmp) / "origin")
            origin.config_dir.mkdir(parents=True)
            target.config_dir.mkdir(parents=True)
            (origin.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            (target.config_dir / "userpref.blend").write_bytes(b"VIEJO")
            baddons.copy_preference_files(baddons.preference_plan(origin, target), target)
            result = baddons.undo_migration(target)
            self.assertEqual(len(result.restored), 2)
            self.assertEqual((destination / "a.py").read_text(), "viejo")
            self.assertEqual((target.config_dir / "userpref.blend").read_bytes(),
                             b"VIEJO")

    def test_undo_borra_lo_que_no_tenia_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            plan = self._plan(self._addon(source), target.addons_dir / "addon")
            baddons.apply_migration([plan], target)
            result = baddons.undo_migration(target)
            self.assertEqual(len(result.removed), 1)
            self.assertFalse(plan.destination.exists())

    def test_undo_no_toca_rutas_vacias_ni_de_fuera(self):
        """Un marcador con una ruta vacía o ajena no borra nada.

        ``Path("")`` es ``Path(".")``: con una entrada vacía, deshacer borraba
        el directorio actual. Y un marcador editado a mano podría apuntar a
        cualquier carpeta del disco.
        """
        with tempfile.TemporaryDirectory() as tmp:
            target = _config(Path(tmp) / "target")
            target.root.mkdir(parents=True)
            ajena = Path(tmp) / "ajena"
            ajena.mkdir()
            (ajena / "dato.txt").write_text("x", encoding="utf-8")
            marker = target.root / bc.MIGRATION_MARKER
            marker.write_text(json.dumps({
                "version": target.version,
                "actions": [{"path": ""}, {"path": str(ajena)},
                            {"path": str(target.config_dir)},
                            {"path": str(target.addons_dir / "x"),
                             "backup": str(ajena)}],
            }), encoding="utf-8")
            cwd = Path.cwd()
            with mock.patch.object(baddons.shutil, "rmtree") as rmtree:
                result = baddons.undo_migration(target)
            rmtree.assert_not_called()
            self.assertEqual(result.removed, [])
            self.assertEqual(result.restored, [])
            self.assertTrue((ajena / "dato.txt").is_file())
            self.assertTrue(cwd.is_dir())

    def test_undo_sin_marcador_no_hace_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = _config(Path(tmp) / "target")
            result = baddons.undo_migration(target)
            self.assertFalse(result.marker_found)
            self.assertEqual(result.restored, [])

    def test_undo_una_sola_vez(self):
        """Un segundo deshacer no puede volver a tocar nada (doble clic)."""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            plan = self._plan(self._addon(source), target.addons_dir / "addon")
            baddons.apply_migration([plan], target)
            first = baddons.undo_migration(target)
            second = baddons.undo_migration(target)
            self.assertTrue(first.marker_found)
            self.assertFalse(second.marker_found)
            self.assertEqual(second.removed, [])

    def test_migra_un_addon_legacy_de_un_solo_fichero(self):
        """Un addon legacy puede ser un ``.py`` suelto, no una carpeta.

        Se copiaba siempre con ``copytree``, que con un fichero revienta con
        ``NotADirectoryError``... después de haber apartado el addon que había
        en destino, que se quedaba en el ``.bak`` sin volver a su sitio.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "solo.py"
            source.write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "solo.py"
            destination.parent.mkdir(parents=True)
            destination.write_text("viejo", encoding="utf-8")
            plan = self._plan(self._addon(source, module="solo"), destination)
            result = baddons.apply_migration([plan], target)
            self.assertEqual(result.failed, [])
            self.assertEqual(destination.read_text(encoding="utf-8"), "nuevo")
            self.assertEqual(result.backed_up[0].read_text(encoding="utf-8"),
                             "viejo")

    def test_una_copia_que_falla_a_medias_no_toca_el_destino(self):
        """Si la copia se corta, el addon que había sigue entero en su sitio.

        Antes se apartaba primero al ``.bak`` y luego se copiaba: un fallo a
        mitad dejaba media copia en destino y el original escondido.
        """
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "addon"
            destination.mkdir(parents=True)
            (destination / "a.py").write_text("viejo", encoding="utf-8")

            def copia_rota(origin, dest, **kwargs):
                Path(dest).mkdir()
                (Path(dest) / "medio.py").write_text("x", encoding="utf-8")
                raise OSError("disco lleno")

            plan = self._plan(self._addon(source), destination)
            with mock.patch.object(baddons.shutil, "copytree",
                                   side_effect=copia_rota):
                result = baddons.apply_migration([plan], target)
            self.assertEqual(len(result.failed), 1)
            self.assertEqual(sorted(p.name for p in target.addons_dir.iterdir()),
                             ["addon"])
            self.assertEqual((destination / "a.py").read_text(), "viejo")
            self.assertIsNone(result.marker)

    def test_si_no_se_puede_poner_en_su_sitio_vuelve_el_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            (source / "a.py").write_text("nuevo", encoding="utf-8")
            target = _config(Path(tmp) / "target")
            destination = target.addons_dir / "addon"
            destination.mkdir(parents=True)
            (destination / "a.py").write_text("viejo", encoding="utf-8")
            plan = self._plan(self._addon(source), destination)
            with mock.patch.object(baddons.os, "replace",
                                   side_effect=OSError("ocupado")):
                result = baddons.apply_migration([plan], target)
            self.assertEqual(len(result.failed), 1)
            self.assertEqual((destination / "a.py").read_text(), "viejo")
            self.assertEqual(sorted(p.name for p in target.addons_dir.iterdir()),
                             ["addon"])

    def test_un_fichero_de_preferencias_que_no_se_copia_no_se_pierde(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin = _config(Path(tmp) / "origin")
            target = _config(Path(tmp) / "target")
            origin.config_dir.mkdir(parents=True)
            target.config_dir.mkdir(parents=True)
            (origin.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            (target.config_dir / "userpref.blend").write_bytes(b"VIEJO")
            items = baddons.preference_plan(origin, target)
            with mock.patch.object(baddons.shutil, "copy2",
                                   side_effect=OSError("sin permiso")):
                result = baddons.copy_preference_files(items, target)
            self.assertEqual(len(result.failed), 1)
            self.assertEqual((target.config_dir / "userpref.blend").read_bytes(),
                             b"VIEJO")
            self.assertEqual(result.backed_up, [])

    def test_no_copia_los_no_seleccionados(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "addon"
            source.mkdir()
            target = _config(Path(tmp) / "target")
            plan = self._plan(self._addon(source), target.addons_dir / "addon",
                              selected=False)
            result = baddons.apply_migration([plan], target)
            self.assertEqual(result.copied, [])
            self.assertEqual(result.skipped, [plan])


class PreferenceTest(unittest.TestCase):
    def _config(self, tmp, version):
        root = Path(tmp) / version
        return bc.BlenderConfig(version, root, "linux", root / "config",
                                root / "scripts", root / "extensions")

    def test_plan_marca_startup_como_no_seleccionado(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5")
            target = self._config(tmp, "5.3")
            source.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"P")
            (source.config_dir / "startup.blend").write_bytes(b"S")
            items = {item.key: item for item in baddons.preference_plan(source, target)}
            self.assertTrue(items["userpref"].exists)
            self.assertTrue(items["userpref"].selected)
            self.assertTrue(items["startup"].exists)
            # El startup no llega marcado: es la escena por defecto.
            self.assertFalse(items["startup"].selected)

    def test_plan_avisa_si_pisa_lo_que_ya_hay(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5")
            target = self._config(tmp, "5.3")
            source.config_dir.mkdir(parents=True)
            target.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            (target.config_dir / "userpref.blend").write_bytes(b"VIEJO")
            items = {item.key: item for item in baddons.preference_plan(source, target)}
            self.assertTrue(items["userpref"].overwrites)

    def test_plan_sin_ficheros_en_origen(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5")
            target = self._config(tmp, "5.3")
            items = baddons.preference_plan(source, target)
            self.assertFalse(any(item.exists for item in items))

    def test_aplicar_copia_y_respalda(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5")
            target = self._config(tmp, "5.3")
            source.config_dir.mkdir(parents=True)
            target.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            (target.config_dir / "userpref.blend").write_bytes(b"VIEJO")
            items = baddons.preference_plan(source, target)
            result = baddons.copy_preference_files(items, target)
            self.assertEqual(len(result.copied), 1)
            self.assertEqual((target.config_dir / "userpref.blend").read_bytes(),
                             b"NUEVO")
            self.assertEqual(len(result.backed_up), 1)
            self.assertEqual(result.backed_up[0].read_bytes(), b"VIEJO")

    def test_dry_run_no_toca_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5")
            target = self._config(tmp, "5.3")
            source.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            items = baddons.preference_plan(source, target)
            result = baddons.copy_preference_files(items, target, dry_run=True)
            self.assertEqual(len(result.copied), 1)
            self.assertFalse((target.config_dir / "userpref.blend").exists())


class FactoryResetTest(unittest.TestCase):
    def _config(self, tmp, version):
        root = Path(tmp) / version
        return bc.BlenderConfig(version, root, "linux", root / "config",
                                root / "scripts", root / "extensions")

    def _config_with_prefs(self, tmp, version):
        config = self._config(tmp, version)
        config.config_dir.mkdir(parents=True)
        (config.config_dir / "userpref.blend").write_bytes(b"MIO")
        return config

    def test_snapshot_aparta_la_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snapshot = bsnap.set_config_aside(config, label="v5.3")
            self.assertIsNotNone(snapshot)
            self.assertFalse(config.config_dir.exists())
            self.assertEqual((snapshot / "userpref.blend").read_bytes(), b"MIO")

    def test_snapshot_sin_config_no_hace_nada(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.3")
            self.assertIsNone(bsnap.set_config_aside(config))

    def test_restaurar_devuelve_la_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snapshot = bsnap.set_config_aside(config, label="v5.3")
            # Blender recrea una config "limpia"
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"FABRICA")
            aside = bsnap.restore_snapshot(config, snapshot)
            self.assertEqual((config.config_dir / "userpref.blend").read_bytes(),
                             b"MIO")
            # La limpia se aparta, no se pierde (se puede deshacer).
            self.assertIsNotNone(aside)
            self.assertEqual((aside / "userpref.blend").read_bytes(), b"FABRICA")
            # Y el guardado NO se consume: se puede volver a restaurar o borrar
            # a mano. Restaurar no puede ser la última oportunidad de recuperar
            # unos ajustes (se perdieron unos así).
            self.assertTrue(snapshot.is_dir())
            self.assertEqual((snapshot / "userpref.blend").read_bytes(), b"MIO")
            self.assertIn(snapshot, bsnap.snapshots_with_settings(config))

    def test_restaurar_reemplaza_en_vez_de_fundir(self):
        """Lo que hubiera en ``config`` y no esté en el guardado, desaparece.

        Con ``copytree(dirs_exist_ok=True)`` se fundían: un fichero que solo
        existía en la config viva sobrevivía a la restauración y el resultado
        no era el guardado, era una mezcla.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snapshot = bsnap.set_config_aside(config, label="v5.3")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"FABRICA")
            (config.config_dir / "solo_en_la_viva.txt").write_text("x")
            bsnap.restore_snapshot(config, snapshot)
            self.assertEqual((config.config_dir / "userpref.blend").read_bytes(),
                             b"MIO")
            self.assertFalse((config.config_dir / "solo_en_la_viva.txt").exists())

    def test_restaurar_sobre_config_vacia_no_aparca_nada(self):
        """Sin ajustes que apartar, el ``aside`` es ``None`` (no añade ruido)."""
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snapshot = bsnap.set_config_aside(config, label="v5.3")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "platform_support.txt").write_bytes(b"x")
            self.assertIsNone(bsnap.restore_snapshot(config, snapshot))
            self.assertEqual((config.config_dir / "userpref.blend").read_bytes(),
                             b"MIO")

    def test_poda_conserva_las_mas_nuevas(self):
        from datetime import datetime as _datetime

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snaps = []
            for second in range(4):
                config.config_dir.mkdir(parents=True, exist_ok=True)
                (config.config_dir / "userpref.blend").write_bytes(
                    str(second).encode())
                with mock.patch.object(bsnap, "datetime") as clock:
                    clock.now.return_value = _datetime(2026, 9, 21, 10, 0,
                                                       second)
                    snaps.append(bsnap.set_config_aside(config))
            removed = bsnap.prune_snapshots(config, 2)
            self.assertEqual(len(removed), 2)
            # Las que quedan son las dos más nuevas, de nueva a vieja.
            self.assertEqual(bsnap.snapshots_with_settings(config), [snaps[3], snaps[2]])

    def test_restaurar_no_tira_los_recientes_de_la_config_viva(self):
        """Una config sin ``userpref`` pero con recientes se aparta, no se borra."""
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snapshot = bsnap.set_config_aside(config, label="v5.3")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / bsnap.RECENT_FILES).write_text(
                "/proyectos/escena.blend\n", encoding="utf-8")
            aside = bsnap.restore_snapshot(config, snapshot)
            self.assertIsNotNone(aside)
            self.assertEqual((aside / bsnap.RECENT_FILES).read_text(),
                             "/proyectos/escena.blend\n")

    def test_poda_borra_antes_las_que_no_tienen_ajustes(self):
        """Las vacías no pueden echar del cupo al guardado con los ajustes."""
        from datetime import datetime as _datetime

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.3")
            snaps = []
            for second in range(4):
                config.config_dir.mkdir(parents=True, exist_ok=True)
                # La primera, la más vieja, es la única con ajustes de verdad.
                name = "userpref.blend" if second == 0 else bsnap.RECENT_FILES
                (config.config_dir / name).write_bytes(b"x")
                with mock.patch.object(bsnap, "datetime") as clock:
                    clock.now.return_value = _datetime(2026, 9, 21, 10, 0,
                                                       second)
                    snaps.append(bsnap.set_config_aside(config))
            bsnap.prune_snapshots(config, 2)
            # Quedan la de ajustes y la más nueva (que nunca se toca).
            self.assertEqual(bsnap.all_snapshots(config), [snaps[3], snaps[0]])

    def test_poda_sin_limite_no_borra(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snapshot = bsnap.set_config_aside(config)
            self.assertEqual(bsnap.prune_snapshots(config, 0), [])
            self.assertTrue(snapshot.is_dir())

    def test_poda_nunca_borra_la_protegida(self):
        from datetime import datetime as _datetime

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            first = None
            for second in range(4):
                config.config_dir.mkdir(parents=True, exist_ok=True)
                (config.config_dir / "userpref.blend").write_bytes(b"x")
                with mock.patch.object(bsnap, "datetime") as clock:
                    clock.now.return_value = _datetime(2026, 9, 21, 10, 0,
                                                       second)
                    snapshot = bsnap.set_config_aside(config)
                if second == 0:
                    first = snapshot
            # La más vieja (que se acaba de restaurar) no se borra aunque el
            # límite sea 1.
            bsnap.prune_snapshots(config, 1, protect=first)
            self.assertTrue(first.is_dir())
            self.assertEqual(len(bsnap.snapshots_with_settings(config)), 2)

    def test_snapshot_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            reset = bsnap.set_config_aside(config, label="v5.3.0")
            self.assertEqual(bsnap.snapshot_label(reset), "v5.3.0")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"x")
            factory = bsnap.set_config_aside(config, label="factory")
            self.assertEqual(bsnap.snapshot_label(factory), "factory")
            self.assertEqual(bsnap.snapshot_label(Path(tmp) / "otra"), "")

    def test_snapshot_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.3")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"x" * 100)
            (config.config_dir / "bookmarks.txt").write_text("a\nb\n\n")
            details = bsnap.snapshot_details(config.config_dir)
            self.assertTrue(details["has_userpref"])
            self.assertFalse(details["has_startup"])
            self.assertEqual(details["bookmarks"], 2)
            self.assertEqual(details["total"], 100 + 5)

    def test_varias_snapshots_y_orden(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            first = bsnap.set_config_aside(config, label="a")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"DOS")
            second = bsnap.set_config_aside(config, label="b")
            shots = bsnap.snapshots_with_settings(config)
            self.assertEqual(len(shots), 2)
            # La más nueva va primero.
            self.assertEqual(shots[0], second)
            self.assertIn(first, shots)

    def test_borrar_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            snapshot = bsnap.set_config_aside(config, label="a")
            self.assertTrue(bsnap.delete_snapshot(snapshot))
            self.assertEqual(bsnap.snapshots_with_settings(config), [])

    def test_sin_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.3")
            self.assertEqual(bsnap.snapshots_with_settings(config), [])

    def test_las_snapshots_vacias_no_cuentan_como_ajustes(self):
        """Restaurar tiene que apuntar a la instantánea con ajustes, no a una vacía.

        Al restaurar se aparta la config que hubiera; si estaba vacía queda una
        carpeta sin ficheros que, siendo la más nueva, tapaba los ajustes de
        verdad y el botón de restaurar no recuperaba nada.
        """
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            real = bsnap.set_config_aside(config, label="v5.3")
            config.config_dir.mkdir(parents=True)
            vacia = bsnap.set_config_aside(config, label="factory")
            self.assertIsNotNone(vacia)
            # La cruda las ve las dos; la que se ofrece restaurar, solo la real.
            self.assertEqual(len(bsnap.all_snapshots(config)), 2)
            self.assertEqual(bsnap.snapshots_with_settings(config), [real])

    def test_snapshot_date(self):
        from datetime import datetime as _datetime

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config_with_prefs(tmp, "5.3")
            with mock.patch.object(bsnap, "datetime") as reloj:
                reloj.now.return_value = _datetime(2026, 9, 18, 14, 21, 31)
                snapshot = bsnap.set_config_aside(config, label="v5.3")
            self.assertEqual(bsnap.snapshot_date(snapshot), "2026-09-18 14:21")
            self.assertEqual(bsnap.snapshot_date(Path(tmp) / "otra"), "")


class SummaryTest(unittest.TestCase):
    def test_cuenta_por_estado(self):
        addon = baddons.Addon(kind="legacy", module="x", name="X", version="",
                         min_version="", max_version="", path=Path("/tmp/x"))
        plans = [
            baddons.AddonPlan(addon, bc.OK, "", Path("/a")),
            baddons.AddonPlan(addon, bc.WARN, bc.REASON_UNKNOWN_VERSION, Path("/b")),
            baddons.AddonPlan(addon, bc.WARN, bc.REASON_WHEEL_ABI, Path("/c")),
            baddons.AddonPlan(addon, bc.BLOCKED, bc.REASON_PLATFORM, Path("/d")),
        ]
        self.assertEqual(baddons.summary_counts(plans),
                         {bc.OK: 1, bc.WARN: 2, bc.BLOCKED: 1})


if __name__ == "__main__":
    unittest.main()
