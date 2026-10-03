"""Author two implementations and frozen, independent teacher requirements."""
from pathlib import Path
import yaml

class Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):
        return True


def write(path, data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(yaml.dump(data,Dumper=Dumper,allow_unicode=True,sort_keys=False),encoding='utf-8')


def build(root, alternative=False):
    write(root/'Проект.yaml',{'ВидЭлемента':'Проект','Имя':'PortableBindings','Поставщик':'Tests','Версия':'1.0','РежимСовместимости':'9.0'})
    for ns in ('Alpha','Beta'):
        write(root/ns/'Подсистема.yaml',{'ВидЭлемента':'Подсистема','Имя':ns,'ОбластьВидимости':'ВПроекте'})
        write(root/ns/'Ledger.yaml',{'ВидЭлемента':'Документ','Имя':'Ledger','ОбластьВидимости':'ВПроекте',
            'Реквизиты':[{'Имя':'Caption','Тип':'Строка'},{'Имя':'Other','Тип':'Строка'}],
            'ТабличныеЧасти':[{'Имя':'Entries','Реквизиты':[{'Имя':'Debit','Тип':'Число'},{'Имя':'Credit','Тип':'Число'}]}]})
        write(root/ns/'Options.yaml',{'ВидЭлемента':'НаборКонстант','Имя':'Options','ОбластьВидимости':'ВПроекте',
            'Константы':[{'Имя':'Text','Тип':'Строка','ЗначениеПоУмолчанию':'seed'},{'Имя':'Enabled','Тип':'Булево'}]})
    panel = {'ВидЭлемента':'КомпонентИнтерфейса','Имя':'Panel','ОбластьВидимости':'ВПроекте',
      'Свойства':[{'Имя':'Selected','Тип':'Булево'},{'Имя':'Supplied','Тип':'Массив<Alpha::Ledger.Entries>'}] + [{'Имя':name,'Тип':'Массив<Alpha::Ledger.Entries>'} for name in ['Lost','Reordered','Duplicated']],
      'Наследует':{'Тип':'ФормаОбъекта<Alpha::Ledger.Объект>','Содержимое':{'Тип':'Группа','Содержимое':[
       {'Тип':'ПолеВвода<Строка>','Имя':'LabelB' if alternative else 'LabelA','Значение':'=Объект.Caption'},
       {'Тип':'Таблица<ИсточникДанныхМассив<Alpha::Ledger.Entries>>','Имя':'GridB' if alternative else 'GridA',
        'Источник':{'Данные':'=не Selected ? Объект.Entries : Supplied' if alternative else '=Selected ? Supplied : Объект.Entries'},
        'Колонки':[{'Тип':'СтандартнаяКолонкаТаблицы<Alpha::Ledger.Entries, Число>','Имя':'AmountB' if alternative else 'AmountA','Значение':'=RowData.Debit'}]}
      ]}}}
    if alternative:
        panel['Наследует']['Содержимое']['Содержимое'].reverse()
    else:
        (root/'Alpha/Panel.xbsl').write_text('импорт Alpha::Ledger как Book\n',encoding='utf-8')
        def aliases(value):
            if isinstance(value,dict):return {k:aliases(v) for k,v in value.items()}
            if isinstance(value,list):return [aliases(v) for v in value]
            return value.replace('Alpha::Ledger','Book') if isinstance(value,str) else value
        panel=aliases(panel)
    write(root/'Alpha/Panel.yaml',panel)
    write(root/'Alpha/Plain.yaml',{'ВидЭлемента':'КомпонентИнтерфейса','Имя':'Plain','Свойства':[{'Имя':'Title','Тип':'Строка','ЗначениеПоУмолчанию':'seed'}],
      'Наследует':{'Тип':'Форма','Заголовок':'=Title'}})
    write(root/'Alpha/RecordPanel.yaml',{'ВидЭлемента':'КомпонентИнтерфейса','Имя':'RecordPanel',
       'Наследует':{'Тип':'ФормаЗаписиНабораКонстант<Options.Запись>','Содержимое':{'Тип':'ПолеВвода<Строка>','Имя':'Text','Значение':'=Запись.Text'}}})
    return root


def teacher():
    context = {'Объект':{'Caption':'caption-marker','Other':'other-marker','Entries':[{'Debit':17,'Credit':23},{'Debit':19,'Credit':29},{'Debit':17,'Credit':23}]},
               'Selected':False,'Supplied':[{'Debit':31,'Credit':47}],
               'Lost':[{'Debit':17,'Credit':23}],
               'Reordered':[{'Debit':19,'Credit':29},{'Debit':17,'Credit':23},{'Debit':17,'Credit':23}],
               'Duplicated':[{'Debit':17,'Credit':23},{'Debit':19,'Credit':29},{'Debit':17,'Credit':23},{'Debit':17,'Credit':23}]}
    table = {'role':'Таблица'}
    requirements = [
      {'id':'caption-structure','clause':'fields','kind':'structure','selector':{'role':'ПолеВвода','binding':'Объект.Caption'},'assert':{'type':'ПолеВвода<Строка>'}},
      {'id':'caption','clause':'fields','kind':'binding','selector':{'role':'ПолеВвода','binding':'Объект.Caption'},'property':'Значение','outputType':'Строка',
       'scenarios':[{'context':context,'expected':{'actions':['caption-marker']}}]},
      {'id':'source','clause':'rows','kind':'binding','selector':table,'property':'Источник.Данные','outputType':'Массив<Alpha::Ledger.Entries>',
       'scenarios':[{'context':context,'steps':[{'snapshot':True},{'set':{'path':'Selected','value':True}},{'snapshot':True},{'set':{'path':'Selected','value':False}},{'snapshot':True}],
                    'expected':{'actions':[[{'Debit':17,'Credit':23},{'Debit':19,'Credit':29},{'Debit':17,'Credit':23}],[{'Debit':31,'Credit':47}],[{'Debit':17,'Credit':23},{'Debit':19,'Credit':29},{'Debit':17,'Credit':23}]]}}]},
      {'id':'debit','clause':'rows','kind':'binding','selector':{'role':'СтандартнаяКолонкаТаблицы','within':table},'property':'Значение','outputType':'Число',
       'scenarios':[{'context':context,'steps':[{'snapshot':True},{'set':{'path':'Selected','value':True}},{'snapshot':True},{'set':{'path':'Selected','value':False}},{'snapshot':True}],
                    'expected':{'actions':[[17,19,17],[31],[17,19,17]]}}]},
      {'id':'no-button','clause':'fields','kind':'structure','selector':{'role':'Кнопка'},'assert':{'exists':False}}
    ]
    return {'schemaVersion':1,'description':'Форма показывает название регистра и строки проводок. Обычный источник — строки документа. В режиме выбора показаны переданные строки. Колонка показывает дебет. Кнопки запрещены. Порядок колонок и имена компонентов свободны.',
      'clauses':[{'id':'fields','text':'Название документа, без кнопок.'},{'id':'rows','text':'Дебет строк выбранного источника с сохранением порядка и повторов.'}],
      'form':{'name':'Panel','namespace':'Alpha'},'requirements':requirements,'underdetermined':['Пиксельная компоновка']}


if __name__ == '__main__':
    home=Path(__file__).resolve().parent
    for name,alternate in [('first',False),('second',True)]:
        build(home/name,alternate)
    write(home/'requirements.yaml',teacher())
    write(home/'assignment.yaml',{'name':'Переносимая проверка формы','formRequirements':['requirements.yaml']})
