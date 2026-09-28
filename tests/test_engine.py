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
        self.assertEqual((result["score"], result["maxScore"], result["unavailablePoints"]), (1, 2, 9))

    def test_only_unsupported_is_incomplete_not_student_failure(self):
        result = self.execute([{"id": "future", "type": "query", "points": 4}])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["maxScore"], 0)

    def test_error_and_timeout_only_are_incomplete(self):
        result = self.execute([
            {"id": "error", "type": "field", "points": 2},
            {"id": "timeout", "type": "runtime", "points": 3,
             "execution": {"status": "TIMEOUT", "message": "executor timeout"}},
            {"id": "continues", "type": "element", "name": "Нет", "exists": False}])
        self.assertEqual([c["status"] for c in result["checks"]], ["ERROR", "TIMEOUT", "PASS"])
        self.assertEqual((result["status"], result["score"], result["maxScore"], result["unavailablePoints"]),
                         ("incomplete", 1, 1, 5))

    def test_unknown_runtime_status_is_error_not_failure(self):
        result = self.execute([
            {"id": "unknown", "type": "runtime", "execution": {"status": "BROKEN"}, "points": 2},
            {"id": "continues", "type": "element", "name": "Нет", "exists": False}])
        self.assertEqual([c["status"] for c in result["checks"]], ["ERROR", "PASS"])
        self.assertEqual((result["status"], result["maxScore"], result["unavailablePoints"]),
                         ("incomplete", 1, 2))

    def test_runtime_result_is_compared_in_sbsl(self):
        result = self.execute([
            {"id": "equal", "type": "runtime", "expected": "Иванов", "execution": {"status": "EXECUTED", "actual": "Иванов"}},
            {"id": "wrong", "type": "runtime", "expected": "Иванов", "execution": {"status": "EXECUTED", "actual": "Петров"}},
            {"id": "timeout", "type": "runtime", "expected": "x", "execution": {"status": "TIMEOUT", "message": "test timeout"}}])
        self.assertEqual([c["status"] for c in result["checks"]], ["PASS", "FAIL", "TIMEOUT"])
        self.assertEqual(result["score"], 1)
        self.assertEqual((result["maxScore"], result["unavailablePoints"]), (2, 1))
        self.assertEqual([c["reasonCode"] for c in result["checks"] if c["status"] != "PASS"],
                         ["result_mismatch", "timeout"])

    def test_reason_codes_keep_unavailable_points_out_of_grade(self):
        result = self.execute([
            {"id": "invalid", "type": "runtime", "points": 2,
             "execution": {"status": "ERROR", "reasonCode": "invalid_test"}},
            {"id": "syntax", "type": "runtime", "points": 3,
             "execution": {"status": "UNSUPPORTED", "reasonCode": "unsupported_syntax"}},
            {"id": "docker", "type": "runtime", "points": 4,
             "execution": {"status": "UNSUPPORTED", "reasonCode": "backend_unavailable"}},
            {"id": "execute", "type": "runtime", "points": 5,
             "execution": {"status": "ERROR", "reasonCode": "execution_error"}},
            {"id": "wrong", "type": "runtime", "points": 6,
             "expected": 1, "execution": {"status": "EXECUTED", "actual": 2}},
            {"id": "next", "type": "runtime", "points": 1,
             "expected": 3, "execution": {"status": "EXECUTED", "actual": 3}}])
        self.assertEqual([c["reasonCode"] for c in result["checks"][:-1]],
                         ["invalid_test", "unsupported_syntax", "backend_unavailable",
                          "execution_error", "result_mismatch"])
        self.assertEqual((result["score"], result["maxScore"], result["unavailablePoints"]), (1, 7, 14))

    def test_structural_absence_namespace_collection_and_mismatch_reason(self):
        model = {"name": "Структура", "sourceHash": "fixture", "diagnostics": [],
                 "modules": [
                     {"name": "Общий", "namespace": "A", "indexComplete": True,
                      "methods": [{"name": "Есть", "annotations": [], "parameters": [], "returnType": "Число"}]},
                     {"name": "Общий", "namespace": "B", "indexComplete": False,
                      "methods": []}],
                 "elements": [{"name": "Док", "namespace": "A", "elementType": "Документ",
                               "properties": {"Реквизиты": [{"Имя": "Код", "Тип": "Строка", "МаксимальнаяДлина": 5}],
                                              "Ресурсы": [{"Имя": "Код", "Тип": "Число"}]}}]}
        checks = [
            {"id": "method_absent", "type": "method", "module": "Общий", "namespace": "A", "name": "Нет", "exists": False},
            {"id": "method_present", "type": "method", "module": "Общий", "namespace": "A", "name": "Есть", "exists": False},
            {"id": "incomplete", "type": "method", "module": "Общий", "namespace": "B", "name": "Нет", "exists": False},
            {"id": "module_absent", "type": "module", "module": "НеСуществует", "exists": False},
            {"id": "ambiguous_field", "type": "field", "element": "Док", "field": "Код"},
            {"id": "exact_collection", "type": "field", "element": "Док", "field": "Код", "collection": "Ресурсы", "expectedType": "Число"},
            {"id": "bad_collection", "type": "field", "element": "Док", "field": "Код", "collection": "Опечатка", "exists": False},
            {"id": "property", "type": "field", "element": "Док", "field": "Код", "collection": "Реквизиты", "properties": {"МаксимальнаяДлина": 6}}]
        result = self.execute(checks, model)
        self.assertEqual([c["status"] for c in result["checks"]],
                         ["PASS", "FAIL", "UNSUPPORTED", "PASS", "ERROR", "PASS", "ERROR", "FAIL"])
        self.assertEqual(result["checks"][-1]["message"], "Свойство не совпадает: МаксимальнаяДлина")
        self.assertEqual((result["checks"][-1]["expected"], result["checks"][-1]["actual"]), (6, 5))
        self.assertEqual(result["unavailablePoints"], 3)

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

    @unittest.skipUnless((REPO / "Dvizhok.xdump").is_file(), "Требуется реальная работа Dvizhok.xdump")
    def test_real_project_semantics_and_negative_checks(self):
        with open_project(REPO / "Dvizhok.xdump") as root:
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
