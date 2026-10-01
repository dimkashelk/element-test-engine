"""Resolved source resource/type contracts. Never dispatch on business names or answers."""
from dataclasses import dataclass
import re

from .indexer import (IDENT, call_code, lex, parse_module, method_call_expressions,
                      method_local_bindings, method_binding_visible)
from .resolution import resolve_call_modules
from .yaml_io import InputError

EXCEPTION_TYPES = {'ИсключениеВалидации', 'ИсключениеНетАктивнойТранзакции'}
EXCEPTION_OWNER = 'ТестБизнесИсключения'


@dataclass(frozen=True)
class ResourceScope:
    source_file: str
    symbol: str
    start: int
    end: int
    scope_start: int
    scope_end: int
    contract: str
    backend: str
    limitations: tuple


def _statements(tree):
    for node in tree.walk():
        if node.kind == 'statement':
            yield node


def _scope_end(tree, statement):
    """The lexical branch, not the enclosing if/try including sibling branches."""
    containers = [n for n in tree.walk() if n.kind in {'body', 'block'}
                  and n.start <= statement.start < n.end]
    scope = max(containers, key=lambda n: n.start)
    boundary = next((n.start for n in scope.children if n.start > statement.start
                     and n.value in {'иначе', 'иначе если', 'поймать', 'вконце', 'когда'}), None)
    if boundary is None:
        # Closing semicolon is absent from the expression tree, so use verified tokens.
        return scope, None
    return scope, boundary


def bind_source_contracts(plan):
    plan.source_transforms = {}
    plan.resources = []
    plan.exception_types = set()
    graph, active_calls = {}, []
    sources = {s.identity.source_file: (plan.root / s.identity.source_file).read_text(encoding='utf-8-sig')
               for s in plan.symbols}
    declarations = {path: parse_module(source)[0] for path,source in sources.items()}
    def destinations(symbol, call):
        from .call_types import infer_receiver_type, receiver_object_modules
        node = parse_module(symbol.source)[0][0]
        inferred = infer_receiver_type(symbol.source,node,call,symbol.owner,plan.model,
                                       declarations,sources,method_local_bindings(symbol.source,node))
        resolved = receiver_object_modules(inferred,symbol.owner,plan.model)
        if resolved:
            return resolved
        return resolve_call_modules(plan.model['modules'],call.receiver,symbol.owner['namespace'],
                  symbol.owner.get('imports', []),plan.model.get('properties')) if call.receiver else [symbol.owner]
    registry = __import__('element_test.execution_plan', fromlist=['CapabilityBinding']).CapabilityBinding
    for symbol in plan.symbols:
        source, owner = symbol.source, symbol.owner
        node = parse_module(source)[0][0]
        tree = node.expression_tree
        bindings = method_local_bindings(source, node)
        key = (symbol.identity.source_file, symbol.identity.declaration)
        spans = plan.source_transforms.setdefault(key, [])
        graph[key] = set()
        scopes = []
        code = call_code(source)
        # Script 9.0 can skip enclosing handlers/finally when no typed catch in
        # a nested try matches. Explicit rethrow restores language propagation.
        # Apply structurally to source tries, including dependency methods.
        for block in tree.walk():
            if block.kind != 'block' or block.value != 'попытка':
                continue
            catches = [n for n in block.children if n.value == 'поймать']
            if not catches or any(re.search(r'(?<![\w])Исключение(?![\w])', code[n.start:n.end].partition(':')[2])
                                  for n in catches):
                continue
            boundary = next((n.start for n in block.children if n.value == 'вконце'), None)
            if boundary is None:
                boundary = max(t.start for t in lex(code) if block.start < t.start < block.end and t.value == ';')
            ending = source.rfind('\n', 0, boundary) + 1
            if source[ending:boundary].strip():
                raise InputError('Конец попытки требует отдельную строку для повторного выброса')
            indent = source[source.rfind('\n', 0, block.start) + 1:block.start]
            from hashlib import sha256
            error = 'ТестНеперехвачено' + sha256((str(key)+str(block.start)).encode()).hexdigest()[:12]
            while re.search(rf'\b{error}\b', code):
                error += '_'
            spans.append((ending, ending, indent + 'поймать ' + error + ': Исключение\n'
                          + indent + '    выбросить ' + error + '\n'))
            plan.bindings.append(registry('language', 'catch-propagation', 'попытка',
                'script9.0-explicit-rethrow', 'Неподходящие typed catch передают исключение внешней области',
                key[0], symbol.start + block.start, symbol.start + block.end))
        for call in method_call_expressions(source, node):
            candidates = destinations(symbol,call)
            for dest in candidates:
                target = (dest['sourceFile'], call.name)
                if any(s.identity.source_file == target[0] and s.identity.declaration == target[1] for s in plan.symbols):
                    graph[key].add(target)
            if call.receiver not in {'Транзакции', 'Стд::БазаДанных::Транзакции'} or candidates:
                continue
            if method_binding_visible(bindings, call.receiver, call.receiver_start):
                raise InputError('Затенённый получатель Транзакции не является платформенным контрактом')
            if call.name == 'ЕстьАктивная':
                if not plan.check.get('storage'):
                    raise InputError('API транзакции требует storage')
                spans.append((call.receiver_start, call.receiver_end, 'ТестСессия'))
                plan.bindings.append(registry('resource-api', call.name, call.receiver, 'storage-session',
                    'Состояние транзакции изолированной сессии', key[0], symbol.start + call.start,
                    symbol.start + call.end))
                continue
            if call.name != 'Начать':
                raise InputError('Неподдержанный API транзакции: ' + str(call.name))
            if not plan.check.get('storage'):
                raise InputError('Исходная транзакция требует storage')
            statement = next((s for s in _statements(tree) if s.start <= call.start and call.end <= s.end), None)
            if statement is None or statement.value != 'исп' or len(statement.children) != 1:
                raise InputError('Начать поддержан только как исходный ресурс исп')
            expression = statement.children[0]
            if code[statement.start:call.receiver_start].strip() != 'исп' or expression.kind != 'call' or expression.start != call.start:
                raise InputError('Именованный ресурс/явное завершение транзакции пока UNSUPPORTED')
            if re.sub(r'\s+', '', code[call.receiver_end:expression.end]) != '.Начать()':
                raise InputError('Неподдержанная сигнатура Транзакции.Начать')
            scope, end = _scope_end(tree, statement)
            if any(n.kind == 'lambda' and n.start <= statement.start < n.end for n in tree.walk()):
                raise InputError('Ресурс транзакции в лямбде пока UNSUPPORTED')
            if end is None:
                closing = [t.start for t in lex(code) if scope.start < t.start < scope.end and t.value == ';']
                if not closing:
                    raise InputError('Не удалось проверить конец области ресурса')
                end = closing[-1]
            # Insert before indentation, so generated catch belongs to our try.
            ending = source.rfind('\n', 0, end) + 1
            if source[ending:end].strip():
                raise InputError('Конец области ресурса требует отдельную строку')
            if any(a <= statement.start < b or statement.start <= a < end for a,b in scopes):
                raise InputError('Вложенные исходные транзакции пока UNSUPPORTED')
            scopes.append((statement.start, end))
            indent = source[source.rfind('\n', 0, statement.start) + 1:statement.start]
            from hashlib import sha256
            error = 'ТестОшибка' + sha256((str(key)+str(statement.start)).encode()).hexdigest()[:12]
            while re.search(rf'\b{error}\b', code):
                error += '_'
            spans.append((statement.start, statement.end, 'ТестСессия.НачатьИсходную()\n' + indent + 'попытка'))
            tail = (indent + 'поймать ' + error + ': Исключение\n' + indent + '    ТестСессия.ОткатитьИсходную()\n'
                    + indent + '    выбросить ' + error + '\n' + indent + 'вконце\n'
                    + indent + '    ТестСессия.ЗавершитьИсходную()\n' + indent + ';\n')
            spans.append((ending, ending, tail))
            resource = ResourceScope(key[0], key[1], symbol.start + statement.start,
                    symbol.start + statement.end, symbol.start + statement.end, symbol.start + end,
                    'lexical-source-transaction-v1', plan.check['storage'].get('backend','memory'),
                    ('isolated-session', 'no-source-nesting', 'anonymous-resource'))
            plan.resources.append(resource)
            plan.bindings.append(registry('resource', 'Начать', call.receiver, 'storage-session',
                    'Лексическая исходная транзакция; обычный выход фиксирует, исключение откатывает',
                    key[0], symbol.start + call.start, symbol.start + call.end))
        for a,b in scopes:
            for call in method_call_expressions(source, node):
                if a < call.start < b:
                    active_calls.append((key, call))
        # Exception type tokens; strings/comments and ordinary member names remain untouched.
        for typ in EXCEPTION_TYPES:
            pattern = rf'(?<![\w.:])(?:Стд::)?{typ}(?![\w:])'
            matches = list(re.finditer(pattern, code))
            if not matches:
                continue
            original = (plan.root / key[0]).read_text(encoding='utf-8-sig')
            if re.search(rf'\b(?:исключение|структура|метод)\s+{typ}\b', call_code(original)) or any(
                    e['name'] == typ for e in plan.model['elements']):
                raise InputError('Проектное объявление исключения требует собственный контракт: ' + typ)
            if typ in bindings:
                raise InputError('Локальное имя исключения затеняет платформенный тип: ' + typ)
            if re.search(r'\b(?:ПолучитьТип|ТипЗнч|Представление)\s*\(', code):
                raise InputError('Отражение/представление адаптированного исключения пока UNSUPPORTED')
            plan.exception_types.add(typ)
            for match in matches:
                if match.start() < node.header_end:
                    continue  # Headers use canonical type bindings in renderer.
                spans.append((*match.span(), EXCEPTION_OWNER + '.' + typ))
                plan.bindings.append(registry('business-exception', 'type', typ, 'custom-exception-9.0',
                      'Отдельный тип с базой Исключение; публичное имя сохраняется', key[0],
                      symbol.start + match.start(), symbol.start + match.end()))
            for expr in tree.walk():
                if expr.kind == 'call' and expr.children and expr.children[0].kind == 'new':
                    name = expr.children[0].children[0].value
                    if name in {typ, 'Стд::' + typ} and len(expr.children) != 2:
                        raise InputError('Поддержан конструктор бизнес-исключения с одним Описание: Строка')
    # Conservative static closure: a second source resource reachable while one is alive
    # is rejected before execution, including handlers (which share the source transaction).
    resource_keys = {(r.source_file, r.symbol) for r in plan.resources}
    def reachable_resource(starts):
        visited, pending = set(), list(starts)
        while pending:
            key = pending.pop()
            if key in visited:
                continue
            visited.add(key)
            if key in resource_keys:
                return True
            pending.extend(graph.get(key, ()))
        return False
    for key, call in active_calls:
        symbol = next(s for s in plan.symbols if (s.identity.source_file,s.identity.declaration) == key)
        starts = [(d['sourceFile'], call.name) for d in destinations(symbol,call)]
        if call.name == 'Записать':
            starts += [(s.identity.source_file,s.identity.declaration) for s in plan.symbols
                       if s.identity.declaration in {'ПередЗаписью','ПослеЗаписи'}]
        if reachable_resource(starts):
            raise InputError('Вложенная исходная транзакция через зависимость пока UNSUPPORTED')


def bind_system_ids(plan):
    c = plan.contracts
    for symbol in plan.symbols:
        c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
        source = symbol.source
        tree = parse_module(source)[0][0].expression_tree
        spans = plan.source_transforms[(symbol.identity.source_file,symbol.identity.declaration)]
        for node in tree.walk():
            if node.kind != 'call' or not node.children or node.children[0].kind != 'new':
                continue
            typ = node.children[0].children[0].value
            if not typ.endswith('.Объект'):
                continue
            matches = c.resolve(typ.removesuffix('.Объект'))
            if len(matches) != 1 or matches[0]['elementType'] not in {'Справочник','Документ'}:
                continue
            if any(f['Имя'] == 'Ид' for f in matches[0]['properties'].get('Реквизиты', [])):
                continue  # ordinary field, not a system constructor parameter
            ids = [arg for arg in node.children[1:] if arg.kind in {'binary', 'assignment'} and arg.value == '='
                   and arg.children[0].value == 'Ид']
            if not ids:
                continue
            if len(ids) != 1 or any(arg.kind in {'binary', 'assignment'} and arg.children[0].value == 'Ссылка' for arg in node.children[1:]):
                raise InputError('Ид конструктора повторён или конфликтует со Ссылка')
            if not plan.storage or c.reference_id_type != 'Ууид':
                raise InputError('Системный Ид конструктора требует storage.idType: Ууид')
            arg = ids[0]
            name, value = arg.children
            owner = c.canonical_type(typ.removesuffix('.Объект'))
            spans.extend([(name.start,name.end,'Ссылка'),
                          (value.start,value.start,owner+'.СсылкаПоИд('), (value.end,value.end,')')])
            plan.bindings.append(__import__('element_test.execution_plan',fromlist=['CapabilityBinding']).CapabilityBinding(
                'metadata', 'system-constructor-id', matches[0]['namespace']+'::'+matches[0]['name'],
                'native-uuid-reference', 'Ид конструктора связан со ссылкой по разрешённому виду метаданных',
                symbol.identity.source_file,symbol.start+arg.start,symbol.start+arg.end))
