"""Stateful generation and live cell gates, with independent SBSL/SQL evidence."""
import copy
import json
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from element_test.execution_plan import plan_execution
from element_test.generative import inputs, planning_check, shrink_candidates, validate, options_schema
from element_test.generative_sequences import accepts_sequence
from element_test.integration import run_integration
from element_test.model import analyze
from element_test.runtime import run_pure
from element_test.storage import PostgresSession
from element_test.yaml_io import InputError, InvalidTestError
from test_engine_e03_e04 import native
from test_interface_actions import fixture as ui_fixture, config as ui_config, write

ID = '12345678-1234-4234-8234-123456789abc'


def fixture(root, ns='Data'):
    root.mkdir(); (root/ns).mkdir()
    (root/'Проект.yaml').write_text('Имя: Stateful\nПоставщик: Tests\nРежимСовместимости: 9.0\n')
    (root/ns/'Item.yaml').write_text('ВидЭлемента: Справочник\nИмя: Item\nОбластьВидимости: ВПроекте\nРеквизиты:\n- {Имя: Amount, Тип: Число}\n')
    (root/ns/'Item.Объект.xbsl').write_text('''метод ПослеЗаписи(До: Item.Данные, ПараметрыЗаписи: Item.ПараметрыЗаписи)
    если Amount < 0
        выбросить новый ИсключениеНедопустимоеСостояние("rollback")
    ;
;
''')
    (root/ns/'Flow.xbsl').write_text('''метод Create(Amount: Число): Число
    знч Existing = новый Item.Ссылка(новый Ууид("IDENTIFIER"))
    если Existing.ЗагрузитьОбъект() != Неопределено
        выбросить новый ИсключениеНедопустимоеСостояние("case leaked storage")
    ;
    знч O = новый Item.Объект(Ид = новый Ууид("IDENTIFIER"), Amount = Amount)
    O.Записать()
    возврат O.Amount
;
метод Update(Amount: Число): Число
    знч Ref = новый Item.Ссылка(новый Ууид("IDENTIFIER"))
    знч O = Ref.ЗагрузитьОбъект() как Item.Объект
    O.Amount = Amount
    O.Записать()
    возврат O.Amount
;
метод Read(): Число
    знч Ref = новый Item.Ссылка(новый Ууид("IDENTIFIER"))
    возврат (Ref.ЗагрузитьОбъект() как Item.Объект).Amount
;
'''.replace('IDENTIFIER', ID))


def check(ns='Data', sql=False):
    def op(name, method, before, after, domains):
        return dict(name=name, method=method, domains=domains, **{'from': before, 'to': after})
    number = lambda a,b: dict(type='Число', min=a, max=b)
    c = {'target': dict(module='Flow', namespace=ns, method='Create'),
        'storage': {'backend': 'postgres' if sql else 'memory', 'idType': 'Ууид', 'lifecycle': True},
        'captureException': True, 'timeout': '15s',
        'generative': {'schemaVersion': 1, 'seed': 73, 'cases': 3, 'maxShrinks': 8,
            'sequence': {'initialState': 'empty', 'finalState': 'done', 'minLength': 4, 'maxLength': 7, 'operations': [
                op('create', 'Create', 'empty', 'created', [number(1,4)]),
                op('update', 'Update', 'created', 'saved', [number(6,6)]),
                op('rewrite', 'Update', 'saved', 'saved', [number(6,6)]),
                op('reject', 'Update', 'saved', 'failed', [number(-5,-1)]),
                op('read', 'Read', 'failed', 'done', [])]},
            'relation': {'op': 'equal', 'args': [
                {'op': 'get', 'args': [
                    {'op': 'get', 'args': [
                        {'op': 'at', 'args': [{'op': 'get', 'args': [{'result': True}, {'literal': 'storage'}]}, {'literal': 0}]},
                        {'literal': 'value'}]}, {'literal': 'Amount'}]}, {'literal': 6}]}}}
    if sql: c['integration'] = dict(backend='postgres', operation='metadata-storage')
    return c


class StatefulPlanTest(unittest.TestCase):
    def test_seed_preconditions_dead_ends_and_strict_replay(self):
        c = check()['generative']; c['cases'] = 32
        original = inputs(c); self.assertEqual(original, inputs(copy.deepcopy(c)))
        self.assertEqual([len(v[0]) for v in original[:2]], [4, 7])
        c['seed'] += 1; self.assertNotEqual(original, inputs(c))
        for values in original:
            self.assertTrue(accepts_sequence(c['sequence'], values))
            for candidate in shrink_candidates(c, values):
                self.assertTrue(accepts_sequence(c['sequence'], candidate))
        c['replayInputs'] = original
        self.assertEqual(inputs(c), original)
        c['replayInputs'][0][0].pop(0)
        with self.assertRaises(InvalidTestError): validate(c)
        c = check()['generative']; c['sequence']['operations'][0]['from'] = 'unreachable'
        with self.assertRaises(InvalidTestError): validate(c)
        # A short dead-end branch cannot be chosen for a longer case.
        c = check()['generative']; c['sequence']['operations'].append(dict(c['sequence']['operations'][0], name='dead', to='dead'))
        self.assertTrue(all(accepts_sequence(c['sequence'], v) for v in inputs(c)))

    def test_every_operation_and_domain_is_validated_before_execution(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; fixture(root)
            c = check(); op = c['generative']['sequence']['operations'][-1]
            op['method'] = 'Missing'
            with patch('element_test.runtime.run_pure') as executor:
                r = run_pure(root, analyze(root), c, base)
            executor.assert_not_called(); self.assertEqual(r['status'], 'UNSUPPORTED', r)
            c = check(); c['generative']['sequence']['operations'][0]['domains'][0]['type'] = 'Строка'
            c['generative']['sequence']['operations'][0]['domains'][0] = {'type':'Строка', 'values':['wrong']}
            with patch('element_test.runtime.run_pure') as executor:
                r = run_pure(root, analyze(root), c, base)
            executor.assert_not_called(); self.assertEqual(r['reasonCode'], 'invalid_test', r)
            for bad in ({'integration': {'backend':'postgres','operation':'sql-smoke'}},
                        {'interface': {}}, {'sequence': []}):
                with self.assertRaises(InvalidTestError): planning_check({**check(), **bad})

    def test_schema_and_postgres_opt_in_and_incomplete_cases(self):
        try: import jsonschema
        except ImportError: jsonschema = None
        if jsonschema:
            jsonschema.Draft202012Validator.check_schema(options_schema())
            jsonschema.validate(check()['generative'], options_schema())
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; fixture(root); model = analyze(root)
            r = run_integration(check(sql=True), model, base, root=root)
            self.assertEqual(r['reasonCode'], 'backend_unavailable', r)
            self.assertEqual(r['generative']['executedCases'], 0)
            with patch('element_test.runtime.run_pure', return_value={'status':'TIMEOUT','reasonCode':'timeout'}):
                r = run_pure(root, model, check(), base)
            self.assertEqual(r['status'], 'TIMEOUT'); self.assertEqual(r['generative']['executedCases'], 0)


class StatefulScriptTest(unittest.TestCase):
    def test_plan_inventory_includes_operations_not_selected_by_seed(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; fixture(root)
            path = root/'Data/Flow.xbsl'
            path.write_text(path.read_text()+'''метод Unused(Value: Булево)
    если Value
        возврат
    ;
;
''')
            c = check(); c['generative'].update(cases=1, maxShrinks=0)
            c['generative']['sequence']['operations'].append(dict(name='unused', method='Unused',
                domains=[{'type':'Булево'}], **{'from':'unreachable', 'to':'done'}))
            plans = []
            with patch('element_test.runtime.run_pure', side_effect=native):
                r = run_pure(root, analyze(root), c, base, plan_sink=plans)
            self.assertEqual(r['status'], 'EXECUTED', r)
            self.assertEqual(len(plans), 1)
            self.assertIn('Unused', [s['identity']['declaration'] for s in plans[0]['symbols']])
            self.assertEqual({v['id'] for v in plans[0]['branches']}, {v['id'] for v in r['branchCoverage']['records']})
            unobserved = [v for v in r['branchCoverage']['records'] if v['symbol']['declaration'] == 'Unused']
            self.assertEqual(len(unobserved), 1)
            self.assertEqual(sum(unobserved[0]['counts'].values()), 0)

    def test_public_grade_pass_fail_and_unavailable_points(self):
        from element_test.bridge import run_test
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; fixture(root)
            good = check(); good.update(id='good', type='runtime', points=2)
            good['generative'].update(cases=1, maxShrinks=0)
            bad = copy.deepcopy(good); bad['id'] = 'bad'
            bad['generative']['relation']['args'][-1] = {'literal':7}
            unsupported = copy.deepcopy(good); unsupported['id'] = 'unavailable'
            unsupported['generative']['sequence']['operations'][-1]['method'] = 'Missing'
            assignment = base/'assignment.yaml'
            write(assignment, {'name':'e05', 'checks':[good,bad,unsupported]})
            with patch('element_test.runtime.run_pure', side_effect=native):
                result, _ = run_test(root, assignment, base/'public')
            self.assertEqual([v['status'] for v in result['checks']], ['PASS','FAIL','UNSUPPORTED'], result)
            self.assertEqual(result['unavailablePoints'], 2)
            self.assertTrue((base/'public/runtime-evidence.json').exists())

    def test_original_create_rewrite_rollback_independent_relation_shrink_and_replay(self):
        for ns in ('Data', 'Учет'):
            with self.subTest(ns=ns), TemporaryDirectory() as d:
                base = Path(d); root = base/'source'; fixture(root, ns); model = analyze(root)
                c = check(ns)
                directories = []
                def execute(*args, **kwargs):
                    directories.append(args[3]); return native(*args, **kwargs)
                with patch('element_test.runtime.run_pure', side_effect=execute):
                    r = run_pure(root, model, c, base)
                self.assertEqual(r['status'], 'EXECUTED', r)
                self.assertIsNone(r['generative']['counterexample'], r)
                self.assertEqual(r['generative']['executedCases'], 3)
                self.assertEqual(len(set(directories)), 3)
                for case in r['actual']['cases']:
                    actions = case['result']['result']['actions']
                    self.assertEqual(actions[0]['result'], case['inputs'][0][0]['args'][0])
                    self.assertEqual(actions[-2]['exception']['message'], 'rollback')
                    self.assertEqual(actions[-1]['result'], 6)
                c['generative']['relation']['args'][-1] = {'literal': 7}
                c['generative']['replayInputs'] = [inputs(c['generative'])[1]]
                c['generative']['cases'] = 1
                with patch('element_test.runtime.run_pure', side_effect=execute):
                    bad = run_pure(root, model, c, base)
                counter = bad['generative']['counterexample']; self.assertIsNotNone(counter, bad)
                self.assertEqual(len(counter['original']['inputs'][0]), 7)
                self.assertEqual(len(counter['minimal']['inputs'][0]), 4)
                self.assertTrue(accepts_sequence(c['generative']['sequence'], counter['minimal']['inputs']))
                replay = counter['replayCheck']
                with patch('element_test.runtime.run_pure', side_effect=execute):
                    again = run_pure(root, model, replay, base)
                self.assertEqual(bad['actual'], again['actual'])
                self.assertEqual(len(directories), len(set(directories)))
                self.assertTrue(all(not p.exists() for p in directories))
                replay['generative']['replaySourceHash'] = '0'*64
                self.assertEqual(run_pure(root, model, replay, base)['reasonCode'], 'invalid_test')


class CellGateScriptTest(unittest.TestCase):
    def test_all_flags_current_row_move_edit_parent_and_detached_snapshots(self):
        for flag, expression in [('Доступность', '=RowData.Amount > 0'), ('Видимость', '=RowData.Amount > 0'), ('ТолькоЧтение', '=RowData.Amount <= 0')]:
            for ns in ('Alpha', 'Проверка'):
                with self.subTest(flag=flag, ns=ns), TemporaryDirectory() as d:
                    base = Path(d); root = base/'source'; ui_fixture(root, namespace=ns)
                    path = root/ns/'Panel.yaml'; form = yaml.safe_load(path.read_text())
                    grid = form['Наследует']['Содержимое']['Содержимое'][-1]
                    grid['Колонки'][0][flag] = expression
                    write(path, form)
                    c = ui_config(namespace=ns)
                    c['context']['Объект']['Lines'] = [{'Amount': 3,'Caption':'yes'}, {'Amount': 0,'Caption':'no'}]
                    def edit(row, value): return {'input': {'selector': {'name':'Amount'}, 'row': row, 'value':value}}
                    c['sequence'] = [edit(1, 99), edit(0, 0), edit(0, 88),
                        {'tableCommand': {'selector': {'name':'Grid'}, 'command':'ПереместитьСтрокуВверх', 'row':1}}, edit(0,77)]
                    r = native(root, analyze(root), c, base)
                    actions = r['actual']['actions']
                    self.assertEqual([a['outcome'] for a in actions], ['blocked','applied','blocked','applied','blocked'])
                    self.assertEqual(actions[0]['observation']['state']['Объект.Lines'][0]['Amount'], 3)
                    self.assertEqual(actions[-1]['observation']['state']['Объект.Lines'], [{'Amount':0,'Caption':'no'}, {'Amount':0,'Caption':'yes'}])
                    # Reordering must also open a gate previously closed at this index.
                    c['sequence'] = [c['sequence'][3], edit(0, 77), edit(1, 4)]
                    r = native(root, analyze(root), c, base)
                    self.assertEqual([a['outcome'] for a in r['actual']['actions']], ['applied','blocked','applied'])
                    grid['Доступность'] = False; write(path, form)
                    c['sequence'] = [edit(0, 77)]
                    self.assertEqual(native(root, analyze(root), c, base)['actual']['actions'][0]['outcome'], 'blocked')
                    c['sequence'] = [edit(99, 77)]
                    r = native(root, analyze(root), c, base)
                    self.assertEqual(r['status'], 'UNSUPPORTED', r)
                    c['sequence'] = [edit(0,77)]; grid['Колонки'][0][flag] = '=RowData.Missing'
                    write(path, form)
                    with self.assertRaises(InputError): plan_execution(root, analyze(root), c)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Docker/PostgreSQL opt-in')
class StatefulBackendTest(unittest.TestCase):
    def test_postgres_scalar_transform_preserves_exact_decimal_sql_audit(self):
        with TemporaryDirectory() as d, patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
            base = Path(d); root = base/'source'; fixture(root)
            c = check(sql=True)
            c['generative'] = {'schemaVersion':1, 'seed':73, 'cases':2, 'maxShrinks':0,
                'domains':[{'type':'Число', 'values':[{'decimal':'0.123456789012345678901234'}]}],
                'transform':[{'arg':0}],
                'relation':{'op':'equal','args':[
                    {'op':'get','args':[{'result':True},{'literal':'storage'}]},
                    {'op':'get','args':[{'pairedResult':True},{'literal':'storage'}]}]}}
            result = run_integration(c, analyze(root), base, root=root, enabled=True)
            self.assertEqual(result['status'], 'EXECUTED', result)
            self.assertIsNone(result['generative']['counterexample'], result)
            self.assertEqual(result['generative']['runtimeExecutions'], 4)
            for case in result['actual']['cases']:
                self.assertEqual(case['result']['storage'][0]['value']['Amount'], {'decimal':'0.123456789012345678901234'})
            runs = result['generative']['backendRuns']
            self.assertEqual(len({r['runId'] for r in runs}), 4)
            self.assertTrue(all(r['cleanup'] for r in runs))

    def test_memory_postgres_generated_replay_shrink_and_independent_audit(self):
        for sql in (False, True):
            with self.subTest(sql=sql), TemporaryDirectory() as d, patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
                base = Path(d); root = base/'source'; fixture(root); model = analyze(root)
                c = check(sql=sql); c['generative']['cases'] = 2
                audit_values = []; original = PostgresSession.audit
                def audit(session, database, docker):
                    value = original(session, database, docker); audit_values.append(copy.deepcopy(value)); return value
                def execute(current):
                    return run_integration(current, model, base, root=root, enabled=True) if sql else run_pure(root, model, current, base)
                with patch.object(PostgresSession, 'audit', audit):
                    r = execute(c)
                    self.assertEqual(r['status'], 'EXECUTED', r)
                    self.assertIsNone(r['generative']['counterexample'], r)
                    c['generative']['cases'] = 1; c['generative']['maxShrinks'] = 2
                    c['generative']['replayInputs'] = [r['actual']['cases'][1]['inputs']]
                    c['generative']['relation']['args'][-1] = {'literal':7}
                    bad = execute(c); counter = bad['generative']['counterexample']
                    self.assertIsNotNone(counter, bad)
                    self.assertEqual(len(counter['original']['inputs'][0]), 7)
                    self.assertEqual(len(counter['minimal']['inputs'][0]), 4)
                    again = execute(counter['replayCheck'])
                    self.assertEqual(bad['actual'], again['actual'])
                if sql:
                    self.assertTrue(audit_values)
                    for value in audit_values:
                        self.assertEqual(value, [{'type':'Data::Item','id':ID,'value':{'Ссылка':{'Идентификатор':ID},'Amount':6}}])
                    runs = r['generative']['backendRuns'] + bad['generative']['backendRuns'] + again['generative']['backendRuns']
                    self.assertEqual(len({v['runId'] for v in runs}), len(runs))
                    self.assertTrue(all(v['cleanup'] for v in runs))
