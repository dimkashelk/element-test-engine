"""Explicit library targets must use the declared dependency's own model and source."""
import copy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from element_test.bridge import run_test, write_json
from element_test.model import analyze, select_check_project
from element_test.runtime import execute_engine, prepare_script, run_pure
from element_test.yaml_io import InputError


SELECTOR = {"provider": "vendor", "name": "Lib", "version": "1.0"}


class LibraryChecksTest(unittest.TestCase):
    def make_project(self, base):
        app, library = base / "App", base / "Lib"
        app.mkdir()
        library.mkdir()
        (app / "Проект.yaml").write_text(
            "Имя: App\nПоставщик: author\nРежимСовместимости: 9.0\n"
            "Библиотеки:\n  - {Поставщик: vendor, Имя: Lib, Версия: 1.0}\n", encoding="utf-8")
        (library / "Проект.yaml").write_text(
            "Имя: Lib\nПоставщик: vendor\nВерсия: 1.0\n"
            "ВидПроекта: Библиотека\nРежимСовместимости: 9.0\n", encoding="utf-8")
        (app / "Main.xbsl").write_text("метод Ответ(): Число\n возврат 1\n;\n", encoding="utf-8")
        (library / "Main.xbsl").write_text("метод Ответ(): Число\n возврат 42\n;\n", encoding="utf-8")
        return app

    def test_structural_selector_and_runtime_source(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = self.make_project(base)
            model = analyze(root)
            selected_root, selected_model = select_check_project(root, model, {"library": SELECTOR})
            self.assertEqual((selected_root.name, selected_model["name"]), ("Lib", "Lib"))
            method = {"id": "library_method", "type": "method", "module": "Main", "name": "Ответ",
                      "returnType": "Число", "library": SELECTOR, "points": 1}
            missing = copy.deepcopy(method)
            missing.update(id="wrong_library", library={**SELECTOR, "version": "2.0"})
            write_json(base / "model.json", model)
            write_json(base / "assignment.json", {"checks": [method, missing]})
            result = execute_engine("test", base / "model.json", base / "assignment.json", base)
            self.assertEqual([item["status"] for item in result["checks"]], ["PASS", "ERROR"])
            self.assertEqual(result["checks"][1]["reasonCode"], "invalid_test")
            with self.assertRaisesRegex(InputError, "не найдена"):
                select_check_project(root, model, missing)
            runtime = {"id": "library_runtime", "type": "runtime", "library": SELECTOR,
                       "target": {"module": "Main", "method": "Ответ"}, "args": [], "expected": 42}
            sandbox = base / "sandbox"
            sandbox.mkdir()
            script = prepare_script(root, model, runtime, sandbox).read_text(encoding="utf-8")
            self.assertIn("возврат 42", script)
            self.assertNotIn("возврат 1", script)

    def test_library_integration_is_explicitly_unavailable(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = self.make_project(base)
            assignment = base / "assignment.yaml"
            assignment.write_text(
                "name: library integration\nchecks:\n  - id: integration\n    type: runtime\n"
                "    library: {provider: vendor, name: Lib, version: '1.0'}\n"
                "    integration: {}\n    points: 1\n", encoding="utf-8")
            result, _ = run_test(root, assignment, base / "output")
            self.assertEqual((result["checks"][0]["status"], result["checks"][0]["reasonCode"]),
                             ("UNSUPPORTED", "unsupported_contract"))

    @unittest.skipUnless(os.environ.get("ELEMENT_TEST_DOCKER_TESTS") == "1", "Docker integration is opt-in")
    def test_library_runtime_pass_fail_and_continuation(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            root = self.make_project(base)
            model = analyze(root)
            positive = {"id": "positive", "type": "runtime", "library": SELECTOR,
                        "target": {"module": "Main", "method": "Ответ"},
                        "args": [], "expected": 42, "points": 1}
            negative = {**positive, "id": "negative", "expected": 43}
            following = {**positive, "id": "following"}
            checks = [positive, negative, following]
            for check in checks:
                check["execution"] = run_pure(root, model, check, base)
                self.assertEqual(check["execution"]["status"], "EXECUTED", check["execution"])
            write_json(base / "model.json", model)
            write_json(base / "assignment.json", {"checks": checks})
            result = execute_engine("test", base / "model.json", base / "assignment.json", base)
            self.assertEqual([item["status"] for item in result["checks"]], ["PASS", "FAIL", "PASS"])


if __name__ == "__main__":
    unittest.main()
