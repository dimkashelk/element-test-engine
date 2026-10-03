"""Task 31: independent storage query expectations, original code runs in Docker."""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import shutil
import unittest
from unittest.mock import patch
import secrets

from element_test.execution_plan import plan_execution
from element_test.model import analyze
from element_test.query_plan import parse_storage_query, query_literals
from element_test.generated_types import ProjectTypes
from element_test.runtime import run_pure, prepare_script, execute_engine
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.bridge import write_json
from element_test.yaml_io import InputError, InvalidTestError

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO/'tests/corpus/storage-queries'
EVIDENCE = REPO/'result/storage-backed-queries'
ARCHIVE = REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump'
ID = '12345678-1234-4234-8234-123456789abc'
OTHER = 'abcdef12-1234-4234-8234-123456789abc'


def setUpModule():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
THIRD = '33333333-1234-4234-8234-123456789abc'


def schema(renamed=False):
    return ({'project':'document','ns':'Вход','module':'Пуск','owner':'Учет::Акт','label':'Тема','amount':'Сумма',
             'active':'Открыт','note':'Заметка','parent':'Основание',
             'methods':dict(zip(['Find','Fields','Asc','Desc','ByRef','Write','Commit','Rollback','Reuse','Side','Expressions','Detached'],
                               ['Искать','Поля','Возрастание','Убывание','ПоСсылке','ЗаписатьАкт','Фиксация','Откат','Повтор','Параметр','Выражения','Снимок']))}
            if renamed else {'project':'catalog','ns':'Entry','module':'Main','owner':'Data::Product','label':'Label',
              'amount':'Amount','active':'Active','note':'Note','parent':'Parent','methods':{}})


def row(s, id=ID, label='A', amount=3, active=False):
    return {'type':s['owner'],'id':id,'value':{'Ссылка':{'Идентификатор':id},s['label']:label,
           s['amount']:amount,s['active']:active,s['note']:None,s['parent']:None}}


def check(s, method='Find', args=None, rows=None, sql=False):
    c={'target':{'module':s['module'],'namespace':s['ns'],'method':s['methods'].get(method,method)},
       'args':['A'] if args is None else args,'storage':{'idType':'Ууид'},'timeout':'15s'}
    if rows is not None:c['storage']['initial']=[{'type':r['type'],'value':r['value']} for r in rows]
    if sql:
        c['storage']['backend']='postgres'
        c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c


class StorageQueryPlanTest(unittest.TestCase):
    def test_plan_binding_original_spans_aliases_and_no_expectations(self):
        for renamed in (False,True):
            s=schema(renamed);root=CORPUS/s['project'];c=check(s,'Fields',['A',3])
            c['expected']={'private-answer':987654321}
            p=plan_execution(root,analyze(root),c)
            self.assertNotIn('private-answer',p.to_json())
            self.assertEqual(len(p.queries),1)
            q=p.queries[0];self.assertEqual(q['ast']['owner'],s['owner'])
            source=(root/q['sourceFile']).read_text()
            self.assertTrue(source[q['start']:q['end']].startswith('Запрос{'))
            self.assertEqual(q['backend'],'memory')
            self.assertEqual(len(q['ast']['parameters']),2)
            self.assertEqual({v.identity.declaration for v in p.symbols},{s['methods'].get('Fields','Fields'),'A','B','C'})
            write_json(EVIDENCE/('plan-'+s['project']+'.json'),p.to_dict())

    def test_token_boundaries_nested_parameters_comments_and_rejections(self):
        s=schema();model=analyze(CORPUS/'catalog');c=ProjectTypes(model,'Entry',['Data::Product как Entity'])
        text='ВЫБРАТЬ P.Label КАК N /* ИЗ Fake */ ИЗ Data::Product КАК P ГДЕ P.Label = %{F({"x": "} % ИЗ ${G()}"})} И P.Label = %Name'
        q=parse_storage_query(text,c)
        self.assertEqual(len(q.parameters),2)
        self.assertEqual(q.parameters[0].expression,'F({"x": "} % ИЗ ${G()}"})')
        self.assertEqual(len(list(query_literals('метод F()\n    возврат Запрос{'+text+'}\n;'))),1)
        for bad in ['ВЫБРАТЬ Label ИЗ Data::Product ГДЕ Note = %N',
                    'ВЫБРАТЬ Label ИЗ Data::Product ГДЕ Label = %X И Amount = %X',
                    'ВЫБРАТЬ Label ИЗ Data::Product УПОРЯДОЧИТЬ ПО Note',
                    'ВЫБРАТЬ Unknown ИЗ Data::Product',
                    'ВЫБРАТЬ X.Label ИЗ Data::Product КАК P',
                    'ВЫБРАТЬ Label КАК N, Amount КАК N ИЗ Data::Product',
                    'ВЫБРАТЬ ПЕРВЫЕ 0 Label ИЗ Data::Product',
                    'ВЫБРАТЬ Label ИЗ Data::Product СОЕДИНЕНИЕ Data::Product',
                    'ВЫБРАТЬ КОЛИЧЕСТВО(Label) ИЗ Data::Product',
                    'ВЫБРАТЬ Label ИЗ Data::Product.Остатки']:
            with self.subTest(bad=bad),self.assertRaises(InputError):parse_storage_query(bad,c)

    def test_ambiguous_sources_wrong_parameter_type_shadow_and_modes(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'catalog',root)
            (root/'Other').mkdir();shutil.copy(root/'Data/Product.yaml',root/'Other/Product.yaml')
            p=root/'Entry/Main.xbsl'
            p.write_text('метод F(A: Число): Объект\n    возврат Запрос{ВЫБРАТЬ Label ИЗ Data::Product ГДЕ Label = %A}.Выполнить()\n;\n')
            with self.assertRaisesRegex(InputError,'Несовместимый'):plan_execution(root,analyze(root),check(schema(),'F',[2]))
            p.write_text('метод F(): Объект\n    возврат Запрос{ВЫБРАТЬ Label ИЗ Product}.Выполнить()\n;\n')
            with self.assertRaisesRegex(InputError,'неоднозначен'):plan_execution(root,analyze(root),check(schema(),'F',[]))
            p.write_text('метод F(Запрос: Строка): Объект\n    возврат Запрос{ВЫБРАТЬ Label ИЗ Data::Product}.Выполнить()\n;\n')
            with self.assertRaisesRegex(InputError,'Затенённый'):plan_execution(root,analyze(root),check(schema(),'F',['x']))
            (root/'Service/Source.xbsl').write_text('@Глобально\nметод Dummy(): Число\n    возврат 1\n;\n')
            p.write_text('импорт Service::Source как Запрос\nметод F(): Объект\n    возврат Запрос{ВЫБРАТЬ Label ИЗ Data::Product}.Выполнить()\n;\n')
            with self.assertRaisesRegex(InputError,'Затенённый'):plan_execution(root,analyze(root),check(schema(),'F',[]))
        c=check(schema());c['mocks']={'queries':[]}
        with self.assertRaises(InvalidTestError):plan_execution(CORPUS/'catalog',analyze(CORPUS/'catalog'),c)
        # An unsupported sibling does not contaminate the reachable closure.
        self.assertTrue(plan_execution(CORPUS/'catalog',analyze(CORPUS/'catalog'),check(schema())).queries)
        with self.assertRaises(InputError):plan_execution(CORPUS/'catalog',analyze(CORPUS/'catalog'),check(schema(),'Unavailable',[]))

    def test_reused_parameter_has_one_slot_and_all_source_ranges(self):
        s=schema();q=plan_execution(CORPUS/'catalog',analyze(CORPUS/'catalog'),check(s,'Reuse',[ID])).queries[0]['ast']
        self.assertEqual([p['slot'] for p in q['parameters']],[0,0])
        self.assertNotEqual(q['parameters'][0]['start'],q['parameters'][1]['start'])


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 31 Docker is opt-in')
class StorageQueryDockerTest(unittest.TestCase):
    def execute(self,root,c,temp,label):
        r=run_pure(root,analyze(root),c,temp)
        write_json(EVIDENCE/(label+'.json'),r)
        self.assertEqual(r['status'],'EXECUTED',r)
        return r

    def grade(self,root,c,actual,expected,temp):
        write_json(temp/'model.json',analyze(root));write_json(temp/'assignment.json',{'checks':[
            {'id':'independent','type':'runtime','points':1,'expected':expected,'execution':{'status':'EXECUTED','actual':actual}}]})
        return execute_engine('test',temp/'model.json',temp/'assignment.json',temp)['checks'][0]['status']

    def test_portable_fields_filters_order_limit_empty_single_reference_and_detachment(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for renamed in (False,True):
                s=schema(renamed);root=CORPUS/s['project'];rows=[row(s),row(s,OTHER,'B',1),row(s,THIRD,'C',2)]
                cases=[('Find',['A'],{'Идентификатор':ID}),('Find',['absent'],None),
                       ('Fields',['A',3],[{'Name':'A','Total':3,'Optional':None,'Link':None}]),
                       ('Fields',['A',4],[]),('Asc',[],[{'Name':'B','Total':1},{'Name':'C','Total':2}]),
                       ('Desc',[],[{'Name':'A'}]),('ByRef',[{'Идентификатор':ID}],3),
                       ('Detached',[],[{'Ссылка':{'Идентификатор':ID}}]),('Loop',[],['B','C','A'])]
                for index,(method,args,expected) in enumerate(cases):
                    seeds=rows[:1] if method=='Detached' else rows
                    r=self.execute(root,check(s,method,args,seeds),temp,f'memory-{renamed}-{index}')
                    self.assertEqual(r['actual']['result'],expected)
                    self.assertEqual(r['actual']['storage'],seeds)
                for method,args,seeds,result in [('Single',[],[],None),('Single',[],rows[:1],{s['label']:'A'})]:
                    c=check(s,method,args,seeds)
                    r=self.execute(root,c,temp,f'single-{renamed}-{len(seeds)}');self.assertEqual(r['actual']['result'],result)
                c=check(s,'Single',[],rows);c['captureException']=True
                r=self.execute(root,c,temp,f'multiple-{renamed}')
                self.assertIsNone(r['actual']['result']['result'])
                self.assertEqual(r['actual']['result']['exception']['type'],'Std::IllegalStateException')
                hostile='"} %{Crash()} ВЫБРАТЬ ИЗ \\ $value'
                hostile_row=row(s,label=hostile)
                r=self.execute(root,check(s,'Find',[hostile],[hostile_row]),temp,f'hostile-{renamed}')
                self.assertEqual(r['actual']['result'],{'Идентификатор':ID})

    def test_parameter_dependency_trace_multiplicity_and_independent_fail(self):
        s=schema()
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'catalog',root)
            for method,times in [('Side',1),('Expressions',2),('Fields',1)]:
                c=check(s,method,['A',3] if method=='Fields' else ['A'],[row(s)]);c['trace']=True
                r=self.execute(root,c,temp,'parameter-'+method)
                exits=[t for t in r['trace'] if t['event']=='exit' and t['symbol'].endswith('::C')]
                self.assertEqual(len(exits),times)
            for method,amount in [('Effects',1),('CapturedEffect',0)]:
                r=self.execute(root,check(s,method,['A'],[row(s)]),temp,'effects-'+method)
                self.assertEqual(r['actual']['result'],[{'Label':'A'}])
                self.assertEqual(r['actual']['storage'][1],row(s,OTHER,'effect',amount,True))
            for method,args in [('CapturedRef',[{'Идентификатор':ID}]),('Nested',['A'])]:
                r=self.execute(root,check(s,method,args,[row(s)]),temp,'captured-'+method)
                self.assertEqual(r['actual']['result'],[{'Label':'A'}])
            c=check(s,'Fields',['A',3],[row(s)])
            p=root/'Service/Parameters.xbsl';p.write_text(p.read_text().replace('возврат Value','возврат Value + "wrong"'))
            r=self.execute(root,c,temp,'wrong-dependency')
            self.assertEqual(self.grade(root,c,r['actual'],{'result':[{'Name':'A','Total':3,'Optional':None,'Link':None}],'storage':[row(s)]},temp),'FAIL')

    def test_source_transactions_handler_reuse_snapshot_and_wrapper(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for renamed in (False,True):
                s=schema(renamed);root=CORPUS/s['project']
                for wrapper in (False,True):
                    prior=row(s,OTHER,'prior',1)
                    c=check(s,'Commit',[ID],[prior]);c['storage']['transaction']=wrapper
                    c['sequence']=[{'method':s['methods'].get(m,m),'args':a} for m,a in [('Commit',[ID]),('Find',['commit']),('Rollback',[THIRD]),('Find',['rollback'])]]
                    r=self.execute(root,c,temp,f'transaction-{renamed}-{wrapper}')
                    self.assertEqual(r['actual']['result']['actions'],[{'Идентификатор':ID},{'Идентификатор':ID},'gone',None])
                    self.assertEqual(r['actual']['storage'],[prior,row(s,ID,'commit',7,True)])
                    self.assertEqual(r['storageTrace'].count('source:commit'),1)
                    self.assertEqual(r['storageTrace'].count('source:rollback'),1)
                    self.assertEqual(r['storageDiagnostics'],{'active':False,'locks':0})
                r=self.execute(root,check(s,'Reuse',[ID],[]),temp,f'reuse-{renamed}')
                self.assertEqual(r['actual']['result'],{'before':[],'after':[{s['label']:'new'}],'again':[{s['label']:'new'}],'empty':True})

    def test_file_failure_cannot_be_swallowed_resource_snapshot_and_recovery(self):
        import element_test.storage as storage
        s=schema();root=CORPUS/'catalog'
        with TemporaryDirectory() as d:
            temp=Path(d)
            c=check(s,'Swallow',[],[row(s)])
            native=storage.session_module
            def failing(*args,**kwargs):
                text=native(*args,**kwargs)
                return text.replace('исп Поток = Ф.ОткрытьПотокЧтения()',
                    'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("injected file read failure")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
            with patch('element_test.storage.session_module',side_effect=failing):
                r=run_pure(root,analyze(root),c,temp)
            write_json(EVIDENCE/'file-failure-swallowed.json',r)
            self.assertEqual(r['status'],'ERROR',r)
            def marker_failure(*args,**kwargs):
                text=native(*args,**kwargs)
                read='возврат СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Соответствие<Строка, Строка>>>(Поток, Пустой.ПолучитьТип())'
                text=text.replace(read,'знч Р = СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Соответствие<Строка, Строка>>>(Поток, Пустой.ПолучитьТип())\n    если Р.СодержитКлюч("Data::Product")\n        выбросить новый ИсключениеНедопустимоеСостояние("injected file failure")\n    ;\n    возврат Р',1)
                # Simulate inability to persist the marker, then successful
                # reads after recovery: driver/Audit alone cannot detect this.
                text=text.replace('исп Поток = новый Файл(Путь + ".failed").ОткрытьПотокЗаписи()\n    СериализацияJson.ЗаписатьОбъект(Поток, Истина)',
                    'СохранитьВсеВнутренний(новый Соответствие<Строка, Соответствие<Строка, Строка>>())')
                return text
            with patch('element_test.storage.session_module',side_effect=marker_failure):
                r=run_pure(root,analyze(root),c,temp)
            write_json(EVIDENCE/'file-failure-marker-unavailable.json',r)
            self.assertEqual(r['status'],'ERROR',r)
            r=self.execute(root,c,temp,'file-recovery')
            self.assertEqual(r['actual']['result'],'ok')
            r=self.execute(root,check(s,'Resource',[OTHER],[row(s)]),temp,'resource-snapshot')
            self.assertEqual(r['actual']['result'],[{'Label':'A'}])
            self.assertEqual(r['actual']['storage'],[row(s),row(s,OTHER,'later',1,True)])
            timeout=check(s,'Asc',[],[row(s)]);timeout['timeout']=0.001
            r=run_pure(root,analyze(root),timeout,temp)
            self.assertEqual(r['status'],'TIMEOUT',r)
            self.execute(root,c,temp,'after-timeout')

    def test_real_archive_unmodified_find_missing_wrong_article_and_uuid(self):
        with open_project(ARCHIVE) as root, TemporaryDirectory() as d:
            temp=Path(d);owner='РаботаСИсключениями::Номенклатура'
            stored={'type':owner,'value':{'Ссылка':{'Идентификатор':ID},'Наименование':'Учебный','Код':'01','Артикул':'SKU'}}
            for article,expected in [('SKU',{'Идентификатор':ID}),('absent',None)]:
                c={'target':{'module':'Номенклатура','namespace':'РаботаСИсключениями','method':'НайтиПоАртикулу'},
                   'args':[article],'storage':{'idType':'Ууид','initial':[stored]},'timeout':'15s'}
                r=self.execute(root,c,temp,'real-'+article)
                self.assertEqual(r['actual'],{'result':expected,'storage':[{'type':owner,'id':ID,'value':stored['value']}]})

    def test_colliding_owners_uuid_and_mutation_grading(self):
        s=schema()
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'catalog',root)
            (root/'Other').mkdir();shutil.copy(root/'Data/Product.yaml',root/'Other/Product.yaml')
            p=root/'Entry/Main.xbsl';original=p.read_text()
            original += '\nметод Other(): Объект\n    возврат Запрос{ВЫБРАТЬ Label ИЗ Other::Product}.Выполнить()\n;\n'
            p.write_text(original)
            other=row(s,label='neighbor');other['type']='Other::Product'
            c=check(s,'Find',['A'],[row(s),other]);c['sequence']=[{'method':'Find','args':['A']},{'method':'Other','args':[]}]
            r=self.execute(root,c,temp,'collision')
            self.assertEqual(r['actual']['result']['actions'],[{'Идентификатор':ID},[{'Label':'neighbor'}]])
            base=check(s,'Fields',['A',3],[row(s),row(s,OTHER,'B',4)])
            expected={'result':[{'Name':'A','Total':3,'Optional':None,'Link':None}],'storage':[row(s),row(s,OTHER,'B',4)]}
            mutations=[('predicate',' И P.Amount = %Amount',''),('parameter','%Amount','%{Amount + 1}'),
                       ('field','P.Label = %{Helper.A(Label)}','P.Note = %{Helper.A(Label)}')]
            # Unknown/unconfirmed field is honest UNSUPPORTED; valid but wrong logic grades FAIL.
            for label,a,b in mutations:
                p.write_text(original.replace(a,b))
                result=run_pure(root,analyze(root),base,temp)
                if label=='field':self.assertEqual(result['status'],'UNSUPPORTED')
                else:
                    # The predicate mutation only changes behavior if rows share Label.
                    if label=='predicate':
                        base2=copy.deepcopy(base);base2['storage']['initial'][1]['value']['Label']='A'
                        result=self.execute(root,base2,temp,'mutation-'+label)
                        exp=copy.deepcopy(expected);exp['storage'][1]['value']['Label']='A'
                    else:exp=expected
                    self.assertEqual(self.grade(root,base,result['actual'],exp,temp),'FAIL')
            p.write_text(original)
            bad=run_pure(root,analyze(root),check(s,'Unavailable',[]),temp)
            self.assertEqual((bad['status'],bad['reasonCode']),('UNSUPPORTED','unsupported_syntax'))
            self.execute(root,check(s),temp,'after-unsupported')

    def test_independent_projection_source_sort_limit_and_stale_mutations(self):
        s=schema()
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'catalog',root)
            (root/'Other').mkdir();shutil.copy(root/'Data/Product.yaml',root/'Other/Product.yaml')
            p=root/'Entry/Main.xbsl';original=p.read_text()
            rows=[row(s),row(s,OTHER,'B',1)]
            neighbor=row(s);neighbor['type']='Other::Product';neighbor['value']['Note']='neighbor'
            fields={'result':[{'Name':'A','Total':3,'Optional':None,'Link':None}],'storage':rows+[neighbor]}
            ordered={'result':[{'Name':'B','Total':1},{'Name':'A','Total':3}],'storage':rows}
            cases=[('projection','Fields',['A',3],rows+[neighbor],fields,'P.Label КАК Name','P.Note КАК Name'),
                   ('source','Fields',['A',3],rows+[neighbor],fields,'ИЗ Entity КАК P ГДЕ P.Label = %{Helper.A(Label)}','ИЗ Other::Product КАК P ГДЕ P.Label = %{Helper.A(Label)}'),
                   ('sort','Asc',[],rows,ordered,'ПО Total ВОЗР','ПО Total УБЫВ'),
                   ('limit','Asc',[],rows,ordered,'ПЕРВЫЕ 2','ПЕРВЫЕ 1'),
                   ('stale','Commit',[ID],[],{'result':{'Идентификатор':ID},'storage':[row(s,ID,'commit',7,True)]},
                    'Write(ID, "commit", 7)\n    возврат Find("commit")',
                    'знч Old = Find("commit")\n    Write(ID, "commit", 7)\n    возврат Old')]
            for label,method,args,seeds,expected,a,b in cases:
                p.write_text(original.replace(a,b))
                c=check(s,method,args,seeds)
                r=self.execute(root,c,temp,'mutation-'+label)
                self.assertEqual(self.grade(root,c,r['actual'],expected,temp),'FAIL',label)
            p.write_text(original)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task 31 PostgreSQL is opt-in')
class StorageQuerySqlTest(unittest.TestCase):
    def test_same_session_results_independent_sql_audit_failure_and_recovery(self):
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            temp=Path(d)
            for renamed in (False,True):
                s=schema(renamed);root=CORPUS/s['project'];prior=row(s,OTHER,'prior',1)
                c=check(s,'Commit',[ID],[prior],sql=True)
                c['sequence']=[{'method':s['methods'].get(m,m),'args':a} for m,a in [('Commit',[ID]),('Find',['commit']),('Rollback',[THIRD]),('Find',['rollback'])]]
                for failure in (False,True,False):
                    r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=failure)
                    write_json(EVIDENCE/f'sql-{renamed}-{failure}.json',r)
                    self.assertTrue(r['integration']['cleanup'],r)
                    if failure:
                        self.assertEqual(r['status'],'ERROR',r);self.assertEqual(r['storageEvidence'],[prior])
                    else:
                        self.assertEqual(r['status'],'EXECUTED',r)
                        self.assertEqual(r['actual']['result']['actions'],[{'Идентификатор':ID},{'Идентификатор':ID},'gone',None])
                        self.assertEqual(r['actual']['storage'],sorted([prior,row(s,ID,'commit',7,True)],key=lambda r:r['id']))
                # Read-only query does not cause a commit/publication failure.
                c=check(s,'Asc',[],[row(s),row(s,OTHER,'B',1)],sql=True)
                r=run_integration(c,analyze(root),temp,root=root,enabled=True)
                self.assertEqual(r['status'],'EXECUTED',r)
                self.assertEqual(r['actual']['result'],[{'Name':'B','Total':1},{'Name':'A','Total':3}])

    def test_real_archive_query_sql_and_independent_audit(self):
        with open_project(ARCHIVE) as root,TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            owner='РаботаСИсключениями::Номенклатура'
            value={'Ссылка':{'Идентификатор':ID},'Наименование':'Реальный','Код':'01','Артикул':'SKU'}
            for article,expected in [('SKU',{'Идентификатор':ID}),('missing',None)]:
                c={'target':{'module':'Номенклатура','namespace':'РаботаСИсключениями','method':'НайтиПоАртикулу'},'args':[article],
                   'storage':{'backend':'postgres','idType':'Ууид','initial':[{'type':owner,'value':value}]},
                   'integration':{'backend':'postgres','operation':'metadata-storage'}}
                r=run_integration(c,analyze(root),Path(d),root=root,enabled=True)
                write_json(EVIDENCE/('sql-real-'+article+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',r)
                self.assertEqual(r['actual'],{'result':expected,'storage':[{'type':owner,'id':ID,'value':value}]})
                self.assertTrue(r['integration']['cleanup'])
