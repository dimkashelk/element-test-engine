"""Co-locate cyclic metadata adapters without changing nominal source owners.

Entity lifecycle -> register -> registrar is a legal dependency in Element,
while Script module imports must be acyclic. Only generated metadata groups
are merged; project-module cycles retain their existing unsupported contract.
"""
from hashlib import sha256
import re

from .indexer import IDENT, mask_noncode
from .yaml_io import InputError


def components(graph):
    seen, order = set(), []
    for root in graph:
        if root in seen: continue
        stack = [(root, False)]
        while stack:
            node, finish = stack.pop()
            if finish:
                order.append(node); continue
            if node in seen: continue
            seen.add(node); stack.append((node, True))
            stack.extend((n, False) for n in graph.get(node, ()) if n in graph and n not in seen)
    reverse = {n: set() for n in graph}
    for node, edges in graph.items():
        for edge in edges:
            if edge in reverse: reverse[edge].add(node)
    seen = set(); result = []
    for root in reversed(order):
        if root in seen: continue
        group, pending = set(), [root]
        while pending:
            node = pending.pop()
            if node in seen: continue
            seen.add(node); group.add(node); pending.extend(reverse[node] - seen)
        result.append(group)
    return result


def prepare(c, graph):
    mapping = {}
    if not getattr(c, 'metadata_lifecycle', False): return mapping
    for group in components(graph):
        if len(group) < 2: continue
        if any(owner not in c.canonical_elements or c.canonical_elements[owner]['elementType'] not in
               {'Справочник', 'Документ', 'РегистрСведений', 'РегистрНакопления'} for owner in group):
            raise InputError('Циклическая зависимость вне metadata lifecycle: ' + ', '.join(sorted(group)))
        alias = 'ТестХранение' + sha256('|'.join(sorted(group)).encode()).hexdigest()[:16]
        for owner in group: mapping[owner] = alias
    c.storage_module_names = mapping
    return mapping


def type_name(c, typ):
    owner, dot, variant = typ.partition('.')
    mapped = getattr(c, 'storage_module_names', {}).get(owner)
    return mapped + '.' + owner + variant if mapped and dot else typ


def lower(text, mapping):
    if not mapping: return text
    code = mask_noncode(text)
    edits = []
    for match in re.finditer(rf'(?<![\w:])({"|".join(re.escape(o) for o in mapping)})\.({IDENT})', code):
        edits.append((match.start(), match.end(), mapping[match[1]] + '.' + match[1] + match[2]))
    for a, b, replacement in reversed(edits): text = text[:a] + replacement + text[b:]
    for owner, target in mapping.items():
        text = re.sub(r'(?m)^#требуется ' + re.escape(owner) + r'\.sbsl$', '#требуется ' + target + '.sbsl', text)
    return text


def definition(c, name, text):
    owner, dot, variant = name.partition('.')
    mapping = getattr(c, 'storage_module_names', {})
    if owner in mapping:
        code = mask_noncode(text)
        edits = []
        # Each generated type is prefixed by its original technical owner.
        for match in re.finditer(rf'(?<=структура ){IDENT}|(?<=перечисление ){IDENT}|(?<=исключение ){IDENT}', code):
            edits.append((match.start(), match.end(), owner + match[0]))
        # Manager operations are the only global methods generated for entities.
        if variant == 'Менеджер':
            for match in re.finditer(rf'(?<=метод ){IDENT}(?=\()', code):
                edits.append((match.start(), match.end(), owner + match[0]))
        variants = {key.partition('.')[2] for key in c.fields if key.startswith(owner + '.')}
        pattern = rf'(?:\b(?:новый|как)\s+|\b{IDENT}\s*:\s+|<\s*|^\s*метод [^\n]*\)\s*:\s+)({IDENT})'
        for match in re.finditer(pattern, code, flags=re.M):
            if match[1] in variants and not code[match.end():].startswith('.'):
                edits.append((match.start(1), match.end(1), owner + match[1]))
        for a, b, replacement in sorted(edits, reverse=True): text = text[:a] + replacement + text[b:]
    return lower(text, mapping)


def finalize(plan, sandbox):
    mapping = getattr(plan.contracts, 'storage_module_names', {})
    for path in sandbox.glob('*.sbsl'):
        # Metadata modules were lowered while writing them. Other generated
        # callers and import directives are lowered after renderer completes.
        if path.stem not in set(mapping.values()):
            path.write_text(lower(path.read_text(encoding='utf-8'), mapping), encoding='utf-8')
