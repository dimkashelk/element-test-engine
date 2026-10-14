"""Bounded deterministic inputs and shrinking; only SBSL decides the relation."""
from copy import deepcopy
import json
from decimal import localcontext
from datetime import timedelta
import re

from .yaml_io import InvalidTestError, InputError
from .generative_values import number, packed, temporal, time_text, ticks, from_tick, value_tick, json_value, TEMPORAL

OPS = {'equal': 2, 'notEqual': 2, 'less': 2, 'lessEqual': 2, 'greater': 2,
       'greaterEqual': 2, 'add': 2, 'subtract': 2, 'multiply': 2,
       'and': 2, 'or': 2, 'not': 1, 'length': 1, 'sum': 1, 'get': 2, 'at': 2, 'reverse': 1,
       'set': 3, 'date': 1, 'dateTime': 1, 'instant': 1, 'number': 1,
       'dateAddDays': 2, 'dateAddSeconds': 2}


class Generator:
    """Versioned SplitMix64 with rejection sampling, independent of Python RNG."""
    def __init__(self, seed): self.state = seed

    def randrange(self, stop):
        if type(stop) is not int or not 0 < stop <= 2**64:
            raise InvalidTestError('Диапазон генератора требует 1..2**64')
        limit = 2**64 - (2**64 % stop)
        mask = 2**64 - 1
        while True:
            self.state = (self.state + 0x9E3779B97F4A7C15) & mask
            value = self.state
            value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & mask
            value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & mask
            value ^= value >> 31
            if value < limit: return value % stop

    def randint(self, low, high): return low + self.randrange(high - low + 1)

    def choice(self, values): return values[self.randrange(len(values))]


def options_schema():
    """Shape schema; signature, constraints and expression arity also use validate()."""
    def obj(properties, required):
        return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}
    domain = obj({'type': {'type': 'string', 'minLength': 1},
        'values': {'type': 'array', 'minItems': 1, 'maxItems': 64},
        'min': {'type': ['number', 'string', 'object']},
        'max': {'type': ['number', 'string', 'object']},
        'scale': {'type': 'integer', 'minimum': 0, 'maximum': 12},
        'minLength': {'type': 'integer', 'minimum': 0, 'maximum': 32},
        'maxLength': {'type': 'integer', 'minimum': 0, 'maximum': 32},
        'alphabet': {'type': 'string', 'minLength': 1, 'maxLength': 64},
        'items': {'$ref': '#/$defs/domain'},
        'fields': {'type': 'object', 'minProperties': 1, 'maxProperties': 32,
                   'additionalProperties': {'$ref': '#/$defs/domain'}}}, ['type'])
    expr = {'oneOf': [obj({'arg': {'type': 'integer', 'minimum': 0, 'maximum': 7}}, ['arg']),
        obj({'result': {'const': True}}, ['result']), obj({'pairedResult': {'const': True}}, ['pairedResult']),
        obj({'literal': {}}, ['literal']),
        obj({'op': {'enum': list(OPS)}, 'args': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': {'$ref': '#/$defs/expression'}}}, ['op', 'args'])]}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
        **obj({'schemaVersion': {'const': 1}, 'seed': {'type': 'integer', 'minimum': 0, 'maximum': 2**32-1},
            'cases': {'type': 'integer', 'minimum': 1, 'maximum': 64},
            'maxShrinks': {'type': 'integer', 'minimum': 0, 'maximum': 64},
            'domains': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {'$ref': '#/$defs/domain'}},
            'relation': {'$ref': '#/$defs/expression'},
            'transform': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {'$ref': '#/$defs/expression'}},
            'replayInputs': {'type': 'array', 'minItems': 1, 'maxItems': 64, 'items': {'type': 'array', 'minItems': 1, 'maxItems': 8}},
            'replaySourceHash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}, ['schemaVersion', 'seed', 'cases', 'domains', 'relation']),
        '$defs': {'domain': domain, 'expression': expr}}


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise InvalidTestError(f'{label} требует целое {low}..{high}')


def accepts(domain, value):
    typ = domain['type']
    if 'values' in domain:
        return any(type(value) is type(v) and value == v for v in domain['values'])
    if typ.endswith('?'):
        return value is None or accepts({**domain, 'type': typ[:-1]}, value)
    if typ == 'Булево':
        return type(value) is bool
    if typ == 'Число':
        try:
            tick = value_tick(domain, value); low, high = ticks(domain)
            return low <= tick <= high
        except InvalidTestError: return False
    if typ in TEMPORAL:
        try:
            tick = value_tick(domain, value); low, high = ticks(domain)
            return low <= tick <= high
        except InvalidTestError: return False
    if typ == 'Строка':
        return isinstance(value, str) and domain['minLength'] <= len(value) <= domain['maxLength'] and all(c in domain['alphabet'] for c in value)
    if typ.startswith('Массив<'):
        return isinstance(value, list) and domain['minLength'] <= len(value) <= domain['maxLength'] and all(accepts(domain['items'], v) for v in value)
    if 'fields' in domain:
        return isinstance(value, dict) and set(value) == set(domain['fields']) and all(accepts(d, value[k]) for k,d in domain['fields'].items())
    return False


def type_accepts(typ, value):
    if typ.endswith('?'):
        return value is None or type_accepts(typ[:-1], value)
    if typ == 'Число':
        try: number(value); return True
        except InvalidTestError: return False
    if typ in TEMPORAL:
        try: temporal(typ, value); return True
        except InvalidTestError: return False
    if typ == 'Булево': return type(value) is bool
    if typ == 'Строка': return isinstance(value, str) and len(value) <= 32
    if typ.startswith('Массив<') and typ.endswith('>'):
        return isinstance(value, list) and len(value) <= 32 and all(type_accepts(typ[7:-1], v) for v in value)
    return nominal(typ) and (isinstance(value, (dict, str))) and json_value(value)


def nominal(typ):
    return bool(re.fullmatch(r'[^\W\d]\w*(?:::[^\W\d]\w*)*(?:\.(?:Ссылка|Объект|[^\W\d]\w*))?', typ)) and typ not in {'Булево', 'Число', 'Строка'}


def domain_size(domain):
    if 'values' in domain:
        return max(len(json.dumps(v, ensure_ascii=False)) for v in domain['values'])
    if 'items' in domain: return 1 + domain['maxLength'] * domain_size(domain['items'])
    if 'fields' in domain: return 1 + sum(len(k) + domain_size(d) for k,d in domain['fields'].items())
    return domain.get('maxLength', 1)


def validate_domain(domain, depth=0):
    if not isinstance(domain, dict) or depth > 4 or not isinstance(domain.get('type'), str):
        raise InvalidTestError('Некорректный типизированный домен')
    typ = domain['type'].removesuffix('?')
    if typ.count('Массив<') + depth > 4:
        raise InvalidTestError('Тип домена превышает глубину 4')
    if typ not in {'Булево', 'Число', 'Строка'} | TEMPORAL and not typ.startswith('Массив<') and not nominal(typ):
        raise InvalidTestError('Неподдержанный тип домена')
    if 'values' in domain:
        if set(domain) != {'type', 'values'} or not isinstance(domain['values'], list) or not 1 <= len(domain['values']) <= 64:
            raise InvalidTestError('values требует 1..64 типизированных значений')
        for value in domain['values']:
            if not type_accepts(domain['type'], value):
                raise InvalidTestError('values не соответствует типу или границам домена')
        # Enumeration is ordered, so shrinking has a deterministic well-founded order.
        if len({json.dumps(v, sort_keys=True, ensure_ascii=False) for v in domain['values']}) != len(domain['values']):
            raise InvalidTestError('Повтор values')
        return
    keys = {'type'}
    if 'fields' in domain:
        fields = domain['fields']; keys.add('fields')
        if not nominal(typ) or not isinstance(fields, dict) or not 1 <= len(fields) <= 32:
            raise InvalidTestError('fields требует составной проектный тип и 1..32 поля')
        for name, child in fields.items():
            if not isinstance(name, str) or not re.fullmatch(r'[^\W\d]\w*', name):
                raise InvalidTestError('Некорректное имя поля домена')
            validate_domain(child, depth + 1)
    elif typ == 'Число' or typ in TEMPORAL:
        keys |= {'min', 'max'}
        if typ == 'Число':
            if 'scale' in domain: keys.add('scale')
            integer(domain.get('scale', 0), 0, 12, 'scale')
        if 'min' not in domain or 'max' not in domain:
            raise InvalidTestError('Домен требует min/max')
        low, high = ticks(domain)
        if typ in TEMPORAL and temporal(typ, domain['max']) < temporal(typ, domain['min']) or low > high or high-low >= 2**64:
            raise InvalidTestError('Некорректные границы домена')
        if typ in {'ДатаВремя', 'Момент'}:
            # Range generation uses days/seconds; explicit values may keep microseconds.
            if temporal(typ, domain['max']).microsecond != temporal(typ, domain['min']).microsecond:
                raise InvalidTestError('Границы времени должны лежать на секундной сетке')
    elif typ == 'Строка' or typ.startswith('Массив<'):
        keys |= {'minLength', 'maxLength', 'alphabet' if typ == 'Строка' else 'items'}
        integer(domain.get('minLength'), 0, 32, 'minLength')
        integer(domain.get('maxLength'), domain['minLength'], 32, 'maxLength')
        if typ == 'Строка':
            if not isinstance(domain.get('alphabet'), str) or not 1 <= len(domain['alphabet']) <= 64 or len(set(domain['alphabet'])) != len(domain['alphabet']):
                raise InvalidTestError('alphabet требует 1..64 различных символов')
        else:
            validate_domain(domain.get('items'), depth + 1)
            if typ != 'Массив<' + domain['items']['type'] + '>':
                raise InvalidTestError('Тип items не соответствует Массив')
    elif typ != 'Булево':
        raise InvalidTestError('Проектный домен требует values или fields')
    if set(domain) != keys:
        raise InvalidTestError('Неизвестные или отсутствующие поля домена')


def validate_expression(expr, arity, paired, depth=0, transform=False):
    if depth > 8 or not isinstance(expr, dict):
        raise InvalidTestError('Отношение требует дерево выражения глубиной до 8')
    if set(expr) == {'arg'}:
        integer(expr['arg'], 0, arity - 1, 'arg'); return
    if set(expr) == {'literal'}:
        value = expr['literal']
        if not json_value(value) or len(json.dumps(value, ensure_ascii=False)) > 4096:
            raise InvalidTestError('literal превышает типизированные границы')
        return
    if not transform and (set(expr) == {'result'} and expr['result'] is True or paired and set(expr) == {'pairedResult'} and expr['pairedResult'] is True):
        return
    if set(expr) != {'op', 'args'} or not isinstance(expr['op'], str) or expr['op'] not in OPS or not isinstance(expr['args'], list) or len(expr['args']) != OPS[expr['op']]:
        raise InvalidTestError('Неизвестный оператор или арность отношения')
    if transform and expr['op'] not in {'add', 'subtract', 'multiply', 'reverse', 'get', 'at', 'set', 'dateAddDays', 'dateAddSeconds'}:
        raise InvalidTestError('Неподдержанный оператор transform')
    for child in expr['args']:
        validate_expression(child, arity, paired, depth + 1, transform)


def validate(config):
    if not isinstance(config, dict) or set(config) - {'schemaVersion', 'seed', 'cases', 'domains', 'relation', 'transform', 'maxShrinks', 'replayInputs', 'replaySourceHash'}:
        raise InvalidTestError('Некорректный generative')
    if type(config.get('schemaVersion')) is not int or config['schemaVersion'] != 1:
        raise InvalidTestError('generative.schemaVersion требует 1')
    integer(config.get('seed'), 0, 2**32 - 1, 'seed')
    integer(config.get('cases'), 1, 64, 'cases')
    integer(config.get('maxShrinks', 32), 0, 64, 'maxShrinks')
    domains = config.get('domains')
    if not isinstance(domains, list) or not 1 <= len(domains) <= 8:
        raise InvalidTestError('domains требует 1..8 доменов')
    for domain in domains:
        validate_domain(domain)
    if sum(domain_size(d) for d in domains) > 4096:
        raise InvalidTestError('Домены превышают бюджет размера 4096')
    if 'replayInputs' in config:
        replay = config['replayInputs']
        if not isinstance(replay, list) or len(replay) != config['cases'] or any(not isinstance(v, list) or len(v) != len(domains) or not all(accepts(d, x) for d, x in zip(domains, v)) for v in replay):
            raise InvalidTestError('replayInputs не соответствует cases/domains')
    if 'replaySourceHash' in config:
        import re
        if not isinstance(config['replaySourceHash'], str) or not re.fullmatch('[a-f0-9]{64}', config['replaySourceHash']):
            raise InvalidTestError('replaySourceHash требует SHA-256')
    transform = config.get('transform')
    if transform is not None:
        if not isinstance(transform, list) or len(transform) != len(domains):
            raise InvalidTestError('transform требует выражение для каждого аргумента')
        for expr in transform:
            validate_expression(expr, len(domains), False, transform=True)
    validate_expression(config.get('relation'), len(domains), transform is not None)


def draw(domain, rng, boundary=None):
    typ = domain['type']
    if 'values' in domain:
        values = domain['values']; return deepcopy(values[boundary % len(values)] if boundary is not None else rng.choice(values))
    if typ.endswith('?'):
        if boundary == 0 or boundary is None and rng.randrange(4) == 0:
            return None
        return draw({**domain, 'type': typ[:-1]}, rng, boundary)
    if typ == 'Булево':
        return bool(boundary % 2) if boundary is not None else bool(rng.randrange(2))
    if typ == 'Число' or typ in TEMPORAL:
        low, high = ticks(domain)
        tick = [low, high, min(high, max(low, 0))][boundary % 3] if boundary is not None else rng.randint(low, high)
        return from_tick(domain, tick)
    if 'fields' in domain:
        return {k: draw(d, rng, boundary) for k,d in domain['fields'].items()}
    n = [domain['minLength'], domain['maxLength']][boundary % 2] if boundary is not None else rng.randint(domain['minLength'], domain['maxLength'])
    if typ == 'Строка':
        return ''.join(rng.choice(domain['alphabet']) for _ in range(n))
    return [draw(domain['items'], rng) for _ in range(n)]


def inputs(config):
    validate(config)
    if 'replayInputs' in config: return deepcopy(config['replayInputs'])
    rng = Generator(config['seed'])
    return [[draw(d, rng, i if i < 3 else None) for d in config['domains']] for i in range(config['cases'])]


def transformed(config, args):
    def evaluate(expr):
        if 'arg' in expr: return deepcopy(args[expr['arg']])
        if 'literal' in expr: return deepcopy(expr['literal'])
        values = [evaluate(e) for e in expr['args']]
        if expr['op'] == 'reverse':
            if not isinstance(values[0], (str, list)):
                raise InvalidTestError('reverse требует Строка или Массив')
            return values[0][::-1]
        if expr['op'] == 'get':
            obj, key = values
            if not isinstance(obj, dict) or not isinstance(key, str) or key not in obj:
                raise InvalidTestError('get требует существующее поле объекта')
            return deepcopy(obj[key])
        if expr['op'] == 'at':
            array, index = values
            if not isinstance(array, list) or type(index) is not int or not 0 <= index < len(array):
                raise InvalidTestError('at требует допустимый индекс массива')
            return deepcopy(array[index])
        if expr['op'] == 'set':
            obj, key, value = values
            if not isinstance(obj, dict) or key not in obj:
                raise InvalidTestError('set требует существующее поле объекта')
            obj[key] = value; return obj
        a, b = values
        if expr['op'] in {'dateAddDays', 'dateAddSeconds'}:
            typ = 'Момент' if isinstance(a, str) and a.endswith('Z') else 'ДатаВремя' if isinstance(a, str) and 'T' in a else 'Дата'
            integer(b, -1000000, 1000000, 'Сдвиг даты')
            if typ == 'Дата' and expr['op'] == 'dateAddSeconds':
                raise InvalidTestError('Дата не поддерживает секундный сдвиг')
            try:
                return time_text(typ, temporal(typ, a) + (timedelta(days=b) if expr['op'] == 'dateAddDays' else timedelta(seconds=b)))
            except OverflowError as exc: raise InvalidTestError('Сдвиг вышел за диапазон даты') from exc
        with localcontext() as ctx:
            ctx.prec = 80
            a, b = number(a), number(b)
            return packed({'add': lambda: a+b, 'subtract': lambda: a-b, 'multiply': lambda: a*b}[expr['op']]())
    result = [evaluate(e) for e in config['transform']]
    if not all(accepts(d, v) for d, v in zip(config['domains'], result)):
        raise InvalidTestError('transform вышел за объявленный домен')
    return result


def planning_check(check):
    config = check['generative']; validate(config)
    forbidden = {'args', 'sequence', 'expected', 'comparison', 'interface', 'querySource', 'formRequirement', 'report', 'navigation', 'panel', 'integration'} & check.keys()
    if forbidden:
        raise InvalidTestError('generative несовместим с ' + ', '.join(sorted(forbidden)))
    if 'storage' in check and not isinstance(check['storage'], dict):
        raise InvalidTestError('storage требует объект')
    if check.get('storage', {}).get('backend') == 'postgres':
        raise InvalidTestError('generative пока требует изолированный memory runtime')
    candidates = inputs(config)
    if 'transform' in config:
        for args in candidates: transformed(config, args)
    return {k: deepcopy(v) for k, v in check.items() if k != 'generative'} | {'args': candidates[0], 'branches': check.get('branches', True)}


def smaller(domain, value):
    if 'values' in domain:
        for item in domain['values']:
            if type(item) is type(value) and item == value: break
            yield deepcopy(item)
        return
    typ = domain['type']
    if typ.endswith('?'):
        if value is None: return
        yield None
        yield from smaller({**domain, 'type': typ[:-1]}, value); return
    if typ == 'Число' or typ in TEMPORAL:
        low, high = ticks(domain); current = value_tick(domain, value)
        target = min(high, max(low, 0))
        if current == target: return
        yield from_tick(domain, target)
        mid = target + (abs(current - target) // 2) * (1 if current > target else -1)
        if mid not in {target, current}: yield from_tick(domain, mid)
        # Probe successively closer neighbours when the halfway point passes.
        # A fine decimal/date grid must not require one execution per tick.
        distance = abs(current - target) // 2
        while distance > 1:
            distance //= 2
            yield from_tick(domain, current - distance * (1 if current > target else -1))
        yield from_tick(domain, current - (1 if current > target else -1))
    elif typ == 'Булево':
        if value: yield False
    elif 'fields' in domain:
        for name, child in domain['fields'].items():
            for item in smaller(child, value[name]):
                yield {**deepcopy(value), name: item}
    else:
        minimum = domain['minLength']
        if len(value) > minimum:
            yield value[:minimum]
            for i in range(len(value)):
                yield value[:i] + value[i+1:]
        if typ == 'Строка':
            for i, char in enumerate(value):
                if char != domain['alphabet'][0]:
                    yield value[:i] + domain['alphabet'][0] + value[i+1:]
        else:
            for i, item in enumerate(value):
                for candidate in smaller(domain['items'], item):
                    yield value[:i] + [candidate] + value[i+1:]


def shrink_candidates(config, args):
    seen = set()
    for i, (domain, value) in enumerate(zip(config['domains'], args)):
        for candidate in smaller(domain, value):
            result = deepcopy(args); result[i] = candidate
            if not accepts(domain, candidate): continue
            key = json.dumps(result, ensure_ascii=False, sort_keys=True)
            if key in seen: continue
            seen.add(key)
            if 'transform' in config:
                try: transformed(config, result)
                except InvalidTestError: continue
            yield result


def run_generated(root, model, check, temporary, *, plan_sink=None):
    from .runtime import run_pure, execute_engine
    try:
        plain = planning_check(check)
        config = check['generative']
        if config.get('replaySourceHash', model['sourceHash']) != model['sourceHash']:
            raise InvalidTestError('Контрпример относится к другому sourceHash')
        # Prepare before any execution; validates every generated value against the signature.
        from .execution_plan import plan_execution
        plan = plan_execution(root, model, plain)
        if [plan.contracts.canonical_type(d['type']) for d in config['domains']] != [plan.contracts.canonical_type(t) for t in plan.entry.parameter_types]:
            raise InvalidTestError('Типы доменов не совпадают с сигнатурой метода')
        validate_project_domains(config['domains'], plan.contracts)
        evidence = {'schemaVersion': 1, 'seed': config['seed'], 'generatorVersion': 'v1-splitmix64',
                    'requestedCases': config['cases'], 'executedCases': 0, 'runtimeExecutions': 0, 'shrinkExecutions': 0, 'shrinkAttempts': [],
                    'sourceHash': model['sourceHash'], 'counterexample': None}
        records, trace = plan.branches, []
        from .branches import summarize
        def unavailable_result(result):
            coverage = {'branchCoverage': summarize(records, trace)} if plain['branches'] else {}
            return {**result, 'generative': evidence, **coverage}
        def execute(args, shrinking=False):
            results = {}
            variants = [('result', args)]
            if 'transform' in config:
                variants.append(('pairedResult', transformed(config, args)))
            for name, values in variants:
                result = run_pure(root, model, {**plain, 'args': values}, temporary,
                                  exact_numbers=True,
                                  plan_sink=plan_sink if not evidence['executedCases'] and name == 'result' else None)
                trace.extend(result.get('trace', []))
                if result['status'] != 'EXECUTED': return None, result
                evidence['runtimeExecutions'] += 1
                results[name] = result['actual']
                if name == 'pairedResult': results['pairedInputs'] = values
            evidence['shrinkExecutions' if shrinking else 'executedCases'] += 1
            return {'inputs': deepcopy(args), **results}, None
        model_path, assignment_path = temporary/'generated-model.json', temporary/'generated-assessment.json'
        model_path.write_text(json.dumps(model, ensure_ascii=False))
        def assess(cases):
            assignment = {'name': 'generated-relation', 'checks': [{'id': 'relation', 'type': 'runtime', 'points': 0,
                'generative': config, 'execution': {'status': 'EXECUTED', 'actual': {'cases': cases}}}]}
            assignment_path.write_text(json.dumps(assignment, ensure_ascii=False))
            return execute_engine('test', model_path, assignment_path, temporary)['checks'][0]
        cases = []
        for args in inputs(config):
            case, unavailable = execute(args)
            if unavailable: return unavailable_result(unavailable)
            cases.append(case)
        assessment = assess(cases)
        if assessment['status'] not in {'PASS', 'FAIL'}:
            return {'status': 'UNSUPPORTED', 'reasonCode': 'invalid_test', 'message': assessment.get('message'), 'generative': evidence}
        if assessment['status'] == 'FAIL':
            index = assessment['counterexampleIndex']; current = cases[index]
            original = deepcopy(current); attempts = 0; budget = config.get('maxShrinks', 32)
            while True:
                improved = False; exhausted = False
                for args in shrink_candidates(config, current['inputs']):
                    if attempts >= budget:
                        exhausted = True; break
                    attempts += 1
                    candidate, unavailable = execute(args, shrinking=True)
                    if unavailable:
                        return unavailable_result(unavailable)
                    verdict = assess([candidate])
                    evidence['shrinkAttempts'].append({'inputs': args, 'status': verdict['status']})
                    if verdict['status'] not in {'PASS', 'FAIL'}:
                        return {'status': 'UNSUPPORTED', 'reasonCode': 'invalid_test', 'message': verdict.get('message'), 'generative': evidence}
                    if verdict['status'] == 'FAIL':
                        current = candidate; improved = True; break
                if not improved: break
            evidence['counterexample'] = {'caseIndex': index, 'original': original, 'minimal': current,
                'minimality': 'budget-exhausted' if exhausted else 'local-under-declared-reductions',
                'replayCheck': {**deepcopy(check), 'generative': {**deepcopy(config), 'cases': 1, 'replayInputs': [current['inputs']], 'replaySourceHash': model['sourceHash'], 'maxShrinks': 0}}}
            cases = [current]
        coverage = {'branchCoverage': summarize(records, trace)} if plain['branches'] else {}
        return {'status': 'EXECUTED', 'actual': {'cases': cases}, 'generative': evidence, **coverage}
    except (InvalidTestError, InputError, ValueError, TypeError, KeyError) as exc:
        from .yaml_io import UnsupportedSyntaxError
        if isinstance(exc, UnsupportedSyntaxError):
            return {'status': 'UNSUPPORTED', 'reasonCode': 'unsupported_syntax', 'message': str(exc)}
        return {'status': 'UNSUPPORTED', 'reasonCode': 'invalid_test', 'message': str(exc)}


def validate_project_domains(domains, contracts):
    """Resolve every nominal value and field, including values not drawn by seed."""
    for domain in domains:
        typ = contracts.canonical_type(domain['type'])
        contracts.require(typ)
        if 'values' in domain:
            for value in domain['values']: contracts.literal(value, typ)
        if 'items' in domain:
            validate_project_domains([domain['items']], contracts)
        if 'fields' in domain:
            fields = {f['Имя']: contracts.canonical_type(f['Тип']) for f in contracts.fields.get(typ.removesuffix('?'), [])}
            if not fields or any(k not in fields or contracts.canonical_type(d['type']) != fields[k] for k,d in domain['fields'].items()):
                raise InvalidTestError('Поля домена не соответствуют проектному типу ' + typ)
            validate_project_domains(list(domain['fields'].values()), contracts)
            # Constructor requirements must hold for every produced object.
            contracts.literal(draw(domain, Generator(0), 0 if not typ.endswith('?') else 1), typ)
