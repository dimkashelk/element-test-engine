"""Task 54 portable teacher inputs and independent expectations."""
import copy
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / 'tests/corpus/tagged-reference-unions'
EVIDENCE = REPO / 'result/tagged-reference-unions'
ID = '22222222-2222-4222-8222-222222222222'
CARD_ID = '11111111-1111-4111-8111-111111111111'
ZERO = '00000000-0000-0000-0000-000000000000'


def names(folder='ordinary'):
    return ('Data','Other','Card','Item','Choice','Rows') if folder == 'ordinary' else ('Учет','Архив','Запись','Узел','Связь','Детали')


def ref(owner, id=ID):
    return {'type': owner + '.Ссылка', 'value': {'Идентификатор': id}}


def values(folder='ordinary'):
    ns, other, card, item, field, table = names(folder)
    a, b = ref(ns+'::'+item), ref(other+'::'+item)
    rows = [{field:copy.deepcopy(v),'Index':i} for i,v in enumerate([a,b,None,a,b])]
    return a, b, rows


def config(folder='ordinary', method='Persist', args=None, *, sql=False, id_type='Строка'):
    ns, other, card, item, field, table = names(folder)
    a,b,rows=values(folder)
    c={'target':{'module':'Main','namespace':ns,'method':method},
       'args':args if args is not None else [a,rows,False],
       'storage':{'lifecycle':True,'idType':id_type,'backend':'postgres' if sql else 'memory',
                  'registers':[ns+'::Flow'],
                  'initial':[{'type':n+'::'+item,'value':{'Ссылка':{'Идентификатор':ID},'Label':label}}
                             for n,label in [(ns,'owner-A'),(other,'owner-B')]]}}
    if sql:c['integration']={'backend':'postgres','operation':'metadata-storage'}
    return c


def transition(folder='ordinary', **kw):
    a,b,rows=values(folder);c=config(folder, **kw)
    c['sequence']=[{'method':'Persist','args':[v,copy.deepcopy(rows),False]} for v in [a,b,None,a]]
    c['sequence'] += [{'method':'Persist','args':[b,[],True]}, {'method':'Read','args':[]},
                     {'method':'Query','args':[]}, {'method':'Load','args':[a]}, {'method':'Load','args':[b]},
                     {'method':'Equal','args':[a,copy.deepcopy(a)]}, {'method':'Equal','args':[a,b]},
                     {'method':'Equal','args':[a,None]}, {'method':'Equal','args':[None,None]}]
    c['sequence'] += [{'method': 'LoadStored', 'args': []}, {'method': 'DetachedReferences', 'args': []}, {'method': 'Fresh', 'args': []}]
    c['captureException']=True;c['snapshotArgs']=True;c['trace']=True
    return c


def card_value(v, previous, rows, folder='ordinary'):
    ns,other,card,item,field,table=names(folder)
    return {'Ссылка':{'Идентификатор':CARD_ID},field:copy.deepcopy(v),'Previous':previous,
            'Detached':True,'Scalar':'text',table:copy.deepcopy(rows)}


def state(v, previous, rows, folder='ordinary'):
    ns,other,card,item,field,table=names(folder)
    a,_,_=values(folder)
    return [{'type':ns+'::'+card,'id':CARD_ID,'value':card_value(v,previous,rows,folder)},
            *[{'type':n+'::'+item,'id':ID,'value':{'Ссылка':{'Идентификатор':ID},'Label':label}}
              for n,label in [(ns,'owner-A'),(other,'owner-B')]],
            {'type':ns+'::Flow','id':json.dumps({'Регистратор': {'Идентификатор': CARD_ID}}, ensure_ascii=False, indent=2, separators=(',', ' : ')),
             'value':[{'Период':'2026-10-11T12:00:00','ВидЗаписи':'Приход','Key':'effect',
                       'Count':1 if v==a else 2,'Активность':True,'Регистратор':{'Идентификатор':CARD_ID}}]}]
