"""Task 37: declaration-shaped fixtures, independently specified business answers."""
from copy import deepcopy
from pathlib import Path
import yaml
from element_test.runtime import REPO

ARCHIVE = REPO / 'Dvizhok.xdump'
ASSIGNMENT = REPO / 'assignments/dvizhok-remaining-business-roots'
OUT = REPO / 'result/dvizhok-remaining-business-roots'
CORPUS = REPO / 'tests/corpus/creation-from-base'
ID = '00000000-0000-4000-8000-000000000037'
OTHER = '00000000-0000-4000-8000-000000000038'
MARKER = '00000000-0000-4000-8000-000000000039'
DATE = '2026-10-03T06:07:08'
ZERO = '00000000-0000-0000-0000-000000000000'
ROWS = [dict(Номенклатура={'Идентификатор':MARKER}, Количество=1.25, Цена=2.5, Сумма=7.75),
        dict(Номенклатура=None, Количество=0, Цена=0.5, Сумма=3.25),
        dict(Номенклатура={'Идентификатор':MARKER}, Количество=1.25, Цена=2.5, Сумма=7.75)]
ROOTS = [('Товары','Отгрузка.Объект','ПриСозданииНаОсновании'),
         ('Финансы','СчетНаОплату.Объект','ПриСозданииНаОсновании'),
         ('Финансы','ПоступлениеДенежныхСредств.Объект','ПриСозданииНаОсновании'),
         ('Финансы','РасходДенежныхСредств.Объект','ПриСозданииНаОсновании'),
         ('Товары','Отгрузка','СоздатьЧасть'), ('Товары','Отгрузка','ПеренестиТабличнуюЧасть')]


def ref(identifier=ID): return {'Идентификатор':identifier}
def entry(owner,value): return {'type':owner,'id':value['Ссылка']['Идентификатор'],'value':deepcopy(value)}
def common(identifier=MARKER): return {'Ссылка':ref(identifier),'Дата':DATE,'Номер':'retain'}
def order(rows,client=MARKER,identifier=ID):
    return {**common(identifier),'Клиент':None if client is None else ref(client),'Архивный':False,
            'СтатусЗаказа':None,'ДатаДоставки':DATE,'АдресДоставки':'retain address','Сделка':None,'Товары':deepcopy(rows)}
def invoice(total=41.5,client=OTHER,identifier=ID):
    return {**common(identifier),'Основание':ref(OTHER),'Контрагент':None if client is None else ref(client),
            'Сумма':total,'СтатусОплаты':'Оплачено'}
def context(name):
    value=common()
    if name=='Отгрузка': return {**value,'Склад':ref(MARKER),'Товары':deepcopy(ROWS[:1])}
    if name=='СчетНаОплату': return {**value,'Основание':ref(MARKER),'Контрагент':ref(MARKER),'Сумма':999,'СтатусОплаты':'Оплачено'}
    incoming=name=='ПоступлениеДенежныхСредств'
    return {**value,'Основание':{'type':'Продажи::Заказ.Ссылка','value':ref(MARKER)} if incoming else ref(MARKER),
            'Клиент' if incoming else 'Контрагент':ref(MARKER),'Сумма':999,'ТипОплаты':'Безналичные' if incoming else 'retain payment'}

def observed(value):
    value=deepcopy(value)
    if isinstance(value.get('Основание'),dict) and 'type' in value['Основание']:value['Основание']={'type':value['Основание']['type'],'value':value['Основание']['value']}
    return value

def storage(check,initial,backend):
    check['storage']={'backend':backend,'idType':'Ууид','initial':deepcopy(initial)}
    states=[entry(x['type'],observed(x['value'])) for x in initial]
    check['expected']={'result':check.pop('_answer'),'storage':deepcopy(states)}
    if check['target']['namespace']=='Товары':check['storage']['registers']=['Товары::РегистрТовары']
    if backend=='postgres':
        check['integration']={'backend':'postgres','operation':'metadata-storage'}
        check['expected']['storage']=sorted(states,key=lambda x:(x['type'],x['id']))
    return check

def handler(name,tag,rows,backend='memory',client=MARKER,total=41.5,sequence=False,missing=False):
    ns='Товары' if name=='Отгрузка' else 'Финансы'
    source='Продажи::Заказ' if name in {'Отгрузка','СчетНаОплату'} else 'Финансы::СчетНаОплату'
    value=context(name)
    base=order(rows,client) if source.endswith('Заказ') else invoice(total,client)
    neighbor=order(ROWS[:1],OTHER,OTHER) if source.endswith('Заказ') else invoice(3.25,None,OTHER)
    initial=[{'type':source,'value':base},{'type':source,'value':neighbor}]
    # Identical native UUID in a different owner must never select that object.
    if name!='Отгрузка':initial.append({'type':'Финансы::СчетНаОплату' if source.endswith('Заказ') else 'Продажи::Заказ',
                    'value':invoice(777,OTHER) if source.endswith('Заказ') else order(ROWS,MARKER)})
    def state(b,identifier,current):
        changed=deepcopy(current)
        if name=='Отгрузка':changed['Товары']+=deepcopy(b['Товары'])
        elif name=='СчетНаОплату':changed.update(Сумма=sum(r['Сумма'] for r in b['Товары']),Контрагент=b['Клиент'],Основание=ref(identifier))
        else:changed.update(Сумма=b['Сумма'],Основание={'type':'Финансы::СчетНаОплату.Ссылка','value':ref(identifier)} if name=='ПоступлениеДенежныхСредств' else ref(identifier));changed['Клиент' if name=='ПоступлениеДенежныхСредств' else 'Контрагент']=b['Контрагент']
        return observed(changed)
    answer=state(base,ID,value)
    c={'id':name+'_'+tag+'_'+backend,'type':'runtime','points':1,
       'target':{'namespace':ns,'module':name+'.Объект','method':'ПриСозданииНаОсновании'},
       'context':value,'args':[ref(ID)],'snapshotArgs':True,'trace':True,
       '_answer':{'result':answer,'args':[ref(ID)]}}
    if missing:
        c['args']=[ref(ZERO)];c['observeFailure']=True
        c['_answer']=None
    if sequence:
        c['sequence']=[{'method':'ПриСозданииНаОсновании','args':[ref(ID)]},{'method':'ПриСозданииНаОсновании','args':[ref(OTHER)]}]
        c['_answer']={'actions':[{'result':answer,'args':[ref(ID)]}, {'result':state(neighbor,OTHER,answer),'args':[ref(OTHER)]}]}
    return storage(c,initial,backend)

def helper(method,tag,rows,backend='memory',sequence=False):
    value=context('Отгрузка')
    args=[deepcopy(rows)]+([value] if method=='ПеренестиТабличнуюЧасть' else [])
    result={**common(ZERO),'Дата':'0001-01-01T00:00:00','Номер':'','Склад':None,'Товары':deepcopy(rows)} if method=='СоздатьЧасть' else None
    answer_args=deepcopy(args)
    if method=='ПеренестиТабличнуюЧасть':answer_args[1]['Товары']+=deepcopy(rows)
    c={'id':method+'_'+tag+'_'+backend,'type':'runtime','points':1,
       'target':{'namespace':'Товары','module':'Отгрузка','method':method},'args':args,
       'trace':True,'snapshotArgs':True,'_answer':{'result':result,'args':answer_args}}
    if sequence:
        c['sequence']=[{'method':method,'args':deepcopy(args)},{'method':method,'args':deepcopy(args)}]
        first=deepcopy(c['_answer']);second=deepcopy(first)
        if method=='ПеренестиТабличнуюЧасть':
            c['sequence'][1]['args'][1]={'actionArg':[0,1]}
            second['args'][1]['Товары']+=deepcopy(rows)
        c['_answer']={'actions':[first,second]}
    return storage(c,[{'type':'Продажи::Заказ','value':order(ROWS)}],backend)

def checks():
    result=[]
    for backend in ['memory','postgres']:
        for name in ['Отгрузка','СчетНаОплату','ПоступлениеДенежныхСредств','РасходДенежныхСредств']:
            for tag,rows,client,total in [('empty',[],None,0),('one',ROWS[:1],MARKER,7.75),('many',ROWS,OTHER,41.5),('nullable',ROWS[1:2],None,3.25)]:
                result.append(handler(name,tag,rows,backend,client,total))
            result.append(handler(name,'sequence',ROWS,backend,OTHER,41.5,sequence=True))
        for method in ['СоздатьЧасть','ПеренестиТабличнуюЧасть']:
            for tag,rows in [('empty',[]),('one',ROWS[:1]),('many',ROWS),('nullable',ROWS[1:2])]:
                result.append(helper(method,tag,rows,backend))
            result.append(helper(method,'sequence',ROWS,backend,sequence=True))
            result.append(probe_check(method,backend))
        c=handler('Отгрузка','empty-target',ROWS,backend);c['context']['Товары']=[];c['expected']['result']['result']['Товары']=deepcopy(ROWS);result.append(c)
        c=helper('ПеренестиТабличнуюЧасть','empty-target',ROWS,backend);c['args'][1]['Товары']=[];c['expected']['result']['args'][1]['Товары']=deepcopy(ROWS);result.append(c)
        c=handler('Отгрузка','probe',ROWS,backend)
        before=deepcopy(c['expected']['result']);after=deepcopy(before);after['result']['Товары'][1]['Количество']=93
        c['probeMutations']=[{'action':0,'target':'context','path':'Товары.1.Количество','value':93}]
        c['expected']['result']={'result':before,'probes':[[after]]};result.append(c)
    return result

def probe_check(method,backend):
    c=helper(method,'probe',ROWS,backend,sequence=True)
    before=deepcopy(c['expected']['result']);after=deepcopy(before['actions'])
    if method=='СоздатьЧасть':
        target='result';path='Товары.0.Количество'
        after[0]['result']['Товары'][0]['Количество']=93
    else:
        target='argument';path='Товары.1.Количество'
        after[0]['args'][1]=deepcopy(after[1]['args'][1])
        for a in after:a['args'][1]['Товары'][1]['Количество']=93
    probe={'action':0,'target':target,'path':path,'value':93}
    if target=='argument':probe['argument']=1
    c['probeMutations']=[probe]
    c['expected']['result']={'result':before,'probes':[deepcopy(after)]}
    # Mutate the original input too: copied document rows must retain their values.
    c['probeMutations'].append({'action':0,'target':'argument','argument':0,'path':'0.Сумма','value':94})
    after[0]['args'][0][0]['Сумма']=94
    c['expected']['result']['probes'].append(after)
    return c


def error_checks(backend):
    return [handler(name,'missing',ROWS,backend,missing=True) for name in ['Отгрузка','СчетНаОплату','ПоступлениеДенежныхСредств','РасходДенежныхСредств']]


def build():
    ASSIGNMENT.mkdir(exist_ok=True)
    yaml.SafeDumper.ignore_aliases=lambda *args:True
    (ASSIGNMENT/'assignment.yaml').write_text(yaml.safe_dump({'name':'Dvizhok: прямые бизнес-корни №37','checks':checks()},allow_unicode=True,sort_keys=False))
if __name__=='__main__':build()

PORTABLE = {'ledgers':('Ledger','Rows','Amount','Code','Archived','Label','Derive','Transfer','Create'),
            'accounts':('Account','Entries','Cost','Number','Disabled','Note','BuildFrom','Append','Make')}

def build_portable():
    for project,names in PORTABLE.items():
        doc,table,amount,number,flag,label,derive,transfer,create=names
        source=REPO/'tests/corpus/form-effects'/project
        root=CORPUS/project
        root.mkdir(parents=True,exist_ok=True)
        (root/'Проект.yaml').write_text((source/'Проект.yaml').read_text())
        for ns in ['Alpha','Beta']:
            (root/ns).mkdir(exist_ok=True)
            (root/ns/'Подсистема.yaml').write_text((source/ns/'Подсистема.yaml').read_text())
            value=yaml.safe_load((source/ns/(doc+'.yaml')).read_text())
            value['ТабличныеЧасти'][0]['Реквизиты'].append({'Имя':'Optional','Тип':'Beta::'+doc+'.Ссылка?'})
            if ns=='Alpha':value['Реквизиты'].append({'Имя':'Origin','Тип':'Alpha::'+doc+'.Ссылка|Beta::'+doc+'.Ссылка|?'})
            (root/ns/(doc+'.yaml')).write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False))
        (root/'Alpha'/(doc+'.xbsl')).write_text(f'''импорт Tests::{project}::Beta::{doc} как Source
@ВПроекте
метод {transfer}(Input: Массив<Source.{table}>, Destination: {doc}.Объект)
    для Item из Input
        Destination.{table}.Добавить(новый {doc}.{table}({amount} = Item.{amount}, Optional = Item.Optional))
    ;
;
@ВПроекте
метод {create}(Input: Массив<Source.{table}>): {doc}.Объект
    знч Value = новый {doc}.Объект()
    {transfer}(Input, Value)
    возврат Value
;
@ВПроекте
метод Fresh(Base: Tests::{project}::Beta::{doc}.Ссылка): Массив<{doc}.{table}>
    знч Loaded = Base.ЗагрузитьОбъект()
    знч A = {create}(Loaded.{table})
    знч B = {create}(Loaded.{table})
    A.{table}[0].{amount} = 99
    Loaded.{table}[0].{amount} = 98
    знч Again = Base.ЗагрузитьОбъект()
    {transfer}(Again.{table}, B)
    возврат B.{table}
;
@ВПроекте
метод Own(): Число
    знч ЗагрузитьОбъект = () -> 71
    возврат ЗагрузитьОбъект()
;
''')
        (root/'Alpha'/(doc+'.Объект.xbsl')).write_text(f'''@Обработчик
метод {derive}(Base: Tests::{project}::Beta::{doc}.Ссылка)
    знч Source = Base.ЗагрузитьОбъект()
    {label} = Source.{label}
    Origin = Base
    {doc}.{transfer}(Source.{table}, этот)
;
@ВПроекте
метод ЗагрузитьОбъект(): Число
    возврат 72
;
''')
        (root/'Beta'/(doc+'.xbsl')).write_text('''@ВПроекте
метод ЗагрузитьОбъект(): Число
    возврат 73
;
@ВПроекте
метод СоздатьОбъект(): Число
    возврат 74
;
''')


def portable_checks(project,backend='memory'):
    doc,table,amount,number,flag,label,derive,transfer,create=PORTABLE[project]
    rows=[{amount:1.25,'Optional':ref(ID)},{amount:0,'Optional':None},{amount:1.25,'Optional':ref(ID)}]
    value={'Ссылка':ref(ID),number:'source',flag:True,label:'from base',table:deepcopy(rows)}
    ctx={'Ссылка':ref(ID),number:'retain',flag:False,label:'stale',table:[{amount:4.5,'Optional':None}],
         'Origin':{'type':'Alpha::'+doc+'.Ссылка','value':ref(ID)}}
    initial=[{'type':'Beta::'+doc,'value':value},{'type':'Alpha::'+doc,'value':deepcopy(ctx)}]
    stored=[entry(x['type'],x['value']) for x in initial]
    stored[1]['value']['Origin']={'type':'Alpha::'+doc+'.Ссылка','value':ref(ID)}
    base={'type':'runtime','points':1,'storage':{'backend':backend,'idType':'Ууид','initial':initial},'trace':True,'snapshotArgs':True}
    changed=deepcopy(ctx);changed[table]+=deepcopy(rows);changed[label]='from base'
    changed['Origin']={'type':'Beta::'+doc+'.Ссылка','value':ref(ID)}
    default={'Ссылка':ref(ZERO),number:'',flag:False,label:'',table:deepcopy(rows),'Origin':None}
    results=[]
    for method,args,context_value,answer in [
        (derive,[ref(ID)],ctx,{'result':changed,'args':[ref(ID)]}),
        (create,[rows],None,{'result':default,'args':[rows]}),
        (transfer,[rows,ctx],None,{'result':None,'args':[rows,{**ctx,table:deepcopy(ctx[table])+deepcopy(rows)}]}),
        ('Fresh',[ref(ID)],None,{'result':deepcopy(rows)+deepcopy(rows),'args':[ref(ID)]}),
        ('Own',[],None,{'result':71,'args':[]}),
        ('ЗагрузитьОбъект',[],ctx,{'result':{'return':72,'context':observed(ctx)},'args':[]})]:
        c=deepcopy(base);c.update(id=project+'-'+method+'-'+backend,target={'namespace':'Alpha','module':doc+('.Объект' if context_value else ''),'method':method},args=deepcopy(args))
        if context_value:c['context']=deepcopy(context_value)
        # Native union JSON tags use canonical owners, not input fixture tags.
        if method==transfer:answer['args'][1]['Origin']={'type':'Alpha::'+doc+'.Ссылка','value':ref(ID)}
        if method=='ЗагрузитьОбъект':answer['result']['context']['Origin']={'type':'Alpha::'+doc+'.Ссылка','value':ref(ID)}
        c['expected']={'result':answer,'storage':deepcopy(stored)}
        if backend=='postgres':
            c['integration']={'backend':'postgres','operation':'metadata-storage'}
            c['expected']['storage']=sorted(c['expected']['storage'],key=lambda x:(x['type'],x['id']))
        results.append(c)
    for method,answer in [('ЗагрузитьОбъект',73),('СоздатьОбъект',74)]:
        c=deepcopy(base);c.update(id=project+'-manager-'+method+'-'+backend,
                               target={'namespace':'Beta','module':doc,'method':method},args=[])
        c['expected']={'result':{'result':answer,'args':[]},'storage':deepcopy(stored)}
        if backend=='postgres':
            c['integration']={'backend':'postgres','operation':'metadata-storage'}
            c['expected']['storage']=sorted(c['expected']['storage'],key=lambda x:(x['type'],x['id']))
        results.append(c)
    return results
