"""Task 53 teacher corpus and expectations, independent of adapter execution."""
import copy
import json
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / 'tests/corpus/partial-information-filters'
REF = {'Идентификатор': '12345678-1234-4234-8234-123456789abc'}
D1, D2 = '2026-10-01T12:00:00Z', '2026-10-02T12:00:00Z'
FINAL_RECEIPT_CONFIG = {
    'target': {'namespace': 'Товары', 'module': 'ПоступлениеТоваров.Объект', 'method': 'ПослеЗаписи'},
    'context': {}, 'args': [{}, {}],
    'storage': {'idType': 'Ууид', 'registers': ['Товары::РегистрТовары', 'Товары::РегистрПоставщикиНоменклатуры']}}


def rows():
    return [dict(Период=D1, Product=p, Supplier=s, Channel=ch, Price=v, Link=copy.deepcopy(REF))
            for p,s,ch,v in [('X','A','local',10),('X','B','local',20),('Y','C','local',30),('X',None,'remote',40)]]


def storage_expected(data=None, namespace='Ware', name='Ledger', second=False):
    data = rows() if data is None else data
    groups = {json.dumps({k:r[k] for k in ('Product','Supplier','Channel')}, ensure_ascii=False, indent=2, separators=(',', ' : ')): [] for r in rows()}
    for r in data:
        key = json.dumps({k:r[k] for k in ('Product','Supplier','Channel')}, ensure_ascii=False, indent=2, separators=(',', ' : '))
        groups.setdefault(key, []).append(copy.deepcopy(r))
    result = [{'type':namespace+'::'+name,'id':k,'value':v} for k,v in groups.items()]
    if second:
        result += [{'type':namespace+'::Mirror','id':x['id'],'value':copy.deepcopy(x['value'])} for x in result[:]]
    return result


def check(method='Read', args=None, sql=False, namespace='Ware', name='Ledger', second=False):
    rs = rows()
    initial = [{'type':namespace+'::'+name, 'filter':{k:r[k] for k in ('Product','Supplier','Channel')}, 'rows':[r]} for r in rs]
    if second:
        initial += [{**copy.deepcopy(s),'type':namespace+'::Mirror'} for s in initial[:]]
    c = {'target':{'namespace':namespace,'module':'Flow','method':method}, 'args':[] if args is None else args,
         'storage':{'idType':'Ууид','lifecycle':True,'registers':[namespace+'::'+name]+([namespace+'::Mirror'] if second else []),
                    'initialRegisters':initial},'timeout':'20s','trace':True}
    if sql:
        c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c


SOURCE = '''метод Read(): Объект
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = "X")
    R.Прочитать()
    пер Sum = 0
    для Row из R
        Sum += Row.Price
    ;
    возврат {"count": R.Размер(), "sum": Sum}
;
метод Full(): Объект
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Channel = "local", Supplier = "A", Product = "X")
    R.Прочитать()
    пер Sum = 0
    для Row из R
        Sum += Row.Price
    ;
    возврат {"count": R.Размер(), "sum": Sum}
;
метод Refilter(): Число
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = "X")
    R.Фильтр.Установить(Supplier = "C")
    R.Прочитать()
    пер Sum = 0
    для Row из R
        Sum += Row.Price
    ;
    возврат Sum
;
метод ClearNull()
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = "X", Supplier = Неопределено)
    R.Записать()
;
метод QuerySum(): Число
    знч Q = Запрос {ВЫБРАТЬ Price ИЗ Ledger ГДЕ Product == %{"X"}}
    пер Sum = 0
    для Row из Q.Выполнить()
        Sum += Row.Price
    ;
    возврат Sum
;
метод Null(): Объект
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = "X", Supplier = Неопределено)
    R.Прочитать()
    возврат R.Записи
;
метод Replace(Empty: Булево, FullFilter: Булево)
    знч R = новый Ledger.НаборЗаписей()
    если FullFilter
        R.Фильтр.Установить(Product = "X", Channel = "local", Supplier = "A")
    иначе
        R.Фильтр.Установить(Product = "X")
    ;
    если не Empty
        R.ДобавитьЗапись(Период = новый Момент("2026-10-02T12:00:00Z"), Product = "X", Supplier = "A", Channel = "local", Price = 99, Link = Неопределено)
    ;
    R.Записать()
;
метод Append()
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = "X")
    R.ДобавитьЗапись(Период = новый Момент("2026-10-02T12:00:00Z"), Product = "X", Supplier = "A", Channel = "local", Price = 99, Link = Неопределено)
    R.Записать(Ложь)
    если не R.Пусто()
        выбросить новый ИсключениеНедопустимоеСостояние("append did not clear")
    ;
;
метод Snapshot(): Объект
    знч A = новый Ledger.НаборЗаписей()
    A.Фильтр.Установить(Product = "X")
    A.Прочитать()
    знч Old = A.Записи
    знч B = новый Ledger.НаборЗаписей()
    B.Фильтр.Установить(Product = "X")
    B.Прочитать()
    для Row из Old
        (Row.Link как Item.Ссылка).Идентификатор = новый Ууид("33333333-1234-4234-8234-123456789abc")
        Row.Price = 777
    ;
    A.Фильтр.Установить(Product = "X", Supplier = Неопределено)
    A.Прочитать()
    пер OtherSum = 0
    пер OtherLinkOK = Истина
    для Row из B
        OtherSum += Row.Price
        OtherLinkOK = OtherLinkOK и (Row.Link как Item.Ссылка).Идентификатор == новый Ууид("12345678-1234-4234-8234-123456789abc")
    ;
    возврат {"oldCount": Old.Размер(), "fresh": A.Записи, "otherCount": B.Размер(), "otherSum": OtherSum, "otherLinkOK": OtherLinkOK}
;
метод Reject()
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = "X")
    R.ДобавитьЗапись(Период = новый Момент("2026-10-02T12:00:00Z"), Product = "Y", Supplier = "A", Channel = "local", Price = 99, Link = Неопределено)
    R.Записать()
;
метод Conflict()
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = "X")
    R.Прочитать()
    R.Записать(Ложь)
;
метод Latest(): Число
    знч Q = Запрос {
        ВЫБРАТЬ Price КАК Price
        ИЗ Ledger.СрезПоследних(%{Момент{Максимум}})
        ГДЕ Product == %{"X"} И Supplier == %{"A"} И Channel == %{"local"}
    }
    пер Result = 0
    для Row из Q.Выполнить()
        Result += Row.Price
    ;
    возврат Result
;
метод Persist()
    знч O = новый Log.Объект(Ссылка = Log.ПолучитьСсылку(новый Ууид("abcdef12-1234-4234-8234-123456789abc")))
    O.Записать()
;
метод EmptyFilter()
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить()
    R.Записать()
;
метод UnknownFilter()
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Unknown = "X")
;
метод WrongType()
    знч R = новый Ledger.НаборЗаписей()
    R.Фильтр.Установить(Product = 123)
;
'''


def fixture(root, namespace='Ware', name='Ledger'):
    root.mkdir(parents=True, exist_ok=True); data=root/namespace;data.mkdir(exist_ok=True)
    (root/'Проект.yaml').write_text('Имя: PartialFilters\nПоставщик: teacher\nРежимСовместимости: 9.0\n')
    original=REPO/'tests/corpus/record-set-reading/information/Data/Item.yaml'
    (data/'Item.yaml').write_bytes(original.read_bytes())
    metadata = {'ВидЭлемента':'РегистрСведений','Имя':name,'ОбластьВидимости':'ВПроекте','Периодичность':'Момент',
                'Измерения':[{'Имя':'Product','Тип':'Строка'},{'Имя':'Supplier','Тип':'Строка?'},{'Имя':'Channel','Тип':'Строка'}],
                'Ресурсы':[{'Имя':'Price','Тип':'Число'}],'Реквизиты':[{'Имя':'Link','Тип':'Item.Ссылка?'}]}
    for reg in (name,'Mirror'):
        (data/(reg+'.yaml')).write_text(yaml.safe_dump({**metadata,'Имя':reg},allow_unicode=True,sort_keys=False))
    (data/'Flow.xbsl').write_text(SOURCE.replace('Ledger',name))
    (data/'Log.yaml').write_text('ВидЭлемента: Документ\nИмя: Log\nОбластьВидимости: ВПроекте\nРеквизиты: []\n')
    (data/'Log.Объект.xbsl').write_text('''метод ПослеЗаписи(До: Log.Данные, Параметры: Log.ПараметрыЗаписи)
    Flow.Replace(Ложь, Ложь)
    знч R = новый Mirror.НаборЗаписей()
    R.Фильтр.Установить(Product = "X")
    R.Записать()
    выбросить новый ИсключениеНедопустимоеСостояние("after-two-effects")
;
''')


def replacement(full=False, empty=False):
    result = [r for r in rows() if r['Product'] != 'X' or full and r['Supplier'] != 'A']
    if not empty:
        result.append(dict(Период=D2,Product='X',Supplier='A',Channel='local',Price=99,Link=None))
    return result


def cases():
    result = [('partial-read',check(),{'count':3,'sum':70},rows()),
              ('full-read',check('Full'),{'count':1,'sum':10},rows()),
              ('null-read',check('Null'),[rows()[3]],rows()),
              ('fresh-and-detached',check('Snapshot'),{'oldCount':3,'fresh':[rows()[3]],'otherCount':3,'otherSum':70,'otherLinkOK':True},rows())]
    for full in (False,True):
        for empty in (False,True):
            c=check('Replace',[empty,full]);data=replacement(full,empty)
            # Repeat writes exercise fresh canonical selection and idempotence.
            c['sequence']=[{'method':'Replace','args':[empty,full]},{'method':'Replace','args':[empty,full]}]
            result.append((f'replace-{full}-{empty}',c,{'actions':[None,None]},data))
    c=check('Append');c['sequence']=[{'method':'Append','args':[]},{'method':'Latest','args':[]},{'method':'Full','args':[]}]
    added=replacement()[-1]
    result.append(('append-slice-overlap',c,{'actions':[None,99,{'count':2,'sum':109}]},rows()+[added]))
    result.append(('refilter-resets-mask',check('Refilter'),30,rows()))
    result.append(('clear-null-only',{**check('ClearNull'), 'sequence':[{'method':'ClearNull','args':[]}]},{'actions':[None]},[r for r in rows() if r['Supplier'] is not None]))
    c=check('Replace',[False,False]);c['sequence']=[{'method':'Replace','args':[False,False]},{'method':'QuerySum','args':[]},{'method':'Latest','args':[]}]
    result.append(('fresh-query-after-replace',c,{'actions':[None,99,99]},replacement()))
    for method in ('Reject','Conflict','Persist'):
        c=check(method,second=method=='Persist');c['captureException']=True
        result.append((method,c,None,rows()))
    return result


def write_assignments():
    for sql in (False,True):
        checks=[]
        for name,c,returned,data in cases():
            if returned is None: continue  # exception payload is asserted by mechanism tests
            if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
            checks.append(dict(id=name,type='runtime',points=1,comparison='record-sets-unordered',**c,
                               expected={'result':returned,'storage':storage_expected(data)}))
        folder=REPO/'assignments'/('partial-information-filters'+('-sql' if sql else ''));folder.mkdir(exist_ok=True)
        class Dumper(yaml.SafeDumper):
            def ignore_aliases(self, data):return True
        (folder/'assignment.yaml').write_text(yaml.dump({'name':'Частичные фильтры РС №53','checks':checks},Dumper=Dumper,allow_unicode=True,sort_keys=False))

if __name__ == '__main__':
    fixture(CORPUS/'ordinary')
    fixture(CORPUS/'renamed','Запасы','История')
    write_assignments()
