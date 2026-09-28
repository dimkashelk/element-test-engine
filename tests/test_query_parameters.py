"""A trusted two-parameter query fixture; student code never runs on the host."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from element_test.bridge import write_json
from element_test.model import analyze
from element_test.runtime import REPO, decode_output, execute_engine, prepare_script, run_pure
from element_test.yaml_io import InvalidTestError, UnsupportedSyntaxError


SOURCE = ("метод Проверить(A: Число, B: Строка): Число\n"
          "    знч Строки = Запрос{ВЫБРАТЬ \"%{literal}\" КАК Подпись, /* %{ignored} */ %{A} КАК A, %{B} КАК B}.Выполнить()\n"
          "    возврат Строки[0].Значение\n;\n")
QUERY = 'ВЫБРАТЬ "%{literal}" КАК Подпись, /* %{ignored} */ %{A} КАК A, %{B} КАК B'


class QueryParametersTest(unittest.TestCase):
    def make_project(self, directory):
        root = directory / 'source'
        root.mkdir()
        (root / 'Проект.yaml').write_text('Имя: Запросы\nПоставщик: Тест\nРежимСовместимости: 9.0\n')
        (root / 'Main.xbsl').write_text(SOURCE)
        return root, analyze(root)

    def check(self, *, rows=None, args=None):
        rows = [{'Значение': 11}] if rows is None else rows
        args = [7, 'x'] if args is None else args
        return {'id': 'two_parameters', 'type': 'runtime', 'points': 1,
                'target': {'module': 'Main', 'method': 'Проверить'}, 'args': args,
                'mocks': {'queries': [{'text': QUERY, 'fields': {'Значение': 'Число'},
                                       'rows': rows, 'parameterTypes': ['Число', 'Строка']}]},
                'captureCalls': True,
                'expected': {'context': rows[0]['Значение'], 'calls': [
                    {'owner': 'query0', 'method': 'Создать',
                     'args': {'Параметр1': args[0], 'Параметр2': args[1]}},
                    {'owner': 'query0', 'method': 'Выполнить', 'args': {}}]}}

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Требуется Script executor')
    def test_two_parameters_execute_and_grade_without_state_leak(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            root, model = self.make_project(temp)
            source_before = (root / 'Main.xbsl').read_bytes()
            checks = [self.check(), self.check(rows=[{'Значение': 19}], args=[9, 'y'])]
            for index, check in enumerate(checks):
                generated = temp / ('generated' + str(index))
                generated.mkdir()
                script = prepare_script(root, model, check, generated)
                self.assertIn('СоздатьЗапрос0(A, B)', script.read_text())
                result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                        cwd=generated, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                check['execution'] = {'status': 'EXECUTED', 'actual': decode_output(result.stdout)['actual']}
            wrong = copy.deepcopy(checks[0])
            wrong['id'] = 'wrong_second_parameter'
            wrong['expected']['calls'][0]['args']['Параметр2'] = 'wrong'
            assignment = {'name': 'Параметры запроса', 'checks': [checks[0], wrong, checks[1]]}
            write_json(temp / 'model.json', model)
            write_json(temp / 'assignment.json', assignment)
            result = execute_engine('test', temp / 'model.json', temp / 'assignment.json', temp)
            self.assertEqual([item['status'] for item in result['checks']], ['PASS', 'FAIL', 'PASS'])
            self.assertEqual((result['score'], result['maxScore']), (2, 3))
            self.assertEqual((root / 'Main.xbsl').read_bytes(), source_before)

    def test_invalid_parameter_contract_is_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            root, model = self.make_project(temp)
            bad = self.check()
            bad['mocks']['queries'][0]['parameterTypes'] = ['Число']
            with self.assertRaisesRegex(InvalidTestError, 'Число типов'):
                prepare_script(root, model, bad, temp)
            both = self.check()
            both['mocks']['queries'][0]['parameterType'] = 'Число'
            with self.assertRaisesRegex(InvalidTestError, 'ровно одно'):
                prepare_script(root, model, both, temp)
            with patch('element_test.runtime.shutil.which', return_value='docker'):
                result = run_pure(root, model, bad, temp)
            self.assertEqual((result['status'], result['reasonCode']), ('UNSUPPORTED', 'invalid_test'))
            resumed = temp / 'resumed'
            resumed.mkdir()
            self.assertTrue(prepare_script(root, model, self.check(), resumed).is_file())

    def test_nested_expression_is_reported_as_unsupported_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            root, model = self.make_project(temp)
            (root / 'Main.xbsl').write_text(SOURCE.replace('%{A}', '%{{A}}'))
            bad = self.check()
            bad['mocks']['queries'][0]['text'] = QUERY.replace('%{A}', '%{{A}}')
            with self.assertRaisesRegex(UnsupportedSyntaxError, 'без вложенных'):
                prepare_script(root, model, bad, temp)
            (root / 'Main.xbsl').write_text(SOURCE.replace('%{A}', '%{ }'))
            bad['mocks']['queries'][0]['text'] = QUERY.replace('%{A}', '%{ }')
            with self.assertRaisesRegex(UnsupportedSyntaxError, 'непустые выражения'):
                prepare_script(root, model, bad, temp)


if __name__ == '__main__':
    unittest.main()
