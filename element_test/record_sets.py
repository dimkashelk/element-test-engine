"""Metadata record-set adapter: planning in Python, state and row operations in Script."""
import re
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
    if any(m['namespace'] == e['namespace'] and m['name'] == e['name'] + '.НаборЗаписей'
           and any(n['name'] in {'ПередЗаписью', 'ПослеЗаписи'} for n in m.get('methods', []))
           for m in c.model.get('modules', [])):
        raise UnsupportedSyntaxError('Исходные обработчики набора регистра пока не поддержаны')
    if identity in storage.register_schemas:
        return
    if e['properties'].get('ПериодВходитВОсновнойФильтр', False):
        raise InputError('Период в основном фильтре пока не поддержан')
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
    finally:
        c.namespace, c.imports = previous
    if not dims:
        raise InputError('Набор хранения требует полный фильтр измерений/регистратора')
    ft, st, row = owner + '.ФильтрНабора', owner + '.НаборЗаписей', owner + '.Запись'
    rows = 'Массив<' + row + '>'
    literal = lambda v: c.literal(v, 'Строка')
    # Field order comes from metadata. Tagged union values preserve registrar
    # types even when their reference UUIDs and serialized fields are identical.
    def values(fs, prefix=''):
        return '{' + ', '.join(literal(f['Имя']) + ': ' +
            ('{"type": (' + prefix + f['Имя'] + ' как ' + f['Тип'] + ').ПолучитьТип().ВСтроку(), "value": ' + prefix + f['Имя'] + '}'
             if '|' in f['Тип'] else prefix + f['Имя']) for f in fs) + '}'
    signature = ', '.join(f['Имя'] + ': ' + c.sbsl_type(f['Тип']) for f in dims)
    key = values(dims)
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
        '        Ключ = СериализацияJson.ЗаписатьОбъект(' + key + ')\n' + registrar_set + '    ;\n;\n')
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
        unique = dims + ([{'Имя': 'Период', 'Тип': 'Дата'}] if any(f['Имя'] == 'Период' for f in c.fields[row]) else [])
        checks += ('            знч Идентичность = СериализацияJson.ЗаписатьОбъект(' + values(unique, 'СтрокаНабора.') + ')\n'
            '            если Ключи.СодержитКлюч(Идентичность)\n'
            '                ТестСессия.ИспортитьТранзакцию()\n'
            '                выбросить новый ИсключениеНедопустимоеСостояние("Неуникальный ключ записи")\n            ;\n'
            '            Ключи.Вставить(Идентичность, Истина)\n')
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
        ТестСессия.Записать({literal(identity)}, Ключ, СериализацияJson.ЗаписатьОбъект(Результат))
        если не Замещать
            Записи.Очистить()
        ;
    ;
;
'''
    materialize = f'''    метод Материализовать(JSON: Строка): {rows}
        знч Результат = новый {rows}()
'''
    if accumulation and '|' in registrar_type:
        # Reference JSON alone cannot disambiguate equal-shaped union members.
        # The typed filter supplies the concrete registrar identity for every
        # row in this full-filter bucket; each row receives a fresh reference.
        materialize += f'''        знч Сырые: Объект? = СериализацияJson.ПрочитатьОбъект(JSON)
        для Значение из (Сырые как Массив<Объект?>)
            знч Снимок = Значение как Соответствие<Строка, Объект?>
            Снимок.Удалить("Регистратор")
            знч СтрокаНабора = СериализацияJson.ПрочитатьОбъект<{row}>(СериализацияJson.ЗаписатьОбъект(Снимок), новый {row}().ПолучитьТип())
            СтрокаНабора.Регистратор = {clone_ref('(Фильтр.ЗначениеРегистратора как ' + registrar_type + ')', registrar_type)}
            Результат.Добавить(СтрокаНабора)
        ;
'''
    else:
        materialize += f'        Результат.ДобавитьВсе(СериализацияJson.ПрочитатьОбъект<{rows}>(JSON, Результат.ПолучитьТип()))\n'
    materialize += '        возврат Результат\n    ;\n'
    definition = definition.rsplit(';', 1)[0] + materialize + ';\n'
    c.definitions[st] = definition
    c.method_dependencies[st] = ['ТестСессия.Записи']
    c.method_dependencies[ft] = [f['Тип'] for f in dims]
    storage.register_schemas[identity] = {'owner': identity, 'kind': e['elementType'],
        'rowType': row, 'setType': st, 'filterType': ft, 'filterFields': dims,
        'fields': c.fields[row], 'periodicity': e['properties'].get('Периодичность', 'Непериодический'),
        'backend': storage.config.get('backend', 'memory'), 'contract': CONTRACT,
        'limitations': ['full-filter-only', 'unordered', 'isolated-staging', 'no-balances', 'no-register-handlers']}


def register_setup(storage):
    """Teacher seeds use the same typed write API; never feed read responses."""
    c = storage.contracts
    seeds = storage.config.get('initialRegisters', [])
    if not isinstance(seeds, list) or len(seeds) > 100:
        raise InvalidTestError('storage.initialRegisters требует список до 100 наборов')
    lines = []
    for i, seed in enumerate(seeds):
        if not isinstance(seed, dict) or set(seed) != {'type', 'filter', 'rows'}:
            raise InvalidTestError('Начальный набор требует type, filter и rows')
        from .generated_types import ProjectTypes
        raw = seed['type']
        matches = (ProjectTypes(c.model, c.namespace, c.imports).resolve(raw) if isinstance(raw, str) and '::' in raw
                   else [e for e in c.model['elements'] if e['name'] == raw] if isinstance(raw, str) else [])
        if len(matches) != 1 or qualified(matches[0]) not in storage.register_schemas:
            raise InvalidTestError('Начальный набор требует однозначный owner из storage.registers')
        schema = storage.register_schemas[qualified(matches[0])]
        dims = schema['filterFields']
        filt, rows = seed['filter'], seed['rows']
        if not isinstance(filt, dict) or set(filt) != {f['Имя'] for f in dims}:
            raise InvalidTestError('Начальный набор требует полный фильтр без неизвестных измерений')
        if not isinstance(rows, list) or len(rows) > 100:
            raise InvalidTestError('Начальный набор требует до 100 строк')
        fields = {f['Имя']: f['Тип'] for f in schema['fields']}
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(fields):
                raise InvalidTestError('Начальная строка требует все поля схемы')
            if any(row[f['Имя']] != filt[f['Имя']] for f in dims):
                raise InvalidTestError('Начальная строка не соответствует фильтру')
            if schema['kind'] == 'РегистрНакопления' and row.get('Активность') is not True:
                raise InvalidTestError('Поддержаны начальные движения с Активность=Истина')
        # Validate duplicate information keys before Script so teacher mistakes
        # cannot be reported as student failures.
        if schema['kind'] == 'РегистрСведений':
            import json
            keys = [json.dumps({k: r[k] for k in list(filt) + (['Период'] if 'Период' in fields else [])}, sort_keys=True) for r in rows]
            if len(set(keys)) != len(keys):
                raise InvalidTestError('Неуникальные ключи начальных строк')
        args = ', '.join(f['Имя'] + ' = ' + c.literal(filt[f['Имя']], f['Тип']) for f in dims)
        expr = c.literal(rows, 'Массив<' + schema['rowType'] + '>')
        name = 'НачальныйНабор' + str(i)
        lines += ['    ТестСессия.Событие("teacher:register-setup")',
                  '    знч ' + name + ' = новый ' + schema['setType'] + '()',
                  '    ' + name + '.Фильтр.Установить(' + args + ')',
                  '    ' + name + '.Записи.ДобавитьВсе(' + expr + ')',
                  '    ' + name + '.Записать()']
    return lines


def bind_record_sets(plan):
    """Only proven metadata set receivers and AST loop expressions are adapted."""
    if not plan.storage:
        # Прочитать is not a new spy API; it needs actual storage.
        if any(b.category == 'metadata' and b.operation == 'Прочитать' for b in plan.bindings):
            raise UnsupportedSyntaxError('Прочитать требует storage; mocks.registers не читают состояние')
        return
    sources = {m['sourceFile']: (plan.root/m['sourceFile']).read_text(encoding='utf-8-sig') for m in plan.model['modules']}
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
                if call.name == 'Прочитать' and schema['periodicity'] not in {'Непериодический', 'День'}:
                    raise UnsupportedSyntaxError('Чтение периодического регистра подтверждено только для День')
                if call.name == 'Установить' and typ.endswith('.ФильтрНабора'):
                    expression = next(n for n in node.expression_tree.walk()
                        if n.kind == 'call' and n.children[0].start == call.start and
                        n.children[0].end <= call.end)
                    args = expression.children[1:]
                    # Calls expose callee + argument nodes in AST.
                    required = {f['Имя'] for f in schema['filterFields']}
                    names = []
                    for arg in args:
                        if arg.kind == 'binary' and arg.value == '=' and arg.children[0].kind == 'name':
                            names.append(arg.children[0].value)
                    if len(args) != len(required) or names and (len(names) != len(args) or set(names) != required):
                        raise UnsupportedSyntaxError('Поддержан только полный фильтр набора')
                plan.record_sets.append({**schema, 'sourceFile': symbol.identity.source_file,
                    'symbol': symbol.identity.declaration, 'operation': call.name,
                    'receiverType': c.canonical_type(typ), 'start': symbol.start+call.start,
                    'end': symbol.start + next(n.end for n in node.expression_tree.walk()
                        if n.kind == 'call' and n.children[0].start == call.start and n.children[0].end <= call.end)})
            for statement in node.expression_tree.walk():
                if statement.kind == 'member' and statement.value == '.' and len(statement.children) == 2:
                    receiver, field = statement.children
                    schema, typ = schema_for(receiver.start, receiver.end)
                    if schema and typ.endswith('.ФильтрНабора') and field.value != 'Установить':
                        raise UnsupportedSyntaxError('Поля фильтра набора вне полного Установить не поддержаны')
                if statement.kind == 'binary' and statement.value in {'=', '??='}:
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
                    'start': symbol.start+expr.start, 'end': symbol.start+expr.end})
    finally:
        c.namespace, c.imports = previous
