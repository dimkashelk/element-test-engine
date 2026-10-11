"""Declaration-derived write validation and lifecycle adapters (Script executes effects)."""
from decimal import Decimal
from hashlib import sha256

from .resolution import qualified
from .yaml_io import InputError, UnsupportedSyntaxError

CONTRACT = 'metadata-lifecycle-v1'
CONSTRAINTS = {'НезаполненноеЗначение', 'МинимальноеЗначение', 'МаксимальноеЗначение',
               'ДлинаЦелойЧасти', 'ДлинаДробнойЧасти', 'Длина', 'МаксимальнаяДлина',
               'Уникальность', 'Автонумерация', 'СерииНумерации', 'Периодичность'}


def declarations(element):
    props = element['properties']
    yield '', props.get('Реквизиты', props.get('Константы', []))
    for group in ('Измерения', 'Ресурсы'):
        if group in props:
            yield '', props[group]
    for table in props.get('ТабличныеЧасти', []):
        yield table['Имя'], table.get('Реквизиты', [])


def describe(element, root=None):
    fields = []
    for table, declarations_ in declarations(element):
        for field in declarations_:
            constraints = {k: field[k] for k in sorted(CONSTRAINTS) if k in field}
            if constraints:
                fields.append({'path': [table, field['Имя']] if table else [field['Имя']],
                               'type': field.get('Тип', 'Строка'), 'constraints': constraints})
    result = {'owner': qualified(element), 'kind': element['elementType'], 'sourceFile': element['sourceFile'],
              'contract': CONTRACT, 'fields': fields,
              'writeParameters': element['properties'].get('ПараметрыЗаписи', []),
              'order': ['snapshot-before', 'before-handler', 'number', 'normalize-and-validate', 'save', 'after-handler', 'commit'],
              'support': 'executor-emulation', 'execution': 'not-checked'}
    if element['elementType'] not in {'Справочник', 'Документ'}:
        result['order'] = ['normalize-and-validate', 'save']
    if root is not None:
        result['sourceHash'] = sha256((root / element['sourceFile']).read_bytes()).hexdigest()
        import yaml
        from .capability_map import yaml_nodes
        positions = {tuple(path): {'range': [node.start_mark.index, node.end_mark.index],
                                  'line': node.start_mark.line + 1, 'column': node.start_mark.column + 1}
                     for path, node in yaml_nodes(yaml.compose((root / element['sourceFile']).read_text(encoding='utf-8-sig')))}
        props = element['properties']
        for item in fields:
            path = item['path']
            group = 'Реквизиты' if 'Реквизиты' in props else 'Константы'
            if len(path) == 1:
                group = next(g for g in ('Реквизиты', 'Константы', 'Измерения', 'Ресурсы')
                             if any(f['Имя'] == path[0] for f in props.get(g, [])))
                index = next(i for i, f in enumerate(props.get(group, [])) if f['Имя'] == path[0])
                yaml_path = [group, str(index)]
            else:
                table_index = next(i for i, t in enumerate(props['ТабличныеЧасти']) if t['Имя'] == path[0])
                index = next(i for i, f in enumerate(props['ТабличныеЧасти'][table_index]['Реквизиты']) if f['Имя'] == path[1])
                yaml_path = ['ТабличныеЧасти', str(table_index), 'Реквизиты', str(index)]
            item['yamlPath'] = yaml_path
            item['position'] = positions.get(tuple(yaml_path))
    return result


def _integer(value, key, minimum=0, maximum=1000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(f'{key} требует целое {minimum}..{maximum}')
    return value


def validation(c, fields, prefix, *, indent='    ', unique=False, identity=None, id_expression=None):
    """Generate guards only; no values or business decisions evaluated in Python."""
    lines = []
    lit = lambda v: c.literal(v, 'Строка')
    for field in fields:
        name, typ = field['Имя'], field.get('Тип', 'Строка')
        expr = prefix + name
        from .generated_types import union_members
        members = union_members(typ)
        base = '|'.join(t for t in members if t != '?') if members else typ.rstrip('?')
        nullable = '?' in typ
        value = '(' + expr + ' как ' + c.sbsl_type(base) + ')' if nullable else expr
        rules = set(field) & CONSTRAINTS
        if not rules:
            continue
        if 'Автонумерация' in field and not unique:
            raise UnsupportedSyntaxError('Автонумерация поддержана только для реквизитов объекта: ' + name)
        def reject(condition, rule):
            lines.extend([indent + 'если ' + condition,
                indent + '    выбросить новый ИсключениеНедопустимоеСостояние(' + lit('metadata:' + name + ':' + rule) + ')', indent + ';'])
        empty = field.get('НезаполненноеЗначение', 'Разрешить')
        if empty not in {'Разрешить', 'ЗапретитьВсегда'}:
            raise UnsupportedSyntaxError('НезаполненноеЗначение вне контракта: ' + str(empty))
        if empty == 'ЗапретитьВсегда':
            if nullable:
                reject(expr + ' == Неопределено', 'required')
            if base == 'Строка':
                reject((expr + ' != Неопределено и ' if nullable else '') + value + '.Пусто()', 'required')
            elif base.endswith('.Ссылка'):
                identifier = value + '.Идентификатор'
                default = c.literal('00000000-0000-0000-0000-000000000000', 'Ууид') if c.reference_id_type == 'Ууид' else lit('')
                reject((expr + ' != Неопределено и ' if nullable else '') + identifier + ' == ' + default, 'required')
        inner = indent
        if nullable:
            lines.append(indent + 'если ' + expr + ' != Неопределено')
            indent += '    '
        if base == 'Число':
            if 'ДлинаДробнойЧасти' in field:
                scale = _integer(field['ДлинаДробнойЧасти'], 'ДлинаДробнойЧасти', maximum=38)
                lines.append(indent + expr + ' = ' + value + '.Округлить(' + str(scale) + ')')
            if 'ДлинаЦелойЧасти' in field:
                precision = _integer(field['ДлинаЦелойЧасти'], 'ДлинаЦелойЧасти', maximum=38)
                bound = str(10 ** precision)
                reject(value + ' >= ' + bound + ' или ' + value + ' <= -' + bound, 'precision')
            for key, op in [('МинимальноеЗначение', '<'), ('МаксимальноеЗначение', '>')]:
                if key in field:
                    bound = field[key]
                    if type(bound) not in {int, float} or not Decimal(str(bound)).is_finite():
                        raise InputError(key + ' требует конечное Число')
                    reject(value + ' ' + op + ' ' + c.literal(bound, 'Число'), key)
            if ('МинимальноеЗначение' in field and 'МаксимальноеЗначение' in field
                    and field['МинимальноеЗначение'] > field['МаксимальноеЗначение']):
                raise InputError('МинимальноеЗначение больше МаксимальноеЗначение')
        elif set(field) & {'МинимальноеЗначение', 'МаксимальноеЗначение', 'ДлинаЦелойЧасти', 'ДлинаДробнойЧасти'}:
            raise UnsupportedSyntaxError('Числовые ограничения требуют Число: ' + name)
        length = field.get('Длина', field.get('МаксимальнаяДлина'))
        if 'Длина' in field and 'МаксимальнаяДлина' in field and field['Длина'] != field['МаксимальнаяДлина']:
            raise UnsupportedSyntaxError('Противоречивые ограничения длины: ' + name)
        if length is not None:
            if base != 'Строка':
                raise UnsupportedSyntaxError('Длина требует Строка: ' + name)
            length = _integer(length, 'Длина', minimum=1, maximum=1000000)
            reject(value + '.Длина() > ' + str(length), 'length')
        if 'Уникальность' in field and type(field['Уникальность']) is not bool:
            raise InputError('Уникальность требует Булево')
        if field.get('Уникальность'):
            if not unique or not identity or not id_expression:
                raise UnsupportedSyntaxError('Уникальность поддержана для реквизита объекта: ' + name)
            if base not in {'Строка', 'Число'}:
                raise UnsupportedSyntaxError('Уникальность требует скаляр Строка/Число: ' + name)
            lines += [indent + 'для Существующий из ТестСессия.Аудит()',
                indent + '    знч ЗаписьАудита = Существующий как ЧитаемоеСоответствие<Строка, Стд::Объект?>',
                indent + '    если ЗаписьАудита["type"] == ' + lit(identity) + ' и ЗаписьАудита["id"] != ' + id_expression,
                indent + '        знч Поля = ЗаписьАудита["value"] как ЧитаемоеСоответствие<Строка, Стд::Объект?>']
            # NULL values do not participate in uniqueness; scan the saved state.
            series = numbering_series(field, prefix)
            condition = 'Поля[' + lit(name) + '] == ' + expr
            if series:
                condition += ' и ' + ' и '.join('СериализацияJson.ЗаписатьОбъект(Поля[' + lit(k) + ']) == СериализацияJson.ЗаписатьОбъект(' + v + ')' for k, v in series)
            period = field.get('Периодичность', 'Непериодический')
            if period != 'Непериодический':
                dates = [f for f in fields if f['Имя'] == 'Дата' and f['Тип'] in {'Дата', 'ДатаВремя'}]
                if len(dates) != 1 or period not in {'Год', 'Месяц', 'День'}:
                    raise UnsupportedSyntaxError('Уникальность по периоду требует Дата/ДатаВремя Дата')
                parts = {'Год': ['Год'], 'Месяц': ['Год', 'Месяц'], 'День': ['Год', 'Месяц', 'День']}[period]
                condition += ' и ' + ' и '.join('(новый ' + dates[0]['Тип'] + '(Поля["Дата"] как Строка)).' + p + ' == ' + prefix + 'Дата.' + p for p in parts)
            lines += [indent + '        если ' + condition,
                indent + '            выбросить новый ИсключениеНедопустимоеСостояние(' + lit('metadata:' + name + ':unique') + ')',
                indent + '        ;', indent + '    ;', indent + ';']
        if nullable:
            indent = inner
            lines.append(indent + ';')
    return '\n'.join(lines) + ('\n' if lines else '')


def numbering_series(field, prefix):
    series = field.get('СерииНумерации', [])
    if (not isinstance(series, list) or any(not isinstance(item, str) for item in series)
            or set(series) - {'Сквозная', 'Префикс', 'Постфикс', 'Владелец'}):
        raise UnsupportedSyntaxError('СерииНумерации вне контракта')
    result = []
    if 'Владелец' in series:
        result.append(('Владелец', prefix + 'Владелец'))
    return result


def numbering(c, element, owner):
    """Local numbering from persisted state; fixed format, series and date periods."""
    code = ''
    fields = element['properties'].get('Реквизиты', [])
    lit = lambda v: c.literal(v, 'Строка')
    for field in fields:
        if 'Автонумерация' not in field:
            continue
        config = field['Автонумерация']
        if not isinstance(config, dict) or set(config) - {'Использовать', 'Префикс', 'Постфикс', 'Формат', 'ДлинаНумерации'}:
            raise UnsupportedSyntaxError('Автонумерация вне контракта')
        if type(config.get('Использовать', True)) is not bool:
            raise InputError('Автонумерация.Использовать требует Булево')
        if not config.get('Использовать', True):
            continue
        name, typ = field['Имя'], field.get('Тип', 'Строка')
        if name not in {'Код', 'Номер'} or typ not in {'Строка', 'Число'}:
            raise UnsupportedSyntaxError('Автонумерация поддержана для Код/Номер Строка/Число')
        fmt = config.get('Формат', {})
        if not isinstance(fmt, dict) or set(fmt) - {'ДлинаПрефикса', 'ДлинаПостфикса', 'РазделительПрефикса', 'РазделительПостфикса'}:
            raise UnsupportedSyntaxError('Формат автонумерации вне контракта')
        for k in ('ДлинаПрефикса', 'ДлинаПостфикса'):
            if k in fmt:
                _integer(fmt[k], k, minimum=-1)
        prefix, postfix = config.get('Префикс', ''), config.get('Постфикс', '')
        pre_sep, post_sep = fmt.get('РазделительПрефикса', ''), fmt.get('РазделительПостфикса', '')
        if any(not isinstance(v, str) for v in (prefix, postfix, pre_sep, post_sep)):
            raise InputError('Префикс/Постфикс/Разделители требуют Строка')
        for key, text in [('ДлинаПрефикса', prefix), ('ДлинаПостфикса', postfix)]:
            if fmt.get(key, -1) >= 0 and fmt[key] != len(text):
                raise UnsupportedSyntaxError('Формат требует точную длину фиксированного ' + key)
        start, end = prefix + (pre_sep if prefix else ''), (post_sep if postfix else '') + postfix
        width = config.get('ДлинаНумерации', field.get('Длина', 10) - len(start) - len(end))
        width = _integer(width, 'ДлинаНумерации', minimum=1, maximum=38)
        period = field.get('Периодичность', 'Непериодический')
        if period not in {'Непериодический', 'Год', 'Месяц', 'День'}:
            raise UnsupportedSyntaxError('Периодичность нумерации вне контракта')
        if period != 'Непериодический' and not any(f['Имя'] == 'Дата' and f.get('Тип') in {'Дата', 'ДатаВремя'} for f in fields):
            raise UnsupportedSyntaxError('Периодическая нумерация требует Дата/ДатаВремя Дата')
        numbering_series(field, 'этот.')
        if 'Владелец' in field.get('СерииНумерации', []) and not any(f['Имя'] == 'Владелец' for f in fields):
            raise UnsupportedSyntaxError('Серия Владелец требует объявленный реквизит Владелец')
        # Prefix/postfix series are encoded in the number's format. Owner series
        # compares serialized references, independent of generated type names.
        series = ['СериализацияJson.ЗаписатьОбъект(Поля["Владелец"]) == СериализацияJson.ЗаписатьОбъект(этот.Владелец)'] if 'Владелец' in field.get('СерииНумерации', []) else []
        if period != 'Непериодический':
            date_type = next(f['Тип'] for f in fields if f['Имя'] == 'Дата')
            parts = {'Год': ['Год'], 'Месяц': ['Год', 'Месяц'], 'День': ['Год', 'Месяц', 'День']}[period]
            series += ['(новый ' + date_type + '(Поля["Дата"] как Строка)).' + p + ' == этот.Дата.' + p for p in parts]
        code += '    если этот.' + name + (' == 0' if typ == 'Число' else '.Пусто()') + '\n'
        code += '        пер Следующий: Число = 1\n        для Существующий из ТестСессия.Аудит()\n'
        code += '            знч ЗаписьАудита = Существующий как ЧитаемоеСоответствие<Строка, Стд::Объект?>\n'
        code += '            если ЗаписьАудита["type"] != ' + lit(qualified(element)) + '\n                продолжить\n            ;\n'
        code += '            знч Поля = ЗаписьАудита["value"] как ЧитаемоеСоответствие<Строка, Стд::Объект?>\n'
        if series:
            code += '            если не (' + ' и '.join(series) + ')\n                продолжить\n            ;\n'
        if typ == 'Число':
            code += '            знч Значение = Поля[' + lit(name) + '] как Число\n'
        else:
            code += '            знч НомерСтрока = Поля[' + lit(name) + '] как Строка\n'
            code += '            если не НомерСтрока.НачинаетсяС(' + lit(start) + ') или не НомерСтрока.ЗаканчиваетсяНа(' + lit(end) + ') или НомерСтрока.Длина() != ' + str(len(start) + width + len(end)) + '\n                продолжить\n            ;\n'
            code += '            знч Цифры = НомерСтрока.Подстрока(' + str(len(start)) + ', ' + str(len(start) + width) + ')\n'
            code += '            если не Цифры.ПолноеСовпадение(новый Стд::РегулярныеВыражения::Образец("[0-9]+"))\n                продолжить\n            ;\n            знч Значение = новый Число(Цифры)\n'
        code += '            если Значение >= Следующий\n                Следующий = Значение + 1\n            ;\n        ;\n'
        code += '        если Следующий >= ' + str(10 ** width) + '\n            выбросить новый ИсключениеНедопустимоеСостояние("metadata:number:overflow")\n        ;\n'
        if typ == 'Число':
            code += '        этот.' + name + ' = Следующий\n'
        else:
            code += '        знч ЦифрыНомера = Следующий.ВСтроку()\n        этот.' + name + ' = ' + lit(start) + ' + ' + lit('0' * width) + '.Подстрока(0, ' + str(width) + ' - ЦифрыНомера.Длина()) + ЦифрыНомера + ' + lit(end) + '\n'
        code += '    ;\n'
    return code


def object_validation(storage, element, owner, id_text):
    c = storage.contracts
    from .generated_types import union_members
    for fields in [c.fields[owner + '.Данные']] + [c.fields[owner + '.' + t['Имя']] for t in element['properties'].get('ТабличныеЧасти', [])]:
        for field in fields:
            members = union_members(field['Тип']) or []
            if sum(t.rstrip('?').endswith('.Ссылка') for t in members) > 1:
                raise UnsupportedSyntaxError('Объектный union нескольких ссылок требует тегированное хранение: ' + field['Имя'])
    code = numbering(c, element, owner)
    code += validation(c, c.fields[owner + '.Данные'], 'этот.', unique=True,
                       identity=qualified(element), id_expression=id_text)
    for table in element['properties'].get('ТабличныеЧасти', []):
        code += '    для СтрокаТаблицы из этот.' + table['Имя'] + '\n'
        code += validation(c, c.fields[owner + '.' + table['Имя']], 'СтрокаТаблицы.', indent='        ')
        code += '    ;\n'
    return code


def atomic(body, indent='    '):
    """Savepoint per write, including effects of nested original handlers."""
    return (indent + 'знч СнимокЗаписи = ТестСессия.НачатьЗапись()\n' + indent + 'попытка\n'
            + ''.join('    ' + line + '\n' for line in body.splitlines())
            + indent + '    ТестСессия.ЗавершитьЗапись()\n'
            + indent + 'поймать ОшибкаЗаписи: Исключение\n'
            + indent + '    ТестСессия.ОткатитьЗапись(СнимокЗаписи)\n'
            + indent + '    выбросить ОшибкаЗаписи\n' + indent + ';\n')


def session_writes(text):
    # Source/driver commits inside a write cannot publish incomplete effects.
    text = text.replace('метод ПубликацияГраницы()\n', 'метод ПубликацияГраницы()\n    если ГлубинаЗаписи() > 0\n        возврат\n    ;\n')
    return text + '''метод ГлубинаЗаписи(): Число
    знч Ф = новый Файл(Путь + ".writes")
    если не Ф.Существует()
        возврат 0
    ;
    исп Чтение = Ф.ОткрытьПотокЧтения()
    возврат СериализацияJson.ПрочитатьОбъект<Число>(Чтение, Тип<Число>)
;
метод ГлубинаЗаписи(Значение: Число)
    исп Запись = новый Файл(Путь + ".writes").ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Запись, Значение)
;
@Глобально
метод НачатьЗапись(): Строка
    ПроверитьСессию()
    ПроверитьТранзакцию()
    знч Снимок = СериализацияJson.ЗаписатьОбъект(ЧитатьВсе())
    ГлубинаЗаписи(ГлубинаЗаписи() + 1)
    возврат Снимок
;
@Глобально
метод ЗавершитьЗапись()
    ПроверитьСессию()
    ПроверитьТранзакцию()
    ГлубинаЗаписи(ГлубинаЗаписи() - 1)
;
@Глобально
метод ОткатитьЗапись(Снимок: Строка)
    знч Пустой = новый Соответствие<Строка, Соответствие<Строка, Строка>>()
    СохранитьВсе(СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Соответствие<Строка, Строка>>>(Снимок, Пустой.ПолучитьТип()))
    ГлубинаЗаписи(ГлубинаЗаписи() - 1)
    Удалить(".invalid")
;
'''


def storage_options_schema():
    """Public schema for the existing storage contract plus explicit lifecycle."""
    from .test_contracts import obj, STRING
    arbitrary = {'type': 'object'}
    seed = obj({'type': STRING, 'value': arbitrary})
    record = obj({'type': STRING, 'filter': arbitrary, 'rows': {'type': 'array', 'items': arbitrary, 'maxItems': 100}})
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', **obj({
        'backend': {'enum': ['memory', 'postgres']}, 'idType': {'enum': ['Строка', 'Ууид']},
        'transaction': {'type': 'boolean'}, 'lifecycle': {'type': 'boolean'},
        'registers': {'type': 'array', 'items': STRING, 'uniqueItems': True},
        'initial': {'type': 'array', 'items': seed, 'maxItems': 100},
        'initialRegisters': {'type': 'array', 'items': record, 'maxItems': 100}}, required=[])}
