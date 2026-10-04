"""Independent author data and answers fixed before executing task-42 sources."""
from pathlib import Path
import copy
from projection_fixtures import portable_check as base_check, expected, REFS, IDS
REPO=Path(__file__).resolve().parents[1]
CORPUS=REPO/'tests/corpus/unions-nesting'
EVIDENCE=REPO/'result/unions-nesting'
def dump_yaml(value,**kwargs):
 import yaml
 class Dumper(yaml.SafeDumper):
  def ignore_aliases(self,data):return True
 return yaml.dump(value,Dumper=Dumper,**kwargs)

QUERIES={
 'Union':'ВЫБРАТЬ Key КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Key КАК Ignored ИЗ Other::Item',
 'All':'ВЫБРАТЬ Key КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Key КАК Ignored ИЗ Other::Item',
 'Mixed':'ВЫБРАТЬ Key КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Key ИЗ Other::Item ОБЪЕДИНИТЬ РАЗЛИЧНЫЕ ВЫБРАТЬ Key ИЗ Other::Item',
 'Sorted':'ВЫБРАТЬ Key КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Key ИЗ Other::Item УПОРЯДОЧИТЬ ПО Value УБЫВ',
 'Nested':'ВЫБРАТЬ Q.Value * 2 КАК Total ИЗ (ВЫБРАТЬ Key КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Key ИЗ Other::Item) КАК Q ГДЕ Q.Value >= 2 УПОРЯДОЧИТЬ ПО Total',
 'Deep':'ВЫБРАТЬ Q.Value ИЗ (ВЫБРАТЬ R.Value ИЗ (ВЫБРАТЬ Amount КАК Value ИЗ Data::Item) КАК R ГДЕ R.Value > 1) КАК Q',
 'NestedJoin':'ВЫБРАТЬ Q.Key, B.Amount КАК Value ИЗ (ВЫБРАТЬ Key ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Key ИЗ Other::Item) КАК Q ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО Q.Key == B.Key',
 'NestedNull':'ВЫБРАТЬ КОЛИЧЕСТВО(Q.Value) КАК Count, СУММА(Q.Value.ЗаменитьNull(10)) КАК Sum ИЗ (ВЫБРАТЬ B.Amount КАК Value ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key) КАК Q',
 'NullUnion':'ВЫБРАТЬ КОЛИЧЕСТВО(Q.Value) КАК Count ИЗ (ВЫБРАТЬ ВЫБОР КОГДА Key == 1 ТОГДА NULL ИНАЧЕ Peer КОНЕЦ КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Peer КАК Value ИЗ Data::Item ГДЕ Key > 1) КАК Q',
 'NullNumber':'ВЫБРАТЬ ПЕРВЫЕ 1 NULL КАК Value ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Amount КАК Value ИЗ Other::Item',
 'In':'ВЫБРАТЬ Amount КАК Value ИЗ Data::Item ГДЕ Key В (ВЫБРАТЬ Key ИЗ Other::Item)',
 'NotIn':'ВЫБРАТЬ Amount КАК Value ИЗ Data::Item ГДЕ Key НЕ В (ВЫБРАТЬ Key ИЗ Other::Item)',
 'EmptyIn':'ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key НЕ В (ВЫБРАТЬ Key ИЗ Other::Item ГДЕ Key < 0)',
 'UnknownIn':'ВЫБРАТЬ Key ИЗ Data::Item ГДЕ Key НЕ В (ВЫБРАТЬ NULL КАК Key ИЗ Other::Item)',
 'Compound':'ВЫБРАТЬ Key ИЗ Data::Item; ВЫБРАТЬ Label ИЗ Other::Item',
 'Temp':'ВЫБРАТЬ Key КАК Value ПОМЕСТИТЬ T ИЗ Data::Item ОБЪЕДИНИТЬ ВЫБРАТЬ Key ИЗ Other::Item ИНДЕКСИРОВАТЬ ПО Value; ВЫБРАТЬ Value ИЗ T ГДЕ Value > 1',
 'TempJoin':'ВЫБРАТЬ Key, Amount ПОМЕСТИТЬ T ИЗ Data::Item; СОЗДАТЬ ИНДЕКС Idx ДЛЯ T (Key) ДОПОЛНИТЕЛЬНО ПО (Amount); ВЫБРАТЬ T.Key, СУММА(T.Amount) КАК Sum ИЗ T ВНУТРЕННЕЕ СОЕДИНЕНИЕ Other::Item КАК B ПО T.Key == B.Key СГРУППИРОВАТЬ ПО T.Key',
 'TempIn':'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Other::Item; ВЫБРАТЬ Amount КАК Value ИЗ Data::Item ГДЕ Key В (ВЫБРАТЬ Key ИЗ T)',
 'TempNull':'ВЫБРАТЬ B.Amount КАК Value ПОМЕСТИТЬ T ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Other::Item КАК B ПО A.Key == B.Key; ВЫБРАТЬ КОЛИЧЕСТВО(Value) КАК Count, СУММА(Value.ЗаменитьNull(10)) КАК Sum ИЗ T',
 'Create':'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Key: Число, Label: Строка); СОЗДАТЬ ИНДЕКС Idx ДЛЯ T (Key); ВЫБРАТЬ Key, Label ИЗ T',
 'Drop':'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item; УНИЧТОЖИТЬ T',
 'Recreate':'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item; УНИЧТОЖИТЬ T; СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Label: Строка); ВЫБРАТЬ Label ИЗ T',
 'Truncate':'ВЫБРАТЬ Key ПОМЕСТИТЬ T ИЗ Data::Item; ОБРЕЗАТЬ T; ВЫБРАТЬ Key ИЗ T',
 'Fill':'ВЫБРАТЬ Q.Key, СУММА(Q.Amount) КАК Sum ЗАПОЛНИТЬ Data::Card ИЗ (ВЫБРАТЬ Key, Amount ИЗ Data::Item ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Key, Amount ИЗ Other::Item) КАК Q СГРУППИРОВАТЬ ПО Q.Key',
 'Params':'ВЫБРАТЬ Amount + %{Capture(Calls, 1)} КАК Value ПОМЕСТИТЬ T ИЗ Data::Item ГДЕ Key == %{Capture(Calls, 2)}; ВЫБРАТЬ Value + %{Capture(Calls, 3)} КАК Value ИЗ T ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Amount + %{Capture(Calls, 4)} КАК Value ИЗ Other::Item',
 'Shared':'ВЫБРАТЬ Key + %Bonus КАК Value ИЗ Data::Item ГДЕ Key == %Bonus ОБЪЕДИНИТЬ ВСЕ ВЫБРАТЬ Key + %Bonus КАК Value ИЗ Other::Item',
 'Quoted':'ВЫБРАТЬ "a; ОБЪЕДИНИТЬ (ВЫБРАТЬ" КАК Value ИЗ Other::Item; // ; ОБЪЕДИНИТЬ\n ВЫБРАТЬ Key ИЗ Other::Item',
 'Unsupported':'ВЫБРАТЬ Label ИЗ Missing::Table',
}
ANSWERS={
 'Union':[{'Value':1},{'Value':2},{'Value':3}],
 'All':[{'Value':1},{'Value':1},{'Value':2},{'Value':3},{'Value':1}],
 'Mixed':[{'Value':1},{'Value':2},{'Value':3}],
 'Sorted':[{'Value':3},{'Value':2},{'Value':1},{'Value':1},{'Value':1}],
 'Nested':[{'Total':4},{'Total':6}],
 'Deep':[{'Value':3},{'Value':3}],
 'NestedJoin':[{'Key':1,'Value':2},{'Key':2,'Value':None},{'Key':3,'Value':None}],
 'NestedNull':[{'Count':2,'Sum':24}],
 'NullUnion':[{'Count':1}],
 'NullNumber':[{'Value':None},{'Value':2}],
 'In':[{'Value':1},{'Value':3}],
 'NotIn':[{'Value':3},{'Value':0}],
 'EmptyIn':[{'Key':1},{'Key':1},{'Key':2},{'Key':3}],
 'UnknownIn':[],
 'Compound':[{'Label':'x'}],
 'Temp':[{'Value':2},{'Value':3}],
 'TempJoin':[{'Key':1,'Sum':4}],
 'TempIn':[{'Value':1},{'Value':3}],
 'TempNull':[{'Count':2,'Sum':24}],
 'Create':[], 'Drop':[], 'Recreate':[], 'Truncate':[],
 'Fill':[{'Key':1,'Sum':6},{'Key':2,'Sum':3},{'Key':3,'Sum':0}],
 'Params':{'rows':[{'Value':7},{'Value':6}],'calls':[1,2,3,4]},
 'Shared':[{'Value':2},{'Value':2},{'Value':2}],
 'Quoted':[{'Key':1}],
 'State':{'old':[{'Sum':11}],'seen':[{'Sum':30}],'after':[{'Sum':11}],'calls':['capture']},
}

def check(variant='ordinary',method='Union',sql=False,empty=False):
 c=base_check(variant,method,sql,empty);c['args']=[1] if method=='Shared' else []
 return c

def answer(variant,method):
 result=copy.deepcopy(ANSWERS[method])
 if variant=='renamed' and isinstance(result,list):
  for row in result:
   if 'Key' in row:row['Номер']=row.pop('Key')
 return result

def build():
 import shutil,yaml
 for variant in ('ordinary','renamed'):
  root=CORPUS/variant;shutil.copytree(REPO/'tests/corpus/projections-aggregates'/variant,root,dirs_exist_ok=True)
  main='Main' if variant=='ordinary' else 'Пуск';source=[]
  for method,q in QUERIES.items():
   if variant=='renamed':q=q.replace('Data::Item','Учет::Строки').replace('Other::Item','Other::Строки').replace('Data::Card','Учет::Card').replace('Amount','Значение').replace('Key','Номер')
   if method=='Params':body='    знч Calls = новый Массив<Число>()\n    знч Q = Запрос{'+q+'}\n    возврат {"rows": Q.Выполнить(), "calls": Calls}'
   else:body='    возврат Запрос{'+q+'}.Выполнить()'
   source.append('метод '+method+('(Bonus: Число)' if method=='Shared' else '()')+': Объект\n'+body+'\n;\n')
  source.append('метод Capture(Calls: Массив<Число>, N: Число): Число\n    Calls.Добавить(N)\n    возврат N\n;\n')
  original=(REPO/'tests/corpus/projections-aggregates'/variant/'Entry'/ (main+'.xbsl')).read_text();state=original[original.index('метод State():'):]
  old='ВЫБРАТЬ СУММА('+('Amount' if variant=='ordinary' else 'Значение')+' + %{Capture(Calls, 1)}) КАК Sum ИЗ '+('Data::Item' if variant=='ordinary' else 'Учет::Строки')
  state=state.replace(old,old.replace(' КАК Sum ИЗ',' КАК Sum ПОМЕСТИТЬ T ИЗ')+'; ВЫБРАТЬ Sum ИЗ T').replace('Capture(Calls, 1)','CaptureState(Calls, 1)')
  source.append('метод CaptureState(Calls: Массив<Строка>, N: Число): Число\n    Calls.Добавить("capture")\n    возврат N\n;\n'+state)
  (root/'Entry'/(main+'.xbsl')).write_text(''.join(source))
  for sql in (False,True):
   methods=ANSWERS if not sql else ('TempJoin','TempIn','State')
   checks=[]
   for method in methods:
    c=check(variant,method,sql);checks.append({'id':method,'type':'runtime','points':1,'comparison':'query-rows-unordered' if isinstance(ANSWERS[method],list) and method!='Sorted' else 'record-sets-unordered',**c,'expected':expected(c,answer(variant,method))})
   p=REPO/'assignments'/('unions-'+variant+('-sql' if sql else ''));p.mkdir(exist_ok=True);(p/'assignment.yaml').write_text(dump_yaml({'name':'Объединения и вложения №42','checks':checks},allow_unicode=True,sort_keys=False))
 root=CORPUS/'documented';shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True);code=[]
 for label in ('distinct','union','union-all','nested','compound'):
  q=(REPO/'tests/corpus/storage-query-fill/documented-forms'/(label+'.xbql')).read_text().strip();code.append('метод '+label.replace('-','_')+'(): Объект\n    возврат Запрос{'+q+'}.Выполнить()\n;\n')
 (root/'Entry/Main.xbsl').write_text(''.join(code))
 control=[]
 for method,result in [('Union',[]),('Unsupported',None),('Union',ANSWERS['Union'])]:
  c=check(method=method);control.append({'id':str(len(control)),'type':'runtime','points':1,'comparison':'query-rows-unordered',**c,'expected':expected(c,result) if result is not None else None})
 p=REPO/'assignments/unions-control';p.mkdir(exist_ok=True);(p/'assignment.yaml').write_text(dump_yaml({'name':'FAIL UNSUPPORTED PASS','checks':control},allow_unicode=True,sort_keys=False))


# Independent current-user fixtures exercise the unchanged real UNION root.
def real_check(case='employee',sql=False):
 ns='ХранилищеНастроек';employee={'type':ns+'::Сотрудники','value':{'Ссылка':REFS[0],'Наименование':'employee','Подразделение':REFS[1] if case!='undefined' else None,'Пользователь':REFS[5]}}
 initial=[{'type':ns+'::Подразделения','value':{'Ссылка':REFS[1],'Наименование':'assigned','Основное':False}},{'type':ns+'::Подразделения','value':{'Ссылка':REFS[2],'Наименование':'fallback','Основное':case!='empty'}}]
 if case in ('employee','undefined'):initial.append(employee)
 if case=='duplicate':
  initial[0]['value']['Основное']=True;initial[1]['value']['Основное']=False;initial.append(employee)
 c={'target':{'namespace':ns,'module':'Подразделения','method':'ТекущееПодразделение'},'args':[],'runtimeProfile':'9.3','timeout':'15s','trace':True,'queryContext':{'currentUser':REFS[5]},'storage':{'idType':'Ууид','initial':initial}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c
REAL={'employee':REFS[1],'fallback':REFS[2],'duplicate':REFS[1],'empty':None,'undefined':None}

def build_real():
 import yaml
 for sql in (False,True):
  p=REPO/'assignments'/('unions-real'+('-sql' if sql else ''));p.mkdir(exist_ok=True)
  checks=[]
  for case,result in REAL.items():
   c=real_check(case,sql);checks.append({'id':case,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':expected(c,result)})
  (p/'assignment.yaml').write_text(dump_yaml({'name':'Текущее подразделение — UNION №42','checks':checks},allow_unicode=True,sort_keys=False))

if __name__=='__main__':
 build()
 build_real()
