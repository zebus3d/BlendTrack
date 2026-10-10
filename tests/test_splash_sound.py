"""Los splash de la galería (``services.splash``) y su "tic" (``services.sound``).

No hay red: ``urlopen`` va mockeado. La caché es la temporal de la suite
(``tests/__init__.py`` redirige ``downloader.cache_dir``).
"""

import io
import os
import struct
import time
import unittest
import urllib.error
from unittest import mock

from services import sound, splash

PNG = splash.PNG_MAGIC + b"\x00" * 64
LFS_POINTER = (b"version https://git-lfs.github.com/spec/v1\n"
               b"oid sha256:9ef1\nsize 837352\n")


def _response(data: bytes):
    response = mock.MagicMock()
    response.__enter__.return_value = io.BytesIO(data)
    return response


class SplashSeriesTests(unittest.TestCase):
    def test_una_estable_usa_la_rama_de_su_serie(self):
        self.assertEqual(splash.series_for("5.1.2", "v51", "stable"), "5.1")
        self.assertIn("blender-v5.1-release", splash.url_for("5.1"))

    def test_las_alfas_usan_main(self):
        self.assertEqual(splash.series_for("5.4.0", "main", "alpha"), "main")
        self.assertIn("/branch/main/", splash.url_for("main"))

    def test_los_forks_no_tienen_splash(self):
        self.assertEqual(splash.series_for("5.0.0", "v50", "stable",
                                           "bforartists"), "")
        self.assertIsNone(splash.cached(""))
        self.assertFalse(splash.needs_fetch(""))

    def test_la_url_es_la_de_lfs_y_no_la_raw(self):
        """``raw/`` devuelve un puntero de LFS de 131 bytes, no el PNG."""
        self.assertIn("/media/branch/", splash.url_for("4.2"))


class SplashEmbeddedTests(unittest.TestCase):
    """El splash va dentro del ejecutable de Blender (``datatoc``)."""

    @staticmethod
    def _png(width, height, body=b"x" * 32):
        header = struct.pack(">II", width, height)
        return (splash.PNG_MAGIC + b"\x00\x00\x00\x0dIHDR" + header + body
                + b"\x00\x00\x00\x00IEND\xaeB`\x82")

    def test_encuentra_el_png_con_proporcion_de_splash(self):
        icon = self._png(64, 64)
        splash_png = self._png(1000, 500)
        binary = b"ELF..." + icon + b"datos" + splash_png + b"mas datos"
        self.assertEqual(splash.find_embedded(binary), splash_png)

    def test_una_firma_suelta_sin_cabecera_no_cuenta(self):
        binary = b"basura" + splash.PNG_MAGIC + b"no es una cabecera" * 4
        self.assertIsNone(splash.find_embedded(binary))

    def test_se_queda_con_el_mayor(self):
        small = self._png(501, 250)
        big = self._png(1002, 500)
        self.assertEqual(splash.find_embedded(small + big), big)

    def test_lo_saca_de_un_ejecutable_a_la_cache(self):
        import tempfile
        from pathlib import Path

        png = self._png(1000, 500)
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "blender"
            exe.write_bytes(b"\x7fELF" + b"\x00" * 100 + png + b"\x00" * 50)
            series = f"8.{os.getpid() % 1000}"
            path = splash.extract_from(series, exe)
            self.addCleanup(path.unlink, missing_ok=True)
        self.assertEqual(path.read_bytes(), png)

    def test_un_ejecutable_que_no_existe_no_rompe(self):
        self.assertIsNone(splash.extract_from("7.7", "/no/existe/blender"))


class SplashFetchTests(unittest.TestCase):
    def setUp(self):
        self.series = f"9.{os.getpid() % 1000}"
        self.addCleanup(self._clean)

    def _clean(self):
        folder = splash._folder()
        for suffix in (".png", ".missing"):
            (folder / f"{self.series}{suffix}").unlink(missing_ok=True)

    def test_baja_el_png_y_lo_deja_en_cache(self):
        with mock.patch.object(splash.urllib.request, "urlopen",
                               return_value=_response(PNG)):
            path = splash.fetch(self.series)
        self.assertEqual(path, splash.cached(self.series))
        self.assertEqual(path.read_bytes(), PNG)
        self.assertFalse(splash.needs_fetch(self.series))

    def test_un_puntero_de_lfs_no_se_guarda_como_imagen(self):
        with mock.patch.object(splash.urllib.request, "urlopen",
                               return_value=_response(LFS_POINTER)):
            self.assertIsNone(splash.fetch(self.series))
        self.assertIsNone(splash.cached(self.series))
        # Y no se vuelve a preguntar en cada arranque.
        self.assertFalse(splash.needs_fetch(self.series))

    def test_un_404_se_recuerda(self):
        error = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(splash.urllib.request, "urlopen",
                               side_effect=error):
            self.assertIsNone(splash.fetch(self.series))
        self.assertFalse(splash.needs_fetch(self.series))

    def test_sin_red_no_se_anota_nada_y_se_reintenta(self):
        with mock.patch.object(splash.urllib.request, "urlopen",
                               side_effect=OSError("sin red")):
            self.assertIsNone(splash.fetch(self.series))
        self.assertTrue(splash.needs_fetch(self.series))

    def test_el_de_main_caduca_a_la_semana(self):
        folder = splash._folder()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "main.png"
        had = path.exists()
        if not had:
            path.write_bytes(PNG)
            self.addCleanup(path.unlink, missing_ok=True)
        old = time.time() - splash.REFRESH_SECONDS - 60
        stat = path.stat()
        os.utime(path, (old, old))
        self.addCleanup(os.utime, path, (stat.st_atime, stat.st_mtime))
        self.assertTrue(splash.needs_fetch("main"))


class SoundTests(unittest.TestCase):
    def test_los_sonidos_son_cortos_y_no_saturan(self):
        for name in ("tick", "lock"):
            data = sound.samples(name)
            self.assertLess(len(data), sound.RATE // 5, name)
            self.assertLessEqual(max(abs(v) for v in data),
                                 32767 * sound.VOLUME[name] + 1, name)
            # Acaban en silencio (fundido): cortar a pelo da un chasquido.
            self.assertEqual(data[-1], 0, name)

    def test_el_clac_suena_mas_fuerte_que_el_tic(self):
        tick = max(abs(v) for v in sound.samples("tick"))
        lock = max(abs(v) for v in sound.samples("lock"))
        self.assertGreater(lock, tick * 2)

    def test_el_wav_se_escribe_una_vez_en_la_cache(self):
        path = sound.path_for("lock")
        self.assertTrue(path.is_file())
        self.assertIn(f"v{sound.SOUND_VERSION}", path.name)
        self.assertEqual(sound.path_for("lock"), path)

    def _play(self, player, *names):
        with mock.patch.object(sound, "_player", player), \
                mock.patch.object(sound, "_last", {}), \
                mock.patch.object(sound.sys, "platform", "linux"), \
                mock.patch.object(sound.subprocess, "Popen") as popen:
            for name in names:
                sound.play(name)
        return popen

    def test_sin_reproductor_no_pasa_nada(self):
        self._play([None], "tick").assert_not_called()

    def test_lanza_el_reproductor_con_el_entorno_limpio(self):
        popen = self._play([["/usr/bin/pw-play"]], "lock")
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], "/usr/bin/pw-play")
        self.assertIn("env", kwargs)

    def test_los_seguidos_se_saltan_pero_cada_sonido_va_aparte(self):
        """Dos tics juntos suenan uno; el clac no lo tapa un tic de antes."""
        popen = self._play([["/usr/bin/pw-play"]], "tick", "tick", "lock")
        self.assertEqual(popen.call_count, 2)


if __name__ == "__main__":
    unittest.main()
