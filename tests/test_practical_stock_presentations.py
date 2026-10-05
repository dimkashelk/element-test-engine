"""Stock filtering, ordering and representations from an independent xdump task."""
import copy
import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.generated_types import ProjectTypes
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine, extract_method, prepare_script, run_pure
from element_test.yaml_io import InputError, InvalidTestError


REPO = Path(__file__).resolve().parent.parent
ARCHIVE = REPO / 'Prakticheskie-primery-2026-09-30-15-20.xdump'
ASSIGNMENT = REPO / 'assignments/practical-stock-presentations'
SOURCE = 'МетодыРаботыСКоллекциями/РаботаСКоллекциями.xbsl'
DOCKER = os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1'


class ReadonlyArrayTest(unittest.TestCase):
    def test_invalid_inputs_and_unsupported_elements(self):
        types = ProjectTypes({'elements': [], 'properties': {}})
        types.require('ЧитаемыйМассив<Строка>')
        for value in ({}, 'text', [1], [None]):
            with self.subTest(value=value), self.assertRaises(InvalidTestError):
                types.literal(value, 'ЧитаемыйМассив<Строка>')
        for name in ('ЧитаемыйМассив<Строка,Число>', 'ЧитаемыйМассив<СекретПриложения>'):
            with self.subTest(name=name), self.assertRaises(InputError):
                types.require(name)

    @unittest.skipUnless(DOCKER, 'Docker integration is opt-in')
    def test_readonly_arguments_results_and_independent_number_formatting(self):
        with TemporaryDirectory() as d:
            root = Path(d) / 'source'
            root.mkdir()
            (root / 'Проект.yaml').write_text('Имя: Контракт\nРежимСовместимости: 9.0\n')
            (root / 'Probe.xbsl').write_text(
                'метод Формат(Числа: ЧитаемыйМассив<Число>): ЧитаемыйМассив<Строка>\n'
                '    возврат Числа.Преобразовать(Ч -> "${Ч}")\n;\n'
                'метод Эхо(Строки: ЧитаемыйМассив<Строка>?): ЧитаемыйМассив<Строка>?\n'
                '    возврат Строки\n;\n'
                'метод Вложенный(Строки: ЧитаемыйМассив<ЧитаемыйМассив<Строка>>): ЧитаемыйМассив<ЧитаемыйМассив<Строка>>\n'
                '    возврат Строки\n;\n')
            model = analyze(root)
            cases = [
                ('Формат', [1000, 2000, 1.25], ['1,000', '2,000', '1.25']),
                ('Формат', [], []),
                ('Эхо', ['${probe()}', 'кавычка "\nстрока'], ['${probe()}', 'кавычка "\nстрока']),
                ('Эхо', None, None),
                ('Вложенный', [[], ['А']], [[], ['А']]),
            ]
            for method, value, expected in cases:
                with self.subTest(method=method, value=value):
                    execution = run_pure(root, model, {'target': {'module': 'Probe', 'method': method},
                                                     'args': [value]}, Path(d))
                    self.assertEqual(execution['status'], 'EXECUTED', execution)
                    self.assertEqual(execution['actual'], expected)


@unittest.skipUnless(ARCHIVE.is_file(), 'Требуется исходный Примеры_new xdump')
class PracticalStockPresentationsTest(unittest.TestCase):
    def test_assignment_source_preservation_and_uuid_boundary(self):
        assignment = load_assignment(ASSIGNMENT)
        self.assertEqual((len(assignment['checks']), sum(c['points'] for c in assignment['checks'])), (10, 12))
        with open_project(ARCHIVE) as root, TemporaryDirectory() as d:
            temp, model = Path(d), analyze(root)
            write_json(temp / 'model.json', model)
            write_json(temp / 'assignment.json', {'checks': assignment['checks'][:1]})
            result = execute_engine('test', temp / 'model.json', temp / 'assignment.json', temp)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS'])
            original = (root / SOURCE).read_bytes()
            method = extract_method((root / SOURCE).read_text(encoding='utf-8-sig'),
                                    'ПолучитьПредставленияТоваровВНаличии')[0]
            for check in assignment['checks'][1:]:
                sandbox = temp / check['id']
                sandbox.mkdir()
                script = prepare_script(root, model, check, sandbox).read_text()
                self.assertIn(method, script)
                self.assertIn('структура ДанныеТовара', script)
                self.assertNotIn('метод ПолучитьОбщуюСтоимость', script)
            self.assertEqual((root / SOURCE).read_bytes(), original)
            boundary = load_assignment(ASSIGNMENT / 'fixtures/unsupported.yaml')['checks'][0]
            with patch('element_test.runtime.shutil.which', return_value='docker'):
                execution = run_pure(root, model, boundary, temp)
            self.assertEqual((execution['status'], execution['reasonCode']), ('UNSUPPORTED', 'unsupported_syntax'))
            self.assertIn('Запрос', execution['message'])

    @unittest.skipUnless(DOCKER, 'Docker integration is opt-in')
    def test_reference_and_independent_filter_sort_representation_mutations(self):
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
            self.assertEqual([c['status'] for c in reference['checks']], ['PASS'] * 10)
            self.assertEqual((reference['score'], reference['maxScore'], reference['unavailablePoints']), (12, 12, 0))
            source, original = root / SOURCE, (root / SOURCE).read_bytes()
            mutations = [
                ('Товар.Количество > 0', 'Товар.Количество >= 0',
                 {'example', 'unavailable', 'fractional_quantity'}, 8),
                ('НаправлениеСортировки.ПоУбыванию', 'НаправлениеСортировки.ПоВозрастанию',
                 {'example', 'descending', 'zero_and_negative_cost'}, 7),
                ('В наличии: ${Товар.Наименование}', 'Товар: ${Товар.Наименование}',
                 {'example', 'single_positive', 'descending', 'fractional_quantity',
                  'zero_and_negative_cost', 'duplicates', 'special_name'}, 3),
            ]
            for old, new, failures, score in mutations:
                with self.subTest(mutation=old):
                    self.assertEqual(original.count(old.encode()), 1)
                    source.write_bytes(original.replace(old.encode(), new.encode()))
                    mutated = assess()
                    self.assertEqual({c['id'] for c in mutated['checks'] if c['status'] == 'FAIL'}, failures)
                    self.assertTrue(all(c['status'] == ('FAIL' if c['id'] in failures else 'PASS')
                                        for c in mutated['checks']))
                    self.assertEqual((mutated['score'], mutated['maxScore'], mutated['unavailablePoints']), (score, 12, 0))
                    self.assertTrue(all(c.get('reasonCode') == 'result_mismatch'
                                        for c in mutated['checks'] if c['status'] == 'FAIL'))
            source.write_bytes(original)
        self.assertEqual(hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(), before)


if __name__ == '__main__':
    unittest.main()
