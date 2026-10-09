"""Tests de ``services.organizer``: qué se mueve, y que moverlo no pierde nada.

El test que de verdad importa es ``test_un_fallo_a_media_copia_no_toca_el_origen``:
es el único que demuestra la invariante del módulo (el origen no se borra hasta
que el destino está completo). Los demás están para que ese siga teniendo
sentido.
"""

import os
import shutil
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest import mock

from services import channels, organizer


@dataclass
class _Carpeta:
    path: str
    types: list = field(default_factory=list)
    writable: bool = True

    def takes(self, build_type):
        return self.writable and build_type in self.types


@dataclass
class _Instalada:
    name: str
    path: Path
    version: str = "5.2.1"
    branch: str = "v52"
    risk: str = "stable"
    root: Path = None
    executable: Path = None


def _instalar(root: Path, nombre: str, contenido=b"blender", **kwargs):
    """Crea una carpeta de instalación de mentira, con algún fichero dentro."""
    carpeta = Path(root) / nombre
    (carpeta / "4.5" / "scripts").mkdir(parents=True, exist_ok=True)
    (carpeta / "blender").write_bytes(contenido)
    (carpeta / "4.5" / "scripts" / "x.py").write_text("pass", encoding="utf-8")
    return _Instalada(name=nombre, path=carpeta, root=Path(root), **kwargs)


class PlanTest(unittest.TestCase):
    def test_una_sola_carpeta_con_todo_no_propone_nada(self):
        """El escenario de todos los usuarios tras actualizar la app.

        Si esto propusiera algo, actualizar le ofrecería mover carpetas a gente
        que no ha pedido nada.
        """
        carpeta = _Carpeta("/datos", list(channels.BUILD_TYPES))
        entradas = [
            _Instalada("blender-5.2.1", Path("/datos/blender-5.2.1"),
                       root=Path("/datos")),
            _Instalada("blender-5.3.0", Path("/datos/blender-5.3.0"),
                       branch="main", risk="alpha", root=Path("/datos")),
        ]
        self.assertEqual(organizer.plan_reorg(entradas, [carpeta]), [])

    def test_propone_la_lts_que_esta_donde_ya_no_toca(self):
        carpetas = [_Carpeta("/datos", [channels.TYPE_STABLE,
                                        channels.TYPE_DAILY]),
                    _Carpeta("/ssd", [channels.TYPE_LTS])]
        lts = _Instalada("blender-5.2.1", Path("/datos/blender-5.2.1"),
                         root=Path("/datos"))
        estable = _Instalada("blender-5.1.2", Path("/datos/blender-5.1.2"),
                             version="5.1.2", branch="v51",
                             root=Path("/datos"))
        moves = organizer.plan_reorg([lts, estable], carpetas)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0].entry, lts)
        self.assertEqual(moves[0].target_root, Path("/ssd"))
        self.assertEqual(moves[0].build_type, channels.TYPE_LTS)

    def test_sin_carpeta_para_ese_tipo_no_se_propone_nada(self):
        """No hay adónde llevarla, así que no se ofrece moverla."""
        carpetas = [_Carpeta("/datos", [channels.TYPE_STABLE])]
        lts = _Instalada("blender-5.2.1", Path("/datos/blender-5.2.1"),
                         root=Path("/datos"))
        self.assertEqual(organizer.plan_reorg([lts], carpetas), [])

    def test_nunca_se_saca_nada_de_una_carpeta_sin_tipos(self):
        carpetas = [_Carpeta("/viejos", []),
                    _Carpeta("/ssd", list(channels.BUILD_TYPES))]
        entrada = _Instalada("blender-5.2.1", Path("/viejos/blender-5.2.1"),
                             root=Path("/viejos"))
        self.assertEqual(organizer.plan_reorg([entrada], carpetas), [])

    def test_nunca_se_saca_nada_de_una_carpeta_de_solo_lectura(self):
        carpetas = [_Carpeta("/viejos", [channels.TYPE_STABLE], writable=False),
                    _Carpeta("/ssd", [channels.TYPE_LTS])]
        entrada = _Instalada("blender-5.2.1", Path("/viejos/blender-5.2.1"),
                             root=Path("/viejos"))
        self.assertEqual(organizer.plan_reorg([entrada], carpetas), [])

    def test_una_instalada_suelta_sin_raiz_se_ignora(self):
        carpetas = [_Carpeta("/ssd", list(channels.BUILD_TYPES))]
        entrada = _Instalada("blender-5.2.1", Path("/otro/blender-5.2.1"))
        self.assertEqual(organizer.plan_reorg([entrada], carpetas), [])

    def test_misplaced_solo_mira_una_carpeta(self):
        carpetas = [_Carpeta("/datos", [channels.TYPE_STABLE]),
                    _Carpeta("/otra", [channels.TYPE_STABLE]),
                    _Carpeta("/ssd", [channels.TYPE_LTS])]
        aqui = _Instalada("a", Path("/datos/a"), root=Path("/datos"))
        alla = _Instalada("b", Path("/otra/b"), root=Path("/otra"))
        moves = organizer.misplaced([aqui, alla], carpetas, "/datos")
        self.assertEqual([m.entry for m in moves], [aqui])


class MoveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.origen = self.base / "datos"
        self.destino = self.base / "ssd"
        self.origen.mkdir()
        self.destino.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def _move(self, entrada):
        return organizer.Move(entry=entrada, source_root=self.origen,
                              target_root=self.destino,
                              build_type=channels.TYPE_LTS)

    def test_mismo_volumen_es_un_renombrado(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        destino = organizer.move_build(self._move(entrada))
        self.assertEqual(destino, self.destino / "blender-5.2.1")
        self.assertTrue((destino / "blender").is_file())
        self.assertTrue((destino / "4.5" / "scripts" / "x.py").is_file())
        self.assertFalse(entrada.path.exists())

    def test_informa_del_progreso(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        avisos = []
        organizer.move_build(self._move(entrada),
                             on_progress=lambda hecho, total: avisos.append(
                                 (hecho, total)))
        self.assertTrue(avisos)
        self.assertEqual(avisos[-1][0], avisos[-1][1])

    def test_no_pisa_una_carpeta_que_ya_existe(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        (self.destino / "blender-5.2.1").mkdir()
        with self.assertRaises(organizer.OrganizerError) as caught:
            organizer.move_build(self._move(entrada))
        self.assertEqual(caught.exception.code, "exists")
        # Y el origen sigue donde estaba.
        self.assertTrue((entrada.path / "blender").is_file())

    def test_sin_espacio_no_empieza(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        falso = mock.Mock(free=1, total=10, used=9)
        with mock.patch.object(organizer.shutil, "disk_usage",
                               return_value=falso):
            with self.assertRaises(organizer.OrganizerError) as caught:
                organizer.move_build(self._move(entrada))
        self.assertEqual(caught.exception.code, "no_space")
        self.assertTrue(entrada.path.exists())

    def test_con_blender_abierto_no_se_mueve(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        entrada.executable = entrada.path / "blender"
        with mock.patch.object(organizer.blender_runner, "is_running",
                               return_value=True):
            with self.assertRaises(organizer.OrganizerError) as caught:
                organizer.move_build(self._move(entrada))
        self.assertEqual(caught.exception.code, "running")
        self.assertTrue(entrada.path.exists())

    def test_destino_sin_permiso_de_escritura(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        with mock.patch.object(organizer.Path, "mkdir",
                               side_effect=OSError("permiso denegado")):
            with self.assertRaises(organizer.OrganizerError) as caught:
                organizer.move_build(self._move(entrada))
        self.assertEqual(caught.exception.code, "not_writable")
        self.assertTrue(entrada.path.exists())


class MoveEntreDiscosTest(unittest.TestCase):
    """El camino peligroso: copiar y borrar, que es donde se pierde una build."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.origen = self.base / "datos"
        self.destino = self.base / "ssd"
        self.origen.mkdir()
        self.destino.mkdir()
        self.addCleanup(self.tmp.cleanup)
        # Se finge que están en discos distintos, que es lo que obliga a copiar.
        parche = mock.patch.object(organizer, "_same_volume",
                                   return_value=False)
        parche.start()
        self.addCleanup(parche.stop)

    def _move(self, entrada):
        return organizer.Move(entry=entrada, source_root=self.origen,
                              target_root=self.destino,
                              build_type=channels.TYPE_LTS)

    def test_copia_y_borra_el_origen_al_final(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        destino = organizer.move_build(self._move(entrada))
        self.assertTrue((destino / "blender").is_file())
        self.assertTrue((destino / "4.5" / "scripts" / "x.py").is_file())
        self.assertFalse(entrada.path.exists())
        # Y no queda ningún temporal suelto.
        self.assertEqual(list(self.destino.glob("*" + organizer.MOVING_SUFFIX)),
                         [])

    def test_el_temporal_no_sale_como_instalacion_a_media_copia(self):
        """Un escaneo mientras se copia no puede ver el temporal.

        Si la pestaña de instaladas se refresca a mitad de una copia entre
        discos, el temporal no puede aparecer como una versión más (con su
        botón de lanzar, y a medio copiar).
        """
        from services import installed

        entrada = _instalar(self.origen, "blender-5.2.1")
        vistas = []
        real = organizer._copy_tree

        def copiar_y_escanear(*args, **kwargs):
            real(*args, **kwargs)
            vistas.extend(installed.scan_folders([self.destino], "linux"))

        with mock.patch.object(organizer, "_copy_tree",
                               side_effect=copiar_y_escanear):
            organizer.move_build(self._move(entrada))
        self.assertEqual(vistas, [])

    def test_un_fallo_a_media_copia_no_toca_el_origen(self):
        """La invariante del módulo, y el único test que la demuestra."""
        entrada = _instalar(self.origen, "blender-5.2.1")
        real = shutil.copy2
        llamadas = {"n": 0}

        def copy2_que_falla(src, dst, **kwargs):
            llamadas["n"] += 1
            if llamadas["n"] > 1:
                raise OSError("el disco se ha desconectado")
            return real(src, dst, **kwargs)

        with mock.patch.object(organizer.shutil, "copy2",
                               side_effect=copy2_que_falla):
            with self.assertRaises(organizer.OrganizerError) as caught:
                organizer.move_build(self._move(entrada))
        self.assertEqual(caught.exception.code, "io")
        # El origen, intacto y completo.
        self.assertTrue((entrada.path / "blender").is_file())
        self.assertTrue((entrada.path / "4.5" / "scripts" / "x.py").is_file())
        # Y en el destino no queda ni la carpeta final ni el temporal.
        self.assertFalse((self.destino / "blender-5.2.1").exists())
        self.assertEqual(list(self.destino.glob("*" + organizer.MOVING_SUFFIX)),
                         [])

    def test_cancelar_deja_el_origen_intacto(self):
        entrada = _instalar(self.origen, "blender-5.2.1")
        with self.assertRaises(organizer.OrganizerError) as caught:
            organizer.move_build(self._move(entrada),
                                 should_cancel=lambda: True)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertTrue((entrada.path / "blender").is_file())
        self.assertFalse((self.destino / "blender-5.2.1").exists())


class TamanoTest(unittest.TestCase):
    def test_folder_size_suma_el_arbol(self):
        with tempfile.TemporaryDirectory() as tmp:
            entrada = _instalar(Path(tmp), "blender-5.2.1", contenido=b"x" * 50)
            self.assertGreaterEqual(organizer.folder_size(entrada.path), 50)

    def test_folder_size_de_algo_que_no_existe_es_cero(self):
        self.assertEqual(organizer.folder_size("/no/existe/nada"), 0)


if __name__ == "__main__":
    unittest.main()
