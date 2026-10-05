"""Task 44 author data and independent answers, fixed before execution."""
from pathlib import Path
import copy,shutil
from fill_fixtures import portable_check as seed_check, expected, card, REFS, REPO
from composite_fixtures import dump_yaml
CORPUS=REPO/'tests/corpus/result-resources';EVIDENCE=REPO/'result/result-resources'
SOURCES={
'Produce':'''метод Produce(): Массив<Row>
    знч Q = Запрос{ВЫБРАТЬ Label ПОРОДИТЬ Row ИЗ Data::Item}
    исп R = Q.Выполнить()
    возврат R.ВМассив()
;
''',
'Fill':'''метод Fill(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label КАК Name ЗАПОЛНИТЬ Data::Card ИЗ Data::Item}
    исп R = Q.Выполнить()
    возврат R.ВМассив()
;
''',
'Positional':'''метод Positional(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label КАК Different, Total КАК Count ЗАПОЛНИТЬ Data::Card ИЗ Data::Item}
    исп R = Q.Выполнить()
    возврат R.ВМассив()
;
''',
'Local':'''метод Local(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label КАК Name ЗАПОЛНИТЬ LocalCard ИЗ Data::Item}
    исп R = Q.Выполнить()
    возврат R.ВМассив()
;
''',
'DirectLoop':'''метод DirectLoop(): Объект
    пер Total = 0
    для Row из Запрос{ВЫБРАТЬ Total ИЗ Data::Item}.Выполнить()
        Total += Row.Total
    ;
    возврат Total
;
''',
'Loop':'''метод Loop(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label, Total ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label}
    исп R = Q.Выполнить()
    пер Sum = 0
    для Row из R
        если Row.Total == 3
            продолжить
        ;
        Sum += Row.Total
    ;
    возврат Sum
;
''',
'Early':'''метод Early(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label}
    исп R = Q.Выполнить()
    для Row из R
        возврат Row.Label
    ;
    возврат "empty"
;
''',
'Break':'''метод Break(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}
    исп R = Q.Выполнить()
    пер Count = 0
    для Row из R
        Count += 1
        прервать
    ;
    возврат Count
;
''',
'Exception':'''метод Exception(): Объект
    попытка
        знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}
        исп R = Q.Выполнить()
        выбросить новый ИсключениеНедопустимоеСостояние("business")
    поймать E: Исключение
        возврат "caught"
    ;
;
''',
'Nested':'''метод Nested(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}
    исп Outer = Q.Выполнить()
    если Истина
        исп Inner = Q.Выполнить()
        Inner.Закрыть()
        Inner.Закрыть()
    ;
    возврат Outer.ВМассив()
;
''',
'Closed':'''метод Closed(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}
    знч R = Q.Выполнить()
    R.Закрыть()
    попытка
        возврат R.ВМассив()
    поймать E: Исключение
        возврат "closed"
    ;
;
''',
'Repeated':'''метод Repeated(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}
    исп R = Q.Выполнить()
    знч First = R.ВМассив()
    попытка
        возврат R.ВМассив()
    поймать E: Исключение
        возврат {"first": First, "repeat": "closed"}
    ;
;
''',
'Dynamic':'''метод Dynamic(K: Строка): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label, Total ИЗ Data::Item ГДЕ Label == &Name")
    Q.УстановитьПараметр("Unused", 17)
    Q.УстановитьПараметр("Name", "missing")
    Q.УстановитьПараметр("Name", K)
    исп R = Q.Выполнить()
    знч First = R.ВМассив()
    Q.УстановитьПараметр("Name", "B")
    исп Again = Q.Выполнить()
    возврат {"first": First, "again": Again.ВМассив()}
;
''',
'ArrayParameter':'''метод ArrayParameter(Keys: Массив<Строка>): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label В (&Keys)")
    Q.УстановитьПараметр("Keys", Keys)
    исп Old = Q.Выполнить()
    Keys[0] = "B"
    исп Again = Q.Выполнить()
    возврат {"old": Old.ВМассив(), "again": Again.ВМассив()}
;
''',
'Missing':'''метод Missing(): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Name")
    попытка
        исп R = Q.Выполнить()
        возврат R.ВМассив()
    поймать E: Исключение
        возврат "missing-parameter"
    ;
;
''',
'WrongType':'''метод WrongType(): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Name")
    Q.УстановитьПараметр("Name", 33)
    попытка
        исп R = Q.Выполнить()
        возврат R.ВМассив()
    поймать E: Исключение
        возврат "wrong-type"
    ;
;
''',
'ResultTemplate':'''метод ResultTemplate(): Объект?
    возврат Запрос{ВЫБРАТЬ Label ИЗ Data::Item}.Выполнить().ЕдинственныйИлиНеопределено()
;
''',
'PassResult':'''метод PassResult(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label ПОРОДИТЬ Transfer ИЗ Data::Item}
    исп R = Q.Выполнить()
    возврат Consume(R)
;
метод Consume(R: РезультатЗапроса<Transfer>): Объект
    возврат R.ВМассив()
;
''',
'State':'''метод State(): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label, Total ИЗ Data::Item ГДЕ Label == &Name")
    Q.УстановитьПараметр("Name", "A")
    исп First = Q.Выполнить()
    знч Old = First.ВМассив()
    попытка
        исп Транзакции.Начать()
        знч O: Data::Item.Объект = Data::Item.ПолучитьСсылку(новый Ууид("11111111-1111-4111-8111-111111111111")).ЗагрузитьОбъект()
        O.Total = 8
        O.Записать()
        исп During = Q.Выполнить()
        знч Inside = During.ВМассив()
        выбросить новый ИсключениеНедопустимоеСостояние(СериализацияJson.ЗаписатьОбъект(Inside))
    поймать E: Исключение
        исп After = Q.Выполнить()
        знч InsideResult: Объект? = СериализацияJson.ПрочитатьОбъект(E.Описание)
        возврат {"old": Old, "during": InsideResult, "after": After.ВМассив()}
    ;
;
''',
'Unsupported':'''метод Unsupported(): Объект
    знч Q = новый ПроизвольныйЗапрос()
    Q.Текст = "ВЫБРАТЬ Label ИЗ Data::Item"
    возврат Q.Выполнить()
;
'''
}
ANSWERS={'Produce':[{'Label':'A'},{'Label':'B'}],'Fill':[card('A'),card('B')], 'Positional':[card('A',3),card('B',1)],'Local':[{'Name':'A','Number':4},{'Name':'B','Number':4}],'Loop':1,'DirectLoop':4,'Early':'A','Break':1,'Exception':'caught','Nested':[{'Label':'A'},{'Label':'B'}],'Closed':'closed','Repeated':{'first':[{'Label':'A'},{'Label':'B'}],'repeat':'closed'},'Dynamic':{'first':[{'Label':'A','Total':3}],'again':[{'Label':'B','Total':1}]},'ArrayParameter':{'old':[{'Label':'A'}],'again':[{'Label':'B'}]},'Missing':'missing-parameter','WrongType':'wrong-type','PassResult':[{'Label':'A'},{'Label':'B'}],'State':{'old':[{'Label':'A','Total':3}],'during':[{'Label':'A','Total':8}],'after':[{'Label':'A','Total':3}]}}

def check(variant='ordinary',method='Produce',sql=False,empty=False):
 c=seed_check(variant if variant=='ordinary' else 'daily',sql=sql,empty=empty)
 c.update(args=['A'] if method=='Dynamic' else [['A']] if method=='ArrayParameter' else [],queryResults=True,runtimeProfile='9.3')
 c['target']['method']=method
 return c

def real_cases():
 cases=[]
 for method,module,fields,answers in [('ДанныеКонтрагентов','Контрагенты',{'Наименование':'Alice','Инн':'123','Кпп':'456'},[{'Наименование':'Alice','Инн':'123','Кпп':'456'}]),('ДанныеНоменклатуры','Номенклатура',{'Наименование':'Widget','Артикул':'sku-42','Описание':'authored'},[{'Наименование':'Widget','Артикул':'sku-42','Описание':'authored'}])]:
  c={'target':{'namespace':'ЗаполнениеСтруктурыВЗапросе','module':module,'method':method},'args':[],'queryResults':True,'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','initial':[{'type':'ЗаполнениеСтруктурыВЗапросе::'+module,'value':{'Ссылка':REFS[0],**fields}}]}}
  cases.append((method,c,expected(c,answers)))
 return cases

def build():
 for variant in ('ordinary','daily'):
  root=CORPUS/variant
  shutil.copytree(REPO/'tests/corpus/storage-query-fill'/variant,root,dirs_exist_ok=True)
  code='структура LocalCard\n    обз знч Name: Строка\n    пер Number: Число = 4\n    @ИменованныеПараметры\n    конструктор\n;\n'+''.join(SOURCES.values())
  if variant=='daily':
   code=code.replace('ИЗ Data::Item', 'ИЗ Data::Book.СрезПоследних(%{новый Дата("2026-09-30")})')
   # Dynamic query text uses named parameters, never captured XBSL expressions.
   code=code.replace('СрезПоследних(%{новый Дата("2026-09-30")}) ГДЕ Label == &Name','СрезПоследних() ГДЕ Label == &Name')
   code=code.replace(SOURCES['State'].replace('ИЗ Data::Item', 'ИЗ Data::Book.СрезПоследних(%{новый Дата("2026-09-30")})'),'')
  (root/'Entry/Main.xbsl').write_text(code)
 # Four exact templates have author data/answers in executable projects.
 root=CORPUS/'documented';shutil.copytree(CORPUS/'daily',root,dirs_exist_ok=True)
 code=''
 for ident in ('fill','generate','result-resources','combined-fill-slice-order'):
  q=(REPO/'tests/corpus/storage-query-fill/documented-forms'/(ident+'.xbql')).read_text().strip()
  method=ident.replace('-','_');args='D: Дата' if '%D' in q else ''
  body=q if ident=='result-resources' else 'Запрос{'+q+'}.Выполнить().ВМассив()'
  code+='метод '+method+'('+args+'): Объект'+('?' if ident=='result-resources' else '')+'\n    возврат '+body+'\n;\n'
 (root/'Entry/Main.xbsl').write_text(code)
 # Complete only the new fixture metadata, with stable generated UUIDs.
 import yaml
 ids=iter(__import__('json').loads((CORPUS/'ids.json').read_text()))
 for variant in ('ordinary','daily','documented'):
  for path in sorted((CORPUS/variant).rglob('*.yaml')):
   meta=yaml.safe_load(path.read_text())
   if 'ВидЭлемента' not in meta:continue
   meta.setdefault('Ид',next(ids))
   ns=path.parent.name
   for group in ('Реквизиты','Измерения','Ресурсы','Элементы'):
    for field in meta.get(group,[]):field.setdefault('Ид',next(ids))
   for group in ('Реквизиты','Измерения','Ресурсы','Поля'):
    for field in meta.get(group,[]):
     typ=field.get('Тип','')
     for prefix in ('Data::','Other::'):
      if typ.startswith(prefix):
       dependency=prefix[:-2]
       field['Тип']=typ[len(prefix):]
       if dependency!=ns:
        imports=meta.setdefault('Импорт',[])
        if dependency not in imports:imports.append(dependency)
   path.write_text(dump_yaml(meta,allow_unicode=True,sort_keys=False))
 def assignment(name,cases):
  p=REPO/'assignments'/name;p.mkdir(exist_ok=True)
  (p/'assignment.yaml').write_text(dump_yaml({'name':'Query API 44','checks':[{'id':n,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':e} for n,c,e in cases]},allow_unicode=True,sort_keys=False))
 assignment('result-real',real_cases())
 for variant in ('ordinary','daily'):
  assignment('result-'+variant,[(m,check(variant,m),expected(check(variant,m),a)) for m,a in ANSWERS.items() if m not in ('State','Dynamic','ArrayParameter') and (variant=='ordinary' or m!='Local')])
 c=check(method='Produce');assignment('result-control',[('fail',c,expected(c,[])),('unsupported',check(method='Unsupported'),None),('pass',c,expected(c,ANSWERS['Produce']))])
if __name__=='__main__':build()
