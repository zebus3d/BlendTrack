import unittest
from datetime import datetime
from unittest import mock

import i18n


class I18nTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_english_passthrough(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tr("Download"), "Download")
        self.assertEqual(i18n.tr("Unknown key"), "Unknown key")

    def test_spanish_translation(self):
        i18n.set_language("es")
        self.assertEqual(i18n.tr("Download"), "Descargar")
        self.assertEqual(i18n.tr("Downloads"), "Descargas")

    def test_format_arguments(self):
        i18n.set_language("es")
        self.assertEqual(i18n.tr("Copied {count} add-ons.", count=3),
                         "Se copiaron 3 addons.")

    def test_broken_placeholder_does_not_raise(self):
        i18n.set_language("es")
        # Una traducción con una llave suelta no puede tumbar la interfaz.
        i18n._load("es")["__rota__"] = "Version {"
        try:
            self.assertEqual(i18n.tr("__rota__", version="1"), "Version {")
        finally:
            del i18n._load("es")["__rota__"]

    def test_auto_falls_back(self):
        i18n.set_language("auto")
        self.assertIn(i18n.get_language(), ("en", "es"))

    def test_cada_idioma_traduce_una_cadena_conocida(self):
        """Un fichero de idioma que no se carga saldría en inglés y nadie se entera."""
        for language in i18n.SUPPORTED:
            if language == "en":
                continue
            i18n.set_language(language)
            self.assertNotEqual(i18n.tr("Download"), "Download", language)
            self.assertEqual(i18n.tr("Unknown key"), "Unknown key", language)

    def test_la_fecha_usa_el_orden_de_cada_idioma(self):
        """Con barras el orden importa: 03/04/2025 son dos fechas distintas."""
        momento = datetime(2025, 3, 4, 17, 48)
        esperado = {
            "es": "04/03/2025",
            "pt_BR": "04/03/2025",
            "ru": "04/03/2025",
            "en": "03/04/2025",   # mes/día/año (EE. UU.)
            "ja": "2025/03/04",   # año/mes/día
            "zh": "2025/03/04",
        }
        for language, text in esperado.items():
            i18n.set_language(language)
            self.assertEqual(i18n.format_date(momento), text, language)

    def test_la_hora_va_donde_espera_cada_idioma(self):
        i18n.set_language("es")
        self.assertEqual(i18n.format_date(datetime(2025, 3, 4, 17, 48),
                                          with_time=True),
                         "04/03/2025 17:48")
        # En japonés y chino la hora va delante.
        i18n.set_language("ja")
        self.assertEqual(i18n.format_date(datetime(2025, 3, 4, 17, 48),
                                          with_time=True),
                         "17:48 2025/03/04")

    def test_la_fecha_no_depende_del_idioma_desconocido(self):
        # Un idioma no soportado cae a inglés (mes/día/año), pero la fecha sale
        # completa: no puede quedar a medias por un idioma raro.
        i18n.set_language("fr")
        self.assertEqual(i18n.get_language(), "en")
        self.assertEqual(i18n.format_date(datetime(2025, 3, 4)),
                         "03/04/2025")


if __name__ == "__main__":
    unittest.main()



class DetectLanguageTests(unittest.TestCase):
    """El idioma se deduce del locale del sistema."""

    def _detect(self, lang):
        env = {"LANG": lang}
        with mock.patch.dict(i18n.os.environ, env, clear=True), \
                mock.patch.object(i18n.locale, "getlocale",
                                  return_value=(None, None)):
            return i18n.detect_language()

    def test_portugues_de_brasil(self):
        """``pt_BR.UTF-8`` buscaba ``pt`` (que no existe) y salía en inglés."""
        self.assertEqual(self._detect("pt_BR.UTF-8"), "pt_BR")
        self.assertEqual(self._detect("pt-BR"), "pt_BR")

    def test_variante_regional_cae_al_idioma(self):
        self.assertEqual(self._detect("es_MX.UTF-8"), "es")
        self.assertEqual(self._detect("ru_RU.UTF-8"), "ru")
        self.assertEqual(self._detect("zh_CN.UTF-8"), "zh")

    def test_chino_tradicional_no_cae_al_simplificado(self):
        for value in ("zh_TW.UTF-8", "zh_HK", "zh_Hant_TW"):
            self.assertEqual(self._detect(value), "en", value)

    def test_idioma_desconocido_es_ingles(self):
        self.assertEqual(self._detect("fr_FR.UTF-8"), "en")
        self.assertEqual(self._detect("C"), "en")

class CoberturaTests(unittest.TestCase):
    """Que no se cuele una cadena sin traducir al español.

    Se leen las llamadas a ``tr("...")`` con ``ast``, que junta solo las
    cadenas partidas en varias líneas: buscarlas con ``grep`` no vale, porque
    en el código van troceadas y la clave es el texto ya unido (justo el error
    que hace que una traducción no enganche y el usuario vea inglés suelto).
    """

    def test_no_hay_claves_repetidas(self):
        """Una clave repetida pisa a la anterior en silencio.

        Pasó con "Restore", que tenía dos traducciones ("Restaurar" y
        "Recuperar") y ganaba la segunda; la primera era código muerto. Al
        cargar el JSON eso ya no se ve, así que se lee el fichero a mano.
        """
        import json
        from collections import Counter

        for language in i18n.SUPPORTED:
            path = i18n.LOCALE_DIR / f"{language}.json"
            if not path.exists():
                continue
            # ``object_pairs_hook=list`` devuelve los pares tal cual vienen en
            # el fichero, repetidos incluidos (un ``dict`` se quedaría solo con
            # el último).
            pares = json.loads(path.read_text(encoding="utf-8"),
                               object_pairs_hook=list)
            cuenta = Counter(key for key, _ in pares)
            repetidas = sorted(key for key, n in cuenta.items() if n > 1)
            self.assertEqual(repetidas, [], language)

    def test_no_quedan_claves_sin_usar(self):
        """Una clave que ya no usa nadie es texto que alguien traducirá en balde.

        Se recogen **todas** las cadenas literales de ``src/`` con ``ast``:
        así entran tanto las de ``tr("...")`` como las que viven en una tabla
        y se traducen por variable (los tooltips de canal, las etiquetas de
        tipo de carpeta, las secciones de preferencias...). ``ast`` junta las
        cadenas partidas en varias líneas, que es justo lo que un ``grep`` no
        sabe hacer y lo que hace inútil buscarlas a mano.
        """
        import ast
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src"
        en_el_codigo = set()
        for path in sorted(root.rglob("*.py")):
            if path.name == "i18n.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    en_el_codigo.add(node.value)
        huerfanas = [key for key in i18n._load("es")
                     if key not in en_el_codigo]
        self.assertEqual(huerfanas, [], "claves sin usar en src/")

    def test_todas_las_cadenas_tienen_traduccion(self):
        import ast
        from pathlib import Path

        spanish = i18n._load("es")
        missing = []
        root = Path(__file__).resolve().parents[1] / "src"
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "tr" and node.args):
                    continue
                first = node.args[0]
                if not (isinstance(first, ast.Constant)
                        and isinstance(first.value, str) and first.value):
                    continue
                if first.value not in spanish:
                    missing.append(f"{path.name}:{node.lineno} {first.value!r}")
        self.assertEqual(missing, [], "\n".join(missing))

    def test_todos_los_idiomas_comparten_las_mismas_claves(self):
        """Una clave que falta en un idioma sale en inglés sin avisar.

        El español es la referencia (de él se extraen las cadenas usadas): cada
        traducción tiene que traer **exactamente** las mismas claves, ni una de
        menos (eso sería texto en inglés suelto) ni de más (texto huérfano que
        alguien traducirá en balde).
        """
        import json

        reference = json.loads(
            (i18n.LOCALE_DIR / "es.json").read_text(encoding="utf-8"))
        for language in i18n.SUPPORTED:
            path = i18n.LOCALE_DIR / f"{language}.json"
            if language == "en" or not path.exists():
                continue
            table = json.loads(path.read_text(encoding="utf-8"))
            faltan = [key for key in reference if key not in table]
            sobran = [key for key in table if key not in reference]
            self.assertEqual(faltan, [], f"{language}: claves sin traducir")
            self.assertEqual(sobran, [], f"{language}: claves que no existen")

    def test_los_placeholders_sobreviven_a_la_traduccion(self):
        """Un ``{version}`` perdido deja el texto sin el dato que lo hace útil.

        Y uno **traducido** o con las llaves cambiadas saltaría en tiempo de
        ejecución (``tr`` lo tolera, pero el usuario vería el texto sin
        sustituir). Se comparan como conjuntos: el orden puede variar según el
        idioma, las llaves no.
        """
        import json
        import re

        def marcas(text):
            return sorted(re.findall(r"\{[^}]*\}", text))

        reference = json.loads(
            (i18n.LOCALE_DIR / "es.json").read_text(encoding="utf-8"))
        for language in i18n.SUPPORTED:
            path = i18n.LOCALE_DIR / f"{language}.json"
            if language == "en" or not path.exists():
                continue
            table = json.loads(path.read_text(encoding="utf-8"))
            malos = [f"{key!r} -> {table[key]!r}"
                     for key in reference
                     if key in table and marcas(key) != marcas(table[key])]
            self.assertEqual(malos, [], f"{language}: placeholders rotos")
