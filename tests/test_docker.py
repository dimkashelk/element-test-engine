"""Opt-in tests of real student code; ELEMENT_TEST_DOCKER_TESTS=1."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from element_test.assignment import load_assignment
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import run_pure, execute_engine
import json

REPO = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.environ.get("ELEMENT_TEST_DOCKER_TESTS") == "1", "Docker integration is opt-in")
class DockerTest(unittest.TestCase):
    def test_real_receipt_and_negative_grading(self):
        from element_test.bridge import report, write_json
        archive = REPO / 'Dvizhok.xdump'
        before = archive.read_bytes()
        assignment = load_assignment(REPO / 'assignments/poc-receipt')
        positive = copy.deepcopy(assignment)
        for field, value in [('Склад', {'Идентификатор': 'wrong'}),
                             ('Номенклатура', {'Идентификатор': 'wrong'}),
                             ('Количество', 99), ('Цена', 99), ('Сумма', 99),
                             ('order', None)]:
            wrong = copy.deepcopy(positive['checks'][2])
            wrong['id'] = 'wrong_' + field
            if field == 'Склад':
                wrong['expected'][field] = value
            elif field == 'order':
                wrong['expected']['Товары'].reverse()
            else:
                wrong['expected']['Товары'][0][field] = value
            resumed = copy.deepcopy(positive['checks'][1])
            resumed['id'] = 'after_' + wrong['id']
            assignment['checks'].extend([wrong, resumed])
        missing = copy.deepcopy(positive['checks'][1])
        missing.update(id='missing_shipment', args=[{'Идентификатор': 'absent'}])
        unsupported = copy.deepcopy(positive['checks'][1])
        unsupported.update(id='unsupported_reference_contract', mocks={'objects': {'Отгрузка.Нет': {}}})
        for unavailable in (missing, unsupported):
            resumed = copy.deepcopy(positive['checks'][0])
            resumed['id'] = 'after_' + unavailable['id']
            assignment['checks'].extend([unavailable, resumed])
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root:
            temp, model = Path(directory), analyze(root)
            for check in assignment['checks']:
                check['execution'] = run_pure(root, model, check, temp)
                expected_status = {'missing_shipment': 'ERROR',
                                   'unsupported_reference_contract': 'UNSUPPORTED'}.get(check['id'], 'EXECUTED')
                self.assertEqual(check['execution']['status'], expected_status, check['execution'])
            m, a = temp / 'model.json', temp / 'assignment.json'
            write_json(m, model)
            write_json(a, {**assignment, 'checks': assignment['checks'][:5]})
            result = execute_engine('test', m, a, temp)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS'] * 5)
            for output_name, data in [('poc-receipt', result), ('poc-receipt-negative', None)]:
                if data is None:
                    write_json(a, assignment)
                    data = execute_engine('test', m, a, temp)
                    self.assertEqual([c['status'] for c in data['checks']],
                        ['PASS'] * 5 + ['FAIL', 'PASS'] * 6 + ['ERROR', 'PASS', 'UNSUPPORTED', 'PASS'])
                    self.assertEqual((data['score'], data['maxScore'], data['unavailablePoints']), (13, 19, 2))
                output = REPO / 'result' / output_name
                output.mkdir(parents=True, exist_ok=True)
                write_json(output / 'result.json', data)
                (output / 'report.html').write_text(report(data), encoding='utf-8')
        self.assertEqual(archive.read_bytes(), before)

    def test_real_movements_and_negative_grading(self):
        from element_test.bridge import report, write_json
        archive = REPO / 'Dvizhok.xdump'
        before = archive.read_bytes()
        assignment = load_assignment(REPO / 'assignments/poc-movements')
        for field, value in [('Регистратор', {'Идентификатор': 'wrong'}),
                             ('Склад', {'Идентификатор': 'wrong'}),
                             ('Количество', 99), ('ВидЗаписи', 'Приход'),
                             ('Номенклатура', {'Идентификатор': 'wrong'}),
                             ('Период', '2000-01-01T00:00:00')]:
            wrong = copy.deepcopy(assignment['checks'][1])
            wrong['id'] = 'wrong_' + field
            call = wrong['expected']['calls'][0 if field == 'Регистратор' else 1]
            call['args'][field] = value
            assignment['checks'].append(wrong)
        wrong = copy.deepcopy(assignment['checks'][1])
        wrong['id'] = 'wrong_order'
        wrong['expected']['calls'].reverse()
        assignment['checks'].append(wrong)
        missing = copy.deepcopy(assignment['checks'][1])
        missing.update(id='unsupported_register_contract', mocks={'registers': ['Отгрузка']})
        assignment['checks'].append(missing)
        resumed = copy.deepcopy(assignment['checks'][0])
        resumed['id'] = 'after_unsupported'
        assignment['checks'].append(resumed)
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root:
            temp, model = Path(directory), analyze(root)
            for check in assignment['checks']:
                check['execution'] = run_pure(root, model, check, temp)
                self.assertEqual(check['execution']['status'],
                    'UNSUPPORTED' if check['id'] == missing['id'] else 'EXECUTED', check['execution'])
            m, a = temp / 'model.json', temp / 'assignment.json'
            write_json(m, model)
            write_json(a, assignment)
            result = execute_engine('test', m, a, temp)
            self.assertEqual([c['status'] for c in result['checks']],
                             ['PASS'] * 4 + ['FAIL'] * 7 + ['UNSUPPORTED', 'PASS'])
            self.assertEqual((result['score'], result['maxScore'], result['unavailablePoints']), (5, 12, 1))
            output = REPO / 'result/poc-movements-negative'
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / 'result.json', result)
            (output / 'report.html').write_text(report(result), encoding='utf-8')
        self.assertEqual(archive.read_bytes(), before)

    def test_real_register_query_handlers_and_negative_grading(self):
        archive = REPO / 'Dvizhok.xdump'
        before = archive.read_bytes()
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root:
            temp = Path(directory)
            model = analyze(root)
            assignment = load_assignment(REPO / 'assignments/poc-platform')
            wrong = copy.deepcopy(assignment['checks'][0])
            wrong.update(id='wrong_replace')
            wrong['expected']['calls'][-1]['args']['Замещать'] = True
            unexpected = copy.deepcopy(assignment['checks'][3])
            unexpected.update(id='wrong_no_exception')
            unexpected['expected']['exception'] = None
            missing_mock = copy.deepcopy(assignment['checks'][1])
            missing_mock.update(id='missing_query', mocks={})
            assignment['checks'].extend([wrong, unexpected, missing_mock])
            for check in assignment['checks']:
                check['execution'] = run_pure(root, model, check, temp)
                status = 'UNSUPPORTED' if check['id'] == 'missing_query' else 'EXECUTED'
                self.assertEqual(check['execution']['status'], status, check['execution'])
            m, a = temp / 'model.json', temp / 'assignment.json'
            m.write_text(json.dumps(model, ensure_ascii=False))
            a.write_text(json.dumps(assignment, ensure_ascii=False))
            result = execute_engine('test', m, a, temp)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS'] * 5 + ['FAIL', 'FAIL', 'UNSUPPORTED'])
            self.assertEqual((result['score'], result['maxScore']), (5, 7))
        self.assertEqual(archive.read_bytes(), before)

    def test_real_handlers_fakes_and_sbsl_grading(self):
        archive = REPO / 'Dvizhok.xdump'
        before = archive.read_bytes()
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root:
            temp = Path(directory)
            model = analyze(root)
            assignment = load_assignment(REPO / 'assignments/poc-handlers')
            wrong = copy.deepcopy(assignment['checks'][0])
            wrong.update(id='wrong_person', expected={'ФИО': 'wrong', 'Телефон': '', 'Почта': ''})
            missing = copy.deepcopy(assignment['checks'][2])
            missing.update(id='missing_deal', args=[{'Идентификатор': 'missing'}])
            escaped = copy.deepcopy(assignment['checks'][2])
            identifier = '${Консоль.Записать("probe")}'
            escaped.update(id='escaped_fake_id', args=[{'Идентификатор': identifier}])
            escaped['mocks']['objects']['Сделка.Ссылка'] = {identifier: {'Клиент': {'Идентификатор': identifier}}}
            escaped['expected'] = {'Клиент': {'Идентификатор': identifier}, 'Сделка': {'Идентификатор': identifier}}
            assignment['checks'].extend([wrong, missing, escaped])
            for check in assignment['checks']:
                check['execution'] = run_pure(root, model, check, temp)
                expected_status = 'ERROR' if check['id'] == 'missing_deal' else 'EXECUTED'
                self.assertEqual(check['execution']['status'], expected_status, check['execution'])
            m, a = temp / 'model.json', temp / 'assignment.json'
            m.write_text(json.dumps(model, ensure_ascii=False))
            a.write_text(json.dumps(assignment, ensure_ascii=False))
            result = execute_engine('test', m, a, temp)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS'] * 5 + ['FAIL', 'ERROR', 'PASS'])
            self.assertEqual((result['score'], result['maxScore'], result['unavailablePoints']), (6, 7, 1))
        self.assertEqual(archive.read_bytes(), before)

    def test_object_context_and_sbsl_grading(self):
        from test_object_context import fixture, checks
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            root = temp / "fixture"
            root.mkdir()
            model = fixture(root)
            assignment = {"name": "Объектный контекст", "checks": checks()}
            wrong = copy.deepcopy(assignment["checks"][1])
            wrong.update(id="wrong_context", expected={"Номер": "wrong"})
            assignment["checks"].append(wrong)
            for check in assignment["checks"]:
                check["execution"] = run_pure(root, model, check, temp)
                self.assertEqual(check["execution"]["status"], "EXECUTED", check["execution"])
            m, a = temp / "model.json", temp / "assignment.json"
            m.write_text(json.dumps(model, ensure_ascii=False))
            a.write_text(json.dumps(assignment, ensure_ascii=False))
            result = execute_engine("test", m, a, temp)
            self.assertEqual([c["status"] for c in result["checks"]], ["PASS", "PASS", "FAIL"])
            self.assertEqual((result["score"], result["maxScore"]), (2, 3))

    def run_case(self, modify):
        archive = REPO / "Dvizhok.xdump"
        before = archive.read_bytes()
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root:
            model = analyze(root)
            check = copy.deepcopy(load_assignment(REPO / "assignments/poc-fio")["checks"][0])
            modify(check, model)
            result = run_pure(root, model, check, Path(directory))
        self.assertEqual(archive.read_bytes(), before)
        return result

    def test_real_method_and_input_interpolation_escape(self):
        text = '${Консоль.Записать("probe")}'
        result = self.run_case(lambda check, model: check.update(args=[text, "", ""]))
        self.assertEqual(result["status"], "EXECUTED", result)
        self.assertEqual(result["actual"], text)

    def test_timeout(self):
        result = self.run_case(lambda check, model: check.update(timeout="0.001s"))
        self.assertEqual(result["status"], "TIMEOUT")

    def test_unconfigured_version(self):
        result = self.run_case(lambda check, model: model.update(compatibilityVersion="999.0"))
        self.assertEqual(result["status"], "UNSUPPORTED")

    def test_order_rows_and_sbsl_grading(self):
        archive = REPO / "Dvizhok.xdump"
        before = archive.read_bytes()
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root:
            temp = Path(directory)
            model = analyze(root)
            assignment = load_assignment(REPO / "assignments/poc-order")
            wrong = copy.deepcopy(assignment["checks"][1])
            wrong.update(id="wrong_total", expected={"Строк": 2, "Товаров": 5, "Сумма": 351})
            assignment["checks"].append(wrong)
            for check in assignment["checks"]:
                check["execution"] = run_pure(root, model, check, temp)
                self.assertEqual(check["execution"]["status"], "EXECUTED", check["execution"])
            m, a = temp / "model.json", temp / "assignment.json"
            m.write_text(json.dumps(model, ensure_ascii=False))
            a.write_text(json.dumps(assignment, ensure_ascii=False))
            result = execute_engine("test", m, a, temp)
            self.assertEqual([c["status"] for c in result["checks"]], ["PASS", "PASS", "PASS", "FAIL"])
            self.assertEqual((result["score"], result["maxScore"]), (3, 4))
        self.assertEqual(archive.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
