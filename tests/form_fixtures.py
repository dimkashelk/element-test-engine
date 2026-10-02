"""Independent form scenarios and portable renamed declarations."""
from pathlib import Path
import yaml
from element_test.runtime import REPO
CORPUS = REPO/'tests/corpus/form-context'
EVIDENCE = REPO/'result/dvizhok-form-context'
ARCHIVE = REPO/'Dvizhok.xdump'
ASSIGNMENT = REPO/'assignments/dvizhok-form-context'


def portable(root, renamed=False):
    import uuid
    names = ('Ledger','Panel','Changed','Notice','Summary','Apply','Init','Helper') if not renamed else (
        'Account','Editor','Editable','TitleText','Banner','Switch','Start','Decorate')
    record,form,flag,title,label,apply,init,helper=names
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        value={'Ид':str(uuid.uuid4()),**value}
        path.write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False),encoding='utf-8')
    write(root/'Проект.yaml',{'ВидЭлемента':'Проект','Имя':'PortableB' if renamed else 'PortableA','Поставщик':'Tests','Версия':'1.0','РежимСовместимости':'9.0'})
    for ns in ('Alpha','Beta'):
        write(root/ns/'Подсистема.yaml',{'ВидЭлемента':'Подсистема','Имя':ns,'ОбластьВидимости':'ВПроекте'})
        write(root/ns/(record+'.yaml'),{'ВидЭлемента':'Документ','Имя':record,'ОбластьВидимости':'ВПроекте','Реквизиты':[{'Имя':'Номер','Тип':'Строка'},{'Имя':'Link','Тип':('Beta' if ns=='Alpha' else 'Alpha')+'::'+record+'.Ссылка?'}],'ТабличныеЧасти':[{'Имя':'Rows','Реквизиты':[{'Имя':'Value','Тип':'Число'}]}]})
        write(root/ns/(form+'.yaml'),{'ВидЭлемента':'КомпонентИнтерфейса','Имя':form,'ОбластьВидимости':'ВПроекте','Свойства':[{'Имя':flag,'Тип':'Булево'},{'Имя':title,'Тип':'Строка','ЗначениеПоУмолчанию':'seed'}], 'Наследует':{'Тип':f'ФормаОбъекта<{record}.Объект>','Содержимое':{'Тип':'Группа','Имя':'Outer','Содержимое':[{'Тип':'Группа','Имя':'Inner','Содержимое':[{'Тип':'Надпись','Имя':label}]}]}}})
        (root/ns/(form+'.xbsl')).write_text(f'''метод {init}()
    если Объект.ЭтоНовый()
        {title} = "fresh"
    иначе
        {title} = "old: ${{Объект.Номер}}"
    ;
    этот.{helper}()
;
@Локально
метод {helper}()
    Компоненты.{label}.Значение = "label: ${{{title}}}"
;
метод {apply}({flag}: Булево, C: ОбычнаяКоманда)
    этот.{flag} = {flag}
    знч {title} = "local"
    Компоненты.{label}.Значение = "${{{title}}}"
;
''',encoding='utf-8')
    return names


def portable_checks(names):
    record,form,flag,title,label,apply,init,helper=names
    return [{'id':'scope-'+ns,'type':'runtime','points':1,
             'target':{'module':form,'namespace':ns,'method':init},
             'context':{'Объект':{'Номер':'R','Rows':[{'Value':1.25}]}, title:'seed'},
             'lifecycle':{'isNew':ns=='Alpha'}, 'args':[],
             'observe':[title,flag,'Компоненты.'+label+'.Значение','Объект.Rows'],
             'sequence':[{'method':init,'args':[]},{'method':apply,'args':[True,{}]}],
             'expected':{'actions':[
                 {title:'fresh' if ns=='Alpha' else 'old: R',flag:False,'Компоненты.'+label+'.Значение':'label: '+('fresh' if ns=='Alpha' else 'old: R'),'Объект.Rows':[{'Value':1.25}]},
                 {title:'fresh' if ns=='Alpha' else 'old: R',flag:True,'Компоненты.'+label+'.Значение':'local','Объект.Rows':[{'Value':1.25}]}]}}
            for ns in ('Alpha','Beta')]
