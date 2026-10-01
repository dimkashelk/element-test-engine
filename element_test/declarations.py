"""Reachable module constants and explicit boundaries for native module state."""
from hashlib import sha256
import re
from .indexer import IDENT, call_code, parse_module, method_local_bindings, method_binding_visible
from .resolution import resolve_call_modules
from .yaml_io import InputError


def declaration_closure(root, model, reachable, aliases, rewrites, dependencies):
    sources, declarations = {}, {}
    for module in model['modules']:
        path = module['sourceFile']
        source = (root / path).read_text(encoding='utf-8-sig')
        sources[path] = source
        methods = parse_module(source)[0]
        entries = {}
        for match in re.finditer(rf'^(конст|пер|знч)\s+({IDENT})[^\n]*', call_code(source), re.M):
            if any(n.start <= match.start() < (n.end or len(source)) for n in methods):
                continue
            start, end = match.span()
            # Preserve a directly attached annotation rather than dropping it.
            line = source.rfind('\n', 0, max(0, start - 1)) + 1
            if source[line:start].strip().startswith('@'):
                start = line
            entries[match[2]] = (match[1], source[start:end], start, end)
        declarations[path] = entries
    selected = {}
    active = set()
    def include(module, name):
        path = module['sourceFile']
        pending = [(name, False)]
        while pending:
            current, finishing = pending.pop()
            key = path, current
            kind, text, start, end = declarations[path][current]
            if finishing:
                active.remove(key)
                selected.setdefault(path, {})[current] = {'source': text, 'start': start, 'end': end}
                continue
            if key in active:
                raise InputError('Циклический инициализатор модуля: ' + current)
            if current in selected.get(path, {}):
                continue
            if kind != 'конст':
                raise InputError('Native executor 9.0 не поддерживает изменяемый инициализатор модуля: ' + path + ':' + current)
            active.add(key)
            pending.append((current, True))
            children = [token[0] for token in re.finditer(IDENT, call_code(text.partition('=')[2]))
                        if token[0] in declarations[path] and token[0] != current]
            pending.extend((child, False) for child in reversed(children))
    for module, text, _ in reachable:
        node = parse_module(text)[0][0]
        bindings = method_local_bindings(text, node)
        code = call_code(text)
        for token in re.finditer(IDENT, code[node.header_end:]):
            offset = node.header_end + token.start()
            if token[0] in declarations[module['sourceFile']] and not method_binding_visible(bindings, token[0], offset):
                include(module, token[0])
        for access in re.finditer(rf'({IDENT}(?:::{IDENT})*)\.({IDENT})', code[node.header_end:]):
            owner, name = access[1], access[2]
            offset = node.header_end + access.start()
            if method_binding_visible(bindings, owner, offset):
                continue
            candidates = resolve_call_modules(model['modules'], owner, module['namespace'], module.get('imports', []), model.get('properties'))
            if len(candidates) != 1 or name not in declarations[candidates[0]['sourceFile']]:
                continue
            other = candidates[0]
            include(other, name)
            if other['sourceFile'] != module['sourceFile']:
                alias = aliases.setdefault(other['sourceFile'], 'ТестВнешнийМодуль' + sha256(other['sourceFile'].encode()).hexdigest()[:16])
                rewrites.setdefault((module['sourceFile'], node.name), []).append((offset, offset + len(owner), alias))
                dependencies.setdefault(module['sourceFile'], set()).add(other['sourceFile'])
    return {path: list(entries.values()) for path,entries in selected.items()}
