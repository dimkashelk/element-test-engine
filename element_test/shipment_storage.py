"""Deliberately limited SQL adapter for the original shipment handlers.

No platform runtime, general XBQL parser or mock result rows. Only the query
below is translated. Reference identity is (document type, teacher fixture ID).
"""
import re

from .generated_types import ProjectTypes
from .indexer import mask_noncode, parse_module
from .model import resolve
from .platform_mocks import PlatformMocks
from .runtime import method_closure, constructor_types
from .yaml_io import InputError

QUERY = '''ВЫБРАТЬ
РегистрТовары.Номенклатура КАК Номенклатура,
РегистрТовары.КоличествоОстаток КАК Количество
ИЗ РегистрТовары.Остатки КАК РегистрТовары
ГДЕ РегистрТовары.Номенклатура В (%{Товары.Преобразовать(Данные -> Данные.Номенклатура)})'''

from .storage import PostgresSession
SCHEMA = PostgresSession().schema() + """
CREATE VIEW smoke.documents AS SELECT regexp_replace(type,'^.*::','') AS kind,id,
(value->>'Дата')::timestamp AS date,value->>'Номер' AS number,
value->'Склад'->>'Идентификатор' AS warehouse FROM smoke.objects;
CREATE VIEW smoke.lines AS SELECT regexp_replace(o.type,'^.*::','') AS kind,id,
(t.position-1)::integer AS position,t.row->'Номенклатура'->>'Идентификатор' AS item,
(t.row->>'Количество')::numeric AS quantity,(t.row->>'Цена')::numeric AS price,
(t.row->>'Сумма')::numeric AS amount FROM smoke.objects o,
jsonb_array_elements(o.value->'Товары') WITH ORDINALITY t(row,position);
CREATE VIEW smoke.movements AS SELECT registrar_type AS kind,id,position,
(value->>'Период')::timestamp AS period,value->>'ВидЗаписи' AS direction,
value->'Номенклатура'->>'Идентификатор' AS item,
value->'Склад'->>'Идентификатор' AS warehouse,(value->>'Количество')::numeric AS quantity
FROM smoke.records;
GRANT SELECT ON smoke.documents,smoke.lines,smoke.movements TO smoke;
"""

# All SQL in the generated adapter is fixed. Inputs always use JDBC parameters.
def sql_call(sql, parameters, *, select=False, indent='    ', variable='ЗапросSql'):
    from .runtime import sbsl_literal
    lines = [indent + 'знч ' + variable + ' = (Соединение как СоединениеSql).СоздатьЗапрос'
             + ('СВыборкой' if select else 'БезВыборки') + '(' + sbsl_literal(sql, 'Строка') + ')']
    lines += [indent + variable + '.УстановитьЗначениеПараметра("' + name + '", ' + 'ТестХранилище.SqlЗначение(' + value + '))'
              for name, value in parameters.items()]
    return '\n'.join(lines) + '\n'


def validate_model(model):
    expected = {
        'Отгрузка': ('Документ', {'Реквизиты': {'Дата': 'ДатаВремя', 'Номер': 'Строка', 'Склад': 'Склады.Ссылка?'}}),
        'РегистрТовары': ('РегистрНакопления', {
            'Измерения': {'Номенклатура': 'Номенклатура.Ссылка?', 'Склад': 'Склады.Ссылка?'},
            'Ресурсы': {'Количество': 'Число'},
            'Реквизиты': {'Регистратор': 'Отгрузка.Ссылка|ПоступлениеТоваров.Ссылка|?'}})}
    for name, (kind, groups) in expected.items():
        found = resolve(model['elements'], name, 'Товары')
        if len(found) != 1 or found[0]['elementType'] != kind:
            raise InputError('shipment-storage: неподдержанный объект ' + name)
        props = found[0]['properties']
        for group, fields in groups.items():
            # Union order differs in raw models; canonicalize independently.
            from .types import parse_type
            actual = {f['Имя']: parse_type(f.get('Тип', ''))[0] for f in props.get(group, [])}
            wanted = {k: parse_type(v)[0] for k, v in fields.items()}
            if actual != wanted:
                raise InputError('shipment-storage: неподдержанная схема ' + name + '.' + group)
        if name == 'РегистрТовары' and props.get('ВидРегистра', 'Остатки') != 'Остатки':
            raise InputError('shipment-storage поддерживает только регистр Остатки')
        if name == 'Отгрузка':
            tables = props.get('ТабличныеЧасти', [])
            if len(tables) != 1 or tables[0]['Имя'] != 'Товары' or {
                f['Имя']: f.get('Тип') for f in tables[0].get('Реквизиты', [])} != {
                    'Номенклатура': 'Номенклатура.Ссылка?', 'Количество': 'Число', 'Цена': 'Число', 'Сумма': 'Число'}:
                raise InputError('shipment-storage: неподдержанная табличная часть')


def reference_helpers(contracts):
    for owner in ('Номенклатура', 'Склады'):
        contracts.require(owner + '.Ссылка')
        # Представление is inherited from SBSL Объект.
    # Представление uses the SBSL base implementation, not platform lookup.


def register(contracts):
    # Reuse only YAML type generation and signatures; replace every spy operation.
    mocks = PlatformMocks(contracts, {'registers': ['РегистрТовары']}, {})
    name = 'РегистрТовары'
    filter_type = name + '.ФильтрНабора'
    contracts.definitions[filter_type] = '''@Глобально
структура ФильтрНабора
    пер ТипДокумента: Строка
    пер Ид: Строка
    пер Установлен: Булево
    @Глобально
    метод Установить(Регистратор: Отгрузка.Ссылка|ПоступлениеТоваров.Ссылка|?)
        если Регистратор == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("UNSUPPORTED: пустой регистратор")
        ;
        если Регистратор это Отгрузка.Ссылка
            ТипДокумента = "Отгрузка"
            Ид = (Регистратор как Отгрузка.Ссылка).Идентификатор
        иначе
            ТипДокумента = "ПоступлениеТоваров"
            Ид = (Регистратор как ПоступлениеТоваров.Ссылка).Идентификатор
        ;
        Установлен = Истина
    ;
;
'''
    definition = contracts.definitions[name + '.НаборЗаписей']
    definition = re.sub(r'        ТестПлатформа\.ЗаписатьВызов\([^\n]*\)\n', '', definition)
    start = definition.index('    @Глобально\n    метод Записать(')
    definition = definition.replace('    пер Фильтр:', '    пер Соединение: СоединениеSql?\n    пер Фильтр:')
    start = definition.index('    @Глобально\n    метод Записать(')
    from .sql_metadata import record_set_write
    definition = definition[:start] + record_set_write(contracts, name)
    contracts.definitions[name + '.НаборЗаписей'] = definition
    contracts.method_dependencies[name + '.НаборЗаписей'] = ['ТестХранилище.Соединение']
    contracts.method_dependencies[filter_type] = ['Отгрузка.Ссылка', 'ПоступлениеТоваров.Ссылка']
    return mocks


def adapt_query(method, contracts=None, queries=None):
    code = mask_noncode(method)
    replacements = []
    for match in re.finditer(r'\bЗапрос\s*\{', code):
        depth, end = 1, match.end()
        while end < len(code) and depth:
            depth += (code[end] == '{') - (code[end] == '}')
            end += 1
        if depth:
            raise InputError('UNSUPPORTED: незакрытый литерал запроса')
        from .query_plan import parse_balance_query
        query = parse_balance_query(method[match.end():end-1], contracts)
        if queries is not None:
            if queries and query.to_dict() != queries[0].to_dict():
                raise InputError('UNSUPPORTED: SQL-профиль поддерживает одну схему запроса')
            queries.append(query)
        replacements.append((match.start(), end, 'ТестОстатки.СоздатьЗапрос(' + query.parameter + ', Соединение как СоединениеSql)'))
    for start, end, replacement in reversed(replacements):
        method = method[:start] + replacement + method[end:]
    return method


def query_module(contracts, query):
    from .query_plan import balance_sql
    element = contracts.resolve(query.register)[0]
    dimensions = element['properties']['Измерения']
    field_type = next(f['Тип'] for f in dimensions if f['Имя'] == query.dimension)
    reference_type = field_type.rstrip('?')
    owner = reference_type.partition('.')[0]
    row = query.dimension_alias
    amount = query.resource_alias
    sql = balance_sql(query, [f['Имя'] for f in dimensions])
    return ('#требуется ТестХранилище.sbsl\n#требуется ' + owner + '.sbsl\n'
            '@Глобально\nструктура СтрокаЗапроса\n    пер ' + row + ': ' + field_type + '\n    пер ' + amount + ': Число\n;\n'
            '@Глобально\nструктура Запрос\n    пер Соединение: СоединениеSql?\n    пер Товары: Массив<' + field_type + '>\n'
            '    @Глобально\n    метод Выполнить(): Массив<СтрокаЗапроса>\n'
            '        знч Строки = новый Массив<СтрокаЗапроса>()\n        знч Встреченные = новый Массив<Строка?>()\n'
            '        знч Идентификаторы = Товары.Преобразовать(Товар -> Товар == Неопределено ? Неопределено : (Товар как ' + reference_type + ').Идентификатор)\n'
            + sql_call(sql, {'register': contracts.literal(query.register, 'Строка')}, select=True, indent='        ')
            + '        исп Выборка = ЗапросSql.Выполнить()\n        пока Выборка.Следующий()\n'
            '            знч Ид = ТестХранилище.SqlСтрока(Выборка.Получить("item"))\n            если Идентификаторы.Содержит(Ид)\n'
            '                если Встреченные.Содержит(Ид)\n                    выбросить новый ИсключениеНеподдерживаемаяОперация("Неоднозначная проекция нескольких измерений")\n                ;\n'
            '                Встреченные.Добавить(Ид)\n                пер Товар: ' + field_type + ' = Неопределено\n'
            '                если Ид != Неопределено\n                    Товар = новый ' + reference_type + '(Идентификатор = Ид как Строка)\n                ;\n'
            '                Строки.Добавить(новый СтрокаЗапроса(' + row + ' = Товар, ' + amount + ' = Выборка.Получить("quantity") как Число))\n'
            '            ;\n        ;\n        возврат Строки\n    ;\n;\n'
            '@Глобально\nметод СоздатьЗапрос(Товары: Массив<' + field_type + '>, Соединение: СоединениеSql): Запрос\n'
            '    возврат новый Запрос(Товары = Товары, Соединение = Соединение)\n;\n')


def storage_module():
    return '''#требуется Номенклатура.sbsl
#требуется Склады.sbsl
@Глобально
метод Открыть(Путь: Строка): СоединениеSql
    исп Поток = новый Файл(Путь).ОткрытьПотокЧтения()
    знч Конфигурация = СериализацияJson.ПрочитатьСоответствие(Поток.ПрочитатьКакСтроку())
    возврат новый СоединениеSql(Конфигурация["connection"] как Строка)
;
@Глобально
метод Команда(Соединение: СоединениеSql, Текст: Строка)
    Соединение.СоздатьЗапросБезВыборки(Текст).Выполнить()
;
@Глобально
метод SqlЗначение(Значение: Объект?): Объект
    если Значение == Неопределено
        возврат Null
    ;
    возврат Значение как Объект
;
@Глобально
метод SqlСтрока(Значение: Объект): Строка?
    если Значение == Null
        возврат Неопределено
    ;
    возврат Значение как Строка
;
@Глобально
метод ИдТовара(Ссылка: Номенклатура.Ссылка?): Строка?
    если Ссылка == Неопределено
        возврат Неопределено
    ;
    возврат (Ссылка как Номенклатура.Ссылка).Идентификатор
;
@Глобально
метод ИдСклада(Ссылка: Склады.Ссылка?): Строка?
    если Ссылка == Неопределено
        возврат Неопределено
    ;
    возврат (Ссылка как Склады.Ссылка).Идентификатор
;
'''


def load_method(contracts):
    from .sql_metadata import document_load
    return document_load(contracts, 'Отгрузка')


def save_method(contracts, *, inject_failure=False):
    from .sql_metadata import document_save
    return document_save(contracts, 'Отгрузка', inject_failure=inject_failure)


def prepare(root, model, check, directory, *, inject_failure=False):
    validate_model(model)
    if any(k in check for k in ('target', 'mocks', 'context', 'args', 'runtimeDateTime')):
        raise InputError('shipment-storage использует фиксированные обработчики; mocks/target не принимаются')
    steps = check.get('steps')
    stock = check.get('stock', [])
    if not isinstance(steps, list) or not steps or len(steps) > 40 or not isinstance(stock, list) or len(stock) > 100:
        raise InputError('shipment-storage требует steps (1..40) и stock (0..100)')
    if any(e['name'] in {'ТестХранилище', 'ТестОстатки', 'ТестКонтекст'} for e in model['elements']):
        raise InputError('Конфликт зарезервированных имён shipment-storage')
    modules = [m for m in model['modules'] if m['name'] == 'Отгрузка.Объект']
    if len(modules) != 1:
        raise InputError('Отгрузка.Объект отсутствует или неоднозначен')
    source = (root / modules[0]['sourceFile']).read_text(encoding='utf-8-sig')
    contracts = ProjectTypes(model, modules[0]['namespace'])
    contracts.require('Отгрузка.Объект')
    contracts.require('Отгрузка.Данные')
    contracts.require('Отгрузка.ПараметрыЗаписи')
    register(contracts)
    reference_helpers(contracts)
    contracts.definitions['Отгрузка.Ссылка'] = contracts.definitions['Отгрузка.Ссылка'].replace('    пер Идентификатор:', '    пер Соединение: СоединениеSql?\n    пер Идентификатор:')
    contracts.attach_method('Отгрузка.Ссылка', load_method(contracts), ['ТестХранилище.Соединение', 'Склады.Ссылка', 'Номенклатура.Ссылка'])
    context = 'ТестКонтекст.Объект'
    contracts.fields[context] = contracts.fields['Отгрузка.Объект']
    contracts.definitions[context] = contracts.definitions['Отгрузка.Объект'].replace('    пер Ссылка:', '    пер Соединение: СоединениеSql?\n    пер Ссылка:')
    from .execution_plan import plan_execution
    plans = []
    queries = []
    attached = set()
    for handler in ('ПередЗаписью', 'ПослеЗаписи'):
        plan = plan_execution(root, model, {'target': {'module': modules[0]['name'], 'namespace': modules[0]['namespace'], 'method': handler},
                              'context': {}, 'args': [{}, {}], 'mocks': {'registers': ['РегистрТовары']}})
        plans.append(plan.to_dict())
        if any(symbol.owner['sourceFile'] != modules[0]['sourceFile'] for symbol in plan.symbols):
            raise InputError('SQL-профиль совместимости требует общий metadata-storage для внешних зависимостей')
        for symbol in plan.symbols:
            method, types = symbol.source, symbol.parameter_types
            name = parse_module(method)[0][0].name
            if name in attached:
                continue
            attached.add(name)
            for _, _, constructor in constructor_types(method):
                if constructor not in {'РегистрТовары.НаборЗаписей', 'ИсключениеНедопустимоеСостояние'}:
                    raise InputError('UNSUPPORTED: конструктор вне контракта shipment-storage')
            for typ in types:
                contracts.require(typ)
            compiled = adapt_query(method, contracts, queries)
            insertions = []
            for start, end, type_name in constructor_types(compiled):
                if type_name == 'РегистрТовары.НаборЗаписей':
                    opening = compiled.find('(', end)
                    closing = compiled.find(')', opening)
                    if opening < 0 or closing < 0 or compiled[opening + 1:closing].strip():
                        raise InputError('UNSUPPORTED: параметры конструктора набора вне SQL-контракта')
                    insertions.append(opening + 1)
            for offset in reversed(insertions):
                compiled = compiled[:offset] + 'Соединение = Соединение' + compiled[offset:]
            contracts.attach_method(context, compiled.rstrip() + '\n', types)
    import json
    combined = {**plans[0], 'entries': [p['entry'] for p in plans],
                'symbols': list({(s['identity']['source_file'],s['identity']['declaration']): s
                                 for p in plans for s in p['symbols']}.values()),
                'bindings': [b for p in plans for b in p['bindings']],
                'backend': 'postgres-generic-objects-records', 'queryMode': 'balance-ast-sql', 'queries': [q.to_dict() for q in queries]}
    for binding in combined['bindings']:
        if binding['category'] == 'metadata':
            binding['adapter'] = 'postgres-generic-objects-records'
            binding['explanation'] = 'Подтверждённая SQL-семантика вида метаданных; подставных вызовов нет'
    (directory / 'execution-plan.json').write_text(json.dumps(combined, ensure_ascii=False, indent=2) + '\n')
    contracts.attach_method(context, save_method(contracts, inject_failure=inject_failure), ['РегистрТовары.НаборЗаписей', 'ТестОстатки.Запрос', 'ТестХранилище.Соединение'])
    setup = ['    исп Соединение = ТестХранилище.Открыть(Путь)', '    знч Результаты = новый Массив<Объект?>()']
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise InputError('Некорректный step')
        action = step.get('action')
        if action == 'write' and set(step) == {'action', 'document'}:
            value = step['document']
            if not isinstance(value, dict) or not {'Ссылка', 'Дата'} <= set(value):
                raise InputError('write требует документ с Ссылка и Дата')
            expression = contracts.literal(value, context)
            setup += ['    знч Документ' + str(index) + ' = ' + expression, '    Документ' + str(index) + '.Соединение = Соединение',
                      '    Документ' + str(index) + '.Ссылка.Соединение = Соединение', '    попытка',
                      '        Документ' + str(index) + '.Записать()',
                      '        Результаты.Добавить("written")', '    поймать Ошибка: Исключение',
                      '        если Ошибка это ИсключениеНедопустимоеСостояние',
                      '            Результаты.Добавить("rejected")',
                      '        иначе если Ошибка это ИсключениеНеподдерживаемаяОперация',
                      '            выбросить Ошибка', '        иначе',
                      '            Результаты.Добавить("error")', '        ;', '    ;']
        elif action in {'load', 'load-fresh'} and set(step) == {'action', 'reference'}:
            reference = contracts.literal(step['reference'], 'Отгрузка.Ссылка')
            setup += ['    знч Ссылка' + str(index) + ' = ' + reference,
                      '    Ссылка' + str(index) + '.Соединение = Соединение',
                      '    знч Снимок' + str(index) + ' = Ссылка' + str(index) + '.ЗагрузитьОбъект()',
                      '    Результаты.Добавить(СнимокВJson(Снимок' + str(index) + '))']
            if action == 'load-fresh':
                setup += ['    (Снимок' + str(index) + ' как Отгрузка.Объект).Товары.Очистить()',
                          '    Результаты.Добавить(СнимокВJson(Ссылка' + str(index) + '.ЗагрузитьОбъект()))']
        elif action == 'clear' and set(step) == {'action', 'reference'}:
            if step['reference'] is None:
                raise InputError('UNSUPPORTED: пустой регистратор')
            reference = contracts.literal(step['reference'], 'Отгрузка.Ссылка|ПоступлениеТоваров.Ссылка|?')
            setup += ['    знч Набор' + str(index) + ' = новый РегистрТовары.НаборЗаписей(Соединение = Соединение)',
                      '    Набор' + str(index) + '.Фильтр.Установить(Регистратор = ' + reference + ')',
                      '    Набор' + str(index) + '.Записать()', '    Результаты.Добавить("cleared")']
        else:
            raise InputError('UNSUPPORTED: неизвестная операция steps')
    # Trusted fixtures are inserted with parameterized SQL before any student method.
    fixture = []
    fixture_positions = {}
    for index, movement in enumerate(stock):
        if not isinstance(movement, dict) or set(movement) != {'reference', 'period', 'direction', 'item', 'warehouse', 'quantity'}:
            raise InputError('stock требует reference/period/direction/item/warehouse/quantity')
        reference = movement['reference']
        contracts.literal(reference, 'Отгрузка.Ссылка|ПоступлениеТоваров.Ссылка|?')
        if not isinstance(reference, dict) or set(reference) != {'type', 'value'} or reference['type'] not in {'Отгрузка.Ссылка', 'ПоступлениеТоваров.Ссылка'}:
            raise InputError('stock.reference требует типизированную ссылку')
        if movement['direction'] not in {'Приход', 'Расход'}:
            raise InputError('Неизвестный вид движения')
        identity = (reference['type'], reference['value']['Идентификатор'])
        position = fixture_positions.get(identity, 0)
        fixture_positions[identity] = position + 1
        params = {'kind': contracts.literal(reference['type'].split('.')[0], 'Строка'),
                  'id': contracts.literal(reference['value']['Идентификатор'], 'Строка'), 'position': str(position),
                  'period': contracts.literal(movement['period'], 'ДатаВремя'),
                  'direction': contracts.literal(movement['direction'], 'Строка'),
                  'item': contracts.literal(movement['item'], 'Строка?'),
                  'warehouse': contracts.literal(movement['warehouse'], 'Строка?'),
                  'quantity': contracts.literal(movement['quantity'], 'Число')}
        params['type'] = contracts.literal('::'.join(filter(None, (contracts.resolve('РегистрТовары')[0]['namespace'], 'РегистрТовары'))), 'Строка')
        params['value'] = 'СериализацияJson.ЗаписатьОбъект(' + contracts.literal({
            'Период': movement['period'], 'ВидЗаписи': movement['direction'],
            'Номенклатура': {'Идентификатор': movement['item']} if movement['item'] is not None else None,
            'Склад': {'Идентификатор': movement['warehouse']} if movement['warehouse'] is not None else None,
            'Количество': movement['quantity']}, 'РегистрТовары.Запись') + ')'
        params = {key: params[key] for key in ('type', 'kind', 'id', 'position', 'value')}
        fixture += [sql_call('INSERT INTO smoke.records VALUES (&type,&kind,&id,&position,CAST(&value AS jsonb))', params, variable='Fixture' + str(index)),
                    '    Fixture' + str(index) + '.Выполнить()']
    contracts.definitions['ТестХранилище'] = storage_module()
    contracts.method_dependencies['ТестХранилище'] = ['Номенклатура.Ссылка', 'Склады.Ссылка']
    contracts.definitions['ТестОстатки'] = query_module(contracts, queries[0])
    contracts.method_dependencies['ТестОстатки'] = ['ТестХранилище.Соединение', 'Номенклатура.Ссылка']
    imports = contracts.write(directory)
    (directory / 'ТестХранилище.sbsl').write_text(storage_module())
    (directory / 'ТестОстатки.sbsl').write_text(query_module(contracts, queries[0]))
    script = imports + '''
метод СнимокВJson(Объект: Отгрузка.Объект?): Объект?
    если Объект == Неопределено
        возврат Неопределено
    ;
    знч Снимок = Объект как Отгрузка.Объект
    возврат {"Ссылка": {"Идентификатор": Снимок.Ссылка.Идентификатор}, "Дата": Снимок.Дата, "Номер": Снимок.Номер, "Склад": Снимок.Склад, "Товары": Снимок.Товары.Преобразовать(Строка -> новый Отгрузка.Товары(Номенклатура = Строка.Номенклатура, Количество = Строка.Количество, Цена = Строка.Цена, Сумма = Строка.Сумма))}
;
метод Скрипт(Путь: Строка)
    попытка
''' + '\n'.join('    ' + line for line in setup[:2]) + '\n' + '\n'.join('    ' + line for line in fixture) + '\n' + '\n'.join('    ' + line for line in setup[2:]) + '''
        если Результаты.Содержит("error")
            Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "ERROR"}))
        иначе
            Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "EXECUTED", "actual": {"actions": Результаты}}))
        ;
    поймать Ошибка: Исключение
        если Ошибка это ИсключениеНеподдерживаемаяОперация
            Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "UNSUPPORTED"}))
        иначе
            Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "ERROR"}))
        ;
    ;
;
'''
    path = directory / 'SqlSmoke.sbsl'  # executor entry path stays fixed
    path.write_text(script)
    return path


def audit(database, docker):
    """Read committed state through trusted psql, independently of student output."""
    import json
    counts = docker('exec', '-i', database, 'psql', '-U', 'postgres', '-d', 'integration', '-At', '-v', 'ON_ERROR_STOP=1',
                    input="SELECT (SELECT count(*) FROM smoke.documents), (SELECT count(*) FROM smoke.lines), (SELECT count(*) FROM smoke.movements);")
    if any(int(value) > 1000 for value in counts.strip().split('|')):
        raise ValueError('Storage audit exceeds its row limit')
    sql = '''SELECT json_build_object(
'documents', COALESCE((SELECT json_agg(d ORDER BY kind,id) FROM
 (SELECT kind,id,to_char(date,'YYYY-MM-DD"T"HH24:MI:SS') AS date,number,warehouse FROM smoke.documents) d),'[]'::json),
'lines', COALESCE((SELECT json_agg(l ORDER BY kind,id,position) FROM smoke.lines l),'[]'::json),
'movements', COALESCE((SELECT json_agg(m ORDER BY kind,id,position) FROM
 (SELECT kind,id,position,to_char(period,'YYYY-MM-DD"T"HH24:MI:SS') AS period,direction,item,warehouse,quantity FROM smoke.movements) m),'[]'::json));'''
    return json.loads(docker('exec', '-i', database, 'psql', '-U', 'postgres', '-d', 'integration', '-At', '-v', 'ON_ERROR_STOP=1', input=sql))
