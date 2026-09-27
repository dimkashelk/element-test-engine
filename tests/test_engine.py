"""Integration checks exercise actual SBSL grading through the supplied executor."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from element_test.assignment import load_assignment
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine

REPO = Path(__file__).resolve().parent.parent


@unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется локальный Script executor")
class EngineTest(unittest.TestCase):
    def execute(self, checks, model=None):
        if model is None:
            model = {"name": "Пример", "sourceHash": "fixture", "elements": [], "modules": [], "diagnostics": []}
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            m, a = temp / "model.json", temp / "assignment.json"
            m.write_text(json.dumps(model, ensure_ascii=False))
            for check in checks:
                check.setdefault("points", 1)
                check.setdefault("properties", {})
            a.write_text(json.dumps({"checks": checks}, ensure_ascii=False))
            return execute_engine("test", m, a, temp)

    def test_failure_error_and_unsupported_do_not_stop_checks(self):
        result = self.execute([
            {"id": "missing", "type": "element", "name": "Нет"},
            {"id": "bad_config", "type": "field"},
            {"id": "not_implemented", "type": "query", "points": 5},
            {"id": "skipped", "type": "element", "skip": True, "points": 3},
            {"id": "absence", "type": "element", "name": "Нет", "exists": False}])
        self.assertEqual([c["status"] for c in result["checks"]], ["FAIL", "ERROR", "UNSUPPORTED", "SKIPPED", "PASS"])
        self.assertEqual((result["score"], result["maxScore"], result["unavailablePoints"]), (1, 3, 8))

    def test_only_unsupported_is_incomplete_not_student_failure(self):
        result = self.execute([{"id": "future", "type": "query", "points": 4}])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["maxScore"], 0)

    def test_runtime_result_is_compared_in_sbsl(self):
        result = self.execute([
            {"id": "equal", "type": "runtime", "expected": "Иванов", "execution": {"status": "EXECUTED", "actual": "Иванов"}},
            {"id": "wrong", "type": "runtime", "expected": "Иванов", "execution": {"status": "EXECUTED", "actual": "Петров"}},
            {"id": "timeout", "type": "runtime", "expected": "x", "execution": {"status": "TIMEOUT", "message": "test timeout"}}])
        self.assertEqual([c["status"] for c in result["checks"]], ["PASS", "FAIL", "TIMEOUT"])
        self.assertEqual(result["score"], 1)

    def test_runtime_date_snapshot_resolves_expected_only(self):
        date = '2026-09-27T12:34:56.789'
        base = {'type': 'runtime', 'runtimeDateTime': 'Дата',
                'expected': {'calls': [{'Период': '__runtimeDateTime__'}]},
                'execution': {'status': 'EXECUTED', 'runtimeDateTime': date,
                              'actual': {'calls': [{'Период': date}]}}}
        passed, failed, missing = [copy.deepcopy(base) for _ in range(3)]
        passed['id'], failed['id'], missing['id'] = 'snapshot', 'wrong_period', 'missing_snapshot'
        failed['execution']['actual']['calls'][0]['Период'] = '2000-01-01T00:00:00'
        del missing['execution']['runtimeDateTime']
        result = self.execute([passed, failed, missing, {**passed, 'id': 'continues'}])
        self.assertEqual([c['status'] for c in result['checks']], ['PASS', 'FAIL', 'ERROR', 'PASS'])
        self.assertEqual(result['checks'][0]['expected']['calls'][0]['Период'], date)

    @unittest.skipUnless((REPO / "Движок.tar").is_file(), "Требуется реальная работа Движок.tar")
    def test_real_project_semantics_and_negative_checks(self):
        with open_project(REPO / "Движок.tar") as root:
            model = analyze(root)
        checks = load_assignment(REPO / "assignments/demo")["checks"]
        passed = self.execute(copy.deepcopy(checks), model)
        self.assertEqual(passed["score"], 6)
        changed = copy.deepcopy(model)
        for element in changed["elements"]:
            element["id"] = "new-id"
            element["sourceFile"] = "unrelated/path.yaml"
            element["properties"].get("Реквизиты", []).reverse()
        self.assertEqual(self.execute(copy.deepcopy(checks), changed)["score"], 6)
        checks[1]["expectedType"] = "Строка"
        failed = self.execute(checks, model)
        self.assertEqual(failed["checks"][1]["status"], "FAIL")
        self.assertEqual(failed["checks"][1]["actual"], "Контрагенты.Ссылка?")
        self.assertEqual(failed["score"], 5)


if __name__ == "__main__":
    unittest.main()
