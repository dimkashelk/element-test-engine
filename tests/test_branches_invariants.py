"""Stage 52 mechanism acceptance, independent of final-dump business scenarios."""
import copy
from importlib.util import find_spec
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.branches import summarize, inventory
from element_test.execution_plan import plan_execution
from element_test.generative import accepts, inputs, planning_check, shrink_candidates, options_schema
from element_test.model import analyze
from element_test.runtime import REPO, decode_output, execute_engine, prepare_script, run_pure
from element_test.yaml_io import InvalidTestError


SOURCE = '''метод Run(X: Число): Число
    пер Total = 0
    если X > 0 и Tick(X) > 0
        Total = 1
    иначе если X == 0
        Total = 2
    иначе
        Total = 3
    ;
    для I = 1 по X
        если I == 2
            продолжить
        ;
        Total += I
        если I == 3
            прервать
        ;
    ;
    пер N = X
    пока N > 0
        N -= 1
    ;
    выбор X
    когда 0
        Total += 10
    когда 1, 2
        Total += 20
    иначе
        Total += 30
    ;
    возврат X > 0 ? Total : -Total
;
метод Tick(X: Число): Число
    Консоль.Записать("ELEMENT_CALL " + СериализацияJson.ЗаписатьОбъект({"method": "Tick", "args": {"X": X}}))
    возврат X
;
метод Identity(X: Число): Число
    если X > 4
        возврат X + 1
    ;
    возврат X
;
метод Scale(X: Число): Число
    возврат X * 2
;
'''


def fixture(root, namespace='Core'):
    root.mkdir()
    (root/'Проект.yaml').write_text('Имя: Mechanism52\nПоставщик: Tests\nРежимСовместимости: 9.0\n')
    (root/namespace).mkdir()
    (root/namespace/'Main.xbsl').write_text(SOURCE)
    return analyze(root)


def config(method='Identity'):
    return {'target': {'module': 'Main', 'namespace': 'Core', 'method': method}, 'timeout': '15s',
            'generative': {'schemaVersion': 1, 'seed': 73, 'cases': 3,
                'domains': [{'type': 'Число', 'min': 0, 'max': 8}],
                'relation': {'op': 'equal', 'args': [{'result': True}, {'arg': 0}]}, 'maxShrinks': 12}}


class BranchFixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base/'source'; self.model = fixture(self.root)

    def trusted(self, check, folder='generated'):
        output = self.base/folder; output.mkdir()
        script = prepare_script(self.root, self.model, check, output)
        result = subprocess.run([str(REPO/'bin/script-runtime'), '-c', '9.0', str(script)],
                                cwd=output, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return decode_output(result.stdout), json.loads((output/'execution-plan.json').read_text())


class BranchPlanTest(BranchFixture):
    @unittest.skipUnless(find_spec('jsonschema'), 'Optional complete JSON Schema validator')
    def test_schema_shape(self):
        import jsonschema
        schema = options_schema(); jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(config()['generative'], schema)
        self.assertEqual(schema, json.loads((REPO/'docs/generative-options-v1.schema.json').read_text()))

    def test_inventory_rejects_stale_source_evidence(self):
        a = inventory(self.root, self.model)
        self.assertEqual(a['coveredOutcomes'], 0)
        coverage = copy.deepcopy(a); coverage['records'][0]['counts'] = {'true': 2}
        b = inventory(self.root, self.model, [{'branchCoverage': coverage}])
        self.assertEqual(b['coveredOutcomes'], 1)
        path = self.root/'Core/Main.xbsl'; path.write_text('// changed\n'+path.read_text())
        changed = inventory(self.root, analyze(self.root), [{'branchCoverage': coverage}])
        self.assertEqual(changed['coveredOutcomes'], 0)
        self.assertEqual(changed['rejectedEvidenceRecords'], len(coverage['records']))

    def test_source_identity_is_stable_and_sensitive_to_file_namespace_and_position(self):
        c = {'target': config()['target'], 'args': [2], 'branches': True}
        a = plan_execution(self.root, self.model, c); b = plan_execution(self.root, self.model, c)
        self.assertEqual(a.branches, b.branches)
        for record in a.branches:
            text = (self.root/record['sourceFile']).read_text()
            self.assertTrue(text[slice(*record['range'])])
            self.assertEqual(record['line'], text.count('\n', 0, record['range'][0])+1)
        path = self.root/'Core/Main.xbsl'; path.write_text('// offset\n' + path.read_text())
        changed = plan_execution(self.root, analyze(self.root), c)
        self.assertTrue({r['id'] for r in a.branches}.isdisjoint(r['id'] for r in changed.branches))
        for bad in (None, 1, {}, 'true'):
            with self.subTest(bad=bad), self.assertRaises(InvalidTestError):
                plan_execution(self.root, self.model, {**c, 'branches': bad})

    def test_domains_seed_boundaries_constraints_and_shrinking(self):
        c = config()['generative']; c['cases'] = 30
        a = inputs(c); self.assertEqual(a, inputs(copy.deepcopy(c)))
        self.assertEqual(a[:3], [[0], [8], [0]])
        self.assertEqual(a[:10], [[0], [8], [0], [0], [7], [8], [7], [1], [3], [0]])
        c['seed'] += 1; self.assertNotEqual(a, inputs(c))
        domains = [{'type': 'Булево'}, {'type': 'Число?', 'min': -4, 'max': 7},
            {'type': 'Строка', 'alphabet': 'ab', 'minLength': 1, 'maxLength': 5},
            {'type': 'Массив<Число>', 'minLength': 1, 'maxLength': 4, 'items': {'type': 'Число', 'min': -2, 'max': 2}}]
        c['domains'] = domains
        for values in inputs(c):
            self.assertTrue(all(accepts(d, v) for d, v in zip(domains, values)))
        candidates = list(shrink_candidates(c, [True, 7, 'bbaba', [2, 1, -1]]))
        self.assertTrue(candidates)
        for values in candidates:
            self.assertTrue(all(accepts(d, v) for d, v in zip(domains, values)))

    def test_invalid_contracts_and_independent_plan(self):
        for key, value in [('seed', True), ('cases', 0), ('cases', 65), ('maxShrinks', -1),
                           ('relation', {'op': 'exec', 'args': []}), ('relation', {'pairedResult': True}),
                           ('domains', [{'type': 'Число', 'min': 7, 'max': 2}])]:
            c = config(); c['generative'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InvalidTestError): planning_check(c)
        c = config(); c['generative']['relation'] = {'literal': 'SECRET_ORACLE'}
        p = plan_execution(self.root, self.model, c)
        self.assertNotIn('SECRET_ORACLE', p.to_json())
        for key in ('expected', 'args', 'sequence'):
            with self.subTest(key=key), self.assertRaises(InvalidTestError): planning_check({**config(), key: []})
        with patch('element_test.runtime.shutil.which', return_value=None):
            unavailable = run_pure(self.root, self.model, config(), self.base)
        self.assertEqual(unavailable['status'], 'UNSUPPORTED')
        self.assertEqual(unavailable['reasonCode'], 'backend_unavailable')
        self.assertEqual(unavailable['generative']['executedCases'], 0)
        self.assertEqual(unavailable['branchCoverage']['coveredOutcomes'], 0)

    def test_no_method_trace_claims_branch_outcomes(self):
        p = plan_execution(self.root, self.model, {'target': config()['target'], 'args': [0], 'branches': True})
        result = summarize(p.branches, [{'event': 'enter', 'symbol': 'Identity'}])
        self.assertEqual(result['coveredOutcomes'], 0)
        self.assertEqual(result['totalOutcomes'], 2)
        self.assertEqual(result['unreachable'], [])


class BranchScriptTest(BranchFixture):
    def test_nested_multiline_collection_loops_and_abrupt_exit(self):
        path = self.root/'Core/Main.xbsl'
        path.write_text('''метод Arrays(Values: Массив<Число>): Число
    для V из (
        Values
    )
        для W из Values
            если V > 0
                возврат V + W
            ;
        ;
    ;
    возврат 0
;
метод Nullable(X: Число?): Число
    если X != Неопределено
        возврат X + 1
    ;
    возврат 0
;
''')
        self.model = analyze(self.root)
        c = {'target': {**config()['target'], 'method': 'Arrays'}, 'args': [[2, 3]], 'branches': True}
        observed, p = self.trusted(c)
        self.assertEqual(observed['actual'], 4)
        loops = [r for r in summarize(p['branches'], observed['trace'])['records'] if r['kind'] == 'loop']
        self.assertEqual(len(loops), 2)
        self.assertTrue(all(r['counts']['abrupt-exit'] == 1 for r in loops))
        c = {'target': {**config()['target'], 'method': 'Nullable'}, 'args': [2], 'branches': True}
        result, _ = self.trusted(c, 'nullable'); self.assertEqual(result['actual'], 3)

    def test_relation_arrays_maps_null_and_short_circuit_in_sbsl(self):
        model = self.base/'model.json'; model.write_text(json.dumps(self.model))
        checks = []
        expressions = [
            ({'op': 'equal', 'args': [{'op': 'sum', 'args': [{'result': True}]}, {'literal': 6}]}, [1, 2, 3]),
            ({'op': 'equal', 'args': [{'op': 'get', 'args': [{'result': True}, {'literal': 'total'}]}, {'literal': 9}]}, {'total': 9}),
            ({'op': 'equal', 'args': [{'result': True}, {'literal': None}]}, None),
            ({'op': 'or', 'args': [{'literal': True}, {'op': 'greater', 'args': [{'result': True}, {'literal': 3}]}]}, 'never cast'),
            ({'op': 'equal', 'args': [{'op': 'length', 'args': [{'result': True}]}, {'literal': 3}]}, 'abc'),
            ({'op': 'equal', 'args': [{'op': 'reverse', 'args': [{'result': True}]}, {'literal': 'cba'}]}, 'abc')]
        for i, (relation, actual) in enumerate(expressions):
            checks.append({'id': str(i), 'type': 'runtime', 'points': 1, 'generative': {**config()['generative'], 'relation': relation},
                'execution': {'status': 'EXECUTED', 'actual': {'cases': [{'inputs': [0], 'result': actual}]}}})
        assignment = self.base/'relations.json'; assignment.write_text(json.dumps({'name': 'relations', 'checks': checks}))
        result = execute_engine('test', model, assignment, self.base)
        self.assertEqual([c['status'] for c in result['checks']], ['PASS'] * len(checks), result)

    def test_instrumentation_preserves_short_circuit_effects_and_loop_boundaries(self):
        c = {'target': {**config()['target'], 'method': 'Run'}, 'args': [0]}
        plain, _ = self.trusted(c, 'plain')
        observed, p = self.trusted({**c, 'branches': True, 'trace': True}, 'probed')
        self.assertEqual(plain['actual'], observed['actual'])
        self.assertFalse(any(t['event'] == 'enter' and t['symbol'].endswith('Tick') for t in observed['trace']))
        coverage = summarize(p['branches'], observed['trace'])
        loop = next(r for r in coverage['records'] if r['kind'] == 'loop')
        self.assertEqual(loop['counts'], {'enter': 1, 'complete': 1, 'exhausted': 1, 'zero': 1})
        c['args'] = [4]
        plain, _ = self.trusted(c, 'positive-plain')
        observed, p = self.trusted({**c, 'branches': True, 'trace': True}, 'positive-probed')
        self.assertEqual(plain['actual'], observed['actual'])
        self.assertEqual(sum(t['event'] == 'enter' and t['symbol'].endswith('Tick') for t in observed['trace']), 1)
        loop = next(r for r in summarize(p['branches'], observed['trace'])['records'] if r['kind'] == 'loop')
        self.assertEqual(loop['counts']['iteration'], 3)
        self.assertEqual(loop['counts']['break'], 1)
        self.assertEqual(loop['counts']['continue'], 1)
        self.assertEqual(loop['counts']['break-exit'], 1)
        self.assertEqual(loop['counts']['nonzero'], 1)

    def test_sbsl_independent_relation_and_invalid_type(self):
        model_path = self.base/'model.json'; model_path.write_text(json.dumps(self.model))
        assignment_path = self.base/'assignment.json'
        for value, status in [(3, 'PASS'), (4, 'FAIL'), ('bad', 'FAIL')]:
            check = {'id': 'invariant', 'type': 'runtime', 'points': 4, 'generative': config()['generative'],
                'execution': {'status': 'EXECUTED', 'actual': {'cases': [{'inputs': [3], 'result': value}]}}}
            assignment_path.write_text(json.dumps({'name': 'independent', 'checks': [check]}))
            result = execute_engine('test', model_path, assignment_path, self.base)
            self.assertEqual(result['checks'][0]['status'], status)
            self.assertEqual(result['score'], 4 if status == 'PASS' else 0)
        check['generative']['relation'] = {'op': 'greater', 'args': [{'result': True}, {'literal': 0}]}
        assignment_path.write_text(json.dumps({'name': 'invalid', 'checks': [check]}))
        result = execute_engine('test', model_path, assignment_path, self.base)
        self.assertEqual(result['checks'][0]['status'], 'ERROR')
        self.assertEqual(result['unavailablePoints'], 4)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Docker runtime opt-in')
class GeneratedExecutionTest(BranchFixture):
    def test_source_exception_retains_completed_outcomes_and_abrupt_loop_exit(self):
        path = self.root/'Core/Main.xbsl'
        path.write_text('метод Fail(X: Число): Число\n    пока X > 0\n        выбросить новый ИсключениеНедопустимоеСостояние("source-error")\n    ;\n    возврат X\n;\n')
        c = {'target': {**config()['target'], 'method': 'Fail'}, 'args': [1], 'branches': True, 'timeout': '15s'}
        result = run_pure(self.root, analyze(self.root), c, self.base)
        self.assertEqual(result['status'], 'ERROR', result)
        self.assertEqual(result['branchCoverage']['coveredOutcomes'], 1)
        loop = next(r for r in result['branchCoverage']['records'] if r['kind'] == 'loop')
        self.assertEqual(loop['counts']['abrupt-exit'], 1)

    def test_failure_shrinks_to_boundary_and_replays(self):
        result = run_pure(self.root, self.model, config(), self.base)
        self.assertEqual(result['status'], 'EXECUTED', result)
        counterexample = result['generative']['counterexample']
        self.assertEqual(counterexample['minimal']['inputs'], [5])
        self.assertEqual(counterexample['minimal']['result'], 6)
        self.assertEqual(counterexample['minimality'], 'local-under-declared-reductions')
        repeat = run_pure(self.root, self.model, config(), self.base)
        self.assertEqual(result['generative'], repeat['generative'])
        self.assertEqual(result['branchCoverage']['coveredOutcomes'], 2)
        replay = run_pure(self.root, self.model, counterexample['replayCheck'], self.base)
        self.assertEqual(replay['actual'], result['actual'])
        self.assertEqual(replay['generative']['counterexample']['minimal']['inputs'], [5])
        path = self.root/'Core/Main.xbsl'; path.write_text(path.read_text()+'// changed\n')
        stale = run_pure(self.root, analyze(self.root), counterexample['replayCheck'], self.base)
        self.assertEqual(stale['reasonCode'], 'invalid_test')

    def test_metamorphic_relation_and_fresh_context(self):
        c = config('Scale'); c['generative']['domains'][0]['max'] = 8
        # An involution remains inside the same declared domain.
        c['generative']['transform'] = [{'op': 'subtract', 'args': [{'literal': 8}, {'arg': 0}]}]
        c['generative']['relation'] = {'op': 'equal', 'args': [
            {'op': 'add', 'args': [{'result': True}, {'pairedResult': True}]}, {'literal': 16}]}
        result = run_pure(self.root, self.model, c, self.base)
        self.assertEqual(result['status'], 'EXECUTED', result)
        self.assertIsNone(result['generative']['counterexample'])
        self.assertEqual(result['generative']['executedCases'], 3)

    def test_array_metamorphism_and_isolated_mutable_context(self):
        path = self.root/'Core/Main.xbsl'
        path.write_text('метод Sum(Values: Массив<Число>): Число\n    пер N = 0\n    для V из Values\n        N += V\n    ;\n    возврат N\n;\n')
        self.model = analyze(self.root)
        c = config(); c['target']['method'] = 'Sum'
        c['generative']['domains'] = [{'type': 'Массив<Число>', 'minLength': 0, 'maxLength': 3, 'items': {'type': 'Число', 'min': -3, 'max': 3}}]
        c['generative']['transform'] = [{'op': 'reverse', 'args': [{'arg': 0}]}]
        c['generative']['relation'] = {'op': 'equal', 'args': [{'result': True}, {'pairedResult': True}]}
        result = run_pure(self.root, self.model, c, self.base)
        self.assertEqual(result['status'], 'EXECUTED', result)
        self.assertIsNone(result['generative']['counterexample'])
        (self.root/'Core/Counter.yaml').write_text('ВидЭлемента: Справочник\nИмя: Counter\nРеквизиты:\n  - Имя: Count\n    Тип: Число\n')
        (self.root/'Core/Counter.Объект.xbsl').write_text('метод Run(X: Число): Число\n    этот.Count += 1\n    возврат этот.Count\n;\n')
        c = config('Run'); c['target']['module'] = 'Counter.Объект'; c['context'] = {'Count': 0}
        c['generative']['relation'] = {'op': 'equal', 'args': [{'op': 'get', 'args': [{'result': True}, {'literal': 'return'}]}, {'literal': 1}]}
        result = run_pure(self.root, analyze(self.root), c, self.base)
        self.assertEqual(result['status'], 'EXECUTED', result)
        self.assertIsNone(result['generative']['counterexample'])

    def test_public_test_run_batch_status_and_unavailable_points(self):
        import yaml
        from element_test.bridge import run_test
        from element_test.batch import run_batch
        c = config('Scale'); c.update({'id': 'relation', 'type': 'runtime', 'points': 4})
        c['generative']['relation'] = {'op': 'equal', 'args': [{'result': True}, {'op': 'multiply', 'args': [{'arg': 0}, {'literal': 2}]}]}
        assignment = self.base/'assignment.yaml'; assignment.write_text(yaml.safe_dump({'name': '52', 'checks': [c]}))
        result, _ = run_test(self.root, assignment, self.base/'public')
        self.assertEqual(result['checks'][0]['status'], 'PASS', result)
        self.assertTrue((self.base/'public/runtime-evidence.json').exists())
        command = subprocess.run([str(REPO/'bin/element-test'), 'run', '--project', str(self.root), '--assignment', str(assignment), '--output', str(self.base/'alias')],capture_output=True,text=True,timeout=120)
        self.assertEqual(command.returncode, 0, command.stdout+command.stderr)
        c['generative']['relation']['args'][1]['args'][1]['literal'] = 3
        c['generative']['maxShrinks'] = 0
        assignment.write_text(yaml.safe_dump({'name': '52', 'checks': [c]}))
        result, _ = run_test(self.root, assignment, self.base/'wrong')
        self.assertEqual(result['checks'][0]['status'], 'FAIL', result)
        c['generative']['domains'][0]['type'] = 'Unknown'
        assignment.write_text(yaml.safe_dump({'name': '52', 'checks': [c]}))
        result, _ = run_test(self.root, assignment, self.base/'unavailable')
        self.assertEqual(result['checks'][0]['status'], 'UNSUPPORTED', result)
        self.assertEqual(result['unavailablePoints'], 4)
        c = config('Scale'); c.update({'id': 'relation', 'type': 'runtime', 'points': 4})
        c['generative']['cases'] = 2; c['generative']['maxShrinks'] = 0
        c['generative']['relation'] = {'op': 'equal', 'args': [{'result': True}, {'op': 'multiply', 'args': [{'arg': 0}, {'literal': 2}]}]}
        assignment.write_text(yaml.safe_dump({'name': '52', 'checks': [c]}))
        projects = [self.root]
        for name, expression in [('wrong-source', 'X * 2 + 1'), ('unsupported-source', 'Missing(X)')]:
            root = self.base/name; fixture(root)
            path = root/'Core/Main.xbsl'; path.write_text(path.read_text().replace('возврат X * 2', 'возврат '+expression))
            projects.append(root)
        manifest = self.base/'manifest.json'
        manifest.write_text(json.dumps({'schemaVersion': '1.0', 'assignment': str(assignment), 'assignmentId': 'mechanism52',
            'submissions': [{'studentId': str(i), 'project': str(p)} for i, p in enumerate(projects)]}))
        self.assertEqual(run_batch(manifest, self.base/'batch', 1), 1)
        batch = json.loads((self.base/'batch/batch-result.json').read_text())
        self.assertEqual(batch['counts'], {'passed': 1, 'failed': 1, 'incomplete': 1, 'inputError': 0})
        self.assertTrue(all(not p['cacheHit'] for p in batch['submissions']))
        if find_spec('jsonschema'):
            import jsonschema
            for path in (self.base/'public/grading.json', self.base/'batch/submissions/000001/grading.json', self.base/'wrong/grading.json', self.base/'unavailable/grading.json'):
                jsonschema.validate(json.loads(path.read_text()), json.loads((REPO/'docs/grading-v1.schema.json').read_text()))


@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS') == '1', 'PostgreSQL opt-in')
class BranchPostgresTest(BranchFixture):
    def test_source_handler_outcomes_preserve_independent_sql_audit(self):
        import secrets
        from tests.test_metadata_lifecycle import fixture as lifecycle_fixture, config as lifecycle_config
        from element_test.integration import run_integration
        lifecycle_fixture(self.root)
        c = lifecycle_config(); c['branches'] = True
        c['storage']['backend'] = 'postgres'
        c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
        with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
            result = run_integration(c, analyze(self.root), self.base, root=self.root, enabled=True)
        self.assertEqual(result['status'], 'EXECUTED', result)
        saved = next(r['value'] for r in result['actual']['storage'] if r['type'] == 'Data::Card')
        self.assertEqual(saved['Title'], 'first')
        coverage = result['branchCoverage']
        self.assertEqual(coverage['totalOutcomes'], 4)
        self.assertEqual(coverage['coveredOutcomes'], 2)
        self.assertTrue(all(r['fileHash'] and r['projectSourceHash'] for r in coverage['records']))
