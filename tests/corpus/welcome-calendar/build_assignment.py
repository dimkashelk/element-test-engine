"""Teacher-authored scenarios. Never reads student execution or source."""
from pathlib import Path
import copy
import yaml

class FixtureDumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True

REPO = Path(__file__).resolve().parents[3]
NS = 'Администрирование'
DAYS = ['Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье']
WEEK = dict(zip(DAYS, [True]*5+[False]*2))
ALL = dict.fromkeys(DAYS, True)
NONE = dict.fromkeys(DAYS, False)
ODD = dict(zip(DAYS, [False,True,False,False,False,True,True]))
COMPANY = {'ПолноеНаименование':'ООО «Двигатель»\nВторая строка', 'СокращенноеНаименование':'ДВ "тест"',
           'Телефон':'+7 123 45 67', 'ЮрАдрес':'Москва, улица №1'}
EMPTY = dict.fromkeys(COMPANY, '')
OWNER_C = NS+'::ДанныеКомпании'
OWNER_W = NS+'::РабочиеДни'
FIXED = {'mode':'fixed','date':'2026-10-02','time':'06:00:00','timezone':'UTC'}
GREETS = ['Доброй ночи','Доброе утро','Добрый день','Добрый вечер']


def org(c):
    # Specification of exact punctuation/field order; fixture is independent of student code.
    return 'Вы работаете в '+c['ПолноеНаименование']+'; сокращённо: '+c['СокращенноеНаименование']+'; телефон: '+c['Телефон']+'; юридический адрес: '+c['ЮрАдрес']


def state(start='2020-01-02', end='2027-12-31', greeting='Исходное', result='Сохранить результат'):
    return {'Приветствие':greeting,'Компоненты':{
        'НадписьТекущееВремя':{'Значение':'Время до'}, 'НадписьМестоРаботы':{'Значение':'Компания до'},
        'НадписьТипДня':{'Значение':'День до'}, 'ДатаНачала':{'Значение':start},
        'ДатаКонца':{'Значение':end}, 'НадписьРезультат':{'Значение':result}}}


def counts(c,w,n):
    return {'Календарных':c,'Рабочих':w,'Нерабочих':n}


def build():
    checks=[]
    def add(id, module, method, args, expected, **extra):
        c={'id':'welcome-'+id,'type':'runtime','points':1,'target':{'module':module,'method':method,'namespace':NS},
           'args':args,'expected':expected,'trace':True,'executorLocale':'ru-RU', **extra}
        checks.append(c)
        return c
    for id,c in [('company-full',COMPANY),('company-empty',EMPTY),('company-default',{}),
                 ('company-partial',{'Телефон':'😀 ${literal}'})]:
        values={**EMPTY,**c}
        add(id,'ДанныеКомпании','ПолучитьДанные',[],{'result':values,'constants':{OWNER_C:values}},
            constants={OWNER_C:c},snapshotConstants=True)
    for id,w in [('days-default',NONE),('days-all',ALL),('days-week',WEEK),('days-odd',ODD)]:
        add(id,'РабочиеДни','ПолучитьДанные',[],{'result':w,'constants':{OWNER_W:w}},
            constants={OWNER_W:{} if id=='days-default' else w},snapshotConstants=True)
    for id,c in [('org-full',COMPANY),('org-empty',EMPTY)]:
        add(id,'ФормаПриветствие','СформироватьТекстОрганизации',[c],{'result':org(c),'args':[c]},snapshotArgs=True)
    for i,t in enumerate(['00:00:00','01:02:03','12:34:56','23:59:59']):
        prefix=['Сейчас','','😀 "время"','Последний'][i]
        add('time-'+str(i),'ФормаПриветствие','ПолучитьНадписьТекущееВремя',[prefix,t],
            {'result':prefix+': '+t,'args':[prefix,t[:-3] if t.endswith(':00') else t]},snapshotArgs=True)
    for d in DAYS:
        for working in (False,True):
            settings={d:working}
            add('day-'+d+'-'+str(working),'ФормаПриветствие','ПолучитьТекстДня',[d,settings],
                {'result':'Сегодня: '+d+(' — рабочий день' if working else ' — выходной день'),'args':[d,settings]},snapshotArgs=True)
    add('day-missing','ФормаПриветствие','ПолучитьТекстДня',['Пятница',{}],
        {'result':{'result':None,'exception':{'type':'Std::IllegalArgumentException','message':'Соответствие не содержит значения по ключу "Пятница"'}},'args':['Пятница',{}]},
        snapshotArgs=True,captureException=True,captureExceptionTypes=['ИсключениеНедопустимыйАргумент'])
    boundaries=[('00:00:00',0),('05:59:59',0),('06:00:00',1),('11:59:59',1),('12:00:00',2),('17:59:59',2),('18:00:00',3),('23:59:59',3)]
    for t,g in boundaries:
        for working in (False,True):
            context=state()
            result=copy.deepcopy(context)
            result['Приветствие']=GREETS[g]
            result['Компоненты']['НадписьТекущееВремя']['Значение']='Сейчас: '+t
            result['Компоненты']['НадписьМестоРаботы']['Значение']=org(COMPANY if working else EMPTY)
            result['Компоненты']['НадписьТипДня']['Значение']='Сегодня: Пятница'+(' — рабочий день' if working else ' — выходной день')
            result['Компоненты']['ДатаНачала']['Значение']='2026-10-02'
            constants={OWNER_C:COMPANY if working else EMPTY,OWNER_W:ALL if working else NONE}
            add('create-'+t.replace(':','')+'-'+str(working),'ФормаПриветствие','ПослеСоздания',[],
                {'result':result,'constants':constants},context=context,constants=constants,snapshotConstants=True,clock={**FIXED,'time':t})
    context=state();result=copy.deepcopy(context)
    result['Приветствие']='__clock.greeting__'
    result['Компоненты']['НадписьТекущееВремя']['Значение']='__clock.timeText__'
    result['Компоненты']['НадписьМестоРаботы']['Значение']=org(COMPANY)
    result['Компоненты']['НадписьТипДня']['Значение']='__clock.dayText__'
    result['Компоненты']['ДатаНачала']['Значение']='__clock.date__'
    constants={OWNER_C:COMPANY,OWNER_W:WEEK}
    add('create-docker-clock','ФормаПриветствие','ПослеСоздания',[],{'result':result,'constants':constants},
        context=context,constants=constants,snapshotConstants=True,clock={'mode':'docker','timezone':'Europe/Moscow'},
        clockOracle={'greetings':GREETS,'workWeek':WEEK,'timePrefix':'Сейчас'})
    ranges=[('friday','2026-10-02','2026-10-02',WEEK,counts(1,1,0)),
            ('sunday','2026-10-04','2026-10-04',WEEK,counts(1,0,1)),
            ('weekend','2026-10-02','2026-10-05',WEEK,counts(4,2,2)),
            ('month','2026-09-30','2026-10-02',WEEK,counts(3,3,0)),
            ('year','2026-12-31','2027-01-01',WEEK,counts(2,2,0)),
            ('leap','2028-02-28','2028-03-01',WEEK,counts(3,3,0)),
            ('all','2026-10-02','2026-10-05',ALL,counts(4,4,0)),
            ('none','2026-10-02','2026-10-05',NONE,counts(4,0,4)),
            ('odd','2026-10-02','2026-10-05',ODD,counts(4,2,2)),
            ('week','2026-10-05','2026-10-11',WEEK,counts(7,5,2)),
            ('reverse','2026-10-05','2026-10-02',WEEK,counts(0,0,0))]
    for id,start,end,week,expected in ranges:
        add('calculate-'+id,'ФормаПриветствие','РассчитатьДни',[start,end,week],
            {'result':expected,'args':[start,end,week]},snapshotArgs=True)
        context=state(start,end);result=copy.deepcopy(context)
        result['Компоненты']['НадписьРезультат']['Значение']=('Дата начала больше даты окончания периода' if id=='reverse' else
            'Календарных: '+str(expected['Календарных'])+'; рабочих: '+str(expected['Рабочих'])+'; нерабочих: '+str(expected['Нерабочих']))
        constants={OWNER_W:week}
        add('button-'+id,'ФормаПриветствие','КнопкаРассчитатьПриНажатии',[{},{}],
            {'result':result,'constants':constants},context=context,constants=constants,snapshotConstants=True)
    add('calculate-sequence','ФормаПриветствие','РассчитатьДни',['2026-10-02','2026-10-02',WEEK],
        {'actions':[{'result':counts(1,1,0),'args':['2026-10-02','2026-10-02',WEEK]},
                    {'result':counts(4,2,2),'args':['2026-10-02','2026-10-05',WEEK]}]},snapshotArgs=True,
        sequence=[{'method':'РассчитатьДни','args':['2026-10-02','2026-10-02',WEEK]},
                  {'method':'РассчитатьДни','args':['2026-10-02','2026-10-05',WEEK]}])
    # After reversed input, creation updates only the start date; then the same instance calculates a valid range.
    context=state('2027-01-01','2026-10-05');bad=copy.deepcopy(context)
    bad['Компоненты']['НадписьРезультат']['Значение']='Дата начала больше даты окончания периода'
    created=copy.deepcopy(bad);created['Приветствие']='Доброе утро'
    for key,value in [('НадписьТекущееВремя','Сейчас: 06:00:00'),('НадписьМестоРаботы',org(COMPANY)),('НадписьТипДня','Сегодня: Пятница — рабочий день'),('ДатаНачала','2026-10-02')]:
        created['Компоненты'][key]['Значение']=value
    final=copy.deepcopy(created);final['Компоненты']['НадписьРезультат']['Значение']='Календарных: 4; рабочих: 2; нерабочих: 2'
    constants={OWNER_C:COMPANY,OWNER_W:WEEK}
    add('button-sequence','ФормаПриветствие','КнопкаРассчитатьПриНажатии',[{},{}],
        {'actions':[{'result':s,'constants':constants} for s in [bad,created,final]]},context=context,constants=constants,
        snapshotConstants=True,clock=FIXED,sequence=[{'method':'КнопкаРассчитатьПриНажатии','args':[{},{}]},
         {'method':'ПослеСоздания','args':[]},{'method':'КнопкаРассчитатьПриНажатии','args':[{},{}]}])
    return {'name':'Dvizhok — constants and welcome calendar (stage 35)','checks':checks}


if __name__=='__main__':
    out=REPO/'assignments/dvizhok-welcome-calendar';out.mkdir(exist_ok=True)
    data=build();(out/'assignment.yaml').write_text(yaml.dump(data,Dumper=FixtureDumper,allow_unicode=True,sort_keys=False),encoding='utf-8')
    print(len(data['checks']), 'criteria')
