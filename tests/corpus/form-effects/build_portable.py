"""Regenerate portable declarations; IDs are independent UUID v4 metadata fixtures."""
from pathlib import Path
import json
import yaml
REPO=Path(__file__).resolve().parents[3]
import uuid
IDS=(str(uuid.uuid4()) for _ in range(100))


def build(project,names):
    record,form,target,rowtable,flag,number,field,amount,apply,helper,openmethod,init=names
    root=REPO/'tests/corpus/form-effects'/project
    def write(path,value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(yaml.safe_dump({'Ид':next(IDS),**value},allow_unicode=True,sort_keys=False))
    write(root/'Проект.yaml',{'ВидЭлемента':'Проект','Имя':project,'Поставщик':'Tests','Версия':'1.0','РежимСовместимости':'9.0'})
    for ns in ['Alpha','Beta']:
        write(root/ns/'Подсистема.yaml',{'ВидЭлемента':'Подсистема','Имя':ns,'ОбластьВидимости':'ВПроекте'})
        write(root/ns/(record+'.yaml'),{'ВидЭлемента':'Документ','Имя':record,'ОбластьВидимости':'ВПроекте',
             'Реквизиты':[{'Имя':number,'Тип':'Строка'},{'Имя':flag,'Тип':'Булево'},{'Имя':field,'Тип':'Строка'}],
             'ТабличныеЧасти':[{'Имя':rowtable,'Реквизиты':[{'Имя':amount,'Тип':'Число'}]}]})
        props=[{'Имя':'Caption','Тип':'Строка','ЗначениеПоУмолчанию':'seed'}]
        write(root/ns/(form+'.yaml'),{'ВидЭлемента':'КомпонентИнтерфейса','Имя':form,'ОбластьВидимости':'ВПроекте',
             'Свойства':props,'Наследует':{'Тип':f'ФормаОбъекта<{record}.Объект>'}})
        write(root/ns/(target+'.yaml'),{'ВидЭлемента':'КомпонентИнтерфейса','Имя':target,'ОбластьВидимости':'ВПроекте',
             'Свойства':[{'Имя':'Lines','Тип':f'Массив<{record}.{rowtable}>'},{'Имя':'Optional','Тип':record+'.Ссылка?'},
                         {'Имя':'Caption','Тип':'Строка','ЗначениеПоУмолчанию':'default'}],
             'Наследует':{'Тип':f'ФормаОбъекта<{record}.Объект>'}})
        (root/ns/(target+'.xbsl')).write_text(f'''метод {init}()
    Caption = "opened: ${{Lines.Размер()}}"
    если Lines.Размер() > 0
        Lines[0].{amount} = 999
    ;
;
''')
        (root/ns/(form+'.xbsl')).write_text(f'''импорт {'Beta' if ns=='Alpha' else 'Alpha'}::{target} как Destination
метод {apply}(C: ОбычнаяКоманда)
    Объект.{flag} = не Объект.{flag}
    Записать()
    {helper}()
;
метод {helper}()
    Caption = Объект.{flag} ? "on" : "off"
;
метод {openmethod}(Rows: Массив<{'Beta' if ns=='Alpha' else 'Alpha'}::{record}.{rowtable}>)
    Destination.Открыть(Lines = Rows, Optional = Неопределено)
    если Rows.Размер() > 0
        Rows[0].{amount} = 33
    ;
    Destination.Открыть(Lines = Rows)
;
метод Local()
    Записать()
;
''')
    return root

if __name__=='__main__':
    for p,names in [('ledgers',('Ledger','Panel','Window','Rows','Archived','Code','Label','Amount','Apply','Decorate','Show','Start')),
                    ('accounts',('Account','Editor','Dialog','Entries','Disabled','Number','Note','Cost','Switch','Refresh','Display','Init'))]:
        print(build(p,names))
