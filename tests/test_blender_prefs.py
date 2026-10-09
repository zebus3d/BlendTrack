"""Tests de las preferencias selectivas (``services/blender_prefs``).

Tres capas:

* la parte pura (filtrado, diff, agrupado), que decide **qué** se ofrece;
* los guiones que corren dentro de Blender, ejecutados de verdad con el ``bpy``
  de mentira de ``tests/fake_bpy.py`` (son Python normal salvo ``bpy``);
* el contrato de ``read_preferences``/``write_preferences`` con ``_run``
  parcheado: qué argumentos se le pasan a Blender y qué se devuelve cuando
  no contesta.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from services import blender_prefs as bp
from services import blender_runner

from tests import fake_bpy


class FilterTest(unittest.TestCase):
    def test_entorno_no_es_preferencia_del_usuario(self):
        self.assertTrue(bp.is_environment("system.gpu_backend"))
        self.assertTrue(bp.is_environment("system.audio_device"))
        self.assertTrue(bp.is_environment("filepaths.temp_directory"))
        self.assertFalse(bp.is_environment("view.ui_scale"))
        self.assertFalse(bp.is_environment("inputs.use_zoom_to_mouse"))
        # El dispositivo de Cycles (OPTIX/CUDA/HIP) es una preferencia del
        # usuario: se migra lo que él tenga configurado, no se descarta.
        self.assertFalse(
            bp.is_environment("addons.cycles.compute_device_type"))

    def test_lo_experimental_es_del_usuario_no_del_equipo(self):
        """Antes ``experimental.*`` y ``apps.*`` salían como "de tu equipo".

        Son elecciones del usuario (activar una función experimental, elegir
        una plantilla), así que se ofrecen marcadas como cualquier otra.
        """
        self.assertFalse(bp.is_environment("experimental.use_new_curves_tools"))
        self.assertFalse(bp.is_environment("apps.show_corner_split"))
        self.assertFalse(bp.is_environment("extensions.use_online_access"))
        self.assertIn("experimental", dict(bp.SECTIONS))
        self.assertIn("extensions", dict(bp.SECTIONS))

    def test_el_estado_interno_no_se_ofrece(self):
        """Las rutas son las que devuelve Blender, no las que uno supone.

        ``use_preferences_save`` cuelga de ``preferences`` y no de
        ``preferences.system``: escrita con sección no ignoraba nada, y el
        ajuste de "guardar preferencias automáticamente" se ofrecía como si
        fuera del usuario (comprobado contra Blender 5.2.2).
        """
        self.assertTrue(bp.is_ignored("active_section"))
        self.assertTrue(bp.is_ignored("is_dirty"))
        self.assertTrue(bp.is_ignored("use_preferences_save"))
        self.assertFalse(bp.is_ignored("view.ui_scale"))

    def test_seccion_de_una_clave_de_addon(self):
        pref = bp.Preference("addons.cycles.compute_device_type", "OPTIX")
        self.assertEqual(pref.section, "addons")
        self.assertEqual(pref.label, "compute_device_type")
        self.assertIn(("addons", "Add-ons"), bp.SECTIONS)

    def test_etiqueta_prefiere_el_nombre_de_blender(self):
        pref = bp.Preference("view.ui_scale", 1.25, name="Resolution Scale",
                             description="Size multiplier")
        self.assertEqual(pref.label, "Resolution Scale")
        self.assertEqual(pref.identifier, "ui_scale")

    def test_valor_legible(self):
        self.assertEqual(bp.format_value(0.1), "0.1")
        self.assertEqual(bp.format_value([0.5, 0.25, 1.0]), "0.5, 0.25, 1")
        self.assertEqual(bp.format_value(["LOCATION", "ROTATION"]),
                         "LOCATION, ROTATION")
        self.assertEqual(bp.format_value(True), "True")

    def test_seccion(self):
        self.assertEqual(bp.Preference("view.ui_scale", 1).section, "view")
        self.assertEqual(
            bp.Preference("inputs.use_zoom_to_mouse", True).section, "inputs")
        self.assertEqual(bp.Preference("", 0).section, "")


class DiffTest(unittest.TestCase):
    def setUp(self):
        self.factory = {
            "view.ui_scale": 1.0,
            "view.show_developer_ui": False,
            "view.color_thing": [0.1, 0.2, 0.3],
            "inputs.use_zoom_to_mouse": False,
            "system.gpu_backend": "OPENGL",
            "system.audio_device": "PulseAudio",
            "edit.key_insert_channels": ["LOCATION", "ROTATION"],
            "active_section": "INTERFACE",
        }

    def test_solo_devuelve_lo_que_cambio(self):
        user = dict(self.factory)
        user["view.ui_scale"] = 1.25
        user["view.show_developer_ui"] = True
        prefs = {p.path: p for p in bp.diff(user, self.factory)}
        self.assertEqual(set(prefs), {"view.ui_scale", "view.show_developer_ui"})
        self.assertEqual(prefs["view.ui_scale"].value, 1.25)

    def test_filtra_claves_del_equipo(self):
        user = dict(self.factory)
        user["system.gpu_backend"] = "VULKAN"
        self.assertEqual(bp.diff(user, self.factory), [])

    def test_las_listas_se_ofrecen(self):
        """Arrays (colores) y enum de varios valores ya son escribibles."""
        user = dict(self.factory)
        user["view.color_thing"] = [0.5, 0.5, 0.5]
        user["edit.key_insert_channels"] = ["LOCATION"]
        self.assertEqual([p.path for p in bp.diff(user, self.factory)],
                         ["edit.key_insert_channels", "view.color_thing"])

    def test_filtra_valores_que_no_se_pueden_escribir(self):
        user = dict(self.factory)
        user["view.ui_scale"] = {"raro": 1}
        user["view.show_developer_ui"] = [{"anidado": True}]
        self.assertEqual(bp.diff(user, self.factory), [])

    def test_el_estado_interno_no_cuenta_como_cambio(self):
        user = dict(self.factory)
        user["active_section"] = "SYSTEM"
        self.assertEqual(bp.changed(user, self.factory), [])

    def test_no_inventa_claves_que_no_estan_en_factory(self):
        user = dict(self.factory)
        user["view.nueva_de_esta_version"] = 3
        self.assertEqual(bp.diff(user, self.factory), [])

    def test_incluye_prefs_de_addons_que_no_estan_en_fabrica(self):
        """Un addon activado solo en la config del usuario no sale en fábrica.

        Sus preferencias faltan del volcado de fábrica (el addon no está
        activado allí) y antes se descartaban; ahora se ofrecen.
        """
        user = dict(self.factory)
        user["addons.hurricane.cache_format"] = "USD"
        self.assertIn("addons.hurricane.cache_format",
                      [pref.path for pref in bp.diff(user, self.factory)])

    def test_environment_preferences_va_aparte_y_sin_marcar(self):
        user = dict(self.factory)
        user["system.gpu_backend"] = "VULKAN"
        items = bp.environment_preferences(user, self.factory)
        self.assertEqual([p.path for p in items], ["system.gpu_backend"])
        self.assertFalse(items[0].selected)

    def test_meta_rellena_nombre_y_descripcion(self):
        user = dict(self.factory)
        user["view.ui_scale"] = 1.25
        meta = {"view.ui_scale": {"name": "Resolution Scale",
                                  "description": "Size multiplier",
                                  "type": "FLOAT", "subtype": "NONE"}}
        pref = bp.diff(user, self.factory, meta=meta)[0]
        self.assertEqual(pref.name, "Resolution Scale")
        self.assertEqual(pref.description, "Size multiplier")
        self.assertEqual(pref.type, "FLOAT")
        self.assertEqual(pref.label, "Resolution Scale")


class GroupTest(unittest.TestCase):
    def test_agrupa_por_seccion_en_orden(self):
        prefs = [
            bp.Preference("inputs.use_zoom_to_mouse", True),
            bp.Preference("view.ui_scale", 1.25),
            bp.Preference("view.show_developer_ui", True),
        ]
        grouped = bp.group_by_section(prefs)
        self.assertEqual([section for section, _ in grouped], ["view", "inputs"])
        self.assertEqual(len(grouped[0][1]), 2)

    def test_etiqueta_de_seccion(self):
        self.assertEqual(bp.section_label("view"), "Interface")
        self.assertEqual(bp.section_label("desconocida"), "desconocida")


def _run_script(script, bpy, addon_utils=None, env=None):
    """Ejecuta un guion de Blender con el ``bpy`` falso y devuelve su salida."""
    restore = fake_bpy.install(bpy, addon_utils)
    out = io.StringIO()
    try:
        with mock.patch.dict(os.environ, env or {}), \
                contextlib.redirect_stdout(out):
            exec(script, {"__name__": "__main__"})  # noqa: S102 - es el guion
    finally:
        restore()
    return out.getvalue()


class DumpScriptExecTest(unittest.TestCase):
    """El guion que vuelca las preferencias, ejecutado de verdad."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.preferences, self.view = fake_bpy.build_preferences()
        self.bpy = fake_bpy.FakeBpy(self.preferences, self.tmp.name)

    def _dump(self):
        out = _run_script(bp._DUMP_SCRIPT, self.bpy)
        payload = blender_runner._parse_marker(out, "BLENDERMANAGER_DUMP=")
        self.assertIsNotNone(payload, out)
        return payload

    def test_volcado_es_formato_2_con_values_y_meta(self):
        payload = self._dump()
        self.assertEqual(payload["format"], 2)
        self.assertEqual(payload["values"]["view.ui_scale"], 1.25)
        meta = payload["meta"]["view.ui_scale"]
        self.assertEqual(meta["name"], "Resolution Scale")
        self.assertEqual(meta["description"], "Size multiplier for the UI")
        self.assertEqual(meta["type"], "FLOAT")
        # La descripción llega aunque la propiedad no haya cambiado: el
        # filtrado es cosa de ``diff``, el guion vuelca todo.
        self.assertEqual(payload["meta"]["view.text_hint"]["description"],
                         "Where files go")

    def test_array_sale_como_lista(self):
        payload = self._dump()
        self.assertEqual(payload["values"]["view.color_thing"], [0.1, 0.2, 0.3])
        meta = payload["meta"]["view.color_thing"]
        self.assertTrue(meta["is_array"])
        self.assertEqual(meta["subtype"], "COLOR")

    def test_enum_de_varios_valores_sale_ordenado(self):
        payload = self._dump()
        self.assertEqual(payload["values"]["edit.key_insert_channels"],
                         ["LOCATION", "ROTATION", "SCALE"])
        self.assertTrue(payload["meta"]["edit.key_insert_channels"]["enum_flag"])

    def test_puntero_none_no_tumba_el_volcado(self):
        payload = self._dump()
        # ``system.broken`` es un POINTER a None: se salta y el resto sigue.
        self.assertEqual(payload["values"]["system.memory_cache_limit"], 8192)
        self.assertNotIn("error", payload)

    def test_salta_colecciones_solo_lectura_y_rna_type(self):
        payload = self._dump()
        claves = set(payload["values"])
        self.assertFalse(any(key.startswith("themes") for key in claves))
        self.assertNotIn("system.readonly_thing", claves)
        self.assertNotIn("view.rna_type", claves)

    def test_una_propiedad_rota_se_anota_y_sigue(self):
        payload = self._dump()
        rotas = [item["path"] for item in payload["skipped"]]
        self.assertIn("system.explodes", rotas)
        self.assertIn("system.memory_cache_limit", payload["values"])

    def test_prefs_de_addon_solo_si_cambiaron(self):
        payload = self._dump()
        self.assertEqual(payload["values"]["addons.cycles.compute_device_type"],
                         "OPTIX")
        # ``peer_memory`` vale lo que su RNA dice por defecto: no se ofrece.
        self.assertNotIn("addons.cycles.peer_memory", payload["values"])
        self.assertEqual(payload["addons"], ["cycles"])

    def test_los_floats_se_redondean_para_que_el_diff_sea_estable(self):
        self.view.ui_scale = 0.10000000149011612
        payload = self._dump()
        self.assertEqual(payload["values"]["view.ui_scale"], 0.1)

    def test_si_revienta_todo_el_marcador_sale_igual(self):
        self.bpy.context.preferences = None
        out = _run_script(bp._DUMP_SCRIPT, self.bpy)
        payload = blender_runner._parse_marker(out, "BLENDERMANAGER_DUMP=")
        self.assertIsNotNone(payload)
        self.assertIn("error", payload)


class ApplyScriptExecTest(unittest.TestCase):
    """El guion que escribe las claves, ejecutado de verdad."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.preferences, self.view = fake_bpy.build_preferences()
        self.bpy = fake_bpy.FakeBpy(self.preferences, self.tmp.name)

    def _apply(self, pairs, enable="0", addon_utils=None):
        pairs_file = os.path.join(self.tmp.name, "preferences.json")
        with open(pairs_file, "w", encoding="utf-8") as handle:
            json.dump(pairs, handle)
        env = {"BLENDERMANAGER_PREFS_FILE": pairs_file,
               "BLENDERMANAGER_ENABLE_ADDONS": enable}
        out = _run_script(bp._APPLY_SCRIPT, self.bpy, addon_utils, env)
        payload = blender_runner._parse_marker(out, "BLENDERMANAGER_RESULT=")
        self.assertIsNotNone(payload, out)
        return payload

    def test_escribe_escalares_arrays_y_sets_y_guarda_una_vez(self):
        result = self._apply([
            ["view.ui_scale", 2.0],
            ["view.color_thing", [0.5, 0.5, 0.5]],
            ["edit.key_insert_channels", ["LOCATION"]],
            ["addons.cycles.compute_device_type", "CUDA"],
        ])
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["applied"]), 4)
        self.assertEqual(self.view.ui_scale, 2.0)
        self.assertEqual(self.view.color_thing, (0.5, 0.5, 0.5))
        self.assertEqual(self.preferences.edit.key_insert_channels, {"LOCATION"})
        cycles = self.preferences.addons.get("cycles").preferences
        self.assertEqual(cycles.compute_device_type, "CUDA")
        self.assertEqual(self.bpy.saved, 1)

    def test_una_clave_que_no_existe_se_anota_y_las_demas_siguen(self):
        result = self._apply([["view.ya_no_existe", 1], ["view.ui_scale", 3.0]])
        self.assertEqual(result["applied"], ["view.ui_scale"])
        self.assertEqual(result["errors"][0]["path"], "view.ya_no_existe")
        self.assertEqual(result["errors"][0]["error"], "unknown property")

    def test_addon_no_activado_lo_dice_con_un_error_estable(self):
        result = self._apply([["addons.hurricane.cache_format", "USD"]])
        self.assertEqual(result["applied"], [])
        self.assertEqual(result["errors"][0]["error"], bp.ADDON_NOT_ENABLED)
        self.assertEqual(self.bpy.saved, 0)

    def test_con_permiso_activa_el_addon_y_escribe(self):
        hurricane = fake_bpy.FakeObj(
            [fake_bpy.FakeProp("cache_format", "ENUM", "VDB")],
            cache_format="VDB")

        def on_enable(module):
            self.preferences.addons.add(fake_bpy.FakeAddon(module, hurricane))

        addon_utils = fake_bpy.FakeAddonUtils(
            ["bl_ext.user_default.hurricane", "cycles"], on_enable)
        result = self._apply(
            [["addons.bl_ext.user_default.hurricane.cache_format", "USD"]],
            enable="1", addon_utils=addon_utils)
        self.assertEqual(result["errors"], [])
        self.assertEqual(addon_utils.enabled, ["bl_ext.user_default.hurricane"])
        self.assertEqual(result["addons_enabled"],
                         [{"module": "bl_ext.user_default.hurricane",
                           "enabled": True}])
        self.assertEqual(hurricane.cache_format, "USD")

    def test_si_el_addon_no_esta_instalado_no_se_puede_activar(self):
        addon_utils = fake_bpy.FakeAddonUtils(["cycles"])
        result = self._apply([["addons.hurricane.cache_format", "USD"]],
                             enable="1", addon_utils=addon_utils)
        self.assertEqual(result["errors"][0]["error"], bp.ADDON_NOT_ENABLED)
        self.assertEqual(addon_utils.enabled, [])


class ReadPreferencesTest(unittest.TestCase):
    """El contrato con ``blender_runner._run``: argumentos y fallos."""

    def _patch_run(self, returns):
        patcher = mock.patch.object(bp.blender_runner, "_run", return_value=returns)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def test_sin_ejecutable_no_lanza_blender(self):
        run = self._patch_run((0, "", ""))
        dump = bp.read_preferences("")
        self.assertFalse(dump.ok)
        run.assert_not_called()

    def test_pasa_factory_startup_y_la_config(self):
        run = self._patch_run((0, 'BLENDERMANAGER_DUMP={"format": 2, "values": {}}', ""))
        bp.read_preferences("/opt/blender", factory=True, config_dir="/tmp/cfg")
        args = run.call_args[0][1]
        self.assertIn("--background", args)
        self.assertIn("--factory-startup", args)
        self.assertEqual(run.call_args[1]["extra_env"],
                         {"BLENDER_USER_CONFIG": "/tmp/cfg"})

    def test_si_no_arranca_es_error_con_motivo(self):
        self._patch_run((None, "", "timeout after 180s"))
        dump = bp.read_preferences("/opt/blender")
        self.assertFalse(dump.ok)
        self.assertIn("timeout", dump.error)

    def test_salida_sin_marcador_es_error_no_vacio(self):
        self._patch_run((0, "ruido sin marcador", ""))
        dump = bp.read_preferences("/opt/blender")
        self.assertFalse(dump.ok)
        self.assertEqual(dump.values, {})

    def test_salida_con_codigo_de_error_dice_el_codigo(self):
        self._patch_run((11, "", "Segmentation fault"))
        dump = bp.read_preferences("/opt/blender")
        self.assertIn("11", dump.error)
        self.assertIn("Segmentation", dump.error)

    def test_formato_2_se_desempaqueta(self):
        payload = {"format": 2, "values": {"view.ui_scale": 1.25},
                   "meta": {"view.ui_scale": {"name": "Scale"}},
                   "skipped": [{"path": "x", "reason": "y"}],
                   "addons": ["cycles"]}
        self._patch_run((0, "BLENDERMANAGER_DUMP=" + json.dumps(payload), ""))
        dump = bp.read_preferences("/opt/blender")
        self.assertTrue(dump.ok)
        self.assertEqual(dump.values, {"view.ui_scale": 1.25})
        self.assertEqual(dump.meta["view.ui_scale"]["name"], "Scale")
        self.assertEqual(dump.skipped[0]["path"], "x")
        self.assertEqual(dump.addons, ["cycles"])

    def test_volcado_vacio_legitimo_es_ok(self):
        self._patch_run((0, 'BLENDERMANAGER_DUMP={"format": 2, "values": {}}', ""))
        dump = bp.read_preferences("/opt/blender")
        self.assertTrue(dump.ok)
        self.assertEqual(dump.values, {})

    def test_volcado_del_esquema_viejo_sigue_valiendo(self):
        self._patch_run((0, 'BLENDERMANAGER_DUMP={"view.ui_scale": 1.5}', ""))
        dump = bp.read_preferences("/opt/blender")
        self.assertTrue(dump.ok)
        self.assertEqual(dump.values, {"view.ui_scale": 1.5})

    def test_snapshot_se_lee_en_una_copia(self):
        """El guardado se abre copiado a un temporal: el original ni se toca."""
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = os.path.join(tmp, "config-2026")
            os.makedirs(snapshot)
            with open(os.path.join(snapshot, "userpref.blend"), "wb") as fh:
                fh.write(b"X")
            run = self._patch_run(
                (0, 'BLENDERMANAGER_DUMP={"format": 2, "values": {}}', ""))
            dump = bp.snapshot_preferences("/opt/blender", snapshot)
            self.assertTrue(dump.ok)
            used = run.call_args[1]["extra_env"]["BLENDER_USER_CONFIG"]
            self.assertNotEqual(os.path.realpath(used), os.path.realpath(snapshot))
            self.assertEqual(os.listdir(snapshot), ["userpref.blend"])

    def test_snapshot_que_no_existe_es_error(self):
        dump = bp.snapshot_preferences("/opt/blender", "/no/existe")
        self.assertFalse(dump.ok)


class WritePreferencesTest(unittest.TestCase):
    def test_manda_los_pares_en_un_fichero_y_el_permiso_de_activar(self):
        """Los pares van en un fichero: una variable de entorno tiene tope.

        En Windows no puede pasar de 32 767 caracteres, y unos cientos de
        claves con colores y listas se acercan.
        """
        recibido = {}

        def run(executable, args, extra_env=None, timeout=None):
            with open(extra_env["BLENDERMANAGER_PREFS_FILE"],
                      encoding="utf-8") as handle:
                recibido["pairs"] = json.load(handle)
            recibido["env"] = dict(extra_env)
            return (0, 'BLENDERMANAGER_RESULT={"applied": ["view.ui_scale"], '
                       '"errors": [], "addons_enabled": []}', "")

        muchas = [bp.Preference(f"view.clave_{index}", "x" * 100)
                  for index in range(400)]
        with mock.patch.object(bp.blender_runner, "_run", side_effect=run):
            result = bp.write_preferences(
                "/opt/blender", [bp.Preference("view.ui_scale", 2.0)] + muchas,
                enable_addons=True)
        self.assertEqual(recibido["pairs"][0], ["view.ui_scale", 2.0])
        self.assertEqual(len(recibido["pairs"]), 401)
        self.assertTrue(all(len(value) < 1000
                            for value in recibido["env"].values()))
        self.assertEqual(recibido["env"]["BLENDERMANAGER_ENABLE_ADDONS"], "1")
        self.assertEqual(result["applied"], ["view.ui_scale"])
        # El temporal no se queda en disco.
        self.assertFalse(os.path.exists(
            recibido["env"]["BLENDERMANAGER_PREFS_FILE"]))

    def test_sin_nada_que_escribir_no_arranca_blender(self):
        with mock.patch.object(bp.blender_runner, "_run") as run:
            result = bp.write_preferences("/opt/blender", [])
        run.assert_not_called()
        self.assertTrue(result["errors"])

    def test_si_blender_no_contesta_lo_dice(self):
        with mock.patch.object(bp.blender_runner, "_run",
                               return_value=(1, "", "boom")):
            result = bp.write_preferences(
                "/opt/blender", [("view.ui_scale", 2.0)])
        self.assertIn("no result", result["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
