"""A bounded XBQL AST: one balance source, two projections and one IN predicate."""
from dataclasses import dataclass, asdict
import re
from .indexer import IDENT, mask_noncode
from .resolution import qualified
from .yaml_io import InputError


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
