"""Programming assignment: fixed lesson contracts and behavioral practice tests."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import html
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path[:0] = [str(REPO), str(HERE)]
from cases import cases, FORMS
from element_test.bridge import canonicalize_model_types, write_json
from element_test.indexer import lex, parse_module, method_calls
from element_test.assignment_names import aliases_config, owner_name, adapt, lower_local_overloads, tree
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import run_pure, execute_engine
from element_test.yaml_io import InputError
from element_test.declarative_bindings import preflight


def identity(item):
    return '::'.join(filter(None, (item['namespace'], item['name'])))


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items() if k != 'Ид'}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def resolve_aliases(model, explicit):
    """Infer only unique signatures and declared form owners; ambiguity is never guessed."""
    config = deepcopy(explicit)
    owners, names = config.setdefault('owners', {}), config.setdefault('names', {})
    elements = {identity(e): e for e in model['elements']}
    modules = {identity(m): m for m in model['modules']}
    for ns, document, _, _ in FORMS:
        canonical = ns+'::'+document+'ФормаОбъекта'
        if canonical not in elements and canonical not in owners:
            matches = [identity(e) for e in model['elements'] if e['namespace']==ns
                       and e['properties'].get('Наследует', {}).get('Тип')==f'ФормаОбъекта<{document}.Объект>']
            if len(matches)==1: owners[canonical]=matches[0]
        physical = owner_name(canonical, config)
        props = elements.get(physical, {}).get('properties', {})
        mapping = names.setdefault(canonical, {})
        caption = props.get('Наследует', {}).get('Заголовок','')
        if isinstance(caption,str) and re.fullmatch(r'=\w+',caption):
            mapping.setdefault('ЗаголовокФормы',caption[1:])
        for field in props.get('Свойства', []):
            if field.get('ЗначениеПоУмолчанию')=='Разрешить редактирование реквизитов':
                mapping.setdefault('ПредставлениеРедактированиеДанных',field['Имя'])
        label = mapping.get('ПредставлениеРедактированиеДанных','ПредставлениеРедактированиеДанных')
        candidates = [v['Обработчик'] for _,v in tree(props) if isinstance(v,dict)
                      and v.get('Представление')=='='+label and 'Обработчик' in v]
        if len(candidates)==1:mapping.setdefault('ПредставлениеРедактированиеДанныхОбработчик',candidates[0])
    signatures = [
        ('CRM::ФизическиеЛица.Объект','СформироватьФИО',['Строка']*3,'Строка'),
        ('Администрирование::ФормаПриветствие','СформироватьТекстОрганизации',['Соответствие<Строка, Строка>'],'Строка'),
        ('Администрирование::ФормаПриветствие','ПолучитьТекстДня',['Строка','Соответствие<Строка, Булево>'],'Строка'),
        ('Администрирование::ФормаПриветствие','РассчитатьДни',['Дата','Дата','Соответствие<Строка, Булево>'],'Соответствие<Строка, Число>'),
        ('Продажи::ЗаказФормаОбъекта','РассчитатьИтоги',['Массив<Заказ.Товары>'],'Соответствие<Строка, Число>')]
    compact=lambda s:re.sub(r'\s+','',s or '')
    for owner,canonical,parameters,returned in signatures:
        methods=modules.get(owner_name(owner,config),{}).get('methods',[])
        mapping=names.setdefault(owner,{})
        if canonical in mapping or any(m['name']==canonical for m in methods):continue
        matches=[m['name'] for m in methods if [compact(p['type']) for p in m['parameters']]==list(map(compact,parameters))
                 and compact(m.get('returnType'))==compact(returned)]
        if len(matches)==1:mapping[canonical]=matches[0]
    # The supplied lesson spells Расчитать with one с; a new handler name is not required.
    welcome='Администрирование::ФормаПриветствие'
    props=elements.get(owner_name(welcome,config),{}).get('properties',{})
    buttons={v['ПриНажатии'] for _,v in tree(props) if isinstance(v,dict)
             and v.get('Тип')=='Кнопка' and isinstance(v.get('ПриНажатии'),str)}
    if len(buttons)==1:
        names.setdefault(welcome,{}).setdefault('КнопкаРассчитатьПриНажатии',next(iter(buttons)))
    for owner,caption,canonical in [
            ('CRM::СделкаФормаОбъекта','Сумма отмеченного спроса','СуммаОтмеченногоСпросаОбработчик'),
            ('CRM::СделкаФормаОбъекта','Посчитать участников','ПосчитатьУчастниковОбработчик'),
            ('Продажи::ЗаказФормаОбъекта','Итоги отмеченных','ИтогиОтмеченныхОбработчик')]:
        props=elements.get(owner_name(owner,config),{}).get('properties',{})
        candidates={v['Обработчик'] for _,v in tree(props) if isinstance(v,dict)
                    and v.get('Представление')==caption and isinstance(v.get('Обработчик'),str)}
        if len(candidates)==1:names.setdefault(owner,{}).setdefault(canonical,next(iter(candidates)))
    return config


def load_source(root, model, owner, method):
    matches = [m for m in model['modules'] if identity(m) == owner]
    if len(matches) != 1:
        return None
    module = matches[0]
    source = (root / module['sourceFile']).read_text(encoding='utf-8-sig')
    nodes, _, errors = parse_module(source)
    matches = [n for n in nodes if n.name == method]
    return (source, matches[0], module) if not errors and len(matches) == 1 else None


def binding_cases(model, config):
    from cases import ROWS
    canonical='Товары::ОтгрузкаФормаОбъекта';owner=owner_name(canonical,config)
    element=next((e for e in model['elements'] if identity(e)==owner),{})
    tables=[(p,v) for p,v in tree(element.get('properties',{})) if isinstance(v,dict)
            and v.get('Тип','').startswith('Таблица<') and 'Отгрузка.Товары' in v['Тип']]
    path=list(map(str,tables[0][0])) if len(tables)==1 else ['Наследует','ТаблицаОтсутствует']
    names=config.get('names',{}).get(canonical,{})
    result=[]
    for partial in [True,False]:
        result.append(dict(id='P09.binding.'+str(partial).lower(),type='runtime',group='P09',points=1,
                           formRequirement={'form':{'namespace':'::'.join(owner.split('::')[:-1]),'name':owner.split('::')[-1]},
                             'clause':{'id':'P09','text':'Частичная отгрузка использует переданные строки; обычная — Объект.Товары'},
                             'requirement':{'id':'source','clause':'P09','kind':'binding','selector':{'path':path},
                                            'property':'Источник.Данные','outputType':'Массив<Отгрузка.Товары>'}},
                           context={'Объект':{'Товары':deepcopy(ROWS)},
                                    names.get('СозданныйНаЧасти','СозданныйНаЧасти'):partial,
                                    names.get('ТоварыТабличнаяЧасть','ТоварыТабличнаяЧасть'):[deepcopy(ROWS[1])]},
                           lifecycle={'isNew':not partial},steps=[{'snapshot':True}],
                           expected={'actions':[[deepcopy(ROWS[1])] if partial else deepcopy(ROWS)]},timeout='20s'))
    return result


def structural(root, model, config, optional=False):
    baseline = json.loads((HERE / 'lesson-contract.json').read_text())
    result = []
    def record(ident, ok, message, **details):
        result.append(dict(id=ident, status='PASS' if ok else 'FAIL', message=message, **details))
    elements = {identity(e): e for e in model['elements']}
    record('L.project', str(model['properties'].get('РежимСовместимости')) == '9.0', 'Режим совместимости 9.0')
    record('L.diagnostics', not model['diagnostics'], 'Нет диагностик метаданных', actual=model['diagnostics'])
    for owner, expected in baseline['dataElements'].items():
        actual = elements.get(owner, {}).get('properties', {})
        # All inherited data declarations are fixed; new fields/operations may be added.
        for key, value in expected.items():
            if key in {'Импорт','Интерфейс','СозданиеНаОсновании','ОбластьВидимости'}:
                continue
            if isinstance(value, list) and all(isinstance(x, dict) and 'Имя' in x for x in value):
                found = {x.get('Имя'): plain(x) for x in actual.get(key, [])}
                ok = all(found.get(x['Имя']) == x for x in value)
            else:
                ok = plain(actual.get(key)) == value
            record(f'L.metadata.{owner}.{key}', ok, f'Сохранить лекционное объявление {owner}.{key}')
    for item in baseline['fixedMethods']:
        found = load_source(root, model, item['owner'], item['method'])
        tokens = [t.value for t in lex(found[0][found[1].start:found[1].end])] if found else None
        record('L.source.'+item['owner']+'.'+item['method'], tokens == item['tokens'],
               'Этот лекционный метод не требуется менять в практической; сохраняются его инструкции и сигнатура')
    for ns, name, _, _ in FORMS:
        canonical = ns+'::'+name+'ФормаОбъекта'
        owner = owner_name(canonical, config)
        props = elements.get(owner, {}).get('properties', {})
        names = config.get('names', {}).get(canonical, {})
        base = props.get('Наследует', {})
        record('P.form.'+name, base.get('Тип') == f'ФормаОбъекта<{name}.Объект>',
               'Форма должна редактировать свой документ')
        field = names.get('РедактированиеДанных','РедактированиеДанных')
        header = names.get('ЗаголовокФормы','ЗаголовокФормы')
        record('P.title.binding.'+name, base.get('Заголовок') == '='+header,
               'Заголовок корневой формы связан со строковым свойством')
        declared = {x.get('Имя'): x for x in props.get('Свойства', [])}
        record('P.state.'+name, declared.get(field, {}).get('Тип') == 'Булево'
               and declared.get(field, {}).get('ЗначениеПоУмолчанию') is False,
               'РедактированиеДанных имеет тип Булево и начальное значение Ложь')
        controls = [v for _, v in tree(base) if isinstance(v, dict)]
        for datafield in ['Номер','Дата']:
            inputs = [v for v in controls if v.get('Значение') == '=Объект.'+datafield]
            record('P.edit.binding.'+name+'.'+datafield, bool(inputs) and
                   all(v.get('Доступность') == '='+field for v in inputs),
                   'Доступность номера и даты связана с флагом редактирования')
        handler = names.get('ПредставлениеРедактированиеДанныхОбработчик','ПредставлениеРедактированиеДанныхОбработчик')
        text = names.get('ПредставлениеРедактированиеДанных','ПредставлениеРедактированиеДанных')
        commands = [v for v in controls if v.get('Обработчик') == handler]
        record('P.edit.command.'+name, any(v.get('Представление') == '='+text for v in commands),
               'Обычная команда связана с обработчиком и динамическим представлением')
        registered=elements.get(ns+'::'+name,{}).get('properties',{}).get('Интерфейс',{}).get('Объект',{}).get('Форма')
        record('P.form.registration.'+name, registered==owner.split('::')[-1],
               'Собственная форма зарегистрирована как форма объекта документа')
        if name in {'Сделка','Заказ'}:
            archive_handler=names.get('АрхивироватьОбработчик','АрхивироватьОбработчик')
            archive_text=names.get('ПредставлениеАрхивирования','ПредставлениеАрхивирования')
            record('P04.command.binding.'+name,any(v.get('Обработчик')==archive_handler
                   and v.get('Представление')=='='+archive_text for v in controls),
                   'Команда архивирования связана с обработчиком и представлением')
        module=next((m for m in model['modules'] if identity(m)==owner),{})
        initializers=[m for m in module.get('methods',[]) if m['name']=='ПослеСоздания']
        record('P.form.event.'+name,len(initializers)==1 and 'Обработчик' in initializers[0]['annotations'],
               'В форме ровно один ПослеСоздания с @Обработчик')
    for canonical, table, handler in [
            ('CRM::СделкаФормаОбъекта','Сделка.ПервичныйСпрос','СуммаОтмеченногоСпросаОбработчик'),
            ('CRM::СделкаФормаОбъекта','Сделка.Участники','ПосчитатьУчастниковОбработчик'),
            ('Продажи::ЗаказФормаОбъекта','Заказ.Товары','ИтогиОтмеченныхОбработчик'),
            ('Продажи::ЗаказФормаОбъекта','Заказ.Товары','ОтгрузитьОбработчик')]:
        owner=owner_name(canonical,config);mapping=config.get('names',{}).get(canonical,{})
        handler=mapping.get(handler,handler)
        props=elements.get(owner,{}).get('properties',{})
        tables=[v for _,v in tree(props) if isinstance(v,dict) and v.get('Тип','').startswith('Таблица<') and table in v['Тип']]
        wired=any(v.get('Обработчик')==handler for t in tables
                  for _,v in tree(t.get('КомандыНадОтмеченнымиСтроками',{})) if isinstance(v,dict))
        record('P08.selected.command.'+canonical+'.'+handler,wired,
               'Команда расположена над отмеченными строками нужной таблицы и связана с обработчиком')
        module=next((m for m in model['modules'] if identity(m)==owner),{})
        method=next((m for m in module.get('methods',[]) if m['name']==handler),{})
        compact=lambda s:re.sub(r'\w+::','',re.sub(r'\s+','',s or ''))
        types=[compact(p['type']) for p in method.get('parameters',[])]
        record('P08.signature.'+canonical+'.'+handler,types==[
               compact('КомандаСПараметром<Массив<'+table+'>>'),compact('Массив<'+table+'>')],
               'Команда с параметром принимает массив строк своей табличной части')
    canonical='Администрирование::ФормаПриветствие';mapping=config.get('names',{}).get(canonical,{})
    props=elements.get(owner_name(canonical,config),{}).get('properties',{})
    handler=mapping.get('КнопкаРассчитатьПриНажатии','КнопкаРассчитатьПриНажатии')
    record('P06.button.binding',any(v.get('ПриНажатии')==handler for _,v in tree(props) if isinstance(v,dict)),
           'Кнопка расчёта связана с обработчиком')
    welcome_module=next((m for m in model['modules'] if identity(m)==owner_name(canonical,config)),{})
    button=next((m for m in welcome_module.get('methods',[]) if m['name']==handler),{})
    record('P06.button.signature',[p['type'] for p in button.get('parameters',[])]==['Кнопка','СобытиеПриНажатии']
           and button.get('returnType') is None,'Обработчик кнопки принимает Кнопка и СобытиеПриНажатии без результата')
    initializers=[m for m in welcome_module.get('methods',[]) if m['name']=='ПослеСоздания']
    record('P05.event',len(initializers)==1 and 'Обработчик' in initializers[0]['annotations'],
           'ПослеСоздания формы приветствия связан с жизненным циклом через @Обработчик')
    person_module=next((m for m in model['modules'] if identity(m)==owner_name('CRM::ФизическиеЛица.Объект',config)),{})
    before=[m for m in person_module.get('methods',[]) if m['name']=='ПередЗаписью']
    record('P01.event',len(before)==1 and 'Обработчик' in before[0]['annotations'],
           'ПередЗаписью физического лица имеет @Обработчик')
    for owner, field, expected_type in [('Финансы::РасходДенежныхСредств','Основание','СчетНаОплату.Ссылка?')]:
        obj = elements.get(owner, {}).get('properties', {})
        record('P07.metadata.'+owner, any(f.get('Имя') == field and f.get('Тип') == expected_type
                                         for f in obj.get('Реквизиты', [])), 'Основание расхода — nullable-ссылка на счёт')
    for owner, reference in [('Продажи::Заказ','Сделка.Ссылка'),('Финансы::РасходДенежныхСредств','СчетНаОплату.Ссылка')]:
        record('P07.base.'+owner, reference in elements.get(owner, {}).get('properties', {}).get('СозданиеНаОсновании', []),
               'Создание на основании разрешено в метаданных')
    expense=elements.get(owner_name('Финансы::РасходДенежныхСредствФормаОбъекта',config),{}).get('properties',{})
    record('P07.expense.field',any(v.get('Значение')=='=Объект.Основание' for _,v in tree(expense) if isinstance(v,dict)),
           'Основание расхода выведено в форму')
    # Check that extracted helpers are actually used by handlers, not dead test-only code.
    reuse = [('CRM::ФизическиеЛица.Объект','ПередЗаписью','СформироватьФИО'),
             ('Администрирование::ФормаПриветствие','КнопкаРассчитатьПриНажатии','РассчитатьДни'),
             ('Администрирование::ФормаПриветствие','ПослеСоздания','СформироватьТекстОрганизации'),
             ('Администрирование::ФормаПриветствие','ПослеСоздания','ПолучитьТекстДня'),
             ('Продажи::ЗаказФормаОбъекта','ИтогиОтмеченныхОбработчик','РассчитатьИтоги'),
             ('Продажи::ЗаказФормаОбъекта','ОтгрузитьОбработчик','РассчитатьИтоги')]
    for owner, caller, callee in reuse:
        names = config.get('names', {}).get(owner, {})
        found = load_source(root, model, owner_name(owner, config), names.get(caller,caller))
        calls = [name for _,name in method_calls(found[0],found[1])] if found else []
        record('P.reuse.'+owner+'.'+caller+'.'+callee, names.get(callee,callee) in calls,
               'Обработчик вызывает выделенный метод '+callee)
    # Exact transport annotations are part of the lesson contract.
    annotations=[('Товары::Отгрузка','СоздатьЧасть',{'ВПроекте','ДоступноСКлиента','НаСервере'}),
                 ('Товары::Отгрузка','ПеренестиТабличнуюЧасть',{'ВПодсистеме'}),
                 ('Администрирование::ДанныеКомпании','ПолучитьДанные',{'ВПодсистеме','ДоступноСКлиента','НаСервере'}),
                 ('Администрирование::РабочиеДни','ПолучитьДанные',{'ВПодсистеме','ДоступноСКлиента','НаСервере'}),
                 ('CRM::ФизическиеЛица.Объект','СформироватьФИО',{'Локально'}),
                 ('Администрирование::ФормаПриветствие','СформироватьТекстОрганизации',{'Локально'}),
                 ('Администрирование::ФормаПриветствие','ПолучитьТекстДня',{'Локально'}),
                 ('Администрирование::ФормаПриветствие','РассчитатьДни',{'Локально'}),
                 ('Продажи::ЗаказФормаОбъекта','РассчитатьИтоги',{'Локально'})]
    for owner,method,expected in annotations:
        actual_owner=owner_name(owner,config);method=config.get('names',{}).get(owner,{}).get(method,method)
        module=next((m for m in model['modules'] if identity(m)==actual_owner),{})
        declared=next((m for m in module.get('methods',[]) if m['name']==method),{})
        record('P10.annotations.'+actual_owner+'.'+method,set(declared.get('annotations',[]))==expected,
               'Требуемые аннотации: '+', '.join(sorted(expected)))
    event_objects=[('Продажи','Заказ'),('Финансы','РасходДенежныхСредств'),('Товары','Отгрузка'),
                   ('Финансы','СчетНаОплату'),('Финансы','ПоступлениеДенежныхСредств')]
    if optional:event_objects.append(('Товары','ПоступлениеТоваров'))
    for ns,name in event_objects:
        owner=ns+'::'+name+'.Объект'
        module=next((m for m in model['modules'] if identity(m)==owner),{})
        event=next((m for m in module.get('methods',[]) if m['name']=='ПриСозданииНаОсновании'),{})
        record('P07.event.'+owner,'Обработчик' in event.get('annotations',[]), 'ПриСозданииНаОсновании помечен @Обработчик')
    if optional:
        record('X07.base','Отгрузка.Ссылка' in elements.get('Товары::ПоступлениеТоваров',{}).get('properties',{}).get('СозданиеНаОсновании',[]),
               'Дополнительный сценарий разрешён в метаданных')
    owner='Администрирование::ФормаПриветствие';mapping=config.get('names',{}).get(owner,{})
    for method,needs_loop in [('РассчитатьДни',True),('КнопкаРассчитатьПриНажатии',False)]:
        found=load_source(root,model,owner_name(owner,config),mapping.get(method,method))
        words=[t.value for t in lex(found[0][found[1].header_end:found[1].end])] if found else []
        record('P06.loop.'+method,('пока' in words)==needs_loop and bool(found),
               'Цикл пока расположен в методе расчёта; в обработчике его нет')
    welcome=next((m for m in model['modules'] if identity(m)==owner_name('Администрирование::ФормаПриветствие',config)),None)
    direct=[]
    if welcome:
        source=(root/welcome['sourceFile']).read_text(encoding='utf-8-sig')
        for node in parse_module(source)[0]:
            direct.extend((receiver,name) for receiver,name in method_calls(source,node)
                          if receiver and receiver.endswith('ДанныеКомпании') and name=='Получить')
    record('P05.server.boundary',bool(welcome) and not direct,
           'Форма получает данные через серверный ПолучитьДанные, без прямого чтения констант компании')
    return result


def trace_checks(runtime,config):
    result=[]
    for c in runtime:
        if c['id']!='P06.button.reverse':continue
        evidence=c['execution'];trace=evidence.get('trace',[])
        helper=config.get('names',{}).get('Администрирование::ФормаПриветствие',{}).get('РассчитатьДни','РассчитатьДни')
        called=any(e.get('event')=='enter' and e.get('symbol','').split('::')[-1]==helper for e in trace)
        result.append(dict(id='P06.reverse.no_calculation_call',status='PASS' if trace and not called else 'FAIL',
                           message='Для ошибочного диапазона обработчик не вызывает метод расчёта',helperCalled=called))
    return result


def html_report(result):
    esc = html.escape
    rows = ''.join('<tr><td>'+esc(c['id'])+'</td><td>'+esc(c['status'])+'</td><td><pre>'+
                   esc(json.dumps(c,ensure_ascii=False,indent=2))+'</pre></td></tr>' for c in result['checks'])
    return ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Программирование</title>'
            '<style>body{font:16px system-ui;max-width:1150px;margin:35px auto}table{border-collapse:collapse;width:100%}'
            'td,th{border:1px solid #ddd;padding:10px;vertical-align:top}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>Программирование Проверка практической работы</h1><p>Итог: '+esc(result['status'])+
            '</p><p>Проверены исходные методы в Script. Браузер и отладочные действия студента не проверяются.</p>'
            '<table><tr><th>Требование</th><th>Результат</th><th>Подробности</th></tr>'+rows+'</table></html>')


def prepare_assessment(root, model, work, *, optional=False, aliases=None):
    """Trusted service adapter. Observations and runtime are scored together by SBSL.

    A structural observation is explicitly labelled; EXECUTED here means the
    observation was obtained, and does not claim that student code was executed.
    Student code runs only through run_pure, with no uploaded Python involved.
    """
    config=resolve_aliases(model,aliases_config(value=aliases))
    observations=structural(root,model,config,optional)
    runtime_root,transforms=lower_local_overloads(root,work/'runtime-source')
    runtime_model=analyze(runtime_root) if transforms else model
    canonicalize_model_types(runtime_model)
    runtime=[adapt(c,config) for c in cases(optional)]+binding_cases(runtime_model,config)
    plans=[]
    # The shared host admits one job; keep its transient runtimes sequential.
    for check in runtime:
        check['timeout']='30s'
        prepared=[]
        if 'formRequirement' in check:
            check['execution']=preflight(runtime_root,runtime_model,check)
        if check.get('execution') is None:
            target=check.get('target',{})
            owner='::'.join(filter(None,(target.get('namespace'),target.get('module'))))
            if 'formRequirement' not in check and not load_source(runtime_root,runtime_model,owner,target.get('method')):
                check['execution']={'status':'FAIL','reasonCode':'result_mismatch',
                                    'message':'Не найден требуемый метод: '+json.dumps(target,ensure_ascii=False)}
            else:
                check['execution']=run_pure(runtime_root,runtime_model,check,work,plan_sink=prepared)
        plans.append({'criterionId':check['id'],**({'plan':prepared[0]} if prepared else
                     {'unavailable':{k:check['execution'].get(k) for k in ('status','reasonCode','message')}})})
    trace=trace_checks(runtime,config)
    reverse=next(c['execution'] for c in runtime if c['id']=='P06.button.reverse')
    if reverse['status'] not in ('EXECUTED','FAIL'):
        for observation in trace:observation['status']=reverse['status']
    observed=[]
    for kind,items in [('structural',observations),('trace',trace)]:
        for item in items:
            execution={'status':'EXECUTED','actual':item['status']=='PASS','observationKind':kind,
                       'message':item['message']}
            if item['status'] not in ('PASS','FAIL'):
                execution.update(status=item['status'],reasonCode=reverse.get('reasonCode','execution_error'))
            observed.append({'id':item['id'],'type':'runtime','points':1,'group':kind,
                             'expected':True,'failureExplanation':item['message'],'execution':execution})
    plans.append({'profile':'programming-practice','version':'1','sourceHash':model['sourceHash'],
                  'runtimeSourceHash':runtime_model['sourceHash'],'sourceAdaptations':transforms,
                  'resolvedAliases':config})
    return observed+runtime,plans


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--aliases')
    parser.add_argument('--optional',action='store_true')
    parser.add_argument('--only',help='Регулярное выражение ID для отладки; это не полная приёмка')
    args=parser.parse_args(argv)
    source, output=Path(args.project).resolve(),Path(args.output).resolve()
    if output==source or source.is_dir() and output.is_relative_to(source):
        parser.error('Отчёт должен находиться вне исходного проекта')
    config=aliases_config(args.aliases)
    with open_project(source) as root, TemporaryDirectory(prefix='programming-check-') as directory:
        work=Path(directory);model=analyze(root);canonicalize_model_types(model)
        config=resolve_aliases(model,config)
        static=structural(root,model,config,args.optional)
        original_hash=model['sourceHash']
        root,transforms=lower_local_overloads(root,work/'runtime-source')
        if transforms:model=analyze(root);canonicalize_model_types(model)
        runtime=[adapt(c,config) for c in cases(args.optional)]+binding_cases(model,config)
        if args.only:runtime=[c for c in runtime if re.search(args.only,c['id'])]
        def execute(check):
            if 'formRequirement' in check:
                check['execution']=preflight(root,model,check) or run_pure(root,model,check,work)
            elif not load_source(root,model,'::'.join(filter(None,(check['target']['namespace'],check['target']['module']))),check['target']['method']):
                check['execution']={'status':'FAIL','reasonCode':'required_method_missing',
                                    'message':'Не найден требуемый метод: '+json.dumps(check['target'],ensure_ascii=False)}
            else:
                check['execution']=run_pure(root,model,check,work)
            print(check['id'],check['execution']['status'],flush=True)
            return check
        with ThreadPoolExecutor(max_workers=2) as pool:runtime=list(pool.map(execute,runtime))
        write_json(work/'model.json',model)
        write_json(work/'assignment.json',dict(name='Программирование',checks=runtime))
        assessed=execute_engine('test',work/'model.json',work/'assignment.json',work)
        combined=static+assessed['checks']+trace_checks(runtime,config)
        unavailable=any(c['status'] not in {'PASS','FAIL'} for c in combined)
        passed=bool(runtime) and not args.only and all(c['status']=='PASS' for c in combined)
        status='passed' if passed else 'incomplete' if unavailable or args.only else 'failed'
        result=dict(status=status,sourceHash=original_hash,runtimeSourceHash=model['sourceHash'],partial=bool(args.only),
                    checks=combined,runtimeScore=assessed['score'],runtimeMaxScore=assessed['maxScore'],
                    counts={s:sum(c['status']==s for c in combined) for s in sorted({c['status'] for c in combined})})
        write_json(output/'result.json',result)
        write_json(output/'resolved-aliases.json',config)
        write_json(output/'runtime-evidence.json',runtime)
        write_json(output/'source-adaptations.json',transforms)
        (output/'report.html').write_text(html_report(result),encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False,indent=2))
        return 0 if passed else 2 if status=='incomplete' else 1


if __name__=='__main__':
    raise SystemExit(main())
