"""E01/E02: portable original source, independent expectations and SQL audit."""
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.integration import run_integration
from element_test.model import analyze
from element_test.storage import PostgresSession
from element_test.runtime import prepare_script, run_pure
from element_test.yaml_io import InputError, InvalidTestError
from record_set_fixtures import CORPUS, REPO, info_check, audit
from test_record_sets import grade, normalized

EVIDENCE = REPO / 'result/engine-e01-e02'

# English field names deliberately match existing portable corpus contracts.
def source(base, renamed=False):
    root = base / 'source'
    shutil.copytree(CORPUS / 'information', root)
    (root / 'Data/Log.yaml').write_text('ВидЭлемента: Документ\nИмя: Log\nОбластьВидимости: ВПроекте\nРеквизиты:\n  - {Имя: Phase, Тип: Число}\n')
    (root / 'Service/Events.xbsl').write_text('''метод Stamp(Phase: Число)
    знч Log = новый Data::Log.Объект()
    Log.Ссылка.Идентификатор = новый Ууид("77777777-7777-4777-8777-777777777777")
    Log.Phase = Phase
    Log.Записать()
;
''')
    (root / 'Data/Prices.НаборЗаписей.xbsl').write_text('''@Обработчик
метод ПередЗаписью(Замещать: Булево)
    Service::Events.Stamp(1)
    для Row из этот
        если Row.Factor < 0
            выбросить новый ИсключениеВалидации("before-denied")
        ;
        Row.Price += (Замещать ? 10 : 20)
    ;
;
@Обработчик
метод ПослеЗаписи(Замещать: Булево)
    знч Stored = новый Prices.НаборЗаписей()
    Stored.Фильтр.Установить(Key = "base", Region = "EU")
    Stored.Прочитать()
    для Row из Stored
        если Row.Factor == 999
            Service::Events.Stamp(99)
            выбросить новый ИсключениеНедопустимоеСостояние("after-denied")
        ;
    ;
    знч Ref = новый Log.Ссылка(Идентификатор = новый Ууид("77777777-7777-4777-8777-777777777777"))
    знч Before = Ref.ЗагрузитьОбъект() как Log.Объект
    Service::Events.Stamp(Before.Phase * 10 + 2 + (Замещать ? 0 : 1))
;
''')
    main = root / 'Entry/Flow.xbsl'
    main.write_text(main.read_text() + '''
метод Hooks(Factor: Число, Replace: Булево): Объект
    знч Set = новый Book.НаборЗаписей()
    Set.Фильтр.Установить(Key = "base", Region = "EU")
    Set.ДобавитьЗапись(Период = новый Дата(2026, 10, 1), Key = "base", Region = "EU", Price = 3, Factor = Factor, Link = Неопределено)
    Set.Записать(Replace)
    знч Empty = Set.Пусто()
    Set.Прочитать()
    возврат {"empty": Empty, "rows": Set.Записи}
;
метод Collections(Values: ЧитаемаяКоллекция<КлючИЗначение<Строка, Data::Item.Ссылка?>>, Mutable: Коллекция<Число>, Pair: КлючИЗначение<Число, Коллекция<Строка?>>): Объект
    знч Names = новый Массив<Строка>()
    для Value из Values
        Names.Добавить(Value.Ключ + (Value.Значение == Неопределено ? "null" : (Value.Значение как Data::Item.Ссылка).Идентификатор.ВСтроку()))
    ;
    Mutable.Добавить(9)
    Pair.Значение.Добавить("changed")
    возврат {"names": Names, "numbers": Mutable, "key": Pair.Ключ, "strings": Pair.Значение}
;
''')
    if renamed:
        for path in list(root.rglob('*')):
            if path.is_file():
                text = path.read_text().replace('Prices', 'История').replace('Item', 'Товар').replace('Data', 'Учет')
                path.write_text(text)
                if 'Prices' in path.name or 'Item' in path.name:
                    path.rename(path.with_name(path.name.replace('Prices', 'История').replace('Item', 'Товар')))
        (root / 'Data').rename(root / 'Учет')
    return root


def check(renamed=False, factor=1, replace=True, sql=False, lifecycle=False):
    c = info_check('Hooks', [factor, replace], sql=sql)
    c['storage']['lifecycle'] = lifecycle
    c['captureException'] = True
    if renamed:
        for seed in c['storage']['initialRegisters']:
            seed['type'] = 'Учет::История'
        c['storage']['registers'] = ['Учет::История']
    return c


class EngineExtensionsPlanTest(unittest.TestCase):
    def test_generic_arity_nested_nullable_and_invalid_pair_inputs(self):
        c = ProjectTypes({'elements': [], 'modules': []})
        for typ in ['ЧитаемаяКоллекция<Строка>', 'Коллекция<КлючИЗначение<Число, Строка?>>?', 'КлючИЗначение<Число, Коллекция<Строка?>>']:
            c.require(typ)
        for typ in ['Коллекция<Строка, Число>', 'КлючИЗначение<Строка>', 'КлючИЗначение<Строка, Число, Булево>']:
            with self.assertRaises(InputError): c.require(typ)
        for value in [[], {'Ключ': 1}, {'Ключ': 1, 'Значение': 'wrong'}, {'Ключ': 1, 'Значение': [], 'extra': 2}]:
            with self.assertRaises(InvalidTestError): c.literal(value, 'КлючИЗначение<Число, Коллекция<Строка?>>')

    def test_hooks_reachable_only_for_writes_seed_bypass_and_exact_source(self):
        for renamed in [False, True]:
            with TemporaryDirectory() as d:
                base = Path(d); root = source(base, renamed); c = check(renamed)
                p = plan_execution(root, analyze(root), c)
                names = [s.identity.declaration for s in p.symbols]
                self.assertIn('ПередЗаписью', names); self.assertIn('ПослеЗаписи', names); self.assertIn('Stamp', names)
                for s in p.symbols:
                    self.assertEqual((root / s.identity.source_file).read_text()[s.start:s.end], s.source)
                prepare_script(root, analyze(root), c, base)
                c['target']['method'] = 'Read'; c['args'] = ['base']; c.pop('captureException')
                p = plan_execution(root, analyze(root), c)
                self.assertFalse(any(s.identity.declaration in {'ПередЗаписью', 'ПослеЗаписи'} for s in p.symbols))
                path = next(root.rglob('*.НаборЗаписей.xbsl'))
                path.write_text(path.read_text().replace('Замещать: Булево', 'Замещать: Строка'))
                with self.assertRaisesRegex(InputError, 'Обработчик набора'): prepare_script(root, analyze(root), check(renamed), base)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS') == '1', 'opt-in Script and PostgreSQL')
class EngineExtensionsRuntimeTest(unittest.TestCase):
    def execute(self, root, c, out, sql=False):
        if sql:
            reads = []; original = PostgresSession.audit
            def audit_sql(session, database, docker):
                value = original(session, database, docker); reads.append(copy.deepcopy(value)); return value
            with patch.object(PostgresSession, 'audit', audit_sql):
                r = run_integration(c, analyze(root), out, root=root, enabled=True)
            self.assertTrue(r.get('integration', {}).get('cleanup'), r)
            self.sql_audit = reads[-1] if reads else None
            r['independentSqlAudit'] = self.sql_audit
        else: r = run_pure(root, analyze(root), c, out)
        self.assertEqual(r['status'], 'EXECUTED', r)
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        return r

    def test_collection_pair_original_source_independent_arguments_and_grading(self):
        for renamed in [False, True]:
            with TemporaryDirectory() as d:
                base = Path(d); root = source(base, renamed)
                c = check(renamed); c.pop('captureException'); c['target']['method'] = 'Collections'; c['snapshotArgs'] = True
                c['args'] = [[{'Ключ': 'a', 'Значение': {'Идентификатор': '12345678-1234-4234-8234-123456789abc'}}, {'Ключ': 'b', 'Значение': None}], [1, 2], {'Ключ': 7, 'Значение': [None, 'old']}]
                c['sequence'] = [{'method': 'Collections', 'args': copy.deepcopy(c['args'])} for _ in range(2)]
                r = self.execute(root, c, base)
                write_json(EVIDENCE / f'collections-{renamed}.json', r)
                wanted = {'names': ['a12345678-1234-4234-8234-123456789abc', 'bnull'], 'numbers': [1, 2, 9], 'key': 7, 'strings': [None, 'old', 'changed']}
                for action in r['actual']['result']['actions']:
                    self.assertEqual(action['result'], wanted)
                    self.assertEqual(action['args'][1], [1, 2, 9])
                observed_args = [copy.deepcopy(c['args'][0]), [1, 2, 9], {'Ключ': 7, 'Значение': [None, 'old', 'changed']}]
                expected = {'result': {'actions': [{'result': copy.deepcopy(wanted), 'args': copy.deepcopy(observed_args)} for _ in range(2)]},
                            'storage': [audit(copy.deepcopy(s)) for s in c['storage']['initialRegisters']]}
                self.assertEqual(grade(r['actual'], expected, base), 'PASS')
                expected['result']['actions'][0]['result']['key'] = -1
                self.assertEqual(grade(r['actual'], expected, base), 'FAIL')
                self.assertEqual(c['args'][1], [1, 2])

    def test_hooks_order_replace_append_business_denial_after_error_and_sql(self):
        with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
            for renamed, sql, lifecycle in [(False, False, False), (True, False, True), (False, True, False), (True, True, True)]:
                with self.subTest(renamed=renamed, sql=sql, lifecycle=lifecycle), TemporaryDirectory() as d:
                    base = Path(d); root = source(base, renamed)
                    for factor, replace in [(1, True), (1, False), (-1, True), (999, True)]:
                        c = check(renamed, factor, replace, sql, lifecycle)
                        r = self.execute(root, c, base, sql)
                        write_json(EVIDENCE / f'hooks-{renamed}-{sql}-{factor}-{replace}.json', r)
                        seeds = c['storage']['initialRegisters']
                        expected_storage = [audit(s) for s in seeds]
                        result = r['actual']['result']
                        if factor in [-1, 999]:
                            self.assertIsNotNone(result['exception'])
                            self.assertEqual(result['exception']['message'], 'before-denied' if factor == -1 else 'after-denied')
                        else:
                            self.assertIsNone(result['exception'])
                            row = {'Период': '2026-10-01', 'Key': 'base', 'Region': 'EU', 'Price': 13 if replace else 23, 'Factor': 1, 'Link': None}
                            expected_storage[0]['value'] = ([row] if replace else seeds[0]['rows'] + [row])
                            owner = 'Учет::Log' if renamed else 'Data::Log'
                            expected_storage.append({'type': owner, 'id': '77777777-7777-4777-8777-777777777777', 'value': {'Ссылка': {'Идентификатор': '77777777-7777-4777-8777-777777777777'}, 'Phase': 12 if replace else 13}})
                            self.assertEqual(result['result']['empty'], not replace)
                            self.assertEqual(result['result']['rows'], expected_storage[0]['value'])
                        self.assertEqual(normalized(r['actual'])['storage'], normalized({'storage': expected_storage})['storage'])
                        if sql:
                            self.assertEqual(normalized({'storage': self.sql_audit})['storage'], normalized({'storage': expected_storage})['storage'])
                        expected_result = ({'result': None, 'exception': {
                            'type': 'ИсключениеВалидации' if factor == -1 else 'Std::IllegalStateException',
                            'message': 'before-denied' if factor == -1 else 'after-denied'}} if factor in [-1, 999]
                            else {'result': {'empty': not replace, 'rows': copy.deepcopy(expected_storage[0]['value'])}, 'exception': None})
                        expected = {'result': expected_result, 'storage': expected_storage}
                        self.assertEqual(grade(r['actual'], expected, base), 'PASS')
                        expected['storage'][0]['value'].append({'wrong': True})
                        self.assertEqual(grade(r['actual'], expected, base), 'FAIL')

    def test_accumulation_hooks_write_parameters_and_rollback(self):
        from record_set_fixtures import accumulation_check, REFS
        import yaml
        with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
            for sql in [False, True]:
                with TemporaryDirectory() as d:
                    base = Path(d); root = base / 'source'; shutil.copytree(CORPUS / 'accumulation', root)
                    path = root / 'Учет/Движения.yaml'; meta = yaml.safe_load(path.read_text())
                    meta['ПараметрыЗаписи'] = [{'Имя': 'Bonus', 'Тип': 'Число'}, {'Имя': 'Deny', 'Тип': 'Булево'}]
                    path.write_text(yaml.safe_dump(meta, allow_unicode=True))
                    (root / 'Учет/Движения.НаборЗаписей.xbsl').write_text('''@Обработчик
метод ПередЗаписью(Замещать: Булево, Params: Движения.ПараметрыЗаписи)
    для Row из этот
        Row.Количество += Params.Bonus
    ;
;
@Обработчик
метод ПослеЗаписи(Замещать: Булево, Params: Движения.ПараметрыЗаписи)
    если Params.Deny
        выбросить новый ИсключениеНедопустимоеСостояние("accumulation-denied")
    ;
;
''')
                    path = root / 'Вход/Поток.xbsl'
                    path.write_text(path.read_text() + '''
метод Hooks(Ref: Учет::Акт.Ссылка, Deny: Булево): Объект
    знч Set = новый Книга.НаборЗаписей()
    Set.Фильтр.Установить(Регистратор = Ref)
    Set.Прочитать()
    Set.Записать(ПараметрыЗаписи = новый Учет::Движения.ПараметрыЗаписи(Bonus = 10, Deny = Deny))
    возврат Set.Записи
;
''')
                    for deny in [False, True]:
                        c = accumulation_check('Hooks', [REFS[0], deny], sql=sql); c['captureException'] = True
                        r = self.execute(root, c, base, sql)
                        write_json(EVIDENCE / f'accumulation-{sql}-{deny}.json', r)
                        expected_storage = [audit(copy.deepcopy(s)) for s in c['storage']['initialRegisters']]
                        if deny:
                            self.assertEqual(r['actual']['result']['exception']['message'], 'accumulation-denied')
                        else:
                            for row in expected_storage[0]['value']: row['Количество'] += 10
                            self.assertEqual(r['actual']['result']['result'], expected_storage[0]['value'])
                        self.assertEqual(normalized(r['actual'])['storage'], normalized({'storage': expected_storage})['storage'])
                        if sql: self.assertEqual(normalized({'storage': self.sql_audit})['storage'], normalized({'storage': expected_storage})['storage'])

    def test_pair_return_empty_nullable_and_equal_shaped_nominal_owners(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = source(base)
            (root / 'Other').mkdir(); shutil.copy(root / 'Data/Item.yaml', root / 'Other/Item.yaml')
            typ = 'ЧитаемаяКоллекция<КлючИЗначение<Data::Item.Ссылка|Other::Item.Ссылка, Коллекция<Data::Item.Ссылка?>?>>?'
            path = root / 'Entry/Flow.xbsl'
            path.write_text(path.read_text() + '\nметод Echo(Values: ' + typ + '): ' + typ + '\n    возврат Values\n;\n')
            ref = {'Идентификатор': '12345678-1234-4234-8234-123456789abc'}
            values = [{'Ключ': {'type': owner + '.Ссылка', 'value': ref}, 'Значение': value}
                      for owner, value in [('Data::Item', [ref, None]), ('Other::Item', None)]]
            c = check(); c['target']['method'] = 'Echo'; c['args'] = [values]; c.pop('captureException'); c['snapshotArgs'] = True
            c['runtimeProfile'] = '9.3'
            c['sequence'] = [{'method': 'Echo', 'args': [v]} for v in [values, [], None]]
            r = self.execute(root, c, base)
            write_json(EVIDENCE / 'pair-nominal-owners.json', r)
            self.assertEqual(r['actual']['result']['actions'], [{'result': v, 'args': [v]} for v in [values, [], None]])

    def test_each_hook_can_be_used_alone(self):
        for after_only in [False, True]:
            with TemporaryDirectory() as d:
                base = Path(d); root = source(base)
                path = root / 'Data/Prices.НаборЗаписей.xbsl'
                if after_only:
                    path.write_text('@Обработчик\nметод ПослеЗаписи(Замещать: Булево)\n    Service::Events.Stamp(2)\n;\n')
                else:
                    text = path.read_text(); path.write_text(text[:text.index('@Обработчик\nметод ПослеЗаписи')])
                c = check(); r = self.execute(root, c, base)
                write_json(EVIDENCE / f'single-hook-{after_only}.json', r)
                saved = next(v['value'] for v in r['actual']['storage'] if v['type'] == 'Data::Prices' and json.loads(v['id'])['Key'] == 'base')
                self.assertEqual(saved[0]['Price'], 3 if after_only else 13)
                log = next(v['value'] for v in r['actual']['storage'] if v['type'] == 'Data::Log')
                self.assertEqual(log['Phase'], 2 if after_only else 1)

    def test_pair_transport_in_original_dependency_module(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = source(base)
            typ = 'КлючИЗначение<Число, Строка?>'
            (root / 'Service/Pairs.xbsl').write_text('метод Echo(Value: ' + typ + '): ' + typ + '\n    возврат Value\n;\n')
            path = root / 'Entry/Flow.xbsl'
            path.write_text(path.read_text() + '\nметод Relay(Value: ' + typ + '): ' + typ + '\n    возврат Service::Pairs.Echo(Value)\n;\n')
            c = check(); c['target']['method'] = 'Relay'; c['args'] = [{'Ключ': 7, 'Значение': None}]; c.pop('captureException')
            r = self.execute(root, c, base)
            write_json(EVIDENCE / 'pair-source-dependency.json', r)
            self.assertEqual(r['actual']['result'], {'Ключ': 7, 'Значение': None})
