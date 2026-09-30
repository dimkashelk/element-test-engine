"""Reachable cross-module calls retain source and compile with explicit imports."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from element_test.runtime import REPO, decode_output, prepare_script, project_method_closure
from element_test.model import analyze
from element_test.yaml_io import InputError


class CrossModuleRuntimeTest(unittest.TestCase):
    def fixture(self, root, *, cycle=False):
        for namespace in ("A", "B", "C"):
            (root / namespace).mkdir()
        (root / "A/Main.xbsl").write_text(
            "импорт B::Helper как Alias\n"
            "метод Run(): Число\n"
            "    возврат B::Helper.Value() + Alias.Value()\n;\n", encoding="utf-8")
        (root / "B/Helper.xbsl").write_text(
            "метод Value(): Число\n"
            + ("    возврат A::Main.Run()\n" if cycle else "    возврат C::Next.One()\n")
            + ";\n", encoding="utf-8")
        (root / "C/Next.xbsl").write_text(
            "метод One(): Число\n    возврат 21\n;\n", encoding="utf-8")
        modules = []
        for namespace, name in (("A", "Main"), ("B", "Helper"), ("C", "Next")):
            modules.append({"name": name, "namespace": namespace,
                            "moduleType": "module", "sourceFile": f"{namespace}/{name}.xbsl",
                            "imports": ["B::Helper как Alias"] if name == "Main" else []})
        return {"properties": {}, "modules": modules, "elements": []}

    def test_qualified_alias_and_transitive_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = self.fixture(root)
            original = {p: p.read_bytes() for p in root.rglob("*.xbsl")}
            sandbox = root / "generated"
            sandbox.mkdir()
            script = prepare_script(root, model, {"target": {"module": "Main", "method": "Run"},
                                                  "args": []}, sandbox)
            generated = list(sandbox.glob("ТестВнешнийМодуль*.sbsl"))
            self.assertEqual(len(generated), 2)
            self.assertNotIn("B::Helper.Value()", script.read_text())
            self.assertNotIn("Alias.Value()", script.read_text())
            self.assertTrue(any("#требуется ТестВнешнийМодуль" in p.read_text()
                                for p in generated))
            self.assertEqual(original, {p: p.read_bytes() for p in original})
            if (REPO / "script_u_10.0.2_1/lib").is_dir():
                result = subprocess.run([str(REPO / "bin/script-runtime"), "-c", "9.0", str(script)],
                                        cwd=sandbox, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(decode_output(result.stdout)["actual"], 42)

    def test_cross_module_cycle_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = self.fixture(root, cycle=True)
            sandbox = root / "generated"
            sandbox.mkdir()
            with self.assertRaisesRegex(InputError, "Циклическая зависимость SBSL-модулей"):
                prepare_script(root, model, {"target": {"module": "Main", "method": "Run"},
                                              "args": []}, sandbox)

    def test_parameter_shadows_module_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = self.fixture(root)
            (root / "A/Main.xbsl").write_text(
                "метод Run(Helper: Число): Число\n    возврат Helper.Value()\n;\n",
                encoding="utf-8")
            reachable, _, _, _ = project_method_closure(root, model, model["modules"][0], "Run")
            self.assertEqual([owner["name"] for owner, _, _ in reachable], ["Main"])

    def test_declared_library_method_is_resolved_from_application(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            app, library = base / "App", base / "Lib"
            app.mkdir()
            library.mkdir()
            (app / "Проект.yaml").write_text(
                "Имя: App\nПоставщик: author\nРежимСовместимости: 9.0\n"
                "Библиотеки:\n  - {Поставщик: vendor, Имя: Lib, Версия: '1.0'}\n",
                encoding="utf-8")
            (library / "Проект.yaml").write_text(
                "Имя: Lib\nПоставщик: vendor\nВерсия: '1.0'\n"
                "ВидПроекта: Библиотека\nРежимСовместимости: 9.0\n", encoding="utf-8")
            (app / "Main.xbsl").write_text(
                "импорт vendor::Lib::Helper как H\n"
                "метод Run(): Число\n    возврат H.Answer()\n;\n", encoding="utf-8")
            (library / "Helper.xbsl").write_text(
                "@Глобально\nметод Answer(): Число\n    пер X: B::Док.Ссылка\n"
                "    возврат 42\n;\n", encoding="utf-8")
            (library / "Helper.yaml").write_text(
                "ВидЭлемента: Модуль\nИмя: Helper\nОбластьВидимости: Глобально\n",
                encoding="utf-8")
            (library / "B").mkdir()
            (library / "B/Док.yaml").write_text(
                "ВидЭлемента: Справочник\nИмя: Док\nОбластьВидимости: ВПроекте\n",
                encoding="utf-8")
            sandbox = base / "generated"
            sandbox.mkdir()
            model = analyze(app)
            self.assertIn({"fromModule": "Main", "fromMethod": "Run",
                           "toModule": "vendor::Lib::Helper", "toMethod": "Answer"},
                          model["callGraph"])
            script = prepare_script(app, model,
                                    {"target": {"module": "Main", "method": "Run"}, "args": []}, sandbox)
            self.assertIn("возврат 42", next(sandbox.glob("ТестВнешнийМодуль*.sbsl")).read_text())
            self.assertTrue((sandbox / "Док.sbsl").is_file())
            if (REPO / "script_u_10.0.2_1/lib").is_dir():
                result = subprocess.run([str(REPO / "bin/script-runtime"), "-c", "9.0", str(script)],
                                        cwd=sandbox, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(decode_output(result.stdout)["actual"], 42)

            (library / "Helper.yaml").write_text(
                "ВидЭлемента: Модуль\nИмя: Helper\nОбластьВидимости: ВПроекте\n",
                encoding="utf-8")
            with self.assertRaisesRegex(InputError, "Недоступный модуль"):
                prepare_script(app, analyze(app),
                               {"target": {"module": "Main", "method": "Run"}, "args": []}, sandbox)
            (library / "Helper.yaml").write_text(
                "ВидЭлемента: Модуль\nИмя: Helper\nОбластьВидимости: Глобально\n",
                encoding="utf-8")
            (library / "Helper.xbsl").write_text(
                "@Локально\nметод Answer(): Число\n    возврат 42\n;\n",
                encoding="utf-8")
            with self.assertRaisesRegex(InputError, "Недоступный метод"):
                prepare_script(app, analyze(app),
                               {"target": {"module": "Main", "method": "Run"}, "args": []}, sandbox)


if __name__ == "__main__":
    unittest.main()
