"""Tests de la interfaz PySide6 (sin pantalla).

Se ejecutan con el plugin *offscreen* de Qt, así que no hace falta servidor
gráfico. Cubren lo que puede romperse sin abrir ventana: el filtrado del
controlador, el cálculo de columnas de la rejilla y los diálogos.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# El plugin offscreen tiene que estar fijado ANTES de crear QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Solo la ausencia de PySide6 salta los tests de UI. Antes el ``try`` abarcaba
# también los imports de ``ui.widgets``, así que cualquier ImportError de la
# propia app (un nombre mal escrito en un widget) saltaba los 212 tests en
# silencio y el CI salía en verde.
try:
    import PySide6  # noqa: F401
    HAVE_QT = True
except ImportError:  # sin PySide6 (p. ej. el job de tests sin deps)
    HAVE_QT = False

if HAVE_QT:
    from PySide6.QtWidgets import QApplication

    # Las partes de la ventana principal (mixins): los tests parchean en el
    # módulo donde **se usa** el nombre, no donde se define, que es lo que
    # exige ``mock.patch``. Se importan aquí y no dentro de cada test para no
    # repetir la línea cincuenta veces.
    from ui.widgets import downloads, folder_library, main_window
    from ui.widgets import settings_view, updates

from model.build import Build


def _wait_until(condition, timeout: float = 3.0) -> None:
    """Procesa eventos hasta que ``condition()`` se cumpla (o se agote el plazo).

    Para lo que pasa en un hilo y vuelve por señal: sin bombear el bucle de
    eventos, la señal encolada no llega nunca al hilo de la interfaz.
    """
    import time

    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def _build(version, branch, risk, lts=False):
    return Build(version=version, branch=branch, risk=risk, platform="linux",
                 arch="x86_64", url="http://x", filename=f"{version}.tar.xz",
                 size=1000, mtime=1)


class SettingsIsolated:
    """Mixin: config y carpeta de descargas en un directorio temporal.

    ``MainWindow`` guarda en disco (zoom, modo de vista...), asi que sin aislar
    la config la suite escribia en el ``settings.json`` real: ya paso, el zoom se
    quedo en 1.4. Y la carpeta de descargas apunta a un temporal vacio porque si
    no los tests leen las versiones de Blender del usuario (y un test de descarga
    que se creia "ya instalada" llego a lanzarle dos Blenders de verdad).
    """

    def setUp(self):
        import json
        import tempfile
        from unittest import mock

        from services import settings as settings_service

        self._config_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._config_dir.cleanup)
        config = Path(self._config_dir.name)
        destino = config / "Blenders"
        destino.mkdir()
        # ``folders_hint_shown`` va a True a propósito: este settings.json es
        # del esquema viejo, así que al cargarlo se migra y la ventana querría
        # enseñar el aviso de bienvenida a la biblioteca de carpetas. Es un
        # modal, y un modal en el arranque cuelga la suite entera (justo lo que
        # avisa el comentario de la red, más abajo).
        (config / "settings.json").write_text(
            json.dumps({"dest_folder": str(destino),
                        "folders_hint_shown": True}), encoding="utf-8")
        patch = mock.patch.object(settings_service, "config_dir",
                                  return_value=config)
        patch.start()
        self.addCleanup(patch.stop)

        # La carga del listado va a la red en un hilo. En los tests no debe
        # depender de ella: además, un listado real podía programar el diálogo
        # modal de "serie nueva" a mitad de un ``QTest.qWait`` de OTRO test (el
        # temporizador vive en la ventana vieja), y la suite se quedaba colgada
        # esperando a que alguien pulsara un botón. Cada test inyecta sus builds.
        from ui.widgets import main_window

        network = mock.patch.object(downloads.api, "get_builds", return_value=[])
        network.start()
        self.addCleanup(network.stop)

    def tearDown(self):
        """Borra de verdad lo que los tests dejan pendiente de borrar.

        Las tarjetas se sueltan con ``setParent(None)`` + ``deleteLater()``, y
        sin un bucle de eventos nunca llegan a borrarse: se acumulaban por
        miles como ventanas de nivel superior. Al terminar la suite, Qt y
        Python las destruían en cualquier orden y el proceso reventaba a veces
        con una violación de segmento (solo al salir, con todo en verde).
        """
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            for widget in app.topLevelWidgets():
                widget.close()
                widget.deleteLater()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class MainWindowTests(SettingsIsolated, unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def _window(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        # Inyectamos builds a mano: así el test no depende de la red.
        window.builds = [
            _build("5.2.1", "v52", "stable", lts=True),
            _build("5.1.2", "v51", "stable"),
            _build("5.3.0", "main", "alpha"),
        ]
        return window

    def test_filter_all(self):
        window = self._window()
        window.channel = "all"
        self.assertEqual(len(window._filtered()), 3)

    def test_filter_lts(self):
        window = self._window()
        window.channel = "lts"
        result = window._filtered()
        self.assertEqual([b.version for b in result], ["5.2.1"])

    def test_filter_stable_excluye_lts(self):
        window = self._window()
        window.channel = "stable"
        result = window._filtered()
        self.assertEqual([b.version for b in result], ["5.1.2"])

    def test_filter_daily(self):
        window = self._window()
        window.channel = "daily"
        result = window._filtered()
        self.assertEqual([b.version for b in result], ["5.3.0"])

    def test_installed_filtra_por_canal(self):
        """La pestaña de instaladas tambien filtra por canal, no solo busqueda.

        Bug real del port: reimplemente filter_installed y solo miraba la
        busqueda, asi que pulsar LTS/Stable/Daily no cambiaba nada en esa
        pestana (que es la que se abre por defecto si hay versiones).
        """
        class _Entry:
            def __init__(self, name, version, branch, lts, fork=""):
                self.name = name
                self.version = version
                self.branch = branch
                self.path = "/tmp/" + name
                self.is_lts = lts
                self.can_launch = True
                self.fork = fork
                self.favorite_key = f"{branch}|{version}"

        window = self._window()
        window.installed = [
            _Entry("blender-5.2.1", "5.2.1", "v52", lts=True),
            _Entry("blender-5.1.2", "5.1.2", "v51", lts=False),
            _Entry("blender-5.3.0-alpha", "5.3.0", "main", lts=False),
        ]
        window.channel = "lts"
        self.assertEqual([e.name for e in window._filtered_installed()],
                         ["blender-5.2.1"])
        # "Estable" es estable de verdad: la diaria de 'main' se queda fuera.
        # Antes aquí significaba solo "no LTS", así que una alfa aparecía entre
        # las estables; la tienda y las instaladas decían cosas distintas sobre
        # la misma compilación (ver services/channels.py).
        window.channel = "stable"
        self.assertEqual([e.name for e in window._filtered_installed()],
                         ["blender-5.1.2"])
        window.channel = "daily"
        self.assertEqual([e.name for e in window._filtered_installed()],
                         ["blender-5.3.0-alpha"])
        window.channel = "all"
        self.assertEqual(len(window._filtered_installed()), 3)

    def test_search_filtra_por_version(self):
        window = self._window()
        window.channel = "all"
        window.search = "5.2"
        self.assertEqual([b.version for b in window._filtered()], ["5.2.1"])

    def test_el_selector_de_idioma_guarda_codigo_no_texto(self):
        """Elegir idioma tenía que guardar su código, no buscar por la etiqueta.

        Las etiquetas se traducen y cambian según el idioma activo, así que
        comparar por el texto dejaba el ajuste en "auto" en cuanto el idioma no
        era inglés. El identificador tiene que viajar como dato del combo.
        """
        import i18n
        from ui.widgets.main_window import MainWindow
        from ui.widgets.shell import LANGUAGE_IDS

        window = MainWindow()
        self.assertEqual(window.language_combo.currentData(),
                         window.settings.language)
        for lang_id in i18n.SUPPORTED:
            index = window.language_combo.findData(lang_id)
            self.assertGreaterEqual(index, 0, lang_id)
            window.language_combo.setCurrentIndex(index)
            self.assertEqual(window.settings.language, lang_id)
        self.assertEqual(set(LANGUAGE_IDS),
                         {"auto", *i18n.SUPPORTED})

    def test_la_arquitectura_del_filtro_es_la_normalizada(self):
        # La API llama "amd64" a la arquitectura de Windows, pero las builds se
        # normalizan a "x86_64" (api.normalize_arch). Si el filtro pidiera
        # "amd64", la tienda de Windows salía vacía.
        window = self._window()
        window.platform_label = "GNU/Linux"
        window.arch_label = "x86_64"
        self.assertEqual(window.arch, "x86_64")
        window.platform_label = "Windows"
        self.assertEqual(window.arch, "x86_64")
        window.arch_label = "arm64"
        self.assertEqual(window.arch, "arm64")

    def test_la_tienda_de_windows_no_sale_vacia(self):
        # Regresión: el filtro pedía "amd64" pero las builds ya venían
        # normalizadas a "x86_64" (api.normalize_arch), así que no coincidía
        # ninguna y la tienda decía "No se encontraron compilaciones".
        from model.build import Build
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.platform_label = "Windows"
        window.arch_label = "x86_64"
        window.builds = [Build(
            version="5.2.1", branch="v52", risk="stable", platform="windows",
            arch="x86_64", url="u", filename="blender-5.2.1-windows-x64.zip")]
        window.channel = "all"
        self.assertEqual([b.version for b in window._filtered()], ["5.2.1"])

    def test_el_icono_de_info_usa_la_url_de_la_api(self):
        """El port la había sustituido por una URL a mano que no existe.

        ``open_release_notes`` tiene que delegar en ``api.release_notes_url``,
        que recorta a la serie ("5.2.1" -> .../release_notes/5.2/). Antes se
        componía "blender.org/download/releases/5.2.1/", que es 404.
        """
        from unittest import mock

        from PySide6.QtTest import QTest

        from services import api
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        abiertas = []
        with mock.patch.object(downloads.opener, "open_url",
                               side_effect=lambda url: abiertas.append(url) or True):
            window.open_release_notes("5.2.1")
            QTest.qWait(200)

        self.assertEqual(abiertas, [api.release_notes_url("5.2.1")])
        self.assertTrue(abiertas[0].endswith("/release_notes/5.2/"))

    def test_si_el_navegador_no_abre_lo_dice(self):
        from unittest import mock

        from i18n import tr
        from PySide6.QtTest import QTest

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        # Se deja pasar el refresco de arranque (a los 100 ms pone "Cargando...")
        # para que no pise el mensaje que estamos comprobando.
        with mock.patch.object(downloads.api, "get_builds", return_value=[]):
            window = MainWindow()
            QTest.qWait(300)
            with mock.patch.object(downloads.opener, "open_url",
                                   return_value=False):
                window.open_release_notes("5.2.1")
                QTest.qWait(200)

        self.assertEqual(window.status_label.text(),
                         tr("Could not open the browser"))


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class ForkUiTests(SettingsIsolated, unittest.TestCase):
    """Las pestañas, casillas y tarjetas de Bforartists y UPBGE."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def setUp(self):
        super().setUp()
        import i18n

        i18n.set_language("en")
        self.addCleanup(i18n.set_language, "en")

    def _window(self):
        from ui.widgets.main_window import MainWindow

        with mock.patch("ui.widgets.downloads.api.get_builds", return_value=[]):
            window = MainWindow()
            # La carga inicial va por temporizador + hilo: se espera a que
            # termine de verdad, no un tiempo fijo (con 150 ms a secas, a veces
            # aún no había llegado y la barra seguía visible).
            _wait_until(lambda: window._refresh_request > 0
                        and window.busy_bar.isHidden())
        return window

    def _fork_builds(self):
        from model.build import Build

        return [
            Build("5.2.0", "bforartists", "stable", "linux", "x86_64", "u",
                  "Bforartists-5.2.0-Linux.tar.xz", fork="bforartists"),
            Build("0.53", "upbge-weekly", "alpha", "linux", "x86_64", "u",
                  "upbge-0.53-alpha-linux-x86_64.tar.gz", experimental=True,
                  fork="upbge"),
        ]

    def test_las_pestanas_de_fork_nacen_ocultas(self):
        from ui.widgets.shell import CHANNELS

        window = self._window()
        for index, (key, _) in enumerate(CHANNELS):
            if key in ("bforartists", "upbge"):
                self.assertFalse(window.channel_tabs.isTabVisible(index), key)

    def test_encender_un_fork_ensena_su_pestana(self):
        from ui.widgets.shell import CHANNELS

        window = self._window()
        window.bforartists_switch.setChecked(True)
        index = next(i for i, (key, _) in enumerate(CHANNELS)
                     if key == "bforartists")
        self.assertTrue(window.channel_tabs.isTabVisible(index))

    def test_los_filtros_separan_los_forks(self):
        window = self._window()
        window.builds = self._fork_builds()
        window.channel = "bforartists"
        self.assertEqual([b.fork for b in window._filtered()], ["bforartists"])
        # "Todas" los mezcla con Blender: es el catálogo entero.
        window.channel = "all"
        self.assertEqual({b.fork for b in window._filtered()},
                         {"bforartists", "upbge"})
        window.channel = "upbge"
        self.assertEqual([b.fork for b in window._filtered()], ["upbge"])

    def test_el_tope_de_versiones_recorta_la_nube(self):
        """Con el tope puesto solo salen las últimas versiones de cada fork."""
        from model.build import Build

        window = self._window()
        bids = []
        for minor in range(1, 13):
            bids.append(Build(f"5.{minor}.0", "bforartists", "stable", "linux",
                              "x86_64", "u", f"Bforartists-5.{minor}.0.tar.xz",
                              mtime=minor, fork="bforartists"))
        window.builds = bids
        window.channel = "bforartists"
        window.settings.fork_version_limit = 10
        versiones = [b.version for b in window._filtered()]
        self.assertEqual(len(versiones), 10)
        # Son las 10 más nuevas, no las 10 primeras.
        self.assertIn("5.12.0", versiones)
        self.assertNotIn("5.1.0", versiones)
        # Sin tope salen todas.
        window.settings.fork_version_limit = 0
        self.assertEqual(len(window._filtered()), 12)

    def test_el_tope_de_versiones_se_guarda_al_elegirlo(self):
        from services.settings import Settings

        window = self._window()
        index = window.fork_limit_combo.findData(25)
        window.fork_limit_combo.setCurrentIndex(index)
        self.assertEqual(window.settings.fork_version_limit, 25)
        self.assertEqual(Settings.load().fork_version_limit, 25)

    def test_apagar_el_fork_saca_su_canal_puesto(self):
        window = self._window()
        window.bforartists_switch.setChecked(True)
        window.channel = "bforartists"
        window.bforartists_switch.setChecked(False)
        self.assertEqual(window.channel, "all")

    def test_cambiar_un_fork_refresca_la_nube(self):
        """Encender/apagar un fork tiene que traer o quitar sus builds.

        Si el listado en memoria se cargó con otros forks, al entrar en la nube
        hay que volver a pedirlo: si no, el interruptor no tenía efecto visible
        hasta reiniciar. Y si el cambio se hace **desde Ajustes**, no se
        descarga ahí mismo (no se vería): ya se pedirá al volver a la nube.
        """
        window = self._window()
        window.builds = self._fork_builds()
        window._builds_forks = []            # cargado sin ningún fork
        llamadas = []
        original = window.refresh
        window.refresh = lambda force=False: (llamadas.append(force),
                                              original(force=force))[1]

        # Desde Ajustes: no se refresca todavía.
        window.set_view("settings")
        window.bforartists_switch.setChecked(True)
        self.assertEqual(llamadas, [])
        # Al volver a la nube, sí (el listado ya no vale).
        window.set_view("store")
        self.assertEqual(llamadas, [True])

        # Estando ya en la nube, el cambio refresca al instante.
        llamadas.clear()
        window.upbge_switch.setChecked(True)
        self.assertEqual(llamadas, [True])

    def test_la_barra_ocupada_aparece_mientras_carga(self):
        """Pedir el listado no tiene porcentaje: la barra del pie dice "en ello".

        Se enseña al empezar el refresco y se esconde cuando llega el listado
        (por señal, desde el hilo). Así, con una conexión lenta, se ve que la
        app está trabajando y no colgada.
        """
        window = self._window()
        self.assertTrue(window.busy_bar.isHidden())

        with mock.patch("ui.widgets.downloads.api.get_builds", return_value=[]):
            window.refresh()
        # ``isVisible`` sería False con la ventana sin enseñar (el padre
        # esconde a los hijos): lo que importa es que no esté oculta a
        # propósito.
        self.assertFalse(window.busy_bar.isHidden())

        # Cuando llega el listado, se esconde.
        window._on_builds_loaded([])
        self.assertTrue(window.busy_bar.isHidden())

    def test_una_respuesta_vieja_del_listado_no_pisa_la_nueva(self):
        """Dos cargas en vuelo: solo cuenta la última que se pidió.

        Pasaba al encender un fork con la carga inicial aún en marcha: si la
        primera respuesta (sin el fork) llegaba la última, la pestaña del fork
        salía vacía hasta refrescar a mano.
        """
        window = self._window()
        nueva = self._fork_builds()
        # Sin hilos de verdad: las respuestas se entregan a mano, en el orden
        # malo (primero la nueva, después la vieja).
        with mock.patch("ui.widgets.build_lists.threading.Thread"):
            window.refresh()
            vieja = window._refresh_request
            window.refresh()
        window._on_refresh_result(window._refresh_request, ["bforartists"],
                                  nueva)
        window._on_refresh_result(vieja, [], [])
        self.assertEqual(window.builds, nueva)
        self.assertEqual(window._builds_forks, ["bforartists"])

    def test_las_casillas_de_fork_solo_salen_con_el_fork_activo(self):
        from services import channels

        window = self._window()
        window.settings.folders[0].types = list(channels.BUILD_TYPES)
        window._rebuild_folder_rows()
        row = next(iter(window.folder_rows.values()))
        self.assertNotIn(channels.TYPE_BFORARTISTS, row.checks)

        window.bforartists_switch.setChecked(True)
        row = next(iter(window.folder_rows.values()))
        self.assertIn(channels.TYPE_BFORARTISTS, row.checks)
        self.assertNotIn(channels.TYPE_UPBGE, row.checks)

    def test_la_tarjeta_de_un_fork_lleva_su_logo_y_su_nombre(self):
        from ui.widgets.cards import GridBuildCard

        bfa = self._fork_builds()[0]
        card = GridBuildCard(bfa, False, False, 1.0, marked=False)
        self.assertEqual(card.title_text, "Bforartists 5.2.0")
        self.assertEqual(card.channel_text, "Bforartists")

    def test_las_notas_de_un_fork_usan_su_url(self):
        from ui.widgets import downloads
        from ui.widgets.cards import _info_button

        class _Sig:
            def __init__(self):
                self.items = []

            def emit(self, value):
                self.items.append(value)

        signal = _Sig()
        bfa = self._fork_builds()[0]
        button = _info_button(bfa, signal)
        button.click()
        self.assertEqual(signal.items, [bfa])


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class LayoutTests(SettingsIsolated, unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # El aspecto (QSS) es parte de lo que se mide aquí: el ancho del botón
        # con solo icono depende del padding que le quita el stylesheet. Antes
        # esto "funcionaba" solo porque otro test lo había aplicado de rebote;
        # al correr la clase sola, el botón medía 80 px y el test fallaba.
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def test_zoom_limits(self):
        from ui.widgets.main_window import MAX_ZOOM, MIN_ZOOM, MainWindow

        window = MainWindow()
        window.set_zoom(99)
        self.assertEqual(window.zoom, MAX_ZOOM)
        window.set_zoom(0.01)
        self.assertEqual(window.zoom, MIN_ZOOM)

    def test_zoom_por_defecto_es_50(self):
        from services.settings import Settings

        self.assertEqual(Settings().zoom, 0.5)
        # El destino del reset arranca en el mismo valor de fabrica.
        self.assertEqual(Settings().reset_zoom, 0.5)

    def test_el_zoom_minimo_no_recorta_ni_solapa_el_contenido(self):
        """Al bajar el zoom el logo y el alto encogen, pero el contenido tiene
        que seguir cabiendo: ni el layout se aprieta por debajo de su mínimo
        (textos solapándose) ni una tarjeta pide más ancho que su columna
        (textos o iconos saliéndose)."""
        from pathlib import Path

        from model.build import InstalledBuild
        from ui.widgets.cards import GridBuildCard, GridInstalledCard
        from ui.widgets.main_window import MAX_ZOOM, MIN_ZOOM, MainWindow

        ancho_minimo = MainWindow.MIN_CARD_WIDTH
        build = _build("5.2.1", "v52", "stable")
        entry = InstalledBuild(name="blender-5.2.1",
                               path=Path("/tmp/blender-5.2.1"),
                               version="5.2.1", branch="v52")

        for zoom in (MIN_ZOOM, 0.5, 0.6, 0.8, 1.0, 1.4, MAX_ZOOM):
            ancho_columna = max(int(300 * zoom), ancho_minimo)
            for card in (GridBuildCard(build, False, False, zoom),
                         GridInstalledCard(entry, False, zoom)):
                self.assertLessEqual(card.layout().minimumSize().height(),
                                     card.height(), zoom)
                self.assertLessEqual(card.minimumSizeHint().width(),
                                     ancho_columna, zoom)

    def test_ctrl_0_va_al_zoom_de_restablecimiento(self):
        """Ctrl+0 vuelve al zoom elegido en los ajustes, no siempre a 80 %.

        El slider de ajustes solo decide el destino: moverlo NO cambia la
        rejilla (el zoom actual sigue siendo cosa del slider del pie).
        """
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "grid"
        window._set_zoom_value(1.0)

        # Mover el ajuste no toca el zoom actual, pero si el destino.
        window.reset_zoom_slider.setValue(120)
        self.assertAlmostEqual(window.zoom, 1.0)
        self.assertAlmostEqual(window.settings.reset_zoom, 1.2)

        window.reset_zoom()
        self.assertAlmostEqual(window.zoom, 1.2)

        # Ctrl+clic en el slider del ajuste vuelve al valor de fabrica.
        from services.settings import DEFAULT_ZOOM

        window.reset_zoom_slider.setValue(150)
        window._factory_reset_zoom()
        self.assertEqual(window.settings.reset_zoom, DEFAULT_ZOOM)

    def test_zoom_en_vivo_con_tope(self):
        from PySide6.QtTest import QTest

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.builds = [_build("5.1.2", "v51", "stable")]
        window.resize(900, 600)
        window.layout_mode = "grid"
        window.zoom = 0.8
        window._rebuild_store()
        card = lambda: window.store_grid.itemAt(0).widget()
        before = card().minimumHeight()

        window.set_zoom(1.4)
        # El valor va al instante, pero la rejilla no se toca en el mismo tick
        # (eso era el parpadeo), aunque tampoco espera a que sueltes: el timer
        # de refresco queda armado.
        self.assertEqual(window.zoom, 1.4)
        self.assertTrue(window._zoom_tick.isActive())
        self.assertEqual(card().minimumHeight(), before)

        # Mientras se arrastra, se refresca sola (esto es lo que faltaba).
        QTest.qWait(200)
        self.assertNotEqual(card().minimumHeight(), before)

        # Y al parar el slider, para y guarda una sola vez.
        QTest.qWait(300)
        self.assertFalse(window._zoom_tick.isActive())
        self.assertFalse(window._zoom_settle.isActive())
        self.assertEqual(window.settings.zoom, 1.4)

    def test_guarda_el_tamano_de_la_ventana(self):
        """Al cerrar se guarda el tamaño, y la próxima apertura lo reutiliza.

        ``main.py`` ya leía ``window_width``/``window_height``, pero nadie los
        escribía: la ventana arrancaba siempre con el tamaño por defecto.
        """
        from services.settings import Settings
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.show()
        window.resize(1234, 777)
        self.app.processEvents()
        window.close()

        saved = Settings.load()
        self.assertEqual((saved.window_width, saved.window_height),
                         (1234, 777))

    def test_guarda_la_posicion_de_la_ventana(self):
        """Al cerrar se guarda también dónde estaba, para reabrir en el mismo
        monitor (con dos monitores y autoarranque, antes siempre se centraba)."""
        from services.settings import Settings
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.show()
        window.move(180, 120)
        self.app.processEvents()
        window.close()

        saved = Settings.load()
        self.assertTrue(saved.window_pos_saved)
        # El gestor de ventanas puede añadir un par de píxeles de marco (en
        # offscreen son 2), así que se compara con holgura.
        self.assertLessEqual(abs(saved.window_x - 180), 2)
        self.assertLessEqual(abs(saved.window_y - 120), 2)

    def test_restaura_la_posicion_guardada(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.show()
        destino = QApplication.primaryScreen().availableGeometry().topLeft() \
            + QPoint(160, 90)
        window.settings.window_pos_saved = True
        window.settings.window_x = destino.x()
        window.settings.window_y = destino.y()

        window.restore_window_geometry()
        arriba_izquierda = window.frameGeometry().topLeft()
        self.assertLessEqual(abs(arriba_izquierda.x() - destino.x()), 2)
        self.assertLessEqual(abs(arriba_izquierda.y() - destino.y()), 2)

    def test_una_posicion_fuera_de_toda_pantalla_se_centra(self):
        """Si el monitor donde estaba ya no existe, no se abre fuera de la
        pantalla: se centra en la principal."""
        from PySide6.QtWidgets import QApplication

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.show()
        window.settings.window_pos_saved = True
        window.settings.window_x = 999999
        window.settings.window_y = 999999

        window.restore_window_geometry()
        screen = window.screen() or QApplication.primaryScreen()
        centro = screen.availableGeometry().center()
        marco = window.frameGeometry().center()
        self.assertLessEqual(abs(marco.x() - centro.x()), 2)
        self.assertLessEqual(abs(marco.y() - centro.y()), 2)

    def test_atajos_registrados(self):
        from PySide6.QtGui import QShortcut

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        keys = {s.key().toString() for s in window.findChildren(QShortcut)}
        self.assertIn("Ctrl++", keys)
        self.assertIn("Ctrl+=", keys)
        self.assertIn("Ctrl+-", keys)
        self.assertIn("Ctrl+0", keys)

    def test_atajos_de_zoom(self):
        from services.settings import DEFAULT_ZOOM
        from ui.widgets.main_window import MAX_ZOOM, MIN_ZOOM, ZOOM_STEP, MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "grid"
        # Un zoom intermedio: así se puede subir y bajar sin tocar los topes.
        window._set_zoom_value(1.0)

        window.zoom_in()
        self.assertAlmostEqual(window.zoom, 1.0 + ZOOM_STEP)
        # El slider va con el valor: si no, la UI mentiría.
        self.assertEqual(window.zoom_slider.value(),
                         round((1.0 + ZOOM_STEP) * 100))

        window.zoom_out()
        window.zoom_out()
        self.assertAlmostEqual(window.zoom, 1.0 - ZOOM_STEP)

        for _ in range(50):
            window.zoom_in()
        self.assertAlmostEqual(window.zoom, MAX_ZOOM)
        for _ in range(50):
            window.zoom_out()
        self.assertAlmostEqual(window.zoom, MIN_ZOOM)

        window.reset_zoom()
        self.assertAlmostEqual(window.zoom, DEFAULT_ZOOM)
        self.assertEqual(window.zoom_slider.value(), round(DEFAULT_ZOOM * 100))

    def test_zoom_no_aplica_en_modo_lista(self):
        from services.settings import DEFAULT_ZOOM
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.layout_mode = "list"
        window._set_zoom_value(DEFAULT_ZOOM)
        window.zoom_in()
        self.assertAlmostEqual(window.zoom, DEFAULT_ZOOM)

    def test_ctrl_clic_en_el_slider_resetea(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        from services.settings import DEFAULT_ZOOM
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "grid"
        window._set_zoom_value(1.4)
        pos = QPointF(10, 5)
        event = QMouseEvent(QEvent.MouseButtonPress, pos, pos,
                            Qt.LeftButton, Qt.LeftButton, Qt.ControlModifier)
        window.zoom_slider.mousePressEvent(event)
        self.assertAlmostEqual(window.zoom, DEFAULT_ZOOM)

    def test_clic_en_la_ranura_lleva_el_tirador_al_punto(self):
        """Un clic en la ranura mueve el tirador ahí, no un ``pageStep``.

        Antes, pulsar a 12 px del tirador daba un salto del 10 % de zoom.
        """
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QStyle

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "grid"
        slider = window.zoom_slider

        def mouse(kind, x, button=Qt.LeftButton, buttons=Qt.LeftButton):
            pos = QPointF(x, 12)
            return QMouseEvent(kind, pos, pos, button, buttons, Qt.NoModifier)

        slider.setValue(120)
        center = slider._sub_rect(QStyle.SC_SliderHandle).center().x()
        for offset in (-30, 20, 40):
            x = center + offset
            slider.mousePressEvent(mouse(QEvent.MouseButtonPress, x))
            slider.mouseReleaseEvent(
                mouse(QEvent.MouseButtonRelease, x, Qt.NoButton, Qt.NoButton))
            landed = slider._sub_rect(QStyle.SC_SliderHandle).center().x()
            self.assertLessEqual(abs(landed - x), 2,
                                 f"clic en {x} dejó el tirador en {landed}")

        # Y tras el salto se sigue pudiendo arrastrar.
        slider.setValue(120)
        center = slider._sub_rect(QStyle.SC_SliderHandle).center().x()
        slider.mousePressEvent(mouse(QEvent.MouseButtonPress, center))
        slider.mouseMoveEvent(mouse(QEvent.MouseMove, center + 30, Qt.NoButton))
        self.assertGreater(slider.value(), 120)
        slider.mouseReleaseEvent(
            mouse(QEvent.MouseButtonRelease, center + 30, Qt.NoButton, Qt.NoButton))

    def test_zebra_solo_en_modo_lista(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.builds = [_build("5.2.1", "v52", "stable"),
                         _build("5.1.2", "v51", "stable")]
        window.channel = "all"
        window.resize(900, 600)

        def zebras():
            return [window.store_grid.itemAt(i).widget().property("zebra")
                    for i in range(window.store_grid.count())]

        window.layout_mode = "grid"
        window._rebuild_store()
        self.assertEqual(zebras(), ["false", "false"])

        window.layout_mode = "list"
        window._rebuild_store()
        self.assertEqual(zebras(), ["false", "true"])

    def test_las_tarjetas_de_la_tienda_miden_igual(self):
        """Instaladas y descargables del mismo canal no pueden bailar de alto.

        La tarjeta instalada llevaba una insignia "Instalada" que las
        descargables no tenían, así que en la rejilla estas salían 22 px más
        bajas y las filas quedaban desniveladas. En el Kivy original esa fila
        siempre existía (vacía si no estaba instalada).
        """
        from pathlib import Path

        from model.build import InstalledBuild
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.builds = [_build("5.2.1", "v52", "stable"),
                         _build("5.1.2", "v51", "stable")]
        window.installed = [InstalledBuild(
            name="blender-5.2.1", path=Path("/tmp/blender-5.2.1"),
            version="5.2.1", branch="v52")]
        window.channel = "all"
        window.resize(900, 600)
        window.layout_mode = "grid"
        window.zoom = 0.8
        window._rebuild_store()

        alturas = {window.store_grid.itemAt(i).widget().height()
                   for i in range(window.store_grid.count())}
        self.assertEqual(len(alturas), 1, alturas)

    def test_la_insignia_de_solo_lectura_no_cambia_el_alto(self):
        """Va en la línea que ya existe, no en una fila nueva.

        Si creciera, al cambiar de pestaña las tarjetas bailarían de tamaño
        (que es lo que vigila el test de al lado).
        """
        from pathlib import Path

        from i18n import tr
        from model.build import InstalledBuild
        from ui.widgets.cards import InstalledCard
        from ui.widgets.labels import ElidedLabel

        entry = InstalledBuild(name="blender-5.2.1", path=Path("/tmp/b"),
                               version="5.2.1", branch="v52")
        normal = InstalledCard(entry, False)
        bloqueada = InstalledCard(entry, False, read_only=True)
        self.assertEqual(normal.height(), bloqueada.height())
        # Y se nota que está bloqueada.
        textos = [w.text() for w in bloqueada.findChildren(ElidedLabel)]
        self.assertTrue(any(tr("Read-only") in t for t in textos), textos)

    def test_las_instaladas_miden_como_las_de_la_tienda(self):
        """Al cambiar de pestaña las tarjetas no pueden bailar de tamaño.

        Las de la tienda (rejilla) salían más altas y con el logo más grande
        porque reservan la fila de la insignia; ahora las instaladas usan la
        misma estructura y medidas, y en lista también coinciden.
        """
        from PySide6.QtWidgets import QLabel

        from model.build import InstalledBuild
        from ui.widgets.cards import (BuildCard, GridBuildCard,
                                      GridInstalledCard, InstalledCard)

        build = _build("5.2.1", "v52", "stable")
        entry = InstalledBuild(name="blender-5.2.1",
                               path=Path("/tmp/blender-5.2.1"),
                               version="5.2.1", branch="v52")

        def logo_size(card):
            for label in card.findChildren(QLabel):
                if not label.pixmap().isNull():
                    return label.pixmap().size()
            return None

        for zoom in (0.6, 0.8, 1.4):
            tienda = GridBuildCard(build, False, False, zoom)
            local = GridInstalledCard(entry, False, zoom)
            self.assertEqual(tienda.height(), local.height(), zoom)
            self.assertEqual(logo_size(tienda), logo_size(local), zoom)

        tienda = BuildCard(build, False, False)
        local = InstalledCard(entry, False)
        self.assertEqual(tienda.height(), local.height())
        self.assertEqual(logo_size(tienda), logo_size(local))

    def test_todas_las_tarjetas_llevan_sombra(self):
        from PySide6.QtWidgets import QGraphicsDropShadowEffect

        from model.build import InstalledBuild
        from ui.widgets.cards import (BuildCard, GridBuildCard,
                                      GridInstalledCard, InstalledCard)

        build = _build("5.2.1", "v52", "stable")
        entry = InstalledBuild(name="blender-5.2.1",
                               path=Path("/tmp/blender-5.2.1"),
                               version="5.2.1", branch="v52")
        tarjetas = [BuildCard(build, False, False),
                    GridBuildCard(build, False, False, 1.0),
                    InstalledCard(entry, False),
                    GridInstalledCard(entry, False, 1.0)]
        for card in tarjetas:
            efecto = card.graphicsEffect()
            self.assertIsInstance(efecto, QGraphicsDropShadowEffect, type(card))
            # Desplazada abajo a la derecha y con poco contraste.
            self.assertGreater(efecto.offset().x(), 0, type(card))
            self.assertGreater(efecto.offset().y(), 0, type(card))
            self.assertLessEqual(efecto.color().alpha(), 130, type(card))

    def test_la_papelera_no_se_queda_sin_icono(self):
        from types import SimpleNamespace

        from ui.widgets.buttons import CardButton
        from ui.widgets.cards import GridInstalledCard
        from ui.widgets.main_window import MIN_ZOOM

        entry = SimpleNamespace(name="blender-5.2.0", version="5.2.0",
                                path="/tmp/blender-5.2.0", fork="",
                                fork_name="Blender")
        card = GridInstalledCard(entry, False, MIN_ZOOM)
        card.show()
        delete = next(b for b in card.findChildren(CardButton)
                      if b.property("iconOnly") == "true")
        # El ancho fijo tiene que dar cabida al glifo (si no, a zoom bajo el
        # boton sale como un recuadro rojo vacio).
        self.assertGreaterEqual(delete.minimumWidth(),
                                delete.sizeHint().width())
        # Y el QSS le quita el padding lateral de 14 px, o no habria forma.
        self.assertLess(delete.sizeHint().width(), 30)

    def test_el_logo_no_instalado_va_atenuado(self):
        from ui.widgets.cards import _logo_label

        claro = _logo_label(40, dim=False).pixmap().toImage()
        tenue = _logo_label(40, dim=True).pixmap().toImage()
        self.assertEqual(claro.size(), tenue.size())
        # El atenuado tiene que pintarse de verdad: en Qt el `opacity` del QSS
        # NO se aplica sobre un QLabel con pixmap, y eso se perdio en el port
        # (en la version Kivy las builds que no tienes se veian mas apagadas).
        self.assertLess(self._alpha_medio(tenue), self._alpha_medio(claro))

    @staticmethod
    def _alpha_medio(image):
        total = count = 0
        for y in range(image.height()):
            for x in range(image.width()):
                alpha = image.pixelColor(x, y).alpha()
                if alpha:
                    total += alpha
                    count += 1
        return total / count if count else 0

    def test_iconos_de_lanzar_y_de_descargar(self):
        from ui.widgets.buttons import CardButton
        from ui.widgets.cards import BuildCard, GridBuildCard

        def action_button(card):
            for button in card.findChildren(CardButton):
                if button.property("variant") in ("dark", "accent"):
                    return button
            return None

        build = _build("5.2.1", "v52", "stable")
        lanzar, descargar = [], []
        for card in (BuildCard(build, True, False),
                     GridBuildCard(build, True, False, 1.0)):
            lanzar.append(action_button(card))
        for card in (BuildCard(build, False, False),
                     GridBuildCard(build, False, False, 1.0)):
            descargar.append(action_button(card))

        # Lanzar va oscuro (como el boton de pestana de Blender) con la flecha
        # verde, en las cuatro tarjetas: en Kivy se pintaba dentro del texto y
        # al portar a Qt se perdio en las dos de la tienda.
        for button in lanzar:
            self.assertEqual(button.property("variant"), "dark")
            self.assertFalse(button.icon().isNull(), type(button))
        # Descargar va azul y con su flecha blanca hacia abajo.
        for button in descargar:
            self.assertEqual(button.property("variant"), "accent")
            self.assertFalse(button.icon().isNull(), type(button))
        # Y no es el mismo icono.
        self.assertNotEqual(lanzar[0].icon().cacheKey(),
                            descargar[0].icon().cacheKey())

    def test_ajustes_funciona_como_interruptor(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        # La segunda pulsacion del boton de ajustes vuelve a donde estabas
        # (en el port se guardaba _previous_view pero no se usaba nunca).
        window.set_view("installed")
        window.set_view("settings")
        self.assertEqual(window.view, "settings")
        window.set_view("settings")
        self.assertEqual(window.view, "installed")

        window.set_view("store")
        window.set_view("settings")
        window.set_view("settings")
        self.assertEqual(window.view, "store")

    def test_las_vistas_nuevas_estan_tras_experimental(self):
        """El gestor de Add-ons no aparece hasta activar Avanzado.

        Recientes y Migración ya no están aquí: quedaron probadas y se ven
        siempre.
        """
        from unittest import mock

        from ui.widgets.main_window import EXPERIMENTAL_VIEWS, MainWindow

        window = MainWindow()
        hidden = EXPERIMENTAL_VIEWS
        for key in ("recent", "migrate"):
            self.assertNotIn(key, hidden)
            self.assertFalse(window.side_buttons[key].isHidden(), key)
        # Apagado (por defecto): los botones no se ven y no se puede entrar.
        for key in hidden:
            self.assertTrue(window.side_buttons[key].isHidden(), key)
            window.set_view(key)
            self.assertNotEqual(window.view, key, key)
        self.assertTrue(window.console_row.isHidden())
        # Al activarlo aparecen y se entra.
        window.experimental_switch.setChecked(True)
        for key in hidden:
            self.assertFalse(window.side_buttons[key].isHidden(), key)
        # Entrar en Add-ons arranca Blender en un hilo para leer su estado;
        # aquí no hay Blender y un hilo vivo al acabar el test revienta la
        # suite en la salida, así que la lectura se anula.
        with mock.patch.object(window.addons_view, "read"):
            window.set_view("addons")
        self.assertEqual(window.view, "addons")
        self.assertFalse(window.console_row.isHidden())
        # Al apagarlo estando dentro de una de ellas, sale.
        window.experimental_switch.setChecked(False)
        self.assertNotEqual(window.view, "addons")
        for key in hidden:
            self.assertTrue(window.side_buttons[key].isHidden(), key)

    def test_menu_contextual_de_las_tarjetas(self):
        """Clic derecho en una tarjeta: acciones de lanzar/abrir/borrar."""
        import i18n

        from ui.widgets.main_window import MainWindow

        self.addCleanup(i18n.set_language, i18n.get_language())

        window = MainWindow()
        # MainWindow fija el idioma de los ajustes al construirse, así que se
        # cambia después.
        i18n.set_language("en")
        window.refresh_installed = lambda: None

        entry = _fake_installed("5.2.2")
        labels = [action.text() for action
                  in window._installed_menu(entry).actions() if action.text()]
        for expected in ("Launch", "Open folder", "Copy path", "Uninstall"):
            self.assertIn(expected, labels)

        build = _build("5.2.1", "v52", "stable")
        labels = [action.text() for action
                  in window._store_menu(build).actions() if action.text()]
        self.assertIn("Download and install", labels)
        self.assertIn("Release notes", labels)

    def test_la_cabecera_ensena_la_seccion(self):
        """Arriba la marca y debajo la sección, para saber dónde estás."""
        import i18n

        from ui.widgets.main_window import MainWindow

        self.addCleanup(i18n.set_language, i18n.get_language())
        window = MainWindow()
        window.refresh_installed = lambda: None
        i18n.set_language("en")
        window.set_view("store")
        self.assertEqual(window.section_label.text(), "Cloud")
        window.set_view("settings")
        self.assertEqual(window.section_label.text(), "Settings")
        # La marca se fija al construir (el idioma se cambia reiniciando).
        self.assertTrue(window.app_label.text())

    def test_la_consola_es_por_version(self):
        """Encender la consola en una tarjeta no toca las demás."""
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.refresh_installed = lambda: None
        one = _fake_installed("5.2.2")
        other = _fake_installed("5.3.0")
        window.set_console_for(one, True)
        self.assertTrue(window._console_state(one))
        self.assertFalse(window._console_state(other))
        self.assertEqual(
            window.settings.launch_console_overrides[one.favorite_key], True)

    def test_reescanea_al_recuperar_el_foco(self):
        from PySide6.QtCore import QEvent

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        calls = []
        window.refresh_installed = lambda: calls.append(1)
        window.isActiveWindow = lambda: False
        window.changeEvent(QEvent(QEvent.ActivationChange))
        self.assertEqual(calls, [])
        window.isActiveWindow = lambda: True
        window.changeEvent(QEvent(QEvent.ActivationChange))
        self.assertEqual(len(calls), 1)
        # El cooldown evita reescanear en cada cambio de ventana.
        window.changeEvent(QEvent(QEvent.ActivationChange))
        self.assertEqual(len(calls), 1)

    def test_ajustes_en_pestanas(self):
        from PySide6.QtWidgets import QTabWidget

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        tabs = window.findChild(QTabWidget, "SettingsTabs")
        self.assertIsNotNone(tabs)
        # Un tema por pestaña, y todos los controles siguen existiendo aunque
        # su pestaña no sea la activa.
        self.assertEqual(tabs.count(), 7)
        for control in (window.dest_input, window.folder_list,
                        window.add_folder_btn,
                        window.archive_switch, window.language_combo,
                        window.reset_zoom_slider, window.args_input,
                        window.close_tray_switch, window.autostart_switch,
                        window.update_switch, window.periodic_switch,
                        window.experimental_switch, window.fork_limit_combo):
            self.assertIsNotNone(control)

    def test_al_arrancar_no_se_asoma_ninguna_ventana_suelta(self):
        """Solo se muestra la ventana principal; ningún widget se cuela solo.

        Un widget sin padre al que se le hace ``setVisible(True)`` antes de
        entrar en su layout se enseña como una ventana de nivel superior: al
        arrancar con las opciones experimentales activadas se veía un
        cuadradito (el botón de la barra lateral) en el centro de la pantalla
        que desaparecía enseguida.
        """
        import json
        from unittest import mock

        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QWidget

        from services import settings as settings_service
        from ui.widgets.main_window import MainWindow

        config = Path(settings_service.config_dir())
        (config / "settings.json").write_text(json.dumps({
            "dest_folder": str(config / "Blenders"),
            "folders_hint_shown": True,
            "experimental_features": True,
        }), encoding="utf-8")

        shown = []

        class Spy(QObject):
            def eventFilter(self, obj, event):
                if (event.type() == QEvent.Show and isinstance(obj, QWidget)
                        and obj.isWindow()):
                    shown.append(type(obj).__name__)
                return False

        spy = Spy()
        self.app.installEventFilter(spy)
        try:
            window = MainWindow()
            window.show()
            self.app.processEvents()
        finally:
            self.app.removeEventFilter(spy)
        self.assertEqual(shown, ["MainWindow"])
        # Y lo experimental sigue respetándose.
        self.assertFalse(window.side_buttons["addons"].isHidden())
        self.assertFalse(window.console_row.isHidden())

    def test_refrescar_vive_con_los_filtros_y_refresca_lo_que_se_ve(self):
        """El botón de refrescar va a la izquierda de rejilla/lista.

        Antes iba en la cabecera junto al buscador y solo refrescaba la nube,
        aunque se viera también en Local.
        """
        from unittest import mock as _mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(1500, 620)
        window.show()
        self.app.processEvents()
        refresh = window.refresh_btn
        # Justo a la izquierda de la pastilla de rejilla, en la misma fila.
        self.assertIs(refresh.parentWidget(), window.grid_btn.parentWidget())
        hueco = window.grid_btn.x() - (refresh.x() + refresh.width())
        self.assertLessEqual(hueco, 14)
        # El buscador sigue pegado al borde derecho de la cabecera.
        self.assertLessEqual(
            window.width() - (window.header_tools.x() + window.header_tools.width()),
            18)
        # Refresca lo que está a la vista, y el tooltip lo dice.
        with _mock.patch.object(window, "refresh") as nube, \
                _mock.patch.object(window, "refresh_installed") as local:
            window.set_view("store")
            tooltip_nube = refresh.toolTip()
            refresh.click()
            nube.assert_called_once_with(force=True)
            local.assert_not_called()
            window.set_view("installed")
            local.reset_mock()
            refresh.click()
            local.assert_called_once()
            self.assertNotEqual(refresh.toolTip(), tooltip_nube)

    def test_estrella_e_info_van_en_ese_orden_en_las_cuatro_tarjetas(self):
        """Estrella → i → acciones, igual en tienda e instaladas.

        Si el orden cambia entre tarjetas, al pasar de Nube a Local los iconos
        bailan de sitio.
        """
        from pathlib import Path

        from model.build import InstalledBuild
        from ui.widgets.buttons import IconLinkButton, StarButton
        from ui.widgets.cards import (BuildCard, GridBuildCard,
                                      GridInstalledCard, InstalledCard)

        build = _build("5.2.1", "v52", "stable")
        entry = InstalledBuild(name="blender-5.2.1", path=Path("/tmp/b"),
                               version="5.2.1", branch="v52")
        cards = (BuildCard(build, False, False), GridBuildCard(build, False, False, 1.0),
                 InstalledCard(entry, False), GridInstalledCard(entry, False, 1.0))
        for card in cards:
            card.show()
            self.app.processEvents()
            star = card.findChild(StarButton)
            info = card.findChild(IconLinkButton)
            self.assertLess(star.x(), info.x(), type(card).__name__)

    def test_la_tarjeta_de_la_nube_en_lista_ensena_la_version(self):
        """El título "Blender x.y.z" no puede quedarse a 0 px junto a la insignia.

        Un ``ElidedLabel`` no pide ancho: en una fila con ``addStretch`` se
        quedaba invisible y solo se veía "LTS"/"Alfa".
        """
        from ui.widgets.cards import BuildCard
        from ui.widgets.labels import ElidedLabel

        card = BuildCard(_build("5.2.1", "v52", "stable"), False, False)
        card.resize(900, 68)
        card.show()
        self.app.processEvents()
        title = next(label for label in card.findChildren(ElidedLabel)
                     if label.text().startswith("Blender "))
        self.assertGreater(title.width(), 40)
        self.assertFalse(title.is_elided())

    def test_la_nube_lanza_con_consola_las_versiones_instaladas(self):
        """Una versión instalada se lanza desde la Nube como desde Local.

        Así que lleva el mismo botón de consola, con la misma clave (la
        serie): encenderlo en una lista se ve en la otra.
        """
        from unittest import mock as _mock

        from model.build import InstalledBuild
        from ui.widgets.buttons import CardButton
        from ui.widgets.main_window import MainWindow
        from ui import icons

        window = MainWindow()
        window.experimental_switch.setChecked(True)
        window.set_layout_mode("list")
        window.installed = [InstalledBuild(
            name="blender-5.2.1", path=Path("/tmp/b"), version="5.2.1",
            branch="v52", executable=Path("/tmp/b/blender"))]
        window.builds = [_build("5.2.1", "v52", "stable"),
                         _build("5.3.0", "v53", "alpha")]
        window._rebuild_store()
        cards = window.store_grid.parentWidget().findChildren(CardButton)
        consolas = [b for b in cards if b.text() == icons.TERMINAL]
        # Solo la instalada lleva consola; la que está por descargar, no.
        self.assertEqual(len(consolas), 1)
        self.assertEqual(consolas[0].property("variant"), "neutral")
        with _mock.patch.object(window, "_rebuild_installed") as local:
            consolas[0].setChecked(True)
            local.assert_called()
        key = window.installed[0].favorite_key
        self.assertTrue(window.settings.launch_console_overrides[key])
        # Tras repintar, la tarjeta de la tienda sale encendida.
        cards = window.store_grid.parentWidget().findChildren(CardButton)
        consola = next(b for b in cards if b.text() == icons.TERMINAL)
        self.assertEqual(consola.property("variant"), "accent")

    def test_la_familia_del_texto_no_pisa_la_fuente_de_iconos(self):
        """El QSS no puede fijar ``font-family``: dejaría a los iconos sin glifo.

        Los iconos se pintan con la fuente de Font Awesome puesta a mano en
        cada widget (``setFont``). Una regla global ``font-family`` en el QSS
        gana a ese ``setFont``, así que todos los glifos salían como cuadraditos
        sin que fallara ningún test. La familia del texto va como fuente de la
        aplicación (``fonts.apply_ui_font``), que sí respeta lo de cada widget.
        """
        from PySide6.QtGui import QFontInfo
        from PySide6.QtWidgets import QLabel

        from ui import fonts, qss
        from ui import icons

        self.assertNotIn("font-family", qss.build_qss())

        fonts.apply_ui_font(self.app)
        label = QLabel(icons.SETTINGS)
        label.setFont(fonts.icon_font(18))
        self.assertIn("Font Awesome", QFontInfo(label.font()).family())

    def test_los_iconos_de_tarjeta_se_realzan_con_su_color(self):
        """Estrella en ámbar, consola en gris oscuro, "i" en azul al pasar."""
        from ui import qss
        from ui import theme as t

        hoja = qss.build_qss()
        self.assertIn(
            f"QPushButton#StarButton:hover {{ color: {t.WARNING}; }}", hoja)
        self.assertIn(
            'QPushButton#CardButton[variant="neutral"][iconOnly="true"]:hover {\n'
            f"        background-color: {t.SURFACE_ALT};", hoja)
        self.assertIn(
            f"QPushButton#IconLink:hover {{ background-color: {t.ACCENT}; }}", hoja)
        # Los tres realces se distinguen del estado normal (>= 1,5:1 es lo que
        # separa dos grises contiguos de la escala; la estrella y la "i" van
        # mucho más allá).
        self.assertGreaterEqual(t.contrast(t.WARNING, t.MUTED), 1.5)
        self.assertGreaterEqual(t.contrast(t.BUTTON, t.SURFACE_ALT), 1.5)
        self.assertGreaterEqual(t.contrast(t.ACCENT, t.INFO_DISC), 1.1)

    def test_las_pestanas_miden_lo_mismo_que_las_pastillas(self):
        """Canales, rejilla/lista y las pestañas de Migración/Ajustes: 28 px."""
        from ui import theme as t
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(1100, 700)
        window.show()
        self.app.processEvents()
        canal = window.channel_tabs.tabRect(0)
        # El rect del tab incluye sus márgenes (8 arriba, 16 abajo).
        self.assertEqual(canal.height() - 24, t.CONTROL_HEIGHT)
        self.assertEqual(window.grid_btn.height(), t.CONTROL_HEIGHT)
        self.assertEqual(window.migrate_view.tabs.tabBar().tabRect(0).height(),
                         t.CONTROL_HEIGHT)
        window.set_view("settings")
        self.app.processEvents()
        self.assertEqual(window.settings_tabs.tabBar().tabRect(0).height(),
                         t.CONTROL_HEIGHT)

    def test_recuerda_el_filtro_al_volver_a_abrir(self):
        from ui.widgets.main_window import CHANNELS, MainWindow

        # Primera sesión: el usuario deja el filtro en Favoritos.
        primera = MainWindow()
        primera.set_channel("favorites")
        self.assertEqual(primera.settings.channel, "favorites")

        # Al volver a abrir, sigue en Favoritos y con su pestaña seleccionada.
        segunda = MainWindow()
        self.assertEqual(segunda.channel, "favorites")
        index = next(i for i, (key, _) in enumerate(CHANNELS)
                     if key == "favorites")
        self.assertEqual(segunda.channel_tabs.currentIndex(), index)

    def test_hay_pestana_de_favoritos(self):
        from ui.widgets.main_window import CHANNELS, MainWindow

        window = MainWindow()
        self.assertIn("favorites", [key for key, _ in CHANNELS])
        self.assertEqual(window.channel_tabs.count(), len(CHANNELS))

    def test_todas_las_pestanas_de_canal_son_alcanzables(self):
        """Con los dos forks activos, las ocho pestañas no pueden quedar cortadas.

        Fue el fallo que hizo pensar que no había builds de fork: a 1000 px de
        ancho la última pestaña se recortaba contra el botón de refrescar
        ("Bforartists" salía como "Bfor...") y parecía no existir. La
        invariante es que **ninguna pestaña quede inalcanzable**: o caben todas,
        o la barra trae botones de scroll para llegar a las que no.

        No se ata a un ancho de fábrica concreto (se ajusta a ojo y cambia):
        solo a que, con las ocho pestañas visibles, la barra tenga la red de
        seguridad del scroll.
        """
        from ui.widgets.main_window import MainWindow
        from ui.widgets.shell import CHANNELS

        window = MainWindow()
        window.settings.enable_bforartists = True
        window.settings.enable_upbge = True
        window._update_fork_tabs()
        window.show()
        self.app.processEvents()

        tabs = window.channel_tabs
        visibles = [i for i in range(tabs.count()) if tabs.isTabVisible(i)]
        self.assertEqual(len(visibles), len(CHANNELS))
        # Con ocho pestañas, la barra tiene que poder desplazarse o caber
        # entera; lo que no vale es recortar la última sin más.
        self.assertTrue(tabs.usesScrollButtons())

    def test_la_estrella_marca_y_desmarca(self):
        from ui.widgets.buttons import StarButton
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "list"
        window.channel = "all"
        window.builds = [_build("5.1.2", "v51", "stable")]
        window._rebuild_store()

        card = window.store_grid.itemAt(0).widget()
        star = next(b for b in card.findChildren(StarButton))
        self.assertFalse(star.isChecked())

        star.setChecked(True)          # como si el usuario pulsara la estrella
        self.assertEqual(window.settings.favorites, ["v51|5.1.2"])
        self.assertTrue(star.isChecked())

        star.setChecked(False)
        self.assertEqual(window.settings.favorites, [])

    def test_la_estrella_sale_marcada_si_ya_era_favorita(self):
        from ui.widgets.buttons import StarButton
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "list"
        window.channel = "all"
        window.builds = [_build("5.1.2", "v51", "stable")]
        window.settings.favorites = ["v51|5.1.2"]
        window._rebuild_store()

        card = window.store_grid.itemAt(0).widget()
        star = next(b for b in card.findChildren(StarButton))
        self.assertTrue(star.isChecked())

    def test_canal_favoritos_filtra_y_se_actualiza(self):
        from ui.widgets.buttons import StarButton
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "list"
        window.builds = [_build("5.1.2", "v51", "stable"),
                         _build("5.2.1", "v52", "stable")]
        window.settings.favorites = ["v52|5.2.1"]
        window.set_channel("favorites")

        self.assertEqual([b.version for b in window._filtered()], ["5.2.1"])
        self.assertEqual(window.store_grid.count(), 1)

        # Quitar la estrella estando en ese canal deja la lista vacia al momento
        # (y sale el mensaje propio, no el generico).
        card = window.store_grid.itemAt(0).widget()
        star = next(b for b in card.findChildren(StarButton))
        star.setChecked(False)
        self.assertEqual(window._filtered(), [])
        self.assertEqual(window.store_grid.count(), 1)   # el placeholder

    def test_columnas_grid_crecen_con_el_ancho(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(900, 600)
        window.layout_mode = "grid"
        narrow = window._columns_for(window.store_scroll, 300)
        window.resize(1600, 600)
        wide = window._columns_for(window.store_scroll, 300)
        self.assertGreaterEqual(wide, narrow)
        self.assertGreaterEqual(narrow, 1)

    def test_el_interruptor_es_un_toggle_pintado(self):
        from ui.widgets.buttons import SwitchPill

        switch = SwitchPill(False)
        self.assertTrue(switch.isCheckable())
        # Ya no muestra "Sí"/"No": el estado se ve por la bolita.
        self.assertEqual(switch.text(), "")
        self.assertEqual((switch.width(), switch.height()),
                         (SwitchPill.WIDTH, SwitchPill.HEIGHT))
        recibidos = []
        switch.toggled.connect(recibidos.append)
        switch.setChecked(True)
        self.assertEqual(recibidos, [True])

    def test_la_ventana_se_centra_en_la_pantalla(self):
        from PySide6.QtWidgets import QApplication

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.resize(1000, 700)
        window.show()
        window.center_on_screen()
        screen = window.screen() or QApplication.primaryScreen()
        center = screen.availableGeometry().center()
        frame = window.frameGeometry().center()
        self.assertLessEqual(abs(frame.x() - center.x()), 2)
        self.assertLessEqual(abs(frame.y() - center.y()), 2)

    def test_restablecer_el_tamano_de_la_ventana(self):
        from ui.widgets.main_window import (
            DEFAULT_WINDOW_HEIGHT,
            DEFAULT_WINDOW_WIDTH,
            MainWindow,
        )

        window = MainWindow()
        window.show()
        window.resize(1500, 900)
        window.settings.window_width = 1500
        window.settings.window_height = 900

        window.reset_window_size()
        self.assertEqual((window.width(), window.height()),
                         (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT))
        # En los ajustes vuelve a 0 para que la próxima apertura use el de
        # fábrica.
        self.assertEqual((window.settings.window_width,
                          window.settings.window_height), (0, 0))

    def test_el_tamano_por_defecto_muestra_tres_filas_al_zoom_de_fabrica(self):
        """Con el zoom de fábrica (50 %) y 3 columnas, 3 filas caben enteras.

        El alto por defecto (670) está medido para eso: con el anterior (680 y
        zoom al 80 %, de cuando la rejilla venía más grande) la tercera fila
        quedaba cortada. Y 670 —y no 650— deja además entrar el canal Favoritos
        entero junto al botón de refrescar.
        """
        from model.build import Build
        from services.settings import DEFAULT_ZOOM
        from ui.widgets.main_window import (
            DEFAULT_WINDOW_HEIGHT,
            DEFAULT_WINDOW_WIDTH,
            MainWindow,
        )

        window = MainWindow()
        window.builds = [
            Build(version=f"5.{i}.0", branch=f"v5{i}", risk="stable",
                  platform="linux", arch="x86_64", url="u",
                  filename=f"b{i}.tar.xz", size=1, mtime=i)
            for i in range(9)
        ]
        window.channel = "all"
        window.layout_mode = "grid"
        window.zoom = DEFAULT_ZOOM
        window.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        window.show()
        for _ in range(5):
            self.app.processEvents()
        window._rebuild_store()
        for _ in range(5):
            self.app.processEvents()
        # Sin barra de scroll: las 3 filas se ven enteras.
        self.assertEqual(window.store_scroll.verticalScrollBar().maximum(), 0)

    def _folder(self, path, types=(), writable=True):
        from services import settings as settings_service

        return settings_service.Folder(path=str(path), types=list(types),
                                       writable=writable)

    def test_las_lts_van_a_su_carpeta_si_esta_configurada(self):
        """El caso que motivó todo: las LTS al SSD, el resto al disco lento."""
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [
            self._folder("/tmp/ssd", [channels.TYPE_LTS]),
            self._folder("/tmp/datos", [channels.TYPE_STABLE,
                                        channels.TYPE_DAILY,
                                        channels.TYPE_EXPERIMENTAL]),
        ]
        lts = _build("4.5.13", "v45", "stable")
        otra = _build("5.1.2", "v51", "stable")
        self.assertEqual(window._destination_for(lts), "/tmp/ssd")
        self.assertEqual(window._destination_for(otra), "/tmp/datos")

    def test_un_tipo_sin_carpeta_no_tiene_destino(self):
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [self._folder("/tmp/ssd", [channels.TYPE_LTS])]
        self.assertEqual(
            window._destination_for(_build("5.1.2", "v51", "stable")), "")

    def test_el_aviso_de_carpetas_se_enseña_una_sola_vez(self):
        """Presentación de la biblioteca a quien viene de una versión vieja.

        **No se construye la ventana con el aviso pendiente**: el arranque
        programa el modal con un QTimer y cualquier test posterior que corra el
        bucle de eventos lo dispararía, colgando la suite (es el mismo fallo
        que ya avisa el mixin ``SettingsIsolated``). Se fuerza el estado sobre
        una ventana ya montada y se llama al método a mano.
        """
        from unittest import mock

        from services import settings as settings_service
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders_hint_shown = False
        with mock.patch.object(folder_library, "AppDialog") as dialogo:
            dialogo.return_value.exec.return_value = 0
            window._show_folders_hint()
        self.assertTrue(dialogo.called)
        # Queda marcado y guardado, así que no vuelve a salir nunca.
        self.assertTrue(window.settings.folders_hint_shown)
        self.assertTrue(settings_service.Settings.load().folders_hint_shown)

    def test_una_subcarpeta_de_otra_se_puede_anadir(self):
        """Tener la raíz y una subcarpeta dentro es un reparto legítimo.

        Es lo que pidió un usuario: ``Blender 3D`` de raíz y
        ``Blender 3D/Experimentales`` para las ramas. Antes se rechazaba con
        "sus versiones ya se encuentran", que además era falso.
        """
        import tempfile
        from pathlib import Path as _Path

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmp:
            raiz = _Path(tmp) / "Blender 3D"
            dentro = raiz / "Experimentales"
            dentro.mkdir(parents=True)
            window.settings.folders = [self._folder(raiz, ["stable"])]
            self.assertEqual(window._folder_problem(str(dentro)), "")
            # La misma carpeta dos veces sí se sigue rechazando.
            self.assertEqual(window._folder_problem(str(raiz)), "duplicate")

    def test_una_subcarpeta_no_duplica_las_versiones(self):
        """El escaneo de la raíz solo mira sus hijos directos.

        Por eso anidar no cuenta ninguna build dos veces, que era el motivo por
        el que se prohibía.
        """
        import tempfile
        from pathlib import Path as _Path

        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmp:
            raiz = _Path(tmp) / "Blender 3D"
            dentro = raiz / "Experimentales"
            (raiz / "blender-5.2.1-linux-x64").mkdir(parents=True)
            (dentro / "blender-5.3.0-linux-x64").mkdir(parents=True)
            window.settings.folders = [
                self._folder(raiz, [channels.TYPE_LTS, channels.TYPE_STABLE,
                                    channels.TYPE_DAILY]),
                self._folder(dentro, [channels.TYPE_EXPERIMENTAL]),
            ]
            window.refresh_installed()
            self.assertEqual(sorted(e.version for e in window.installed),
                             ["5.2.1", "5.3.0"])

    def test_reabrir_el_candado_devuelve_los_tipos(self):
        """Cerrar y abrir el candado no puede dejar la carpeta sin nada.

        Antes, cerrarlo vaciaba las casillas y abrirlo no las devolvía: quien
        lo probara con su única carpeta se quedaba sin sitio donde descargar
        sin haber tocado ninguna casilla.
        """
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [
            self._folder("/tmp/datos", channels.BUILD_TYPES)]
        window._rebuild_folder_rows()
        window._on_folder_writable_toggled("/tmp/datos", False)
        self.assertEqual(window.settings.folders[0].types, [])
        window._on_folder_writable_toggled("/tmp/datos", True)
        self.assertEqual(window.settings.folders[0].types,
                         list(window.settings.active_build_types()))
        self.assertEqual(channels.orphan_types(
            window.settings.folders, window.settings.active_build_types()), [])

    def test_reabrir_el_candado_no_roba_tipos_a_otra_carpeta(self):
        """Solo recupera los que no tenga nadie."""
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [
            self._folder("/tmp/datos", [channels.TYPE_LTS]),
            self._folder("/tmp/otra", [channels.TYPE_STABLE]),
        ]
        window._rebuild_folder_rows()
        window._on_folder_writable_toggled("/tmp/datos", False)
        # Mientras estaba cerrada, otra carpeta se queda con las LTS.
        window.settings.folders[1].types = [channels.TYPE_STABLE,
                                            channels.TYPE_LTS]
        window._on_folder_writable_toggled("/tmp/datos", True)
        self.assertNotIn(channels.TYPE_LTS, window.settings.folders[0].types)
        self.assertIn(channels.TYPE_LTS, window.settings.folders[1].types)

    def test_se_puede_quitar_la_ultima_carpeta(self):
        """Sin esto el usuario se quedaba encerrado: ni cambiarla ni quitarla."""
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        ruta = window.settings.folders[0].path
        with mock.patch.object(folder_library, "confirm", return_value=True):
            window._on_folder_remove_requested(ruta)
        self.assertEqual(window.settings.folders, [])

    def test_una_lista_vacia_a_proposito_no_se_repone(self):
        """Reponer una carpeta que acaban de quitar es deshacer su decisión."""
        from services import settings as settings_service

        settings = settings_service.Settings.load()
        settings.folders = []
        settings.save()
        self.assertEqual(settings_service.Settings.load().folders, [])

    def test_una_carpeta_con_todo_enseña_el_modo_simple(self):
        """Con una sola carpeta, Ajustes se ve como siempre.

        Es lo que hace que quien no quiera separar nada no estrene un concepto
        que no ha pedido.
        """
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        self.assertEqual(len(window.settings.folders), 1)
        self.assertFalse(window._branched())
        self.assertFalse(window.simple_box.isHidden())
        self.assertTrue(window.folder_list.isHidden())

    def test_con_dos_carpetas_aparece_la_lista(self):
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [
            self._folder("/tmp/ssd", [channels.TYPE_LTS]),
            self._folder("/tmp/datos", [channels.TYPE_STABLE,
                                        channels.TYPE_DAILY,
                                        channels.TYPE_EXPERIMENTAL]),
        ]
        window._rebuild_folder_rows()
        self.assertTrue(window._branched())
        self.assertTrue(window.simple_box.isHidden())
        self.assertFalse(window.folder_list.isHidden())
        self.assertEqual(len(window.folder_rows), 2)

    def test_marcar_un_tipo_se_lo_quita_a_la_otra_carpeta(self):
        """Cada tipo tiene un dueño: si no, el destino sería ambiguo."""
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [
            self._folder("/tmp/ssd", [channels.TYPE_LTS]),
            self._folder("/tmp/datos", [channels.TYPE_STABLE]),
        ]
        window._rebuild_folder_rows()
        fila = window.folder_rows[channels.normalize_path("/tmp/datos")]
        fila.checks[channels.TYPE_LTS].setChecked(True)
        self.assertEqual(window.settings.folders[0].types, [])
        self.assertEqual(window.settings.folders[1].types,
                         [channels.TYPE_LTS, channels.TYPE_STABLE])
        # Y la otra fila se entera (sin reconstruir la lista entera).
        otra = window.folder_rows[channels.normalize_path("/tmp/ssd")]
        self.assertFalse(otra.checks[channels.TYPE_LTS].isChecked())

    def test_cerrar_el_candado_apaga_las_casillas(self):
        """No se puede descargar donde la aplicación no escribe."""
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [
            self._folder("/tmp/ssd", [channels.TYPE_LTS]),
            self._folder("/tmp/datos", [channels.TYPE_STABLE]),
        ]
        window._rebuild_folder_rows()
        fila = window.folder_rows[channels.normalize_path("/tmp/ssd")]
        fila.write_toggle.setChecked(False)
        self.assertFalse(window.settings.folders[0].writable)
        self.assertEqual(window.settings.folders[0].types, [])
        self.assertFalse(fila.checks[channels.TYPE_LTS].isEnabled())
        self.assertFalse(fila.checks[channels.TYPE_LTS].isChecked())

    def test_un_tipo_sin_dueño_se_avisa_en_la_tarjeta(self):
        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.settings.folders = [self._folder("/tmp/ssd", [channels.TYPE_LTS])]
        window._rebuild_folder_rows()
        from i18n import tr
        from ui.widgets.folders import TYPE_LABELS

        self.assertFalse(window.folders_warning.isHidden())
        self.assertIn(tr(TYPE_LABELS[channels.TYPE_DAILY]),
                      window.folders_warning.text())

    def test_escanea_todas_las_carpetas(self):
        import tempfile
        from pathlib import Path

        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            principal = base / "principal"
            lts = base / "lts"
            (principal / "blender-5.1.2-linux-x64").mkdir(parents=True)
            (lts / "blender-4.5.13-linux-x64").mkdir(parents=True)
            window.settings.folders = [
                self._folder(principal, [channels.TYPE_STABLE,
                                         channels.TYPE_DAILY,
                                         channels.TYPE_EXPERIMENTAL]),
                self._folder(lts, [channels.TYPE_LTS]),
            ]
            window.refresh_installed()
            self.assertEqual([e.version for e in window.installed],
                             ["5.1.2", "4.5.13"])

    def test_una_carpeta_de_solo_lectura_se_escanea_pero_no_recibe(self):
        """Es lo que antes era la "carpeta extra"."""
        import tempfile
        from pathlib import Path

        from services import channels
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            principal = base / "principal"
            mios = base / "mis-blenders"
            principal.mkdir()
            (mios / "blender-5.0.1-linux-x64").mkdir(parents=True)
            window.settings.folders = [
                self._folder(principal, channels.BUILD_TYPES),
                self._folder(mios, [], writable=False),
            ]
            window.refresh_installed()
            # Sus versiones se ven...
            self.assertEqual([e.version for e in window.installed], ["5.0.1"])
            # ...pero ahí no baja nada.
            self.assertEqual(
                window._destination_for(_build("5.0.2", "v50", "stable")),
                str(principal))

@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class BlenderUpdateTests(SettingsIsolated, unittest.TestCase):
    """Aviso de Blender más nuevos que los instalados."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def _entrada(self, version="5.2.0", carpeta=None):
        from services.installed import InstalledBuild

        path = Path(carpeta) if carpeta else Path("/tmp") / f"blender-{version}"
        return InstalledBuild(name=f"blender-{version}-linux-x64", path=path,
                              version=version, branch="v52")

    def test_recalcula_parche_y_serie(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.builds = [_build("5.2.2", "v52", "stable"),
                         _build("5.3.0", "v53", "stable")]
        window.installed = [self._entrada("5.2.0")]
        window._recompute_updates()

        self.assertEqual(window.updates_by_path[str(window.installed[0].path)].version,
                         "5.2.2")
        self.assertEqual([u.build.version for u in window.series_updates], ["5.3.0"])

    def test_la_tarjeta_ofrece_actualizar(self):
        from ui.widgets.buttons import CardButton
        from ui.widgets.cards import InstalledCard

        entry = self._entrada("5.2.0")
        build = _build("5.2.2", "v52", "stable")
        card = InstalledCard(entry, False, update=build)
        boton = next(b for b in card.findChildren(CardButton)
                     if "5.2.2" in b.text())
        recibidos = []
        card.update_clicked.connect(lambda e, b: recibidos.append((e, b)))
        boton.click()
        self.assertEqual(recibidos, [(entry, build)])

    def test_reemplazar_borra_la_instalada_vieja(self):
        import tempfile
        from unittest import mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as temp:
            vieja = Path(temp) / "blender-5.2.0-linux-x64"
            vieja.mkdir()
            (vieja / "blender").write_text("binario", encoding="utf-8")
            window._replace_entry = self._entrada("5.2.0", carpeta=vieja)
            with mock.patch.object(window, "refresh_installed"), \
                    mock.patch.object(window, "_rebuild_store"), \
                    mock.patch.object(window, "_show_message") as aviso:
                window._on_extract_done(str(Path(temp) / "nueva"))
            self.assertFalse(vieja.exists())
            self.assertIsNone(window._replace_entry)
            self.assertTrue(aviso.called)

    def test_nunca_silencia_la_serie_de_blender(self):
        from unittest import mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.builds = [_build("5.2.2", "v52", "stable")]
        window.installed = [self._entrada("5.2.0")]
        window._recompute_updates()
        self.assertTrue(window.updates_by_path)

        with mock.patch.object(window, "_show_message"):
            window.mute_blender_series(window.installed[0])
        self.assertEqual(window.settings.ignored_blender_series, ["5.2"])
        # Sin actualizaciones para esa serie: ni parche ni salto.
        self.assertEqual(window.updates_by_path, {})

    def test_reactivar_vuelve_a_ofrecer_las_series(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.builds = [_build("5.2.2", "v52", "stable")]
        window.installed = [self._entrada("5.2.0")]
        window.settings.ignored_blender_series = ["5.2"]
        window._recompute_updates()
        self.assertEqual(window.updates_by_path, {})

        window.reset_blender_series()
        self.assertEqual(window.settings.ignored_blender_series, [])
        self.assertTrue(window.updates_by_path)

    def test_la_serie_solo_se_ofrece_una_vez(self):
        from unittest import mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        entry = self._entrada("5.2.0")
        build = _build("5.3.0", "v53", "stable")
        from services.installed import Update

        window.series_updates = [Update(entry, build, "series")]
        with mock.patch.object(window, "offer_blender_update") as ofrecer:
            window._offer_series_update()
            window._offer_series_update()
        ofrecer.assert_called_once_with(entry, build)


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class SourceUpdateUiTests(SettingsIsolated, unittest.TestCase):
    """Modo fuente: la actualización es ``git pull``, no un binario."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def test_no_avisa_al_arrancar_en_modo_fuente(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window._update_checking = False
        # En tests no hay binario (sys.frozen es False): modo fuente.
        window.check_updates(manual=False)
        # Ni hilo ni comprobación: en un checkout no se avisa al arrancar.
        self.assertFalse(window._update_checking)

    def test_modo_fuente_no_descarga_binario(self):
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(updates.updater, "source_root",
                               return_value=Path("/tmp/repo")), \
                mock.patch.object(window, "_show_update_available") as binario, \
                mock.patch.object(window, "_show_source_update") as fuente:
            window._on_update_result("v9.9.9", [], True)
        fuente.assert_called_once()
        binario.assert_not_called()

    def test_fuente_sin_git_abre_la_release(self):
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(updates.updater, "source_root",
                               return_value=None), \
                mock.patch.object(updates.updater, "open_releases") as abrir, \
                mock.patch.object(window, "_show_update_available") as binario, \
                mock.patch.object(window, "_show_message"):
            window._on_update_result("v9.9.9", [], True)
        # Sin git no hay nada que aplicar: nada de descargar un binario.
        abrir.assert_called_once()
        binario.assert_not_called()

    def test_si_falla_la_comprobacion_lo_dice(self):
        from unittest import mock

        from i18n import tr
        from services import updater
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.current_version = "1.3.0"
        mensajes = []

        def falso_mensaje(text, timeout=4):
            mensajes.append(text)

        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(window, "_show_message", falso_mensaje), \
                mock.patch.object(window, "_show_update_available") as disponible:
            # Sin tag: la comprobacion no llego a hacerse (red, TLS, cuota...).
            # Antes esto decia "ya tienes la ultima version", que es mentira y
            # fue lo que hizo pensar que no habia actualizacion cuando si la
            # habia (paso en Arch: el OpenSSL del binario no encontraba las CAs).
            window._on_update_result("", [], True)
            self.assertEqual(mensajes, [tr("Update check failed")])
            disponible.assert_not_called()

            # Con tag y asset, si ofrece la actualizacion.
            mensajes.clear()
            asset = {"name": updater.asset_for(window.system), "url": "http://x"}
            window._on_update_result("v1.4.0", [asset], True)
            disponible.assert_called_once()

    def test_actualiza_directo_a_la_ultima_no_es_secuencial(self):
        """No hay parches: desde una version vieja se baja la ultima directa.

        Si tienes la 1.0.0 y la ultima es la 1.3.0, se ofrece y se descarga el
        asset de la 1.3.0; no hay que pasar por la 1.1.0 ni la 1.2.0. Lo que se
        mira es ``releases/latest``, no la siguiente version.
        """
        from services import updater
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.current_version = "1.0.0"
        asset = {"name": updater.asset_for(window.system),
                 "url": "http://example/v1.3.0"}
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(window, "_show_update_available") as ofrecer, \
                mock.patch.object(updates.updater, "checksum_for",
                                  return_value="ab" * 32), \
                mock.patch.object(window.update_downloader, "start") as bajar:
            window._on_update_result("v1.3.0", [asset], False)
            # Se ofrece la 1.3.0 (no una intermedia).
            self.assertEqual(ofrecer.call_args.args[0], "v1.3.0")
            # Y al aceptar se descarga el asset de la 1.3.0.
            window._do_update(asset, [asset])
            _wait_until(lambda: bajar.called)

        bajar.assert_called_once()
        self.assertEqual(bajar.call_args.args[0], "http://example/v1.3.0")
        # Con el SHA-256 de checksums.txt: lo que se baja aquí se ejecuta.
        self.assertEqual(bajar.call_args.args[3], "ab" * 32)

    def test_sin_checksum_no_se_instala_la_actualizacion(self):
        """Sin ``checksums.txt`` no se baja (ni se ejecuta) el binario nuevo.

        El port a PySide6 dejó de pasar el SHA-256 al descargador y durante un
        tiempo se instalaban actualizaciones sin verificar nada.
        """
        from i18n import tr
        from services import updater
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        asset = {"name": updater.asset_for(window.system), "url": "http://x"}
        with mock.patch.object(updates.updater, "checksum_for",
                               return_value=None), \
                mock.patch.object(window.update_downloader, "start") as bajar, \
                mock.patch.object(updates, "AppDialog") as dialogo:
            window._do_update(asset, [asset])
            _wait_until(lambda: dialogo.called)
        bajar.assert_not_called()
        # Se ofrece la salida manual (la página de releases) con el motivo.
        mensaje = dialogo.call_args.args[2]
        self.assertIn(tr("Checksum error"), mensaje)

    def test_dialogo_fuente_rehabilita_al_fallar(self):
        from i18n import tr
        from ui.widgets.dialogs import AppDialog
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        dialog = AppDialog(window, tr("Update available"), tr("Updating..."))
        update_btn = dialog.add_button(tr("Update"), variant="accent")
        later_btn = dialog.add_button(tr("Later"))
        window._source_dialog = dialog
        window._on_source_update_done(False, "dirty")
        # Tras un pull fallido el diálogo explica el motivo y deja reintentar.
        self.assertEqual(
            dialog.body_label.text(),
            tr("You have local changes. Commit or stash them and try again."))
        self.assertTrue(update_btn.isEnabled())
        self.assertTrue(later_btn.isEnabled())


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class DownloadSourceTests(SettingsIsolated, unittest.TestCase):
    """La descarga usa la fuente que elige la sonda (CDN o release oficial)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_descarga_desde_la_fuente_elegida(self):
        from unittest import mock

        from PySide6.QtTest import QTest

        from services.sources import Source
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.builds = [_build("9.9.9", "v99", "stable")]
        window.installed = []
        elegida = Source("Blender release (Cloudflare)",
                         "https://download.blender.org/release/Blender5.1/f.tar.xz",
                         "hash-del-release")
        with mock.patch.object(downloads.sources, "choose",
                               return_value=elegida), \
                mock.patch.object(window.downloader, "start") as arranque:
            window.install_build(window.builds[0])
            # La eleccion va en un hilo y vuelve por senal: hay que dejar correr
            # el bucle de eventos para que llegue.
            QTest.qWait(300)

        self.assertTrue(arranque.called)
        url, _destino, _nombre, checksum = arranque.call_args.args
        self.assertEqual(url, elegida.url)
        self.assertEqual(checksum, "hash-del-release")

    def test_write_problem_detecta_carpeta_imposible(self):
        import tempfile
        from pathlib import Path as _Path

        from ui.widgets.shell import write_problem

        with tempfile.TemporaryDirectory() as tmp:
            # Una carpeta nueva se puede crear y escribir: no hay problema.
            self.assertEqual(write_problem(_Path(tmp) / "nueva"), "")
            # Si el "padre" es un fichero, no se puede crear la carpeta.
            blocker = _Path(tmp) / "archivo"
            blocker.write_text("x", encoding="utf-8")
            self.assertTrue(write_problem(blocker / "sub"))

    def test_no_descarga_si_no_se_puede_escribir_y_no_elige_otra(self):
        from unittest import mock

        from services.sources import Source
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        build = _build("4.2.23", "v42", "stable")
        fuente = Source("Blender CDN", "https://x/f.zip")
        with mock.patch.object(downloads, "write_problem",
                               return_value="[Errno 13] Permission denied"), \
                mock.patch.object(window, "_ask_other_folder",
                                  return_value=False) as preguntar, \
                mock.patch.object(window.downloader, "start") as arranque:
            window._start_download(build, fuente)
        # Se ofrece cambiar de carpeta y, si dice que no, no se baja nada.
        preguntar.assert_called_once()
        arranque.assert_not_called()

    def test_reintenta_con_la_carpeta_nueva(self):
        from unittest import mock

        from services.sources import Source
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        build = _build("4.2.23", "v42", "stable")
        fuente = Source("Blender CDN", "https://x/f.zip")
        problemas = {"n": 0}

        def problema(folder):
            problemas["n"] += 1
            # La primera carpeta no deja; la que elige el usuario, sí.
            return "denied" if problemas["n"] == 1 else ""

        with mock.patch.object(downloads, "write_problem",
                               side_effect=problema), \
                mock.patch.object(window, "_ask_other_folder",
                                  return_value=True), \
                mock.patch.object(window.downloader, "start") as arranque:
            window._start_download(build, fuente)
        # Tras elegir otra carpeta, la descarga arranca sola.
        self.assertTrue(arranque.called)
        self.assertEqual(problemas["n"], 2)

    def test_actualizar_la_app_no_cruza_la_descarga_de_blender(self):
        """Cada descarga vuelve por su propio puente.

        Antes las dos compartían ``self._bridge``: si se aceptaba actualizar la
        app con un Blender bajando, al acabar el Blender se llamaba a
        ``_apply_update`` con su ``.tar.xz`` (y con el AppImage, se copiaba
        encima del ejecutable de la app).
        """
        from services import updater
        from services.sources import Source
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        build = _build("4.2.23", "v42", "stable")
        asset = {"name": updater.asset_for(window.system), "url": "http://x"}
        with mock.patch.object(downloads, "write_problem", return_value=""), \
                mock.patch.object(window.downloader, "start") as blender, \
                mock.patch.object(window.update_downloader, "start") as app, \
                mock.patch.object(updates.updater, "checksum_for",
                                  return_value="ab" * 32), \
                mock.patch.object(window, "_on_download_done") as hecho, \
                mock.patch.object(window, "_apply_update") as aplicar:
            window._start_download(build, Source("Blender CDN",
                                                 "https://x/f.tar.xz"))
            window._do_update(asset, [asset])
            _wait_until(lambda: app.called)
            # Termina la descarga de Blender, la que empezó primero.
            blender.call_args.kwargs["on_done"](Path("/tmp/f.tar.xz"))
            _wait_until(lambda: hecho.called or aplicar.called)
        hecho.assert_called_once()
        aplicar.assert_not_called()

    def test_cancelar_mientras_se_elige_la_fuente(self):
        """Cancelar durante la medición descarta la fuente que llegue tarde."""
        from services.sources import Source
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        build = _build("4.2.23", "v42", "stable")
        with mock.patch.object(downloads.sources, "choose",
                               return_value=Source("Blender CDN", "https://x")), \
                mock.patch.object(window, "_start_download") as empezar:
            window.install_build(build)
            window.cancel_download()
            _wait_until(lambda: False, timeout=0.3)
        empezar.assert_not_called()
        self.assertFalse(window._download_busy())

    def test_pide_permiso_de_administrador(self):
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(folder_library.elevate, "available",
                               return_value=True), \
                mock.patch.object(folder_library.elevate, "relaunch_elevated",
                                  return_value=True) as relanzar, \
                mock.patch.object(folder_library, "write_problem",
                                  return_value=""):
            # Se parchea donde se **usa** (folder_library): parchearlo en
            # downloads dejaba correr el real, que creaba la carpeta
            # "C:\\Program Files\\X" en el directorio actual.
            self.assertTrue(window._grant_permission("C:\\Program Files\\X"))
        # Se pide el UAC con el argumento interno que da el permiso.
        self.assertEqual(relanzar.call_args.args[0][0], "--grant-access")

    def test_si_cancela_el_uac_lo_dice(self):
        from unittest import mock

        from i18n import tr
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(folder_library.elevate, "available",
                               return_value=True), \
                mock.patch.object(folder_library.elevate, "relaunch_elevated",
                                  return_value=False), \
                mock.patch.object(window, "_show_message") as aviso:
            self.assertFalse(window._grant_permission("C:\\Program Files\\X"))
        self.assertEqual(aviso.call_args.args[0],
                         tr("The permission request was cancelled."))

    def test_el_error_de_descarga_muestra_el_motivo(self):
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(downloads, "show_error") as error:
            window._on_download_error("[Errno 13] Permission denied: 'C:\\\\LTS'")
        self.assertTrue(error.called)
        self.assertIn("Permission denied", error.call_args.args[2])

    def test_checksum_con_mensaje_claro(self):
        from unittest import mock

        from i18n import tr
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(downloads, "show_error") as error:
            window._on_download_error("checksum")
        self.assertEqual(error.call_args.args[2], tr("Checksum error"))

    def test_cancelar_no_dice_fallo(self):
        from unittest import mock

        from i18n import tr
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.cancel_download()
        with mock.patch.object(downloads, "show_error") as error:
            window._on_download_error("cancelled")
        self.assertEqual(window.status_label.text(), tr("Cancelled"))
        error.assert_not_called()

    def test_un_dmg_no_se_intenta_extraer(self):
        """macOS: el .dmg no es un comprimido; se revela y se avisa, sin extraer.

        Fallo real: ``_on_download_done`` llamaba a ``extract()`` siempre, y
        ``tarfile.open('r:*')`` sobre un .dmg daba "file could not be opened
        successfully" como si la descarga hubiera fallado.
        """
        import tempfile
        from pathlib import Path as _Path

        from i18n import tr
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmp:
            dmg = _Path(tmp) / "blender-5.2.1-macos-arm64.dmg"
            dmg.write_bytes(b"esto no es un tar")
            with mock.patch.object(downloads, "extract") as extraer, \
                    mock.patch.object(downloads.opener, "reveal",
                                      return_value=True) as revelar, \
                    mock.patch.object(window, "_show_message") as aviso:
                window._on_download_done(str(dmg), _build("5.2.1", "v52", "stable"))
            # El .dmg es el único artefacto útil: no se borra aunque
            # ``delete_archive`` esté activo.
            self.assertTrue(dmg.exists())
        extraer.assert_not_called()
        revelar.assert_called_once()
        self.assertIn(tr("Downloaded to {folder}", folder=dmg.parent),
                      aviso.call_args.args[0])
        self.assertIn(tr("Open it to install Blender manually."),
                      aviso.call_args.args[0])

    def test_extraer_anota_el_marcador(self):
        """Al extraer se escribe ``.blendermanager.json`` (se perdió en el port).

        Sin marcador no se distinguen dos diarias de la misma versión ni se
        reconocen las ramas experimentales (ver ``services/installed.py``).
        """
        import tempfile
        from pathlib import Path as _Path

        from PySide6.QtTest import QTest

        from services import installed
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        build = _build("5.2.1", "v52", "stable")
        build.build_hash = "abc123"
        with tempfile.TemporaryDirectory() as tmp:
            destino = _Path(tmp)
            # La extracción crea una carpeta nueva dentro del destino.
            carpeta = destino / "blender-5.2.1-linux-x64"
            carpeta.mkdir()
            archivo = destino / "blender-5.2.1-linux-x64.tar.xz"
            archivo.write_bytes(b"x")
            with mock.patch.object(window, "_destination_for",
                                   return_value=str(destino)), \
                    mock.patch.object(downloads, "extract",
                                      return_value=carpeta):
                window._on_download_done(str(archivo), build)
                # La extracción va en un hilo: hay que dejarle terminar.
                QTest.qWait(300)
            marcador = installed.read_marker(carpeta)
        self.assertEqual(marcador.get("version"), "5.2.1")
        self.assertEqual(marcador.get("hash"), "abc123")

    def test_si_falla_bajar_la_actualizacion_ofrece_los_releases(self):
        """Un fallo de red al actualizar no puede ser un callejón sin salida.

        Caso real (macOS): ``urlopen error _ssl.c:993: The handshake operation
        timed out`` al bajar ``BlenderManager-macos.zip``. El navegador sigue
        siendo una vía, así que se ofrece la página de releases.
        """
        from i18n import tr
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        motivo = ("<urlopen error _ssl.c:993: "
                  "The handshake operation timed out>")
        with mock.patch.object(updates, "AppDialog") as dialogo, \
                mock.patch.object(updates.updater, "open_releases") as abrir:
            window._on_update_download_error(motivo)
            # El motivo real va en el cuerpo del diálogo.
            self.assertIn(motivo, dialogo.call_args.args[2])
            botones = dialogo.return_value.add_button.call_args_list
            abridor = next(c for c in botones
                           if c.args[0] == tr("Open the releases page"))
            # El botón accent es el que abre la página; al pulsarlo, se llama.
            self.assertEqual(abridor.kwargs.get("variant"), "accent")
            abridor.kwargs["on_click"]()
        abrir.assert_called_once()

    def test_en_mac_el_dmg_se_instala_en_la_carpeta(self):
        """macOS: el .dmg se monta y el Blender.app queda en la carpeta destino.

        Antes solo se revelaba el fichero y no había forma de lanzar esa build
        desde la app; montarlo y copiarlo la deja como cualquier otra (escaneo,
        lanzar, desinstalar).
        """
        import tempfile
        from pathlib import Path as _Path

        from PySide6.QtTest import QTest

        from services import installed
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.system = window.system._replace(os_name="darwin")
        build = _build("5.2.1", "v52", "stable")
        with tempfile.TemporaryDirectory() as tmp:
            destino = _Path(tmp)
            dmg = destino / "blender-5.2.1-macos-arm64.dmg"
            dmg.write_bytes(b"dmg")
            carpeta = destino / "blender-5.2.1-macos-arm64"
            carpeta.mkdir()
            with mock.patch.object(window, "_destination_for",
                                   return_value=str(destino)), \
                    mock.patch.object(downloads.macos_dmg, "available",
                                      return_value=True), \
                    mock.patch.object(downloads.macos_dmg, "install",
                                      return_value=carpeta) as instalar, \
                    mock.patch.object(downloads.opener, "reveal") as revelar:
                window._on_download_done(str(dmg), build)
                QTest.qWait(300)
            marcador = installed.read_marker(carpeta)
        instalar.assert_called_once()
        revelar.assert_not_called()
        self.assertEqual(marcador.get("version"), "5.2.1")

    def test_si_falla_instalar_el_dmg_se_revela_y_avisa(self):
        """Plan B: si montar o copiar falla, se revela el .dmg y se avisa."""
        import tempfile
        from pathlib import Path as _Path

        from PySide6.QtTest import QTest

        from i18n import tr
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.system = window.system._replace(os_name="darwin")
        build = _build("5.2.1", "v52", "stable")
        with tempfile.TemporaryDirectory() as tmp:
            dmg = _Path(tmp) / "blender-5.2.1-macos-arm64.dmg"
            dmg.write_bytes(b"dmg")
            with mock.patch.object(window, "_destination_for",
                                   return_value=tmp), \
                    mock.patch.object(downloads.macos_dmg, "available",
                                      return_value=True), \
                    mock.patch.object(downloads.macos_dmg, "install",
                                      side_effect=RuntimeError("mount failed")), \
                    mock.patch.object(downloads.opener, "reveal",
                                      return_value=True) as revelar, \
                    mock.patch.object(window, "_show_message") as aviso:
                window._on_download_done(str(dmg), build)
                QTest.qWait(300)
        revelar.assert_called_once()
        self.assertIn(tr("Open it to install Blender manually."),
                      aviso.call_args.args[0])


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class ElideTests(SettingsIsolated, unittest.TestCase):
    """Los nombres largos no deben ensanchar su columna ni salirse de la ventana."""

    LARGO = "blender-5.3.0-alpha+main.1fd06ddba680-linux.x86_64-release"

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def _entrada(self, nombre):
        from pathlib import Path

        from services.installed import InstalledBuild

        return InstalledBuild(name=nombre,
                              path=Path("/home/u/Descargas/Blenders") / nombre,
                              version="5.3.0", branch="v53")

    def test_rejilla_con_nombre_largo_deja_las_columnas_iguales(self):
        from PySide6.QtWidgets import QGridLayout, QWidget

        from ui.widgets.cards import GridInstalledCard

        panel = QWidget()
        panel.resize(1060, 300)
        grid = QGridLayout(panel)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setSpacing(14)
        for columna in range(3):
            grid.setColumnStretch(columna, 1)
        nombres = [self.LARGO, "blender-5.2.0", "blender-5.1.2-linux-x64"]
        for indice, nombre in enumerate(nombres):
            grid.addWidget(GridInstalledCard(self._entrada(nombre), False, 1.0),
                           0, indice)
        panel.show()
        self.app.processEvents()

        # El minimo ya no depende del texto (antes la larga pedia 507 px).
        minimos = {grid.itemAtPosition(0, i).widget().minimumSizeHint().width()
                   for i in range(3)}
        self.assertEqual(len(minimos), 1)
        # Y en pantalla las tres ocupan lo mismo (1 px arriba o abajo, que es el
        # redondeo de repartir el ancho entre tres).
        anchos = [grid.itemAtPosition(0, i).widget().width() for i in range(3)]
        self.assertLessEqual(max(anchos) - min(anchos), 1)

    def test_lista_con_ruta_larga_cabe_en_la_ventana(self):
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        from ui.widgets.cards import InstalledCard

        panel = QWidget()
        panel.resize(900, 200)
        lay = QHBoxLayout(panel)
        tarjeta = InstalledCard(self._entrada(self.LARGO), False)
        lay.addWidget(tarjeta)
        panel.show()
        self.app.processEvents()
        # Antes necesitaba 974 px (el nombre 483 + el meta con la ruta 666).
        self.assertLess(tarjeta.minimumSizeHint().width(), 500)

    def test_la_etiqueta_recorta_y_solo_avisa_si_no_cabe(self):
        from PySide6.QtCore import Qt

        from ui.widgets.labels import ElidedLabel

        etiqueta = ElidedLabel(self.LARGO, Qt.ElideMiddle)
        etiqueta.setFixedWidth(300)
        etiqueta.show()
        self.app.processEvents()
        self.assertTrue(etiqueta.is_elided())
        self.assertIn("…", etiqueta.displayed_text())
        # El texto completo sigue en text() y en el tooltip...
        self.assertEqual(etiqueta.text(), self.LARGO)
        self.assertEqual(etiqueta.toolTip(), self.LARGO)

        etiqueta.setFixedWidth(900)
        self.app.processEvents()
        # ...pero si cabe entero, el tooltip desaparece (no repite lo que ya se ve).
        self.assertFalse(etiqueta.is_elided())
        self.assertEqual(etiqueta.displayed_text(), self.LARGO)
        self.assertEqual(etiqueta.toolTip(), "")


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class UninstallTests(SettingsIsolated, unittest.TestCase):
    """Borrar una version instalada: el boton de la papelera."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def _entrada(self, carpeta):
        from pathlib import Path

        from services.installed import InstalledBuild

        return InstalledBuild(name=carpeta.name, path=carpeta, version="3.5.0",
                              branch="v35")

    def test_el_mensaje_no_revienta_con_la_ruta(self):
        # entry.path es un Path y se concatenaba a un str: TypeError al
        # construir el mensaje, ANTES de crear el dialogo. Por eso la papelera
        # no hacia nada y no decia nada (PySide se come la excepcion del slot).
        import tempfile
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow
        from i18n import tr

        window = MainWindow()
        with tempfile.TemporaryDirectory() as temp:
            entrada = self._entrada(Path(temp) / "blender-3.5.0-linux-x64")
            with mock.patch.object(downloads, "confirm", return_value=False) as conf:
                window.delete_installed(entrada)
            # La ruta tiene que llegar como texto.
            _, _, mensaje = conf.call_args.args
            self.assertIn(str(entrada.path), mensaje)
            self.assertIn(tr("Delete {name}?", name=entrada.name), mensaje)

    def test_borra_de_verdad_la_carpeta(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow
        from i18n import tr

        window = MainWindow()
        with tempfile.TemporaryDirectory() as temp:
            carpeta = Path(temp) / "blender-3.5.0-linux-x64"
            carpeta.mkdir()
            (carpeta / "blender").write_text("binario", encoding="utf-8")
            with mock.patch.object(downloads, "confirm", return_value=True), \
                    mock.patch.object(window, "refresh_installed"), \
                    mock.patch.object(window, "_show_message") as aviso:
                window.delete_installed(self._entrada(carpeta))
            self.assertFalse(carpeta.exists())
            self.assertEqual(aviso.call_args.args[0],
                             tr("Deleted {name}", name=carpeta.name))

    def test_si_falla_lo_dice_y_lo_registra(self):
        # Antes era rmtree(..., ignore_errors=True): un fallo no dejaba rastro
        # ni en pantalla ni en el log.
        import tempfile
        from pathlib import Path
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as temp:
            carpeta = Path(temp) / "blender-3.5.0-linux-x64"
            with mock.patch.object(downloads, "confirm", return_value=True), \
                    mock.patch.object(downloads.shutil, "rmtree",
                                      side_effect=OSError("read-only")), \
                    mock.patch.object(window, "refresh_installed") as refresco, \
                    mock.patch.object(downloads, "download_log") as registro, \
                    mock.patch.object(downloads, "show_error") as error:
                window.delete_installed(self._entrada(carpeta))
            self.assertTrue(error.called)
            self.assertIn("read-only", error.call_args.args[2])
            self.assertTrue(registro.called)
            self.assertFalse(refresco.called)


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class ExceptionHookTests(SettingsIsolated, unittest.TestCase):
    """Un slot que revienta ya no se pierde en silencio."""

    def test_registra_y_avisa(self):
        import sys
        from unittest import mock

        import main
        from services import downloader
        from ui.widgets import dialogs

        original = sys.excepthook
        self.addCleanup(setattr, sys, "excepthook", original)
        with mock.patch.object(downloader, "log") as registro, \
                mock.patch.object(dialogs, "show_error") as aviso:
            main._install_exception_hook()
            try:
                raise TypeError("como el de la papelera")
            except TypeError:
                sys.excepthook(*sys.exc_info())
        # El traceback completo al log, y un aviso en pantalla.
        self.assertTrue(registro.called)
        self.assertIn("unhandled error", registro.call_args.args[0])
        self.assertIn("TypeError", registro.call_args.args[0])
        self.assertTrue(aviso.called)


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class DialogTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import qss

        cls.app.setStyleSheet(qss.build_qss())

    def test_un_mensaje_largo_no_se_corta(self):
        """El texto del diálogo tiene que caber entero (verticalmente).

        Con el nombre largo de una instalada, el aviso de desinstalar se cortaba:
        Qt mide el `QLabel` con `wordWrap` antes de que el estilo aplique la
        fuente y con un ancho que después cambia, así que se quedaba con el alto
        viejo (90 px cuando necesitaba 108).
        """
        from i18n import tr
        from ui.widgets.dialogs import AppDialog

        nombre = "blender-5.3.0-alpha+main.1fd06ddba680-linux.x86_64-release"
        mensaje = (tr("Delete {name}?", name=nombre) + "\n\n"
                   + tr("This will remove the folder permanently.") + "\n\n"
                   + "/home/alguien/Descargas/Blenders/" + nombre)
        dialog = AppDialog(None, tr("Uninstall"), mensaje)
        dialog.add_button(tr("Cancel"), on_click=dialog.reject)
        dialog.show()
        for _ in range(3):
            self.app.processEvents()
        etiqueta = dialog.body_label
        self.assertGreaterEqual(etiqueta.height(),
                                etiqueta.heightForWidth(etiqueta.width()))
        # Y un nombre enorme no puede sacar la ventana de la pantalla.
        self.assertLessEqual(dialog.width(), dialog.MAX_WIDTH)

    def test_confirm_rechazado(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog

        from ui.widgets import dialogs

        def close():
            for widget in self.app.topLevelWidgets():
                if isinstance(widget, QDialog):
                    widget.reject()

        QTimer.singleShot(200, close)
        self.assertFalse(dialogs.confirm(None, "Uninstall", "¿Borrar?", danger=True))

    def test_confirm_aceptado(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog

        from ui.widgets import dialogs

        def close():
            for widget in self.app.topLevelWidgets():
                if isinstance(widget, QDialog):
                    widget.accept()

        QTimer.singleShot(200, close)
        self.assertTrue(dialogs.confirm(None, "Uninstall", "¿Borrar?"))


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class PeriodicUpdateTests(SettingsIsolated, unittest.TestCase):
    """El chequeo de la propia app se repite solo cada X minutos."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_se_programa_segun_los_ajustes(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        self.assertTrue(window._update_timer.isActive())
        self.assertEqual(window._update_timer.interval(),
                         window.settings.update_interval_min * 60 * 1000)

    def test_el_combo_cambia_el_intervalo(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        combo = window.update_interval_combo
        index = combo.findData(60)
        combo.setCurrentIndex(index)
        self.assertEqual(window.settings.update_interval_min, 60)
        self.assertEqual(window._update_timer.interval(), 60 * 60 * 1000)

    def test_apagar_el_chequeo_al_iniciar_no_para_el_periodico(self):
        """Los dos ajustes son independientes: apagar uno no toca el otro."""
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.update_switch.setChecked(False)
        # El periódico sigue programado y sus controles activos.
        self.assertTrue(window._update_timer.isActive())
        self.assertTrue(window.periodic_switch.isEnabled())
        self.assertTrue(window.update_interval_combo.isEnabled())
        self.assertFalse(window.settings.auto_update)

    def test_apagar_el_periodico_no_para_el_de_arranque(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.periodic_switch.setChecked(False)
        self.assertFalse(window.settings.periodic_update)
        self.assertFalse(window._update_timer.isActive())
        self.assertFalse(window.update_interval_combo.isEnabled())
        # El interruptor maestro sigue encendido (se comprueba al arrancar).
        self.assertTrue(window.auto_update)
        self.assertTrue(window.update_switch.isChecked())

    def test_chequea_cuando_toca(self):
        from unittest import mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.downloader = mock.Mock(running=False)
        window.update_downloader = mock.Mock(running=False)
        with mock.patch.object(window, "check_updates") as check:
            window._periodic_update_check()
        check.assert_called_once_with(manual=False)

    def test_no_interrumpe_si_hay_descarga(self):
        from unittest import mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.downloader = mock.Mock(running=True)
        window.update_downloader = mock.Mock(running=False)
        with mock.patch.object(window, "check_updates") as check:
            window._periodic_update_check()
        check.assert_not_called()

    def test_no_repite_el_aviso_de_la_misma_version(self):
        from unittest import mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window._offered_update_tag = "v9.9.9"
        window.downloader = mock.Mock(running=False)
        window.update_downloader = mock.Mock(running=False)
        with mock.patch.object(window, "check_updates") as check:
            window._periodic_update_check()
        check.assert_not_called()

    def test_el_aviso_no_se_muestra_dos_veces(self):
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.current_version = "1.0.0"
        assets = [{"name": "BlenderManager-linux", "url": "u"}]
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(updates.updater, "asset_for",
                                  return_value="BlenderManager-linux"), \
                mock.patch.object(window, "_show_update_available") as avisar:
            window._on_update_result("v1.1.0", assets, False)
            window._on_update_result("v1.1.0", assets, False)
            self.assertEqual(avisar.call_count, 1)
            # Una versión más nueva sí vuelve a avisar.
            window._on_update_result("v1.2.0", assets, False)
            self.assertEqual(avisar.call_count, 2)

    def test_saltar_una_version_la_silencia(self):
        from unittest import mock

        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.current_version = "1.0.0"
        window.settings.skipped_version = "v1.1.0"
        assets = [{"name": "BlenderManager-linux", "url": "u"}]
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(updates.updater, "asset_for",
                                  return_value="BlenderManager-linux"), \
                mock.patch.object(window, "_show_update_available") as avisar:
            # Automático: la versión saltada no se ofrece.
            window._on_update_result("v1.1.0", assets, False)
            avisar.assert_not_called()
            # Manual: se muestra igual (el usuario la pidió a propósito).
            window._on_update_result("v1.1.0", assets, True)
            self.assertEqual(avisar.call_count, 1)

    def test_skip_guarda_la_version(self):
        from unittest import mock

        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with mock.patch.object(window, "_show_message") as aviso:
            window.skip_update_version("v1.9.9")
        self.assertEqual(window.settings.skipped_version, "v1.9.9")
        self.assertTrue(aviso.called)


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class TooltipTests(SettingsIsolated, unittest.TestCase):
    """Todo control interactivo tiene que explicarse en un tooltip."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_los_filtros_tienen_tooltip_y_explican_la_lts(self):
        from ui.widgets.main_window import CHANNELS, MainWindow

        window = MainWindow()
        for index, (key, _) in enumerate(CHANNELS):
            self.assertTrue(window.channel_tabs.tabToolTip(index), key)
        lts = next(i for i, (key, _) in enumerate(CHANNELS) if key == "lts")
        tip = window.channel_tabs.tabToolTip(lts)
        self.assertIn("LTS", tip)
        # Multi-línea: el tooltip explica, no es una etiqueta de dos palabras.
        self.assertIn("\n", tip)

    def test_los_controles_principales_tienen_tooltip(self):
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        controles = [
            window.grid_btn, window.list_btn, window.search_input,
            window.platform_combo, window.arch_combo,
            window.zoom_slider, window.reset_zoom_slider,
            window.dest_input, window.split_btn, window.add_folder_btn,
            window.archive_switch, window.language_combo,
            window.args_input, window.update_switch, window.periodic_switch,
            window.update_interval_combo,
        ]
        for control in controles:
            self.assertTrue(control.toolTip(), type(control).__name__)

    def test_los_controles_de_una_carpeta_tienen_tooltip(self):
        """Fila de la biblioteca: casillas, candado y papelera.

        Es la pantalla con más controles pequeños y sin etiqueta de la app, así
        que es donde más falta hace que cada uno se explique solo.
        """
        from services import channels, settings as settings_service
        from ui.widgets.folders import FolderRow

        folder = settings_service.Folder("/tmp/ssd", [channels.TYPE_LTS])
        row = FolderRow(folder)
        controles = list(row.checks.values()) + [row.write_toggle]
        from ui.widgets.buttons import CardButton

        controles += list(row.findChildren(CardButton))
        for control in controles:
            tip = control.toolTip()
            self.assertTrue(tip, type(control).__name__)
            # Descriptivos de verdad, no una repetición de la etiqueta.
            self.assertGreater(len(tip), 25, tip)

    def test_el_candado_dice_cosas_distintas_en_cada_estado(self):
        from services import channels, settings as settings_service
        from ui.widgets.folders import FolderRow

        folder = settings_service.Folder("/tmp/ssd", [channels.TYPE_LTS])
        row = FolderRow(folder)
        abierto = row.write_toggle.toolTip()
        row.write_toggle.setChecked(False)
        cerrado = row.write_toggle.toolTip()
        self.assertTrue(abierto and cerrado)
        self.assertNotEqual(abierto, cerrado)

    def test_los_botones_de_los_dialogos_tienen_tooltip(self):
        """Hasta "Cerrar" dice qué pasa al pulsarlo.

        Son los botones que deciden si algo se borra, se mueve o se instala:
        el que no explica lo que hace es el que da miedo pulsar.
        """
        from unittest import mock as _mock

        from ui.widgets.buttons import CardButton
        from ui.widgets.dialogs import AppDialog, ProgressDialog, confirm

        vistos = []
        real_exec = AppDialog.exec

        def espia(self):
            vistos.append([b.toolTip() for b in self.findChildren(CardButton)])
            return 0

        with _mock.patch.object(AppDialog, "exec", espia):
            confirm(None, "t", "m")
            confirm(None, "t", "m", accept_text="Borrar", danger=True)
        for tooltips in vistos:
            self.assertTrue(all(tooltips), tooltips)
        progreso = ProgressDialog(None, "t", "m")
        for boton in progreso.findChildren(CardButton):
            self.assertTrue(boton.toolTip())
        del real_exec

    def test_las_acciones_de_los_menus_tienen_tooltip(self):
        """Los menús contextuales explican cada entrada, como los botones."""
        from pathlib import Path

        from model.build import InstalledBuild
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        entry = InstalledBuild(name="b", path=Path("/tmp/b"), version="5.2.2",
                               branch="v52", executable=Path("/tmp/b/blender"))
        for menu in (window._installed_menu(entry),
                     window._store_menu(_build("5.2.2", "v52", "stable"))):
            self.assertTrue(menu.toolTipsVisible())
            for action in menu.actions():
                if action.isSeparator():
                    continue
                self.assertTrue(action.toolTip() != action.text(),
                                action.text())

    def test_las_pestanas_explican_que_hay_en_cada_una(self):
        """Ajustes y Migración: cada pestaña con su tooltip.

        "User prefs" y "Factory settings" suenan parecido y hacen cosas muy
        distintas; el nombre solo no basta.
        """
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        for tabs in (window.settings_tabs, window.migrate_view.tabs):
            for index in range(tabs.count()):
                self.assertTrue(tabs.tabToolTip(index), tabs.tabText(index))

    def test_los_botones_de_las_tarjetas_tienen_tooltip(self):
        from pathlib import Path

        from model.build import InstalledBuild
        from ui.widgets.buttons import CardButton, IconLinkButton, StarButton
        from ui.widgets.cards import GridInstalledCard, InstalledCard

        entry = InstalledBuild(name="blender-5.2.1", path=Path("/tmp/b"),
                               version="5.2.1", branch="v52")
        for card in (InstalledCard(entry, False),
                     GridInstalledCard(entry, False, 1.0)):
            for clase in (CardButton, IconLinkButton, StarButton):
                for widget in card.findChildren(clase):
                    self.assertTrue(widget.toolTip(), (type(card).__name__,
                                                       type(widget).__name__))


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class RenameTests(SettingsIsolated, unittest.TestCase):
    """Renombrar una instalada con doble clic (cambia la carpeta real)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def test_la_etiqueta_editable_emite_el_nombre(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from ui.widgets.labels import EditableLabel

        label = EditableLabel("viejo")
        recibidos = []
        label.renamed.connect(recibidos.append)
        label.start_editing()
        label._editor.setText("nuevo")
        QTest.keyClick(label._editor, Qt.Key_Return)
        self.assertEqual(recibidos, ["nuevo"])

    def test_escape_cancela_la_edicion(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from ui.widgets.labels import EditableLabel

        label = EditableLabel("viejo")
        recibidos = []
        label.renamed.connect(recibidos.append)
        label.start_editing()
        label._editor.setText("nuevo")
        QTest.keyClick(label._editor, Qt.Key_Escape)
        self.assertEqual(recibidos, [])
        self.assertEqual(label.text(), "viejo")

    def test_renombrar_cambia_la_carpeta_real(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from services.installed import InstalledBuild
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp) / "blender-5.2.1-linux-x64"
            carpeta.mkdir()
            (carpeta / "blender").write_text("bin", encoding="utf-8")
            entry = InstalledBuild(name=carpeta.name, path=carpeta,
                                   version="5.2.1", branch="v52")
            with mock.patch.object(window, "refresh_installed") as refresco, \
                    mock.patch.object(window, "_show_message"):
                window.rename_installed(entry, "Mi Blender")
            self.assertTrue((Path(tmp) / "Mi Blender").is_dir())
            self.assertFalse(carpeta.exists())
            self.assertTrue(refresco.called)

    def test_un_nombre_invalido_avisa_y_no_toca_nada(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from services.installed import InstalledBuild
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        with tempfile.TemporaryDirectory() as tmp:
            carpeta = Path(tmp) / "blender-5.2.1-linux-x64"
            carpeta.mkdir()
            entry = InstalledBuild(name=carpeta.name, path=carpeta,
                                   version="5.2.1", branch="v52")
            with mock.patch.object(downloads, "show_error") as error, \
                    mock.patch.object(window, "refresh_installed") as refresco:
                window.rename_installed(entry, "a/b")
            self.assertTrue(error.called)
            self.assertTrue(carpeta.exists())
            self.assertFalse(refresco.called)


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class TrayTests(SettingsIsolated, unittest.TestCase):
    """Bandeja del sistema: cerrar/minimizar a la bandeja, restaurar y salir.

    En el plugin *offscreen* la bandeja nunca está disponible, así que la
    disponibilidad se mockea: es la única forma de probar el camino "sí hay
    bandeja" sin un escritorio de verdad.
    """

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def _window(self, available=True):
        from ui.widgets import main_window
        from ui.widgets.main_window import MainWindow
        from ui.widgets.tray import TrayIcon

        patch = mock.patch.object(TrayIcon, "available", return_value=available)
        patch.start()
        self.addCleanup(patch.stop)
        # El estado del autoarranque se lee del sistema al construir la ventana:
        # en un equipo con el autoarranque ya activado el interruptor nace
        # marcado y el test del toggle no vería ningún cambio (en CI no existe
        # el fichero y sí lo vería). Lo fijamos para no depender de la máquina.
        autostart = mock.patch.object(settings_view.autostart, "is_enabled",
                                      return_value=False)
        autostart.start()
        self.addCleanup(autostart.stop)
        # No se destruye la ventana en el cleanup: lleva un QTimer a 100 ms que
        # llama a ``refresh``; si se borra antes de dispararse, el temporizador
        # revienta al procesar eventos en OTRO test. Las demás clases de tests
        # tampoco la destruyen.
        return MainWindow()

    def test_el_menu_ofrece_mostrar_y_salir(self):
        from i18n import tr
        from ui.widgets.tray import TrayIcon

        tray = TrayIcon()
        self.assertEqual(tray.show_action.text(), tr("Show"))
        self.assertEqual(tray.quit_action.text(), tr("Quit"))
        restaurar, salir = [], []
        tray.restore_requested.connect(lambda: restaurar.append(1))
        tray.quit_requested.connect(lambda: salir.append(1))
        tray.show_action.trigger()
        tray.quit_action.trigger()
        self.assertEqual((len(restaurar), len(salir)), (1, 1))

    def test_el_icono_esta_visible_con_la_ventana_abierta(self):
        """El icono vive siempre que la app corre, no solo escondida."""
        window = self._window()
        window.show()
        self.app.processEvents()
        self.assertIsNotNone(window._tray)
        self.assertTrue(window._tray.is_visible())
        # Sigue estando al restaurarla desde la bandeja.
        window._restore_from_tray()
        self.assertTrue(window._tray.is_visible())

    def test_cerrar_va_a_la_bandeja_si_esta_activado(self):
        window = self._window()
        self.assertTrue(window.close_to_tray)
        window.show()
        self.app.processEvents()
        window.close()
        self.assertFalse(window.isVisible())
        self.assertIsNotNone(window._tray)
        self.assertTrue(window._tray.is_visible())

    def test_cerrar_cierra_si_esta_apagado(self):
        window = self._window()
        window.close_to_tray = False
        window.show()
        self.app.processEvents()
        window.close()
        self.assertFalse(window.isVisible())
        # El icono ya no tiene a qué volver: se retira al cerrar de verdad.
        if window._tray is not None:
            self.assertFalse(window._tray.is_visible())

    def test_sin_bandeja_cerrar_cierra(self):
        """El fallback: sin bandeja, esconder la ventana la dejaría perdida."""
        window = self._window(available=False)
        window.show()
        self.app.processEvents()
        window.close()
        self.assertIsNone(window._tray)

    def test_minimizar_va_a_la_bandeja_si_esta_activado(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        window = self._window()
        window.minimize_to_tray = True
        window.show()
        self.app.processEvents()
        window.setWindowState(Qt.WindowMinimized)
        QTest.qWait(30)
        self.assertIsNotNone(window._tray)
        self.assertTrue(window._tray.is_visible())
        self.assertFalse(window.isVisible())

    def test_minimizar_no_va_a_la_bandeja_si_esta_apagado(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        window = self._window()
        window.show()
        self.app.processEvents()
        window.setWindowState(Qt.WindowMinimized)
        QTest.qWait(30)
        # El icono existe (siempre está visible con la app), pero la ventana
        # no se escondió a la bandeja: sigue minimizada, no oculta.
        self.assertFalse(window.isHidden())

    def test_el_aviso_de_la_bandeja_solo_sale_una_vez(self):
        from ui.widgets import main_window

        window = self._window()
        window._ensure_tray()
        with mock.patch.object(main_window.TrayIcon, "notify") as notify:
            window._hide_to_tray()
            window._hide_to_tray()
        self.assertEqual(notify.call_count, 1)
        self.assertTrue(window.settings.tray_hint_shown)

    def test_restaurar_vuelve_a_mostrar_la_ventana(self):
        window = self._window()
        window.show()
        self.app.processEvents()
        window._hide_to_tray()
        self.assertTrue(window._tray.is_visible())
        window._restore_from_tray()
        self.assertTrue(window.isVisible())
        # El icono sigue en la bandeja: está siempre que la app corre.
        self.assertTrue(window._tray.is_visible())

    def test_salir_desde_la_bandeja_es_una_salida_de_verdad(self):
        window = self._window()
        window._quit_from_tray()
        self.assertTrue(window._force_quit)

    def test_el_reinicio_del_fuente_no_se_queda_en_la_bandeja(self):
        from ui.widgets import main_window

        window = self._window()
        with mock.patch.object(updates.updater, "relaunch_source",
                               return_value=True):
            window._restart_from_source()
        self.assertTrue(window._force_quit)

    def test_los_interruptores_guardan_el_ajuste(self):
        from services.settings import Settings

        window = self._window()
        window.close_tray_switch.setChecked(False)
        self.assertFalse(window.settings.close_to_tray)
        window.minimize_tray_switch.setChecked(True)
        self.assertTrue(window.settings.minimize_to_tray)
        loaded = Settings.load()
        self.assertFalse(loaded.close_to_tray)
        self.assertTrue(loaded.minimize_to_tray)

    def test_sin_bandeja_los_interruptores_se_deshabilitan(self):
        window = self._window(available=False)
        self.assertFalse(window.close_tray_switch.isEnabled())
        self.assertFalse(window.minimize_tray_switch.isEnabled())

    def test_sin_soporte_no_se_ofrece_minimizar_a_la_bandeja(self):
        """Sin poder detectar el minimizado, 'cerrar' sigue; 'minimizar' no."""
        from ui.widgets import main_window

        with mock.patch.object(settings_view.detector,
                               "minimize_to_tray_supported", return_value=False):
            window = self._window()
        # Cerrar a la bandeja sí sigue disponible; minimizar no.
        self.assertTrue(window.close_tray_switch.isEnabled())
        self.assertFalse(window.minimize_tray_switch.isEnabled())

    def test_en_wayland_no_se_ofrece_minimizar_a_la_bandeja(self):
        from ui.widgets import main_window

        with mock.patch.object(settings_view.detector, "session_is_wayland",
                               return_value=True):
            window = self._window()
        self.assertTrue(window.close_tray_switch.isEnabled())
        self.assertFalse(window.minimize_tray_switch.isEnabled())

    def test_arrancar_minimizado_deja_la_ventana_en_la_bandeja(self):
        from PySide6.QtTest import QTest

        import main

        window = self._window()
        main._start_window(window, start_minimized=True)
        QTest.qWait(20)
        self.assertFalse(window.isVisible())
        self.assertIsNotNone(window._tray)
        self.assertTrue(window._tray.is_visible())

    def test_sin_bandeja_arrancar_minimizado_enseña_la_ventana(self):
        import main

        window = self._window(available=False)
        main._start_window(window, start_minimized=True)
        self.app.processEvents()
        # Sin icono al que volver, se enseña igual en vez de quedar escondida.
        self.assertTrue(window.isVisible())
        self.assertIsNone(window._tray)

    def test_arrancar_minimizado_se_guarda(self):
        from services.settings import Settings

        window = self._window()
        window.start_minimized_switch.setChecked(True)
        self.assertTrue(window.settings.start_minimized)
        self.assertTrue(Settings.load().start_minimized)

    def test_el_autoarranque_se_registra_y_se_revierte_si_falla(self):
        from ui.widgets import main_window

        window = self._window()
        with mock.patch.object(settings_view.autostart, "enable",
                               return_value=True) as activar:
            window.autostart_switch.setChecked(True)
        self.assertTrue(activar.called)

        # Si el sistema no deja desactivarlo, se avisa y el interruptor vuelve
        # al estado real (no puede quedarse mintiendo).
        with mock.patch.object(settings_view.autostart, "disable",
                               return_value=False), \
                mock.patch.object(settings_view.autostart, "is_enabled",
                                  return_value=True), \
                mock.patch.object(settings_view, "show_error") as error:
            window.autostart_switch.setChecked(False)
        self.assertTrue(error.called)
        self.assertTrue(window.autostart_switch.isChecked())

    def test_sin_soporte_de_autoarranque_el_interruptor_se_deshabilita(self):
        from ui.widgets import main_window

        with mock.patch.object(settings_view.autostart, "supported",
                               return_value=False):
            window = self._window()
        self.assertFalse(window.autostart_switch.isEnabled())


def _fake_installed(version, name=None):
    from model.build import InstalledBuild

    return InstalledBuild(
        name=name or f"blender-{version}-linux-x64",
        path=Path("/tmp") / f"blender-{version}",
        version=version,
        executable=Path("/tmp") / f"blender-{version}" / "blender",
        fork="",
    )


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class MigrateViewTests(SettingsIsolated, unittest.TestCase):
    """La vista de migración de addons (services/blender_config por debajo)."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def setUp(self):
        # Otros tests de la suite dejan el idioma en español; aquí se comprueban
        # textos, así que hay que fijarlo (y restaurarlo al terminar).
        import i18n

        self.addCleanup(i18n.set_language, i18n.get_language())
        i18n.set_language("en")

    def _view(self):
        from ui.widgets.migrate import MigrateView

        view = MigrateView()
        view.set_system("linux", "x86_64")
        return view

    def _settle(self, view):
        """Deja que el layout se asiente (sin pantalla no basta un processEvents)."""
        self.app.processEvents()
        view.repaint()
        self.app.processEvents()

    @staticmethod
    def _run_now(target=None, *args, **kwargs):
        """Sustituto síncrono de ``threading.Thread`` (ver un test que lo usa).

        La vista lanza su trabajo en hilos; en un test, un hilo que emite una
        señal cuando el test ya terminó abre un diálogo modal que cuelga el
        suite. Aquí se ejecuta el trabajo en el momento, dejando el estado
        cerrado y determinista.
        """
        if target is not None:
            target()

        class _Immediate:
            def start(self):
                pass

        return _Immediate()

    @staticmethod
    def _prefs_answer(view, handler_name, payload):
        """Simula que el hilo de lectura/escritura de ajustes responde.

        La respuesta lleva el ticket de un trabajo recién abierto (como haría
        el hilo de verdad); sin él la vista la ignora por venir "de fuera".
        """
        from ui.widgets.migrate.common import _entry_key

        if handler_name == "_on_prefs_loaded":
            ticket = view._prefs_read.start(_entry_key(view.source_entry))
        else:
            ticket = view._prefs_write.start(payload.get("version"))
        getattr(view, handler_name)(dict(payload, ticket=ticket))

    def _with_configs(self, view, configs, source=None, target=None):
        """Inyecta instaladas y redirige ``config_for`` a carpetas temporales.

        ``configs`` mapea versión completa -> ``BlenderConfig``. Se pueden fijar
        los desplegables con ``source``/``target`` (versión completa).
        """
        from model.build import minor_of
        from services import blender_config as bc
        from services import blender_addons as baddons
        from services import blender_snapshots as bsnap

        def fake_config_for(version, platform, env=None, fork=""):
            series = minor_of(version)
            for key, value in configs.items():
                if minor_of(key) == series:
                    return value
            raise KeyError(series)

        patch = mock.patch.object(bc, "config_for", side_effect=fake_config_for)
        patch.start()
        self.addCleanup(patch.stop)
        # Más nueva primero, como las devuelve el escaneo real.
        entries = [_fake_installed(v) for v in sorted(configs, reverse=True)]
        view.set_installed(entries)
        if source:
            view.source_combo.setCurrentIndex(
                [e.version for e in entries].index(source))
        if target:
            view.target_combo.setCurrentIndex(
                [e.version for e in entries].index(target))

    def _config(self, tmp, version, manifest=None, legacy=None):
        """Monta una config de mentira; ``legacy`` admite una o varias tuplas."""
        from services import blender_config as bc

        series = ".".join(version.split(".")[:2])
        root = Path(tmp) / series
        config = bc.BlenderConfig(series, root, "linux", root / "config",
                                  root / "scripts", root / "extensions")
        if manifest:
            folder = config.extensions_dir / "user_default" / manifest[0]
            folder.mkdir(parents=True, exist_ok=True)
            (folder / bc.MANIFEST_NAME).write_text(manifest[1], encoding="utf-8")
        for item in ([legacy] if legacy and isinstance(legacy[0], str)
                     else (legacy or [])):
            folder = config.addons_dir / item[0]
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "__init__.py").write_text(item[1], encoding="utf-8")
        return config

    def test_necesita_dos_versiones(self):
        from unittest import mock as _mock

        view = self._view()
        with _mock.patch("services.blender_runner.is_running",
                         return_value=False):
            view.set_installed([_fake_installed("5.2.1")])
        self.assertEqual(len(view._choices), 1)
        self.assertFalse(view.copy_btn.isEnabled())
        self.assertIn("two", view.summary.text().lower())

    def test_origen_y_destino_no_pueden_coincidir(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            view = self._view()
            # Dos versiones instaladas, pero apuntando la misma en los dos lados.
            configs = {"4.5.0": self._config(tmp, "4.5.0"),
                       "5.3.0": self._config(tmp, "5.3.0")}
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False):
                self._with_configs(view, configs, source="5.3.0",
                                   target="5.3.0")
            self.assertTrue(view.source_combo.isVisible() or True)
            self.assertFalse(view.copy_btn.isEnabled())
            self.assertIn("different", view.summary.text().lower())

    def test_planifica_y_marca_incompatibles(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(
                tmp, "4.5.0",
                manifest=("MatPlus",
                          'id = "MatPlus"\nname = "MatPlus"\nversion = "1.3.0"\n'
                          'blender_version_min = "4.5.0"\n'),
                legacy=("oldtool",
                        'bl_info = {"name": "OldTool", "version": (0, 9), '
                        '"blender": (6, 0, 0)}\n'))
            target = self._config(tmp, "5.3.0")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False):
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
            by_name = {plan.addon.name: plan for plan in view.plans}
            self.assertEqual(by_name["MatPlus"].status, "ok")
            self.assertTrue(by_name["MatPlus"].selected)
            self.assertEqual(by_name["OldTool"].status, "blocked")
            self.assertFalse(by_name["OldTool"].selected)
            # Las filas del tablero se pintan una por addon.
            self.assertEqual(len(view._rows), len(view.plans))

    def test_no_migra_los_bloqueados_aunque_los_marquen(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(
                tmp, "4.5.0",
                legacy=("oldtool",
                        'bl_info = {"name": "OldTool", "version": (0, 9), '
                        '"blender": (6, 0, 0)}\n'))
            target = self._config(tmp, "5.3.0")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"):
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view._select_all(True)
                view.apply()
            self.assertFalse(list(target.addons_dir.iterdir())
                             if target.addons_dir.is_dir() else [])

    def test_copiar_escribe_en_el_destino(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(
                tmp, "4.5.0",
                legacy=("miaddon",
                        'bl_info = {"name": "MiAddon", "version": (1, 0), '
                        '"blender": (4, 0, 0)}\n'))
            target = self._config(tmp, "5.3.0")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"):
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                # Sin lectura del origen, ``was_enabled`` es False, así que no
                # se activa nada (no se lanza un Blender de verdad en tests).
                view.apply()
            self.assertTrue((target.addons_dir / "miaddon" / "__init__.py")
                            .is_file())

    def test_solo_activa_los_que_estaban_activos_en_origen(self):
        """El estado activado se imita: no se activa todo por migrar."""
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(
                tmp, "4.5.0",
                legacy=[("activo",
                         'bl_info = {"name": "Activo", "version": (1, 0), '
                         '"blender": (4, 0, 0)}\n'),
                        ("apagado",
                         'bl_info = {"name": "Apagado", "version": (1, 0), '
                         '"blender": (4, 0, 0)}\n')])
            target = self._config(tmp, "5.3.0")
            exe = Path(tmp) / "blender"
            exe.write_text("", encoding="utf-8")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"), \
                    _mock.patch("ui.widgets.dialogs.confirm",
                                return_value=True), \
                    _mock.patch("ui.widgets.migrate.addons_tab.threading") as threading, \
                    _mock.patch("services.blender_runner.enable_addons",
                                return_value={"enabled": ["activo"],
                                              "errors": []}) as enable:
                # El hilo de activación se ejecuta síncrono: así el test no deja
                # una señal diferida que luego abra un diálogo modal.
                threading.Thread.side_effect = self._run_now
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view.target_entry = _fake_installed("5.3.0")
                view.target_entry.executable = exe
                # Solo "activo" lo estaba en origen.
                view.source_enabled = {"activo"}
                view._rebuild_plan()
                view.apply()
                # Se llama a enable_addons una vez, solo con el activo.
                self.assertTrue(enable.called)
                _, modules = enable.call_args[0][:2]
                self.assertEqual(modules, ["activo"])

    def test_no_intenta_activar_si_el_ejecutable_no_existe(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(
                tmp, "4.5.0",
                legacy=("miaddon",
                        'bl_info = {"name": "MiAddon", "version": (1, 0), '
                        '"blender": (4, 0, 0)}\n'))
            target = self._config(tmp, "5.3.0")
            view = self._view()
            # El ejecutable de la instalada falsa no existe en disco.
            with _mock.patch("services.blender_runner.enable_addons") \
                    as enable, \
                    _mock.patch("ui.widgets.dialogs.show_info"), \
                    _mock.patch("services.blender_runner.is_running",
                                return_value=False):
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view.apply()
            enable.assert_not_called()

    def test_preferencias_copia_userpref_con_backup(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5.0")
            target = self._config(tmp, "5.3.0")
            source.config_dir.mkdir(parents=True)
            target.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            (target.config_dir / "userpref.blend").write_bytes(b"VIEJO")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"):
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view.copy_preference_files()
            self.assertEqual((target.config_dir / "userpref.blend").read_bytes(),
                             b"NUEVO")
            backups = [p for p in target.config_dir.iterdir()
                       if "blendermanager-bak" in p.name]
            self.assertEqual(len(backups), 1)

    def test_startup_no_se_migra_sin_marcarlo(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5.0")
            target = self._config(tmp, "5.3.0")
            source.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"P")
            (source.config_dir / "startup.blend").write_bytes(b"S")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"):
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view.copy_preference_files()
            self.assertFalse((target.config_dir / "startup.blend").exists())
            self.assertTrue((target.config_dir / "userpref.blend").exists())

    def test_preferencias_avisa_si_blender_esta_abierto(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5.0")
            target = self._config(tmp, "5.3.0")
            source.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"P")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=True), \
                    _mock.patch("ui.widgets.dialogs.show_info") as info:
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view.copy_preference_files()
            # No debe copiar y debe avisar.
            self.assertTrue(info.called)
            self.assertFalse((target.config_dir / "userpref.blend").exists())

    def test_undo_restaura_y_oculta_el_boton(self):
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5.0")
            target = self._config(tmp, "5.3.0")
            source.config_dir.mkdir(parents=True)
            target.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"NUEVO")
            (target.config_dir / "userpref.blend").write_bytes(b"VIEJO")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"), \
                    _mock.patch("ui.widgets.dialogs.confirm",
                                return_value=True):
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view.copy_preference_files()
                # La vista no se ha mostrado (offscreen), así que ``isVisible``
                # siempre es False: se comprueba el flag propio del widget.
                self.assertFalse(view.undo_btn.isHidden())
                view.undo()
            self.assertEqual((target.config_dir / "userpref.blend").read_bytes(),
                             b"VIEJO")
            self.assertTrue(view.undo_btn.isHidden())

    def test_preferencias_en_detalle_carga_y_aplica(self):
        from unittest import mock as _mock

        from services import blender_prefs as bprefs

        user = bprefs.PreferenceDump(
            values={"view.ui_scale": 1.25, "view.show_developer_ui": True,
                    "inputs.navigation_mode": "FLY"},
            meta={"view.ui_scale": {"name": "Resolution Scale",
                                    "description": "Size of the UI"}})
        factory = bprefs.PreferenceDump(
            values={"view.ui_scale": 1.0, "view.show_developer_ui": False,
                    "inputs.navigation_mode": "WALK"})
        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "4.5.0")
            target = self._config(tmp, "5.3.0")
            source.config_dir.mkdir(parents=True)
            # El ejecutable tiene que existir para que write_detail_prefs no
            # corte antes de llamar al servicio (que va mockeado).
            exe = Path(tmp) / "blender"
            exe.write_text("", encoding="utf-8")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"), \
                    _mock.patch("services.blender_prefs.read_preferences",
                                side_effect=[user, factory]), \
                    _mock.patch("services.blender_runner.enabled_addons",
                                return_value=["matplus"]), \
                    _mock.patch("services.blender_prefs.write_preferences",
                                return_value={"applied": ["view.ui_scale"],
                                              "errors": []}) as apply:
                self._with_configs(view, {"4.5.0": source, "5.3.0": target},
                                   source="4.5.0", target="5.3.0")
                view.source_entry = _fake_installed("4.5.0")
                view.target_entry = _fake_installed("5.3.0")
                view.target_entry.executable = exe
                view.source_entry.executable = exe
                # La lectura es automática y corre en un hilo; aquí se simula
                # que ya llegó el resultado de las preferencias.
                self._prefs_answer(
                    view, "_on_prefs_loaded", {"user": user, "factory": factory})
                self.assertEqual(len(view.detail_prefs), 3)
                # La fila enseña el nombre de Blender y explica la clave.
                fila = next(row for row in view.detail_checks
                            if row.pref.path == "view.ui_scale")
                self.assertEqual(fila.check.text(), "Resolution Scale")
                self.assertIn("Size of the UI", fila.toolTip())
                self.assertIn("view.ui_scale", fila.toolTip())
                # Sin ajustes de addons, la casilla de activarlos no se ve.
                self.assertTrue(view.enable_addons_check.isHidden())
                view.detail_prefs[0].selected = True
                view.write_detail_prefs()
                self._prefs_answer(
                    view, "_on_prefs_applied",
                    {"result": {"applied": ["view.ui_scale"], "errors": []},
                     "version": "5.3.0"})
            self.assertTrue(apply.called)

    def test_leer_de_nuevo_sin_cambios_limpia_la_lista(self):
        """Si la lectura nueva dice "de fábrica", no pueden quedar casillas.

        Tras restablecer y pulsar "Leer de nuevo", el texto cambiaba a "no hay
        cambios" pero las casillas de la lectura anterior seguían ahí (parecía
        que aún detectaba aquellos ajustes).
        """
        from services import blender_prefs as bprefs

        view = self._view()
        view.detail_prefs = [bprefs.Preference("view.ui_scale", 1.25)]
        view._fill_detail_rows()
        self.assertFalse(view.detail_scroll.isHidden())
        self._prefs_answer(
            view, "_on_prefs_loaded", {
            "user": bprefs.PreferenceDump(values={"view.ui_scale": 1.0}),
            "factory": bprefs.PreferenceDump(values={"view.ui_scale": 1.0})})
        self.assertEqual(view.detail_prefs, [])
        self.assertEqual(view.detail_checks, [])
        self.assertTrue(view.detail_scroll.isHidden())

    def test_si_no_se_pudo_leer_se_dice_el_motivo(self):
        """"No se pudieron leer" a secas no ayuda: el motivo va en el tooltip."""
        from services import blender_prefs as bprefs

        view = self._view()
        self._prefs_answer(
            view, "_on_prefs_loaded", {
            "user": bprefs.PreferenceDump(error="timeout after 180s"),
            "factory": bprefs.PreferenceDump(values={})})
        self.assertIn("Could not read", view.detail_status.text())
        self.assertIn("timeout", view.detail_status.toolTip())

    def test_los_ajustes_de_addons_ofrecen_activarlos_y_explican_el_fallo(self):
        """Con claves ``addons.*`` aparece la casilla, y el error se agrupa.

        "Ya no existe en esta versión" era mentira para un addon que solo
        está desactivado: ahora se dice qué hacer (copiarlo desde Add-ons).
        """
        from unittest import mock as _mock

        from services import blender_prefs as bprefs

        view = self._view()
        self._prefs_answer(
            view, "_on_prefs_loaded", {
            "user": bprefs.PreferenceDump(
                values={"addons.hurricane.cache_format": "USD"}),
            "factory": bprefs.PreferenceDump(values={})})
        self.assertFalse(view.enable_addons_check.isHidden())
        with _mock.patch("ui.widgets.dialogs.show_info") as info:
            self._prefs_answer(
                view, "_on_prefs_applied", {"result": {
                "applied": [],
                "errors": [{"path": "addons.hurricane.cache_format",
                            "error": bprefs.ADDON_NOT_ENABLED},
                           {"path": "view.gone", "error": "unknown property"}],
                "addons_enabled": [{"module": "cycles", "enabled": True}]},
                "version": "5.3.0"})
        texto = info.call_args[0][2]
        self.assertIn("Add-ons tab", texto)
        self.assertIn("addons.hurricane.cache_format", texto)
        self.assertIn("no longer exist", texto)
        self.assertIn("view.gone", texto)
        self.assertIn("cycles", texto)

    def test_las_claves_cambiadas_van_en_un_scroll_con_tope(self):
        """Con cientos de ajustes la tarjeta no puede crecer sin límite.

        Sin un tope, la lista empuja los botones fuera de la pantalla y no se
        llega a leer: las claves van en un scroll de alto máximo y los botones,
        fuera de él.
        """
        from services import blender_prefs as bprefs
        from ui.widgets.migrate.common import DETAIL_SCROLL_HEIGHT

        view = self._view()
        # Sin filas no se enseña el área de scroll.
        self.assertTrue(view.detail_scroll.isHidden())
        view.detail_prefs = [
            bprefs.Preference(path=f"view.clave_{i}", value=i)
            for i in range(40)]
        view._fill_detail_rows()
        self.assertFalse(view.detail_scroll.isHidden())
        self.assertEqual(view.detail_scroll.maximumHeight(),
                         DETAIL_SCROLL_HEIGHT)
        # Las filas están dentro del scroll, no sueltas en la tarjeta.
        body = view.detail_scroll.widget()
        self.assertIs(body.layout(), view.detail_rows)
        self.assertGreater(view.detail_rows.count(), 40)
        # Al vaciarlas, el área se esconde otra vez.
        view._clear_detail_rows()
        self.assertTrue(view.detail_scroll.isHidden())

    def test_los_paneles_con_scroll_se_pueden_estirar(self):
        """El asa estira la lista y, con ella, la tarjeta."""
        from services import blender_prefs as bprefs

        view = self._view()
        view.resize(1100, 900)
        view.tabs.setCurrentIndex(1)
        view.show()
        self.app.processEvents()
        view.detail_prefs = [
            bprefs.Preference(f"view.clave_{i}", i) for i in range(20)]
        view._fill_detail_rows()
        self._settle(view)
        self.assertIsNotNone(view.detail_grip)
        card_before = view.detail_card.height()
        scroll_before = view.detail_scroll.height()
        view._resize_detail(120)
        self._settle(view)
        self.assertGreater(view.detail_card.height(), card_before)
        self.assertGreater(view.detail_scroll.height(), scroll_before)

        # Al encoger del todo, la tarjeta se encoge con la lista y los botones
        # siguen dentro, por debajo de ella.
        view._resize_detail(-10000)
        self._settle(view)
        self.assertLess(view.detail_card.height(), card_before)
        self.assertLessEqual(view.detail_apply_btn.geometry().bottom(),
                             view.detail_card.height())

    def test_la_lista_de_claves_no_se_queda_en_una_rendija(self):
        """Encogida del todo siguen cabiendo las filas enteras del suelo."""
        from services import blender_prefs as bprefs
        from ui.widgets.migrate.common import DETAIL_MIN_ROWS

        view = self._view()
        view.resize(1100, 900)
        view.tabs.setCurrentIndex(1)
        view.show()
        self.app.processEvents()
        view.detail_prefs = [
            bprefs.Preference(f"view.clave_{i}", i) for i in range(40)]
        view._fill_detail_rows()
        view._resize_detail(-10000)
        self._settle(view)
        fila = view.detail_checks[0].sizeHint().height()
        self.assertGreaterEqual(view.detail_scroll.height(),
                                DETAIL_MIN_ROWS * fila)
        # Y los botones siguen por debajo de la lista, no encima.
        self.assertGreaterEqual(
            view.detail_apply_btn.geometry().top(),
            view.detail_scroll.geometry().bottom())

    def test_la_tarjeta_de_detalle_encoge_con_la_ventana(self):
        """Preferencias en detalle no empuja su texto fuera de la ventana.

        Con la casilla larga de addons metida en la fila de botones, el ancho
        mínimo de la tarjeta superaba el de la ventana y el panel se salía por
        la derecha (textos y botón "Apply" recortados). La casilla va en su
        propia línea, así que la página no puede pedir más que su vista.
        """
        from services import blender_prefs as bprefs

        view = self._view()
        view.resize(820, 700)
        view.tabs.setCurrentIndex(1)
        view.show()
        self.app.processEvents()
        view.detail_prefs = [
            bprefs.Preference("addons.miaddon.clave", "x", name="Un ajuste",
                              description="Un ajuste del addon.")]
        view._fill_detail_rows()
        view._show_detail_buttons(True)
        # La casilla se ve (hay ajustes de addons): es el caso más ancho.
        self.assertFalse(view.enable_addons_check.isHidden())
        self._settle(view)
        host = view.tabs.currentWidget()
        page = view._page_of(host)
        # Sin scroll horizontal, si la página pidiera más que su vista se
        # recortaría por la derecha.
        self.assertLessEqual(page.width(), host.viewport().width())

    def test_la_lista_de_guardados_mide_igual_sea_cual_sea_la_ventana(self):
        """Valores de fábrica sigue las mismas reglas que Preferencias.

        Alto fijo (ni el layout la aplasta cuando la ventana se queda corta, ni
        abre encogida), suelo de una tarjeta entera y lienzo hundido con barra.
        """
        from services import blender_snapshots as bsnap
        from unittest import mock as _mock
        from services import blender_config as bc

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.2.0")
            for i in range(4):
                config.config_dir.mkdir(parents=True, exist_ok=True)
                (config.config_dir / "userpref.blend").write_bytes(b"X" * (i + 1))
                bsnap.set_config_aside(config, label=f"v5.2.{i}")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False),                     _mock.patch.object(view, "_factory_config",
                                       return_value=config):
                view.tabs.setCurrentIndex(2)
                alturas = []
                for alto in (760, 600, 500):
                    view.resize(950, alto)
                    view.show()
                    view._fill_factory()
                    self._settle(view)
                    alturas.append(view.snapshot_scroll.height())
                # El mismo alto en las tres: lo que sobra lo scrollea la página.
                self.assertEqual(len(set(alturas)), 1)
                self.assertEqual(view.snapshot_scroll.property("scrolling"),
                                 "true")
                # Encogida del todo cabe una tarjeta entera.
                fila = next(iter(view._snapshot_widgets.values()))
                view._resize_snapshots(-10000)
                self._settle(view)
                self.assertGreaterEqual(view.snapshot_scroll.height(),
                                        fila.height())

    def test_estirar_un_panel_empuja_a_los_de_abajo(self):
        """La página de preferencias va en scroll: nada se queda por detrás."""
        from PySide6.QtWidgets import QScrollArea
        from services import blender_prefs as bprefs

        view = self._view()
        view.resize(1100, 900)
        view.tabs.setCurrentIndex(1)
        view.show()
        self.app.processEvents()
        host = view.tabs.currentWidget()
        self.assertIsInstance(host, QScrollArea)
        view.detail_prefs = [
            bprefs.Preference(f"view.clave_{i}", i) for i in range(40)]
        view._fill_detail_rows()
        self._settle(view)
        page = view._page_of(host)
        before = page.height()
        view._resize_detail(400)
        self._settle(view)
        # La página crece con la tarjeta y aparece la barra de la página.
        self.assertGreater(page.height(), before)
        self.assertGreater(host.verticalScrollBar().maximum(), 0)

    def test_el_asa_vive_en_la_esquina_de_la_tarjeta(self):
        """El asa va en la esquina del panel gris, no en la fila de botones.

        Si vuelve a un layout queda a los márgenes de la tarjeta (se lee como
        un botón más) y, peor, puede solaparse con el botón de acento.
        """
        view = self._view()
        view.resize(900, 700)
        view.show()
        self.app.processEvents()
        for grip, inside in ((view.detail_grip, view.detail_scroll),
                             (view.snapshot_grip, view.snapshot_scroll)):
            card = inside.parent()
            while card.objectName() != "SettingsCard":
                card = card.parent()
            self.assertIs(grip.parent(), card)
            # Pegada al borde de la tarjeta, no a los 16/14 px del layout.
            self.assertLessEqual(card.width() - grip.geometry().right(), 6)
            self.assertLessEqual(card.height() - grip.geometry().bottom(), 6)
            # Y sin nada del layout por debajo.
            margins = card.layout().contentsMargins()
            self.assertGreaterEqual(margins.bottom(), grip.height())

    def test_el_lienzo_se_hunde_solo_cuando_hay_barra(self):
        """Con barra de scroll, el fondo de la lista baja al gris oscuro."""
        from services import blender_prefs as bprefs

        view = self._view()
        view.resize(1100, 900)
        # La pestaña tiene que estar abierta: una página oculta no se coloca,
        # así que el scroll no tendría alto y nunca saldría la barra.
        view.tabs.setCurrentIndex(1)
        view.show()
        self.app.processEvents()
        def llenar(count):
            view.detail_prefs = [
                bprefs.Preference(f"view.clave_{i}", i) for i in range(count)]
            view._fill_detail_rows()
            # El rango de la barra no se recalcula hasta que el layout se
            # asienta; sin el repaint, sin pantalla, se queda a cero.
            self._settle(view)

        llenar(1)
        self.assertEqual(view.detail_scroll.property("scrolling"), "false")
        llenar(80)
        self.assertEqual(view.detail_scroll.property("scrolling"), "true")
        self.assertEqual(
            view.detail_scroll.widget().property("scrolling"), "true")

    def test_reset_fabrica_aparta_la_config_y_permite_recuperar(self):
        from services import blender_snapshots as bsnap
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            target = self._config(tmp, "5.3.0")
            target.config_dir.mkdir(parents=True)
            (target.config_dir / "userpref.blend").write_bytes(b"MIO")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch("ui.widgets.dialogs.show_info"), \
                    _mock.patch("ui.widgets.dialogs.confirm",
                                return_value=True):
                # La pestaña de fábrica usa su propio selector de una versión.
                entry = _fake_installed("5.3.0")
                view.platform = "linux"
                view._choices = [entry]
                view.factory_combo.addItem("5.3.0")
                view.factory_combo.setCurrentIndex(0)
                with _mock.patch.object(view, "_factory_config",
                                        return_value=target):
                    view.reset_to_factory()
                    snapshots = bsnap.snapshots_with_settings(target)
                    self.assertEqual(len(snapshots), 1)
                    self.assertFalse(target.config_dir.exists())
                    view.restore_factory_snapshot()
            self.assertEqual((target.config_dir / "userpref.blend").read_bytes(),
                             b"MIO")

    def test_el_aviso_de_sin_cambios_nombra_la_version_y_el_destino(self):
        """Sin cambios + instantáneas: decir de qué versión y dónde se recuperan.

        La pestaña "Valores de fábrica" tiene su propio selector de versión, así
        que el aviso tiene que decir que se elija esa versión **allí**; antes
        decía «recupéralos en la pestaña Valores de fábrica» a secas y el
        usuario la abría con otra versión y no encontraba nada.
        """
        from services import blender_snapshots as bsnap
        from services import blender_config as bc

        with tempfile.TemporaryDirectory() as tmp:
            source = self._config(tmp, "5.2.2")
            source.config_dir.mkdir(parents=True)
            (source.config_dir / "userpref.blend").write_bytes(b"MIO")
            bsnap.set_config_aside(source, label="v5.2.2")
            view = self._view()
            view.source_cfg = source
            view.source_entry = _fake_installed("5.2.2")
            message = view._no_changes_message()
        self.assertIn("5.2.2", message)
        self.assertIn("Factory settings", message)
        # Ni el nombre crudo de la carpeta ni una fecha inventada.
        self.assertNotIn("config-", message)

    def test_fabrica_tiene_su_propio_selector_de_una_version(self):
        """La pestaña de fábrica no usa la barra origen → destino.

        Solo hay que elegir una versión: si allí aparecen "Desde" y "Hacia" se
        cree que la operación usa los dos y no se sabe cuál manda.
        """
        from services import blender_snapshots as bsnap
        from unittest import mock as _mock

        from services import blender_config as bc

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.2.2")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"MIO")
            bsnap.set_config_aside(config, label="v5.2.2")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch.object(
                        view, "_factory_config", return_value=config):
                view._choices = [_fake_installed("5.2.2")]
                view.factory_combo.addItem("5.2.2")
                view.factory_combo.setCurrentIndex(0)
                view.tabs.setCurrentIndex(2)
                # La cabecera que se ve es la de una sola versión…
                self.assertIs(view.factory_header.parentWidget(),
                              view.factory_page)
                self.assertIsNot(view.header.parentWidget(),
                                 view.factory_page)
                # …y las pestañas de migración sí llevan la de dos. La de
                # add-ons va dentro de un scroll, así que la cabecera cuelga
                # de la página de dentro (``_page_of``), no del área.
                view.tabs.setCurrentIndex(0)
                self.assertIs(view.header.parentWidget(),
                              view._page_of(view.tabs.widget(0)))
                # Funciona con una sola versión instalada (no exige dos): hay
                # una fila de guardado y su botón de restaurar está activo.
                row = next(iter(view._snapshot_widgets.values()))
                self.assertTrue(row.restore_btn.isEnabled())

    def test_el_selector_de_fabrica_ensena_solo_la_version(self):
        """En fábrica el nombre de la carpeta repetía el número.

        «Blender 5.3.0-alpha · blender-5.3.0-alpha» con una etiqueta que ya dice
        "Versión" sobra; la ruta de la config está justo debajo.
        """
        from unittest import mock as _mock

        with tempfile.TemporaryDirectory() as tmp:
            configs = {"4.5.0": self._config(tmp, "4.5.0"),
                       "5.3.0": self._config(tmp, "5.3.0")}
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False):
                self._with_configs(view, configs, source="4.5.0",
                                   target="5.3.0")
            self.assertEqual(view.factory_combo.itemText(0), "5.3.0")
            # Las de migración sí llevan la build, para distinguirla.
            self.assertIn("blender-5.3.0", view.target_combo.itemText(0))

    def test_gestor_de_guardados_lista_analiza_y_borra(self):
        """El gestor: una fila por guardado, con análisis y borrado individual."""
        from services import blender_snapshots as bsnap
        from unittest import mock as _mock
        from services import blender_config as bc
        from services import blender_prefs as bprefs

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.2.2")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"MIO")
            first = bsnap.set_config_aside(config, label="v5.2.2")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"OTRA")
            second = bsnap.set_config_aside(config, label="v5.2.2")
            newest = bsnap.snapshots_with_settings(config)[0]
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch.object(
                        view, "_factory_config", return_value=config):
                view._choices = [_fake_installed("5.2.2")]
                view.factory_combo.addItem("5.2.2")
                view.factory_combo.setCurrentIndex(0)
                view.tabs.setCurrentIndex(2)
                self.assertEqual(len(view._snapshot_widgets), 2)
                # La más nueva se marca como reciente.
                self.assertIn("recent",
                              view._snapshot_widgets[newest].badge.text().lower())
                # Llega el análisis: se cuentan y se marca el más completo.
                view._on_snapshots_analyzed({
                    "ticket": view._snapshot_job.start(("", "5.2.2")),
                    "version": "5.2.2",
                    "results": {
                        str(first): [bprefs.Preference("view.ui_scale", 1.1)],
                        str(second): [
                            bprefs.Preference("view.ui_scale", 1.2),
                            bprefs.Preference("view.show_developer_ui", True)],
                    },
                    "live": 0,
                })
                most = max(view._snapshot_widgets,
                           key=lambda path: len(
                               view._snapshot_widgets[path].analysis or []))
                self.assertIn("complete",
                              view._snapshot_widgets[most].badge.text().lower())
                self.assertTrue(
                    view._snapshot_widgets[most].details_btn.isEnabled())
                # Borrar uno (con confirmación) deja el otro.
                with _mock.patch("ui.widgets.dialogs.confirm",
                                 return_value=True):
                    view.delete_snapshot(first)
                self.assertEqual(len(view._snapshot_widgets), 1)
                self.assertNotIn(first, view._snapshot_widgets)

    def test_el_detalle_de_un_guardado_lista_sus_ajustes(self):
        """"Ver ajustes" abre un diálogo con las claves de ese guardado."""
        from services import blender_snapshots as bsnap
        from unittest import mock as _mock
        from services import blender_config as bc
        from services import blender_prefs as bprefs

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(tmp, "5.2.2")
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"MIO")
            snap = bsnap.set_config_aside(config, label="v5.2.2")
            view = self._view()
            with _mock.patch("services.blender_runner.is_running",
                             return_value=False), \
                    _mock.patch.object(
                        view, "_factory_config", return_value=config):
                view._choices = [_fake_installed("5.2.2")]
                view.factory_combo.addItem("5.2.2")
                view.factory_combo.setCurrentIndex(0)
                view.tabs.setCurrentIndex(2)
                view._on_snapshots_analyzed({
                    "ticket": view._snapshot_job.start(("", "5.2.2")),
                    "version": "5.2.2",
                    "results": {
                        str(snap): [
                            bprefs.Preference("view.ui_scale", 1.25)]},
                    "live": 0,
                })
                with _mock.patch("ui.widgets.dialogs.AppDialog.exec",
                                 return_value=0) as run:
                    view.show_snapshot_details(snap)
            self.assertTrue(run.called)

    def test_la_retencion_de_guardados_se_puede_cambiar(self):
        from unittest import mock as _mock

        view = self._view()
        view.set_snapshot_keep(10)
        self.assertEqual(view.snapshot_keep, 10)
        self.assertEqual(view.snapshot_keep_combo.currentData(), 10)
        seen = []
        view.snapshot_keep_changed.connect(seen.append)
        with _mock.patch.object(view, "_factory_config", return_value=None):
            view.snapshot_keep_combo.setCurrentIndex(
                view.snapshot_keep_combo.findData(3))
        self.assertEqual(seen, [3])
        self.assertEqual(view.snapshot_keep, 3)

    def test_bloquea_con_cualquier_blender_abierto(self):
        """Escribir la config se bloquea si hay CUALQUIER Blender abierto.

        Dos builds de la misma serie comparten carpeta, así que un Blender
        abierto que no es el elegido también pisaría el cambio al cerrarse.
        """
        from unittest import mock as _mock

        view = self._view()
        view.target_entry = _fake_installed("5.3.0")
        with _mock.patch("services.blender_runner.is_running",
                         return_value=True) as running, \
                _mock.patch("ui.widgets.dialogs.show_info") as info:
            self.assertTrue(view._blocked_by_running())
        running.assert_called_with(None)
        self.assertTrue(info.called)

    def test_review_explica_como_revisar(self):
        """El amarillo "Review" tiene que decir qué hacer, no solo el motivo."""
        from services import blender_addons as baddons
        from services import blender_config as bc
        from ui.widgets.migrate.common import _status_tooltip

        addon = baddons.Addon(kind="legacy", module="x", name="X", version="1.0",
                         min_version="", max_version="", path=Path("/tmp/x"))
        unknown = baddons.AddonPlan(addon, bc.WARN, bc.REASON_UNKNOWN_VERSION,
                               Path("/tmp/x"))
        tip = _status_tooltip(unknown)
        self.assertIn("minimum version", tip)      # el motivo
        self.assertIn("test", tip.lower())          # y cómo comprobarlo

        wheels = baddons.AddonPlan(addon, bc.WARN, bc.REASON_WHEEL_ABI,
                              Path("/tmp/x"), detail="numpy",
                              target_python="3.13")
        tip = _status_tooltip(wheels)
        self.assertIn("enable it", tip.lower())
        # El motivo nombra al paquete culpable y al Python del destino: el
        # texto genérico de antes se leía como si comparase los dos Blender.
        self.assertIn("numpy", tip)
        self.assertIn("3.13", tip)

    def test_compatible_explica_que_se_copia(self):
        from services import blender_addons as baddons
        from services import blender_config as bc
        from ui.widgets.migrate.common import _status_tooltip

        addon = baddons.Addon(kind="legacy", module="x", name="X", version="1.0",
                         min_version="4.0.0", max_version="", path=Path("/tmp/x"))
        plan = baddons.AddonPlan(addon, bc.OK, "", Path("/tmp/x"))
        # La verde también lleva tooltip: dice que no hay nada que revisar.
        self.assertIn("destination version", _status_tooltip(plan))

    def test_las_pestanas_usan_los_canales_del_servicio(self):
        """Las claves de la barra son las de ``services.channels``.

        La interfaz pone las etiquetas traducidas, pero las claves tienen que
        ser exactamente las mismas: si aquí apareciera una que los filtros no
        entienden, la pestaña se vería y no filtraría nada.
        """
        from services import channels
        from ui.widgets import main_window

        self.assertEqual(tuple(key for key, _ in main_window.CHANNELS),
                         channels.CHANNELS)

    def test_los_filtros_viven_con_las_listas(self):
        """La fila de filtros solo está en Local y Nube.

        Antes era global y en Migración/Ajustes se reservaba ocultando su
        contenido para que la interfaz no diera un salto de 44 px; ahora vive
        con las listas, así que en Migración desaparece con ellas.
        """
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        # Migración es experimental: hay que activarla para poder entrar.
        window.experimental_switch.setChecked(True)
        window.migrate_view.set_installed([])
        window.set_view("store")
        self.assertTrue(window.filters.isVisibleTo(window))
        self.assertFalse(window.grid_btn.isHidden())

        window.set_view("migrate")
        self.assertEqual(window.view, "migrate")
        self.assertFalse(window.filters.isVisibleTo(window))
        self.assertFalse(window._zoom_enabled())

        # Y al volver, los filtros reaparecen.
        window.set_view("store")
        self.assertTrue(window.filters.isVisibleTo(window))


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class AddonsViewTests(SettingsIsolated, unittest.TestCase):
    """La vista de gestión de addons."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def setUp(self):
        import i18n

        self.addCleanup(i18n.set_language, i18n.get_language())
        i18n.set_language("en")

    def _state(self, kind="legacy", module="mi_addon", name="Mi Addon",
               enabled=False, path="/tmp/mi_addon"):
        from services import blender_addons as baddons
        from services import addons as ap

        addon = ap.baddons.Addon(kind=kind, module=module, name=name, version="1.0",
                            min_version="", max_version="", path=Path(path))
        return ap.AddonState(addon, enabled=enabled)

    def test_lista_y_filtra_por_tipo(self):
        from ui.widgets.addons import AddonsView

        view = AddonsView()
        view.addons = [self._state(kind="legacy"),
                       self._state(kind="extension", module="bl_ext.repo.otro",
                                   name="Otro")]
        view._fill_rows()
        self.assertEqual(view.rows.count(), 2)
        view.type_combo.setCurrentIndex(
            view.type_combo.findData("extension"))
        self.assertEqual(view.rows.count(), 1)

    def test_el_nombre_del_addon_se_ve(self):
        """El título no puede quedarse a 0 px (``ElidedLabel`` sin estirar).

        Un ``ElidedLabel`` tiene ancho mínimo 0; si va en un ``HBox`` con un
        ``addStretch`` se queda sin sitio y el nombre desaparece.
        """
        from PySide6.QtWidgets import QLabel

        from ui.widgets.addons import _AddonRow

        row = _AddonRow(self._state(name="Hurricane"), lambda *a: None,
                        lambda *a: None, lambda *a: None)
        row.resize(700, 60)
        row.show()
        QApplication.processEvents()
        titles = [label for label in row.findChildren(QLabel)
                  if label.objectName() == "Title"]
        self.assertTrue(titles)
        self.assertGreater(titles[0].width(), 0)

    def test_los_tres_puntos_abren_el_menu_en_su_sitio(self):
        """El botón de los tres puntos no puede pasarle un bool al menú.

        ``clicked`` emite su estado ``checked``. Conectado a un método que
        acepta la posición, ese bool llegaba como ``position`` y acababa en
        ``menu.exec(False)`` -> ``TypeError``: el menú salía con el clic
        derecho (que pasa un QPoint de verdad) y fallaba con el botón.
        """
        from unittest import mock as _mock

        from PySide6.QtCore import QPoint

        from ui.widgets import addons as addons_ui

        row = addons_ui._AddonRow(self._state(name="Hurricane"), lambda *a: None,
                                  lambda *a: None, lambda *a: None)
        row.resize(700, 60)
        row.show()
        QApplication.processEvents()

        class MenuFalso:
            """Un menú que apunta con qué se le llama en vez de abrirse.

            Abrir un ``QMenu`` de verdad bloquea la suite: ``exec`` es modal.
            """

            def __init__(self):
                self.position = "(sin llamar)"

            def addAction(self, *args):
                return _mock.MagicMock()

            def exec(self, position=None):
                self.position = position

        # Por el botón.
        menu = MenuFalso()
        with _mock.patch.object(addons_ui, "card_menu", return_value=menu):
            row._menu_btn.click()
        self.assertIsInstance(menu.position, QPoint)

        # Y por el clic derecho, que ya funcionaba: los dos caminos valen.
        otro = MenuFalso()
        with _mock.patch.object(addons_ui, "card_menu", return_value=otro):
            row._show_menu(QPoint(10, 20))
        self.assertEqual(otro.position, QPoint(10, 20))

    def test_set_installed_no_arranca_blender(self):
        from unittest import mock as _mock

        from model.build import InstalledBuild
        from ui.widgets.addons import AddonsView

        entry = InstalledBuild(name="b", path=Path("/tmp/b"), version="5.3.0",
                               executable=Path("/tmp/b/blender"))
        view = AddonsView()
        with _mock.patch.object(view, "read") as read:
            view.set_installed([entry])
        read.assert_not_called()
        self.assertEqual(view.version_combo.count(), 1)

    def test_bloquea_si_hay_blender_abierto(self):
        from unittest import mock as _mock

        from ui.widgets.addons import AddonsView

        view = AddonsView()
        with _mock.patch("services.blender_runner.is_running",
                         return_value=True), \
                _mock.patch("ui.widgets.dialogs.show_info") as info:
            self.assertTrue(view._blocked())
        self.assertTrue(info.called)


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class RecentViewTests(SettingsIsolated, unittest.TestCase):
    """La vista de ficheros recientes."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def setUp(self):
        # Otros tests dejan el idioma en español; aquí se comprueban textos.
        import i18n

        self.addCleanup(i18n.set_language, i18n.get_language())
        i18n.set_language("en")

    def _labels(self, view):
        return [view.body.itemAt(i).widget() for i in range(view.body.count())]

    def test_sin_instaladas_avisa(self):
        from ui.widgets.recent import RecentView

        view = RecentView()
        view.set_installed([])
        texts = [getattr(w, "text", lambda: "")() for w in self._labels(view)]
        self.assertIn("No installed Blender versions.", texts)

    def test_muestra_un_grupo_por_serie(self):
        from unittest import mock as _mock

        from model.build import InstalledBuild
        from services import recent as rp
        from ui.widgets.recent import RecentView, _RecentRow

        entry = InstalledBuild(name="b", path=Path("/tmp/b"), version="5.2.2",
                               executable=Path("/tmp/b/blender"))
        group = rp.RecentGroup(series="5.2", version="5.2.2",
                               files=[rp.RecentFile(Path("/tmp/a.blend"))])
        view = RecentView()
        view.set_system("linux")
        with _mock.patch("ui.widgets.recent.recent_service.grouped",
                         return_value=[group]):
            view.set_installed([entry])
        rows = [w for w in self._labels(view) if isinstance(w, _RecentRow)]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].path, Path("/tmp/a.blend"))

    def test_abrir_pasa_por_el_lanzador_de_la_ventana(self):
        """Recientes no lanza por su cuenta: pide a MainWindow que abra.

        Así el fichero se abre con los argumentos de Ajustes y la consola de
        esa versión, igual que desde las tarjetas (antes se los saltaba).
        """
        from unittest import mock as _mock

        from model.build import InstalledBuild
        from ui.widgets.recent import RecentView

        entry = InstalledBuild(name="b", path=Path("/tmp/b"), version="5.2.2",
                               executable=Path("/tmp/b/blender"))
        opener = _mock.Mock(return_value=True)
        view = RecentView(open_file=opener)
        mensajes = []
        view.status_message.connect(mensajes.append)
        view._open(Path("/tmp/a.blend"), entry)
        opener.assert_called_once_with(entry, Path("/tmp/a.blend"))
        self.assertTrue(any("a.blend" in m for m in mensajes))
        # Si no arrancó, no se anuncia que se está abriendo.
        opener.return_value = False
        mensajes.clear()
        view._open(Path("/tmp/a.blend"), entry)
        self.assertEqual(mensajes, [])

    def test_la_ventana_abre_recientes_con_argumentos_y_consola(self):
        """``launch_installed`` con fichero: argumentos de Ajustes + consola."""
        from unittest import mock as _mock

        from model.build import InstalledBuild
        from ui.widgets.main_window import MainWindow

        window = MainWindow()
        window.launch_args = "--debug"
        entry = InstalledBuild(name="b", path=Path("/tmp/b"), version="5.2.2",
                               executable=Path("/tmp/b/blender"))
        with _mock.patch.object(window.launcher, "launch") as launch, \
                _mock.patch.object(window, "_console_state", return_value=True), \
                _mock.patch("ui.widgets.downloads.launcher.terminal_available",
                            return_value=True):
            self.assertTrue(window.launch_installed(entry, Path("/tmp/a.blend")))
        launch.assert_called_once_with(
            entry.executable, args=["--debug", "/tmp/a.blend"], console=True,
            env={})

    def test_los_ficheros_que_ya_no_estan_se_ven_apagados(self):
        from unittest import mock as _mock

        from model.build import InstalledBuild
        from services import recent as rp
        from ui.widgets.recent import RecentView, _RecentRow

        entry = InstalledBuild(name="b", path=Path("/tmp/b"), version="5.2.2",
                               executable=Path("/tmp/b/blender"))
        group = rp.RecentGroup(series="5.2", version="5.2.2", files=[
            rp.RecentFile(Path("/tmp/a.blend")),
            rp.RecentFile(Path("/tmp/borrado.blend"), missing=True)])
        opener = _mock.Mock(return_value=True)
        view = RecentView(open_file=opener)
        with _mock.patch("ui.widgets.recent.recent_service.grouped",
                         return_value=[group]):
            view.set_installed([entry])
        rows = [w for w in self._labels(view) if isinstance(w, _RecentRow)]
        vivo, muerto = rows
        self.assertEqual(vivo.property("missing"), "false")
        self.assertEqual(muerto.property("missing"), "true")
        self.assertIn("no longer", muerto.toolTip())
        self.assertFalse(muerto._menu_btn.isEnabled())
        self.assertTrue(muerto.missing)
        # Y hay botón para releer la lista.
        self.assertTrue(view.refresh_btn.toolTip())


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class SingleInstanceTests(SettingsIsolated, unittest.TestCase):
    """Abrir la app dos veces no crea una segunda ventana.

    El nombre del socket depende de la carpeta de configuración, y el mixin la
    pone en un temporal por test, así que cada test usa un socket distinto y no
    se pisan entre ellos.
    """

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        from ui.single_instance import SingleInstance

        self._servers = []
        self.addCleanup(self._close_servers)
        self._SingleInstance = SingleInstance

    def _close_servers(self):
        for single in self._servers:
            single.close()

    def _single(self):
        single = self._SingleInstance()
        self._servers.append(single)
        return single

    def test_sin_otra_instancia_no_avisa(self):
        self.assertFalse(self._single().notify_existing())

    def test_la_segunda_apertura_trae_la_ventana_al_frente(self):
        from PySide6.QtTest import QTest

        primera = self._single()
        traida = []
        self.assertTrue(primera.listen(lambda: traida.append(True)))
        # La segunda instancia detecta a la primera y se va (aquí solo importa
        # que el aviso llegue y dispare el callback).
        self.assertTrue(self._single().notify_existing())
        for _ in range(50):
            QTest.qWait(10)
            if traida:
                break
        self.assertEqual(traida, [True])

    def test_escuchar_sobre_un_socket_ya_existente(self):
        """Un socket huérfano de un cierre brusco no bloquea el arranque."""
        self.assertTrue(self._single().listen(lambda: None))
        self.assertTrue(self._single().listen(lambda: None))



class _CapturedThreads:
    """Sustituto de ``threading`` que guarda los hilos en vez de lanzarlos.

    Así el test decide **en qué orden** terminan los trabajos en segundo plano,
    que es justo lo que provocaba los fallos de las banderas compartidas.
    """

    def __init__(self):
        self.targets = []
        outer = self

        class _Thread:
            def __init__(self, target=None, daemon=None, **_kwargs):
                outer.targets.append(target)

            def start(self):
                pass

        self.Thread = _Thread

    def run(self, index):
        """Ejecuta ahora el trabajo número ``index`` (emite su señal)."""
        self.targets[index]()


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class MigrateBackgroundJobTests(SettingsIsolated, unittest.TestCase):
    """Los trabajos en segundo plano de Migración no se pisan entre sí."""

    app = None
    _view = MigrateViewTests._view
    _with_configs = MigrateViewTests._with_configs
    _config = MigrateViewTests._config

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        from ui import fonts, qss

        fonts.load()
        cls.app.setStyleSheet(qss.build_qss())

    def setUp(self):
        super().setUp()
        import i18n

        self.addCleanup(i18n.set_language, i18n.get_language())
        i18n.set_language("en")

    def _prepared_view(self, tmp, versions, source, target, threads):
        """Vista con instaladas cuyo ejecutable existe (sin hilos de verdad)."""
        exe = Path(tmp) / "blender"
        exe.write_text("", encoding="utf-8")
        view = self._view()
        configs = {version: self._config(tmp, version) for version in versions}
        for patch in (mock.patch("services.blender_runner.is_running",
                                 return_value=False),
                      mock.patch("ui.widgets.migrate.prefs_tab.threading",
                                 threads)):
            patch.start()
            self.addCleanup(patch.stop)
        self._with_configs(view, configs)
        for entry in view._choices:
            entry.executable = exe
        versions = [entry.version for entry in view._choices]
        view.target_combo.setCurrentIndex(versions.index(target))
        view.source_combo.setCurrentIndex(versions.index(source))
        # Con los ejecutables ya en su sitio, la lectura automática del origen
        # arranca (la de ``_with_configs`` se cortó por no tener ejecutable).
        view._fill_from_installed()
        return view

    @staticmethod
    def _dumps():
        from services import blender_prefs as bprefs

        user = bprefs.PreferenceDump(values={"view.ui_scale": 1.25})
        factory = bprefs.PreferenceDump(values={"view.ui_scale": 1.0})
        return user, factory

    def test_la_escritura_no_se_come_el_resultado_de_la_lectura(self):
        """Leer y escribir a la vez: cada respuesta llega a su sitio.

        Compartían la bandera ``_prefs_waiting``: si la escritura terminaba
        antes, se "gastaba" la bandera y la lectura se tiraba, así que la lista
        se quedaba en "Reading settings..." para siempre.
        """
        threads = _CapturedThreads()
        with tempfile.TemporaryDirectory() as tmp:
            view = self._prepared_view(tmp, ["4.5.0", "5.3.0"], "4.5.0",
                                       "5.3.0", threads)
            reads = len(threads.targets)
            self.assertGreaterEqual(reads, 1)
            from services import blender_prefs as bprefs

            view.detail_prefs = [bprefs.Preference("view.ui_scale", 1.25)]
            view.detail_prefs[0].selected = True
            view.write_detail_prefs()
            self.assertEqual(len(threads.targets), reads + 1)
            user = bprefs.PreferenceDump(
                values={"inputs.navigation_mode": "FLY"})
            factory = bprefs.PreferenceDump(
                values={"inputs.navigation_mode": "WALK"})
            with mock.patch("services.blender_prefs.write_preferences",
                            return_value={"applied": ["view.ui_scale"],
                                          "errors": []}), \
                    mock.patch("ui.widgets.dialogs.show_info") as info:
                threads.run(reads)          # termina antes la escritura
            self.assertTrue(info.called)
            with mock.patch("services.blender_runner.enabled_addons",
                            return_value=[]), \
                    mock.patch("services.blender_prefs.read_preferences",
                               side_effect=[user, factory]):
                threads.run(reads - 1)      # y luego la lectura
            # Lo que se ve es lo recién leído, no lo que se acababa de escribir.
            self.assertEqual([p.path for p in view.detail_prefs],
                             ["inputs.navigation_mode"])
            self.assertIn("1 settings changed", view.detail_status.text())

    def test_cambiar_el_origen_mientras_se_lee_relee_el_nuevo(self):
        """El resultado de la versión vieja no se pinta sobre la nueva."""
        threads = _CapturedThreads()
        with tempfile.TemporaryDirectory() as tmp:
            view = self._prepared_view(tmp, ["4.4.0", "4.5.0", "5.3.0"],
                                       "4.5.0", "5.3.0", threads)
            first = len(threads.targets) - 1
            versions = [entry.version for entry in view._choices]
            view.source_combo.setCurrentIndex(versions.index("4.4.0"))
            # Mientras la primera lectura sigue en marcha no se lanza otra.
            self.assertEqual(len(threads.targets), first + 1)
            user, factory = self._dumps()
            with mock.patch("services.blender_runner.enabled_addons",
                            return_value=["viejo"]), \
                    mock.patch("services.blender_prefs.read_preferences",
                               side_effect=[user, factory]):
                threads.run(first)
            # Lo leído era de la 4.5: no se aplica y se relee la 4.4.
            self.assertEqual(view.detail_prefs, [])
            self.assertNotIn("viejo", view.source_enabled)
            self.assertEqual(len(threads.targets), first + 2)
            self.assertIn("Reading", view.detail_status.text())
            with mock.patch("services.blender_runner.enabled_addons",
                            return_value=["nuevo"]), \
                    mock.patch("services.blender_prefs.read_preferences",
                               side_effect=[user, factory]):
                threads.run(first + 1)
            self.assertEqual(len(view.detail_prefs), 1)
            self.assertIn("nuevo", view.source_enabled)

    def test_el_analisis_de_otra_version_se_relanza(self):
        """Cambiar de versión mientras se analizan los guardados no los deja a medias.

        El análisis de la versión anterior llegaba, se descartaba por ser de
        otra versión y ya nadie volvía a lanzar el de la nueva: sus filas se
        quedaban en "Analyzing..." para siempre.
        """
        from services import blender_snapshots as bsnap

        threads = _CapturedThreads()
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "blender"
            exe.write_text("", encoding="utf-8")
            configs = {}
            for version in ("4.5.0", "5.2.2"):
                config = self._config(tmp, version)
                config.config_dir.mkdir(parents=True)
                (config.config_dir / "userpref.blend").write_bytes(b"MIO")
                bsnap.set_config_aside(config, label=f"v{version}")
                configs[version] = config
            view = self._view()
            for patch in (mock.patch("services.blender_runner.is_running",
                                     return_value=False),
                          mock.patch("ui.widgets.migrate.factory_tab.threading",
                                     threads)):
                patch.start()
                self.addCleanup(patch.stop)
            self._with_configs(view, configs)
            for entry in view._choices:
                entry.executable = exe
            versions = [entry.version for entry in view._choices]
            view.factory_combo.setCurrentIndex(versions.index("4.5.0"))
            view.tabs.setCurrentIndex(2)
            self.assertEqual(len(threads.targets), 1)
            row = next(iter(view._snapshot_widgets.values()))
            self.assertIn("Analyzing", row.analysis_label.text())
            view.factory_combo.setCurrentIndex(versions.index("5.2.2"))
            # Mientras la primera sigue en marcha no se arranca otro Blender.
            self.assertEqual(len(threads.targets), 1)
            with mock.patch("services.blender_prefs.read_preferences"), \
                    mock.patch("services.blender_prefs.snapshot_preferences"):
                threads.run(0)
            # Lo que llegó era de la 4.5: se tira y se analiza la 5.2.
            self.assertEqual(len(threads.targets), 2)
            row = next(iter(view._snapshot_widgets.values()))
            self.assertIn("Analyzing", row.analysis_label.text())

    def test_una_senal_sin_ticket_no_abre_nada(self):
        """Una señal pública emitida desde fuera no dispara diálogos."""
        view = self._view()
        with mock.patch("ui.widgets.dialogs.show_info") as info:
            view.prefs_applied.emit({"result": {"applied": []},
                                     "version": "5.3.0"})
            view.activation_done.emit({"result": {}, "version": "5.3.0"})
        self.assertFalse(info.called)



@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class ForkSeriesChoiceTests(SettingsIsolated, unittest.TestCase):
    """Los selectores de versión distinguen el fork, no solo la serie."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _installed():
        from model.build import InstalledBuild

        def entry(name, version, fork=""):
            return InstalledBuild(name=name, path=Path("/tmp") / name,
                                  version=version,
                                  executable=Path("/tmp") / name / "blender",
                                  fork=fork)

        return [entry("blender-4.5.0", "4.5.0"),
                entry("blender-4.5.1", "4.5.1"),
                entry("bforartists-4.5.0", "4.5.0", fork="bforartists")]

    def test_migracion_no_junta_bforartists_con_blender(self):
        """Bforartists 4.5 y Blender 4.5 son dos opciones (configs distintas)."""
        from ui.widgets.migrate import MigrateView

        view = MigrateView()
        with mock.patch("services.blender_runner.is_running",
                        return_value=False):
            view.set_installed(self._installed())
            forks = sorted((entry.fork, entry.version)
                           for entry in view._choices)
            # Las dos Blender 4.5 comparten config: queda la más nueva.
            self.assertEqual(forks, [("", "4.5.1"), ("bforartists", "4.5.0")])
            bfa = [entry.fork for entry in view._choices].index("bforartists")
            view.factory_combo.setCurrentIndex(bfa)
            self.assertIn("Bforartists", view.factory_combo.currentText())
            # Al volver a recibir la lista, se queda elegida la Bforartists,
            # no la Blender de la misma versión.
            view.set_installed(list(reversed(self._installed())))
            self.assertEqual(view._factory_entry().fork, "bforartists")

    def test_addons_no_junta_bforartists_con_blender(self):
        from ui.widgets.addons import AddonsView

        view = AddonsView()
        view.set_installed(self._installed())
        self.assertEqual(view.version_combo.count(), 2)
        bfa = [entry.fork for entry in view._choices].index("bforartists")
        view.version_combo.setCurrentIndex(bfa)
        with mock.patch.object(view, "read"):
            view.set_installed(list(reversed(self._installed())))
        self.assertEqual(view._selected().fork, "bforartists")
        self.assertIn("Bforartists", view.version_combo.currentText())


@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class FactorySafetyTests(SettingsIsolated, unittest.TestCase):
    """La pestaña de fábrica no borra ni promete nada sin decirlo."""

    app = None
    _view = MigrateViewTests._view
    _config = MigrateViewTests._config

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        import i18n

        self.addCleanup(i18n.set_language, i18n.get_language())
        i18n.set_language("en")

    def _factory_view(self, tmp, copies=4):
        """Vista en la pestaña de fábrica con ``copies`` guardados de la 5.2."""
        from services import blender_snapshots as bsnap

        config = self._config(tmp, "5.2.2")
        for index in range(copies):
            config.config_dir.mkdir(parents=True)
            (config.config_dir / "userpref.blend").write_bytes(b"MIO")
            bsnap.set_config_aside(config, label=f"v5.2.{index}")
        view = self._view()
        for patch in (mock.patch("services.blender_runner.is_running",
                                 return_value=False),
                      mock.patch.object(view, "_factory_config",
                                        return_value=config)):
            patch.start()
            self.addCleanup(patch.stop)
        view._choices = [_fake_installed("5.2.2")]
        view.factory_combo.addItem("5.2.2")
        view.factory_combo.setCurrentIndex(0)
        view.set_snapshot_keep(10)
        return view, config

    def test_bajar_el_tope_pregunta_cuantos_se_borran(self):
        """Elegir "como mucho 3" con 4 guardados borra uno: se pregunta antes."""
        from services import blender_snapshots as bsnap

        with tempfile.TemporaryDirectory() as tmp:
            view, config = self._factory_view(tmp, copies=4)
            seen = []
            view.snapshot_keep_changed.connect(seen.append)
            three = view.snapshot_keep_combo.findData(3)
            with mock.patch("ui.widgets.dialogs.confirm",
                            return_value=False) as ask:
                view.snapshot_keep_combo.setCurrentIndex(three)
            self.assertIn("1", ask.call_args[0][2])
            self.assertTrue(ask.call_args.kwargs.get("danger"))
            # Dijo que no: nada borrado y el desplegable vuelve a 10.
            self.assertEqual(len(bsnap.all_snapshots(config)), 4)
            self.assertEqual(view.snapshot_keep_combo.currentData(), 10)
            self.assertEqual(seen, [])
            with mock.patch("ui.widgets.dialogs.confirm", return_value=True):
                view.snapshot_keep_combo.setCurrentIndex(three)
            self.assertEqual(len(bsnap.all_snapshots(config)), 3)
            self.assertEqual(seen, [3])

    def test_subir_el_tope_no_pregunta(self):
        with tempfile.TemporaryDirectory() as tmp:
            view, _ = self._factory_view(tmp, copies=2)
            with mock.patch("ui.widgets.dialogs.confirm") as ask:
                view.snapshot_keep_combo.setCurrentIndex(
                    view.snapshot_keep_combo.findData(3))
            ask.assert_not_called()
            self.assertEqual(view.snapshot_keep, 3)


    def test_si_no_se_puede_borrar_un_guardado_lo_dice(self):
        """``delete_snapshot`` devuelve False: no se anuncia "borrado"."""
        with tempfile.TemporaryDirectory() as tmp:
            view, config = self._factory_view(tmp, copies=1)
            from services import blender_snapshots as bsnap

            snapshot = bsnap.all_snapshots(config)[0]
            said = []
            view.status_message.connect(said.append)
            with mock.patch("ui.widgets.dialogs.confirm", return_value=True), \
                    mock.patch("services.blender_snapshots.delete_snapshot",
                               return_value=False), \
                    mock.patch("ui.widgets.dialogs.show_error") as error:
                view.delete_snapshot(snapshot)
            self.assertTrue(error.called)
            self.assertEqual(said, [])

    def test_un_fallo_al_apartar_la_config_se_ensena(self):
        """Un ``OSError`` al restablecer no puede perderse en el slot."""
        with tempfile.TemporaryDirectory() as tmp:
            view, config = self._factory_view(tmp, copies=0)
            config.config_dir.mkdir(parents=True)
            with mock.patch("ui.widgets.dialogs.confirm", return_value=True), \
                    mock.patch("services.blender_snapshots.set_config_aside",
                               side_effect=PermissionError("denied")), \
                    mock.patch("ui.widgets.dialogs.show_error") as error:
                view.reset_to_factory()
            self.assertIn("denied", error.call_args[0][2])

    def test_un_fallo_al_restaurar_se_ensena(self):
        with tempfile.TemporaryDirectory() as tmp:
            view, config = self._factory_view(tmp, copies=1)
            with mock.patch("ui.widgets.dialogs.confirm", return_value=True), \
                    mock.patch("services.blender_snapshots.restore_snapshot",
                               side_effect=OSError("disk full")), \
                    mock.patch("ui.widgets.dialogs.show_error") as error:
                view.restore_factory_snapshot()
            self.assertIn("disk full", error.call_args[0][2])



@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class ProgressDialogTextTests(unittest.TestCase):
    """El diálogo de progreso cuenta lo que pasa aunque nazca sin mensaje."""

    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_set_text_funciona_sin_mensaje_inicial(self):
        """Mover carpetas crea el diálogo con ``message=""`` y luego escribe.

        El cuerpo solo existía si había mensaje inicial, así que "Moviendo
        X..." no se veía nunca.
        """
        from ui.widgets.dialogs import ProgressDialog

        dialog = ProgressDialog(None, "Moving versions", "", primary_text="",
                                secondary_text="Cancel")
        self.assertTrue(dialog.body_label.isHidden())
        dialog.set_text("Moving blender-5.2 (1 of 2)")
        self.assertEqual(dialog.body_label.text(),
                         "Moving blender-5.2 (1 of 2)")
        self.assertFalse(dialog.body_label.isHidden())
        dialog.deleteLater()



@unittest.skipUnless(HAVE_QT, "PySide6 no instalado")
class AddonsViewStateTests(SettingsIsolated, unittest.TestCase):
    """El gestor de addons no miente sobre el estado ni se calla los fallos."""

    app = None
    _state = AddonsViewTests._state

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        import i18n

        self.addCleanup(i18n.set_language, i18n.get_language())
        i18n.set_language("en")

    def _view_with_row(self):
        from ui.widgets.addons import AddonsView

        view = AddonsView()
        view.entry = _fake_installed("5.2.2")
        view.addons = [self._state(enabled=False)]
        view._fill_rows()
        return view, view._rows[0]

    def test_el_interruptor_vuelve_si_hay_otra_accion_en_marcha(self):
        """Con otra operación en curso el clic no hace nada: el interruptor
        tampoco puede quedarse encendido como si lo hubiera hecho."""
        view, row = self._view_with_row()
        view._acting = True
        with mock.patch("ui.widgets.addons.threading") as threads:
            row.switch.setChecked(True)
        threads.Thread.assert_not_called()
        self.assertFalse(row.switch.isChecked())
        self.assertFalse(row.state.enabled)

    def test_un_fallo_al_leer_dice_el_motivo_y_lo_registra(self):
        view, _ = self._view_with_row()
        with mock.patch("ui.widgets.addons.download_log") as log:
            view._on_addons_loaded({"version": "5.2.2",
                                    "error": "Blender timed out"})
        self.assertIn("Could not read", view.status.text())
        self.assertIn("Blender timed out", view.status.toolTip())
        self.assertTrue(any("Blender timed out" in str(call)
                            for call in log.call_args_list))

    def test_si_no_se_sabe_si_blender_esta_abierto_se_registra(self):
        """Un fallo al detectar Blender no bloquea, pero deja rastro."""
        from ui.widgets.migrate import common

        with mock.patch("services.blender_runner.is_running",
                        side_effect=OSError("no /proc")), \
                mock.patch.object(common, "download_log") as log, \
                mock.patch("ui.widgets.dialogs.show_info") as info:
            self.assertFalse(common.blocked_by_running(None))
        info.assert_not_called()
        self.assertIn("no /proc", str(log.call_args))


if __name__ == "__main__":
    unittest.main()
