"""Metadata-derived storage contracts. Python handles schema/audit, never business logic."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from .resolution import qualified
from .yaml_io import InvalidTestError, InputError


@dataclass(frozen=True)
class StorageSchema:
    identity: str
    kind: str
    fields: tuple
    tables: tuple

    @classmethod
    def from_element(cls, element):
        if element['elementType'] not in {'Справочник', 'Документ'}:
            raise InputError('Хранение поддерживает справочники и документы')
        props = element['properties']
        return cls(qualified(element), element['elementType'],
                   tuple((f['Имя'], f.get('Тип', 'Строка') if f['Имя'] == 'Наименование' else f.get('Тип'))
                         for f in props.get('Реквизиты', [])),
                   tuple((t['Имя'], tuple((f['Имя'], f.get('Тип')) for f in t.get('Реквизиты', [])))
                         for t in props.get('ТабличныеЧасти', [])))


class StorageSession(ABC):
    """Infrastructure boundary implemented in executor adapters and trusted SQL audit."""
    @abstractmethod
    def schema(self): ...

    @abstractmethod
    def audit(self, database, docker): ...


class PostgresSession(StorageSession):
    def schema(self):
        return '''CREATE TABLE smoke.objects(type text NOT NULL, id text NOT NULL, value jsonb NOT NULL,
PRIMARY KEY(type,id));
CREATE TABLE smoke.records(type text NOT NULL, registrar_type text NOT NULL, id text NOT NULL,
position integer NOT NULL, value jsonb NOT NULL, PRIMARY KEY(type,registrar_type,id,position));
CREATE TABLE smoke.form_snapshots(ordinal bigserial PRIMARY KEY, value jsonb NOT NULL);
GRANT SELECT,INSERT,UPDATE,DELETE ON smoke.objects,smoke.records TO smoke;
GRANT SELECT,INSERT ON smoke.form_snapshots TO smoke;
GRANT USAGE ON SEQUENCE smoke.form_snapshots_ordinal_seq TO smoke;'''

    def audit(self, database, docker):
        import json
        result = docker('exec', '-i', database, 'psql', '-U', 'postgres', '-d', 'integration',
                        '-At', '-v', 'ON_ERROR_STOP=1', input='''SELECT COALESCE(json_agg(r ORDER BY type,id),'[]'::json)
FROM (SELECT type,id,value FROM smoke.objects LIMIT 1001) r;''')
        values = json.loads(result)
        if len(values) > 1000:
            raise InputError('Превышен лимит аудита хранилища')
        return values


    def audit_history(self, database, docker):
        import json
        result = docker('exec', '-i', database, 'psql', '-U', 'postgres', '-d', 'integration',
                        '-At', '-v', 'ON_ERROR_STOP=1', input="SELECT COALESCE(json_agg(value ORDER BY ordinal),'[]'::json) FROM smoke.form_snapshots;")
        values = json.loads(result)
        if len(values) > 1000:
            raise InputError('Превышен лимит SQL-аудита границ форм')
        return values


def session_module(path, *, postgres=False, audit_history=False):
    from .runtime import sbsl_literal
    text = """конст Путь = __PATH__
конст Резерв = __BACKUP__
@Глобально
метод ЧитатьВсе(): Соответствие<Строка, Соответствие<Строка, Строка>>
    знч Пустой = новый Соответствие<Строка, Соответствие<Строка, Строка>>()
    знч Ф = новый Файл(Путь)
    если не Ф.Существует()
        возврат Пустой
    ;
    исп Поток = Ф.ОткрытьПотокЧтения()
    возврат СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Соответствие<Строка, Строка>>>(Поток, Пустой.ПолучитьТип())
;
метод СохранитьВсе(Записи: Соответствие<Строка, Соответствие<Строка, Строка>>)
    исп Поток = новый Файл(Путь).ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Поток, Записи)
;
@Глобально
метод Прочитать(Тип: Строка, Ид: Строка): Строка?
    знч Записи = ЧитатьВсе()
    если не Записи.СодержитКлюч(Тип)
        возврат Неопределено
    ;
    знч Таблица = Записи[Тип]
    если не Таблица.СодержитКлюч(Ид)
        возврат Неопределено
    ;
    возврат Таблица[Ид]
;
@Глобально
метод Записать(Тип: Строка, Ид: Строка, Значение: Строка)
    ПроверитьТранзакцию()
    знч Записи = ЧитатьВсе()
    если не Записи.СодержитКлюч(Тип)
        Записи.Вставить(Тип, новый Соответствие<Строка, Строка>())
    ;
    Записи[Тип].Вставить(Ид, Значение)
    СохранитьВсе(Записи)
;
метод Снимок(Имя: Строка)
    исп Поток = новый Файл(Путь + Имя).ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Поток, ЧитатьВсе())
;
метод Восстановить(Имя: Строка)
    исп Поток = новый Файл(Путь + Имя).ОткрытьПотокЧтения()
    знч Пустой = новый Соответствие<Строка, Соответствие<Строка, Строка>>()
    СохранитьВсе(СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Соответствие<Строка, Строка>>>(Поток, Пустой.ПолучитьТип()))
;
метод Удалить(Имя: Строка)
    знч Ф = новый Файл(Путь + Имя)
    если Ф.Существует()
        Файлы.Удалить(Ф)
    ;
;
@Глобально
метод ЕстьАктивная(): Булево
    возврат новый Файл(Путь + ".source").Существует() или новый Файл(Резерв).Существует()
;
@Глобально
метод Событие(Вид: Строка)
    знч Ф = новый Файл(Путь + ".events")
    пер События = новый Массив<Строка>()
    если Ф.Существует()
        исп Чтение = Ф.ОткрытьПотокЧтения()
        События = СериализацияJson.ПрочитатьОбъект<Массив<Строка>>(Чтение, События.ПолучитьТип())
    ;
    События.Добавить(Вид)
    исп Запись = Ф.ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Запись, События)
;
@Глобально
метод Трасса(): Массив<Строка>
    знч Пустой = новый Массив<Строка>()
    знч Ф = новый Файл(Путь + ".events")
    если не Ф.Существует()
        возврат Пустой
    ;
    исп Чтение = Ф.ОткрытьПотокЧтения()
    возврат СериализацияJson.ПрочитатьОбъект<Массив<Строка>>(Чтение, Пустой.ПолучитьТип())
;
@Глобально
метод Заблокировать(Тип: Строка, Ид: Строка)
    если не ЕстьАктивная()
        выбросить новый ТестБизнесИсключения.ИсключениеНетАктивнойТранзакции("Блокировка требует активную транзакцию")
    ;
    знч Ф = новый Файл(Путь + ".locks")
    пер Ключи = новый Соответствие<Строка, Строка>()
    если Ф.Существует()
        исп Чтение = Ф.ОткрытьПотокЧтения()
        Ключи = СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Строка>>(Чтение, Ключи.ПолучитьТип())
    ;
    Ключи.Вставить(СериализацияJson.ЗаписатьОбъект([Тип, Ид]), Ид)
    исп Запись = Ф.ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Запись, Ключи)
    Событие("lock:" + Тип + ":" + Ид)
;
@Глобально
метод Блокировок(): Число
    знч Ф = новый Файл(Путь + ".locks")
    если не Ф.Существует()
        возврат 0
    ;
    знч Пустой = новый Соответствие<Строка, Строка>()
    исп Чтение = Ф.ОткрытьПотокЧтения()
    возврат СериализацияJson.ПрочитатьОбъект<Соответствие<Строка, Строка>>(Чтение, Пустой.ПолучитьТип()).Размер()
;
@Глобально
метод Начать()
    если ЕстьАктивная()
        выбросить новый ИсключениеНеподдерживаемаяОперация("Вложенная оболочка driver")
    ;
    Снимок(".backup")
    Событие("driver:begin")
;
@Глобально
метод Завершить()
    если не новый Файл(Резерв).Существует()
        возврат
    ;
    ПроверитьТранзакцию()
    ПубликацияГраницы()
    Удалить(".backup")
    Удалить(".locks")
    Событие("driver:commit")
;
@Глобально
метод Откатить()
    если не новый Файл(Резерв).Существует()
        возврат
    ;
    Восстановить(".backup")
    Удалить(".backup")
    Удалить(".source")
    Удалить(".locks")
    Событие("driver:rollback")
    Удалить(".invalid")
;
@Глобально
метод НачатьИсходную()
    если новый Файл(Путь + ".source").Существует()
        выбросить новый ИсключениеНеподдерживаемаяОперация("Вложенная исходная транзакция")
    ;
    Снимок(".source")
    Событие("source:begin")
;
@Глобально
метод ЗавершитьИсходную()
    если не новый Файл(Путь + ".source").Существует()
        возврат
    ;
    попытка
        ПроверитьТранзакцию()
        если не новый Файл(Резерв).Существует()
            ПубликацияГраницы()
        ;
    поймать Ошибка: Исключение
        ОткатитьИсходную()
        выбросить Ошибка
    ;
    Удалить(".source")
    если не ЕстьАктивная()
        Удалить(".locks")
    ;
    Событие("source:commit")
;
@Глобально
метод ОткатитьИсходную()
    если не новый Файл(Путь + ".source").Существует()
        возврат
    ;
    Восстановить(".source")
    Удалить(".source")
    если не ЕстьАктивная()
        Удалить(".locks")
    ;
    Событие("source:rollback")
    Удалить(".invalid")
;
@Глобально
метод ИспортитьТранзакцию()
    если ЕстьАктивная()
        исп Поток = новый Файл(Путь + ".invalid").ОткрытьПотокЗаписи()
        СериализацияJson.ЗаписатьОбъект(Поток, Истина)
    ;
;
метод ПроверитьТранзакцию()
    если новый Файл(Путь + ".invalid").Существует()
        выбросить новый ИсключениеНедопустимоеСостояние("Транзакция непригодна после нарушения уникальности")
    ;
;
@Глобально
метод Аудит(Сырой: Булево = Ложь): Массив<Объкт?>
    знч Записи = ЧитатьВсе()
    знч Результат = новый Массив<Объект?>()
    для Тип из Записи.Ключи()
        для Ид из Записи[Тип].Ключи()
            пер Значение: Объект? = Записи[Тип][Ид]
            если не Сырой
                знч Прочитанное: Объект? = СериализацияJson.ПрочитатьОбъект(Записи[Тип][Ид])
                Значение = Прочитанное
            ;
            Результат.Добавить({"type": Тип, "id": Ид, "value": Значение})
        ;
    ;
    возврат Результат
;
""".replace('Объкт', 'Объект').replace('__PATH__', sbsl_literal(path, 'Строка')).replace(
        '__BACKUP__', sbsl_literal(path + '.backup', 'Строка'))
    if not postgres:
        text += """метод ПубликацияГраницы()
;
@Глобально
метод ПроверитьСессию()
    если новый Файл(Путь + ".failed").Существует()
        выбросить новый ТестБизнесИсключения.СбойХранилища("Сбой файла состояния сессии")
    ;
;
"""
    if postgres:
        text += """@Глобально
метод ПроверитьСессию()
    если новый Файл(Путь + ".failed").Существует()
        выбросить новый ТестБизнесИсключения.СбойХранилища("Backend публикации завершился отказом")
    ;
;
@Глобально
метод НастроитьSql(Конфигурация: Строка)
    исп Запись = новый Файл(Путь + ".sql").ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Запись, Конфигурация)
;
метод ПубликацияГраницы()
    исп Чтение = новый Файл(Путь + ".sql").ОткрытьПотокЧтения()
    знч Прочитанное: Объект? = СериализацияJson.ПрочитатьОбъект(Чтение)
    знч Конфигурация = Прочитанное как Строка
    Опубликовать(Конфигурация)
;
@Глобально
метод Опубликовать(ПутьКонфигурации: Строка, Подготовка: Булево = Ложь)
    ПроверитьСессию()
    знч Записи = ЧитатьВсе()
    знч Снимок = СериализацияJson.ЗаписатьОбъект(Записи)
    знч Опубликованный = новый Файл(Путь + ".published")
    если Опубликованный.Существует()
        исп Чтение = Опубликованный.ОткрытьПотокЧтения()
        знч Прочитанное: Объект? = СериализацияJson.ПрочитатьОбъект(Чтение)
        если (Прочитанное как Строка) == Снимок
            возврат
        ;
    ;
    исп Поток = новый Файл(ПутьКонфигурации).ОткрытьПотокЧтения()
    знч ПрочитаннаяКонфигурация: Объект? = СериализацияJson.ПрочитатьОбъект(Поток)
    знч Конфигурация = ПрочитаннаяКонфигурация как Соответствие<Строка, Объект?>
    исп Соединение = новый СоединениеSql(Конфигурация["connection"] как Строка)
    Соединение.СоздатьЗапросБезВыборки("BEGIN").Выполнить()
    попытка
        для Тип из Записи.Ключи()
            для Ид из Записи[Тип].Ключи()
                знч Запрос = Соединение.СоздатьЗапросБезВыборки("INSERT INTO smoke.objects VALUES (&type,&id,CAST(&value AS jsonb)) ON CONFLICT(type,id) DO UPDATE SET value=EXCLUDED.value")
                Запрос.УстановитьЗначениеПараметра("type", Тип)
                Запрос.УстановитьЗначениеПараметра("id", Ид)
                Запрос.УстановитьЗначениеПараметра("value", Записи[Тип][Ид])
                Запрос.Выполнить()
            ;
        ;
        Соединение.СоздатьЗапросБезВыборки("COMMIT").Выполнить()
        исп Фиксация = Опубликованный.ОткрытьПотокЗаписи()
        СериализацияJson.ЗаписатьОбъект(Фиксация, Снимок)
        Событие("sql:commit")
    поймать Ошибка: Исключение
        исп Отказ = новый Файл(Путь + ".failed").ОткрытьПотокЗаписи()
        СериализацияJson.ЗаписатьОбъект(Отказ, Истина)
        Соединение.СоздатьЗапросБезВыборки("ROLLBACK").Выполнить()
        выбросить Ошибка
    ;
;
"""
    if postgres and audit_history:
        sql = "INSERT INTO smoke.form_snapshots(value) SELECT COALESCE(jsonb_agg(jsonb_build_object('type',type,'id',id,'value',value) ORDER BY type,id),'[]'::jsonb) FROM smoke.objects"
        commit = '        Соединение.СоздатьЗапросБезВыборки("COMMIT").Выполнить()'
        text = text.replace(commit, '        Соединение.СоздатьЗапросБезВыборки(' + sbsl_literal(sql, 'Строка') + ').Выполнить()\n' + commit)
    # A source catch must not turn file infrastructure failures into a graded
    # result. Record the failure outside the staging snapshot, so rollback
    # cannot clear it; the driver always checks this marker before publishing.
    # The out-of-band output also invalidates the executor response if the
    # filesystem cannot persist the marker and a source catch swallows that.
    text = text.replace('метод ЧитатьВсе():', 'метод ЧитатьВсеВнутренний():', 1)
    text = text.replace('метод СохранитьВсе(Записи:', 'метод СохранитьВсеВнутренний(Записи:', 1)
    text += """метод СбойФайла()
    исп Поток = новый Файл(Путь + ".failed").ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Поток, Истина)
;
@Глобально
метод ЧитатьВсе(): Соответствие<Строка, Соответствие<Строка, Строка>>
    попытка
        возврат ЧитатьВсеВнутренний()
    поймать Ошибка: Исключение
        Консоль.Записать("ELEMENT_STORAGE_FAILURE")
        СбойФайла()
        выбросить новый ТестБизнесИсключения.СбойХранилища("Не удалось прочитать файл состояния")
    ;
;
метод СохранитьВсе(Записи: Соответствие<Строка, Соответствие<Строка, Строка>>)
    попытка
        СохранитьВсеВнутренний(Записи)
    поймать Ошибка: Исключение
        Консоль.Записать("ELEMENT_STORAGE_FAILURE")
        СбойФайла()
        выбросить новый ТестБизнесИсключения.СбойХранилища("Не удалось записать файл состояния")
    ;
;
"""
    return text


class MetadataStorage:
    def __init__(self, contracts, config, *, audit_history=False):
        if not isinstance(config, dict) or set(config) - {'backend', 'idType', 'initial', 'initialRegisters', 'transaction', 'registers'}:
            raise InvalidTestError('storage принимает backend, idType, initial, initialRegisters, transaction, registers')
        if config.get('backend', 'memory') not in {'memory', 'postgres'}:
            raise InputError('Неподдержанный backend storage')
        if config.get('idType', 'Строка') not in {'Строка', 'Ууид'}:
            raise InputError('storage.idType поддерживает Строка и Ууид')
        if not isinstance(config.get('transaction', False), bool):
            raise InvalidTestError('storage.transaction должен быть Булево')
        self.contracts, self.config = contracts, config
        self.attached = set()
        self.register_schemas = {}
        contracts.reference_id_type = config.get('idType', 'Строка')
        import uuid
        self.path = '/tmp/element-storage-' + uuid.uuid4().hex + '.json'
        from .source_contracts import EXCEPTION_OWNER
        canonical = EXCEPTION_OWNER + '.ИсключениеНетАктивнойТранзакции'
        contracts.definitions.setdefault(canonical, '@Глобально\nисключение ИсключениеНетАктивнойТранзакции\n;\n')
        failure_type = EXCEPTION_OWNER + '.СбойХранилища'
        contracts.definitions.setdefault(failure_type, '@Глобально\nисключение СбойХранилища\n;\n')
        contracts.method_dependencies['ТестСессия'] = [canonical]
        contracts.definitions['ТестСессия'] = session_module(self.path, postgres=config.get('backend') == 'postgres', audit_history=audit_history)
        registers = config.get('registers', [])
        if not isinstance(registers, list) or len(set(str(r) for r in registers)) != len(registers):
            raise InvalidTestError('storage.registers требует список уникальных типов')
        for register in registers:
            self.attach_register(register)

    def attach_register(self, name):
        """Reuse declaration-derived signatures, replace all spy behavior with state operations."""
        from .record_sets import attach_register
        return attach_register(self, name)

    def attach(self, element):
        c = self.contracts
        schema = StorageSchema.from_element(element)
        if schema.identity in self.attached:
            return
        self.attached.add(schema.identity)
        owner = c.canonical_type(schema.identity)
        obj, ref, data = owner + '.Объект', owner + '.Ссылка', owner + '.Данные'
        for typ in (obj, ref, data):
            c.require(typ)
        literal = lambda v: c.literal(v, 'Строка')
        id_type = c.reference_id_type
        id_text = 'Идентификатор.ВСтроку()' if id_type == 'Ууид' else 'Идентификатор'
        c.attach_method(ref, f'''метод ЗагрузитьОбъект(Заблокировать: Булево = Ложь): {obj}?
    если Заблокировать
        ТестСессия.Заблокировать({literal(schema.identity)}, {id_text})
    ;
    знч JSON = ТестСессия.Прочитать({literal(schema.identity)}, {id_text})
    если JSON == Неопределено
        возврат Неопределено
    ;
    возврат СериализацияJson.ПрочитатьОбъект<{obj}>(JSON как Строка, новый {obj}().ПолучитьТип())
;
''', ['ТестСессия.Записи'])
        c.definitions[owner + '.Менеджер'] = f'''@Глобально
метод ПолучитьСсылку(Ид: {id_type}): {ref}
    возврат новый {ref}(Идентификатор = Ид)
;
@Глобально
метод СоздатьОбъект(): {obj}
    возврат новый {obj}()
;
'''
        if id_type == 'Ууид':
            c.definitions[owner + '.Менеджер'] += f'''@Глобально
метод СсылкаПоИд(Ид: Ууид?): {ref}
    возврат новый {ref}(Идентификатор = Ид == Неопределено ? новый Ууид() : Ид как Ууид)
;
'''
        projection = ', '.join(f['Имя'] + ' = этот.' + f['Имя'] for f in c.fields[data])
        previous_projection = ', '.join(f['Имя'] + ' = Снимок.' + f['Имя'] for f in c.fields[data])
        before = (f'    знч Старый = Ссылка.ЗагрузитьОбъект()\n    пер До: {data}\n'
                  f'    если Старый == Неопределено\n        До = новый {data}({projection})\n'
                  f'    иначе\n        знч Снимок = Старый как {obj}\n'
                  f'        До = новый {data}({previous_projection})\n    ;\n')
        handlers = c.methods.get(obj, '')
        calls_before = '    ПередЗаписью(До, новый ' + owner + '.ПараметрыЗаписи())\n' if 'метод ПередЗаписью(' in handlers else ''
        calls_after = '    ПослеЗаписи(До, новый ' + owner + '.ПараметрыЗаписи())\n' if 'метод ПослеЗаписи(' in handlers else ''
        if calls_before or calls_after:
            c.require(owner + '.ПараметрыЗаписи')
        snapshot = ', '.join(f['Имя'] + ' = этот.' + f['Имя'] for f in c.fields[obj])
        stored = 'новый ' + obj + '(' + snapshot + ')'
        if 'ТестСостояниеНовизны' in c.definitions[obj]:
            stored = '{' + ', '.join(literal(f['Имя']) + ': этот.' + f['Имя'] for f in c.fields[obj]) + '}'
        id_text = 'Ссылка.Идентификатор.ВСтроку()' if id_type == 'Ууид' else 'Ссылка.Идентификатор'
        c.attach_method(obj, f'''метод Записать()
{before}{calls_before}    ТестСессия.Записать({literal(schema.identity)}, {id_text}, СериализацияJson.ЗаписатьОбъект({stored}))
{calls_after};
''', ['ТестСессия.Записи', data])

    def setup(self):
        c = self.contracts
        initial = self.config.get('initial', [])
        if not isinstance(initial, list) or len(initial) > 100:
            raise InvalidTestError('storage.initial требует список до 100 записей')
        lines = []
        for item in initial:
            if not isinstance(item, dict) or set(item) != {'type', 'value'}:
                raise InvalidTestError('storage.initial запись требует type и value')
            owner = item['type'].removesuffix('.Объект')
            matches = c.resolve(owner)
            if len(matches) != 1:
                raise InvalidTestError('Неоднозначный тип начального состояния')
            self.attach(matches[0])
            value = item['value']
            if not isinstance(value, dict) or 'Ссылка' not in value:
                raise InvalidTestError('Начальная запись требует Ссылка')
            typ = c.canonical_type(owner + '.Объект')
            obj = c.literal(value, typ)
            if 'ТестСостояниеНовизны' in c.definitions[typ]:
                obj = '{' + ', '.join(c.literal(f['Имя'], 'Строка') + ': (' + obj + ').' + f['Имя'] for f in c.fields[typ]) + '}'
            identifier = value['Ссылка'].get('Идентификатор')
            c.literal(identifier, c.reference_id_type)
            lines.append('    ТестСессия.Записать(' + c.literal(qualified(matches[0]), 'Строка') + ', '
                         + c.literal(identifier, 'Строка') + ', СериализацияJson.ЗаписатьОбъект(' + obj + '))')
        from .record_sets import register_setup
        lines.extend(register_setup(self))
        return '\n'.join(lines) + ('\n' if lines else '')
