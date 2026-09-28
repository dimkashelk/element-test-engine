"""Preflight tests are offline; real SQL smoke requires explicit opt-in."""
import copy
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from element_test.assignment import load_assignment
from element_test.bridge import report, write_json
from element_test.integration import BackendUnavailable, docker, preflight, run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import REPO, execute_engine, run_pure


def contract():
    return {'id': 'sql', 'type': 'runtime', 'integration': {'backend': 'postgres', 'operation': 'sql-smoke'}}


class PreflightTest(unittest.TestCase):
    def test_explicit_opt_in_and_no_fallback_to_mocks(self):
        check = {**contract(), 'mocks': {'queries': []}}
        with patch('element_test.integration.docker') as command:
            result = run_integration(check, {}, Path('/tmp'))
            self.assertEqual(result['status'], 'UNSUPPORTED')
            self.assertIn('--integration', result['message'])
            self.assertEqual(run_pure(None, {}, check, None)['status'], 'UNSUPPORTED')
            command.assert_not_called()

    def test_unsupported_operations_and_student_targets(self):
        with patch('element_test.integration.docker') as command:
            checks = [None, {}, {'backend': 'element', 'operation': 'sql-smoke'},
                      {'backend': 'postgres', 'operation': 'xbql'},
                      {'backend': 'postgres', 'operation': 'write-document'},
                      {**contract()['integration'], 'url': 'forbidden'}]
            for value in checks:
                with self.subTest(value=value):
                    result = run_integration({**contract(), 'integration': value}, {}, Path('/tmp'), enabled=True)
                    self.assertEqual(result['status'], 'UNSUPPORTED')
            for key in ('target', 'context', 'args', 'mocks', 'runtimeDateTime'):
                self.assertEqual(run_integration({**contract(), key: {}}, {}, Path('/tmp'), enabled=True)['status'], 'UNSUPPORTED')
            command.assert_not_called()

    def test_config_driver_secret_and_backend_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, runtimes = root / 'integration.json', root / 'runtime.json'
            config.write_text((REPO / 'config/integration.json').read_text())
            runtime_data = {'9.0': {'directory': str(root), 'image': 'test-runtime'}}
            runtimes.write_text(json.dumps(runtime_data))
            lib = root / 'lib'
            lib.mkdir()
            env = {'ELEMENT_TEST_INTEGRATION_CONFIG': str(config), 'ELEMENT_TEST_RUNTIMES': str(runtimes),
                   'ELEMENT_SCRIPT_HOME': str(root), 'ELEMENT_TEST_INTEGRATION_PASSWORD': 'test-secret'}
            with patch.dict(os.environ, env), patch('element_test.integration.docker') as command, patch('element_test.integration.shutil.which', return_value='/docker'):
                model = {'compatibilityVersion': '9.0'}
                self.assertEqual(run_integration(contract(), model, root, enabled=True)['status'], 'UNSUPPORTED')
                command.assert_not_called()
                with zipfile.ZipFile(lib / 'postgresql-test.jar', 'w') as driver:
                    driver.writestr('org/postgresql/Driver.class', b'fixture')
                (lib / 'com.e1c.g5rt.appliedobjects.sql.xbsl.runtime-test.jar').touch()
                self.assertEqual(preflight(contract(), model, enabled=True)[2], root.resolve())
                command.side_effect = BackendUnavailable('Docker недоступен')
                self.assertEqual(run_integration(contract(), model, root, enabled=True)['status'], 'UNSUPPORTED')
                command.side_effect = None
                with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': ''}):
                    result = run_integration(contract(), model, root, enabled=True)
                    self.assertEqual(result['status'], 'UNSUPPORTED')
                for contents in ('[]', '{', '{"backend":"postgres","image":"postgres:17","url":"secret-url"}'):
                    config.write_text(contents)
                    result = run_integration(contract(), model, root, enabled=True)
                    self.assertEqual(result['status'], 'UNSUPPORTED')
                    self.assertNotIn('secret-url', str(result))

    def test_docker_diagnostics_never_expose_secrets(self):
        import subprocess
        with patch('element_test.integration.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'password=private-secret')):
            with self.assertRaises(BackendUnavailable) as error:
                docker('create', '--env', 'POSTGRES_PASSWORD')
            self.assertNotIn('private-secret', str(error.exception))

    def test_partial_provision_is_cleaned_without_mocking_a_smoke_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            present = set()
            def command(*args, **kwargs):
                if args[:2] == ('network', 'create'):
                    present.add(args[-1])
                elif args[0] == 'create':
                    present.add(args[args.index('--name') + 1])
                elif args[0] == 'start':
                    raise BackendUnavailable('PostgreSQL startup unavailable')
                elif args[0] == 'ps' or args[:2] == ('network', 'ls'):
                    name = args[-1].removeprefix('name=')
                    return name if name in present else ''
                elif args[0] == 'rm' or args[:2] == ('network', 'rm'):
                    present.discard(args[-1])
                return ''
            with patch('element_test.integration.preflight', return_value=({'image': 'fixture'}, {}, root, 'secret')), patch('element_test.integration.docker', side_effect=command):
                result = run_integration(contract(), {}, root, enabled=True)
            self.assertEqual(result['status'], 'UNSUPPORTED')
            self.assertTrue(result['integration']['cleanup'])
            self.assertEqual(present, set())
            self.assertEqual(list(root.iterdir()), [])

    def test_cleanup_failure_is_error_and_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch('element_test.integration.preflight', return_value=({'image': 'fixture'}, {}, root, 'secret')), patch('element_test.integration.docker', side_effect=BackendUnavailable('unavailable')):
                result = run_integration(contract(), {}, root, enabled=True)
            self.assertEqual(result['status'], 'ERROR')
            self.assertFalse(result['integration']['cleanup'])
            self.assertNotIn('actual', result)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS') == '1', 'Real SQL integration is opt-in')
class SqlSmokeTest(unittest.TestCase):
    def test_real_sql_isolation_failure_cleanup_and_sbsl_grading(self):
        archive = REPO / 'Dvizhok.xdump'
        before = archive.read_bytes()
        positive = load_assignment(REPO / 'assignments/poc-integration-environment')
        assignment = copy.deepcopy(positive)
        wrong = copy.deepcopy(positive['checks'][0])
        wrong['id'] = 'wrong_fraction'
        wrong['expected']['after'][0]['quantity'] = 3.376
        error = copy.deepcopy(positive['checks'][0])
        error['id'] = 'error_after_writes'
        unsupported = copy.deepcopy(positive['checks'][0])
        unsupported['id'] = 'unsupported_xbql'
        unsupported['integration']['operation'] = 'xbql'
        unavailable = copy.deepcopy(positive['checks'][0])
        unavailable['id'] = 'backend_unavailable'
        checks = [*assignment['checks'], wrong]
        for item in (error, unsupported, unavailable):
            continued = copy.deepcopy(positive['checks'][0])
            continued['id'] = 'after_' + item['id']
            checks.extend([item, continued])
        assignment['checks'] = checks
        # Deliberately exercise URI encoding and SQL password escaping, with no logging.
        password = secrets.token_hex(24) + "'&?${probe}"
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root, patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': password}):
            temp, model = Path(directory), analyze(root)
            ids = []
            for check in checks:
                if check['id'] == 'backend_unavailable':
                    with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_CONFIG': str(temp / 'missing.json')}):
                        execution = run_integration(check, model, temp, enabled=True)
                else:
                    execution = run_integration(check, model, temp, enabled=True,
                                                inject_failure=check['id'] == 'error_after_writes')
                check['execution'] = execution
                expected_status = {'error_after_writes': 'ERROR', 'unsupported_xbql': 'UNSUPPORTED',
                                   'backend_unavailable': 'UNSUPPORTED'}.get(check['id'], 'EXECUTED')
                self.assertEqual(execution['status'], expected_status, execution)
                if 'integration' in execution:
                    self.assertTrue(execution['integration']['cleanup'], execution)
                    run_id = execution['integration']['runId']
                    ids.append(run_id)
                    # Independent Docker reads verify destruction after success AND error.
                    self.assertEqual(docker('ps', '-a', '--filter', 'name=element-integration-' + run_id, '--format', '{{.Names}}').strip(), '')
                    self.assertEqual(docker('network', 'ls', '--filter', 'name=element-integration-' + run_id, '--format', '{{.Name}}').strip(), '')
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(list(temp.glob('integration-*')), [])
            m, a = temp / 'model.json', temp / 'assignment.json'
            write_json(m, model)
            output = REPO / 'result/poc-integration-environment'
            output.mkdir(parents=True, exist_ok=True)
            write_json(a, {**positive, 'checks': checks[:2]})
            result = execute_engine('test', m, a, temp)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS', 'PASS'])
            write_json(output / 'result.json', result)
            (output / 'report.html').write_text(report(result))
            write_json(a, assignment)
            negative = execute_engine('test', m, a, temp)
            self.assertEqual([c['status'] for c in negative['checks']],
                             ['PASS', 'PASS', 'FAIL', 'ERROR', 'PASS', 'UNSUPPORTED', 'PASS', 'UNSUPPORTED', 'PASS'])
            self.assertEqual((negative['score'], negative['maxScore'], negative['unavailablePoints']), (5, 6, 3))
            write_json(output / 'negative.json', negative)
            (output / 'negative.html').write_text(report(negative))
            for data in (result, negative):
                self.assertNotIn(password, json.dumps(data))
                self.assertNotIn('jdbc:', json.dumps(data))
        self.assertEqual(archive.read_bytes(), before)
