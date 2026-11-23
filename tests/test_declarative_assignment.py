"""Portable criteria travel through the same entry point as legacy checks."""
from copy import deepcopy
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

import yaml

from element_check_service.config import Config
from element_check_service.inputs import prepare_criteria
from element_test.assignment import load_assignment
from element_test.bridge import canonicalize_model_types, evaluate_assertions, prepare_declarative, run_test
from element_test.model import analyze
from element_test.profiles import implementation
from element_test.yaml_io import InputError

from tests.test_programming_practice import REFERENCE, rename_reference


class DeclarativeAssignmentTests(unittest.TestCase):
    def test_changing_yaml_changes_grade_without_changing_project_or_engine(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'criteria.yaml'
            clause = {'query': {'from': 'element', 'owner': 'Продажи::Заказ'},
                      'some': [{'path': ['СозданиеНаОсновании'], 'contains': 'Сделка.Ссылка'}]}
            check = {'id': 'declared-base', 'type': 'assertion', 'points': 2,
                     'assertion': {'all': [clause]}}
            assignment = {'name': 'Any assignment', 'checks': [check]}
            for index, (wanted, grade) in enumerate([('Сделка.Ссылка', 'passed'), ('Другая.Ссылка', 'failed')]):
                clause['some'][0]['contains'] = wanted
                source.write_text(yaml.safe_dump(assignment, allow_unicode=True))
                extracted = root / f'criteria-{index}'
                prepare_criteria(source, extracted, Config(data=root))
                _, actual = run_test(REFERENCE, extracted, root / f'out-{index}', render_html=False)
                self.assertEqual(grade, actual['status'])
                self.assertEqual(2 if grade == 'passed' else 0, actual['score'])

    def test_trace_observes_execution_and_propagates_unavailable_result(self):
        query = {'from': 'execution', 'criterion': 'runtime', 'steps': [{'path': ['trace']}, {'items': True}]}
        with TemporaryDirectory() as directory:
            work = Path(directory)
            model = analyze(REFERENCE)
            assertion = {'id': 'trace', 'type': 'assertion',
                         'assertion': {'all': [{'query': query, 'some': [{'path': ['event'], 'eq': 'enter'}]}]}}
            for evidence, expected in [
                ({'status': 'EXECUTED', 'trace': [{'event': 'enter'}]}, {'status': 'EXECUTED', 'actual': True}),
                ({'status': 'EXECUTED', 'trace': []}, {'status': 'EXECUTED', 'actual': False}),
                ({'status': 'UNSUPPORTED', 'reasonCode': 'unsupported_contract'},
                 {'status': 'UNSUPPORTED', 'reasonCode': 'unsupported_contract'}),
            ]:
                checks = [{'id': 'runtime', 'type': 'runtime', 'execution': evidence}, deepcopy(assertion)]
                _, _, queries = prepare_declarative(REFERENCE, model, work, {'checks': checks}, [])
                evaluate_assertions(checks, queries)
                self.assertEqual({**expected, 'observationKind': 'trace'}, checks[-1]['execution'])

    def test_rejects_untrusted_execution_and_invalid_query(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'criteria.yaml'
            for clause, extra in [
                ({'query': {'from': 'project'}, 'count': 1}, {'execution': {'status': 'EXECUTED', 'actual': True}}),
                ({'query': {'from': 'execution', 'criterion': 'unknown'}, 'count': 1}, {}),
                ({'query': {'from': 'file', 'path': '/etc/passwd'}, 'count': 1}, {}),
                ({'query': {'from': 'project', 'steps': [{'eval': '1'}]}, 'count': 1}, {}),
            ]:
                with self.subTest(clause=clause):
                    path.write_text(yaml.safe_dump({'checks': [{'id': 'bad', 'type': 'assertion',
                                                                'assertion': {'all': [clause]}, **extra}]}))
                    with self.assertRaises(InputError):
                        load_assignment(path)

    def test_fixed_method_allows_optional_final_newline(self):
        from export_criteria import criteria

        criterion_id = 'L.source.Товары::Отгрузка.Объект.ПриСозданииНаОсновании'
        check = next(c for c in criteria()['checks'] if c['id'] == criterion_id)
        with TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / 'source'
            shutil.copytree(REFERENCE, source)
            module = source / 'Товары/Отгрузка.Объект.xbsl'
            original = module.read_text().rstrip()
            for body, expected in [
                (original, True),
                (original + '\n', True),
                (original + '\r\n\r\n', True),
                (original + ' этот', False),
                (original + ' НедопустимыйТокен', False),
                (original.replace('ЗаказКлиента.Товары, этот', 'этот, ЗаказКлиента.Товары'), False),
            ]:
                with self.subTest(suffix=body[-45:]):
                    module.write_text(body)
                    _, _, queries = prepare_declarative(source, analyze(source), work, {'checks': [check]}, [])
                    self.assertEqual(expected, queries.assertion(check['assertion']['all']))

    def test_export_matches_legacy_structural_results_and_renamed_targets(self):
        from export_criteria import criteria

        legacy = implementation()
        with TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / 'source'
            shutil.copytree(REFERENCE, source)
            rename_reference(source)
            model = analyze(source)
            canonicalize_model_types(model)
            config = criteria(optional=True)
            _, _, queries = prepare_declarative(source, model, work, config, [])
            aliases = legacy.resolve_aliases(model, {})
            self.assertEqual(aliases, queries.aliases)
            expected = [item['status'] == 'PASS' for item in legacy.structural(source, model, aliases, optional=True)]
            structural = [c for c in config['checks'] if c['group'] == 'structural']
            self.assertEqual(181, len(structural))
            self.assertEqual(expected, [queries.assertion(c['assertion']['all']) for c in structural])
            self.assertTrue(all(expected))
            self.assertEqual(326, len(config['checks']))
            runtime = {c['id']: c for c in config['checks'] if 'target' in c}
            for original in legacy.cases(True):
                self.assertEqual(legacy.adapt(original, aliases)['target'], runtime[original['id']]['target'])
