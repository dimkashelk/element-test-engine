"""Stage 45 independent author data/answers, persisted before execution."""
from pathlib import Path
import copy,shutil
from fill_fixtures import REPO,REFS,expected
from result_fixtures import check as previous_check
from composite_fixtures import dump_yaml
CORPUS=REPO/'tests/corpus/state-rights';EVIDENCE=REPO/'result/state-rights'
QUERIES={
'Between':'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Total МЕЖДУ %Low И %High',
'Like':'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label ПОДОБНО %Pattern',
'Distinct':'ВЫБРАТЬ X.Label, Y.Label КАК Other ИЗ Data::Item КАК X ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК Y ПО X.Label == Y.Label ГДЕ Y.Label НЕ ОТЛИЧАЕТСЯ ОТ NULL',
'Exists':'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ СУЩЕСТВУЕТ (ВЫБРАТЬ Label ИЗ Other::Item ГДЕ Total МЕЖДУ 1 И 4)',
'NotExists':'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ НЕ СУЩЕСТВУЕТ (ВЫБРАТЬ Label ИЗ Other::Item ГДЕ Total == 999)',
'Combined':'ВЫБРАТЬ Label, СУММА(Total) КАК Amount ИЗ (ВЫБРАТЬ Label, Total ИЗ Data::Item ГДЕ Total МЕЖДУ 1 И 3 ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Label, Total ИЗ Data::Item ГДЕ Label ПОДОБНО "a") КАК X СГРУППИРОВАТЬ ПО Label ИМЕЮЩИЕ СУММА(Total) МЕЖДУ 2 И 8 УПОРЯДОЧИТЬ ПО Label',
'Mutate':'ВЫБРАТЬ Label, Total ПОМЕСТИТЬ T ИЗ Data::Item; ВСТАВИТЬ В T (Label, Total) ЗНАЧЕНИЯ ("C", 4); ИЗМЕНИТЬ T УСТАНОВИТЬ Total = Total + 10, Label = Label + "!" ГДЕ Total МЕЖДУ 3 И 4; УДАЛИТЬ ИЗ T ГДЕ Label ПОДОБНО "c%"; ВЫБРАТЬ Label, Total ИЗ T УПОРЯДОЧИТЬ ПО Label',
'InsertSelect':'ВЫБРАТЬ Label, Total ПОМЕСТИТЬ T ИЗ Data::Item; ВСТАВИТЬ В T (Total, Label) ВЫБРАТЬ Total + 10 КАК Changed, Label КАК Name ИЗ Data::Item',
'UpdateCount':'ВЫБРАТЬ Label, Total ПОМЕСТИТЬ T ИЗ Data::Item; ИЗМЕНИТЬ T УСТАНОВИТЬ Total = Total + 1 ГДЕ Total МЕЖДУ 1 И 3',
'DeleteCount':'ВЫБРАТЬ Label, Total ПОМЕСТИТЬ T ИЗ Data::Item; УДАЛИТЬ ИЗ T ГДЕ Total == 3',
'NullInsert':'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Name: Строка, Note: Строка?); ВСТАВИТЬ В T (Name) ЗНАЧЕНИЯ ("a"); ВЫБРАТЬ Name, Note ИЗ T',
'Repeat':'ВЫБРАТЬ Label ПОМЕСТИТЬ T ИЗ Data::Item; УДАЛИТЬ ИЗ T; ВЫБРАТЬ Label ИЗ T',
'Access':'ВЫБРАТЬ Label ИЗ Data::Item',
'UnsupportedHierarchy':'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Ссылка В ИЕРАРХИИ Data::Item.Unknown (%Parent)',
'Hierarchy':'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Ссылка В ИЕРАРХИИ (%Parent)',
'Correlated':'ВЫБРАТЬ X.Label ИЗ Data::Item КАК X ГДЕ СУЩЕСТВУЕТ (ВЫБРАТЬ Y.Label ИЗ Other::Item КАК Y ГДЕ Y.Label == X.Label)',
'PlatformDml':'УДАЛИТЬ ИЗ Data::Item',
}
ARGS={'Between':'Low: Число, High: Число','Like':'Pattern: Строка','Hierarchy':'Parent: Data::Item.Ссылка','UnsupportedHierarchy':'Parent: Data::Item.Ссылка'}
ANSWERS={'Between':[{'Label':'A'},{'Label':'B'}],'Like':[{'Label':'A'}], 'Distinct':[{'Label':'A','Other':None},{'Label':'B','Other':None}], 'Exists':[{'Label':'A'},{'Label':'B'}], 'NotExists':[{'Label':'A'},{'Label':'B'}], 'Combined':[{'Label':'A','Amount':6}], 'Mutate':[{'Label':'A!','Total':13},{'Label':'B','Total':1}], 'InsertSelect':[{'КоличествоЗаписей':2}], 'UpdateCount':[{'КоличествоЗаписей':2}], 'DeleteCount':[{'КоличествоЗаписей':1}], 'NullInsert':[{'Name':'a','Note':None}], 'Repeat':[], 'Access':[{'Label':'A'}], 'Hierarchy':[{'Label':'A'}], 'Correlated':[]}

def check(variant='ordinary',method='Between',sql=False,empty=False):
 c=previous_check('ordinary' if variant=='hierarchy' else variant,method,sql=sql,empty=empty)
 if variant=='hierarchy':
  c['storage']['initial'][0]['value']['Родитель']=None
  c['storage']['initial'][1]['value']['Родитель']=copy.deepcopy(REFS[0])
  third=copy.deepcopy(c['storage']['initial'][0]);third['value'].update(Label='C',Ссылка=copy.deepcopy(REFS[2]),Родитель=copy.deepcopy(REFS[1]));c['storage']['initial'].insert(2,third)
 c['args']=[1,3] if method=='Between' else ['a'] if method=='Like' else [REFS[0]] if method in ('Hierarchy','UnsupportedHierarchy') else []
 if method=='Access':c['queryAccess']={'mode':'executor-fixture','sources':{'Data::Item':{'read':True,'ids':[REFS[0]['Идентификатор']]}}}
 return c

def build():
 for variant in ('ordinary','daily'):
  root=CORPUS/variant;shutil.copytree(REPO/'tests/corpus/result-resources'/variant,root,dirs_exist_ok=True)
  source=''
  for name,q in QUERIES.items():
   if variant=='daily' and name in ('Access','Hierarchy','UnsupportedHierarchy'):continue
   if variant=='daily':q=q.replace('ИЗ Data::Item','ИЗ Data::Book.СрезПоследних(%{новый Дата("2026-09-30")})')
   source+='метод '+name+'('+ARGS.get(name,'')+'): Объект\n    возврат Запрос{'+q+'}.Выполнить().ВМассив()\n;\n'
  # Preserve state/transaction author program, with independent expected history.
  from result_fixtures import SOURCES
  if variant=='ordinary':source+=SOURCES['State']
  (root/'Entry/Main.xbsl').write_text(source)
 root=CORPUS/'hierarchy';shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
 import yaml
 path=root/'Data/Item.yaml';meta=yaml.safe_load(path.read_text());meta['Иерархический']=True;path.write_text(dump_yaml(meta,allow_unicode=True,sort_keys=False))
 root=CORPUS/'documented';shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
 source=''
 signatures={'ordinary':'','parameters':'Name: Строка','captured-expression':'Name: Строка','order-limit':'','in':'Values: Массив<Число>','between':ARGS['Between'],'like':ARGS['Like']}
 for name,sig in signatures.items():
  text=(REPO/'tests/corpus/storage-query-fill/documented-forms'/(name+'.xbql')).read_text().strip()
  source+='метод '+name.replace('-','_')+'('+sig+'): Объект\n    возврат Запрос{'+text+'}.Выполнить().ВМассив()\n;\n'
 (root/'Entry/Main.xbsl').write_text(source)
 def assignment(name,cases):
  folder=REPO/'assignments'/name;folder.mkdir(exist_ok=True)
  (folder/'assignment.yaml').write_text(dump_yaml({'name':'Предикаты и состояние 45','checks':[{'id':m,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':e} for m,c,e in cases]},allow_unicode=True,sort_keys=False))
 assignment('state-real',real_cases())
 assignment('state-ordinary',[(m,check(method=m),expected(check(method=m),a)) for m,a in ANSWERS.items()])
 c=check();assignment('state-control',[('fail',c,expected(c,[])),('unsupported',check(method='UnsupportedHierarchy'),None),('pass',c,expected(c,ANSWERS['Between']))])

def real_cases(sql=False):
 owner='Общие::КурсыВалют::Валюты'
 data=[{'type':owner,'value':{'Ссылка':REFS[0],'Код':'RUB','Наименование':'Base','ВнешнийИдентификатор':'authored-base'}}, {'type':owner,'value':{'Ссылка':REFS[1],'Код':'USD','Наименование':'Foreign','ВнешнийИдентификатор':'authored-other'}}]
 cases=[]
 for method,answer in [('ПолучитьВсе',[REFS[1]]),('ПолучитьБазовуюВалюту',REFS[0])]:
  c={'target':{'namespace':'Общие::КурсыВалют','module':'Валюты','method':method},'args':[],'runtimeProfile':'9.3','trace':True,'timeout':'15s','storage':{'idType':'Ууид','initial':copy.deepcopy(data)},'queryAccess':{'mode':'executor-fixture','sources':{owner:{'read':True,'ids':[r['Идентификатор'] for r in REFS[:2]]}}}}
  if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
  cases.append((method,c,expected(c,answer)))
 return cases

if __name__=='__main__':build()
