"""Portable stage 35 declarations, aliases, collisions, and explicit oracle data."""
from pathlib import Path
import importlib.util
import yaml
from element_test.runtime import REPO

spec=importlib.util.spec_from_file_location('welcome_assignment',REPO/'tests/corpus/welcome-calendar/build_assignment.py')
author=importlib.util.module_from_spec(spec);spec.loader.exec_module(author)
CORPUS=REPO/'tests/corpus/welcome-calendar'
EVIDENCE=REPO/'result/dvizhok-welcome-calendar'
ARCHIVE=REPO/'Dvizhok.xdump'
ASSIGNMENT=REPO/'assignments/dvizhok-welcome-calendar'


def portable(root, renamed=False):
    names=('Options','CalendarView','Title','Summary','DateField','Read','Start','Apply','Decorate','Text','Enabled') if not renamed else (
        'Preferences','WelcomePanel','Caption','Banner','DayInput','Fetch','Initialize','Switch','Format','Address','Active')
    record,form,title,label,field,read,start,apply,helper,text,flag=names
    def write(path,data):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(yaml.dump(data,Dumper=author.FixtureDumper,allow_unicode=True,sort_keys=False))
    write(root/'Проект.yaml',{'ВидЭлемента':'Проект','Имя':'PortableWelcomeB' if renamed else 'PortableWelcomeA','Поставщик':'Tests','Версия':'1.0','РежимСовместимости':'9.0'})
    for ns in ['Alpha','Beta']:
        write(root/ns/'Подсистема.yaml',{'ВидЭлемента':'Подсистема','Имя':ns,'ОбластьВидимости':'ВПроекте'})
        write(root/ns/(record+'.yaml'),{'ВидЭлемента':'НаборКонстант','Имя':record,'ОбластьВидимости':'ВПроекте','Ид':'00000000-0000-0000-0000-000000000035',
              'Константы':[{'Имя':text,'Тип':'Строка','ЗначениеПоУмолчанию':'default-'+ns},{'Имя':flag,'Тип':'Булево'}]})
        (root/ns/(record+'.xbsl')).write_text(f'''@ДоступноСКлиента
@НаСервере
@ВПроекте
метод {read}(): Соответствие<Строка, Строка>
    знч Д = {record}.Получить()
    знч Повтор = {record}.Получить()
    знч Р = <Строка, Строка>{{"value": Д.{text}, "again": Повтор.{text}}}
    Р.Вставить("temporary", "discard")
    Р.Удалить("temporary")
    возврат Р
;
''')
        write(root/ns/(form+'.yaml'),{'ВидЭлемента':'КомпонентИнтерфейса','Имя':form,'ОбластьВидимости':'ВПроекте',
          'Свойства':[{'Имя':title,'Тип':'Строка','ЗначениеПоУмолчанию':'seed'}],
          'Наследует':{'Тип':'Форма','Содержимое':{'Тип':'Группа','Имя':'Outer','Содержимое':[
            {'Тип':'Группа','Имя':'Inner','Содержимое':[{'Тип':'Надпись','Имя':label},{'Тип':'ПолеВвода<Дата>','Имя':field}]},
            {'Тип':'СворачиваемыйКомпонент','Имя':record},{'Тип':'Кнопка','Имя':'Action','ПриНажатии':apply}]}}})
        (root/ns/(form+'.xbsl')).write_text(f'''импорт {('Beta' if ns=='Alpha' else 'Alpha')}::{record} как Other
метод {start}()
    знч Д = {record}.{read}()
    знч Другие = Other.{read}()
    Компоненты.{field}.Значение = Дата.Сейчас()
    {title} = Д.Получить("value") + "/" + Другие.Получить("again")
    этот.{helper}()
;
@Локально
метод {helper}()
    Компоненты.{label}.Значение = "${{{title}}}"
;
метод {apply}(Source: Кнопка, Event: СобытиеПриНажатии)
    знч {title} = "local"
    Компоненты.{label}.Значение = {title}
;
метод Shadow({title}: Строка, {record}: Соответствие<Строка, Строка>): Строка
    возврат {title} + ":" + {record}.Получить("key")
;
''')
    return names


def portable_checks(names):
    record,form,title,label,field,read,start,apply,helper,text,flag=names
    constants={'Alpha::'+record:{text:'A 😀',flag:True},'Beta::'+record:{text:'B "test"',flag:False}}
    checks=[]
    for ns in ['Alpha','Beta']:
        value='A 😀/B "test"' if ns=='Alpha' else 'B "test"/A 😀'
        observe=[title,'Компоненты.'+label+'.Значение','Компоненты.'+field+'.Значение']
        actions=[{title:value,observe[1]:value,observe[2]:'2028-02-29'},
                 {title:value,observe[1]:'local',observe[2]:'2028-02-29'}]
        checks.append({'id':'portable-'+ns,'type':'runtime','points':1,'target':{'module':form,'namespace':ns,'method':start},
             'args':[],'context':{},'observe':observe,'constants':constants,
             'clock':{'mode':'fixed','date':'2028-02-29','time':'23:59:59','timezone':'UTC'},'executorLocale':'ru-RU',
             'sequence':[{'method':start,'args':[]},{'method':apply,'args':[{},{}]}],
             'snapshotConstants':True,'expected':{'actions':[{'result':a,'constants':constants} for a in actions]}})
    checks.append({'id':'portable-shadow','type':'runtime','points':1,'target':{'module':form,'namespace':'Alpha','method':'Shadow'},
         'args':['param',{'key':'dict'}], 'expected':{'result':'param:dict','args':['param',{'key':'dict'}]},'snapshotArgs':True})
    return checks
