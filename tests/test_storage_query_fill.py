"""Task 39: nominal typed fill, Script execution and independent SBSL/SQL criteria."""
import copy
import json
import os
import secrets
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from fill_fixtures import *
from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_plan import parse_storage_query, query_literals
from element_test.runtime import run_pure, prepare_script
from element_test.yaml_io import InputError, InvalidTestError
from test_record_sets import grade, normalized


def setUpModule():EVIDENCE.mkdir(parents=True,exist_ok=True)

class FillPlanTest(unittest.TestCase):
 def test_catalog_all_projects_stable_ids_and_visible_malformed_candidates(self):
  from element_test.query_catalog import build_catalog, literals_with_failures
  catalog=build_catalog(evidence=[])
  self.assertEqual(catalog['counts']['literal'],213);self.assertEqual(catalog['counts']['xbql-file'],18)
  records=catalog['contracts'];self.assertEqual(len({r['contractId'] for r in records}),len(records))
  for r in records:
   self.assertFalse(r['executed']);self.assertFalse(r['independentlyAssessed'])
   if r['family']=='documented-form':self.assertIsNone(r['range']);self.assertTrue((REPO/r['fixture']).exists())
  self.assertEqual(sum(r['family']=='literal' and r['projectIdentity']['Имя']=='БазаЗнаний' for r in records),93)
  self.assertTrue(literals_with_failures('метод F()\nвозврат Запрос{ВЫБРАТЬ X')[0][-1])
  write_json(EVIDENCE/'catalog-discovery.json',{'counts':catalog['counts'],'archives':catalog['archives']})

 def test_catalog_evidence_is_per_occurrence_and_requires_runtime_and_direct_root(self):
  from element_test.query_catalog import attach_evidence
  record={'projectSourceHash':'same','file':'M.xbsl','range':[10,20],
          'target':{'namespace':'N','module':'M','method':'F'},'planned':False,
          'executed':False,'independentlyAssessed':False,'criteria':[],'evidence':[]}
  documentation={'projectSourceHash':None,'file':None,'range':None}
  with TemporaryDirectory() as d:
   folder=Path(d)
   write_json(folder/'result.json',{'sourceHash':'same','checks':[{'id':'one','status':'PASS'}]})
   write_json(folder/'execution-plans.json',[{'criterionId':'one','plan':{
    'entry':{'declaration':'F','source_file':'M.xbsl'},'queries':[{'sourceFile':'M.xbsl','start':10,'end':20}]}}])
   attach_evidence([record,documentation],[folder]);self.assertTrue(record['planned']);self.assertFalse(record['executed'])
   write_json(folder/'runtime-evidence.json',[{'criterionId':'one','status':'EXECUTED'}])
   attach_evidence([record,documentation],[folder]);self.assertTrue(record['executed']);self.assertTrue(record['independentlyAssessed'])
   indirect=copy.deepcopy(record);indirect['target']['method']='Helper';indirect['independentlyAssessed']=False
   attach_evidence([indirect],[folder]);self.assertTrue(indirect['executed']);self.assertFalse(indirect['independentlyAssessed'])

 def test_ir_nominal_identity_spans_mapping_defaults_and_no_expected(self):
  for variant in ('ordinary','daily'):
   root=CORPUS/variant;c=portable_check(variant);c['expected']={'secret-answer':456789}
   p=plan_execution(root,analyze(root),c);q=p.queries[0];f=q['ast']['fill']
   self.assertNotIn('secret-answer',p.to_json());self.assertEqual(f['owner'],'Data::Card')
   a,b=f['typeRange'];self.assertEqual(q['text'][a:b],'Result')
   self.assertEqual(q['rowType'],f['type']);self.assertEqual(f['constructor'],'automatic-named')
   fields={x['Имя']:x for x in f['fields']};self.assertTrue(fields['Name']['constructorRequired']);self.assertTrue(fields['Name']['ТолькоЧтение'])
   self.assertEqual(fields['Default']['ЗначениеПоУмолчанию'],'authored');self.assertEqual(fields['Empty']['defaultKind'],'implicit')
   self.assertEqual(f['mapping'][0]['columnType'],'Число');self.assertEqual(f['mapping'][0]['fieldType'],'Число?')
   write_json(EVIDENCE/('plan-'+variant+'.json'),p.to_dict())
   p=plan_execution(root,analyze(root),portable_check(variant,'Construct'))
   self.assertEqual(sum(e['elementType']=='Структура' for e in p.contracts.canonical_elements.values()),1)

 def test_type_rejections_have_ranges_and_unreachable_types_are_lazy(self):
  with TemporaryDirectory() as d:
   root=Path(d)/'source';shutil.copytree(CORPUS/'ordinary',root)
   p=root/'Entry/Main.xbsl';base=p.read_text();meta=root/'Data/Card.yaml';yaml_base=meta.read_text()
   cases=[('P.Label КАК Name','P.Label КАК Unknown','Неизвестная колонка'),
          ('P.Label КАК Name, ','','обязательное поле'),('P.Total КАК Amount','P.Optional КАК Amount','Несовместимые типы'),
          ('P.Link КАК Second','P.Label КАК Name','Повторяющийся'),('ЗАПОЛНИТЬ Result','ЗАПОЛНИТЬ Missing','Тип ЗАПОЛНИТЬ'),
          ('ЗАПОЛНИТЬ Result','ЗАПОЛНИТЬ Other::Card','Несовместимые типы')]
   for a,b,reason in cases:
    p.write_text(base.replace(a,b))
    with self.subTest(reason=reason),self.assertRaisesRegex(InputError,reason):plan_execution(root,analyze(root),portable_check())
   p.write_text(base)
   # A non-null runtime fixture and WHERE cannot justify nullable narrowing.
   meta.write_text(yaml_base.replace('Тип: Строка?','Тип: Строка'))
   with self.assertRaisesRegex(InputError,r'Строка\? → Строка'):plan_execution(root,analyze(root),portable_check())
   meta.write_text(yaml_base.replace('Имя: Card','Имя: Card\nАннотации: []'))
   with self.assertRaisesRegex(InputError,'Неподдержанное свойство'):plan_execution(root,analyze(root),portable_check())
   self.assertTrue(plan_execution(root,analyze(root),portable_check(method='Neighbor')).queries)
   meta.write_text(yaml_base)
   p.write_text(base.replace('метод Rows(D: Дата):','метод Rows(D: Дата, Result: Строка):'))
   c=portable_check();c['args'].append('shadow')
   with self.assertRaisesRegex(InputError,'Затенённый тип'):plan_execution(root,analyze(root),c)

 def test_visibility_fully_qualified_and_alias_same_nominal_type(self):
  m=analyze(CORPUS/'ordinary');c=ProjectTypes(m,'Entry',['Data::Card как Shape']);c.rename_collisions=True
  c.require('Shape');a=c.canonical_type('Shape');self.assertEqual(a,c.canonical_type('Data::Card'))
  self.assertNotEqual(a,c.canonical_type('Other::Card'))
  self.assertIn('обз знч Name',c.definitions[a]);self.assertIn('Default: Строка = "authored"',c.definitions[a])
  c.require('Массив<Shape>');self.assertIn('новый '+a,c.literal({'Name':'input'},'Shape'))
  for e in m['elements']:
   if e['namespace']=='Data' and e['name']=='Card':e['visibility']='ВПодсистеме'
  with self.assertRaises(InputError):ProjectTypes(m,'Entry').require('Data::Card')

 def test_ordinary_readonly_and_literal_strings_comments_nested_parameters(self):
  c=ProjectTypes(analyze(CORPUS/'ordinary'),'Entry');c.rename_collisions=True
  q=parse_storage_query('ВЫБРАТЬ Label ИЗ Data::Item',c);self.assertIsNone(q.fill)
  s='// Запрос{bad}\nметод F(): Объект\n возврат Запрос{ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == %{{"k": "} //"}["k"] как Строка}}.Выполнить()\n;'
  found=list(query_literals(s));self.assertEqual(len(found),1)
  parsed=parse_storage_query(found[0][3],c);self.assertIn('} //',parsed.parameters[0].expression)

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 39 Docker opt-in')
class FillDockerTest(unittest.TestCase):
 def execute(self,root,c,temp,label):
  r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/(label+'.json'),r)
  self.assertEqual(r['status'],'EXECUTED',r);return r

 def test_real_both_direct_roots_and_independent_rounding_history(self):
  with open_project(ARCHIVE) as root,TemporaryDirectory() as d:
   for label,c,exp in real_cases():
    r=self.execute(root,c,Path(d),'real-'+label)
    self.assertEqual(normalized(r['actual']),normalized(exp));self.assertEqual(grade(r['actual'],exp,Path(d)),'PASS')

 def test_nominal_structure_input_uses_helper_constructor_and_rejects_missing_required_field(self):
  with TemporaryDirectory() as d:
   temp=Path(d)
   for variant in ('ordinary','daily'):
    c=portable_check(variant,'Identity');c['args']=[{'Name':'input','Amount':5,'Kind':'Second','Ref':REFS[0]}]
    result=card('input',5,ref=REFS[0],kind='Second');result['Second']=None
    r=self.execute(CORPUS/variant,c,temp,variant+'-input')
    self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS')
    c['args']=[{'Amount':5}]
    r=run_pure(CORPUS/variant,analyze(CORPUS/variant),c,temp)
    self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','invalid_test'),r)
    alternate=temp/variant;shutil.copytree(CORPUS/variant,alternate)
    source=alternate/'Entry/Main.xbsl'
    source.write_text(source.read_text().replace('P.Total КАК Amount, P.Link КАК Ref',
        'P.Link КАК Ref, P.Total КАК Amount').replace('ЗАПОЛНИТЬ Result','ЗАПОЛНИТЬ Data::Card'))
    c=portable_check(variant)
    r=self.execute(alternate,c,temp,variant+'-alternative')
    self.assertEqual(grade(r['actual'],expected(c,portable_rows()),temp),'PASS')

 def test_portable_defaults_nominal_helper_new_array_fresh_and_collision(self):
  with TemporaryDirectory() as d:
   temp=Path(d)
   for variant in ('ordinary','daily'):
    root=CORPUS/variant
    cases=[('Rows',portable_rows()),('Construct',[card('new',9)]),('Collision',{'local':portable_rows(),'other':card('neighbor')})]
    fresh=portable_rows();fresh[0]['Amount']=999
    # Mutate one reference projection; its second projection remains detached.
    fresh[1]['Ref']={'Идентификатор':IDS[2]}
    cases.append(('Detached',{'old':portable_rows(),'fresh':fresh,'again':portable_rows()}))
    for method,result in cases:
     c=portable_check(variant,method);r=self.execute(root,c,temp,variant+'-'+method)
     self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS',(method,r))
    c=portable_check(variant,'Single',empty=True);r=self.execute(root,c,temp,variant+'-empty');self.assertIsNone(r['actual']['result'])
    c=portable_check(variant,'Single');c['captureException']=True
    r=self.execute(root,c,temp,variant+'-multiple');self.assertEqual(r['actual']['result']['exception']['type'],'Std::IllegalStateException')

 def test_business_mutations_are_sbsl_fail_and_recovery(self):
  with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
   temp=Path(d);root=temp/'source';shutil.copytree(original,root)
   p=root/'Общие/КурсыВалют/КурсыВалют.xbsl';base=p.read_text()
   cases=[('resource','Курс КАК Курс','Кратность КАК Курс',{}),
    ('swap','Курс КАК Курс,\n            КурсыВалютСрезПоследних.Кратность КАК Кратность','Кратность КАК Курс,\n            КурсыВалютСрезПоследних.Курс КАК Кратность',{}),
    ('date','%ДатаКурса','%{ДатаКурса.ДобавитьДни(-1)}',{}),
    ('currency','%Валюта','%{новый Валюты.Ссылка(Идентификатор = новый Ууид("22222222-2222-4222-8222-222222222222"))}',{}),
    ('division',' / КурсВалюты.Кратность','',{'method':'ПересчитатьПоКурсу'}),
    ('round','Округлить(2)','Округлить(1)',{'method':'ПересчитатьПоКурсу','amount':1}),
    ('missing','возврат 0','возврат 17',{'method':'ПересчитатьПоКурсу','ref':2}),
    ('zero','возврат 0','возврат 17',{'method':'ПересчитатьПоКурсу','ref':1})]
   for label,a,b,kwargs in cases:
    self.assertIn(a,base);p.write_text(base.replace(a,b));c=real_check(**kwargs)
    exp=next(exp for name,_,exp in real_cases() if name==('formula' if label=='division' else 'round-positive' if label=='round' else 'formula-missing' if label=='missing' else 'zero-factor' if label=='zero' else 'found'))
    r=self.execute(root,c,temp,'mutation-'+label);self.assertEqual(grade(r['actual'],exp,temp),'FAIL',label)
   p.write_text(base);self.execute(root,real_check(),temp,'real-recovery')
   root=temp/'portable';shutil.copytree(CORPUS/'ordinary',root)
   p=root/'Data/Card.yaml';p.write_text(p.read_text().replace('authored','mutated'))
   c=portable_check();r=self.execute(root,c,temp,'mutation-default');self.assertEqual(grade(r['actual'],expected(c,portable_rows()),temp),'FAIL')

 def test_invalid_fixture_timeout_unsupported_and_next_supported(self):
  root=CORPUS/'ordinary'
  with TemporaryDirectory() as d:
   temp=Path(d);c=portable_check();c['storage']['initial'][0]['value']['Key']='bad-uuid'
   r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'invalid-fixture.json',r);self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','invalid_test'))
   c=portable_check();c['timeout']=0.001;r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'timeout.json',r);self.assertEqual(r['status'],'TIMEOUT')
   r=run_pure(root,analyze(root),portable_check(method='Unsupported'),temp);write_json(EVIDENCE/'unsupported.json',r);self.assertEqual(r['status'],'UNSUPPORTED')
   self.execute(root,portable_check(),temp,'after-unavailable')

 def test_captured_order_reexecution_write_mutation_rollback_and_old_snapshots(self):
  with TemporaryDirectory() as d:
   for variant in ('ordinary','daily'):
    c=portable_check(variant,'State');c['args'].append('A')
    result={'old':[card('A',3)],'seen':[card('A',700)],'again':[card('A',17)],'after':[card('A',3)],
            'calls':['label'] if variant=='ordinary' else ['date','label']}
    r=self.execute(CORPUS/variant,c,Path(d),variant+'-state')
    self.assertEqual(grade(r['actual'],expected(c,result),Path(d)),'PASS',r)
    self.assertEqual(r['storageTrace'].count('source:rollback'),1)
    self.assertNotIn('source:commit',r['storageTrace'])

 def test_sticky_backend_error_after_catch_and_recovery(self):
  import element_test.storage as storage
  native=storage.session_module
  def failing(*args,**kwargs):
   source=native(*args,**kwargs)
   return source.replace('исп Поток = Ф.ОткрытьПотокЧтения()',
    'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("injected fill read failure")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
  with TemporaryDirectory() as d:
   c=portable_check(method='Swallow');root=CORPUS/'ordinary'
   with patch('element_test.storage.session_module',side_effect=failing):r=run_pure(root,analyze(root),c,Path(d))
   write_json(EVIDENCE/'sticky-error.json',r);self.assertEqual(r['status'],'ERROR',r)
   self.execute(root,c,Path(d),'sticky-recovery')

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task 39 SQL opt-in')
class FillSqlTest(unittest.TestCase):
 def test_portable_state_rollback_parity_and_sql_cleanup(self):
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for variant in ('ordinary','daily'):
    c=portable_check(variant,'State',sql=True);c['args'].append('A')
    result={'old':[card('A',3)],'seen':[card('A',700)],'again':[card('A',17)],'after':[card('A',3)],
            'calls':['label'] if variant=='ordinary' else ['date','label']}
    root=CORPUS/variant;r=run_integration(c,analyze(root),Path(d),root=root,enabled=True)
    write_json(EVIDENCE/('sql-'+variant+'-state.json'),r);self.assertTrue(r['integration']['cleanup'],r)
    self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],expected(c,result),Path(d)),'PASS',r)
 def test_real_both_roots_no_publication_independent_full_history_audit(self):
  with open_project(ARCHIVE) as root,TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for label,c,exp in [x for x in real_cases(True) if x[0] in ('found','formula','missing','zero-factor')]:
    r=run_integration(c,analyze(root),Path(d),root=root,enabled=True,inject_failure=True)
    write_json(EVIDENCE/('sql-real-'+label+'.json'),r);self.assertTrue(r['integration']['cleanup'],r)
    self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],exp,Path(d)),'PASS')
    # run_integration replaces actual.storage with an independent SQL SELECT,
    # not the executor's staging snapshot (storageEvidence is failure-only).
    self.assertEqual(normalized({'storage':r['actual']['storage']}),normalized({'storage':exp['storage']}))

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 39 public Docker opt-in')
class FillPublicTest(unittest.TestCase):
 def test_public_test_run_control_two_fresh_batches_and_nominal_inputs(self):
  from element_test.batch import run_batch
  for mode in ('test','run'):
   folder=EVIDENCE/('public-'+mode)
   command=[str(REPO/'bin/element-test'),mode,'--project',str(ARCHIVE),'--assignment',str(REPO/'assignments/storage-query-fill-real'),'--output',str(folder)]
   p=subprocess.run(command,capture_output=True,text=True,timeout=900)
   (EVIDENCE/('public-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-'+mode+'.stderr')).write_text(p.stderr)
   self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
   r=json.loads((folder/'result.json').read_text());self.assertEqual(r['score'],16);self.assertTrue(all(c['status']=='PASS' for c in r['checks']))
  a=json.loads((EVIDENCE/'public-test/result.json').read_text());b=json.loads((EVIDENCE/'public-run/result.json').read_text())
  self.assertEqual(a['checks'],b['checks'])
  from element_test.bridge import run_test
  result,package=run_test(CORPUS/'ordinary',REPO/'assignments/storage-query-fill-control',EVIDENCE/'public-control')
  self.assertEqual([c['status'] for c in result['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(package['unavailablePoints'],1)
  with TemporaryDirectory() as d:
   temp=Path(d);submissions=[]
   for name in ('correct','mutated','neighbor'):
    root=temp/name;shutil.copytree(CORPUS/'ordinary',root)
    if name=='mutated':
     p=root/'Data/Card.yaml';p.write_text(p.read_text().replace('authored','mutated'))
    submissions.append({'studentId':name,'project':str(root)})
   manifest={'schemaVersion':'1.0','assignment':str(REPO/'assignments/storage-query-fill-ordinary'),'assignmentId':'fill-039','submissions':submissions}
   path=temp/'manifest.json';write_json(path,manifest)
   inputs=EVIDENCE/'batch-inputs';inputs.mkdir(exist_ok=True)
   saved=copy.deepcopy(manifest)
   for sub in saved['submissions']:
    destination=inputs/sub['studentId'];shutil.copytree(Path(sub['project']),destination,dirs_exist_ok=True);sub['project']=str(destination)
   write_json(EVIDENCE/'batch-manifest.json',saved)
   for iteration in (1,2):
    folder=temp/('batch-'+str(iteration))
    with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/'cache')}):code=run_batch(path,folder,2)
    self.assertEqual(code,1)
    packages=[json.loads((folder/'submissions'/f'{n:06d}'/'grading.json').read_text()) for n in (1,2,3)]
    self.assertEqual([p['score'] for p in packages],[4,1,4])
    report=json.loads((folder/'batch-result.json').read_text());self.assertFalse(any(s['cacheHit'] for s in report['submissions']))
    shutil.copytree(folder,EVIDENCE/('batch-'+str(iteration)),dirs_exist_ok=True)
