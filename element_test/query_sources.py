"""Metadata-derived source schemas and Script loaders; never teacher query rows.

The reference inventory records wider platform contracts. This executor adapter
supports typed histories, dimension filters, calendar totals, parameterized saved
sources and stored tabular/array members.
Unsupported platform-only sources fail before executing a student's method.
"""
from dataclasses import replace
from .query_plan import QueryField
from .resolution import qualified
from .yaml_io import UnsupportedSyntaxError

LEAF_KINDS = {'ordinary', 'slice-last', 'slice-first', 'register', 'balance',
              'turnover', 'balance-turnover', 'table-part', 'collection','users','constants','saved','custom-collection'}
CALENDAR_PERIODS = ('Год', 'Полугодие', 'Квартал', 'Месяц', 'Декада', 'Неделя',
                    'День', 'Час', 'Минута', 'Секунда')
HISTORY_PERIOD_TYPES = {'День':'Дата', 'Секунда':'ДатаВремя', 'Момент':'Момент'}
PERIOD_SUFFIXES = {'Год':'Года','Полугодие':'Полугодия','Квартал':'Квартала','Месяц':'Месяца',
                   'Декада':'Декады','Неделя':'Недели','День':'Дня','Час':'Часа','Минута':'Минуты','Секунда':'Секунды'}


def source_arguments(tokens, pos):
    """Split positional arguments without losing original parameter ranges."""
    args, current, depth = [], [], 0
    while pos < len(tokens):
        t = tokens[pos]; pos += 1
        if t[0] == 'token':
            if t[1] == ')' and depth == 0:
                if current or args: args.append(tuple(current))
                return args, pos
            if t[1] == ',' and depth == 0:
                args.append(tuple(current)); current = []; continue
            depth += (t[1] == '(') - (t[1] == ')')
        current.append(t)
    raise UnsupportedSyntaxError('Незакрытые параметры виртуального источника')


def enum_argument(tokens, owner, values, default):
    if not tokens: return default
    if (len(tokens) != 3 or tokens[0][:2] != ('token', owner)
            or tokens[1][:2] != ('token', '.') or tokens[2][1] not in values):
        raise UnsupportedSyntaxError('Параметр источника требует '+owner+': '+', '.join(values))
    return tokens[2][1]


def virtual_arguments(kind, args):
    count = 2 if kind in {'slice-first','slice-last','balance'} else 4 if kind=='turnover' else 5
    if len(args) > count: raise UnsupportedSyntaxError('Лишние параметры виртуального источника')
    args = list(args) + [()] * (count-len(args))
    def bound(tokens):
        if not tokens or (len(tokens)==1 and tokens[0][:2]==('token','Неопределено')): return None
        if len(tokens)!=1 or tokens[0][0]!='parameter':
            raise UnsupportedSyntaxError('Граница виртуальной таблицы требует captured параметр')
        return tokens[0]
    start = bound(args[0]); end = None; periodicity = 'Период'; complement = 'ЗаписиИГраницыПериода'
    if kind in {'turnover','balance-turnover'}:
        end = bound(args[1])
        periodicity = enum_argument(args[2], 'ПериодичностьИтоговРегистраНакопления',
                                    ('Авто','Период',*CALENDAR_PERIODS,'Регистратор','Запись'), 'Период')
        if kind=='balance-turnover':
            complement = enum_argument(args[3], 'СпособДополненияПериодовРегистраНакопления',
                                        ('Записи','ЗаписиИГраницыПериода'), complement)
    return start, end, periodicity, complement, args[-1]


def bind_source_filter(text, tokens, c, schema, alias, parameter):
    """Bind an ordinary scalar predicate against declared dimensions only."""
    from types import SimpleNamespace
    from .query_projections import walk
    from .query_composites import NestedParser
    p = NestedParser(text, c, {}); p.tokens = list(tokens)
    p.sources = [SimpleNamespace(alias=alias, fields=schema['dimensions'], source_kind='register')]
    p.fields, p.used = [{}], [{}]
    expression = p.bind(p.condition(), 1)
    if p.pos != len(tokens) or expression.type != 'Булево':
        raise UnsupportedSyntaxError('Фильтр источника требует выражение Булево по измерениям')
    forbidden = {'aggregate','in-query','exists-query','hierarchy'}
    if any(e.kind in forbidden for e in walk(expression)):
        raise UnsupportedSyntaxError('Фильтр источника требует скалярное выражение без агрегатов/подзапросов')
    slots = {(a.start,a.end):parameter(('parameter',a.expression,a.start,a.end),a.type) for a in p.parameters}
    def capture(e):
        return replace(e, value=slots[e.range]) if e.kind=='parameter' else replace(e,children=tuple(capture(x) for x in e.children))
    return capture(expression)


def filter_fields(q):
    from .query_projections import walk
    names = {e.name for e in walk(q.source_filter) if e.kind=='field'} if q.source_filter else set()
    return [f for f in q.dimensions if f.name in names]


def filter_condition(q, c, snapshot=False, helpers=None):
    from .query_projections import ExpressionRenderer
    class SourceRenderer(ExpressionRenderer):
        def null(self, e, row='С', group='Группа'):
            return 'Ложь' if e.kind=='field' else super().null(e,row,group)
        def value(self, e, row='С', group='Группа'):
            if e.kind=='field':
                if snapshot:
                    value='СериализацияJson.ПрочитатьОбъект<'+c.sbsl_type(e.type)+'>(СериализацияJson.ЗаписатьОбъект(Снимок['+c.literal(e.name,'Строка')+']), Тип<'+c.sbsl_type(e.type)+'>)'
                    return '('+value+')'
                return 'СтрокаДанных.'+e.name
            return super().value(e,row,group)
    renderer=SourceRenderer(q,c,())
    condition=renderer.tri(q.source_filter)+' == 1'
    if helpers is not None:helpers.update(renderer.helpers)
    return condition


def source_schema(c, name, member='', totals_periodicity='Период'):
    if name in getattr(c,'custom_query_sources',{}):
        if member:raise UnsupportedSyntaxError('Пользовательский источник не имеет виртуальных членов')
        return c.custom_query_sources[name]
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
        if not hasattr(c,'query_root'):
            raise UnsupportedSyntaxError('Сохранённый запрос требует доступный исходник')
        path=Path(e['sourceFile']).with_suffix('.xbql'); root=Path(c.query_root)
        if not (root/path).is_file():raise UnsupportedSyntaxError('Отсутствует исходник виртуальной таблицы: '+str(path))
        active=getattr(c,'active_query_sources',set())
        if identity in active:raise UnsupportedSyntaxError('Циклическая виртуальная таблица: '+identity)
        c.active_query_sources=active;active.add(identity)
        previous=c.namespace,c.imports;c.namespace,c.imports=e['namespace'],props.get('Импорт',[])
        try:
            from .query_api import normalized_text
            from .storage_queries import unique_parameters
            text=(root/path).read_text(encoding='utf-8-sig');child=parse_storage_query(normalized_text(text),c)
            parameters=[{'Имя':p['Имя'],'Тип':c.canonical_type(p['Тип'])} for p in props.get('Параметры',[])]
            if len({p['Имя'] for p in parameters}) != len(parameters):
                raise UnsupportedSyntaxError('Повторное имя параметра сохранённого источника')
            declared={p['Имя']:p['Тип'] for p in parameters}
            for p in unique_parameters(child):
                if p.expression not in declared or p.type.rstrip('?')!=declared[p.expression].rstrip('?') or declared[p.expression].endswith('?') and not p.type.endswith('?'):
                    raise UnsupportedSyntaxError('Параметр сохранённого запроса не соответствует объявлению: '+p.expression)
            if child.fill or child.source_kind=='batch':
                raise UnsupportedSyntaxError('Сохранённый источник с fill/пакетом вне текущего контракта')
        finally:c.namespace,c.imports=previous;active.remove(identity)
        fields=tuple(QueryField(identity,label,f.type,sql_nullable=getattr(f,'sql_nullable',False)) for f,label in child.projections)
        return {'owner':identity,'kind':'saved','fields':fields,'dimensions':(),'resources':(),'period_type':'Дата','periodicity':None,'register_kind':'','definition':child,'parameters':parameters,'definition_source':{'file':str(path),'sourceHash':sha256((root/path).read_bytes()).hexdigest(),'range':[0,len(text)]}}
    period_type = HISTORY_PERIOD_TYPES.get(periodicity, 'ДатаВремя')
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
            if props.get('Иерархический') is True and not any(f['Имя']=='Родитель' for f in declarations):
                declarations.append({'Имя':'Родитель','Тип':identity+'.Ссылка?'})
    elif kind in {'РегистрСведений','РегистрНакопления'}:
        source_kind='register'; dimensions=list(props.get('Измерения',[])); resources=[{'Тип':'Число',**f} for f in props.get('Ресурсы',[])]
        if kind=='РегистрСведений':
            if member and member not in {'СрезПоследних','СрезПервых'}:
                raise UnsupportedSyntaxError('Источник регистра сведений вне executor-контракта: '+member)
            if member:
                if periodicity not in HISTORY_PERIOD_TYPES:
                    raise UnsupportedSyntaxError('Неизвестная периодичность среза')
                source_kind='slice-last' if member=='СрезПоследних' else 'slice-first'
            declarations=dimensions+resources+declarations
            if periodicity!='Непериодический':
                if periodicity not in HISTORY_PERIOD_TYPES:
                    raise UnsupportedSyntaxError('Периодичность источника вне executor-контракта')
                declarations=[{'Имя':'Период','Тип':period_type}]+declarations
                if member:
                    declarations += [{'Имя':'СледующийПериод','Тип':period_type}]
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
                if source_kind != 'balance':
                    if totals_periodicity == 'Авто':
                        declarations += [{'Имя':'Период'+p,'Тип':'ДатаВремя'} for p in (*CALENDAR_PERIODS,'Записи')]
                    elif totals_periodicity != 'Период':
                        declarations += [{'Имя':'Период','Тип':'ДатаВремя'}]
                    if totals_periodicity in {'Авто','Регистратор','Запись'}:
                        registrar = [f for f in props.get('Реквизиты',[]) if f['Имя']=='Регистратор']
                        if len(registrar)!=1 or totals_periodicity!='Авто' and '|' in registrar[0].get('Тип',''):
                            raise UnsupportedSyntaxError('Разворот по регистратору требует однозначный тип ссылки')
                        declarations += registrar
                    if totals_periodicity in {'Авто','Запись'}:
                        declarations += [{'Имя':'Индекс','Тип':'Число'},{'Имя':'НомерСтроки','Тип':'Число'}]
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
    if q.source_kind=='saved':return generate_saved_query(q,c)
    name=query_name(q)
    if name in c.definitions:return name
    plain=replace(q, source_kind='ordinary',period_slot=None,end_slot=None,source_filter=None)
    existed=query_name(plain) in c.definitions
    other=generate_query(plain,c); text=c.definitions[other].replace(other,name)
    dependencies=c.method_dependencies[other]
    if not existed:del c.definitions[other];del c.method_dependencies[other]
    lit=lambda v:c.literal(v,'Строка')
    helpers={}
    required={f.name:f for f,_ in q.projections+q.predicates+q.ordering}
    fields=''.join('    пер '+f.name+': '+c.sbsl_type(f.type)+'\n' for f in required.values())
    a=text.index('структура Данные\n')+len('структура Данные\n'); b=text.index(';\n',a)
    text=text[:a]+fields+text[b:]
    read=('        знч Состояние = ТестСессия.ЧитатьВсе()\n'
          '        знч Исходные = новый Массив<Соответствие<Строка, Объект?>>()\n'
          '        если Состояние.СодержитКлюч('+lit(q.owner)+')\n'
          '            для JSON из Состояние['+lit(q.owner)+'].Значения()\n'
          '                знч Значение: Объект? = СериализацияJson.ПрочитатьОбъект(JSON)\n')
    if q.source_kind in {'users','constants','saved','custom-collection'}:
        if q.source_kind=='custom-collection':
            p=next(p for p in q.parameters if p.expression=='__Источник_'+q.owner.removeprefix('&'))
            value='П'+str(p.slot);dependencies=dependencies+[p.type]
        elif q.source_kind=='users':value='ТестСистемныеИсточники.Пользователи()';dependencies=dependencies+['ТестСистемныеИсточники.Пользователь']
        elif q.source_kind=='saved':
            child=generate_query(q.definition,c);value=child+'.Создать().Выполнить()';dependencies=dependencies+[child+'.Запрос']
            value=child+'.Создать('+', '.join('П'+str(slot) for slot in q.definition_slots)+').Выполнить()'
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
    if q.source_kind not in {'users','constants','saved','custom-collection'}:read+='            ;\n        ;\n'
    if q.source_filter:
        read += ('        знч Отфильтрованные = новый Массив<Соответствие<Строка, Объект?>>()\n'
                 '        для Снимок из Исходные\n            если '+filter_condition(q,c,snapshot=True,helpers=helpers)+'\n'
                 '                Отфильтрованные.Добавить(Снимок)\n            ;\n        ;\n')
        read = read.replace('знч Исходные =', 'пер Исходные =')
        read += '        Исходные = Отфильтрованные\n'
    if q.source_kind in {'balance','turnover','balance-turnover'}:
        expanded=q.totals_periodicity!='Период' and (q.totals_periodicity!='Авто' or any(f.startswith('Период') or f in {'Регистратор','Индекс','НомерСтроки'} for f in required))
        read+=period_totals_loader(q,c,required) if expanded else totals_loader(q,c,required)
    else:
        projected='{'+', '.join(lit(f.name)+': Снимок['+lit(f.name)+']' for f in required.values())+'}'
        read+='        для Снимок из Исходные\n            знч Проекция = СериализацияJson.ЗаписатьОбъект('+projected+')\n            знч СтрокаДанных = СериализацияJson.ПрочитатьОбъект<Данные>(Проекция, новый Данные().ПолучитьТип())\n            Все.Добавить(СтрокаДанных)\n        ;\n'
    # Replace only the loading/filtering loops; keep the common output renderer.
    a=text.index('        знч Состояние ='); b=text.index('                если ',a)
    text=text[:a]+'        знч Все = новый Массив<Данные>()\n'+read+'        для СтрокаДанных из Все\n'+text[b:]
    text=text.replace('                ;\n            ;\n        ;\n        знч Результат', '                ;\n        ;\n        знч Результат',1)
    text+=''.join(body for _,body in helpers.values())
    if 'ТестШаблоны.' in text:dependencies=dependencies+['ТестШаблоны.Совпадает']
    if 'ТестПолноеСовпадение.' in text:dependencies=dependencies+['ТестПолноеСовпадение.Совпадает']
    c.definitions[name]=text;c.method_dependencies[name]=dependencies
    return name


def generate_saved_query(q,c):
    """Keep SQL NULL flags across a saved definition, including nested sources."""
    from .storage_queries import generate_query, query_name, unique_parameters
    from .query_composites import declaration, public_result, internal, null_column
    name=query_name(q)
    if name in c.definitions:return name
    child=generate_query(q.definition,c)
    parameters=unique_parameters(q)
    text=declaration('СтрокаДанных',q.projections,c,flags=True)
    public=public_result(q,c);end=public.index('    @Глобально')
    text+=public[:end]+'@Глобально\nструктура Запрос\n    знч Источник: '+child+'.Запрос\n'
    text+=public[end:]+'    @Глобально\n    метод ВыполнитьВнутренне(): Массив<СтрокаДанных>\n        знч Результат = новый Массив<СтрокаДанных>()\n        для С из Источник.'+('ВыполнитьВнутренне' if internal(q.definition) else 'Выполнить')+'()\n'
    args=[]
    for i,(f,label) in enumerate(q.projections):
        index=next(j for j,(_,l) in enumerate(q.definition.projections) if l==f.name)
        flag='С.'+null_column(q.definition.projections,index) if internal(q.definition) else 'Ложь'
        args += [label+' = С.'+f.name, null_column(q.projections,i)+' = '+flag]
    text+='            Результат.Добавить(новый СтрокаДанных('+', '.join(args)+'))\n        ;\n        возврат Результат\n    ;\n;\n'
    sig=', '.join('П'+str(p.slot)+': '+c.sbsl_type(p.type) for p in parameters)
    mapped=', '.join('П'+str(slot) for slot in q.definition_slots)
    text+='@Глобально\nметод Создать('+sig+'): Запрос\n    возврат новый Запрос(Источник = '+child+'.Создать('+mapped+'))\n;\n'
    c.definitions[name]=text;c.method_dependencies[name]=[child+'.Запрос']+[f.type for f,_ in q.projections]
    return name


def bucket(value, periodicity):
    if periodicity in CALENDAR_PERIODS:
        return value+'.Начало'+PERIOD_SUFFIXES[periodicity]+'('+('ДеньНедели.Понедельник' if periodicity=='Неделя' else '')+')'
    return value


def period_totals_loader(q,c,required):
    """Build buckets and independently calculate opening/closing from history.

    Calendar groups retain dimensions even when SELECT omits them. Auto groups
    use only requested period fields; dimensions remain implicit in every case.
    All computations below execute in Script against the live storage session.
    """
    lit=lambda v:c.literal(v,'Строка')
    combined=q.source_kind=='balance-turnover'
    extra=[]
    if q.totals_periodicity=='Авто':
        extra=[f.name for f in required.values() if f.name.startswith('Период') or f.name in {'Регистратор','Индекс','НомерСтроки'}]
    else:
        extra=['Период']
        if q.totals_periodicity in {'Регистратор','Запись'}:extra+=['Регистратор']
        if q.totals_periodicity=='Запись':extra+=['Индекс','НомерСтроки']
    if combined and (q.totals_periodicity in {'Регистратор','Запись'} or any(f in extra for f in ('Регистратор','Индекс','НомерСтроки','ПериодЗаписи'))):
        raise UnsupportedSyntaxError('ОстаткиИОбороты по записям/регистраторам требуют native порядок движений и nullable границы')
    periods=[q.totals_periodicity] if q.totals_periodicity in CALENDAR_PERIODS else [f[6:] for f in extra if f.startswith('Период') and f[6:] in CALENDAR_PERIODS]
    finest=next((p for p in reversed(CALENDAR_PERIODS) if p in periods),None)
    start='П'+str(q.period_slot) if q.period_slot is not None else 'Неопределено'
    end='П'+str(q.end_slot) if q.end_slot is not None else 'Неопределено'
    group_fields=[f.name for f in q.dimensions]+extra
    def key(row):return 'СериализацияJson.ЗаписатьОбъект({'+', '.join(lit(f)+': '+row+'['+lit(f)+']' for f in group_fields)+'})'
    def dates(row,value):
        out=''
        for f in extra:
            if f.startswith('Период'):
                p=q.totals_periodicity if f=='Период' else f[6:]
                out+='            '+row+'.Вставить('+lit(f)+', '+bucket(value,p)+'.ВСтроку())\n'
        return out
    s='        знч Группы = новый Соответствие<Строка, Соответствие<Строка, Объект?>>()\n'
    s+='        для Снимок из Исходные\n            если не (Снимок["Активность"] как Булево)\n                продолжить\n            ;\n            знч Период = новый ДатаВремя(Снимок["Период"] как Строка)\n'
    if q.period_slot is not None and q.end_slot is not None:
        s+='            если '+start+' != Неопределено и '+end+' != Неопределено и ('+start+' как ДатаВремя) > ('+end+' как ДатаВремя)\n                продолжить\n            ;\n'
    if q.end_slot is not None:s+='            если '+end+' != Неопределено и Период > ('+end+' как ДатаВремя)\n                продолжить\n            ;\n'
    # Candidate periods come from interval movements. Boundary periods may also
    # be introduced for dimensions with history before the beginning.
    if combined and q.complement=='ЗаписиИГраницыПериода' and finest:
        for bound in (start,end):
            if bound!='Неопределено':
                s+='            если '+bound+' != Неопределено\n                знч Граница = новый Соответствие<Строка, Объект?>()\n'
                for f in q.dimensions:s+='                Граница.Вставить('+lit(f.name)+', Снимок['+lit(f.name)+'])\n'
                s+=dates('Граница','('+bound+' как ДатаВремя)')
                s+='                Группы.Вставить('+key('Граница')+', Граница)\n            ;\n'
    if q.period_slot is not None:s+='            если '+start+' != Неопределено и Период < ('+start+' как ДатаВремя)\n                продолжить\n            ;\n'
    s+='            знч Новая = новый Соответствие<Строка, Объект?>()\n'
    for f in q.dimensions:s+='            Новая.Вставить('+lit(f.name)+', Снимок['+lit(f.name)+'])\n'
    s+=dates('Новая','Период')
    for f in extra:
        if not f.startswith('Период'):s+='            Новая.Вставить('+lit(f)+', Снимок['+lit(f)+'])\n'
    s+='            Группы.Вставить('+key('Новая')+', Новая)\n        ;\n'
    s+='        для Снимок из Группы.Значения()\n'
    for f in q.fields:
        if f.name not in group_fields:s+='            Снимок.Вставить('+lit(f.name)+', 0)\n'
    # Calendar bounds are clipped to the user interval. No intermediate empty
    # periods are fabricated: only movements and requested boundary buckets.
    if finest:
        field='Период' if q.totals_periodicity!='Авто' else 'Период'+finest
        opening='новый ДатаВремя(Снимок['+lit(field)+'] как Строка)'
        suffix=PERIOD_SUFFIXES[finest]
        closing='Начало.Конец'+suffix+'('+('ДеньНедели.Понедельник' if finest=='Неделя' else '')+')'
        s+='            пер Начало = '+opening+'\n            пер Конец = '+closing+'\n'
        for index,p in enumerate(periods):
            if p==finest:continue
            value='новый ДатаВремя(Снимок['+lit('Период'+p)+'] как Строка)'
            a,b='НачалоРазворота'+str(index),'КонецРазворота'+str(index)
            finish=a+'.Конец'+PERIOD_SUFFIXES[p]+'('+('ДеньНедели.Понедельник' if p=='Неделя' else '')+')'
            s+='            знч '+a+' = '+value+'\n            знч '+b+' = '+finish+'\n'
            s+='            если '+a+' > Начало\n                Начало = '+a+'\n            ;\n            если '+b+' < Конец\n                Конец = '+b+'\n            ;\n'
        if q.period_slot is not None:s+='            если '+start+' != Неопределено и Начало < ('+start+' как ДатаВремя)\n                Начало = '+start+' как ДатаВремя\n            ;\n'
        if q.end_slot is not None:s+='            если '+end+' != Неопределено и Конец > ('+end+' как ДатаВремя)\n                Конец = '+end+' как ДатаВремя\n            ;\n'
    s+='            для Движение из Исходные\n                если не (Движение["Активность"] как Булево)\n                    продолжить\n                ;\n'
    equal=' и '.join('СериализацияJson.ЗаписатьОбъект(Движение['+lit(f.name)+']) == СериализацияJson.ЗаписатьОбъект(Снимок['+lit(f.name)+'])' for f in q.dimensions) or 'Истина'
    s+='                если не ('+equal+')\n                    продолжить\n                ;\n                знч ПериодДвижения = новый ДатаВремя(Движение["Период"] как Строка)\n'
    if finest:
        s+='                если ПериодДвижения > Конец\n                    продолжить\n                ;\n                знч ДоНачала = ПериодДвижения < Начало\n'
    else:
        if q.end_slot is not None:s+='                если '+end+' != Неопределено и ПериодДвижения > ('+end+' как ДатаВремя)\n                    продолжить\n                ;\n'
        s+='                знч ДоНачала = '+(start+' != Неопределено и ПериодДвижения < ('+start+' как ДатаВремя)' if q.period_slot is not None else 'Ложь')+'\n'
    if not combined:
        s+='                если ДоНачала\n                    продолжить\n                ;\n'
        for f in extra:
            p=q.totals_periodicity if f=='Период' else f[6:] if f.startswith('Период') else None
            val=bucket('ПериодДвижения',p)+'.ВСтроку()' if p else 'Движение['+lit(f)+']'
            s+='                если СериализацияJson.ЗаписатьОбъект('+val+') != СериализацияJson.ЗаписатьОбъект(Снимок['+lit(f)+'])\n                    продолжить\n                ;\n'
    signed='(Движение["ВидЗаписи"] как Строка) == "Приход"' if q.register_kind=='Остатки' else 'Истина'
    def add(r,suffix,value):return '                Снимок['+lit(r.name+suffix)+'] = (Снимок['+lit(r.name+suffix)+'] как Число) + '+value+'\n'
    for r in q.resources:
        value='(Движение['+lit(r.name)+'] как Число)';delta='('+signed+' ? '+value+' : -'+value+')'
        if combined:
            s+=add(r,'КонечныйОстаток',delta)+'                если ДоНачала\n'+add(r,'НачальныйОстаток',delta)+'                иначе\n'
        s+=add(r,'Оборот',delta)
        if q.register_kind=='Остатки':s+=add(r,'Приход','('+signed+' ? '+value+' : 0)')+add(r,'Расход','('+signed+' ? 0 : '+value+')')
        if combined:s+='                ;\n'
    s+='            ;\n'
    if combined:
        for r in q.resources:
            for base in ('НачальныйОстаток','КонечныйОстаток'):
                v='(Снимок['+lit(r.name+base)+'] как Число)'
                s+='            Снимок['+lit(r.name+base+'Положительный')+'] = '+v+' > 0 ? '+v+' : 0\n'
                s+='            Снимок['+lit(r.name+base+'Отрицательный')+'] = '+v+' < 0 ? -'+v+' : 0\n'
    projected='{'+', '.join(lit(f.name)+': Снимок['+lit(f.name)+']' for f in required.values())+'}'
    s+='            знч Проекция = СериализацияJson.ЗаписатьОбъект('+projected+')\n            Все.Добавить(СериализацияJson.ПрочитатьОбъект<Данные>(Проекция, новый Данные().ПолучитьТип()))\n        ;\n'
    return s


def totals_loader(q,c,required):
    lit=lambda v:c.literal(v,'Строка')
    key='{'+', '.join(lit(f.name)+': Снимок['+lit(f.name)+']' for f in q.dimensions)+'}'
    period='новый ДатаВремя(Снимок["Период"] как Строка)'
    start='П'+str(q.period_slot) if q.period_slot is not None else 'Неопределено'
    end='П'+str(q.end_slot) if q.end_slot is not None else 'Неопределено'
    # Constructor fields for optional bounds already exist in the common renderer.
    s='        знч Группы = новый Соответствие<Строка, Соответствие<Строка, Объект?>>()\n        для Снимок из Исходные\n            если не (Снимок["Активность"] как Булево)\n                продолжить\n            ;\n            знч Период = '+period+'\n'
    if q.period_slot is not None and q.end_slot is not None:
        s+='            если '+start+' != Неопределено и '+end+' != Неопределено и ('+start+' как ДатаВремя) > ('+end+' как ДатаВремя)\n                продолжить\n            ;\n'
    if q.source_kind=='balance':
        s+='            если '+start+' != Неопределено и Период >= ('+start+' как ДатаВремя)\n                продолжить\n            ;\n' if q.period_slot is not None else ''
    else:
        s+='            если '+end+' != Неопределено и Период > ('+end+' как ДатаВремя)\n                продолжить\n            ;\n' if q.end_slot is not None else ''
        if q.source_kind=='turnover' and q.period_slot is not None:s+='            если '+start+' != Неопределено и Период < ('+start+' как ДатаВремя)\n                продолжить\n            ;\n'
    s+='            знч Ключ = СериализацияJson.ЗаписатьОбъект('+key+')\n            если не Группы.СодержитКлюч(Ключ)\n                знч Новая = новый Соответствие<Строка, Объект?>()\n'
    for f in q.dimensions:s+='                Новая.Вставить('+lit(f.name)+', Снимок['+lit(f.name)+'])\n'
    for f in q.fields:
        if f not in q.dimensions:s+='                Новая.Вставить('+lit(f.name)+', 0)\n'
    records_only=q.source_kind=='balance-turnover' and q.complement=='Записи'
    if records_only:s+='                Новая.Вставить("_ЕстьДвижение", Ложь)\n'
    s+='                Группы.Вставить(Ключ, Новая)\n            ;\n            знч Группа = Группы[Ключ]\n'
    signed='(Снимок["ВидЗаписи"] как Строка) == "Приход"' if q.register_kind=='Остатки' else 'Истина'
    if q.source_kind=='balance-turnover':
        s+='            знч ДоНачала = '+(start+' != Неопределено и Период < ('+start+' как ДатаВремя)' if q.period_slot is not None else 'Ложь')+'\n'
        if records_only:s+='            если не ДоНачала\n                Группа["_ЕстьДвижение"] = Истина\n            ;\n'
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
    if records_only:s+='            если не (Снимок["_ЕстьДвижение"] как Булево)\n                продолжить\n            ;\n'
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
