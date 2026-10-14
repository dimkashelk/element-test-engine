"""Independent acceptance of detailed totals, subordinated RS and live gates."""
import copy
import json
import os
import secrets
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.execution_plan import plan_execution
from element_test.integration import run_integration
from element_test.model import analyze
from element_test.runtime import prepare_script, decode_output
from element_test.storage import PostgresSession
from element_test.yaml_io import InputError
from test_report_navigation_panel import write, fixture as surface_fixture, config as surface_config

REPO = Path(__file__).resolve().parents[1]
START, END = '2026-10-01T00:00:00', '2026-10-02T00:00:00'
MID = '2026-10-01T12:00:00'


def execute(root, check, out):
    script = prepare_script(root, analyze(root), check, out)
    version = 'current' if analyze(root)['compatibilityVersion'] == '10.0' else '9.0'
    p = subprocess.run([str(REPO/'bin/script-runtime'), '-c', version, str(script)],
                       cwd=out, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise AssertionError(p.stdout + p.stderr)
    return decode_output(p.stdout)


def totals_fixture(root, ns='Data', union=False):
    write(root, 'Проект.yaml', {'Имя':'Detailed', 'РежимСовместимости':'9.0'})
    for name in ('Act', 'Other'):
        write(root, ns+'/'+name+'.yaml', {'ВидЭлемента':'Документ', 'Имя':name, 'ОбластьВидимости':'ВПроекте'})
    write(root, ns+'/Ledger.yaml', {'ВидЭлемента':'РегистрНакопления', 'Имя':'Ledger', 'ОбластьВидимости':'ВПроекте',
        'Измерения':[{'Имя':'Key', 'Тип':'Строка'}], 'Ресурсы':[{'Имя':'Total','Тип':'Число'}],
        'Реквизиты':[{'Имя':'Регистратор','Тип':'Act.Ссылка|Other.Ссылка' if union else 'Act.Ссылка'}]})
    for mode in ('Запись', 'Регистратор', 'Авто'):
        for complement in ('Записи', 'ЗаписиИГраницыПериода'):
            fields = ['Key', 'ПериодЗаписи' if mode == 'Авто' else 'Период', 'Регистратор']
            if mode != 'Регистратор': fields += ['Индекс', 'НомерСтроки']
            fields += ['TotalНачальныйОстаток', 'TotalОборот', 'TotalПриход', 'TotalРасход', 'TotalКонечныйОстаток']
            query = 'ВЫБРАТЬ '+', '.join(fields)+' ИЗ Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.'+mode+', СпособДополненияПериодовРегистраНакопления.'+complement+')'
            path = root/ns/'Flow.xbsl'
            with path.open('a') as f:
                f.write('метод '+mode+complement+'(S: ДатаВремя, E: ДатаВремя): Объект\n    возврат Запрос{'+query+'}.Выполнить()\n;\n')
    with (root/ns/'Flow.xbsl').open('a') as f:
        f.write('''метод Fresh(S: ДатаВремя, E: ДатаВремя, Failure: Булево): Объект
    знч Query = Запрос{ВЫБРАТЬ Key, Период, TotalОборот, TotalКонечныйОстаток ИЗ Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Запись, СпособДополненияПериодовРегистраНакопления.Записи)}
    знч Before = Query.Выполнить()
    пер During: Объект = новый Массив<Объект>()
    пер Rejected = Ложь
    попытка
        исп Транзакции.Начать()
        знч R = новый Ledger.НаборЗаписей()
        R.Фильтр.Установить(новый Act.Ссылка(Идентификатор = "a"))
        R.ДобавитьЗапись(Период = новый ДатаВремя("2026-10-01T12:00:00"), ВидЗаписи = ВидЗаписиРегистраНакопления.Приход, Key = "x", Total = 2)
        R.Записать()
        During = Query.Выполнить()
        если Failure
            выбросить новый ИсключениеВалидации("rollback")
        ;
    поймать Error: ИсключениеВалидации
        Rejected = Error.Описание == "rollback"
    ;
    возврат {"before": Before, "during": During, "after": Query.Выполнить(), "rejected": Rejected}
;
метод All(): Объект
    возврат Запрос{ВЫБРАТЬ Key, Период, TotalОборот ИЗ Ledger.ОстаткиИОбороты(,, ПериодичностьИтоговРегистраНакопления.Запись)}.Выполнить()
;
метод Count(S: ДатаВремя, E: ДатаВремя): Число
    возврат Запрос{ВЫБРАТЬ TotalОборот ИЗ Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Запись, СпособДополненияПериодовРегистраНакопления.Записи)}.Выполнить().Размер()
;
''')
    return root


def totals_check(ns='Data', mode='Запись', complement='ЗаписиИГраницыПериода', union=False):
    def seed(owner, identifier, history):
        ref = {'Идентификатор':identifier}
        registrar = {'type':ns+'::'+owner+'.Ссылка', 'value':ref} if union else ref
        return {'type':ns+'::Ledger', 'filter':{'Регистратор':registrar}, 'rows':[
            {'Регистратор':registrar, 'Активность':active, 'Период':date, 'ВидЗаписи':kind, 'Key':key, 'Total':amount}
            for date, kind, key, amount, active in history]}
    return {'target':{'module':'Flow', 'namespace':ns, 'method':mode+complement}, 'args':[START, END],
        'runtimeProfile':'9.3', 'storage':{'registers':[ns+'::Ledger'], 'initialRegisters':[
            seed('Act','a', [('2026-09-30T12:00:00','Приход','x',10,True),
                             (MID,'Приход','x',3,True), (MID,'Расход','x',2,True),
                             (MID,'Приход','x',100,False)]),
            seed('Other' if union else 'Act','a' if union else 'b', [(MID,'Расход','x',4,True),
                             (END,'Приход','x',7,True)]),
            seed('Act','old', [('2026-09-30T12:00:00','Расход','quiet',2,True)])]}}


def info_fixture(root, ns='Data'):
    write(root, 'Проект.yaml', {'Имя':'Subordinated', 'РежимСовместимости':'10.0'})
    for name in ('Act','Other'):
        write(root, ns+'/'+name+'.yaml', {'ВидЭлемента':'Документ', 'Имя':name, 'ОбластьВидимости':'ВПроекте'})
    write(root, ns+'/History.yaml', {'ВидЭлемента':'РегистрСведений', 'Имя':'History', 'ОбластьВидимости':'ВПроекте',
        'РежимЗаписи':'ПодчинениеРегистратору', 'Периодичность':'День',
        'Измерения':[{'Имя':'Key','Тип':'Строка'}], 'Ресурсы':[{'Имя':'Amount','Тип':'Число'}],
        'Реквизиты':[{'Имя':'Регистратор','Тип':'Act.Ссылка|Other.Ссылка|?'}]})
    (root/ns/'Flow.xbsl').write_text('''метод Run(Mode: Число): Объект
    знч R = новый History.НаборЗаписей()
    R.Фильтр.Установить(Регистратор = новый Act.Ссылка(Идентификатор = "same"))
    R.Прочитать()
    пер Before = 0
    для Row из R
        Before += Row.Amount
    ;
    R.Очистить()
    если Mode != 2
        R.ДобавитьЗапись(Период = новый Дата("2026-10-01"), Key = Mode == 3 ? "other" : "first", Amount = 9)
    ;
    если Mode == 4
        R.УстановитьАктивность(Ложь)
    ;
    R.Записать()
    если Mode == 5
        R.Очистить()
        R.ДобавитьЗапись(Период = новый Дата("2026-10-02"), Key = "first", Amount = 3)
        R.Записать(Замещать = Ложь)
    ;
    R.Прочитать()
    пер After = 0
    для Row из R
        After += Row.Amount
    ;
    возврат {"before": Before, "after": After, "count": R.Размер()}
;
метод Slice(): Объект
    возврат Запрос{ВЫБРАТЬ Key, Amount, Регистратор ИЗ History.СрезПоследних()}.Выполнить()
;
метод Raw(): Объект
    возврат Запрос{ВЫБРАТЬ Key, Amount, Регистратор, Активность ИЗ History}.Выполнить()
;
метод NullRegistrar(): Число
    знч R = новый History.НаборЗаписей()
    R.Фильтр.Установить(Регистратор = Неопределено)
    R.ДобавитьЗапись(Период = новый Дата("2026-10-01"), Key = "null", Amount = 7)
    R.Записать()
    R.Прочитать()
    возврат R.Размер()
;
''')


def info_check(ns='Data', mode=1):
    def seed(owner, key, amount):
        ref = {'type':ns+'::'+owner+'.Ссылка', 'value':{'Идентификатор':'same'}}
        return {'type':ns+'::History', 'filter':{'Регистратор':ref}, 'rows':[
            {'Регистратор':ref, 'Активность':True, 'Период':'2026-10-01', 'Key':key, 'Amount':amount}]}
    return {'target':{'module':'Flow', 'namespace':ns, 'method':'Run'}, 'args':[mode], 'captureException':True,
        'storage':{'registers':[ns+'::History'], 'transaction':True, 'initialRegisters':[
            seed('Act','first',2), seed('Other','other',5)]}}


class DetailedTotalsTest(unittest.TestCase):
    def test_same_query_rereads_replacement_and_rollback_without_changing_old_snapshots(self):
        for union in (False,True):
            with self.subTest(union=union), TemporaryDirectory() as d:
                base=Path(d);root=base/'source';totals_fixture(root,union=union)
                for failure in (False,True):
                    c=totals_check(union=union);c['target']['method']='Fresh';c['args'].append(failure)
                    r=execute(root,c,base)['actual']['result']
                    for stage, total, closing in (('before',4,14),('during',5,5),('after',4 if failure else 5,14 if failure else 5)):
                        rows=r[stage]
                        self.assertEqual(sum(v['TotalОборот'] for v in rows),total)
                        self.assertEqual(next(v['TotalКонечныйОстаток'] for v in rows if v['Период']==END),closing)
                    self.assertEqual(len(r['before']),4)
                    self.assertEqual(len(r['during']),3)
                    self.assertEqual(r['rejected'],failure)

    def test_records_registrars_auto_boundaries_and_nominal_unions(self):
        for ns, union in (('Data', False), ('Учет', True)):
            with self.subTest(ns=ns), TemporaryDirectory() as d:
                base = Path(d); root = base/'source'; totals_fixture(root, ns, union)
                for mode in ('Запись','Регистратор','Авто'):
                    for complement in ('Записи','ЗаписиИГраницыПериода'):
                        c = totals_check(ns, mode, complement, union)
                        r = execute(root, c, base); rows = r['actual']['result']
                        x = [v for v in rows if v['Key'] == 'x']
                        movement = [v for v in x if v['Регистратор'] is not None]
                        self.assertEqual(sorted(v['TotalОборот'] for v in movement), [-4,1,7] if mode=='Регистратор' else [-4,-2,3,7])
                        self.assertEqual(sum(v['TotalОборот'] for v in x), 4)
                        self.assertEqual(sorted((v['TotalНачальныйОстаток'],v['TotalКонечныйОстаток']) for v in movement),
                                         [(7,14),(10,11),(11,7)] if mode=='Регистратор' else [(7,14),(10,13),(11,7),(13,11)])
                        boundaries = [v for v in x if v['Регистратор'] is None]
                        if complement == 'Записи':
                            self.assertEqual(boundaries, [])
                            self.assertFalse(any(v['Key']=='quiet' for v in rows))
                        else:
                            self.assertEqual(sorted(v['TotalНачальныйОстаток'] for v in boundaries), [10,14])
                            self.assertEqual(len([v for v in rows if v['Key']=='quiet']), 2)
                            for v in boundaries:
                                if mode != 'Регистратор': self.assertIsNone(v['Индекс']); self.assertIsNone(v['НомерСтроки'])
                        if union:
                            self.assertEqual({v['Регистратор']['type'] for v in movement}, {ns+'::Act.Ссылка',ns+'::Other.Ссылка'})

    def test_reversed_empty_and_unbounded_intervals(self):
        with TemporaryDirectory() as d:
            base=Path(d); root=base/'source'; totals_fixture(root)
            c=totals_check(); c['args']=[END,START]
            self.assertEqual(execute(root,c,base)['actual']['result'],[])
            c=totals_check();c['target']['method']='All';c['args']=[]
            result=execute(root,c,base)['actual']['result']
            self.assertEqual(len(result),6)
            self.assertEqual(sum(row['TotalОборот'] for row in result),12)
            c['args']=[START,END]; c['storage']['initialRegisters']=[]
            c['target']['method']='ЗаписьЗаписиИГраницыПериода'
            self.assertEqual(execute(root,c,base)['actual']['result'],[])


class SubordinatedInformationTest(unittest.TestCase):
    def test_imported_and_qualified_same_named_document_owners(self):
        import yaml
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';info_fixture(root)
            for old,ns in (('Act','Left'),('Other','Right')):
                (root/'Data'/(old+'.yaml')).unlink()
                write(root, ns+'/Act.yaml', {'ВидЭлемента':'Документ','Имя':'Act','ОбластьВидимости':'ВПроекте'})
            meta=yaml.safe_load((root/'Data/History.yaml').read_text())
            meta['Импорт']=['Left'];meta['Реквизиты'][0]['Тип']='Act.Ссылка|Right::Act.Ссылка|?'
            write(root,'Data/History.yaml',meta)
            module=root/'Data/Flow.xbsl';module.write_text('импорт Left\n'+module.read_text())
            c=info_check()
            for seed,owner in zip(c['storage']['initialRegisters'],('Left','Right')):
                seed['filter']['Регистратор']['type']=owner+'::Act.Ссылка'
            result=execute(root,c,base)['actual']
            self.assertEqual(result['result']['result'],{'before':2,'after':9,'count':1})
            rows=[row for bucket in result['storage'] for row in bucket['value']]
            self.assertEqual({row['Регистратор']['type'] for row in rows},{'Left::Act.Ссылка','Right::Act.Ссылка'})

    def test_append_null_registrar_and_inactive_slices(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';info_fixture(root)
            self.assertEqual(execute(root,info_check(mode=5),base)['actual']['result']['result'],{'before':2,'after':12,'count':2})
            c=info_check();c['target']['method']='NullRegistrar';c['args']=[]
            self.assertEqual(execute(root,c,base)['actual']['result']['result'],1)
            c=info_check();c['target']['method']='Slice';c['args']=[]
            c['storage']['initialRegisters'][0]['rows'][0]['Активность']=False
            rows=execute(root,c,base)['actual']['result']['result']
            self.assertEqual([(v['Key'],v['Amount']) for v in rows],[('other',5)])
            c['target']['method']='Raw'
            rows=execute(root,c,base)['actual']['result']['result']
            self.assertEqual(sorted((v['Key'],v['Amount'],v['Активность']) for v in rows),[('first',2,False),('other',5,True)])
            self.assertEqual({v['Регистратор']['type'] for v in rows},{'Data::Act.Ссылка','Data::Other.Ссылка'})

    def test_replace_read_cancel_inactive_and_atomic_conflict(self):
        for ns in ('Data','Учет'):
            with self.subTest(ns=ns), TemporaryDirectory() as d:
                base=Path(d); root=base/'source'; info_fixture(root,ns)
                for mode in (1,2,3,4):
                    r=execute(root,info_check(ns,mode),base)
                    if mode==3:
                        self.assertIsNotNone(r['actual']['result']['exception'])
                        values=[v['value'][0]['Amount'] for v in r['actual']['storage'] if v['value']]
                        self.assertEqual(sorted(values),[2,5])
                    else:
                        self.assertEqual(r['actual']['result']['result'], {'before':2,'after':0 if mode==2 else 9,'count':0 if mode==2 else 1})
                        values=[row for v in r['actual']['storage'] for row in v['value']]
                        self.assertIn(5,[row['Amount'] for row in values])
                        if mode==4: self.assertFalse(next(row for row in values if row['Amount']==9)['Активность'])

    def test_version_nominal_type_missing_filter_and_unknown_filter(self):
        with TemporaryDirectory() as d:
            base=Path(d); root=base/'source'; info_fixture(root)
            (root/'Проект.yaml').write_text('Имя: Old\nРежимСовместимости: 9.0\n')
            with self.assertRaisesRegex(InputError,'10.0'): plan_execution(root,analyze(root),info_check())
            info_fixture(root)
            source=root/'Data/Flow.xbsl'; original=source.read_text()
            for call in ('R.Фильтр.Установить()', 'R.Фильтр.Установить(Key = "first")'):
                source.write_text(original.replace('R.Фильтр.Установить(Регистратор = новый Act.Ссылка(Идентификатор = "same"))',call))
                with self.assertRaises(InputError): prepare_script(root,analyze(root),info_check(),base)


class LiveNavigationTest(unittest.TestCase):
    def test_compound_fields_constants_recalculation_and_independent_snapshots(self):
        for ns in ('Data','Учет'):
            with self.subTest(ns=ns), TemporaryDirectory() as d:
                base=Path(d); root=base/'source'; surface_fixture(root,ns)
                write(root, ns+'/State.yaml', {'ВидЭлемента':'Структура','Имя':'State','ОбластьВидимости':'ВПроекте',
                    'Поля':[{'Имя':'Amount','Тип':'Число'}, {'Имя':'Ready','Тип':'Булево'}]})
                write(root, ns+'/Settings.yaml', {'ВидЭлемента':'НаборКонстант','Имя':'Settings',
                    'Константы':[{'Имя':'Limit','Тип':'Число'}]})
                menu=root/ns/'Menu.yaml'
                import yaml
                value=yaml.safe_load(menu.read_text())
                value['Элементы'][0]['Видимость']='=Show и State.Ready'
                value['Элементы'][0]['Элементы'][0]['Доступность']='=State.Amount >= Settings.Получить().Limit и не Locked'
                write(root, ns+'/Menu.yaml',value)
                c=surface_config('navigation','Menu',ns)
                c['navigationContext']={'Show':True, 'Locked':False, 'State':{'type':ns+'::State','value':{'Amount':2,'Ready':True}}}
                c['constants']={ns+'::Settings':{'Limit':3}}
                opening={'operation':'open','selector':{'caption':'Entries'}}
                c['navigationActions']=[{'operation':'resolve'},opening, {'operation':'set','path':'State.Amount','value':4},
                    opening, {'operation':'setConstant','owner':ns+'::Settings','path':'Limit','value':5}, opening,
                    {'operation':'set','path':'Show','value':False},opening, {'operation':'resolve'}]
                r=execute(root,c,base)['actual']['actions']
                self.assertEqual([a['outcome'] for a in r if a['operation']=='open'],['blocked','opened-model','blocked','blocked'])
                self.assertTrue(r[0]['routes'][0]['visible']); self.assertFalse(r[0]['routes'][0]['available'])
                self.assertFalse(r[-1]['routes'][0]['visible']); self.assertFalse(r[-1]['routes'][0]['available'])
                self.assertEqual(r[2]['context']['State']['Amount'],4)

    def test_unknown_api_and_nonboolean_hidden_descendants_remain_unsupported(self):
        import yaml
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';surface_fixture(root)
            value=yaml.safe_load((root/'Data/Menu.yaml').read_text())
            value['Элементы'][0]['Видимость']=False
            for expr in ('=Unknown.Получить().Flag', '=1 + 2', '=Show.НеведомыйМетод()'):
                value['Элементы'][0]['Элементы'][0]['Доступность']=expr
                write(root,'Data/Menu.yaml',value)
                with self.assertRaises(InputError): plan_execution(root,analyze(root),surface_config('navigation','Menu'))


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','PostgreSQL opt-in')
class SqlAcceptance(unittest.TestCase):
    def sql(self,root,c,out):
        c=copy.deepcopy(c);c['storage']['backend']='postgres'
        c['integration']={'backend':'postgres','operation':'metadata-storage'}
        values=[]; original=PostgresSession.audit
        def audit(session,database,docker):
            r=original(session,database,docker);values.append(copy.deepcopy(r));return r
        with patch.object(PostgresSession,'audit',audit), patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            r=run_integration(c,analyze(root),out,root=root,enabled=True)
        self.assertEqual(r['status'],'EXECUTED',r)
        self.assertTrue(r['integration']['cleanup'],r)
        self.assertTrue(values)
        return r,values[-1]

    def test_detailed_totals_postgres_and_independent_select(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';totals_fixture(root,union=True)
            r,audit=self.sql(root,totals_check(union=True),base)
            self.assertEqual(sum(row['TotalОборот'] for row in r['actual']['result']),4)
            self.assertEqual(len(audit),3)

    def test_detailed_replacement_and_rollback_sql_audit(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';totals_fixture(root,union=True)
            for failure in (False,True):
                c=totals_check(union=True);c['target']['method']='Fresh';c['args'].append(failure)
                r,audit=self.sql(root,c,base)
                rows=[row for b in audit for row in b['value']]
                # Raw SELECT includes the inactive fixture row; the query excludes it.
                self.assertEqual(sorted(row['Total'] for row in rows), [2,2,3,4,7,10,100] if failure else [2,2,4,7])
                self.assertEqual(sum(row['TotalОборот'] for row in r['actual']['result']['after']),4 if failure else 5)

    def test_subordinated_conflict_rollback_and_commit_sql_audit(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';info_fixture(root)
            for mode in (1,2,3):
                r,audit=self.sql(root,info_check(mode=mode),base)
                rows=[row for bucket in audit for row in bucket['value']]
                self.assertEqual(sorted(row['Amount'] for row in rows), [5,9] if mode==1 else [5] if mode==2 else [2,5])


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Public Docker opt-in')
class PublicAcceptance(unittest.TestCase):
    def test_public_pass_fail_unsupported_and_two_isolated_submissions(self):
        from element_test.bridge import run_test
        from element_test.batch import run_batch
        from record_set_fixtures import audit
        import shutil
        with TemporaryDirectory() as d:
            base=Path(d)
            for kind in ('totals','information','navigation'):
                root=base/kind
                if kind=='totals':
                    totals_fixture(root);c=totals_check();c['target']['method']='Count'
                    expected={'result':4,'storage':[audit(s) for s in c['storage']['initialRegisters']]}
                elif kind=='information':
                    info_fixture(root);c=info_check();c.pop('captureException')
                    seeds=copy.deepcopy(c['storage']['initialRegisters'])
                    seeds[0]['rows'][0]['Amount']=9
                    for seed in seeds: seed['filter']={'Key':seed['rows'][0]['Key']}
                    expected={'result':{'before':2,'after':9,'count':1},'storage':[audit(s) for s in seeds]}
                else:
                    surface_fixture(root);c=surface_config('navigation','Menu');c['navigationContext']['Show']=False
                    opening={'operation':'open','selector':{'caption':'Entries'}}
                    c['navigationActions']=[opening,{'operation':'set','path':'Show','value':True},opening]
                    route=plan_execution(root,analyze(root),c).navigation['routes'][0]['id']
                    expected={'actions':[
                        {'operation':'open','outcome':'blocked','routeId':route,'target':None},
                        {'operation':'set','context':{'Show':True}},
                        {'operation':'open','outcome':'opened-model','routeId':route,'target':{
                            'owner':'Data::Entries','surface':'АвтоматическаяФорма','sourceFile':'Data/Entries.yaml',
                            'kind':'Отчет','rendering':'external-native-ui'}}]}
                good={**c,'id':'good','type':'runtime','points':2,'comparison':'record-sets-unordered','expected':expected}
                if kind=='navigation': good.pop('comparison')
                bad=copy.deepcopy(good);bad['id']='wrong'
                if kind=='totals': bad['expected']['result']=99
                elif kind=='information': bad['expected']['result']['after']=99
                else: bad['expected']['actions'][0]['outcome']='opened-model'
                unavailable=copy.deepcopy(good);unavailable['id']='unavailable'
                if kind=='navigation': unavailable['navigationContext']['Show']={'type':'Missing','value':True}
                else: unavailable['target']['method']='Missing'
                from composite_fixtures import dump_yaml
                assignment=base/(kind+'.yaml');assignment.write_text(dump_yaml({'name':kind,'checks':[good,bad,unavailable]},allow_unicode=True,sort_keys=False))
                result,_=run_test(root,assignment,base/(kind+'-public'))
                self.assertEqual([v['status'] for v in result['checks']],['PASS','FAIL','UNSUPPORTED'],result)
                second=base/(kind+'-second');shutil.copytree(root,second)
                manifest=base/(kind+'-batch.json');manifest.write_text(json.dumps({'schemaVersion':'1.0',
                    'assignment':str(assignment),'assignmentId':kind,'submissions':[
                        {'studentId':'first','project':str(root)},{'studentId':'second','project':str(second)}]}))
                batch_dir=base/(kind+'-batch');self.assertEqual(run_batch(manifest,batch_dir,workers=2),1)
                batch=json.loads((batch_dir/'batch-result.json').read_text())
                for row in batch['submissions']:
                    evidence=json.loads((batch_dir/row['result']).read_text())
                    self.assertEqual([v['status'] for v in evidence['checks']],['PASS','FAIL','UNSUPPORTED'])
                    self.assertEqual((evidence['score'],evidence['unavailablePoints']),(2,2))


if __name__=='__main__': unittest.main()
