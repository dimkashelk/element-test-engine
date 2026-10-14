"""Authored failure feedback reaches students only after an assessed FAIL."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from element_test.assignment import load_assignment
from element_test.bridge import report, run_test
from element_test.grading import grading_package
from element_test.runtime import execute_engine, REPO
from element_test.yaml_io import InputError


class FailureExplanationTest(unittest.TestCase):
    def test_assignment_accepts_multiline_feedback_in_split_files(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            (root / 'assignment.yaml').write_text('name: Example\nchecks: []\n')
            (root / 'structural').mkdir()
            text = 'Добавьте реквизит Клиент.\nТип: Контрагенты.Ссылка?.\n'
            (root / 'structural/fields.yaml').write_text(
                'id: client\ntype: field\nfailureExplanation: |\n'
                '  Добавьте реквизит Клиент.\n  Тип: Контрагенты.Ссылка?.\n')
            self.assertEqual(load_assignment(root)['checks'][0]['failureExplanation'], text)

    def test_assignment_rejects_empty_or_nontext_feedback(self):
        with TemporaryDirectory() as work:
            path = Path(work) / 'assignment.json'
            for value in (None, True, 4, [], {}, '', ' \n\t'):
                with self.subTest(value=value):
                    path.write_text(json.dumps({'checks': [{'id': 'client', 'type': 'field',
                                                           'failureExplanation': value}]}))
                    with self.assertRaisesRegex(InputError, 'client: failureExplanation'):
                        load_assignment(path)

    def test_transport_preserves_diagnostics_and_redacts_feedback(self):
        result = {'project': 'Example', 'sourceHash': 'a' * 64, 'status': 'failed',
                  'score': 1, 'maxScore': 2, 'unavailablePoints': 0, 'checks': [
                      {'id': 'fail', 'status': 'FAIL', 'points': 1, 'score': 0,
                       'message': 'Тип члена не совпадает', 'expected': 'Число', 'actual': 'Строка',
                       'failureExplanation': 'Исправьте реквизит /Users/private/example'},
                      {'id': 'pass', 'status': 'PASS', 'points': 1, 'score': 1,
                       'failureExplanation': 'HIDDEN_ON_PASS'}]}
        package = grading_package(result, {'name': 'Example'}, {'compatibilityVersion': '9.0'})
        self.assertEqual(package['feedback'][0]['failureExplanation'], '[redacted]')
        self.assertEqual(package['feedback'][0]['message'], 'Тип члена не совпадает')
        self.assertEqual((package['feedback'][0]['expected'], package['feedback'][0]['actual']),
                         ('Число', 'Строка'))
        self.assertNotIn('failureExplanation', package['feedback'][1])
        self.assertNotIn('HIDDEN_ON_PASS', report(result, package))

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Requires Script executor')
    def test_sbsl_attaches_feedback_only_to_failed_criteria(self):
        checks = [
            {'id': 'structural', 'type': 'element', 'name': 'Missing'},
            {'id': 'runtime', 'type': 'runtime', 'expected': 1,
             'execution': {'status': 'EXECUTED', 'actual': 2}},
            {'id': 'pass', 'type': 'element', 'name': 'Missing', 'exists': False},
            {'id': 'error', 'type': 'runtime', 'execution': {'status': 'ERROR', 'message': 'execution error'}},
            {'id': 'timeout', 'type': 'runtime', 'execution': {'status': 'TIMEOUT'}},
            {'id': 'unsupported', 'type': 'query'},
            {'id': 'skipped', 'type': 'element', 'skip': True},
            {'id': 'legacy', 'type': 'element', 'name': 'Missing'}]
        for check in checks:
            check.update(points=1, properties={})
            if check['id'] != 'legacy':
                check['failureExplanation'] = 'Проверьте ' + check['id']
        with TemporaryDirectory() as work:
            root = Path(work)
            model, assignment = root / 'model.json', root / 'assignment.json'
            model.write_text(json.dumps({'name': 'Example', 'sourceHash': 'fixture',
                                         'elements': [], 'modules': [], 'diagnostics': []}))
            assignment.write_text(json.dumps({'checks': checks}, ensure_ascii=False))
            result = execute_engine('test', model, assignment, root)
        self.assertEqual([c['status'] for c in result['checks']],
                         ['FAIL', 'FAIL', 'PASS', 'ERROR', 'TIMEOUT', 'UNSUPPORTED', 'SKIPPED', 'FAIL'])
        self.assertEqual((result['score'], result['maxScore'], result['unavailablePoints']), (1, 4, 4))
        for check in result['checks']:
            self.assertEqual('failureExplanation' in check, check['id'] in {'structural', 'runtime'})
        self.assertIn('message', result['checks'][0])
        self.assertEqual((result['checks'][1]['expected'], result['checks'][1]['actual']), (1, 2))

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Requires Script executor')
    def test_public_run_emits_escaped_feedback_and_valid_grading(self):
        import jsonschema
        with TemporaryDirectory() as work:
            base = Path(work)
            project = base / 'project'
            project.mkdir()
            source = project / 'Проект.yaml'
            source.write_text('Имя: Пример\nРежимСовместимости: 9.0\n')
            before = source.read_bytes()
            assignment = base / 'assignment.yaml'
            explanation = 'Добавьте документ Заказ.\n<script>alert("x")</script> & текст'
            assignment.write_text(json.dumps({'name': 'Example', 'checks': [
                {'id': 'missing', 'type': 'element', 'name': 'Заказ', 'points': 2,
                 'failureExplanation': explanation},
                {'id': 'absence', 'type': 'element', 'name': 'Нет', 'exists': False,
                 'failureExplanation': 'HIDDEN_ON_PASS'}]}, ensure_ascii=False))
            output = base / 'output'
            result, package = run_test(project, assignment, output)
            for path, data in (('result.json', result), ('grading.json', package)):
                self.assertEqual(json.loads((output / path).read_text()), data)
            self.assertEqual(result['checks'][0]['failureExplanation'], explanation)
            self.assertEqual(package['feedback'][0]['failureExplanation'], explanation)
            self.assertNotIn('failureExplanation', result['checks'][1])
            self.assertNotIn('failureExplanation', package['feedback'][1])
            schema = json.loads((REPO / 'docs/grading-v1.schema.json').read_text())
            jsonschema.Draft202012Validator(schema).validate(package)
            for rendered in (report(result), (output / 'report.html').read_text()):
                self.assertIn('<strong>Пояснение:</strong>', rendered)
                self.assertIn('&lt;script&gt;', rendered)
                self.assertNotIn('<script>', rendered)
                self.assertNotIn('HIDDEN_ON_PASS', rendered)
                self.assertEqual(rendered.count('<div class="failure-explanation">'), 1)
            self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
