"""Independent teacher data shared by acceptance tests and public assignments."""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO/'tests/corpus/record-set-reading'
ARCHIVE = REPO/'Prakticheskie-primery-2026-09-30-15-20.xdump'
IDS = ['12345678-1234-4234-8234-123456789abc', 'abcdef12-1234-4234-8234-123456789abc', '33333333-1234-4234-8234-123456789abc']
REFS = [{'Идентификатор': x} for x in IDS]
DATES = ['2026-09-29', '2026-09-30']


def audit(seed):
    # Native serializer format verified by the trusted probe; comparison of
    # filter objects is also used in tests rather than relying on formatting.
    return {'type':seed['type'], 'id':json.dumps(seed['filter'],ensure_ascii=False,indent=2,separators=(',', ' : ')), 'value':seed['rows']}


def info_seed(key='base', region='EU', rows=None):
    return {'type':'Data::Prices', 'filter':{'Key':key,'Region':region}, 'rows':rows if rows is not None else [
        {'Период':DATES[0],'Key':key,'Region':region,'Price':2.5,'Factor':10,'Link':REFS[0]},
        {'Период':DATES[1],'Key':key,'Region':region,'Price':4,'Factor':1,'Link':REFS[1]}]}


def info_check(method='Copy', args=None, sql=False):
    seeds=[info_seed(),info_seed('target',rows=[{'Период':'2025-01-01','Key':'target','Region':'EU','Price':99,'Factor':7,'Link':None}]),info_seed('neighbor','US')]
    c={'target':{'module':'Flow','namespace':'Entry','method':method},'args':['base','target',3] if args is None else args,
       'storage':{'idType':'Ууид','registers':['Data::Prices'],'initialRegisters':seeds},'timeout':'15s','trace':True}
    if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c


def accumulation_seed(ref=REFS[0], price=2.5):
    return {'type':'Учет::Движения','filter':{'Регистратор':ref}, 'rows':[
        {'Регистратор':ref,'Активность':True,'Период':'2026-09-29T12:30:15','ВидЗаписи':'Расход','Товар':'A','Количество':price,'Стоимость':10},
        {'Регистратор':ref,'Активность':True,'Период':'2026-09-30T15:00:00','ВидЗаписи':'Приход','Товар':'B','Количество':4,'Стоимость':7}]}


def accumulation_check(method='Перенести', args=None, sql=False):
    c={'target':{'module':'Поток','namespace':'Вход','method':method},'args':[REFS[0],REFS[1],3] if args is None else args,
       'storage':{'idType':'Ууид','registers':['Учет::Движения'], 'initialRegisters':[accumulation_seed(),accumulation_seed(REFS[1],99),accumulation_seed(REFS[2],7)]},'timeout':'15s','trace':True}
    if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c


def real_check(empty=False, missing=False, sql=False, nullable=False):
    owner='ПараметрыЗаписи::КурсыВалют'
    def seed(ref, rows):return {'type':owner,'filter':{'Валюта':ref},'rows':rows}
    base=None if nullable else REFS[0]
    rows=[{'Период':DATES[i],'Валюта':base,'Курс':n,'Кратность':k} for i,(n,k) in enumerate([(2.5,10),(4,1)])]
    seeds=[seed(base,[] if empty else rows),seed(REFS[1],[{'Период':'2025-01-01','Валюта':REFS[1],'Курс':99,'Кратность':7}]),seed(REFS[2],[{'Период':DATES[1],'Валюта':REFS[2],'Курс':8,'Кратность':100}])]
    initial=[] if missing else [{'type':'ПараметрыЗаписи::Валюты','value':{'Ссылка':REFS[1],'Код':'TGT','Наименование':'Целевая','ОсновнаяВалюта':base,'Наценка':3}}]
    c={'target':{'module':'КурсыВалют','namespace':'ПараметрыЗаписи','method':'Пересчитать'}, 'args':[REFS[1]],
       'storage':{'idType':'Ууид','registers':[owner],'initial':initial,'initialRegisters':seeds},'timeout':'15s','trace':True}
    if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c


def expected_copy(c, info=False, accumulation=False, missing=False):
    import copy
    seeds=copy.deepcopy(c['storage']['initialRegisters'])
    if not missing:
        seeds[1]['rows']=copy.deepcopy(seeds[0]['rows'])
        for r in seeds[1]['rows']:
            if info:r['Key']='target';r['Price']*=3
            elif accumulation:r['Количество']*=3;r['Регистратор']=REFS[1]
            else:r['Валюта']=REFS[1];r['Курс']*=3
    result=[audit(s) for s in seeds]
    for obj in c['storage'].get('initial',[]):
        result.append({'type':obj['type'],'id':obj['value']['Ссылка']['Идентификатор'],'value':obj['value']})
    return {'result':None,'storage':result}


def write_assignments():
    """Teacher-controlled expected values; does not read business implementation."""
    import copy
    import yaml
    class NoAliases(yaml.SafeDumper):
        def ignore_aliases(self, data):return True
    dump=lambda data:yaml.dump(data,Dumper=NoAliases,allow_unicode=True,sort_keys=False)
    groups={'information':[(info_check(),{'info':True},'copy')],
            'accumulation':[(accumulation_check(),{'accumulation':True},'copy')],
            'real':[(real_check(),{},'copy'),(real_check(empty=True),{},'empty'),
                    (real_check(missing=True),{'missing':True},'missing'),(real_check(nullable=True),{},'nullable')]}
    for group, cases in groups.items():
        for sql in (False,True):
            checks=[]
            for c,flags,name in cases:
                c=copy.deepcopy(c)
                expected=expected_copy(c,**flags)
                if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
                checks.append({'id':name,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':expected})
            folder=REPO/'assignments'/('record-sets-'+group+('-sql' if sql else ''));folder.mkdir(exist_ok=True)
            (folder/'assignment.yaml').write_text(dump({'name':'Чтение наборов — '+group,'checks':checks}))
    control=[]
    c=info_check();expected=expected_copy(c,info=True);wrong=copy.deepcopy(expected);wrong['storage'][1]['value'][0]['Price']=123
    for name,c,expect in [('wrong-answer',c,wrong),('partial-filter',info_check('Partial',[]),None),('recovery',info_check(),expected)]:
        control.append({'id':name,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':expect})
    folder=REPO/'assignments/record-sets-control';folder.mkdir(exist_ok=True)
    (folder/'assignment.yaml').write_text(dump({'name':'Контроль независимых исходов №32','checks':control}))


if __name__=='__main__':write_assignments()
