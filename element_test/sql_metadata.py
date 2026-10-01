"""SQL object/record adapters derived from declarations, shared by compatibility profiles."""
from .resolution import qualified
from .yaml_io import InputError


def document_load(contracts, owner):
    from .shipment_storage import sql_call
    obj = owner + '.Объект'
    matches = contracts.resolve(owner)
    if len(matches) != 1:
        raise InputError('Неоднозначный владелец SQL объекта')
    identity = qualified(matches[0])
    return ('метод ЗагрузитьОбъект(Заблокировать: Булево = Ложь): ' + obj + '?\n'
            '    если Заблокировать\n        выбросить новый ИсключениеНеподдерживаемаяОперация("Блокировка не поддержана")\n    ;\n'
            + sql_call('SELECT value::text AS value FROM smoke.objects WHERE type=&type AND id=&id',
                       {'type': contracts.literal(identity, 'Строка'), 'id': 'Идентификатор'}, select=True)
            + '    исп Выборка = ЗапросSql.Выполнить()\n    если не Выборка.Следующий()\n        возврат Неопределено\n    ;\n'
            + '    знч Объект = СериализацияJson.ПрочитатьОбъект<' + obj + '>(Выборка.Получить("value") как Строка, новый ' + obj + '().ПолучитьТип())\n'
            + '    Объект.Ссылка.Соединение = Соединение\n    возврат Объект\n;\n')


def document_save(contracts, owner, *, inject_failure=False):
    from .shipment_storage import sql_call
    obj, data, params = owner + '.Объект', owner + '.Данные', owner + '.ПараметрыЗаписи'
    element = contracts.resolve(owner)[0]
    identity = contracts.literal(qualified(element), 'Строка')
    projection = lambda prefix: ', '.join(f['Имя'] + ' = ' + prefix + f['Имя'] for f in contracts.fields[data])
    snapshot = '{' + ', '.join(contracts.literal(f['Имя'], 'Строка') + ': ' + ('{"Идентификатор": Ссылка.Идентификатор}' if f['Имя'] == 'Ссылка' else f['Имя'])
                             for f in contracts.fields[obj]) + '}'
    return ('метод Записать()\n    ТестХранилище.Команда(Соединение как СоединениеSql, "BEGIN")\n    попытка\n'
            '        знч Старый = Ссылка.ЗагрузитьОбъект()\n        пер До: ' + data + '\n'
            '        если Старый == Неопределено\n            До = новый ' + data + '(' + projection('') + ')\n'
            '        иначе\n            знч Снимок = Старый как ' + obj + '\n            До = новый ' + data + '(' + projection('Снимок.') + ')\n        ;\n'
            '        знч Параметры = новый ' + params + '()\n        ПередЗаписью(До, Параметры)\n'
            + sql_call('INSERT INTO smoke.objects VALUES (&type,&id,CAST(&value AS jsonb)) ON CONFLICT(type,id) DO UPDATE SET value=EXCLUDED.value',
                       {'type': identity, 'id': 'Ссылка.Идентификатор',
                        'value': 'СериализацияJson.ЗаписатьОбъект(' + snapshot + ')'}, indent='        ')
            + '        ЗапросSql.Выполнить()\n        ПослеЗаписи(До, Параметры)\n'
            + ('        ТестХранилище.Команда(Соединение как СоединениеSql, "INSERT INTO smoke.missing VALUES (1)")\n' if inject_failure else '')
            + '        ТестХранилище.Команда(Соединение как СоединениеSql, "COMMIT")\n'
            '    поймать Ошибка: Исключение\n        ТестХранилище.Команда(Соединение как СоединениеSql, "ROLLBACK")\n        выбросить Ошибка\n    ;\n;\n')


def record_set_write(contracts, name):
    from .shipment_storage import sql_call
    identity = contracts.literal(qualified(contracts.resolve(name)[0]), 'Строка')
    key = {'type': identity, 'kind': 'Фильтр.ТипДокумента', 'id': 'Фильтр.Ид'}
    snapshot = '{' + ', '.join(contracts.literal(f['Имя'], 'Строка') + ': Запись.' + f['Имя']
                             for f in contracts.fields[name + '.Запись']) + '}'
    return ('    @Глобально\n    метод Записать(Замещать: Булево = Истина)\n'
            '        если не Фильтр.Установлен\n            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен фильтр")\n        ;\n'
            '        если Замещать\n'
            + sql_call('DELETE FROM smoke.records WHERE type=&type AND registrar_type=&kind AND id=&id', key, indent='            ')
            + '            ЗапросSql.Выполнить()\n        ;\n'
            + sql_call('SELECT COALESCE(MAX(position)+1,0) AS position FROM smoke.records WHERE type=&type AND registrar_type=&kind AND id=&id',
                       key, select=True, variable='ИндексЗапрос', indent='        ')
            + '        исп Индексы = ИндексЗапрос.Выполнить()\n        Индексы.Следующий()\n        пер Индекс = Индексы.Получить("position") как Число\n'
            '        для Запись из Записи\n'
            + sql_call('INSERT INTO smoke.records VALUES (&type,&kind,&id,&position,CAST(&value AS jsonb))',
                       {**key, 'position': 'Индекс', 'value': 'СериализацияJson.ЗаписатьОбъект(' + snapshot + ')'}, indent='            ')
            + '            ЗапросSql.Выполнить()\n            Индекс += 1\n        ;\n        если не Замещать\n            Записи.Очистить()\n        ;\n    ;\n;\n')
