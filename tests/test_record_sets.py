"""Task 32 acceptance: original source executes in Docker, grading remains SBSL."""
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
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine, prepare_script, run_pure
from element_test.yaml_io import InputError, InvalidTestError
from record_set_fixtures import *

EVIDENCE = REPO/'result/record-set-reading'


def normalized(actual):
    value = copy.deepcopy(actual)
    for r in value['storage']:
        if isinstance(r['value'],list):r['value'].sort(key=lambda x:json.dumps(x,sort_keys=True))
    value['storage'].sort(key=lambda x:(x['type'],x['id']))
    return value


def grade(actual, expected, temp):
    write_json(temp/'model.json',{'elements':[],'modules':[]})
    write_json(temp/'assignment.json',{'checks':[{'id':'independent','type':'runtime','points':1,
        'comparison':'record-sets-unordered','expected':expected,'execution':{'status':'EXECUTED','actual':actual}}]})
    return execute_engine('test',temp/'model.json',temp/'assignment.json',temp)['checks'][0]['status']


class RecordSetPlanTest(unittest.TestCase):
    def test_binding_spans_aliases_parameters_dependencies_and_unordered_grading(self):
        root=CORPUS/'information';c=info_check();c['expected']={'secret-answer':1234567}
        p=plan_execution(root,analyze(root),c)
        self.assertNotIn('secret-answer',p.to_json())
        self.assertTrue(any(r['symbol']=='Refresh' and r['operation']=='Прочитать' for r in
            plan_execution(root,analyze(root),info_check('Detached',[])).record_sets))
        for r in p.record_sets:
            source=(root/r['sourceFile']).read_text()
            self.assertEqual(r['owner'],'Data::Prices');self.assertEqual(r['contract'],'storage-record-set-v1')
            self.assertTrue(source[r['start']:r['end']]);self.assertEqual(r['fields'][0]['Тип'],'Дата')
        loop=next(r for r in p.record_sets if r['operation']=='iterate')
        self.assertEqual((root/loop['sourceFile']).read_text()[loop['start']:loop['end']],'R')
        write_json(EVIDENCE/'plan-information.json',p.to_dict())
        with TemporaryDirectory() as d:
            temp=Path(d);expected=expected_copy(c,info=True);actual=copy.deepcopy(expected)
            actual['storage'].reverse();actual['storage'][0]['value'].reverse()
            self.assertEqual(grade(actual,expected,temp),'PASS')
            actual['storage'][0]['value'].append(copy.deepcopy(actual['storage'][0]['value'][0]))
            self.assertEqual(grade(actual,expected,temp),'FAIL')

    def test_full_filter_required_and_teacher_input_errors(self):
        root=CORPUS/'information';model=analyze(root)
        with self.assertRaisesRegex(InputError,'полный фильтр'):
            plan_execution(root,model,info_check('Partial',[]))
        with TemporaryDirectory() as d:
            p=Path(d)
            for key,value in [('filter',{'Unknown':'base'}),('type','Missing'),('rows',[{'Период':'invalid'}])]:
                c=info_check();c['storage']['initialRegisters'][0][key]=value
                with self.assertRaises(InvalidTestError):prepare_script(root,model,c,p)
            for key,value in [('Price','wrong'),('Период','2026-02-30'),('Период','2026-09-30T12:00:00')]:
                c=info_check();c['storage']['initialRegisters'][0]['rows'][0][key]=value
                with self.assertRaises(InvalidTestError):prepare_script(root,model,c,p)
            c=info_check();c['storage']['initialRegisters'][0]['rows'][0]['Key']='mismatch'
            with self.assertRaisesRegex(InvalidTestError,'не соответствует'):prepare_script(root,model,c,p)
            c=info_check();c['storage']['initialRegisters'][0]['rows'][1]['Период']=DATES[0]
            with self.assertRaisesRegex(InvalidTestError,'Неуникальные'):prepare_script(root,model,c,p)
            # Ambiguous owner is a teacher error, explicit qualified source remains valid.
            other=p/'source';shutil.copytree(root,other);(other/'Other').mkdir()
            shutil.copy(other/'Data/Prices.yaml',other/'Other/Prices.yaml')
            c=info_check();c['storage']['initialRegisters'][0]['type']='Prices'
            with self.assertRaises(InvalidTestError):prepare_script(other,analyze(other),c,p)

    def test_unconfirmed_period_and_filter_fields_are_unsupported(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'information',root)
            meta=root/'Data/Prices.yaml';meta.write_text(meta.read_text().replace('Периодичность: День','Периодичность: Секунда'))
            with self.assertRaisesRegex(InputError,'только для День'):plan_execution(root,analyze(root),info_check())
            meta.write_text(meta.read_text().replace('Периодичность: Секунда','Периодичность: День'))
            main=root/'Entry/Flow.xbsl';main.write_text(main.read_text()+'''\nметод UnknownFilter()
    знч R = новый Book.НаборЗаписей()
    R.Фильтр.Ключ = "arbitrary"
    R.Прочитать()
;
''')
            with self.assertRaises(InputError):plan_execution(root,analyze(root),info_check('UnknownFilter',[]))

    def test_project_name_collision_shadowing_and_no_spy_reads(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'information',root)
            main=root/'Entry/Flow.xbsl'
            main.write_text(main.read_text()+'\nметод Прочитать(): Число\n    возврат 17\n;\nметод Own(): Число\n    возврат Прочитать()\n;\n')
            p=plan_execution(root,analyze(root),{'target':{'module':'Flow','namespace':'Entry','method':'Own'}})
            self.assertEqual(p.bindings[0].category,'project');self.assertEqual(p.record_sets,[])
            main.write_text('метод Own()\n    знч Book = "shadow"\n    Book.Прочитать()\n;\n')
            with self.assertRaises(InputError):plan_execution(root,analyze(root),{'target':{'module':'Flow','namespace':'Entry','method':'Own'}})
            root=CORPUS/'accumulation';c=accumulation_check('Прочесть',[REFS[0]]);c.pop('storage');c['mocks']={'registers':['Учет::Движения']}
            with self.assertRaisesRegex(InputError,'требует storage'):plan_execution(root,analyze(root),c)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 32 Docker is opt-in')
class RecordSetDockerTest(unittest.TestCase):
    def execute(self, root, c, temp, name):
        r=run_pure(root,analyze(root),c,temp)
        write_json(EVIDENCE/(name+'.json'),r)
        self.assertEqual(r['status'],'EXECUTED',r)
        return r

    def test_portable_copy_empty_one_many_and_grading(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for name,make,flags in [('information',info_check,{'info':True}),('accumulation',accumulation_check,{'accumulation':True})]:
                root=CORPUS/name
                for n in (0,1,2):
                    c=make();c['storage']['initialRegisters'][0]['rows']=c['storage']['initialRegisters'][0]['rows'][:n]
                    r=self.execute(root,c,temp,f'{name}-{n}');expected=expected_copy(c,**flags)
                    self.assertEqual(normalized(r['actual']),normalized(expected));self.assertEqual(grade(r['actual'],expected,temp),'PASS')
                    wrong=copy.deepcopy(expected);wrong['storage'][1]['value'].append({'teacher':'independent-wrong'})
                    self.assertEqual(grade(r['actual'],wrong,temp),'FAIL')
                unsupported=make('Partial' if name=='information' else 'Недоступно',[])
                self.assertEqual(run_pure(root,analyze(root),unsupported,temp)['status'],'UNSUPPORTED')
                self.execute(root,make(),temp,name+'-recovery')

    def test_snapshots_repeat_append_replace_and_rollback(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=CORPUS/'information';seeds=info_check()['storage']['initialRegisters']
            r=self.execute(root,info_check('Detached',[]),temp,'detached')['actual']
            self.assertEqual(r['result']['second'],seeds[0]['rows']);self.assertEqual(r['result']['refreshed'],seeds[2]['rows'])
            self.assertEqual(r['result']['old'],[]);self.assertEqual(normalized(r)['storage'],normalized({'storage':[audit(s) for s in seeds]})['storage'])
            r=self.execute(root,info_check('Repeat',[]),temp,'repeat')['actual']['result']
            self.assertEqual(r['old'],seeds[0]['rows']);expected=copy.deepcopy(seeds[0]['rows'])
            for row in expected:row['Price']*=2
            self.assertEqual(r['new'],expected)
            r=self.execute(root,info_check('Append',[]),temp,'append')['actual']
            self.assertTrue(r['result']['emptyAfterAppend']);self.assertEqual(len(r['result']['rows']),3)
            self.assertEqual(r['result']['rows'][-1]['Период'],'2026-10-01')
            r=self.execute(root,info_check('Clear',[]),temp,'clear')['actual']
            self.assertEqual(next(x for x in r['storage'] if json.loads(x['id'])==seeds[0]['filter'])['value'],[])
            r=self.execute(root,info_check('Rollback',[]),temp,'rollback')
            self.assertEqual(r['actual']['result'],seeds[1]['rows']);self.assertIn('source:rollback',r['storageTrace'])
            r=self.execute(root,info_check('Poison',[]),temp,'poisoned-transaction')
            self.assertEqual(r['actual']['result'],seeds[1]['rows'])
            self.assertEqual(normalized(r['actual'])['storage'],normalized({'storage':[audit(s) for s in seeds]})['storage'])
            for method in ('MissingFilter','Duplicate'):
                c=info_check(method,[]);c['captureException']=True
                r=self.execute(root,c,temp,method)['actual'];self.assertIsNotNone(r['result']['exception'])
                self.assertEqual(normalized(r)['storage'],normalized({'storage':[audit(s) for s in seeds]})['storage'])

    def test_independent_nested_references_and_old_snapshots(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'information',root)
            main=root/'Entry/Flow.xbsl';main.write_text(main.read_text()+'''\nметод Nested(): Объект
    знч A: Book.НаборЗаписей = Helper.Load("base")
    знч B: Book.НаборЗаписей = Helper.Load("base")
    знч Old = A.Записи
    для R из A
        (R.Link как Data::Item.Ссылка).Идентификатор = новый Ууид("33333333-1234-4234-8234-123456789abc")
        R.Price = 100
    ;
    A.Прочитать()
    возврат {"old": Old, "second": B.Записи, "new": A.Записи}
;
''')
            c=info_check('Nested',[]);r=self.execute(root,c,temp,'nested')['actual']
            base=c['storage']['initialRegisters'][0]['rows']
            self.assertEqual(r['result']['second'],base);self.assertEqual(r['result']['new'],base)
            self.assertEqual(r['result']['old'][0]['Link'],REFS[2]);self.assertEqual(r['result']['old'][0]['Price'],100)
            self.assertEqual(normalized(r)['storage'],normalized({'storage':[audit(s) for s in c['storage']['initialRegisters']]})['storage'])

    def test_owner_registrar_collisions_and_computed_dependency_loop(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'info';shutil.copytree(CORPUS/'information',root)
            main=root/'Entry/Flow.xbsl';main.write_text(main.read_text()+'''\nметод Computed(): Число
    пер Sum = 0
    для R из Helper.Load("base")
        Sum += R.Price
    ;
    возврат Sum
;
''')
            r=self.execute(root,info_check('Computed',[]),temp,'computed-loop');self.assertEqual(r['actual']['result'],6.5)
            other=root/'Other';other.mkdir();shutil.copy(root/'Data/Prices.yaml',other/'Prices.yaml');shutil.copy(root/'Data/Item.yaml',other/'Item.yaml')
            main.write_text(main.read_text()+'''\nметод Both(): Объект
    знч A = новый Data::Prices.НаборЗаписей()
    знч B = новый Other::Prices.НаборЗаписей()
    A.Фильтр.Установить(Key = "base", Region = "EU")
    B.Фильтр.Установить(Region = "EU", Key = "base")
    A.Прочитать()
    B.Прочитать()
    возврат {"a": A.Записи, "b": B.Записи}
;
''')
            c=info_check('Both',[]);c['storage']['registers'].append('Other::Prices')
            seed=copy.deepcopy(c['storage']['initialRegisters'][0]);seed['type']='Other::Prices';seed['rows'][0]['Price']=99
            c['storage']['initialRegisters'].append(seed)
            r=self.execute(root,c,temp,'owner-collision')['actual'];self.assertEqual(r['result']['a'][0]['Price'],2.5);self.assertEqual(r['result']['b'][0]['Price'],99)
            root=temp/'acc';shutil.copytree(CORPUS/'accumulation',root);(root/'Other').mkdir();shutil.copy(root/'Учет/Акт.yaml',root/'Other/Акт.yaml')
            reg=root/'Учет/Движения.yaml';reg.write_text(reg.read_text().replace('Тип: Акт.Ссылка','Тип: "Учет::Акт.Ссылка|Other::Акт.Ссылка"'))
            main=root/'Вход/Поток.xbsl';main.write_text(main.read_text()+'''\nметод UnionRead(Ref: Учет::Акт.Ссылка|Other::Акт.Ссылка): Объект
    знч R = новый Книга.НаборЗаписей()
    R.Фильтр.Установить(Регистратор = Ref)
    R.Прочитать()
    возврат {"rows": R.Записи, "typeIsFirst": R.Записи[0].Регистратор это Учет::Акт.Ссылка}
;
''')
            c=accumulation_check('UnionRead',[{'type':'Учет::Акт.Ссылка','value':REFS[0]}])
            seed=accumulation_seed(REFS[0],99)
            first=accumulation_seed(REFS[0],2.5)
            first['filter']['Регистратор']={'type':'Учет::Акт.Ссылка','value':REFS[0]}
            seed['filter']['Регистратор']={'type':'Other::Акт.Ссылка','value':REFS[0]}
            for item in (first,seed):
                for row in item['rows']:row['Регистратор']=copy.deepcopy(item['filter']['Регистратор'])
            c['storage']['initialRegisters']=[first,seed]
            c['sequence']=[{'method':'UnionRead','args':[first['filter']['Регистратор']]},{'method':'UnionRead','args':[seed['filter']['Регистратор']]}]
            r=self.execute(root,c,temp,'registrar-type-collision')['actual']
            self.assertEqual([a['rows'][0]['Количество'] for a in r['result']['actions']],[2.5,99]);self.assertEqual(len(r['storage']),2)
            self.assertEqual([a['typeIsFirst'] for a in r['result']['actions']],[True,False])

    def test_handler_reads_staged_rows_and_atomic_rollback(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'information',root)
            (root/'Data/Log.yaml').write_text('ВидЭлемента: Документ\nИмя: Log\nОбластьВидимости: ВПроекте\nРеквизиты: []\n')
            (root/'Data/Log.Объект.xbsl').write_text('''метод ПослеЗаписи(До: Log.Данные, Параметры: Log.ПараметрыЗаписи)
    знч R = новый Prices.НаборЗаписей()
    R.Фильтр.Установить(Key = "target", Region = "EU")
    R.Прочитать()
    для Row из R
        Row.Price += 1
    ;
    R.Записать()
;
''')
            main=root/'Entry/Flow.xbsl';main.write_text(main.read_text()+'''\nметод Persist(ID: Ууид, Crash: Булево): Объект
    попытка
        исп Транзакции.Начать()
        Copy("base", "target", 3)
        знч O = новый Data::Log.Объект(Ид = ID)
        O.Записать()
        если Crash
            выбросить новый ИсключениеВалидации("rollback")
        ;
    поймать E: ИсключениеВалидации
        возврат Read("target")
    ;
    возврат Read("target")
;
''')
            for crash in (False,True):
                c=info_check('Persist',[IDS[0],crash]);r=self.execute(root,c,temp,'handler-'+str(crash))
                if crash:
                    self.assertEqual(r['actual']['result'],c['storage']['initialRegisters'][1]['rows'])
                    self.assertFalse(any(x['type']=='Data::Log' for x in r['actual']['storage']))
                else:
                    self.assertEqual([x['Price'] for x in r['actual']['result']],[8.5,13])
                    self.assertTrue(any(x['type']=='Data::Log' for x in r['actual']['storage']))

    def test_real_unmodified_nullable_empty_missing_and_sql_independent_answers(self):
        with open_project(ARCHIVE) as root,TemporaryDirectory() as d:
            temp=Path(d)
            for name,options in [('real',{}),('real-empty',{'empty':True}),('real-missing',{'missing':True}),('real-nullable',{'nullable':True})]:
                c=real_check(**options);r=self.execute(root,c,temp,name)
                expected=expected_copy(c,missing=options.get('missing',False))
                self.assertEqual(normalized(r['actual']),normalized(expected));self.assertEqual(grade(r['actual'],expected,temp),'PASS')

    def test_valid_source_mutations_grade_fail(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'information',root)
            main=root/'Entry/Flow.xbsl';helper=root/'Service/Reader.xbsl';original=main.read_text();original_helper=helper.read_text()
            c=info_check();expected=expected_copy(c,info=True)
            mutations=[('skip-read',helper,'    R.Прочитать()\n',''),
                ('wrong-filter',helper,'Region = "EU", Key = Key','Region = "US", Key = Key'),
                ('formula',main,'Row.Price *= N','Row.Price += N'),('field',main,'Row.Price *= N','Row.Factor *= N'),
                ('factor',main,'Row.Price *= N','Row.Price *= N\n        Row.Factor = 0'),
                ('period',main,'Row.Price *= N','Row.Price *= N\n        Row.Период = новый Дата(2025, 1, 1)'),
                ('base-write',main,'    для Row из R','    R.Очистить()\n    R.Записать()\n    для Row из R'),
                ('other-bucket',main,'Key = To, Region = "EU"','Key = To, Region = "US"'),
                ('stale',main,'    R.Записать()','    R.Прочитать()\n    R.Записать()'),
                ('append',main,'    R.Записать()','    R.Записать(Замещать = Ложь)')]
            for name,path,a,b in mutations:
                main.write_text(original);helper.write_text(original_helper);path.write_text(path.read_text().replace(a,b))
                if name=='period':
                    # Different normalized dates are valid keys, so this mutation
                    # produces a graded mismatch instead of duplicate-key rejection.
                    main.write_text(main.read_text().replace('новый Дата(2025, 1, 1)','Row.Период == новый Дата(2026, 9, 29) ? новый Дата(2026, 10, 1) : новый Дата(2026, 10, 2)'))
                if name=='other-bucket':
                    main.write_text(main.read_text().replace('Row.Key = To','Row.Key = To\n        Row.Region = "US"'))
                r=self.execute(root,c,temp,'mutation-'+name)
                self.assertEqual(grade(r['actual'],expected,temp),'FAIL',name)
            main.write_text(original);helper.write_text(original_helper);self.execute(root,c,temp,'mutation-recovery')

    def test_file_error_sticky_timeout_invalid_teacher_and_recovery(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=CORPUS/'information';c=info_check('Read',['base'])
            from element_test.runtime import prepare_script as original_prepare
            def broken(root,model,check,out):
                script=original_prepare(root,model,check,out)
                adapter=out/'ТестСессия.sbsl'
                # Fail precisely in the read operation, after teacher preparation
                # and the source transaction snapshot have succeeded.
                code=adapter.read_text()
                code=code.replace('    знч Записи = ЧитатьВсе()\n    если не Записи.СодержитКлюч(Тип)',
                    '    если Тип != ""\n        СбойФайла()\n        выбросить новый ТестБизнесИсключения.СбойХранилища("injected file read failure")\n    ;\n    знч Записи = ЧитатьВсе()\n    если не Записи.СодержитКлюч(Тип)',1)
                adapter.write_text(code)
                return script
            # Deliberate trusted backend fault; source catch cannot hide it.
            local=temp/'source';shutil.copytree(root,local);main=local/'Entry/Flow.xbsl'
            main.write_text(main.read_text()+'''\nметод Swallow(): Объект
    попытка
        исп Транзакции.Начать()
        возврат Read("base")
    поймать E: Исключение
        возврат "swallowed"
    ;
;
''')
            with patch('element_test.runtime.prepare_script',side_effect=broken):
                r=run_pure(local,analyze(local),info_check('Swallow',[]),temp)
            write_json(EVIDENCE/'file-failure.json',r);self.assertEqual(r['status'],'ERROR',r)
            timeout=copy.deepcopy(c);timeout['timeout']=0.001
            self.assertEqual(run_pure(root,analyze(root),timeout,temp)['status'],'TIMEOUT')
            invalid=copy.deepcopy(c);invalid['storage']['initialRegisters'][0]['rows'][0]['Price']='wrong'
            r=run_pure(root,analyze(root),invalid,temp);self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','invalid_test'))
            self.execute(root,c,temp,'after-failures')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task 32 PostgreSQL is opt-in')
class RecordSetSqlTest(unittest.TestCase):
    def test_portable_real_audit_rollback_failure_recovery(self):
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}),open_project(ARCHIVE) as real:
            temp=Path(d)
            for name,root,c,expected in [('info',CORPUS/'information',info_check(sql=True),expected_copy(info_check(),info=True)),
                ('accumulation',CORPUS/'accumulation',accumulation_check(sql=True),expected_copy(accumulation_check(),accumulation=True)),
                ('real',real,real_check(sql=True),expected_copy(real_check()))]:
                for failure in (False,True,False):
                    r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=failure)
                    write_json(EVIDENCE/f'sql-{name}-{failure}.json',r);self.assertTrue(r.get('integration',{}).get('cleanup'),r)
                    if failure:
                        self.assertEqual(r['status'],'ERROR',r)
                        prior=[audit(s) for s in c['storage']['initialRegisters']]
                        prior += [{'type':s['type'],'id':s['value']['Ссылка']['Идентификатор'],'value':s['value']} for s in c['storage'].get('initial',[])]
                        self.assertEqual(normalized({'storage':r['storageEvidence']})['storage'],normalized({'storage':prior})['storage'])
                    else:
                        self.assertEqual(r['status'],'EXECUTED',r)
                        self.assertEqual(normalized(r['actual']),normalized(expected));self.assertEqual(grade(r['actual'],expected,temp),'PASS')
            c=info_check('Rollback',[],sql=True)
            r=run_integration(c,analyze(CORPUS/'information'),temp,root=CORPUS/'information',enabled=True)
            self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(r['actual']['result'],c['storage']['initialRegisters'][1]['rows'])
            self.assertIn('source:rollback',r['storageTrace'])

    def test_record_read_only_sql_no_commit_invalid_teacher_and_swallowed_failure(self):
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            temp=Path(d);root=CORPUS/'information'
            c=info_check('Read',['base'],sql=True)
            # Injection occurs only at a changed publication boundary. Reading
            # leaves the initial publication unchanged, so this remains executed.
            r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=True)
            self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(r['actual']['result'],c['storage']['initialRegisters'][0]['rows'])
            self.assertEqual(r['storageTrace'].count('sql:commit'),1)
            c=info_check('SwallowSql',[],sql=True)
            r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=True)
            write_json(EVIDENCE/'sql-swallowed-failure.json',r);self.assertEqual(r['status'],'ERROR',r)
            self.assertTrue(r['integration']['cleanup'])
            self.assertEqual(normalized({'storage':r['storageEvidence']}),normalized({'storage':[audit(s) for s in c['storage']['initialRegisters']]}))
            r=run_integration(c,analyze(root),temp,root=root,enabled=True)
            self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(r['actual']['result'],'normal')
            c['storage']['initialRegisters'][0]['rows'][0]['Price']='wrong'
            r=run_integration(c,analyze(root),temp,root=root,enabled=True)
            self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','invalid_test'))
