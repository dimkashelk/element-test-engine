"""Stage 45 executable contracts, independent grading and preserved receipts."""
import copy,json,os,secrets,shutil,subprocess,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from state_fixtures import *
from element_test.model import analyze
from element_test.execution_plan import plan_execution
from element_test.runtime import run_pure
from element_test.bridge import write_json,run_test
from element_test.yaml_io import InputError
from test_query_projections import grade

class StatePlanTest(unittest.TestCase):
 def test_two_projects_queries_ranges_and_no_answers(self):
  for v in ('ordinary','daily'):
   for name in ANSWERS:
    if v=='daily' and name in ('Access','Hierarchy'):continue
    c=check(v,name);c['expected']={'secret-answer':-811211}
    p=plan_execution(CORPUS/v,analyze(CORPUS/v),c);self.assertNotIn('secret-answer',p.to_json())
    q=p.queries[0];source=(CORPUS/v/q['sourceFile']).read_text();self.assertEqual(source[q['start']:q['end']],'Запрос{'+q['text']+'}')
    for parameter in q['ast']['parameters']:
     self.assertEqual(q['text'][parameter['start']:parameter['end']],parameter['expression'])
    write_json(EVIDENCE/('plan-'+v+'-'+name+'.json'),p.to_dict())
 def test_unconfirmed_hierarchy_correlation_platform_writes_fail_closed(self):
  for name in ('UnsupportedHierarchy','PlatformDml'):
   with self.subTest(name=name),self.assertRaises(InputError):plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),check(method=name))
 def test_dml_duplicate_missing_type_and_unknown_tables(self):
  from element_test.query_plan import parse_storage_query
  p=plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),check())
  for text in ('СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число); ВСТАВИТЬ В T (A, A) ЗНАЧЕНИЯ (1,2)', 'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число, B: Строка); ВСТАВИТЬ В T (A) ЗНАЧЕНИЯ (1)', 'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число); ИЗМЕНИТЬ T УСТАНОВИТЬ A = "wrong"', 'УДАЛИТЬ ИЗ Missing'):
   with self.subTest(text=text),self.assertRaises(InputError):parse_storage_query(text,p.contracts)
 def test_access_policy_rejects_ambiguous_owner_or_invalid_id(self):
  for owner,ids in [('Item',[]),('Data::Item',['not-a-uuid'])]:
   c=check(method='Access');c['queryAccess']['sources']={owner:{'read':True,'ids':ids}}
   with self.subTest(owner=owner),self.assertRaises(InputError):plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),c)

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Task45 Docker opt-in')
class StateDockerTest(unittest.TestCase):
 def assess(self,v,c,temp,label,answer):
  r=run_pure(CORPUS/v,analyze(CORPUS/v),c,temp);write_json(EVIDENCE/(label+'.json'),r);self.assertEqual(r['status'],'EXECUTED',(label,r))
  status=grade(r['actual'],expected(c,answer),temp);self.assertEqual(status,'PASS',(label,r))
  write_json(EVIDENCE/('assessment-'+label+'.json'),{'status':status,'engine':'SBSL','criterionId':c['target']['method'],'sourceHash':analyze(CORPUS/v)['sourceHash'],'runtime':str((EVIDENCE/(label+'.json')).relative_to(REPO))});return r
 def test_two_projects_independent_answers(self):
  with TemporaryDirectory() as d:
   for v in ('ordinary','daily'):
    for name,answer in ANSWERS.items():
     if v=='daily' and name in ('Access','Hierarchy'):continue
     with self.subTest(variant=v,method=name):self.assess(v,check(v,name),Path(d),v+'-'+name,answer)
 def test_between_boundaries_reversed_negative_null_and_empty(self):
  with TemporaryDirectory() as d:
   for low,high,answer in [(1,1,[{'Label':'B'}]),(3,3,[{'Label':'A'}]),(2,2,[]),(3,1,[]),(-3,0,[])]:
    c=check();c['args']=[low,high];self.assess('ordinary',c,Path(d),f'boundary-{low}-{high}',answer)
   self.assess('ordinary',check(empty=True),Path(d),'empty',[])
 def test_like_case_wildcards_classes_escapes_and_bad_patterns(self):
  with TemporaryDirectory() as d:
   for pattern,answer in [('A',[{'Label':'A'}]),('%',[{'Label':'A'},{'Label':'B'}]),('_',[{'Label':'A'},{'Label':'B'}]),('[ab]',[{'Label':'A'},{'Label':'B'}]),('[^a]',[{'Label':'B'}]),('a%', [{'Label':'A'}]),('z%',[]),('\\%',[])]:
    c=check(method='Like');c['args']=[pattern];self.assess('ordinary',c,Path(d),'pattern-'+str(len(list(EVIDENCE.glob('pattern-*')))),answer)
   for pattern in ('','[]','[a','a\\'):
    c=check(method='Like');c['args']=[pattern];r=run_pure(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),c,Path(d));write_json(EVIDENCE/('bad-pattern-'+str(len(pattern))+'.json'),r);self.assertEqual(r['status'],'ERROR',r)
 def test_state_repeated_snapshots_and_rollback(self):
  from result_fixtures import ANSWERS as old_answers
  with TemporaryDirectory() as d:
   r=self.assess('ordinary',check(method='State'),Path(d),'state-memory',old_answers['State']);self.assertNotIn('source:commit',r['storageTrace'])
   # A temporary mutation is reconstructed separately on every Execute.
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text();q=QUERIES['Mutate']
   p.write_text(base+'метод Twice(): Объект\n    знч Q = Запрос{'+q+'}\n    исп First = Q.Выполнить()\n    знч Old = First.ВМассив()\n    исп Again = Q.Выполнить()\n    возврат {"old": Old, "again": Again.ВМассив()}\n;\n')
   c=check(method='Twice');r=run_pure(root,analyze(root),c,Path(d));write_json(EVIDENCE/'repeat-temp.json',r);self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],expected(c,{'old':ANSWERS['Mutate'],'again':ANSWERS['Mutate']}),Path(d)),'PASS')
 def test_access_deny_empty_neighbor_and_union(self):
  with TemporaryDirectory() as d:
   c=check(method='Access');c['queryAccess']['sources']['Data::Item']['ids']=[];self.assess('ordinary',c,Path(d),'access-none',[])
   c['queryAccess']['sources']['Data::Item']['read']=False
   r=run_pure(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),c,Path(d));write_json(EVIDENCE/'access-denied.json',r);self.assertEqual(r['status'],'ERROR',r)
   c=check(method='Combined');c['queryAccess']=check(method='Access')['queryAccess'];self.assess('ordinary',c,Path(d),'access-combined',[{'Label':'A','Amount':6}])
 def test_hierarchy_transitive_self_missing_cycle_and_correlated_exists(self):
  with TemporaryDirectory() as d:
   c=check('hierarchy','Hierarchy');self.assess('hierarchy',c,Path(d),'hierarchy-transitive',[{'Label':'A'},{'Label':'B'},{'Label':'C'}])
   c['args']=[REFS[1]];self.assess('hierarchy',c,Path(d),'hierarchy-self',[{'Label':'B'},{'Label':'C'}])
   c['args']=[{'Идентификатор':'44444444-4444-4444-8444-444444444444'}];self.assess('hierarchy',c,Path(d),'hierarchy-missing',[])
   c['args']=[REFS[0]];c['storage']['initial'][0]['value']['Родитель']=copy.deepcopy(REFS[1]);self.assess('hierarchy',c,Path(d),'hierarchy-cycle',[{'Label':'A'},{'Label':'B'},{'Label':'C'}])
   c=check(method='Correlated');c['storage']['initial'][-1]['value']['Label']='A';self.assess('ordinary',c,Path(d),'correlated-match',[{'Label':'A'}])
 def test_mutations_correct_alternative_and_infrastructure_recovery(self):
  changes=[('Between','Total МЕЖДУ %Low И %High','Total МЕЖДУ 2 И %High'),('Like','Label ПОДОБНО %Pattern','Label НЕ ПОДОБНО %Pattern'),('Mutate','Total + 10','Total + 11'),('DeleteCount','Total == 3','Total == 999'),('Exists','Total МЕЖДУ 1 И 4','Total МЕЖДУ 90 И 100'),('Distinct','НЕ ОТЛИЧАЕТСЯ ОТ NULL','ОТЛИЧАЕТСЯ ОТ NULL')]
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text()
   for name,old,new in changes:
    p.write_text(base.replace(old,new));c=check(method=name);r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/('mutation-'+name+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r)
    status=grade(r['actual'],expected(c,ANSWERS[name]),temp);self.assertEqual(status,'FAIL');write_json(EVIDENCE/('mutation-assessment-'+name+'.json'),{'status':status,'engine':'SBSL'})
   p.write_text(base.replace('Total МЕЖДУ %Low И %High','Total >= %Low И Total <= %High'));c=check();r=run_pure(root,analyze(root),c,temp);self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],expected(c,ANSWERS['Between']),temp),'PASS');write_json(EVIDENCE/'alternative.json',r)
   from element_test.storage import session_module
   def broken(*a,**k):return session_module(*a,**k).replace('исп Поток = Ф.ОткрытьПотокЧтения()', 'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("stage45 infrastructure")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
   with patch('element_test.storage.session_module',side_effect=broken):r=run_pure(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),check(),temp)
   write_json(EVIDENCE/'infrastructure-failure.json',r);self.assertEqual(r['status'],'ERROR');self.assess('ordinary',check(),temp,'infrastructure-recovery',ANSWERS['Between'])

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Task45 documented/real Docker opt-in')
class StateSourceTest(unittest.TestCase):
 def test_exact_review_templates(self):
  cases=[('ordinary',[],[{'Name':'A'},{'Name':'B'}]),('parameters',['A'],[{'Label':'A'}]),('captured_expression',['B'],[{'Label':'B'}]),('order_limit',[],[{'Label':'A'},{'Label':'B'}]),('in',[[1]],[{'Label':'B'}]),('between',[1,3],[{'Label':'A'},{'Label':'B'}]),('like',['a'],[{'Label':'A'}])]
  with TemporaryDirectory() as d:
   for method,args,answer in cases:
    c=check(method=method);c['args']=args
    r=run_pure(CORPUS/'documented',analyze(CORPUS/'documented'),c,Path(d));write_json(EVIDENCE/('documented-'+method+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],expected(c,answer),Path(d)),'PASS')
    write_json(EVIDENCE/('assessment-documented-'+method+'.json'),{'status':'PASS','engine':'SBSL','criterionId':method,'sourceHash':analyze(CORPUS/'documented')['sourceHash'],'runtime':str((EVIDENCE/('documented-'+method+'.json')).relative_to(REPO))})
 def test_direct_real_roots(self):
  from element_test.loader import open_project
  with TemporaryDirectory() as d,open_project(REPO/'Demo-SRM-dev-2026-09-28-21-38.xdump') as root:
   for label,c,exp in real_cases():
    r=run_pure(root,analyze(root),c,Path(d));write_json(EVIDENCE/('real-'+label+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],exp,Path(d)),'PASS')

@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task45 PostgreSQL opt-in')
class StateSqlTest(unittest.TestCase):
 def test_sql_data_queries_access_snapshots_rollback_and_cleanup(self):
  from element_test.integration import run_integration
  from result_fixtures import ANSWERS as old
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for v,name in [('ordinary','State'),('ordinary','Mutate'),('ordinary','Access'),('daily','Combined'),('hierarchy','Hierarchy')]:
    c=check(v,name,sql=True);r=run_integration(c,analyze(CORPUS/v),Path(d),root=CORPUS/v,enabled=True,inject_failure=True);write_json(EVIDENCE/('sql-'+v+'-'+name+'.json'),r)
    self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],expected(c,[{'Label':'A'},{'Label':'B'},{'Label':'C'}] if v=='hierarchy' else (old if name=='State' else ANSWERS)[name]),Path(d)),'PASS');self.assertNotIn('source:commit',r['storageTrace'])

@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task45 real PostgreSQL opt-in')
class StateRealSqlTest(unittest.TestCase):
 def test_direct_real_root_sql_audit_and_cleanup(self):
  from element_test.integration import run_integration
  from element_test.loader import open_project
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}),open_project(REPO/'Demo-SRM-dev-2026-09-28-21-38.xdump') as root:
   name,c,answer=real_cases(sql=True)[0]
   r=run_integration(c,analyze(root),Path(d),root=root,enabled=True,inject_failure=True);write_json(EVIDENCE/'sql-real.json',r);self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],answer,Path(d)),'PASS')

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Task45 public Docker opt-in')
class StatePublicTest(unittest.TestCase):
 def test_public_real_test_run_and_control(self):
  for mode in ('test','run'):
   folder=EVIDENCE/('public-real-'+mode)
   p=subprocess.run([str(REPO/'bin/element-test'),mode,'--project',str(REPO/'Demo-SRM-dev-2026-09-28-21-38.xdump'),'--assignment',str(REPO/'assignments/state-real'),'--output',str(folder)],capture_output=True,text=True,timeout=900)
   (EVIDENCE/('public-real-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-real-'+mode+'.stderr')).write_text(p.stderr);self.assertEqual(p.returncode,0,p.stdout+p.stderr)
   r=json.loads((folder/'result.json').read_text());self.assertEqual(r['score'],2);self.assertTrue(all(c['status']=='PASS' for c in r['checks']))
  r,g=run_test(CORPUS/'ordinary',REPO/'assignments/state-control',EVIDENCE/'public-control');self.assertEqual([c['status'] for c in r['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(g['unavailablePoints'],1)
 def test_two_isolated_batches(self):
  from element_test.batch import run_batch
  with TemporaryDirectory() as d:
   temp=Path(d);assignment=EVIDENCE/'batch-assignment';assignment.mkdir(exist_ok=True);checks=[]
   for name in ('Between','Mutate','Access'):
    c=check(method=name);checks.append({'id':name,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':expected(c,ANSWERS[name])})
   (assignment/'assignment.yaml').write_text(dump_yaml({'name':'Batch45','checks':checks},allow_unicode=True,sort_keys=False));submissions=[]
   for label in ('correct','mutated','neighbor'):
    root=EVIDENCE/'batch-inputs'/label;shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
    if label=='mutated':
     p=root/'Entry/Main.xbsl';p.write_text(p.read_text().replace('Total МЕЖДУ %Low И %High','Total МЕЖДУ 2 И %High').replace('Total + 10','Total + 11').replace('ВЫБРАТЬ Label ИЗ Data::Item}.Выполнить()','ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == "never"}.Выполнить()'))
    submissions.append({'studentId':label,'project':str(root)})
   manifest=EVIDENCE/'batch-manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'state-045','submissions':submissions})
   saved=[]
   for n in (1,2):
    folder=EVIDENCE/f'batch-{n}';suffix=1
    while folder.exists():folder=EVIDENCE/f'batch-{n}-repeat-{suffix}';suffix+=1
    with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/f'cache-{n}')}):code=run_batch(manifest,folder,2)
    self.assertEqual(code,1);data=json.loads((folder/'batch-result.json').read_text());self.assertEqual([s['score'] for s in data['submissions']],[3,0,3]);self.assertFalse(any(s['cacheHit'] for s in data['submissions']));saved.append(str(folder.relative_to(REPO)))
   write_json(EVIDENCE/'batch-latest.json',saved)

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Task45 edge Docker opt-in')
class StateEdgeTest(unittest.TestCase):
 def test_conditional_assignments_counts_duplicate_rows_and_exists_union(self):
  cases=[('Lazy','ВЫБРАТЬ Total ПОМЕСТИТЬ T ИЗ Data::Item; ИЗМЕНИТЬ T УСТАНОВИТЬ Total = 10 / (Total - 1) ГДЕ Total == 3; ВЫБРАТЬ Total ИЗ T УПОРЯДОЧИТЬ ПО Total',[{'Total':1},{'Total':5}]),
         ('Duplicate','ВЫБРАТЬ Label ПОМЕСТИТЬ T ИЗ Data::Item; ВСТАВИТЬ В T (Label) ЗНАЧЕНИЯ ("A"); УДАЛИТЬ ИЗ T ГДЕ Label == "A"',[{'КоличествоЗаписей':2}]),
         ('ExistsUnion','ВЫБРАТЬ Label ИЗ Data::Item ГДЕ СУЩЕСТВУЕТ (ВЫБРАТЬ 1 ИЗ Other::Item ГДЕ Total == 999 ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ 1 ИЗ Data::Item ГДЕ Total == 3)',[{'Label':'A'},{'Label':'B'}]),
         ('InUnion','ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label В (ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Total == 1 ОБЪЕДИНИТЬ ВЫБРАТЬ Label ИЗ Other::Item)',[{'Label':'B'}]),
         ('NullBounds','ВЫБРАТЬ X.Label ИЗ Data::Item КАК X ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК Y ПО X.Label == Y.Label ГДЕ Y.Total МЕЖДУ 0 И 100',[]),
         ('MixedDistinct','ВЫБРАТЬ Label ИЗ Data::Item ГДЕ 42 ОТЛИЧАЕТСЯ ОТ "42"',[{'Label':'A'},{'Label':'B'}])]
  with TemporaryDirectory() as d:
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text()
   for name,q,answer in cases:
    p.write_text(base+'метод '+name+'(): Объект\n    возврат Запрос{'+q+'}.Выполнить().ВМассив()\n;\n')
    c=check(method=name);r=run_pure(root,analyze(root),c,Path(d));write_json(EVIDENCE/('edge-'+name+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],expected(c,answer),Path(d)),'PASS')
 def test_invalid_pattern_on_empty_source_and_custom_escape(self):
  with TemporaryDirectory() as d:
   c=check(method='Like',empty=True);c['args']=['[]'];r=run_pure(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),c,Path(d));write_json(EVIDENCE/'bad-pattern-empty.json',r);self.assertEqual(r['status'],'ERROR',r)
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';p.write_text(p.read_text().replace('Label ПОДОБНО %Pattern','Label ПОДОБНО %Pattern СПЕЦСИМВОЛ "!"'))
   c=check(method='Like');c['storage']['initial'][0]['value']['Label']='A%';c['args']=['a!%'];r=run_pure(root,analyze(root),c,Path(d));write_json(EVIDENCE/'custom-escape.json',r);self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(grade(r['actual'],expected(c,[{'Label':'A%'}]),Path(d)),'PASS')

class CorrelationGuardTest(unittest.TestCase):
 def test_outer_capture_names_ranges_and_shadowed_alias(self):
  from element_test.query_plan import parse_storage_query
  c=plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),check()).contracts
  text='ВЫБРАТЬ X.Label ИЗ Data::Item КАК X ГДЕ СУЩЕСТВУЕТ (ВЫБРАТЬ 1 ИЗ Other::Item КАК Y ГДЕ Y.Label == X.Label И Y.Total == %__Outer45_0_Label)'
  q=parse_storage_query(text,c);self.assertEqual([p.expression for p in q.parameters],['__Outer45_0_Label'])
  child=q.subqueries[0];self.assertEqual(child.outer_parameters[0][0],'__Outer45_0_Label_')
  for parameter in q.parameters:self.assertEqual(text[parameter.start:parameter.end],parameter.expression)
  shadow='ВЫБРАТЬ X.Label ИЗ Data::Item КАК X ГДЕ СУЩЕСТВУЕТ (ВЫБРАТЬ 1 ИЗ (ВЫБРАТЬ Total ИЗ Other::Item) КАК X ГДЕ X.Label == "A")'
  with self.assertRaises(InputError):parse_storage_query(shadow,c)
