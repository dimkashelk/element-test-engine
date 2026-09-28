"""Deliberately limited SQL adapter for the original shipment handlers.

No platform runtime, general XBQL parser or mock result rows. Only the query
below is translated. Reference identity is (document type, teacher fixture ID).
"""
import re

from .generated_types import ProjectTypes
from .indexer import mask_noncode, METHOD
from .model import resolve
from .platform_mocks import PlatformMocks
from .runtime import method_closure, constructor_types
from .yaml_io import InputError

QUERY = '''ВЫБРАТЬ
РегистрТовары.Номенклатура КАК Номенклатура,
РегистрТовары.КоличествоОстаток КАК Количество
ИЗ РегистрТовары.Остатки КАК РегистрТовары
ГДЕ РегистрТовары.Номенклатура В (%{Товары.Преобразовать(Данные -> Данные.Номенклатура)})'''

SCHEMA = '''
CREATE TABLE smoke.documents(kind text NOT NULL CHECK(kind IN ('Отгрузка','ПоступлениеТоваров')),
 id text NOT NULL, date timestamp NOT NULL, number text NOT NULL, warehouse text,
 PRIMARY KEY(kind,id));
CREATE TABLE smoke.lines(kind text NOT NULL, id text NOT NULL, position integer NOT NULL,
 item text, quantity numeric NOT NULL, price numeric NOT NULL, amount numeric NOT NULL,
 PRIMARY KEY(kind,id,position), FOREIGN KEY(kind,id) REFERENCES smoke.documents(kind,id));
CREATE TABLE smoke.movements(kind text NOT NULL CHECK(kind IN ('Отгрузка','ПоступлениеТоваров')),
 id text NOT NULL, position integer NOT NULL, period timestamp NOT NULL,
 direction text NOT NULL CHECK(direction IN ('Приход','Расход')), item text, warehouse text,
 quantity numeric NOT NULL, PRIMARY KEY(kind,id,position));
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA smoke TO smoke;
'''

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
    definition = definition[:start] + '''    @Глобально
    метод Записать(Замещать: Булево = Истина)
        если не Фильтр.Установлен
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен фильтр")
        ;
        если Замещать
''' + sql_call('DELETE FROM smoke.movements WHERE kind=&kind AND id=&id',
            {'kind': 'Фильтр.ТипДокумента', 'id': 'Фильтр.Ид'}, indent='            ') + '''            ЗапросSql.Выполнить()
        ;
''' + sql_call('SELECT COALESCE(MAX(position)+1,0) AS position FROM smoke.movements WHERE kind=&kind AND id=&id',
            {'kind': 'Фильтр.ТипДокумента', 'id': 'Фильтр.Ид'}, select=True, variable='ИндексЗапрос', indent='        ') + '''        исп Индексы = ИндексЗапрос.Выполнить()
        Индексы.Следующий()
        пер Индекс = Индексы.Получить("position") как Число
        для Запись из Записи
''' + sql_call('INSERT INTO smoke.movements VALUES (&kind,&id,&position,&period,&direction,&item,&warehouse,&quantity)', {
        'kind': 'Фильтр.ТипДокумента', 'id': 'Фильтр.Ид', 'position': 'Индекс', 'period': 'Запись.Период',
        'direction': 'Запись.ВидЗаписи.ВСтроку()', 'item': 'ТестХранилище.ИдТовара(Запись.Номенклатура)',
        'warehouse': 'ТестХранилище.ИдСклада(Запись.Склад)', 'quantity': 'Запись.Количество'}, indent='            ') + '''            ЗапросSql.Выполнить()
            Индекс += 1
        ;
        если не Замещать
            Записи.Очистить()
        ;
    ;
;
'''
    contracts.definitions[name + '.НаборЗаписей'] = definition
    contracts.method_dependencies[name + '.НаборЗаписей'] = ['ТестХранилище.Соединение']
    contracts.method_dependencies[filter_type] = ['Отгрузка.Ссылка', 'ПоступлениеТоваров.Ссылка']
    return mocks


def adapt_query(method):
    code = mask_noncode(method)
    replacements = []
    for match in re.finditer(r'\bЗапрос\s*\{', code):
        depth, end = 1, match.end()
        while end < len(code) and depth:
            depth += (code[end] == '{') - (code[end] == '}')
            end += 1
        if depth or PlatformMocks.query_key(method[match.end():end-1]) != PlatformMocks.query_key(QUERY):
            raise InputError('UNSUPPORTED: shipment-storage поддерживает только исходный литерал остатков')
        replacements.append((match.start(), end, 'ТестОстатки.СоздатьЗапрос(Товары.Преобразовать(Данные -> Данные.Номенклатура), Соединение как СоединениеSql)'))
    for start, end, replacement in reversed(replacements):
        method = method[:start] + replacement + method[end:]
    return method


def query_module():
    return '''#требуется ТестХранилище.sbsl
#требуется Номенклатура.sbsl
@Глобально
структура СтрокаЗапроса
    пер Номенклатура: Номенклатура.Ссылка?
    пер Количество: Число
;
@Глобально
структура Запрос
    пер Соединение: СоединениеSql?
    пер Товары: Массив<Номенклатура.Ссылка?>
    @Глобально
    метод Выполнить(): Массив<СтрокаЗапроса>
        знч Строки = новый Массив<СтрокаЗапроса>()
        знч Встреченные = новый Массив<Строка?>()
        знч Идентификаторы = Товары.Преобразовать(Товар -> ТестХранилище.ИдТовара(Товар))
''' + sql_call('SELECT item, warehouse, SUM(CASE WHEN direction=\'Приход\' THEN quantity ELSE -quantity END) AS quantity FROM smoke.movements GROUP BY item,warehouse HAVING SUM(CASE WHEN direction=\'Приход\' THEN quantity ELSE -quantity END) <> 0 ORDER BY item,warehouse', {}, select=True, indent='        ') + '''        исп Выборка = ЗапросSql.Выполнить()
        пока Выборка.Следующий()
            знч Ид = ТестХранилище.SqlСтрока(Выборка.Получить("item"))
            если Идентификаторы.Содержит(Ид)
                если Встреченные.Содержит(Ид)
                    выбросить новый ИсключениеНеподдерживаемаяОперация("Несколько складов одного товара: неоднозначная проекция")
                ;
                Встреченные.Добавить(Ид)
                пер Товар: Номенклатура.Ссылка? = Неопределено
                если Ид != Неопределено
                    Товар = новый Номенклатура.Ссылка(Идентификатор = Ид как Строка)
                ;
                Строки.Добавить(новый СтрокаЗапроса(Номенклатура = Товар, Количество = Выборка.Получить("quantity") как Число))
            ;
        ;
        возврат Строки
    ;
;
@Глобально
метод СоздатьЗапрос(Товары: Массив<Номенклатура.Ссылка?>, Соединение: СоединениеSql): Запрос
    возврат новый Запрос(Товары = Товары, Соединение = Соединение)
;
'''


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


def load_method():
    query = 'SELECT number,warehouse,EXTRACT(YEAR FROM date) AS y,EXTRACT(MONTH FROM date) AS m,EXTRACT(DAY FROM date) AS d,EXTRACT(HOUR FROM date) AS h,EXTRACT(MINUTE FROM date) AS n,EXTRACT(SECOND FROM date) AS s FROM smoke.documents WHERE kind=\'Отгрузка\' AND id=&id'
    return '''метод ЗагрузитьОбъект(Заблокировать: Булево = Ложь): Отгрузка.Объект?
    если Заблокировать
        выбросить новый ИсключениеНедопустимоеСостояние("UNSUPPORTED: блокировка ссылки")
    ;
''' + sql_call(query, {'id': 'Идентификатор'}, select=True) + '''    исп Выборка = ЗапросSql.Выполнить()
    если не Выборка.Следующий()
        возврат Неопределено
    ;
    пер Склад: Склады.Ссылка? = Неопределено
    знч ИдСклада = ТестХранилище.SqlСтрока(Выборка.Получить("warehouse"))
    если ИдСклада != Неопределено
        Склад = новый Склады.Ссылка(Идентификатор = ИдСклада как Строка)
    ;
    знч Дата = новый ДатаВремя(''' + ','.join('Выборка.Получить("' + col + '") как Число' for col in 'ymdhns') + ''')
    знч Объект = новый Отгрузка.Объект(Ссылка = новый Отгрузка.Ссылка(Идентификатор = Идентификатор, Соединение = Соединение), Дата = Дата, Номер = Выборка.Получить("number") как Строка, Склад = Склад)
''' + sql_call('SELECT * FROM smoke.lines WHERE kind=\'Отгрузка\' AND id=&id ORDER BY position', {'id': 'Идентификатор'}, select=True, variable='ЗапросСтрок') + '''    исп Строки = ЗапросСтрок.Выполнить()
    пока Строки.Следующий()
        пер Товар: Номенклатура.Ссылка? = Неопределено
        знч ИдТовара = ТестХранилище.SqlСтрока(Строки.Получить("item"))
        если ИдТовара != Неопределено
            Товар = новый Номенклатура.Ссылка(Идентификатор = ИдТовара как Строка)
        ;
        Объект.Товары.Добавить(новый Отгрузка.Товары(Номенклатура = Товар, Количество = Строки.Получить("quantity") как Число, Цена = Строки.Получить("price") как Число, Сумма = Строки.Получить("amount") как Число))
    ;
    возврат Объект
;
'''


def save_method(*, inject_failure=False):
    return '''метод Записать()
    ТестХранилище.Команда(Соединение как СоединениеSql, "BEGIN")
    попытка
        знч Старый = Ссылка.ЗагрузитьОбъект()
        пер До: Отгрузка.Данные
        если Старый == Неопределено
            До = новый Отгрузка.Данные(Дата = Дата, Номер = Номер, Склад = Склад, Товары = Товары)
        иначе
            знч Снимок = Старый как Отгрузка.Объект
            До = новый Отгрузка.Данные(Дата = Снимок.Дата, Номер = Снимок.Номер, Склад = Снимок.Склад, Товары = Снимок.Товары)
        ;
        знч Параметры = новый Отгрузка.ПараметрыЗаписи()
        ПередЗаписью(До, Параметры)
''' + sql_call('INSERT INTO smoke.documents VALUES (\'Отгрузка\',&id,&date,&number,&warehouse) ON CONFLICT(kind,id) DO UPDATE SET date=EXCLUDED.date,number=EXCLUDED.number,warehouse=EXCLUDED.warehouse', {'id': 'Ссылка.Идентификатор', 'date': 'Дата', 'number': 'Номер', 'warehouse': 'ТестХранилище.ИдСклада(Склад)'}, indent='        ') + '''        ЗапросSql.Выполнить()
''' + sql_call('DELETE FROM smoke.lines WHERE kind=\'Отгрузка\' AND id=&id', {'id': 'Ссылка.Идентификатор'}, indent='        ', variable='Удаление') + '''        Удаление.Выполнить()
        пер Индекс = 0
        для Строка из Товары
''' + sql_call('INSERT INTO smoke.lines VALUES (\'Отгрузка\',&id,&position,&item,&quantity,&price,&amount)', {'id': 'Ссылка.Идентификатор', 'position': 'Индекс', 'item': 'ТестХранилище.ИдТовара(Строка.Номенклатура)', 'quantity': 'Строка.Количество', 'price': 'Строка.Цена', 'amount': 'Строка.Сумма'}, indent='            ', variable='Вставка') + '''            Вставка.Выполнить()
            Индекс += 1
        ;
        ПослеЗаписи(До, Параметры)
''' + ('        ТестХранилище.Команда(Соединение как СоединениеSql, "INSERT INTO smoke.missing VALUES (1)")\n' if inject_failure else '') + '''        ТестХранилище.Команда(Соединение как СоединениеSql, "COMMIT")
    поймать Ошибка: Исключение
        ТестХранилище.Команда(Соединение как СоединениеSql, "ROLLBACK")
        выбросить Ошибка
    ;
;
'''


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
    contracts.attach_method('Отгрузка.Ссылка', load_method(), ['ТестХранилище.Соединение', 'Склады.Ссылка', 'Номенклатура.Ссылка'])
    context = 'ТестКонтекст.Объект'
    contracts.fields[context] = contracts.fields['Отгрузка.Объект']
    contracts.definitions[context] = contracts.definitions['Отгрузка.Объект'].replace('    пер Ссылка:', '    пер Соединение: СоединениеSql?\n    пер Ссылка:')
    attached = set()
    for handler in ('ПередЗаписью', 'ПослеЗаписи'):
        for method, types in method_closure(source, handler):
            name = METHOD.search(mask_noncode(method))[1]
            if name in attached:
                continue
            attached.add(name)
            for _, _, constructor in constructor_types(method):
                if constructor not in {'РегистрТовары.НаборЗаписей', 'ИсключениеНедопустимоеСостояние'}:
                    raise InputError('UNSUPPORTED: конструктор вне контракта shipment-storage')
            for typ in types:
                contracts.require(typ)
            contracts.attach_method(context, adapt_query(method).replace('новый РегистрТовары.НаборЗаписей()', 'новый РегистрТовары.НаборЗаписей(Соединение = Соединение)').rstrip() + '\n', types)
    contracts.attach_method(context, save_method(inject_failure=inject_failure), ['РегистрТовары.НаборЗаписей', 'ТестОстатки.Запрос', 'ТестХранилище.Соединение'])
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
        fixture += [sql_call('INSERT INTO smoke.movements VALUES (&kind,&id,&position,&period,&direction,&item,&warehouse,&quantity)', params, variable='Fixture' + str(index)),
                    '    Fixture' + str(index) + '.Выполнить()']
    contracts.definitions['ТестХранилище'] = storage_module()
    contracts.method_dependencies['ТестХранилище'] = ['Номенклатура.Ссылка', 'Склады.Ссылка']
    contracts.definitions['ТестОстатки'] = query_module()
    contracts.method_dependencies['ТестОстатки'] = ['ТестХранилище.Соединение', 'Номенклатура.Ссылка']
    imports = contracts.write(directory)
    (directory / 'ТестХранилище.sbsl').write_text(storage_module())
    (directory / 'ТестОстатки.sbsl').write_text(query_module())
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
