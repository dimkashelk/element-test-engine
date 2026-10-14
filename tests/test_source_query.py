"""Task 47 mechanism tests; no Dvizhok-final business assignments or oracles."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from element_test.execution_plan import plan_execution
from element_test.indexer import parse_module
from element_test.model import analyze
from element_test.query_plan import query_literals
from element_test.runtime import prepare_script, run_pure
from element_test.integration import run_integration
from element_test.yaml_io import InputError

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / 'tests/corpus/virtual-tables/ordinary'
LAST = 'ВЫБРАТЬ Key, Total ИЗ Data::Book.СрезПоследних УПОРЯДОЧИТЬ ПО Key'
HISTORY = [{'type': 'Data::Book', 'filter': {'Key': 'a'}, 'rows': [
    {'Период': '2026-10-01', 'Key': 'a', 'Total': 5},
    {'Период': '2026-10-03', 'Key': 'a', 'Total': 9}]}]
REFS = [{'Идентификатор': '00000000-0000-0000-0000-00000000000' + str(i)} for i in (1, 2)]


def config():
    return {'querySource': {'file': 'Data/Totals.xbql'}, 'runtimeProfile': '9.3',
            'storage': {'idType': 'Ууид', 'registers': ['Data::Book'], 'initialRegisters': copy.deepcopy(HISTORY)},
            'clock': {'mode': 'fixed', 'date': '2026-10-02', 'time': '12:00:00', 'timezone': 'UTC'}}


class SourceQueryFixture(unittest.TestCase):
    def setUp(self):
        self.work = TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name) / 'project'
        shutil.copytree(CORPUS, self.root)
        self.query = self.root / 'Data/Totals.xbql'
        self.query.write_text(LAST)

    def plan(self, c=None):
        return plan_execution(self.root, analyze(self.root), c or config())


class SourceQueryPlanTest(SourceQueryFixture):
    def test_entry_is_original_query_with_no_method_or_expected(self):
        c = config(); c['expected'] = {'secret': 834151}
        plan = self.plan(c)
        data = plan.to_dict()
        self.assertEqual(data['symbols'], [])
        self.assertEqual(data['entry']['declaration'], '')
        self.assertEqual(data['queryEntry']['text'], LAST)
        self.assertEqual(data['queryEntry']['range'], [0, len(LAST)])
        self.assertEqual(data['queryEntry']['stages']['execution'], 'not-checked')
        self.assertNotIn('secret', plan.to_json())
        self.assertTrue(data['queryEntry']['dependencies'])
        sandbox = Path(self.work.name) / 'generated'; sandbox.mkdir()
        script = prepare_script(self.root, analyze(self.root), c, sandbox)
        self.assertEqual([n.name for n in parse_module(script.read_text())[0]], ['Скрипт'])

    def test_bare_slice_equals_empty_arguments_and_keeps_original_ranges(self):
        bare = self.plan().queries[0]['ast']
        self.query.write_text(LAST.replace('СрезПоследних', 'СрезПоследних()'))
        brackets = self.plan().queries[0]['ast']
        for key in ('source_kind', 'period_slot', 'periodicity', 'owner', 'parameters'):
            self.assertEqual(bare[key], brackets[key])
        self.assertEqual(LAST[slice(*bare['source_range'])], 'Data::Book.СрезПоследних')

    def test_literal_occurrences_are_selected_without_running_containing_method(self):
        path = self.root / 'Entry/Main.xbsl'; source = path.read_text()
        occurrences = list(query_literals(source))
        c = config(); c['querySource'] = {'file': 'Entry/Main.xbsl'}
        with self.assertRaisesRegex(InputError, 'неоднозначно'):
            self.plan(c)
        a, b, _, _ = occurrences[1]
        c['querySource']['range'] = [a, b]
        c['queryParameters'] = {'D': {'type': 'Дата', 'value': '2026-10-02'}}
        plan = self.plan(c)
        self.assertEqual(plan.entry.start, a)
        self.assertEqual(plan.symbols, [])
        self.assertEqual(plan.query_entry['family'], 'literal')
        c['querySource']['range'] = [a + 1, b]
        with self.assertRaises(InputError): self.plan(c)

    def test_invalid_selectors_hashes_and_contracts(self):
        cases = [{'file': '../Data/Totals.xbql'}, {'file': str(self.query)},
                 {'file': 'Data/Totals.yaml'}, {'file': 'Data/Totals.xbql', 'sourceHash': 'stale'},
                 {'file': 'Data/Totals.xbql', 'projectSourceHash': 'stale'},
                 {'file': 'Data/Totals.xbql', 'range': [False, len(LAST)]}]
        for selector in cases:
            c = config(); c['querySource'] = selector
            with self.subTest(selector=selector), self.assertRaises(InputError): self.plan(c)
        for key, value in [('target', {'method': 'Fake'}), ('storage', None),
                           ('queryActions', []), ('queryActions', [{'operation': 'execute', 'rows': []}]),
                           ('queryActions', [{'operation': 'write', 'objects': []}]),
                           ('queryParameters', {'unused': {'type': 'Число', 'value': 1}})]:
            c = config(); c[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(InputError): self.plan(c)

    def test_parameter_types_arrays_and_xbql_normalization(self):
        self.query.write_text('ВЫБРАТЬ Key ИЗ Data::Book.СрезПоследних ГДЕ Key В (&Keys)')
        c = config(); c['queryParameters'] = {'Keys': {'type': 'Массив<Строка>', 'value': ['a']}}
        p = self.plan(c)
        self.assertIn('&Keys', p.query_entry['text'])
        self.assertIn('%Keys', p.query_entry['astText'])
        self.assertEqual(p.queries[0]['ast']['parameters'][0]['type'], 'Массив<Строка>')
        self.query.write_text('ВЫБРАТЬ Key ИЗ Data::Book.СрезПоследних ГДЕ Total == &N')
        for params in ({}, {'N': {'type': 'Строка', 'value': 'wrong'}}, {'N': {'type': 'Число', 'value': 'wrong'}}):
            c['queryParameters'] = params
            with self.subTest(params=params), self.assertRaises(InputError): self.plan(c)

    def test_union_case_is_typed_and_union_arithmetic_remains_unavailable(self):
        self.query.write_text('ВЫБРАТЬ ВЫБОР КОГДА Total == 5 ТОГДА "missing" ИНАЧЕ Total КОНЕЦ КАК Value ИЗ Data::Book.СрезПоследних')
        p = self.plan()
        self.assertEqual(p.queries[0]['ast']['projections'][0][0]['type'], 'Строка|Число')
        self.query.write_text(self.query.read_text().replace('КОНЕЦ КАК', 'КОНЕЦ + 1 КАК'))
        with self.assertRaises(InputError): self.plan()

    def test_all_five_final_dump_forms_validate_plan_and_generate_without_execution(self):
        from hashlib import sha256
        from element_test.loader import open_project
        from element_test.query_catalog import source_records
        from element_test.query_catalog_refresh import inspect_query
        archive = REPO / 'Dvizhok-final.xdump'; before = sha256(archive.read_bytes()).hexdigest()
        with open_project(archive) as root:
            model = analyze(root)
            records = [r for r in source_records(root, model, archive.name, 'Движок') if r['family'] in {'literal', 'xbql-file'}]
            self.assertEqual(len(records), 5)
            for index, record in enumerate(records):
                module = next((m for m in model['modules'] if m['sourceFile'] == record['file']), {'namespace': record['ownerIdentity']['namespace']})
                source = (root / record['file']).read_text(encoding='utf-8-sig')
                node = next((n for n in parse_module(source)[0] if n.start <= record['range'][0] < n.end), None)
                review = inspect_query(root, model, record, module, source, node)
                self.assertTrue(review['queryPlanned'], review)
                # Only typed validation placeholders: no expected, execution or
                # independent application assessment is constructed here.
                params = {p['expression']: {'type': p['type'], 'value': [] if p['type'].startswith('Массив<') else REFS[0]}
                          for p in review['parameters']}
                c = {'querySource': {'file': record['file'], 'range': record['range'], 'sourceHash': record['sourceHash'], 'projectSourceHash': model['sourceHash']},
                     'queryParameters': params, 'storage': {'idType': 'Ууид', 'registers': review['registerOwners'], 'initial': []}, 'runtimeProfile': '9.3'}
                sandbox = Path(self.work.name) / ('dump-plan-' + str(index)); sandbox.mkdir()
                with self.subTest(file=record['file'], span=record['range']):
                    prepare_script(root, model, c, sandbox)
                    plan = json.loads((sandbox / 'execution-plan.json').read_text())
                    self.assertEqual(plan['symbols'], [])
                    self.assertEqual(plan['queryEntry']['text'], record['text'])
                    self.assertEqual(plan['queryEntry']['stages']['execution'], 'not-checked')
        self.assertEqual(sha256(archive.read_bytes()).hexdigest(), before)

    def test_sbsl_multiset_keeps_duplicates_resources_and_action_order(self):
        def observation(rows):
            return {'rows': rows, 'cardinality': len(rows), 'columns': [],
                    'resource': {'kind': 'query-result', 'closed': True, 'started': True}}
        expected = {'result': observation([{'v': 1}, {'v': 2}]), 'storage': []}
        actual = {'result': observation([{'v': 2}, {'v': 1}]), 'storage': []}
        # Request the query multiset contract through the common SBSL transport.
        from element_test.bridge import write_json
        from element_test.runtime import execute_engine
        temp = Path(self.work.name)
        def assess(a, e):
            write_json(temp / 'model.json', {'elements': [], 'modules': []})
            write_json(temp / 'assignment.json', {'checks': [{'id': 'mechanism', 'type': 'runtime', 'points': 1,
                'comparison': 'query-rows-unordered', 'expected': e, 'execution': {'status': 'EXECUTED', 'actual': a}}]})
            return execute_engine('test', temp / 'model.json', temp / 'assignment.json', temp)['checks'][0]['status']
        self.assertEqual(assess(actual, expected), 'PASS')
        actual['result']['rows'] = [{'v': 1}, {'v': 1}]
        self.assertEqual(assess(actual, expected), 'FAIL')
        actual = copy.deepcopy(expected); actual['result']['resource']['closed'] = False
        self.assertEqual(assess(actual, expected), 'FAIL')
        first, second = observation([{'v': 1}]), observation([{'v': 2}])
        self.assertEqual(assess({'result': {'actions': [second, first]}, 'storage': []},
                               {'result': {'actions': [first, second]}, 'storage': []}), 'FAIL')


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Script opt-in')
class SourceQueryDockerTest(SourceQueryFixture):
    def execute(self, c=None):
        result = run_pure(self.root, analyze(self.root), c or config(), Path(self.work.name))
        self.assertEqual(result['status'], 'EXECUTED', result)
        self.assertEqual(result['storageDiagnostics'], {'active': False, 'locks': 0})
        return result

    def test_bare_slice_fixed_clock_empty_repeat_write_and_isolation(self):
        c = config()
        result = self.execute(c)['actual']['result']
        self.assertEqual(result['rows'], [{'Key': 'a', 'Total': 5}])
        self.assertEqual(result['cardinality'], 1)
        self.assertEqual(result['resource'], {'kind': 'query-result', 'closed': True, 'started': True})
        c['queryActions'] = [{'operation': 'execute'}, {'operation': 'write', 'registers': [
            {'type': 'Data::Book', 'filter': {'Key': 'a'}, 'rows': [{'Период': '2026-10-02', 'Key': 'a', 'Total': 12}]}]}, {'operation': 'execute'}]
        actions = self.execute(c)['actual']['result']['actions']
        self.assertEqual([a['rows'] for a in actions], [[{'Key': 'a', 'Total': 5}], [{'Key': 'a', 'Total': 12}]])
        self.assertEqual(self.execute()['actual']['result']['rows'], [{'Key': 'a', 'Total': 5}])
        c = config(); c['storage']['initialRegisters'] = []
        self.assertEqual(self.execute(c)['actual']['result']['rows'], [])

    def test_left_join_union_case_null_and_columns(self):
        self.query.write_text('ВЫБРАТЬ A.Key, ВЫБОР КОГДА B.Total ЕСТЬ NULL ТОГДА "missing" ИНАЧЕ B.Total КОНЕЦ КАК Value, B.Total КАК Nullable ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Data::Book.СрезПоследних КАК B ПО A.Key == B.Key УПОРЯДОЧИТЬ ПО A.Key')
        c = config(); c['storage']['initial'] = [
            {'type': 'Data::Item', 'value': {'Ссылка': REFS[i], 'Key': key, 'Phones': [], 'Rows': []}} for i, key in enumerate(('a', 'b'))]
        result = self.execute(c)['actual']['result']
        self.assertEqual(result['rows'], [{'Key': 'a', 'Value': 5, 'Nullable': 5}, {'Key': 'b', 'Value': 'missing', 'Nullable': None}])
        types = result['columns'][1]['ТестТип']['Типы']
        self.assertIn('Std::Number', types)
        self.assertIn('Std::String', types)

    def test_typed_array_capture_and_literal(self):
        self.query.write_text('ВЫБРАТЬ Key ИЗ Data::Book.СрезПоследних ГДЕ Key В (&Keys)')
        c = config(); c['queryParameters'] = {'Keys': {'type': 'Массив<Строка>', 'value': ['a', 'a']}}
        self.assertEqual(self.execute(c)['actual']['result']['rows'], [{'Key': 'a'}])
        c['queryParameters']['Keys']['value'] = []
        self.assertEqual(self.execute(c)['actual']['result']['rows'], [])
        source = (self.root / 'Entry/Main.xbsl').read_text(); a, b, _, _ = list(query_literals(source))[1]
        c = config(); c['querySource'] = {'file': 'Entry/Main.xbsl', 'range': [a, b]}
        c['queryParameters'] = {'D': {'type': 'Дата', 'value': '2026-10-02'}}
        self.assertEqual(self.execute(c)['actual']['result']['rows'], [{'Key': 'a', 'Total': 5}])

    def test_literal_fill_resolves_local_structure_without_fake_method(self):
        path = self.root / 'Entry/Main.xbsl'
        path.write_text('структура View\n знч Key: Строка\n знч Total: Число\n;\n'
                        'метод Rows(): Объект\n возврат Запрос{ВЫБРАТЬ Key, Total ЗАПОЛНИТЬ View ИЗ Data::Book.СрезПоследних}.Выполнить()\n;\n')
        c = config(); c['querySource'] = {'file': 'Entry/Main.xbsl'}
        self.assertEqual(self.execute(c)['actual']['result']['rows'], [{'Key': 'a', 'Total': 5}])

    def test_transaction_actions_keep_detached_results(self):
        c = config(); c['storage']['transaction'] = True
        c['queryActions'] = [{'operation': 'execute'}, {'operation': 'execute'}]
        actions = self.execute(c)['actual']['result']['actions']
        self.assertEqual([a['rows'] for a in actions], [[{'Key': 'a', 'Total': 5}]] * 2)

    def test_seconds_and_moment_slices_use_fixed_clock(self):
        self.query.write_text('ВЫБРАТЬ Key, Total ИЗ Data::Seconds.СрезПоследних')
        c = config(); c['storage']['registers'] = ['Data::Seconds']
        for period, suffix in [('Секунда', ''), ('Момент', 'Z')]:
            path = self.root / 'Data/Seconds.yaml'
            path.write_text(path.read_text().replace('Периодичность: Секунда', 'Периодичность: ' + period))
            c['storage']['initialRegisters'] = [{'type': 'Data::Seconds', 'filter': {'Key': 'a'}, 'rows': [
                {'Период': '2026-10-02T12:00:00' + suffix, 'Key': 'a', 'Total': 5},
                {'Период': '2026-10-02T13:00:00' + suffix, 'Key': 'a', 'Total': 9}]}]
            with self.subTest(period=period):
                self.assertEqual(self.execute(c)['actual']['result']['rows'], [{'Key': 'a', 'Total': 5}])

    def test_public_test_run_batch_grade_independent_oracle_and_unavailable_points(self):
        import yaml
        from element_test.batch import run_batch
        from record_set_fixtures import audit
        column = lambda name, typ, qualifier: {'Имя': name, 'ТестТип': {'Типы': [typ],
            'КвалификаторЧисла': qualifier if typ == 'Std::Number' else None,
            'КвалификаторСтроки': qualifier if typ == 'Std::String' else None, 'КвалификаторБайт': None}}
        expected = {'result': {'rows': [{'Key': 'a', 'Total': 5}], 'cardinality': 1,
            'columns': [column('Key', 'Std::String', {'ДлинаСтроки': 0}),
                        column('Total', 'Std::Number', {'ДлинаЦелойЧасти': 32, 'ДлинаДробнойЧасти': 32})],
            'resource': {'kind': 'query-result', 'closed': True, 'started': True}},
            'storage': [audit(HISTORY[0])]}
        good = {'id': 'good', 'type': 'runtime', 'points': 2, 'comparison': 'query-rows-unordered', **config(), 'expected': expected}
        wrong = copy.deepcopy(good); wrong['id'] = 'wrong'; wrong['expected']['result']['rows'][0]['Total'] = 700
        invalid = copy.deepcopy(good); invalid['id'] = 'invalid'; invalid['points'] = 3
        invalid['querySource']['sourceHash'] = 'stale'
        base = Path(self.work.name); assignment = base / 'assignment.yaml'
        assignment.write_text(yaml.safe_dump({'name': 'Query mechanism', 'checks': [good, wrong, invalid]}, allow_unicode=True))
        for command in ('test', 'run'):
            output = base / command
            result = subprocess.run([str(REPO / 'bin/element-test'), command, '--project', str(self.root), '--assignment', str(assignment), '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)  # independent FAIL is preserved
            self.assertNotIn('Traceback', result.stderr)
            self.assertTrue((output / 'execution-plans.json').is_file(), result.stderr)
            self.assertTrue((output / 'runtime-evidence.json').is_file(), result.stderr)
            data = json.loads((output / 'result.json').read_text())
            self.assertEqual([c['status'] for c in data['checks']], ['PASS', 'FAIL', 'UNSUPPORTED'], data)
            self.assertEqual((data['score'], data['maxScore'], data['unavailablePoints']), (2, 4, 3))
        manifest = base / 'batch.json'
        batch_assignment = base / 'batch-assignment.yaml'
        batch_assignment.write_text(yaml.safe_dump({'name': 'Query mechanism batch', 'checks': [good, wrong]}, allow_unicode=True))
        second = base / 'second-project'; shutil.copytree(self.root, second)
        (second / 'Data/Totals.xbql').write_text(LAST.replace('Key, Total ИЗ', 'Key, Total + 1 КАК Total ИЗ'))
        manifest.write_text(json.dumps({'schemaVersion': '1.0', 'assignment': str(batch_assignment), 'assignmentId': 'query47',
            'submissions': [{'studentId': 'one', 'project': str(self.root)}, {'studentId': 'two', 'project': str(second)}]}))
        # Both entries use a valid source owner, with distinct expected business
        # outcomes; the second source intentionally differs from this fixture.
        exit_code = run_batch(manifest, base / 'batch', workers=1)
        self.assertEqual(exit_code, 1)  # intentionally failing independent oracle
        batch = json.loads((base / 'batch/batch-result.json').read_text())
        self.assertEqual(len(batch['submissions']), 2)
        self.assertEqual(batch['counts']['inputError'], 0, batch)
        self.assertEqual(batch['counts']['incomplete'], 0, batch)
        self.assertEqual(batch['counts']['failed'], 2)
        first = json.loads((base / 'batch' / batch['submissions'][0]['result']).read_text())
        self.assertEqual([c['status'] for c in first['checks']], ['PASS', 'FAIL'])

    @unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS') == '1', 'PostgreSQL opt-in')
    def test_postgres_uses_same_source_entry_and_independent_audit(self):
        c = config(); c['storage']['backend'] = 'postgres'
        c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
        result = run_integration(c, analyze(self.root), Path(self.work.name), enabled=True, root=self.root)
        self.assertEqual(result['status'], 'EXECUTED', result)
        self.assertEqual(result['actual']['result']['rows'], [{'Key': 'a', 'Total': 5}])
        self.assertEqual(result['storageDiagnostics'], {'active': False, 'locks': 0})
        self.assertEqual(result['actual']['storage'][0]['value'], HISTORY[0]['rows'])
        self.assertEqual(json.loads(result['actual']['storage'][0]['id']), {'Key': 'a'})


if __name__ == '__main__':
    unittest.main()
