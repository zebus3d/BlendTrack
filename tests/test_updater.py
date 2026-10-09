import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from services import updater


class VersionCompareTests(unittest.TestCase):
    def test_is_newer(self):
        self.assertTrue(updater.is_newer("1.0.0", "1.0.1"))
        self.assertTrue(updater.is_newer("1.0", "v2.0"))
        self.assertTrue(updater.is_newer("1.0.0", "1.10.0"))
        self.assertFalse(updater.is_newer("1.2.0", "1.1.9"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("1.0.0", ""))

    def test_is_newer_con_las_formas_que_se_ven_de_verdad(self):
        # El tag de la release lleva "v" delante.
        self.assertTrue(updater.is_newer("1.6.0", "v1.7.0"))
        self.assertTrue(updater.is_newer("1.7.0", "v1.8.0"))
        # En modo fuente la version es el describe del checkout, con sufijo.
        self.assertFalse(updater.is_newer("1.7.0-19-g24a0b43", "v1.7.0"))
        self.assertTrue(updater.is_newer("1.7.0-19-g24a0b43", "v1.8.0"))
        # Instalada MAS nueva que la última release (p. ej. la pre-release que
        # se acaba de promover): no hay nada que ofrecer.
        self.assertFalse(updater.is_newer("1.8.0", "v1.7.0"))
        # La numeración del esquema viejo (parche = nº de build) también ordena.
        self.assertTrue(updater.is_newer("1.1.203", "v1.2.39"))
        self.assertTrue(updater.is_newer("1.2.39", "v1.7.0"))

    def test_asset_for(self):
        self.assertEqual(updater.asset_for("linux"), "BlenderManager-x86_64.AppImage")
        self.assertEqual(updater.asset_for("windows"), "BlenderManager-windows-x86_64.zip")
        self.assertEqual(updater.asset_for("darwin"), "BlenderManager-macos.zip")
        self.assertIsNone(updater.asset_for("plan9"))


class ParseReleaseTests(unittest.TestCase):
    def test_parse(self):
        payload = {
            "tag_name": "v1.2.0",
            "assets": [
                {"name": "a", "browser_download_url": "http://a"},
                {"name": "b", "browser_download_url": "http://b"},
            ],
        }
        tag, assets = updater._parse_release(payload)
        self.assertEqual(tag, "v1.2.0")
        self.assertEqual([asset["name"] for asset in assets], ["a", "b"])
        self.assertEqual(assets[0]["url"], "http://a")

    def test_parse_empty(self):
        tag, assets = updater._parse_release({})
        self.assertEqual(tag, "")
        self.assertEqual(assets, [])


class _FakeResponse:
    def __init__(self, body=b"", headers=None):
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


class ChecksumTests(unittest.TestCase):
    def test_checksum_for(self):
        body = (
            b"abc123  BlenderManager-x86_64.AppImage\n"
            b"def456  ./BlenderManager-linux/BlenderManager-x86_64.AppImage\n"
        )
        assets = [{"name": "checksums.txt", "url": "http://checksums"}]
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            value = updater.checksum_for(assets, "BlenderManager-x86_64.AppImage")
        self.assertEqual(value, "abc123")

    def test_checksum_missing_asset(self):
        self.assertIsNone(updater.checksum_for([], "whatever"))


class LatestReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = updater.cache_dir
        updater.cache_dir = lambda: Path(self.tmp.name)

    def tearDown(self):
        updater.cache_dir = self._original
        self.tmp.cleanup()

    def test_latest_release(self):
        payload = json.dumps({
            "tag_name": "v2.0.0",
            "assets": [{"name": "x", "browser_download_url": "http://x"}],
        }).encode("utf-8")
        response = _FakeResponse(payload, headers={"ETag": '"etag1"'})
        with mock.patch("urllib.request.urlopen", return_value=response):
            tag, assets = updater.latest_release()
        self.assertEqual(tag, "v2.0.0")
        self.assertEqual(assets[0]["name"], "x")
        self.assertTrue((Path(self.tmp.name) / "updates" / "release.json").is_file())

    def test_latest_release_network_error(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertIsNone(updater.latest_release())


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = updater.cache_dir
        updater.cache_dir = lambda: Path(self.tmp.name)

    def tearDown(self):
        updater.cache_dir = self._original
        self.tmp.cleanup()

    def test_cleanup_removes_downloads_but_keeps_cache(self):
        base = updater.updates_dir()
        (base / "staging-1").mkdir(parents=True)
        (base / "BlenderManager-x86_64.AppImage").write_text("binario grande")
        (base / "release.json").write_text("{}")
        (base / "release.etag").write_text("etag")
        updater.cleanup_staging()
        self.assertFalse((base / "staging-1").exists())
        self.assertFalse((base / "BlenderManager-x86_64.AppImage").exists())
        self.assertTrue((base / "release.json").is_file())
        self.assertTrue((base / "release.etag").is_file())

    def test_cleanup_partials(self):
        folder = Path(self.tmp.name) / "blenders"
        folder.mkdir()
        (folder / "blender.tar.xz.part").write_text("a medias")
        (folder / "blender.tar.xz").write_text("completo")
        updater.cleanup_partials(folder)
        self.assertFalse((folder / "blender.tar.xz.part").exists())
        self.assertTrue((folder / "blender.tar.xz").is_file())

    def test_cleanup_missing_folder(self):
        updater.cleanup_partials("/nonexistent/path/xyz")


class StartupCleanupTests(unittest.TestCase):
    """El arranque tiene que limpiar los restos de la sesión anterior.

    Las dos funciones estaban bien y con tests, pero al portar la interfaz se
    perdió quien las llamaba (vivían en ``root.py``): el AppImage que se
    descarga en cada actualización, ~73 MB, se quedaba en el caché para siempre.
    """

    def test_el_arranque_llama_a_las_dos_limpiezas(self):
        from unittest import mock

        import main
        from services import settings as settings_service

        from services import channels

        settings = settings_service.Settings(folders=[
            settings_service.Folder("/tmp/blenders", list(channels.BUILD_TYPES)),
        ])
        with mock.patch.object(main.updater, "cleanup_staging") as restos, \
                mock.patch.object(main.updater, "cleanup_partials") as partes:
            main._clean_previous_session(settings)
        self.assertTrue(restos.called)
        partes.assert_called_once_with("/tmp/blenders")

    def test_el_arranque_limpia_todas_las_carpetas_de_descarga(self):
        from unittest import mock

        import main
        from services import channels, settings as settings_service

        settings = settings_service.Settings(folders=[
            settings_service.Folder("/tmp/blenders", [channels.TYPE_STABLE,
                                                      channels.TYPE_DAILY]),
            settings_service.Folder("/tmp/lts", [channels.TYPE_LTS]),
        ])
        with mock.patch.object(main.updater, "cleanup_staging"), \
                mock.patch.object(main.updater, "cleanup_partials") as partes:
            main._clean_previous_session(settings)
        self.assertEqual([call.args[0] for call in partes.call_args_list],
                         ["/tmp/blenders", "/tmp/lts"])

    def test_el_arranque_no_toca_las_carpetas_de_solo_lectura(self):
        """Un .part no puede estar ahí, y borrar es escribir."""
        from unittest import mock

        import main
        from services import channels, settings as settings_service

        settings = settings_service.Settings(folders=[
            settings_service.Folder("/tmp/blenders", list(channels.BUILD_TYPES)),
            settings_service.Folder("/tmp/viejos", [], writable=False),
        ])
        with mock.patch.object(main.updater, "cleanup_staging"), \
                mock.patch.object(main.updater, "cleanup_partials") as partes:
            main._clean_previous_session(settings)
        self.assertEqual([call.args[0] for call in partes.call_args_list],
                         ["/tmp/blenders"])


class AppImageApplyTests(unittest.TestCase):
    """Self-replace de la AppImage en Linux.

    El caso 1 (sin $APPIMAGE) es el que mordió a un usuario en Linux Mint:
    lanzaba la AppImage desde un launcher que no propagaba la variable, el
    updater no podía reemplazar nada y la app se cerraba igual, dejándole
    con un fichero descargado sin bit de ejecución que no podía abrir.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _archive(self):
        # Simula el .AppImage descargado (sin +x, como lo deja el downloader).
        archive = self.root / "update" / "BlenderManager-x86_64.AppImage"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"contenido")
        archive.chmod(0o644)
        return archive

    def test_make_executable_sets_exec_bit(self):
        archive = self._archive()
        self.assertFalse(archive.stat().st_mode & 0o111)
        updater._make_executable(archive)
        self.assertTrue(archive.stat().st_mode & 0o111)

    def test_make_executable_missing_file_does_not_raise(self):
        updater._make_executable(self.root / "no-existe")

    def test_apply_without_appimage_env_falls_back(self):
        archive = self._archive()
        with mock.patch.dict(updater.os.environ, {}, clear=False):
            updater.os.environ.pop("APPIMAGE", None)
            self.assertFalse(updater._apply_appimage(archive))
        # El fallback deja la copia ejecutable por si el usuario la abre a mano.
        self.assertTrue(archive.stat().st_mode & 0o111)

    def test_apply_target_not_a_file_falls_back(self):
        archive = self._archive()
        folder = self.root / "carpeta"
        folder.mkdir()
        with mock.patch.dict(updater.os.environ, {"APPIMAGE": str(folder)}):
            self.assertFalse(updater._apply_appimage(archive))
        self.assertTrue(archive.stat().st_mode & 0o111)

    def test_apply_success_replaces_and_relaunches(self):
        archive = self._archive()
        target = self.root / "instalada.AppImage"
        target.write_bytes(b"viejo")
        with mock.patch.dict(updater.os.environ, {"APPIMAGE": str(target)}), \
                mock.patch.object(updater.subprocess, "Popen") as popen:
            self.assertTrue(updater._apply_appimage(archive))
        self.assertEqual(target.read_bytes(), b"contenido")
        self.assertTrue(target.stat().st_mode & 0o111)
        popen.assert_called_once()

    def test_apply_replace_failure_keeps_old_and_falls_back(self):
        archive = self._archive()
        target = self.root / "instalada.AppImage"
        target.write_bytes(b"viejo")
        with mock.patch.dict(updater.os.environ, {"APPIMAGE": str(target)}), \
                mock.patch.object(updater.os, "replace",
                                  side_effect=OSError("read-only")):
            self.assertFalse(updater._apply_appimage(archive))
        # El binario antiguo sigue intacto: no dejamos al usuario sin app.
        self.assertEqual(target.read_bytes(), b"viejo")
        self.assertTrue(archive.stat().st_mode & 0o111)


class SourceUpdateTests(unittest.TestCase):
    """Actualizar un checkout en modo fuente (git pull) sin red ni repo real."""

    def test_no_git_checkout(self):
        with mock.patch.object(updater, "source_root", return_value=None):
            self.assertEqual(updater.source_update(), (False, "no-git"))
            self.assertFalse(updater.relaunch_source())

    def test_source_root_without_git(self):
        with mock.patch.object(updater, "APP_DIR", Path("/tmp/no-repo-xyz")):
            self.assertIsNone(updater.source_root())

    def test_source_root_only_looks_in_app_dir(self):
        # No debe subir a un repo padre: si APP_DIR cuelga de otro repo, no
        # cuenta como checkout propio.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            nested = Path(tmp) / "tools" / "BlenderManager"
            nested.mkdir(parents=True)
            with mock.patch.object(updater, "APP_DIR", nested):
                self.assertIsNone(updater.source_root())

    def test_refuses_when_dirty(self):
        with mock.patch.object(updater, "source_root", return_value=Path("/tmp/repo")), \
                mock.patch.object(updater.subprocess, "check_output",
                                  return_value=b" M src/main.py\n"):
            self.assertEqual(updater.source_update(), (False, "dirty"))

    def test_pulls_when_clean(self):
        result = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(updater, "source_root", return_value=Path("/tmp/repo")), \
                mock.patch.object(updater.subprocess, "check_output", return_value=b""), \
                mock.patch.object(updater.subprocess, "run", return_value=result) as run:
            self.assertEqual(updater.source_update(), (True, "ok"))
            self.assertIn("pull", run.call_args.args[0])
            self.assertIn("--ff-only", run.call_args.args[0])

    def test_reports_failed_pull(self):
        result = mock.Mock(returncode=1, stderr="boom")
        with mock.patch.object(updater, "source_root", return_value=Path("/tmp/repo")), \
                mock.patch.object(updater.subprocess, "check_output", return_value=b""), \
                mock.patch.object(updater.subprocess, "run", return_value=result):
            self.assertEqual(updater.source_update(), (False, "failed"))

    def test_app_version_uses_describe_in_source(self):
        # En modo fuente version.py es 0.0.0: la versión mostrada es el describe
        # del checkout, que dice cuántos commits va por delante del tag.
        with mock.patch.object(updater.version, "__version__", "0.0.0"), \
                mock.patch.object(updater, "source_describe",
                                  return_value="v1.2.0-19-g24a0b43"):
            self.assertEqual(updater.app_version(), "1.2.0-19-g24a0b43")

    def test_app_version_cae_al_tag_sin_describe(self):
        with mock.patch.object(updater.version, "__version__", "0.0.0"), \
                mock.patch.object(updater, "source_describe", return_value=""), \
                mock.patch.object(updater, "source_tag", return_value="v1.2.3"):
            self.assertEqual(updater.app_version(), "1.2.3")

    def test_app_version_uses_injected_version(self):
        with mock.patch.object(updater.version, "__version__", "1.9.9"), \
                mock.patch.object(updater, "source_describe",
                                  return_value="v1.2.3"):
            self.assertEqual(updater.app_version(), "1.9.9")

    def test_pull_sin_cambios_reporta_up_to_date(self):
        result = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(updater, "source_root", return_value=Path("/tmp/repo")), \
                mock.patch.object(updater.subprocess, "check_output", return_value=b""), \
                mock.patch.object(updater, "_git_head", side_effect=["sha1", "sha1"]), \
                mock.patch.object(updater.subprocess, "run", return_value=result):
            self.assertEqual(updater.source_update(), (True, "up-to-date"))


class WindowsUpdateLayoutTests(unittest.TestCase):
    """El updater de Windows debe encontrar el `.exe` en cualquier layout.

    El asset pasó a ser **onefile** (un único `BlenderManager.exe` en la raíz del
    zip) para que nadie pueda romper la instalación borrando `_internal`. Antes
    `_apply_windows` daba por hecho que `extract()` devolvía la carpeta que
    contiene el ejecutable; con un zip plano eso ya no se cumple.
    """

    def _apply_con(self, entries):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive = base / "update.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for name in entries:
                    zf.writestr(name, "contenido")
            with mock.patch.object(updater, "updates_dir",
                                   return_value=base / "updates"), \
                    mock.patch.object(updater.subprocess, "Popen") as popen:
                ok = updater._apply_windows(archive)
        return ok, popen

    def test_onefile_un_solo_exe_en_la_raiz(self):
        ok, popen = self._apply_con([updater.EXE_NAME])
        self.assertTrue(ok)
        lanzado = popen.call_args.args[0]
        self.assertTrue(lanzado[0].endswith(updater.EXE_NAME))
        self.assertIn("--apply-update", lanzado)

    def test_onefolder_con_carpeta(self):
        ok, popen = self._apply_con([f"BlenderManager/{updater.EXE_NAME}"])
        self.assertTrue(ok)
        self.assertTrue(popen.call_args.args[0][0].endswith(updater.EXE_NAME))


if __name__ == "__main__":
    unittest.main()


class MacosUpdateTests(unittest.TestCase):
    """macOS: un helper reemplaza el .app cuando la app se cierra (estilo Sparkle).

    En el CI (Ubuntu) no hay bundle ni `/bin/sh` de macOS, así que se mockean
    `sys.executable`, `updates_dir` y `Popen`, y se comprueba el script generado.
    """

    def _fake_bundle(self, base):
        bundle = base / "BlenderManager.app"
        (bundle / "Contents" / "MacOS").mkdir(parents=True)
        return bundle / "Contents" / "MacOS" / "BlenderManager"

    def _zip(self, base):
        # El contenido da igual: la extracción (ditto) se mockea, porque en
        # Linux no existe.
        archive = base / "BlenderManager-macos.zip"
        archive.write_bytes(b"zip")
        return archive

    @staticmethod
    def _extrae_app(_zip_path, dest):
        """Simula `ditto -x -k`: deja el Blender.app dentro del staging."""
        app = Path(dest) / "BlenderManager.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "MacOS" / "BlenderManager").write_text("bin")

    def test_app_bundle_detecta_el_bundle(self):
        from pathlib import Path as _Path

        self.assertEqual(
            updater._app_bundle(
                "/Applications/BlenderManager.app/Contents/MacOS/BlenderManager"),
            _Path("/Applications/BlenderManager.app"))
        self.assertIsNone(updater._app_bundle("/usr/bin/python3"))
        self.assertIsNone(
            updater._app_bundle("/Applications/Foo/Contents/MacOS/Bar"))

    def test_aplica_con_un_helper_y_extrae_con_ditto(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            exe = self._fake_bundle(base)
            archive = self._zip(base)
            updates = base / "updates"
            with mock.patch.object(updater.sys, "executable", str(exe)), \
                    mock.patch.object(updater, "updates_dir", return_value=updates), \
                    mock.patch.object(updater.macos_dmg, "extract_zip",
                                      side_effect=self._extrae_app) as extraer, \
                    mock.patch.object(updater, "extract") as extractor_python, \
                    mock.patch.object(updater, "_open_fallback") as fallback, \
                    mock.patch.object(updater.subprocess, "Popen") as popen:
                ok = updater._apply_macos(archive)

            self.assertTrue(ok)
            fallback.assert_not_called()
            # Se extrae con ditto, NO con zipfile (que rompe el bundle).
            extraer.assert_called_once()
            extractor_python.assert_not_called()
            popen.assert_called_once()
            # El helper espera al PID, mueve, copia con ditto y relanza.
            script = next(updates.rglob("apply-update.sh"))
            texto = script.read_text(encoding="utf-8")
            self.assertIn(f"PID={os.getpid()}", texto)
            self.assertIn("ditto", texto)
            self.assertIn("xattr", texto)
            self.assertIn("/usr/bin/open", texto)

    def test_sin_permiso_cae_al_plan_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            exe = self._fake_bundle(base)
            archive = self._zip(base)
            with mock.patch.object(updater.sys, "executable", str(exe)), \
                    mock.patch.object(updater.os, "access", return_value=False), \
                    mock.patch.object(updater, "_open_fallback") as fallback:
                ok = updater._apply_macos(archive)

        self.assertFalse(ok)
        fallback.assert_called_once()

    def test_zip_sin_app_cae_al_plan_b(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            exe = self._fake_bundle(base)
            archive = self._zip(base)
            with mock.patch.object(updater.sys, "executable", str(exe)), \
                    mock.patch.object(updater, "updates_dir",
                                      return_value=base / "updates"), \
                    mock.patch.object(updater.macos_dmg, "extract_zip"), \
                    mock.patch.object(updater, "_open_fallback") as fallback:
                ok = updater._apply_macos(archive)

        self.assertFalse(ok)
        fallback.assert_called_once()
