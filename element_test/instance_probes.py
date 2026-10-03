"""Declaration-checked mutations of live Script instances, never serialized copies."""
import re
from .indexer import parse_module, IDENT
from .yaml_io import InvalidTestError


def prepare_probes(plan):
    probes = plan.check.get('probeMutations', [])
    if not isinstance(probes, list) or len(probes) > 40:
        raise InvalidTestError('probeMutations требует список до 40 мутаций')
    if probes and (not plan.check.get('snapshotArgs') or plan.check.get('captureException') or plan.check.get('observeFailure')):
        raise InvalidTestError('probeMutations требует snapshotArgs без перехвата исключений')
    steps = plan.sequence or [{'method': plan.entry.identity.declaration, 'args': plan.check.get('args', [])}]
    c = plan.contracts
    for probe in probes:
        if not isinstance(probe, dict) or set(probe) - {'action','target','argument','path','value'} or not {'action','target','path','value'} <= set(probe):
            raise InvalidTestError('probeMutations: требуются action, target, path, value')
        index = probe['action']
        if type(index) is not int or not 0 <= index < len(steps):
            raise InvalidTestError('probeMutations: неизвестный action')
        symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == plan.module['sourceFile'] and s.identity.declaration == steps[index]['method'])
        suffix = str(index) if plan.sequence else ''
        if probe['target'] == 'result' and 'argument' not in probe:
            typ = parse_module(symbol.source)[0][0].return_type(symbol.source)
            expression = 'Результат' + suffix
        elif probe['target'] == 'argument' and type(probe.get('argument')) is int and 0 <= probe['argument'] < len(symbol.parameter_types):
            typ = symbol.parameter_types[probe['argument']]
            expression = 'ТестАргумент' + suffix + '_' + str(probe['argument'])
        elif probe['target'] == 'context' and 'context' in plan.check and 'argument' not in probe:
            typ = plan.form['canonical'] if plan.form else plan.module['name']
            expression = 'Контекст'
        else:
            raise InvalidTestError('probeMutations: неверный target/argument')
        if not typ or typ == 'ничто' or not isinstance(probe['path'], str) or not probe['path']:
            raise InvalidTestError('probeMutations требует типизированный объект и непустой path')
        typ = c.canonical_type(typ)
        for part in probe['path'].split('.'):
            if typ.endswith('?'):
                typ = typ[:-1]; expression = '(' + expression + ' как ' + c.sbsl_type(typ) + ')'
            collection = re.fullmatch(r'Массив<(.+)>', typ)
            if collection and re.fullmatch(r'0|[1-9][0-9]*', part):
                typ = collection[1]; expression += '[' + part + ']'
            elif re.fullmatch(IDENT, part):
                fields = {f['Имя']: f['Тип'] for f in c.fields.get(typ, [])}
                if part not in fields:
                    raise InvalidTestError('probeMutations: неизвестное поле ' + part)
                typ = fields[part]; expression += '.' + part
            else:
                raise InvalidTestError('probeMutations: неверный сегмент path')
        plan.probes.append({'expression': expression, 'literal': c.literal(probe['value'], typ), 'type':typ,
                            'sourceFile':symbol.identity.source_file,'start':symbol.start,'end':symbol.end})
