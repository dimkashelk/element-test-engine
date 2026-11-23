"""Frozen object-form declarations: structure, binding, commands and order."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_constants_full as constants
import test_table_sections_full as common

ASSIGNMENT = common.REPO / 'assignments/object-forms-full'
SPEC = importlib.util.spec_from_file_location('object_forms_full', ASSIGNMENT / 'check_full.py')
FULL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FULL)


class ObjectFormsFullTest(constants.ConstantsFullTest):
    assignment_dir = ASSIGNMENT
    full = FULL
    criterion_count = 326
    leaf_count = 1144
    object_count = 36
    module_count = 11
    mutation_count = 1049
    report_marker = 'без браузера'

    def test_uuid_comments_and_named_declaration_order_are_ignored(self):
        changed = deepcopy(self.reference)
        for _, value in list(common.walk(changed)):
            if isinstance(value, dict) and ('Имя' in value or 'ВидЭлемента' in value):
                value['Ид'] = 'new-id'
        for key, obj in changed['elements'].items():
            if obj['ВидЭлемента'] == 'КомпонентИнтерфейса':
                changed['elements'][key] = self.full.without_ids(obj)
            else:
                for _, value in list(common.walk(obj)):
                    if isinstance(value, list) and value and all(isinstance(v, dict) and 'Имя' in v for v in value):
                        value.reverse()
                changed['elements'][key] = self.full.COMPARISON.normalize(obj)
        changed['project']['Ид'] = 'new-project-id'
        changed['project'] = self.full.COMPARISON.normalize(changed['project'])
        self.assertTrue(all(c['status'] == 'PASS' for c in self.full.check_contract(self.reference, changed)))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_project(root)
            self.assertEqual(self.reference, self.full.snapshot(root, self.model(root)))

    def test_form_inventory_and_binding_map(self):
        coverage = json.loads((self.assignment_dir / 'coverage.json').read_text())
        forms = coverage['forms']
        self.assertEqual(14, len(forms))
        self.assertEqual(10, sum(f['baseType'].startswith('ФормаОбъекта<') for f in forms))
        self.assertEqual(3, sum(f['baseType'].startswith('ФормаЗаписиНабораКонстант<') for f in forms))
        self.assertEqual(187, sum(len(f['components']) for f in forms))
        self.assertEqual(202, sum(len(f['bindings']) for f in forms))
        for form in forms:
            obj = self.reference['elements'][form['identity']]
            for binding in form['bindings']:
                self.assertEqual(binding['expression'], common.at(obj, binding['path']))
            for component in form['components']:
                self.assertEqual(component['type'], common.at(obj, component['path'])['Тип'])

    def test_runner_ignores_nested_form_ids(self):
        with TemporaryDirectory() as directory:
            root, output = Path(directory) / 'project', Path(directory) / 'reports'
            self.write_project(root)
            for name, obj in self.reference['elements'].items():
                if obj['ВидЭлемента'] != 'КомпонентИнтерфейса':
                    continue
                changed = deepcopy(obj)
                for _, value in common.walk(changed):
                    if isinstance(value, dict):
                        value['Ид'] = 'new-id'
                path = root.joinpath(*name.split('::')).with_suffix('.yaml')
                path.write_text(json.dumps(changed, ensure_ascii=False))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, self.full.main(['--project', str(root), '--output', str(output)]))

    def test_every_form_list_preserves_order(self):
        tested = 0
        for name, obj in self.reference['elements'].items():
            if obj['ВидЭлемента'] != 'КомпонентИнтерфейса':
                continue
            for path, value in common.walk(obj):
                if not isinstance(value, list) or len(value) < 2 or value[0] == value[1]:
                    continue
                with self.subTest(form=name, path=path):
                    changed = deepcopy(self.reference)
                    target = common.at(changed['elements'][name], path)
                    target[0], target[1] = target[1], target[0]
                    self.assert_rejected(changed)
                    tested += 1
        self.assertGreater(tested, 30)

    def test_form_owner_field_binding_availability_and_commands(self):
        # All declared formulas/commands are fixed expectations, not evaluated UI.
        for name, obj in self.reference['elements'].items():
            if obj['ВидЭлемента'] != 'КомпонентИнтерфейса':
                continue
            for path, value in common.walk(obj):
                if not path or not isinstance(value, str):
                    continue
                if value.startswith('=') or path == ('Наследует', 'Тип'):
                    with self.subTest(form=name, path=path):
                        changed = deepcopy(self.reference)
                        common.at(changed['elements'][name], path[:-1])[path[-1]] = '=ОшибочнаяПривязка'
                        self.assert_rejected(changed)

    def test_runner_detects_changed_layout_and_missing_form(self):
        with TemporaryDirectory() as directory:
            root, output = Path(directory) / 'project', Path(directory) / 'reports'
            self.write_project(root)
            path = root / 'Товары/НоменклатураФормаОбъекта.yaml'
            obj = json.loads(path.read_text())
            fields = obj['Наследует']['Содержимое']['Содержимое']['Содержимое']
            fields[0], fields[1] = fields[1], fields[0]
            path.write_text(json.dumps(obj, ensure_ascii=False))
            for operation in ('layout', 'missing'):
                with self.subTest(operation=operation):
                    if operation == 'missing':
                        path.unlink()
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(1, self.full.main(['--project', str(root), '--output', str(output)]))
                    result = json.loads((output / 'full-result.json').read_text())
                    self.assertEqual('failed', result['status'])
                    self.assertTrue(any(c['status'] == 'FAIL' for c in result['strictChecks']))


if __name__ == '__main__':
    unittest.main()
