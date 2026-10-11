"""Declaration-driven UI state/actions. Original handlers execute in Script.

This is a bounded model, not browser event delivery or native rendering. Python
resolves declarations and types; it never evaluates bindings or handler results.
"""
from copy import deepcopy
from hashlib import sha256
import re

from .yaml_io import InvalidTestError, UnsupportedSyntaxError
from .form_context import (add_structure, describe_form, generate_form_types,
                           form_observation, member_path, tree_values, validate_path)
from .declarative_bindings import (ExpressionCompiler, component_value_type, form_element,
    get_path, handler_binding, resolver_for, table_context, unique_component, validate_declaration)
from .form_requirements import validate_selector
from .expression_ast import parse_expression
from .resolution import qualified

STEPS = {'input', 'invoke', 'selectRows', 'tableCommand', 'snapshotInterface', 'recalculate', 'writeInterface'}
FLAGS = {'Видимость': True, 'Доступность': True, 'ТолькоЧтение': False}
TEXT = {'Заголовок', 'Представление', 'Описание'}


def interface_options_schema():
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', 'title': 'runtime.interface v1',
        'type': 'object', 'additionalProperties': False, 'required': ['schemaVersion', 'form'],
        'properties': {'schemaVersion': {'type': 'integer', 'enum': [1]}, 'form': {
            'type': 'object', 'additionalProperties': False, 'required': ['name'], 'properties': {
                'name': {'type': 'string', 'minLength': 1}, 'namespace': {'type': 'string'}}}}}


def component_properties(item):
    fields = {**{k: 'Булево' for k in FLAGS}, **{k: 'Строка' for k in TEXT}}
    typ = component_value_type(item)
    if typ:
        fields['Значение'] = 'Объект?' if item['Тип'] == 'Надпись' else typ
    if item['Тип'] == 'Группа':
        fields['Содержимое'] = 'Массив<Объект?>'
    return fields


def prepare_ui_aliases(plan, c):
    """Only opt-in UI types; project declarations always take precedence."""
    for typ, fields in {
        'АбсолютныйШрифт': [{'Имя': 'Размер', 'Тип': 'Число'}, {'Имя': 'Полужирный', 'Тип': 'Булево'}],
        'Надпись': [{'Имя': 'Значение', 'Тип': 'Объект?'}, {'Имя': 'Шрифт', 'Тип': 'АбсолютныйШрифт'},
                    {'Имя': 'ТипКомпонента', 'Тип': 'Строка', 'ЗначениеПоУмолчанию': 'Надпись'}],
        'СобытиеПриОткрытииПоСсылке': []}.items():
        if c.resolve(typ):
            raise UnsupportedSyntaxError('Конфликт проектного и системного UI типа: ' + typ)
        alias = 'ТестИнтерфейс' + sha256(typ.encode()).hexdigest()[:16] + '.Значение'
        c.platform_type_aliases[typ] = alias
        add_structure(c, alias, fields)


def generate_components(plan, c, form, alias):
    fields = []
    for item in form['components'].values():
        typ = alias + '.К' + sha256(item['name'].encode()).hexdigest()[:12]
        properties = []
        for name, field_type in item['properties'].items():
            f = {'Имя': name, 'Тип': field_type}
            value = item['declaration'].get(name, FLAGS.get(name, '' if field_type == 'Строка' else None))
            if value is not None and not (isinstance(value, str) and value.startswith('=')) and not isinstance(value, (dict, list)):
                # Dates/numbers without a declaration use native typed defaults.
                f['ЗначениеПоУмолчанию'] = value
            properties.append(f)
        properties += [{'Имя': 'Имя', 'Тип': 'Строка', 'ЗначениеПоУмолчанию': item['name']},
                       {'Имя': 'ТипКомпонента', 'Тип': 'Строка', 'ЗначениеПоУмолчанию': item['type']}]
        add_structure(c, typ, properties)
        item['canonical'] = typ
        fields.append({'Имя': item['name'], 'Тип': typ})
    add_structure(c, alias + '.Компоненты', fields)
    return alias + '.Компоненты'


def _step(step):
    if not isinstance(step, dict) or len(step) != 1 or not (set(step) & STEPS):
        return None
    op, value = next(iter(step.items()))
    if op in {'snapshotInterface', 'recalculate', 'writeInterface'}:
        if value is not True:
            raise InvalidTestError(op + ' требует true')
        return op, value
    allowed = {'input': {'selector', 'value', 'row'}, 'invoke': {'selector', 'rows'},
               'selectRows': {'selector', 'indices'}, 'tableCommand': {'selector', 'command', 'row', 'value'}}[op]
    required = {'input': {'selector', 'value'}, 'invoke': {'selector'},
                'selectRows': {'selector', 'indices'}, 'tableCommand': {'selector', 'command'}}[op]
    if not isinstance(value, dict) or not required <= value.keys() or set(value) - allowed:
        raise InvalidTestError(op + ': некорректные поля действия')
    validate_selector(value['selector'])
    if 'row' in value and (type(value['row']) is not int or value['row'] < 0):
        raise InvalidTestError('row требует неотрицательный индекс')
    if 'indices' in value and (not isinstance(value['indices'], list) or any(type(i) is not int or i < 0 for i in value['indices'])
            or len(set(value['indices'])) != len(value['indices'])):
        raise InvalidTestError('indices требует уникальные неотрицательные индексы')
    if 'rows' in value and (not isinstance(value['rows'], str) or value['rows'] not in {'selected', 'all'}):
        raise InvalidTestError('rows поддерживает selected/all')
    if op == 'tableCommand' and (not isinstance(value['command'], str) or value['command'] not in {'ДобавитьСтроку', 'Удалить', 'ПереместитьСтрокуВверх', 'ПереместитьСтрокуВниз'}):
        raise UnsupportedSyntaxError('Стандартная команда таблицы вне контракта: ' + str(value['command']))
    return op, value


def plan_interface(root, source_model, check):
    from .test_contracts import validate_schema
    from .model import select_check_project
    from .execution_plan import ExecutionPlan, SourceSymbol, SymbolIdentity, plan_execution
    from .session_contracts import prepare_session, generate_session_types, add_constants
    from .timer_events import prepare_timers, validate_step
    from .indexer import parse_module
    validate_schema(check['interface'], interface_options_schema(), 'interface')
    if set(check) & {'target', 'args', 'querySource', 'formRequirement', 'probeMutations', 'snapshotArgs', 'instanceProbes', 'captureCalls', 'captureException', 'observeFailure', 'clockOracle', 'snapshotConstants', 'runtimeDateTime', 'mocks'}:
        raise InvalidTestError('interface имеет собственный декларативный вход, без target/args/probes')
    if 'storage' in check and not isinstance(check['storage'], dict):
        raise InvalidTestError('interface.storage требует объект')
    if check.get('storage', {}).get('transaction'):
        raise UnsupportedSyntaxError('interface не реализует внешнюю storage.transaction; атомарность записи объекта использует formEffects, общий lifecycle — №50')
    root, model = select_check_project(root, source_model, check)
    e = form_element(model, check['interface']['form']); validate_declaration(e)
    c = resolver_for(model, e)
    sequence = check.get('sequence', [{'snapshotInterface': True}])
    if not isinstance(sequence, list) or not 1 <= len(sequence) <= 40:
        raise InvalidTestError('interface.sequence требует 1..40 действий')
    resolved = []
    for step in sequence:
        parsed = _step(step)
        if parsed:
            op, value = parsed
            if op == 'invoke':
                path, item = unique_component(e, value['selector'], c)
                bound = handler_binding(root, model, e, path, item, c)
                args = [{} for _ in bound['parameters']]
                # Preserve a source-root call for the shared planner, then lower
                # its actual argument from the selected table in Script.
                if len(args) == 2 and item.get('Тип', '').startswith('КомандаСПараметром<'):
                    args[1] = [] if bound['parameters'][1].startswith('Массив<') else {}
                resolved.append({'method': bound['method'], 'args': args, '_invoke': value, '_path': list(path)})
            else:
                resolved.append(deepcopy(step))
        elif validate_step(step, check):
            resolved.append(deepcopy(step))
        elif isinstance(step, dict) and set(step) == {'method', 'args'}:
            resolved.append(deepcopy(step))
        else:
            raise InvalidTestError('Неизвестное действие interface.sequence')
    calls = [s for s in resolved if 'method' in s]
    plain = {k: deepcopy(v) for k, v in check.items() if k not in {'interface', 'expected', 'sequence'}}
    plain.setdefault('context', {})
    if not isinstance(plain['context'], dict):
        raise InvalidTestError('interface.context требует объект')
    plain['_interface'] = True
    # Private invocation evidence is not a method-call option.
    plain['sequence'] = [{k: v for k, v in s.items() if not k.startswith('_')} for s in resolved]
    if calls:
        plain['target'] = {'module': e['name'], 'namespace': e['namespace'], 'method': calls[0]['method']}
        plain['args'] = calls[0]['args']
        plan = plan_execution(root, source_model, plain)
    else:
        if plain.get('formEffects') == {'write': True} and describe_form(e, extended=True).get('recordType'):
            pass
        elif plain.get('formEffects') == {'write': True} and describe_form(e, extended=True).get('objectType'):
            if not isinstance(plain.get('storage'), dict) or plain['storage'].get('idType') != 'Ууид':
                raise InvalidTestError('Запись объекта требует storage.idType: Ууид')
            object_owner = c.resolve(describe_form(e, extended=True)['objectType'].removesuffix('.Объект'))
            if len(object_owner) != 1:
                raise UnsupportedSyntaxError('Неоднозначный owner записи формы')
            hooks = [m for m in model['modules'] if m['name'] == object_owner[0]['name'] + '.Объект' and m['namespace'] == object_owner[0]['namespace']
                     and any(h['name'] in {'ПередЗаписью', 'ПослеЗаписи'} for h in m['methods'])]
            if hooks:
                raise UnsupportedSyntaxError('Декларативная запись с исходными lifecycle hooks требует исходный методный вход; расширение №50')
        elif 'formEffects' in plain:
            raise InvalidTestError('Без исходного обработчика поддержан только formEffects.write')
        path = e['sourceFile'].removesuffix('.yaml') + '.xbsl'
        module = next((m for m in model['modules'] if m['sourceFile'] == path),
                      {'name': e['name'], 'namespace': e['namespace'], 'sourceFile': path,
                       'imports': e['properties'].get('Импорт', []), 'moduleType': 'interface'})
        identity = SymbolIdentity(model['properties'].get('Поставщик', ''), model['properties'].get('Имя', ''),
                                  e['namespace'], e['name'], '', e['sourceFile'])
        plan = ExecutionPlan(root, model, plain, module, '', SourceSymbol(identity, module, '', [], 0, 0), [], {}, {}, {})
        from .form_context import prepare_form
        plan.sequence = plain["sequence"]
        prepare_form(plan); prepare_session(plan); prepare_timers(plan)
        c = resolver_for(model, e); prepare_ui_aliases(plan, c)
        generate_form_types(plan, c); generate_session_types(plan, c)
        plan.contracts = c
        if 'storage' in plain:
            from .storage import MetadataStorage
            plan.storage = MetadataStorage(c, plain['storage'], audit_history=True)
            if plain.get('formEffects') and plan.form.get('objectType'):
                from .form_effects import generate_effects
                generate_effects(plan, c)
                for owner in plan.storage_elements:
                    plan.storage.attach(owner)
    c = plan.contracts
    if plan.form.get('recordOwner'):
        state = add_constants(plan, plan.form['recordOwner'])
        plan.form['recordState'] = state['owner']
        if state['type'] not in c.fields:
            add_structure(c, state['type'], state['fields'])
    # Constants are shared, mutable, typed session records only for this opt-in.
    plan.interface = {'schemaVersion': 1, 'owner': qualified(e), 'sourceFile': e['sourceFile'],
        'sourceHash': sha256((root / e['sourceFile']).read_bytes()).hexdigest(), 'projectSourceHash': model['sourceHash'],
        'execution': 'not-checked', 'bindings': [], 'actions': [], 'remaining': [], 'tables': {}}
    _prepare_bindings(plan, e)
    import yaml
    from .capability_map import yaml_nodes
    positions = {tuple(p): {'range': [n.start_mark.index, n.end_mark.index], 'line': n.start_mark.line + 1, 'column': n.start_mark.column + 1}
                 for p, n in yaml_nodes(yaml.compose((root / e['sourceFile']).read_text(encoding='utf-8-sig')))}
    for binding in plan.interface['bindings']:
        binding['position'] = positions[tuple(binding['yamlPath'])]
        binding['sourceHash'] = plan.interface['sourceHash']
    prepared = []
    for index, step in enumerate(resolved):
        prepared.append(_lower_step(plan, e, step, index))
    plan._interface_steps = prepared
    plan.interface['driverActions'] = prepared
    plan.sequence = resolved
    plan._interface_element = e
    # Direct object writes use the existing storage/lifecycle adapter and source
    # handlers. Require explicit permission and prepare the same dependency closure.
    if any('writeInterface' in s for s in resolved):
        if not check.get('formEffects', {}).get('write'):
            raise InvalidTestError('writeInterface требует formEffects.write: true')
    return plan


def _prepare_bindings(plan, e):
    c, form = plan.contracts, plan.form
    bindings = []
    for path, item in tree_values(e['properties']['Наследует'], ('Наследует',)):
        if 'Тип' not in item:
            continue
        table = table_context(e, path, c)
        column = table and 'Колонки' in path[len(table[0]):]
        for prop, text in item.items():
            if prop not in set(FLAGS) | TEXT | {'Значение'} or not isinstance(text, str) or not text.startswith('='):
                continue
            compiler = ExpressionCompiler(c, form['canonical'], c.canonical_type(table[2]) if column else None)
            expr, typ = compiler.compile(text)
            declared = component_value_type(item) if prop == 'Значение' else 'Булево' if prop in FLAGS else 'Строка'
            if declared and item['Тип'] != 'Надпись' and c.canonical_type(declared) != c.canonical_type(typ):
                raise UnsupportedSyntaxError('Тип привязки не соответствует компоненту: ' + '/'.join(path))
            target = ('Контекст.Компоненты.' + item['Имя'] + '.' + prop) if item.get('Имя') in form['components'] and not column else None
            binding = {'sourceFile': e['sourceFile'], 'yamlPath': list(path + (prop,)), 'expression': text,
                       'compiled': expr, 'outputType': typ, 'symbols': compiler.symbols, 'target': target}
            if column:
                source, source_type = ExpressionCompiler(c, form['canonical']).compile(table[1]['Источник']['Данные'])
                if c.canonical_type(source_type) != 'Массив<' + c.canonical_type(table[2]) + '>':
                    raise UnsupportedSyntaxError('Несовместимый источник таблицы')
                binding['sourceCompiled'] = source
            bindings.append(binding)
        for prop, text in item.items():
            if isinstance(text, str) and text.startswith('=') and prop not in set(FLAGS) | TEXT | {'Значение'}:
                if prop == 'Данные' and 'Источник' in path:
                    continue
                plan.interface['remaining'].append({'yamlPath': list(path + (prop,)),
                    'reason': 'Command/navigation/report declaration; evaluated only by its explicit action contract'})
    # Evaluate component dependencies in topological order; cycles are explicit.
    pending = list(bindings); ordered = []
    while pending:
        targets = {b['target'] for b in pending if b['target']}
        ready = [b for b in pending if not any('Контекст.' + '.'.join(s['path'][1:] if s['path'][0] == 'этот' else s['path']) in targets for s in b['symbols'])]
        if not ready:
            raise UnsupportedSyntaxError('Циклические привязки интерфейса')
        ordered.extend(ready); pending = [b for b in pending if b not in ready]
    plan.interface['bindings'] = ordered


def _component(plan, e, selector):
    path, item = unique_component(e, selector, plan.contracts)
    return path, item, table_context(e, path, plan.contracts)


def _table(plan, e, path, item):
    c = plan.contracts
    table = table_context(e, path, c)
    if not table or tuple(path) != tuple(table[0]):
        raise UnsupportedSyntaxError('Действие требует таблицу с ИсточникДанныхМассив')
    key = '/'.join(path)
    if key not in plan.interface['tables']:
        text = get_path(item, ('Источник', 'Данные'))
        compiler = ExpressionCompiler(c, plan.form['canonical'])
        source, typ = compiler.compile(text)
        row = c.canonical_type(table[2])
        if c.canonical_type(typ) != 'Массив<' + row + '>':
            raise UnsupportedSyntaxError('Источник таблицы не соответствует полному типу строки')
        if not member_path(parse_expression(text[1:])):
            raise UnsupportedSyntaxError('Изменяемая таблица требует прямую привязку к массиву владельца')
        plan.interface['tables'][key] = {'source': source, 'rowType': row,
            'selected': 'ТестВыбор' + sha256(key.encode()).hexdigest()[:12], 'yamlPath': list(path)}
    return plan.interface['tables'][key]


def _gate(plan, e, path, editing=False):
    compiler = ExpressionCompiler(plan.contracts, plan.form['canonical'])
    conditions = []
    for i in range(1, len(path) + 1):
        item = get_path(e['properties'], path[:i])
        if not isinstance(item, dict):
            continue
        for prop, default in FLAGS.items():
            if prop == 'ТолькоЧтение' and not editing:
                continue
            text = item.get(prop, default)
            nearest = table_context(e, path[:i], plan.contracts)
            column_formula = (nearest and 'Колонки' in path[:i][len(nearest[0]):] and isinstance(text, str) and text.startswith('='))
            if item.get('Имя') in plan.form['components'] and not column_formula:
                expression = 'Контекст.Компоненты.' + item['Имя'] + '.' + prop
            elif isinstance(text, str) and text.startswith('='):
                expression, typ = compiler.compile(text)
                if typ != 'Булево':
                    raise UnsupportedSyntaxError('Флаг интерфейса требует Булево')
            else:
                if type(text) is not bool:
                    raise UnsupportedSyntaxError('Флаг интерфейса требует Булево')
                expression = 'Истина' if text else 'Ложь'
            conditions.append(('не (' + expression + ')') if prop == 'ТолькоЧтение' else '(' + expression + ')')
    return ' и '.join(conditions) or 'Истина'


def _lower_step(plan, e, step, index):
    c, form = plan.contracts, plan.form
    result = {'code': '', 'gate': 'Истина', 'step': deepcopy(step)}
    if 'method' in step:
        symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == plan.module['sourceFile'] and s.identity.declaration == step['method'])
        args = step['args']
        if '_invoke' in step:
            value = step['_invoke']; path, item, table = _component(plan, e, value['selector'])
            result['gate'] = _gate(plan, e, path)
            args = [c.literal({}, symbol.parameter_types[0])]
            if item['Тип'] == 'Кнопка':
                args.append(c.literal({}, symbol.parameter_types[1]))
            elif len(symbol.parameter_types) == 2:
                if not table:
                    raise UnsupportedSyntaxError('Параметр команды требует ближайшую таблицу')
                t = _table(plan, e, table[0], table[1]); rows = value.get('rows', 'selected')
                actual_type = c.canonical_type(symbol.parameter_types[1])
                if actual_type == t['rowType']:
                    if rows != 'selected':
                        raise InvalidTestError('Команда строки требует rows: selected')
                    result['code'] += '    если ' + t['selected'] + '.Размер() != 1\n        выбросить новый ИсключениеНеподдерживаемаяОперация("Команда строки требует одну выбранную строку")\n    ;\n' + _bounds(t['source'], t['selected'] + '[0]')
                    args.append(t['source'] + '[' + t['selected'] + '[0]]')
                    result['code'] += '    Контекст.' + step['method'] + '(' + ', '.join(args) + ')\n'
                    plan.interface['actions'].append({'operation': 'handler', 'method': step['method'], 'sourceFile': symbol.identity.source_file, 'range': [symbol.start, symbol.end], 'sourceHash': sha256(symbol.source.encode()).hexdigest()})
                    return result
                if actual_type != 'Массив<' + t['rowType'] + '>':
                    raise UnsupportedSyntaxError('Тип строк команды не соответствует owner таблицы')
                var = 'ТестСтроки' + str(index)
                result['code'] += '    знч ' + var + ' = новый Массив<' + c.sbsl_type(t['rowType']) + '>()\n'
                if rows == 'all':
                    result['code'] += '    ' + var + '.ДобавитьВсе(' + t['source'] + ')\n'
                else:
                    result['code'] += '    для Индекс из ' + t['selected'] + '\n' + _bounds(t['source'], 'Индекс', '        ') + '        ' + var + '.Добавить(' + t['source'] + '[Индекс])\n    ;\n'
                args.append(var)
            call_args = args
        else:
            from .form_context import form_argument
            call_args = [form_argument(plan, v, t) or c.literal(v, t) for v, t in zip(args, symbol.parameter_types)]
        result['code'] += '    Контекст.' + step['method'] + '(' + ', '.join(call_args) + ')\n'
        if parse_module_return(symbol) not in {None, 'ничто'}:
            raise InvalidTestError('interface actions вызывают void обработчики; возврат проверяется обычным runtime')
        evidence = {'operation': 'handler', 'method': step['method'], 'sourceFile': symbol.identity.source_file,
                    'range': [symbol.start, symbol.end], 'sourceHash': sha256(symbol.source.encode()).hexdigest()}
    else:
        parsed = _step(step)
        if not parsed:
            return result  # Timer action is driven by the common queue renderer.
        op, value = parsed
        evidence = {'operation': op}
        if op in {'input', 'selectRows', 'tableCommand'}:
            path, item, table = _component(plan, e, value['selector'])
            evidence['yamlPath'] = list(path)
            result['gate'] = _gate(plan, e, path, editing=op != 'selectRows')
            if op == 'input':
                typ = component_value_type(item)
                if not typ or item['Тип'] == 'Надпись':
                    raise UnsupportedSyntaxError('Ввод требует поле, флажок или колонку')
                text = item.get('Значение')
                compiler = ExpressionCompiler(c, form['canonical'], c.canonical_type(table[2]) if table else None)
                if isinstance(text, str) and text.startswith('='):
                    path_ast = member_path(parse_expression(text[1:]))
                    if not path_ast:
                        raise UnsupportedSyntaxError('Двусторонний ввод требует прямую привязку; вычисляемое выражение доступно для чтения')
                    expression, actual_type = compiler.compile(text)
                    if c.canonical_type(actual_type) != c.canonical_type(typ):
                        raise UnsupportedSyntaxError('Тип ввода не соответствует привязке')
                elif item.get('Имя') in form['components']:
                    expression = 'Контекст.Компоненты.' + item['Имя'] + '.Значение'
                else:
                    raise UnsupportedSyntaxError('Несвязанное поле требует именованный экземпляр')
                if expression.startswith('RowData.'):
                    if 'row' not in value:
                        raise InvalidTestError('Ввод в колонку требует row')
                    t = _table(plan, e, table[0], table[1]); row = str(value['row'])
                    result['code'] += _bounds(t['source'], row)
                    expression = t['source'] + '[' + row + ']' + expression[len('RowData'):]
                elif 'row' in value:
                    raise InvalidTestError('row допустим только для колонки')
                result['code'] += '    ' + expression + ' = ' + c.literal(value['value'], typ) + '\n'
                evidence['destination'] = expression
            else:
                t = _table(plan, e, path, item)
                if op == 'selectRows':
                    if not item.get('ИспользоватьОтметкиСтрок', False):
                        raise UnsupportedSyntaxError('Таблица не объявляет отметки строк')
                    for row in value['indices']:
                        result['code'] += _bounds(t['source'], str(row))
                    result['code'] += '    ' + t['selected'] + ' = ' + c.literal(value['indices'], 'Массив<Число>') + '\n'
                else:
                    result['code'] += _command(c, t, value, index)
        elif op == 'writeInterface':
            result['gate'] = _gate(plan, e, ('Наследует',), editing=True)
            if form.get('recordOwner'):
                owner = qualified(form['recordOwner']); state = plan.constants[owner]
                expr = 'новый ' + state['type'] + '(' + ', '.join(f['Имя'] + ' = Контекст.Запись.' + f['Имя'] for f in state['fields']) + ')'
                result['code'] = '    ' + state['alias'] + '.ТестЗаписать(' + expr + ')\n'
            elif form.get('objectType'):
                if not plan.storage or not plan.check.get('formEffects', {}).get('write'):
                    raise InvalidTestError('Запись объекта требует storage/formEffects.write')
                result['code'] = '    Контекст.ТестЗаписатьФорму()\n'
            else:
                raise UnsupportedSyntaxError('writeInterface требует форму объекта или констант')
    plan.interface['actions'].append(evidence)
    return result


def parse_module_return(symbol):
    from .indexer import parse_module
    return parse_module(symbol.source)[0][0].return_type(symbol.source)


def _bounds(source, row, indent='    '):
    return indent + 'если ' + row + ' < 0 или ' + row + ' >= ' + source + '.Размер()\n' + indent + '    выбросить новый ИсключениеНеподдерживаемаяОперация("Индекс UI строки вне текущего массива")\n' + indent + ';\n'


def _command(c, t, value, index):
    source, selected, op = t['source'], t['selected'], value['command']
    if op == 'ДобавитьСтроку':
        if 'row' in value:
            raise InvalidTestError('ДобавитьСтроку не принимает row')
        return '    ' + source + '.Добавить(' + c.literal(value.get('value', {}), t['rowType']) + ')\n'
    if 'value' in value:
        raise InvalidTestError('value допустим только для ДобавитьСтроку')
    row = str(value['row']) if 'row' in value else None
    if row is None:
        if op != 'Удалить':
            raise InvalidTestError('Перемещение требует row')
        # Remove selected indices descending, retaining distinct equal rows.
        return ('    для Индекс из ' + selected + '\n' + _bounds(source, 'Индекс', '        ') + '    ;\n'
                '    пер Удаляемый' + str(index) + ' = ' + source + '.Размер() - 1\n'
                '    пока Удаляемый' + str(index) + ' >= 0\n'
                '        если ' + selected + '.Содержит(Удаляемый' + str(index) + ')\n'
                '            ' + source + '.УдалитьПоИндексу(Удаляемый' + str(index) + ')\n        ;\n'
                '        Удаляемый' + str(index) + ' -= 1\n    ;\n    ' + selected + '.Очистить()\n')
    code = _bounds(source, row)
    if op == 'Удалить':
        return code + '    ' + source + '.УдалитьПоИндексу(' + row + ')\n    ' + selected + '.Очистить()\n'
    dest = str(value['row'] + (-1 if op.endswith('Вверх') else 1))
    var = 'ТестПеремещаемая' + str(index)
    return code + '    если ' + dest + ' >= 0 и ' + dest + ' < ' + source + '.Размер()\n        знч ' + var + ' = ' + source + '[' + row + ']\n        ' + source + '.УдалитьПоИндексу(' + row + ')\n        ' + source + '.Вставить(' + dest + ', ' + var + ')\n    ;\n    ' + selected + '.Очистить()\n'


def _recalculate(plan, indent='    '):
    return ''.join(indent + b['target'] + ' = ' + b['compiled'] + '\n' for b in plan.interface['bindings'] if b['target'])


def _observation(plan, index):
    c = plan.contracts
    code, pairs = '', []
    from .declarative_bindings import observation_value
    for n, b in enumerate(plan.interface['bindings']):
        key = '/'.join(b['yamlPath'])
        expr = b['compiled']
        if 'sourceCompiled' in b:
            var = 'ТестКолонка' + str(index) + '_' + str(n)
            code += '    знч ' + var + ' = новый Массив<Объект?>()\n    для RowData из ' + b['sourceCompiled'] + '\n        ' + var + '.Добавить(' + observation_value(c, expr, b['outputType']) + ')\n    ;\n'
            expr = var
        else:
            expr = observation_value(c, expr, b['outputType'])
        pairs.append(c.literal(key, 'Строка') + ': ' + expr)
    bindings = '{' + ', '.join(pairs) + '}' if pairs else 'новый Соответствие<Строка, Объект?>()'
    selections = '{' + ', '.join(c.literal(key, 'Строка') + ': ' + t['selected'] for key, t in plan.interface['tables'].items()) + '}' if plan.interface['tables'] else 'новый Соответствие<Строка, Объект?>()'
    actual = '{"state": ' + form_observation(plan) + ', "bindings": ' + bindings + ', "selection": ' + selections
    if plan.constants:
        from .session_contracts import constants_observation
        actual += ', "constants": ' + constants_observation(plan)
    if 'formEffects' in plan.check and not plan.form.get('recordType'):
        actual += ', "openings": ТестЗапросыФорм.Читать()'
    if plan.storage:
        actual += ', "storage": ТестСессия.Аудит()'
    if plan.form.get('objectType'):
        actual += ', "isNew": Контекст.Объект.ЭтоНовый()'
    return code, actual + '}'


def render_interface(plan, sandbox):
    from .session_contracts import render_session
    from .renderer import render_plan
    from .timer_events import render_event_step, observation as timer_observation
    c, form = plan.contracts, plan.form
    # Shared renderer attaches only original source methods and prepares storage.
    if plan.symbols:
        # Its temporary driver must contain only method/timer calls. The final
        # driver below owns all UI actions; the original source is unchanged.
        old_sequence = plan.sequence
        plan.sequence = [s for s in old_sequence if 'method' in s]
        try:
            script = render_plan(plan, sandbox)
        finally:
            plan.sequence = old_sequence
        imports = script.read_text().split('\nметод Скрипт(', 1)[0]
        session_setup, metadata = plan.rendered_session
    else:
        imports = c.write(sandbox) + '\n'
        session_setup, metadata = render_session(plan, sandbox)
        plan.rendered_session = session_setup, metadata
    setup = session_setup
    if plan.storage:
        setup += plan.storage.setup()
    setup += '    знч Контекст = ' + c.literal(plan.check['context'], form['canonical']) + '\n'
    if form.get('objectType'):
        setup += '    Контекст.Объект.ТестСостояниеНовизны = ' + c.literal(plan.check['lifecycle']['isNew'], 'Булево') + '\n'
        if 'formEffects' in plan.check and 'Ссылка' not in plan.check['context'].get('Объект', {}):
            setup += '    Контекст.Объект.Ссылка.Идентификатор = Ууид.Случайный()\n'
    if form.get('recordOwner') and 'Запись' not in plan.check['context']:
        state = plan.constants[form['recordState']]
        setup += ''.join('    Контекст.Запись.' + f['Имя'] + ' = ' + c.literal(state['values'][f['Имя']], f['Тип']) + '\n' for f in state['fields'])
    # Initialize named group contents with the same live instances exposed by
    # Компоненты. Dynamic insertions operate on that actual Script array.
    for item in form['components'].values():
        if 'Содержимое' not in item['properties']:
            continue
        parent = tuple(item['yamlPath'])
        for child in form['components'].values():
            path = tuple(child['yamlPath'])
            if len(path) == len(parent) + 2 and path[:len(parent)] == parent and path[len(parent)] == 'Содержимое':
                setup += '    Контекст.Компоненты.' + item['name'] + '.Содержимое.Добавить(Контекст.Компоненты.' + child['name'] + ')\n'
    for t in plan.interface['tables'].values():
        setup += '    пер ' + t['selected'] + ' = новый Массив<Число>()\n'
    if plan.storage and plan.storage.config.get('backend') == 'postgres':
        setup = '    ТестСессия.НастроитьSql(Путь)\n' + setup + '    ТестСессия.Опубликовать(Путь, Истина)\n'
    setup += _recalculate(plan)
    invocation = '    знч Действия = новый Массив<Строка>()\n'
    for index, prepared in enumerate(plan._interface_steps):
        step = prepared['step']; op = next(iter(step))
        invocation += '    пер ТестИсход' + str(index) + ' = "applied"\n'
        if op in {'advanceTime', 'timerEvent', 'closeTimers'}:
            event_code, events = render_event_step(plan, step, index, form_observation(plan), True)
            # Recompute live bindings before capturing each callback state.
            event_code = event_code.replace('        ' + events + '.Добавить(', _recalculate(plan, '        ') + '        ' + events + '.Добавить(')
            invocation += event_code
        else:
            invocation += '    если ' + prepared['gate'] + '\n'
            invocation += ''.join('    ' + line + '\n' for line in prepared['code'].splitlines())
            invocation += '    иначе\n        ТестИсход' + str(index) + ' = "blocked"\n    ;\n'
        invocation += _recalculate(plan)
        code, actual = _observation(plan, index)
        invocation += code
        actual = '{"outcome": ТестИсход' + str(index) + ', "observation": ' + actual
        if plan.timers:
            actual += ', "timers": ' + timer_observation() + ', "clock": ТестЧасы.Снимок()'
            if op in {'advanceTime', 'timerEvent', 'closeTimers'}:
                actual += ', "events": ' + events
        invocation += '    Действия.Добавить(СериализацияJson.ЗаписатьОбъект(' + actual + '}))\n'
    if plan.timers:
        invocation += '    ТестТаймеры.Закрыть()\n'
    actual = '{"actions": Действия}'
    if plan.storage:
        invocation += '    ТестСессия.ПроверитьСессию()\n'
        if plan.storage.config.get('backend') == 'postgres':
            invocation += '    ТестСессия.Опубликовать(Путь)\n'
        actual = '{"result": ' + actual + ', "storage": ТестСессия.Аудит(Истина)}'
        metadata += ', "_rawStorage": Истина, "storageTrace": ТестСессия.Трасса()'
    identities = {'Scripts::' + typ: qualified(c.canonical_elements[typ.split('.')[0]]) + '.' + typ.partition('.')[2]
                  for typ in c.fields if '.' in typ and typ.split('.')[0] in c.canonical_elements}
    metadata += ', "_snapshotMode": "sequence", "_typeIdentities": ' + c.literal(identities, 'Соответствие<Строка, Строка>')
    statements = setup + invocation + '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": ' + actual + metadata + '}))\n'
    body = '    попытка\n' + ''.join('    ' + line + '\n' for line in statements.splitlines()) + '    поймать Недоступно: ИсключениеНеподдерживаемаяОперация\n        Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "UNSUPPORTED", "message": Недоступно.Описание}))\n    ;\n'
    if plan.timers:
        body = body.replace('        Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "UNSUPPORTED"', '        ТестТаймеры.Закрыть()\n        Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "UNSUPPORTED"')
        body = body.rsplit('    ;\n', 1)[0] + '    вконце\n        ТестТаймеры.Закрыть()\n    ;\n'
    script = sandbox / 'test.sbsl'
    signature = 'Путь: Строка' if plan.storage and plan.storage.config.get('backend') == 'postgres' else ''
    script.write_text(imports + '\nметод Скрипт(' + signature + ')\n' + body + ';\n')
    return script
