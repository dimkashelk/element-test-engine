"""Bounded operation state machines; state is author-declared, never inferred from code."""
from copy import deepcopy
import re

from .yaml_io import InvalidTestError


def validate_sequence(spec):
    from .generative import integer, validate_domain, domain_size
    if not isinstance(spec, dict) or set(spec) != {'initialState', 'finalState', 'minLength', 'maxLength', 'operations'}:
        raise InvalidTestError('generative.sequence требует initialState/finalState/minLength/maxLength/operations')
    integer(spec['minLength'], 1, 40, 'sequence.minLength')
    integer(spec['maxLength'], spec['minLength'], 40, 'sequence.maxLength')
    def state(value):
        return isinstance(value, str) and 1 <= len(value) <= 64
    if not state(spec['initialState']) or not state(spec['finalState']):
        raise InvalidTestError('initialState/finalState требуют непустую строку до 64 символов')
    operations = spec['operations']
    if not isinstance(operations, list) or not 1 <= len(operations) <= 32:
        raise InvalidTestError('operations требует 1..32 операций')
    names = set()
    for op in operations:
        if not isinstance(op, dict) or set(op) != {'name', 'method', 'from', 'to', 'domains'}:
            raise InvalidTestError('Операция требует name/method/from/to/domains')
        if any(not isinstance(op[k], str) or not re.fullmatch(r'[^\W\d]\w*', op[k]) for k in ('name', 'method')) or op['name'] in names:
            raise InvalidTestError('Некорректное или повторное имя операции')
        names.add(op['name'])
        if not state(op['from']) or not state(op['to']):
            raise InvalidTestError('from/to требуют состояния до 64 символов')
        if not isinstance(op['domains'], list) or len(op['domains']) > 8:
            raise InvalidTestError('Операция требует 0..8 domains')
        for domain in op['domains']: validate_domain(domain)
    if spec['maxLength'] * max(sum(domain_size(d) for d in op['domains']) + len(op['name']) for op in operations) > 4096:
        raise InvalidTestError('Последовательность превышает бюджет размера 4096')
    if not lengths(spec):
        raise InvalidTestError('Нет допустимой последовательности объявленной длины')


def paths(spec):
    """States from which exactly n further operations can execute."""
    possible = [{spec['finalState']}]
    for _ in range(spec['maxLength']):
        possible.append({op['from'] for op in spec['operations'] if op['to'] in possible[-1]})
    return possible


def lengths(spec):
    possible = paths(spec)
    return [n for n in range(spec['minLength'], spec['maxLength'] + 1) if spec['initialState'] in possible[n]]


def accepts_sequence(spec, values):
    from .generative import accepts
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], list): return False
    steps = values[0]
    if not spec['minLength'] <= len(steps) <= spec['maxLength']: return False
    state = spec['initialState']; operations = {op['name']: op for op in spec['operations']}
    for step in steps:
        if not isinstance(step, dict) or set(step) != {'operation', 'args'} or not isinstance(step['operation'], str): return False
        op = operations.get(step['operation'])
        if not op or op['from'] != state or not isinstance(step['args'], list) or len(step['args']) != len(op['domains']): return False
        if not all(accepts(d, v) for d, v in zip(op['domains'], step['args'])): return False
        state = op['to']
    return state == spec['finalState']


def sequence_inputs(config, rng):
    from .generative import draw
    spec = config['sequence']; allowed = lengths(spec); possible = paths(spec)
    cases = []
    for i in range(config['cases']):
        count = allowed[0 if i % 2 == 0 else -1] if i < 3 else rng.choice(allowed)
        state = spec['initialState']; steps = []
        for index in range(count):
            options = [op for op in spec['operations'] if op['from'] == state and op['to'] in possible[count-index-1]]
            op = rng.choice(options)
            steps.append({'operation': op['name'], 'args': [draw(d, rng, i if i < 3 else None) for d in op['domains']]})
            state = op['to']
        cases.append([steps])
    return cases


def sequence_check(plain, spec, values):
    operations = {op['name']: op for op in spec['operations']}
    steps = [{'method': operations[s['operation']]['method'], 'args': deepcopy(s['args'])} for s in values[0]]
    return {**deepcopy(plain), 'target': {**plain['target'], 'method': steps[0]['method']},
            'args': deepcopy(steps[0]['args']), 'sequence': steps}


def sequence_shrinks(config, values):
    from .generative import smaller
    spec = config['sequence']; steps = values[0]
    # Delete contiguous chunks only when the remaining state path is still valid.
    for size in range(len(steps) - spec['minLength'], 0, -1):
        for start in range(len(steps) - size + 1):
            candidate = [deepcopy(steps[:start] + steps[start+size:])]
            if accepts_sequence(spec, candidate): yield candidate
    operations = {op['name']: op for op in spec['operations']}
    for i, step in enumerate(steps):
        for j, (domain, value) in enumerate(zip(operations[step['operation']]['domains'], step['args'])):
            for smaller_value in smaller(domain, value):
                candidate = deepcopy(values); candidate[0][i]['args'][j] = smaller_value
                if accepts_sequence(spec, candidate): yield candidate
