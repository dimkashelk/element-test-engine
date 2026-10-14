"""Metadata record-set adapter: planning in Python, state and row operations in Script."""
import re
from hashlib import sha256
from types import SimpleNamespace

from .indexer import parse_module, method_call_expressions, method_local_bindings
from .call_types import infer_receiver_type
from .resolution import qualified
from .yaml_io import InputError, InvalidTestError, UnsupportedSyntaxError

CONTRACT = 'storage-record-set-v1'


def attach_register(storage, name):
    from .platform_mocks import PlatformMocks
    c = storage.contracts
    matches = c.resolve(name)
    if len(matches) != 1 or matches[0]['elementType'] not in {'РегистрСведений', 'РегистрНакопления'}:
        raise InputError('Хранение набора требует однозначный регистр')
    e = matches[0]
    identity = qualified(e)
    if e['elementType'] == 'РегистрСведений' and e['properties'].get('РежимЗаписи') == 'ПодчинениеРегистратору':
        raise UnsupportedSyntaxError('Подчинённый регистр сведений 10.0 вне контракта хранения')
    if identity in storage.register_schemas:
        return
    previous = c.namespace, c.imports
    c.namespace, c.imports = e['namespace'], e['properties'].get('Импорт', [])
    try:
        PlatformMocks(c, {'registers': [identity]}, {}, storage=True)
        dims = e['properties'].get('Измерения', [])
        accumulation = e['elementType'] == 'РегистрНакопления'
        if accumulation:
            dims = [f for f in e['properties'].get('Реквизиты', []) if f['Имя'] == 'Регистратор']
        dims = [{**f, 'Тип': c.canonical_type(f['Тип'])} for f in dims]
        owner = c.canonical_type(identity)
        parameters = owner + '.ПараметрыЗаписи'
        parameter_fields = [dict(f) for f in e['properties'].get('ПараметрыЗаписи', [])]
        for field in parameter_fields:
            c.require(field['Тип'])
            field['Тип'] = c.canonical_type(field['Тип'])
        c.fields[parameters] = parameter_fields
        c.definitions[parameters] = '@Глобально\nструктура ПараметрыЗаписи\n' + ''.join(
            '    пер ' + f['Имя'] + ': ' + c.sbsl_type(f['Тип']) +
            (' = ' + c.literal(f['ЗначениеПоУмолчанию'], f['Тип']) if 'ЗначениеПоУмолчанию' in f else '') + '\n'
            for f in parameter_fields) + ';\n'
    finally:
        c.namespace, c.imports = previous
    if not dims and accumulation:
        raise InputError('Набор хранения требует полный фильтр измерений/регистратора')
    ft, st, row = owner + '.ФильтрНабора', owner + '.НаборЗаписей', owner + '.Запись'
    rows = 'Массив<' + row + '>'
    from .reference_codec import encode, decode
    if accumulation:
        c.reference_codec_raw_fields = getattr(c, 'reference_codec_raw_fields', {})
        c.reference_codec_raw_fields[row] = {'Регистратор'}
    literal = lambda v: c.literal(v, 'Строка')
    # Field order comes from metadata. Tagged union values preserve registrar
    # types even when their reference UUIDs and serialized fields are identical.
    def values(fs, prefix=''):
        from .reference_codec import encode, reference_union
        if not fs:
            return 'новый Соответствие<Строка, Объект?>()'
        return '{' + ', '.join(literal(f['Имя']) + ': ' +
            (encode(c, prefix + f['Имя'], f['Тип']) if reference_union(f['Тип']) else
             '{"type": (' + prefix + f['Имя'] + ' как ' + f['Тип'] + ').ПолучитьТип().ВСтроку(), "value": ' + prefix + f['Имя'] + '}' if '|' in f['Тип'] else prefix + f['Имя']) for f in fs) + '}'
    filter_fields = list(dims)
    if e['properties'].get('ПериодВходитВОсновнойФильтр', False):
        period = [f for f in c.fields[row] if f['Имя'] == 'Период']
        if accumulation or not period:
            raise InputError('ПериодВходитВОсновнойФильтр требует периодический регистр сведений')
        filter_fields = period + filter_fields
    signature = ', '.join(f['Имя'] + ': ' + c.sbsl_type(f['Тип']) for f in filter_fields)
    key = values(filter_fields)
    def clone_ref(expr, typ):
        from .generated_types import union_members
        variants = union_members(typ) or [typ]
        choices = [t.rstrip('?') for t in variants if t != '?']
        if any(not t.endswith('.Ссылка') for t in choices):
            raise UnsupportedSyntaxError('Регистратор требует ссылочный тип документа')
        parts = ['новый ' + t + '(Идентификатор = (' + expr + ' как ' + t + ').Идентификатор)' for t in choices]
        result = parts[-1]
        for t, part in reversed(list(zip(choices[:-1], parts[:-1]))):
            result = expr + ' это ' + t + ' ? ' + part + ' : ' + result
        return '(' + (expr + ' == Неопределено ? Неопределено : ' if '?' in typ else '') + result + ')'
    registrar_field = registrar_set = ''
    if accumulation:
        registrar_type = dims[0]['Тип']
        nullable_slot = '?' if '|' in registrar_type and '?' not in registrar_type else ''
        registrar_field = '    пер ЗначениеРегистратора: ' + c.sbsl_type(registrar_type) + nullable_slot + '\n'
        registrar_set = '        ЗначениеРегистратора = ' + clone_ref('Регистратор', registrar_type) + '\n'
        if nullable_slot:
            c.definitions[row] = c.definitions[row].replace('пер Регистратор: ' + c.sbsl_type(registrar_type),
                                                          'пер Регистратор: ' + c.sbsl_type(registrar_type) + nullable_slot)
    c.fields[ft] = [{'Имя': 'Ключ', 'Тип': 'Строка?'}]
    c.definitions[ft] = ('@Глобально\nструктура ФильтрНабора\n    пер Ключ: Строка?\n' + registrar_field +
        '    @Глобально\n    метод Установить(' + signature + ')\n'
        '        этот.Ключ = СериализацияJson.ЗаписатьОбъект(' + key + ')\n' + registrar_set + '    ;\n;\n')
    # Direct writes extend the explicit mask. A missing field is never a NULL.
    for f in filter_fields:
        name, typ = f['Имя'], c.sbsl_type(f['Тип'])
        encoded = values([f], 'Значение.').replace('Значение.' + name, 'Значение')
        decoded = decode(c, 'Отбор[' + literal(name) + ']', f['Тип'])
        methods = f'''    @Глобально
    метод ТестЗадать{name}(Значение: {typ})
        знч Отбор = Ключ == Неопределено ? новый Соответствие<Строка, Объект?>() : СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Объект?>>(Ключ как Строка, Тип<Соответствие<Строка, Объект?>>)
        знч Поле = СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Объект?>>(СериализацияJson.ЗаписатьОбъект({encoded}), Тип<Соответствие<Строка, Объект?>>)
        Отбор.Вставить({literal(name)}, Поле[{literal(name)}])
        Ключ = СериализацияJson.ЗаписатьОбъект(Отбор)
'''
        if accumulation:
            methods += '        ЗначениеРегистратора = ' + clone_ref('Значение', f['Тип']) + '\n'
        methods += f'''    ;
    @Глобально
    метод ТестПолучить{name}(): {typ}
        если Ключ == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен фильтр")
        ;
        знч Отбор = СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Объект?>>(Ключ как Строка, Тип<Соответствие<Строка, Объект?>>)
        если не Отбор.СодержитКлюч({literal(name)})
            выбросить новый ИсключениеНедопустимоеСостояние("Не задано поле фильтра")
        ;
        возврат {decoded}
    ;
'''
        c.definitions[ft] = c.definitions[ft].rsplit(';', 1)[0] + methods + ';\n'
    definition = c.definitions[st]
    definition = re.sub(r'        ТестПлатформа\.ЗаписатьВызов\([^\n]*\)\n', '', definition)
    start = definition.index('    @Глобально\n    метод Записать(')
    checks = ''
    row_key = values(dims, 'СтрокаНабора.')
    checks += ('            если СериализацияJson.ЗаписатьОбъект(' + row_key + ') != Ключ\n'
        '                выбросить новый ИсключениеНедопустимоеСостояние("Запись не соответствует фильтру")\n            ;\n')
    if accumulation:
        checks += ('            если не СтрокаНабора.Активность\n'
            '                выбросить новый ИсключениеНедопустимоеСостояние("Активность записи не соответствует набору")\n            ;\n')
    else:
        unique = dims + [f for f in c.fields[row] if f['Имя'] == 'Период']
        checks += ('            знч Идентичность = СериализацияJson.ЗаписатьОбъект(' + values(unique, 'СтрокаНабора.') + ')\n'
            '            если Ключи.СодержитКлюч(Идентичность)\n'
            '                ТестСессия.ИспортитьТранзакцию()\n'
            '                выбросить новый ИсключениеНедопустимоеСостояние("Неуникальный ключ записи")\n            ;\n'
            '            Ключи.Вставить(Идентичность, Истина)\n')
    if storage.config.get('lifecycle'):
        from .metadata_lifecycle import validation, describe
        checks = validation(c, c.fields[row], 'СтрокаНабора.', indent='            ') + checks
        storage.lifecycle_contracts.append(describe(e))
    encoded_rows = encode(c, 'Результат', rows)
    definition = definition[:start] + f'''    @Глобально
    метод Прочитать()
        если Фильтр.Ключ == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен фильтр")
        ;
        знч JSON = ТестСессия.Прочитать({literal(identity)}, Фильтр.Ключ как Строка)
        знч Новые = новый {rows}()
        если JSON != Неопределено
            Новые.ДобавитьВсе(Материализовать(JSON как Строка))
        ;
        Записи = Новые
    ;
    @Глобально
    метод Очистить()
        Записи.Очистить()
    ;
    @Глобально
    метод Размер(): Число
        возврат Записи.Размер()
    ;
    @Глобально
    метод Пусто(): Булево
        возврат Записи.Пусто()
    ;
    @Глобально
    метод Записать(Замещать: Булево = Истина)
        если Фильтр.Ключ == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен фильтр")
        ;
        знч Ключ = Фильтр.Ключ как Строка
        знч Результат = новый {rows}()
        если не Замещать
            знч Предыдущие = ТестСессия.Прочитать({literal(identity)}, Ключ)
            если Предыдущие != Неопределено
                Результат.ДобавитьВсе(Материализовать(Предыдущие как Строка))
            ;
        ;
        Результат.ДобавитьВсе(Записи)
        знч Ключи = новый Соответствие<Строка, Булево>()
        для СтрокаНабора из Результат
{checks}        ;
        ТестСессия.Записать({literal(identity)}, Ключ, СериализацияJson.ЗаписатьОбъект({encoded_rows}))
        если не Замещать
            Записи.Очистить()
        ;
    ;
;
'''
    if storage.config.get('lifecycle'):
        from .metadata_lifecycle import atomic
        begin = definition.index('    метод Записать(Замещать: Булево = Истина)\n') + len('    метод Записать(Замещать: Булево = Истина)\n')
        end = definition.rindex('    ;\n;\n')
        definition = definition[:begin] + atomic(definition[begin:end], indent='        ') + definition[end:]
    materialize = f'''    метод Материализовать(JSON: Строка): {rows}
        знч Результат = новый {rows}()
'''
    if accumulation and '|' in registrar_type:
        # Reference JSON alone cannot disambiguate equal-shaped union members.
        # The typed filter supplies the concrete registrar identity for every
        # row in this full-filter bucket; each row receives a fresh reference.
        decoded_row = decode(c, 'Снимок', row)
        materialize += f'''        знч Сырые: Объект? = СериализацияJson.ПрочитатьОбъект(JSON)
        для Значение из (Сырые как Массив<Объект?>)
            знч Снимок = Значение как Соответствие<Строка, Объект?>
            Снимок.Удалить("Регистратор")
            знч СтрокаНабора = {decoded_row}
            СтрокаНабора.Регистратор = {clone_ref('(Фильтр.ЗначениеРегистратора как ' + registrar_type + ')', registrar_type)}
            Результат.Добавить(СтрокаНабора)
        ;
'''
    else:
        decoded_rows = decode(c, 'СериализацияJson.ПрочитатьОбъект(JSON)', rows)
        materialize += f'        Результат.ДобавитьВсе({decoded_rows})\n'
    materialize += '        возврат Результат\n    ;\n'
    definition = definition.rsplit(';', 1)[0] + materialize + ';\n'
    c.definitions[st] = definition
    if not accumulation:
        _information_storage(c, identity, st, row, dims, filter_fields, values,
                             bool(storage.config.get('lifecycle')))
    c.method_dependencies[st] = ['ТестСессия.Записи']
    c.method_dependencies[ft] = [f['Тип'] for f in dims]
    storage.register_schemas[identity] = {'owner': identity, 'kind': e['elementType'],
        'rowType': row, 'setType': st, 'filterType': ft, 'filterFields': filter_fields, 'bucketFields': dims,
        'parameterType': parameters,
        'fields': c.fields[row], 'periodicity': e['properties'].get('Периодичность', 'Непериодический'),
        'backend': storage.config.get('backend', 'memory'), 'contract': CONTRACT,
        'limitations': ['full-filter-only' if accumulation else 'explicit-named-field-subset',
                        'unordered', 'isolated-staging', 'no-balances']}


def attach_handlers(storage):
    """Wrap the generated persistence API after original instance methods attach.

    Teacher setup bypasses callbacks. A savepoint spans both hooks and every
    nested write, even without the optional metadata validation contract.
    """
    from .metadata_lifecycle import atomic
    c = storage.contracts
    for schema in storage.register_schemas.values():
        st = schema['setType']
        source = c.methods.get(st, '')
        nodes = {n.name: n for n in parse_module(source)[0]}
        hooks = [name for name in ('ПередЗаписью', 'ПослеЗаписи') if name in nodes]
        if not hooks or schema.get('handlers'):
            continue
        for name in hooks:
            node = nodes[name]
            params = node.parameters(source)
            if (len(params) not in {1, 2} or params[0].partition(':')[2].partition('=')[0].strip() != 'Булево'
                    or len(params) == 2 and params[1].partition(':')[2].strip() != schema['parameterType']
                    or node.return_type(source) not in {None, 'ничто'}):
                raise UnsupportedSyntaxError('Обработчик набора требует ' + name + '(Замещать: Булево[, ПараметрыЗаписи]) без результата')
        definition = c.definitions[st].replace('метод Записать(Замещать:', 'метод ТестЗаписатьБезОбработчиков(Замещать:', 1)
        pt = schema['parameterType']
        def invoke(name):
            if name not in hooks:
                return ''
            args = 'Замещать' + (', ТестПараметрыЗаписи' if len(nodes[name].parameters(source)) == 2 else '')
            return '        ' + name + '(' + args + ')\n'
        body = '        знч ТестПараметрыЗаписи = ПараметрыЗаписи == Неопределено ? новый ' + pt + '() : ПараметрыЗаписи как ' + pt + '\n'
        body += invoke('ПередЗаписью')
        body += '        ТестЗаписатьБезОбработчиков(Замещать)\n'
        body += invoke('ПослеЗаписи')
        wrapper = '    @Глобально\n    метод Записать(Замещать: Булево = Истина, ПараметрыЗаписи: ' + pt + '? = Неопределено)\n' + atomic(body, indent='        ') + '    ;\n'
        c.definitions[st] = definition.rsplit(';', 1)[0] + wrapper + ';\n'
        schema['handlers'] = hooks
        c.method_dependencies[st].append(pt)


def filter_method(c, schema, names):
    """One typed signature per observed mask: omitted is never a nullable default.

    Full positional calls retain their old signature. Partial named calls are
    lowered by AST to these internal methods; original files remain untouched.
    """
    dims = schema['filterFields']
    selected = [f for f in dims if f['Имя'] in names]
    if len(selected) == len(dims):
        return 'Установить'
    mask = sum(1 << i for i, f in enumerate(dims) if f['Имя'] in names)
    name = 'УстановитьПодмножество' + str(mask)
    ft = schema['filterType']
    if 'метод ' + name + '(' not in c.definitions[ft]:
        signature = ', '.join(f['Имя'] + ': ' + c.sbsl_type(f['Тип']) for f in selected)
        from .reference_codec import encode, reference_union
        fields = ', '.join(c.literal(f['Имя'], 'Строка') + ': ' +
            (encode(c, f['Имя'], f['Тип']) if reference_union(f['Тип']) else
             '{"type": (' + f['Имя'] + ' как ' + f['Тип'] + ').ПолучитьТип().ВСтроку(), "value": ' + f['Имя'] + '}'
             if '|' in f['Тип'] else f['Имя']) for f in selected)
        expression = '{' + fields + '}' if selected else 'новый Соответствие<Строка, Объект?>()'
        method = ('    @Глобально\n    метод ' + name + '(' + signature + ')\n'
                  '        этот.Ключ = СериализацияJson.ЗаписатьОбъект(' + expression + ')\n    ;\n')
        c.definitions[ft] = c.definitions[ft].rsplit(';', 1)[0] + method + ';\n'
    return name


def _information_storage(c, identity, st, row, dims, filter_fields, values, lifecycle):
    """Predicate selection over canonical full-dimension buckets, not filter buckets.

    Full dimensions identify a bucket; dimensions + period identify its rows.
    Keep the public audit format and empty full buckets for existing consumers.
    Validate the entire replacement before one session-table mutation.
    """
    lit = lambda v: c.literal(v, 'Строка')
    rows = 'Массив<' + row + '>'
    from .reference_codec import encode, decode
    unique = dims + [f for f in c.fields[row] if f['Имя'] == 'Период']
    encoded_bucket = encode(c, 'Группы[Ключ]', rows)
    decoded_all = decode(c, 'СериализацияJson.ПрочитатьОбъект(JSON)', rows)
    full_group = ('{' + ', '.join(lit(f['Имя']) + ': Отбор[' + lit(f['Имя']) + ']' for f in dims) + '}'
                  if dims else 'новый Соответствие<Строка, Объект?>()')
    definition = c.definitions[st]
    start = definition.index('    @Глобально\n    метод Прочитать()')
    validation = ''
    if lifecycle:
        from .metadata_lifecycle import validation as validate
        validation = validate(c, c.fields[row], 'СтрокаНабора.', indent='            ')
    body = f'''        если Фильтр.Ключ == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен фильтр")
        ;
        знч Предыдущие = ВсеСтроки()
        знч Результат = новый {rows}()
        для СтрокаНабора из Предыдущие
            если не Замещать или не Соответствует(СтрокаНабора)
                Результат.Добавить(СтрокаНабора)
            ;
        ;
        для СтрокаНабора из Записи
{validation}            если не Соответствует(СтрокаНабора)
                ТестСессия.ИспортитьТранзакцию()
                выбросить новый ИсключениеНедопустимоеСостояние("Запись не соответствует фильтру")
            ;
            Результат.Добавить(СтрокаНабора)
        ;
        знч Ключи = новый Соответствие<Строка, Булево>()
        знч Группы = новый Соответствие<Строка, {rows}>()
        знч Состояние = ТестСессия.ЧитатьВсе()
        если Состояние.СодержитКлюч({lit(identity)})
            для Ключ из Состояние[{lit(identity)}].Ключи()
                Группы.Вставить(Ключ, новый {rows}())
            ;
        ;
        // A full empty replacement retains the previous public empty bucket.
        знч Отбор = СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Объект?>>(Фильтр.Ключ как Строка, Тип<Соответствие<Строка, Объект?>>)
        если Отбор.Размер() == {len(dims)} и {' и '.join('Отбор.СодержитКлюч(' + lit(f['Имя']) + ')' for f in dims) or 'Истина'}
            знч КлючГруппы = СериализацияJson.ЗаписатьОбъект({full_group})
            если не Группы.СодержитКлюч(КлючГруппы)
                Группы.Вставить(КлючГруппы, новый {rows}())
            ;
        ;
        для СтрокаНабора из Результат
            знч Идентичность = СериализацияJson.ЗаписатьОбъект({values(unique, 'СтрокаНабора.')})
            если Ключи.СодержитКлюч(Идентичность)
                ТестСессия.ИспортитьТранзакцию()
                выбросить новый ИсключениеНедопустимоеСостояние("Неуникальный ключ записи")
            ;
            Ключи.Вставить(Идентичность, Истина)
            знч Ключ = СериализацияJson.ЗаписатьОбъект({values(dims, 'СтрокаНабора.')})
            если не Группы.СодержитКлюч(Ключ)
                Группы.Вставить(Ключ, новый {rows}())
            ;
            Группы[Ключ].Добавить(СтрокаНабора)
        ;
        знч Таблица = новый Соответствие<Строка, Строка>()
        для Ключ из Группы.Ключи()
            Таблица.Вставить(Ключ, СериализацияJson.ЗаписатьОбъект({encoded_bucket}))
        ;
        ТестСессия.ЗаписатьТаблицу({lit(identity)}, Таблица)
        если не Замещать
            Записи.Очистить()
        ;
'''
    if lifecycle:
        from .metadata_lifecycle import atomic
        body = atomic(body, indent='        ')
    definition = definition[:start] + f'''    @Глобально
    метод Прочитать()
        если Фильтр.Ключ == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен фильтр")
        ;
        знч Новые = новый {rows}()
        для СтрокаНабора из ВсеСтроки()
            если Соответствует(СтрокаНабора)
                Новые.Добавить(СтрокаНабора)
            ;
        ;
        Записи = Новые
    ;
    @Глобально
    метод Очистить()
        Записи.Очистить()
    ;
    @Глобально
    метод Размер(): Число
        возврат Записи.Размер()
    ;
    @Глобально
    метод Пусто(): Булево
        возврат Записи.Пусто()
    ;
    @Глобально
    метод Записать(Замещать: Булево = Истина)
{body}    ;
    метод ВсеСтроки(): {rows}
        знч Результат = новый {rows}()
        знч Состояние = ТестСессия.ЧитатьВсе()
        если Состояние.СодержитКлюч({lit(identity)})
            для JSON из Состояние[{lit(identity)}].Значения()
                Результат.ДобавитьВсе({decoded_all})
            ;
        ;
        возврат Результат
    ;
    метод Соответствует(СтрокаНабора: {row}): Булево
        знч Отбор = СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Объект?>>(Фильтр.Ключ как Строка, Тип<Соответствие<Строка, Объект?>>)
        знч Полный = СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Объект?>>(СериализацияJson.ЗаписатьОбъект({values(filter_fields, 'СтрокаНабора.')}), Тип<Соответствие<Строка, Объект?>>)
        для Имя из Отбор.Ключи()
            если СериализацияJson.ЗаписатьОбъект(Отбор[Имя]) != СериализацияJson.ЗаписатьОбъект(Полный[Имя])
                возврат Ложь
            ;
        ;
        возврат Истина
    ;
;
'''
    c.definitions[st] = definition


def register_setup(storage, variable_prefix='НачальныйНабор'):
    """Teacher seeds use the same typed write API; never feed read responses."""
    c = storage.contracts
    seeds = storage.config.get('initialRegisters', [])
    if not isinstance(seeds, list) or len(seeds) > 100:
        raise InvalidTestError('storage.initialRegisters требует список до 100 наборов')
    lines = []
    initial_keys = set()
    for i, seed in enumerate(seeds):
        if not isinstance(seed, dict) or set(seed) != {'type', 'filter', 'rows'}:
            raise InvalidTestError('Начальный набор требует type, filter и rows')
        from .generated_types import ProjectTypes
        raw = seed['type']
        matches = ([e for e in c.model['elements'] if qualified(e) == raw] if isinstance(raw, str) and raw in storage.register_schemas
                   else ProjectTypes(c.model, c.namespace, c.imports).resolve(raw) if isinstance(raw, str) and '::' in raw
                   else [e for e in c.model['elements'] if e['name'] == raw] if isinstance(raw, str) else [])
        if len(matches) != 1 or qualified(matches[0]) not in storage.register_schemas:
            raise InvalidTestError('Начальный набор требует однозначный owner из storage.registers')
        schema = storage.register_schemas[qualified(matches[0])]
        dims = schema['filterFields']
        filt, rows = seed['filter'], seed['rows']
        required = {f['Имя'] for f in dims}
        if (not isinstance(filt, dict) or not set(filt) <= required or
                schema['kind'] == 'РегистрНакопления' and set(filt) != required):
            raise InvalidTestError('Начальный набор требует явный фильтр без неизвестных измерений')
        if not isinstance(rows, list) or len(rows) > 100:
            raise InvalidTestError('Начальный набор требует до 100 строк')
        fields = {f['Имя']: f['Тип'] for f in schema['fields']}
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(fields):
                raise InvalidTestError('Начальная строка требует все поля схемы')
            if any(row[name] != value for name, value in filt.items()):
                raise InvalidTestError('Начальная строка не соответствует фильтру')
            # Query fixtures include inactive historical movements. Writes keep
            # their existing active-record contract; totals ignore these rows.
        # Validate duplicate information keys before Script so teacher mistakes
        # cannot be reported as student failures.
        if schema['kind'] == 'РегистрСведений':
            import json
            keys = [json.dumps({k: r[k] for k in [f['Имя'] for f in schema['bucketFields']] + (['Период'] if 'Период' in fields else [])}, sort_keys=True) for r in rows]
            identities = {(schema['owner'], key) for key in keys}
            if len(set(keys)) != len(keys) or initial_keys & identities:
                raise InvalidTestError('Неуникальные ключи начальных строк')
            initial_keys.update(identities)
        method = filter_method(c, schema, set(filt))
        args = ', '.join(f['Имя'] + ' = ' + c.literal(filt[f['Имя']], f['Тип']) for f in dims if f['Имя'] in filt)
        expr = c.literal(rows, 'Массив<' + schema['rowType'] + '>')
        name = variable_prefix + str(i)
        lines += ['    ТестСессия.Событие("teacher:register-setup")',
                  '    знч ' + name + ' = новый ' + schema['setType'] + '()',
                  '    ' + name + '.Фильтр.' + method + '(' + args + ')',
                  '    ' + name + '.Записи.ДобавитьВсе(' + expr + ')']
        if schema['kind']=='РегистрНакопления' and any(not row['Активность'] for row in rows):
            # Author setup represents historical inactive rows, without
            # expanding the student's record-set write contract.
            lines += ['    ТестСессия.Записать('+c.literal(schema['owner'],'Строка')+', '+name+'.Фильтр.Ключ как Строка, СериализацияJson.ЗаписатьОбъект('+name+'.Записи))']
        else:
            write = 'ТестЗаписатьБезОбработчиков' if schema.get('handlers') else 'Записать'
            lines += ['    ' + name + '.' + write + '()']
    return lines


def bind_record_sets(plan):
    """Only proven metadata set receivers and AST loop expressions are adapted."""
    if not plan.storage:
        # Прочитать is not a new spy API; it needs actual storage.
        if any(b.category == 'metadata' and b.operation == 'Прочитать' for b in plan.bindings):
            raise UnsupportedSyntaxError('Прочитать требует storage; mocks.registers не читают состояние')
        return
    sources = {m['sourceFile']: (plan.root/m['sourceFile']).read_text(encoding='utf-8-sig') for m in plan.model['modules']}
    source_hashes = {path: sha256((plan.root/path).read_bytes()).hexdigest() for path in sources}
    declarations = {p: parse_module(s)[0] for p, s in sources.items()}
    c = plan.contracts
    previous = c.namespace, c.imports
    try:
        for symbol in plan.symbols:
            c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
            node = parse_module(symbol.source)[0][0]
            bindings = method_local_bindings(symbol.source, node)
            def schema_for(a, b):
                fake = SimpleNamespace(receiver_start=a, receiver_end=b)
                typ = infer_receiver_type(symbol.source, node, fake, symbol.owner, plan.model, declarations, sources, bindings)
                if not typ or not typ.endswith(('.НаборЗаписей', '.ФильтрНабора')):
                    return None, typ
                canonical = c.canonical_type(typ)
                return next((s for s in plan.storage.register_schemas.values()
                    if canonical in {s['setType'], s['filterType']}), None), typ
            def validate_filter_value(f, expr):
                from .generated_types import union_members
                raw = symbol.source[expr.start:expr.end]
                inferred = ('Строка' if expr.kind == 'string' else
                            'Число' if expr.kind == 'number' else
                            'Булево' if raw in {'Истина', 'Ложь'} else
                            '?' if raw == 'Неопределено' else
                            infer_receiver_type(symbol.source, node,
                                SimpleNamespace(receiver_start=expr.start, receiver_end=expr.end),
                                symbol.owner, plan.model, declarations, sources, bindings))
                if inferred:
                    actual = c.canonical_type(inferred)
                    accepted = union_members(f['Тип']) or [f['Тип'].rstrip('?')] + (['?'] if f['Тип'].endswith('?') else [])
                    actual_members = (['?'] if actual == '?' else union_members(actual) or
                                      [actual.rstrip('?')] + (['?'] if actual.endswith('?') else []))
                    if not set(actual_members) <= set(accepted):
                        raise InputError('Неверный тип фильтра ' + f['Имя'] + ': ' + actual + ', ожидается ' + f['Тип'])

            for call in method_call_expressions(symbol.source, node):
                if call.receiver_start is None:
                    continue
                schema, typ = schema_for(call.receiver_start, call.receiver_end)
                if not schema:
                    continue
                # Project calls keep their own implementation even with an API name.
                bound = next((b for b in plan.bindings if b.source_file == symbol.identity.source_file
                    and b.start == symbol.start + call.start and b.operation == call.name), None)
                if bound and bound.category == 'project':
                    continue
                if call.name == 'Прочитать' and not typ.endswith('.НаборЗаписей'):
                    raise UnsupportedSyntaxError('Прочитать поддержан только для НаборЗаписей')
                if call.name == 'Прочитать' and schema['periodicity'] not in ({'Непериодический', 'День', 'Секунда', 'Момент'} if plan.storage.config.get('lifecycle') else {'Непериодический', 'День'}):
                    raise UnsupportedSyntaxError('Чтение периодического регистра подтверждено только для День')
                filter_ir = {}
                if call.name == 'Установить' and typ.endswith('.ФильтрНабора'):
                    expression = next(n for n in node.expression_tree.walk()
                        if n.kind == 'call' and n.children[0].start == call.start and
                        n.children[0].end <= call.end)
                    args = expression.children[1:]
                    # Calls expose callee + argument nodes in AST.
                    required = {f['Имя'] for f in schema['filterFields']}
                    names = []
                    for arg in args:
                        if arg.kind in {'binary', 'assignment'} and arg.value == '=' and arg.children[0].kind == 'name':
                            names.append(arg.children[0].value)
                    if names and len(names) != len(args):
                        raise UnsupportedSyntaxError('Смешанная именованная/позиционная форма фильтра не поддержана')
                    if len(set(names)) != len(names):
                        raise InputError('Повторное имя измерения фильтра')
                    if not set(names) <= required:
                        raise InputError('Неизвестное измерение фильтра: ' + ', '.join(sorted(set(names) - required)))
                    if (args and not names and len(args) != len(required) or
                            schema['kind'] == 'РегистрНакопления' and len(args) != len(required)):
                        raise UnsupportedSyntaxError('Поддержан только полный позиционный фильтр/фильтр регистратора')
                    mask = set(names) if names or not args else required
                    method = filter_method(c, schema, mask)
                    if method != call.name:
                        token = expression.children[0].children[-1]
                        plan.source_transforms.setdefault((symbol.identity.source_file, symbol.identity.declaration), []).append(
                            (token.start, token.end, method))
                    selected = [f for f in schema['filterFields'] if f['Имя'] in mask]
                    expressions = ({a.children[0].value: a.children[1] for a in args} if names else
                                   {f['Имя']: a for f, a in zip(selected, args)})
                    for f in selected:
                        validate_filter_value(f, expressions[f['Имя']])
                    filter_ir = {'filterMask': [f['Имя'] for f in selected],
                        'filterValues': [{'name': f['Имя'], 'type': f['Тип'],
                            'expression': symbol.source[expressions[f['Имя']].start:expressions[f['Имя']].end],
                            'range': [symbol.start + expressions[f['Имя']].start, symbol.start + expressions[f['Имя']].end]}
                            for f in selected]}
                plan.record_sets.append({**schema, 'sourceFile': symbol.identity.source_file,
                    'symbol': symbol.identity.declaration, 'operation': call.name,
                    'sourceHash': source_hashes[symbol.identity.source_file], **filter_ir,
                    'receiverType': c.canonical_type(typ), 'start': symbol.start+call.start,
                    'end': symbol.start + next(n.end for n in node.expression_tree.walk()
                        if n.kind == 'call' and n.children[0].start == call.start and n.children[0].end <= call.end)})
            field_writes = {}
            for statement in node.expression_tree.walk():
                if statement.kind not in {'binary', 'assignment'} or statement.value not in {'=', '+=', '-=', '*=', '/=', '??='}:
                    continue
                left, right = statement.children
                if left.kind != 'member':
                    continue
                receiver, field = left.children
                schema, typ = schema_for(receiver.start, receiver.end)
                if not schema or not typ.endswith('.ФильтрНабора'):
                    continue
                if field.value not in {f['Имя'] for f in schema['filterFields']}:
                    raise InputError('Неизвестное измерение фильтра: ' + field.value)
                if statement.value != '=':
                    raise UnsupportedSyntaxError('Поле фильтра поддерживает простое присваивание')
                validate_filter_value(next(f for f in schema['filterFields'] if f['Имя'] == field.value), right)
                field_writes[left.start, left.end] = True
                plan.source_transforms.setdefault((symbol.identity.source_file, symbol.identity.declaration), []).extend([
                    (field.start, right.start, 'ТестЗадать' + field.value + '('),
                    (statement.end, statement.end, ')')])
                plan.record_sets.append({**schema, 'sourceFile': symbol.identity.source_file,
                    'symbol': symbol.identity.declaration, 'operation': 'filter-field-write',
                    'field': field.value, 'sourceHash': source_hashes[symbol.identity.source_file],
                    'start': symbol.start + statement.start, 'end': symbol.start + statement.end})
            for statement in node.expression_tree.walk():
                if statement.kind == 'member' and statement.value == '.' and len(statement.children) == 2:
                    receiver, field = statement.children
                    schema, typ = schema_for(receiver.start, receiver.end)
                    if schema and typ.endswith('.ФильтрНабора') and field.value != 'Установить':
                        if field.value not in {f['Имя'] for f in schema['filterFields']}:
                            raise InputError('Неизвестное измерение фильтра: ' + field.value)
                        if (statement.start, statement.end) not in field_writes:
                            plan.source_transforms.setdefault((symbol.identity.source_file, symbol.identity.declaration), []).append(
                                (field.start, field.end, 'ТестПолучить' + field.value + '()'))
                if statement.kind in {'binary', 'assignment'} and statement.value in {'=', '??='}:
                    left = statement.children[0]
                    if left.kind == 'member' and left.children[1].value == 'Фильтр':
                        receiver = left.children[0]
                        schema, typ = schema_for(receiver.start, receiver.end)
                        if schema and typ.endswith('.НаборЗаписей'):
                            raise UnsupportedSyntaxError('Фильтр набора доступен только для чтения')
                if statement.kind != 'statement' or statement.value != 'для' or len(statement.children) != 1:
                    continue
                expr = statement.children[0]
                schema, typ = schema_for(expr.start, expr.end)
                if not schema or not typ.endswith('.НаборЗаписей'):
                    continue
                plan.source_transforms.setdefault((symbol.identity.source_file, symbol.identity.declaration), []).extend(
                    [(expr.start, expr.start, '('), (expr.end, expr.end, ').Записи')])
                plan.record_sets.append({**schema, 'sourceFile': symbol.identity.source_file,
                    'symbol': symbol.identity.declaration, 'operation': 'iterate', 'receiverType': c.canonical_type(typ),
                    'sourceHash': source_hashes[symbol.identity.source_file],
                    'start': symbol.start+expr.start, 'end': symbol.start+expr.end})
    finally:
        c.namespace, c.imports = previous
