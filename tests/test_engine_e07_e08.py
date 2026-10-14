"""E07/E08 acceptance with original Script methods and independent expected values."""
import copy
import json
import shutil
import os
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml

from element_test.execution_plan import plan_execution
from element_test.integration import run_integration
from element_test.model import analyze
from element_test.runtime import run_pure
from element_test.storage import PostgresSession
from element_test.yaml_io import InputError
from test_engine_e03_e04 import native
from test_report_navigation_panel import fixture as surface_fixture, config as surface_config, field, write

ID = '12345678-1234-4234-8234-123456789abc'
ID2 = '22345678-1234-4234-8234-123456789abc'


def ui_fixture(root, ns='Data'):
    write(root, 'Проект.yaml', {'Имя':'Transactions', 'РежимСовместимости':'9.0'})
    write(root, ns+'/Item.yaml', {'ВидЭлемента':'Справочник', 'Имя':'Item', 'ОбластьВидимости':'ВПроекте',
                                'Реквизиты':[{'Имя':'Amount', 'Тип':'Число'}]})
    write(root, ns+'/History.yaml', {'ВидЭлемента':'РегистрСведений', 'Имя':'History', 'ОбластьВидимости':'ВПроекте',
        'Измерения':[{'Имя':'Key', 'Тип':'Строка'}], 'Ресурсы':[{'Имя':'Amount', 'Тип':'Число'}]})
    write(root, ns+'/Ui.yaml', {'ВидЭлемента':'КомпонентИнтерфейса', 'Имя':'Ui', 'Свойства':[{'Имя':'Count','Тип':'Число'}], 'Наследует':{'Тип':'Форма','Содержимое':{'Тип':'Надпись','Имя':'Counter','Значение':'=Count'}}})
    (root/ns/'Ui.xbsl').write_text('''метод Save(Id: Ууид, Amount: Число)
    знч O = новый Item.Объект(Ид = Id, Amount = Amount)
    O.Записать()
;
метод Fail()
    выбросить новый ИсключениеНедопустимоеСостояние("scenario-failure")
;
''')
    (root/ns/'Item.Объект.xbsl').write_text('''метод ПослеЗаписи(До: Item.Данные, Параметры: Item.ПараметрыЗаписи)
    знч R = новый History.НаборЗаписей()
    R.Фильтр.Установить(Key = Ссылка.Идентификатор.ВСтроку())
    R.ДобавитьЗапись(Key = Ссылка.Идентификатор.ВСтроку(), Amount = Amount)
    R.Записать()
    если Amount < 0
        выбросить новый ИсключениеНедопустимоеСостояние("after-save")
    ;
;
''')


def ui_check(ns='Data', failure=True):
    return {'interface':{'schemaVersion':1,'form':{'name':'Ui', 'namespace':ns}},
        'storage':{'transaction':True, 'lifecycle':True, 'idType':'Ууид', 'registers':[ns+'::History']},
        'sequence':[{'method':'Save','args':[ID,3]}, {'method':'Save','args':[ID2,-1 if failure else 6]},
                    {'snapshotInterface':True}], 'trace':True}


def panel_check(failure=True, ns='Data'):
    c = surface_config('panel','Dashboard',ns)
    c['storage'].update(transaction=True, lifecycle=True)
    obj = {'type':ns+'::Incoming','value':{'Ссылка':{'Идентификатор':'three'},'Номер':'03', 'Amount':12, 'Дата':'2026-10-03'}}
    c['storage']['registers'].append(ns+'::History')
    record = {'type':ns+'::History','filter':{'Key':'three'},'rows':[{'Key':'three','Amount':12}]}
    second = copy.deepcopy(record)
    if failure: second['rows'][0]['Amount'] = -1
    else: second['rows'][0]['Amount'] = 15
    c['panelActions'] = [{'operation':'write','objects':[obj], 'registers':[record]},
                         {'advanceTime':30000}, {'operation':'write','registers':[second]},
                         {'operation':'refresh'}]
    return c


def panel_fixture(root, ns='Data'):
    surface_fixture(root, ns)
    write(root, ns+'/History.yaml', {'ВидЭлемента':'РегистрСведений', 'Имя':'History', 'ОбластьВидимости':'ВПроекте',
        'Измерения':[{'Имя':'Key', 'Тип':'Строка'}], 'Ресурсы':[{'Имя':'Amount', 'Тип':'Число','МинимальноеЗначение':0}]})


def constants_check(root):
    ui_fixture(root)
    write(root,'Data/Settings.yaml', {'ВидЭлемента':'НаборКонстант','Имя':'Settings', 'Константы':[{'Имя':'Amount','Тип':'Число'}]})
    write(root,'Data/ConstUi.yaml',{'ВидЭлемента':'КомпонентИнтерфейса','Имя':'ConstUi',
        'Наследует':{'Тип':'ФормаЗаписиНабораКонстант<Settings.Запись>', 'Содержимое':{
            'Тип':'ПолеВвода<Число>','Имя':'Amount','Значение':'=Запись.Amount'}}})
    (root/'Data/ConstUi.xbsl').write_text('метод Fail()\n    выбросить новый ИсключениеНедопустимоеСостояние("constants-failure")\n;\n')
    c={'interface':{'schemaVersion':1,'form':{'name':'ConstUi'}}, 'constants':{'Data::Settings':{'Amount':2}},
        'storage':{'transaction':True}, 'formEffects':{'write':True},
        'sequence':[{'input':{'selector':{'name':'Amount'},'value':7}}, {'writeInterface':True}, {'method':'Fail','args':[]}]}
    return c


def sort_fixture(root, ns='Data', id_type='Строка'):
    surface_fixture(root,ns)
    # Deliberately reverse the union declaration: owner order must be independent.
    write(root, ns+'/Cards.yaml', {'ВидЭлемента':'Документ', 'Имя':'Cards', 'ОбластьВидимости':'ВПроекте',
        'Реквизиты':[{'Имя':'Link','Тип':'Outgoing.Ссылка|Incoming.Ссылка|?'}, {'Имя':'Label','Тип':'Строка'}]})
    write(root, ns+'/Sorted.yaml', {'ВидЭлемента':'Отчет','Имя':'Sorted','ОбластьВидимости':'ВПроекте',
        'ИсточникДанных':'Cards', 'Макет':{'Поля':[field('link','Link',НаправлениеСортировки='ПоВозрастанию'),field('label','Label')]}})
    def ref(owner, identifier): return {'type':ns+'::'+owner+'.Ссылка','value':{'Идентификатор':identifier}}
    one, two = ('a','b') if id_type == 'Строка' else (ID,ID2)
    links = [(ref('Outgoing',one),'out'),(ref('Incoming',two),'b'),(ref('Incoming',one),'a1'),
             (ref('Incoming',one),'a2'),(None,'null')]
    c = surface_config(name='Sorted',namespace=ns)
    c['storage'] = {'idType':id_type,'initial':[{'type':ns+'::Cards','value':{
        'Ссылка':{'Идентификатор':str(i) if id_type=='Строка' else f'{i+1:08d}-1234-4234-8234-123456789abc'},
        'Link':link,'Label':label}} for i,(link,label) in enumerate(links)]}
    return c


class SurfaceTransactionTest(unittest.TestCase):
    def test_ui_original_hooks_commit_rollback_and_fresh_session(self):
        for ns in ('Data','Учет'):
            with self.subTest(ns=ns), TemporaryDirectory() as d:
                base=Path(d);root=base/'source';ui_fixture(root,ns);model=analyze(root)
                for failure in (True,False):
                    c=ui_check(ns,failure);r=native(root,model,c,base)
                    result=r['actual']['result']
                    self.assertEqual(result['transaction']['outcome'],'rolledBack' if failure else 'committed',r)
                    self.assertEqual(len(result['actions']),1 if failure else 3)
                    self.assertEqual(r['storageDiagnostics'],{'active':False,'locks':0})
                    self.assertIn('driver:rollback' if failure else 'driver:commit',r['storageTrace'])
                    if failure:
                        self.assertEqual(r['actual']['storage'],[])
                        self.assertEqual(result['transaction']['exception']['message'],'after-save')
                    else:
                        items=[v['value']['Amount'] for v in r['actual']['storage'] if v['type']==ns+'::Item']
                        self.assertEqual(items,[3,6])
                        self.assertEqual(len(r['actual']['storage']),4)
                        self.assertEqual([v['value'][0]['Amount'] for v in r['actual']['storage'] if v['type']==ns+'::History'],[3,6])
                # A new execution must not inherit the preceding committed case.
                self.assertEqual(native(root,model,ui_check(ns),base)['actual']['storage'],[])

    def test_validation_type_and_source_catch_boundary(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';ui_fixture(root)
            p=root/'Data/Ui.xbsl';p.write_text(p.read_text()+
                'метод Validate()\n    выбросить новый ИсключениеВалидации("validation-failure")\n;\n'
                'метод Caught()\n    попытка\n        выбросить новый ИсключениеНедопустимоеСостояние("caught")\n'
                '    поймать E: Исключение\n        Count += 1\n    ;\n;\n')
            c=ui_check(failure=False);c['sequence'][1]={'method':'Validate','args':[]}
            r=native(root,analyze(root),c,base)
            self.assertEqual(r['actual']['storage'],[])
            self.assertEqual(r['actual']['result']['transaction']['exception'],{'type':'ИсключениеВалидации','message':'validation-failure'})
            c['sequence'][1]={'method':'Caught','args':[]}
            r=native(root,analyze(root),c,base)
            self.assertEqual(r['actual']['result']['transaction'],{'outcome':'committed','exception':None})
            self.assertEqual(len(r['actual']['storage']),2)

    def test_constants_without_lifecycle_are_inside_boundary(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source'
            c=constants_check(root)
            r=native(root,analyze(root),c,base)
            self.assertEqual(r['actual']['storage'],[{'type':'Data::Settings','id':'constants','value':{'Amount':2}}])
            self.assertEqual(r['actual']['result']['actions'][1]['observation']['constants'],{'Data::Settings':{'Amount':7}})
            c['sequence'].pop();r=native(root,analyze(root),c,base)
            self.assertEqual(r['actual']['storage'][0]['value']['Amount'],7)

    def test_panel_writes_timer_reads_rollback_and_success(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';panel_fixture(root)
            for failure in (True,False):
                r=native(root,analyze(root),panel_check(failure),base)
                actions=r['actual']['result']['actions']
                self.assertEqual(r['actual']['result']['transaction']['outcome'],'rolledBack' if failure else 'committed')
                self.assertEqual(len(actions),2 if failure else 4)
                self.assertEqual(actions[1]['observation']['revision'],2)
                self.assertEqual(actions[0]['observation']['revision'],1)
                self.assertEqual(actions[1]['observation']['widgets']['entries']['cardinality'],3)
                self.assertEqual(len(r['actual']['storage']),2 if failure else 4)
                self.assertEqual({v['id']:v['value']['Amount'] for v in r['actual']['storage'] if v['type']=='Data::Incoming'},
                                 {'one':3,'two':8} if failure else {'one':3,'two':8,'three':12})
                if not failure:
                    self.assertEqual(next(v['value'] for v in r['actual']['storage'] if v['type']=='Data::History'),[{'Key':'three','Amount':15}])
                self.assertEqual(r['storageDiagnostics'],{'active':False,'locks':0})

    def test_direct_write_rejection_stops_transaction_and_preserves_snapshots(self):
        from test_metadata_lifecycle import direct_fixture
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';root.mkdir();c=direct_fixture(root)
            # Constant identity makes the saved object observable independently.
            c['context']['Объект']['Ссылка']={'Идентификатор':ID};c['storage']['transaction']=True
            p=root/'Data/Panel.yaml';meta=yaml.safe_load(p.read_text());meta['Наследует']['Содержимое']['Тип']='ПолеВвода<Строка>';write(root,'Data/Panel.yaml',meta)
            c['sequence']=[{'writeInterface':True},{'input':{'selector':{'name':'Title'},'value':'too-long'}},{'writeInterface':True},{'snapshotInterface':True}]
            r=native(root,analyze(root),c,base)
            self.assertEqual(r['actual']['storage'],[])
            actions=r['actual']['result']['actions']
            self.assertEqual([a['outcome'] for a in actions],['applied','applied','rejected'])
            self.assertEqual(actions[0]['observation']['state']['Объект.Title'],'saved!')
            self.assertEqual(r['actual']['result']['transaction']['outcome'],'rolledBack')


class ReportUnionSortTest(unittest.TestCase):
    def test_nominal_refs_same_id_null_stable_ties_both_directions(self):
        for ns,id_type in [('Data','Строка'),('Учет','Ууид')]:
            with self.subTest(ns=ns,id_type=id_type), TemporaryDirectory() as d:
                base=Path(d);root=base/'source';c=sort_fixture(root,ns,id_type)
                for descending in (False,True):
                    p=root/ns/'Sorted.yaml';meta=yaml.safe_load(p.read_text());meta['Макет']['Поля'][0]['НаправлениеСортировки']='ПоУбыванию' if descending else 'ПоВозрастанию';write(root,ns+'/Sorted.yaml',meta)
                    r=native(root,analyze(root),c,base);values=r['actual']['result']['actions'][0]['values']
                    self.assertEqual([v['label'] for v in values],['out','b','a1','a2','null'] if descending else ['null','a1','a2','b','out'])
                    self.assertEqual(values[0 if descending else -1]['link']['type'],ns+'::Outgoing.Ссылка')
                    self.assertEqual(plan_execution(root,analyze(root),c).report['sortingContract']['ties'],'stable')

    def test_mixed_scalars_nullable_date_bool_and_secondary_keys(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';surface_fixture(root)
            meta=yaml.safe_load((root/'Data/Entries.yaml').read_text())
            expression='ЕСЛИ(Amount = 0, Option, ЕСЛИ(Amount = 1, Amount > 0, ЕСЛИ(Amount = 2, Amount, ЕСЛИ(Amount = 3, Number, Date))))'
            meta['Макет']['Поля']=[field('value',expression,НаправлениеСортировки='ПоВозрастанию'),field('number','Number',НаправлениеСортировки='ПоУбыванию')]
            write(root,'Data/Entries.yaml',meta)
            c=surface_config();c['storage']['initial']=[{'type':'Data::Incoming','value':{'Ссылка':{'Идентификатор':str(i)},'Amount':amount,'Номер':number,'Дата':'2026-10-01','Option':None}}
                for i,(amount,number) in enumerate([(4,'date'),(2,'a'),(0,'null'),(3,'text'),(1,'bool'),(2,'b')])]
            r=native(root,analyze(root),c,base)
            self.assertEqual([v['number'] for v in r['actual']['result']['actions'][0]['values']],['null','bool','b','a','text','date'])
            meta['Макет']['Поля'][0]['НаправлениеСортировки']='ПоУбыванию';write(root,'Data/Entries.yaml',meta)
            r=native(root,analyze(root),c,base)
            self.assertEqual([v['number'] for v in r['actual']['result']['actions'][0]['values']],['date','text','b','a','bool','null'])

    def test_all_scalar_kinds_and_declared_enum_order(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';surface_fixture(root)
            write(root,'Data/Status.yaml', {'ВидЭлемента':'Перечисление','Имя':'Status','ОбластьВидимости':'ВПроекте',
                'Элементы':[{'Имя':'Z','ПоУмолчанию':True},{'Имя':'A'}]})
            p=root/'Data/Incoming.yaml';meta=yaml.safe_load(p.read_text())
            types=[('Flag','Булево',False),('Clock','Время','12:00:00'),('Stamp','ДатаВремя','2026-10-01T12:00:00'),
                   ('Instant','Момент','2026-10-01T12:00:00Z'),('Uid','Ууид',ID),('State','Status','Z')]
            meta['Реквизиты'].extend({'Имя':n,'Тип':t} for n,t,_ in types);write(root,'Data/Incoming.yaml',meta)
            fields=['Flag','Amount','Number','Date','Clock','Stamp','Instant','Uid','State']
            expr='State'
            for i in reversed(range(len(fields)-1)):
                expr='ЕСЛИ(Amount = '+str(i)+', '+fields[i]+', '+expr+')'
            p=root/'Data/Entries.yaml';meta=yaml.safe_load(p.read_text());meta['Макет']['Поля']=[field('value',expr,НаправлениеСортировки='ПоВозрастанию'),field('label','Number')];write(root,'Data/Entries.yaml',meta)
            c=surface_config();c['storage']['initial']=[]
            for i in reversed(range(10)):
                v={'Ссылка':{'Идентификатор':str(i)},'Amount':min(i,8),'Номер':str(i),'Дата':'2026-10-01','Option':None,
                   **{n:value for n,t,value in types}}
                if i==9:v['State']='A'
                c['storage']['initial'].append({'type':'Data::Incoming','value':v})
            r=native(root,analyze(root),c,base)
            self.assertEqual([v['label'] for v in r['actual']['result']['actions'][0]['values']],[str(i) for i in range(10)])

    def test_unsupported_sort_type_remains_unavailable(self):
        from element_test.generated_types import ProjectTypes
        from element_test.report_sorting import comparison
        with TemporaryDirectory() as d:
            root=Path(d);surface_fixture(root)
            c=ProjectTypes(analyze(root),'Data',[])
            with self.assertRaisesRegex(InputError,'Сортировка макета вне контракта'):
                comparison(c,'Объект','A','B',True)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Public batch Docker opt-in')
class SurfacePublicTest(unittest.TestCase):
    def test_public_pass_fail_unavailable_and_batch_storage_isolation(self):
        from element_test.bridge import run_test
        from element_test.batch import run_batch
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';ui_fixture(root)
            p=root/'Data/Ui.xbsl';p.write_text(p.read_text().replace('    знч O =',
                '    если (новый Item.Ссылка(Id)).ЗагрузитьОбъект() != Неопределено\n'
                '        выбросить новый ИсключениеНедопустимоеСостояние("session-leak")\n    ;\n    знч O ='))
            writer=ui_check(failure=False);reject=ui_check()
            writer_expected=native(root,analyze(root),writer,base)['actual']
            reject_expected=native(root,analyze(root),reject,base)['actual']
            # Assert business expectations before using the complete observed envelope.
            self.assertEqual([v['value']['Amount'] for v in writer_expected['storage'] if v['type']=='Data::Item'],[3,6])
            self.assertEqual(reject_expected['storage'],[])
            self.assertEqual(reject_expected['result']['transaction']['exception']['message'],'after-save')
            good={**writer,'id':'commit','type':'runtime','points':2,'expected':writer_expected}
            rollback={**reject,'id':'rollback','type':'runtime','points':2,'expected':reject_expected}
            bad=copy.deepcopy(good);bad['id']='wrong';bad['expected']['storage'][0]['value']['Amount']=777
            unavailable=copy.deepcopy(good);unavailable['id']='unavailable';unavailable['sequence'][0]['method']='Missing'
            assignment=base/'assignment.yaml';write(base,'assignment.yaml',{'name':'E07 public','checks':[good,rollback,bad,unavailable]})
            result,_=run_test(root,assignment,base/'public')
            self.assertEqual([v['status'] for v in result['checks']],['PASS','PASS','FAIL','UNSUPPORTED'],result)
            second=base/'second';shutil.copytree(root,second)
            manifest=base/'batch.json';manifest.write_text(json.dumps({'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'e07',
                'submissions':[{'studentId':'first','project':str(root)},{'studentId':'second','project':str(second)}]}))
            self.assertEqual(run_batch(manifest,base/'batch',workers=2),1)
            batch=json.loads((base/'batch/batch-result.json').read_text())
            for row in batch['submissions']:
                result=json.loads((base/'batch'/row['result']).read_text())
                self.assertEqual([v['status'] for v in result['checks']],['PASS','PASS','FAIL','UNSUPPORTED'],result)
                self.assertEqual((result['score'],result['unavailablePoints']),(4,2))


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Docker/PostgreSQL opt-in')
class SurfaceTransactionSqlTest(unittest.TestCase):
    def test_unavailable_ui_action_rolls_back_prior_write_without_pass(self):
        from test_interface_actions import fixture as grid_fixture, config as grid_config
        with TemporaryDirectory() as d, patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            base=Path(d);root=base/'source';grid_fixture(root)
            c=grid_config();c.update(storage={'backend':'postgres','transaction':True,'lifecycle':True,'idType':'Ууид'},
                formEffects={'write':True},integration={'backend':'postgres','operation':'metadata-storage'})
            c['context']['Объект']['Ссылка']={'Идентификатор':ID}
            c['sequence']=[{'writeInterface':True},{'input':{'selector':{'name':'Amount'},'row':99,'value':777}}]
            r=run_integration(c,analyze(root),base,root=root,enabled=True)
            self.assertEqual(r['status'],'UNSUPPORTED',r)
            self.assertEqual(r['storageEvidence'],[])
            self.assertTrue(r['integration']['cleanup'])

    def test_constants_without_lifecycle_independent_sql_rollback(self):
        with TemporaryDirectory() as d, patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            base=Path(d);root=base/'source';c=constants_check(root)
            c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
            for failure in (True,False):
                if not failure:c['sequence'].pop()
                r=run_integration(c,analyze(root),base,root=root,enabled=True)
                self.assertEqual(r['status'],'EXECUTED',r)
                self.assertEqual(r['actual']['storage'],[{'type':'Data::Settings','id':'constants','value':{'Amount':2 if failure else 7}}])
                self.assertTrue(r['integration']['cleanup'])

    def test_both_surfaces_independent_sql_audit_commit_rollback(self):
        for kind in ('ui','panel'):
            with self.subTest(kind=kind), TemporaryDirectory() as d, patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
                base=Path(d);root=base/'source';(ui_fixture if kind=='ui' else panel_fixture)(root)
                for failure in (True,False):
                    c=ui_check(failure=failure) if kind=='ui' else panel_check(failure)
                    c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
                    audited=[];original=PostgresSession.audit
                    def audit(session,database,docker):
                        v=original(session,database,docker);audited.append(copy.deepcopy(v));return v
                    with patch.object(PostgresSession,'audit',audit):
                        r=run_integration(c,analyze(root),base,root=root,enabled=True)
                    self.assertEqual(r['status'],'EXECUTED',r)
                    self.assertEqual(audited[-1],r['actual']['storage'])
                    self.assertEqual(len(audited[-1]),(0 if kind=='ui' else 2) if failure else 4)
                    self.assertEqual(r['actual']['result']['transaction']['outcome'],'rolledBack' if failure else 'committed')
                    self.assertTrue(r['integration']['cleanup'])
                    if kind=='panel':
                        self.assertEqual({v['id']:v['value']['Amount'] for v in audited[-1] if v['type']=='Data::Incoming'},
                                         {'one':3,'two':8} if failure else {'one':3,'two':8,'three':12})
                        if not failure:
                            self.assertEqual(next(v['value'] for v in audited[-1] if v['type']=='Data::History'),[{'Key':'three','Amount':15}])
                    elif not failure:
                        self.assertEqual({v['id']:v['value']['Amount'] for v in audited[-1] if v['type']=='Data::Item'},{ID:3,ID2:6})
                        self.assertEqual([v['value'][0]['Amount'] for v in audited[-1] if v['type']=='Data::History'],[3,6])
