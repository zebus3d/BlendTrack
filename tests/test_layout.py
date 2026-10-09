"""La forma del árbol de fuentes: tamaño de los módulos y nombres sin definir.

``main_window.py`` llegó a 3314 líneas y ``migrate.py`` a 2492: a partir de ahí
nadie lee el fichero entero, y lo que no se lee es donde se esconde el código
repetido. El tope es blando (se puede subir con un motivo), pero tiene que ser
una decisión, no un descuido.
"""

import builtins
import symtable
import unittest
from pathlib import Path

# Un módulo de más de mil líneas ya no se lee de una sentada. Los que están
# cerca se parten por responsabilidad, no por número de líneas.
MAX_LINES = 1000

# Excepciones, con su porqué. Vacío: si hace falta añadir una, que se vea en
# la revisión.
ALLOWED = {}


class UndefinedNameTest(unittest.TestCase):
    """Un nombre usado y no importado tiene que saltar AQUÍ, no en el CI.

    Pasó de verdad: al repartir ``main_window.py`` en módulos, ``build_lists``
    quedó usando ``QMenu``, ``QApplication`` y ``opener`` sin importarlos. La
    suite pasaba en local y el CI se caía entero (test + smoke de Linux +
    bundle de macOS), porque **no corren el mismo Python**: en 3.14 las
    anotaciones son diferidas (PEP 649) y un ``-> QMenu`` sin importar no se
    evalúa nunca; en el 3.12 del CI se evalúa al definir la función y revienta
    el import del módulo.

    ``symtable`` hace el análisis de ámbitos de verdad (qué nombre es local,
    cuál es global, cuál es libre), así que esto no depende de la versión de
    Python que uno tenga delante.
    """

    # Dunders que el propio intérprete inyecta. ``__conditional_annotations__``
    # es de la maquinaria de PEP 649 y solo aparece en 3.14.
    INTERNAL = frozenset({
        "__file__", "__name__", "__doc__", "__builtins__", "__spec__",
        "__package__", "__loader__", "__path__", "__conditional_annotations__",
    })

    def _undefined(self, path):
        """Nombres globales que el módulo referencia y nadie define en él."""
        table = symtable.symtable(path.read_text(encoding="utf-8"),
                                  str(path), "exec")
        bound = {symbol.get_name() for symbol in table.get_symbols()
                 if symbol.is_assigned() or symbol.is_imported()
                 or symbol.is_namespace()}
        missing = set()

        def walk(scope):
            for symbol in scope.get_symbols():
                if symbol.is_global() and symbol.get_name() not in bound:
                    missing.add(symbol.get_name())
            for child in scope.get_children():
                walk(child)

        walk(table)
        return sorted(name for name in missing
                      if not hasattr(builtins, name)
                      and name not in self.INTERNAL)

    def test_ningun_modulo_usa_un_nombre_que_no_importa(self):
        root = Path(__file__).resolve().parents[1] / "src"
        problemas = []
        for path in sorted(root.rglob("*.py")):
            faltan = self._undefined(path)
            if faltan:
                problemas.append(f"{path.relative_to(root)}: {faltan}")
        self.assertEqual(problemas, [], "nombres usados sin importar")


class ModuleSizeTest(unittest.TestCase):
    def test_ningun_modulo_pasa_de_mil_lineas(self):
        root = Path(__file__).resolve().parents[1] / "src"
        grandes = []
        for path in sorted(root.rglob("*.py")):
            lines = len(path.read_text(encoding="utf-8").splitlines())
            name = str(path.relative_to(root))
            if lines > ALLOWED.get(name, MAX_LINES):
                grandes.append(f"{name}: {lines}")
        self.assertEqual(grandes, [], "módulos que hay que partir")


if __name__ == "__main__":
    unittest.main()


class IsolatedTestsCallSuperTest(unittest.TestCase):
    """Un test que hereda ``SettingsIsolated`` y redefine ``setUp`` llama a super.

    Si no lo hace, el mixin no aísla nada y la ventana del test lee el
    ``settings.json`` y las carpetas de Blender **del usuario**. Pasó con tres
    clases de ``test_ui.py`` (Migración, Addons y Recientes): la suite pasaba
    en verde mientras arrancaba en segundo plano el Blender 5.2.2 de quien la
    ejecutaba para leer sus preferencias, y lo único visible era un
    ``RuntimeError: Signal source has been deleted`` cuando ese Blender
    contestaba con la ventana del test ya cerrada.
    """

    def test_setup_y_teardown_llaman_a_super(self):
        import ast

        offenders = []
        for path in sorted(Path(__file__).resolve().parent.glob("test_*.py")):
            source = path.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {getattr(base, "id", getattr(base, "attr", ""))
                         for base in node.bases}
                if "SettingsIsolated" not in bases:
                    continue
                for item in node.body:
                    if (isinstance(item, ast.FunctionDef)
                            and item.name in ("setUp", "tearDown")
                            and f"super().{item.name}()"
                            not in ast.get_source_segment(source, item)):
                        offenders.append(f"{path.name}:{node.name}.{item.name}")
        self.assertEqual(offenders, [])
