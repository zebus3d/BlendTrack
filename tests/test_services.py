import json
import os
import sys
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock
from pathlib import Path

import services.settings as settings_module
from model.build import Build, favorite_key
from services import api, channels, detector, elevate, installed, macos_dmg, tls
from services.extractor import extract, is_archive


def make_build(version, risk, branch, filename, platform="linux", arch="x86_64", mtime=0,
               build_hash="", experimental=False):
    return Build(version, branch, risk, platform, arch, "https://example/" + filename, filename,
                 mtime=mtime, build_hash=build_hash, experimental=experimental)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.builds = [
            make_build("5.2.1", "stable", "v52", "b-5.2.1-linux.x86_64.tar.xz", mtime=100),
            make_build("5.3.0", "alpha", "main", "b-5.3.0-alpha-linux.x86_64.tar.xz", mtime=200),
            make_build("4.5.5", "stable", "v45", "b-4.5.5-linux.x86_64.tar.xz", mtime=50),
            make_build("5.2.0", "stable", "v52", "b-5.2.0-windows.amd64.zip", platform="windows", arch="amd64"),
        ]

    def test_available_filters_platform_and_arch(self):
        result = api.available_for(self.builds, "linux", "x86_64")
        self.assertEqual(len(result), 3)
        self.assertTrue(all(build.platform == "linux" for build in result))

    def test_available_sorted_desc(self):
        result = api.available_for(self.builds, "linux", "x86_64")
        self.assertEqual(result[0].version, "5.3.0")
        self.assertEqual(result[-1].version, "4.5.5")

    def test_available_prefers_extension(self):
        builds = [
            make_build("5.2.1", "stable", "v52", "b-linux.x86_64.tar.xz", mtime=1),
            make_build("5.2.1", "stable", "v52", "b-linux.x86_64.zip", mtime=2),
        ]
        result = api.available_for(builds, "linux", "x86_64")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].filename.endswith(".tar.xz"))

    def test_to_build(self):
        entry = {
            "version": "5.2.1",
            "branch": "v52",
            "risk_id": "stable",
            "platform": "linux",
            "architecture": "x86_64",
            "url": "https://example/f.tar.xz",
            "file_name": "f.tar.xz",
            "file_size": 123,
            "checksum": "ABC",
            "file_mtime": 10,
            "hash": "b787619620d1",
        }
        build = api._to_build(entry)
        self.assertEqual(build.version, "5.2.1")
        self.assertEqual(build.checksum, "ABC")
        self.assertEqual(build.build_hash, "b787619620d1")
        self.assertTrue(build.is_lts)

    def test_to_build_experimental_flag(self):
        entry = {
            "version": "4.5.0", "branch": "geometry-nodes", "risk_id": "alpha",
            "platform": "linux", "architecture": "x86_64", "url": "u",
            "file_name": "f.tar.xz",
        }
        self.assertTrue(api._to_build(entry, experimental=True).experimental)
        self.assertFalse(api._to_build(entry).experimental)

    def test_available_no_mezcla_blender_y_fork_de_la_misma_version(self):
        """Bforartists 5.2.0 y Blender 5.2.0 comparten número: no se pisan."""
        blender = make_build("5.2.0", "stable", "v52", "b.tar.xz", mtime=1)
        bfa = make_build("5.2.0", "stable", "bforartists", "f.tar.xz", mtime=1)
        bfa.fork = "bforartists"
        result = api.available_for([blender, bfa], "linux", "x86_64")
        self.assertEqual(len(result), 2)
        self.assertEqual(sorted(b.fork for b in result), ["", "bforartists"])

    def test_una_plataforma_de_fork_sin_el_formato_preferido_no_desaparece(self):
        """UPBGE en Linux solo publica .tar.gz: no puede caer por no ser .xz.

        La preferencia de formato se aplicaba al conjunto entero, así que en
        cuanto hubiera un .tar.xz de Blender, todas las versiones de UPBGE
        (Linux) desaparecían de la tienda.
        """
        blender = make_build("5.2.0", "stable", "v52", "b.tar.xz", mtime=1)
        upbge = make_build("0.53", "alpha", "upbge-weekly",
                           "upbge-0.53.tar.gz", mtime=1)
        upbge.fork = "upbge"
        result = api.available_for([blender, upbge], "linux", "x86_64")
        self.assertEqual(sorted(b.fork for b in result), ["", "upbge"])

    def test_windows_amd64_se_normaliza_y_coincide_con_el_filtro(self):
        # La API llama "amd64" a la arquitectura de Windows; la barra de filtros
        # pide "x86_64". Sin normalizar, elegir Windows dejaba la tienda vacía.
        entry = {
            "version": "5.2.1", "branch": "v52", "risk_id": "stable",
            "platform": "windows", "architecture": "amd64", "url": "u",
            "file_name": "blender-5.2.1-windows-x64.zip",
            "file_extension": "zip",
        }
        build = api._to_build(entry)
        self.assertEqual(build.arch, "x86_64")
        self.assertEqual(len(api.available_for([build], "windows", "x86_64")), 1)
        # Y el nombre sin normalizar (el que pedía el filtro antes) no encuentra
        # nada: eso era el bug.
        self.assertEqual(api.available_for([build], "windows", "amd64"), [])

    def test_available_prefers_dmg_on_darwin(self):
        # En macOS la API solo publica .dmg; que quede claro en los tests,
        # porque de ahí viene que no se pueda extraer.
        builds = [
            make_build("5.2.1", "stable", "v52", "b-darwin.arm64.dmg",
                       platform="darwin", arch="arm64"),
        ]
        result = api.available_for(builds, "darwin", "arm64")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].filename.endswith(".dmg"))

    def test_load_cache_ignores_unknown_fields(self):
        # Un caché escrito por una versión anterior de la app no debe dejar la
        # tienda vacía por traer un campo que ya no existe.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "builds.json"
            payload = {
                "saved_at": 10 ** 12,
                "builds": [{
                    "version": "5.2.1", "branch": "v52", "risk": "stable",
                    "platform": "linux", "arch": "x86_64", "url": "u",
                    "filename": "f.tar.xz", "campo_que_ya_no_existe": "x",
                }],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            original = api.cache_path
            api.cache_path = lambda: path
            try:
                builds = api.load_cache()
            finally:
                api.cache_path = original
            self.assertEqual(len(builds), 1)
            self.assertEqual(builds[0].version, "5.2.1")

    def test_304_reutiliza_la_cache(self):
        """Si el listado no cambió, no se vuelve a bajar: se usa la caché."""
        cached = [
            make_build("5.2.1", "stable", "v52", "b.tar.xz"),
            make_build("5.3.0", "alpha", "main", "b.tar.xz", experimental=True),
        ]
        with mock.patch.object(api, "_fetch_json",
                               return_value=(None, "etag")):
            builds, etags = api.fetch_builds(
                etags={api.API_URL: "e1", api.EXPERIMENTAL_URL: "e2"},
                cached=cached)
        self.assertEqual(builds, cached)
        # Sin respuesta nueva, se conserva el etag que ya teníamos.
        self.assertEqual(etags[api.API_URL], "e1")

    def test_guarda_y_lee_los_etags(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "builds.json"
            original = api.cache_path
            api.cache_path = lambda: path
            try:
                api.save_cache([], {api.API_URL: "abc"})
                self.assertEqual(api.load_etags(), {api.API_URL: "abc"})
            finally:
                api.cache_path = original

    def test_fetch_json_304_devuelve_none(self):
        import urllib.error

        error = urllib.error.HTTPError("https://x", 304, "Not Modified", {},
                                       None)
        with mock.patch.object(api.urllib.request, "urlopen", side_effect=error):
            self.assertEqual(api._fetch_json("https://x", etag="e"),
                             (None, "e"))


class GetBuildsTests(unittest.TestCase):
    """``api.get_builds``: el listado y su caché en disco, sin red."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(api, "cache_dir",
                                    lambda: Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self.blender = make_build("5.2.1", "stable", "v52", "blender-5.2.1.tar.xz")
        self.bfa = Build("5.2.0", "bforartists", "stable", "linux", "x86_64",
                         "https://cloud.bforartists.de/x", "Bforartists-5.2.0.tar.xz",
                         fork="bforartists")

    def test_si_no_se_puede_guardar_el_cache_se_devuelve_lo_descargado(self):
        """Un disco lleno o sin permiso no puede tirar el listado recién bajado."""
        fresh = [self.blender]
        with mock.patch.object(api, "fetch_builds", return_value=(fresh, {})), \
                mock.patch.object(api, "save_cache",
                                  side_effect=OSError("disco lleno")):
            self.assertEqual(api.get_builds(force=True), fresh)

    def test_el_cache_no_trae_versiones_de_un_fork_apagado(self):
        """Un caché escrito con Bforartists encendido no la enseña al apagarlo."""
        api.save_cache([self.blender, self.bfa])
        self.assertEqual(api.get_builds(forks=()), [self.blender])
        self.assertEqual(api.get_builds(forks=("bforartists",)),
                         [self.blender, self.bfa])

    def test_sin_red_el_cache_viejo_tampoco_trae_forks_apagados(self):
        api.save_cache([self.blender, self.bfa])
        with mock.patch.object(api, "fetch_builds",
                               side_effect=OSError("sin red")):
            self.assertEqual(api.get_builds(force=True, forks=()),
                             [self.blender])


class ChannelFilterTests(unittest.TestCase):
    """Filtro de canal de la tienda (incluidas las ramas experimentales)."""

    def _builds(self):
        return [
            make_build("5.2.1", "stable", "v52", "b.tar.xz", mtime=1),
            make_build("5.3.0", "alpha", "main", "b.tar.xz", mtime=2),
            make_build("4.5.13", "stable", "v45", "b.tar.xz", mtime=3),
            make_build("4.5.0", "alpha", "geometry-nodes", "b.tar.xz", mtime=4,
                       experimental=True),
        ]

    def test_experimental_only_in_its_own_channel(self):
        builds = self._builds()
        experimental = api.filter_builds(builds, "experimental")
        self.assertEqual([build.branch for build in experimental], ["geometry-nodes"])
        # "Todas" las incluye (es literalmente todas); los canales concretos no.
        self.assertIn("geometry-nodes",
                      [b.branch for b in api.filter_builds(builds, "all")])
        for channel in ("lts", "stable", "lts_stable", "daily"):
            selected = api.filter_builds(builds, channel)
            self.assertNotIn("geometry-nodes", [build.branch for build in selected])

    def test_channels_keep_their_meaning(self):
        builds = self._builds()
        self.assertEqual(len(api.filter_builds(builds, "lts")), 2)
        self.assertEqual(len(api.filter_builds(builds, "stable")), 0)
        self.assertEqual(len(api.filter_builds(builds, "lts_stable")), 2)
        self.assertEqual(len(api.filter_builds(builds, "daily")), 1)
        # "Todas" enseña el catálogo entero, experimentales incluidas.
        self.assertEqual(len(api.filter_builds(builds, "all")), 4)

    def test_search(self):
        builds = self._builds()
        result = api.filter_builds(builds, "all", "5.3")
        self.assertEqual([build.version for build in result], ["5.3.0"])

    def test_cada_fork_en_su_canal_y_fuera_de_los_demas(self):
        bfa = make_build("5.2.0", "stable", "bforartists", "f.tar.xz")
        bfa.fork = "bforartists"
        upbge = make_build("0.53", "alpha", "upbge-weekly", "f.tar.gz")
        upbge.fork = "upbge"
        upbge.experimental = True
        builds = self._builds() + [bfa, upbge]

        self.assertEqual(
            [b.fork for b in api.filter_builds(builds, "bforartists")],
            ["bforartists"])
        self.assertEqual(
            [b.fork for b in api.filter_builds(builds, "upbge")], ["upbge"])
        # "Todas" mezcla los forks con Blender (es el catálogo entero).
        self.assertEqual(
            {b.fork for b in api.filter_builds(builds, "all")},
            {"", "bforartists", "upbge"})
        # Los canales concretos de Blender no mezclan forks: son otros programas.
        for channel in ("lts", "stable", "daily", "lts_stable"):
            selected = api.filter_builds(builds, channel)
            self.assertTrue(all(not b.fork for b in selected), channel)
        # El de experimentales tampoco se traga las alfa de UPBGE.
        self.assertTrue(all(not b.fork
                            for b in api.filter_builds(builds, "experimental")))

    def test_favoritos_incluye_forks(self):
        bfa = make_build("5.2.0", "stable", "bforartists", "f.tar.xz")
        bfa.fork = "bforartists"
        marked = [bfa.favorite_key]
        result = api.filter_builds([bfa], "favorites", favorites=marked)
        self.assertEqual([b.fork for b in result], ["bforartists"])


class FavoriteFilterTests(unittest.TestCase):
    """El canal "Favoritos" es transversal: no es un canal de Blender."""

    def _builds(self):
        return [
            make_build("4.5.13", "stable", "v45", "a.tar.xz"),
            make_build("5.3.0", "alpha", "main", "b.tar.xz"),
            make_build("4.5.0", "alpha", "geometry-nodes", "c.tar.xz",
                       experimental=True),
        ]

    def test_solo_las_marcadas(self):
        builds = self._builds()
        marked = [favorite_key("v45", "4.5.13")]
        selected = api.filter_builds(builds, "favorites", "", marked)
        self.assertEqual([b.version for b in selected], ["4.5.13"])

    def test_incluye_experimentales_si_estan_marcadas(self):
        # A diferencia del resto de canales, aqui no se excluyen: si la has
        # marcado, la quieres ver.
        builds = self._builds()
        marked = [favorite_key("geometry-nodes", "4.5.0")]
        selected = api.filter_builds(builds, "favorites", "", marked)
        self.assertEqual([b.version for b in selected], ["4.5.0"])

    def test_la_busqueda_sigue_aplicando(self):
        builds = self._builds()
        marked = [b.favorite_key for b in builds]
        selected = api.filter_builds(builds, "favorites", "5.3", marked)
        self.assertEqual([b.version for b in selected], ["5.3.0"])

    def test_sin_marcadas_sale_vacio(self):
        self.assertEqual(api.filter_builds(self._builds(), "favorites"), [])


class InstalledTests(unittest.TestCase):
    def test_scan_finds_versions_and_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "blender-4.5.13-linux-x64"
            folder.mkdir()
            (folder / "blender").write_text("#!/bin/sh\n")
            (root / "not-a-build").mkdir()
            results = installed.scan(root, "linux")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].version, "4.5.13")
            self.assertTrue(results[0].can_launch)

    def test_scan_missing_folder(self):
        self.assertEqual(installed.scan("/nonexistent/path/xyz", "linux"), [])

    def test_scan_ignora_carpetas_sin_permiso(self):
        # Al apuntar al home del usuario aparece ~/.gvfs (montaje FUSE): en
        # Python 3.12 ``is_dir()``/``is_file()`` lanzan PermissionError al no
        # poder recorrerla, y eso tumbaba la app entera. Se simula ese
        # comportamiento para no depender de la version de Python del CI.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gvfs").mkdir()
            (root / "blender-5.2.1-linux-x64").mkdir()
            (root / "blender-5.2.1-linux-x64" / "blender").write_text("#!/bin/sh\n")

            real_is_dir, real_is_file = Path.is_dir, Path.is_file

            def is_dir(path):
                if ".gvfs" in str(path):
                    raise PermissionError(13, "Permiso denegado", str(path))
                return real_is_dir(path)

            def is_file(path):
                if ".gvfs" in str(path):
                    raise PermissionError(13, "Permiso denegado", str(path))
                return real_is_file(path)

            with mock.patch.object(Path, "is_dir", is_dir), \
                    mock.patch.object(Path, "is_file", is_file):
                results = installed.scan(root, "linux")
            # La carpeta ilegible se ignora; la build válida se encuentra igual.
            self.assertEqual([entry.version for entry in results], ["5.2.1"])

    def test_scan_folders_une_las_dos_carpetas(self):
        # Las LTS pueden vivir en otra carpeta: hay que ver las dos.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            principal = base / "principal"
            lts = base / "lts"
            (principal / "blender-5.1.2-linux-x64").mkdir(parents=True)
            (lts / "blender-4.5.13-linux-x64").mkdir(parents=True)
            results = installed.scan_folders([principal, lts], "linux")
            self.assertEqual([entry.version for entry in results],
                             ["5.1.2", "4.5.13"])

    def test_scan_folders_no_repite_la_misma_carpeta(self):
        # Si el usuario pone la misma ruta en las dos (o la LTS coincide con la
        # de destino), no puede salir la instalación por duplicado.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "blender-5.2.1-linux-x64").mkdir()
            results = installed.scan_folders([root, str(root), ""], "linux")
            self.assertEqual(len(results), 1)

    def test_scan_encuentra_una_renombrada_con_marcador(self):
        # Al renombrar, el nombre puede dejar de llevar la versión; el marcador
        # sigue diciendo qué es y el escaneo tiene que encontrarla igual.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Mi Blender"
            folder.mkdir()
            (folder / "blender").write_text("bin")
            installed.write_marker(
                folder, make_build("5.2.1", "stable", "v52", "f.tar.xz"))
            results = installed.scan(Path(tmp), "linux")
            self.assertEqual([entry.version for entry in results], ["5.2.1"])

    def test_scan_encuentra_bforartists_con_su_ejecutable(self):
        """Una carpeta de Bforartists no lleva "blender": se reconocía como nada.

        El ejecutable también es ``bforartists``, no ``blender``; sin esto la
        versión salía en la lista pero no se podía lanzar.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "Bforartists-5.2.0-Linux"
            folder.mkdir()
            (folder / "bforartists").write_text("#!/bin/sh\n")
            results = installed.scan(root, "linux")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].version, "5.2.0")
            self.assertEqual(results[0].fork, "bforartists")
            self.assertTrue(results[0].can_launch)

    def test_scan_encuentra_upbge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "upbge-0.53-alpha-linux-x86_64-2026-09-20"
            folder.mkdir()
            (folder / "blender").write_text("#!/bin/sh\n")
            results = installed.scan(root, "linux")
            self.assertEqual([(e.version, e.fork) for e in results],
                             [("0.53", "upbge")])

    def test_find_installed_no_confunde_un_fork_con_blender(self):
        """Bforartists 5.2.0 y Blender 5.2.0 comparten número, no son lo mismo."""
        bfa_entry = installed.InstalledBuild(
            "Bforartists-5.2.0-Linux", Path("/tmp/bfa"), "5.2.0",
            fork="bforartists")
        plain = installed.InstalledBuild(
            "blender-5.2.0-linux-x64", Path("/tmp/bl"), "5.2.0")
        bfa_build = make_build("5.2.0", "stable", "bforartists", "f.tar.xz")
        bfa_build.fork = "bforartists"
        self.assertIs(installed.find_installed([bfa_entry, plain], bfa_build),
                      bfa_entry)
        blender_build = make_build("5.2.0", "stable", "v52", "f.tar.xz")
        self.assertIs(installed.find_installed([bfa_entry, plain], blender_build),
                      plain)
        self.assertIsNone(installed.find_installed([plain], bfa_build))

    def test_rename_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "blender-5.2.1-linux-x64"
            folder.mkdir()
            (Path(tmp) / "otra").mkdir()
            self.assertEqual(installed.rename_failure(folder, ""), "empty")
            self.assertEqual(installed.rename_failure(folder, "a/b"), "invalid")
            self.assertEqual(installed.rename_failure(folder, folder.name), "same")
            self.assertEqual(installed.rename_failure(folder, "otra"), "exists")
            self.assertEqual(installed.rename_failure(folder, "Mi Blender"), "")

    def test_rename_cambia_la_carpeta_y_conserva_la_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "blender-5.2.1-linux-x64"
            folder.mkdir()
            (folder / "blender").write_text("bin")
            new_path = installed.rename(folder, "Mi Blender", version="5.2.1",
                                        branch="v52", build_hash="abc123")
            self.assertEqual(new_path.name, "Mi Blender")
            self.assertFalse(folder.exists())
            results = installed.scan(Path(tmp), "linux")
            self.assertEqual([entry.version for entry in results], ["5.2.1"])
            self.assertEqual(results[0].build_hash, "abc123")

    def test_rename_no_pisa_el_marcador_existente(self):
        # Una instalación con marcador conserva el suyo (con su hash) al
        # renombrar; no se reescribe con lo que le pase el escaneo.
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "blender-5.3.0-linux-x64"
            folder.mkdir()
            installed.write_marker(
                folder, make_build("5.3.0", "alpha", "main", "f.tar.xz",
                                   build_hash="original"))
            new_path = installed.rename(folder, "Mi Blender", version="5.3.0",
                                        branch="main", build_hash="otro")
            self.assertEqual(installed.read_marker(new_path)["hash"], "original")

    def test_is_version_installed(self):
        entry = installed.InstalledBuild("b", Path("/tmp/b"), "5.2.0")
        self.assertTrue(installed.is_version_installed([entry], "5.2.0"))
        self.assertFalse(installed.is_version_installed([entry], "5.2.1"))

    def test_executable_search_is_depth_limited(self):
        # El ejecutable enterrado a tres niveles no cuenta: si no ponemos
        # tope, un escaneo recorre el árbol entero de Blender.
        with tempfile.TemporaryDirectory() as tmp:
            deep = Path(tmp) / "blender-5.2.0" / "a" / "b" / "c"
            deep.mkdir(parents=True)
            (deep / "blender").write_text("bin")
            results = installed.scan(Path(tmp), "linux")
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0].can_launch)


class InstalledFilterTests(unittest.TestCase):
    """Filtro de canal de la pestaña de instaladas."""

    def _entries(self):
        return [
            installed.InstalledBuild("blender-4.5.13-linux-x64", Path("/tmp/a"), "4.5.13",
                                     branch="v45"),
            installed.InstalledBuild("blender-5.3.0-alpha-linux-x64", Path("/tmp/b"), "5.3.0",
                                     branch="main"),
            installed.InstalledBuild("blender-4.5.0-geometry-nodes-linux-x64", Path("/tmp/c"),
                                     "4.5.0", branch="geometry-nodes"),
        ]

    def test_experimental_only_in_its_own_channel(self):
        entries = self._entries()
        experimental = installed.filter_installed(entries, "experimental")
        self.assertEqual([entry.branch for entry in experimental], ["geometry-nodes"])
        # "Todas" las incluye; los canales concretos no.
        self.assertIn("geometry-nodes",
                      [e.branch for e in installed.filter_installed(entries, "all")])
        for channel in ("lts", "stable", "daily"):
            selected = installed.filter_installed(entries, channel)
            self.assertNotIn("geometry-nodes", [entry.branch for entry in selected])

    def test_daily_and_lts(self):
        entries = self._entries()
        self.assertEqual(len(installed.filter_installed(entries, "lts")), 1)
        self.assertEqual(len(installed.filter_installed(entries, "daily")), 1)

    def test_favoritos_comparten_clave_con_la_tienda(self):
        # La clave no lleva plataforma ni arquitectura a proposito: asi marcar
        # una version en la tienda marca tambien la que tienes instalada.
        build = make_build("4.5.13", "stable", "v45", "b.tar.xz")
        entry = installed.InstalledBuild("blender-4.5.13-linux-x64", Path("/tmp/a"),
                                         "4.5.13", branch="v45")
        self.assertEqual(build.favorite_key, entry.favorite_key)
        self.assertEqual(build.favorite_key, favorite_key("v45", "4.5.13"))
        # Dos builds del mismo dia (mismo numero, hash distinto) comparten
        # favorito: si no, el de una diaria se perderia al dia siguiente.
        otra = make_build("4.5.13", "daily", "v45", "c.tar.xz", build_hash="ffff")
        self.assertEqual(build.favorite_key, otra.favorite_key)

    def test_filtro_de_favoritos(self):
        entries = self._entries()
        marked = [entries[1].favorite_key]
        selected = installed.filter_installed(entries, "favorites", "", marked)
        self.assertEqual([entry.branch for entry in selected], ["main"])
        # Sin marcar nada el canal sale vacio (y no revienta).
        self.assertEqual(installed.filter_installed(entries, "favorites"), [])


class FindInstalledTests(unittest.TestCase):
    """Las diarias comparten número de versión, así que se comparan por hash."""

    def _entry(self, build_hash=""):
        return installed.InstalledBuild(
            "blender-5.3.0-linux-x64", Path("/tmp/x"), "5.3.0", build_hash=build_hash)

    def test_stable_matches_by_version(self):
        entry = installed.InstalledBuild("b", Path("/tmp/b"), "5.2.1")
        build = make_build("5.2.1", "stable", "v52", "f.tar.xz", build_hash="otro")
        self.assertIsNotNone(installed.find_installed([entry], build))

    def test_daily_with_other_hash_is_not_installed(self):
        entry = self._entry(build_hash="aaaaaaaaaaaa")
        build = make_build("5.3.0", "alpha", "main", "f.tar.xz", build_hash="bbbbbbbbbbbb")
        self.assertIsNone(installed.find_installed([entry], build))

    def test_daily_with_same_hash_is_installed(self):
        entry = self._entry(build_hash="aaaaaaaaaaaa")
        build = make_build("5.3.0", "alpha", "main", "f.tar.xz", build_hash="aaaaaaaaaaaa")
        self.assertIsNotNone(installed.find_installed([entry], build))

    def test_daily_without_marker_falls_back_to_version(self):
        # Carpetas instaladas antes de que existiera el marcador: se comportan
        # como siempre en vez de aparecer de golpe como no instaladas.
        entry = self._entry()
        build = make_build("5.3.0", "alpha", "main", "f.tar.xz", build_hash="bbbbbbbbbbbb")
        self.assertIsNotNone(installed.find_installed([entry], build))

    def test_marker_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "blender-5.3.0-linux-x64"
            folder.mkdir()
            (folder / "blender").write_text("bin")
            build = make_build("5.3.0", "alpha", "main", "f.tar.xz", build_hash="abc123abc123")
            installed.write_marker(folder, build)
            scanned = installed.scan(Path(tmp), "linux")
            self.assertEqual(scanned[0].build_hash, "abc123abc123")
            self.assertIsNone(installed.find_installed(
                scanned, make_build("5.3.0", "alpha", "main", "f.tar.xz", build_hash="otro")))


class ExtractorTests(unittest.TestCase):
    def _make_tar(self, path, member_name):
        source = Path(path).parent / "payload"
        source.mkdir()
        (source / "blender").write_text("bin")
        with tarfile.open(path, "w:xz") as archive:
            archive.add(source / "blender", arcname=f"{member_name}/blender")

    def test_extract_tar_xz(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "build.tar.xz"
            self._make_tar(archive_path, "blender-9.9.9-linux-x64")
            dest = Path(tmp) / "out"
            target = extract(archive_path, dest)
            self.assertEqual(target.name, "blender-9.9.9-linux-x64")
            self.assertTrue((target / "blender").is_file())

    def test_extract_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "build.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("blender-8.8.8-windows.zip/blender.exe", "bin")
            target = extract(archive_path, Path(tmp) / "out")
            self.assertTrue((target / "blender.exe").is_file())

    def test_dmg_is_not_an_archive(self):
        # macOS: la API sirve .dmg y no sabemos abrirlo. Que no acabe en la
        # rama de tarfile es justo lo que evita el falso "Download failed".
        self.assertFalse(is_archive("blender-5.2.1-macos.dmg"))
        self.assertTrue(is_archive("blender-5.2.1-linux.tar.xz"))
        self.assertTrue(is_archive("blender-5.2.1-windows.zip"))

    def test_extract_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "evil.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../evil.txt", "nope")
            with self.assertRaises(ValueError):
                extract(archive_path, Path(tmp) / "out")


class SettingsMigrationTests(unittest.TestCase):
    """Pasar del esquema viejo (tres campos sueltos) a la biblioteca.

    La regla que vigilan todos estos tests es una: **no se mueve ni un byte**.
    Cada carpeta que se escaneaba se sigue escaneando y cada tipo acaba en la
    misma carpeta en la que acababa antes; lo único que cambia es cómo se
    escribe en el JSON.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = settings_module.config_dir
        settings_module.config_dir = lambda: Path(self.tmp.name)

    def tearDown(self):
        settings_module.config_dir = self._original
        self.tmp.cleanup()

    def _viejo(self, **campos):
        """Escribe un settings.json del esquema 1 y devuelve lo que se cargó."""
        data = {"dest_folder": "/tmp/datos"}
        data.update(campos)
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps(data), encoding="utf-8")
        return settings_module.Settings.load()

    def test_solo_carpeta_de_destino(self):
        loaded = self._viejo()
        self.assertEqual(len(loaded.folders), 1)
        self.assertEqual(loaded.folders[0].path, "/tmp/datos")
        self.assertEqual(loaded.folders[0].types, list(channels.BUILD_TYPES))

    def test_lts_separadas_el_caso_del_ssd(self):
        loaded = self._viejo(lts_folder="/tmp/ssd", separate_lts=True)
        self.assertEqual([f.path for f in loaded.folders],
                         ["/tmp/datos", "/tmp/ssd"])
        # La de siempre se queda con todo MENOS las LTS, que es exactamente lo
        # que hacía destination_for(is_lts). Los tipos de fork entran desde el
        # principio (la carpeta de siempre los recibe por defecto).
        self.assertEqual(loaded.folders[0].types,
                         [channels.TYPE_STABLE, channels.TYPE_DAILY,
                          channels.TYPE_EXPERIMENTAL,
                          channels.TYPE_BFORARTISTS, channels.TYPE_UPBGE])
        self.assertEqual(loaded.folders[1].types, [channels.TYPE_LTS])
        self.assertEqual(loaded.destination_for_type(channels.TYPE_LTS),
                         "/tmp/ssd")

    def test_lts_con_el_interruptor_apagado_solo_se_escanea(self):
        loaded = self._viejo(lts_folder="/tmp/ssd", separate_lts=False)
        self.assertEqual(loaded.folders[1].types, [])
        self.assertEqual(loaded.destination_for_type(channels.TYPE_LTS),
                         "/tmp/datos")
        # Pero se sigue mirando: las LTS ya instaladas ahí no desaparecen.
        self.assertIn("/tmp/ssd", loaded.library_roots())

    def test_lts_apuntando_al_destino_no_es_separar(self):
        """Si las dos rutas son la misma no hay nada separado.

        Sin mirarlo, el dedup se llevaba la segunda entrada y las LTS se
        quedaban sin ninguna carpeta que las recibiera.
        """
        loaded = self._viejo(lts_folder="/tmp/datos", separate_lts=True)
        self.assertEqual(len(loaded.folders), 1)
        self.assertEqual(loaded.folders[0].types, list(channels.BUILD_TYPES))
        self.assertEqual(loaded.destination_for_type(channels.TYPE_LTS),
                         "/tmp/datos")

    def test_carpeta_extra_encendida_entra_con_el_candado_cerrado(self):
        loaded = self._viejo(extra_folder="/tmp/viejos", use_extra_folder=True)
        self.assertEqual(loaded.folders[1].path, "/tmp/viejos")
        self.assertFalse(loaded.folders[1].writable)
        self.assertEqual(loaded.folders[1].types, [])

    def test_carpeta_extra_apagada_desaparece(self):
        loaded = self._viejo(extra_folder="/tmp/viejos", use_extra_folder=False)
        self.assertEqual([f.path for f in loaded.folders], ["/tmp/datos"])

    def test_sin_dest_folder_se_usa_el_de_fabrica(self):
        loaded = self._viejo(dest_folder="")
        self.assertEqual(loaded.folders[0].path,
                         str(settings_module.default_destination()))

    def test_no_se_vuelve_a_migrar(self):
        """Cargar, guardar y volver a cargar deja la lista igual.

        Es para lo que sirve ``settings_version``: sin él, alguien que borrase
        todas sus carpetas se las vería reaparecer en el siguiente arranque.
        """
        primera = self._viejo(lts_folder="/tmp/ssd", separate_lts=True)
        primera.save()
        segunda = settings_module.Settings.load()
        self.assertEqual([(f.path, f.types, f.writable) for f in segunda.folders],
                         [(f.path, f.types, f.writable) for f in primera.folders])
        # Y si el usuario se queda con una sola, no se le añaden de vuelta.
        segunda.folders = [segunda.folders[0]]
        segunda.save()
        self.assertEqual(len(settings_module.Settings.load().folders), 1)

    def test_el_aviso_solo_lo_ve_quien_viene_del_esquema_viejo(self):
        """Marca para presentar la biblioteca de carpetas una sola vez.

        Una instalación nueva no ve nada: la pantalla se comporta igual que
        siempre y no hay novedad que explicar.
        """
        # Esquema viejo sin la marca: hay que avisar.
        self.assertFalse(self._viejo().folders_hint_shown)
        # Esquema viejo que ya la vio: no se repite.
        self.assertTrue(self._viejo(folders_hint_shown=True).folders_hint_shown)
        # Sin fichero (instalación nueva): nada que explicar.
        (Path(self.tmp.name) / "settings.json").unlink()
        self.assertTrue(settings_module.Settings.load().folders_hint_shown)

    def test_se_escriben_los_campos_viejos_por_si_hay_downgrade(self):
        """Una versión anterior de la app tiene que seguir arrancando bien."""
        loaded = self._viejo(lts_folder="/tmp/ssd", separate_lts=True,
                             extra_folder="/tmp/viejos", use_extra_folder=True)
        loaded.save()
        data = json.loads(
            (Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(data["dest_folder"], "/tmp/datos")
        self.assertEqual(data["lts_folder"], "/tmp/ssd")
        self.assertTrue(data["separate_lts"])
        self.assertEqual(data["extra_folder"], "/tmp/viejos")
        self.assertTrue(data["use_extra_folder"])

    def test_el_espejo_no_inventa_lts_separadas(self):
        loaded = self._viejo()
        loaded.save()
        data = json.loads(
            (Path(self.tmp.name) / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(data["dest_folder"], "/tmp/datos")
        self.assertEqual(data["lts_folder"], "")
        self.assertFalse(data["separate_lts"])


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = settings_module.config_dir
        settings_module.config_dir = lambda: Path(self.tmp.name)

    def tearDown(self):
        settings_module.config_dir = self._original
        self.tmp.cleanup()

    def _build(self, version="5.2.1", branch="v52", risk="stable",
               experimental=False):
        from model.build import Build

        return Build(version=version, branch=branch, risk=risk,
                     platform="linux", arch="x86_64", url="",
                     filename="x.tar.xz", experimental=experimental)

    def _carpeta(self, path, types=(), writable=True):
        return settings_module.Folder(path=path, types=list(types),
                                      writable=writable)

    def _write_settings(self, text):
        (Path(self.tmp.name) / "settings.json").write_text(text,
                                                           encoding="utf-8")

    def test_un_json_que_no_es_un_objeto_da_los_valores_de_fabrica(self):
        """Un ``settings.json`` editado a mano no puede impedir el arranque."""
        for text in ("[1, 2, 3]", '"hola"', "42", "null"):
            with self.subTest(text=text):
                self._write_settings(text)
                loaded = settings_module.Settings.load()
                self.assertEqual(loaded.language, "auto")
                self.assertEqual(loaded.zoom, settings_module.DEFAULT_ZOOM)
                self.assertTrue(loaded.folders)

    def test_numeros_que_no_son_numeros_caen_al_valor_por_defecto(self):
        self._write_settings(json.dumps({
            "settings_version": "dos",
            "window_width": "ancho",
            "window_height": [1],
            "window_x": {"a": 1},
            "window_y": None,
            "zoom": "mucho",
            "reset_zoom": "nan",
            "update_interval_min": "a menudo",
        }))
        loaded = settings_module.Settings.load()
        self.assertEqual(loaded.window_width, 0)
        self.assertEqual(loaded.window_height, 0)
        self.assertEqual(loaded.window_x, 0)
        self.assertEqual(loaded.window_y, 0)
        self.assertEqual(loaded.zoom, settings_module.DEFAULT_ZOOM)
        self.assertEqual(loaded.reset_zoom, settings_module.DEFAULT_ZOOM)
        self.assertEqual(loaded.update_interval_min,
                         settings_module.DEFAULT_UPDATE_INTERVAL)

    def test_roundtrip(self):
        settings = settings_module.Settings(
            folders=[
                self._carpeta("/tmp/ssd", [channels.TYPE_LTS,
                                           channels.TYPE_STABLE]),
                self._carpeta("/tmp/datos", [channels.TYPE_DAILY,
                                             channels.TYPE_EXPERIMENTAL]),
                self._carpeta("/tmp/viejos", [], writable=False),
            ],
            language="es",
            delete_archive=False,
            launch_args="--background",
            layout_mode="list",
            zoom=1.4,
            reset_zoom=1.2,
            auto_update=False,
            window_width=1200,
            window_height=700,
            window_x=-1920,
            window_y=40,
            window_pos_saved=True,
            platform="Windows",
            arch="arm64",
            experimental_features=True,
            snapshot_keep=3,
        )
        settings.save()
        loaded = settings_module.Settings.load()
        self.assertEqual(loaded.settings_version,
                         settings_module.SETTINGS_VERSION)
        self.assertEqual([f.path for f in loaded.folders],
                         ["/tmp/ssd", "/tmp/datos", "/tmp/viejos"])
        self.assertEqual(loaded.folders[0].types,
                         [channels.TYPE_LTS, channels.TYPE_STABLE])
        self.assertFalse(loaded.folders[2].writable)
        self.assertEqual(loaded.language, "es")
        self.assertFalse(loaded.delete_archive)
        self.assertEqual(loaded.layout_mode, "list")
        self.assertAlmostEqual(loaded.zoom, 1.4)
        self.assertAlmostEqual(loaded.reset_zoom, 1.2)
        self.assertFalse(loaded.auto_update)
        self.assertEqual(loaded.window_width, 1200)
        self.assertEqual(loaded.window_height, 700)
        self.assertEqual((loaded.window_x, loaded.window_y), (-1920, 40))
        self.assertTrue(loaded.window_pos_saved)
        self.assertEqual(loaded.platform, "Windows")
        self.assertEqual(loaded.arch, "arm64")
        self.assertTrue(loaded.experimental_features)
        self.assertEqual(loaded.snapshot_keep, 3)

    def test_save_is_atomic(self):
        # Tras guardar no debe quedar ningún .tmp suelto y el JSON debe ser
        # legible (la escritura va a un temporal y luego se mueve encima).
        settings_module.Settings().save()
        directory = Path(self.tmp.name)
        self.assertEqual(list(directory.glob("*.tmp")), [])

    def test_los_forks_vienen_apagados_por_defecto(self):
        settings = settings_module.Settings.load()
        self.assertFalse(settings.enable_bforartists)
        self.assertFalse(settings.enable_upbge)
        self.assertEqual(settings.enabled_forks(), [])
        self.assertNotIn(channels.TYPE_BFORARTISTS,
                         settings.active_build_types())

    def test_los_forks_se_encienden_y_se_guardan(self):
        settings = settings_module.Settings.load()
        settings.enable_bforartists = True
        settings.enable_upbge = True
        settings.save()
        loaded = settings_module.Settings.load()
        self.assertEqual(loaded.enabled_forks(), ["bforartists", "upbge"])
        self.assertIn(channels.TYPE_BFORARTISTS, loaded.active_build_types())
        self.assertIn(channels.TYPE_UPBGE, loaded.active_build_types())

    def test_un_canal_de_fork_apagado_no_se_queda_puesto(self):
        """Su pestaña no existe al arrancar: la lista saldría vacía sin más."""
        settings = settings_module.Settings(channel="bforartists")
        settings.save()
        self.assertEqual(settings_module.Settings.load().channel, "all")
        settings.enable_bforartists = True
        settings.channel = "bforartists"
        settings.save()
        self.assertEqual(settings_module.Settings.load().channel,
                         "bforartists")

    def test_la_carpeta_de_fabrica_recibe_los_forks(self):
        """En una instalación nueva la carpeta de siempre recibe todo."""
        settings = settings_module.Settings.load()
        self.assertIn(channels.TYPE_BFORARTISTS, settings.folders[0].types)
        self.assertIn(channels.TYPE_UPBGE, settings.folders[0].types)

    def test_defaults_fill_destination(self):
        """Sin fichero: una sola carpeta que se queda con todo.

        Es lo que hacía la app de siempre, y lo que hace que el modo simple de
        los ajustes se vea igual que antes.
        """
        loaded = settings_module.Settings.load()
        self.assertEqual(len(loaded.folders), 1)
        self.assertEqual(loaded.folders[0].path,
                         str(settings_module.default_destination()))
        self.assertEqual(loaded.folders[0].types, list(channels.BUILD_TYPES))
        self.assertTrue(loaded.folders[0].writable)
        self.assertEqual(loaded.layout_mode, "grid")
        self.assertTrue(loaded.auto_update)
        self.assertEqual(loaded.favorites, [])
        # Las opciones experimentales (Migración) vienen apagadas: así la
        # release es estable sin que nadie tenga que tocar nada.
        self.assertFalse(loaded.experimental_features)
        self.assertEqual(loaded.snapshot_keep, 5)

    def test_las_lts_pueden_ir_a_otra_carpeta(self):
        """El caso que motivó todo: las LTS al SSD, el resto al disco lento."""
        settings = settings_module.Settings(folders=[
            self._carpeta("/tmp/ssd", [channels.TYPE_LTS]),
            self._carpeta("/tmp/datos", [channels.TYPE_STABLE,
                                         channels.TYPE_DAILY,
                                         channels.TYPE_EXPERIMENTAL]),
        ])
        self.assertEqual(settings.destination_for(self._build("5.2.1", "v52")),
                         "/tmp/ssd")
        self.assertEqual(settings.destination_for(self._build("5.1.2", "v51")),
                         "/tmp/datos")
        self.assertEqual(settings.destination_for(
            self._build("5.3.0", "main", risk="alpha")), "/tmp/datos")
        self.assertEqual(settings.library_roots(), ["/tmp/ssd", "/tmp/datos"])

    def test_un_tipo_sin_carpeta_no_tiene_destino(self):
        """Nadie recibe las diarias: se dice, no se inventa un sitio."""
        settings = settings_module.Settings(folders=[
            self._carpeta("/tmp/ssd", [channels.TYPE_LTS]),
        ])
        self.assertEqual(settings.destination_for(
            self._build("5.3.0", "main", risk="alpha")), "")

    def test_una_carpeta_sin_tipos_se_escanea_pero_no_recibe(self):
        """Es la antigua 'carpeta extra': se mira, no se descarga ahí."""
        settings = settings_module.Settings(folders=[
            self._carpeta("/tmp/datos", channels.BUILD_TYPES),
            self._carpeta("/tmp/viejos", [], writable=False),
        ])
        self.assertEqual(settings.library_roots(), ["/tmp/datos", "/tmp/viejos"])
        self.assertEqual([f.path for f in settings.install_folders()],
                         ["/tmp/datos"])
        self.assertEqual(settings.destination_for(self._build()), "/tmp/datos")

    def test_carpeta_repetida_se_escanea_una_vez(self):
        settings = settings_module.Settings(folders=[
            self._carpeta("/tmp/datos", channels.BUILD_TYPES),
            self._carpeta("/tmp/datos", []),
        ])
        self.assertEqual(settings.library_roots(), ["/tmp/datos"])

    def test_folder_for_localiza_el_candado(self):
        settings = settings_module.Settings(folders=[
            self._carpeta("/tmp/viejos", [], writable=False),
        ])
        self.assertFalse(settings.folder_for("/tmp/viejos").writable)
        self.assertIsNone(settings.folder_for("/tmp/otra"))

    def test_clean_folders_aguanta_un_json_a_mano(self):
        """Un settings.json editado no puede tumbar la app ni crear ambigüedad."""
        limpio = settings_module.clean_folders([
            "no soy un dict",
            {"types": ["lts"]},                       # sin ruta
            {"path": "/a", "types": ["lts", "inventado"]},
            {"path": "/b", "types": ["lts", "daily"]},  # 'lts' ya tiene dueño
            {"path": "/A", "types": ["stable"]},        # repetida (según SO)
            {"path": "/c", "types": ["stable"], "writable": False},
        ])
        self.assertEqual([f.path for f in limpio][:2], ["/a", "/b"])
        self.assertEqual(limpio[0].types, [channels.TYPE_LTS])
        # El segundo pierde 'lts' porque ya lo tenía el primero.
        self.assertEqual(limpio[1].types, [channels.TYPE_DAILY])
        # Con el candado cerrado no se recibe nada, aunque el JSON lo pida.
        cerrada = [f for f in limpio if not f.writable][0]
        self.assertEqual(cerrada.types, [])

    def test_clean_folders_con_basura_no_revienta(self):
        self.assertEqual(settings_module.clean_folders("hola"), [])
        self.assertEqual(settings_module.clean_folders(None), [])

    def test_favoritos_se_marcan_y_se_guardan(self):
        settings = settings_module.Settings()
        self.assertTrue(settings.set_favorite("v45|4.5.13", True))
        # Repetirlo no cambia nada (y no ensucia el JSON con duplicados).
        self.assertFalse(settings.set_favorite("v45|4.5.13", True))
        self.assertTrue(settings.set_favorite("main|5.3.0", True))
        settings.save()
        self.assertEqual(settings_module.Settings.load().favorites,
                         ["v45|4.5.13", "main|5.3.0"])
        # Y se pueden quitar.
        self.assertTrue(settings.set_favorite("v45|4.5.13", False))
        self.assertFalse(settings.set_favorite("v45|4.5.13", False))
        self.assertEqual(settings.favorites, ["main|5.3.0"])
        # Una clave vacia se ignora.
        self.assertFalse(settings.set_favorite("", True))

    def test_recuerda_el_filtro_de_canal(self):
        settings = settings_module.Settings()
        self.assertEqual(settings.channel, "all")
        settings.channel = "favorites"
        settings.save()
        self.assertEqual(settings_module.Settings.load().channel, "favorites")

    def test_el_intervalo_de_actualizacion_se_guarda(self):
        settings = settings_module.Settings()
        # Por defecto, cada 30 minutos y el periódico encendido.
        self.assertEqual(settings.update_interval_min,
                         settings_module.DEFAULT_UPDATE_INTERVAL)
        self.assertTrue(settings.periodic_update)
        settings.update_interval_min = 60
        settings.periodic_update = False
        settings.save()
        loaded = settings_module.Settings.load()
        self.assertEqual(loaded.update_interval_min, 60)
        self.assertFalse(loaded.periodic_update)

    def test_se_recuerda_la_version_saltada(self):
        settings = settings_module.Settings()
        self.assertEqual(settings.skipped_version, "")
        self.assertEqual(settings.ignored_blender_series, [])
        settings.skipped_version = "v1.20.0"
        settings.ignored_blender_series = ["5.2", "4.5"]
        settings.save()
        loaded = settings_module.Settings.load()
        self.assertEqual(loaded.skipped_version, "v1.20.0")
        self.assertEqual(loaded.ignored_blender_series, ["5.2", "4.5"])

    def test_las_preferencias_de_bandeja_se_guardan(self):
        settings = settings_module.Settings()
        # Cerrar a la bandeja viene activado; minimizar, apagado (minimizar a la
        # barra de tareas es lo que espera la mayoria).
        self.assertTrue(settings.close_to_tray)
        self.assertFalse(settings.minimize_to_tray)
        self.assertFalse(settings.start_minimized)
        self.assertFalse(settings.tray_hint_shown)
        settings.close_to_tray = False
        settings.minimize_to_tray = True
        settings.start_minimized = True
        settings.tray_hint_shown = True
        settings.save()
        loaded = settings_module.Settings.load()
        self.assertFalse(loaded.close_to_tray)
        self.assertTrue(loaded.minimize_to_tray)
        self.assertTrue(loaded.start_minimized)
        self.assertTrue(loaded.tray_hint_shown)

    def test_el_tope_de_versiones_de_los_forks_se_guarda(self):
        settings = settings_module.Settings()
        # Por defecto se enseñan las 10 últimas de cada fork.
        self.assertEqual(settings.fork_version_limit, 10)
        settings.fork_version_limit = 0
        settings.save()
        self.assertEqual(settings_module.Settings.load().fork_version_limit, 0)

    def test_un_tope_de_forks_inventado_cae_al_por_defecto(self):
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"fork_version_limit": 7}), encoding="utf-8")
        self.assertEqual(settings_module.Settings.load().fork_version_limit,
                         settings_module.DEFAULT_FORK_VERSION_LIMIT)
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"fork_version_limit": "muchas"}), encoding="utf-8")
        self.assertEqual(settings_module.Settings.load().fork_version_limit,
                         settings_module.DEFAULT_FORK_VERSION_LIMIT)

    def test_limit_forks_recorta_solo_las_ultimas_de_cada_fork(self):
        from model.build import FORK_BFORARTISTS, Build

        def build(version, fork="", mtime=0):
            return Build(version=version, branch=fork or "main", risk="stable",
                         platform="linux", arch="x86_64", url="u",
                         filename="f", fork=fork, mtime=mtime)

        builds = [build("5.2"),
                  build("5.2.0", FORK_BFORARTISTS, 100),
                  build("5.1.0", FORK_BFORARTISTS, 90),
                  build("5.0.0", FORK_BFORARTISTS, 80)]
        settings = settings_module.Settings(fork_version_limit=2)
        kept = settings.limit_forks(builds)
        versiones = {b.version for b in kept if b.fork}
        # Se quedan las dos últimas del fork; la de Blender no se toca.
        self.assertEqual(versiones, {"5.2.0", "5.1.0"})
        self.assertTrue(any(not b.fork for b in kept))

    def test_limit_forks_con_todas_no_recorta(self):
        from model.build import FORK_UPBGE, Build

        builds = [Build(version="0.53", branch="upbge-weekly", risk="alpha",
                        platform="linux", arch="x86_64", url="u",
                        filename="f", fork=FORK_UPBGE)]
        settings = settings_module.Settings(fork_version_limit=0)
        self.assertEqual(len(settings.limit_forks(builds)), 1)

    def test_un_intervalo_inventado_cae_al_por_defecto(self):
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"update_interval_min": -5}), encoding="utf-8")
        self.assertEqual(settings_module.Settings.load().update_interval_min,
                         settings_module.DEFAULT_UPDATE_INTERVAL)
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"update_interval_min": "cada rato"}), encoding="utf-8")
        self.assertEqual(settings_module.Settings.load().update_interval_min,
                         settings_module.DEFAULT_UPDATE_INTERVAL)
        # El 0 tampoco vale: para no comprobar está el interruptor.
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"update_interval_min": 0}), encoding="utf-8")
        self.assertEqual(settings_module.Settings.load().update_interval_min,
                         settings_module.DEFAULT_UPDATE_INTERVAL)

    def test_filtro_desconocido_cae_a_todas(self):
        # Un settings.json editado a mano (o de una version con otros filtros)
        # no puede dejar la barra sin ninguna pastilla marcada.
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"channel": "inventado"}), encoding="utf-8")
        self.assertEqual(settings_module.Settings.load().channel, "all")

    def test_favoritos_con_basura_en_el_json(self):
        (Path(self.tmp.name) / "settings.json").write_text(
            json.dumps({"favorites": ["v45|4.5.13", 7, None, "", "v45|4.5.13"]}),
            encoding="utf-8")
        loaded = settings_module.Settings.load()
        self.assertEqual(loaded.favorites, ["v45|4.5.13"])


class SmokeTests(unittest.TestCase):
    """`--smoke` tiene que decir la verdad: si no hay red, fallar."""

    def _smoke(self, efecto):
        """Ejecuta smoke() con la descarga simulada, sin ensuciar la salida."""
        import contextlib
        import io

        import main

        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(main.api, "fetch_builds", **efecto):
            return main.smoke()

    def test_con_red_devuelve_cero(self):
        builds = [make_build("5.2.1", "stable", "v52", "b.tar.xz")]
        # ``fetch_builds`` devuelve ``(builds, etags)``: si ``smoke`` no lo
        # desempaqueta, revienta (pasó en el CI con el binario empaquetado).
        self.assertEqual(self._smoke({"return_value": (builds, {})}), 0)

    def test_sin_red_falla(self):
        # Antes caia al cache de disco y salia con 0: un binario sin HTTPS
        # "parecia" funcionar. Se descubrio en Arch, con el auto-update roto.
        self.assertEqual(
            self._smoke({"side_effect": OSError("sin red")}), 1)

    def test_listado_vacio_falla(self):
        self.assertEqual(self._smoke({"return_value": ([], {})}), 1)


class TlsTests(unittest.TestCase):
    """El binario empaquetado no encontraba las CAs fuera de Debian/Ubuntu."""

    def setUp(self):
        self._original = tls._ca_file
        tls._ca_file = None

    def tearDown(self):
        tls._ca_file = self._original

    def test_usa_el_primer_candidato_que_existe(self):
        # En esta maquina (como en la mayoria) hay al menos uno.
        self.assertTrue(tls.ca_file())
        self.assertTrue(any(tls.ca_file() == c for c in tls.CA_CANDIDATES))

    def test_el_contexto_trae_autoridades_de_verdad(self):
        # Lo que fallaba era justo esto: un contexto sin CAs, que rechaza
        # cualquier certificado con "unable to get local issuer certificate".
        contexto = tls.ssl_context()
        self.assertGreater(contexto.cert_store_stats()["x509_ca"], 0)

    def test_sin_candidatos_cae_al_contexto_por_defecto(self):
        # Si no hay ningun fichero conocido, no se inventa nada: contexto
        # normal, y como mucho fallara la peticion (no se degrada la seguridad).
        with mock.patch.object(tls, "CA_CANDIDATES", ("/no/existe/ca.crt",)):
            tls._ca_file = None
            self.assertIsNone(tls.ca_file())
            self.assertIsNotNone(tls.ssl_context())


class DetectorTests(unittest.TestCase):
    def test_detect_returns_known_arch(self):
        info = detector.detect()
        self.assertIn(info.arch, ("x86_64", "amd64", "arm64", "x86", ""))

    def test_session_is_wayland(self):
        self.assertTrue(detector.session_is_wayland({"WAYLAND_DISPLAY": "wayland-0"}))
        self.assertTrue(detector.session_is_wayland({"XDG_SESSION_TYPE": "wayland"}))
        self.assertFalse(detector.session_is_wayland({"XDG_SESSION_TYPE": "x11"}))
        self.assertFalse(detector.session_is_wayland({}))

    def test_minimizar_a_la_bandeja_no_se_ofrece_en_wayland(self):
        # En Wayland el compositor no comunica el minimizado, ni con XWayland:
        # correr bajo X11 es lo que choca con los atajos globales del
        # compositor, así que la opción no se ofrece aunque haya DISPLAY.
        self.assertFalse(detector.minimize_to_tray_supported(
            {"XDG_SESSION_TYPE": "wayland"}))
        self.assertFalse(detector.minimize_to_tray_supported(
            {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}))
        self.assertFalse(detector.minimize_to_tray_supported(
            {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}))
        # X11 de verdad, Windows o macOS: no hay problema.
        self.assertTrue(detector.minimize_to_tray_supported(
            {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}))
        self.assertTrue(detector.minimize_to_tray_supported({}))


class AutostartTests(unittest.TestCase):
    """Autoarranque con la sesión (Linux: XDG autostart)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": self.tmp.name,
            "HOME": self.tmp.name,
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_activar_y_desactivar_en_linux(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("el fichero .desktop es solo de Linux")
        from services import autostart

        self.assertFalse(autostart.is_enabled())
        self.assertTrue(autostart.enable())
        self.assertTrue(autostart.is_enabled())
        path = Path(self.tmp.name) / "autostart" / autostart.DESKTOP_FILE
        self.assertTrue(path.is_file())
        # Volver a activarlo no falla (idempotente).
        self.assertTrue(autostart.enable())
        self.assertTrue(autostart.disable())
        self.assertFalse(autostart.is_enabled())
        self.assertFalse(path.exists())
        # Desactivar dos veces tampoco falla.
        self.assertTrue(autostart.disable())

    def test_el_desktop_entrecomilla_rutas_con_espacios(self):
        from services import autostart

        entry = autostart._desktop_entry(["/ruta/con espacios/BlenderManager"])
        self.assertIn('Exec="/ruta/con espacios/BlenderManager"', entry)
        self.assertIn("Type=Application", entry)
        self.assertIn("X-GNOME-Autostart-enabled=true", entry)

    def test_el_plist_de_macos_es_valido(self):
        import plistlib

        from services import autostart

        data = plistlib.loads(autostart._launch_agent_bytes(["/opt/BM"]))
        self.assertEqual(data["Label"], autostart.MACOS_LABEL)
        self.assertEqual(data["ProgramArguments"], ["/opt/BM"])
        self.assertTrue(data["RunAtLoad"])

    def test_macos_si_launchctl_falla_no_deja_el_plist(self):
        if not hasattr(os, "getuid"):
            self.skipTest("getuid es de Unix")
        from services import autostart

        agent = (Path(self.tmp.name) / "LaunchAgents"
                 / f"{autostart.MACOS_LABEL}.plist")
        common = (
            mock.patch.object(autostart, "_launch_agent_path",
                              return_value=agent),
            mock.patch.object(autostart, "_exec_command",
                              return_value=["/opt/BM"]),
            mock.patch.object(autostart.os, "getuid", return_value=1000),
        )
        with common[0], common[1], common[2], \
                mock.patch.object(autostart, "_run_launchctl",
                                  return_value=False):
            self.assertFalse(autostart._macos_apply(True))
        self.assertFalse(agent.exists())

        with common[0], common[1], common[2], \
                mock.patch.object(autostart, "_run_launchctl",
                                  return_value=True):
            self.assertTrue(autostart._macos_apply(True))
        self.assertTrue(agent.exists())

    def test_en_appimage_usa_la_variable_appimage(self):
        from services import autostart

        with mock.patch.object(autostart.sys, "frozen", True, create=True), \
                mock.patch.dict(os.environ, {"APPIMAGE": "/tmp/BM.AppImage"}):
            self.assertEqual(autostart._exec_command(), ["/tmp/BM.AppImage"])

    def test_en_modo_fuente_el_comando_es_python_mas_main(self):
        from services import autostart

        command = autostart._exec_command()
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("main.py"))


class ElevateTests(unittest.TestCase):
    """Pedir permisos de administrador (Windows/UAC). Solo Windows los usa."""

    def test_solo_esta_disponible_en_windows(self):
        if not sys.platform.startswith("win"):
            self.assertFalse(elevate.available())
            # Sin Windows, pedir elevación no hace nada (ni revienta).
            self.assertFalse(elevate.relaunch_elevated(["--grant-access", "/tmp/x"]))

    def test_el_comando_relanza_esta_app(self):
        with mock.patch.object(elevate.sys, "frozen", True, create=True):
            self.assertEqual(
                elevate._command(["--grant-access", "C:\\x"]),
                [elevate.sys.executable, "--grant-access", "C:\\x"])
        # En modo fuente va también el main.py.
        self.assertEqual(
            elevate._command(["--grant-access", "C:\\x"]),
            [elevate.sys.executable, elevate.sys.argv[0],
             "--grant-access", "C:\\x"])

    def test_grant_write_usa_icacls(self):
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp) / "nueva"
            with mock.patch.object(elevate.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
                self.assertTrue(elevate.grant_write(carpeta))
            self.assertTrue(carpeta.is_dir())
            comando = run.call_args.args[0]
            self.assertEqual(comando[0], "icacls")
            self.assertIn("/grant", comando)

    def test_grant_write_falla_si_icacls_falla(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(elevate.subprocess, "run") as run:
                run.return_value = mock.Mock(returncode=1, stdout="", stderr="no")
                self.assertFalse(elevate.grant_write(Path(tmp) / "x"))


if __name__ == "__main__":
    unittest.main()


class ReleaseNotesUrlTests(unittest.TestCase):
    """La "i" de cada tarjeta abre las notas de esa serie de Blender."""

    def test_series_page(self):
        self.assertEqual(
            api.release_notes_url("4.2.1"),
            "https://developer.blender.org/docs/release_notes/4.2/")
        self.assertEqual(
            api.release_notes_url("5.2.0-alpha"),
            "https://developer.blender.org/docs/release_notes/5.2/")
        self.assertEqual(
            api.release_notes_url("2.93.18"),
            "https://developer.blender.org/docs/release_notes/2.93/")

    def test_falls_back_to_the_index(self):
        # Sin versión entendible o antes de que hubiera notas publicadas.
        for version in ("", None, "sin numero", "2.78c", "1.0"):
            self.assertEqual(
                api.release_notes_url(version),
                "https://developer.blender.org/docs/release_notes/")


class AvailableUpdatesTests(unittest.TestCase):
    """Avisar de Blender más nuevos que los que ya tienes instalados."""

    def _entry(self, version, branch="v52", name=None):
        return installed.InstalledBuild(
            name=name or f"blender-{version}-linux-x64",
            path=Path("/tmp") / (name or f"blender-{version}"),
            version=version, branch=branch)

    def test_parche_y_salto_de_serie(self):
        builds = [
            make_build("5.2.1", "stable", "v52", "a.tar.xz"),
            make_build("5.2.2", "stable", "v52", "b.tar.xz"),
            make_build("5.3.0", "stable", "v53", "c.tar.xz"),
        ]
        result = installed.available_updates([self._entry("5.2.0")], builds)
        by_kind = {u.kind: u.build.version for u in result}
        self.assertEqual(by_kind, {"patch": "5.2.2", "series": "5.3.0"})

    def test_sin_novedades_no_hay_avisos(self):
        builds = [make_build("5.2.0", "stable", "v52", "a.tar.xz")]
        self.assertEqual(installed.available_updates([self._entry("5.2.0")], builds), [])

    def test_las_no_estables_no_cuentan(self):
        # Una alfa con el mismo número no es un parche, y una serie alfa no
        # debe ofrecerse como salto (todavía no es una versión de verdad).
        builds = [
            make_build("5.2.9", "alpha", "main", "a.tar.xz"),
            make_build("5.9.0", "alpha", "main", "b.tar.xz"),
        ]
        self.assertEqual(installed.available_updates([self._entry("5.2.0")], builds), [])

    def test_una_instalada_diaria_no_se_avisa(self):
        builds = [make_build("5.3.0", "stable", "v53", "a.tar.xz")]
        entry = self._entry("5.3.0-alpha", branch="main")
        self.assertEqual(installed.available_updates([entry], builds), [])

    def test_no_avisa_si_ya_bajaste_la_nueva_como_copia(self):
        # Tienes 5.2.0 y 5.2.2: la 5.2.0 ya no debe seguir pidiendo la 5.2.2.
        builds = [make_build("5.2.2", "stable", "v52", "b.tar.xz")]
        entries = [self._entry("5.2.0"), self._entry("5.2.2")]
        self.assertEqual(installed.available_updates(entries, builds), [])

    def test_avisa_solo_en_la_instalada_mas_nueva_de_la_serie(self):
        # Con 5.2.0 y 5.2.1 instaladas, el aviso va en la 5.2.1 (no en las dos).
        builds = [make_build("5.2.2", "stable", "v52", "b.tar.xz")]
        entries = [self._entry("5.2.0"), self._entry("5.2.1")]
        result = installed.available_updates(entries, builds)
        self.assertEqual([(u.entry.version, u.build.version) for u in result],
                         [("5.2.1", "5.2.2")])

    def test_el_salto_no_se_ofrece_si_la_serie_ya_esta_instalada(self):
        builds = [make_build("5.3.0", "stable", "v53", "c.tar.xz")]
        entries = [self._entry("5.2.0"), self._entry("5.3.0", branch="v53")]
        self.assertEqual(installed.available_updates(entries, builds), [])


class OpenerTests(unittest.TestCase):
    """Abrir cosas fuera del binario sin heredar el entorno de PyInstaller.

    En el AppImage, PyInstaller mete ``_internal`` en ``LD_LIBRARY_PATH`` y el
    navegador (o Blender) cargaba sus librerías en vez de las del sistema, así
    que no arrancaba. El entorno va saneado.
    """

    def test_clean_env_restaura_el_original(self):
        from services import opener

        with mock.patch.dict("os.environ", {
                "LD_LIBRARY_PATH": "/tmp/.mount/usr/bin/BlenderManager/_internal",
                "LD_LIBRARY_PATH_ORIG": "/usr/lib",
                "LD_PRELOAD": "/tmp/.mount/libfoo.so",
                "QT_PLUGIN_PATH": "/tmp/.mount/plugins",
                "PATH": "/usr/bin"}, clear=False):
            env = opener.clean_env()

        self.assertEqual(env["LD_LIBRARY_PATH"], "/usr/lib")
        self.assertNotIn("LD_PRELOAD", env)
        self.assertNotIn("QT_PLUGIN_PATH", env)
        self.assertEqual(env["PATH"], "/usr/bin")

    def test_clean_env_sin_original_borra_la_variable(self):
        from services import opener

        with mock.patch.dict("os.environ", {
                "LD_LIBRARY_PATH": "/tmp/.mount/_internal"}, clear=False):
            env = opener.clean_env()

        self.assertNotIn("LD_LIBRARY_PATH", env)

    @unittest.skipIf(__import__("sys").platform != "linux", "solo Linux")
    def test_open_url_lanza_xdg_open_con_entorno_limpio(self):
        from services import opener

        with mock.patch.dict("os.environ", {
                "LD_LIBRARY_PATH": "/tmp/.mount/_internal",
                "LD_PRELOAD": "/tmp/.mount/libfoo.so"}, clear=False), \
                mock.patch.object(opener.subprocess, "Popen") as popen:
            ok = opener.open_url("https://example.test/x")

        self.assertTrue(ok)
        command = popen.call_args.args[0]
        self.assertEqual(command, ["xdg-open", "https://example.test/x"])
        env = popen.call_args.kwargs["env"]
        self.assertNotIn("LD_LIBRARY_PATH", env)
        self.assertNotIn("LD_PRELOAD", env)


class MacosDmgTests(unittest.TestCase):
    """El .dmg de macOS se monta y se copia el Blender.app a la carpeta destino.

    No hace falta un Mac: se mockea ``_run`` (hdiutil/ditto) y se comprueba la
    logica de montaje, nombre de carpeta y copia.
    """

    def _fake_run(self, calls):
        import plistlib
        import shutil

        def run(command, check=True):
            calls.append(command)
            if command[0].endswith("hdiutil") and "attach" in command:
                mount = Path(command[command.index("-mountpoint") + 1])
                app = mount / "Blender.app"
                (app / "Contents" / "MacOS").mkdir(parents=True)
                (app / "Contents" / "MacOS" / "Blender").write_text("bin")
                with (app / "Contents" / "Info.plist").open("wb") as handle:
                    plistlib.dump({"CFBundleShortVersionString": "9.9.9"}, handle)
            elif command[0].endswith("ditto") and Path(command[-2]).is_dir():
                shutil.copytree(command[-2], command[-1])
            return mock.Mock(returncode=0, stdout="", stderr="")

        return run

    def test_instala_el_app_en_el_destino(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dmg = base / "blender-9.9.9-macos-arm64.dmg"
            dmg.write_bytes(b"dmg")
            calls = []
            with mock.patch.object(macos_dmg, "_run",
                                   side_effect=self._fake_run(calls)):
                folder = macos_dmg.install(dmg, base / "Blenders", "9.9.9", "arm64")

            self.assertEqual(folder.name, "blender-9.9.9-macos-arm64")
            executable = folder / "Blender.app" / "Contents" / "MacOS" / "Blender"
            self.assertTrue(executable.is_file())
            # Se monta y SIEMPRE se desmonta (aunque la copia fallara).
            self.assertTrue(any("attach" in c for c in calls))
            self.assertTrue(any("detach" in c for c in calls))
            # Se copia con ditto y se quita la cuarentena (Gatekeeper).
            self.assertTrue(any(c[0].endswith("ditto") for c in calls))
            self.assertTrue(any(c[0].endswith("xattr") for c in calls))

    def test_extract_zip_usa_ditto_no_zipfile(self):
        """El .zip de la app se extrae con ditto: zipfile rompe el bundle.

        Python's zipfile pierde los bits de ejecución y los symlinks del .app,
        y el bundle extraído no arranca. Blender Launcher V2 usa ditto por esto.
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            zip_path = base / "BlenderManager-macos.zip"
            zip_path.write_bytes(b"zip")
            calls = []
            with mock.patch.object(macos_dmg, "_run",
                                   side_effect=lambda c, check=True: calls.append(c)):
                macos_dmg.extract_zip(zip_path, base / "out")
        self.assertEqual(calls[0][0], "/usr/bin/ditto")
        self.assertIn("-x", calls[0])
        self.assertIn("-k", calls[0])

    def test_usa_la_version_del_plist(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            dmg = base / "b.dmg"
            dmg.write_bytes(b"dmg")
            with mock.patch.object(macos_dmg, "_run",
                                   side_effect=self._fake_run([])):
                # El hint dice 1.0.0 pero el Info.plist dice 9.9.9: manda el plist.
                folder = macos_dmg.install(dmg, base, "1.0.0", "arm64")
            self.assertEqual(folder.name, "blender-9.9.9-macos-arm64")

    def test_dmg_sin_app_falla(self):
        import tempfile

        def run(command, check=True):
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmp:
            dmg = Path(tmp) / "b.dmg"
            dmg.write_bytes(b"dmg")
            with mock.patch.object(macos_dmg, "_run", side_effect=run):
                with self.assertRaises(macos_dmg.DmgError):
                    macos_dmg.install(dmg, Path(tmp) / "out", "5.2.1", "arm64")

    def test_available_solo_en_macos(self):
        with mock.patch.object(macos_dmg.sys, "platform", "linux"):
            self.assertFalse(macos_dmg.available())
        with mock.patch.object(macos_dmg.sys, "platform", "darwin"):
            self.assertTrue(macos_dmg.available())


class DefaultDestinationTests(unittest.TestCase):
    """La carpeta propuesta la primera vez depende del sistema."""

    def test_usa_downloads_si_no_hay_descargas(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(settings_module.Path, "home",
                                   return_value=Path(tmp)):
                self.assertEqual(settings_module.default_destination(),
                                 Path(tmp) / "Downloads" / "Blenders")

    def test_respeta_la_descargas_localizada(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Descargas").mkdir()
            with mock.patch.object(settings_module.Path, "home",
                                   return_value=Path(tmp)):
                self.assertEqual(settings_module.default_destination(),
                                 Path(tmp) / "Descargas" / "Blenders")


class WindowsDownloadsTests(unittest.TestCase):
    """En Windows la carpeta Descargas puede estar movida: manda el registro."""

    def _fake_winreg(self, value=None, raises=False):
        import contextlib
        import types

        module = types.ModuleType("winreg")
        module.HKEY_CURRENT_USER = object()
        if raises:
            def boom(*args, **kwargs):
                raise OSError("clave inexistente")
            module.OpenKey = boom
            module.QueryValueEx = boom
        else:
            module.OpenKey = lambda *a, **k: contextlib.nullcontext(object())
            module.QueryValueEx = lambda key, name: (value, 1)
        return module

    def test_usa_la_carpeta_conocida_del_registro(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            moved = Path(tmp) / "Descargas movidas"
            moved.mkdir()
            with mock.patch.object(settings_module.sys, "platform", "win32"), \
                    mock.patch.dict(sys.modules,
                                    {"winreg": self._fake_winreg(str(moved))}):
                self.assertEqual(settings_module._downloads_dir(), moved)

    def test_si_el_registro_falla_usa_downloads(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(settings_module.sys, "platform", "win32"), \
                    mock.patch.object(settings_module.Path, "home",
                                      return_value=Path(tmp)), \
                    mock.patch.dict(sys.modules,
                                    {"winreg": self._fake_winreg(raises=True)}):
                self.assertEqual(settings_module._downloads_dir(),
                                 Path(tmp) / "Downloads")


class DownloaderRetryTests(unittest.TestCase):
    """La conexión se reintenta: el handshake TLS de un CDN es intermitente.

    Caso real (Mac, 2026-09-18): "urlopen error _ssl.c:993: The handshake
    operation timed out" al bajar el zip de una release; el mismo asset había
    funcionado otras veces. Como el fallo es ANTES de descargar, reintentar sale
    gratis.
    """

    def setUp(self):
        from services import downloader

        self.downloader = downloader

    def test_reintenta_el_handshake_y_acaba_bien(self):
        import urllib.error

        d = self.downloader.Downloader()
        exito = object()
        with mock.patch.object(self.downloader.urllib.request, "urlopen",
                               side_effect=[urllib.error.URLError("handshake"),
                                            exito]) as abrir, \
                mock.patch.object(self.downloader, "RETRY_DELAY", 0):
            self.assertIs(d._connect("https://x/asset.zip"), exito)
        self.assertEqual(abrir.call_count, 2)

    def test_http_error_no_se_reintenta(self):
        import urllib.error

        d = self.downloader.Downloader()
        error = urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)
        with mock.patch.object(self.downloader.urllib.request, "urlopen",
                               side_effect=error) as abrir:
            with self.assertRaises(urllib.error.HTTPError):
                d._connect("https://x")
        self.assertEqual(abrir.call_count, 1)

    def test_cancelar_corta_los_reintentos(self):
        import urllib.error

        d = self.downloader.Downloader()
        d.cancel()
        with mock.patch.object(self.downloader.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("x")) as abrir:
            with self.assertRaises(urllib.error.URLError):
                d._connect("https://x")
        self.assertEqual(abrir.call_count, 1)

    def test_reintenta_el_429(self):
        """Un 429 ("Too Many Requests") sí se reintenta: el servidor pide espera."""
        import urllib.error

        d = self.downloader.Downloader()
        error = urllib.error.HTTPError("https://x", 429, "Too Many Requests",
                                       {"Retry-After": "0"}, None)
        exito = object()
        with mock.patch.object(self.downloader.urllib.request, "urlopen",
                               side_effect=[error, exito]) as abrir:
            self.assertIs(d._connect("https://x/asset.zip"), exito)
        self.assertEqual(abrir.call_count, 2)

    def test_retry_after_con_valor_raro_usa_el_respaldo(self):
        import urllib.error

        error = urllib.error.HTTPError("https://x", 429, "x",
                                       {"Retry-After": "pronto"}, None)
        self.assertEqual(self.downloader._retry_after(error),
                         float(self.downloader.RETRY_DELAY))
