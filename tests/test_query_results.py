"""Task 44: author oracles, source ranges, resources, SQL and public grading."""
import copy,json,os,secrets,shutil,subprocess,unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from pathlib import Path
from result_fixtures import *
from element_test.model import analyze
from element_test.execution_plan import plan_execution
from element_test.runtime import run_pure
from element_test.bridge import write_json,run_test
from element_test.loader import open_project
from element_test.yaml_io import InputError
from test_query_projections import grade

class ResultPlanTest(unittest.TestCase):
 def test_produced_nominal_rows_constructor_mapping_ranges_no_answers(self):
  for v in ('ordinary','daily'):
   for method in ('Produce','Fill','Positional','Local','Loop','PassResult','DirectLoop'):
    with self.subTest(variant=v,method=method):
     c=check(v,method);c['expected']={'answer-secret':-741832}
     p=plan_execution(CORPUS/v,analyze(CORPUS/v),c);self.assertNotIn('answer-secret',p.to_json())
     q=p.queries[0];source=(CORPUS/v/q['sourceFile']).read_text();self.assertEqual(source[q['start']:q['end']], 'Запрос{'+q['text']+'}')
     if method=='Positional':self.assertEqual(q['ast']['fill']['constructor'],'automatic-positional');self.assertEqual([m['parameter'] for m in q['ast']['fill']['mapping']],['Name','Amount'])
     if method=='Produce':self.assertEqual(q['ast']['fill']['constructor'],'produced-readonly-internal');self.assertTrue(all(f['ТолькоЧтение'] for f in q['ast']['fill']['fields']))
     if method=='Local':self.assertIn('@ИменованныеПараметры',p.contracts.definitions[q['ast']['fill']['type']])
     if not q['ast'].get('fill'):
      self.assertIn(q['rowType'].split('.')[0],p.contracts.definitions)
      self.assertNotIn('РесурсЗапрос',q['rowType'])
     self.assertEqual(q['resultContract'],'single-pass-044')
     write_json(EVIDENCE/('plan-'+v+'-'+method+'.json'),p.to_dict())
 def test_dynamic_original_text_and_deferred_bindings(self):
  c=check(method='Dynamic');p=plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),c);q=p.queries[0]
  self.assertIn('&Name',q['text']);self.assertEqual(q['ast']['parameters'][0]['expression'],'Name')
  source=(CORPUS/'ordinary'/q['sourceFile']).read_text();self.assertEqual(source[q['bodyStart']:q['end']-2],q['text'])
  self.assertTrue(any(b.operation=='УстановитьПараметр' and b.category=='query-api' for b in p.bindings))
  write_json(EVIDENCE/'plan-dynamic.json',p.to_dict())
 def test_shadowing_reassignment_visibility_generated_public_constructor(self):
  from element_test.generated_types import ProjectTypes
  from element_test.query_plan import parse_storage_query
  unscoped=ProjectTypes(analyze(CORPUS/'ordinary'),'Entry',[])
  with self.assertRaisesRegex(InputError,'идентичность исходного модуля'):
   parse_storage_query('ВЫБРАТЬ Label ПОРОДИТЬ Row ИЗ Data::Item',unscoped)
  with TemporaryDirectory() as d:
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text()
   for label,text in [('reassign',base.replace('исп R = Q.Выполнить()','Q = "shadow"\n    исп R = Q.Выполнить()',1)),('produced-constructor',base.replace('возврат R.ВМассив()','возврат новый Row()',1)),('unsupported-text',base)]:
    p.write_text(text);c=check(method='Unsupported' if label=='unsupported-text' else 'Produce')
    with self.subTest(label=label),self.assertRaises(InputError):plan_execution(root,analyze(root),c)
   p.write_text(base.replace('ВЫБРАТЬ Label КАК Name ЗАПОЛНИТЬ LocalCard','ВЫБРАТЬ Label КАК Wrong ЗАПОЛНИТЬ LocalCard'))
   with self.assertRaisesRegex(InputError,'только именованные'):plan_execution(root,analyze(root),check(method='Local'))
 def test_unrelated_project_execute_and_parameter_methods_are_not_query_api(self):
  with TemporaryDirectory() as d:
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl'
   p.write_text('''метод ProjectApi(): Объект
    возврат Helper.Выполнить()
;
''');(root/'Entry/Helper.xbsl').write_text('''@ВПроекте
метод Выполнить(): Число
    возврат 12
;
''')
   c=check(method='ProjectApi');p=plan_execution(root,analyze(root),c)
   self.assertFalse(p.queries);self.assertFalse(any(b.category=='query-api' for b in p.bindings));self.assertTrue(any(b.operation=='Выполнить' and b.category=='project' for b in p.bindings))

 def test_local_project_constructor_spelling_is_excluded(self):
  with TemporaryDirectory() as d:
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root)
   (root/'Entry/Main.xbsl').write_text('структура ПроизвольныйЗапрос\n    пер Text: Строка\n;\nметод ProjectConstructor(): Объект\n    возврат новый ПроизвольныйЗапрос("project")\n;\n')
   c=check(method='ProjectConstructor');c.pop('queryResults')
   p=plan_execution(root,analyze(root),c)
   self.assertFalse(p.queries);self.assertFalse(p.contracts.query_results)
   self.assertIn('ПроизвольныйЗапрос',p.contracts.required_structures)

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Task44 Docker opt-in')
class ResultDockerTest(unittest.TestCase):
 def execute(self,root,c,temp,label):
  r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/(label+'.json'),r);self.assertEqual(r['status'],'EXECUTED',(label,r));return r
 def assess(self,root,c,temp,label,answer):
  r=self.execute(root,c,temp,label);exp=expected(c,answer);status=grade(r['actual'],exp,temp)
  write_json(EVIDENCE/('assessment-'+label+'.json'),{'status':status,'engine':'SBSL','criterionId':c['target']['method'],'sourceHash':analyze(root)['sourceHash'],'runtime':str((EVIDENCE/(label+'.json')).relative_to(REPO))})
  self.assertEqual(status,'PASS',(label,r));return r
 def test_two_portable_projects_and_constructor_defaults(self):
  with TemporaryDirectory() as d:
   for v in ('ordinary','daily'):
    for method,result in ANSWERS.items():
     if method=='State' or v=='daily' and method in ('Dynamic','ArrayParameter'):continue
     with self.subTest(variant=v,method=method):self.assess(CORPUS/v,check(v,method),Path(d),v+'-'+method,result)
 def test_state_memory_snapshot_rollback(self):
  with TemporaryDirectory() as d:
   r=self.assess(CORPUS/'ordinary',check(method='State'),Path(d),'state-memory-repeat',ANSWERS['State'])
   self.assertNotIn('source:commit',r['storageTrace'])
 def test_result_empty_explicit_scope_close_return_break_throw_and_nested(self):
  with TemporaryDirectory() as d:
   for method,answer,opened in [('Loop',1,1),('Early','A',1),('Break',1,1),('Exception','caught',1),('Nested',ANSWERS['Nested'],2),('Closed','closed',1),('Repeated',ANSWERS['Repeated'],1)]:
    r=self.assess(CORPUS/'ordinary',check(method=method),Path(d),'resource-'+method,answer)
    trace=r['storageTrace'];self.assertEqual(trace.count('query-result:open'),opened);self.assertEqual(trace.count('query-result:close'),opened)
   for method,answer in [('Produce',[]),('Loop',0),('Early','empty'),('Break',0),('Closed','closed'),('Repeated',{'first':[],'repeat':'closed'}),('ResultTemplate',None)]:
    r=self.assess(CORPUS/'ordinary',check(method=method,empty=True),Path(d),'empty-'+method,answer)
    self.assertEqual(r['storageTrace'].count('query-result:open'),r['storageTrace'].count('query-result:close'))
 def test_exact_review_templates_independent_oracles(self):
  with TemporaryDirectory() as d:
   for method,result in [('fill',[card('A'),card('B')]),('generate',[{'Label':'A'},{'Label':'B'}]),('combined_fill_slice_order',[card('B')]),('result_resources',{'Label':'A'})]:
    c=check('daily' if method=='combined_fill_slice_order' else 'ordinary',method);c['args']=['2026-09-30'] if method=='combined_fill_slice_order' else []
    if method=='result_resources':c['storage']['initial']=c['storage']['initial'][:1]
    self.assess(CORPUS/'documented',c,Path(d),'documented-'+method,result)
 def test_two_real_direct_roots_unchanged_archive(self):
  with TemporaryDirectory() as d,open_project(REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump') as root:
   for label,c,exp in real_cases():
    r=self.execute(root,c,Path(d),'real-'+label);self.assertEqual(grade(r['actual'],exp,Path(d)),'PASS')
    write_json(EVIDENCE/('assessment-real-'+label+'.json'),{'status':'PASS','engine':'SBSL','criterionId':label,'sourceHash':analyze(root)['sourceHash'],'runtime':str((EVIDENCE/('real-'+label+'.json')).relative_to(REPO))})
 def test_business_mutations_alternative_and_infrastructure_recovery(self):
  changes=[('Produce','ВЫБРАТЬ Label ПОРОДИТЬ Row','ВЫБРАТЬ Label КАК Wrong ПОРОДИТЬ Row'),('Early','УПОРЯДОЧИТЬ ПО Label','УПОРЯДОЧИТЬ ПО Label УБЫВ'),('Dynamic','УстановитьПараметр("Name", K)','УстановитьПараметр("Name", "B")'),('Loop','Row.Total == 3','Row.Total == 1'),('Closed','    R.Закрыть()\n',''),('Local','Число = 4','Число = 5')]
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text()
   for method,old,new in changes:
    p.write_text(base.replace(old,new));c=check(method=method)
    r=self.execute(root,c,temp,'mutation-'+method);status=grade(r['actual'],expected(c,ANSWERS[c['target']['method']]),temp)
    self.assertEqual(status,'FAIL');write_json(EVIDENCE/('mutation-assessment-'+method+'.json'),{'status':status,'engine':'SBSL'})
   p.write_text(base.replace('Row.Total == 3','3 == Row.Total'));self.assess(root,check(method='Loop'),temp,'alternative',1)
   p.write_text(base)
   import element_test.storage as storage
   native=storage.session_module
   def broken(*a,**k):return native(*a,**k).replace('исп Поток = Ф.ОткрытьПотокЧтения()', 'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("task44 infrastructure injection")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
   with patch('element_test.storage.session_module',side_effect=broken):r=run_pure(root,analyze(root),check(),temp)
   write_json(EVIDENCE/'infrastructure-failure.json',r);self.assertEqual(r['status'],'ERROR')
   self.assess(root,check(),temp,'infrastructure-recovery',ANSWERS['Produce'])

@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task44 PostgreSQL opt-in')
class ResultSqlTest(unittest.TestCase):
 def test_sql_snapshots_rollback_audit_cleanup(self):
  from element_test.integration import run_integration
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for variant,method in [('ordinary','Dynamic'),('ordinary','State'),('daily','Produce')]:
    c=check(variant,method,sql=True);r=run_integration(c,analyze(CORPUS/variant),Path(d),root=CORPUS/variant,enabled=True,inject_failure=True)
    write_json(EVIDENCE/('sql-'+variant+'-'+method+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],expected(c,ANSWERS[method]),Path(d)),'PASS');self.assertNotIn('source:commit',r['storageTrace'])
   with open_project(REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump') as root:
    label,c,exp=real_cases()[0];c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
    r=run_integration(c,analyze(root),Path(d),root=root,enabled=True,inject_failure=True);write_json(EVIDENCE/'sql-real.json',r);self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],exp,Path(d)),'PASS')

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Task44 public Docker opt-in')
class ResultPublicTest(unittest.TestCase):
 def test_public_test_run_fail_unsupported_pass(self):
  for mode in ('test','run'):
   folder=EVIDENCE/('public-real-'+mode);p=subprocess.run([str(REPO/'bin/element-test'),mode,'--project',str(REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump'),'--assignment',str(REPO/'assignments/result-real'),'--output',str(folder)],capture_output=True,text=True,timeout=900)
   (EVIDENCE/('public-real-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-real-'+mode+'.stderr')).write_text(p.stderr);self.assertEqual(p.returncode,0,p.stdout+p.stderr)
   r=json.loads((folder/'result.json').read_text());self.assertEqual(r['score'],2);self.assertTrue(all(c['status']=='PASS' for c in r['checks']))
  r,g=run_test(CORPUS/'ordinary',REPO/'assignments/result-control',EVIDENCE/'public-control');self.assertEqual([c['status'] for c in r['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(g['unavailablePoints'],1)
 def test_two_isolated_batches(self):
  from element_test.batch import run_batch
  with TemporaryDirectory() as d:
   temp=Path(d);assignment=EVIDENCE/'batch-assignment';assignment.mkdir(exist_ok=True);checks=[]
   for method in ('Produce','Dynamic','Closed'):
    c=check(method=method);checks.append({'id':method,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':expected(c,ANSWERS[method])})
   (assignment/'assignment.yaml').write_text(dump_yaml({'name':'Batch44','checks':checks},allow_unicode=True,sort_keys=False));submissions=[]
   for label in ('correct','mutated','neighbor'):
    root=EVIDENCE/'batch-inputs'/label;shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
    if label=='mutated':
     p=root/'Entry/Main.xbsl';p.write_text(p.read_text().replace('ВЫБРАТЬ Label ПОРОДИТЬ Row','ВЫБРАТЬ Label КАК Wrong ПОРОДИТЬ Row').replace('УстановитьПараметр("Name", K)','УстановитьПараметр("Name", "B")').replace('    R.Закрыть()\n',''))
    submissions.append({'studentId':label,'project':str(root)})
   manifest=EVIDENCE/'batch-manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'result-044','submissions':submissions})
   saved=[]
   for n in (1,2):
    folder=EVIDENCE/f'batch-{n}';suffix=1
    while folder.exists():folder=EVIDENCE/f'batch-{n}-repeat-{suffix}';suffix+=1
    with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/f'cache-{n}')}):code=run_batch(manifest,folder,2)
    self.assertEqual(code,1);data=json.loads((folder/'batch-result.json').read_text());self.assertEqual([s['score'] for s in data['submissions']],[3,0,3]);self.assertFalse(any(s['cacheHit'] for s in data['submissions']));saved.append(str(folder.relative_to(REPO)))
   write_json(EVIDENCE/'batch-latest.json',saved)
