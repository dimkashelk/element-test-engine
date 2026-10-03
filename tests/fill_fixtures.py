"""Authored histories/expected answers fixed before executing student sources."""
import copy
from pathlib import Path
from record_set_fixtures import audit
REPO=Path(__file__).resolve().parent.parent
CORPUS=REPO/'tests/corpus/storage-query-fill'
EVIDENCE=REPO/'result/storage-query-fill'
ARCHIVE=REPO/'Demo-SRM-dev-2026-09-28-21-38.xdump'
IDS=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222','33333333-3333-4333-8333-333333333333']
REFS=[{'Идентификатор':x} for x in IDS]
OWNER='Общие::КурсыВалют::КурсыВалют'

def real_check(method='ПолучитьКурсВалюты',amount=100,ref=0,date='2026-09-30',empty=False,sql=False):
 history=[{'type':OWNER,'filter':{'Валюта':REFS[i]},'rows':[
  {'Валюта':REFS[i],'Период':d,'Курс':r,'Кратность':f} for d,r,f in entries]}
  for i,entries in [(0,[('2026-09-28',12,2),('2026-09-30',2.01,2),('2026-10-02',99,3)]),(1,[('2026-09-29',7,0)])]]
 args=[REFS[ref],date] if method=='ПолучитьКурсВалюты' else [amount,REFS[ref],date]
 c={'target':{'namespace':'Общие::КурсыВалют','module':'КурсыВалют','method':method},'args':copy.deepcopy(args),
    'storage':{'idType':'Ууид','registers':[OWNER],'initialRegisters':[] if empty else copy.deepcopy(history)},'timeout':'15s','trace':True}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c

def real_result(date='2026-09-30',ref=0,rate=2.01,factor=2):
 return {'Валюта':REFS[ref],'Код':'','Период':date,'Курс':rate,'Кратность':factor}

def expected(c,result):
 return {'result':copy.deepcopy(result),'storage':[{**copy.deepcopy(s), 'id':s['value']['Ссылка']['Идентификатор']} for s in c['storage'].get('initial',[])]+[audit(s) for s in c['storage'].get('initialRegisters',[])]}

def portable_check(variant='ordinary',method='Rows',sql=False,empty=False):
 values=[{'Label':'A','Total':3,'Optional':None,'Link':REFS[0],'Flag':True,'Day':'2026-09-30','Key':IDS[0],'Kind':'First'},
         {'Label':'B','Total':1,'Optional':'note','Link':None,'Flag':False,'Day':'2026-09-29','Key':IDS[1],'Kind':'Second'}]
 c={'target':{'namespace':'Entry','module':'Main','method':method},'args':[] if method in ('Construct','Unsupported','Neighbor') else ['2026-09-30'],
    'storage':{'idType':'Ууид'},'timeout':'15s','trace':True}
 neighbor={'type':'Other::Item','value':{'Ссылка':REFS[0],**values[0],'Label':'neighbor'}}
 if variant=='ordinary':c['storage']['initial']=[] if empty else [{'type':'Data::Item','value':{'Ссылка':REFS[i],**v}} for i,v in enumerate(values)]+[neighbor]
 else:
  c['storage']['registers']=['Data::Book'];c['storage']['initial']=[neighbor]
  c['storage']['initialRegisters']=[] if empty else [{'type':'Data::Book','filter':{'Label':v['Label']},'rows':[
   {'Период':'2026-09-28',**v,'Total':100},{'Период':'2026-09-30',**v},{'Период':'2026-10-02',**v,'Total':200}]} for v in values]
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c

def card(name,amount=None,note=None,ref=None,flag=False,day=None,key=None,kind=None):
 return {'Name':name,'Amount':amount,'Note':note,'Ref':ref,'Second':copy.deepcopy(ref),'Default':'authored','Empty':'','Flag':flag,'Day':day,'Key':key,'Kind':kind}

def portable_rows():
 return [card('B',1,'note',flag=False,day='2026-09-29',key=IDS[1],kind='Second'),card('A',3,ref=REFS[0],flag=True,day='2026-09-30',key=IDS[0],kind='First')]

def real_cases(sql=False):
 cases=[]
 for label,kwargs,result in [
  ('found',{},real_result()),('before',{'date':'2026-09-27'},None),('boundary',{'date':'2026-09-28'},real_result('2026-09-28',rate=12)),
  ('between',{'date':'2026-09-29'},real_result('2026-09-28',rate=12)),('after',{'date':'2026-10-03'},real_result('2026-10-02',rate=99,factor=3)),
  ('missing',{'ref':2},None),('empty',{'empty':True},None),('zero-factor-row',{'ref':1},real_result('2026-09-29',ref=1,rate=7,factor=0)),
  ('formula',{'method':'ПересчитатьПоКурсу'},100.5),('round-positive',{'method':'ПересчитатьПоКурсу','amount':1},1.01),
  ('round-negative',{'method':'ПересчитатьПоКурсу','amount':-1},-1.01),('round-fraction',{'method':'ПересчитатьПоКурсу','amount':0.5},0.5),
  ('zero-sum',{'method':'ПересчитатьПоКурсу','amount':0},0),('zero-factor',{'method':'ПересчитатьПоКурсу','ref':1},0),
  ('formula-missing',{'method':'ПересчитатьПоКурсу','ref':2},0),('formula-empty',{'method':'ПересчитатьПоКурсу','empty':True},0)]:
  c=real_check(**kwargs,sql=sql);cases.append((label,c,expected(c,result)))
 return cases

def write_assignments():
 import yaml
 class Dumper(yaml.SafeDumper):
  def ignore_aliases(self,data):return True
 def write(name,cases):
  folder=REPO/'assignments'/name;folder.mkdir(exist_ok=True)
  (folder/'assignment.yaml').write_text(yaml.dump({'name':'Именованное заполнение — '+name,'checks':[
   {'id':label,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':exp} for label,c,exp in cases]},Dumper=Dumper,allow_unicode=True,sort_keys=False))
 for sql in (False,True):write('storage-query-fill-real'+('-sql' if sql else ''),real_cases(sql))
 for variant in ('ordinary','daily'):
  for sql in (False,True):
   cases=[]
   for method,result in [('Rows',portable_rows()),('Construct',[card('new',9)]),('Collision',{'local':portable_rows(),'other':card('neighbor')})]:
    c=portable_check(variant,method,sql);cases.append((method,c,expected(c,result)))
   c=portable_check(variant,'Rows',sql,True);cases.append(('empty',c,expected(c,[])))
   write('storage-query-fill-'+variant+('-sql' if sql else ''),cases)
 c=portable_check();bad=expected(c,[])
 write('storage-query-fill-control',[('wrong-answer',c,bad),('unsupported',portable_check(method='Unsupported'),None),('recovery',c,expected(c,portable_rows()))])

if __name__=='__main__':write_assignments()
