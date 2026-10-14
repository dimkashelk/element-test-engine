"""E03/E04 acceptance: original Script, deterministic domains and independent oracle."""
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.generative import (accepts, inputs, shrink_candidates,
    transformed, validate_domain, validate_project_domains, options_schema)
from element_test.integration import run_integration
from element_test.model import analyze
from element_test.runtime import decode_output, execute_engine, prepare_script, run_pure
from element_test.storage import PostgresSession
from element_test.yaml_io import InputError, InvalidTestError
from partial_filter_fixtures import CORPUS, REPO, check as filter_check, rows, D1, D2


def native(root, model, check, out, *, exact_numbers=False, **kwargs):
    script = prepare_script(root, model, check, out)
    p = subprocess.run([str(REPO/'bin/script-runtime'), '-c', '9.0', str(script)],
                       cwd=out, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise AssertionError(p.stdout + p.stderr)
    return {'status': 'EXECUTED', **decode_output(p.stdout, exact_numbers=exact_numbers)}


def register_fixture(root, renamed=False):
    shutil.copytree(CORPUS/('renamed' if renamed else 'ordinary'), root)
    ns, name = ('Запасы', 'История') if renamed else ('Ware', 'Ledger')
    meta = root/ns/(name+'.yaml')
    meta.write_text(meta.read_text()+'ПериодВходитВОсновнойФильтр: Истина\n')
    p = root/ns/'Flow.xbsl'
    p.write_text(p.read_text()+'''
метод Extended(Mode: Число): Объект
    знч R = новый REGISTER.НаборЗаписей()
    если Mode == 1
        R.Фильтр.Установить()
    иначе если Mode == 2
        R.Фильтр.Product = "X"
        R.Фильтр.Supplier = Неопределено
    иначе если Mode == 3
        R.Фильтр.Установить(Период = новый Момент("2026-10-01T12:00:00Z"))
        R.Фильтр.Product = "X"
    иначе если Mode == 4
        R.Фильтр.Установить(Supplier = Неопределено)
        R.Фильтр.Установить()
    иначе если Mode == 5
        знч F = R.Фильтр
        F.Product = "Y"
        F.Product = F.Product
    ;
    R.Прочитать()
    пер Sum = 0
    для Row из R
        Sum += Row.Price
    ;
    возврат {"count": R.Размер(), "sum": Sum}
;
метод Mutate(Mode: Число, Price: Число)
    знч R = новый REGISTER.НаборЗаписей()
    R.Фильтр.Установить(Период = новый Момент("2026-10-01T12:00:00Z"))
    если Mode == 3
        R.Фильтр.Установить()
        R.Записать()
        возврат
    ;
    если Mode != 2
        R.ДобавитьЗапись(Период = новый Момент("2026-10-01T12:00:00Z"), Product = "X", Supplier = "D", Channel = "local", Price = Price, Link = Неопределено)
    ;
    R.Записать(Mode != 1)
;
'''.replace('REGISTER', name))
    (root/ns/(name+'.НаборЗаписей.xbsl')).write_text('''@Обработчик
метод ПослеЗаписи(Замещать: Булево)
    для Row из этот
        если Row.Price < 0
            выбросить новый ИсключениеНедопустимоеСостояние("rollback-period-filter")
        ;
    ;
;
''')
    return ns, name


def generated_fixture(root, renamed=False):
    root.mkdir(); ns = 'Учет' if renamed else 'Data'
    (root/'Проект.yaml').write_text('Имя: Extended\nПоставщик: Tests\nРежимСовместимости: 9.0\n')
    (root/ns).mkdir()
    (root/ns/'Item.yaml').write_text('ВидЭлемента: Справочник\nИмя: Item\nОбластьВидимости: ВПроекте\nРеквизиты: []\n')
    (root/ns/'Status.yaml').write_text('ВидЭлемента: Перечисление\nИмя: Status\nОбластьВидимости: ВПроекте\nЭлементы:\n- {Имя: Open, ПоУмолчанию: Истина}\n- {Имя: Closed}\n')
    (root/ns/'Card.yaml').write_text('ВидЭлемента: Справочник\nИмя: Card\nОбластьВидимости: ВПроекте\nРеквизиты:\n- {Имя: Amount, Тип: Число}\n- {Имя: Day, Тип: Дата}\n- {Имя: Link, Тип: "Item.Ссылка?"}\n')
    return ns


def generated_check(typ, domain, relation=None, cases=5):
    return {'target': {'module': 'Main', 'namespace': 'Data', 'method': 'Echo'}, 'timeout': '20s',
        'generative': {'schemaVersion': 1, 'seed': 73, 'cases': cases, 'maxShrinks': 8,
            'domains': [dict(type=typ, **domain)],
            'relation': relation or {'op': 'equal', 'args': [{'result': True}, {'arg': 0}]}}}


class ExtendedDomainTest(unittest.TestCase):
    def test_bad_direct_field_cannot_create_an_empty_predicate(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; register_fixture(root)
            path = root/'Ware/Flow.xbsl'; original = path.read_text()
            for assignment in ['R.Фильтр.Unknown = "X"', 'R.Фильтр.Product = 123',
                               'R.Фильтр.Product += "X"', 'R.Фильтр = новый Ledger.ФильтрНабора()']:
                path.write_text(original+'\nметод Broken()\n    знч R = новый Ledger.НаборЗаписей()\n    '+assignment+'\n    R.Записать()\n;\n')
                with self.subTest(assignment=assignment), self.assertRaises(InputError):
                    plan_execution(root, analyze(root), filter_check('Broken', []))

    def test_seed_exact_decimals_temporal_bounds_replay_and_valid_reductions(self):
        domains = [dict(type='Число', min={'decimal': '-0.05'}, max={'decimal': '0.15'}, scale=2),
            dict(type='Дата', min='2024-02-28', max='2024-03-02'),
            dict(type='ДатаВремя', min='2026-12-31T23:59:58', max='2027-01-01T00:00:02'),
            dict(type='Момент', min='2026-10-14T00:00:00.123Z', max='2026-10-14T00:00:03.123Z'),
            dict(type='Число?', values=[None, 1.25, {'decimal': '0.123456789012345678901234'}])]
        c = generated_check('Число', {'min': 0, 'max': 1})['generative']; c['domains'] = domains; c['cases'] = 20
        values = inputs(c)
        self.assertEqual(values, inputs(copy.deepcopy(c)))
        self.assertEqual(values[0][0], {'decimal': '-0.05'})
        self.assertTrue(any(v[1] == '2024-02-29' for v in values))
        for v in values:
            self.assertTrue(all(accepts(d, x) for d, x in zip(domains, v)))
            for smaller in shrink_candidates(c, v):
                self.assertTrue(all(accepts(d, x) for d, x in zip(domains, smaller)))
        c['replayInputs'] = values; self.assertEqual(inputs(c), values)
        for domain in [dict(type='Число', min=0.01, max=1), dict(type='Число', min=0, max=1, scale=13),
                       dict(type='Число', values=[True]), dict(type='Число', values=[float('nan')]),
                       dict(type='Дата', min='2024-02-30', max='2024-03-01'),
                       dict(type='ДатаВремя', min='2024-01-01T00:00:00Z', max='2024-01-01T00:00:01'),
                       dict(type='Момент', min='2024-01-01T00:00:00Z', max='2024-01-01T00:00:00.1Z')]:
            with self.subTest(domain=domain), self.assertRaises(InvalidTestError): validate_domain(domain)

    def test_transform_decimal_dates_and_composite_without_aliasing(self):
        c = generated_check('Число', {'min': 0, 'max': 1, 'scale': 2})['generative']
        c['transform'] = [{'op': 'subtract', 'args': [{'literal': 1}, {'arg': 0}]}]
        self.assertEqual(transformed(c, [{'decimal': '0.12'}]), [{'decimal': '0.88'}])
        c['domains'] = [dict(type='Дата', min='2024-02-28', max='2024-03-02')]
        c['transform'] = [{'op': 'dateAddDays', 'args': [{'arg': 0}, {'literal': 1}]}]
        self.assertEqual(transformed(c, ['2024-02-28']), ['2024-02-29'])
        with self.assertRaises(InvalidTestError): transformed(c, ['2024-03-02'])
        c['domains'] = [dict(type='Data::Card.Объект', fields={'Amount': dict(type='Число', min=0, max=1, scale=2)})]
        c['transform'] = [{'op': 'set', 'args': [{'arg': 0}, {'literal': 'Amount'}, {'literal': {'decimal': '0.25'}}]}]
        args = [{'Amount': 1}]; self.assertEqual(transformed(c, args), [{'Amount': {'decimal': '0.25'}}]); self.assertEqual(args, [{'Amount': 1}])

    def test_project_validation_checks_undrawn_values_fields_and_nominal_owners(self):
        with TemporaryDirectory() as d:
            root = Path(d)/'source'; generated_fixture(root)
            c = ProjectTypes(analyze(root), 'Data', [])
            for domain in [dict(type='Data::Status', values=['Open', 'Missing']),
                           dict(type='Other::Item.Ссылка', values=[{'Идентификатор': '12345678-1234-4234-8234-123456789abc'}]),
                           dict(type='Data::Card.Объект', fields={'Amount': dict(type='Строка', values=['wrong'])})]:
                with self.subTest(domain=domain), self.assertRaises(InputError): validate_project_domains([domain], c)

    def test_schema(self):
        schema = options_schema()
        self.assertEqual(schema, json.loads((REPO/'docs/generative-options-v1.schema.json').read_text()))


class ExtendedScriptTest(unittest.TestCase):
    def test_equal_reference_ids_preserve_different_nominal_owners(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; generated_fixture(root)
            (root/'Other').mkdir(); shutil.copy(root/'Data/Item.yaml', root/'Other/Item.yaml')
            (root/'Data/Main.xbsl').write_text('метод Echo(A: Data::Item.Ссылка, B: Other::Item.Ссылка): Булево\n    возврат A.ПолучитьТип().ВСтроку() == B.ПолучитьТип().ВСтроку()\n;\n')
            value = {'Идентификатор':'12345678-1234-4234-8234-123456789abc'}
            c = generated_check('Data::Item.Ссылка', {'values':[value]}, cases=1)
            c['generative']['domains'].append(dict(type='Other::Item.Ссылка', values=[value]))
            c['generative']['relation'] = {'op':'equal','args':[{'result':True},{'literal':False}]}
            with patch('element_test.runtime.run_pure', side_effect=native):
                r = run_pure(root, analyze(root), c, base)
            self.assertEqual(r['status'],'EXECUTED',r); self.assertIsNone(r['generative']['counterexample'],r)
            self.assertIs(r['actual']['cases'][0]['result'], False)

    def test_source_defect_decimal_boundary_and_typed_temporal_metamorphism(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; generated_fixture(root)
            (root/'Data/Main.xbsl').write_text('''метод Echo(Value: Число): Число
    возврат Value > 0.50 ? Value + 0.01 : Value
;
''')
            c = generated_check('Число', {'min': 0, 'max': 1, 'scale': 2}, cases=3)
            c['generative']['maxShrinks'] = 32
            with patch('element_test.runtime.run_pure', side_effect=native):
                r = run_pure(root, analyze(root), c, base)
            self.assertEqual(r['generative']['counterexample']['minimal']['inputs'], [{'decimal': '0.51'}], r)
            self.assertEqual(r['generative']['counterexample']['minimal']['result'], {'decimal': '0.52'})
            with patch('element_test.runtime.run_pure', side_effect=native):
                replay = run_pure(root, analyze(root), r['generative']['counterexample']['replayCheck'], base)
            self.assertEqual(replay['actual'], r['actual'])
            (root/'Data/Main.xbsl').write_text('метод Echo(Value: Момент, Seconds: Число): Момент\n    возврат Value\n;\n')
            c = generated_check('Момент', {'min': '2026-10-14T00:00:00.123Z', 'max': '2026-10-14T00:00:02.123Z'}, cases=3)
            c['generative']['domains'].append(dict(type='Число', min=0, max=2))
            shift = {'op':'dateAddSeconds','args':[{'literal':'2026-10-14T00:00:00.123Z'},{'arg':1}]}
            c['generative']['transform'] = [shift, {'arg':1}]
            c['generative']['relation'] = {'op':'equal','args':[{'op':'instant','args':[{'pairedResult':True}]},{'op':'instant','args':[shift]}]}
            with patch('element_test.runtime.run_pure', side_effect=native):
                r = run_pure(root, analyze(root), c, base)
            self.assertEqual(r['status'],'EXECUTED',r); self.assertIsNone(r['generative']['counterexample'],r)

    def test_register_filters_read_null_omission_period_empty_and_alias(self):
        for renamed in [False, True]:
            with TemporaryDirectory() as d:
                base = Path(d); root = base/'source'; ns, name = register_fixture(root, renamed)
                c = filter_check('Extended', [1], namespace=ns, name=name)
                expected = [(4,100), (1,40), (3,70), (4,100), (1,30)]
                for mode, (count, total) in enumerate(expected, 1):
                    c['args'] = [mode]
                    r = native(root, analyze(root), c, base)
                    self.assertEqual(r['actual']['result'], {'count': count, 'sum': total})
                c['args'] = [0]; c['captureException'] = True
                r = native(root, analyze(root), c, base)
                self.assertIn('Не установлен фильтр', r['actual']['result']['exception']['message'])
                c = filter_check('Extended', [3], namespace=ns, name=name)
                plan = plan_execution(root, analyze(root), c)
                self.assertTrue(any(r.get('filterMask') == ['Период'] for r in plan.record_sets))
                c['storage']['initialRegisters'] = [{'type': ns+'::'+name, 'filter': {}, 'rows': rows()}]
                self.assertEqual(native(root, analyze(root), c, base)['actual']['result']['count'], 3)

    def test_register_replace_append_clear_and_rollback_keep_full_history(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; ns, name = register_fixture(root)
            for mode, price in [(0,99),(1,99),(2,99),(3,99),(0,-99)]:
                c = filter_check('Mutate', [mode,price]); c['captureException'] = True
                # History outside period selection must survive replacement.
                c['storage']['initialRegisters'][0]['rows'].append({**rows()[0], 'Период': D2, 'Price': 7})
                r = native(root, analyze(root), c, base)
                saved = [row for group in r['actual']['storage'] for row in group['value']]
                original = rows()+[{**rows()[0], 'Период': D2, 'Price': 7}]
                replacement = dict(Период=D1, Product='X', Supplier='D', Channel='local', Price=99, Link=None)
                expected = original if price < 0 else [] if mode == 3 else original+[replacement] if mode == 1 else [original[-1]]+([replacement] if mode == 0 else [])
                self.assertEqual(sorted(saved,key=lambda r: json.dumps(r,sort_keys=True)), sorted(expected,key=lambda r: json.dumps(r,sort_keys=True)), r)
                self.assertEqual(r['actual']['result']['exception'] is not None, price < 0)

    def test_generated_source_exact_decimals_dates_enum_references_objects_and_wrong_relation(self):
        for renamed in [False, True]:
            with TemporaryDirectory() as d:
                base = Path(d); root = base/'source'; ns = generated_fixture(root, renamed)
                ref = {'Идентификатор': '12345678-1234-4234-8234-123456789abc'}
                domains = [('Число', {'values': [0.1, {'decimal': '0.123456789012345678901234'}]}),
                    ('Дата', {'min': '2024-02-28', 'max': '2024-03-02'}),
                    ('ДатаВремя', {'min': '2026-12-31T23:59:59', 'max': '2027-01-01T00:00:01'}),
                    ('Момент', {'min': '2026-10-14T00:00:00Z', 'max': '2026-10-14T00:00:02Z'}),
                    (ns+'::Status', {'values': ['Open','Closed']}),
                    (ns+'::Item.Ссылка?', {'values': [None,ref]}),
                    (ns+'::Card.Объект', {'fields': {
                        'Ссылка': dict(type=ns+'::Card.Ссылка', values=[ref]),
                        'Amount': dict(type='Число', min=0, max=1, scale=2),
                        'Day': dict(type='Дата', min='2024-02-28', max='2024-03-02'),
                        'Link': dict(type=ns+'::Item.Ссылка?', values=[None,ref])}})]
                for typ, domain in domains:
                    (root/ns/'Main.xbsl').write_text('метод Echo(Value: '+typ+'): '+typ+'\n    возврат Value\n;\n')
                    c = generated_check(typ, domain, cases=3); c['target']['namespace'] = ns
                    with patch('element_test.runtime.run_pure', side_effect=native):
                        result = run_pure(root, analyze(root), c, base)
                    self.assertEqual(result['status'], 'EXECUTED', result)
                    self.assertIsNone(result['generative']['counterexample'], result)
                    self.assertEqual(result['generative']['executedCases'], 3)
                    c['generative']['relation'] = {'literal': False}
                    with patch('element_test.runtime.run_pure', side_effect=native):
                        wrong = run_pure(root, analyze(root), c, base)
                    self.assertIsNotNone(wrong['generative']['counterexample'], wrong)
                    replay = wrong['generative']['counterexample']['replayCheck']
                    with patch('element_test.runtime.run_pure', side_effect=native):
                        again = run_pure(root, analyze(root), replay, base)
                    self.assertEqual(wrong['actual'], again['actual'])

    def test_independent_sbsl_decimal_arithmetic_date_operations_and_incomplete_execution(self):
        with TemporaryDirectory() as d:
            base = Path(d); model = base/'model.json'; model.write_text('{}'); assignment = base/'assignment.json'
            relations = [
                {'op':'equal','args':[{'op':'add','args':[{'literal':{'decimal':'0.1'}},{'literal':{'decimal':'0.2'}}]},{'literal':{'decimal':'0.3'}}]},
                {'op':'less','args':[{'op':'date','args':[{'literal':'2024-02-29'}]},{'op':'date','args':[{'literal':'2024-03-01'}]}]},
                {'op':'equal','args':[{'op':'dateAddDays','args':[{'literal':'2024-02-28'},{'literal':1}]},{'literal':'2024-02-29'}]},
                {'op':'equal','args':[{'op':'dateAddSeconds','args':[{'literal':'2026-12-31T23:59:59Z'},{'literal':1}]},{'literal':'2027-01-01T00:00:00Z'}]}]
            checks = [dict(id=str(i), type='runtime', points=1, generative={**generated_check('Число',{'min':0,'max':1})['generative'],'relation':r}, execution={'status':'EXECUTED','actual':{'cases':[{'inputs':[0],'result':0}]}}) for i,r in enumerate(relations)]
            assignment.write_text(json.dumps({'name':'independent','checks':checks}))
            result = execute_engine('test', model, assignment, base)
            self.assertEqual([c['status'] for c in result['checks']], ['PASS']*len(checks), result)
            root = base/'source'; generated_fixture(root)
            (root/'Data/Main.xbsl').write_text('метод Echo(Value: Число): Число\n    возврат Value\n;\n')
            with patch('element_test.runtime.run_pure', return_value={'status':'ERROR','reasonCode':'execution_error'}):
                result = run_pure(root, analyze(root), generated_check('Число',{'min':0,'max':1}), base)
            self.assertEqual(result['status'],'ERROR'); self.assertEqual(result['generative']['executedCases'],0)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Docker/PostgreSQL opt-in')
class ExtendedBackendTest(unittest.TestCase):
    def test_generated_docker_exact_number_and_date_transform(self):
        with TemporaryDirectory() as d:
            base = Path(d); root = base/'source'; generated_fixture(root)
            for typ, domain in [('Число', {'min':0,'max':1,'scale':2}),
                                ('Дата', {'min':'2024-02-28','max':'2024-03-01'})]:
                (root/'Data/Main.xbsl').write_text('метод Echo(Value: '+typ+'): '+typ+'\n    возврат Value\n;\n')
                c = generated_check(typ,domain,cases=3)
                if typ == 'Число':
                    c['generative']['transform'] = [{'op':'subtract','args':[{'literal':1},{'arg':0}]}]
                    c['generative']['relation'] = {'op':'equal','args':[{'op':'add','args':[{'result':True},{'pairedResult':True}]},{'literal':1}]}
                r = run_pure(root,analyze(root),c,base)
                self.assertEqual(r['status'],'EXECUTED',r); self.assertIsNone(r['generative']['counterexample'],r)

    def test_memory_postgres_predicate_mutation_rollback_and_independent_sql(self):
        with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}):
            for sql in [False, True]:
                with TemporaryDirectory() as d:
                    base = Path(d); root = base/'source'; register_fixture(root)
                    for mode, price in [(0,99),(1,99),(2,99),(3,99),(0,-99)]:
                        c = filter_check('Mutate',[mode,price],sql=sql); c['captureException'] = True
                        if sql:
                            audits = []; original = PostgresSession.audit
                            def audit(session, database, docker):
                                value = original(session,database,docker); audits.append(copy.deepcopy(value)); return value
                            with patch.object(PostgresSession,'audit',audit):
                                r = run_integration(c,analyze(root),base,root=root,enabled=True)
                        else: r = run_pure(root,analyze(root),c,base)
                        self.assertEqual(r['status'],'EXECUTED',r)
                        replacement = dict(Период=D1, Product='X', Supplier='D', Channel='local', Price=99, Link=None)
                        expected = rows() if price < 0 else rows()+[replacement] if mode == 1 else [replacement] if mode == 0 else []
                        observed = audits[-1] if sql else r['actual']['storage']
                        saved = [row for group in observed for row in group['value']]
                        self.assertEqual(sorted(saved,key=lambda r: json.dumps(r,sort_keys=True)),sorted(expected,key=lambda r: json.dumps(r,sort_keys=True)),r)
