"""Typed, stable report ordering. Script compares values; Python orders type identities."""
from .generated_types import union_members
from .reference_codec import identity
from .resolution import qualified
from .yaml_io import UnsupportedSyntaxError

SCALARS = ('Булево', 'Число', 'Строка', 'Дата', 'Время', 'ДатаВремя', 'Момент', 'Ууид')
CONTRACT = {'name': 'typed-report-order-v1', 'ascendingTypes': list(SCALARS) + ['enum', 'reference'],
            'nominalTypes': 'full-owner-unicode-codepoint', 'references': 'identifier',
            'enums': 'declaration-order', 'null': 'first-ascending-last-descending',
            'ties': 'stable', 'nativePresentation': False}


def _key(c, typ):
    if typ in SCALARS:
        return (SCALARS.index(typ), '')
    if typ in c.enums:
        owner = typ.partition('.')[0]
        name = qualified(c.canonical_elements[owner]) if owner in c.canonical_elements else typ
        return (len(SCALARS), name)
    if typ.endswith('.Ссылка'):
        return (len(SCALARS) + 1, identity(c, typ))
    raise UnsupportedSyntaxError('Сортировка макета вне контракта: ' + typ)


def _value(c, expr, typ):
    if typ.endswith('.Ссылка'):
        return expr + '.Идентификатор' + ('.ВСтроку()' if c.reference_id_type == 'Ууид' else '')
    if typ == 'Ууид':
        return expr + '.ВСтроку()'
    if typ == 'Булево':
        return '(' + expr + ' ? 1 : 0)'
    if typ in c.enums:
        members = c.enums[typ]
        result = str(len(members) - 1)
        for n in reversed(range(len(members) - 1)):
            result = '(' + expr + ' == ' + c.sbsl_type(typ) + '.' + members[n] + ' ? ' + str(n) + ' : ' + result + ')'
        return result
    return expr


def comparison(c, typ, left, right, ascending):
    typ = c.canonical_type(typ)
    members = union_members(typ)
    variants = [v for v in members if v != '?'] if members else [typ.rstrip('?')]
    variants = sorted(variants, key=lambda v: _key(c, v))
    after, before = ('Истина', 'Ложь') if ascending else ('Ложь', 'Истина')
    code = ''
    if typ.endswith('?'):
        code += ('    если ' + left + ' == Неопределено и ' + right + ' != Неопределено\n        возврат ' + before + '\n    ;\n'
                 '    если ' + left + ' != Неопределено и ' + right + ' == Неопределено\n        возврат ' + after + '\n    ;\n'
                 '    если ' + left + ' != Неопределено и ' + right + ' != Неопределено\n')
    for variant in variants:
        av, bv = left, right
        if members:
            a, b = left + ' это ' + c.sbsl_type(variant), right + ' это ' + c.sbsl_type(variant)
            code += ('    если ' + a + ' и не (' + b + ')\n        возврат ' + before + '\n    ;\n'
                     '    если ' + b + ' и не (' + a + ')\n        возврат ' + after + '\n    ;\n'
                     '    если ' + a + ' и ' + b + '\n')
        if members or typ.endswith('?'):
            av, bv = '(' + left + ' как ' + c.sbsl_type(variant) + ')', '(' + right + ' как ' + c.sbsl_type(variant) + ')'
        av, bv = _value(c, av, variant), _value(c, bv, variant)
        code += '    если ' + av + ' != ' + bv + '\n        возврат ' + av + (' > ' if ascending else ' < ') + bv + '\n    ;\n'
        if members:
            # Same-type equality must fall through to the next report field,
            # rather than trying the remaining union variants.
            code += '    иначе\n'
    if members:
        code += '    выбросить новый ИсключениеНеподдерживаемаяОперация("Unknown report sort variant")\n'
        code += '    ;\n' * len(variants)
    if typ.endswith('?'):
        code += '    ;\n'
    return code
