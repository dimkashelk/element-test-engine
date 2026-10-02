"""Declaration-driven read-only constants and a coherent Docker clock snapshot."""
from hashlib import sha256
import re
from .generated_types import ProjectTypes
from .indexer import IDENT, method_binding_visible, method_call_expressions, method_local_bindings, parse_module
from .resolution import qualified
from .yaml_io import InvalidTestError, UnsupportedSyntaxError

DEFAULTS = {'Строка': '', 'Булево': False, 'Число': 0, 'Дата': '0001-01-01', 'Время': '00:00:00'}


def prepare_session(plan):
    c = ProjectTypes(plan.model, plan.module['namespace'], plan.module.get('imports', []))
    fixture = plan.check.get('constants', {})
    if not isinstance(fixture, dict):
        raise InvalidTestError('constants требует словарь owner → значения')
    for name, values in fixture.items():
        if not isinstance(name, str) or not name:
            raise InvalidTestError('constants owner требует непустое имя')
        matches = c.resolve(name)
        if len(matches) != 1 or matches[0]['elementType'] != 'НаборКонстант':
            raise InvalidTestError('Owner constants отсутствует или неоднозначен: ' + str(name))
        add_constants(plan, matches[0], values)
    for key in ('snapshotConstants', 'snapshotArgs'):
        if not isinstance(plan.check.get(key, False), bool):
            raise InvalidTestError(key + ' требует Булево')
    locale = plan.check.get('executorLocale', 'en-US' if plan.form else None)
    if locale not in {None, 'en-US', 'ru-RU'}:
        raise InvalidTestError('executorLocale поддерживает en-US или ru-RU')
    plan.executor_locale = locale
    oracle = plan.check.get('clockOracle')
    if oracle is not None:
        days = {'Понедельник','Вторник','Среда','Четверг','Пятница','Суббота','Воскресенье'}
        if (not isinstance(oracle, dict) or set(oracle) != {'greetings','workWeek','timePrefix'}
                or not isinstance(oracle['greetings'], list) or len(oracle['greetings']) != 4
                or any(not isinstance(s, str) for s in oracle['greetings'])
                or not isinstance(oracle['timePrefix'], str) or not isinstance(oracle['workWeek'], dict)
                or set(oracle['workWeek']) != days or any(not isinstance(v, bool) for v in oracle['workWeek'].values())
                or 'clock' not in plan.check):
            raise InvalidTestError('clockOracle требует часы, четыре greetings, семь Булево workWeek и строку timePrefix')
    exceptions = plan.check.get('captureExceptionTypes', [])
    if exceptions not in ([], ['ИсключениеНедопустимыйАргумент']) or exceptions and not plan.check.get('captureException'):
        raise InvalidTestError('captureExceptionTypes: разрешён только ИсключениеНедопустимыйАргумент с captureException')
    if plan.form:
        plan.form['executorLocale'] = locale
    if 'clock' not in plan.check:
        return
    clock = plan.check['clock']
    if not isinstance(clock, dict) or clock.get('mode') not in {'fixed', 'docker'}:
        raise InvalidTestError('clock требует mode: fixed или docker')
    keys = {'mode', 'timezone'} | ({'date', 'time'} if clock['mode'] == 'fixed' else set())
    if set(clock) != keys or clock['timezone'] not in {'UTC', 'Europe/Moscow'}:
        raise InvalidTestError('clock: неверные поля или timezone (UTC/Europe/Moscow)')
    if clock['mode'] == 'fixed':
        c.literal(clock['date'], 'Дата')
        c.literal(clock['time'], 'Время')
    plan.clock = {**clock, 'locale': locale, 'coherentSnapshot': True}


def add_constants(plan, element, values=None):
    owner = qualified(element)
    if owner in plan.constants:
        if values is not None:
            raise InvalidTestError('Повторный owner constants: ' + owner)
        return plan.constants[owner]
    fields = element['properties'].get('Константы')
    if not isinstance(fields, list) or not fields:
        raise UnsupportedSyntaxError('Константы должны быть непустым списком: ' + owner)
    names = set()
    prepared = {}
    for f in fields:
        if not isinstance(f, dict) or not re.fullmatch(IDENT, str(f.get('Имя', ''))) or f['Имя'] in names:
            raise UnsupportedSyntaxError('Некорректная или повторная константа: ' + owner)
        typ = f.get('Тип', '')
        if typ.rstrip('?') not in DEFAULTS:
            raise UnsupportedSyntaxError('Тип константы вне read-only контракта: ' + str(typ))
        names.add(f['Имя'])
        prepared[f['Имя']] = f.get('ЗначениеПоУмолчанию', None if typ.endswith('?') else DEFAULTS[typ])
    if values is not None:
        if not isinstance(values, dict) or set(values) - names:
            raise InvalidTestError('Неизвестные поля constants: ' + owner)
        prepared.update(values)
    alias = 'ТестКонстанты' + sha256(owner.encode()).hexdigest()[:16]
    result = {'owner': owner, 'sourceFile': element['sourceFile'], 'fields': fields,
              'values': prepared, 'alias': alias, 'type': alias + '.Данные'}
    plan.constants[owner] = result
    return result


def bind_session_call(plan, symbol, node, call, *, project):
    if project or not call.receiver:
        return None
    bindings = method_local_bindings(symbol.source, node)
    if method_binding_visible(bindings, call.receiver.split('.')[0], call.receiver_start or call.start):
        return None
    c = ProjectTypes(plan.model, symbol.owner['namespace'], symbol.owner.get('imports', []))
    matches = c.resolve(call.receiver)
    ast_call = next((n for n in node.expression_tree.walk() if n.kind == 'call'
                     and n.children[0].start == call.start and n.children[0].end <= call.end), None)
    from .execution_plan import CapabilityBinding
    if len(matches) > 1 and any(e['elementType'] == 'НаборКонстант' for e in matches):
        raise UnsupportedSyntaxError('Неоднозначный owner констант: ' + call.receiver)
    if len(matches) == 1 and matches[0]['elementType'] == 'НаборКонстант':
        if call.name != 'Получить':
            raise UnsupportedSyntaxError('Эффект набора констант вне read-only контракта: ' + call.name)
        if ast_call is None or len(ast_call.children) != 1:
            raise UnsupportedSyntaxError('Системное чтение констант требует Получить() без аргументов')
        state = add_constants(plan, matches[0])
        plan.source_transforms.setdefault((symbol.owner['sourceFile'], node.name), []).append(
            (call.receiver_start, call.receiver_end, state['alias']))
        plan.module_type_dependencies.setdefault(symbol.owner['sourceFile'], []).append(state['type'])
        return CapabilityBinding('constants', 'Получить', state['owner'], 'read-only-session',
                                 'Typed snapshot of declared YAML constants', symbol.owner['sourceFile'],
                                 symbol.start + call.start, symbol.start + call.end)
    if plan.clock and not matches and call.receiver in {'Дата', 'Время'} and call.name == 'Сейчас':
        if ast_call is None or len(ast_call.children) != 1:
            raise UnsupportedSyntaxError('clock перехватывает только Сейчас() без аргументов')
        plan.source_transforms.setdefault((symbol.owner['sourceFile'], node.name), []).append(
            (call.receiver_start, ast_call.end, 'ТестЧасы.' + call.receiver + '()'))
        plan.module_type_dependencies.setdefault(symbol.owner['sourceFile'], []).append('ТестЧасы.Метка')
        return CapabilityBinding('clock', 'Сейчас', call.receiver, 'docker-session-snapshot',
                                 'One date/time snapshot before all original calls', symbol.owner['sourceFile'],
                                 symbol.start + call.start, symbol.start + call.end)
    return None


def generate_session_types(plan, c):
    from .form_context import add_structure
    for state in plan.constants.values():
        add_structure(c, state['type'], state['fields'])
        c.literal(state['values'], state['type'])
    if plan.clock:
        add_structure(c, 'ТестЧасы.Метка', [])


def render_session(plan, sandbox):
    from .runtime import sbsl_literal
    c = plan.contracts
    for state in plan.constants.values():
        path = sandbox / (state['alias'] + '.sbsl')
        with path.open('a', encoding='utf-8') as f:
            f.write('\n@Глобально\nметод Получить(): ' + state['type'] + '\n    возврат '
                    + c.literal(state['values'], state['type']) + '\n;\n')
    setup, metadata = '', ''
    if plan.clock:
        clock = plan.clock
        instant = ('новый ДатаВремя(' + sbsl_literal(clock['date'] + 'T' + clock['time'], 'Строка') + ')'
                   if clock['mode'] == 'fixed' else 'ДатаВремя.Сейчас(новый ЧасовойПояс('
                   + sbsl_literal(clock['timezone'], 'Строка') + '))')
        path = sandbox / 'ТестЧасы.sbsl'
        path.write_text(path.read_text(encoding='utf-8') + '\n' + '''конст Путь = "/tmp/element-clock.json"
@Глобально
метод Инициализировать(Снимок: ДатаВремя)
    исп Поток = новый Файл(Путь).ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Поток, Снимок)
;
@Глобально
метод Снимок(): ДатаВремя
    исп Поток = новый Файл(Путь).ОткрытьПотокЧтения()
    возврат СериализацияJson.ПрочитатьОбъект<ДатаВремя>(Поток, Тип<ДатаВремя>)
;
@Глобально
метод Дата(): Дата
    знч С = Снимок()
    возврат новый Дата(С.Год, С.Месяц, С.День)
;
@Глобально
метод Время(): Время
    знч С = Снимок()
    возврат новый Время(С.Час, С.Минута, С.Секунда, С.Миллисекунда)
;
''', encoding='utf-8')
        setup = '    ТестЧасы.Инициализировать(' + instant + ')\n'
        metadata = ', "clock": {"date": ТестЧасы.Дата(), "time": ТестЧасы.Время(), "snapshot": ТестЧасы.Снимок(), "timezone": ' + sbsl_literal(clock['timezone'], 'Строка') + ', "locale": ' + sbsl_literal(plan.executor_locale or '', 'Строка') + ', "mode": ' + sbsl_literal(clock['mode'], 'Строка') + '}'
    return setup, metadata


def constants_observation(plan):
    from .runtime import sbsl_literal
    return '{' + ', '.join(sbsl_literal(s['owner'], 'Строка') + ': ' + s['alias'] + '.Получить()'
                           for s in plan.constants.values()) + '}' if plan.constants else 'новый Соответствие<Строка, Объект?>()'
