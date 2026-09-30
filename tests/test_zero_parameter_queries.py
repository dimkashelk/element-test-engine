"""A real parameterless XBSL query, executed by Script and graded by SBSL."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import REPO, decode_output, execute_engine, prepare_script, run_pure
from element_test.yaml_io import InvalidTestError, UnsupportedSyntaxError


ARCHIVE = REPO / 'Demo-SRM-dev-2026-09-28-21-38.xdump'
ASSIGNMENT = REPO / 'assignments/demo-srm-stages'


class ZeroParameterQueryTest(unittest.TestCase):
    @unittest.skipUnless(ARCHIVE.is_file() and (REPO / 'script_u_10.0.2_1/lib').is_dir(),
                         'Требуются ДемоСРМ и Script executor')
    def test_real_method_executes_and_sbsl_detects_wrong_expectation(self):
        with open_project(ARCHIVE) as root, tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            model = analyze(root)
            checks = load_assignment(ASSIGNMENT)['checks']
            module = next(m for m in model['modules'] if m['name'] == 'СтадииСделок')
            original = (root / module['sourceFile']).read_bytes()
            for index, check in enumerate(checks):
                generated = temporary / str(index)
                generated.mkdir()
                script = prepare_script(root, model, check, generated)
                self.assertIn('СоздатьЗапрос0(' + ('Стадия' if index >= 2 else '') + ')', script.read_text())
                result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                        cwd=generated, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                check['execution'] = {'status': 'EXECUTED', 'actual': decode_output(result.stdout)['actual']}
            wrong = copy.deepcopy(checks[0])
            wrong['id'] = 'wrong_first_stage'
            wrong['expected']['context']['Идентификатор'] = 'wrong'
            assignment = {'name': 'Начальная стадия', 'checks': [checks[0], wrong] + checks[1:]}
            write_json(temporary / 'model.json', model)
            write_json(temporary / 'assignment.json', assignment)
            result = execute_engine('test', temporary / 'model.json', temporary / 'assignment.json', temporary)
            self.assertEqual([item['status'] for item in result['checks']], ['PASS', 'FAIL'] + ['PASS'] * 3)
            self.assertEqual((result['score'], result['maxScore']), (8, 10))
            self.assertEqual((root / module['sourceFile']).read_bytes(), original)

    @unittest.skipUnless(ARCHIVE.is_file(), 'Требуется ДемоСРМ')
    def test_missing_parameter_contract_is_unavailable_then_valid_check_prepares(self):
        with open_project(ARCHIVE) as root, tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            model = analyze(root)
            check = load_assignment(ASSIGNMENT)['checks'][0]
            bad = copy.deepcopy(check)
            bad['mocks']['queries'][0]['parameterTypes'] = ['Число']
            with self.assertRaisesRegex(InvalidTestError, 'Число типов'):
                prepare_script(root, model, bad, temporary)
            with patch('element_test.runtime.shutil.which', return_value='docker'):
                result = run_pure(root, model, bad, temporary)
            self.assertEqual((result['status'], result['reasonCode']), ('UNSUPPORTED', 'invalid_test'))
            generated = temporary / 'valid'
            generated.mkdir()
            self.assertTrue(prepare_script(root, model, check, generated).is_file())

    def test_malformed_query_parameter_is_not_silently_treated_as_parameterless(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            root = temporary / 'source'
            root.mkdir()
            (root / 'Проект.yaml').write_text('Имя: Query\nПоставщик: Test\nРежимСовместимости: 9.0\n')
            (root / 'Main.xbsl').write_text(
                'метод Проверить(A: Число): Число\n'
                '    возврат Запрос{ВЫБРАТЬ %(A) КАК Число}.Выполнить()[0].Число\n;\n')
            check = {'target': {'module': 'Main', 'method': 'Проверить'}, 'args': [1],
                     'mocks': {'queries': [{'text': 'ВЫБРАТЬ %(A) КАК Число',
                                            'fields': {'Число': 'Число'},
                                            'rows': [{'Число': 1}], 'parameterTypes': []}]}}
            with self.assertRaisesRegex(UnsupportedSyntaxError, 'Неподдержанная форма'):
                prepare_script(root, analyze(root), check, temporary)


if __name__ == '__main__':
    unittest.main()
