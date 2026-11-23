"""Regression and isolated mutations for the lesson 04 reference contract."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_catalogs_full as catalogs
from element_test.assignment import load_assignment
from element_test.bridge import canonicalize_model_types
from element_test.model import analyze
from element_test.runtime import execute_engine

REPO = Path(__file__).resolve().parent.parent
ASSIGNMENT = REPO / 'assignments/documents-full'
SPEC = importlib.util.spec_from_file_location('documents_full', ASSIGNMENT / 'check_full.py')
FULL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FULL)
CHECKER = FULL.CHECKER


def leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from leaves(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from leaves(item, path + (index,))
    else:
        yield path, value


def replace(value):
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    return value + '_ошибка'


class DocumentsFullTest(catalogs.CatalogsFullTest):
    assignment_dir = ASSIGNMENT
    full_checker = FULL
    criterion_count = 176
    report_title = 'Документы — полное соответствие'

    # Reuse the contract's strict-comparison tests, not lesson 03 expectations.
    def setUp(self):
        self.reference = json.loads((self.assignment_dir / 'reference.json').read_text(encoding='utf-8'))
        self.assignment = load_assignment(self.assignment_dir)

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Requires Script executor')
    def test_each_structural_criterion_detects_its_own_mutation(self):
        with TemporaryDirectory() as work:
            temporary = Path(work)
            root = temporary / 'project'
            self.write_project(root)
            model = analyze(root)
            canonicalize_model_types(model)
            self.assertEqual(self.criterion_count, len(self.assignment['checks']))
            cases = []

            def selected(changed, check):
                name = check.get('element', check.get('name'))
                element = next(item for item in changed['elements'] if CHECKER.identity(item) == name)
                if check['type'] == 'field':
                    return next(item for item in element['properties']['Реквизиты']
                                if item['Имя'] == check['field'])
                if check['type'] == 'enumValue':
                    return next(item for item in element['properties']['Элементы']
                                if item['Имя'] == check['name'])
                return element['properties']

            for original in self.assignment['checks']:
                check, changed = deepcopy(original), deepcopy(model)
                if check['type'] == 'module':
                    changed['modules'] = [item for item in changed['modules']
                                          if not (item['name'] == check['module'] and
                                                  item['namespace'] == check['namespace'])]
                elif check['properties']:
                    del selected(changed, check)[next(iter(check['properties']))]
                elif check['type'] == 'element':
                    changed['elements'] = [item for item in changed['elements']
                                           if CHECKER.identity(item) != check['name']]
                elif 'expectedType' in check:
                    selected(changed, check)['Тип'] = 'Булево' if check['expectedType'] != 'Булево' else 'Строка'
                elif check['type'] == 'enumValue':
                    member = selected(changed, check)
                    element = next(item for item in changed['elements']
                                   if CHECKER.identity(item) == check['element'])
                    element['properties']['Элементы'].remove(member)
                else:
                    field = selected(changed, check)
                    element = next(item for item in changed['elements']
                                   if CHECKER.identity(item) == check['element'])
                    element['properties']['Реквизиты'].remove(field)
                cases.append((check, changed))
                # Prove nested numbering/interface/import settings are compared,
                # including every separator, length, prefix and boolean flag.
                for key, value in original['properties'].items():
                    if not isinstance(value, (dict, list)):
                        continue
                    for index, (path, expected) in enumerate(leaves(value)):
                        check, changed = deepcopy(original), deepcopy(model)
                        target = selected(changed, check)[key]
                        for part in path[:-1]:
                            target = target[part]
                        target[path[-1]] = replace(expected)
                        check['id'] += f'__nested_{index}'
                        cases.append((check, changed))

            self.assertGreater(len(cases), self.criterion_count)
            for offset in range(0, len(cases), 48):
                chunk = cases[offset:offset + 48]
                mutations = {'name': 'Мутации', 'sourceHash': 'fixture', 'elements': [],
                             'modules': [], 'diagnostics': [], 'libraries': []}
                checks = []
                for check, changed in chunk:
                    selector = {'provider': 'Mutation', 'name': check['id'], 'version': '1'}
                    check['library'] = selector
                    mutations['libraries'].append({**selector, 'model': changed})
                    checks.append(check)
                model_path, assignment_path = temporary / 'model.json', temporary / 'assignment.json'
                model_path.write_text(json.dumps(mutations, ensure_ascii=False), encoding='utf-8')
                assignment_path.write_text(json.dumps({'checks': checks}, ensure_ascii=False), encoding='utf-8')
                result = execute_engine('test', model_path, assignment_path, temporary)
                self.assertEqual(len(checks), len(result['checks']))
                for check in result['checks']:
                    with self.subTest(criterion=check['id']):
                        self.assertEqual('FAIL', check['status'])
                self.assertEqual((0, len(checks), 0),
                                 (result['score'], result['maxScore'], result['unavailablePoints']))

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Requires Script executor')
    def test_full_runner_status_reports_and_read_only_input(self):
        with TemporaryDirectory() as work:
            root, output = Path(work) / 'project', Path(work) / 'output'
            self.write_project(root)
            initial = {path.relative_to(root): path.read_bytes()
                       for path in root.rglob('*') if path.is_file()}
            args = ['--project', str(root), '--output', str(output)]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, self.full_checker.main(args))
            self.assertEqual(initial, {path.relative_to(root): path.read_bytes()
                                      for path in root.rglob('*') if path.is_file()})
            self.assertIn(f'<h1>{self.report_title}</h1>',
                          (output / 'report.html').read_text(encoding='utf-8'))
            self.assertTrue((output / 'structural/report.html').is_file())
            path = root / 'Продажи/Заказ.yaml'
            properties = json.loads(path.read_text(encoding='utf-8'))
            properties['Реквизиты'].append({'Имя': 'Лишний', 'Тип': 'Строка'})
            path.write_text(json.dumps(properties, ensure_ascii=False), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(1, self.full_checker.main(args))
            result = json.loads((output / 'full-result.json').read_text(encoding='utf-8'))
            self.assertEqual('failed', result['status'])
            self.assertEqual((self.criterion_count, self.criterion_count),
                             (result['structural']['score'], result['structural']['maxScore']))
            elements = next(check for check in result['strictChecks'] if check['id'] == 'elements')
            self.assertEqual('FAIL', elements['status'])
            self.assertIn('Лишний', json.dumps(elements, ensure_ascii=False))
            with redirect_stderr(io.StringIO()):
                self.assertEqual(2, self.full_checker.main(
                    ['--project', str(root), '--output', str(root / 'reports')]))
            self.assertFalse((root / 'reports').exists())


if __name__ == '__main__':
    raise SystemExit(unittest.main())
