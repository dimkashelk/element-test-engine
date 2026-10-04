"""Authored fixtures and expected answers, fixed before running student sources."""
import copy
from pathlib import Path
REPO=Path(__file__).resolve().parents[1]
CORPUS=REPO/'tests/corpus/projections-aggregates'
EVIDENCE=REPO/'result/projections-aggregates'
IDS=[f'{i}'*8+'-'+f'{i}'*4+'-4'+f'{i}'*3+'-8'+f'{i}'*3+'-'+f'{i}'*12 for i in range(1,8)]
REFS=[{'Идентификатор':i} for i in IDS]

def portable_check(variant='ordinary',method='Aggregate',sql=False,empty=False):
 ns,item,main,key,amount=('Data','Item','Main','Key','Amount') if variant=='ordinary' else ('Учет','Строки','Пуск','Номер','Значение')
 records=[(0,1,1,'a',True),(1,1,3,'b',False),(2,2,3,'c',True),(3,3,0,'d',False)]
 initial=[{'type':ns+'::'+item,'value':{'Ссылка':REFS[i],key:k,amount:n,'Label':label,'Flag':flag,'Day':'2024-02-28','Peer':REFS[0] if i<2 else None,'Kind':'One' if i<3 else 'Two'}} for i,k,n,label,flag in records]
 initial += [{'type':'Other::'+item,'value':{'Ссылка':REFS[4],key:1,amount:2,'Label':'x','Flag':True,'Day':'2024-02-28','Peer':None,'Kind':'One'}}]
 c={'target':{'namespace':'Entry','module':main,'method':method},'args':[1,1,4] if method=='Params' else [],'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','initial':[] if empty else initial}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c

def expected(c,result):
 return {'result':copy.deepcopy(result),'storage':[{**copy.deepcopy(s),'id':s['value']['Ссылка']['Идентификатор']} for s in c['storage'].get('initial',[])]}

ANSWERS={
 'Computed':[{'Text':'a!','Double':2.3,'Choice':1,'Prefix':'a','Tomorrow':'2024-02-29'},{'Text':'b!','Double':6.3,'Choice':0,'Prefix':'b','Tomorrow':'2024-02-29'},{'Text':'c!','Double':6.3,'Choice':3,'Prefix':'c','Tomorrow':'2024-02-29'},{'Text':'d!','Double':0.3,'Choice':0,'Prefix':'d','Tomorrow':'2024-02-29'}],
 'Aggregate':[{'Count':4,'Sum':7,'Min':0,'Max':3,'Avg':1.75}],
 'Grouped':[{'Key':1,'Count':2,'Sum':4},{'Key':2,'Count':1,'Sum':3},{'Key':3,'Count':1,'Sum':0}],
 'Having':[{'Key':1,'Sum':4}],
 'Distinct':[{'Key':1},{'Key':2},{'Key':3}],
 'DistinctAggregate':[{'Count':3,'Sum':4,'Avg':4/3}],
 'JoinGroup':[{'Key':1,'Rows':2,'Present':2,'Sum':4,'Safe':4},{'Key':2,'Rows':1,'Present':0,'Sum':None,'Safe':0},{'Key':3,'Rows':1,'Present':0,'Sum':None,'Safe':0}],
 'NullGroup':[{'Name':'x','Count':2},{'Name':None,'Count':2}],
 'NullDistinct':[{'Name':'x'},{'Name':None}],
 'Case':[{'Key':1,'Name':'one','Quotient':10},{'Key':1,'Name':'one','Quotient':10/3},{'Key':2,'Name':'two','Quotient':10/3},{'Key':3,'Name':None,'Quotient':0}],
 'Cast':[{'Rounded':1,'Text':'a'},{'Rounded':3,'Text':'b'},{'Rounded':3,'Text':'c'},{'Rounded':0,'Text':'d'}],
 'Typed':[{'Peer':REFS[0],'Kind':'One','Count':2},{'Peer':None,'Kind':'One','Count':1},{'Peer':None,'Kind':'Two','Count':1}],
 'Fill':[{'Key':1,'Sum':4},{'Key':2,'Sum':3},{'Key':3,'Sum':0}],
 'Params':[{'Key':1,'Sum':6}],
 'GroupedExpression':[{'K':2,'Count':2},{'K':4,'Count':1},{'K':6,'Count':1}],
 'NullUndefined':[{'Value':None,'Count':2},{'Value':None,'Count':2}],
 'CountUndefined':[{'DefinedRows':4,'NullRows':0}],
 'Precision':[{'Positive':1.01,'Negative':-1.01,'Exact':0.3,'Remainder':1}],
 'DistinctLimit':[{'Value':3},{'Value':1}],
 'State':{'old':[{'Sum':11}],'seen':[{'Sum':30}],'after':[{'Sum':11}],'calls':['capture']},
}
EMPTY=[{'Count':0,'Sum':None,'Min':None,'Max':None,'Avg':None}]

def answer(variant,method):
 result=copy.deepcopy(ANSWERS[method])
 if variant=='renamed' and isinstance(result,list):
  for row in result:
   if 'Key' in row:row['Номер']=row.pop('Key')
 return result


def real_task_check(empty=False,sql=False):
 owner='Мероприятия::Задачи'
 initial=[{'type':owner,'value':{'Ссылка':REFS[i],'Наименование':'task','Ответственный':ref,'Статус':status,'Дата':'2025-01-01T12:00:00','Номер':'T'+str(i),'Владелец':None,'Описание':'authored','ДатаНачала':'2025-01-01T12:00:00Z','ДатаЗавершения':'2025-01-02T12:00:00Z','Приоритет':'Обычный'}} for i,ref,status in [(0,REFS[0],'Запланировано'),(1,REFS[0],'Запланировано'),(2,REFS[0],'ВПроцессе'),(3,REFS[0],'Завершено'),(4,REFS[1],'ВПроцессе'),(5,None,'Запланировано')]]
 c={'target':{'namespace':'Мероприятия','module':'Задачи','method':'КоличествоОткрытыхЗадачСотрудника'},'args':[REFS[0]],'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','initial':[] if empty else initial}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c
REAL_TASK={'Запланировано':2,'ВПроцессе':1}

def real_max_check(sql=False):
 c={'target':{'namespace':'Администрирование','module':'ОписаниеРаботыФормаОбъекта','method':'ПолучитьДоступныйНомер'},'args':[],'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','initial':[{'type':'Администрирование::ОписаниеРаботы','value':{'Ссылка':REFS[i],'Наименование':str(n),'ОписаниеHTML':'','ПорядковыйНомер':n}} for i,n in enumerate([4,17,9,17])]}}
 if sql:c['storage']['backend']='postgres';c['integration']={'backend':'postgres','operation':'metadata-storage'}
 return c

def write_assignments():
 import yaml
 class Dumper(yaml.SafeDumper):
  def ignore_aliases(self,data):return True
 def write(name,checks):
  p=REPO/'assignments'/name;p.mkdir(exist_ok=True)
  (p/'assignment.yaml').write_text(yaml.dump({'name':'Вычисления и агрегаты — '+name,'checks':checks},Dumper=Dumper,allow_unicode=True,sort_keys=False))
 def check(label,c,result):return {'id':label,'type':'runtime','points':1,'comparison':'query-rows-unordered' if isinstance(result,list) else 'record-sets-unordered',**c,'expected':expected(c,result)}
 for variant in ('ordinary','renamed'):
  for sql in (False,True):
   methods=ANSWERS if not sql else ('JoinGroup','State')
   write('projections-'+variant+('-sql' if sql else ''),[check(m,portable_check(variant,m,sql),answer(variant,m)) for m in methods])
 for sql in (False,True):
  write('projections-real-tasks'+('-sql' if sql else ''),[check('active',real_task_check(sql=sql),REAL_TASK),check('empty',real_task_check(empty=True,sql=sql),{'Запланировано':0,'ВПроцессе':0})])
  write('projections-real-max'+('-sql' if sql else ''),[check('maximum',real_max_check(sql),17)])
 write('projections-control',[check('wrong',portable_check(),EMPTY),{**check('unsupported',portable_check(method='Unsupported'),[]),'expected':None},check('recovery',portable_check(),ANSWERS['Aggregate'])])
if __name__=='__main__':write_assignments()

DOCUMENTED={
 'ComputedProjection':[{'Double':4},{'Double':-2},{'Double':6}],
 'Group':[{'Label':'a','СУММА':1},{'Label':'b','СУММА':3}],
 'Having':[{'Label':'a','СУММА':1},{'Label':'b','СУММА':3}],
 'Case':[{'Value':2},{'Value':0},{'Value':3}],
 'CombinedJoinGroupNull':[{'Label':'a','СУММА':8},{'Label':'b','СУММА':0}],
}

def documented_check(method):
 return {'target':{'namespace':'Entry','module':'Main','method':method},'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','initial':[
 {'type':'Data::Item','value':{'Ссылка':REFS[0],'Key':1,'Label':'a','Total':2,'Flag':True}},
 {'type':'Data::Item','value':{'Ссылка':REFS[1],'Key':1,'Label':'a','Total':-1,'Flag':False}},
 {'type':'Data::Item','value':{'Ссылка':REFS[2],'Key':2,'Label':'b','Total':3,'Flag':True}},
 {'type':'Other::Item','value':{'Ссылка':REFS[3],'Key':1,'Label':'x','Total':4,'Flag':True}}]}}
