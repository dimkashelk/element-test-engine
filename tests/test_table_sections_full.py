"""Frozen dump contract: positive cases, negative cases and SBSL mutation checks."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
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

REPO = Path(__file__).resolve().parents[1]
ASSIGNMENT = REPO / 'assignments/table-sections-full'
SPEC = importlib.util.spec_from_file_location('table_sections_full', ASSIGNMENT / 'check_full.py')
FULL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FULL)


def walk(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, path + (index,))


def at(value, path):
    for key in path:
        value = value[key]
    return value


def wrong(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if value is None:
        return 'ошибка'
    return value + '_ошибка'


def selected(model, check):
    owner = check.get('element', check.get('name')).split('.')
    element = next(e for e in model['elements']
                   if FULL.COMPARISON.identity(e) == owner[0])
    target, members = element['properties'], None
    if len(owner) > 1:
        target = next(t for t in target['ТабличныеЧасти'] if t['Имя'] == owner[1])
    if check['type'] in {'field', 'enumValue', 'tableSection'}:
        collection = {'field': check.get('collection', 'Реквизиты'), 'enumValue': 'Элементы',
                      'tableSection': 'ТабличныеЧасти'}[check['type']]
        members = target[collection]
        target = next(m for m in members if m['Имя'] == check.get('field', check.get('name')))
    return element, target, members


class TableSectionsFullTest(unittest.TestCase):
    assignment_dir = ASSIGNMENT
    full = FULL
    criterion_count = 241
    leaf_count = 409
    object_count = 19
    module_count = 7
    mutation_count = 319
    report_marker = 'без интерфейса'

    def setUp(self):
        self.reference = json.loads((self.assignment_dir / 'reference.json').read_text(encoding='utf-8'))
        self.assignment = load_assignment(self.assignment_dir)

    def write_project(self, root):
        def save(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        save(root / 'Проект.yaml', self.reference['project'])
        for namespace, value in self.reference['subsystems'].items():
            save(root.joinpath(*namespace.split('::')) / 'Подсистема.yaml', value)
        for name, value in self.reference['elements'].items():
            save(root.joinpath(*name.split('::')).with_suffix('.yaml'), value)
        for name in self.reference['modules']:
            path = root.joinpath(*name.split('::')).with_suffix('.xbsl')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('// Комментарий не является бизнес-логикой\n', encoding='utf-8')

    def model(self, root):
        model = analyze(root)
        canonicalize_model_types(model)
        return model

    def assert_rejected(self, candidate):
        self.assertTrue(any(c['status'] == 'FAIL'
                            for c in self.full.check_contract(self.reference, candidate)))

    def test_complete_inventory_and_frozen_criteria(self):
        coverage = json.loads((self.assignment_dir / 'coverage.json').read_text(encoding='utf-8'))
        self.assertEqual(self.criterion_count, len(self.assignment['checks']))
        self.assertEqual(self.leaf_count, sum(not isinstance(v, (dict, list)) for _, v in walk(self.reference)))
        self.assertEqual(self.object_count, len(self.reference['elements']))
        self.assertEqual(self.module_count, len(self.reference['modules']))
        sections = [(name, table) for name, obj in self.reference['elements'].items()
                    for table in obj.get('ТабличныеЧасти', [])]
        self.assertEqual(5, len(sections))
        self.assertEqual(20, sum(len(table['Реквизиты']) for _, table in sections))
        self.assertEqual(56, sum(len(obj.get('Реквизиты', []))
                                 for obj in self.reference['elements'].values()))
        self.assertEqual(21, sum(len(obj.get('Элементы', []))
                                 for obj in self.reference['elements'].values()))
        self.assertFalse(any(key == 'Интерфейс' for path, _ in walk(self.reference) for key in path))
        self.assertTrue(all(c['type'] != 'runtime' and 'Интерфейс' not in c['properties']
                            and c['failureExplanation'] for c in self.assignment['checks']))
        matrix_ids = [ident for row in coverage['declarations'] for ident in row['checks']]
        self.assertCountEqual([c['id'] for c in self.assignment['checks']], matrix_ids)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_project(root)
            self.assertCountEqual([str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()],
                                  {row['sourceFile'] for row in coverage['declarations']})
            self.assertEqual(0, sum(len(m['methods']) for m in self.model(root)['modules']))

    def test_each_scalar_value_is_enforced(self):
        for path, value in walk(self.reference):
            if not path or isinstance(value, (dict, list)):
                continue
            with self.subTest(path=path):
                changed = deepcopy(self.reference)
                at(changed, path[:-1])[path[-1]] = wrong(value)
                self.assert_rejected(changed)

    def test_each_property_and_list_member_is_required(self):
        for path, value in walk(self.reference):
            if not path:
                continue
            with self.subTest(path=path):
                changed = deepcopy(self.reference)
                del at(changed, path[:-1])[path[-1]]
                self.assert_rejected(changed)

    def test_extra_properties_and_duplicate_members_are_rejected(self):
        for path, value in walk(self.reference):
            if not isinstance(value, (dict, list)):
                continue
            with self.subTest(path=path):
                changed = deepcopy(self.reference)
                target = at(changed, path)
                if isinstance(value, dict):
                    if not path:
                        continue  # Snapshot sections are fixed by the runner.
                    target['ЛишняяНастройка'] = 'ошибка'
                else:
                    target.append(deepcopy(value[0]) if value else {'Имя': 'Лишний'})
                self.assert_rejected(changed)

    def test_uuid_comments_and_named_declaration_order_are_ignored(self):
        changed = deepcopy(self.reference)
        for _, value in list(walk(changed)):
            if isinstance(value, dict) and ('Имя' in value or 'ВидЭлемента' in value):
                value['Ид'] = 'new-id'
            elif isinstance(value, list) and value and all(isinstance(v, dict) and 'Имя' in v for v in value):
                value.reverse()
        changed['project']['Ид'] = 'new-project-id'
        normalized = self.full.COMPARISON.normalize(changed)
        self.assertTrue(all(c['status'] == 'PASS' for c in self.full.check_contract(self.reference, normalized)))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_project(root)
            self.assertEqual(self.reference, self.full.snapshot(root, self.model(root)))

    def test_import_order_and_duplicates_are_preserved(self):
        for path, value in walk(self.reference):
            if not isinstance(value, list) or not value or isinstance(value[0], dict):
                continue
            for operation in ('reverse', 'append', 'remove'):
                with self.subTest(path=path, operation=operation):
                    changed = deepcopy(self.reference)
                    target = at(changed, path)
                    if operation == 'reverse':
                        target.reverse()
                        if target == value:
                            continue
                    elif operation == 'append':
                        target.append(value[0])
                    else:
                        target.pop()
                    self.assert_rejected(self.full.COMPARISON.normalize(changed))

    def test_boolean_is_not_numeric(self):
        for path, value in walk(self.reference):
            if isinstance(value, bool):
                with self.subTest(path=path):
                    changed = deepcopy(self.reference)
                    at(changed, path[:-1])[path[-1]] = int(value)
                    self.assert_rejected(changed)

    def test_interface_settings_are_excluded_and_code_is_detected(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_project(root)
            path = root / 'Товары/Отгрузка.yaml'
            obj = json.loads(path.read_text(encoding='utf-8'))
            obj['Интерфейс'] = {'ВключатьВАвтоИнтерфейс': False, 'ПроизвольнаяНастройка': True}
            path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
            self.assertEqual(self.reference, self.full.snapshot(root, self.model(root)))
            for name in self.reference['modules']:
                with self.subTest(module=name):
                    module = root.joinpath(*name.split('::')).with_suffix('.xbsl')
                    module.write_text('метод Лишний()\n;\n', encoding='utf-8')
                    self.assert_rejected(self.full.snapshot(root, self.model(root)))
                    module.write_text('// Комментарий\n', encoding='utf-8')

    def test_table_column_type_nullable_and_constraints_are_enforced(self):
        for name, obj in self.reference['elements'].items():
            for ti, table in enumerate(obj.get('ТабличныеЧасти', [])):
                for fi, field in enumerate(table['Реквизиты']):
                    path = ('elements', name, 'ТабличныеЧасти', ti, 'Реквизиты', fi)
                    for kind in ('wrong_type', 'nullable', 'extra_constraint'):
                        with self.subTest(table=table['Имя'], field=field['Имя'], kind=kind):
                            changed = deepcopy(self.reference)
                            target = at(changed, path)
                            if kind == 'wrong_type':
                                target['Тип'] = 'Булево' if target['Тип'] != 'Булево' else 'Строка'
                            elif kind == 'nullable':
                                target['Тип'] = (target['Тип'][:-1] if target['Тип'].endswith('?')
                                                 else target['Тип'] + '?')
                            else:
                                target['НезаполненноеЗначение'] = 'ЗапретитьВсегда'
                            self.assert_rejected(changed)

    def test_every_criterion_detects_isolated_mutation_in_sbsl(self):
        with TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / 'project'
            self.write_project(root)
            model = self.model(root)
            cases = []
            for original in self.assignment['checks']:
                check, changed = deepcopy(original), deepcopy(model)
                if check['type'] == 'module':
                    changed['modules'] = [m for m in changed['modules'] if not (
                        m['name'] == check['module'] and m['namespace'] == check['namespace'])]
                else:
                    element, target, members = selected(changed, check)
                    if check['properties']:
                        del target[next(iter(check['properties']))]
                    elif 'expectedType' in check:
                        target['Тип'] = 'Булево' if check['expectedType'] != 'Булево' else 'Строка'
                    elif check['type'] == 'element':
                        changed['elements'].remove(element)
                    else:
                        members.remove(target)
                cases.append((check, changed))
                # Nested numbering settings must be compared, not just present.
                for key, value in original['properties'].items():
                    if not isinstance(value, (dict, list)):
                        continue
                    for index, (path, expected) in enumerate(walk(value)):
                        if isinstance(expected, (dict, list)):
                            continue
                        nested_check, nested_model = deepcopy(original), deepcopy(model)
                        _, member, _ = selected(nested_model, original)
                        at(member[key], path[:-1])[path[-1]] = wrong(expected)
                        nested_check['id'] += f'__nested_{index}'
                        cases.append((nested_check, nested_model))
            self.assertEqual(self.mutation_count, len(cases))
            for offset in range(0, len(cases), 48):
                checks, libraries = [], []
                for check, changed in cases[offset:offset + 48]:
                    selector = {'provider': 'Mutation', 'name': check['id'], 'version': '1'}
                    check['library'] = selector
                    checks.append(check)
                    libraries.append({**selector, 'model': changed})
                mutated = {'name': 'Мутации', 'sourceHash': 'fixture', 'elements': [],
                           'modules': [], 'diagnostics': [], 'libraries': libraries}
                model_path, assignment_path = work / 'model.json', work / 'assignment.json'
                model_path.write_text(json.dumps(mutated, ensure_ascii=False), encoding='utf-8')
                assignment_path.write_text(json.dumps({'checks': checks}, ensure_ascii=False), encoding='utf-8')
                result = execute_engine('test', model_path, assignment_path, work)
                self.assertEqual(len(checks), len(result['checks']))
                for check in result['checks']:
                    with self.subTest(criterion=check['id']):
                        self.assertEqual('FAIL', check['status'])
                self.assertEqual((0, len(checks), 0),
                                 (result['score'], result['maxScore'], result['unavailablePoints']))

    def test_full_runner_reports_failures_and_preserves_source(self):
        with TemporaryDirectory() as directory:
            root, output = Path(directory) / 'project', Path(directory) / 'reports'
            self.write_project(root)
            initial = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            args = ['--project', str(root), '--output', str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, self.full.main(args))
            self.assertEqual(initial, {p.relative_to(root): p.read_bytes()
                                       for p in root.rglob('*') if p.is_file()})
            result = json.loads((output / 'full-result.json').read_text(encoding='utf-8'))
            self.assertEqual('passed', result['status'])
            self.assertEqual((self.criterion_count, self.criterion_count, 0), tuple(result['structural'][k] for k in
                                                 ('score', 'maxScore', 'unavailablePoints')))
            self.assertTrue((output / 'structural/report.html').is_file())
            self.assertIn(self.report_marker, (output / 'report.html').read_text(encoding='utf-8'))
            path = root / 'Продажи/Заказ.yaml'
            obj = json.loads(path.read_text(encoding='utf-8'))
            obj['ТабличныеЧасти'][0]['Реквизиты'].append({'Имя': 'Лишний', 'Тип': 'Строка'})
            path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(1, self.full.main(args))
            result = json.loads((output / 'full-result.json').read_text(encoding='utf-8'))
            self.assertEqual('failed', result['status'])
            self.assertEqual(self.criterion_count, result['structural']['score'])
            self.assertIn('Лишний', json.dumps(result['strictChecks'], ensure_ascii=False))
            with redirect_stderr(io.StringIO()):
                self.assertEqual(2, self.full.main(['--project', str(root), '--output', str(root / 'reports')]))
            self.assertFalse((root / 'reports').exists())


if __name__ == '__main__':
    unittest.main()
