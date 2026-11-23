"""Bounded declarative queries over project facts and trusted execution evidence."""
from copy import deepcopy
import re

from .assignment_names import aliases_config, owner_name, tree
from .indexer import lex, method_calls, parse_module
from .yaml_io import InputError

MISSING = object()
SOURCES = {'project', 'element', 'module', 'method', 'execution'}
STEPS = {'path', 'items', 'walk', 'where', 'stripPrefix', 'withoutIds', 'distinctStrings'}
COMPARISONS = {'eq', 'contains', 'prefix', 'suffix', 'exists', 'typeEq', 'typeEqLocal', 'setEq'}


def mapping(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - allowed or not set(required) <= set(value):
        raise InputError('Некорректные поля декларативного критерия')
    return value


def validate_refs(value):
    if isinstance(value, list):
        for item in value:
            validate_refs(item)
    elif isinstance(value, dict):
        if '$owner' in value:
            mapping(value, {'$owner', 'short'}, {'$owner'})
            if not isinstance(value['$owner'], str) or type(value.get('short', False)) is not bool:
                raise InputError('Некорректная ссылка $owner')
        elif '$name' in value:
            mapping(value, {'$name', 'prefix'}, {'$name'})
            pair = value['$name']
            if not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(x, str) for x in pair):
                raise InputError('Некорректная ссылка $name')
            if not isinstance(value.get('prefix', ''), str):
                raise InputError('prefix должен быть строкой')
        else:
            for item in value.values():
                validate_refs(item)


def validate_path(path):
    if not isinstance(path, list) or len(path) > 24 or any(
        not isinstance(item, (str, dict)) and (type(item) is not int or item < 0) for item in path
    ):
        raise InputError('path должен быть списком ключей и неотрицательных индексов')
    for item in path:
        if isinstance(item, dict) and not ({'$name', '$owner'} & set(item)):
            raise InputError('Ссылка в path должна задавать имя или владельца')


def validate_filter(filters):
    if not isinstance(filters, list) or not filters or len(filters) > 32:
        raise InputError('Условие требует непустой список сравнений')
    for predicate in filters:
        mapping(predicate, COMPARISONS | {'path'})
        if len(set(predicate) & COMPARISONS) != 1:
            raise InputError('Ожидается одна операция сравнения')
        validate_path(predicate.get('path', []))
        if 'exists' in predicate and type(predicate['exists']) is not bool:
            raise InputError('exists должен быть логическим значением')


def validate_query(query, *, execution=True):
    mapping(query, {'from', 'owner', 'method', 'criterion', 'steps'}, {'from'})
    source = query['from']
    if not isinstance(source, str) or source not in SOURCES or (source == 'execution' and not execution):
        raise InputError('Неизвестный источник декларативного запроса')
    required = {'project': (), 'element': ('owner',), 'module': ('owner',),
                'method': ('owner', 'method'), 'execution': ('criterion',)}[source]
    mapping(query, {'from', 'steps', *required}, {'from', *required})
    for key in required:
        value = query[key]
        if not isinstance(value, str) and not (key != 'criterion' and isinstance(value, dict)
                                               and {'$name', '$owner'} & set(value)):
            raise InputError('Некорректный идентификатор источника запроса')
    steps = query.get('steps', [])
    if not isinstance(steps, list) or len(steps) > 24:
        raise InputError('steps должен быть списком не более чем из 24 шагов')
    for step in steps:
        mapping(step, STEPS)
        if len(step) != 1:
            raise InputError('Ожидается одна операция запроса')
        operation, value = next(iter(step.items()))
        if operation == 'path':
            validate_path(value)
        elif operation == 'where':
            validate_filter(value)
        elif operation == 'stripPrefix':
            if not isinstance(value, str) or not value:
                raise InputError('stripPrefix требует непустую строку')
        elif value is not True:
            raise InputError(f'{operation} должен иметь значение true')


def validate_assertion(spec):
    mapping(spec, {'all'})
    clauses = spec.get('all')
    if not isinstance(clauses, list) or not 1 <= len(clauses) <= 128:
        raise InputError('assertion.all должен содержать от 1 до 128 условий')
    validate_refs(spec)
    for clause in clauses:
        mapping(clause, {'query', 'count', 'some', 'every'}, {'query'})
        validate_query(clause['query'])
        if len(clause) < 2:
            raise InputError('Укажите count, some или every')
        if 'count' in clause and (type(clause['count']) is not int or clause['count'] < 0):
            raise InputError('count должен быть неотрицательным целым')
        for op in ('some', 'every'):
            if op in clause:
                validate_filter(clause[op])


def normalize_options(value):
    mapping(value, {'aliases', 'resolveAliases', 'lowerLocalOverloads', 'missingMethodFails'})
    aliases = aliases_config(value=value.get('aliases', {}))
    rules = value.get('resolveAliases', [])
    if not isinstance(rules, list) or len(rules) > 256:
        raise InputError('resolveAliases должен быть списком не более чем из 256 правил')
    for rule in rules:
        mapping(rule, {'owner', 'name', 'select', 'unless'}, {'owner', 'select'})
        key = rule['owner']
        if not isinstance(key, str):
            raise InputError('owner должен быть строкой')
        aliases_config(value={'owners': {key: key}})
        if 'name' in rule:
            name = rule['name']
            if not isinstance(name, str):
                raise InputError('name должен быть строкой')
            aliases_config(value={'names': {key: {name: name}}})
        validate_query(rule['select'], execution=False)
        if 'unless' in rule:
            validate_query(rule['unless'], execution=False)
    validate_refs(rules)
    for key in ('lowerLocalOverloads', 'missingMethodFails'):
        if type(value.get(key, False)) is not bool:
            raise InputError(f'{key} должен быть логическим значением')
    return {**value, 'aliases': aliases}


def lookup(value, path):
    for key in path:
        if isinstance(value, dict) and isinstance(key, (str, int)):
            value = value.get(key, MISSING)
        elif isinstance(value, list) and type(key) is int and 0 <= key < len(value):
            value = value[key]
        else:
            return MISSING
    return value


def strip_ids(value):
    if isinstance(value, dict):
        return {key: strip_ids(item) for key, item in value.items() if key != 'Ид'}
    if isinstance(value, list):
        return [strip_ids(item) for item in value]
    return value


def equal(actual, expected):
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(equal(actual[k], expected[k]) for k in actual)
    return actual == expected


def compact_type(value, local):
    if isinstance(value, list):
        return [compact_type(item, local) for item in value]
    if isinstance(value, str):
        text = re.sub(r'\s+', '', value)
        return re.sub(r'\w+::', '', text) if local else text
    return value


def matches(value, predicates):
    for predicate in predicates:
        actual = lookup(value, predicate.get('path', []))
        operation, expected = next((k, v) for k, v in predicate.items() if k != 'path')
        if operation == 'exists':
            passed = (actual is not MISSING) is expected
        elif actual is MISSING:
            passed = False
        elif operation == 'eq':
            passed = equal(actual, expected)
        elif operation == 'contains':
            passed = (isinstance(actual, list) and any(equal(item, expected) for item in actual)
                      or isinstance(actual, str) and isinstance(expected, str) and expected in actual)
        elif operation in ('prefix', 'suffix'):
            passed = isinstance(actual, str) and isinstance(expected, str) and (
                actual.startswith(expected) if operation == 'prefix' else actual.endswith(expected))
        elif operation in ('typeEq', 'typeEqLocal'):
            local = operation == 'typeEqLocal'
            passed = equal(compact_type(actual, local), compact_type(expected, local))
        else:
            passed = isinstance(actual, list) and isinstance(expected, list) and (
                all(any(equal(a, b) for b in expected) for a in actual)
                and all(any(equal(a, b) for a in actual) for b in expected))
        if not passed:
            return False
    return True


class QueryEngine:
    def __init__(self, root, model, aliases=None):
        self.root, self.model = root, model
        self.aliases = deepcopy(aliases or {})
        self.elements = {self.identity(e): e['properties'] for e in model['elements']}
        self.project = {**model, 'elements': [{**e, 'qualifiedName': self.identity(e)} for e in model['elements']]}
        self.modules = {self.identity(m): m for m in model['modules']}
        self.parsed = {}
        self.executions = {}
        self.remaining = 5_000_000

    @staticmethod
    def identity(item):
        return '::'.join(filter(None, (item['namespace'], item['name'])))

    def spend(self, count=1):
        self.remaining -= count
        if self.remaining < 0:
            raise InputError('Превышен лимит вычислений декларативных запросов')

    def substitute(self, value):
        if isinstance(value, list):
            return [self.substitute(item) for item in value]
        if isinstance(value, dict):
            if '$owner' in value:
                name = owner_name(value['$owner'], self.aliases)
                return name.split('::')[-1] if value.get('short') else name
            if '$name' in value:
                owner, name = value['$name']
                return value.get('prefix', '') + self.aliases.get('names', {}).get(owner, {}).get(name, name)
            return {key: self.substitute(item) for key, item in value.items()}
        return value

    def method_facts(self, owner):
        if owner not in self.parsed:
            module = self.modules[owner]
            source = (self.root / module['sourceFile']).read_text(encoding='utf-8-sig')
            nodes, _, errors = parse_module(source)
            facts = []
            if not errors:
                for node, method in zip((node for node in nodes if node.end is not None), module['methods']):
                    facts.append({**method, 'parameterTypes': [p['type'] for p in method['parameters']],
                                  'tokens': [t.value for t in lex(source[node.start:node.end])],
                                  'bodyTokens': [t.value for t in lex(source[node.header_end:node.end])],
                                  'calls': [{'receiver': receiver, 'name': name}
                                            for receiver, name in method_calls(source, node)]})
            self.parsed[owner] = facts
        return self.parsed[owner]

    def start(self, query):
        source = query['from']
        if source == 'project':
            return [self.project]
        if source == 'execution':
            value = self.executions.get(query['criterion'])
            return [value] if value is not None else []
        owner = query['owner']
        if source == 'element':
            return [self.elements[owner]] if owner in self.elements else []
        if owner not in self.modules:
            return []
        facts = self.method_facts(owner)
        if source == 'method':
            return [method for method in facts if method['name'] == query['method']]
        return [{**self.modules[owner], 'methods': facts,
                 'calls': [call for method in facts for call in method['calls']]}]

    def query(self, spec):
        spec = self.substitute(spec)
        rows = self.start(spec)
        for step in spec.get('steps', []):
            operation, value = next(iter(step.items()))
            self.spend(len(rows))
            if operation == 'path':
                rows = [found for row in rows if (found := lookup(row, value)) is not MISSING]
            elif operation == 'where':
                rows = [row for row in rows if matches(row, value)]
            elif operation in ('items', 'walk'):
                expanded = []
                for row in rows:
                    if operation == 'items':
                        if isinstance(row, list):
                            self.spend(len(row))
                            expanded.extend(row)
                    else:
                        for _, item in tree(row):
                            self.spend()
                            if isinstance(item, dict):
                                expanded.append(item)
                rows = expanded
            elif operation == 'withoutIds':
                rows = [strip_ids(row) for row in rows]
            elif operation == 'stripPrefix':
                rows = [row.removeprefix(value) for row in rows
                        if isinstance(row, str) and row.startswith(value)]
            elif operation == 'distinctStrings':
                rows = list(dict.fromkeys(row for row in rows if isinstance(row, str)))
        return rows

    def resolve(self, rules):
        for rule in rules:
            owner = rule['owner']
            if 'name' in rule:
                target, key = self.aliases.setdefault('names', {}).setdefault(owner, {}), rule['name']
            else:
                target, key = self.aliases.setdefault('owners', {}), owner
            if key in target or ('unless' in rule and self.query(rule['unless'])):
                continue
            candidates = self.query(rule['select'])
            if len(candidates) != 1 or not isinstance(candidates[0], str):
                continue
            candidate = candidates[0]
            inferred = {'names': {owner: {key: candidate}}} if 'name' in rule else {'owners': {key: candidate}}
            try:
                aliases_config(value=inferred)
            except InputError:
                continue
            target[key] = candidate

    def assertion(self, clauses):
        for clause in clauses:
            rows = self.query(clause['query'])
            if 'count' in clause and len(rows) != clause['count']:
                return False
            if 'some' in clause:
                predicates = self.substitute(clause['some'])
                if not any(matches(row, predicates) for row in rows):
                    return False
            if 'every' in clause:
                predicates = self.substitute(clause['every'])
                if not rows or not all(matches(row, predicates) for row in rows):
                    return False
        return True
