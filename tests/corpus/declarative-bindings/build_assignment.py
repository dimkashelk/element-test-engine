"""Offline teacher authoring for Dvizhok. Never used to grade submissions.

Intent is chosen from field purposes/names below, not by executing the reference
expression. Frozen requirements/fixtures are reused for every submission.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import re
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from build_portable import write
from element_test.loader import open_project
from element_test.model import analyze
from element_test.form_context import expression_values, tree_values
from element_test.declarative_bindings import get_path, resolver_for, component_value_type, table_context, plan_binding
from element_test.generated_types import union_members
from element_test.resolution import qualified

REPO=Path(__file__).resolve().parents[3]
ASSIGNMENT=REPO/'assignments/dvizhok-declarative-bindings'
UUID='00000000-0000-4000-8000-000000000038'


def marker(c,typ,label,state):
    typ=c.canonical_type(typ)
    union=union_members(typ)
    if union:
        variants=[v for v in union if v!='?']
        if state==0 and union[-1]=='?':return None
        variant=variants[(state-1)%len(variants)]
        owner=c.canonical_elements[variant.partition('.')[0]]
        return {'type':qualified(owner)+'.'+variant.partition('.')[2], 'value':marker(c,variant,label,state)}
    if typ.endswith('?'):
        return None if state==0 else marker(c,typ[:-1],label,state)
    if typ=='Строка':return '' if state==0 else label+'-marker-'+str(state)
    if typ=='Число':return 0 if state==0 else 11.25+sum(map(ord,label))%29+state
    if typ=='Булево':return state%2==1
    if typ=='Ууид':return UUID
    if typ=='Дата':return '2026-10-03'
    if typ=='ДатаВремя':return '2026-10-03T12:34:56'
    if typ in c.enums:return c.enums[typ][state%len(c.enums[typ])]
    collection=re.fullmatch(r'Массив<(.+)>',typ)
    if collection:
        if state==0:return []
        row=marker(c,collection[1],label+'.row',state)
        return [row,marker(c,collection[1],label+'.next',2),deepcopy(row)] if state==1 else [row]
    if typ in c.fields:
        return {f['Имя']:marker(c,f['Тип'],label+'.'+f['Имя'],state) for f in c.fields[typ]}
    raise ValueError('Teacher fixture type: '+typ)


def teacher_intent(form,path,item):
    """Independent purpose catalogue of this assignment (not an evaluator)."""
    key=path[-1];name=item.get('Имя','')
    name={'ПолеВводаКонтактноеЛицо':'КонтактноеЛицо'}.get(name,name)
    if key=='Заголовок':return 'Приветствие' if form['name']=='ФормаПриветствие' else 'ЗаголовокФормы'
    if key=='Представление':
        return 'ПредставлениеАрхивирования' if item.get('Обработчик')=='АрхивироватьОбработчик' else 'ПредставлениеРедактированиеДанных'
    if key=='Доступность':
        return {'Телефон':'ТелефонАктивен','Почта':'ПочтаАктивна'}.get(name,'РедактированиеДанных')
    if key=='Видимость':return 'СозданныйНаЧасти'
    if key=='Данные':return 'Объект.'+get_path(form['properties'],path[:-2])['Имя']
    if key=='Значение':
        if name=='НадписьИтогиЧасти':return 'ТекстИтоговЧасти'
        if name in {'ФлажокТелефонАктивен','ФлажокПочтаАктивна'}:return name.removeprefix('Флажок')
        if 'Колонки' in path:return 'RowData.'+name.partition('_')[2]
        root='Запись' if form['properties']['Наследует']['Тип'].startswith('ФормаЗаписиНабораКонстант<') else 'Объект'
        return root+'.'+name
    raise ValueError('Unspecified teacher purpose: '+str(path))


def read_fixture(value,path):
    for part in path.split('.'):
        value=value[part]
    return deepcopy(value)


def author(model,form,index):
    props=form['properties'];base=props['Наследует'];c=resolver_for(model,form)
    prefix='form-'+str(index).zfill(2)
    clauses=[];requirements=[]
    def add(text,kind,selector,**kwargs):
        cid=prefix+'-'+str(len(requirements)).zfill(3)
        clauses.append({'id':cid,'text':text})
        requirements.append({'id':cid,'clause':cid,'kind':kind,'points':1,'selector':selector,**kwargs})
    add('Базовый тип формы и владелец данных: '+base['Тип']+'.','structure',{},**{'assert':{'type':base['Тип']}})
    for path,item in tree_values(base,('Наследует',)):
        if 'Обработчик' in item or item.get('Тип')=='Кнопка' and 'ПриНажатии' in item:
            add('Собственная команда в указанном фрагменте имеет совместимый обработчик без результата.','structure',{'path':list(path)},**{'assert':{'handler':True}})
        if 'Тип' in item and item['Тип'] in {'РабочиеДниФормаЗаписи','ДанныеКомпанииФормаЗаписи','НастройкиФормаЗаписи'}:
            add('Встроенная форма '+item['Тип']+' доступна только для чтения.','structure',{'name':item['Имя']},**{'assert':{'type':item['Тип'],'properties':{'ТолькоЧтение':True}}})
    for value in expression_values(props):
        path=value['yamlPath'];expression=value['expression'];last=path[-1]
        if last.isdigit() or last=='ОсновнаяКоманда':
            add('Связь команды в фрагменте '+ '/'.join(path)+' указывает '+expression[1:]+', без запуска эффектов.','structure',{},
                **{'assert':{'command':{'path':path,'reference':expression}}})
            continue
        parent=path[:-2] if last=='Данные' else path[:-1]
        item=get_path(props,parent);selector={'name':item['Имя']} if 'Имя' in item else {'path':parent}
        property='Источник.Данные' if last=='Данные' else last
        purpose=teacher_intent(form,path,item)
        if last=='Данные':
            table=table_context(form,path,c);output='Массив<'+table[2]+'>'
        elif last=='Значение':output=component_value_type(item)
        elif last in {'Доступность','Видимость','ТолькоЧтение'}:output='Булево'
        else:output='Строка'
        stub={'formRequirement':{'form':{'name':form['name'],'namespace':form['namespace']},'requirement':{'kind':'binding','selector':selector,'property':property,'outputType':output}},
              'context':{},'steps':[{'snapshot':True}]}
        # Resolve declaration types only; neither source evaluation nor actual
        # results are used to generate the oracle.
        plan=plan_binding(REPO,model,stub)
        c=plan.contracts;form_type=plan.form['canonical']
        contexts=[marker(c,form_type,form['name'],state) for state in range(3)]
        column='RowData.' in purpose
        table=table_context(form,path,c)
        values=[]
        for state,context in enumerate(contexts):
            if column:
                table_name=table[1]['Имя']
                rows=context['Объект'][table_name]
                values.append([read_fixture(row,purpose.removeprefix('RowData.')) for row in rows])
            else:
                values.append(read_fixture(context,purpose))
        steps=[{'snapshot':True}]
        # Replace typed fixtures between snapshots; the live session is retained.
        for context in contexts[1:]:
            for key,v in context.items():steps.append({'set':{'path':key,'value':v}})
            steps.append({'snapshot':True})
        if plan.form.get('recordOwner'):
            scenarios=[{'context':context,'steps':[{'snapshot':True}],'expected':{'actions':[value]}} for context,value in zip(contexts,values)]
        else:
            scenarios=[{'context':contexts[0],'steps':steps,'expected':{'actions':values}}]
        if purpose in {'РедактированиеДанных','ПредставлениеРедактированиеДанных'}:
            context=contexts[1];context['РедактированиеДанных']=False
            context['ПредставлениеРедактированиеДанных']='Разрешить редактирование реквизитов'
            vals=[False,True,False] if purpose=='РедактированиеДанных' else ['Разрешить редактирование реквизитов','Запретить редактирование реквизитов','Разрешить редактирование реквизитов']
            scenarios=[{'context':context,'lifecycle':{'isNew':False},'steps':[{'snapshot':True},{'call':{'method':'ПредставлениеРедактированиеДанныхОбработчик','args':[{}]}},{'snapshot':True},{'call':{'method':'ПредставлениеРедактированиеДанныхОбработчик','args':[{}]}},{'snapshot':True}], 'expected':{'actions':vals}}]
        if purpose=='ПредставлениеАрхивирования':
            context=contexts[1];context['Объект']['Архивный']=False;context['ПредставлениеАрхивирования']='Архивировать'
            scenarios=[{'context':context,'lifecycle':{'isNew':False},'steps':[{'call':{'method':'ОбновитьПредставлениеАрхивирования','args':[]}},{'snapshot':True},{'set':{'path':'Объект.Архивный','value':True}},{'call':{'method':'ОбновитьПредставлениеАрхивирования','args':[]}},{'snapshot':True}], 'expected':{'actions':['Архивировать','Вернуть из архива']}}]
        if form['name']=='ОтгрузкаФормаОбъекта' and (last=='Данные' or column or purpose=='СозданныйНаЧасти'):
            context=contexts[1];context['СозданныйНаЧасти']=False
            rows=context['Объект']['Товары'];supplied=context['ТоварыТабличнаяЧасть']
            for row in rows:row.update(Количество=2,Цена=10,Сумма=17)
            rows[1].update(Количество=0,Цена=0.5,Сумма=19)
            for row in supplied:row.update(Количество=3,Цена=11,Сумма=31)
            supplied[1].update(Количество=1.25,Цена=2.5,Сумма=33)
            if column:
                field=purpose.removeprefix('RowData.');vals=[[r[field] for r in rows],[r[field] for r in supplied],[r[field] for r in rows]]
            elif last=='Данные':vals=[rows,supplied,rows]
            else:vals=[False,True,False]
            scenarios=[{'context':context,'steps':[{'snapshot':True},{'set':{'path':'СозданныйНаЧасти','value':True}},{'snapshot':True},{'set':{'path':'СозданныйНаЧасти','value':False}},{'snapshot':True}], 'expected':{'actions':deepcopy(vals)}}]
        if form['name']=='ФормаПриветствие':
            context={'Приветствие':'До создания'}
            scenarios=[{'context':context,'executorLocale':'ru-RU','clock':{'mode':'fixed','date':'2026-10-02','time':'00:00:00','timezone':'UTC'},
                        'steps':[{'snapshot':True},{'call':{'method':'ПослеСоздания','args':[]}},{'snapshot':True}],
                        'expected':{'actions':['До создания','Доброй ночи']}}]
        add('Компонент '+str(item.get('Имя','/'.join(parent)))+' в указанной области: '+property+' показывает '+purpose+'. Допускаются эквивалентные выражения.','binding',selector,
            property=property,outputType=output,scenarios=scenarios)
        if 'Тип' in item:
            add('Тип требуемого компонента соответствует данным '+output+'.','structure',selector,**{'assert':{'type':item['Тип']}})
    for i,field in enumerate(props.get('Свойства',[])):
        properties={'Имя':field['Имя']}
        if 'ЗначениеПоУмолчанию' in field:properties['ЗначениеПоУмолчанию']=field['ЗначениеПоУмолчанию']
        add('Собственное свойство '+field['Имя']+' имеет тип '+field['Тип']+' и явно объявленные значения по умолчанию.',
            'structure',{'role':'СвойствоФормы','name':field['Имя']},**{'assert':{'type':field['Тип'],'properties':properties}})
    description='\n'.join(c['text'] for c in clauses)+'\nДополнительные компоненты разрешены. Имена обязательны только для явно названных компонентов. Порядок остальных компонентов свободный. Пути анонимных командных фрагментов явно закреплены указанными пунктами. Константы доступны только для чтения. Browser/native UI не оценивается.'
    return {'schemaVersion':1,'description':description,'clauses':clauses,'form':{'name':form['name'],'namespace':form['namespace']},
            'requirements':requirements,'underdetermined':['Пиксельная компоновка','Реактивные подписки и браузерные события']}


def main():
    ASSIGNMENT.mkdir(parents=True,exist_ok=True)
    with open_project(REPO/'Dvizhok.xdump') as root:
        model=analyze(root)
        # Authoring resolves declared imports; original modules stay available
        # under the archive extraction only for optional explicit method plans.
        forms=[e for e in model['elements'] if e['elementType']=='КомпонентИнтерфейса']
        filenames=[]
        for i,form in enumerate(forms):
            contract=author(model,form,i)
            filename='forms/'+str(i).zfill(2)+'-'+form['name']+'.yaml'
            write(ASSIGNMENT/filename,contract);filenames.append(filename)
        write(ASSIGNMENT/'assignment.yaml',{'name':'Dvizhok: форма по описанию №38','formRequirements':filenames})

if __name__=='__main__':main()
