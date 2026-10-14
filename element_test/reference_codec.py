"""Nominal reference-union transport, generated from resolved metadata types.

Python generates adapters; Script performs every conversion and copy. Single
references and scalar unions retain their existing JSON representation.
"""
from hashlib import sha256
import re

from .generated_types import union_members, COLLECTIONS
from .indexer import IDENT, split_parameters
from .resolution import qualified

ARRAY_PATTERN = r'(?:' + '|'.join(sorted(COLLECTIONS)) + r')<(.+)>'

CONTRACT = 'tagged-reference-union-v1'


def reference_union(typ):
    members = union_members(typ)
    variants = [t for t in members if t != '?']
    return variants if len(variants) > 1 and all(t.endswith('.Ссылка') for t in variants) else []


def needed(c, typ, seen=None):
    typ = c.canonical_type(typ)
    if reference_union(typ):
        return True
    if typ.endswith('?') and not union_members(typ):
        return needed(c, typ[:-1], seen)
    # Native Script JSON cannot serialize KeyAndValue, including a scalar pair.
    if re.fullmatch(r'КлючИЗначение<(.+)>', typ):
        return True
    array = re.fullmatch(ARRAY_PATTERN, typ)
    if array:
        return needed(c, array[1], seen)
    seen = set(seen or ())
    if typ in seen:
        return False
    return any(needed(c, f['Тип'], seen | {typ}) for f in c.fields.get(typ, []))


def identity(c, typ):
    owner, _, variant = typ.partition('.')
    return qualified(c.canonical_elements[owner]) + '.' + variant


def encode(c, expr, typ):
    typ = c.canonical_type(typ)
    variants = reference_union(typ)
    if variants:
        result = 'Неопределено'
        for variant in reversed(variants):
            tagged = '{"type": ' + c.literal(identity(c, variant), 'Строка') + ', "value": {"Идентификатор": (' + expr + ' как ' + c.sbsl_type(variant) + ').Идентификатор}}'
            result = '(' + expr + ' это ' + c.sbsl_type(variant) + ' ? ' + tagged + ' : ' + result + ')'
        return result
    if not needed(c, typ):
        return expr
    if typ.endswith('?') and not union_members(typ):
        return '(' + expr + ' == Неопределено ? Неопределено : ' + encode(c, '(' + expr + ' как ' + c.sbsl_type(typ[:-1]) + ')', typ[:-1]) + ')'
    return _helper(c, typ, 'encode') + '(' + expr + ')'


def decode(c, expr, typ):
    typ = c.canonical_type(typ)
    if typ.endswith('?') and not union_members(typ):
        return '(' + expr + ' == Неопределено ? Неопределено : ' + decode(c, expr, typ[:-1]) + ')'
    if union_members(typ) and not needed(c, typ):
        name = 'ТестСкалярCodec' + sha256(typ.encode()).hexdigest()[:16]
        key = name + '.Значение'
        if key not in c.definitions:
            c.definitions[key] = '@Глобально\nструктура Значение\n    ' + ('' if typ.endswith('?') else 'обз ') + 'пер Value: ' + c.sbsl_type(typ) + '\n;\n'
            c.method_dependencies[key] = [typ]
        return '(СериализацияJson.ПрочитатьОбъект<' + key + '>(СериализацияJson.ЗаписатьОбъект({"Value": ' + expr + '}), Тип<' + key + '>)).Value'
    if not needed(c, typ):
        emitted = c.sbsl_type(typ)
        return 'СериализацияJson.ПрочитатьОбъект<' + emitted + '>(СериализацияJson.ЗаписатьОбъект(' + expr + '), Тип<' + emitted + '>)'
    return _helper(c, typ, 'decode') + '(' + expr + ')'


def _helper(c, typ, operation):
    array = re.fullmatch(ARRAY_PATTERN, typ)
    pair = re.fullmatch(r'КлючИЗначение<(.+)>', typ)
    pair_types = [p.strip() for p in split_parameters(pair[1])] if pair else []
    variants = reference_union(typ)
    owner_type = array[1] if array else variants[0] if variants else typ
    if reference_union(owner_type):
        owner_type = reference_union(owner_type)[0]
    owner = owner_type.partition('.')[0]
    if not re.fullmatch(IDENT, owner):
        # Keep nominal helpers with their metadata module, including nested
        # containers. This avoids a needless entity -> helper -> entity cycle.
        nominal = [o for o in re.findall(rf'({IDENT})\.', typ) if o in c.canonical_elements]
        owner = nominal[0] if nominal else 'ТестТранспорт' + sha256(typ.encode()).hexdigest()[:16]
    name = 'ТестCodec' + sha256((operation + ':' + typ).encode()).hexdigest()[:16]
    key = owner + '.' + name
    if key in c.definitions:
        return key
    c.definitions[key] = ''  # Reserve before descending into table types.
    c.method_dependencies[key] = [typ]
    emitted = c.sbsl_type(typ)
    if operation == 'encode':
        code = '@Глобально\nметод ' + name + '(Значение: ' + emitted + '): Стд::Объект?\n'
        if array:
            code += '    знч Результат = новый Массив<Стд::Объект?>()\n    для СтрокаТаблицы из Значение\n        Результат.Добавить(' + encode(c, 'СтрокаТаблицы', array[1]) + ')\n    ;\n    возврат Результат\n'
        elif pair:
            code += '    возврат {' + ', '.join(c.literal(f, 'Строка') + ': ' + encode(c, 'Значение.' + f, t)
                                               for f, t in zip(('Ключ', 'Значение'), pair_types)) + '}\n'
        else:
            raw_fields = getattr(c, 'reference_codec_raw_fields', {}).get(typ, set())
            code += '    возврат {' + ', '.join(c.literal(f['Имя'], 'Строка') + ': ' + ('Значение.' + f['Имя'] if f['Имя'] in raw_fields else encode(c, 'Значение.' + f['Имя'], f['Тип'])) for f in c.fields[typ]) + '}\n'
    else:
        code = '@Глобально\nметод ' + name + '(Значение: Стд::Объект?): ' + emitted + '\n'
        if variants:
            if typ.endswith('?'):
                code += '    если Значение == Неопределено\n        возврат Неопределено\n    ;\n'
            code += '    знч Тег = Значение как ЧитаемоеСоответствие<Строка, Стд::Объект?>\n'
            for variant in variants:
                code += '    если Тег["type"] == ' + c.literal(identity(c, variant), 'Строка') + '\n        возврат ' + decode(c, 'Тег["value"]', variant) + '\n    ;\n'
            code += '    выбросить новый ИсключениеНедопустимоеСостояние("reference-codec:unknown-tag")\n'
        elif array:
            code += '    знч Результат = новый Массив<' + c.sbsl_type(array[1]) + '>()\n    для СтрокаТаблицы из (Значение как Массив<Стд::Объект?>)\n        Результат.Добавить(' + decode(c, 'СтрокаТаблицы', array[1]) + ')\n    ;\n    возврат Результат\n'
        elif pair:
            code += '    знч Поля = Значение как ЧитаемоеСоответствие<Строка, Стд::Объект?>\n'
            code += '    возврат новый ' + emitted + '(' + ', '.join(decode(c, 'Поля[' + c.literal(f, 'Строка') + ']', t)
                                           for f, t in zip(('Ключ', 'Значение'), pair_types)) + ')\n'
        else:
            required = [f for f in c.fields[typ] if f.get('constructorRequired')]
            arguments = ', '.join(f['Имя'] + ' = ' + decode(c, 'Поля[' + c.literal(f['Имя'], 'Строка') + ']', f['Тип']) for f in required)
            code += '    знч Поля = Значение как ЧитаемоеСоответствие<Строка, Стд::Объект?>\n    знч Результат = новый ' + emitted + '(' + arguments + ')\n'
            raw_fields = getattr(c, 'reference_codec_raw_fields', {}).get(typ, set())
            for f in c.fields[typ]:
                if f['Имя'] in raw_fields or f in required:
                    continue  # Full-filter register restores the nominal registrar separately.
                label = c.literal(f['Имя'], 'Строка')
                code += '    если Поля.СодержитКлюч(' + label + ')\n        Результат.' + f['Имя'] + ' = ' + decode(c, 'Поля[' + label + ']', f['Тип']) + '\n    ;\n'
            code += '    возврат Результат\n'
    owners = {k.partition('.')[0] for k in c.definitions}
    c.method_dependencies[key] += [o + '.Значение' for o in set(re.findall(rf'({IDENT})\.', code)) & owners if o != owner]
    c.definitions[key] = code + ';\n'
    return key


def materialize(c, target, fields, raw):
    """Decode query DTOs using the same codec as persistent objects."""
    c.fields[target] = [{'Имя': f.name, 'Тип': f.type} for f in fields]
    return decode(c, raw, target)


def describe(c):
    return {'contract': CONTRACT, 'observationContract': 'nominal-reference-union-v1', 'tag': {'type': 'full-owner.Ссылка', 'value': {'Идентификатор': 'storage.idType'}},
            'null': None, 'fields': [{'container': typ, 'field': f['Имя'],
                'variants': [identity(c, v) for v in reference_union(f['Тип'])], 'nullable': f['Тип'].endswith('?')}
                for typ, fields in sorted(c.fields.items()) for f in fields if reference_union(f['Тип'])]}
