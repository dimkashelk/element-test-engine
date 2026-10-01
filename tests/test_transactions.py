"""Task 30: independent evidence for source transaction and exception contracts."""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from element_test.execution_plan import plan_execution
from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure, execute_engine
from element_test.loader import open_project
from element_test.bridge import write_json
from element_test.integration import run_integration
from element_test.yaml_io import InputError
from transaction_fixtures import fixture, check, objects, ID, OTHER

REPO = Path(__file__).resolve().parent.parent
EVIDENCE = REPO/'result/transactions-and-business-exceptions'
ARCHIVE = REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump'


class TransactionPlanTest(unittest.TestCase):
    def test_ir_scopes_constructor_binding_and_no_answers(self):
        with TemporaryDirectory() as d:
            root=Path(d);s=fixture(root)
            c=check(s);c['expected']={'secret-teacher-answer':19}
            plan=plan_execution(root,analyze(root),c)
            ir=plan.to_dict()
            self.assertEqual(len(ir['resources']),1)
            r=ir['resources'][0]
            self.assertEqual((r['contract'],r['backend']),('lexical-source-transaction-v1','memory'))
            source=(root/r['source_file']).read_text()
            self.assertEqual(source[r['start']:r['end']].strip(),'исп Транзакции.Начать()')
            self.assertNotIn('expected',plan.to_json())
            self.assertNotIn('secret-teacher-answer',plan.to_json())
            self.assertTrue(any(b['operation']=='system-constructor-id' for b in ir['bindings']))
            self.assertEqual(ir['exceptionContracts'],['ИсключениеВалидации'])
            out=root/'out';out.mkdir();script=prepare_script(root,analyze(root),c,out).read_text()
            self.assertIn('ТестСессия.НачатьИсходную()',script)
            self.assertIn('ТестСессия.ОткатитьИсходную()',script)
            self.assertIn('ТестСессия.ЗавершитьИсходную()',script)
            self.assertIn('Peer.First',source)

    def test_nested_source_alias_owner_shadow_and_unreachable_boundary(self):
        with TemporaryDirectory() as d:
            root=Path(d);s=fixture(root);m=analyze(root)
            for method in ('Nested','Unsupported'):
                with self.assertRaisesRegex(InputError,'Вложенн|Именованн'):
                    plan_execution(root,m,check(s,method,[]))
            self.assertTrue(plan_execution(root,m,check(s)).resources)
            p=root/'Entry'/(s['main']+'.xbsl')
            p.write_text('метод Shadow(Транзакции: Строка): Число\n    исп Транзакции.Начать()\n    возврат 0\n;\n')
            with self.assertRaisesRegex(InputError,'Затенённый'):
                plan_execution(root,analyze(root),check(s,'Shadow',['x']))
            p.write_text('метод ReadType(): Строка\n    знч E = новый ИсключениеВалидации("x")\n    возврат E.ПолучитьТип().ВСтроку()\n;\n')
            with self.assertRaisesRegex(InputError,'Отражение'):
                plan_execution(root,analyze(root),check(s,'ReadType',[]))
            (root/'Entry/Транзакции.xbsl').write_text('@Глобально\nметод Начать(): Число\n    возврат 31\n;\n')
            p.write_text('метод ProjectOwner(): Число\n    возврат Транзакции.Начать()\n;\n')
            plan=plan_execution(root,analyze(root),check(s,'ProjectOwner',[]))
            self.assertEqual(plan.resources,[])
            self.assertTrue(any(b.category=='project' and b.operation=='Начать' for b in plan.bindings))
            p.write_text('метод Overload(): Число\n    выбросить новый ИсключениеВалидации("x", Истина)\n;\n')
            with self.assertRaisesRegex(InputError,'конструктор'):
                plan_execution(root,analyze(root),check(s,'Overload',[]))

    def test_system_uuid_requires_explicit_contract(self):
        with TemporaryDirectory() as d:
            root=Path(d);s=fixture(root);c=check(s);c['storage']['idType']='Строка'
            with self.assertRaisesRegex(InputError,'Системный Ид'):
                plan_execution(root,analyze(root),c)

    def test_catch_binding_name_does_not_replace_type_and_boundary_insert_order(self):
        with TemporaryDirectory() as d:
            root=Path(d);s=fixture(root)
            p=root/'Entry'/(s['main']+'.xbsl')
            source=p.read_text().replace('поймать E: ИсключениеВалидации','поймать Исключение: ИсключениеВалидации')
            # CatchResource returns directly; it never refers to that binding.
            p.write_text(source)
            plan=plan_execution(root,analyze(root),check(s,'CatchResource',[ID]))
            self.assertTrue(any(b.operation=='catch-propagation' for b in plan.bindings))
            out=root/'out';out.mkdir()
            script=prepare_script(root,analyze(root),check(s,'CatchResource',[ID]),out).read_text()
            self.assertLess(script.index('ТестСессия.ЗавершитьИсходную()'),script.index('поймать ТестНеперехвачено'))

    def test_ordinary_id_field_is_not_system_id(self):
        with TemporaryDirectory() as d:
            root=Path(d);s=fixture(root)
            obj=root/s['ns']/(s['obj']+'.yaml')
            obj.write_text(obj.read_text().replace('Реквизиты:\n','Реквизиты:\n  - {Имя: Ид, Тип: Число}\n',1))
            p=root/'Entry'/(s['main']+'.xbsl')
            p.write_text('метод Ordinary(): '+s['ns']+'::'+s['obj']+'.Объект\n    возврат новый '+s['ns']+'::'+s['obj']+'.Объект(Ид = 17)\n;\n')
            plan=plan_execution(root,analyze(root),check(s,'Ordinary',[]))
            self.assertFalse(any(b.operation=='system-constructor-id' for b in plan.bindings))


def expected_state(s, text='valid', number=2.5, identifier=ID, second=True, sql=False):
    def value(mult):
        return {'Ссылка':{'Идентификатор':identifier},s['label']:text,s['value']:number*mult,
                s['rows']:[{s['value']:number*mult}]}
    result=[{'type':s['ns']+'::'+s['obj'],'id':identifier,'value':value(1)}]
    if second:
        result += [{'type':s['ns']+'::'+s['reg'],'id':'{\n  "Key" : "bucket"\n}',
                    'value':[{'Key':'bucket',s['value']:number}]},
                   {'type':s['ns']+'::'+s['doc'],'id':identifier,'value':value(2)}]
    return sorted(result,key=lambda x:(x['type'],x['id'])) if sql else result


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Docker task 30 is opt-in')
class TransactionDockerTest(unittest.TestCase):
    def execute(self,root,c,temp,label):
        plans=[];r=run_pure(root,analyze(root),c,temp,plan_sink=plans)
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        write_json(EVIDENCE/(label+'.json'),{'execution':r,'plans':plans})
        self.assertEqual(r['status'],'EXECUTED',r)
        self.assertEqual(r['storageDiagnostics'],{'active':False,'locks':0})
        return r

    def grade(self,root,c,actual,expected,temp):
        write_json(temp/'model.json',analyze(root))
        write_json(temp/'assignment.json',{'checks':[{'id':'independent','type':'runtime','points':2,
                      'expected':expected,'execution':{'status':'EXECUTED','actual':actual}}]})
        return execute_engine('test',temp/'model.json',temp/'assignment.json',temp)['checks'][0]['status']

    def test_portable_atomicity_lifetime_dependencies_and_driver(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for renamed in (False,True):
                root=temp/('renamed' if renamed else 'accounts');s=fixture(root,renamed)
                for mode,second in ((0,True),(1,False),(3,True)):
                    c=check(s,args=[ID,'valid',2.5,mode])
                    c['trace']=mode == 0
                    r=self.execute(root,c,temp,f'portable-{renamed}-{mode}')
                    exp={'result':{'result':{'Идентификатор':ID},'exception':None},'storage':expected_state(s,second=second)}
                    self.assertEqual(self.grade(root,c,r['actual'],exp,temp),'PASS')
                    self.assertEqual(r['storageTrace'].count('source:commit'),1)
                    if mode == 0:
                        entered={t['symbol'].split('::')[-1] for t in r['trace'] if t['event']=='enter'}
                        self.assertTrue({'Apply','Validate','First','Second','ПослеЗаписи'} <= entered)
                for text,mode,message in (('valid',2,'late dependency'),('handler-error',0,'after-write'),('',0,'empty label')):
                    c=check(s,args=[ID,text,2.5,mode]);r=self.execute(root,c,temp,f'rollback-{renamed}-{mode}-{text or "empty"}')
                    self.assertEqual(r['actual'],{'result':{'result':None,'exception':{'type':'ИсключениеВалидации','message':message}},'storage':[]})
                c=check(s,transaction=True,args=[ID,'valid',2.5,2]);r=self.execute(root,c,temp,f'driver-{renamed}')
                self.assertEqual(r['actual']['storage'],[])
                self.assertEqual(r['storageTrace'],['driver:begin','source:begin','source:rollback','driver:rollback'])
                c=check(s);c['sequence']=[{'method':'Apply','args':[OTHER,'prior',7,1]},
                                        {'method':'Apply','args':[ID,'valid',2.5,2]}]
                r=self.execute(root,c,temp,f'prior-{renamed}')
                self.assertEqual(r['actual']['storage'],expected_state(s,'prior',7,OTHER,False))
                c=check(s,'Branch',[ID]);r=self.execute(root,c,temp,f'branch-{renamed}')
                self.assertEqual(r['actual']['storage'],expected_state(s,'branch',5,ID,False))
                self.assertEqual(r['actual']['result']['exception']['message'],'late dependency')
                c=check(s,args=[ID,'valid',2.5,2])
                preserved=expected_state(s,'prior',7,OTHER,False) + [expected_state(s,'neighbor',9,ID)[-1]]
                c['storage']['initial']=[{'type':x['type'],'value':x['value']} for x in preserved]
                r=self.execute(root,c,temp,f'preserved-neighbors-{renamed}')
                self.assertEqual(r['actual']['storage'],preserved)

    def test_catch_rethrow_lock_release_missing_and_same_uuid_type(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';s=fixture(root)
            for method,expected in (('General',{'result':'late dependency','exception':None}),
                                    ('Rethrow',{'result':None,'exception':{'type':'ИсключениеВалидации','message':'late dependency'}})):
                r=self.execute(root,check(s,method,[]),temp,'catch-'+method)
                self.assertEqual(r['actual']['result'],expected)
            c=check(s,'Locked',[{'Идентификатор':ID}]);r=self.execute(root,c,temp,'lock-missing')
            self.assertIsNone(r['actual']['result']['result']);self.assertIn('source:commit',r['storageTrace'])
            c=check(s);c['sequence']=[{'method':'Apply','args':[ID,'valid',2.5,0]},
                                     {'method':'Locked','args':[{'Идентификатор':ID}]}]
            r=self.execute(root,c,temp,'lock-existing')
            self.assertEqual(r['actual']['result']['actions'][1]['result'][s['value']],2.5)
            self.assertEqual(len(r['actual']['storage']),3)
            state=check(s,'ScopeState',[])
            state['sequence']=[{'method':'ScopeState','args':[]},{'method':'State','args':[]}]
            state_result=self.execute(root,state,temp,'state-release')
            self.assertEqual(state_result['actual']['result']['actions'],[{'result':True,'exception':None},{'result':False,'exception':None}])
            inactive=self.execute(root,check(s,'InactiveCatch',[{'Идентификатор':ID}]),temp,'inactive-catch')
            self.assertEqual(inactive['actual']['result'],{'result':'Блокировка требует активную транзакцию','exception':None})
            typed=self.execute(root,check(s,'TypeOps',[]),temp,'exception-union-cast-type')
            self.assertEqual(typed['actual']['result'],{'result':True,'exception':None})
            loop=self.execute(root,check(s,'Loop',[ID]),temp,'loop-break-continue')
            self.assertEqual(loop['actual']['storage'],expected_state(s,'loop',2,ID,False))
            self.assertEqual(loop['storageTrace'].count('source:commit'),2)
            for method,stored in (('TryResource',[]),('CatchResource',expected_state(s,'catch',5,ID,False))):
                scoped=self.execute(root,check(s,method,[ID]),temp,'scoped-'+method)
                self.assertEqual(scoped['actual']['storage'],stored)
                self.assertEqual(scoped['actual']['result'],{'result':'caught','exception':None})
            c['sequence'] += [{'method':'Lock','args':[{'Идентификатор':ID}]}]
            r=run_pure(root,analyze(root),c,temp)
            self.assertEqual(r['status'],'ERROR',r) # infrastructure/contract misuse is not a captured validation
            c=check(s,'Nested',[]);r=run_pure(root,analyze(root),c,temp)
            self.assertEqual(r['status'],'UNSUPPORTED',r)
            self.execute(root,check(s),temp,'after-unsupported')

    def test_independent_mutations_validation_id_record_catch_atomicity(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';s=fixture(root)
            main=root/'Entry'/(s['main']+'.xbsl');helper=root/'Service/Worker.xbsl'
            original_main,original_helper=main.read_text(),helper.read_text()
            baseline={'result':{'result':{'Идентификатор':ID},'exception':None},'storage':expected_state(s)}
            mutations=[('validation',original_main.replace('Peer.Validate(Text)',''),original_helper,[ID,'',2.5,0],
                        {'result':{'result':None,'exception':{'type':'ИсключениеВалидации','message':'empty label'}},'storage':[]}),
                       ('id',original_main,original_helper.replace('Ид = ID','Ид = новый Ууид("'+OTHER+'")'),None,baseline),
                       ('second',original_main.replace('    Peer.Second(ID, Text, N)\n',''),original_helper,None,baseline),
                       ('catch',original_main.replace('поймать E: ИсключениеВалидации','поймать E: ИсключениеНедопустимыйАргумент'),original_helper,[ID,'valid',2.5,3],baseline),
                       ('atomicity',original_main.replace('    исп Транзакции.Начать()\n',''),original_helper,[ID,'valid',2.5,2],
                        {'result':{'result':None,'exception':{'type':'ИсключениеВалидации','message':'late dependency'}},'storage':[]}),
                       ('dependency',original_main,original_helper.replace('N * 2','N * 3'),None,baseline)]
            for label,m,h,args,expected in mutations:
                main.write_text(m);helper.write_text(h);c=check(s,args=args)
                r=self.execute(root,c,temp,'mutation-'+label)
                self.assertEqual(self.grade(root,c,r['actual'],expected,temp),'FAIL',label)
            main.write_text(original_main);helper.write_text(original_helper)
            self.execute(root,check(s),temp,'after-mutations')
            unlocked=original_main.replace('Заблокировать = Истина','Заблокировать = Ложь')
            main.write_text(unlocked)
            c=check(s,'InactiveCatch',[{'Идентификатор':ID}]);r=self.execute(root,c,temp,'mutation-lock')
            expected={'result':{'result':'Блокировка требует активную транзакцию','exception':None},'storage':[]}
            self.assertEqual(self.grade(root,c,r['actual'],expected,temp),'FAIL')
            main.write_text(original_main)

    def test_real_archive_create_update_and_validation_independent_audit(self):
        with open_project(ARCHIVE) as root, TemporaryDirectory() as d:
            temp=Path(d)
            c={'target':{'module':'Покупатели','namespace':'РаботаСИсключениями','method':'СоздатьИлиОбновить'},
               'args':[ID,'Первый','+71234567890'],'captureException':True,'timeout':'15s','storage':{'idType':'Ууид'}}
            c['sequence']=[{'method':'СоздатьИлиОбновить','args':[ID,'Первый','+71234567890']},
                           {'method':'СоздатьИлиОбновить','args':[ID,'Второй','+79991234567']},
                           {'method':'СоздатьИлиОбновить','args':[OTHER,'','+71234567890']},
                           {'method':'СоздатьИлиОбновить','args':[OTHER,'Некорректный','123']}]
            r=self.execute(root,c,temp,'real-archive')
            actions=r['actual']['result']['actions']
            self.assertEqual(actions[:2],[{'result':{'Идентификатор':ID},'exception':None}]*2)
            self.assertEqual(actions[2],{'result':None,'exception':{'type':'ИсключениеВалидации','message':'Пустое ФИО покупателя'}})
            self.assertEqual(actions[3],{'result':None,'exception':{'type':'ИсключениеВалидации',
                   'message':'Некорректный номер телефона "123". Ожидаемый формат: "+7хххххххххх"'}})
            self.assertEqual(r['actual']['storage'],[{'type':'РаботаСИсключениями::Покупатели','id':ID,
                 'value':{'Ссылка':{'Идентификатор':ID},'Наименование':'Второй','Код':'','Телефон':'+79991234567'}}])


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','SQL task 30 is opt-in')
class TransactionSqlTest(unittest.TestCase):
    def test_source_boundaries_backend_failure_driver_and_independent_audit(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for renamed in (False,True):
                root=temp/str(renamed);s=fixture(root,renamed)
                for mode in (0,2,3):
                    c=check(s,args=[ID,'valid',2.5,mode],sql=True)
                    r=run_integration(c,analyze(root),temp,root=root,enabled=True)
                    write_json(EVIDENCE/f'sql-{renamed}-{mode}.json',r)
                    self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
                    self.assertEqual(r['storageDiagnostics'],{'active':False,'locks':0})
                    self.assertEqual(r['actual']['storage'],[] if mode==2 else expected_state(s,sql=True))
                c=check(s,sql=True);c['sequence']=[{'method':'Apply','args':[OTHER,'prior',7,1]},
                                                {'method':'Apply','args':[ID,'valid',2.5,2]}]
                r=run_integration(c,analyze(root),temp,root=root,enabled=True)
                self.assertEqual(r['actual']['storage'],expected_state(s,'prior',7,OTHER,False,sql=True),r)
                c=check(s,sql=True,transaction=True)
                r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=True)
                write_json(EVIDENCE/f'sql-failure-{renamed}.json',r)
                self.assertEqual(r['status'],'ERROR',r)
                self.assertEqual(r['storageEvidence'],[]);self.assertTrue(r['integration']['cleanup'])
                r=run_integration(c,analyze(root),temp,root=root,enabled=True)
                self.assertEqual(r['status'],'EXECUTED',r)
                self.assertEqual(r['actual']['storage'],expected_state(s,sql=True))
                seeded=check(s,sql=True)
                preserved=expected_state(s,'prior',7,OTHER,False,sql=True)
                seeded['storage']['initial']=[{'type':x['type'],'value':x['value']} for x in preserved]
                r=run_integration(seeded,analyze(root),temp,root=root,enabled=True,inject_failure=True)
                write_json(EVIDENCE/f'sql-seeded-failure-{renamed}.json',r)
                self.assertEqual(r['status'],'ERROR',r)
                self.assertEqual(r['storageEvidence'],preserved)
                self.assertTrue(r['integration']['cleanup'])
                swallowed=copy.deepcopy(seeded)
                swallowed['target']['method']='Swallow';swallowed['args']=[ID]
                r=run_integration(swallowed,analyze(root),temp,root=root,enabled=True,inject_failure=True)
                write_json(EVIDENCE/f'sql-swallowed-failure-{renamed}.json',r)
                self.assertEqual(r['status'],'ERROR',r)
                self.assertEqual(r['storageEvidence'],preserved)

    def test_real_archive_sql_creation_update_validation(self):
        with open_project(ARCHIVE) as root, TemporaryDirectory() as d:
            c={'target':{'module':'Покупатели','namespace':'РаботаСИсключениями','method':'СоздатьИлиОбновить'},
               'args':[ID,'Первый','+71234567890'],'captureException':True,
               'storage':{'backend':'postgres','idType':'Ууид'},'integration':{'backend':'postgres','operation':'metadata-storage'},
               'sequence':[{'method':'СоздатьИлиОбновить','args':[ID,'Первый','+71234567890']},
                           {'method':'СоздатьИлиОбновить','args':[ID,'Второй','+79991234567']},
                           {'method':'СоздатьИлиОбновить','args':[OTHER,'','+71234567890']},
                           {'method':'СоздатьИлиОбновить','args':[OTHER,'Некорректный','123']}]}
            r=run_integration(c,analyze(root),Path(d),root=root,enabled=True)
            write_json(EVIDENCE/'sql-real-archive.json',r)
            self.assertEqual(r['status'],'EXECUTED',r)
            self.assertEqual(r['actual']['storage'],[{'type':'РаботаСИсключениями::Покупатели','id':ID,
                'value':{'Ссылка':{'Идентификатор':ID},'Наименование':'Второй','Код':'','Телефон':'+79991234567'}}])
            self.assertEqual(r['actual']['result']['actions'][2]['exception']['type'],'ИсключениеВалидации')
            self.assertEqual(r['actual']['result']['actions'][3]['exception'],{
                'type':'ИсключениеВалидации',
                'message':'Некорректный номер телефона "123". Ожидаемый формат: "+7хххххххххх"'})
            self.assertTrue(r['integration']['cleanup'])
