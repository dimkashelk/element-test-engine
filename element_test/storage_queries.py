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
    if query.source_kind in ('union','batch','temporary'):
        from .query_composites import generate_composite_query
        return generate_composite_query(query, contracts)
    if query.source_kind == 'relational':
        from .query_joins import generate_relational_query
        return generate_relational_query(query, contracts)
    if query.source_kind not in {'ordinary','slice-last','slice-first'}:
        from .query_sources import generate_source_query
        return generate_source_query(query,contracts)
    name = query_name(query)
    if name in contracts.definitions:
        return name
    parameters = unique_parameters(query)
    required = {f.name: f for f,_ in query.projections + query.predicates + query.ordering}
    sliced = query.source_kind in {'slice-last','slice-first'}
    if sliced:
        from .query_plan import QueryField
        required.update({f.name: f for f in query.dimensions})
        period_type = 'Дата' if query.periodicity=='День' else 'ДатаВремя'
        required['Период'] = QueryField(query.owner, 'Период', period_type, True)
    def declaration(structure, fields, readonly=False):
        return '@Глобально\nструктура ' + structure + '\n' + ''.join(
            '    ' + ('знч' if readonly else 'пер') + ' ' + label + ': ' + contracts.sbsl_type(f.type) + '\n'
            for f,label in fields) + ';\n'
    text = declaration('Данные', [(f,f.name) for f in required.values()])
    row_type = query.fill['type'] if query.fill else 'СтрокаРезультата'
    if not query.fill:
        text += declaration('СтрокаРезультата', query.projections, readonly=True)
    text += '@Глобально\nструктура Запрос\n'
    text += ''.join('    знч П' + str(p.slot) + ': ' + contracts.sbsl_type(p.type) + '\n' for p in parameters)
    if sliced:
        text += '    знч Граница: '+period_type+'\n'
    text += '    @Глобально\n    метод Выполнить(): Массив<' + row_type + '>\n'
    text += ('        знч Строки = новый Массив<Данные>()\n'
             '        знч Состояние = ТестСессия.ЧитатьВсе()\n'
             '        если Состояние.СодержитКлюч(' + contracts.literal(query.owner,'Строка') + ')\n'
             '            знч Таблица = Состояние[' + contracts.literal(query.owner,'Строка') + ']\n'
             '            для JSON из Таблица.Значения()\n'
             '                знч Значение: Объект? = СериализацияJson.ПрочитатьОбъект(JSON)\n')
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
        first=query.source_kind=='slice-first'
        text += ('                    если СтрокаДанных.Период '+('>=' if first else '<=')+' Граница\n'
                 '                        знч Ключ = СериализацияJson.ЗаписатьОбъект(' + key + ')\n'
                 '                        если не Группы.СодержитКлюч(Ключ) или Группы[Ключ].Период '+('>' if first else '<')+' СтрокаДанных.Период\n'
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
    text += '        знч Результат = новый Массив<' + row_type + '>()\n        для С из Строки\n'
    if query.limit:
        text += '            если Результат.Размер() >= ' + str(query.limit) + '\n                прервать\n            ;\n'
    # Each projected reference is detached independently, including two columns
    # sourced from the same field. Native scalars/enums are immutable.
    def projection(field):
        value = 'С.' + field.name
        if field.type.rstrip('?').endswith('.Ссылка'):
            base = field.type.rstrip('?')
            copied = 'новый ' + base + '(Идентификатор = ' + value + '.Идентификатор)'
            return '(' + value + ' == Неопределено ? Неопределено : ' + copied + ')' if field.type.endswith('?') else copied
        return value
    text += '            Результат.Добавить(новый ' + row_type + '(' + ', '.join(label + ' = ' + projection(f) for f,label in query.projections) + '))\n'
    text += '        ;\n        возврат Результат\n    ;\n;\n'
    if query.ordering:
        text += '@Глобально\nметод Раньше(А: Данные, Б: Данные): Булево\n'
        for f,descending in query.ordering:
            text += '    если А.' + f.name + ' != Б.' + f.name + '\n        возврат А.' + f.name + (' > ' if descending else ' < ') + 'Б.' + f.name + '\n    ;\n'
        text += '    возврат Ложь\n;\n'
    signature = ', '.join('П' + str(p.slot) + ': ' + contracts.sbsl_type(p.type) for p in parameters)
    def captured(p):
        name = 'П' + str(p.slot)
        return ('новый ' + p.type + '(Идентификатор = ' + name + '.Идентификатор)'
                if p.type.endswith('.Ссылка') else name)
    args = ['П' + str(p.slot) + ' = ' + captured(p) for p in parameters]
    prelude = ''
    if sliced:
        period = 'П' + str(query.period_slot) if query.period_slot is not None else 'Неопределено'
        strict_period = next((p.type == period_type for p in parameters if p.slot == query.period_slot),False)
        default = period_type+'{Минимум}' if query.source_kind=='slice-first' else period_type+'.Сейчас(новый ЧасовойПояс("UTC"))'
        boundary = period if strict_period else period + ' ?? '+default
        prelude = ('    знч ДатаГраницы = ' + boundary + '\n'
                   '    ТестСессия.Событие("query:slice-boundary:" + ДатаГраницы.ВСтроку())\n')
        args.append('Граница = ДатаГраницы')
    text += '@Глобально\nметод Создать(' + signature + '): Запрос\n' + prelude + '    возврат новый Запрос(' + ', '.join(args) + ')\n;\n'
    contracts.definitions[name] = text
    contracts.method_dependencies[name] = ['ТестСессия.Записи'] + [f.type for f in required.values()]
    if query.fill:
        contracts.method_dependencies[name].append(row_type)
    return name


def adapt_storage_queries(source, contracts):
    replacements = []
    from .call_types import _local_type
    declarations = parse_module(source)[0]
    node = declarations[0] if declarations else None
    bindings = method_local_bindings(source,node) if node else ()
    owner = next((m for m in contracts.model['modules']
                  if m['sourceFile'] == contracts.current_source),
                 {'name':'', 'namespace':contracts.namespace, 'imports':contracts.imports})
    for start,end,body,text in query_literals(source):
        # Rendering happens after planning all reachable methods. Re-establish
        # the capture scope from this method, never from the last planned one.
        contracts.query_parameter_type = lambda expression, at: _local_type(
            source,node,expression,body+at,bindings,owner,contracts.model
        ) if node and re.fullmatch(IDENT,expression) else None
        query = parse_storage_query(text, contracts)
        name = generate_query(query, contracts)
        replacements.append((start,end,name + '.Создать(' + ', '.join(getattr(contracts,'query_context_expressions',{}).get(p.expression,p.expression) for p in unique_parameters(query)) + ')'))
    for a,b,value in reversed(replacements):
        source = source[:a] + value + source[b:]
    return source


def bind_queries(plan):
    if not plan.storage:
        return
    c = plan.contracts
    c.query_root=plan.root
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
            # A lone IN capture can be a scalar or a collection. Use the actual
            # declaration before choosing its constructor parameter contract.
            c.query_parameter_type = lambda expression, at: _local_type(
                symbol.source,node,expression,body+at,locals_,symbol.owner,plan.model
            ) if re.fullmatch(IDENT,expression) else None
            query = parse_storage_query(text,c)
            from .query_composites import leaves, nodes
            for nested in nodes(query):
                fill = nested.fill
                if fill and (method_binding_visible(locals_,fill['sourceName'].split('::')[0],body+fill['typeRange'][0])
                             or fill['sourceName'] in c.local_by_source.get(c.current_source, {})):
                    raise UnsupportedSyntaxError('Затенённый тип ЗАПОЛНИТЬ: ' + fill['sourceName'] +
                                                 f" ({symbol.start+body+fill['typeRange'][0]}-{symbol.start+body+fill['typeRange'][1]})")
            sources = tuple(leaves(query))
            for source in sources:
                if source.source_kind=='constants':
                    from .session_contracts import add_constants
                    state=add_constants(plan,c.resolve(source.owner)[0])
                    if state['type'] not in c.definitions:
                        from .form_context import add_structure
                        add_structure(c,state['type'],state['fields'])
                    if not hasattr(c,'query_constants'):c.query_constants={}
                    c.query_constants[source.owner]=state
            for source in sources:
                if (method_binding_visible(locals_,source.source_name.split('::')[0],body+source.source_range[0])
                        or source.source_name in c.local_by_source.get(c.current_source, {})):
                    raise UnsupportedSyntaxError('Затенённый источник запроса: ' + source.source_name)
                if source.source_kind in {'slice-last','slice-first','register','balance','turnover','balance-turnover'} and source.owner not in plan.storage.register_schemas:
                    raise UnsupportedSyntaxError('Источник регистра требует явный storage.registers: ' + source.owner)
            for p in query.parameters:
                if p.expression in getattr(c,'query_context_expressions',{}):
                    owner=p.expression.split('.')[0]
                    if method_binding_visible(locals_,owner,body+p.start) or c.resolve(owner) or owner in c.local_by_source.get(c.current_source,{}):
                        raise UnsupportedSyntaxError('Затенённый системный captured параметр: '+p.expression)
                if re.fullmatch(IDENT,p.expression):
                    inferred = _local_type(symbol.source,node,p.expression,body+p.start,locals_,symbol.owner,plan.model)
                    compatible = {p.type, p.type[:-1]} if p.type.endswith('?') else {p.type}
                    if inferred and c.canonical_type(inferred) not in compatible:
                        raise UnsupportedSyntaxError('Несовместимый тип параметра запроса: ' + p.expression)
            name = generate_query(query,c)
            plan.queries.append({'sourceFile': symbol.identity.source_file, 'symbol': symbol.identity.declaration,
                'start': symbol.start + start, 'end': symbol.start + end, 'bodyStart': symbol.start + body,
                'text': text, 'ast': query.to_dict(), 'rowType': query.fill['type'] if query.fill else name + '.СтрокаРезультата',
                'backend': plan.storage.config.get('backend','memory'),
                'limitations': (['implicit-execute-local-temporary-scope', 'last-statement-result', 'validated-index-hints', 'native-XBQL-unavailable'] if query.mode == 'storage-unions-nesting-v1' or query.source_kind in ('union','batch') else ['metadata-derived-virtual-sources', 'left-associated-joins', 'NULL-output-as-Undefined', 'no-unsorted-order-guarantee']
                                if query.source_kind == 'relational' else ['single-source', 'equality-and', 'filled-reference-parameter-only' if query.source_kind == 'slice-last' else 'no-nullable-predicate', 'no-unsorted-order-guarantee']) + ['native-XBQL-and-access-rights-unavailable', 'no-exchange-change-lifecycle', 'no-virtual-source-filter-or-period-expansion']})
            for source in sources:
                if source.source_kind=='users':continue
                element = c.resolve(source.owner)[0]
                if source.source_kind in {'ordinary','table-part','collection'} and element not in plan.storage_elements:
                    plan.storage_elements.append(element)
            plan.module_type_dependencies.setdefault(symbol.owner['sourceFile'],[]).append(name + '.Запрос')
            if any(p.expression in getattr(c,'query_context_expressions',{}) for p in query.parameters):
                from .query_context import OWNER
                plan.module_type_dependencies[symbol.owner['sourceFile']].append(OWNER+'.Ссылка')
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
