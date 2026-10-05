"""Resolve bounded dynamic query construction from source, never from fixtures.

Only constant query text is compiled by this standalone adapter. Values, setter
names, repeated execution and type checks run in Script. A project declaration
with the same API spelling is excluded before adaptation.
"""
from dataclasses import dataclass
from hashlib import sha256
import re
from .indexer import IDENT, parse_module, mask_noncode, method_local_bindings, method_binding_visible
from .resolution import resolve_call_modules
from .yaml_io import UnsupportedSyntaxError


@dataclass(frozen=True)
class DynamicQuery:
    start: int
    end: int
    text: str
    text_start: int
    text_end: int
    variable: str


def dynamic_queries(source, model, owner, local_types=()):
    node = parse_module(source)[0][0]
    code = mask_noncode(source)
    for e in node.expression_tree.walk():
        if e.kind != 'call' or not e.children or e.children[0].kind != 'new':
            continue
        typ = e.children[0].children[0].value
        if typ not in {'ПроизвольныйЗапрос', 'Стд::БазаДанных::ПроизвольныйЗапрос'}:
            continue
        from .local_structures import scalar_structures
        if typ in local_types or typ in scalar_structures(source):
            continue
        if (resolve_call_modules(model['modules'], typ, owner['namespace'], owner.get('imports', []), model.get('properties'))
            or any(x['name'] == typ and x['namespace'] == owner['namespace'] for x in model['elements'])):
            continue
        bindings = method_local_bindings(source, node)
        if method_binding_visible(bindings, typ.split('::')[0], e.start):
            raise UnsupportedSyntaxError('Затенённый конструктор Query API: ' + typ)
        if len(e.children) != 2:
            raise UnsupportedSyntaxError('ПроизвольныйЗапрос требует постоянный текст; runtime изменение Текст пока недоступно')
        value = source[e.children[1].start:e.children[1].end]
        if not value.startswith('"') or not value.endswith('"') or '${' in value or '\\' in value or '"' in value[1:-1]:
            raise UnsupportedSyntaxError('Динамически вычисляемый текст Query API пока недоступен')
        line = code[code.rfind('\n', 0, e.start)+1:e.start]
        decl = re.fullmatch(rf'\s*(?:знч|пер)\s+({IDENT})\s*=\s*', line)
        if not decl:
            raise UnsupportedSyntaxError('Query API требует проверяемое локальное объявление')
        yield DynamicQuery(e.start, e.end, value[1:-1], e.children[1].start+1, e.children[1].end-1, decl[1])


def normalized_text(text):
    code = mask_noncode(text)
    # Named parameters have identical offsets after & -> %. Literals/comments
    # are protected by the lexer. & sources are deliberately not fabricated.
    chars = list(text)
    for m in re.finditer(rf'&({IDENT})', code):
        if re.search(r'\bИЗ\s*$', code[:m.start()], re.I):
            raise UnsupportedSyntaxError('Query API collection data source requires native descriptor contract')
        chars[m.start()] = '%'
    return ''.join(chars)


def api_receivers(symbol, model):
    """Prove local query/result receivers before general capability binding."""
    source = symbol.source
    queries = {d.variable: d.end for d in dynamic_queries(source, model, symbol.owner, symbol.local_structures)}
    from .query_plan import query_literals
    code = mask_noncode(source)
    for a, b, _, _ in query_literals(source):
        m = re.fullmatch(rf'\s*(?:знч|пер)\s+({IDENT})\s*=\s*', code[code.rfind('\n', 0, a)+1:a])
        if m:
            queries[m[1]] = b
    results = {p.split(':')[0].strip(): 0 for p in parse_module(source)[0][0].parameters(source) if re.search(r'РезультатЗапроса<', p)}
    for m in re.finditer(rf'\b(?:знч|пер|исп)\s+({IDENT})\s*=\s*({IDENT})\.Выполнить\s*\(\s*\)', code):
        if m[2] in queries and queries[m[2]] < m.start():
            results[m[1]] = m.end()
    return queries, results


def proven_api_call(symbol, model, call):
    if not call.receiver or not re.fullmatch(IDENT, call.receiver):
        return False
    queries, results = api_receivers(symbol, model)
    known = queries if call.name in {'УстановитьПараметр', 'Выполнить'} else results if call.name in {
        'Закрыть', 'ВМассив', 'Пусто', 'Первый', 'ПервыйИлиНеопределено', 'Единственный', 'ЕдинственныйИлиНеопределено'} else {}
    end = known.get(call.receiver)
    if end is None or end >= call.start:
        return False
    code = mask_noncode(symbol.source)
    if re.search(rf'\b{re.escape(call.receiver)}\s*=(?!=)', code[end:call.start]):
        raise UnsupportedSyntaxError('Переприсваивание Query API требует flow-sensitive контракт')
    return True


def prepare_query_types(plan, c):
    from .indexer import method_call_expressions
    from .query_plan import query_literals, parse_storage_query
    from .call_types import _local_type
    c.query_root = plan.root
    c.query_results = bool(plan.check.get('queryResults')) or any(
        re.search(r'\bРезультатЗапроса\s*<', mask_noncode(s.source))
        or any(call.name == 'Закрыть' and proven_api_call(s, plan.model, call)
               for call in method_call_expressions(s.source, parse_module(s.source)[0][0]))
        or list(dynamic_queries(s.source, plan.model, s.owner, s.local_structures)) for s in plan.symbols)
    if plan.check.get('queryResults') is not None and type(plan.check['queryResults']) is not bool:
        from .yaml_io import InvalidTestError
        raise InvalidTestError('queryResults должен быть Булево')
    for symbol in plan.symbols:
        c.current_source = symbol.identity.source_file
        c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
        node = parse_module(symbol.source)[0][0]
        bindings = method_local_bindings(symbol.source, node)
        for _, _, body, text in query_literals(symbol.source):
            fill = re.search(rf'\bЗАПОЛНИТЬ\s+({IDENT})\b', mask_noncode(text))
            local_fill = fill and fill[1] in c.local_by_source.get(c.current_source, {})
            if re.search(r'\bПОРОДИТЬ\b', mask_noncode(text)) or local_fill:
                c.query_parameter_type = lambda expression, at: _local_type(
                    symbol.source, node, expression, body+at, bindings, symbol.owner, plan.model
                ) if re.fullmatch(IDENT, expression) else None
                parse_storage_query(text, c)


def generate_dynamic(d, q, c):
    from .storage_queries import generate_query, unique_parameters
    base = generate_query(q, c)
    from .query_results import wrap_query
    base, result = wrap_query(base, q, c)
    name = 'ТестДинамический' + sha256((base+d.text).encode()).hexdigest()[:16]
    if name in c.definitions:
        return name
    values = []
    for p in unique_parameters(q):
        if not re.fullmatch(IDENT, p.expression):
            raise UnsupportedSyntaxError('Query API параметр должен иметь имя')
        key = c.literal(p.expression, 'Строка')
        values.append('Читать(' + key + ') как ' + c.sbsl_type(p.type))
    c.definitions[name] = ('''@Глобально
структура Запрос
    знч Параметры: Соответствие<Строка, Объект?>
    @Глобально
    метод УстановитьПараметр(Имя: Строка, Значение: Объект?)
        Параметры[Имя] = Значение
    ;
    метод Читать(Имя: Строка): Объект?
        если не Параметры.СодержитКлюч(Имя)
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен параметр запроса: " + Имя)
        ;
        возврат Параметры[Имя]
    ;
    @Глобально
    метод Выполнить(): ''' + result + '\n        возврат ' + base + '.Создать(' + ', '.join(values) + ''').Выполнить()
    ;
;
@Глобально
метод Создать(): Запрос
    возврат новый Запрос(Параметры = новый Соответствие<Строка, Объект?>())
;
''')
    c.method_dependencies[name] = [base + '.Запрос', result]
    return name


def bind_dynamic(plan):
    from .query_plan import parse_storage_query
    from .query_composites import leaves
    from .execution_plan import CapabilityBinding
    c = plan.contracts
    for symbol in plan.symbols:
        c.current_source = symbol.identity.source_file
        c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
        for d in dynamic_queries(symbol.source, plan.model, symbol.owner, symbol.local_structures):
            if not plan.storage:
                raise UnsupportedSyntaxError('Query API требует storage')
            code = mask_noncode(symbol.source)
            if re.search(rf'\b{re.escape(d.variable)}\s*(?:=(?!=)|\.Текст\s*=)', code[d.end:]):
                raise UnsupportedSyntaxError('Изменение Текст/получателя Query API пока недоступно')
            from .call_types import _local_type
            node = parse_module(symbol.source)[0][0]
            bindings = method_local_bindings(symbol.source, node)
            setter_types = {}
            for expr in node.expression_tree.walk():
                member = expr.children[0] if expr.kind == 'call' and len(expr.children) == 3 else None
                if (not member or member.kind != 'member' or member.children[0].value != d.variable
                    or member.children[1].value != 'УстановитьПараметр'):
                    continue
                key = symbol.source[expr.children[1].start:expr.children[1].end]
                value = symbol.source[expr.children[2].start:expr.children[2].end]
                if re.fullmatch(r'"\w+"', key) and re.fullmatch(IDENT, value):
                    typ = _local_type(symbol.source, node, value, expr.children[2].start,
                                      bindings, symbol.owner, plan.model)
                    if typ:
                        setter_types[key[1:-1]] = typ
            c.query_parameter_type = lambda expression, at: setter_types.get(expression)
            q = parse_storage_query(normalized_text(d.text), c)
            for s in leaves(q):
                if s.source_kind in {'slice-first','slice-last','register','balance','turnover','balance-turnover'} and s.owner not in plan.storage.register_schemas:
                    raise UnsupportedSyntaxError('Query API register requires storage.registers: ' + s.owner)
                if s.source_kind in {'ordinary','table-part','collection'}:
                    e = c.resolve(s.owner)[0]
                    if e not in plan.storage_elements:
                        plan.storage_elements.append(e)
            name = generate_dynamic(d, q, c)
            plan.module_type_dependencies.setdefault(symbol.identity.source_file, []).append(name+'.Запрос')
            plan.source_transforms.setdefault((symbol.identity.source_file, symbol.identity.declaration), []).append((d.start, d.end, name+'.Создать()'))
            plan.queries.append({'sourceFile': symbol.identity.source_file, 'symbol':symbol.identity.declaration,
                'start': symbol.start+d.start, 'end': symbol.start+d.end,
                'bodyStart': symbol.start+d.text_start, 'text': d.text, 'ast': q.to_dict(),
                'backend':plan.storage.config.get('backend','memory'), 'api':'constant-text-dynamic-044',
                'limitations':['no-native-XBQL', 'constant-query-text', 'single-pass-materialized-result']})
            plan.bindings.append(CapabilityBinding('query-api', 'ПроизвольныйЗапрос', d.variable,
                'constant-text-dynamic-044', 'Именованные параметры связываются и проверяются при выполнении в Script',
                symbol.identity.source_file, symbol.start+d.start, symbol.start+d.end))
        if c.query_results:
            from .query_results import lower_results
            _, results = api_receivers(symbol, plan.model)
            lower_results(plan, symbol, results)
