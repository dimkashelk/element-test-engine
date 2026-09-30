"""Conservative types for postfix call receivers in XBSL source.

Only declarations, casts, constructors, project method signatures and a few
specified container/reference operations establish a type. Unknown values stay
unknown; a matching method name elsewhere in the project proves nothing.
"""
import re

from .indexer import IDENT, call_code, lex, method_binding_visible, split_parameters
from .resolution import method_visible, resolve_call_modules, resolve_symbols, visible_from


def _spelling(tokens):
    return ''.join(token.value for token in tokens if token.value != '\n')


def _matching(tokens, end, opening, closing):
    depth = 0
    for index in range(end, -1, -1):
        if tokens[index].value == closing:
            depth += 1
        elif tokens[index].value == opening:
            depth -= 1
            if depth == 0:
                return index
    return None


def _local_type(source, method, name, offset, bindings, module, model):
    if not method_binding_visible(bindings, name, offset):
        # Form context properties are declared in the companion YAML.
        element = _element(model, module['name'], module)
        if element:
            for member in element.get('properties', {}).get('Свойства', []):
                if member.get('Имя') == name:
                    return member.get('Тип')
        return None
    parameter_type = None
    for parameter in method.parameters(source):
        label, separator, type_name = parameter.partition(':')
        if separator and label.strip() == name:
            parameter_type = type_name.partition('=')[0].strip()
    tokens = lex(call_code(source[method.header_end:offset]))
    found = []
    for index, token in enumerate(tokens[:-2]):
        if token.value not in {'пер', 'знч'} or tokens[index + 1].value != name:
            continue
        if tokens[index + 2].value == ':':
            tail = []
            for part in tokens[index + 3:]:
                if part.value in {'=', ';', '\n'}:
                    break
                tail.append(part)
            found.append(_spelling(tail) or None)
        elif tokens[index + 2].value == '=' and index + 4 < len(tokens):
            if tokens[index + 3].value == 'новый':
                tail = []
                for part in tokens[index + 4:]:
                    if part.value in {'(', '[', ';', '\n'}:
                        break
                    tail.append(part)
                found.append(_spelling(tail) or None)
            elif tokens[index + 3].value == '<':
                tail = []
                for part in tokens[index + 3:]:
                    if part.value in {'{', ';', '\n'}:
                        break
                    tail.append(part)
                spelling = _spelling(tail)
                if spelling.startswith('<') and spelling.endswith('>') and ',' in spelling:
                    found.append('Соответствие' + spelling)
                else:
                    found.append(None)
            else:
                found.append(None)
        else:
            found.append(None)
    # Reused names and shadowing need a full scoped data-flow proof. A
    # conflicting declaration cannot safely inherit the parameter's type.
    candidates = [type_name for type_name in ([parameter_type] if parameter_type else []) + found]
    return candidates[0] if candidates and all(type_name == candidates[0] for type_name in candidates) else None


def _element(model, owner, module):
    items = [item for item in model.get('elements', [])
             if visible_from(item, module.get('namespace', ''), module.get('_libraryPrefix'))]
    matches = resolve_symbols(items, owner, module.get('namespace', ''), module.get('imports', ()),
                              model.get('properties'))
    return matches[0] if len(matches) == 1 else None


def _field_type(model, type_name, field, module):
    owner, dot, variant = type_name.partition('.')
    element = _element(model, owner, module)
    if element is None or (dot and variant not in {'Объект', 'Данные', 'Ссылка'}):
        return None
    properties = element.get('properties', {})
    if field == 'Ссылка' and variant == 'Объект':
        return owner + '.Ссылка'
    for member in (properties.get('Реквизиты', []) + properties.get('Поля', [])
                   + properties.get('Свойства', [])):
        if member.get('Имя') == field:
            return member.get('Тип')
    for member in properties.get('ТабличныеЧасти', []):
        if member.get('Имя') == field:
            return f'Массив<{owner}.{field}>'
    return None


def infer_receiver_type(source, method, call, module, model, declarations, sources, bindings):
    """Return a proven receiver type, or None for an unsupported expression."""
    if call.receiver_start is None or call.receiver_end is None:
        return None
    tokens = [token for token in lex(call_code(source[call.receiver_start:call.receiver_end]))
              if token.value != '\n']
    if not tokens:
        return None
    offset = call.receiver_start

    def infer(parts):
        if not parts:
            return None
        while parts and parts[-1].value == '?':
            parts = parts[:-1]
        if not parts:
            return None
        last = parts[-1].value
        if last == ')':
            opening = _matching(parts, len(parts) - 1, '(', ')')
            if opening is None:
                return None
            prefix = parts[:opening]
            if not prefix:
                inside = parts[opening + 1:-1]
                # An explicit cast is the only parenthesized expression whose
                # type is established without evaluating its contents.
                for index in range(len(inside) - 1, -1, -1):
                    if inside[index].value == 'как':
                        type_name = _spelling(inside[index + 1:])
                        return type_name if re.fullmatch(rf'{IDENT}(?:(?:::|\.){IDENT})*\??', type_name) else None
                return infer(inside)
            if prefix[0].value == 'новый':
                return _spelling(prefix[1:])
            if not re.fullmatch(IDENT, prefix[-1].value):
                return None
            called = prefix[-1].value
            if len(prefix) >= 2 and prefix[-2].value == '.':
                receiver = infer(prefix[:-2])
                if receiver:
                    receiver = receiver.rstrip('?')
                    if (called == 'ЗагрузитьОбъект' and receiver.endswith('.Ссылка')
                            and _element(model, receiver[:-len('.Ссылка')], module)):
                        return receiver[:-len('Ссылка')] + 'Объект'
                    if called == 'СоздатьКопию' and receiver.endswith('.Объект'):
                        return receiver
                    if called == 'ВставитьЕслиОтсутствует':
                        match = re.fullmatch(r'Соответствие<(.+)>', receiver)
                        if match:
                            arguments = split_parameters(match[1])
                            if len(arguments) == 2:
                                return arguments[1].strip()
                    owner = receiver if receiver.endswith('.Объект') else None
                    if owner:
                        candidates = [item for item in model['modules'] if item['name'] == owner
                                      and visible_from(item, module.get('namespace', ''), module.get('_libraryPrefix'))]
                        methods = [(item, node) for item in candidates
                                   for node in declarations.get(item['sourceFile'], ())
                                   if node.name == called and method_visible(node, module, item)]
                        if len(methods) == 1:
                            item, node = methods[0]
                            return node.return_type(sources[item['sourceFile']])
                return None
            # Same-module and explicitly addressed static methods.
            if len(prefix) == 1:
                local = [node for node in declarations.get(module['sourceFile'], ()) if node.name == called]
                if len(local) == 1:
                    return local[0].return_type(source)
            return None
        if last == ']':
            opening = _matching(parts, len(parts) - 1, '[', ']')
            if opening is None:
                return None
            outer = infer(parts[:opening])
            match = re.fullmatch(r'Массив<(.+)>', outer or '')
            if match:
                return match[1]
            match = re.fullmatch(r'Соответствие<(.+)>', outer or '')
            if match:
                arguments = split_parameters(match[1])
                if len(arguments) == 2:
                    return arguments[1].strip()
            return None
        if re.fullmatch(IDENT, last):
            if len(parts) >= 2 and parts[-2].value == '.':
                outer = infer(parts[:-2])
                return _field_type(model, outer.rstrip('?'), last, module) if outer else None
            if len(parts) == 1:
                if last == 'этот' and module['name'].endswith('.Объект'):
                    return module['name']
                return _local_type(source, method, last, offset, bindings, module, model)
        return None

    return infer(tokens)


def receiver_object_modules(type_name, module, model):
    """Project object modules matching a known result type."""
    if not type_name:
        return []
    type_name = type_name.rstrip('?')
    candidates = resolve_call_modules(model['modules'], type_name, module.get('namespace', ''),
                                      module.get('imports', ()), model.get('properties'))
    return candidates


def known_receiver_type(type_name, module, model):
    """A type is useful evidence only when its owner is unambiguous."""
    if not type_name:
        return False
    type_name = type_name.rstrip('?')
    if type_name in {'Строка', 'Число', 'Булево', 'Дата', 'ДатаВремя', 'Момент'}:
        return True
    if type_name.startswith(('Массив<', 'Соответствие<', 'Множество<', 'ЧитаемаяКоллекция<')):
        return True
    if receiver_object_modules(type_name, module, model):
        return True
    owner, dot, _ = type_name.partition('.')
    return bool(dot and _element(model, owner, module))
