"""Tests de ``services.channels``: qué tipo es cada compilación y dónde va.

Todo son funciones puras, así que no hace falta disco ni Qt. Es a propósito:
de esta clasificación depende en qué carpeta se escriben cientos de MB, y eso
tiene que poder probarse con datos de mentira y en milisegundos.
"""

import unittest
from dataclasses import dataclass, field

from model.build import Build
from services import channels as ch


@dataclass
class _Carpeta:
    """Lo mínimo que ``channels`` necesita de una carpeta.

    Se usa una de mentira en vez de ``settings.Folder`` para que estos tests no
    dependan del esquema de ajustes: aquí solo se prueba el reparto.
    """

    path: str
    types: list = field(default_factory=list)
    writable: bool = True

    def takes(self, build_type: str) -> bool:
        return self.writable and build_type in self.types


def _build(version="5.2.1", branch="v52", risk="stable", experimental=False,
           fork=""):
    return Build(version=version, branch=branch, risk=risk, platform="linux",
                 arch="x86_64", url="", filename="x.tar.xz",
                 experimental=experimental, fork=fork)


class TipoDeCompilacionTest(unittest.TestCase):
    def test_estable_normal(self):
        self.assertEqual(ch.type_of_build(_build("5.1.2", "v51")), ch.TYPE_STABLE)

    def test_lts_por_su_serie(self):
        # 5.2 está en LTS_MINORS.
        self.assertEqual(ch.type_of_build(_build("5.2.1", "v52")), ch.TYPE_LTS)

    def test_una_alfa_de_una_serie_lts_es_diaria(self):
        """Manda de dónde viene la compilación, no cómo se llama.

        Una alfa de 5.2 lleva el número de una serie LTS pero no es una LTS:
        si contase como tal acabaría en la carpeta de las LTS.
        """
        self.assertEqual(ch.type_of_build(_build("5.2.0", "main", risk="alpha")),
                         ch.TYPE_DAILY)

    def test_experimental_manda_sobre_todo(self):
        build = _build("5.2.0", "geometry-nodes", risk="stable",
                       experimental=True)
        self.assertEqual(ch.type_of_build(build), ch.TYPE_EXPERIMENTAL)

    def test_un_fork_tiene_su_propio_tipo(self):
        """Bforartists 5.2.0 no es la LTS de Blender 5.2, aunque se llame igual."""
        self.assertEqual(ch.type_of_build(_build("5.2.0", fork="bforartists")),
                         ch.TYPE_BFORARTISTS)
        self.assertEqual(ch.type_of_build(_build("0.53", "upbge-weekly",
                                                 risk="alpha", fork="upbge")),
                         ch.TYPE_UPBGE)


class TipoDeInstalacionTest(unittest.TestCase):
    def test_el_marcador_manda(self):
        """Con ``risk`` anotado no hay que adivinar: lo dijo la API."""
        self.assertEqual(
            ch.type_from_marker("main", "5.3.0", "blender-5.3.0", risk="alpha"),
            ch.TYPE_DAILY)

    def test_rama_de_funciones_es_experimental(self):
        self.assertEqual(ch.type_from_marker("geometry-nodes", "5.2.0", "x"),
                         ch.TYPE_EXPERIMENTAL)

    def test_rama_main_es_diaria(self):
        self.assertEqual(ch.type_from_marker("main", "5.3.0", "blender-5.3.0"),
                         ch.TYPE_DAILY)

    def test_rama_con_v_es_estable(self):
        self.assertEqual(ch.type_from_marker("v51", "5.1.2", "blender-5.1.2"),
                         ch.TYPE_STABLE)

    def test_una_rama_que_solo_empieza_por_v_es_experimental(self):
        """``vulkan`` empieza por "v" pero no es una rama de versión (``v52``)."""
        for branch in ("vulkan", "vse-rework", "v"):
            with self.subTest(branch=branch):
                self.assertEqual(
                    ch.type_from_marker(branch, "5.2.0", "blender-5.2.0"),
                    ch.TYPE_EXPERIMENTAL)

    def test_sin_rama_el_nombre_delata_la_diaria(self):
        """Instalaciones de antes del marcador: solo queda el nombre."""
        self.assertEqual(ch.type_from_marker("", "5.3.0", "blender-5.3.0-alpha"),
                         ch.TYPE_DAILY)

    def test_sin_rama_y_serie_lts_es_lts(self):
        self.assertEqual(ch.type_from_marker("", "5.2.1", "blender-5.2.1"),
                         ch.TYPE_LTS)

    def test_sin_nada_se_da_por_estable(self):
        self.assertEqual(ch.type_from_marker("", "5.1.2", ""), ch.TYPE_STABLE)

    def test_una_instalacion_de_fork_es_de_su_tipo(self):
        self.assertEqual(ch.type_from_marker("bforartists", "5.2.0",
                                             "Bforartists-5.2.0-Linux",
                                             "stable", "bforartists"),
                         ch.TYPE_BFORARTISTS)
        self.assertEqual(ch.type_from_marker("upbge-weekly", "0.53", "x",
                                             "alpha", "upbge"),
                         ch.TYPE_UPBGE)


class DestinoTest(unittest.TestCase):
    def _biblioteca(self):
        return [
            _Carpeta("/ssd", [ch.TYPE_LTS, ch.TYPE_STABLE]),
            _Carpeta("/datos", [ch.TYPE_DAILY, ch.TYPE_EXPERIMENTAL]),
            _Carpeta("/viejos", [], writable=False),
        ]

    def test_cada_tipo_va_a_su_carpeta(self):
        carpetas = self._biblioteca()
        self.assertEqual(ch.resolve_destination(carpetas, ch.TYPE_LTS), "/ssd")
        self.assertEqual(ch.resolve_destination(carpetas, ch.TYPE_STABLE), "/ssd")
        self.assertEqual(ch.resolve_destination(carpetas, ch.TYPE_DAILY), "/datos")

    def test_un_tipo_sin_dueno_no_tiene_destino(self):
        """Nada de colarlo en la primera carpeta que pille: se dice y ya.

        Con casillas, quién recibe cada tipo lo ha puesto el usuario a mano; si
        no lo ha puesto, adivinar sería escribir cientos de MB donde no pidió.
        """
        carpetas = [_Carpeta("/ssd", [ch.TYPE_LTS])]
        self.assertEqual(ch.resolve_destination(carpetas, ch.TYPE_DAILY), "")
        self.assertIsNone(ch.owner_of(carpetas, ch.TYPE_DAILY))

    def test_una_carpeta_de_solo_lectura_nunca_recibe(self):
        carpetas = [_Carpeta("/viejos", [ch.TYPE_LTS], writable=False)]
        self.assertEqual(ch.resolve_destination(carpetas, ch.TYPE_LTS), "")

    def test_sin_carpetas_no_hay_destino(self):
        self.assertEqual(ch.resolve_destination([], ch.TYPE_LTS), "")

    def test_tipos_huerfanos_en_orden(self):
        carpetas = [_Carpeta("/ssd", [ch.TYPE_STABLE])]
        self.assertEqual(ch.orphan_types(carpetas),
                         [ch.TYPE_LTS, ch.TYPE_DAILY, ch.TYPE_EXPERIMENTAL,
                          ch.TYPE_BFORARTISTS, ch.TYPE_UPBGE])

    def test_los_forks_apagados_no_salen_como_huerfanos(self):
        """Sin forks activados no hay que avisar de que no tienen carpeta."""
        carpetas = [_Carpeta("/ssd", [ch.TYPE_STABLE])]
        self.assertEqual(ch.orphan_types(carpetas, ch.active_types([])),
                         [ch.TYPE_LTS, ch.TYPE_DAILY, ch.TYPE_EXPERIMENTAL])
        self.assertEqual(
            ch.orphan_types(carpetas, ch.active_types(["bforartists"])),
            [ch.TYPE_LTS, ch.TYPE_DAILY, ch.TYPE_EXPERIMENTAL,
             ch.TYPE_BFORARTISTS])

    def test_sin_huerfanos_cuando_una_carpeta_lo_coge_todo(self):
        """Es lo que hereda la carpeta de siempre al actualizar la app."""
        carpetas = [_Carpeta("/blenders", list(ch.BUILD_TYPES))]
        self.assertEqual(ch.orphan_types(carpetas), [])


class RutaNormalizadaTest(unittest.TestCase):
    def test_expande_la_virgulilla(self):
        self.assertNotIn("~", ch.normalize_path("~/Blender"))

    def test_vacio_se_queda_vacio(self):
        self.assertEqual(ch.normalize_path(""), "")
        self.assertEqual(ch.normalize_path(None), "")

    def test_la_misma_ruta_normaliza_igual(self):
        self.assertEqual(ch.normalize_path("/tmp/datos"),
                         ch.normalize_path("/tmp/datos/"))


if __name__ == "__main__":
    unittest.main()
