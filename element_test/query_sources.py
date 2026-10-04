"""Metadata-derived source schemas and Script loaders; never teacher query rows.

The reference inventory records wider platform contracts. This executor adapter
supports day/second histories, period totals and stored tabular/array members.
Unsupported platform-only sources fail before executing a student's method.
"""
from dataclasses import replace
from .query_plan import QueryField
from .resolution import qualified
from .yaml_io import UnsupportedSyntaxError

LEAF_KINDS = {'ordinary', 'slice-last', 'slice-first', 'register', 'balance',
              'turnover', 'balance-turnover', 'table-part', 'collection','users','constants','saved'}


def source_schema(c, name, member=''):
    if name=='Пользователи' and hasattr(c,'query_user_fields') and not c.resolve(name):
        if member:raise UnsupportedSyntaxError('Системный источник Пользователи не имеет табличных частей')
        return {'owner':name,'kind':'users','fields':tuple(QueryField(name,f['Имя'],f['Тип']) for f in c.query_user_fields),'dimensions':(),'resources':(),'period_type':'Дата','periodicity':None,'register_kind':''}
    matches = c.resolve(name)
    if len(matches) != 1:
        raise UnsupportedSyntaxError('Источник запроса отсутствует, неоднозначен или вне контракта: '+name)
    e = matches[0]; props = e['properties']; identity = qualified(e)
    kind = e['elementType']; periodicity = props.get('Периодичность', 'Непериодический')
    if kind=='ВиртуальнаяТаблица' and not member:
        from .query_plan import parse_storage_query
        from pathlib import Path
        from hashlib import sha256
        if props.get('Параметры') or not hasattr(c,'query_root'):
            raise UnsupportedSyntaxError('Сохранённый запрос требует доступный исходник и отдельный контракт внешних параметров')
        path=Path(e['sourceFile']).with_suffix('.xbql'); root=Path(c.query_root)
        if not (root/path).is_file():raise UnsupportedSyntaxError('Отсутствует исходник виртуальной таблицы: '+str(path))
        active=getattr(c,'active_query_sources',set())
        if identity in active:raise UnsupportedSyntaxError('Циклическая виртуальная таблица: '+identity)
        c.active_query_sources=active;active.add(identity)
        previous=c.namespace,c.imports;c.namespace,c.imports=e['namespace'],props.get('Импорт',[])
        try:
            text=(root/path).read_text(encoding='utf-8-sig');child=parse_storage_query(text,c)
            if child.parameters or child.fill or child.source_kind=='batch' or any(getattr(f,'sql_nullable',False) for f,_ in child.projections):
                raise UnsupportedSyntaxError('Сохранённый источник с параметрами/fill/NULL/pакетом вне текущего контракта')
        finally:c.namespace,c.imports=previous;active.remove(identity)
        fields=tuple(QueryField(identity,label,f.type) for f,label in child.projections)
        return {'owner':identity,'kind':'saved','fields':fields,'dimensions':(),'resources':(),'period_type':'Дата','periodicity':None,'register_kind':'','definition':child,'definition_source':{'file':str(path),'sourceHash':sha256((root/path).read_bytes()).hexdigest(),'range':[0,len(text)]}}
    period_type = {'День':'Дата', 'Секунда':'ДатаВремя'}.get(periodicity, 'ДатаВремя')
    register_kind = props.get('ВидРегистра', 'Остатки') if kind=='РегистрНакопления' else ''
    dimensions, resources = [], []
    source_kind = 'ordinary'
    declarations = list(props.get('Реквизиты', []))
    if kind in {'Справочник','Документ'}:
        if any(f['Имя']=='Ссылка' for f in declarations):
            raise UnsupportedSyntaxError('Обычный член Ссылка конфликтует с системной ссылкой')
        if member:
            tables = [t for t in props.get('ТабличныеЧасти',[]) if t['Имя']==member]
            attrs = [f for f in declarations if f['Имя']==member]
            if len(tables)==1 and not attrs:
                source_kind='table-part'; declarations=list(tables[0].get('Реквизиты',[]))
                declarations += [{'Имя':'НомерСтроки','Тип':'Число'}]
            elif len(attrs)==1 and not tables and attrs[0].get('Тип','').startswith('Массив<') and attrs[0]['Тип'].endswith('>'):
                source_kind='collection'; declarations=[{'Имя':'Элемент','Тип':attrs[0]['Тип'][7:-1]}]
            else:
                raise UnsupportedSyntaxError('Источник члена требует однозначную табличную часть или Массив: '+name+'.'+member)
            declarations += [{'Имя':'Владелец','Тип':identity+'.Ссылка'}, {'Имя':'Индекс','Тип':'Число'}]
        else:
            declarations += [{'Имя':'Ссылка','Тип':identity+'.Ссылка'}]
    elif kind in {'РегистрСведений','РегистрНакопления'}:
        source_kind='register'; dimensions=list(props.get('Измерения',[])); resources=list(props.get('Ресурсы',[]))
        if kind=='РегистрСведений':
            if member and member not in {'СрезПоследних','СрезПервых'}:
                raise UnsupportedSyntaxError('Источник регистра сведений вне executor-контракта: '+member)
            if member:
                if periodicity not in {'День','Секунда'}:
                    raise UnsupportedSyntaxError('Срез поддерживает День/Секунда; иная периодичность требует отдельного контракта')
                source_kind='slice-last' if member=='СрезПоследних' else 'slice-first'
            declarations=dimensions+resources+declarations
            if periodicity!='Непериодический':
                if periodicity not in {'День','Секунда'}:
                    raise UnsupportedSyntaxError('Периодичность источника вне executor-контракта')
                declarations=[{'Имя':'Период','Тип':period_type}]+declarations
        else:
            period_type='ДатаВремя'; periodicity=None
            if member:
                mapping={'Остатки':'balance','Обороты':'turnover','ОстаткиИОбороты':'balance-turnover'}
                if member not in mapping:
                    raise UnsupportedSyntaxError('Изменения требуют план обмена и регистрацию изменений платформой')
                source_kind=mapping[member]
                if source_kind!='turnover' and register_kind!='Остатки':
                    raise UnsupportedSyntaxError('Остатки доступны только регистру вида Остатки')
                if any(f.get('Тип')!='Число' for f in resources):
                    raise UnsupportedSyntaxError('Итоги требуют числовые ресурсы')
                suffixes = ['Остаток','ОстатокПоложительный','ОстатокОтрицательный'] if source_kind=='balance' else ['Оборот']
                if source_kind!='balance' and register_kind=='Остатки': suffixes+=['Приход','Расход']
                if source_kind=='balance-turnover': suffixes += [p+s for p in ('НачальныйОстаток','КонечныйОстаток') for s in ('','Положительный','Отрицательный')]
                declarations=dimensions+[{'Имя':f['Имя']+s,'Тип':'Число'} for f in resources for s in suffixes]
            else:
                declarations=[{'Имя':'Период','Тип':'ДатаВремя'}, {'Имя':'Активность','Тип':'Булево'}]+dimensions+resources+declarations
                declarations += [{'Имя':'Индекс','Тип':'Число'},{'Имя':'НомерСтроки','Тип':'Число'}]
                if register_kind=='Остатки':declarations += [{'Имя':'ВидЗаписи','Тип':'ВидЗаписиРегистраНакопления'}]
    elif kind=='НаборКонстант' and not member and periodicity=='Непериодический':
        source_kind='constants';declarations=list(props.get('Константы',[]))
    elif kind=='КонтрактСущности':
        raise UnsupportedSyntaxError('Таблица контракта сущности удалена из платформы после 7.0 (справка 9.3)')
    else:
        raise UnsupportedSyntaxError('Источник '+kind+' требует отдельный native/platform storage-контракт')
    previous=c.namespace,c.imports; c.namespace,c.imports=e['namespace'],props.get('Импорт',[])
    try:
        fields=[]; names=set()
        for f in declarations:
            if f['Имя'] in names:raise UnsupportedSyntaxError('Повторяющееся поле источника запроса: '+f['Имя'])
            names.add(f['Имя'])
            typ=f.get('Тип','Строка' if f['Имя']=='Наименование' and kind=='Справочник' else '')
            fields.append(QueryField(identity,f['Имя'],c.canonical_type(typ),f['Имя'] in {'Ссылка','Владелец','Индекс','НомерСтроки','Период'}))
        dims=tuple(QueryField(identity,f['Имя'],c.canonical_type(f['Тип'])) for f in dimensions)
        res=tuple(QueryField(identity,f['Имя'],c.canonical_type(f['Тип'])) for f in resources)
        if source_kind in {'slice-last','slice-first'} and any('|' in f.type for f in dims):
            raise UnsupportedSyntaxError('Union-измерение среза требует отдельного типизированного контракта')
    finally:c.namespace,c.imports=previous
    return {'owner':identity,'kind':source_kind,'fields':tuple(fields),'dimensions':dims,'resources':res,
            'period_type':period_type,'periodicity':periodicity if kind=='РегистрСведений' else None,'register_kind':register_kind}


def generate_source_query(q,c):
    """Reuse common filtering/projection/sort renderer after a typed Script load."""
    from .storage_queries import generate_query, query_name, unique_parameters
    name=query_name(q)
    if name in c.definitions:return name
    plain=replace(q, source_kind='ordinary',period_slot=None,end_slot=None)
    existed=query_name(plain) in c.definitions
    other=generate_query(plain,c); text=c.definitions[other].replace(other,name)
    dependencies=c.method_dependencies[other]
    if not existed:del c.definitions[other];del c.method_dependencies[other]
    lit=lambda v:c.literal(v,'Строка')
    required={f.name:f for f,_ in q.projections+q.predicates+q.ordering}
    fields=''.join('    пер '+f.name+': '+c.sbsl_type(f.type)+'\n' for f in required.values())
    a=text.index('структура Данные\n')+len('структура Данные\n'); b=text.index(';\n',a)
    text=text[:a]+fields+text[b:]
    read=('        знч Состояние = ТестСессия.ЧитатьВсе()\n'
          '        знч Исходные = новый Массив<Соответствие<Строка, Объект?>>()\n'
          '        если Состояние.СодержитКлюч('+lit(q.owner)+')\n'
          '            для JSON из Состояние['+lit(q.owner)+'].Значения()\n'
          '                знч Значение: Объект? = СериализацияJson.ПрочитатьОбъект(JSON)\n')
    if q.source_kind in {'users','constants','saved'}:
        if q.source_kind=='users':value='ТестСистемныеИсточники.Пользователи()';dependencies=dependencies+['ТестСистемныеИсточники.Пользователь']
        elif q.source_kind=='saved':
            child=generate_query(q.definition,c);value=child+'.Создать().Выполнить()';dependencies=dependencies+[child+'.Запрос']
        else:
            state=c.query_constants[q.owner];value='['+state['alias']+'.Получить()]';dependencies=dependencies+[state['type']]
        read='        знч Исходные = новый Массив<Соответствие<Строка, Объект?>>()\n        для Запись из '+value+'\n            знч Значение: Объект? = СериализацияJson.ПрочитатьОбъект(СериализацияJson.ЗаписатьОбъект(Запись))\n            Исходные.Добавить(Значение как Соответствие<Строка, Объект?>)\n        ;\n'
    elif q.source_kind in {'table-part','collection'}:
        read+='                знч ОбъектВладельца = Значение как Соответствие<Строка, Объект?>\n'
        read+='                пер Индекс = 0\n                для Элемент из (ОбъектВладельца['+lit(q.member)+'] как Массив<Объект?>)\n'
        if q.source_kind=='table-part':read+='                    знч Снимок = Элемент как Соответствие<Строка, Объект?>\n                    Снимок.Вставить("НомерСтроки", Индекс + 1)\n'
        else:read+='                    знч Снимок = новый Соответствие<Строка, Объект?>()\n                    Снимок.Вставить("Элемент", Элемент)\n'
        read+='                    Снимок.Вставить("Владелец", ОбъектВладельца["Ссылка"])\n                    Снимок.Вставить("Индекс", Индекс)\n                    Индекс += 1\n                    Исходные.Добавить(Снимок)\n                ;\n'
    else:
        read+='                пер Индекс = 0\n                для Элемент из (Значение как Массив<Объект?>)\n                    знч Снимок = Элемент как Соответствие<Строка, Объект?>\n'
        if q.register_kind:read+='                    Снимок.Вставить("Индекс", Индекс)\n                    Снимок.Вставить("НомерСтроки", Индекс + 1)\n'
        read+='                    Индекс += 1\n                    Исходные.Добавить(Снимок)\n                ;\n'
    if q.source_kind not in {'users','constants','saved'}:read+='            ;\n        ;\n'
    if q.source_kind in {'balance','turnover','balance-turnover'}:
        read+=totals_loader(q,c,required)
    else:
        projected='{'+', '.join(lit(f.name)+': Снимок['+lit(f.name)+']' for f in required.values())+'}'
        read+='        для Снимок из Исходные\n            знч Проекция = СериализацияJson.ЗаписатьОбъект('+projected+')\n            знч СтрокаДанных = СериализацияJson.ПрочитатьОбъект<Данные>(Проекция, новый Данные().ПолучитьТип())\n            Все.Добавить(СтрокаДанных)\n        ;\n'
    # Replace only the loading/filtering loops; keep the common output renderer.
    a=text.index('        знч Состояние ='); b=text.index('                если ',a)
    text=text[:a]+'        знч Все = новый Массив<Данные>()\n'+read+'        для СтрокаДанных из Все\n'+text[b:]
    text=text.replace('                ;\n            ;\n        ;\n        знч Результат', '                ;\n        ;\n        знч Результат',1)
    c.definitions[name]=text;c.method_dependencies[name]=dependencies
    return name


def totals_loader(q,c,required):
    lit=lambda v:c.literal(v,'Строка')
    key='{'+', '.join(lit(f.name)+': Снимок['+lit(f.name)+']' for f in q.dimensions)+'}'
    period='новый ДатаВремя(Снимок["Период"] как Строка)'
    start='П'+str(q.period_slot) if q.period_slot is not None else 'Неопределено'
    end='П'+str(q.end_slot) if q.end_slot is not None else 'Неопределено'
    # Constructor fields for optional bounds already exist in the common renderer.
    s='        знч Группы = новый Соответствие<Строка, Соответствие<Строка, Объект?>>()\n        для Снимок из Исходные\n            если не (Снимок["Активность"] как Булево)\n                продолжить\n            ;\n            знч Период = '+period+'\n'
    if q.source_kind=='balance':
        s+='            если '+start+' != Неопределено и Период >= ('+start+' как ДатаВремя)\n                продолжить\n            ;\n' if q.period_slot is not None else ''
    else:
        s+='            если '+end+' != Неопределено и Период > ('+end+' как ДатаВремя)\n                продолжить\n            ;\n' if q.end_slot is not None else ''
        if q.source_kind=='turnover' and q.period_slot is not None:s+='            если '+start+' != Неопределено и Период < ('+start+' как ДатаВремя)\n                продолжить\n            ;\n'
    s+='            знч Ключ = СериализацияJson.ЗаписатьОбъект('+key+')\n            если не Группы.СодержитКлюч(Ключ)\n                знч Новая = новый Соответствие<Строка, Объект?>()\n'
    for f in q.dimensions:s+='                Новая.Вставить('+lit(f.name)+', Снимок['+lit(f.name)+'])\n'
    for f in q.fields:
        if f not in q.dimensions:s+='                Новая.Вставить('+lit(f.name)+', 0)\n'
    s+='                Группы.Вставить(Ключ, Новая)\n            ;\n            знч Группа = Группы[Ключ]\n'
    signed='(Снимок["ВидЗаписи"] как Строка) == "Приход"' if q.register_kind=='Остатки' else 'Истина'
    if q.source_kind=='balance-turnover':
        s+='            знч ДоНачала = '+(start+' != Неопределено и Период < ('+start+' как ДатаВремя)' if q.period_slot is not None else 'Ложь')+'\n'
    for r in q.resources:
        value='(Снимок['+lit(r.name)+'] как Число)';delta='('+signed+' ? '+value+' : -'+value+')'
        def add(suffix,expr):return '            Группа['+lit(r.name+suffix)+'] = (Группа['+lit(r.name+suffix)+'] как Число) + '+expr+'\n'
        if q.source_kind=='balance':s+=add('Остаток',delta)
        else:
            if q.source_kind=='balance-turnover':
                s+=add('КонечныйОстаток',delta)+'            если ДоНачала\n'+add('НачальныйОстаток',delta)+'            иначе\n'
            s+=add('Оборот',delta)
            if q.register_kind=='Остатки':s+=add('Приход','('+signed+' ? '+value+' : 0)')+add('Расход','('+signed+' ? 0 : '+value+')')
            if q.source_kind=='balance-turnover':s+='            ;\n'
    s+='        ;\n        для Снимок из Группы.Значения()\n'
    bases=['Остаток'] if q.source_kind=='balance' else ['НачальныйОстаток','КонечныйОстаток'] if q.source_kind=='balance-turnover' else []
    for r in q.resources:
        for base in bases:
            value='(Снимок['+lit(r.name+base)+'] как Число)'
            s+='            Снимок['+lit(r.name+base+'Положительный')+'] = '+value+' > 0 ? '+value+' : 0\n'
            s+='            Снимок['+lit(r.name+base+'Отрицательный')+'] = '+value+' < 0 ? -'+value+' : 0\n'
    if q.source_kind=='balance':
        s+='            если '+(' и '.join('(Снимок['+lit(r.name+'Остаток')+'] как Число) == 0' for r in q.resources) or 'Истина')+'\n                продолжить\n            ;\n'
    projected='{'+', '.join(lit(f.name)+': Снимок['+lit(f.name)+']' for f in required.values())+'}'
    return s+'            знч Проекция = СериализацияJson.ЗаписатьОбъект('+projected+')\n            Все.Добавить(СериализацияJson.ПрочитатьОбъект<Данные>(Проекция, новый Данные().ПолучитьТип()))\n        ;\n'
