"""Validate the lesson 03 contract without relying on an external input dump."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from element_test.assignment import load_assignment
from element_test.bridge import canonicalize_model_types
from element_test.model import analyze
from element_test.runtime import execute_engine

REPO = Path(__file__).resolve().parent.parent
ASSIGNMENT = REPO / 'assignments/catalogs-full'
SPEC = importlib.util.spec_from_file_location('catalogs_full', ASSIGNMENT / 'check_full.py')
FULL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FULL)


class CatalogsFullTest(unittest.TestCase):
    def setUp(self):
        self.reference = json.loads((ASSIGNMENT / 'reference.json').read_text(encoding='utf-8'))
        self.assignment = load_assignment(ASSIGNMENT)

    def write_project(self, root):
        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        save(root / 'Проект.yaml', self.reference['project'])
        for key, value in self.reference['subsystems'].items():
            save(root.joinpath(*key.split('::')) / 'Подсистема.yaml', value)
        for key, value in self.reference['elements'].items():
            save(root.joinpath(*key.split('::')).with_suffix('.yaml'), value)
        for key in self.reference['modules']:
            path = root.joinpath(*key.split('::')).with_suffix('.xbsl')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('// Только комментарий\n', encoding='utf-8')

    def assert_rejected(self, candidate):
        self.assertTrue(any(check['status'] == 'FAIL'
                            for check in FULL.check_contract(self.reference, candidate)))

    def test_all_reference_values_are_enforced(self):
        def leaves(value, path=()):
            if isinstance(value, dict):
                for key, item in value.items():
                    yield from leaves(item, path + (key,))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    yield from leaves(item, path + (index,))
            else:
                yield path, value
        for path, value in leaves(self.reference):
            with self.subTest(path=path):
                changed = deepcopy(self.reference)
                parent = changed
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = not value if isinstance(value, bool) else (
                    value + 1 if isinstance(value, (int, float)) else value + '_ошибка')
                self.assert_rejected(changed)

    def test_extra_and_missing_members_and_properties_are_rejected(self):
        for section in ('project', 'subsystems', 'elements', 'modules', 'serviceFiles'):
            with self.subTest(section=section, change='extra'):
                changed = deepcopy(self.reference)
                changed[section]['Лишнее'] = {}
                self.assert_rejected(changed)
            if self.reference[section]:
                with self.subTest(section=section, change='missing'):
                    changed = deepcopy(self.reference)
                    del changed[section][next(iter(changed[section]))]
                    self.assert_rejected(changed)
        for operation in ('extra_field', 'missing_field', 'extra_property', 'table_section'):
            with self.subTest(operation=operation):
                changed = deepcopy(self.reference)
                element = changed['elements']['Товары::Номенклатура']
                if operation == 'extra_field':
                    element['Реквизиты'].append({'Имя': 'Лишний', 'Тип': 'Строка'})
                elif operation == 'missing_field':
                    element['Реквизиты'].pop()
                elif operation == 'extra_property':
                    element['Реквизиты'][0]['МаксимальнаяДлина'] = 17
                else:
                    element['ТабличныеЧасти'] = [{'Имя': 'Лишняя'}]
                self.assert_rejected(changed)
        changed = deepcopy(self.reference)
        changed['diagnostics'].append({'code': 'unresolved_type'})
        self.assert_rejected(changed)

    def test_bool_is_not_a_number_and_duplicate_fields_are_not_lost(self):
        changed = deepcopy(self.reference)
        changed['elements']['CRM::Контрагенты']['Иерархический'] = 1
        self.assert_rejected(changed)
        changed = deepcopy(self.reference)
        fields = changed['elements']['CRM::Контрагенты']['Реквизиты']
        fields.append(deepcopy(fields[0]))
        self.assert_rejected(changed)

    def test_uuid_and_declaration_order_are_ignored(self):
        changed = deepcopy(self.reference)
        changed['project']['Ид'] = 'new-project-id'
        for element in changed['elements'].values():
            element['Ид'] = 'new-object-id'
            element['Реквизиты'].reverse()
            for field in element['Реквизиты']:
                field['Ид'] = 'new-field-id'
        checks = FULL.check_contract(self.reference, FULL.normalize(changed))
        self.assertTrue(all(check['status'] == 'PASS' for check in checks))

    def test_project_snapshot_comments_and_code(self):
        with TemporaryDirectory() as work:
            root = Path(work)
            self.write_project(root)
            # Actual dump has empty subsystem containers, which the loader accepts.
            (root / 'CRM/Подсистема.yaml').write_text('', encoding='utf-8')
            model = analyze(root)
            canonicalize_model_types(model)
            candidate = FULL.snapshot(root, model)
            self.assertEqual([], FULL.differences(self.reference, candidate))
            (root / 'Проект.xbsl').write_text('метод Лишний()\n;\n', encoding='utf-8')
            self.assert_rejected(FULL.snapshot(root, model))

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Requires Script executor')
    def test_full_runner_status_reports_and_read_only_input(self):
        with TemporaryDirectory() as work:
            root, output = Path(work) / 'project', Path(work) / 'output'
            self.write_project(root)
            initial = {path.relative_to(root): path.read_bytes()
                       for path in root.rglob('*') if path.is_file()}
            args = ['--project', str(root), '--output', str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, FULL.main(args))
            self.assertEqual(initial, {path.relative_to(root): path.read_bytes()
                                      for path in root.rglob('*') if path.is_file()})
            self.assertTrue((output / 'report.html').is_file())
            self.assertTrue((output / 'structural/report.html').is_file())
            path = root / 'Товары/Номенклатура.yaml'
            properties = json.loads(path.read_text(encoding='utf-8'))
            properties['Реквизиты'].append({'Имя': 'Лишний', 'Тип': 'Строка'})
            path.write_text(json.dumps(properties, ensure_ascii=False), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(1, FULL.main(args))
            result = json.loads((output / 'full-result.json').read_text(encoding='utf-8'))
            self.assertEqual('failed', result['status'])
            self.assertEqual((51, 51), (result['structural']['score'], result['structural']['maxScore']))
            elements = next(check for check in result['strictChecks'] if check['id'] == 'elements')
            self.assertEqual('FAIL', elements['status'])
            self.assertIn('Лишний', json.dumps(elements, ensure_ascii=False))
            with redirect_stderr(io.StringIO()):
                self.assertEqual(2, FULL.main(['--project', str(root), '--output', str(root / 'reports')]))
            self.assertFalse((root / 'reports').exists())

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Requires Script executor')
    def test_each_structural_criterion_detects_its_own_mutation(self):
        with TemporaryDirectory() as work:
            temporary = Path(work)
            root = temporary / 'project'
            self.write_project(root)
            model = analyze(root)
            canonicalize_model_types(model)
            # Each library is an isolated mutated model; all 51 checks run in one
            # real SBSL invocation, keeping scoring in the existing engine.
            mutations = {'name': 'Мутации', 'sourceHash': 'fixture', 'elements': [],
                         'modules': [], 'diagnostics': [], 'libraries': []}
            checks = deepcopy(self.assignment['checks'])
            self.assertEqual(51, len(checks))
            for check in checks:
                changed = deepcopy(model)
                if check['type'] == 'module':
                    changed['modules'] = [item for item in changed['modules']
                                          if not (item['name'] == check['module'] and
                                                  item['namespace'] == check['namespace'])]
                else:
                    name = check.get('element', check.get('name'))
                    element = next(item for item in changed['elements'] if FULL.identity(item) == name)
                    if check['type'] == 'element' and not check['properties']:
                        changed['elements'].remove(element)
                    elif check['type'] == 'element':
                        del element['properties'][next(iter(check['properties']))]
                    else:
                        fields = element['properties']['Реквизиты']
                        field = next(item for item in fields if item['Имя'] == check['field'])
                        if check['properties']:
                            del field[next(iter(check['properties']))]
                        elif 'expectedType' in check:
                            field['Тип'] = 'Булево'
                        else:
                            fields.remove(field)
                selector = {'provider': 'Mutation', 'name': check['id'], 'version': '1'}
                check['library'] = selector
                mutations['libraries'].append({**selector, 'model': changed})
            model_path, assignment_path = temporary / 'model.json', temporary / 'assignment.json'
            model_path.write_text(json.dumps(mutations, ensure_ascii=False), encoding='utf-8')
            assignment_path.write_text(json.dumps({'checks': checks}, ensure_ascii=False), encoding='utf-8')
            result = execute_engine('test', model_path, assignment_path, temporary)
            self.assertEqual(51, len(result['checks']))
            for check in result['checks']:
                with self.subTest(criterion=check['id']):
                    self.assertEqual('FAIL', check['status'])
            self.assertEqual((0, 51, 0),
                             (result['score'], result['maxScore'], result['unavailablePoints']))


if __name__ == '__main__':
    unittest.main()
