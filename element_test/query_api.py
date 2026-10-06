"""Resolve bounded dynamic query construction from source, never from fixtures.

Source-finite text uses the common typed AST; unbounded/heterogeneous text uses
an explicit Script parser. Values, setters, repeated execution and checks run in Script. A project declaration
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
    expression: str = '""'
    texts: tuple = ()
    runtime_text: bool = False
    spelling: str = 'ПроизвольныйЗапрос'


def finite_texts(source, node, expression, active=()):
    """Prove a finite source-derived text family; execution chooses in Script.

    This is string-domain analysis only. It never evaluates parameters, rows,
    predicates, expected answers or branch conditions.
    """
    if expression.kind == 'string':
        value = source[expression.start:expression.end]
        if '${' in value or '\\' in value or '"' in value[1:-1]:
            raise UnsupportedSyntaxError('Интерполяция/escape текста Query API требует отдельный контракт')
        text = value[1:-1]
        if '\n' in text:
            prefix = source[source.rfind('\n',0,expression.start)+1:expression.start]
            if prefix.strip():
                raise UnsupportedSyntaxError('Многострочная строка должна начинаться на отдельной строке')
            # Native String literals remove indentation up to the first character
            # after the opening quote, and trailing whitespace except on the last line.
            boundary = len(prefix) + 1
            lines = text.split('\n')
            for i,line in enumerate(lines):
                if i:
                    indent = len(line) - len(line.lstrip(' '))
                    line = line[min(indent,boundary):]
                    if line.startswith('\t') and indent < boundary:
                        raise UnsupportedSyntaxError('Табуляция до границы многострочной строки')
                lines[i] = line.rstrip() if i < len(lines)-1 else line
            text = '\n'.join(lines)
        return {text}
    if expression.kind == 'binary' and expression.value == '+':
        left, right = expression.children
        left_values = finite_texts(source,node,left,active)
        right_values = finite_texts(source,node,right,active)
        if len(left_values)*len(right_values)>64:
            raise UnsupportedSyntaxError('Семейство текстов требует runtime-разбор')
        return {a+b for a in left_values for b in right_values}
    if expression.kind == 'name' and expression.value not in active:
        values = []
        for item in node.expression_tree.walk():
            if item.kind == 'statement' and item.value in {'знч','пер'} and item.children:
                if re.match(rf'(?:знч|пер)\s+{re.escape(expression.value)}\s*(?::\s*Строка\s*)?=', source[item.start:item.end]):
                    values.append(item.children[-1])
            if item.kind == 'assignment' and item.children[0].kind == 'name' and item.children[0].value == expression.value:
                if item.value != '=':
                    raise UnsupportedSyntaxError('Неподтверждённое изменение текста Query API')
                values.append(item.children[1])
        if values:
            domain=set()
            for value in values:
                domain.update(finite_texts(source,node,value,active+(expression.value,)))
                if len(domain)>64:
                    raise UnsupportedSyntaxError('Семейство текстов требует runtime-разбор')
            return domain
    raise UnsupportedSyntaxError('Динамически вычисляемый текст Query API не имеет доказанного конечного набора')


def query_aliases(source, node, root):
    aliases = {root}
    for item in node.expression_tree.walk():
        if item.kind=='statement' and item.value=='знч' and item.children:
            value=item.children[-1]
            m=re.match(rf'знч\s+({IDENT})\s*=',source[item.start:value.start])
            if m and value.kind=='name' and value.value in aliases:
                aliases.add(m[1])
    return aliases


def dynamic_queries(source, model, owner, local_types=()):
    node = parse_module(source)[0][0]
    code = mask_noncode(source)
    for e in node.expression_tree.walk():
        if e.kind != 'call' or not e.children or e.children[0].kind != 'new':
            continue
        typ = e.children[0].children[0].value
        if typ not in {'ПроизвольныйЗапрос', 'Стд::БазаДанных::ПроизвольныйЗапрос', 'ЗапросСВыборкой'}:
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
        if len(e.children) not in {1,2}:
            raise UnsupportedSyntaxError('ПроизвольныйЗапрос требует один аргумент Текст')
        line = code[code.rfind('\n', 0, e.start)+1:e.start]
        decl = re.fullmatch(rf'\s*(?:знч|пер)\s+({IDENT})\s*(?::\s*(?:(?:Стд::БазаДанных::)?ПроизвольныйЗапрос|ЗапросСВыборкой)\s*)?=\s*', line)
        if not decl:
            raise UnsupportedSyntaxError('Query API требует проверяемое локальное объявление')
        runtime_text = False
        try:
            initial = finite_texts(source,node,e.children[1]) if len(e.children)==2 else {''}
        except UnsupportedSyntaxError:
            initial = set(); runtime_text = True
        texts = set(initial)
        aliases = query_aliases(source,node,decl[1])
        for assignment in node.expression_tree.walk():
            if assignment.kind != 'assignment':
                continue
            target = assignment.children[0]
            if target.kind == 'member' and target.children[0].value in aliases and target.children[1].value == 'Текст':
                if assignment.value != '=':
                    raise UnsupportedSyntaxError('Неподтверждённое изменение Текст Query API')
                try:
                    texts.update(finite_texts(source,node,assignment.children[1]))
                except UnsupportedSyntaxError:
                    runtime_text = True
        # Returning an opaque resource as JSON has no grading contract. This
        # does not prevent typed result transfer between reachable methods.
        if node.return_type(source) == 'Объект' and re.search(rf'\bвозврат\s+{re.escape(decl[1])}\.Выполнить\s*\(\s*\)\s*(?:\n|;)', code):
            raise UnsupportedSyntaxError('Возврат непрочитанного ресурса Query API не имеет JSON-контракта')
        expr = source[e.children[1].start:e.children[1].end] if len(e.children)==2 else '""'
        # AST spans omit the original indentation. Emit the proven literal value
        # so moving the constructor cannot change multiline indentation semantics.
        if len(e.children)==2 and e.children[1].kind=='string' and '\n' in expr:
            if initial:
                from .runtime import sbsl_literal
                expr = sbsl_literal(next(iter(initial)), 'Строка')
            else:
                prefix = source[source.rfind('\n',0,e.children[1].start)+1:e.children[1].start]
                expr = '\n' + prefix + expr
        a,b = (e.children[1].start+1,e.children[1].end-1) if len(e.children)==2 else (e.end,e.end)
        yield DynamicQuery(e.start,e.end,next(iter(sorted(initial)),''),a,b,decl[1],expr,tuple(sorted(texts)),runtime_text,typ)


def normalized_text(text):
    code = mask_noncode(text)
    # Named parameters have identical offsets after & -> %. Literals/comments
    # are protected by the lexer. & sources are deliberately not fabricated.
    chars = list(text)
    for m in re.finditer(rf'&({IDENT})', code):
        chars[m.start()] = '_' if re.search(r'\bИЗ\s*$',code[:m.start()],re.I) else '%'
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
    results = {p.split(':')[0].strip(): 0 for p in parse_module(source)[0][0].parameters(source)
               if re.search(r'(?<![\w:])(?:Стд::БазаДанных::)?РезультатЗапроса\s*<',p)}
    for m in re.finditer(rf'\b(?:знч|пер|исп)\s+({IDENT})\s*=\s*({IDENT})\.Выполнить\s*\(\s*\)', code):
        if m[2] in queries and queries[m[2]] < m.start():
            results[m[1]] = m.end()
    for item in parse_module(source)[0][0].expression_tree.walk():
        if item.kind != 'statement' or item.value not in {'знч','пер','исп'} or not item.children:
            continue
        e = item.children[-1]
        declaration = re.match(rf'(?:знч|пер|исп)\s+({IDENT})\s*(?::[^=]+)?=',code[item.start:e.start])
        if declaration and e.kind=='name':
            if item.value=='пер':
                continue
            known = queries if e.value in queries else results if e.value in results else {}
            if e.value in known and known[e.value]<e.start:
                if re.search(rf'\b{re.escape(e.value)}\s*=(?!=)',code[known[e.value]:e.start]):
                    raise UnsupportedSyntaxError('Alias Query API требует неизменённого исходного получателя')
                known[declaration[1]]=e.end
        member = e.children[0] if e.kind == 'call' and len(e.children)==1 else None
        if member and member.kind=='member' and member.children[1].value=='Выполнить':
            receiver=member.children[0]
            if declaration and (receiver.kind=='query' or receiver.kind=='name' and receiver.value in queries):
                results[declaration[1]] = e.end
    return queries, results


def proven_api_call(symbol, model, call):
    if not call.receiver or not re.fullmatch(IDENT, call.receiver):
        return False
    queries, results = api_receivers(symbol, model)
    known = queries if call.name in {'УстановитьПараметр','УстановитьИсточникДанных', 'Выполнить'} else results if call.name in {
        'Закрыть', 'ПолучитьОписанияКолонок', 'ВМассив', 'Пусто', 'Первый', 'ПервыйИлиНеопределено', 'Единственный', 'ЕдинственныйИлиНеопределено'} else {}
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
        or any(call.name in {'Закрыть','ПолучитьОписанияКолонок'} and proven_api_call(s, plan.model, call)
               for call in method_call_expressions(s.source, parse_module(s.source)[0][0]))
        or list(dynamic_queries(s.source, plan.model, s.owner, s.local_structures)) for s in plan.symbols)
    if plan.check.get('queryResults') is not None and type(plan.check['queryResults']) is not bool:
        from .yaml_io import InvalidTestError
        raise InvalidTestError('queryResults должен быть Булево')
    if c.query_results:
        from .query_columns import prepare_columns
        prepare_columns(c)
    for symbol in plan.symbols:
        c.current_source = symbol.identity.source_file
        c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
        node = parse_module(symbol.source)[0][0]
        c.query_declaration_annotations = node.annotations
        bindings = method_local_bindings(symbol.source, node)
        for _, _, body, text in query_literals(symbol.source):
            fill = re.search(rf'\bЗАПОЛНИТЬ\s+({IDENT})\b', mask_noncode(text))
            local_fill = fill and fill[1] in c.local_by_source.get(c.current_source, {})
            if re.search(r'\bПОРОДИТЬ\b', mask_noncode(text)) or local_fill:
                c.query_parameter_type = lambda expression, at: _local_type(
                    symbol.source, node, expression, body+at, bindings, symbol.owner, plan.model
                ) if re.fullmatch(IDENT, expression) else None
                parse_storage_query(text, c)


def generate_dynamic(d, variants, c):
    from .storage_queries import generate_query, unique_parameters
    from .query_results import result_type
    valid = [(text,q) for text,q in variants if q is not None]
    if not valid:
        raise UnsupportedSyntaxError('Query API не содержит исполнимого текста')
    shape = tuple((label, f.type + ('?' if getattr(f,'sql_nullable',False) and not f.type.endswith('?') else ''))
                  for f,label in valid[0][1].projections)
    if any(q.fill or tuple((label,f.type + ('?' if getattr(f,'sql_nullable',False) and not f.type.endswith('?') else ''))
                          for f,label in q.projections)!=shape for _,q in valid):
        raise UnsupportedSyntaxError('Изменение схемы строк Query API требует динамический heterogeneous контракт')
    row_module = 'ТестДинамическаяСтрока' + sha256(str(shape).encode()).hexdigest()[:16]
    row = row_module + '.Значение'
    if row not in c.definitions:
        c.definitions[row] = ('@Глобально\nструктура Значение\n' + ''.join(
            '    знч '+label+': '+c.sbsl_type(typ)+'\n' for label,typ in shape) + ';\n'
            '@Глобально\nметод Прочитать(Строка: Значение, Ключ: Строка): Объект?\n' + ''.join(
            '    если Ключ == '+c.literal(label,'Строка')+'\n        возврат Строка.'+label+'\n    ;\n' for label,_ in shape)
            + '    выбросить новый ИсключениеНедопустимыйАргумент("Неизвестная колонка: " + Ключ)\n;\n')
        c.fields[row] = [{'Имя':label,'Тип':typ,'ТолькоЧтение':True} for label,typ in shape]
        c.method_dependencies[row] = [typ for _,typ in shape]
    result = result_type(row,c)
    identity = (tuple((text,q.to_dict() if q else None) for text,q in variants),shape)
    name = 'ТестДинамический' + sha256(str(identity).encode()).hexdigest()[:16]
    if name in c.definitions:
        return name
    execute, dependencies = [], []
    from .query_columns import columns_source, COLUMN
    source_names={s['parameter'] for s in getattr(c,'custom_query_sources',{}).values()}
    for text,q in variants:
        if q is None:
            continue
        base = generate_query(q,c)
        dependencies.append(base+'.Запрос')
        values = []
        for p in unique_parameters(q):
            if not re.fullmatch(IDENT,p.expression):
                raise UnsupportedSyntaxError('Query API параметр должен иметь имя')
            reader = 'ЧитатьИсточник' if p.expression in source_names else 'Читать'
            key = p.expression.removeprefix('__Источник_') if reader=='ЧитатьИсточник' else p.expression
            values.append(reader+'('+c.literal(key,'Строка')+') как '+c.sbsl_type(p.type))
        constructor = ', '.join(label+' = С.'+label for label,_ in shape)
        execute.append('        если Текст == '+c.literal(text,'Строка')+'\n'
            + columns_source(q,c,'            ') +
            '            знч Строки = новый Массив<Объект?>()\n'
            '            для С из '+base+'.Создать('+', '.join(values)+').Выполнить()\n'
            '                Строки.Добавить(новый '+row+'('+constructor+'))\n            ;\n'
            '            возврат '+result.removesuffix('.Результат')+'.Создать(Строки, (Значение: Объект?) -> Значение как '+row+', Колонки)\n        ;\n')
    c.definitions[name] = ('''@Глобально
структура Запрос
    пер Текст: Строка
    знч Параметры: Соответствие<Строка, Объект?>
    знч Источники: Соответствие<Строка, Объект?>
    @Глобально
    метод УстановитьИсточникДанных(Имя: Строка, Значение: Объект?)
        Источники[Имя] = Значение
    ;
    метод ЧитатьИсточник(Имя: Строка): Объект?
        если не Источники.СодержитКлюч(Имя)
            выбросить новый ИсключениеНедопустимоеСостояние("Не установлен источник запроса: " + Имя)
        ;
        возврат Источники[Имя]
    ;
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
    метод Выполнить(): ''' + result + '\n' + ''.join(execute) + '''        выбросить новый ИсключениеНедопустимоеСостояние("Некорректный текст запроса")
    ;
;
@Глобально
метод Создать(Текст: Строка): Запрос
    возврат новый Запрос(Текст = Текст, Параметры = новый Соответствие<Строка, Объект?>(), Источники = новый Соответствие<Строка, Объект?>())
;
''')
    c.method_dependencies[name] = dependencies + [result,row,COLUMN]
    c.dynamic_row_modules = getattr(c,'dynamic_row_modules',{})
    c.dynamic_row_modules[name] = row_module
    return name


def lower_dynamic_indexes(plan, symbol, query_variable, row_module, dynamic_members=False):
    """Lower only proven dynamic rows; ordinary arrays/maps keep native []."""
    source = symbol.source
    node = parse_module(source)[0][0]
    code = mask_noncode(source)
    query_names='(?:'+'|'.join(re.escape(x) for x in query_aliases(source,node,query_variable))+')'
    results = {m[1]:m.end() for m in re.finditer(
        rf'\b(?:знч|пер|исп)\s+({IDENT})\s*=\s*{query_names}\.Выполнить\s*\(\s*\)',code)}
    rows, arrays = {}, {}
    bindings = method_local_bindings(source, node)
    def remember(table, name, item):
        table.setdefault(name, []).extend((a,b) for a,b in bindings.get(name,[]) if item.start <= a <= item.end+1)
    def visible(table, name, at):
        return next(((a,b) for a,b in table.get(name,[]) if a <= at < b),None)
    def result_call(e, methods):
        member=e.children[0] if e.kind=='call' and len(e.children)==1 else None
        return bool(member and member.kind=='member' and member.children[0].kind=='name'
                    and member.children[0].value in results and results[member.children[0].value]<e.start
                    and member.children[1].value in methods)
    def array(e):
        return e.kind=='name' and visible(arrays,e.value,e.start) or result_call(e,{'ВМассив'})
    def row(e):
        return (e.kind=='name' and visible(rows,e.value,e.start) or
                e.kind=='index' and array(e.children[0]) or
                result_call(e,{'Первый','Единственный'}))
    for item in node.expression_tree.walk():
        if item.kind=='statement' and item.value in {'знч','исп'} and item.children:
            e=item.children[-1]
            m=re.match(rf'(?:знч|исп)\s+({IDENT})\s*=',code[item.start:e.start])
            if m and e.kind=='name' and e.value in results and results[e.value]<e.start:
                results[m[1]]=e.end
        if item.kind=='statement' and item.value=='для' and item.children:
            e = item.children[0]
            direct = (e.kind=='call' and len(e.children)==1 and e.children[0].kind=='member'
                      and e.children[0].children[0].value in query_aliases(source,node,query_variable)
                      and e.children[0].children[1].value=='Выполнить')
            if e.kind=='name' and e.value in results or direct or array(e):
                m = re.match(rf'для\s+({IDENT})\s+из',code[item.start:e.start])
                if m:remember(rows,m[1],item)
        if item.kind=='statement' and item.value in {'знч','пер'} and item.children:
            e=item.children[-1]
            m=re.match(rf'(?:знч|пер)\s+({IDENT})\s*=',code[item.start:e.start])
            if m:
                if array(e): remember(arrays,m[1],item)
                if row(e): remember(rows,m[1],item)
    transforms = plan.source_transforms.setdefault((symbol.identity.source_file,symbol.identity.declaration),[])
    for e in node.expression_tree.walk():
        if e.kind not in ({'index','member'} if dynamic_members else {'index'}) or len(e.children)!=2 or not row(e.children[0]):
            continue
        receiver=e.children[0]
        if receiver.kind=='name':
            scope=visible(rows,receiver.value,e.start)
            if re.search(rf'\b{re.escape(receiver.value)}\s*=(?!=)',code[scope[0]:e.start]):
                raise UnsupportedSyntaxError('Индекс строки Query API требует неизменённого получателя')
        if any(a.kind=='assignment' and a.children[0].start==e.start for a in node.expression_tree.walk()):
            raise UnsupportedSyntaxError('Строка Query API доступна только для чтения')
        key = source[e.children[1].start:e.children[1].end]
        if e.kind=='member': key = plan.contracts.literal(key,'Строка')
        transforms.append((e.start,e.end,row_module+'.Прочитать('+source[receiver.start:receiver.end]+', '+key+')'))
        plan.module_type_dependencies.setdefault(symbol.identity.source_file,[]).append(row_module+'.Дерево' if dynamic_members else row_module+'.Значение')


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
            if re.search(rf'\b{re.escape(d.variable)}\s*=(?!=)', code[d.end:]):
                raise UnsupportedSyntaxError('Переприсваивание получателя Query API пока недоступно')
            from .call_types import _local_type
            node = parse_module(symbol.source)[0][0]
            bindings = method_local_bindings(symbol.source, node)
            setter_types = {}
            source_types = {}
            aliases = query_aliases(symbol.source,node,d.variable)
            for expr in node.expression_tree.walk():
                member = expr.children[0] if expr.kind == 'call' and len(expr.children) == 3 else None
                if (not member or member.kind != 'member' or member.children[0].value not in aliases
                    or member.children[1].value not in {'УстановитьПараметр','УстановитьИсточникДанных'}):
                    continue
                key = symbol.source[expr.children[1].start:expr.children[1].end]
                value = symbol.source[expr.children[2].start:expr.children[2].end]
                if re.fullmatch(r'"\w+"', key) and re.fullmatch(IDENT, value):
                    typ = _local_type(symbol.source, node, value, expr.children[2].start,
                                      bindings, symbol.owner, plan.model)
                    if typ:
                        (source_types if member.children[1].value=='УстановитьИсточникДанных' else setter_types)[key[1:-1]] = typ
            c.custom_query_sources = {}
            from .query_plan import QueryField
            for key,typ in source_types.items():
                match = re.fullmatch(r'(?:Массив|ЧитаемыйМассив)<(.+)>',typ)
                if not match:
                    raise UnsupportedSyntaxError('Источник Query API требует массив объявленных структур')
                row = c.canonical_type(match[1]); c.require(row)
                if row not in c.fields:
                    raise UnsupportedSyntaxError('Нет объявленной схемы источника Query API: '+key)
                if c.resolve('_'+key):
                    raise UnsupportedSyntaxError('Конфликт технического имени источника Query API: '+key)
                c.custom_query_sources['_'+key] = {'owner':'&'+key,'kind':'custom-collection',
                    'fields':tuple(QueryField('&'+key,f['Имя'],f['Тип']) for f in c.fields[row]),
                    'dimensions':(),'resources':(),'period_type':'Дата','periodicity':None,'register_kind':'',
                    'parameter': '__Источник_'+key, 'type':c.canonical_type(typ)}
            c.query_parameter_type = lambda expression, at: setter_types.get(expression)
            variants = []
            from .yaml_io import InputError
            for text in d.texts:
                try:
                    q = parse_storage_query(normalized_text(text),c) if re.match(r'\s*ВЫБРАТЬ\b',text,re.I) else None
                except InputError:
                    # A Text setter never validates XBQL. The runtime parser
                    # reports invalid text only when the program executes it.
                    q = None
                variants.append((text,q))
            for s in (s for _,q in variants if q for s in leaves(q)):
                if s.source_kind in {'slice-first','slice-last','register','balance','turnover','balance-turnover'} and s.owner not in plan.storage.register_schemas:
                    raise UnsupportedSyntaxError('Query API register requires storage.registers: ' + s.owner)
                if s.source_kind in {'ordinary','table-part','collection'}:
                    e = c.resolve(s.owner)[0]
                    if e not in plan.storage_elements:
                        plan.storage_elements.append(e)
                        plan.query_storage_elements.append(e)
            heterogeneous = len({tuple((label,f.type) for f,label in q.projections) for _,q in variants if q}) > 1
            if d.runtime_text or heterogeneous or not any(q for _,q in variants):
                from .query_runtime_text import generate_runtime_text
                name = generate_runtime_text(plan, c)
            else:
                name = generate_dynamic(d, variants, c)
            plan.module_type_dependencies.setdefault(symbol.identity.source_file, []).append(name+'.Запрос')
            plan.source_transforms.setdefault((symbol.identity.source_file, symbol.identity.declaration), []).append((d.start, d.end, name+'.Создать('+d.expression+')'))
            prefix_start=code.rfind('\n',0,d.start)+1
            typed=re.search(r':\s*((?:(?:Стд::БазаДанных::)?ПроизвольныйЗапрос|ЗапросСВыборкой))\s*=\s*$',code[prefix_start:d.start])
            if typed:
                plan.source_transforms[(symbol.identity.source_file,symbol.identity.declaration)].append(
                    (prefix_start+typed.start(1),prefix_start+typed.end(1),name+'.Запрос'))
            for text,q in variants:
                if not q:continue
                plan.queries.append({'sourceFile': symbol.identity.source_file, 'symbol':symbol.identity.declaration,
                'start': symbol.start+d.start, 'end': symbol.start+d.end,
                'bodyStart': symbol.start+d.text_start, 'text': text, 'ast': q.to_dict(),
                'backend':plan.storage.config.get('backend','memory'), 'api':'source-finite-text-dynamic-044',
                'textFamily':list(d.texts), 'platformSpelling':d.spelling, 'emulated':True, 'rowType': getattr(c,'dynamic_row_types',{}).get(name,c.dynamic_row_modules[name]+'.Значение'),
                'limitations':['no-native-XBQL', 'source-proven-finite-text', 'single-pass-materialized-result']})
            if d.runtime_text:
                plan.queries.append({'sourceFile':symbol.identity.source_file,'symbol':symbol.identity.declaration,
                    'start':symbol.start+d.start,'end':symbol.start+d.end,'text':None,'textExpression':d.expression,
                    'api':'runtime-text-emulation-044','platformSpelling':d.spelling,'emulated':True,'ast':{'kind':'runtime-select','parser':'Script',
                    'sources':getattr(c,'runtime_query_sources',[])},'rowType':'Соответствие<Строка, Объект?>',
                    'limitations':['emulated-XBQL-subset','single-pass-materialized-result']})
            lower_dynamic_indexes(plan,symbol,d.variable,c.dynamic_row_modules[name],c.dynamic_row_modules[name]=='ТестРазборТекста')
            plan.bindings.append(CapabilityBinding('query-api', d.spelling, d.variable,
                'source-finite-text-dynamic-044', 'Текст и именованные параметры выбираются при выполнении в Script',
                symbol.identity.source_file, symbol.start+d.start, symbol.start+d.end))
        if c.query_results:
            from .query_results import lower_results
            from .query_columns import lower_columns
            _, results = api_receivers(symbol, plan.model)
            lower_results(plan, symbol, results)
            lower_columns(plan, symbol, results)
