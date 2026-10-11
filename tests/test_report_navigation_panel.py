"""Stage 51 mechanism tests; final-dump declarations are prepared, not graded."""
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
from element_test.loader import open_project
from element_test.model import analyze
from element_test.report_formulas import parse_formula
from element_test.report_runtime import surface_options_schema
from element_test.runtime import prepare_script, run_pure
from element_test.yaml_io import InputError

REPO = Path(__file__).resolve().parents[1]


def write(root, path, data):
    target = root/path; target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False))


def field(id, expression, **extra):
    return {'Ид':id,'Вид':'Измерение','Выражение':expression,'Использовать':True,'ВизуальныеРоли':['Строки'],**extra}


def fixture(root, namespace='Data'):
    write(root,'Проект.yaml',{'Имя':'Mechanism51','Поставщик':'Tests','РежимСовместимости':'9.0'})
    write(root,namespace+'/Подсистема.yaml',{'ВидЭлемента':'Подсистема','Имя':namespace})
    for name in ('Incoming','Outgoing'):
        write(root,namespace+'/'+name+'.yaml',{'ВидЭлемента':'Документ','Имя':name,'ОбластьВидимости':'ВПроекте',
            'Реквизиты':[{'Имя':'Номер','Тип':'Строка'}, {'Имя':'Amount','Тип':'Число'}, {'Имя':'Дата','Тип':'Дата'}, {'Имя':'Option','Тип':'Число?'}]})
    write(root,namespace+'/Ledger.yaml',{'ВидЭлемента':'РегистрНакопления','Имя':'Ledger','ОбластьВидимости':'ВПодсистеме',
        'Ресурсы':[{'Имя':'Amount','Тип':'Число'}],
        'Реквизиты':[{'Имя':'Регистратор','Тип':'Incoming.Ссылка|Outgoing.Ссылка'}]})
    write(root,namespace+'/Entries.yaml',{'ВидЭлемента':'Отчет','Имя':'Entries','ОбластьВидимости':'ВПроекте',
        'ИсточникДанных':'Incoming','Макет':{'ВидОтображения':'СводнаяТаблица','Поля':[
            field('number','Number',НаправлениеСортировки='ПоВозрастанию'), field('value','Amount * 2'),
            field('option','Option',НаправлениеСортировки='ПоУбыванию')]}})
    write(root,namespace+'/Movements.yaml',{'ВидЭлемента':'Отчет','Имя':'Movements','ОбластьВидимости':'ВПроекте',
        'ИсточникДанных':'Ledger','Макет':{'Поля':[field('document','ЕСЛИ(RecordKind = ССЫЛКА("Receipt", "Enum.Std::AccumulationRegisters::AccumulationRegisterRecordKind", "Приход"), "in:" + Recorder.Number, "out:" + Recorder.Number)'),
        field('value','ЕСЛИ(RecordKind = ССЫЛКА("Receipt", "Enum.Std::AccumulationRegisters::AccumulationRegisterRecordKind", "Приход"), Amount, "")')]}})
    write(root,namespace+'/Query.yaml',{'ВидЭлемента':'Отчет','Имя':'Query','ОбластьВидимости':'ВПроекте',
        'ВидИсточникаДанных':'Запрос','Макет':{'Поля':[field('value','Total')]}})
    (root/namespace/'Query.xbql').write_text('ВЫБРАТЬ Amount + &N КАК Total ИЗ Incoming')
    write(root,namespace+'/Menu.yaml',{'ВидЭлемента':'ФрагментКомандногоИнтерфейса','Имя':'Menu','ОбластьВидимости':'ВПроекте',
        'Элементы':[{'Тип':'ГруппаКомандногоИнтерфейса','Представление':'Reports','Видимость':'=Show','Элементы':[
            {'Тип':'НавигационнаяКоманда','Представление':'Entries','ТипФормы':'Entries.АвтоматическаяФорма'}]},
            {'Тип':'НавигационнаяКоманда','Представление':'Ledger','ТипФормы':'Incoming.АвтоматическаяФормаСписка'},
            {'Тип':'НавигационнаяКоманда','Представление':'Console','ТипФормы':'ФормаКонсолиЗапросов'}]})
    write(root,namespace+'/Dashboard.yaml',{'ВидЭлемента':'ПанельОтчетов','Имя':'Dashboard','ОбластьВидимости':'ВПроекте',
        'Макет':{'АвтоматическоеОбновление':{'Использовать':True,'Вид':'Интервал','ЕдиницаВремени':'Секунда','Значение':30},
            'Виджеты':[{'Ид':'entries','Вид':'Отчет','Отчет':namespace+'::Entries'}, {'Ид':'movements','Вид':'Отчет','Отчет':namespace+'::Movements'}],
            'НастройкиРасположения':{'Настольный':[{'ИдВиджета':'entries','X':0,'Y':0,'Ширина':20,'Высота':10}]}}})


def config(kind='report',name='Entries',namespace='Data'):
    c = {kind:{'schemaVersion':1,kind:{'name':name,'namespace':namespace}}, 'runtimeProfile':'9.3'}
    if kind == 'navigation':
        c['navigationContext']={'Show':True}; return c
    c['storage']={'registers':[namespace+'::Ledger'], 'initial':[
        {'type':namespace+'::Incoming','value':{'Ссылка':{'Идентификатор':'one'},'Номер':'02','Amount':3,'Дата':'2026-10-01','Option':None}},
        {'type':namespace+'::Incoming','value':{'Ссылка':{'Идентификатор':'two'},'Номер':'01','Amount':8,'Дата':'2026-10-02','Option':4}}]}
    if kind == 'panel':
        c.update({'timers':{'onClose':'cancel'},'executorLocale':'en-US',
            'clock':{'mode':'fixed','date':'2026-10-11','time':'12:00:00','timezone':'UTC'}})
    return c


class SurfaceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name); self.root = self.base/'project'; fixture(self.root)

    def plan(self,c=None): return plan_execution(self.root,analyze(self.root),c or config())


class SurfacePlanTest(SurfaceFixture):
    def test_formula_ir_is_distinct_and_rejects_unknown_language(self):
        for text in ('=Amount','Amount.ВСтроку()','SUM(Amount)','__import__("os")','Amount; Other', 'Amount +'):
            with self.subTest(text=text), self.assertRaises(InputError): parse_formula(text)
        ast = parse_formula('ЕСЛИ(Amount = 0, "empty", Amount * 2)')
        self.assertEqual(ast.kind,'ЕСЛИ'); self.assertEqual(ast.children[0].range,(5,15))

    def test_report_plan_has_source_provenance_types_and_no_oracle(self):
        c=config(); c['expected']={'SECRET_ORACLE':31991}; p=self.plan(c)
        self.assertNotIn('SECRET_ORACLE',p.to_json())
        self.assertEqual(p.report['sourceKind'],'Таблица')
        self.assertEqual(p.query_entry['kind'],'metadata-table')
        self.assertEqual(p.report['fields'][0]['outputType'],'Строка')
        self.assertEqual(p.report['stages']['execution'],'not-checked')
        a,b=p.report['fields'][0]['position']['range']
        self.assertIn('Number',(self.root/'Data/Entries.yaml').read_text()[a:b])
        prepare_script(self.root,analyze(self.root),c,self.base)
        source=(self.base/'test.sbsl').read_text()
        self.assertIn('.Evaluate(',source); self.assertNotIn('"rows": Строки',source)

    def test_invalid_selectors_actions_schema_and_unsupported_formulas(self):
        for key,value in [('target',{}),('reportActions',[]),('reportActions',[{'operation':'execute','rows':[]}]),('reportActions',[{'operation':'write','objects':[]}]),('timers',{'onClose':'cancel'})]:
            c=config();c[key]=value
            with self.subTest(key=key,value=value), self.assertRaises(InputError):self.plan(c)
        c=config();c['report']['report']['sourceHash']='a'*64
        with self.assertRaises(InputError):self.plan(c)
        path=self.root/'Data/Entries.yaml';data=yaml.safe_load(path.read_text());data['Макет']['Поля'][0]['Выражение']='Missing'
        write(self.root,'Data/Entries.yaml',data)
        with self.assertRaisesRegex(InputError,'Неизвестное поле'):self.plan()
        from element_test.test_contracts import validate_schema
        for kind in ('report','navigation','panel'):
            validate_schema(config(kind,{'report':'Entries','navigation':'Menu','panel':'Dashboard'}[kind])[kind],surface_options_schema(kind))
            self.assertEqual(json.loads((REPO/('docs/'+kind+'-options-v1.schema.json')).read_text()),surface_options_schema(kind))
        library=self.base/'library';fixture(library)
        write(library,'Проект.yaml',{'Имя':'Lib','Поставщик':'Tests','РежимСовместимости':'9.0'})
        model=analyze(self.root);model['libraries']=[{'provider':'Tests','name':'Lib','version':'1.0','sourceDirectory':'library','model':analyze(library)}]
        c=config();c['library']={'provider':'Tests','name':'Lib','version':'1.0'}
        p=plan_execution(self.root,model,c);self.assertEqual(p.root,library);self.assertEqual(p.entry.identity.project,'Lib')

    def test_navigation_recurses_gates_routes_native_and_ambiguity(self):
        c=config('navigation','Menu');p=self.plan(c)
        self.assertEqual(len(p.navigation['routes']),3)
        self.assertEqual(p.navigation['routes'][-1]['target']['rendering'],'external-native-ui')
        c['navigationContext']['Show']=False
        c['navigationActions']=[{'operation':'open','selector':{'owner':'Data::Entries'}}]
        p=self.plan(c); self.assertFalse(p.navigation['routes'][0]['visible'])
        c['navigationActions'][0]['selector']={'surface':'АвтоматическаяФорма'}
        self.plan(c)
        path=self.root/'Data/Menu.yaml'; data=yaml.safe_load(path.read_text());data['Элементы']=['=Menu'];write(self.root,'Data/Menu.yaml',data)
        with self.assertRaisesRegex(InputError,'Цикл'):self.plan(c)

    def test_panel_layout_dependencies_interval_and_preparation(self):
        c=config('panel','Dashboard');c['panelActions']=[{'advanceTime':29999},{'timerEvent':'next'},{'closeTimers':True}]
        p=self.plan(c);self.assertEqual(p.panel['refresh']['intervalMs'],30000)
        self.assertEqual([w['report'] for w in p.panel['widgets']],['Data::Entries','Data::Movements'])
        prepare_script(self.root,analyze(self.root),c,self.base)
        self.assertIn('panel-refresh',(self.base/'test.sbsl').read_text())
        c.pop('timers')
        with self.assertRaises(InputError):self.plan(c)

    def test_repeated_fragment_occurrences_and_duplicate_widget_data_sources(self):
        write(self.root,'Data/Wrapper.yaml',{'ВидЭлемента':'ФрагментКомандногоИнтерфейса','Имя':'Wrapper','Элементы':['=Menu','=Menu']})
        p=self.plan(config('navigation','Wrapper'))
        self.assertEqual(len({r['id'] for r in p.navigation['routes']}),6)
        self.assertEqual(len({r['id'] for r in p.navigation['sections']}),2)
        path=self.root/'Data/Dashboard.yaml';data=yaml.safe_load(path.read_text())
        data['Макет']['Виджеты'].append({'Ид':'again','Вид':'Отчет','Отчет':'Data::Entries'})
        write(self.root,'Data/Dashboard.yaml',data)
        p=self.plan(config('panel','Dashboard'));self.assertEqual(p.entry.identity.owner,'Dashboard')
        prepare_script(self.root,analyze(self.root),config('panel','Dashboard'),self.base)
        self.assertEqual(len(p.panel['widgets']),3)

    def test_all_final_dump_surfaces_prepare_without_business_execution(self):
        archive=REPO/'Dvizhok-final.xdump'; before=sha256(archive.read_bytes()).hexdigest();counts={}
        with open_project(archive) as root:
            model=analyze(root);regs=[e['namespace']+'::'+e['name'] for e in model['elements'] if e['elementType'] in {'РегистрСведений','РегистрНакопления'}]
            for e in model['elements']:
                kind={'Отчет':'report','ПанельОтчетов':'panel','ФрагментКомандногоИнтерфейса':'navigation'}.get(e['elementType'])
                if not kind:continue
                c=config(kind,e['name'],e['namespace']);c.pop('navigationContext',None)
                if kind!='navigation': c['storage']={'idType':'Ууид','registers':regs}
                folder=self.base/('dump-'+e['name']);folder.mkdir()
                with self.subTest(owner=e['name']):
                    prepare_script(root,model,c,folder)
                    p=json.loads((folder/'execution-plan.json').read_text());self.assertEqual(p['symbols'],[])
                    counts[kind]=counts.get(kind,0)+1
                    if kind!='navigation':self.assertEqual(p[kind]['stages']['execution'],'not-checked')
                    if kind=='panel':self.assertEqual(len(p['panel']['widgets']),6)
        self.assertEqual(counts,{'report':6,'panel':1,'navigation':6});self.assertEqual(sha256(archive.read_bytes()).hexdigest(),before)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Script runtime opt-in')
class SurfaceExecutionTest(SurfaceFixture):
    def execute(self,c=None):
        result=run_pure(self.root,analyze(self.root),c or config(),self.base)
        self.assertEqual(result['status'],'EXECUTED',result);return result['actual']

    def test_metadata_formulas_sorting_null_empty_write_repeat_isolation(self):
        c=config(); result=self.execute(c)['result']['actions'][0]
        self.assertEqual(result['values'],[{'number':'01','value':16,'option':4},{'number':'02','value':6,'option':None}])
        self.assertEqual(result['cardinality'],2)
        c['reportActions']=[{'operation':'execute'},{'operation':'write','objects':[{'type':'Data::Incoming','value':{'Ссылка':{'Идентификатор':'one'},'Номер':'00','Amount':12,'Дата':'2026-10-01'}}]},{'operation':'execute'}]
        actions=self.execute(c)['result']['actions'];self.assertEqual(actions[1]['values'][0]['value'],24)
        self.assertEqual(self.execute()['result']['actions'][0]['values'],result['values'])
        c=config();c['storage']['initial']=[]
        self.assertEqual(self.execute(c)['result']['actions'][0]['values'],[])

    def test_original_xbql_and_union_registrar_formulas(self):
        c=config(name='Query');c['queryParameters']={'N':{'type':'Число','value':4}}
        self.assertEqual([v['value'] for v in self.execute(c)['result']['actions'][0]['values']],[7,12])
        c=config(name='Movements');c['storage']['initial'].append({'type':'Data::Outgoing','value':{'Ссылка':{'Идентификатор':'out'},'Номер':'05','Amount':2,'Дата':'2026-10-03'}})
        c['storage']['initialRegisters']=[{'type':'Data::Ledger','filter':{'Регистратор':{'type':'Data::Incoming.Ссылка','value':{'Идентификатор':'one'}}},'rows':[{'Период':'2026-10-01T00:00:00','ВидЗаписи':'Приход','Amount':3,'Активность':True,'Регистратор':{'type':'Data::Incoming.Ссылка','value':{'Идентификатор':'one'}}}]},
            {'type':'Data::Ledger','filter':{'Регистратор':{'type':'Data::Outgoing.Ссылка','value':{'Идентификатор':'out'}}},'rows':[{'Период':'2026-10-03T00:00:00','ВидЗаписи':'Расход','Amount':2,'Активность':True,'Регистратор':{'type':'Data::Outgoing.Ссылка','value':{'Идентификатор':'out'}}}]}]
        values=self.execute(c)['result']['actions'][0]['values']
        self.assertEqual(values,[{'document':'in:02','value':3},{'document':'out:05','value':''}])
        path=self.root/'Data/Ledger.yaml';ledger=yaml.safe_load(path.read_text());ledger['Реквизиты'][0]['Тип'] += '|?'
        write(self.root,'Data/Ledger.yaml',ledger)
        self.assertEqual(self.execute(c)['result']['actions'][0]['values'],values)

    def test_formula_if_is_lazy_and_missing_reference_is_unavailable(self):
        path=self.root/'Data/Movements.yaml';data=yaml.safe_load(path.read_text())
        data['Макет']['Поля']=[field('value','ЕСЛИ(Amount = 3, "safe", Recorder.Number)')]
        write(self.root,'Data/Movements.yaml',data)
        registrar={'type':'Data::Incoming.Ссылка','value':{'Идентификатор':'missing'}}
        c=config(name='Movements');c['storage']['initial']=[]
        c['storage']['initialRegisters']=[{'type':'Data::Ledger','filter':{'Регистратор':registrar},'rows':[
            {'Период':'2026-10-01T00:00:00','ВидЗаписи':'Приход','Amount':3,'Активность':True,'Регистратор':registrar}]}]
        self.assertEqual(self.execute(c)['result']['actions'][0]['values'],[{'value':'safe'}])
        data['Макет']['Поля'][0]['Выражение']='ЕСЛИ(Amount = 0, "safe", Recorder.Number)';write(self.root,'Data/Movements.yaml',data)
        result=run_pure(self.root,analyze(self.root),c,self.base)
        self.assertEqual((result['status'],result['reasonCode']),('UNSUPPORTED','unsupported_contract'),result)

    def test_panel_duplicate_report_and_private_cross_namespace_seed(self):
        fixture(self.root,'Else')
        c=config('panel','Dashboard');c['storage']['registers'].append('Else::Ledger')
        registrar={'type':'Else::Incoming.Ссылка','value':{'Идентификатор':'other'}}
        c['storage']['initial'].append({'type':'Else::Incoming','value':{'Ссылка':{'Идентификатор':'other'},'Номер':'09','Amount':11,'Дата':'2026-10-01'}})
        c['storage']['initialRegisters']=[{'type':'Else::Ledger','filter':{'Регистратор':registrar},'rows':[
            {'Период':'2026-10-01T00:00:00','ВидЗаписи':'Приход','Amount':11,'Активность':True,'Регистратор':registrar}]}]
        path=self.root/'Data/Dashboard.yaml';data=yaml.safe_load(path.read_text())
        data['Макет']['Виджеты'] += [{'Ид':'again','Вид':'Отчет','Отчет':'Data::Entries'}, {'Ид':'else','Вид':'Отчет','Отчет':'Else::Movements'}]
        write(self.root,'Data/Dashboard.yaml',data)
        widgets=self.execute(c)['result']['actions'][0]['observation']['widgets']
        self.assertEqual(widgets['entries'],widgets['again'])
        self.assertEqual(widgets['else']['values'],[{'document':'in:09','value':11}])

    def test_navigation_open_blocked_and_external_native(self):
        c=config('navigation','Menu');c['navigationContext']['Show']=False
        c['navigationActions']=[{'operation':'open','selector':{'owner':'Data::Entries'}},{'operation':'open','selector':{'surface':'native'}}]
        actions=self.execute(c)['actions'];self.assertEqual([a['outcome'] for a in actions],['blocked','opened-model'])

    def test_panel_refresh_boundary_multiple_events_storage_writes_close_and_relocation(self):
        c=config('panel','Dashboard');c['panelActions']=[{'advanceTime':29999},{'operation':'write','objects':[{'type':'Data::Incoming','value':{'Ссылка':{'Идентификатор':'one'},'Номер':'02','Amount':10,'Дата':'2026-10-01'}}]}, {'advanceTime':1},{'advanceTime':60000},{'closeTimers':True}]
        actions=self.execute(c)['result']['actions']
        self.assertEqual([a['observation']['revision'] for a in actions],[1,1,2,4,4])
        self.assertEqual(actions[2]['observation']['widgets']['entries']['values'][1]['value'],20)
        self.assertEqual([len(actions[i]['events']) for i in (0,2,3)],[0,1,2])
        self.assertTrue(actions[-1]['timers']['closed'])
        moved=self.base/'moved';fixture(moved,'Renamed');self.root=moved
        self.assertEqual(self.execute(config('panel','Dashboard','Renamed'))['result']['actions'][0]['observation']['revision'],2)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Public CLI opt-in')
class SurfacePublicTest(SurfaceFixture):
    def test_public_test_run_batch_pass_fail_unavailable_and_formula_mutation(self):
        path=self.root/'Data/Entries.yaml';data=yaml.safe_load(path.read_text());data['Макет']['Поля']=[field('value','Amount * 2')]
        write(self.root,'Data/Entries.yaml',data)
        c=config();c['storage']['initial']=c['storage']['initial'][:1]
        expected={'result':{'actions':[{'data':[{'Amount':3}], 'values':[{'value':6}], 'cardinality':1,
            'layout':{'fields':[{'id':'value','expression':'Amount * 2','roles':['Строки'],'used':True,'caption':'','sorting':None,'outputType':'Число'}], 'display':'СводнаяТаблица','hierarchy':[]}}]},
            'storage':[{'type':'Data::Incoming','id':'one','value':{'Ссылка':{'Идентификатор':'one'},'Номер':'02','Amount':3,'Дата':'2026-10-01','Option':None}}]}
        good={'id':'good','type':'runtime','points':2,**c,'expected':expected}
        wrong=copy.deepcopy(good);wrong['id']='wrong';wrong['expected']['result']['actions'][0]['values'][0]['value']=777
        unsupported=copy.deepcopy(good);unsupported['id']='unsupported';unsupported['points']=3;unsupported['report']['report']['name']='Broken'
        broken=copy.deepcopy(data);broken['Имя']='Broken';broken['Макет']['Поля'][0]['Выражение']='UNKNOWN(Amount)'
        write(self.root,'Data/Broken.yaml',broken)
        assignment=self.base/'assignment.yaml';write(self.base,'assignment.yaml',{'name':'Report mechanism','checks':[good,wrong,unsupported]})
        for command in ('test','run'):
            out=self.base/command
            fact=subprocess.run([str(REPO/'bin/element-test'),command,'--project',str(self.root),'--assignment',str(assignment),'--output',str(out)],capture_output=True,text=True,timeout=120)
            self.assertEqual(fact.returncode,1,fact.stderr)
            result=json.loads((out/'result.json').read_text())
            self.assertEqual([x['status'] for x in result['checks']],['PASS','FAIL','UNSUPPORTED'],result)
            self.assertEqual((result['score'],result['maxScore'],result['unavailablePoints']),(2,4,3))
        second=self.base/'second';shutil.copytree(self.root,second)
        path=second/'Data/Entries.yaml';path.write_text(path.read_text().replace('Amount * 2','Amount * 3'))
        manifest=self.base/'batch.json';manifest.write_text(json.dumps({'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'report51',
            'submissions':[{'studentId':'one','project':str(self.root)},{'studentId':'two','project':str(second)}]}))
        from element_test.batch import run_batch
        self.assertEqual(run_batch(manifest,self.base/'batch',workers=2),1)
        batch=json.loads((self.base/'batch/batch-result.json').read_text())
        for row,statuses in zip(batch['submissions'],(['PASS','FAIL','UNSUPPORTED'],['FAIL','FAIL','UNSUPPORTED'])):
            result=json.loads((self.base/'batch'/row['result']).read_text())
            self.assertEqual([x['status'] for x in result['checks']],statuses)


@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','PostgreSQL opt-in')
class SurfaceSqlTest(SurfaceFixture):
    def test_report_postgres_independent_audit(self):
        self._sql(config())

    def test_panel_postgres_timer_refresh_audit(self):
        c=config('panel','Dashboard');c['panelActions']=[{'advanceTime':30000}]
        self._sql(c)

    def _sql(self,c):
        from unittest.mock import patch
        import secrets
        from element_test.integration import run_integration
        c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
        with patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            result=run_integration(c,analyze(self.root),self.base,root=self.root,enabled=True)
        self.assertEqual(result['status'],'EXECUTED',result)
        self.assertEqual([r['value']['Amount'] for r in result['actual']['storage']],[3,8])
        observations=result['actual']['result']['actions']
        report=observations[0] if 'report' in c else observations[0]['observation']['widgets']['entries']
        self.assertEqual([v['value'] for v in report['values']],[16,6])
        if 'panel' in c:self.assertEqual(observations[0]['observation']['revision'],2)
