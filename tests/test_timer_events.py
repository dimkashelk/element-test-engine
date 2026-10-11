"""Task 48 mechanism acceptance; portable fixtures, no dump business scenarios."""
import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

import yaml

from element_test.execution_plan import plan_execution
from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure
from element_test.yaml_io import InputError

REPO = Path(__file__).resolve().parents[1]
SOURCE = '''метод Start()
    ПодключитьОбработчикТаймера(&Tick, 1с)
    ПодключитьОбработчикТаймера(&Once, 2с, Истина)
;
метод Tick()
    Count += 1
    Text += "T"
    Stamp = "${Дата.Сейчас().ВСтроку()} ${Время.Сейчас().ВСтроку()}"
    если Count == 3
        ОтключитьОбработчикТаймера(&этот.Tick)
    ;
;
метод Once()
    Text += "O"
;
метод Restart()
    ПодключитьОбработчикТаймера(&Tick, 2с)
;
метод Stop()
    ОтключитьОбработчикТаймера(&Tick)
    ОтключитьОбработчикТаймера(&Tick)
;
'''


def fixture(root, namespace='Alpha', name='Panel', source=SOURCE):
    def write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    write(root / 'Проект.yaml', {'ВидЭлемента': 'Проект', 'Имя': 'TimerMechanism',
          'Поставщик': 'Tests', 'Версия': '1.0', 'РежимСовместимости': '9.0'})
    write(root / namespace / 'Подсистема.yaml', {'ВидЭлемента': 'Подсистема', 'Имя': namespace,
          'ОбластьВидимости': 'ВПроекте'})
    write(root / namespace / (name + '.yaml'), {'ВидЭлемента': 'КомпонентИнтерфейса',
          'Имя': name, 'ОбластьВидимости': 'ВПроекте', 'Наследует': {'Тип': 'Форма'},
          'Свойства': [{'Имя': 'Count', 'Тип': 'Число'}, {'Имя': 'Text', 'Тип': 'Строка'},
                       {'Имя': 'Stamp', 'Тип': 'Строка'}]})
    (root / namespace / (name + '.xbsl')).write_text(source)


def config(namespace='Alpha', name='Panel'):
    return {'target': {'module': name, 'namespace': namespace, 'method': 'Start'}, 'args': [],
            'context': {}, 'observe': ['Count', 'Text', 'Stamp'], 'executorLocale': 'en-US',
            'clock': {'mode': 'fixed', 'date': '2028-02-29', 'time': '23:59:59', 'timezone': 'UTC'},
            'timers': {'onClose': 'cancel'},
            'sequence': [{'method': 'Start', 'args': []}, {'advanceTime': 3000}, {'closeTimers': True}]}


class TimerFixture(unittest.TestCase):
    def setUp(self):
        self.work = TemporaryDirectory(); self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name) / 'project'
        fixture(self.root)

    def plan(self, check=None):
        return plan_execution(self.root, analyze(self.root), check or config())

    def source(self, text):
        (self.root / 'Alpha/Panel.xbsl').write_text(text)


class TimerPlanTest(TimerFixture):
    def test_callback_closure_identity_ranges_and_no_teacher_answer(self):
        c = config(); c['expected'] = {'SECRET_TEACHER_ANSWER': 938273}
        plan = self.plan(c)
        self.assertEqual({s.identity.declaration for s in plan.symbols}, {'Start', 'Tick', 'Once'})
        self.assertEqual({cb['identity']['declaration'] for cb in plan.timers['callbacks']}, {'Tick', 'Once'})
        for cb in plan.timers['callbacks']:
            a, b = cb['range']
            self.assertIn('метод ' + cb['identity']['declaration'], SOURCE[a:b])
        self.assertNotIn('SECRET_TEACHER_ANSWER', plan.to_json())
        self.assertEqual(plan.timers['execution'], 'not-checked')
        generated = Path(self.work.name) / 'generated'; generated.mkdir()
        prepare_script(self.root, analyze(self.root), c, generated)
        rendered = '\n'.join(p.read_text() for p in generated.glob('*.sbsl'))
        self.assertIn('Count += 1', rendered)
        self.assertIn('ТестТаймеры.ПодключитьОбработчикТаймера', rendered)
        self.assertIn('Контекст.Tick()', rendered)
        self.assertNotIn('SECRET_TEACHER_ANSWER', rendered)

    def test_invalid_contracts_and_steps(self):
        from element_test.timer_events import timer_options_schema
        self.assertEqual(json.loads((REPO / 'docs/timer-options-v1.schema.json').read_text()), timer_options_schema())
        changes = [{'timers': None}, {'timers': {}}, {'timers': {'onClose': 'drain'}},
                   {'timers': {'onClose': 'cancel', 'maxEvents': True}},
                   {'timers': {'onClose': 'cancel', 'maxEvents': 0}},
                   {'timers': {'onClose': 'cancel', 'maxEvents': 10001}},
                   {'timers': {'onClose': 'cancel', 'unknown': 1}},
                   {'clock': {'mode': 'docker', 'timezone': 'UTC'}}, {'executorLocale': None},
                   {'sequence': [{'advanceTime': -1}]}, {'sequence': [{'advanceTime': 1.5}]},
                   {'sequence': [{'advanceTime': True}]}, {'sequence': [{'timerEvent': 'all'}]},
                   {'sequence': [{'closeTimers': 1}]}, {'sequence': [{'closeTimers': True}, {'advanceTime': 1}]},
                   {'sequence': [{'advanceTime': 1, 'method': 'Start', 'args': []}]}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(InputError): self.plan({**config(), **change})
        for remove in ('clock', 'timers', 'executorLocale'):
            c = config(); del c[remove]
            with self.assertRaises(InputError): self.plan(c)

    def test_dynamic_callbacks_and_bad_signatures_refused(self):
        mutations = [SOURCE.replace('&Tick, 1с', '() -> Tick(), 1с'),
                     SOURCE.replace('метод Tick()', 'метод Tick(X: Число)'),
                     SOURCE.replace('метод Once()', 'метод Once(): Число'),
                     SOURCE.replace('&Tick, 1с', '&Other.Tick, 1с'),
                     SOURCE.replace('&Tick, 1с', '&Tick, 1с, Ложь, 4'),
                     SOURCE.replace('ПодключитьОбработчикТаймера(&Tick, 1с)',
                                    'знч Handler = &Tick\n    ПодключитьОбработчикТаймера(Handler, 1с)')]
        for source in mutations:
            self.source(source)
            with self.subTest(source=source), self.assertRaises(InputError): self.plan()

    def test_shadowed_api_is_original_project_code(self):
        self.source('''метод Start()
    ПодключитьОбработчикТаймера()
;
метод ПодключитьОбработчикТаймера()
    Count += 7
;
''')
        plan = self.plan()
        self.assertFalse(plan.timers['callbacks'])
        self.assertFalse(any(b.category == 'timer-events' for b in plan.bindings))
        c = config(); c.pop('timers'); c.pop('sequence')
        self.assertTrue(self.plan(c))
        from element_test.timer_events import bind_timer_call
        from element_test.indexer import method_call_expressions, parse_module
        # An arbitrary computed receiver with the same method name is not the
        # free platform API, even when its path cannot be named statically.
        source = 'метод Start()\n (Unknown).ПодключитьОбработчикТаймера(&Tick, 1с)\n;\n'
        node = parse_module(source)[0][0]
        call = next(c for c in method_call_expressions(source, node) if c.name == 'ПодключитьОбработчикТаймера')
        self.assertIsNone(bind_timer_call(plan, plan.entry, node, call, project=False, callback=False))

    def test_clock_date_time_moment_and_local_shadowing(self):
        self.source('''метод Start()
    Stamp = "${Дата.Сейчас().ВСтроку()} ${Время.Сейчас().ВСтроку()} ${ДатаВремя.Сейчас().ВСтроку()} ${Момент.Сейчас().ВСтроку()}"
;
''')
        plan = self.plan()
        self.assertEqual(len([b for b in plan.bindings if b.category == 'clock']), 4)
        self.source('''метод Start()
    знч Дата = "local"
    Stamp = Дата.ВВерхнийРегистр()
;
''')
        self.assertFalse(any(b.category == 'clock' for b in self.plan().bindings))

    def test_action_reference_cannot_address_time_action(self):
        self.source(SOURCE + 'метод Accept(S: Строка)\n;\n')
        c = config(); c['snapshotArgs'] = True
        c['sequence'] = [{'advanceTime': 0}, {'method': 'Accept', 'args': [{'actionResult': 0}]}]
        with self.assertRaisesRegex(InputError, 'action вызова'):
            prepare_script(self.root, analyze(self.root), c, Path(self.work.name))

    def test_original_welcome_registration_plans_without_dump_execution(self):
        from element_test.assignment import load_assignment
        from element_test.loader import open_project
        archive = REPO / 'Dvizhok-final.xdump'
        before = sha256(archive.read_bytes()).hexdigest()
        c = next(c for c in load_assignment(REPO / 'assignments/dvizhok-welcome-calendar')['checks']
                 if c.get('target', {}).get('method') == 'ПослеСоздания')
        c = {**c, 'timers': {'onClose': 'cancel'}}
        c.pop('expected', None)
        with open_project(archive) as root:
            plan = plan_execution(root, analyze(root), c)
            self.assertEqual({cb['identity']['declaration'] for cb in plan.timers['callbacks']},
                             {'ОбновитьТекущееВремя', 'ОбновитьПриветствие'})
            prepare_script(root, analyze(root), c, Path(self.work.name))
            self.assertEqual(plan.timers['execution'], 'not-checked')
        self.assertEqual(sha256(archive.read_bytes()).hexdigest(), before)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Script Docker opt-in')
class TimerDockerTest(TimerFixture):
    def execute(self, c=None):
        fact = run_pure(self.root, analyze(self.root), c or config(), Path(self.work.name))
        self.assertEqual(fact['status'], 'EXECUTED', fact)
        return fact

    def test_periodic_once_tie_order_self_cancel_and_leap_midnight(self):
        fact = self.execute()
        actions = fact['actual']['actions']
        self.assertEqual([e['elapsedMs'] for e in actions[1]['events']], [1000, 2000, 2000, 3000])
        self.assertEqual([e['state']['Text'] for e in actions[1]['events']], ['T', 'TT', 'TTO', 'TTOT'])
        self.assertEqual(actions[1]['result'], {'Count': 3, 'Text': 'TTOT', 'Stamp': '2028-03-01 00:00:02.000'})
        self.assertEqual(actions[1]['timers']['active'], [])
        self.assertEqual(fact['clock']['snapshot'], '2028-03-01T00:00:02')
        self.assertTrue(fact['timers']['closed'])
        self.assertEqual(fact['timers']['executed'], 4)

    def test_reconnect_replaces_from_now_next_event_and_close_cancels(self):
        c = config(); c['sequence'] = [{'method': 'Start', 'args': []}, {'advanceTime': 500},
            {'method': 'Restart', 'args': []}, {'timerEvent': 'next'}, {'timerEvent': 'next'}, {'closeTimers': True}]
        fact = self.execute(c); a = fact['actual']['actions']
        self.assertEqual(a[1]['events'], [])
        self.assertEqual(a[2]['timers']['active'][-1]['dueMs'], 2500)
        self.assertEqual([a[3]['events'][0]['elapsedMs'], a[4]['events'][0]['elapsedMs']], [2000, 2500])
        self.assertEqual(a[4]['result']['Text'], 'OT')
        self.assertEqual(fact['timers'], {'elapsedMs': 2500, 'active': [], 'executed': 2, 'closed': True, 'cancelledOnClose': 1})

    def test_cancel_other_equal_due_reconnect_inside_callback_and_zero_once(self):
        self.source('''метод Start()
    ПодключитьОбработчикТаймера(&Tick, 0с, Истина)
    ПодключитьОбработчикТаймера(&Once, 0с, Истина)
;
метод Tick()
    Count += 1
    ОтключитьОбработчикТаймера(&Once)
    если Count == 1
        ПодключитьОбработчикТаймера(&Tick, 1с, Истина)
    ;
;
метод Once()
    Text = "unexpected"
;
''')
        a = self.execute()['actual']['actions'][1]
        self.assertEqual([e['elapsedMs'] for e in a['events']], [0, 1000])
        self.assertEqual(a['result']['Count'], 2)
        self.assertEqual(a['result']['Text'], '')

    def test_empty_next_cancel_idempotence_automatic_close_and_isolation(self):
        c = config(); c['sequence'] = [{'method': 'Start', 'args': []}, {'method': 'Stop', 'args': []},
            {'advanceTime': 2000}, {'timerEvent': 'next'}]
        a = self.execute(c)['actual']['actions']
        self.assertEqual(a[2]['result']['Text'], 'O')
        self.assertEqual(a[3]['events'], [])
        self.assertEqual(a[3]['timers']['elapsedMs'], 2000)
        c['sequence'] = [{'method': 'Start', 'args': []}]
        fact = self.execute(c)
        self.assertEqual(fact['timers']['cancelledOnClose'], 2)
        self.assertEqual(fact['actual']['actions'][0]['result']['Count'], 0)
        self.assertEqual(fact['timers']['elapsedMs'], 0)
        self.assertEqual(self.execute()['actual']['actions'][1]['result']['Count'], 3)

    def test_limit_and_invalid_interval_are_safe_outcomes(self):
        c = config(); c['timers']['maxEvents'] = 2
        fact = run_pure(self.root, analyze(self.root), c, Path(self.work.name))
        self.assertEqual(fact['status'], 'UNSUPPORTED', fact)
        self.assertEqual(fact['reasonCode'], 'unsupported_contract', fact)
        self.assertIn('maxEvents', fact['message'])
        self.assertTrue(fact['timers']['closed'])
        self.source(SOURCE.replace('&Tick, 1с', '&Tick, 0с'))
        c = config(); c['sequence'] = [{'method': 'Start', 'args': []}]
        c['captureException'] = True; c['captureExceptionTypes'] = ['ИсключениеНедопустимыйАргумент']
        fact = self.execute(c)
        self.assertEqual(fact['actual']['actions'][0]['result']['exception']['type'], 'Std::IllegalArgumentException')
        self.assertTrue(fact['timers']['closed'])

    def test_portability_same_name_other_namespace_timezone_and_clock_types(self):
        fixture(self.root, 'Beta', 'ClockFace', SOURCE.replace('Tick', 'Pulse').replace('Once', 'Finish'))
        c = config('Beta', 'ClockFace'); c['clock']['timezone'] = 'Europe/Moscow'
        c['clock']['time'] = '05:59:59'; c['executorLocale'] = 'ru-RU'
        fact = self.execute(c)
        self.assertEqual(fact['actual']['actions'][1]['result']['Stamp'], '2028-02-29 06:00:02.000')
        self.assertEqual(fact['clock']['timezone'], 'Europe/Moscow')
        self.source('''метод Start()
    Stamp = "${Дата.Сейчас().ВСтроку()} ${Время.Сейчас().ВСтроку()} ${ДатаВремя.Сейчас().ВСтроку()} ${Момент.Сейчас().ВСтроку()}"
;
''')
        c = config(); c['clock']['timezone'] = 'Europe/Moscow'
        a = self.execute(c)['actual']['actions'][0]
        self.assertIn('2028-02-29T23:59:59', a['result']['Stamp'])
        self.assertIn('2028-02-29T20:59:59.000Z', a['result']['Stamp'])

    def test_public_test_run_batch_independent_grade_and_unavailable_points(self):
        from element_test.batch import run_batch
        self.source('''метод Start()
    ПодключитьОбработчикТаймера(&Tick, 1с)
;
метод Tick()
    Count += 1
    если Count == 2
        ОтключитьОбработчикТаймера(&Tick)
    ;
;
''')
        c = config(); c['observe'] = ['Count']
        c['sequence'] = [{'method': 'Start', 'args': []}, {'advanceTime': 2000}]
        # Read only the source selector identity, never execute to build expected.
        key = self.plan(c).timers['callbacks'][0]['id']
        initial_timer = {'elapsedMs': 0, 'active': [{'callback': key, 'intervalMs': 1000,
            'dueMs': 1000, 'order': 1, 'once': False}], 'executed': 0, 'closed': False, 'cancelledOnClose': 0}
        final_timer = {'elapsedMs': 2000, 'active': [], 'executed': 2, 'closed': False, 'cancelledOnClose': 0}
        expected = {'actions': [{'result': {'Count': 0}, 'timers': initial_timer, 'clock': '2028-02-29T23:59:59'},
            {'result': {'Count': 2}, 'timers': final_timer, 'clock': '2028-03-01T00:00:01', 'events': [
                {'callback': key, 'elapsedMs': 1000, 'state': {'Count': 1}, 'clock': '2028-03-01T00:00:00'},
                {'callback': key, 'elapsedMs': 2000, 'state': {'Count': 2}, 'clock': '2028-03-01T00:00:01'}]}]}
        good = {'id': 'good', 'type': 'runtime', 'points': 2, **c, 'expected': expected}
        wrong = copy.deepcopy(good); wrong['id'] = 'wrong'; wrong['expected']['actions'][1]['result']['Count'] = 90
        invalid = copy.deepcopy(good); invalid['id'] = 'invalid'; invalid['points'] = 3
        invalid['timers']['maxEvents'] = True
        base = Path(self.work.name); assignment = base / 'assignment.yaml'
        assignment.write_text(yaml.safe_dump({'name': 'Timer mechanism', 'checks': [good, wrong, invalid]}, allow_unicode=True))
        for command in ('test', 'run'):
            output = base / command
            result = subprocess.run([str(REPO / 'bin/element-test'), command, '--project', str(self.root),
                '--assignment', str(assignment), '--output', str(output)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 1, result.stderr)
            data = json.loads((output / 'result.json').read_text())
            self.assertEqual([f['status'] for f in data['checks']], ['PASS', 'FAIL', 'UNSUPPORTED'], data)
            self.assertEqual((data['score'], data['maxScore'], data['unavailablePoints']), (2, 4, 3))
            self.assertTrue((output / 'execution-plans.json').is_file())
        second = base / 'second-project'; shutil.copytree(self.root, second)
        path = second / 'Alpha/Panel.xbsl'; path.write_text(path.read_text().replace('Count += 1', 'Count += 2'))
        assignment.write_text(yaml.safe_dump({'name': 'Timer mechanism', 'checks': [good, wrong]}, allow_unicode=True))
        manifest = base / 'batch.json'
        manifest.write_text(json.dumps({'schemaVersion': '1.0', 'assignment': str(assignment), 'assignmentId': 'timer48',
            'submissions': [{'studentId': 'one', 'project': str(self.root)}, {'studentId': 'two', 'project': str(second)}]}))
        self.assertEqual(run_batch(manifest, base / 'batch', workers=2), 1)
        data = json.loads((base / 'batch/batch-result.json').read_text())
        self.assertEqual(data['counts']['inputError'], 0, data)
        self.assertEqual(data['counts']['incomplete'], 0, data)
        self.assertEqual(data['counts']['failed'], 2, data)
        for submission, statuses in zip(data['submissions'], (['PASS', 'FAIL'], ['FAIL', 'FAIL'])):
            result = json.loads((base / 'batch' / submission['result']).read_text())
            self.assertEqual([f['status'] for f in result['checks']], statuses)

    def test_storage_moment_boundary_advances_with_session_and_static_callback(self):
        # The implicit query boundary must read the current clock, not bake in
        # the initial moment during Python generation.
        root = Path(self.work.name) / 'queries'
        shutil.copytree(REPO / 'tests/corpus/virtual-tables/ordinary', root)
        path = root / 'Data/Seconds.yaml'
        path.write_text(path.read_text().replace('Периодичность: Секунда', 'Периодичность: Момент'))
        (root / 'Entry/Main.xbsl').write_text('''метод Start()
    Tick()
    ПодключитьОбработчикТаймера(&Tick, 1с, Истина)
;
метод Tick()
    знч R = Запрос{ВЫБРАТЬ Key, Total ИЗ Data::Seconds.СрезПоследних}.Выполнить()
;
''')
        c = config(); c.pop('context'); c.pop('observe')
        c['target'] = {'module': 'Main', 'namespace': 'Entry', 'method': 'Start'}
        c['clock']['timezone'] = 'Europe/Moscow'
        c['sequence'] = [{'method': 'Start', 'args': []}, {'advanceTime': 1000}]
        c['storage'] = {'idType': 'Ууид', 'registers': ['Data::Seconds']}
        fact = run_pure(root, analyze(root), c, Path(self.work.name))
        self.assertEqual(fact['status'], 'EXECUTED', fact)
        boundaries = [s.partition('query:slice-boundary:')[2] for s in fact['storageTrace'] if s.startswith('query:slice-boundary:')]
        self.assertEqual(boundaries, ['2028-02-29T20:59:59.000Z', '2028-02-29T21:00:00.000Z'])
        self.assertIsNone(fact['actual']['result']['actions'][1]['events'][0]['state'])

    def test_single_invocation_observes_registration_before_automatic_close(self):
        c = config(); c.pop('sequence')
        fact = self.execute(c)
        self.assertEqual(len(fact['actual']['timers']['active']), 2)
        self.assertFalse(fact['actual']['timers']['closed'])
        self.assertTrue(fact['timers']['closed'])
        self.assertEqual(fact['timers']['cancelledOnClose'], 2)


if __name__ == '__main__':
    unittest.main()
