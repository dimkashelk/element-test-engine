"""Task 41 independent semantic, mutation, storage and public acceptance tests."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from projection_fixtures import *
from element_test.bridge import write_json,run_test
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_plan import parse_storage_query
from element_test.runtime import run_pure,execute_engine
from element_test.yaml_io import InputError


def grade(actual,exp,temp,ordered=False):
 write_json(temp/'model.json',{'elements':[],'modules':[]})
 write_json(temp/'assignment.json',{'checks':[{'id':'independent','type':'runtime','points':1,'comparison':'query-rows-unordered' if isinstance(exp.get('result'),list) and not ordered else 'record-sets-unordered','expected':exp,'execution':{'status':'EXECUTED','actual':actual}}]})
 return execute_engine('test',temp/'model.json',temp/'assignment.json',temp)['checks'][0]['status']


class ProjectionPlanTest(unittest.TestCase):
 def test_portable_typed_ast_ranges_group_aliases_and_no_teacher_answers(self):
  for v in ('ordinary','renamed'):
   for m in ANSWERS:
    c=portable_check(v,m);c['expected']={'secret-answer':981273}
    p=plan_execution(CORPUS/v,analyze(CORPUS/v),c)
    self.assertNotIn('secret-answer',p.to_json());self.assertEqual(p.queries[0]['ast']['mode'],'storage-projections-aggregates-v1')
    for q in p.queries:
     for arg in q['ast']['parameters']:self.assertEqual(q['text'][arg['start']:arg['end']],arg['expression'])
     for source in q['ast']['sources']:
      a,b=source['source_range'];self.assertIn(source['source_name'],q['text'][a:b])

 def test_invalid_group_types_aggregates_casts_and_distinct_are_unavailable(self):
  c=ProjectTypes(analyze(CORPUS/'ordinary'),'Entry');c.rename_collisions=True
  bad=['ВЫБРАТЬ Label, СУММА(Amount) КАК Total ИЗ Data::Item',
       'ВЫБРАТЬ СУММА(СУММА(Amount)) КАК Total ИЗ Data::Item',
       'ВЫБРАТЬ СУММА(Label) КАК Total ИЗ Data::Item',
       'ВЫБРАТЬ Amount + Label КАК Total ИЗ Data::Item',
       'ВЫБРАТЬ ВЫРАЗИТЬ(Amount КАК Строка) КАК Total ИЗ Data::Item',
       'ВЫБРАТЬ РАЗЛИЧНЫЕ Key ИЗ Data::Item УПОРЯДОЧИТЬ ПО Amount',
       'ВЫБРАТЬ СУММА(Amount) КАК Total ИЗ Data::Item ГДЕ СУММА(Amount) > 0',
       'ВЫБРАТЬ СУММА(Amount) КАК Total ИЗ Data::Item СГРУППИРОВАТЬ ПО СУММА(Amount)',
       'ВЫБРАТЬ ВЫБОР КОГДА Flag ТОГДА Amount ИНАЧЕ Label КОНЕЦ КАК Total ИЗ Data::Item',
       'ВЫБРАТЬ ВЫРАЗИТЬ(Peer КАК Other::Item.Ссылка) КАК Total ИЗ Data::Item']
  for text in bad:
   with self.subTest(text=text),self.assertRaises(InputError):parse_storage_query(text,c)

 def test_decimal_source_precision_parameter_types_and_capture_order(self):
  c=ProjectTypes(analyze(CORPUS/'ordinary'),'Entry');c.rename_collisions=True
  q=parse_storage_query('ВЫБРАТЬ Amount + 0.1000000000000000000001 КАК Value ИЗ Data::Item',c)
  self.assertEqual(q.projections[0][0].children[1].value,'0.1000000000000000000001')
  p=plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),portable_check(method='Params'))
  args=p.queries[0]['ast']['parameters'];self.assertEqual([a['expression'] for a in args],['Bonus','Threshold','Bonus','Min']);self.assertEqual([a['slot'] for a in args],[0,1,0,2])


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 41 Script Docker opt-in')
class ProjectionDockerTest(unittest.TestCase):
 def execute(self,root,c,temp,label):
  r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/(label+'.json'),r)
  self.assertEqual(r['status'],'EXECUTED',r);return r

 def test_portable_computed_aggregate_group_distinct_case_cast_fill_and_params(self):
  with TemporaryDirectory() as d:
   for variant in ('ordinary','renamed'):
    for method in (m for m in ANSWERS if m not in ('NullUndefined','CountUndefined','Precision','DistinctLimit')):
     c=portable_check(variant,method);r=self.execute(CORPUS/variant,c,Path(d),variant+'-'+method)
     self.assertEqual(grade(r['actual'],expected(c,answer(variant,method)),Path(d)),'PASS',(variant,method,r))

 def test_empty_global_aggregates_and_empty_groups(self):
  with TemporaryDirectory() as d:
   for method in ('Aggregate','Grouped','Having','Distinct','JoinGroup'):
    c=portable_check(method=method,empty=True);r=self.execute(CORPUS/'ordinary',c,Path(d),'empty-'+method)
    self.assertEqual(grade(r['actual'],expected(c,EMPTY if method=='Aggregate' else []),Path(d)),'PASS',r)

 def test_real_direct_roots(self):
  with TemporaryDirectory() as d:
   temp=Path(d)
   with open_project(REPO/'Demo-SRM-dev-2026-09-28-21-38.xdump') as root:
    for empty in (False,True):
     c=real_task_check(empty);r=self.execute(root,c,temp,'real-tasks-'+str(empty))
     self.assertEqual(grade(r['actual'],expected(c,{'Запланировано':0,'ВПроцессе':0} if empty else REAL_TASK),temp),'PASS',r)
   with open_project(REPO/'autocheck-2026-09-24-16-14.xdump','dimkashelk::check') as root:
    c=real_max_check();r=self.execute(root,c,temp,'real-max')
    self.assertEqual(grade(r['actual'],expected(c,17),temp),'PASS',r)

 def test_mutations_alternative_correct_source_and_recovery(self):
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text()
   for label,a,b,method in [('multiply','Amount * 2 +','Amount * 3 +','Computed'),('case','КОГДА Flag ТОГДА Amount','КОГДА Flag ТОГДА 0','Computed'),('count','КОЛИЧЕСТВО(*) КАК Count','КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Key) КАК Count','Aggregate'),('sum','СУММА(Amount) КАК Sum','МАКСИМУМ(Amount) КАК Sum','Aggregate'),('having','СУММА(Amount) >= 4','СУММА(Amount) >= 5','Having'),('join','ЛЕВОЕ СОЕДИНЕНИЕ','ВНУТРЕННЕЕ СОЕДИНЕНИЕ','JoinGroup'),('distinct','ВЫБРАТЬ РАЗЛИЧНЫЕ Key','ВЫБРАТЬ Key','Distinct')]:
    self.assertIn(a,base);p.write_text(base.replace(a,b));c=portable_check(method=method);r=self.execute(root,c,temp,'mutation-'+label)
    self.assertEqual(grade(r['actual'],expected(c,ANSWERS[method]),temp),'FAIL',label)
   # Arithmetic-equivalent implementation is accepted by independent criteria.
   p.write_text(base.replace('Amount * 2 + 0.1 + 0.2','(Amount + Amount) + 0.3'));c=portable_check(method='Computed')
   r=self.execute(root,c,temp,'alternative');self.assertEqual(grade(r['actual'],expected(c,ANSWERS['Computed']),temp),'PASS')
   p.write_text(base);r=self.execute(root,c,temp,'mutation-recovery');self.assertEqual(grade(r['actual'],expected(c,ANSWERS['Computed']),temp),'PASS')

 def test_invalid_fixture_timeout_unsupported_backend_failure_then_recovery(self):
  import element_test.storage as storage
  native=storage.session_module
  def failing(*args,**kwargs):
   return native(*args,**kwargs).replace('исп Поток = Ф.ОткрытьПотокЧтения()', 'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("injected task 41 failure")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
  with TemporaryDirectory() as d:
   temp=Path(d);root=CORPUS/'ordinary';c=portable_check();c['storage']['initial'][0]['value']['Amount']='bad'
   r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'invalid.json',r);self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','invalid_test'))
   c=portable_check();c['timeout']=0.001;r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'timeout.json',r);self.assertEqual(r['status'],'TIMEOUT')
   c=portable_check(method='Unsupported');r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'unsupported.json',r);self.assertEqual(r['status'],'UNSUPPORTED')
   c=portable_check()
   with patch('element_test.storage.session_module',side_effect=failing):r=run_pure(root,analyze(root),c,temp)
   write_json(EVIDENCE/'infrastructure-error.json',r);self.assertEqual(r['status'],'ERROR',r)
   r=self.execute(root,c,temp,'infrastructure-recovery');self.assertEqual(grade(r['actual'],expected(c,ANSWERS['Aggregate']),temp),'PASS')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task 41 SQL opt-in')
class ProjectionSqlTest(unittest.TestCase):
 def test_storage_group_join_state_rollback_independent_sql_and_cleanup(self):
  import secrets
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for variant in ('ordinary','renamed'):
    for method in ('JoinGroup','State'):
     c=portable_check(variant,method,sql=True);r=run_integration(c,analyze(CORPUS/variant),Path(d),root=CORPUS/variant,enabled=True,inject_failure=True)
     write_json(EVIDENCE/('sql-'+variant+'-'+method+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
     self.assertEqual(grade(r['actual'],expected(c,answer(variant,method)),Path(d)),'PASS',r)
     self.assertNotIn('source:commit',r['storageTrace'])

 def test_real_roots_independent_sql(self):
  import secrets
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for archive,project,c,result,label in [('Demo-SRM-dev-2026-09-28-21-38.xdump',None,real_task_check(sql=True),REAL_TASK,'tasks'),('autocheck-2026-09-24-16-14.xdump','dimkashelk::check',real_max_check(sql=True),17,'max')]:
    with open_project(REPO/archive,project) as root:
     r=run_integration(c,analyze(root),Path(d),root=root,enabled=True);write_json(EVIDENCE/('sql-real-'+label+'.json'),r)
     self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],expected(c,result),Path(d)),'PASS')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 41 public Docker opt-in')
class ProjectionPublicTest(unittest.TestCase):
 def test_public_real_test_run_and_control(self):
  for label,archive,project,assignment,score in [('tasks','Demo-SRM-dev-2026-09-28-21-38.xdump',None,'projections-real-tasks',2),('max','autocheck-2026-09-24-16-14.xdump','dimkashelk::check','projections-real-max',1)]:
   for mode in ('test','run'):
    folder=EVIDENCE/('public-'+label+'-'+mode)
    cmd=[str(REPO/'bin/element-test'),mode,'--project',str(REPO/archive),'--assignment',str(REPO/'assignments'/assignment),'--output',str(folder)]
    if project:cmd+=['--project-name',project]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
    (EVIDENCE/('public-'+label+'-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-'+label+'-'+mode+'.stderr')).write_text(p.stderr)
    self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout);result=json.loads((folder/'result.json').read_text());self.assertEqual(result['score'],score)
    self.assertTrue(all(c['status']=='PASS' for c in result['checks']))
  result,package=run_test(CORPUS/'ordinary',REPO/'assignments/projections-control',EVIDENCE/'public-control')
  self.assertEqual([c['status'] for c in result['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(package['unavailablePoints'],1)

 def test_two_fresh_batches_no_student_state_leak_and_schema_packages(self):
  from element_test.batch import run_batch
  with TemporaryDirectory() as d:
   temp=Path(d);checks=[]
   for m in ('Aggregate','Distinct','JoinGroup'):
    c=portable_check(method=m);checks.append({'id':m,'type':'runtime','points':1,'comparison':'query-rows-unordered',**c,'expected':expected(c,ANSWERS[m])})
   import yaml
   class Dumper(yaml.SafeDumper):
    def ignore_aliases(self,data):return True
   assignment=temp/'assignment';assignment.mkdir();(assignment/'assignment.yaml').write_text(yaml.dump({'name':'Batch 41','checks':checks},Dumper=Dumper,allow_unicode=True,sort_keys=False))
   submissions=[];inputs=EVIDENCE/'batch-inputs';inputs.mkdir(exist_ok=True)
   for label in ('correct','mutated','neighbor'):
    root=inputs/label;shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
    if label=='mutated':
     p=root/'Entry/Main.xbsl';p.write_text(p.read_text().replace('СУММА(Amount) КАК Sum','МАКСИМУМ(Amount) КАК Sum').replace('ВЫБРАТЬ РАЗЛИЧНЫЕ Key','ВЫБРАТЬ Key').replace('ЛЕВОЕ СОЕДИНЕНИЕ','ВНУТРЕННЕЕ СОЕДИНЕНИЕ'))
    submissions.append({'studentId':label,'project':str(root)})
   saved=EVIDENCE/'batch-assignment';shutil.copytree(assignment,saved,dirs_exist_ok=True)
   manifest={'schemaVersion':'1.0','assignment':str(saved),'assignmentId':'projections-041','submissions':submissions};path=EVIDENCE/'batch-manifest.json';write_json(path,manifest)
   for n in (1,2):
    folder=EVIDENCE/f'batch-{n}'
    with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/f'cache-{n}')}):code=run_batch(path,folder,2)
    self.assertEqual(code,1)
    packages=[json.loads((folder/'submissions'/f'{i:06d}'/'grading.json').read_text()) for i in (1,2,3)]
    self.assertEqual([p['score'] for p in packages],[3,0,3]);report=json.loads((folder/'batch-result.json').read_text());self.assertFalse(any(s['cacheHit'] for s in report['submissions']))

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 41 edges Docker opt-in')
class ProjectionEdgeTest(unittest.TestCase):
 execute=ProjectionDockerTest.execute
 def test_null_vs_undefined_precision_and_distinct_limit(self):
  with TemporaryDirectory() as d:
   for variant in ('ordinary','renamed'):
    for method in ('NullUndefined','CountUndefined','Precision','DistinctLimit'):
     c=portable_check(variant,method);r=self.execute(CORPUS/variant,c,Path(d),'edge-'+variant+'-'+method)
     self.assertEqual(grade(r['actual'],expected(c,answer(variant,method)),Path(d),ordered=method=='DistinctLimit'),'PASS',r)

 def test_rounding_overflow_null_coalesce_and_dead_case_branch(self):
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl'
   with p.open('a') as f:
    f.write('метод NullSum(): Объект\n    возврат Запрос{ВЫБРАТЬ СУММА(Amount).ЗаменитьNull(0) КАК Value ИЗ Data::Item}.Выполнить()\n;\n')
    f.write('метод Overflow(): Объект\n    возврат Запрос{ВЫБРАТЬ ВЫРАЗИТЬ(9.995 КАК Число(1, 2)) КАК Value ИЗ Data::Item}.Выполнить()\n;\n')
   c=portable_check(method='NullSum',empty=True);r=self.execute(root,c,temp,'empty-coalesced-sum');self.assertEqual(grade(r['actual'],expected(c,[{'Value':0}]),temp),'PASS')
   c=portable_check(method='Overflow');r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'precision-overflow.json',r);self.assertEqual(r['status'],'ERROR',r)

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 41 documented review fixtures')
class ProjectionDocumentedTest(unittest.TestCase):
 execute=ProjectionDockerTest.execute
 def test_exact_review_template_queries_have_executable_teacher_criteria(self):
  with TemporaryDirectory() as d:
   for method,answer in DOCUMENTED.items():
    c=documented_check(method);r=self.execute(CORPUS/'documented',c,Path(d),'documented-'+method)
    assessed=grade(r['actual'],expected(c,answer),Path(d));self.assertEqual(assessed,'PASS',r)
    from hashlib import sha256
    source=CORPUS/'documented/Entry/Main.xbsl'
    write_json(EVIDENCE/('documented-assessment-'+method+'.json'),{'criterionId':method,'status':assessed,'engine':'SBSL','sourceHash':sha256(source.read_bytes()).hexdigest(),'projectSourceHash':analyze(CORPUS/'documented')['sourceHash'],'runtime':str((EVIDENCE/('documented-'+method+'.json')).relative_to(REPO)),'expected':expected(c,answer)})
