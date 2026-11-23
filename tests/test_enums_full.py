"""Verify lesson 05 values, defaults, references and strict conformity."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import test_documents_full as documents
from element_test.bridge import canonicalize_model_types
from element_test.model import analyze
from element_test.runtime import execute_engine

REPO = Path(__file__).resolve().parent.parent
ASSIGNMENT = REPO / 'assignments/enums-full'
SPEC = importlib.util.spec_from_file_location('enums_full', ASSIGNMENT / 'check_full.py')
FULL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FULL)
CHECKER = FULL.CHECKER


class EnumsFullTest(documents.DocumentsFullTest):
    assignment_dir = ASSIGNMENT
    full_checker = FULL
    criterion_count = 220
    report_title = 'Перечисления — полное соответствие'

    def test_uuid_and_declaration_order_are_ignored(self):
        changed = deepcopy(self.reference)
        changed['project']['Ид'] = 'new-project-id'
        for element in changed['elements'].values():
            element['Ид'] = 'new-object-id'
            for collection in ('Реквизиты', 'Элементы'):
                members = element.get(collection, [])
                members.reverse()
                for member in members:
                    member['Ид'] = 'new-member-id'
        checks = CHECKER.check_contract(self.reference, CHECKER.normalize(changed))
        self.assertTrue(all(check['status'] == 'PASS' for check in checks))

    def test_exact_enum_values_defaults_and_presentations(self):
        for name, element in self.reference['elements'].items():
            if element['ВидЭлемента'] != 'Перечисление':
                continue
            for operation in ('extra', 'missing', 'duplicate', 'extra_default'):
                with self.subTest(enum=name, operation=operation):
                    changed = deepcopy(self.reference)
                    members = changed['elements'][name]['Элементы']
                    if operation == 'extra':
                        members.append({'Имя': 'ЛишнееЗначение'})
                    elif operation == 'missing':
                        members.pop()
                    elif operation == 'duplicate':
                        members.append(deepcopy(members[0]))
                    else:
                        target = next(member for member in members if not member.get('ПоУмолчанию'))
                        target['ПоУмолчанию'] = True
                    self.assert_rejected(changed)
        changed = deepcopy(self.reference)
        # A stale string default from lesson 04 must not survive the migration.
        fields = changed['elements']['CRM::Сделка']['Реквизиты']
        next(field for field in fields if field['Имя'] == 'СтатусСделки')['ЗначениеПоУмолчанию'] = 'Новая'
        self.assert_rejected(changed)

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Requires Script executor')
    def test_enum_field_nullable_contract_and_substituted_enum(self):
        with TemporaryDirectory() as work:
            temporary = Path(work)
            root = temporary / 'project'
            self.write_project(root)
            model = analyze(root)
            canonicalize_model_types(model)
            enum_names = {element['Имя'] for element in self.reference['elements'].values()
                          if element['ВидЭлемента'] == 'Перечисление'}
            checks, libraries = [], []
            enum_fields = [check for check in self.assignment['checks'] if check['type'] == 'field'
                           and check.get('expectedType', '').rstrip('?') in enum_names]
            self.assertEqual(7, len(enum_fields))
            for original in enum_fields:
                expected = original['expectedType']
                for label, wrong_type in (
                    ('nullable', expected.rstrip('?') if expected.endswith('?') else expected + '?'),
                    ('other_enum', next(name for name in sorted(enum_names)
                                        if name != expected.rstrip('?')) + ('?' if expected.endswith('?') else '')),
                ):
                    changed, check = deepcopy(model), deepcopy(original)
                    element = next(item for item in changed['elements']
                                   if CHECKER.identity(item) == check['element'])
                    field = next(item for item in element['properties']['Реквизиты']
                                 if item['Имя'] == check['field'])
                    field['Тип'] = wrong_type
                    check['id'] += '__' + label
                    selector = {'provider': 'Mutation', 'name': check['id'], 'version': '1'}
                    check['library'] = selector
                    checks.append(check)
                    libraries.append({**selector, 'model': changed})
            mutated = {'name': 'Мутации типов', 'sourceHash': 'fixture', 'elements': [],
                       'modules': [], 'diagnostics': [], 'libraries': libraries}
            model_path, assignment_path = temporary / 'model.json', temporary / 'assignment.json'
            model_path.write_text(json.dumps(mutated, ensure_ascii=False), encoding='utf-8')
            assignment_path.write_text(json.dumps({'checks': checks}, ensure_ascii=False), encoding='utf-8')
            result = execute_engine('test', model_path, assignment_path, temporary)
            self.assertEqual(14, len(result['checks']))
            self.assertTrue(all(check['status'] == 'FAIL' for check in result['checks']))
            self.assertEqual((0, 14, 0),
                             (result['score'], result['maxScore'], result['unavailablePoints']))


if __name__ == '__main__':
    raise SystemExit(unittest.main())
