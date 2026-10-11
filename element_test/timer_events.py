"""Bounded deterministic Script scheduler and declaration-resolved callbacks.

Python validates/lowers API spans. The event queue, clock and original callbacks
execute in Script in the same isolated runtime session as ordinary method calls.
"""
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path

from .indexer import method_binding_visible, method_local_bindings, parse_module
from .yaml_io import InvalidTestError, UnsupportedSyntaxError

OPERATIONS = {'ПодключитьОбработчикТаймера', 'ОтключитьОбработчикТаймера'}
STEPS = {'advanceTime', 'timerEvent', 'closeTimers'}


def timer_options_schema():
    """Schema of the runtime.timers options, not a new assignment type."""
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'runtime.timers v1', 'type': 'object', 'required': ['onClose'],
            'additionalProperties': False, 'properties': {
                'onClose': {'type': 'string', 'enum': ['cancel']},
                'maxEvents': {'type': 'integer', 'minimum': 1, 'maximum': 10000, 'default': 1000}}}


def validate_step(step, check):
    if not isinstance(step, dict) or not (set(step) & STEPS):
        return False
    if 'timers' not in check or len(step) != 1:
        raise InvalidTestError('Шаг таймера требует timers и одно действие')
    if 'advanceTime' in step and (type(step['advanceTime']) is not int or not 0 <= step['advanceTime'] <= 31536000000):
        raise InvalidTestError('advanceTime требует целое число миллисекунд 0..31536000000')
    if 'timerEvent' in step and step['timerEvent'] != 'next':
        raise InvalidTestError('timerEvent поддерживает только next')
    if 'closeTimers' in step and step['closeTimers'] is not True:
        raise InvalidTestError('closeTimers требует true')
    return True


def prepare_timers(plan):
    if 'timers' not in plan.check:
        return
    config = plan.check['timers']
    from .test_contracts import validate_schema
    validate_schema(config, timer_options_schema(), 'timers')
    # The bounded v1 schema validator does not implement maximum.
    if config.get('maxEvents', 1000) > 10000:
        raise InvalidTestError('timers требует onClose: cancel и maxEvents: 1..10000 (по умолчанию 1000)')
    if plan.clock.get('mode') != 'fixed' or plan.check.get('executorLocale') not in {'en-US', 'ru-RU'}:
        raise InvalidTestError('timers требует clock.mode: fixed и явный executorLocale')
    for i, step in enumerate(plan.sequence):
        if 'closeTimers' in step and i != len(plan.sequence) - 1:
            raise InvalidTestError('closeTimers должен завершать sequence')
    if plan.check.get('probeMutations'):
        raise InvalidTestError('timers не поддерживает probeMutations')
    if any(e['name'] in {'ТестТаймеры', 'ТестЧасы'} for e in plan.model['elements'] + plan.model['modules']):
        raise UnsupportedSyntaxError('Конфликт зарезервированного имени сессии таймеров')
    plan.timers = {'schemaVersion': 1, 'maxEvents': config.get('maxEvents', 1000), 'onClose': 'cancel',
                   'ordering': 'due-time-then-registration', 'reconnect': 'replace-from-now',
                   'periodic': 'fixed-rate', 'callbacks': [], 'sourceHash': plan.model['sourceHash'],
                   'execution': 'not-checked', 'limitations': ['Only own &Method / &этот.Method with ()->ничто signature',
                       'No dynamic callback values, lambdas or foreign object instances']}
    plan.clock['advancing'] = True


def bind_timer_call(plan, symbol, node, call, *, project, callback):
    if (call.kind in {'reference', 'computed'} or call.name not in OPERATIONS or project or callback
            or call.receiver not in {None, 'этот'}):
        return None
    bindings = method_local_bindings(symbol.source, node)
    if call.receiver is None and method_binding_visible(bindings, call.name, call.start):
        return None
    if not plan.timers:
        raise UnsupportedSyntaxError('Обработчик таймера требует явный контракт timers')
    if symbol.owner['sourceFile'] != plan.module['sourceFile']:
        raise UnsupportedSyntaxError('Таймер требует callback собственного целевого модуля')
    ast = next((n for n in node.expression_tree.walk() if n.kind == 'call'
                and n.children[0].start == call.start), None)
    args = ast.children[1:] if ast else []
    if len(args) not in ({2, 3} if call.name == 'ПодключитьОбработчикТаймера' else {1}):
        raise UnsupportedSyntaxError('Неподдержанная сигнатура ' + call.name)
    ref = args[0]
    if ref.kind != 'reference':
        raise UnsupportedSyntaxError('Таймер требует статическую ссылку &Метод или &этот.Метод')
    name = ref.value.removeprefix('этот.')
    if '.' in name or '::' in name or method_binding_visible(bindings, name, ref.start):
        raise UnsupportedSyntaxError('Callback таймера должен быть собственным методом')
    dest = next((s for s in plan.symbols if s.owner['sourceFile'] == symbol.owner['sourceFile']
                 and s.identity.declaration == name), None)
    if dest is None or dest.parameter_types or parse_module(dest.source)[0][0].return_type(dest.source) not in {None, 'ничто'}:
        raise UnsupportedSyntaxError('Callback таймера требует сигнатуру ()->ничто: ' + name)
    identity = asdict(dest.identity)
    key = 'callback-' + sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    if not any(c['id'] == key for c in plan.timers['callbacks']):
        plan.timers['callbacks'].append({'id': key, 'identity': identity, 'range': [dest.start, dest.end],
                                         'sourceHash': sha256(dest.source.encode()).hexdigest()})
    from .runtime import sbsl_literal
    spans = plan.source_transforms.setdefault((symbol.owner['sourceFile'], node.name), [])
    spans.extend([(call.start, ast.children[0].end, 'ТестТаймеры.' + call.name),
                  (ref.start, ref.end, sbsl_literal(key, 'Строка'))])
    plan.module_type_dependencies.setdefault(symbol.owner['sourceFile'], []).append('ТестТаймеры.Маркер')
    from .execution_plan import CapabilityBinding
    return CapabilityBinding('timer-events', call.name, key, 'script-session-queue',
                             'Declaration-resolved original callback; deterministic queue',
                             symbol.owner['sourceFile'], symbol.start + call.start, symbol.start + ast.end)


def generate_timer_types(plan, c):
    if plan.timers:
        from .form_context import add_structure
        add_structure(c, 'ТестТаймеры.Маркер', [])


def render_timers(plan, sandbox):
    if not plan.timers:
        return ''
    path = sandbox / 'ТестТаймеры.sbsl'
    path.write_text('#требуется ТестЧасы.sbsl\n' + TEMPLATE.read_text(encoding='utf-8'), encoding='utf-8')
    return '    ТестТаймеры.Инициализировать(' + str(plan.timers['maxEvents']) + ')\n'


def observation():
    return 'ТестТаймеры.Наблюдение()'


def render_event_step(plan, step, index, observed, is_object):
    """Drive original callbacks in Script; serialize state at each event boundary."""
    from .runtime import sbsl_literal
    suffix = str(index)
    events = 'СобытияТаймера' + suffix
    event = 'СобытиеТаймера' + suffix
    code = '    знч ' + events + ' = новый Массив<Строка>()\n'
    if 'closeTimers' in step:
        return code + '    ТестТаймеры.Закрыть()\n', events
    single = 'timerEvent' in step
    limit = 'ГраницаТаймера' + suffix
    code += ('    знч ' + limit + ' = ТестТаймеры.ТекущееВремя() + ' + str(step.get('advanceTime', 0)) + '\n'
             + '    пока Истина\n        знч ' + event + ' = ТестТаймеры.Следующее(' + limit + ', '
             + ('Истина' if single else 'Ложь') + ')\n'
             + '        если ' + event + ' == Неопределено\n            прервать\n        ;\n')
    for cb in plan.timers['callbacks']:
        name = cb['identity']['declaration']
        code += ('        если ' + event + '.Обработчик == ' + sbsl_literal(cb['id'], 'Строка') + '\n'
                 + '            ' + ('Контекст.' if is_object else '') + name + '()\n        ;\n')
    code += ('        ' + events + '.Добавить(СериализацияJson.ЗаписатьОбъект({"callback": ' + event
             + '.Обработчик, "elapsedMs": ' + event + '.Срок, "state": ' + observed
             + ', "clock": ТестЧасы.Снимок()}))\n')
    if single:
        code += '        прервать\n'
    code += '    ;\n'
    if not single:
        code += '    ТестТаймеры.Продвинуть(' + limit + ')\n'
    return code, events


TEMPLATE = Path(__file__).with_name('timer_queue.sbsl')
