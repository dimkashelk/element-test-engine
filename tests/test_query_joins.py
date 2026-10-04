"""Task 40: independent bag/NULL criteria and real Script/storage execution."""
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from join_fixtures import *
from element_test.bridge import write_json, run_test
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.query_plan import parse_storage_query
from element_test.runtime import run_pure, execute_engine, body_type_references
from element_test.yaml_io import InputError


def setUpModule():EVIDENCE.mkdir(parents=True,exist_ok=True)


def grade(actual,exp,temp,ordered=False):
 write_json(temp/'model.json',{'elements':[],'modules':[]})
 write_json(temp/'assignment.json',{'checks':[{'id':'independent','type':'runtime','points':1,
  'comparison':'query-rows-unordered' if isinstance(exp.get('result'),list) and not ordered else 'record-sets-unordered',
  'expected':exp,'execution':{'status':'EXECUTED','actual':actual}}]})
 return execute_engine('test',temp/'model.json',temp/'assignment.json',temp)['checks'][0]['status']


class JoinPlanTest(unittest.TestCase):
 def test_typed_sources_aliases_ranges_lifts_fill_and_no_teacher_answers(self):
  for variant in ('ordinary','renamed'):
   c=portable_check(variant,'Full');c['expected']={'secret-answer':7654321}
   p=plan_execution(CORPUS/variant,analyze(CORPUS/variant),c);q=p.queries[0];ast=q['ast']
   self.assertNotIn('secret-answer',p.to_json());self.assertEqual(ast['joins'][0]['kind'],'full')
   self.assertEqual(len(ast['sources']),2);self.assertEqual(len(p.storage_elements),2)
   for s in ast['sources']:
    a,b=s['source_range'];self.assertIn(s['source_name'],q['text'][a:b])
   self.assertTrue(all(e['sql_nullable'] for e,label in ast['projections']))
   self.assertTrue(all(e['type'].endswith('?') for e,label in ast['projections']))
   write_json(EVIDENCE/('plan-'+variant+'.json'),p.to_dict())

 def test_scope_ambiguity_types_shadowing_and_required_slice_storage(self):
  root=CORPUS/'ordinary';c=ProjectTypes(analyze(root),'Entry');c.rename_collisions=True
  bad=[('ВЫБРАТЬ Key ИЗ Data::Item КАК L СОЕДИНЕНИЕ Data::Entry КАК R ПО Истина','неоднозначное поле'),
       ('ВЫБРАТЬ L.Label ИЗ Data::Item КАК L СОЕДИНЕНИЕ Data::Entry КАК L ПО Истина','псевдоним источника'),
       ('ВЫБРАТЬ L.Label ИЗ Data::Item КАК L СОЕДИНЕНИЕ Data::Entry КАК R ПО R.Key == O.Key СОЕДИНЕНИЕ Other::Item КАК O ПО Истина','Неизвестное'),
       ('ВЫБРАТЬ R.Amount КАК Left ЗАПОЛНИТЬ Data::Card ИЗ Data::Item КАК L ЛЕВОЕ СОЕДИНЕНИЕ Data::Entry КАК R ПО Истина','Несовместимые типы'),
       ('ВЫБРАТЬ R.Amount.ЗаменитьNull("bad") КАК Amount ИЗ Data::Entry КАК R','Несовместимые типы'),
       ('ВЫБРАТЬ L.Label ИЗ Data::Item КАК L СОЕДИНЕНИЕ','Ожидается')]
  for text,reason in bad:
   with self.subTest(text=text),self.assertRaises(InputError):parse_storage_query(text,c)
  with TemporaryDirectory() as d:
   target=Path(d)/'p';shutil.copytree(root,target);p=target/'Entry/Main.xbsl';p.write_text(p.read_text().replace('метод Left():','метод Left(Data: Строка):'))
   check=portable_check();check['args']=['shadow']
   with self.assertRaisesRegex(InputError,'Затенённый источник'):plan_execution(target,analyze(target),check)
  with open_project(ARCHIVE) as root:
   check=real_check();check['storage'].pop('registers')
   with self.assertRaisesRegex(InputError,'storage.registers'):plan_execution(root,analyze(root),check)

 def test_parameter_order_reuse_and_source_boundary_slots(self):
  root=CORPUS/'ordinary';c=ProjectTypes(analyze(root),'Entry');c.rename_collisions=True
  text='ВЫБРАТЬ R.Amount.ЗаменитьNull(%{Fallback()}) КАК Amount ИЗ Data::Item КАК L ЛЕВОЕ СОЕДИНЕНИЕ Data::Entry КАК R ПО L.Key == %K И R.Key == %K ГДЕ L.Key == %{Next()} И R.Amount != %{Next()}'
  q=parse_storage_query(text,c)
  self.assertEqual([p.expression for p in q.parameters],['Fallback()','K','K','Next()','Next()'])
  self.assertEqual([p.slot for p in q.parameters],[0,1,1,2,3])
  for p in q.parameters:self.assertEqual(text[p.start:p.end],p.expression)
  self.assertEqual(body_type_references('метод F(): Объект\n    возврат <Data::Card>[]\n;')[0][2],'Data::Card')
  with self.assertRaisesRegex(InputError,'Несовместимые типы сравнения'):
   parse_storage_query('ВЫБРАТЬ L.Label ИЗ Data::Item КАК L СОЕДИНЕНИЕ Other::Item КАК O ПО L.Ссылка == O.Ссылка',c)

 def test_sbsl_bag_comparison_preserves_multiplicity_and_storage_audit(self):
  c=portable_check();exp=expected(c,INNER);actual=copy.deepcopy(exp);actual['result'].reverse();actual['storage'].reverse()
  with TemporaryDirectory() as d:
   self.assertEqual(grade(actual,exp,Path(d)),'PASS')
   actual['result'][0]=copy.deepcopy(actual['result'][1]);self.assertEqual(grade(actual,exp,Path(d)),'FAIL')
   actual=copy.deepcopy(exp);actual['storage'].pop();self.assertEqual(grade(actual,exp,Path(d)),'FAIL')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 40 Docker opt-in')
class JoinDockerTest(unittest.TestCase):
 def execute(self,root,c,temp,label):
  r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/(label+'.json'),r)
  self.assertEqual(r['status'],'EXECUTED',r);self.assertEqual(r['runtime']['executionCompatibilityVersion'],'current')
  return r

 def test_scalar_enum_date_uuid_and_parameterized_ordering(self):
  import yaml
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'ordinary',root)
   for owner in ('Item','Entry'):
    p=root/'Data'/(owner+'.yaml');meta=yaml.safe_load(p.read_text())
    meta['Реквизиты'] += [{'Имя':'Flag','Тип':'Булево'},{'Имя':'Day','Тип':'Дата'},{'Имя':'Token','Тип':'Ууид'},{'Имя':'Kind','Тип':'Data::Category'}]
    p.write_text(yaml.safe_dump(meta,allow_unicode=True,sort_keys=False))
   (root/'Data/Category.yaml').write_text('ВидЭлемента: Перечисление\nИмя: Category\nОбластьВидимости: ВПроекте\nЭлементы:\n  - {Имя: First, ПоУмолчанию: true}\n  - {Имя: Second}\n')
   p=root/'Entry/Main.xbsl'
   with p.open('a') as f:
    f.write('метод Typed(D: Дата, F: Число): Объект\n    возврат Запрос{ВЫБРАТЬ L.Label КАК Left, R.Flag КАК Flag, R.Day.ЗаменитьNull(%D) КАК Day, R.Token КАК Token, R.Kind КАК Kind ИЗ Data::Item КАК L ЛЕВОЕ СОЕДИНЕНИЕ Data::Entry КАК R ПО L.Key == R.Key И L.Flag == R.Flag И L.Day == R.Day И L.Token == R.Token И L.Kind == R.Kind УПОРЯДОЧИТЬ ПО R.Amount.ЗаменитьNull(%F)}.Выполнить()\n;\n')
   c=portable_check(method='Typed');c['args']=['2025-03-01',0]
   for seed in c['storage']['initial']:
    if seed['type'].startswith('Data::'):seed['value'].update(Flag=True,Day='2025-01-01',Token=IDS[0],Kind='First')
   result=[{'Left':'A','Flag':True,'Day':'2025-01-01','Token':IDS[0],'Kind':'First'},
           {'Left':'A','Flag':True,'Day':'2025-01-01','Token':IDS[0],'Kind':'First'},
           {'Left':'B','Flag':True,'Day':'2025-01-01','Token':IDS[0],'Kind':'First'},
           {'Left':'B','Flag':True,'Day':'2025-01-01','Token':IDS[0],'Kind':'First'},
           {'Left':'C','Flag':None,'Day':'2025-03-01','Token':None,'Kind':None}]
   r=self.execute(root,c,temp,'typed-scalars');self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS',r)
   # Limit makes the parameterized order observable without assuming tie order.
   p.write_text(p.read_text().replace('ВЫБРАТЬ L.Label КАК Left, R.Flag', 'ВЫБРАТЬ ПЕРВЫЕ 1 L.Label КАК Left, R.Flag'))
   r=self.execute(root,c,temp,'typed-scalars-limit')
   self.assertEqual(grade(r['actual'],expected(c,[result[-1]]),temp,ordered=True),'PASS',r)

 def test_real_direct_root_empty_base_multiple_missing_and_daily_boundaries(self):
  with open_project(ARCHIVE) as root,TemporaryDirectory() as d:
   for label,c,exp in real_cases():
    r=self.execute(root,c,Path(d),'real-'+label);self.assertEqual(grade(r['actual'],exp,Path(d)),'PASS',r)
    self.assertTrue(any(s.startswith('query:slice-boundary:') for s in r['storageTrace']))

 def test_autocheck_direct_root_enum_duplicates_order_and_mutations(self):
  with open_project(REPO/'autocheck-2026-09-24-16-14.xdump','dimkashelk::check') as source,TemporaryDirectory() as d:
   temp=Path(d)
   for empty in (False,True):
    c=cases_check(empty);r=self.execute(source,c,temp,'cases-empty' if empty else 'cases-active')
    self.assertEqual(grade(r['actual'],expected(c,[] if empty else CASES),temp,ordered=True),'PASS')
   # Preserve declared library siblings when copying an extracted application.
   workspace=temp/'workspace';shutil.copytree(source.parent,workspace)
   root=workspace/source.name;p=root/'Проверка/Кейсы.xbsl';base=p.read_text();c=cases_check()
   for label,a,b in [('status','ПеречислениеСтатусыПроверок.Действующий','ПеречислениеСтатусыПроверок.Черновик'),('order','Кейсы.Код\n','Кейсы.Код УБЫВ\n')]:
    self.assertIn(a,base);p.write_text(base.replace(a,b));r=self.execute(root,c,temp,'mutation-cases-'+label)
    self.assertEqual(grade(r['actual'],expected(c,CASES),temp,ordered=True),'FAIL')

 def test_portable_all_four_bag_joins_null_undefined_truth_tables_and_owner_types(self):
  with TemporaryDirectory() as d:
   for variant in ('ordinary','renamed'):
    methods=ANSWERS if variant=='ordinary' else ('Inner','Left','Right','Full','Nested','RefJoin')
    for method in methods:
     c=portable_check(variant,method);r=self.execute(CORPUS/variant,c,Path(d),variant+'-'+method)
     self.assertEqual(grade(r['actual'],expected(c,ANSWERS[method]),Path(d)),'PASS',(variant,method,r))

 def test_empty_sides_cross_join_on_vs_where_and_alternative_correct_source(self):
  with TemporaryDirectory() as d:
   temp=Path(d);root=temp/'p';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text()
   for empty,result in [('Item',RIGHT_ONLY+[card(None,'x',3),card(None,'y',5,'note')]),('Entry',[card('A',None,ref=REFS[0]),card('B',None,ref=REFS[1]),MISSING[0]]),('both',[])]:
    c=portable_check(method='Full',empty=empty);r=self.execute(root,c,temp,'empty-'+empty)
    self.assertEqual(grade(r['actual'],expected(c,result),temp),'PASS',r)
   p.write_text(base.replace('ЛЕВОЕ СОЕДИНЕНИЕ','ЛЕВОЕ ВНЕШНЕЕ СОЕДИНЕНИЕ').replace('L.Key == R.Key','R.Key = L.Key'))
   c=portable_check();r=self.execute(root,c,temp,'alternative');self.assertEqual(grade(r['actual'],expected(c,LEFT),temp),'PASS')
   # ON keeps missing left rows; WHERE removes them.
   p.write_text(base.replace('ПО L.Key == R.Key','ПО L.Key == R.Key И R.Amount == 5'))
   r=self.execute(root,c,temp,'on-filter');self.assertEqual(grade(r['actual'],expected(c,[INNER[1],INNER[3],MISSING[0]]),temp),'PASS')
   p.write_text(base.replace('ЗАПОЛНИТЬ Result ИЗ Data::Item КАК L ЛЕВОЕ СОЕДИНЕНИЕ Data::Entry КАК R ПО L.Key == R.Key}', 'ЗАПОЛНИТЬ Result ИЗ Data::Item КАК L ЛЕВОЕ СОЕДИНЕНИЕ Data::Entry КАК R ПО L.Key == R.Key ГДЕ R.Amount == 5}'))
   r=self.execute(root,c,temp,'where-filter');self.assertEqual(grade(r['actual'],expected(c,[INNER[1],INNER[3]]),temp),'PASS')
   p.write_text(base.replace('ПО L.Key == R.Key','ПО Истина'))
   r=self.execute(root,portable_check(method='Inner'),temp,'cross');self.assertEqual(len(r['actual']['result']),9)

 def test_fresh_reference_projections_capture_reexecute_write_and_rollback(self):
  with TemporaryDirectory() as d:
   temp=Path(d)
   for variant in ('ordinary','renamed'):
    c=portable_check(variant,'State');r=self.execute(CORPUS/variant,c,temp,variant+'-state')
    self.assertEqual(grade(r['actual'],expected(c,state_answer()),temp),'PASS',r)
    self.assertEqual(r['storageTrace'].count('source:rollback'),1);self.assertNotIn('source:commit',r['storageTrace'])
   c=portable_check(method='Detached');r=self.execute(CORPUS/'ordinary',c,temp,'detached')
   fresh=copy.deepcopy(LEFT);fresh[0]['Ref']={'Идентификатор':'99999999-9999-4999-8999-999999999999'};fresh[0]['Amount']=700
   self.assertEqual(grade(r['actual'],expected(c,{'old':LEFT,'fresh':fresh,'again':LEFT}),temp),'PASS',r)

 def test_business_mutations_sbsl_fail_and_recovery(self):
  with open_project(ARCHIVE) as source,TemporaryDirectory() as d:
   temp=Path(d);root=temp/'real';shutil.copytree(source,root);p=root/'Общие/КурсыВалют/КурсыВалют.xbsl';base=p.read_text();c=real_check();exp=expected(c,REAL)
   for label,a,b in [('inner','ЛЕВОЕ СОЕДИНЕНИЕ','ВНУТРЕННЕЕ СОЕДИНЕНИЕ'),('on','Валюты.Ссылка == Курсы.Валюта','Валюты.Ссылка != Курсы.Валюта'),('resource','Курсы.Курс.ЗаменитьNull(0)','Курсы.Кратность.ЗаменитьNull(0)'),('fallback','Курсы.Курс.ЗаменитьNull(0)','Курсы.Курс.ЗаменитьNull(17)'),('base','Валюты.Код !=','Валюты.Код =='),('boundary','СрезПоследних()','СрезПоследних(%{новый Дата(2025, 1, 1)})')]:
    self.assertIn(a,base);p.write_text(base.replace(a,b));r=self.execute(root,c,temp,'mutation-'+label);self.assertEqual(grade(r['actual'],exp,temp),'FAIL',label)
   p.write_text(base);r=self.execute(root,c,temp,'mutation-recovery');self.assertEqual(grade(r['actual'],exp,temp),'PASS')
   root=temp/'portable';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';base=p.read_text()
   for label,a,b,method in [('right-left','ПРАВОЕ ВНЕШНЕЕ','ЛЕВОЕ ВНЕШНЕЕ','Right'),('full-left','ПОЛНОЕ СОЕДИНЕНИЕ','ЛЕВОЕ СОЕДИНЕНИЕ','Full'),('null-not-null','R.Key ЕСТЬ NULL','R.Key ЕСТЬ НЕ NULL','Missing')]:
    p.write_text(base.replace(a,b));c=portable_check(method=method);r=self.execute(root,c,temp,'mutation-'+label);self.assertEqual(grade(r['actual'],expected(c,ANSWERS[method]),temp),'FAIL')

 def test_invalid_timeout_unsupported_sticky_infrastructure_error_then_pass(self):
  import element_test.storage as storage
  native=storage.session_module
  def failing(*args,**kwargs):
   source=native(*args,**kwargs)
   return source.replace('исп Поток = Ф.ОткрытьПотокЧтения()', 'если Ф.Существует()\n        выбросить новый ИсключениеНедопустимоеСостояние("injected join failure")\n    ;\n    исп Поток = Ф.ОткрытьПотокЧтения()',1)
  root=CORPUS/'ordinary'
  with TemporaryDirectory() as d:
   temp=Path(d);c=portable_check();c['storage']['initial'][0]['value']['Key']='bad'
   r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'invalid.json',r);self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','invalid_test'))
   c=portable_check();c['timeout']=0.001;r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'timeout.json',r);self.assertEqual(r['status'],'TIMEOUT')
   c=portable_check(method='Unsupported');r=run_pure(root,analyze(root),c,temp);write_json(EVIDENCE/'unsupported.json',r);self.assertEqual(r['status'],'UNSUPPORTED')
   c=portable_check(method='Swallow')
   with patch('element_test.storage.session_module',side_effect=failing):r=run_pure(root,analyze(root),c,temp)
   write_json(EVIDENCE/'sticky-error.json',r);self.assertEqual(r['status'],'ERROR',r)
   self.execute(root,c,temp,'infrastructure-recovery')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task 40 SQL opt-in')
class JoinSqlTest(unittest.TestCase):
 def test_real_and_portable_sql_audit_readonly_and_rollback(self):
  with open_project(ARCHIVE) as real,TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   temp=Path(d)
   cases=[(real,'sql-real',real_check(sql=True),REAL)]
   for variant in ('ordinary','renamed'):
    cases.extend([(CORPUS/variant,'sql-'+variant+'-full',portable_check(variant,'Full',True),ANSWERS['Full']), (CORPUS/variant,'sql-'+variant+'-state',portable_check(variant,'State',True),state_answer())])
   for root,label,c,exp in cases:
    r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=True);write_json(EVIDENCE/(label+'.json'),r)
    self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'],r)
    self.assertEqual(grade(r['actual'],expected(c,exp),temp),'PASS',r)
    self.assertNotIn('source:commit',r['storageTrace'])

 def test_autocheck_enum_order_and_independent_sql_audit(self):
  with open_project(REPO/'autocheck-2026-09-24-16-14.xdump','dimkashelk::check') as root,TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
   c=cases_check(sql=True);r=run_integration(c,analyze(root),Path(d),root=root,enabled=True)
   write_json(EVIDENCE/'sql-cases.json',r);self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
   self.assertEqual(grade(r['actual'],expected(c,CASES),Path(d),ordered=True),'PASS')


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 40 public Docker opt-in')
class JoinPublicTest(unittest.TestCase):
 def test_public_autocheck_direct_root_test_run(self):
  for mode in ('test','run'):
   folder=EVIDENCE/('public-cases-'+mode)
   p=subprocess.run([str(REPO/'bin/element-test'),mode,'--project',str(REPO/'autocheck-2026-09-24-16-14.xdump'),'--project-name','dimkashelk::check','--assignment',str(REPO/'assignments/joins-null-cases'),'--output',str(folder)],capture_output=True,text=True,timeout=900)
   (EVIDENCE/('public-cases-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-cases-'+mode+'.stderr')).write_text(p.stderr)
   self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
   result=json.loads((folder/'result.json').read_text());self.assertEqual(result['score'],2);self.assertTrue(all(c['status']=='PASS' for c in result['checks']))

 def test_public_test_run_control_two_fresh_batches_and_packages(self):
  from element_test.batch import run_batch
  for mode in ('test','run'):
   folder=EVIDENCE/('public-'+mode)
   p=subprocess.run([str(REPO/'bin/element-test'),mode,'--project',str(ARCHIVE),'--assignment',str(REPO/'assignments/joins-null-real'),'--output',str(folder)],capture_output=True,text=True,timeout=900)
   (EVIDENCE/('public-'+mode+'.stdout')).write_text(p.stdout);(EVIDENCE/('public-'+mode+'.stderr')).write_text(p.stderr)
   self.assertEqual(p.returncode,0,p.stderr+'\n'+p.stdout)
   result=json.loads((folder/'result.json').read_text());self.assertEqual(result['score'],4);self.assertTrue(all(c['status']=='PASS' for c in result['checks']))
  a=json.loads((EVIDENCE/'public-test/result.json').read_text());b=json.loads((EVIDENCE/'public-run/result.json').read_text());self.assertEqual(a['checks'],b['checks'])
  result,package=run_test(CORPUS/'ordinary',REPO/'assignments/joins-null-control',EVIDENCE/'public-control')
  self.assertEqual([c['status'] for c in result['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(package['unavailablePoints'],1)
  with TemporaryDirectory() as d:
   temp=Path(d);subs=[]
   for label in ('correct','mutated','neighbor'):
    root=temp/label;shutil.copytree(CORPUS/'ordinary',root)
    if label=='mutated':
     p=root/'Entry/Main.xbsl';p.write_text(p.read_text().replace('ЛЕВОЕ СОЕДИНЕНИЕ','ВНУТРЕННЕЕ СОЕДИНЕНИЕ'))
    subs.append({'studentId':label,'project':str(root)})
   manifest={'schemaVersion':'1.0','assignment':str(REPO/'assignments/joins-null-ordinary'),'assignmentId':'joins-040','submissions':subs}
   path=temp/'manifest.json';write_json(path,manifest)
   inputs=EVIDENCE/'batch-inputs';inputs.mkdir(exist_ok=True);saved=copy.deepcopy(manifest)
   for sub in saved['submissions']:
    dest=inputs/sub['studentId'];shutil.copytree(Path(sub['project']),dest,dirs_exist_ok=True);sub['project']=str(dest)
   write_json(EVIDENCE/'batch-manifest.json',saved)
   for n in (1,2):
    folder=temp/f'batch-{n}'
    with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/'cache')}):code=run_batch(path,folder,2)
    self.assertEqual(code,1)
    packages=[json.loads((folder/'submissions'/f'{i:06d}'/'grading.json').read_text()) for i in (1,2,3)]
    self.assertEqual([p['score'] for p in packages],[6,3,6])
    report=json.loads((folder/'batch-result.json').read_text());self.assertFalse(any(s['cacheHit'] for s in report['submissions']))
    shutil.copytree(folder,EVIDENCE/f'batch-{n}',dirs_exist_ok=True)
