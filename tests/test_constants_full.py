"""Lesson 07: complete non-UI contract and constant-specific regressions."""
from copy import deepcopy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_table_sections_full as common

ASSIGNMENT = common.REPO / 'assignments/constants-full'
SPEC = importlib.util.spec_from_file_location('constants_full', ASSIGNMENT / 'check_full.py')
FULL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FULL)


class ConstantsFullTest(common.TableSectionsFullTest):
    assignment_dir = ASSIGNMENT
    full = FULL
    criterion_count = 272
    leaf_count = 456
    object_count = 22
    module_count = 10
    mutation_count = 350

    def test_constant_names_types_defaults_and_collection(self):
        expected = {
            'ДанныеКомпании': ['ПолноеНаименование', 'СокращенноеНаименование', 'Телефон', 'ЮрАдрес'],
            'Настройки': ['АвтоматическиАрхивироватьЗавершенныеПроцессы', 'РазрешитьОтрицательныеОстатки'],
            'РабочиеДни': ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'],
        }
        fields = [c for c in self.assignment['checks'] if c.get('collection') == 'Константы']
        self.assertEqual(22, len(fields))  # 13 typed declarations and 9 explicit defaults.
        for name, members in expected.items():
            obj = self.reference['elements']['Администрирование::' + name]
            self.assertEqual('НаборКонстант', obj['ВидЭлемента'])
            self.assertCountEqual(members, [f['Имя'] for f in obj['Константы']])
            for field in obj['Константы']:
                self.assertEqual('Строка' if name == 'ДанныеКомпании' else 'Булево', field['Тип'])
                if name != 'ДанныеКомпании':
                    self.assertIs(field['ЗначениеПоУмолчанию'], False)
                else:
                    self.assertNotIn('ЗначениеПоУмолчанию', field)

    def test_constants_cannot_be_substituted_by_object_fields(self):
        for name, obj in self.reference['elements'].items():
            if 'Константы' not in obj:
                continue
            for operation in ('move', 'extra', 'nullable', 'numeric_default'):
                with self.subTest(owner=name, operation=operation):
                    changed = deepcopy(self.reference)
                    target = changed['elements'][name]
                    if operation == 'move':
                        target['Реквизиты'] = target.pop('Константы')
                    elif operation == 'extra':
                        target['Константы'].append({'Имя': 'ЛишняяКонстанта', 'Тип': 'Строка'})
                    elif operation == 'nullable':
                        target['Константы'][0]['Тип'] += '?'
                    else:
                        target['Константы'][0]['ЗначениеПоУмолчанию'] = 0
                    self.assert_rejected(changed)

    def test_runner_rejects_extra_constant_and_missing_default(self):
        with TemporaryDirectory() as directory:
            root, output = Path(directory) / 'project', Path(directory) / 'reports'
            self.write_project(root)
            path = root / 'Администрирование/Настройки.yaml'
            original = json.loads(path.read_text(encoding='utf-8'))
            for operation in ('extra', 'missing_default'):
                with self.subTest(operation=operation):
                    changed = deepcopy(original)
                    if operation == 'extra':
                        changed['Константы'].append({'Имя': 'ЛишняяКонстанта', 'Тип': 'Строка'})
                    else:
                        del changed['Константы'][0]['ЗначениеПоУмолчанию']
                    path.write_text(json.dumps(changed, ensure_ascii=False), encoding='utf-8')
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(1, self.full.main(['--project', str(root), '--output', str(output)]))
                    result = json.loads((output / 'full-result.json').read_text(encoding='utf-8'))
                    self.assertEqual('failed', result['status'])
                    self.assertEqual(self.criterion_count if operation == 'extra' else self.criterion_count - 1,
                                     result['structural']['score'])
                    self.assertTrue(any(c['status'] == 'FAIL' for c in result['strictChecks']))


if __name__ == '__main__':
    unittest.main()
