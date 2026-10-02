"""Declaration-derived staged query adapter; every data operation runs in Script."""
from hashlib import sha256
import json
import re

from .query_plan import parse_storage_query, query_literals
from .indexer import IDENT, parse_module, method_local_bindings, method_binding_visible, mask_noncode
from .yaml_io import UnsupportedSyntaxError


def unique_parameters(query):
    return sorted({p.slot: p for p in reversed(query.parameters)}.values(), key=lambda p: p.slot)


def query_name(query):
    schema = query.to_dict()
    schema['parameters'] = [{'type': p.type, 'slot': p.slot} for p in unique_parameters(query)]
    return 'ТестЗапрос' + sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16]


def generate_query(query, contracts):
    """Only metadata/AST become code; no rows, expected values or Python filter."""
    name = query_name(query)
    if name in contracts.definitions:
        return name
    parameters = unique_parameters(query)
    required = {f.name: f for f,_ in query.projections + query.predicates + query.ordering}
    sliced = query.source_kind == 'slice-last'
    if sliced:
        from .query_plan import QueryField
        required.update({f.name: f for f in query.dimensions})
        required['Период'] = QueryField(query.owner, 'Период', 'Дата', True)
    def declaration(structure, fields, readonly=False):
        return '@Глобально\nструктура ' + structure + '\n' + ''.join(
            '    ' + ('знч' if readonly else 'пер') + ' ' + label + ': ' + f.type + '\n'
            for f,label in fields) + ';\n'
    text = declaration('Данные', [(f,f.name) for f in required.values()])
    text += declaration('СтрокаРезультата', query.projections, readonly=True)
    text += '@Глобально\nструктура Запрос\n'
    text += ''.join('    знч П' + str(p.slot) + ': ' + p.type + '\n' for p in parameters)
    if sliced:
        text += '    знч Граница: Дата\n'
    text += '    @Глобально\n    метод Выполнить(): Массив<СтрокаРезультата>\n'
    text += ('        знч Строки = новый Массив<Данные>()\n'
             '        знч Состояние = ТестСессия.ЧитатьВсе()\n'
             '        если Состояние.СодержитКлюч(' + contracts.literal(query.owner,'Строка') + ')\n'
             '            знч Таблица = Состояние[' + contracts.literal(query.owner,'Строка') + ']\n'
             '            для JSON из Таблица.Значения()\n'
             '                знч Значение = СериализацияJson.ПрочитатьОбъект(JSON)\n')
    if sliced:
        text += '                для ЗначениеСтроки из (Значение как Массив<Объект?>)\n                    знч Снимок = ЗначениеСтроки как Соответствие<Строка, Объект?>\n'
    else:
        text += '                знч Снимок = Значение как Соответствие<Строка, Объект?>\n'
    projected = '{' + ', '.join(contracts.literal(f.name,'Строка') + ': Снимок[' + contracts.literal(f.name,'Строка') + ']'
                              for f in required.values()) + '}'
    text += ('                знч Проекция = СериализацияJson.ЗаписатьОбъект(' + projected + ')\n'
             '                знч СтрокаДанных = СериализацияJson.ПрочитатьОбъект<Данные>(Проекция, новый Данные().ПолучитьТип())\n')
    if sliced:
        # Group only after the inclusive boundary. WHERE is applied to complete
        # slices below; a resource predicate must never resurrect an older row.
        key = '{' + ', '.join(contracts.literal(f.name,'Строка') + ': {"type": ' +
            contracts.literal(f.type,'Строка') + ', "value": СтрокаДанных.' + f.name + '}' for f in query.dimensions) + '}'
        text = text.replace('        знч Строки = новый Массив<Данные>()\n',
            '        знч Строки = новый Массив<Данные>()\n        знч Группы = новый Соответствие<Строка, Данные>()\n')
        text += ('                    если СтрокаДанных.Период <= Граница\n'
                 '                        знч Ключ = СериализацияJson.ЗаписатьОбъект(' + key + ')\n'
                 '                        если не Группы.СодержитКлюч(Ключ) или Группы[Ключ].Период < СтрокаДанных.Период\n'
                 '                            Группы[Ключ] = СтрокаДанных\n'
                 '                        ;\n                    ;\n                ;\n            ;\n        ;\n'
                 '        для СтрокаДанных из Группы.Значения()\n')
    condition = ' и '.join(('СтрокаДанных.' + f.name + ' != Неопределено и ' if f.type.endswith('?') else '') +
                           'СтрокаДанных.' + f.name + ' == П' + str(slot) for f,slot in query.predicates) or 'Истина'
    text += '                если ' + condition + '\n'
    if query.ordering:
        # Insertion sort keeps tie order internal; no tie order is promised.
        text += '                    пер Позиция = 0\n                    пока Позиция < Строки.Размер() и не ' + name + '.Раньше(СтрокаДанных, Строки[Позиция])\n                        Позиция += 1\n                    ;\n'
        text += '                    Строки.Вставить(Позиция, СтрокаДанных)\n'
    else:
        text += '                    Строки.Добавить(СтрокаДанных)\n'
    text += ('                ;\n        ;\n' if sliced else '                ;\n            ;\n        ;\n')
    text += '        знч Результат = новый Массив<СтрокаРезультата>()\n        для С из Строки\n'
    if query.limit:
        text += '            если Результат.Размер() >= ' + str(query.limit) + '\n                прервать\n            ;\n'
    text += '            Результат.Добавить(новый СтрокаРезультата(' + ', '.join(label + ' = С.' + f.name for f,label in query.projections) + '))\n'
    text += '        ;\n        возврат Результат\n    ;\n;\n'
    if query.ordering:
        text += '@Глобально\nметод Раньше(А: Данные, Б: Данные): Булево\n'
        for f,descending in query.ordering:
            text += '    если А.' + f.name + ' != Б.' + f.name + '\n        возврат А.' + f.name + (' > ' if descending else ' < ') + 'Б.' + f.name + '\n    ;\n'
        text += '    возврат Ложь\n;\n'
    signature = ', '.join('П' + str(p.slot) + ': ' + p.type for p in parameters)
    def captured(p):
        name = 'П' + str(p.slot)
        return ('новый ' + p.type + '(Идентификатор = ' + name + '.Идентификатор)'
                if p.type.endswith('.Ссылка') else name)
    args = ['П' + str(p.slot) + ' = ' + captured(p) for p in parameters]
    prelude = ''
    if sliced:
        period = 'П' + str(query.period_slot) if query.period_slot is not None else 'Неопределено'
        strict_period = next((p.type == 'Дата' for p in parameters if p.slot == query.period_slot),False)
        boundary = period if strict_period else period + ' ?? Дата.Сейчас(новый ЧасовойПояс("UTC"))'
        prelude = ('    знч ДатаГраницы = ' + boundary + '\n'
                   '    ТестСессия.Событие("query:slice-boundary:" + ДатаГраницы.ВСтроку())\n')
        args.append('Граница = ДатаГраницы')
    text += '@Глобально\nметод Создать(' + signature + '): Запрос\n' + prelude + '    возврат новый Запрос(' + ', '.join(args) + ')\n;\n'
    contracts.definitions[name] = text
    contracts.method_dependencies[name] = ['ТестСессия.Записи'] + [f.type for f in required.values()]
    return name


def adapt_storage_queries(source, contracts):
    replacements = []
    for start,end,body,text in query_literals(source):
        query = parse_storage_query(text, contracts)
        name = generate_query(query, contracts)
        replacements.append((start,end,name + '.Создать(' + ', '.join(p.expression for p in unique_parameters(query)) + ')'))
    for a,b,value in reversed(replacements):
        source = source[:a] + value + source[b:]
    return source


def bind_queries(plan):
    if not plan.storage:
        return
    c = plan.contracts
    from .execution_plan import CapabilityBinding
    from .call_types import _local_type
    from .resolution import resolve_call_modules
    for symbol in plan.symbols:
        c.current_source = symbol.owner['sourceFile']
        c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
        node = parse_module(symbol.source)[0][0]
        locals_ = method_local_bindings(symbol.source,node)
        query_variables = {}
        for start,end,body,text in query_literals(symbol.source):
            project_owners = resolve_call_modules(plan.model['modules'], 'Запрос', c.namespace,
                                                  c.imports, plan.model.get('properties'))
            if (method_binding_visible(locals_, 'Запрос', start) or project_owners
                    or 'Запрос' in c.local_by_source.get(c.current_source, {}) or c.resolve('Запрос')):
                raise UnsupportedSyntaxError('Затенённый владелец литерала Запрос')
            query = parse_storage_query(text,c)
            if (query.source_kind == 'slice-last' and
                    (method_binding_visible(locals_,query.source_name.split('::')[0],body+query.source_range[0])
                     or query.source_name in c.local_by_source.get(c.current_source, {}))):
                raise UnsupportedSyntaxError('Затенённый источник СрезПоследних')
            if query.source_kind == 'slice-last' and query.owner not in plan.storage.register_schemas:
                raise UnsupportedSyntaxError('СрезПоследних требует явный storage.registers: ' + query.owner)
            for p in query.parameters:
                if re.fullmatch(IDENT,p.expression):
                    inferred = _local_type(symbol.source,node,p.expression,body+p.start,locals_,symbol.owner,plan.model)
                    compatible = {p.type, 'Дата'} if p.type == 'Дата?' else {p.type}
                    if inferred and c.canonical_type(inferred) not in compatible:
                        raise UnsupportedSyntaxError('Несовместимый тип параметра запроса: ' + p.expression)
            name = generate_query(query,c)
            plan.queries.append({'sourceFile': symbol.identity.source_file, 'symbol': symbol.identity.declaration,
                'start': symbol.start + start, 'end': symbol.start + end, 'bodyStart': symbol.start + body,
                'text': text, 'ast': query.to_dict(), 'rowType': name + '.СтрокаРезультата',
                'backend': plan.storage.config.get('backend','memory'),
                'limitations': ['single-source', 'equality-and', 'filled-reference-parameter-only' if query.source_kind == 'slice-last' else 'no-nullable-predicate', 'no-unsorted-order-guarantee']})
            element = c.resolve(query.owner)[0]
            if query.source_kind == 'ordinary' and element not in plan.storage_elements:
                plan.storage_elements.append(element)
            plan.module_type_dependencies.setdefault(symbol.owner['sourceFile'],[]).append(name + '.Запрос')
            plan.bindings.append(CapabilityBinding('query','Выполнить',query.owner,query.mode,
                'Выборка в executor по текущему staging; параметры при создании, новые строки при Выполнить',
                symbol.identity.source_file,symbol.start+start,symbol.start+end))
            line = symbol.source[symbol.source.rfind('\n',0,start)+1:start]
            declaration = re.fullmatch(rf'\s*(?:знч|пер)\s+({IDENT})\s*=\s*', line)
            if declaration:
                query_variables[declaration[1]] = end
        # The adapter materializes a detached native array and holds no cursor.
        # Lower only resources proven to be its query result; unrelated исп
        # declarations retain native resource semantics.
        code = mask_noncode(symbol.source)
        for statement in node.expression_tree.walk():
            if statement.kind != 'statement' or statement.value != 'исп' or len(statement.children) != 1:
                continue
            expression = statement.children[0]
            call = re.fullmatch(rf'({IDENT})\.Выполнить\s*\(\s*\)', code[expression.start:expression.end])
            member = expression.children[0] if expression.kind == 'call' and len(expression.children) == 1 else None
            direct = (member is not None and member.kind == 'member' and len(member.children) == 2
                      and member.children[0].kind == 'query' and member.children[1].value == 'Выполнить')
            if not call and not direct:
                continue
            if call:
                initialized = query_variables.get(call[1])
                if initialized is None or initialized >= statement.start or re.search(rf'\b{re.escape(call[1])}\s*=(?!=)',code[initialized:statement.start]):
                    continue
            plan.source_transforms.setdefault((symbol.identity.source_file,symbol.identity.declaration),[]).append(
                (statement.start,statement.start+3,'знч'))
            plan.bindings.append(CapabilityBinding('query-resource','materialized-result','Запрос',
                'detached-array-no-cursor','Материализация без ресурсов: закрытие области не изменяет снимок',
                symbol.identity.source_file,symbol.start+statement.start,symbol.start+statement.end))
