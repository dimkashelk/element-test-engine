"""Independent cost-sum assessment and the narrow same-module structure contract."""
import copy
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine, extract_method, prepare_script, run_pure
from element_test.yaml_io import InputError, InvalidTestError


REPO = Path(__file__).resolve().parent.parent
ARCHIVE = REPO / 'Prakticheskie-primery-2026-09-30-15-20.xdump'
ASSIGNMENT = REPO / 'assignments/practical-total-cost'
SOURCE = 'МетодыРаботыСКоллекциями/РаботаСКоллекциями.xbsl'


class LocalStructureTest(unittest.TestCase):
    def prepare(self, root, declaration, args=None, extra=''):
        source = declaration + '\nметод Главный(Товары: Обходимое<Товар>): Число\n' \
            '    возврат Товары.Преобразовать(Т -> Т.Стоимость).Сумма()\n;\n' + extra
        (root / 'Модуль.xbsl').write_text(source)
        model = {'properties': {}, 'elements': [], 'modules': [
            {'name': 'Модуль', 'namespace': 'А', 'sourceFile': 'Модуль.xbsl', 'moduleType': 'module'}]}
        check = {'target': {'module': 'Модуль', 'method': 'Главный'}, 'args': [args if args is not None else []]}
        return prepare_script(root, model, check, root).read_text()

    def test_verbatim_structure_and_method_with_typed_empty_input(self):
        declaration = 'структура Товар\n    // Комментарий сохраняется\n    пер Стоимость: Число\n;'
        with TemporaryDirectory() as d:
            root = Path(d)
            script = self.prepare(root, declaration, extra='структура Лишний\n    пер Ид: Ууид\n;\n')
            self.assertIn(declaration, script)
            source = (root / 'Модуль.xbsl').read_text()
            self.assertIn(extract_method(source, 'Главный')[0], script)
            self.assertIn('новый Массив<Товар>([])', script)
            self.assertNotIn('структура Лишний', script)
            self.assertNotIn('структура НеСуществует', script)

    def test_invalid_arguments_are_teacher_errors(self):
        for rows in ({}, [{'Лишнее': 1}], [{'Стоимость': True}], [{'Стоимость': '${код()}'}]):
            with self.subTest(rows=rows), TemporaryDirectory() as d:
                with self.assertRaises(InvalidTestError):
                    self.prepare(Path(d), 'структура Товар\n    пер Стоимость: Число\n;', rows)

    def test_unsupported_structure_semantics_are_not_silently_dropped(self):
        for declaration in (
            'структура Товар\n    пер Стоимость: Число = 100\n;',
            'структура Товар\n    пер Стоимость: СекретПриложения\n;',
            '@ВПроекте\nструктура Товар\n    пер Стоимость: Число\n;',
            'структура Товар\n    пер Стоимость: Число\n    метод М(): Число\n        возврат 1\n    ;\n;',
            'структура Товар\n    пер Стоимость: Число\n',
            'структура Товар\n    пер Стоимость: Число\n    пер Стоимость: Число\n;',
            'структура Товар\n;\nструктура Товар\n;'
        ):
            with self.subTest(declaration=declaration), TemporaryDirectory() as d:
                with self.assertRaises(InputError):
                    self.prepare(Path(d), declaration)

    def test_external_module_cannot_reuse_same_named_target_structure(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / 'Главный.xbsl').write_text('структура Товар\n    пер Стоимость: Число\n;\n'
                'метод Главный(Товары: Обходимое<Товар>): Число\n    возврат Другой.Главный(Товары)\n;\n')
            (root / 'Другой.xbsl').write_text('@Глобально\nметод Главный(Товары: Обходимое<Товар>): Число\n    возврат 0\n;\n')
            model = {'properties': {}, 'elements': [], 'modules': [
                {'name': name, 'namespace': 'А', 'sourceFile': name + '.xbsl', 'moduleType': 'module'}
                for name in ('Главный', 'Другой')]}
            with self.assertRaisesRegex(InputError, 'другого модуля'):
                prepare_script(root, model, {'target': {'module': 'Главный', 'method': 'Главный'}, 'args': [[]]}, root)


@unittest.skipUnless(ARCHIVE.is_file(), 'Требуется исходный Примеры_new xdump')
class PracticalTotalCostTest(unittest.TestCase):
    def test_assignment_and_real_unsupported_boundary(self):
        assignment = load_assignment(ASSIGNMENT)
        self.assertEqual((len(assignment['checks']), sum(c['points'] for c in assignment['checks'])), (9, 10))
        with open_project(ARCHIVE) as root, TemporaryDirectory() as d:
            temp, model = Path(d), analyze(root)
            write_json(temp / 'model.json', model)
            write_json(temp / 'assignment.json', {'checks': assignment['checks'][:1]})
            result = execute_engine('test', temp / 'model.json', temp / 'assignment.json', temp)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS'])
            original = (root / SOURCE).read_bytes()
            for check in assignment['checks'][1:]:
                sandbox = temp / check['id']
                sandbox.mkdir()
                script = prepare_script(root, model, check, sandbox).read_text()
                self.assertIn('структура ДанныеТовара', script)
                self.assertIn(extract_method((root / SOURCE).read_text(encoding='utf-8-sig'), 'ПолучитьОбщуюСтоимость')[0], script)
            self.assertEqual((root / SOURCE).read_bytes(), original)
            boundary = load_assignment(ASSIGNMENT / 'fixtures/unsupported.yaml')['checks'][0]
            with patch('element_test.runtime.shutil.which', return_value='docker'):
                execution = run_pure(root, model, boundary, temp)
            self.assertEqual((execution['status'], execution['reasonCode']), ('UNSUPPORTED', 'unsupported_syntax'))
            self.assertIn('Nullable-ссылка иерархии', execution['message'])

    @unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Docker integration is opt-in')
    def test_reference_and_mutated_cost_rule(self):
        before = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
        with open_project(ARCHIVE) as root, TemporaryDirectory() as d:
            temp, model = Path(d), analyze(root)
            checks = copy.deepcopy(load_assignment(ASSIGNMENT)['checks'])
            def assess():
                for check in checks[1:]:
                    check['execution'] = run_pure(root, model, check, temp)
                    self.assertEqual(check['execution']['status'], 'EXECUTED', check['execution'])
                write_json(temp / 'model.json', model)
                write_json(temp / 'assignment.json', {'checks': checks})
                return execute_engine('test', temp / 'model.json', temp / 'assignment.json', temp)
            reference = assess()
            self.assertEqual([c['status'] for c in reference['checks']], ['PASS'] * 9)
            self.assertEqual((reference['score'], reference['maxScore'], reference['unavailablePoints']), (10, 10, 0))
            source = root / SOURCE
            original = source.read_bytes()
            old = 'Товар -> Товар.Стоимость).Сумма()'.encode()
            self.assertEqual(original.count(old), 1)
            source.write_bytes(original.replace(old, 'Товар -> Товар.Стоимость * Товар.Количество).Сумма()'.encode()))
            mutated = assess()
            self.assertEqual([c['status'] for c in mutated['checks']],
                             ['PASS', 'FAIL', 'PASS', 'FAIL', 'PASS', 'FAIL', 'FAIL', 'PASS', 'FAIL'])
            self.assertEqual((mutated['score'], mutated['maxScore'], mutated['unavailablePoints']), (4, 10, 0))
        self.assertEqual(hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
