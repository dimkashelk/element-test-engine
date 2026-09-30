"""Conservative, lossless runtime contract for same-module scalar structures."""
import re

from .indexer import IDENT, lex, mask_noncode, parse_module
from .yaml_io import InputError


def scalar_structures(source):
    """Discover declarations, retaining unsupported ones as explicit boundaries.

    Only plain mutable scalar fields are supported. Constructors, defaults,
    annotations, nested types and methods need separate runtime contracts.
    """
    methods = parse_module(source)[0]
    tokens = lex(mask_noncode(source))
    result = {}
    for i, token in enumerate(tokens):
        if token.value != 'структура' or (i and tokens[i - 1].value != '\n'):
            continue
        if any(m.start <= token.start < (m.end or len(source)) for m in methods):
            continue
        if i + 1 >= len(tokens) or not re.fullmatch(IDENT, tokens[i + 1].value):
            continue
        name = tokens[i + 1].value
        if name in result:
            raise InputError(f'Неоднозначная структура модуля: {name}')
        fields, j, names = [], i + 2, set()
        error = f'Структура {name}: поддерживаются только поля пер с типом Строка, Число или Булево'
        previous = i - 2
        while previous >= 0 and tokens[previous].value == '\n':
            previous -= 1
        line_start = previous
        while line_start >= 0 and tokens[line_start].value != '\n':
            line_start -= 1
        if previous >= 0 and tokens[line_start + 1].value == '@':
            result[name] = (None, [], f'Аннотации структуры {name} пока не поддерживаются')
            continue
        if j < len(tokens) and tokens[j].value == '\n':
            j += 1
            while j < len(tokens):
                if tokens[j].value == '\n':
                    j += 1
                    continue
                if tokens[j].value == ';' and (j + 1 == len(tokens) or tokens[j + 1].value == '\n'):
                    result[name] = (source[token.start:tokens[j].end], fields, None)
                    break
                row = tokens[j:j + 5]
                if (len(row) != 5 or row[0].value != 'пер' or not re.fullmatch(IDENT, row[1].value)
                        or row[1].value in names or row[2].value != ':'
                        or row[3].value not in {'Строка', 'Число', 'Булево'} or row[4].value != '\n'):
                    break
                names.add(row[1].value)
                fields.append({'Имя': row[1].value, 'Тип': row[3].value})
                j += 5
        result.setdefault(name, (None, [], error))
    return result
