"""Task 43: independent author oracles, Script execution and SQL state audit."""
import os,json,shutil,subprocess,copy,secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from virtual_fixtures import *
from element_test.bridge import write_json,run_test
from element_test.model import analyze
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.query_plan import parse_storage_query
from element_test.runtime import run_pure
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.yaml_io import InputError
from test_query_projections import grade

class SourcePlanTest(unittest.TestCase):
 def test_metadata_types_source_ranges_and_expected_excluded(self):
  for variant in ('ordinary','renamed'):
   for method in ANSWERS:
    with self.subTest(variant=variant,method=method):
     c=check(variant,method);c['expected']={'teacher-secret':-927394}
     p=plan_execution(CORPUS/variant,analyze(CORPUS/variant),c)
     self.assertNotIn('teacher-secret',p.to_json())
     for q in p.queries:
      for a in q['ast']['parameters']:self.assertEqual(q['text'][a['start']:a['end']],a['expression'])
     write_json(EVIDENCE/('plan-'+variant+'-'+method+'.json'),p.to_dict())

 def test_invalid_sources_fields_periods_shadowing_and_saved_cycles(self):
  c=ProjectTypes(analyze(CORPUS/'ordinary'),'Entry');c.rename_collisions=True;c.query_root=CORPUS/'ordinary'
  bad=['ВЫБРАТЬ TotalОстаток ИЗ Data::Sales.Остатки','ВЫБРАТЬ TotalПриход ИЗ Data::Sales.Обороты','ВЫБРАТЬ Период ИЗ Data::Plain.СрезПервых()',
       'ВЫБРАТЬ Элемент ИЗ Data::Item.Key','ВЫБРАТЬ Ссылка ИЗ Data::Item.Rows','ВЫБРАТЬ Total ИЗ Data::Ledger.Остатки','ВЫБРАТЬ TotalОстаток ИЗ Data::Ledger.Остатки(%D, Key == %K)',
       'ВЫБРАТЬ Период ИЗ Data::Ledger.Обороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Месяц)','ВЫБРАТЬ Администратор ИЗ Пользователи',
       'ВЫБРАТЬ Ключ ИЗ НеудаленныеОбъекты']
  for query in bad:
   with self.subTest(query=query),self.assertRaises(InputError):parse_storage_query(query,c)
  with TemporaryDirectory() as d:
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root)
   (root/'Data/Totals.xbql').write_text('ВЫБРАТЬ Key ИЗ Totals')
   with self.assertRaisesRegex(InputError,'Циклическая'):plan_execution(root,analyze(root),check(method='Saved'))
   p=root/'Entry/Main.xbsl';s=p.read_text();p.write_text(s.replace('метод Balance(T: ДатаВремя)','метод Balance(Data: Строка, T: ДатаВремя)'))
   cfg=check();cfg['args']=['shadow',START]
   with self.assertRaisesRegex(InputError,'Затенённый'):plan_execution(root,analyze(root),cfg)

 def test_system_fixture_validation_and_nominal_refs(self):
  for change in ('unknown','duplicate','type'):
   c=check(method='Users')
   if change=='unknown':c['queryContext']['users'][0]['secret']='x'
   if change=='duplicate':c['queryContext']['users'][1]['Ссылка']=REFS[0]
   if change=='type':c['queryContext']['users'][0]['Администратор']='yes'
   with self.subTest(change=change),self.assertRaises(InputError):plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),c)

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Script opt-in')
class SourceDockerTest(unittest.TestCase):
 def execute(self,root,c,temp,label):
  r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/(label+'.json'),r)
  self.assertEqual(r['status'],'EXECUTED',(label,r.get('message'),r.get('stderr')));return r
 def assess(self,root,c,temp,label,result):
  r=self.execute(root,c,temp,label);status=grade(r['actual'],expected(c,result),temp)
  write_json(EVIDENCE/('assessment-'+label+'.json'),{'status':status,'engine':'SBSL','criterionId':c['target']['method'],'sourceHash':analyze(root)['sourceHash'],'runtime':str((EVIDENCE/(label+'.json')).relative_to(REPO))})
  self.assertEqual(status,'PASS',(label,r['actual']));return r

 def test_two_portable_projects_semantics_in_script(self):
  with TemporaryDirectory() as d:
   for variant in ('ordinary','renamed'):
    for method,result in ANSWERS.items():
     with self.subTest(variant=variant,method=method):self.assess(CORPUS/variant,check(variant,method),Path(d),variant+'-'+method,result)

 def test_empty_sources_boundaries_and_post_selection_filter(self):
  with TemporaryDirectory() as d:
   for method in ('First','Last','Balance','Turnover','Rows','Phones','Plain','Sales','Combined'):
    self.assess(CORPUS/'ordinary',check(method=method,empty=True),Path(d),'empty-'+method,[])
   cases=[('First',['2027-01-01'],[]),('Last',['2020-01-01'],[]),('Balance',['2026-09-30T23:59:59'],[{'Key':'b','TotalОстаток':-2,'TotalОстатокПоложительный':0,'TotalОстатокОтрицательный':2}]),
          ('Turnover',[END,START],[]),('Rows',[REFS[2]],[]),('PostFilter',[D,3],[{'Key':'a'}])]
   for n,(method,args,result) in enumerate(cases):
    c=check(method=method);c['args']=args;self.assess(CORPUS/'ordinary',c,Path(d),'boundary-'+str(n),result)

 def test_exact_review_templates_independent_assessment(self):
  cases={'slice_last':[{'Total':3},{'Total':6}],'slice_first':[{'Total':3},{'Total':7}],'balances':[{'TotalОстаток':13},{'TotalОстаток':-1}],'turnovers':[{'TotalОборот':13},{'TotalОборот':-1},{'TotalОборот':0}]}
  with TemporaryDirectory() as d:
   for method,result in cases.items():
    c=check(method=method);c['args']=[D] if method.startswith('slice') else []
    self.assess(CORPUS/'documented',c,Path(d),'documented-'+method,result)

 def test_mutations_alternative_infrastructure_failure_and_recovery(self):
  mutations=[('First','СрезПервых(%D)','СрезПоследних(%D)'),('Balance','Остатки(%T)','Остатки(%{новый ДатаВремя("2026-10-02T00:00:00")})'),('Turnover','Обороты(%S, %E)','Обороты(%S, %S)'),('Rows','КАК Value','КАК Different'),('Nested','СУММА(Q.TotalОборот)','МАКСИМУМ(Q.TotalОборот)'),('Phones','УПОРЯДОЧИТЬ ПО Индекс','УПОРЯДОЧИТЬ ПО Индекс УБЫВ')]
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';original=p.read_text()
   for method,old,new in mutations:
    if method=='Rows':old='Индекс, НомерСтроки, Value';new='Индекс, НомерСтроки, Индекс КАК Value'
    p.write_text(original.replace(old,new));c=check(method=method)
    r=self.execute(root,c,temp,'mutation-'+method);self.assertEqual(grade(r['actual'],expected(c,ANSWERS[method]),temp,ordered=method=='Phones'),'FAIL')
    write_json(EVIDENCE/('mutation-assessment-'+method+'.json'),{'status':'FAIL','engine':'SBSL'})
   p.write_text(original.replace('СУММА(Q.TotalОборот)','СУММА(Q.TotalОборот + 0)'));self.assess(root,check(method='Nested'),temp,'alternative',ANSWERS['Nested'])
   p.write_text(original)
   import element_test.storage as storage
   native=storage.session_module
   def broken(*a,**k):return native(*a,**k).replace('исп Поток = Ф.ОткрытьПотокЧтения()', 'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("task43 infrastructure injection")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
   # Existing failure marker ensures a caught storage error never grades PASS.
   with patch('element_test.storage.session_module',side_effect=broken):r=run_pure(root,analyze(root),check(),temp)
   write_json(EVIDENCE/'infrastructure-failure.json',r);self.assertEqual(r['status'],'ERROR')
   self.assess(root,check(),temp,'infrastructure-recovery',ANSWERS['Balance'])

 def test_scalar_and_array_in_capture_detached_at_creation(self):
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl'
   p.write_text(p.read_text()+'''
метод Scalar(K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key В (%K)}.Выполнить()
;
метод ScalarExpression(K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key В (%{K})}.Выполнить()
;
метод ScalarLiteral(): Объект
    возврат Запрос{ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key В (%{"a"})}.Выполнить()
;
метод Capture(K: Массив<Строка>): Объект
    знч Q = Запрос{ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key В (%K)}
    знч Old = Q.Выполнить()
    K[0] = "b"
    возврат {"old": Old, "again": Q.Выполнить(), "new": Запрос{ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key В (%K)}.Выполнить()}
;
''')
   c=check(method='Scalar');c['args']=['a'];self.assess(root,c,temp,'scalar-in',[{'Key':'a'}])
   c['target']['method']='ScalarExpression';self.assess(root,c,temp,'scalar-expression-in',[{'Key':'a'}])
   c['target']['method']='ScalarLiteral';c['args']=[];self.assess(root,c,temp,'scalar-literal-in',[{'Key':'a'}])
   c=check(method='Capture');c['args']=[['a','a']]
   self.assess(root,c,temp,'captured-array',{'old':[{'Key':'a'}],'again':[{'Key':'a'}],'new':[{'Key':'a'},{'Key':'b'}]})
   c['sequence']=[{'method':'Capture','args':[['a','a']]},{'method':'Scalar','args':['b']}]
   self.assess(root,c,temp,'mixed-capture-types',{'actions':[{'old':[{'Key':'a'}],'again':[{'Key':'a'}],'new':[{'Key':'a'},{'Key':'b'}]},[{'Key':'b'}]]})

 def test_multiple_dimensions_resources_and_raw_inactive_rows(self):
  from element_test.yaml_io import load_yaml
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Data/Ledger.yaml';meta=load_yaml(p)
   ids=json.loads((CORPUS/'ids.json').read_text())
   meta['Измерения'].append({'Ид':ids[-2],'Имя':'Zone','Тип':'Строка'});meta['Ресурсы'].append({'Ид':ids[-1],'Имя':'Cost','Тип':'Число'})
   p.write_text(dump_yaml(meta,allow_unicode=True,sort_keys=False));p=root/'Entry/Main.xbsl'
   p.write_text(p.read_text()+'''
метод Multi(): Объект
    возврат Запрос{ВЫБРАТЬ Key, TotalОстаток, CostОстаток ИЗ Data::Ledger.Остатки}.Выполнить()
;
метод Raw(): Объект
    возврат Запрос{ВЫБРАТЬ Индекс, НомерСтроки, Активность ИЗ Data::Ledger УПОРЯДОЧИТЬ ПО Индекс}.Выполнить()
;
''')
   c=check(method='Multi');rows=c['storage']['initialRegisters'][-2]['rows']
   for i,(row,cost) in enumerate(zip(rows,[20,6,4,999,10,3,2,2])):row.update(Zone='US' if i==2 else 'EU',Cost=cost)
   self.assess(root,c,temp,'multiple-dimensions-resources',[{'Key':'a','TotalОстаток':5,'CostОстаток':14},{'Key':'a','TotalОстаток':8,'CostОстаток':4},{'Key':'b','TotalОстаток':-1,'CostОстаток':-7}])
   c['target']['method']='Raw'
   self.assess(root,c,temp,'raw-inactive',[{'Индекс':i,'НомерСтроки':i+1,'Активность':i!=3} for i in range(8)])

 def test_real_direct_root_in_unchanged_archive(self):
  with TemporaryDirectory() as d,open_project(REPO/'Dvizhok.xdump') as root:
   for empty in (False,True):
    c=real_check(empty);r=self.execute(root,c,Path(d),'real-'+str(empty));self.assertEqual(grade(r['actual'],real_expected(c),Path(d)),'PASS')

@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','PostgreSQL opt-in')
class SourceSqlTest(unittest.TestCase):
 def test_sources_repeat_snapshots_rollback_sql_audit_and_cleanup(self):
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for variant in ('ordinary','renamed'):
    for method in ('Balance','Combined','Rows','State'):
     with self.subTest(variant=variant,method=method):
      c=check(variant,method,sql=True);r=run_integration(c,analyze(CORPUS/variant),Path(d),root=CORPUS/variant,enabled=True,inject_failure=True)
      write_json(EVIDENCE/('sql-'+variant+'-'+method+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],expected(c,ANSWERS[method]),Path(d)),'PASS');self.assertNotIn('source:commit',r['storageTrace'])

@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Real PostgreSQL opt-in')
class SourceRealSqlTest(unittest.TestCase):
 def test_real_direct_root_independent_sql_audit_and_cleanup(self):
  with TemporaryDirectory() as d,open_project(REPO/'Dvizhok.xdump') as root,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   c=real_check(sql=True);r=run_integration(c,analyze(root),Path(d),root=root,enabled=True,inject_failure=True)
   write_json(EVIDENCE/'sql-real.json',r);self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],real_expected(c),Path(d)),'PASS');self.assertNotIn('source:commit',r['storageTrace'])

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Public Script opt-in')
class SourcePublicTest(unittest.TestCase):
 def test_public_real_test_run_and_fail_unsupported_pass(self):
  for mode in ('test','run'):
   folder=EVIDENCE/('public-real-'+mode);p=subprocess.run([str(REPO/'bin/element-test'),mode,'--project',str(REPO/'Dvizhok.xdump'),'--assignment',str(REPO/'assignments/virtual-real'),'--output',str(folder)],capture_output=True,text=True,timeout=900)
   (EVIDENCE/('public-real-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-real-'+mode+'.stderr')).write_text(p.stderr)
   self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout);r=json.loads((folder/'result.json').read_text());self.assertEqual(r['score'],2);self.assertTrue(all(c['status']=='PASS' for c in r['checks']))
  r,g=run_test(CORPUS/'ordinary',REPO/'assignments/virtual-control',EVIDENCE/'public-control');self.assertEqual([c['status'] for c in r['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(g['unavailablePoints'],1)

 def test_two_fresh_isolated_batches(self):
  from element_test.batch import run_batch
  with TemporaryDirectory() as d:
   temp=Path(d);assignment=EVIDENCE/'batch-assignment';assignment.mkdir(exist_ok=True);checks=[]
   for method in ('First','Balance','Nested'):
    c=check(method=method);checks.append({'id':method,'type':'runtime','points':1,'comparison':'query-rows-unordered',**c,'expected':expected(c,ANSWERS[method])})
   (assignment/'assignment.yaml').write_text(dump_yaml({'name':'Batch43','checks':checks},allow_unicode=True,sort_keys=False));submissions=[]
   for label in ('correct','mutated','neighbor'):
    root=EVIDENCE/'batch-inputs'/label;shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
    if label=='mutated':
     p=root/'Entry/Main.xbsl';p.write_text(p.read_text().replace('СрезПервых(%D)','СрезПоследних(%D)').replace('Остатки(%T)','Остатки(%{новый ДатаВремя("2026-10-02T00:00:00")})').replace('СУММА(Q.TotalОборот)','МАКСИМУМ(Q.TotalОборот)'))
    submissions.append({'studentId':label,'project':str(root)})
   manifest=EVIDENCE/'batch-manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'virtual-043','submissions':submissions})
   saved=[]
   for n in (1,2):
    folder=EVIDENCE/f'batch-{n}';suffix=1
    while folder.exists():folder=EVIDENCE/f'batch-{n}-repeat-{suffix}';suffix+=1
    with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/f'cache-{n}')}):code=run_batch(manifest,folder,2)
    self.assertEqual(code,1)
    data=json.loads((folder/'batch-result.json').read_text());self.assertEqual([s['score'] for s in data['submissions']],[3,0,3]);self.assertFalse(any(s['cacheHit'] for s in data['submissions']));saved.append(str(folder.relative_to(REPO)))
   write_json(EVIDENCE/'batch-latest.json',saved)
