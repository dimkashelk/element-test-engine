"""Task 42 semantic, isolation and independently graded acceptance."""
import json,os,shutil,subprocess,copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from composite_fixtures import *
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.query_plan import parse_storage_query
from element_test.model import analyze
from element_test.runtime import run_pure
from element_test.integration import run_integration
from element_test.bridge import write_json,run_test
from element_test.yaml_io import InputError
from test_query_projections import grade

class CompositePlanTest(unittest.TestCase):
 def test_nested_capture_ranges_and_teacher_data_excluded(self):
  for variant in ('ordinary','renamed'):
   for method in ANSWERS:
    with self.subTest(variant=variant,method=method):
     c=check(variant,method);c['expected']={'teacher-secret':-978654321};p=plan_execution(CORPUS/variant,analyze(CORPUS/variant),c)
     self.assertNotIn('teacher-secret',p.to_json())
     for q in p.queries:
      for a in q['ast']['parameters']:self.assertEqual(q['text'][a['start']:a['end']],a['expression'])
  p=plan_execution(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),check(method='Shared'))
  self.assertEqual([p['slot'] for p in p.queries[0]['ast']['parameters']],[0,0,0])

 def test_invalid_columns_scopes_correlations_and_indices(self):
  c=ProjectTypes(analyze(CORPUS/'ordinary'),'Entry');c.rename_collisions=True
  bad=['ВЫБРАТЬ Key ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Key, Amount ИЗ Other::Item',
       'ВЫБРАТЬ Key ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Label ИЗ Other::Item',
       'ВЫБРАТЬ Peer ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Ссылка ИЗ Other::Item',
       'ВЫБРАТЬ Q.Missing ИЗ (ВЫБРАТЬ Key ИЗ Data::Item) КАК Q',
       'ВЫБРАТЬ A.Key ИЗ Data::Item КАК A ГДЕ A.Key В (ВЫБРАТЬ B.Key ИЗ Other::Item КАК B ГДЕ B.Key == A.Key)',
       'ВЫБРАТЬ Key ИЗ T; ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item',
       'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item; УНИЧТОЖИТЬ T; ВЫБРАТЬ Key ИЗ T',
       'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item; ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Other::Item',
       'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item; СОЗДАТЬ ИНДЕКС I ДЛЯ T (Missing)',
       'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item; СОЗДАТЬ ИНДЕКС I ДЛЯ T (Key); СОЗДАТЬ ИНДЕКС J ДЛЯ T (Key)',
       'ВЫБРАТЬ Key ИЗ Data::Item ИНДЕКСИРОВАТЬ ПО Key',
       'ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key В (ВЫБРАТЬ Key, Amount ИЗ Other::Item)',
       'ВЫБРАТЬ Key, Amount КАК Sum ЗАПОЛНИТЬ Data::Card ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ NULL КАК Key, Amount КАК Sum ИЗ Other::Item']
  for q in bad:
   with self.subTest(text=q),self.assertRaises(InputError):parse_storage_query(q,c)
  with TemporaryDirectory() as d:
   root=Path(d)/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';original=p.read_text()
   for query in ['ВЫБРАТЬ Key, Amount КАК Sum ЗАПОЛНИТЬ Data::Card ИЗ Data::Item; ВЫБРАТЬ Key ИЗ Other::Item',
                 'ВЫБРАТЬ Q.Key ИЗ (ВЫБРАТЬ Key, Amount КАК Sum ЗАПОЛНИТЬ Data::Card ИЗ Data::Item) КАК Q']:
    p.write_text(original+'\nметод ShadowFill(Data: Строка): Объект\n    возврат Запрос{'+query+'}.Выполнить()\n;\n')
    fixture=check(method='ShadowFill');fixture['args']=['shadow']
    with self.subTest(query=query),self.assertRaisesRegex(InputError,'Затенённый тип ЗАПОЛНИТЬ'):
     plan_execution(root,analyze(root),fixture)

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Script opt-in')
class CompositeDockerTest(unittest.TestCase):
 def execute(self,root,c,temp,label):
  r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/(label+'.json'),r)
  self.assertEqual(r['status'],'EXECUTED',(label,r.get('reason'),r.get('stderr'),r.get('diagnostics')));return r

 def test_portable_semantics_and_independent_sbsl_answers(self):
  with TemporaryDirectory() as d:
   for variant in ('ordinary','renamed'):
    for method in ANSWERS:
     with self.subTest(variant=variant,method=method):
      c=check(variant,method);r=self.execute(CORPUS/variant,c,Path(d),variant+'-'+method)
      self.assertEqual(grade(r['actual'],expected(c,answer(variant,method)),Path(d),ordered=method=='Sorted'),'PASS',(method,r['actual']['result']))

 def test_named_fill_dependencies_after_repair(self):
  with TemporaryDirectory() as d:
   for variant in ('ordinary','renamed'):
    c=check(variant,'Fill');r=self.execute(CORPUS/variant,c,Path(d),variant+'-Fill-repeat');self.assertEqual(grade(r['actual'],expected(c,answer(variant,'Fill')),Path(d)),'PASS')

 def test_empty_sources_union_aggregate_in_and_temporary(self):
  with TemporaryDirectory() as d:
   for method,result in [('Union',[]),('All',[]),('Nested',[]),('NestedNull',[{'Count':0,'Sum':None}]),('Temp',[]),('Create',[]),('UnknownIn',[])]:
    c=check(method=method,empty=True);r=self.execute(CORPUS/'ordinary',c,Path(d),'empty-'+method);self.assertEqual(grade(r['actual'],expected(c,result),Path(d)),'PASS')

 def test_exact_documented_templates_independently_assessed(self):
  with TemporaryDirectory() as d:
   for method,result in [('distinct',[{'Label':x} for x in 'abcd']),('union',[{'Label':x} for x in 'abcdx']),('union_all',[{'Label':x} for x in 'abcdx']),('nested',[{'Label':x} for x in 'abcd']),('compound',[{'Label':'x'}])]:
    c=check(method=method);r=self.execute(CORPUS/'documented',c,Path(d),'documented-'+method);self.assertEqual(grade(r['actual'],expected(c,result),Path(d)),'PASS')
    from hashlib import sha256
    write_json(EVIDENCE/('documented-assessment-'+method+'.json'),{'status':'PASS','engine':'SBSL','criterionId':method,'sourceHash':sha256((CORPUS/'documented/Entry/Main.xbsl').read_bytes()).hexdigest(),'projectSourceHash':analyze(CORPUS/'documented')['sourceHash'],'runtime':'result/unions-nesting/documented-'+method+'.json'})

 def test_mutations_alternative_and_recovery(self):
  mutations=[('Union','ОБЪЕДИНИТЬ ВЫБРАТЬ','ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ'),('All','ОБЪЕДИНИТЬ ВСЕ','ОБЪЕДИНИТЬ'),('Nested','Q.Value >= 2','Q.Value >= 3'),('NestedNull','ЗаменитьNull(10)','ЗаменитьNull(0)'),('Temp','Value > 1','Value > 2'),('Compound','ВЫБРАТЬ Label ИЗ Other::Item','ВЫБРАТЬ Label ИЗ Data::Item'),('TempJoin','СУММА(T.Amount)','МАКСИМУМ(T.Amount)')]
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';original=p.read_text()
   for method,old,new in mutations:
    p.write_text(original.replace(old,new));c=check(method=method);r=self.execute(root,c,temp,'mutation-'+method);status=grade(r['actual'],expected(c,ANSWERS[method]),temp);self.assertEqual(status,'FAIL');write_json(EVIDENCE/('mutation-assessment-'+method+'.json'),{'status':status,'engine':'SBSL'})
   p.write_text(original.replace('Q.Value * 2','Q.Value + Q.Value'));c=check(method='Nested');r=self.execute(root,c,temp,'alternative');self.assertEqual(grade(r['actual'],expected(c,ANSWERS['Nested']),temp),'PASS')
   p.write_text(original);r=self.execute(root,c,temp,'mutation-recovery');self.assertEqual(grade(r['actual'],expected(c,ANSWERS['Nested']),temp),'PASS')

 def test_invalid_timeout_backend_failure_then_recovery(self):
  import element_test.storage as storage
  native=storage.session_module
  def failing(*a,**k):return native(*a,**k).replace('исп Поток = Ф.ОткрытьПотокЧтения()', 'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("injected task42 failure")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
  with TemporaryDirectory() as d:
   temp=Path(d);root=CORPUS/'ordinary';c=check();c['storage']['initial'][0]['value']['Amount']='bad';r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'invalid.json',r);self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','invalid_test'))
   c=check();c['timeout']=0.001;r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'timeout.json',r);self.assertEqual(r['status'],'TIMEOUT')
   c=check(method='Unsupported');r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'unsupported.json',r);self.assertEqual(r['status'],'UNSUPPORTED')
   c=check(method='Temp')
   with patch('element_test.storage.session_module',side_effect=failing):r=run_pure(root,analyze(root),c,temp)
   write_json(EVIDENCE/'infrastructure-error.json',r);self.assertEqual(r['status'],'ERROR')
   r=self.execute(root,c,temp,'infrastructure-recovery');self.assertEqual(grade(r['actual'],expected(c,ANSWERS['Temp']),temp),'PASS')

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','SQL opt-in')
class CompositeSqlTest(unittest.TestCase):
 def test_temporary_nested_join_snapshot_rollback_sql_audit_and_cleanup(self):
  import secrets
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   for variant in ('ordinary','renamed'):
    for method in ('TempJoin','TempIn','State'):
     with self.subTest(variant=variant,method=method):
      c=check(variant,method,sql=True);r=run_integration(c,analyze(CORPUS/variant),Path(d),root=CORPUS/variant,enabled=True,inject_failure=True);write_json(EVIDENCE/('sql-'+variant+'-'+method+'.json'),r)
      self.assertEqual(r['status'],'EXECUTED',r.get('reason'));self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],expected(c,answer(variant,method)),Path(d)),'PASS');self.assertNotIn('source:commit',r['storageTrace'])

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Real Script opt-in')
class CompositeRealTest(unittest.TestCase):
 execute=CompositeDockerTest.execute
 def test_direct_unchanged_current_department_root(self):
  from element_test.loader import open_project
  with TemporaryDirectory() as d,open_project(REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump') as root:
   for case,result in REAL.items():
    c=real_check(case);r=self.execute(root,c,Path(d),'real-'+case);self.assertEqual(grade(r['actual'],expected(c,result),Path(d)),'PASS',r['actual'])

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Public Script opt-in')
class CompositePublicTest(unittest.TestCase):
 def test_public_real_test_run_and_fail_unsupported_pass(self):
  for mode in ('test','run'):
   folder=EVIDENCE/('public-real-'+mode)
   cmd=[str(REPO/'bin/element-test'),mode,'--project',str(REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump'),'--assignment',str(REPO/'assignments/unions-real'),'--output',str(folder)]
   p=subprocess.run(cmd,capture_output=True,text=True,timeout=900);(EVIDENCE/('public-real-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-real-'+mode+'.stderr')).write_text(p.stderr)
   self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout);r=json.loads((folder/'result.json').read_text());self.assertEqual(r['score'],5);self.assertTrue(all(c['status']=='PASS' for c in r['checks']))
  r,p=run_test(CORPUS/'ordinary',REPO/'assignments/unions-control',EVIDENCE/'public-control');self.assertEqual([c['status'] for c in r['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(p['unavailablePoints'],1)

 def test_two_fresh_isolated_batches(self):
  from element_test.batch import run_batch
  import yaml
  with TemporaryDirectory() as d:
   temp=Path(d);assignment=EVIDENCE/'batch-assignment';assignment.mkdir(exist_ok=True);checks=[]
   for method in ('Union','All','Temp'):
    c=check(method=method);checks.append({'id':method,'type':'runtime','points':1,'comparison':'query-rows-unordered',**c,'expected':expected(c,ANSWERS[method])})
   (assignment/'assignment.yaml').write_text(dump_yaml({'name':'Batch 42','checks':checks},allow_unicode=True,sort_keys=False));submissions=[]
   for label in ('correct','mutated','neighbor'):
    root=EVIDENCE/'batch-inputs'/label;shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
    if label=='mutated':
     p=root/'Entry/Main.xbsl';source=p.read_text()
     for method,old,new in [('Union','ОБЪЕДИНИТЬ ВЫБРАТЬ','ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ'),('All','ОБЪЕДИНИТЬ ВСЕ','ОБЪЕДИНИТЬ'),('Temp','Value > 1','Value > 2')]:
      declaration='метод '+method+'(): Объект\n    возврат Запрос{'+QUERIES[method]
      assert declaration in source
      source=source.replace(declaration,declaration.replace(old,new))
     p.write_text(source)
    submissions.append({'studentId':label,'project':str(root)})
   manifest=EVIDENCE/'batch-manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'unions-042','submissions':submissions})
   saved_batches=[]
   for n in (1,2):
    folder=EVIDENCE/f'batch-{n}';repeat=1
    while folder.exists():
     folder=EVIDENCE/f'batch-{n}-repeat-{repeat}';repeat+=1
    saved_batches.append(str(folder.relative_to(REPO)))
    with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/f'cache-{n}')}):code=run_batch(manifest,folder,2)
    self.assertEqual(code,1);report=json.loads((folder/'batch-result.json').read_text());self.assertEqual([s['score'] for s in report['submissions']],[3,0,3]);self.assertFalse(any(s['cacheHit'] for s in report['submissions']))
   write_json(EVIDENCE/'batch-latest.json',saved_batches)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Real SQL opt-in')
class CompositeRealSqlTest(unittest.TestCase):
 def test_real_employee_and_fallback_sql_audit_cleanup(self):
  import secrets
  from element_test.loader import open_project
  with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}),open_project(REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump') as root:
   for case in ('employee','fallback'):
    c=real_check(case,sql=True);r=run_integration(c,analyze(root),Path(d),root=root,enabled=True);write_json(EVIDENCE/('sql-real-'+case+'.json'),r)
    self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertEqual(grade(r['actual'],expected(c,REAL[case]),Path(d)),'PASS')

@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Edges Script opt-in')
class CompositeEdgeTest(unittest.TestCase):
 execute=CompositeDockerTest.execute
 def test_hidden_null_column_collision_and_nullable_alias_comparison(self):
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl'
   extra=[('Collision','ВЫБРАТЬ Q.Н0 ИЗ (ВЫБРАТЬ Key КАК Н0 ИЗ Data::Item ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Key ИЗ Other::Item) КАК Q УПОРЯДОЧИТЬ ПО Q.Н0',[{'Н0':1},{'Н0':1},{'Н0':1},{'Н0':2},{'Н0':3}]),('Nullable','ВЫБРАТЬ Q.Value КАК Peer ИЗ (ВЫБРАТЬ Peer КАК Value ИЗ Data::Item) КАК Q ГДЕ Q.Value != Неопределено',[{'Peer':REFS[0]},{'Peer':REFS[0]}])]
   with p.open('a') as f:
    for method,q,_ in extra:f.write('метод '+method+'(): Объект\n    возврат Запрос{'+q+'}.Выполнить()\n;\n')
   for method,_,result in extra:
    c=check(method=method);r=self.execute(root,c,temp,'edge-'+method);self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS',r['actual'])

 def test_union_and_batch_reference_captures_are_detached(self):
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl'
   source='''метод RefCapture(): Объект
    знч Ref = Data::Item.ПолучитьСсылку(новый Ууид("11111111-1111-4111-8111-111111111111"))
    знч Union = Запрос{ВЫБРАТЬ Amount КАК Value ИЗ Data::Item ГДЕ Ссылка == %Ref ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Amount ИЗ Data::Item ГДЕ Ссылка == %Ref}
    знч Batch = Запрос{ВЫБРАТЬ Amount КАК Value ПОМЕСТИТЬ T ИЗ Data::Item ГДЕ Ссылка == %Ref; ВЫБРАТЬ Value ИЗ T}
    Ref.Идентификатор = новый Ууид("22222222-2222-4222-8222-222222222222")
    возврат {"union": Union.Выполнить(), "batch": Batch.Выполнить()}
;
'''
   with p.open('a') as f:f.write(source)
   c=check(method='RefCapture');r=self.execute(root,c,temp,'edge-ref-capture');self.assertEqual(grade(r['actual'],expected(c,{'union':[{'Value':1},{'Value':1}],'batch':[{'Value':1}]}),temp),'PASS',r['actual'])


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Membership edges opt-in')
class CompositeMembershipEdgeTest(unittest.TestCase):
 execute=CompositeDockerTest.execute
 def test_missing_join_side_membership_and_optional_derived_alias(self):
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl'
   scenarios=[('AbsentIn','ВЫБРАТЬ A.Key ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key ГДЕ B.Amount В (ВЫБРАТЬ Amount ИЗ Other::Item)',[{'Key':1},{'Key':1}]),('AbsentNotIn','ВЫБРАТЬ A.Key ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key ГДЕ B.Amount НЕ В (ВЫБРАТЬ Amount ИЗ Other::Item)',[]),('OptionalAlias','ВЫБРАТЬ Value ИЗ (ВЫБРАТЬ Key КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Key ИЗ Other::Item)',[{'Value':1},{'Value':2},{'Value':3}])]
   with p.open('a') as f:
    for method,q,_ in scenarios:f.write('метод '+method+'(): Объект\n    возврат Запрос{'+q+'}.Выполнить()\n;\n')
   for method,_,result in scenarios:
    c=check(method=method);r=self.execute(root,c,temp,'membership-'+method);self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS',r['actual'])
