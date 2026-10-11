"""Original literal/XBQL entry: select bytes, bind inputs, execute the shared AST.

No source method is fabricated and no result is computed in Python. The driver
only supplies typed fixtures and serializes observations from Script.
"""
from hashlib import sha256
from pathlib import Path
import re

from .execution_plan import ExecutionPlan, SourceSymbol, SymbolIdentity, CapabilityBinding
from .generated_types import ProjectTypes
from .indexer import parse_module, method_local_bindings, method_binding_visible
from .local_structures import scalar_structures
from .model import select_check_project
from .resolution import combined_library_symbols, resolve_call_modules, qualified
from .storage import MetadataStorage
from .storage_queries import unique_parameters, generate_public_query, query_name
from .query_plan import query_literals, parse_storage_query
from .query_composites import leaves
from .yaml_io import InputError, InvalidTestError, UnsupportedSyntaxError


def select_source(root, model, selector):
    if (not isinstance(selector, dict) or set(selector) - {'file', 'range', 'sourceHash', 'projectSourceHash'}
            or not isinstance(selector.get('file'), str)):
        raise InvalidTestError('querySource требует file, опциональные range/sourceHash/projectSourceHash')
    relative = Path(selector['file'])
    path = (root / relative).resolve()
    if relative.is_absolute() or '..' in relative.parts or not path.is_relative_to(root.resolve()) or not path.is_file():
        raise InvalidTestError('querySource.file должен быть файлом внутри выбранного проекта')
    digest = sha256(path.read_bytes()).hexdigest()
    for key, actual in [('sourceHash', digest), ('projectSourceHash', model['sourceHash'])]:
        if key in selector and selector[key] != actual:
            raise InvalidTestError('querySource.' + key + ' не соответствует исходнику')
    source = path.read_text(encoding='utf-8-sig')
    if path.suffix == '.xbsl':
        modules = [m for m in model['modules'] if m['sourceFile'] == relative.as_posix()]
        if len(modules) != 1:
            raise InvalidTestError('querySource: модуль отсутствует или неоднозначен')
        module = modules[0]
        candidates = list(query_literals(source))
    elif path.suffix == '.xbql':
        owners = [e for e in model['elements'] if Path(e['sourceFile']).with_suffix('.xbql') == relative]
        if len(owners) != 1:
            raise InvalidTestError('querySource: XBQL требует однозначного владельца метаданных')
        owner = owners[0]
        module = {'namespace': owner['namespace'], 'imports': owner['properties'].get('Импорт', []),
                  'name': owner['name'], 'sourceFile': relative.as_posix(), 'moduleType': 'query'}
        candidates = [(0, len(source), 0, source)]
    else:
        raise InvalidTestError('querySource.file требует .xbsl или .xbql')
    if 'range' in selector:
        span = selector['range']
        if not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span):
            raise InvalidTestError('querySource.range требует [start, end]')
        candidates = [q for q in candidates if list(q[:2]) == span]
    if len(candidates) != 1:
        raise InvalidTestError('querySource: исходное вхождение отсутствует или неоднозначно; задайте range')
    return source, module, candidates[0], digest


def plan_source_query(root, source_model, check):
    root, selected = select_check_project(root, source_model, check)
    conflicts = {'target', 'args', 'context', 'sequence', 'timers', 'formRequirement', 'mocks', 'captureCalls',
                 'captureException', 'observeFailure', 'observe', 'snapshotArgs', 'instanceProbes', 'queryResults',
                 'snapshotConstants', 'clockOracle', 'runtimeDateTime', 'captureExceptionTypes', 'formEffects'}
    if conflicts & check.keys():
        raise InvalidTestError('querySource несовместим с методным контрактом: ' + ', '.join(sorted(conflicts & check.keys())))
    if type(check.get('trace', False)) is not bool:
        raise InvalidTestError('trace требует Булево')
    model = combined_library_symbols(root, selected, source_model)
    source, module, (start, end, body, text), digest = select_source(root, model, check['querySource'])
    c = ProjectTypes(model, module['namespace'], module.get('imports', []))
    c.rename_collisions = True
    c.inline_locals = False
    c.local_source = c.current_source = module['sourceFile']
    c.query_root = root
    c.local_structures = scalar_structures(source) if module['moduleType'] != 'query' else {}
    c.local_by_source = {module['sourceFile']: c.local_structures}
    node = next((n for n in parse_module(source)[0] if n.start <= start < n.end), None) if module['moduleType'] != 'query' else None
    c.query_declaration_annotations = node.annotations if node else ()
    if module['moduleType'] != 'query':
        bindings = method_local_bindings(source, node) if node else {}
        if (method_binding_visible(bindings, 'Запрос', start) or c.resolve('Запрос')
                or 'Запрос' in c.local_structures
                or resolve_call_modules(model['modules'], 'Запрос', c.namespace, c.imports, model['properties'])):
            raise UnsupportedSyntaxError('Затенённый владелец литерала Запрос')
    identity = SymbolIdentity(model['properties'].get('Поставщик', ''), model['properties'].get('Имя', ''),
                              module['namespace'], module['name'], '', module['sourceFile'])
    entry = SourceSymbol(identity, module, text, [], start, end)
    plan = ExecutionPlan(root, model, check, module, source, entry, [], {}, {}, {})
    from .session_contracts import prepare_session, generate_session_types
    prepare_session(plan)
    from .query_context import prepare_query_context
    from .query_access import prepare_query_access
    prepare_query_context(plan, c)
    prepare_query_access(plan, c)
    if not isinstance(check.get('storage'), dict):
        raise InvalidTestError('querySource требует явный storage')
    plan.storage = MetadataStorage(c, check['storage'])
    parameters = check.get('queryParameters', {})
    if not isinstance(parameters, dict):
        raise InvalidTestError('queryParameters требует словарь expression → {type, value}')
    for name, value in parameters.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(value, dict)
                or set(value) != {'type', 'value'} or not isinstance(value['type'], str)):
            raise InvalidTestError('queryParameters: каждый вход требует type и value')
        try:
            c.require(value['type'])
            c.literal(value['value'], value['type'])
        except InputError as exc:
            raise InvalidTestError('queryParameters.' + name + ': ' + str(exc)) from exc
    c.query_parameter_type = lambda expression, at: parameters.get(expression, {}).get('type')
    c.query_slice_clock = plan.clock
    c.query_results = True
    from .query_api import normalized_text
    ast_text = normalized_text(text) if module['moduleType'] == 'query' else text
    query = parse_storage_query(ast_text, c)
    needed = {p.expression for p in query.parameters} - set(getattr(c, 'query_context_expressions', {}))
    if set(parameters) != needed:
        raise InvalidTestError('queryParameters: требуются ровно входы ' + ', '.join(sorted(needed)))
    values = []
    for p in unique_parameters(query):
        if p.expression in getattr(c, 'query_context_expressions', {}):
            values.append(c.query_context_expressions[p.expression])
            continue
        supplied = c.canonical_type(parameters[p.expression]['type'])
        if supplied not in ({p.type, p.type[:-1]} if p.type.endswith('?') else {p.type}):
            raise InvalidTestError('Несовместимый тип queryParameters: ' + p.expression)
        values.append(c.literal(parameters[p.expression]['value'], p.type))
    for leaf in leaves(query):
        if node:
            locals_ = method_local_bindings(source, node)
            if (method_binding_visible(locals_, leaf.source_name.split('::')[0], body + leaf.source_range[0])
                    or leaf.source_name in c.local_structures):
                raise UnsupportedSyntaxError('Затенённый источник запроса: ' + leaf.source_name)
        if leaf.source_kind in {'slice-last', 'slice-first', 'register', 'balance', 'turnover', 'balance-turnover'}:
            if leaf.owner not in plan.storage.register_schemas:
                raise InvalidTestError('Источник регистра требует явный storage.registers: ' + leaf.owner)
        elif leaf.source_kind in {'ordinary', 'table-part', 'collection'}:
            plan.storage.attach(c.resolve(leaf.owner)[0], query_only=True)
        elif leaf.source_kind == 'constants':
            from .session_contracts import add_constants
            state = add_constants(plan, c.resolve(leaf.owner)[0])
            c.query_constants = getattr(c, 'query_constants', {})
            c.query_constants[leaf.owner] = state
    generate_session_types(plan, c)
    plan.contracts = c
    name = generate_public_query(query, c)
    # Validate and generate writes now, while all referenced types are available.
    actions = check.get('queryActions', [{'operation': 'execute'}])
    if not isinstance(actions, list) or not 1 <= len(actions) <= 40:
        raise InvalidTestError('queryActions требует 1..40 действий execute/write')
    initial_setup = plan.storage.setup()
    prepared = []
    for action_index, action in enumerate(actions):
        if not isinstance(action, dict) or action.get('operation') not in {'execute', 'write'}:
            raise InvalidTestError('queryActions: неизвестное действие')
        if action['operation'] == 'execute':
            if set(action) != {'operation'}:
                raise InvalidTestError('execute не принимает дополнительные поля')
            prepared.append(('execute', ''))
        else:
            if set(action) - {'operation', 'objects', 'registers'} or not (set(action) & {'objects', 'registers'}):
                raise InvalidTestError('write требует objects и/или registers')
            config = plan.storage.config
            try:
                plan.storage.config = {**config, 'initial': action.get('objects', []), 'initialRegisters': action.get('registers', [])}
                prepared.append(('write', plan.storage.setup(variable_prefix='ЗаписьДействия' + str(action_index) + '_')))
            finally:
                plan.storage.config = config
    if not any(op == 'execute' for op, _ in prepared):
        raise InvalidTestError('queryActions требует хотя бы одно execute')
    plan._query_driver = (name, values, initial_setup, prepared)
    dependencies = [{'file': e['sourceFile'], 'sourceHash': sha256((root / e['sourceFile']).read_bytes()).hexdigest(),
                     'owner': qualified(e), 'kind': e['elementType']} for e in c.canonical_elements.values()]
    plan.query_entry = {'kind': 'source-query', 'family': 'xbql-file' if module['moduleType'] == 'query' else 'literal',
                        'sourceFile': module['sourceFile'], 'sourceHash': digest, 'projectSourceHash': model['sourceHash'],
                        'owner': {'namespace': module['namespace'], 'name': module['name']},
                        'range': [start, end], 'bodyStart': body, 'text': text, 'astText': ast_text,
                        'dependencies': dependencies, 'parameters': [{'expression': p.expression, 'type': p.type, 'slot': p.slot} for p in unique_parameters(query)],
                        'actions': [op for op, _ in prepared], 'support': 'executor-emulation',
                        'stages': {'validation': 'ready', 'planning': 'ready', 'generation': 'ready', 'execution': 'not-checked'}}
    plan.queries = [{'sourceFile': module['sourceFile'], 'symbol': '', 'start': start, 'end': end, 'bodyStart': body,
                     'text': text, 'ast': query.to_dict(), 'rowType': query.fill['type'] if query.fill else query_name(query) + '.СтрокаРезультата',
                     'backend': plan.storage.config.get('backend', 'memory'), 'resultContract': 'single-pass-044'}]
    plan.bindings.append(CapabilityBinding('query', 'source-entry', query.owner, query.mode,
                         'Исходный query AST без методного входа; сравнение только в SBSL', module['sourceFile'], start, end))
    return plan


def render_source_query(plan, sandbox):
    c, storage = plan.contracts, plan.storage
    name, values, setup, actions = plan._query_driver
    from .session_contracts import render_session
    invocation = '    знч ЗапросДанных = ' + name + '.Создать(' + ', '.join(values) + ')\n'
    multiple = 'queryActions' in plan.check
    if multiple:
        invocation += '    знч Действия = новый Массив<Строка>()\n'
    for index, (op, statements) in enumerate(actions):
        if op == 'write':
            invocation += statements
            continue
        result, rows = 'Выборка' + str(index), 'Строки' + str(index)
        invocation += ('    знч ' + result + ' = ЗапросДанных.Выполнить()\n'
                       '    знч ' + rows + ' = ' + result + '.ВМассив()\n')
        observation = '{"rows": ' + rows + ', "cardinality": ' + rows + '.Размер(), "columns": ' + result + '.ПолучитьОписанияКолонок(), "resource": ' + result + '.ТестНаблюдение()}'
        if multiple:
            invocation += '    Действия.Добавить(СериализацияJson.ЗаписатьОбъект(' + observation + '))\n'
    actual = '{"actions": Действия}' if multiple else observation
    if storage.config.get('transaction'):
        invocation = '    ТестСессия.Начать()\n    попытка\n' + ''.join('    ' + line + '\n' for line in invocation.splitlines()) + '        ТестСессия.Завершить()\n    поймать Ошибка: Исключение\n        ТестСессия.Откатить()\n        выбросить Ошибка\n    ;\n'
        # Results have block scope: snapshot them before leaving the transaction.
        invocation = invocation.replace('        ТестСессия.Завершить()', '        Снимок = СериализацияJson.ЗаписатьОбъект(' + actual + ')\n        ТестСессия.Завершить()')
        invocation = '    пер Снимок: Строка\n' + invocation
        actual = 'СериализацияJson.ПрочитатьОбъект<Объект>(Снимок)'
    invocation += '    ТестСессия.ПроверитьСессию()\n'
    if storage.config.get('backend') == 'postgres':
        setup = '    ТестСессия.НастроитьSql(Путь)\n' + setup + '    ТестСессия.Опубликовать(Путь, Истина)\n'
        invocation += '    ТестСессия.Опубликовать(Путь)\n'
    imports = c.write(sandbox)
    session_setup, metadata = render_session(plan, sandbox)
    plan.rendered_session = (session_setup, metadata)
    identities = {'Scripts::' + typ: qualified(c.canonical_elements[typ.split('.')[0]]) + '.' + typ.partition('.')[2]
                  for typ in c.fields if '.' in typ and typ.split('.')[0] in c.canonical_elements}
    metadata += ', "_typeIdentities": ' + c.literal(identities, 'Соответствие<Строка, Строка>')
    metadata += ', "_rawStorage": Истина, "storageTrace": ТестСессия.Трасса(), "storageDiagnostics": {"active": ТестСессия.ЕстьАктивная(), "locks": ТестСессия.Блокировок()}'
    if multiple:
        metadata += ', "_snapshotMode": "sequence"'
    statements = session_setup + setup + invocation + '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": {"result": ' + actual + ', "storage": ТестСессия.Аудит(Истина)}' + metadata + '}))\n'
    body = '    попытка\n' + ''.join('    ' + line + '\n' for line in statements.splitlines()) + '    поймать Недоступно: ИсключениеНеподдерживаемаяОперация\n        Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "UNSUPPORTED"}))\n    ;\n'
    script = sandbox / 'test.sbsl'
    signature = 'Путь: Строка' if storage.config.get('backend') == 'postgres' else ''
    script.write_text(imports + '\nметод Скрипт(' + signature + ')\n' + body + ';\n', encoding='utf-8')
    return script
