"""Independent authored data and answers, fixed before acceptance execution."""
import copy
from pathlib import Path
from record_set_fixtures import audit
REPO=Path(__file__).resolve().parent.parent
CORPUS=REPO/'tests/corpus/joins-null'
EVIDENCE=REPO/'result/joins-null'
ARCHIVE=REPO/'Demo-SRM-dev-2026-09-28-21-38.xdump'
IDS=[f'{n}'*8+'-'+f'{n}'*4+'-4'+f'{n}'*3+'-8'+f'{n}'*3+'-'+f'{n}'*12 for n in range(1,8)]
REFS=[{'Идентификатор':x} for x in IDS]
OWNER='Общие::КурсыВалют::КурсыВалют'
CURRENCY='Общие::КурсыВалют::Валюты'


def expected(c,result):
 return {'result':copy.deepcopy(result),'storage':[
  {**copy.deepcopy(s),'id':s['value']['Ссылка']['Идентификатор']} for s in c['storage'].get('initial',[])]+[audit(s) for s in c['storage'].get('initialRegisters',[])]}


def portable_check(variant='ordinary',method='Left',sql=False,empty=None):
 ns,left,right,key,amount,main=('Data','Item','Entry','Key','Amount','Main') if variant=='ordinary' else ('Учет','Клиенты','Записи','Номер','Сумма','Пуск')
 data=[(left,0,1,'A',0,None,None),(left,1,1,'B',0,None,None),(left,2,2,'C',0,None,None),
       (right,3,1,'x',3,None,REFS[0]),(right,4,1,'y',5,'note',REFS[1]),(right,5,4,'z',9,None,None)]
 initial=[{'type':ns+'::'+owner,'value':{'Ссылка':REFS[i],key:k,'Label':label,amount:n,'Note':note,'Peer':peer}} for owner,i,k,label,n,note,peer in data if owner!=empty and empty!='both']
 initial+=[{'type':'Other::'+left,'value':{'Ссылка':REFS[0],key:1,'Label':'p'}},{'type':'Other::'+left,'value':{'Ссылка':REFS[6],key:7,'Label':'q'}}]
 c={'target':{'namespace':'Entry','module':main,'method':method},'args':[1] if method=='State' else [],'timeout':'15s','trace':True,'runtimeProfile':'9.3','storage':{'idType':'Ууид','initial':copy.deepcopy(initial)}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c


def card(left,right,amount=None,note=None,ref=None):
 return {'Left':left,'Right':right,'Amount':amount,'Note':note,'Ref':copy.deepcopy(ref),'Second':copy.deepcopy(ref)}

# Explicit bag answers: duplicate keys yield all four distinct pairings.
INNER=[card('A','x',3,ref=REFS[0]),card('A','y',5,'note',REFS[0]),card('B','x',3,ref=REFS[1]),card('B','y',5,'note',REFS[1])]
MISSING=[card('C',None,ref=REFS[2])]
RIGHT_ONLY=[card(None,'z',9)]
LEFT=INNER+MISSING
ANSWERS={'Inner':INNER,'Left':LEFT,'Right':INNER+RIGHT_ONLY,'Full':LEFT+RIGHT_ONLY,
 'Missing':MISSING,'Present':INNER,'UnknownNot':[INNER[1],INNER[3]],'NullComparison':[],
 'Or':[INNER[0],INNER[2],MISSING[0]],'FalseAnd':[],'OptionalMissing':[INNER[0],INNER[2]],
 'Coalesce':[{'Left':'A','Amount':3,'Note':None,'Absent':False},{'Left':'A','Amount':5,'Note':'note','Absent':False},{'Left':'B','Amount':3,'Note':None,'Absent':False},{'Left':'B','Amount':5,'Note':'note','Absent':False},{'Left':'C','Amount':0,'Note':'missing','Absent':True}],
 'Default':[{'Left':'A','Amount':3},{'Left':'A','Amount':5},{'Left':'B','Amount':3},{'Left':'B','Amount':5},{'Left':'C','Amount':0}],
 'Self':[{'Left':'A','Right':'A'},{'Left':'A','Right':'B'},{'Left':'B','Right':'A'},{'Left':'B','Right':'B'},{'Left':'C','Right':'C'}],
 'RefJoin':[{'Left':'A','Right':'x'},{'Left':'B','Right':'y'}],
 'Nested':[{'Left':'A','Right':'x','Other':'p'},{'Left':'A','Right':'y','Other':'p'},{'Left':'B','Right':'x','Other':'p'},{'Left':'B','Right':'y','Other':'p'},{'Left':'C','Right':None,'Other':None},{'Left':None,'Right':None,'Other':'q'}]}


def state_answer():
 again=copy.deepcopy(INNER);again[0]['Amount']=17;again[2]['Amount']=17
 # A write creates default Undefined Note/Peer, equivalent to the authored x.
 seen=copy.deepcopy(again);seen[0]['Amount']=700
 return {'old':INNER,'seen':seen,'again':again,'after':INNER,'calls':['key']}


def real_check(empty=False,no_rates=False,only_base=False,sql=False):
 initial=[{'type':CURRENCY,'value':{'Ссылка':REFS[i],'Код':code,'Наименование':code,'ВнешнийИдентификатор':code}} for i,code in enumerate(['USD','EUR','JPY','RUB']) if not only_base or i==3]
 history=[{'type':OWNER,'filter':{'Валюта':REFS[i]},'rows':[{'Валюта':REFS[i],'Период':d,'Курс':r,'Кратность':f} for d,r,f in rows]} for i,rows in [
  (0,[('2025-01-01',10,1),('2025-02-01',12.5,2),('2200-01-01',999,1)]),
  (1,[('2025-01-02',0,0)]),(3,[('2025-01-03',1,1)]),(4,[('2025-01-04',77,3)]),
  (5,[('2200-01-01',88,4)])]]
 c={'target':{'namespace':'Общие::КурсыВалют','module':'КурсыВалют','method':'ПолучитьКурсыВалют'},'args':[], 'timeout':'15s','trace':True,'runtimeProfile':'9.3',
    'storage':{'idType':'Ууид','registers':[OWNER],'initial':[] if empty else initial,'initialRegisters':[] if no_rates else history}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c

REAL=[{'Валюта':REFS[0],'Код':'USD','Период':'2025-02-01','Курс':12.5,'Кратность':2},
      {'Валюта':REFS[1],'Код':'EUR','Период':'2025-01-02','Курс':0,'Кратность':0},
      {'Валюта':REFS[2],'Код':'JPY','Период':None,'Курс':0,'Кратность':0}]


def real_cases(sql=False):
 cases=[]
 for label,kwargs,result in [('multiple',{},REAL),('empty-catalog',{'empty':True},[]),('base-only',{'only_base':True},[]),('no-rates',{'no_rates':True},[{**r,'Период':None,'Курс':0,'Кратность':0} for r in REAL])]:
  c=real_check(sql=sql,**kwargs);cases.append((label,c,expected(c,result)))
 return cases



def cases_check(empty=False,sql=False):
 initial=[{'type':'Проверка::Кейсы','value':{'Ссылка':REFS[i],'Код':code,'Наименование':name,'МаксимальныйБалл':10}} for i,code,name in [(0,20,'second'),(1,10,'first'),(2,30,'draft'),(3,40,'missing')]]
 initial += [{'type':'Проверка::ДанныеПроверок','value':{'Ссылка':REFS[i],'Наименование':'definition','Статус':status,'Кейс':ref,'СкрытьИтоговуюОценку':False,'Критерии':[],'Переменные':[]}} for i,status,ref in [(0,'Действующий',REFS[0]),(1,'Действующий',REFS[1]),(2,'Действующий',REFS[0]),(3,'Черновик',REFS[2]),(4,'Действующий',None)]]
 c={'target':{'namespace':'Проверка','module':'Кейсы','method':'ПолучитьДоступныеКейсы'},'comparison':'record-sets-unordered','args':[],'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','initial':[] if empty else initial}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c
CASES=[REFS[1],REFS[0],REFS[0]]

def write_assignments():
 import yaml
 class Dumper(yaml.SafeDumper):
  def ignore_aliases(self,data):return True
 def write(name,cases):
  folder=REPO/'assignments'/name;folder.mkdir(exist_ok=True)
  (folder/'assignment.yaml').write_text(yaml.dump({'name':'Соединения и NULL — '+name,'checks':[{'id':label,'type':'runtime','points':1,'comparison':'query-rows-unordered' if isinstance(exp,dict) and isinstance(exp.get('result'),list) else 'record-sets-unordered',**c,'expected':exp} for label,c,exp in cases]},Dumper=Dumper,allow_unicode=True,sort_keys=False))
 for sql in (False,True):
  write('joins-null-real'+('-sql' if sql else ''),real_cases(sql))
  write('joins-null-cases'+('-sql' if sql else ''),[(label,cases_check(empty,sql),expected(cases_check(empty,sql),result)) for label,empty,result in [('active',False,CASES),('empty',True,[])]])
 for variant in ('ordinary','renamed'):
  for sql in (False,True):
   methods=('Inner','Left','Right','Full','Coalesce','Missing') if not sql else ('Full','Coalesce')
   write('joins-null-'+variant+('-sql' if sql else ''),[(m,portable_check(variant,m,sql),expected(portable_check(variant,m,sql),ANSWERS[m])) for m in methods])
 c=portable_check();write('joins-null-control',[('wrong',c,expected(c,[])),('unsupported',portable_check(method='Unsupported'),None),('recovery',c,expected(c,LEFT))])
if __name__=='__main__':write_assignments()
