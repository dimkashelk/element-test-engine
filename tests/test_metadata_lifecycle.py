"""Stage 50 mechanism fixtures, independent of final-dump business scenarios."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from tempfile import TemporaryDirectory

import yaml
from element_test.execution_plan import plan_execution
from element_test.model import analyze
from element_test.runtime import prepare_script, decode_output, REPO
from element_test.yaml_io import InputError, InvalidTestError


def fixture(root, namespace='Data', name='Card'):
    root.mkdir(exist_ok=True); (root / namespace).mkdir()
    (root / 'Проект.yaml').write_text('Имя: Lifecycle\nРежимСовместимости: 9.0\n')
    metadata = {'ВидЭлемента': 'Документ', 'Имя': name, 'ОбластьВидимости': 'ВПроекте',
        'Реквизиты': [{'Имя': 'Title', 'Тип': 'Строка', 'НезаполненноеЗначение': 'ЗапретитьВсегда', 'МаксимальнаяДлина': 6, 'Уникальность': True},
            {'Имя': 'Amount', 'Тип': 'Число', 'МинимальноеЗначение': 0, 'МаксимальноеЗначение': 100, 'ДлинаЦелойЧасти': 3, 'ДлинаДробнойЧасти': 2},
            {'Имя': 'Номер', 'Тип': 'Строка', 'Длина': 8, 'Уникальность': True,
                'Автонумерация': {'ДлинаНумерации': 3, 'Префикс': 'A', 'Постфикс': 'Z', 'Формат': {'РазделительПрефикса': '-', 'РазделительПостфикса': '/'}}}],
        'ПараметрыЗаписи': [{'Имя': 'Fail', 'Тип': 'Булево'}, {'Имя': 'Suffix', 'Тип': 'Строка'}],
        'ТабличныеЧасти': [{'Имя': 'Rows', 'Реквизиты': [{'Имя': 'Value', 'Тип': 'Число', 'МинимальноеЗначение': 0}]}]}
    (root / namespace / (name + '.yaml')).write_text(yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False))
    (root / namespace / (name + '.Объект.xbsl')).write_text('''метод ПередЗаписью(До: Card.Данные, Параметры: Card.ПараметрыЗаписи)
    если не До.Rows.Пусто()
        До.Rows[0].Value = 777
    ;
    Title += Параметры.Suffix
;
метод ПослеЗаписи(До: Card.Данные, Параметры: Card.ПараметрыЗаписи)
    если Параметры.Fail
        знч Related = новый Card.Объект(Ссылка = Card.ПолучитьСсылку("related"), Title = "link")
        Related.Записать()
        выбросить новый ИсключениеНедопустимоеСостояние("after-failure")
    ;
;
'''.replace('Card', name))
    (root / namespace / 'Main.xbsl').write_text('''метод Write(ID: Строка, Title: Строка, Amount: Число, Row: Число, Fail: Булево, Suffix: Строка): Card.Объект?
    знч O = новый Card.Объект(Ссылка = Card.ПолучитьСсылку(ID), Title = Title, Amount = Amount)
    O.Rows.Добавить(новый Card.Rows(Value = Row))
    O.Записать(новый Card.ПараметрыЗаписи(Fail = Fail, Suffix = Suffix))
    возврат O.Ссылка.ЗагрузитьОбъект()
;
метод Read(ID: Строка): Card.Объект?
    возврат Card.ПолучитьСсылку(ID).ЗагрузитьОбъект()
;
'''.replace('Card', name))


def config(namespace='Data'):
    return {'target': {'module': 'Main', 'namespace': namespace, 'method': 'Write'},
        'args': ['one', 'first', 1, 2, False, ''], 'storage': {'lifecycle': True}, 'captureException': True}


def direct_fixture(root):
    fixture(root)
    (root / 'Data/Card.Объект.xbsl').write_text('метод ПередЗаписью(До: Card.Данные, Параметры: Card.ПараметрыЗаписи)\n    Title += "!"\n;\n')
    (root / 'Data/Panel.yaml').write_text(yaml.safe_dump({'ВидЭлемента': 'КомпонентИнтерфейса', 'Имя': 'Panel',
        'Наследует': {'Тип': 'ФормаОбъекта<Card.Объект>', 'Содержимое': {'Тип': 'Надпись', 'Имя': 'Title', 'Значение': '=Объект.Title'}}}, allow_unicode=True))
    c = {'interface': {'schemaVersion': 1, 'form': {'name': 'Panel', 'namespace': 'Data'}},
        'context': {'Объект': {'Title': 'saved', 'Amount': 2}}, 'lifecycle': {'isNew': True},
        'storage': {'lifecycle': True, 'idType': 'Ууид'}, 'formEffects': {'write': True}, 'observe': ['Объект.Title'],
        'sequence': [{'writeInterface': True}], 'trace': True}
    return c


class LifecyclePlanTest(unittest.TestCase):
    def test_constraints_identity_params_and_no_expected(self):
        with TemporaryDirectory() as d:
            root = Path(d); fixture(root)
            check = config(); check['expected'] = {'secret-oracle': 123}
            plan = plan_execution(root, analyze(root), check)
            contract = plan.to_dict()['metadataLifecycle'][0]
            self.assertEqual(contract['owner'], 'Data::Card')
            self.assertEqual(len(contract['sourceHash']), 64)
            self.assertEqual(contract['writeParameters'][0]['Имя'], 'Fail')
            self.assertNotIn('secret-oracle', plan.to_json())
            prepare_script(root, analyze(root), check, root)
            text = (root / 'Card.sbsl').read_text()
            self.assertIn('ПередЗаписью(До, Параметры)', text)
            self.assertIn('ОткатитьЗапись(СнимокЗаписи)', text)

    def test_storage_schema_matches_published_contract(self):
        from element_test.metadata_lifecycle import storage_options_schema
        self.assertEqual(json.loads((REPO / 'docs/storage-options-v1.schema.json').read_text()), storage_options_schema())

    def test_invalid_options_and_unsupported_constraints(self):
        with TemporaryDirectory() as d:
            root = Path(d); fixture(root)
            check = config(); check['storage']['lifecycle'] = 1
            with self.assertRaises(InvalidTestError): plan_execution(root, analyze(root), check)
            path = root / 'Data/Card.yaml'; m = yaml.safe_load(path.read_text())
            m['Реквизиты'][2]['Автонумерация']['Unknown'] = True
            path.write_text(yaml.safe_dump(m, allow_unicode=True))
            with self.assertRaisesRegex(InputError, 'Автонумерация вне'): plan_execution(root, analyze(root), config())


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'opt-in Script runtime')
class LifecycleExecutionTest(unittest.TestCase):
    def execute(self, root, check, out):
        out.mkdir()
        script = prepare_script(root, analyze(root), check, out)
        result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)], cwd=out,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return decode_output(result.stdout)['actual']

    def test_normalization_required_bounds_unique_tables_and_after_rollback(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; fixture(root)
            check = config()
            check['sequence'] = [
                {'method': 'Write', 'args': ['one', 'first', 1.235, 2, False, '']},
                {'method': 'Write', 'args': ['two', 'second', 100, 3, False, '']},
                {'method': 'Write', 'args': ['three', 'first', 1, 2, False, '']},
                {'method': 'Write', 'args': ['one', 'update', 5, 4, False, '']},
                {'method': 'Write', 'args': ['empty', '', 1, 2, False, '']},
                {'method': 'Write', 'args': ['long', 'toolong', 1, 2, False, '']},
                {'method': 'Write', 'args': ['min', 'min', -1, 2, False, '']},
                {'method': 'Write', 'args': ['max', 'max', 101, 2, False, '']},
                {'method': 'Write', 'args': ['table', 'table', 1, -1, False, '']},
                {'method': 'Write', 'args': ['bad', 'bad', 1, 2, True, '']},
                {'method': 'Write', 'args': ['new', 'last', 2, 8, False, '!']},
                {'method': 'Read', 'args': ['related']}]
            value = self.execute(root, check, base / 'out')
            actions = [json.loads(s) if isinstance(s, str) else s for s in value['result']['actions']]
            self.assertEqual(actions[0]['result']['Amount'], 1.24)
            self.assertEqual(actions[0]['result']['Rows'], [{'Value': 2}])
            self.assertIn('unique', actions[2]['exception']['message'])
            self.assertEqual(actions[3]['result']['Rows'], [{'Value': 4}])
            for i in range(4, 10): self.assertIsNotNone(actions[i]['exception'], actions[i])
            self.assertEqual(actions[10]['result']['Title'], 'last!')
            self.assertIsNone(actions[11]['result'])
            state = {s['id']: s['value'] for s in value['storage']}
            self.assertEqual(set(state), {'one', 'two', 'new'})
            self.assertEqual(state['new']['Номер'], 'A-004/Z')
            self.assertEqual(actions[0]['result']['Title'], 'first')




def register_fixture(root):
    fixture(root)
    for name, kind in [('Cash', 'РегистрНакопления'), ('History', 'РегистрСведений')]:
        data = {'ВидЭлемента': kind, 'Имя': name, 'ОбластьВидимости': 'ВПроекте',
            'Измерения': [{'Имя': 'Partner', 'Тип': 'Строка', 'МаксимальнаяДлина': 10}, {'Имя': 'Item', 'Тип': 'Строка', 'МаксимальнаяДлина': 10}],
            'Ресурсы': [{'Имя': 'Price', 'Тип': 'Число', 'МинимальноеЗначение': 0, 'ДлинаДробнойЧасти': 2}]}
        if name == 'Cash':
            data['ВидРегистра'] = 'Остатки'; data['Реквизиты'] = [{'Имя': 'Регистратор', 'Тип': 'Card.Ссылка'}]
        else: data['Периодичность'] = 'Момент'
        (root / 'Data' / (name + '.yaml')).write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    path = root / 'Data/Card.Объект.xbsl'; source = path.read_text()
    source = source.replace('    если Параметры.Fail\n', '''    знч CashRows = новый Cash.НаборЗаписей()
    CashRows.Фильтр.Установить(Регистратор = Ссылка)
    CashRows.ДобавитьЗапись(Период = новый ДатаВремя("2026-10-11T12:00:00"), ВидЗаписи = ВидЗаписиРегистраНакопления.Приход, Partner = "p", Item = "i", Price = Amount)
    CashRows.Записать()
    знч HistoryRows = новый History.НаборЗаписей()
    HistoryRows.Фильтр.Установить(Partner = "p", Item = "i")
    HistoryRows.ДобавитьЗапись(Период = новый Момент("2026-10-11T12:00:00Z"), Partner = "p", Item = "i", Price = Amount)
    HistoryRows.Записать()
    если Параметры.Fail
''')
    path.write_text(source)
    path = root / 'Data/Main.xbsl'
    path.write_text(path.read_text() + '''метод HistoryWrite(Partner: Строка, Item: Строка, Time: Момент, Price: Число, Replace: Булево): Массив<History.Запись>
    знч HistoryRows = новый History.НаборЗаписей()
    HistoryRows.Фильтр.Установить(Partner = Partner, Item = Item)
    HistoryRows.ДобавитьЗапись(Период = Time, Partner = Partner, Item = Item, Price = Price)
    HistoryRows.Записать(Replace)
    HistoryRows.Прочитать()
    возврат HistoryRows.Записи
;
метод HistoryRead(Partner: Строка, Item: Строка): Массив<History.Запись>
    знч HistoryRows = новый History.НаборЗаписей()
    HistoryRows.Фильтр.Установить(Partner = Partner, Item = Item)
    HistoryRows.Прочитать()
    возврат HistoryRows.Записи
;
''')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'opt-in Script runtime')
class RegisterLifecycleTest(LifecycleExecutionTest):
    # Do not rerun the parent mechanism test in this group.
    test_normalization_required_bounds_unique_tables_and_after_rollback = None

    def test_register_replacement_multidimension_moment_append_duplicate_rollback(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; register_fixture(root)
            check = config(); check['storage']['registers'] = ['Data::Cash', 'Data::History']
            check['sequence'] = [
                {'method': 'Write', 'args': ['one', 'first', 1, 2, False, '']},
                {'method': 'Write', 'args': ['one', 'update', 3, 2, False, '']},
                {'method': 'Write', 'args': ['bad', 'bad', 8, 2, True, '']},
                {'method': 'HistoryRead', 'args': ['p', 'i']},
                {'method': 'HistoryWrite', 'args': ['p', 'i', '2026-10-11T13:00:00Z', 9.876, False]},
                {'method': 'HistoryWrite', 'args': ['p', 'j', '2026-10-11T13:00:00Z', 7, False]},
                {'method': 'HistoryWrite', 'args': ['p', 'i', '2026-10-11T13:00:00Z', 88, False]},
                {'method': 'HistoryRead', 'args': ['p', 'i']},
                {'method': 'HistoryWrite', 'args': ['p', 'i', '2026-10-11T14:00:00Z', -1, False]},
                {'method': 'HistoryRead', 'args': ['p', 'j']}]
            value = self.execute(root, check, base / 'out'); actions = value['result']['actions']
            self.assertEqual(actions[3]['result'][0]['Price'], 3)
            self.assertEqual([x['Price'] for x in actions[4]['result']], [3, 9.88])
            self.assertIn('Неуникальный', actions[6]['exception']['message'])
            self.assertEqual([x['Price'] for x in actions[7]['result']], [3, 9.88])
            self.assertIn('Минимальное', actions[8]['exception']['message'])
            self.assertEqual(actions[9]['result'][0]['Price'], 7)
            cash = [s['value'] for s in value['storage'] if s['type'] == 'Data::Cash']
            self.assertEqual(len(cash), 1); self.assertEqual(cash[0][0]['Price'], 3)
            self.assertFalse(any(s['id'] in {'bad', 'related'} for s in value['storage']))


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'opt-in Script runtime')
class ConstantsLifecycleTest(unittest.TestCase):
    def test_interface_constants_constraints_and_storage_audit(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; root.mkdir()
            (root / 'Проект.yaml').write_text('Имя: Constants\nРежимСовместимости: 9.0\n')
            (root / 'Settings.yaml').write_text(yaml.safe_dump({'ВидЭлемента': 'НаборКонстант', 'Имя': 'Settings',
                'Константы': [{'Имя': 'Title', 'Тип': 'Строка', 'НезаполненноеЗначение': 'ЗапретитьВсегда', 'МаксимальнаяДлина': 6}]}, allow_unicode=True))
            (root / 'Panel.yaml').write_text(yaml.safe_dump({'ВидЭлемента': 'КомпонентИнтерфейса', 'Имя': 'Panel',
                'Наследует': {'Тип': 'ФормаЗаписиНабораКонстант<Settings.Запись>', 'Содержимое': {
                    'Тип': 'ПолеВвода<Строка>', 'Имя': 'Title', 'Значение': '=Запись.Title'}}}, allow_unicode=True))
            c = {'interface': {'schemaVersion': 1, 'form': {'name': 'Panel'}}, 'storage': {'lifecycle': True},
                'formEffects': {'write': True}, 'constants': {'Settings': {'Title': 'saved'}}, 'observe': ['Запись.Title'],
                'sequence': [{'input': {'selector': {'name': 'Title'}, 'value': 'edited'}}, {'writeInterface': True},
                             {'input': {'selector': {'name': 'Title'}, 'value': ''}}, {'writeInterface': True},
                             {'input': {'selector': {'name': 'Title'}, 'value': 'local'}}]}
            out = base / 'out'; out.mkdir(); script = prepare_script(root, analyze(root), c, out)
            r = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)], cwd=out, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            actual = decode_output(r.stdout)['actual']
            self.assertEqual(actual['storage'][0]['value']['Title'], 'edited')
            self.assertEqual(actual['result']['actions'][4]['observation']['state']['Запись.Title'], 'local')
            self.assertEqual(actual['result']['actions'][4]['observation']['constants']['Settings']['Title'], 'edited')
            plan = json.loads((out / 'execution-plan.json').read_text())
            self.assertEqual(plan['metadataLifecycle'][0]['kind'], 'НаборКонстант')
            self.assertEqual(actual['result']['actions'][3]['outcome'], 'rejected')
            self.assertIn('required', actual['result']['actions'][3]['observation']['exception']['message'])


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'opt-in Script runtime')
class DirectLifecycleTest(LifecycleExecutionTest):
    test_normalization_required_bounds_unique_tables_and_after_rollback = None

    def test_declarative_write_executes_original_hooks_without_form_method(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'
            c = direct_fixture(root)
            actual = self.execute(root, c, base / 'out')
            self.assertEqual(actual['storage'][0]['value']['Title'], 'saved!')
            self.assertEqual(actual['result']['actions'][0]['observation']['state']['Объект.Title'], 'saved!')
            ir = json.loads((base / 'out/execution-plan.json').read_text())
            self.assertEqual([s['identity']['declaration'] for s in ir['symbols']], ['ПередЗаписью'])
            self.assertEqual(ir['entry']['declaration'], '')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS') == '1', 'opt-in PostgreSQL')
class SqlLifecycleTest(unittest.TestCase):
    def test_independent_postgres_register_audit_and_after_rollback(self):
        import secrets
        from unittest.mock import patch
        from element_test.integration import run_integration
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; register_fixture(root)
            c = config(); c['storage'].update(backend='postgres', registers=['Data::Cash', 'Data::History'], transaction=True)
            c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
            c['sequence'] = [{'method': 'Write', 'args': ['one', 'first', 1, 2, False, '']},
                             {'method': 'Write', 'args': ['one', 'update', 3, 2, False, '']},
                             {'method': 'Write', 'args': ['bad', 'bad', 8, 2, True, '']},
                             {'method': 'HistoryRead', 'args': ['p', 'i']}]
            with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
                fact = run_integration(c, analyze(root), base, root=root, enabled=True)
            self.assertEqual(fact['status'], 'EXECUTED', fact)
            state = fact['actual']['storage']
            self.assertFalse(any(row['id'] in {'bad', 'related'} for row in state))
            self.assertEqual(next(row['value'] for row in state if row['type'] == 'Data::Cash')[0]['Price'], 3)
            self.assertEqual(next(row['value'] for row in state if row['type'] == 'Data::Card')['Title'], 'update')
            self.assertEqual(fact['actual']['result']['actions'][-1]['result'][0]['Price'], 3)

    def test_declarative_original_hooks_publish_independent_sql_snapshots(self):
        import secrets
        from unittest.mock import patch
        from element_test.integration import run_integration
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; c = direct_fixture(root)
            c['storage']['backend'] = 'postgres'
            c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
            with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
                fact = run_integration(c, analyze(root), base, root=root, enabled=True)
            self.assertEqual(fact['status'], 'EXECUTED', fact)
            actual = fact['actual']
            self.assertEqual(actual['storage'][0]['value']['Title'], 'saved!')
            self.assertGreaterEqual(len(actual['sqlSnapshots']), 2)
            self.assertEqual(actual['sqlSnapshots'][-1], actual['storage'])



@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'opt-in Docker/public CLI')
class PublicLifecycleTest(unittest.TestCase):
    def test_public_test_run_batch_independent_grades_and_mutation(self):
        import copy
        import shutil
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; fixture(root)
            c = config()
            obj = {'Ссылка': {'Идентификатор': 'one'}, 'Title': 'first', 'Amount': 1, 'Номер': 'A-001/Z', 'Rows': [{'Value': 2}]}
            expected = {'result': {'result': copy.deepcopy(obj), 'exception': None}, 'storage': [{'type': 'Data::Card', 'id': 'one', 'value': copy.deepcopy(obj)}]}
            good = {'id': 'good', 'type': 'runtime', 'points': 2, **c, 'expected': expected}
            wrong = copy.deepcopy(good); wrong['id'] = 'wrong'; wrong['expected']['result']['result']['Amount'] = 19
            unavailable = copy.deepcopy(good); unavailable['id'] = 'unavailable'; unavailable['points'] = 3
            unavailable['target']['method'] = 'Unsupported'
            path = root / 'Data/Main.xbsl'; path.write_text(path.read_text() + 'метод Unsupported()\n    Missing()\n;\n')
            unavailable['args'] = []
            assignment = base / 'assignment.yaml'
            assignment.write_text(yaml.safe_dump({'name': 'Lifecycle engine', 'checks': [good, wrong, unavailable]}, allow_unicode=True))
            for command in ('test', 'run'):
                out = base / command
                result = subprocess.run([str(REPO / 'bin/element-test'), command, '--project', str(root), '--assignment', str(assignment), '--output', str(out)], capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 1, result.stderr)
                data = json.loads((out / 'result.json').read_text())
                self.assertEqual([x['status'] for x in data['checks']], ['PASS', 'FAIL', 'UNSUPPORTED'], data)
                self.assertEqual((data['score'], data['maxScore'], data['unavailablePoints']), (2, 4, 3))
            second = base / 'second'; shutil.copytree(root, second)
            path = second / 'Data/Main.xbsl'; path.write_text(path.read_text().replace('Amount = Amount)', 'Amount = Amount + 1)'))
            from element_test.batch import run_batch
            manifest = base / 'batch.json'
            manifest.write_text(json.dumps({'schemaVersion': '1.0', 'assignment': str(assignment), 'assignmentId': 'lifecycle50',
                'submissions': [{'studentId': 'one', 'project': str(root)}, {'studentId': 'two', 'project': str(second)}]}))
            self.assertEqual(run_batch(manifest, base / 'batch', workers=2), 1)
            batch = json.loads((base / 'batch/batch-result.json').read_text())
            for item, statuses in zip(batch['submissions'], (['PASS', 'FAIL', 'UNSUPPORTED'], ['FAIL', 'FAIL', 'UNSUPPORTED'])):
                data = json.loads((base / 'batch' / item['result']).read_text())
                self.assertEqual([x['status'] for x in data['checks']], statuses)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'opt-in Script runtime')
class DomainsLifecycleTest(LifecycleExecutionTest):
    test_normalization_required_bounds_unique_tables_and_after_rollback = None

    def test_before_creation_update_detachment_nullable_enum_and_scalar_union(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; fixture(root)
            path = root / 'Data/Card.yaml'; data = yaml.safe_load(path.read_text())
            data['Реквизиты'][0]['Уникальность'] = False
            data['Реквизиты'] += [{'Имя': 'Seen', 'Тип': 'Число'},
                {'Имя': 'Option', 'Тип': 'Число?', 'МинимальноеЗначение': 0, 'НезаполненноеЗначение': 'ЗапретитьВсегда'},
                {'Имя': 'Mixed', 'Тип': 'Число|Строка|?'}, {'Имя': 'Mode', 'Тип': 'Status'}]
            path.write_text(yaml.safe_dump(data, allow_unicode=True))
            (root / 'Data/Status.yaml').write_text('ВидЭлемента: Перечисление\nИмя: Status\nОбластьВидимости: ВПроекте\nЭлементы:\n  - {Имя: Open, ПоУмолчанию: Истина}\n  - {Имя: Closed}\n')
            path = root / 'Data/Card.Объект.xbsl'; path.write_text(path.read_text().replace('    Title += Параметры.Suffix', '    Seen = До.Amount\n    Title += Параметры.Suffix'))
            (root / 'Data/Main.xbsl').write_text('метод Domain(ID: Строка, Option: Число?, Mixed: Число|Строка|?, Mode: Status, Amount: Число): Card.Объект?\n'
                '    знч O = новый Card.Объект(Ссылка = Card.ПолучитьСсылку(ID), Title = "title", Amount = Amount, Option = Option, Mixed = Mixed, Mode = Mode)\n'
                '    O.Записать()\n    возврат O.Ссылка.ЗагрузитьОбъект()\n;\n')
            c = config(); c['target']['method'] = 'Domain'; c['args'] = ['one', 0, {'type': 'Число', 'value': 4}, 'Open', 2]
            c['sequence'] = [{'method': 'Domain', 'args': c['args']},
                {'method': 'Domain', 'args': ['one', 2, {'type': 'Строка', 'value': 'x'}, 'Closed', 3]},
                {'method': 'Domain', 'args': ['bad', None, None, 'Open', 4]},
                {'method': 'Domain', 'args': ['min', -1, None, 'Open', 4]},
                {'method': 'Domain', 'args': ['last', 0, None, 'Open', 4]}]
            actual = self.execute(root, c, base / 'out'); actions = actual['result']['actions']
            self.assertEqual(actions[0]['result']['Seen'], 2)
            self.assertEqual(actions[1]['result']['Seen'], 2)
            self.assertEqual(actions[1]['result']['Mode'], 'Closed')
            self.assertIn('required', actions[2]['exception']['message'])
            self.assertIn('Минимальное', actions[3]['exception']['message'])
            self.assertIsNone(actions[4]['exception'])
            self.assertIsNone(actions[4]['result']['Mixed'])

    def test_renamed_owner_and_namespace_preserve_lifecycle(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; fixture(root, 'Other', 'Voucher')
            c = config('Other')
            c['args'] = ['one', 'first', 1.236, 2, False, '!']
            actual = self.execute(root, c, base / 'out')
            self.assertEqual(actual['storage'][0]['type'], 'Other::Voucher')
            self.assertEqual(actual['storage'][0]['value']['Title'], 'first!')
            self.assertEqual(actual['storage'][0]['value']['Amount'], 1.24)

    def test_year_number_series_and_precision_overflow(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base / 'source'; fixture(root)
            path = root / 'Data/Card.yaml'; data = yaml.safe_load(path.read_text())
            data['Реквизиты'][0]['Уникальность'] = False
            data['Реквизиты'][2]['Периодичность'] = 'Год'
            data['Реквизиты'].append({'Имя': 'Дата', 'Тип': 'Дата'})
            path.write_text(yaml.safe_dump(data, allow_unicode=True))
            (root / 'Data/Main.xbsl').write_text('метод Year(ID: Строка, Date: Дата): Card.Объект?\n'
                '    знч O = новый Card.Объект(Ссылка = Card.ПолучитьСсылку(ID), Title = "title", Дата = Date)\n'
                '    O.Записать()\n    возврат O.Ссылка.ЗагрузитьОбъект()\n;\n'
                'метод Overflow()\n    знч O = новый Card.Объект(Title = "title", Amount = 1000, Дата = новый Дата("2026-01-01"))\n    O.Записать()\n;\n')
            c = config(); c['target']['method'] = 'Year'; c['args'] = ['one', '2025-01-01']
            c['sequence'] = [{'method': 'Year', 'args': ['one', '2025-01-01']},
                {'method': 'Year', 'args': ['two', '2026-01-01']}, {'method': 'Year', 'args': ['three', '2026-12-31']}, {'method': 'Overflow', 'args': []}]
            actual = self.execute(root, c, base / 'out')
            self.assertEqual([a['result']['Номер'] for a in actual['result']['actions'][:3]], ['A-001/Z', 'A-001/Z', 'A-002/Z'])
            self.assertIn('precision', actual['result']['actions'][3]['exception']['message'])


if __name__ == '__main__': unittest.main()
