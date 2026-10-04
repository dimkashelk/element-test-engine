"""Task 33: shared AST, independent SBSL assessment and Docker/SQL history audits."""
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
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_plan import parse_storage_query
from element_test.runtime import execute_engine, run_pure, prepare_script
from element_test.yaml_io import InputError, InvalidTestError
from slice_fixtures import *
from test_record_sets import grade, normalized


def setUpModule():
    EVIDENCE.mkdir(parents=True, exist_ok=True)

class SlicePlanTest(unittest.TestCase):
    def test_generic_ir_owners_aliases_fields_boundary_ranges_and_no_expected(self):
        for renamed in (False,True):
            s=schema(renamed);root=CORPUS/s['project'];c=check(s,'Reuse');c['expected']={'secret-answer':456789}
            p=plan_execution(root,analyze(root),c);q=p.queries[0];ast=q['ast']
            self.assertNotIn('secret-answer',p.to_json())
            self.assertEqual(ast['owner'],s['owner']);self.assertEqual(ast['source_kind'],'slice-last')
            self.assertEqual([f['name'] for f in ast['dimensions']],['Product','Vendor','Zone'])
            self.assertEqual([v['expression'] for v in ast['parameters']],['Helper.Boundary(Calls, D)','Helper.Product(Calls, P)'])
            text=(root/q['sourceFile']).read_text();self.assertEqual(text[q['start']:q['end']],'Запрос{'+q['text']+'}')
            a,b=ast['period_range'];self.assertEqual(q['text'][a:b],'(%{Helper.Boundary(Calls, D)})')
            self.assertEqual(ast['periodicity'],'День');self.assertEqual(ast['ordering'][0][0]['name'],'Период')
            self.assertTrue({'Boundary','Product'}.issubset({v.identity.declaration for v in p.symbols}))
            write_json(EVIDENCE/('plan-'+s['project']+'.json'),p.to_dict())

    def test_unconfirmed_sources_types_periodicity_nullable_and_shadowing(self):
        s=schema();root=CORPUS/'prices';model=analyze(root)
        for method,args in [('Nullable',[None,'2026-09-30']),('Unsupported',[])]:
            with self.assertRaises(InputError):plan_execution(root,model,check(s,method,args))
        c=check(s);c['storage']['registers']=[];c['storage']['initialRegisters']=[]
        with self.assertRaisesRegex(InputError,'storage.registers'):plan_execution(root,model,c)
        contracts=ProjectTypes(model,'Entry',['Data::Prices как Book'])
        shared=parse_storage_query('ВЫБРАТЬ Price ИЗ Book.СрезПоследних(%D) ГДЕ Период == %D',contracts)
        self.assertEqual([(p.slot,p.type) for p in shared.parameters],[(0,'Дата'),(0,'Дата')])
        for query in ['ВЫБРАТЬ Price ИЗ Book.СрезПоследних(%{Неопределено}, %D)',
                      'ВЫБРАТЬ Unknown ИЗ Book.СрезПоследних()',
                      'ВЫБРАТЬ Price ИЗ Book.СрезПоследних() ГДЕ Note == %N',
                      'ВЫБРАТЬ Price ИЗ Book.СрезПоследних() УПОРЯДОЧИТЬ ПО Product',
                      'ВЫБРАТЬ Price ИЗ Book.СрезПоследних() СОЕДИНЕНИЕ Book',
                      'ВЫБРАТЬ Price ИЗ Book.СрезПоследних(%D) ГДЕ Price == %D']:
            with self.subTest(query=query),self.assertRaises(InputError):parse_storage_query(query,contracts)
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'prices',root)
            main=root/'Entry/Flow.xbsl';original=main.read_text()
            main.write_text(original.replace('метод Current():','метод Current(Book: Строка):'))
            with self.assertRaisesRegex(InputError,'Затенённый'):plan_execution(root,analyze(root),check(s,'Current',['x']))
            main.write_text(original)
            meta=root/'Data/Prices.yaml';meta.write_text(meta.read_text().replace('Периодичность: День','Периодичность: Секунда'))
            # Task 43 accepts second histories, but a Day-typed boundary cannot
            # be silently coerced to DateTime after the metadata change.
            with self.assertRaisesRegex(InputError,'тип параметра'):plan_execution(root,analyze(root),check(s))
            meta.write_text(meta.read_text().replace('Периодичность: Секунда','Периодичность: День').replace(
                'Тип: Item.Ссылка?', 'Тип: "Item.Ссылка|Partner.Ссылка"'))
            with self.assertRaisesRegex(InputError,'Union'):
                parse_storage_query('ВЫБРАТЬ Price ИЗ Data::Prices.СрезПоследних()',ProjectTypes(analyze(root),'Entry',[]))

    def test_invalid_teacher_rows_and_period_and_duplicate_keys(self):
        s=schema();root=CORPUS/'prices'
        with TemporaryDirectory() as d:
            for change in ['missing','date','duplicate','filter']:
                c=check(s);first=c['storage']['initialRegisters'][0]
                if change=='missing':del first['rows'][0]['Product']
                if change=='date':first['rows'][0]['Период']='2026-09-30T00:00:00'
                if change=='duplicate':first['rows'][1]['Период']=first['rows'][0]['Период']
                if change=='filter':del first['filter']['Vendor']
                with self.subTest(change=change),self.assertRaises(InvalidTestError):prepare_script(root,analyze(root),c,Path(d))

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 33 Docker opt-in')
class SliceDockerTest(unittest.TestCase):
    def execute(self,root,c,temp,name):
        r=run_pure(root,analyze(root),c,temp)
        write_json(EVIDENCE/(name+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r)
        return r

    def test_two_projects_boundaries_complete_keys_nullable_and_post_slice_filter(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for renamed in (False,True):
                s=schema(renamed);root=CORPUS/s['project']
                cases=[('All',['2026-09-30'],last_rows(s)),('ByPeriod',['2026-09-30'],[{s['amount']:5}]),('Find',[REFS[0],'2026-09-30'],5),
                       ('Find',[REFS[0],'2026-09-29'],8),('Find',[REFS[0],'2026-09-28'],100),
                       ('Find',[REFS[0],'2026-09-24'],None),('Find',[REFS[2],'2026-09-30'],None),
                       ('Resource',['2026-09-30',100],[]),('Resource',['2026-09-28',100],[{s['amount']:100}]),
                       ('Current',[],[{'Период':r['Период'],s['amount']:r[s['amount']]} for r in last_rows(s)]),
                       ('DefaultDate',[],[{'Период':r['Период'],s['amount']:r[s['amount']]} for r in last_rows(s)])]
                for i,(method,args,result) in enumerate(cases):
                    c=check(s,method,args);r=self.execute(root,c,temp,f'portable-{renamed}-{i}')
                    self.assertEqual(normalized(r['actual']),normalized(expected(c,result)))
                    self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS')
                    self.assertFalse(any(x.endswith(':commit') for x in r['storageTrace']))
                for values in [[],[seed(s,REFS[0],REFS[0],[('2026-09-30',11,1)])]]:
                    c=check(s,initial=values);r=self.execute(root,c,temp,f'empty-single-{renamed}-{len(values)}')
                    self.assertEqual(r['actual']['result'],11 if values else None)

    def test_current_clock_snapshot_is_from_executor(self):
        import subprocess
        s=schema()
        with TemporaryDirectory() as d:
            runtime=json.loads((REPO/'config/runtimes.json').read_text())['9.0']
            def snapshot():
                return subprocess.run(['docker','run','--rm','--network','none','--read-only',runtime['image'],
                    'date','-u','+%F'],capture_output=True,text=True,check=True,timeout=15).stdout.strip()
            before=snapshot()
            r=self.execute(CORPUS/'prices',check(s,'Current',[]),Path(d),'runtime-clock')
            after=snapshot()
            value=next(x.split('query:slice-boundary:',1)[1] for x in r['storageTrace'] if x.startswith('query:slice-boundary:'))
            self.assertIn(value,{before,after})
            write_json(EVIDENCE/'independent-docker-clock.json',{'beforeUTC':before,'capturedUTC':value,'afterUTC':after})
            self.assertNotIn('9999-12-31',json.dumps(r['actual']['result']))

    def test_reexecution_captured_effects_refs_and_transaction_rollback(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for renamed in (False,True):
                s=schema(renamed);root=CORPUS/s['project']
                reuse=check(s,'Reuse')
                r=self.execute(root,reuse,temp,f'reexecution-{renamed}')
                self.assertEqual(grade(r['actual'],expected_reuse(s,reuse),temp),'PASS')
                self.assertEqual(r['actual']['result']['calls'],['date','product'])
                self.assertEqual([x[s['amount']] for x in r['actual']['result']['old']],[5,8,3])
                self.assertEqual([x[s['amount']] for x in r['actual']['result']['new']],[17,5,8])
                self.assertEqual(r['actual']['result']['new'],r['actual']['result']['again'])
                self.assertEqual(r['actual']['result']['new'][0]['Product'],REFS[0])
                self.assertEqual(r['actual']['result']['old'][0]['Product'],REFS[1])
                for wrapper in (False,True):
                    c=check(s,'Transaction');c['storage']['transaction']=wrapper
                    r=self.execute(root,c,temp,f'rollback-{renamed}-{wrapper}')
                    self.assertEqual(r['actual']['result'],{'seen':77,'after':5})
                    self.assertEqual(normalized(r['actual'])['storage'],normalized(expected(c,None))['storage'])
                    self.assertEqual(r['storageDiagnostics'],{'active':False,'locks':0})

    def test_real_archive_unchanged_no_mock_and_independent_history(self):
        with open_project(ARCHIVE) as root,TemporaryDirectory() as d:
            for missing,empty in [(False,False),(True,False),(False,True)]:
                c=real_check(missing,empty);r=self.execute(root,c,Path(d),f'real-{missing}-{empty}')
                exp=expected(c,None if missing or empty else 5)
                self.assertEqual(normalized(r['actual']),normalized(exp));self.assertEqual(grade(r['actual'],exp,Path(d)),'PASS')
                self.assertEqual(r['storageTrace'][0],'teacher:register-setup')
                self.assertEqual(len([x for x in r['storageTrace'] if x.startswith('query:')]),1)
                self.assertFalse(any('commit' in x for x in r['storageTrace']))

    def test_source_and_renderer_mutations_fail_independent_sbsl(self):
        import element_test.storage_queries as adapter
        s=schema()
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'prices',root)
            main=root/'Entry/Flow.xbsl';original=main.read_text()
            c=check(s);exp=expected(c,5)
            for label,a,b in [('boundary','СрезПоследних(%D)','СрезПоследних(%{новый Дата(2026, 9, 29)})'),
                              ('resource','X.Price КАК Value','X.Factor КАК Value'),
                              ('sort','ПО X.Период УБЫВ','ПО X.Период ВОЗР'),
                              ('limit','ПЕРВЫЕ 1','ПЕРВЫЕ 2')]:
                main.write_text(original.replace(a,b))
                c2=copy.deepcopy(c)
                if label=='limit':c2['captureException']=True
                r=self.execute(root,c2,temp,'mutation-'+label)
                self.assertEqual(grade(r['actual'],exp,temp),'FAIL',label)
            main.write_text(original)
            reuse=check(s,'Reuse')
            main.write_text(original.replace('знч New = Q.Выполнить()','знч New = Old'))
            r=self.execute(root,reuse,temp,'mutation-stale')
            self.assertEqual(grade(r['actual'],expected_reuse(s,reuse),temp),'FAIL')
            main.write_text(original)
            native=adapter.generate_query
            for label,a,b,method,args,result in [
                ('strict','СтрокаДанных.Период <= Граница','СтрокаДанных.Период < Граница','Find',None,5),
                ('global','СериализацияJson.ЗаписатьОбъект({"Product":','СериализацияJson.ЗаписатьОбъект({"Product":','All',['2026-09-30'],last_rows(s)),
                ('future','СтрокаДанных.Период <= Граница','Истина','Find',None,5),
                ('early-filter','СтрокаДанных.Период <= Граница','СтрокаДанных.Период <= Граница и СтрокаДанных.Price == П1','Resource',['2026-09-30',100],[]),
                ('dimension','','','All',['2026-09-30'],last_rows(s))]:
                def mutant(query,contracts):
                    if label=='dimension':
                        from dataclasses import replace
                        query=replace(query,dimensions=tuple(f for f in query.dimensions if f.name!='Vendor'))
                    name=native(query,contracts)
                    if label=='global':
                        import re
                        contracts.definitions[name]=re.sub(r'знч Ключ = СериализацияJson.ЗаписатьОбъект\([^\n]+','знч Ключ = "one"',contracts.definitions[name])
                    elif label!='dimension':contracts.definitions[name]=contracts.definitions[name].replace(a,b)
                    return name
                c2=check(s,method,args)
                with patch('element_test.storage_queries.generate_query',side_effect=mutant):
                    r=self.execute(root,c2,temp,'mutation-'+label)
                self.assertEqual(grade(r['actual'],expected(c2,result),temp),'FAIL',label)
            unavailable=run_pure(root,analyze(root),check(s,'Unsupported',[]),temp)
            self.assertEqual(unavailable['status'],'UNSUPPORTED');write_json(EVIDENCE/'unsupported.json',unavailable)
            r=self.execute(root,c,temp,'recovery-after-unsupported');wrong=expected(c,123)
            self.assertEqual(grade(r['actual'],wrong,temp),'FAIL')

    def test_multiple_owners_equal_uuid_and_unrelated_project_method(self):
        s=schema()
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'prices',root)
            (root/'Other').mkdir();shutil.copy(root/'Data/Prices.yaml',root/'Other/Prices.yaml')
            meta=root/'Other/Prices.yaml'
            meta.write_text(meta.read_text().replace('Item.Ссылка','Data::Item.Ссылка').replace('Partner.Ссылка','Data::Partner.Ссылка'))
            main=root/'Entry/Flow.xbsl';main.write_text(main.read_text()+'''\nметод Other(D: Дата): Объект
    возврат Запрос{ВЫБРАТЬ Price ИЗ Other::Prices.СрезПоследних(%D) ГДЕ Zone == %{"EU"} УПОРЯДОЧИТЬ ПО Период}.Выполнить()
;
метод СрезПоследних(): Число
    возврат 99
;
метод Own(): Число
    возврат СрезПоследних()
;
''')
            c=check(s);c['storage']['registers'].append('Other::Prices')
            other=seed(s,REFS[0],REFS[0],[('2026-09-30',13,1)]);other['type']='Other::Prices';c['storage']['initialRegisters'].append(other)
            c['sequence']=[{'method':'Find','args':[REFS[0],'2026-09-30']},{'method':'Other','args':['2026-09-30']},{'method':'Own','args':[]}]
            r=self.execute(root,c,temp,'owners-types-collision')
            self.assertEqual(r['actual']['result']['actions'],[5,[{'Price':13}],99])
            main.write_text(main.read_text().replace('ИЗ Book.СрезПоследних(%D) КАК X','ИЗ Other::Prices.СрезПоследних(%D) КАК X'))
            mutant=self.execute(root,c,temp,'mutation-owner')
            self.assertEqual(grade(mutant['actual'],r['actual'],temp),'FAIL')

    def test_failure_swallowed_timeout_and_recovery(self):
        import element_test.storage as storage
        s=schema();root=CORPUS/'prices'
        with TemporaryDirectory() as d:
            # One teacher bucket ensures the first existing-file read belongs
            # to the source query, rather than the next teacher setup write.
            temp=Path(d);c=check(s,'Swallow',[],initial=[seeds(s)[0]]);native=storage.session_module
            def failing(*args,**kwargs):
                text=native(*args,**kwargs)
                return text.replace('исп Поток = Ф.ОткрытьПотокЧтения()',
                    'если Ф.Существует() и не новый Файл(Путь + ".once").Существует()\n'
                    '        исп Отметка = новый Файл(Путь + ".once").ОткрытьПотокЗаписи()\n'
                    '        СериализацияJson.ЗаписатьОбъект(Отметка, Истина)\n'
                    '        выбросить новый ИсключениеНедопустимоеСостояние("injected slice read failure")\n'
                    '    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
            with patch('element_test.storage.session_module',side_effect=failing):r=run_pure(root,analyze(root),c,temp)
            self.assertEqual(r['status'],'ERROR');write_json(EVIDENCE/'swallowed-error.json',r)
            c2=check(s);c2['timeout']=0.001;r=run_pure(root,analyze(root),c2,temp)
            self.assertEqual(r['status'],'TIMEOUT');write_json(EVIDENCE/'timeout.json',r)
            r=self.execute(root,c,temp,'after-failures');self.assertEqual(r['actual']['result'],'ok')

    def test_handler_slice_sees_staging_and_atomic_rollback(self):
        s=schema()
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'prices',root)
            (root/'Data/Log.yaml').write_text('ВидЭлемента: Документ\nИмя: Log\nОбластьВидимости: ВПроекте\nРеквизиты:\n  - {Имя: Product, Тип: Item.Ссылка}\n')
            (root/'Data/Log.Объект.xbsl').write_text('''метод ПослеЗаписи(До: Log.Данные, Параметры: Log.ПараметрыЗаписи)
    знч R = новый Prices.НаборЗаписей()
    R.Фильтр.Установить(Product = Product, Vendor = Неопределено, Zone = "EU")
    R.ДобавитьЗапись(Период = новый Дата(2026, 9, 30), Product = Product, Vendor = Неопределено, Zone = "EU", Price = 77, Factor = 1, Link = Неопределено, Note = "handler")
    R.Записать()
    знч Q = Запрос{ВЫБРАТЬ Price ИЗ Prices.СрезПоследних(%{новый Дата(2026, 9, 30)}) ГДЕ Product == %{Product} И Price == %{77}}
    если Q.Выполнить().Единственный().Price != 77
        выбросить новый ИсключениеНедопустимоеСостояние("handler slice")
    ;
;
''')
            main=root/'Entry/Flow.xbsl';main.write_text(main.read_text()+'''\nметод Persist(P: Entity.Ссылка, D: Дата, Crash: Булево): Объект
    пер Seen: Объект? = Неопределено
    попытка
        исп Транзакции.Начать()
        знч O = новый Data::Log.Объект(Ид = P.Идентификатор, Product = P)
        O.Записать()
        Seen = Find(P, D)
        если Crash
            выбросить новый ИсключениеВалидации("rollback")
        ;
    поймать E: ИсключениеВалидации
        возврат {"seen": Seen, "after": Find(P, D)}
    ;
    возврат {"seen": Seen, "after": Find(P, D)}
;
''')
            for sql in (False,True) if os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1' else (False,):
                for crash in (False,True):
                    c=check(s,'Persist',[REFS[0],'2026-09-30',crash],sql=sql)
                    r=(run_integration(c,analyze(root),temp,root=root,enabled=True) if sql else run_pure(root,analyze(root),c,temp))
                    write_json(EVIDENCE/f'handler-{sql}-{crash}.json',r);self.assertEqual(r['status'],'EXECUTED',r)
                    self.assertEqual(r['actual']['result'],{'seen':77,'after':5 if crash else 77})
                    exp=expected(c,{'seen':77,'after':5 if crash else 77})
                    if not crash:
                        changed=seed(s,REFS[0],None,[('2026-09-30',77,1)])
                        changed['rows'][0]['Link']=None;changed['rows'][0]['Note']='handler'
                        exp['storage'][4]=audit(changed)
                        exp['storage'].append({'type':'Data::Log','id':IDS[0],'value':{'Ссылка':REFS[0],'Product':REFS[0]}})
                    self.assertEqual(grade(r['actual'],exp,temp),'PASS')

    def test_public_test_run_control_batch_isolation_and_mutation(self):
        from element_test.bridge import run_test
        from element_test.batch import run_batch
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'one';other=temp/'two'
            shutil.copytree(CORPUS/'prices',root);shutil.copytree(root,other)
            result,package=run_test(root,REPO/'assignments/slices-control',EVIDENCE/'cli-control')
            self.assertEqual([c['status'] for c in result['checks']],['FAIL','UNSUPPORTED','PASS'])
            self.assertEqual(package['unavailablePoints'],1)
            manifest={'schemaVersion':'1.0','assignment':str(REPO/'assignments/slices-prices'),'assignmentId':'slices-33',
                      'submissions':[{'studentId':'one','project':str(root)},{'studentId':'two','project':str(other)}]}
            path=temp/'manifest.json';write_json(path,manifest)
            write_json(EVIDENCE/'batch-manifest.json',manifest)
            for mutation in (False,True):
                if mutation:
                    p=other/'Entry/Flow.xbsl';p.write_text(p.read_text().replace('X.Price КАК Value','X.Factor КАК Value'))
                label='batch-mutated' if mutation else 'batch'
                output=temp/label
                with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/'cache')}):
                    code=run_batch(path,output,2)
                shutil.copytree(output,EVIDENCE/label,dirs_exist_ok=True)
                self.assertEqual(code,1 if mutation else 0)
                scores=[json.loads((output/'submissions'/f'{n:06d}'/'grading.json').read_text())['score'] for n in (1,2)]
                self.assertEqual(scores,[3,1] if mutation else [3,3])
                inputs=EVIDENCE/(label+'-inputs');inputs.mkdir(exist_ok=True)
                shutil.copytree(root,inputs/'one',dirs_exist_ok=True)
                shutil.copytree(other,inputs/'two',dirs_exist_ok=True)
                saved=copy.deepcopy(manifest)
                for sub in saved['submissions']:sub['project']=str(inputs/sub['studentId'])
                write_json(EVIDENCE/(label+'-manifest.json'),saved)

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task 33 SQL opt-in')
class SliceSqlTest(unittest.TestCase):
    def test_memory_sql_parity_current_history_no_publication_and_failure_recovery(self):
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            temp=Path(d)
            for renamed in (False,True):
                s=schema(renamed);root=CORPUS/s['project']
                for method,args,result in [('All',['2026-09-30'],last_rows(s)),('Find',None,5),('Transaction',None,{'seen':77,'after':5})]:
                    c=check(s,method,args,sql=True)
                    r=run_integration(c,analyze(root),temp,root=root,enabled=True)
                    write_json(EVIDENCE/f'sql-{renamed}-{method}.json',r)
                    self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
                    self.assertEqual(normalized(r['actual']),normalized(expected(c,result)))
                    self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS')
                # Injected publication failure cannot affect a read-only method.
                c=check(s,sql=True);r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=True)
                self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(r['actual']['result'],5)
                c=check(s,'Reuse',sql=True)
                for failure in (True,False):
                    r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=failure)
                    write_json(EVIDENCE/f'sql-reuse-{renamed}-{failure}.json',r)
                    self.assertEqual(r['status'],'ERROR' if failure else 'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
                    if failure:self.assertEqual(normalized({'storage':r['storageEvidence']})['storage'],normalized(expected(c,None))['storage'])
                    else:self.assertEqual(grade(r['actual'],expected_reuse(s,c),temp),'PASS')

    def test_real_method_and_independent_sql_history_audit(self):
        with open_project(ARCHIVE) as root,TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            for missing in (False,True):
                c=real_check(missing=missing,sql=True)
                r=run_integration(c,analyze(root),Path(d),root=root,enabled=True,inject_failure=True)
                write_json(EVIDENCE/f'sql-real-{missing}.json',r)
                self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
                self.assertEqual(normalized(r['actual']),normalized(expected(c,None if missing else 5)))
                self.assertEqual(grade(r['actual'],expected(c,None if missing else 5),Path(d)),'PASS')
