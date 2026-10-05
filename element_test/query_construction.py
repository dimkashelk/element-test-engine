"""Metadata-derived named then positional construction; no data evaluation."""
from hashlib import sha256
import re
from dataclasses import replace
from .indexer import IDENT, mask_noncode
from .resolution import qualified
from .yaml_io import UnsupportedSyntaxError


def resolve_fill(c, name, projections, span, type_span, projection_spans=None):
    targets = c.resolve(name)
    local = c.local_by_source.get(c.current_source, {}).get(name)
    if not targets and local:
        canonical, fields, named_only = local_constructor(c, name)
        owner, file = c.current_source + '::' + name, c.current_source
    else:
        if len(targets) != 1 or targets[0]['elementType'] != 'Структура':
            raise UnsupportedSyntaxError('Тип ЗАПОЛНИТЬ отсутствует или неоднозначен: ' + name)
        target = targets[0]
        canonical = c.canonical_type(qualified(target))
        c.require(canonical)
        fields = c.fields[canonical]
        owner, file, named_only = qualified(target), target['sourceFile'], False
    by_name = {f['Имя']: f for f in fields}
    supplied = {label for _, label in projections}
    compatible = lambda column, field: column.type == field['Тип'] or column.type + '?' == field['Тип']
    named = (all(label in by_name and compatible(col, by_name[label]) for col, label in projections)
             and all(not f.get('constructorRequired') or f['Имя'] in supplied for f in fields))
    if named:
        chosen = [by_name[label] for _, label in projections]
        mode = 'automatic-named'
    else:
        if named_only:
            raise UnsupportedSyntaxError('Конструктор ЗАПОЛНИТЬ допускает только именованные параметры: ' + name)
        chosen = fields[:len(projections)]
        if (len(chosen) != len(projections) or
            any(not compatible(col, f) for (col, _), f in zip(projections, chosen)) or
            any(f.get('constructorRequired') for f in fields[len(projections):])):
            for col, label in projections:
                if label in by_name and not compatible(col, by_name[label]):
                    raise UnsupportedSyntaxError('Несовместимые типы ЗАПОЛНИТЬ: ' + col.type + ' → ' + by_name[label]['Тип'])
            if all(label in by_name for _,label in projections):
                raise UnsupportedSyntaxError('Отсутствующее обязательное поле ЗАПОЛНИТЬ: ' + name)
            raise UnsupportedSyntaxError('Неизвестная колонка ЗАПОЛНИТЬ: ' + name)
        mode = 'automatic-positional'
    return {'owner': owner, 'sourceFile': file, 'type': canonical,
            'sourceName': name, 'range': span, 'typeRange': type_span,
            'constructor': mode, 'fields': tuple(fields), 'mapping': tuple(
                {'column': col.name, 'alias': label, 'parameter': f['Имя'], 'columnType': col.type,
                 'fieldType': f['Тип'], 'range': projection_spans[i] if projection_spans else getattr(col, 'range', None)}
                for i, ((col, label), f) in enumerate(zip(projections, chosen)))}


def local_constructor(c, name):
    """Preserve a source structure and explicit constructor configuration.

    Script structures have automatic constructors; the source `конструктор`
    configures that constructor, rather than introducing a callable body.
    Custom platform/native classes and structures with methods stay unavailable.
    """
    source = (c.query_root / c.current_source).read_text(encoding='utf-8-sig')
    m = re.search(rf'(?m)^структура\s+{re.escape(name)}\s*\n(.*?)^;\s*$', source, re.S)
    if not m:
        raise UnsupportedSyntaxError('Нет проверяемой локальной структуры ЗАПОЛНИТЬ: ' + name)
    body = m[1]
    fields, names, named_only = [], set(), False
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith('//'):
            continue
        if line == '@ИменованныеПараметры':
            named_only = True
            continue
        if line == 'конструктор':
            continue
        f = re.fullmatch(rf'(обз\s+)?(пер|знч)\s+({IDENT})\s*:\s*([^=]+?)(?:\s*=\s*(.+))?', line)
        if not f or f[3] in names:
            raise UnsupportedSyntaxError('Методы/выражения локального конструктора требуют отдельный native контракт: ' + name)
        typ = c.canonical_type(f[4].strip())
        if f[5] and not re.fullmatch(r'Неопределено|Истина|Ложь|-?\d+(?:\.\d+)?|"[^"\\]*"', f[5]):
            raise UnsupportedSyntaxError('Умолчание локального конструктора вне literal контракта: ' + name)
        c.require(typ)
        names.add(f[3])
        fields.append({'Имя': f[3], 'Тип': typ, 'ТолькоЧтение': f[2] == 'знч',
                       'constructorRequired': bool(f[1]), 'defaultKind': 'source' if f[5] else 'implicit'})
    identity = (c.current_source, name)
    canonical = 'ТестКонструктор' + sha256(str(identity).encode()).hexdigest()[:16] + '.Значение'
    c.local_names[identity] = canonical
    declaration = '@Глобально\n' + m[0].replace('структура ' + name, 'структура Значение', 1)
    for f in fields:
        declaration = re.sub(r'(?<=: )'+re.escape(f['Тип'])+r'(?=\s|$)', c.sbsl_type(f['Тип']), declaration)
    c.definitions[canonical] = declaration + '\n'
    c.fields[canonical] = fields
    c.method_dependencies[canonical] = [f['Тип'] for f in fields]
    # require() sees a successful contract; it does not replace this source body.
    c.local_by_source[c.current_source][name] = (m[0], fields, None)
    return canonical, fields, named_only


def constructor_args(query, values):
    labels = [m['parameter'] for m in query.fill['mapping']] if query.fill else [l for _, l in query.projections]
    return ', '.join(label + ' = ' + value for label, value in zip(labels, values))


def parse_produced(text, c):
    # Keep all original offsets by blanking only the annotation/clause.
    code = mask_noncode(text)
    pattern = rf'(?:(@(Локально|ВПодсистеме|ВПроекте|Глобально))\s*)?ПОРОДИТЬ\s+({IDENT})\b'
    matches = list(re.finditer(pattern, code, re.I))
    if len(matches) != 1:
        raise UnsupportedSyntaxError('ПОРОДИТЬ требует одну область объявления')
    m = matches[0]
    from .query_plan import parse_storage_query
    q = parse_storage_query(text[:m.start()] + ' '*(m.end()-m.start()) + text[m.end():], c)
    if q.fill or q.source_kind in {'union', 'batch'}:
        raise UnsupportedSyntaxError('Сочетание ПОРОДИТЬ с пакетами/ЗАПОЛНИТЬ требует отдельный контракт')
    name = m[3]
    source = c.current_source
    if source is None:
        raise UnsupportedSyntaxError('ПОРОДИТЬ требует идентичность исходного модуля')
    identity = (source, name)
    canonical = 'ТестПорожденный' + sha256(str(identity).encode()).hexdigest()[:16] + '.Значение'
    types = [(col.type + ('?' if getattr(col, 'sql_nullable', False) and not col.type.endswith('?') else ''), label)
             for col, label in q.projections]
    declaration = '@Глобально\nструктура Значение\n' + ''.join(
        '    знч ' + label + ': ' + c.sbsl_type(typ) + '\n' for typ, label in types) + ';\n'
    if canonical in c.definitions and c.definitions[canonical] != declaration:
        raise UnsupportedSyntaxError('Неоднозначный порождённый тип: ' + name)
    if c.resolve(name) or name in c.local_by_source.get(source, {}):
        raise UnsupportedSyntaxError('Конфликт порождённого типа: ' + name)
    c.definitions[canonical] = declaration
    c.method_dependencies[canonical] = [typ for typ, _ in types]
    c.produced_types = getattr(c, 'produced_types', {})
    c.produced_types[identity] = canonical
    fields = [{'Имя': label, 'Тип': typ, 'ТолькоЧтение': True} for typ, label in types]
    c.fields[canonical] = fields
    fill = {'owner': source + '::' + name, 'sourceFile': source, 'sourceName': name,
            'type': canonical, 'range': m.span(), 'typeRange': m.span(3),
            'constructor': 'produced-readonly-internal', 'visibility': m[2] or 'declaration',
            'fields': tuple(fields), 'mapping': tuple(
                {'column': col.name, 'alias': label, 'parameter': label, 'columnType': col.type,
                 'fieldType': typ} for (col, label), (typ, _) in zip(q.projections, types))}
    return replace(q, fill=fill)
