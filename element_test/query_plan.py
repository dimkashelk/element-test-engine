"""Bounded declaration-derived XBQL ASTs for balances, objects and daily slices."""
from dataclasses import dataclass, asdict, replace
import re
from .indexer import IDENT, mask_noncode
from .resolution import qualified
from .yaml_io import InputError
from .yaml_io import UnsupportedSyntaxError


@dataclass(frozen=True)
class BalanceQuery:
    register: str
    alias: str
    dimension: str
    dimension_alias: str
    resource: str
    resource_alias: str
    parameter: str
    parameter_start: int
    parameter_end: int
    mode: str = 'balance-ast-sql'

    def to_dict(self):
        return asdict(self)


def parse_balance_query(text, contracts=None):
    # The parameter is parsed as XBSL by the existing renderer. SQL accepts only
    # declarative owner/field identifiers, never the expression text.
    code = mask_noncode(text, strings=False)
    owner = rf'{IDENT}(?:::{IDENT})*'
    pattern = (rf'\s*ВЫБРАТЬ\s+({IDENT})\.({IDENT})\s+КАК\s+({IDENT})\s*,\s*'
               rf'({IDENT})\.({IDENT})Остаток\s+КАК\s+({IDENT})\s+'
               rf'ИЗ\s+({owner})\.Остатки\s+КАК\s+({IDENT})\s+'
               rf'ГДЕ\s+({IDENT})\.({IDENT})\s+В\s*\(\s*%\{{')
    match = re.match(pattern, code, re.I)
    if not match:
        raise InputError('UNSUPPORTED: запрос вне AST контракта остатков')
    if len({match[1], match[4], match[8], match[9]}) != 1 or match[2] != match[10] or match[3] == match[6]:
        raise InputError('UNSUPPORTED: несогласованные владельцы/проекции запроса')
    masked = mask_noncode(text)
    start = match.end()
    end, depth = start, 1
    while end < len(masked) and depth:
        depth += (masked[end] == '{') - (masked[end] == '}')
        end += 1
    if depth or not re.fullmatch(r'\s*\)\s*', masked[end:]) or not text[start:end-1].strip():
        raise InputError('UNSUPPORTED: предикат запроса вне AST контракта')
    register, dimension, resource = match[7], match[2], match[5]
    if contracts:
        candidates = contracts.resolve(register)
        if len(candidates) != 1 or candidates[0]['elementType'] != 'РегистрНакопления':
            raise InputError('UNSUPPORTED: владелец запроса не является однозначным регистром накопления')
        element = candidates[0]
        props = element['properties']
        if props.get('ВидРегистра', 'Остатки') != 'Остатки':
            raise InputError('UNSUPPORTED: требуется регистр остатков')
        dimensions = {f['Имя']: f.get('Тип') for f in props.get('Измерения', [])}
        resources = {f['Имя']: f.get('Тип') for f in props.get('Ресурсы', [])}
        if dimension not in dimensions or resources.get(resource) != 'Число':
            raise InputError('UNSUPPORTED: типы проекций запроса вне контракта')
        if not dimensions[dimension].rstrip('?').endswith('.Ссылка'):
            raise InputError('UNSUPPORTED: проекция баланса требует ссылочное измерение')
        register = qualified(element)
    return BalanceQuery(register, match[8], dimension, match[3], resource, match[6],
                        text[start:end-1], start, end-1)


def balance_sql(query, dimensions):
    """Generate projection from resolved metadata; values are supplied via JDBC."""
    quote = lambda v: "'" + v.replace("'", "''") + "'"
    if query.dimension not in dimensions:
        raise InputError('Измерение SQL-проекции отсутствует')
    secondary = [field for field in dimensions if field != query.dimension]
    if len(secondary) != 1:
        raise InputError('UNSUPPORTED: проекция остатков поддерживает два измерения')
    key = "value->" + quote(query.dimension) + "->>'Идентификатор'"
    other = "value->" + quote(secondary[0]) + "->>'Идентификатор'"
    amount = 'SUM(CASE WHEN value->>\'ВидЗаписи\'=\'Приход\' THEN (value->>' + quote(query.resource) + ')::numeric ELSE -(value->>' + quote(query.resource) + ')::numeric END)'
    return ('SELECT ' + key + ' AS item,' + other + ' AS warehouse,' + amount + ' AS quantity '
            'FROM smoke.records WHERE type=&register GROUP BY ' + key + ',' + other + ' HAVING ' + amount + ' <> 0 ORDER BY item,warehouse')


@dataclass(frozen=True)
class QueryField:
    owner: str
    name: str
    type: str
    system: bool = False


@dataclass(frozen=True)
class QueryParameter:
    expression: str
    start: int
    end: int
    type: str
    slot: int


@dataclass(frozen=True)
class StorageQuery:
    owner: str
    alias: str
    projections: tuple
    predicates: tuple
    parameters: tuple
    ordering: tuple
    limit: int | None
    mode: str = 'storage-staged-executor-v1'
    source_kind: str = 'ordinary'
    periodicity: str | None = None
    dimensions: tuple = ()
    period_slot: int | None = None
    period_range: tuple | None = None
    source_range: tuple | None = None
    source_name: str = ''
    boundary: str | None = None
    fill: dict | None = None
    fields: tuple = ()
    member: str = ''
    resources: tuple = ()
    register_kind: str = ''
    end_slot: int | None = None
    definition: object = None
    definition_source: dict | None = None

    def to_dict(self):
        return asdict(self)


def query_literals(source):
    """Verified source ranges; braces in strings/comments cannot close a literal."""
    code = mask_noncode(source)
    consumed = 0
    for match in re.finditer(r'\bЗапрос\s*\{', code):
        if match.start() < consumed:
            continue
        start, end, depth = match.end(), match.end(), 1
        while end < len(code) and depth:
            depth += (code[end] == '{') - (code[end] == '}')
            end += 1
        if depth:
            raise UnsupportedSyntaxError('Незакрытый литерал Запрос')
        yield match.start(), end, start, source[start:end-1]
        consumed = end


def parse_storage_query(text, contracts):
    """Parse typed storage/relational AST without evaluating data."""
    if re.search(r'\bПОРОДИТЬ\b', mask_noncode(text), re.I):
        from .query_construction import parse_produced
        return parse_produced(text, contracts)
    # Captured Script expressions are opaque to the XBQL dispatcher. Their
    # method calls/arithmetic must not turn an ordinary slice into an aggregate.
    routing = list(mask_noncode(text))
    i = 0
    while i < len(routing)-1:
        if routing[i:i+2] == ['%', '{']:
            end, depth = i+2, 1
            while end < len(routing) and depth:
                depth += (routing[end] == '{') - (routing[end] == '}')
                end += 1
            routing[i:end] = ' ' * (end-i)
            i = end
        else:
            i += 1
    routing = ''.join(routing)
    if re.search(r'\b(?:ОБЪЕДИНИТЬ|ПОМЕСТИТЬ|СОЗДАТЬ|УНИЧТОЖИТЬ|ОБРЕЗАТЬ|ИНДЕКСИРОВАТЬ|ВСТАВИТЬ|ИЗМЕНИТЬ|УДАЛИТЬ|СУЩЕСТВУЕТ|МЕЖДУ|ПОДОБНО|ОТЛИЧАЕТСЯ|ИЕРАРХИИ)\b|;|\(\s*ВЫБРАТЬ|\bВ\s*\(', routing, re.I):
        from .query_composites import parse_composite_query
        return parse_composite_query(text, contracts)
    if re.search(r'\b(?:КОЛИЧЕСТВО|СУММА|МИНИМУМ|МАКСИМУМ|СРЕДНЕЕ|ВЫРАЗИТЬ)\s*\(|\b(?:СГРУППИРОВАТЬ|ИМЕЮЩИЕ|РАЗЛИЧНЫЕ|ВЫБОР)\b|[+*/-]|%\s*\d|\.(?!СрезПоследних|ЗаменитьNull)[A-Za-zА-Яа-яЁё]+\s*\(', routing, re.I):
        from .query_projections import parse_computed_query
        return parse_computed_query(text, contracts)
    if re.search(r'\bСОЕДИНЕНИЕ\b|\bNULL\b|\bЗаменитьNull\b', routing, re.I):
        from .query_joins import parse_relational_query
        return parse_relational_query(text, contracts)
    try:
        return parse_simple_storage_query(text, contracts)
    except UnsupportedSyntaxError:
        # General typed expressions also cover nullable equality and scalar
        # literal comparisons. Source/type ambiguity is still rejected there.
        from .query_projections import parse_computed_query
        return parse_computed_query(text, contracts)


def parse_simple_storage_query(text, contracts):
    visible = mask_noncode(text, strings=False)
    hidden = mask_noncode(text)
    token_pattern = re.compile(rf'{IDENT}|\d+|::|==|[().,=]|%')
    tokens = []
    i = 0
    while i < len(visible):
        if visible[i].isspace():
            i += 1
            continue
        if visible[i] == '%':
            a = i + 1
            if a < len(text) and text[a] == '{':
                b, depth = a + 1, 1
                while b < len(text) and depth:
                    depth += (hidden[b] == '{') - (hidden[b] == '}')
                    b += 1
                if depth or not text[a+1:b-1].strip():
                    raise UnsupportedSyntaxError('Непустое выражение параметра запроса не закрыто')
                tokens.append(('parameter', text[a+1:b-1], a+1, b-1))
                i = b
                continue
            match = re.match(IDENT, text[a:])
            if not match:
                raise UnsupportedSyntaxError('Неподдержанная форма параметра запроса')
            b = a + len(match[0])
            tokens.append(('parameter', match[0], a, b))
            i = b
            continue
        match = token_pattern.match(visible, i)
        if not match:
            raise UnsupportedSyntaxError(f'Запрос вне storage AST: позиция {i}')
        tokens.append(('token', match[0], i, match.end()))
        i = match.end()
    pos = 0

    def accept(word):
        nonlocal pos
        if pos < len(tokens) and tokens[pos][0] == 'token' and tokens[pos][1].upper() == word:
            pos += 1
            return True
        return False

    def expect(word):
        if not accept(word):
            raise UnsupportedSyntaxError('Запрос вне storage AST: ожидается ' + word)

    def identifier():
        nonlocal pos
        if pos >= len(tokens) or tokens[pos][0] != 'token' or not re.fullmatch(IDENT, tokens[pos][1]):
            raise UnsupportedSyntaxError('Запрос требует идентификатор')
        value = tokens[pos][1]
        pos += 1
        return value

    def field_path():
        first = identifier()
        return (first, identifier()) if accept('.') else (None, first)

    expect('ВЫБРАТЬ')
    limit = None
    if accept('ПЕРВЫЕ'):
        if pos >= len(tokens) or not tokens[pos][1].isdigit() or int(tokens[pos][1]) <= 0:
            raise UnsupportedSyntaxError('ПЕРВЫЕ требует положительный целый литерал')
        limit = int(tokens[pos][1])
        pos += 1
    projections, projection_ranges = [], []
    while True:
        projection_start = tokens[pos][2] if pos < len(tokens) else len(text)
        path = field_path()
        label = identifier() if accept('КАК') else path[1]
        projections.append((path, label))
        projection_ranges.append((projection_start, tokens[pos-1][3]))
        if not accept(','):
            break
    fill_name, fill_range, fill_type_range = None, None, None
    if accept('ЗАПОЛНИТЬ'):
        fill_start = tokens[pos-1][2]
        type_start = tokens[pos][2] if pos < len(tokens) else len(text)
        fill_name = identifier()
        while accept('::'):
            fill_name += '::' + identifier()
        fill_range = (fill_start, tokens[pos-1][3])
        fill_type_range = (type_start, tokens[pos-1][3])
    expect('ИЗ')
    source_start = tokens[pos][2] if pos < len(tokens) else len(text)
    owner = identifier()
    while accept('::'):
        owner += '::' + identifier()
    source_kind, period_token, period_range = 'ordinary', None, None
    member, end_token = '', None
    if accept('.'):
        member = identifier()
    from .query_sources import source_schema
    schema = source_schema(contracts, owner, member)
    source_kind = schema['kind']
    if accept('('):
        if source_kind not in {'slice-last','slice-first','balance','turnover','balance-turnover'}:
            raise UnsupportedSyntaxError('Параметры для этого источника не поддержаны')
        period_start = tokens[pos-1][2]
        if not accept(')'):
            if pos < len(tokens) and tokens[pos][0] == 'parameter':
                period_token = tokens[pos]; pos += 1
            elif not (pos < len(tokens) and tokens[pos][1] == ','):
                raise UnsupportedSyntaxError('Граница виртуальной таблицы требует параметр')
            if accept(','):
                if source_kind not in {'turnover','balance-turnover'}:
                    raise UnsupportedSyntaxError('Фильтр виртуальной таблицы пока вне контракта')
                if pos < len(tokens) and tokens[pos][0] == 'parameter':
                    end_token = tokens[pos]; pos += 1
            expect(')')
        period_range = (period_start, tokens[pos-1][3])
    elif source_kind in {'slice-last','slice-first'}:
        raise UnsupportedSyntaxError('Срез требует скобки параметров')
    source_range = (source_start, tokens[pos-1][3])
    alias = identifier() if accept('КАК') else owner.split('::')[-1]
    identity, fields = schema['owner'], {f.name:f for f in schema['fields']}

    def bind(path):
        qualifier, name = path
        if qualifier is not None and qualifier != alias:
            raise UnsupportedSyntaxError('Неизвестный владелец поля запроса: ' + qualifier)
        if name not in fields:
            raise UnsupportedSyntaxError('Неизвестное поле запроса: ' + identity + '.' + name)
        field = fields[name]
        if source_kind in {'slice-last','slice-first'} and '|' in field.type:
            raise UnsupportedSyntaxError('Union-поле среза требует отдельного типизированного контракта')
        scalars = {'Строка', 'Число', 'Булево', 'Ууид', 'Дата', 'ДатаВремя'}
        enum = contracts.canonical_elements.get(field.type.rstrip('?'))
        if field.type.rstrip('?') not in scalars and not field.type.rstrip('?').endswith('.Ссылка') and not (enum and enum['elementType'] == 'Перечисление'):
            raise UnsupportedSyntaxError('Тип поля запроса вне контракта: ' + field.type)
        contracts.require(field.type)
        return field

    bound_projections = tuple((bind(path), label) for path,label in projections)
    if len({label for _,label in projections}) != len(projections):
        seen = set()
        for (_,label), span in zip(projections, projection_ranges):
            if label in seen:
                raise UnsupportedSyntaxError(f'Повторяющийся псевдоним проекции запроса: {label} ({span[0]}-{span[1]})')
            seen.add(label)
    fill = None
    if fill_name:
        from .query_construction import resolve_fill
        fill = resolve_fill(contracts, fill_name, bound_projections, fill_range, fill_type_range, projection_ranges)
    predicates, parameters, slots, slot_types = [], [], {}, {}
    def parameter(token, typ):
        _, expression, start, end = token
        key = expression if re.fullmatch(IDENT, expression) else (start,end)
        if key in slots and slot_types[slots[key]] != typ:
            if {slot_types[slots[key]], typ} in ({'Дата', 'Дата?'},{'ДатаВремя','ДатаВремя?'}):
                # A shared boundary/WHERE date has the stricter non-nullable
                # signature required by WHERE; capture still happens once.
                typ = typ.rstrip('?')
                parameters[:] = [replace(p,type=typ) if p.slot == slots[key] else p for p in parameters]
            else:
                raise UnsupportedSyntaxError('Несовместимые типы одного параметра запроса')
        slot = slots.setdefault(key, len(slots))
        slot_types[slot] = typ
        parameters.append(QueryParameter(expression, start, end, typ, slot))
        return slot
    period_slot = parameter(period_token, schema['period_type']+'?') if period_token else None
    end_slot = parameter(end_token, schema['period_type']+'?') if end_token else None
    if accept('ГДЕ'):
        while True:
            field = bind(field_path())
            if not accept('=='):
                expect('=')
            if pos >= len(tokens) or tokens[pos][0] != 'parameter':
                raise UnsupportedSyntaxError('Равенство требует параметр %Имя или %{выражение}')
            token = tokens[pos]
            pos += 1
            # Nullable equality is intentionally unavailable: SQL NULL and
            # XBSL Неопределено are different contracts, not interchangeable.
            nullable_reference = source_kind in {'slice-last','slice-first'} and field.type.endswith('.Ссылка?')
            if field.type.endswith('?') and not nullable_reference:
                raise UnsupportedSyntaxError('Сравнение nullable-поля запроса не подтверждено')
            # Simple variable interpolation reuses one captured value. Each
            # expression occurrence is a separate evaluation, in source order.
            if token[1] == 'Неопределено':
                raise UnsupportedSyntaxError('Nullable-параметр ГДЕ не подтверждён')
            slot = parameter(token, field.type.rstrip('?') if nullable_reference else field.type)
            predicates.append((field, slot))
            if not accept('И'):
                break
    ordering = []
    if accept('УПОРЯДОЧИТЬ'):
        expect('ПО')
        while True:
            path = field_path()
            # XBQL permits a projection alias in ORDER BY.
            projected = [f for f,label in bound_projections if path == (None,label)]
            field = projected[0] if projected else bind(path)
            if field.type not in {'Число', 'Строка', 'Дата', 'ДатаВремя'}:
                raise UnsupportedSyntaxError('Сортировка вне подтверждённых не-nullable типов')
            descending = accept('УБЫВ')
            if not descending:
                accept('ВОЗР')
            ordering.append((field, descending))
            if not accept(','):
                break
    if pos != len(tokens):
        raise UnsupportedSyntaxError('Достижимый запрос вне storage AST: ' + tokens[pos][1])
    dimensions = schema['dimensions']
    return StorageQuery(identity, alias, bound_projections, tuple(predicates), tuple(parameters), tuple(ordering), limit,
        'storage-slice-last-day-v1' if source_kind == 'slice-last' and schema['periodicity']=='День' else 'storage-staged-executor-v1' if source_kind=='ordinary' else 'storage-virtual-sources-v1',
        source_kind, schema['periodicity'], dimensions, period_slot, period_range,
        source_range, owner, 'captured-date-or-runtime-UTC-at-creation' if source_kind in {'slice-last','slice-first'} else None, fill,
        schema['fields'], member, schema['resources'], schema['register_kind'], end_slot,
        schema.get('definition'),schema.get('definition_source'))
