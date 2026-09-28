"""Fixed subset boundaries, independent SQL evidence, rollback and SBSL grading."""
import copy
import json
import os
import re
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from element_test.assignment import load_assignment
from element_test.bridge import report, write_json
from element_test.integration import docker, executor_command, run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import REPO, execute_engine
from element_test.shipment_storage import QUERY, adapt_query, prepare
from element_test.yaml_io import InputError

ARCHIVE = REPO / 'Dvizhok.xdump'


class StorageContractTest(unittest.TestCase):
    def test_only_original_query_and_no_mock_rows(self):
        self.assertIn('ТестОстатки.СоздатьЗапрос', adapt_query('метод Test()\n знч X = Запрос{' + QUERY + '}\n;'))
        for query in (QUERY.replace('КоличествоОстаток', 'КоличествоОборот'),
                      QUERY.replace('В (%{', '= (%{'), 'ВЫБРАТЬ 1', QUERY + ' И РегистрТовары.Склад = %{"x"}'):
            with self.subTest(query=query), self.assertRaises(InputError):
                adapt_query('метод Test()\n знч X = Запрос{' + query + '}\n;')

    def test_unsupported_schema_and_typed_inputs(self):
        with open_project(ARCHIVE) as root, tempfile.TemporaryDirectory() as tmp:
            model = analyze(root)
            check = load_assignment(REPO / 'assignments/poc-shipment-storage')['checks'][0]
            with self.assertRaises(InputError):
                prepare(root, model, {**check, 'mocks': {}}, Path(tmp))
            for path, value in [('steps', [{'action': 'unknown'}]), ('stock', [{'quantity': 1}])]:
                with self.assertRaises(InputError):
                    prepare(root, model, {**check, path: value}, Path(tmp))
            bad = copy.deepcopy(check)
            bad['steps'][0]['document']['Ссылка'] = {'type': 'ПоступлениеТоваров.Ссылка', 'value': {'Идентификатор': 'wrong'}}
            with self.assertRaises(InputError):
                prepare(root, model, bad, Path(tmp))
            altered = copy.deepcopy(model)
            register = next(e for e in altered['elements'] if e['name'] == 'РегистрТовары')
            register['properties']['Ресурсы'].append({'Имя': 'Extra', 'Тип': 'Число'})
            with self.assertRaises(InputError):
                prepare(root, altered, check, Path(tmp))

    def test_containment_and_no_secret_in_generated_source(self):
        with open_project(ARCHIVE) as root, tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            check = load_assignment(REPO / 'assignments/poc-shipment-storage')['checks'][0]
            prepare(root, analyze(root), check, directory)
            generated = '\n'.join(p.read_text() for p in directory.glob('*.sbsl'))
            self.assertNotIn('mocks', generated)
            self.assertNotIn('ELEMENT_CALL', generated)
            self.assertNotIn('jdbc:', generated)
            self.assertIn('ROLLBACK', generated)
            command = executor_command({'image': 'fixture'}, Path('/runtime-fixture'), directory, 'internal-run', 'executor', '9.0')
            for arg in ('--read-only', '--cap-drop', '--user', '--pids-limit', '--memory'):
                self.assertIn(arg, command)
            self.assertNotIn('--privileged', command)
            self.assertFalse(any('docker.sock' in a for a in command))


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS') == '1', 'Storage Docker integration is opt-in')
class StorageIntegrationTest(unittest.TestCase):
    def test_sql_storage_rollback_cleanup_and_sbsl_grading(self):
        before = ARCHIVE.read_bytes()
        assignment = load_assignment(REPO / 'assignments/poc-shipment-storage')
        password = secrets.token_hex(24) + "'&?${probe}"
        with open_project(ARCHIVE) as root, tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': password}):
            directory, model = Path(tmp), analyze(root)
            def run(check, **kw):
                credentials_verified = []
                def inspect_command(*args, **kwargs):
                    bootstrap = kwargs.get('input', '')
                    if 'CREATE ROLE smoke LOGIN PASSWORD' in bootstrap:
                        restricted = re.search("CREATE ROLE smoke LOGIN PASSWORD '([0-9a-f]+)'", bootstrap)[1]
                        self.assertNotEqual(restricted, password)
                        self.assertIn('NOSUPERUSER NOCREATEDB NOCREATEROLE', bootstrap)
                    if args[0] == 'create' and any('target=/trusted' in arg for arg in args):
                        mounted = next(arg for arg in args if 'target=/trusted' in arg)
                        path = Path(mounted.split('source=', 1)[1].split(',target=', 1)[0])
                        contents = (path / 'connection.json').read_text()
                        self.assertNotIn(password, contents)
                        self.assertNotIn(quote(password, safe=''), contents)
                        self.assertIn('user=smoke', contents)
                        self.assertFalse(any('POSTGRES_PASSWORD' in arg for arg in args))
                        self.assertFalse(any('CREATE ROLE' in file.read_text() for file in path.glob('*.sbsl')))
                        credentials_verified.append(True)
                    return docker(*args, **kwargs)
                with patch('element_test.integration.docker', side_effect=inspect_command):
                    execution = run_integration(check, model, directory, enabled=True, root=root, **kw)
                if execution['status'] in {'EXECUTED', 'ERROR'} and 'integration' in execution:
                    self.assertEqual(credentials_verified, [True])
                if 'integration' in execution:
                    self.assertTrue(execution['integration']['cleanup'], execution)
                    run_id = execution['integration']['runId']
                    self.assertFalse(docker('ps', '-a', '--filter', 'name=element-integration-' + run_id, '--format', '{{.Names}}').strip())
                    self.assertFalse(docker('network', 'ls', '--filter', 'name=element-integration-' + run_id, '--format', '{{.Name}}').strip())
                self.assertNotIn(password, json.dumps(execution))
                self.assertNotIn('jdbc:', json.dumps(execution))
                return execution
            for check in assignment['checks']:
                check['execution'] = run(check)
                self.assertEqual(check['execution']['status'], 'EXECUTED', check['execution'])
            wrong = copy.deepcopy(assignment['checks'][0])
            wrong['id'] = 'wrong_quantity'
            wrong['expected']['database']['lines'][0]['quantity'] = 1.251
            error = copy.deepcopy(assignment['checks'][0])
            error['id'] = 'failure_after_movements'
            error['execution'] = run(error, inject_failure=True)
            self.assertEqual(error['execution']['status'], 'ERROR', error['execution'])
            evidence = error['execution']['storageEvidence']
            self.assertEqual(evidence['documents'], [])
            self.assertEqual(evidence['lines'], [])
            self.assertTrue(all(m['kind'] == 'ПоступлениеТоваров' for m in evidence['movements']))
            def continuation(identifier):
                check = copy.deepcopy(assignment['checks'][0])
                check['id'] = identifier
                check['execution'] = run(check)
                self.assertEqual(check['execution']['status'], 'EXECUTED', check['execution'])
                return check
            continued = continuation('continued_after_failure')
            unsupported = copy.deepcopy(assignment['checks'][0])
            unsupported['id'] = 'unsupported_action'
            unsupported['steps'][0]['action'] = 'delete-document'
            unsupported['execution'] = run(unsupported)
            self.assertEqual(unsupported['execution']['status'], 'UNSUPPORTED')
            after_unsupported = continuation('continued_after_unsupported')
            ambiguous = copy.deepcopy(assignment['checks'][0])
            ambiguous['id'] = 'unsupported_multiple_warehouses'
            ambiguous['stock'].append({**ambiguous['stock'][0], 'warehouse': 'another'})
            ambiguous['execution'] = run(ambiguous)
            self.assertEqual(ambiguous['execution']['status'], 'UNSUPPORTED', ambiguous['execution'])
            self.assertEqual(ambiguous['execution']['storageEvidence']['documents'], [])
            after_ambiguous = continuation('continued_after_ambiguous')
            model_path, assignment_path = directory / 'model.json', directory / 'assignment.json'
            write_json(model_path, model)
            write_json(assignment_path, assignment)
            result = execute_engine('test', model_path, assignment_path, directory)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS'] * len(assignment['checks']), result)
            output = REPO / 'result/poc-shipment-storage'
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / 'result.json', result)
            (output / 'report.html').write_text(report(result))
            negative = {**assignment, 'checks': [wrong, error, continued, unsupported, after_unsupported, ambiguous, after_ambiguous]}
            write_json(assignment_path, negative)
            result = execute_engine('test', model_path, assignment_path, directory)
            self.assertEqual([c['status'] for c in result['checks']], ['FAIL', 'ERROR', 'PASS', 'UNSUPPORTED', 'PASS', 'UNSUPPORTED', 'PASS'], result)
            write_json(output / 'negative.json', result)
            (output / 'negative.html').write_text(report(result))
            self.assertEqual(list(directory.glob('integration-*')), [])
        self.assertEqual(ARCHIVE.read_bytes(), before)
