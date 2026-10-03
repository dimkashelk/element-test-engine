"""Teacher fixtures and independently specified answers for task 36."""
from pathlib import Path
from copy import deepcopy
import yaml
from element_test.runtime import REPO
yaml.SafeDumper.ignore_aliases=lambda *args:True

OUT=REPO/'result/dvizhok-form-effects'
ASSIGNMENT=REPO/'assignments/dvizhok-form-effects'
ARCHIVE=REPO/'Dvizhok.xdump'
CORPUS=REPO/'tests/corpus/form-effects'
ID='00000000-0000-4000-8000-000000000036'
OTHER='00000000-0000-4000-8000-000000000037'
PRODUCT='00000000-0000-4000-8000-000000000038'
DATE='2026-10-02T06:00:00'
ROWS=[{'Номенклатура':{'Идентификатор':PRODUCT},'Количество':1.25,'Цена':2.5,'Сумма':7.75},
      {'Номенклатура':{'Идентификатор':PRODUCT},'Количество':1.25,'Цена':2.5,'Сумма':7.75},
      {'Номенклатура':None,'Количество':0,'Цена':0.5,'Сумма':3.25}]


def object_value(name, flag=False, number=''):
    common={'Ссылка':{'Идентификатор':ID},'Дата':DATE,'Номер':number,'Клиент':None,'Архивный':flag}
    if name=='Заказ':
        return {**common,'СтатусЗаказа':None,'ДатаДоставки':DATE,'АдресДоставки':'untouched address',
                'Сделка':None,'Товары':deepcopy(ROWS)}
    return {**common,'СтатусСделки':'Новая','Сумма':13.75,'ДатаЗакрытия':DATE,
            'Участники':[{'ВыигралСделку':True,'Партнер':None,'Комментарий':'retain'}],
            'ПервичныйСпрос':[{'ОписаниеПотребностиКлиента':'retain','Сумма':3.25,'ПроцентУдовлетворения':25,
                              'ПричинаНеудовлетворения':'reason','Комментарий':'other'}]}


def entry(owner,value):
    return {'type':owner,'id':value['Ссылка']['Идентификатор'],'value':deepcopy(value)}


def archive_check(ns,name,flag,new,number,backend='memory',sequence=False):
    value=object_value(name,flag,number);owner=ns+'::'+name
    neighbor=deepcopy(value);neighbor['Ссылка']['Идентификатор']=OTHER;neighbor['Номер']='neighbor'
    initial=([{'type':owner,'value':deepcopy(value)}] if not new else [])+[{'type':owner,'value':neighbor}]
    def state(f):
        changed=deepcopy(value);changed['Архивный']=f
        stored=[entry(owner,changed),entry(owner,neighbor)]
        # Session maps enumerate insertion order; initial current first for existing.
        if new:stored.reverse()
        return {'result':{'Объект':changed,'ПредставлениеАрхивирования':'Вернуть из архива' if f else 'Архивировать',
                          'ЗаголовокФормы':'untouched'},'openings':[],'lifecycle':False,'storage':stored}
    method='АрхивироватьОбработчик'
    states=[state(not flag),state(flag)] if sequence else [state(not flag)]
    c={'id':f'{name}_write_{int(flag)}_{int(new)}_{number or "empty"}_{backend}'+('_sequence' if sequence else ''),
       'type':'runtime','points':1,'target':{'module':name+'ФормаОбъекта','namespace':ns,'method':method},
       'context':{'Объект':value,'ЗаголовокФормы':'untouched'},'lifecycle':{'isNew':new},'args':[{}],
       'formEffects':{'write':True},'storage':{'backend':backend,'idType':'Ууид','initial':initial},
       'observe':['Объект','ПредставлениеАрхивирования','ЗаголовокФормы'],'trace':True,
       'expected':{'result':{'actions':states} if sequence else states[0],'storage':states[-1]['storage']}}
    if backend=='postgres':
        c['integration']={'backend':'postgres','operation':'metadata-storage'}
        # Trusted audit orders type,id; driver snapshot order remains original.
        c['expected']['storage']=sorted(c['expected']['storage'],key=lambda x:(x['type'],x['id']))
        c['expected']['sqlSnapshots']=[sorted([entry(owner,x['value']) for x in initial],key=lambda x:(x['type'],x['id']))]+[sorted(state['storage'],key=lambda x:(x['type'],x['id'])) for state in states]
    if sequence:c['sequence']=[{'method':method,'args':[{}]},{'method':method,'args':[{}]}]
    return c


def opening_check(tag,rows,count,quantity,total,backend='memory',lifecycle=False,sequence=False,path=False):
    value=object_value('Заказ',False,'saved');owner='Продажи::Заказ';storage=[entry(owner,value)]
    def request(data,n,q,s):
        req={'owner':'Товары::ОтгрузкаФормаОбъекта','properties':{'СозданныйНаЧасти':True,
             'ТоварыТабличнаяЧасть':deepcopy(data),'КоличествоВыбранныхСтрок':n,'КоличествоТоваров':q,'СуммаВыбранныхТоваров':s}}
        if lifecycle:req['context']={'ЗаголовокФормы':'Отгрузка (новая)',
                                    'ТекстИтоговЧасти':f'Выбрано строк: {n}; товаров: {q}; сумма: {s}', 'Объект.Товары':[]}
        return req
    def state(data,requests):
        return {'result':{'result':{'Объект':deepcopy(value),'ЗаголовокФормы':'untouched'},'args':[{},deepcopy(data)]},
                'openings':requests,'lifecycle':False,'storage':storage}
    requests=[request(rows,count,quantity,total)]
    states=[state(rows,requests)]
    if sequence:states.append(state([],requests+[request([],0,0,0)]))
    method='ОтгрузитьОбработчик'
    cfg={'open':['Товары::ОтгрузкаФормаОбъекта']}
    if lifecycle:cfg['lifecycle']={'Товары::ОтгрузкаФормаОбъекта':{'method':'ПослеСоздания','isNew':True,
                                                           'observe':['ЗаголовокФормы','ТекстИтоговЧасти','Объект.Товары']}}
    args=[{}, {'contextPath':'Объект.Товары'} if path else deepcopy(rows)]
    c={'id':'Заказ_open_'+tag+'_'+backend,'type':'runtime','points':1,
       'target':{'module':'ЗаказФормаОбъекта','namespace':'Продажи','method':method},
       'context':{'Объект':value,'ЗаголовокФормы':'untouched'},'lifecycle':{'isNew':False},'args':args,
       'observe':['Объект','ЗаголовокФормы'],'snapshotArgs':True,'trace':True,'formEffects':cfg,
       'storage':{'backend':backend,'idType':'Ууид','initial':[{'type':owner,'value':value}]},
       'expected':{'result':{'actions':states} if sequence else states[0],'storage':storage}}
    if backend=='postgres':
        c['integration']={'backend':'postgres','operation':'metadata-storage'}
        c['expected']['sqlSnapshots']=[deepcopy(storage)]
    if sequence:c['sequence']=[{'method':method,'args':args},{'method':method,'args':[{},[]]}]
    return c


def checks():
    result=[]
    for backend in ['memory','postgres']:
        for ns,name in [('CRM','Сделка'),('Продажи','Заказ')]:
            for flag in [False,True]:
                for new,number in [(True,''),(True,'42'),(False,''),(False,'42')]:
                    result.append(archive_check(ns,name,flag,new,number,backend))
            result.append(archive_check(ns,name,False,False,'',backend,sequence=True))
        for tag,rows,n,q,s in [('empty',[],0,0,0),('one',[ROWS[0]],1,1.25,7.75),
                              ('several',ROWS,3,2.5,18.75),('subset',[ROWS[2]],1,0,3.25)]:
            result.append(opening_check(tag,rows,n,q,s,backend,lifecycle=tag=='several'))
        result.append(opening_check('sequence',ROWS,3,2.5,18.75,backend,sequence=True,path=True))
    return result


def build():
    ASSIGNMENT.mkdir(exist_ok=True)
    yaml.SafeDumper.ignore_aliases=lambda *args:True
    (ASSIGNMENT/'assignment.yaml').write_text(yaml.safe_dump({'name':'Dvizhok: запись и открытие форм (явный контракт №36)',
                                                            'checks':checks()},allow_unicode=True,sort_keys=False))

if __name__=='__main__':build()

PORTABLE={'ledgers':('Ledger','Panel','Window','Rows','Archived','Code','Label','Amount','Apply','Decorate','Show','Start'),
          'accounts':('Account','Editor','Dialog','Entries','Disabled','Number','Note','Cost','Switch','Refresh','Display','Init')}


def portable_checks(project,backend='memory'):
    record,form,target,table,flag,number,field,amount,apply,helper,show,init=PORTABLE[project]
    result=[]
    for ns in ['Alpha','Beta']:
        value={'Ссылка':{'Идентификатор':ID},number:'existing',flag:False,field:'retain',table:[{amount:1.25}]}
        changed=deepcopy(value);changed[flag]=True
        stored=[entry(ns+'::'+record,changed)]
        cfg={'write':True}
        result.append({'id':ns+'-write','type':'runtime','points':1,'target':{'module':form,'namespace':ns,'method':apply},
                       'context':{'Объект':value},'lifecycle':{'isNew':False},'args':[{}],
                       'observe':['Объект','Caption'],'formEffects':cfg,
                       'storage':{'backend':backend,'idType':'Ууид','initial':[{'type':ns+'::'+record,'value':value}]},
                       'expected':{'result':{'result':{'Объект':changed,'Caption':'on'},'lifecycle':False,'openings':[],'storage':stored},'storage':stored},'trace':True})
        dest=('Beta' if ns=='Alpha' else 'Alpha')+'::'+target
        reqs=[{'owner':dest,'properties':{'Lines':[{amount:1.25}],'Optional':None},'context':{'Caption':'opened: 1','Lines':[{amount:999}],'Объект.'+table:[]}},
              {'owner':dest,'properties':{'Lines':[{amount:33}]},'context':{'Caption':'opened: 1','Lines':[{amount:999}],'Объект.'+table:[]}}]
        result.append({'id':ns+'-open','type':'runtime','points':1,'target':{'module':form,'namespace':ns,'method':show},
                       'context':{'Объект':{}},'lifecycle':{'isNew':True},'args':[[{amount:1.25}]],
                       'observe':['Caption'],'snapshotArgs':True,'formEffects':{'open':[dest],
                         'lifecycle':{dest:{'method':init,'isNew':True,'observe':['Caption','Lines','Объект.'+table]}}},
                       'storage':{'backend':backend,'idType':'Ууид'},'trace':True,
                       'expected':{'result':{'result':{'result':{'Caption':'seed'},'args':[[{amount:33}]]},
                          'openings':reqs,'lifecycle':True,'storage':[]},'storage':[]}})
    if backend=='postgres':
        for c in result:
            c['integration']={'backend':'postgres','operation':'metadata-storage'}
            before=[entry(x['type'],x['value']) for x in c['storage'].get('initial',[])]
            c['expected']['sqlSnapshots']=[before]+([c['expected']['storage']] if c['id'].endswith('write') else [])
    return result
