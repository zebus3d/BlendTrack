"""Tests de ``services.forks``: Bforartists (WebDAV) y UPBGE (GitHub).

Los dos parsers son funciones puras sobre el texto que devuelve el servidor,
así que se prueban con XML/JSON de mentira y **sin red**. Lo que se comprueba
es lo que de verdad se rompe en producción: que el fichero de cada plataforma
se elija bien, que los dos .dmg de macOS no se mezclen, que una versión vieja
de UPBGE no cuele y que un listado caído se sirva del caché en vez de dejar la
pestaña vacía.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from model.build import FORK_BFORARTISTS, FORK_UPBGE
from services import forks as fk

# Un ``PROPFIND`` de la raíz con dos versiones y ruido (ficheros sueltos).
ROOT_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/public.php/webdav/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>
      <d:getlastmodified>Wed, 05 Aug 2026 09:44:14 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/Bforartists%203.2.1/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>
      <d:getlastmodified>Thu, 08 Dec 2022 09:59:21 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/Bforartists%205.2.0/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
  <d:response>
    <d:href>/public.php/webdav/notas.txt</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>12</d:getcontentlength>
    </d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>
  </d:response>
</d:multistatus>"""

# El contenido de ``Bforartists 5.2.0``: los ficheros de cada plataforma más
# capturas y el instalador de Windows, que no deben colarse.
VERSION_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response><d:href>/public.php/webdav/Bforartists%205.2.0/</d:href>
    <d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
    </d:prop></d:propstat></d:response>
  <d:response><d:href>/public.php/webdav/Bforartists%205.2.0/Bforartists-5.2.0-Linux.tar.xz</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>1000</d:getcontentlength>
    </d:prop></d:propstat></d:response>
  <d:response><d:href>/public.php/webdav/Bforartists%205.2.0/Bforartists-5.2.0-Windows.zip</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>2000</d:getcontentlength>
    </d:prop></d:propstat></d:response>
  <d:response><d:href>/public.php/webdav/Bforartists%205.2.0/Bforartists-5.2.0-Mac-Intel.dmg</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>3000</d:getcontentlength>
    </d:prop></d:propstat></d:response>
  <d:response><d:href>/public.php/webdav/Bforartists%205.2.0/Bforartists-5.2.0-Mac-Silicon.dmg</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>4000</d:getcontentlength>
    </d:prop></d:propstat></d:response>
  <d:response><d:href>/public.php/webdav/Bforartists%205.2.0/Install_Bforartists5_520.exe</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>5000</d:getcontentlength>
    </d:prop></d:propstat></d:response>
  <d:response><d:href>/public.php/webdav/Bforartists%205.2.0/splash.png</d:href>
    <d:propstat><d:prop><d:resourcetype/>
      <d:getlastmodified>Tue, 01 Jul 2026 10:00:00 GMT</d:getlastmodified>
      <d:getcontentlength>10</d:getcontentlength>
    </d:prop></d:propstat></d:response>
</d:multistatus>"""


class BfaWebDavTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = fk.cache_dir
        fk.cache_dir = lambda: Path(self.tmp.name)

    def tearDown(self):
        fk.cache_dir = self._original
        self.tmp.cleanup()

    def _propfind(self, url, timeout):
        return VERSION_XML.encode() if "5.2.0" in url else ROOT_XML.encode()

    def test_lista_las_versiones_y_elige_el_fichero_de_cada_plataforma(self):
        with mock.patch.object(fk, "_propfind", side_effect=self._propfind):
            builds = fk.fetch_bforartists()
        self.assertEqual(sorted({b.version for b in builds}), ["5.2.0"])
        de_520 = [b for b in builds if b.version == "5.2.0"]
        por_plataforma = {(b.platform, b.arch): b for b in de_520}
        self.assertIn(("linux", "x86_64"), por_plataforma)
        self.assertIn(("windows", "x86_64"), por_plataforma)
        # Los dos .dmg de macOS no pueden caer en la misma clave.
        self.assertIn(("darwin", "x86_64"), por_plataforma)
        self.assertIn(("darwin", "arm64"), por_plataforma)
        self.assertTrue(all(b.fork == FORK_BFORARTISTS for b in builds))
        self.assertTrue(all(b.risk == "stable" for b in builds))

    def test_ignora_capturas_e_instaladores(self):
        with mock.patch.object(fk, "_propfind", side_effect=self._propfind):
            builds = fk.fetch_bforartists()
        nombres = {b.filename for b in builds}
        self.assertNotIn("Install_Bforartists5_520.exe", nombres)
        self.assertNotIn("splash.png", nombres)

    def test_segunda_pasada_usa_el_cache_por_fecha(self):
        """Si nada cambió en la versión, no se vuelve a pedir su contenido."""
        calls = []

        def counting(url, timeout):
            calls.append(url)
            # La 3.2.1 no tiene ficheros que nos interesen, así que su
            # contenido se pide igual la primera vez.
            return VERSION_XML.encode() if "5.2.0" in url else ROOT_XML.encode()

        with mock.patch.object(fk, "_propfind", side_effect=counting):
            fk.fetch_bforartists()
        first = len(calls)
        self.assertGreater(first, 1)   # raíz + contenido de las versiones
        calls.clear()
        with mock.patch.object(fk, "_propfind", side_effect=counting):
            builds = fk.fetch_bforartists()
        # La segunda vez solo se pide la raíz, no el contenido de ninguna.
        self.assertEqual(len(calls), 1)
        self.assertTrue(builds)

    def test_si_la_raiz_cae_se_sirve_del_cache(self):
        with mock.patch.object(fk, "_propfind", side_effect=self._propfind):
            first = fk.fetch_bforartists()
        self.assertTrue(first)
        # Se borra la marca de fecha de una versión: al reconsultar, falla.
        path = fk.bfa_cache_path()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["versions"]["3.2.1"]["modified"] = 0
        path.write_text(json.dumps(data), encoding="utf-8")

        def fail(url, timeout):
            if "3.2.1" in url:
                raise OSError("sin red")
            return self._propfind(url, timeout)

        with mock.patch.object(fk, "_propfind", side_effect=fail):
            builds = fk.fetch_bforartists()
        # La que falló se queda con lo guardado; la pestaña no queda vacía.
        self.assertTrue(builds)
        self.assertTrue(all(b.fork == FORK_BFORARTISTS for b in builds))

    def test_sin_red_en_la_raiz_se_sirve_el_cache_entero(self):
        """Con la red caída del todo, la pestaña enseña lo de la última vez.

        El caché se guarda por versión (``versions``); antes, al caer la raíz,
        se buscaba una clave ``builds`` que no existía y salía vacía.
        """
        with mock.patch.object(fk, "_propfind", side_effect=self._propfind):
            first = fk.fetch_bforartists()
        self.assertTrue(first)
        with mock.patch.object(fk, "_propfind", side_effect=OSError("sin red")):
            offline = fk.fetch_bforartists()
        self.assertEqual(sorted(b.filename for b in offline),
                         sorted(b.filename for b in first))

    def test_xml_roto_no_revienta(self):
        self.assertEqual(fk._parse_multistatus(b"<no xml"), [])


def _release(tag, published, assets, html="https://github.com/x/y"):
    return {
        "tag_name": tag,
        "published_at": published,
        "html_url": html,
        "draft": False,
        "assets": [
            {"name": name, "browser_download_url": "https://x/" + name,
             "size": len(name), "digest": "sha256:" + "a" * 64}
            for name in assets
        ],
    }


class UpbgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._original = fk.cache_dir
        fk.cache_dir = lambda: Path(self.tmp.name)

    def tearDown(self):
        fk.cache_dir = self._original
        self.tmp.cleanup()

    def test_weekly_es_alfa_experimental(self):
        releases = [_release("weekly-build-101", "2026-09-20T07:56:13Z", [
            "upbge-0.53-alpha-linux-x86_64-2026-09-20.tar.gz",
            "upbge-0.53-alpha-windows-x86_64-2026-09-20.zip",
        ])]
        builds = fk._parse_upbge(releases)
        self.assertEqual(len(builds), 2)
        for build in builds:
            self.assertEqual(build.fork, FORK_UPBGE)
            self.assertEqual(build.version, "0.53")
            self.assertEqual(build.branch, "upbge-weekly")
            self.assertEqual(build.risk, "alpha")
            self.assertTrue(build.experimental)
            self.assertEqual(build.notes_url, "https://github.com/x/y")

    def test_un_tag_estable_sale_como_estable(self):
        releases = [_release("v0.40", "2026-01-01T00:00:00Z", [
            "upbge-0.40-linux-x86_64.tar.xz"])]
        builds = fk._parse_upbge(releases)
        self.assertEqual(builds[0].branch, "upbge-stable")
        self.assertEqual(builds[0].risk, "stable")
        self.assertFalse(builds[0].experimental)

    def test_las_versiones_antiguas_no_cuelan(self):
        """Por debajo de 0.30 son releases heredados y no van a la tienda."""
        releases = [_release("v0.25", "2018-01-01T00:00:00Z", [
            "upbge-0.25-linux-x86_64.tar.xz"])]
        self.assertEqual(fk._parse_upbge(releases), [])

    def test_un_draft_no_cuenta(self):
        release = _release("v0.40", "2026-01-01T00:00:00Z",
                           ["upbge-0.40-linux-x86_64.tar.xz"])
        release["draft"] = True
        self.assertEqual(fk._parse_upbge([release]), [])

    def test_304_reutiliza_el_cache(self):
        releases = [_release("weekly-build-1", "2026-09-20T07:56:13Z",
                             ["upbge-0.53-alpha-linux-x86_64-x.tar.gz"])]
        with mock.patch.object(fk, "_http_get_json",
                               return_value=(releases, "etag1")):
            first = fk.fetch_upbge()
        self.assertTrue(first)
        # Segunda vez: la API contesta 304 (None) y se sirve lo guardado.
        with mock.patch.object(fk, "_http_get_json",
                               return_value=(None, "etag1")) as got:
            second = fk.fetch_upbge()
        self.assertEqual(len(second), len(first))
        # Se mandó el ETag guardado como ``If-None-Match``.
        self.assertEqual(got.call_args.kwargs.get("etag"), "etag1")
        self.assertEqual(second[0].fork, FORK_UPBGE)

    def test_la_api_de_github_con_error_se_sirve_del_cache(self):
        releases = [_release("weekly-build-1", "2026-09-20T07:56:13Z",
                             ["upbge-0.53-alpha-linux-x86_64-x.tar.gz"])]
        with mock.patch.object(fk, "_http_get_json",
                               return_value=(releases, "e")):
            fk.fetch_upbge()
        with mock.patch.object(fk, "_http_get_json",
                               side_effect=OSError("rate limit")):
            second = fk.fetch_upbge()
        self.assertTrue(second)


class ForkFetchTest(unittest.TestCase):
    def test_fetch_por_identificador(self):
        original = dict(fk.FORKS)
        try:
            fk.FORKS[FORK_BFORARTISTS] = {"fetch": lambda timeout=20: ["x"],
                                          "label": "Bforartists"}
            fk.FORKS[FORK_UPBGE] = {"fetch": lambda timeout=20: ["y"],
                                    "label": "UPBGE"}
            self.assertEqual(fk.fetch(FORK_BFORARTISTS), ["x"])
            self.assertEqual(fk.fetch(FORK_UPBGE), ["y"])
        finally:
            fk.FORKS.clear()
            fk.FORKS.update(original)

    def test_un_fork_desconocido_devuelve_vacio(self):
        self.assertEqual(fk.fetch("nope"), [])

    def test_las_descargas_de_bforartists_llevan_autenticacion(self):
        """El share es público pero el WebDAV pide la misma Basic que el listado.

        Sin esto, descargar una versión de Bforartists daba 401.
        """
        headers = fk.download_headers(
            "https://cloud.bforartists.de/public.php/webdav/x.tar.xz")
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_las_descargas_de_blender_no_llevan_autenticacion(self):
        self.assertEqual(fk.download_headers("https://cdn.blender.org/x.tar.xz"),
                         {})
        self.assertEqual(fk.download_headers("https://github.com/x/y.zip"), {})


if __name__ == "__main__":
    unittest.main()
