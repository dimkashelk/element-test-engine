"""Task 43 author histories/answers, fixed independently of executor outputs."""
from pathlib import Path
import copy,json
from composite_fixtures import dump_yaml
from record_set_fixtures import REFS,audit

REPO=Path(__file__).resolve().parents[1]
CORPUS=REPO/'tests/corpus/virtual-tables'
EVIDENCE=REPO/'result/virtual-tables'
D='2026-10-01'; START=D+'T00:00:00'; END=D+'T23:59:59'
QUERIES={
 'First':'ВЫБРАТЬ Key, Total ИЗ Data::Book.СрезПервых(%D)',
 'Last':'ВЫБРАТЬ Key, Total ИЗ Data::Book.СрезПоследних(%D)',
 'FirstAll':'ВЫБРАТЬ Key, Total ИЗ Data::Book.СрезПервых()',
 'Seconds':'ВЫБРАТЬ Key, Total ИЗ Data::Seconds.СрезПоследних(%T)',
 'SecondsFirst':'ВЫБРАТЬ Key, Total ИЗ Data::Seconds.СрезПервых(%T)',
 'Info':'ВЫБРАТЬ Key, Total ИЗ Data::Book',
 'Plain':'ВЫБРАТЬ Key, Total ИЗ Data::Plain',
 'Balance':'ВЫБРАТЬ Key, TotalОстаток, TotalОстатокПоложительный, TotalОстатокОтрицательный ИЗ Data::Ledger.Остатки(%T)',
 'BalanceAll':'ВЫБРАТЬ Key, TotalОстаток ИЗ Data::Ledger.Остатки',
 'Turnover':'ВЫБРАТЬ Key, TotalОборот, TotalПриход, TotalРасход ИЗ Data::Ledger.Обороты(%S, %E)',
 'TurnoverAll':'ВЫБРАТЬ Key, TotalОборот ИЗ Data::Ledger.Обороты',
 'Combined':'ВЫБРАТЬ Key, TotalНачальныйОстаток, TotalКонечныйОстаток, TotalОборот, TotalПриход, TotalРасход ИЗ Data::Ledger.ОстаткиИОбороты(%S, %E)',
 'Sales':'ВЫБРАТЬ Key, TotalОборот ИЗ Data::Sales.Обороты(%S, %E)',
 'Rows':'ВЫБРАТЬ Индекс, НомерСтроки, Value ИЗ Data::Item.Rows ГДЕ Владелец == %R УПОРЯДОЧИТЬ ПО НомерСтроки',
 'Phones':'ВЫБРАТЬ Индекс, Элемент ИЗ Data::Item.Phones ГДЕ Владелец == %R УПОРЯДОЧИТЬ ПО Индекс',
 'Join':'ВЫБРАТЬ A.Key, B.TotalОстаток.ЗаменитьNull(0) КАК Value ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Data::Ledger.Остатки(%T) КАК B ПО A.Key == B.Key',
 'Nested':'ВЫБРАТЬ СУММА(Q.TotalОборот) КАК Value ИЗ (ВЫБРАТЬ TotalОборот ИЗ Data::Ledger.Обороты(%S, %E)) КАК Q',
 'Union':'ВЫБРАТЬ Key КАК Value ИЗ Data::Book.СрезПервых(%D) ОБЪЕДИНИТЬ ВЫБРАТЬ Key ИЗ Data::Ledger.Остатки',
 'PostFilter':'ВЫБРАТЬ Key ИЗ Data::Book.СрезПервых(%D) ГДЕ Total == %N',
 'Users':'ВЫБРАТЬ Представление ИЗ Пользователи ГДЕ Администратор == %B',
 'Constants':'ВЫБРАТЬ Total ИЗ Data::Options',
 'Saved':'ВЫБРАТЬ Key, TotalОстаток ИЗ Data::Totals',
 'Unsupported':'ВЫБРАТЬ Key ИЗ Data::Ledger.Изменения',
}
ANSWERS={
 'First':[{'Key':'a','Total':3},{'Key':'b','Total':7}],
 'Last':[{'Key':'a','Total':3},{'Key':'b','Total':6}],
 'FirstAll':[{'Key':'a','Total':10},{'Key':'b','Total':6}],
 'Seconds':[{'Key':'a','Total':2}], 'SecondsFirst':[{'Key':'a','Total':2}],
 'Info':[{'Key':'a','Total':10},{'Key':'a','Total':3},{'Key':'a','Total':9},{'Key':'b','Total':6},{'Key':'b','Total':7}],
 'Plain':[{'Key':'a','Total':4}],
 'Balance':[{'Key':'a','TotalОстаток':10,'TotalОстатокПоложительный':10,'TotalОстатокОтрицательный':0},{'Key':'b','TotalОстаток':-2,'TotalОстатокПоложительный':0,'TotalОстатокОтрицательный':2}],
 'BalanceAll':[{'Key':'a','TotalОстаток':13},{'Key':'b','TotalОстаток':-1}],
 'Turnover':[{'Key':'a','TotalОборот':3,'TotalПриход':8,'TotalРасход':5},{'Key':'b','TotalОборот':1,'TotalПриход':1,'TotalРасход':0},{'Key':'c','TotalОборот':0,'TotalПриход':2,'TotalРасход':2}],
 'TurnoverAll':[{'Key':'a','TotalОборот':13},{'Key':'b','TotalОборот':-1},{'Key':'c','TotalОборот':0}],
 'Combined':[{'Key':'a','TotalНачальныйОстаток':10,'TotalКонечныйОстаток':13,'TotalОборот':3,'TotalПриход':8,'TotalРасход':5},{'Key':'b','TotalНачальныйОстаток':-2,'TotalКонечныйОстаток':-1,'TotalОборот':1,'TotalПриход':1,'TotalРасход':0},{'Key':'c','TotalНачальныйОстаток':0,'TotalКонечныйОстаток':0,'TotalОборот':0,'TotalПриход':2,'TotalРасход':2}],
 'Sales':[{'Key':'a','TotalОборот':6}],
 'Rows':[{'Индекс':0,'НомерСтроки':1,'Value':3},{'Индекс':1,'НомерСтроки':2,'Value':3}],
 'Phones':[{'Индекс':0,'Элемент':'x'},{'Индекс':1,'Элемент':'x'},{'Индекс':2,'Элемент':'y'}],
 'Join':[{'Key':'a','Value':10},{'Key':'b','Value':-2},{'Key':'d','Value':0}],
 'Nested':[{'Value':4}], 'Union':[{'Value':'a'},{'Value':'b'}], 'PostFilter':[],
 'Users':[{'Представление':'admin'}], 'Constants':[{'Total':12}],
 'Saved':[{'Key':'a','TotalОстаток':13},{'Key':'b','TotalОстаток':-1}],
 'State':{'old':[{'Key':'a','TotalОстаток':13},{'Key':'b','TotalОстаток':-1}], 'during':[{'Key':'a','TotalОстаток':18},{'Key':'b','TotalОстаток':-1}], 'after':[{'Key':'a','TotalОстаток':13},{'Key':'b','TotalОстаток':-1}]},
}
ARGS={'First':[D],'Last':[D],'Seconds':[START],'SecondsFirst':[START],'Balance':[START],'Turnover':[START,END],'Combined':[START,END],'Sales':[START,END],'Rows':[REFS[0]],'Phones':[REFS[0]],'Join':[START],'Nested':[START,END],'Union':[D],'PostFilter':[D,10]}
ARGS['Users']=[True]

def check(variant='ordinary',method='Balance',sql=False,empty=False):
 ns='Учет' if variant=='renamed' else 'Data'
 def seed(owner,filter,rows):return {'type':ns+'::'+owner,'filter':filter,'rows':rows}
 history=[seed('Book',{'Key':key},[{'Key':key,'Период':d,'Total':n} for d,n in entries]) for key,entries in [('a',[('2026-09-30',10),(D,3),('2026-10-02',9)]),('b',[('2026-09-29',6),('2026-10-02',7)])]]
 history += [seed('Seconds',{'Key':'a'},[{'Key':'a','Период':t,'Total':n} for t,n in [('2026-09-30T23:59:59',1),(START,2),('2026-10-01T00:00:01',3)]]),seed('Plain',{'Key':'a'},[{'Key':'a','Total':4}])]
 moves=[]
 for key,t,n,k,active in [('a','2026-09-30T23:59:59',10,'Приход',True),('a',START,5,'Расход',True),('a',END,8,'Приход',True),('a',START,900,'Приход',False),('b','2026-09-30T00:00:00',2,'Расход',True),('b',START,1,'Приход',True),('c',START,2,'Приход',True),('c',END,2,'Расход',True)]:
  moves.append({'Регистратор':REFS[0],'Активность':active,'Период':t,'Key':key,'Total':n,'ВидЗаписи':k})
 history += [seed('Ledger',{'Регистратор':REFS[0]},moves),seed('Sales',{'Регистратор':REFS[1]},[{'Регистратор':REFS[1],'Активность':True,'Период':START,'Key':'a','Total':6}])]
 objects=[{'type':ns+'::Item','value':{'Ссылка':REFS[i],'Key':key,'Phones':['x','x','y'] if i==0 else [],'Rows':[{'Value':3},{'Value':3}] if i==0 else []}} for i,key in enumerate(('a','b','d'))]
 c={'target':{'namespace':'Вход' if variant=='renamed' else 'Entry','module':'Поток' if variant=='renamed' else 'Main','method':method},'args':copy.deepcopy(ARGS.get(method,[])),'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','registers':[ns+'::'+name for name in ('Book','Seconds','Plain','Ledger','Sales')],'initial':[] if empty else objects,'initialRegisters':[] if empty else history}}
 if method=='Users':c['queryContext']={'users':[{'Ссылка':REFS[i],'Представление':name,'Администратор':i==0,'БылУспешныйВход':True,'ЗапрещенВход':False,'РазрешенДоступПоТокену':False} for i,name in enumerate(('admin','reader'))]}
 if method=='Constants':c['constants']={ns+'::Options':{'Total':12}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c

def expected(c,result):
 return {'result':copy.deepcopy(result),'storage':[audit(s) for s in c['storage']['initialRegisters']]+[{'type':o['type'],'id':o['value']['Ссылка']['Идентификатор'],'value':copy.deepcopy(o['value'])} for o in c['storage']['initial']]}

def real_check(empty=False,sql=False):
 from creation_fixtures import context
 value=context('Отгрузка')
 if empty:value['Товары']=[]
 registrar={'type':'Товары::ПоступлениеТоваров.Ссылка','value':REFS[0]}
 seed={'type':'Товары::РегистрТовары','filter':{'Регистратор':registrar},'rows':[{'Регистратор':registrar,'Активность':True,'Период':'2026-10-01T00:00:00','ВидЗаписи':'Приход','Номенклатура':value['Товары'][0]['Номенклатура'] if value['Товары'] else REFS[1],'Склад':value['Склад'],'Количество':5}]}
 c={'target':{'namespace':'Товары','module':'Отгрузка.Объект','method':'ПередЗаписью'},'context':value,'args':[{},{}],'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','registers':['Товары::РегистрТовары'],'initialRegisters':[seed]}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c

def real_expected(c):
 states=[audit(s) for s in c['storage']['initialRegisters']]
 # Filter keys use the Script nominal registrar identity, whereas the audited
 # row keeps the original qualified project identity (task-32 storage contract).
 for state in states:state['id']=state['id'].replace('Товары::ПоступлениеТоваров.Ссылка','Scripts::ПоступлениеТоваров.Ссылка')
 return {'result':copy.deepcopy(c['context']),'storage':states}

def build():
 ids=iter(json.loads((CORPUS/'ids.json').read_text()))
 def field(name,typ):return {'Ид':next(ids),'Имя':name,'Тип':typ}
 for variant in ('ordinary','renamed'):
  ns='Учет' if variant=='renamed' else 'Data';entry='Вход' if variant=='renamed' else 'Entry';main='Поток' if variant=='renamed' else 'Main'
  root=CORPUS/variant;(root/ns).mkdir(parents=True,exist_ok=True);(root/entry).mkdir(exist_ok=True)
  (root/'Проект.yaml').write_text(dump_yaml({'Имя':'virtual-'+variant,'Поставщик':'Teacher','РежимСовместимости':'9.3'},allow_unicode=True,sort_keys=False))
  for sub in (ns,entry):(root/sub/'Подсистема.yaml').write_text(dump_yaml({'ВидЭлемента':'Подсистема','Ид':next(ids),'Имя':sub,'ОбластьВидимости':'ВПроекте',**({'Использование':[ns]} if sub==entry else {})},allow_unicode=True,sort_keys=False))
  def meta(name,kind,**extra):
   value={'ВидЭлемента':kind,'Ид':next(ids),'Имя':name,'ОбластьВидимости':'ВПроекте',**extra}
   if kind in {'Справочник','Документ','РегистрСведений','РегистрНакопления'}:value['КонтрольДоступа']={'Разрешения':{'ПоУмолчанию':'РазрешеноВсем'}}
   (root/ns/(name+'.yaml')).write_text(dump_yaml(value,allow_unicode=True,sort_keys=False))
  meta('Item','Справочник',Реквизиты=[field('Key','Строка'),field('Phones','Массив<Строка>')],ТабличныеЧасти=[{'Ид':next(ids),'Имя':'Rows','Реквизиты':[field('Value','Число')]}])
  meta('Act','Документ',Реквизиты=[{'Имя':'Дата','Тип':'ДатаВремя'}])
  for name,p in [('Book','День'),('Seconds','Секунда'),('Plain','Непериодический')]:meta(name,'РегистрСведений',Периодичность=p,Измерения=[field('Key','Строка')],Ресурсы=[field('Total','Число')])
  for name,k in [('Ledger','Остатки'),('Sales','Обороты')]:meta(name,'РегистрНакопления',ВидРегистра=k,Измерения=[field('Key','Строка')],Ресурсы=[field('Total','Число')],Реквизиты=[{'Имя':'Регистратор','Тип':'Act.Ссылка?'}])
  meta('Options','НаборКонстант',Константы=[field('Total','Число')])
  meta('Totals','ВиртуальнаяТаблица',Параметры=[])
  (root/ns/'Totals.xbql').write_text('ВЫБРАТЬ Key, TotalОстаток ИЗ Ledger.Остатки\n')
  code='// Portable generated English test contract; production names are unchanged.\n'
  for method,query in QUERIES.items():
   types=['D: Дата'] if method in ('First','Last','Union') else ['T: ДатаВремя'] if method in ('Seconds','SecondsFirst','Balance','Join') else ['S: ДатаВремя, E: ДатаВремя'] if method in ('Turnover','Combined','Sales','Nested') else ['R: '+ns+'::Item.Ссылка'] if method in ('Rows','Phones') else ['D: Дата, N: Число'] if method=='PostFilter' else ['B: Булево'] if method=='Users' else []
   code+='метод '+method+'('+', '.join(types)+'): Объект\n    возврат Запрос{'+query.replace('Data::',ns+'::')+'}.Выполнить()\n;\n'
  code+='''метод State(): Объект
    знч Query = Запрос{ВЫБРАТЬ Key, TotalОстаток ИЗ Data::Ledger.Остатки}
    знч Old = Query.Выполнить()
    пер During: Объект = новый Массив<Объект>()
    попытка
        исп Транзакции.Начать()
        знч Records = новый Data::Ledger.НаборЗаписей()
        Records.Фильтр.Установить(новый Data::Act.Ссылка(Идентификатор = новый Ууид("33333333-1234-4234-8234-123456789abc")))
        Records.ДобавитьЗапись(Период = новый ДатаВремя("2026-10-03T00:00:00"), ВидЗаписи = ВидЗаписиРегистраНакопления.Приход, Key = "a", Total = 5)
        Records.Записать()
        During = Query.Выполнить()
        выбросить новый ИсключениеВалидации("rollback")
    поймать Failure: ИсключениеВалидации
        возврат {"old": Old, "during": During, "after": Query.Выполнить()}
    ;
;
'''.replace('Data::',ns+'::')
  (root/entry/(main+'.xbsl')).write_text(code)
  for sql in (False,True):
   methods=ANSWERS if not sql else ('Balance','Combined','Rows','State')
   checks=[]
   for method in methods:
    c=check(variant,method,sql);checks.append({'id':method,'type':'runtime','points':1,'comparison':'record-sets-unordered' if method=='State' else 'query-rows-unordered',**c,'expected':expected(c,ANSWERS[method])})
   p=REPO/'assignments'/('virtual-'+variant+('-sql' if sql else ''));p.mkdir(exist_ok=True);(p/'assignment.yaml').write_text(dump_yaml({'name':'Виртуальные источники №43','checks':checks},allow_unicode=True,sort_keys=False))
 root=CORPUS/'documented';import shutil;shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
 # Review templates remain byte-for-byte XBQL; Ledger uses the same storage.
 text=[]
 for method in ('slice-last','slice-first','balances','turnovers'):
  query=(REPO/'tests/corpus/storage-query-fill/documented-forms'/(method+'.xbql')).read_text().strip()
  text.append('импорт Data\n' if not text else '')
  text.append('метод '+method.replace('-','_')+'('+('D: Дата' if method.startswith('slice') else '')+'): Объект\n    возврат Запрос{'+query+'}.Выполнить()\n;\n')
 (root/'Entry/Main.xbsl').write_text(''.join(text))
 control=[]
 for method,result in [('Balance',[]),('Unsupported',None),('Balance',ANSWERS['Balance'])]:
  c=check(method=method);control.append({'id':str(len(control)),'type':'runtime','points':1,'comparison':'query-rows-unordered',**c,'expected':expected(c,result)})
 p=REPO/'assignments/virtual-control';p.mkdir(exist_ok=True);(p/'assignment.yaml').write_text(dump_yaml({'name':'FAIL UNSUPPORTED PASS','checks':control},allow_unicode=True,sort_keys=False))
 for sql in (False,True):
  checks=[]
  for empty in (False,True):
   c=real_check(empty,sql);checks.append({'id':'empty' if empty else 'sufficient','type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':real_expected(c)})
  p=REPO/'assignments'/('virtual-real'+('-sql' if sql else ''));p.mkdir(exist_ok=True);(p/'assignment.yaml').write_text(dump_yaml({'name':'Реальная проверка остатков Отгрузка','checks':checks},allow_unicode=True,sort_keys=False))

if __name__=='__main__':build()
