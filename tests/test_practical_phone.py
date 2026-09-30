"""Independent phone rule from the fourth xdump and its runtime boundary."""
import copy
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine, prepare_script, run_pure


REPO = Path(__file__).resolve().parent.parent
ARCHIVE = REPO / "Prakticheskie-primery-2026-09-30-15-20.xdump"
ASSIGNMENT = REPO / "assignments/practical-phone"


@unittest.skipUnless(ARCHIVE.is_file(), "Требуется исходный Примеры_new xdump")
class PracticalPhoneTest(unittest.TestCase):
    def test_assignment_structure_and_unsupported_uuid_boundary(self):
        assignment = load_assignment(ASSIGNMENT)
        self.assertEqual((len(assignment["checks"]), sum(c["points"] for c in assignment["checks"])), (7, 8))
        with open_project(ARCHIVE) as root, TemporaryDirectory() as directory:
            temp, model = Path(directory), analyze(root)
            structural = [copy.deepcopy(c) for c in assignment["checks"] if c["type"] != "runtime"]
            write_json(temp / "model.json", model)
            write_json(temp / "assignment.json", {"checks": structural})
            result = execute_engine("test", temp / "model.json", temp / "assignment.json", temp)
            self.assertEqual([c["status"] for c in result["checks"]], ["PASS"] * 3)
            for check in assignment["checks"][3:]:
                sandbox = temp / check["id"]
                sandbox.mkdir()
                self.assertTrue(prepare_script(root, model, check, sandbox).is_file())
            boundary = load_assignment(ASSIGNMENT / "fixtures/unsupported.yaml")["checks"][0]
            with patch("element_test.runtime.shutil.which", return_value="docker"):
                execution = run_pure(root, model, boundary, temp)
            self.assertEqual((execution["status"], execution["reasonCode"]),
                             ("UNSUPPORTED", "unsupported_contract"))
            self.assertIn("Ууид", execution["message"])

    @unittest.skipUnless(os.environ.get("ELEMENT_TEST_DOCKER_TESTS") == "1", "Docker integration is opt-in")
    def test_reference_and_mutated_phone_rule(self):
        assignment = load_assignment(ASSIGNMENT)
        with open_project(ARCHIVE) as root, TemporaryDirectory() as directory:
            temp, model = Path(directory), analyze(root)
            checks = copy.deepcopy(assignment["checks"])
            for check in checks:
                if check["type"] == "runtime":
                    check["execution"] = run_pure(root, model, check, temp)
                    self.assertEqual(check["execution"]["status"], "EXECUTED", check["execution"])
            write_json(temp / "model.json", model)
            write_json(temp / "assignment.json", {"checks": checks})
            reference = execute_engine("test", temp / "model.json", temp / "assignment.json", temp)
            self.assertEqual((reference["score"], reference["maxScore"]), (8, 8))
            self.assertEqual([c["status"] for c in reference["checks"]], ["PASS"] * 7)

            source = root / "РаботаСИсключениями/Покупатели.xbsl"
            original = source.read_bytes()
            old = "    возврат Телефон.ПолноеСовпадение('^\\+7\\d{10}$')".encode()
            self.assertEqual(original.count(old), 1)
            source.write_bytes(original.replace(old, "    возврат Истина".encode()))
            for check in checks[3:]:
                check["execution"] = run_pure(root, model, check, temp)
                self.assertEqual(check["execution"]["status"], "EXECUTED", check["execution"])
            write_json(temp / "assignment.json", {"checks": checks})
            mutated = execute_engine("test", temp / "model.json", temp / "assignment.json", temp)
            self.assertEqual([c["status"] for c in mutated["checks"]],
                             ["PASS"] * 4 + ["FAIL"] * 3)
            self.assertEqual((mutated["score"], mutated["maxScore"], mutated["unavailablePoints"]),
                             (5, 8, 0))


if __name__ == "__main__":
    unittest.main()
