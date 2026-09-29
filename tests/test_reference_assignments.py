"""Source-based assignments stay aligned with the supplied reference dumps."""
from pathlib import Path
from tempfile import TemporaryDirectory
import copy
import unittest

from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine, prepare_script
from element_test.yaml_io import InputError


REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "Demo-SRM-dev-2026-09-28-21-38.xdump"
CHECK = REPO / "autocheck-2026-09-24-16-14.xdump"


class ReferenceAssignmentsTest(unittest.TestCase):
    def grade_structure(self, model, assignment):
        checks = [copy.deepcopy(check) for check in assignment["checks"] if check["type"] != "runtime"]
        with TemporaryDirectory() as directory:
            temp = Path(directory)
            write_json(temp / "model.json", model)
            write_json(temp / "assignment.json", {"checks": checks})
            return execute_engine("test", temp / "model.json", temp / "assignment.json", temp)

    def prepare_runtime(self, root, model, assignment):
        with TemporaryDirectory() as directory:
            for check in assignment["checks"]:
                if check["type"] == "runtime":
                    generated = Path(directory) / check["id"]
                    generated.mkdir()
                    self.assertTrue(prepare_script(root, model, check, generated).is_file())

    @unittest.skipUnless(DEMO.is_file(), "Требуется исходный ДемоСРМ xdump")
    def test_demo_assignment_passes_structure_and_detects_wrong_type(self):
        assignment = load_assignment(REPO / "assignments/demo-srm")
        self.assertEqual((len(assignment["checks"]), sum(c["points"] for c in assignment["checks"])), (7, 9))
        with open_project(DEMO) as root:
            model = analyze(root)
            self.assertEqual([check["status"] for check in self.grade_structure(model, assignment)["checks"]],
                             ["PASS"] * 5)
            wrong = copy.deepcopy(assignment)
            next(check for check in wrong["checks"] if check["id"] == "task_owner")["expectedType"] = "Строка"
            self.assertEqual(self.grade_structure(model, wrong)["checks"][1]["status"], "FAIL")
            self.prepare_runtime(root, model, assignment)

    @unittest.skipUnless(CHECK.is_file(), "Требуется исходный check xdump")
    def test_check_assignment_uses_fixed_source_and_exact_library(self):
        assignment = load_assignment(REPO / "assignments/check-odata")
        self.assertEqual((len(assignment["checks"]), sum(c["points"] for c in assignment["checks"])), (10, 12))
        with open_project(CHECK, "dimkashelk::check") as root:
            source = root / "Проверка/ПакетПроверки/КритерийПроверкиФормаОбъекта.yaml"
            lines = source.read_text(encoding="utf-8-sig").splitlines(keepends=True)
            self.assertEqual(lines[111].count("\\'"), 0)
            model = analyze(root)
            self.assertEqual([(item["provider"], item["name"], item["version"])
                              for item in model["libraries"]], [("e1c", "БазаЗнаний", "1.1.1")])
            self.assertEqual([check["status"] for check in self.grade_structure(model, assignment)["checks"]],
                             ["PASS"] * 3)
            wrong = copy.deepcopy(assignment)
            next(check for check in wrong["checks"] if check["id"] == "odata_response_signature")["returnType"] = "Строка"
            self.assertEqual(self.grade_structure(model, wrong)["checks"][2]["status"], "FAIL")
            library_check = {"id": "library_method", "type": "method", "module": "ПраваБиблиотеки",
                             "namespace": "ПраваДоступа", "name": "ЭтоАдминистраторБиблиотеки",
                             "library": {"provider": "e1c", "name": "БазаЗнаний", "version": "1.1.1"},
                             "points": 1}
            self.assertEqual(self.grade_structure(model, {"checks": [library_check]})["checks"][0]["status"], "PASS")
            library_check["library"]["version"] = "1.1.2"
            unmatched = self.grade_structure(model, {"checks": [library_check]})["checks"][0]
            self.assertEqual((unmatched["status"], unmatched["reasonCode"]), ("ERROR", "invalid_test"))
            self.prepare_runtime(root, model, assignment)


if __name__ == "__main__":
    unittest.main()
