"""Stable algorithm corpus: outcome contract plus trusted executor semantics."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.model import analyze
from element_test.generated_types import ProjectTypes
from element_test.indexer import index_module, parse_module
from element_test.loader import open_project
from element_test.resolution import resolve_symbols
from element_test.runtime import REPO, decode_output, execute_engine, extract_method, prepare_script
from element_test.yaml_io import InputError

CORPUS = Path(__file__).parent / "corpus"


class AlgorithmCorpusTest(unittest.TestCase):
    def test_multiline_return_type_and_incomplete_index(self):
        source = ("@ВПроекте\n"
                  "метод Найти(Значения: Массив<Строка>):\n"
                  "    Соответствие<Строка, Массив<Число>>\n"
                  "    возврат <:>{:}\n"
                  ";\n")
        methods, _, errors = parse_module(source)
        self.assertEqual(errors, [])
        self.assertEqual(len(methods), 1)
        self.assertEqual(methods[0].return_type(source), "Соответствие<Строка, Массив<Число>>")
        self.assertEqual(source[methods[0].header_end:methods[0].end].lstrip().splitlines()[0],
                         "возврат <:>{:}")
        self.assertEqual(methods[0].annotations, ["ВПроекте"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Модуль.xbsl").write_text(source + "метод Сломан():\n;\n", encoding="utf-8")
            module = index_module(root / "Модуль.xbsl", root)
            self.assertFalse(module["indexComplete"])
            self.assertEqual([item["name"] for item in module["methods"]], ["Найти"])
            model = {"name": "Регрессия", "sourceHash": "fixture", "elements": [],
                     "modules": [module], "diagnostics": []}
            assignment = {"checks": [{"id": "found", "type": "method", "module": "Модуль",
                                      "name": "Найти", "points": 1, "properties": {}},
                                     {"id": "uncertain_absence", "type": "method", "module": "Модуль",
                                      "name": "Неизвестный", "points": 1, "properties": {}}]}
            write_json(root / "model.json", model)
            write_json(root / "assignment.json", assignment)
            result = execute_engine("test", root / "model.json", root / "assignment.json", root)
            self.assertEqual([item["status"] for item in result["checks"]], ["PASS", "UNSUPPORTED"])

    @unittest.skipUnless((Path(__file__).resolve().parent.parent /
                          "autocheck-2026-09-24-16-14.xdump").is_file(), "Требуется исходный xdump")
    def test_real_library_multiline_signature(self):
        dump = Path(__file__).resolve().parent.parent / "autocheck-2026-09-24-16-14.xdump"
        with open_project(dump, "e1c::БазаЗнаний") as root:
            path = root / "Пространства/Пространства.xbsl"
            source = path.read_text(encoding="utf-8-sig")
            module = index_module(path, root)
            self.assertTrue(module["indexComplete"], module["parseErrors"])
            method = next(item for item in parse_module(source)[0] if item.line == 70)
            self.assertEqual(method.name, "ПространстваСоСтраницами")
            self.assertEqual(method.return_type(source),
                             "Соответствие<Пространства.Ссылка, Массив<СтраницыПространств.Ссылка|ЧерновикиСтраницПространств.Ссылка>>")
            self.assertEqual(source[method.header_end:method.end].lstrip().splitlines()[0],
                             "знч ПрофилиЗаполнены = не Профили.Пусто()")

    def test_known_statuses_and_unchanged_sources(self):
        root = CORPUS / "names"
        before = {p: sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
        model = analyze(root)
        self.assertEqual(model["diagnostics"], [])
        self.assertEqual([(e["fromModule"], e["toModule"]) for e in model["callGraph"]],
                         [("A::Main", "A::Main"), ("A::Main", "B::Shared"),
                          ("A::Main", "A::Shared")])
        assignment = load_assignment(CORPUS)
        for check in assignment["checks"]:
            if check["id"] in {"runtime_pass", "runtime_negative"}:
                check["execution"] = {"status": "EXECUTED", "actual": 5}
            elif check["id"] == "runtime_error":
                check["execution"] = {"status": "ERROR", "message": "executor failed"}
            elif check["id"] == "runtime_unsupported":
                check["execution"] = {"status": "UNSUPPORTED", "message": "unsupported contract"}
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            write_json(tmp / "model.json", model)
            write_json(tmp / "assignment.json", assignment)
            result = execute_engine("test", tmp / "model.json", tmp / "assignment.json", tmp)
        self.assertEqual([c["status"] for c in result["checks"]],
                         ["PASS", "PASS", "FAIL", "PASS", "PASS", "FAIL", "ERROR", "UNSUPPORTED"])
        self.assertEqual(result["score"], 4)
        self.assertEqual(result["unavailablePoints"], 2)
        self.assertEqual(before, {p: sha256(p.read_bytes()).hexdigest() for p in before})

    def test_syntax_ambiguity_and_incomplete_index(self):
        model = analyze(CORPUS / "names")
        module = next(m for m in model["modules"] if m["name"] == "Main")
        self.assertEqual([m["name"] for m in module["methods"]], ["Проверить", "Локальный"])
        source = (CORPUS / "names" / module["sourceFile"]).read_text()
        method, _ = extract_method(source, "Локальный")
        self.assertIn("возврат 0", method)
        broken = analyze(CORPUS / "broken")
        self.assertFalse(broken["modules"][0]["indexComplete"])
        self.assertIn("invalid_xbsl", [d["code"] for d in broken["diagnostics"]])
        with self.assertRaisesRegex(InputError, "не закрыт"):
            extract_method((CORPUS / "broken/Main.xbsl").read_text(), "Незакрытый")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(InputError, "неоднознач"):
                prepare_script(CORPUS / "names", model,
                               {"target": {"module": "Shared", "method": "Значение"}, "args": []}, Path(directory))
        overloaded = ("метод A(): Число\n возврат 1\n;\n"
                      "метод A(V: Число): Число\n возврат V\n;\n")
        with self.assertRaisesRegex(InputError, "неоднознач"):
            extract_method(overloaded, "A")

    def test_structural_namespace_selects_exact_module(self):
        model = analyze(CORPUS / "names")
        assignment = {"name": "namespace", "checks": [
            {"id": "exact", "type": "method", "module": "Shared", "namespace": "B",
             "name": "Значение", "points": 1, "properties": {}},
            {"id": "ambiguous", "type": "method", "module": "Shared",
             "name": "Значение", "points": 1, "properties": {}}]}
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            write_json(tmp / "model.json", model)
            write_json(tmp / "assignment.json", assignment)
            result = execute_engine("test", tmp / "model.json", tmp / "assignment.json", tmp)
        self.assertEqual([c["status"] for c in result["checks"]], ["PASS", "ERROR"])
        self.assertIn("Неоднознач", result["checks"][1]["message"])

    def test_type_import_alias_visibility_and_ambiguous_short_name(self):
        model = analyze(CORPUS / "names")
        local = ProjectTypes(model, "A")
        local.require("Док.Ссылка")
        self.assertEqual(local.owners["Док"], ("A", "Док"))
        imported = ProjectTypes(model, "A", ["B::Док как ExternalDoc"])
        imported.require("ExternalDoc.Ссылка")
        self.assertEqual(imported.owners["Док"], ("B", "Док"))
        with self.assertRaisesRegex(InputError, "неоднознач"):
            ProjectTypes(model).require("Док.Ссылка")
        hidden = [dict(e) for e in model["elements"]]
        for element in hidden:
            if element["namespace"] == "B":
                element["visibility"] = "ВПодсистеме"
        self.assertEqual(resolve_symbols(hidden, "B::Док", "A"), [])
        with self.assertRaises(InputError):
            ProjectTypes({**model, "elements": hidden}, "A").require("B::Док.Ссылка")

    def test_local_method_is_not_imported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Main.xbsl").write_text("импорт Helper\nметод Run(): Число\n возврат Helper.Secret()\n;\n")
            (root / "Helper.xbsl").write_text("@Локально\nметод Secret(): Число\n возврат 9\n;\n")
            model = {"modules": [
                {"name": "Main", "namespace": "", "moduleType": "module", "sourceFile": "Main.xbsl", "imports": ["Helper"]},
                {"name": "Helper", "namespace": "", "moduleType": "module", "sourceFile": "Helper.xbsl"}],
                "elements": [], "properties": {}}
            with self.assertRaisesRegex(InputError, "Недоступный метод"):
                prepare_script(root, model, {"target": {"module": "Main", "method": "Run"}, "args": []}, root)

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется Script executor")
    def test_imported_method_in_trusted_executor(self):
        root = CORPUS / "names"
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            script = prepare_script(root, analyze(root),
                                    {"target": {"module": "Main", "namespace": "A", "method": "Проверить"},
                                     "args": []}, sandbox)
            result = subprocess.run([str(REPO / "bin/script-runtime"), "-c", "9.0", str(script)],
                                    cwd=sandbox, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(decode_output(result.stdout)["actual"], 5)
