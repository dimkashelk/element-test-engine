"""Declaration-driven object-form state. No UI rendering or business evaluation."""
from hashlib import sha256
import re
from .indexer import IDENT, parse_module, method_local_bindings, method_binding_visible
from .yaml_io import InvalidTestError, UnsupportedSyntaxError


def tree_values(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from tree_values(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from tree_values(child, path + (str(index),))


def expression_values(value, path=()):
    if isinstance(value, str) and value.startswith('='):
        yield {'expression': value, 'yamlPath': list(path), 'status': 'NOT_CHECKED'}
    elif isinstance(value, dict):
        for key, child in value.items():
            yield from expression_values(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from expression_values(child, path + (str(index),))


def form_declaration(model, module):
    candidates = [e for e in model['elements'] if e['name'] == module['name']
                  and e['namespace'] == module['namespace'] and e['elementType'] == 'КомпонентИнтерфейса']
    return candidates[0] if len(candidates) == 1 else None


def describe_form(element):
    props = element['properties']
    base = props.get('Наследует', {})
    match = re.fullmatch(r'ФормаОбъекта<(.+\.Объект)>', base.get('Тип', ''))
    if not match and base.get('Тип') != 'Форма':
        raise UnsupportedSyntaxError('Контекст формы требует Наследует.Тип: Форма или ФормаОбъекта<T.Объект>')
    fields = props.get('Свойства', [])
    if not isinstance(fields, list):
        raise UnsupportedSyntaxError('Свойства формы должны быть списком')
    names = {'Объект', 'Компоненты'}
    for f in fields:
        if (not isinstance(f, dict) or not re.fullmatch(IDENT, str(f.get('Имя', '')))
                or f['Имя'] in names or not isinstance(f.get('Тип'), str)):
            raise UnsupportedSyntaxError('Некорректное или повторное свойство формы')
        names.add(f['Имя'])
    components, handlers = {}, []
    for path, item in tree_values(base):
        if 'Имя' in item and 'Тип' in item:
            name = item['Имя']
            if not isinstance(name, str) or not re.fullmatch(IDENT, name) or name in components:
                raise UnsupportedSyntaxError('Некорректное или повторное имя компонента: ' + str(name))
            components[name] = {'name': name, 'type': item['Тип'], 'yamlPath': list(path),
                                'properties': {'Значение': 'Строка'} if item['Тип'] == 'Надпись' else
                                              {'Значение': 'Дата'} if item['Тип'] == 'ПолеВвода<Дата>' else {},
                                'declaration': item}
        for key, value in item.items():
            if key == 'Обработчик' or key == 'ПриНажатии' and item.get('Тип') == 'Кнопка':
                handlers.append({'name': value, 'type': item.get('Тип'), 'yamlPath': list(path) + [key]})
    return {'identity': {k: element.get(k) for k in ('name','namespace','sourceFile')},
            'objectType': match[1] if match else None, 'fields': fields, 'components': components,
            'handlers': handlers, 'expressions': list(expression_values(base)),
            'executorLocale': 'en-US',
            'limitations': ['No UI/layout/events or YAML expression evaluation', 'No form write/open effects',
                            'Only Надпись.Значение: Строка; passive command arguments']}


def prepare_form(plan):
    if 'context' not in plan.check:
        return
    element = form_declaration(plan.model, plan.module)
    if not element:
        return
    plan.form = describe_form(element)
    if 'formEffects' in plan.check:
        plan.form['limitations'] = [v for v in plan.form['limitations'] if v != 'No form write/open effects']
        plan.form['limitations'].append('Only explicitly allowed formEffects; synchronous storage adapter and detached open requests')
    lifecycle = plan.check.get('lifecycle')
    if plan.form['objectType'] and (not isinstance(lifecycle, dict) or set(lifecycle) != {'isNew'} or not isinstance(lifecycle['isNew'], bool)):
        raise InvalidTestError('Контекст формы требует lifecycle: {isNew: Булево}')
    if not plan.form['objectType'] and 'lifecycle' in plan.check:
        raise InvalidTestError('Обычная Форма не принимает lifecycle')
    for handler in plan.form['handlers']:
        if handler['type'] == 'Кнопка':
            methods = [n for n in parse_module(plan.original)[0] if n.name == handler['name']]
            if len(methods) != 1 or [p.partition(':')[2].strip() for p in methods[0].parameters(plan.original)] != ['Кнопка', 'СобытиеПриНажатии'] or methods[0].return_span:
                raise UnsupportedSyntaxError('ПриНажатии требует собственный метод (Кнопка, СобытиеПриНажатии) без результата')
            handler['status'] = 'SIGNATURE_CHECKED_NO_UI_EVENT'
    if ('storage' in plan.check or 'mocks' in plan.check) and 'formEffects' not in plan.check:
        raise InvalidTestError('Контекст формы не поддерживает storage/mocks')


def member_path(node):
    if node.kind == 'name':
        return [node.value]
    if node.kind == 'member' and node.value == '.':
        head = member_path(node.children[0])
        return head + [node.children[1].value] if head else None
    return None


def rooted_path(path, bindings, offset, fields):
    if not path:
        return None
    if path[0] == 'этот':
        return path[1:]
    if path[0] in fields and not method_binding_visible(bindings, path[0], offset):
        return path
    return None


def passive_argument_names(source, node):
    return {p.partition(':')[0].strip() for p in node.parameters(source)
            if p.partition(':')[2].strip() in {'ОбычнаяКоманда', 'Кнопка', 'СобытиеПриНажатии'}
            or re.fullmatch(r'КомандаСПараметром<Массив<.+>>', p.partition(':')[2].strip())}


def bind_form_call(plan, symbol, node, call):
    """Return a binding only for a proved system receiver, respecting locals."""
    form = plan.form if symbol.owner['sourceFile'] == plan.module['sourceFile'] else next(
        (f for f in plan.open_forms.values() if f['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl' == symbol.owner['sourceFile']), None)
    if not form:
        return None
    fields = {'Объект', 'Компоненты'} | {f['Имя'] for f in form['fields']}
    bindings = method_local_bindings(symbol.source, node)
    path = rooted_path((call.receiver or '').split('.') if call.receiver else None,
                       bindings, call.receiver_start or call.start, fields)
    command_names = passive_argument_names(symbol.source, node)
    if call.receiver and call.receiver.split('.')[0] in command_names:
        raise UnsupportedSyntaxError('API пассивного аргумента команды недоступен: ' + call.receiver + '.' + call.name)
    if call.receiver in {None, 'этот'} and any(s.identity.declaration == call.name and s.owner['sourceFile'] == symbol.owner['sourceFile'] for s in plan.symbols):
        return None
    if call.name in {'Записать', 'Открыть'}:
        from .form_effects import bind_effect
        effect = bind_effect(plan, symbol, node, call, path, bindings)
        if effect:
            return effect
    if path == ['Объект']:
        if call.name != 'ЭтоНовый':
            raise UnsupportedSyntaxError('Операция объекта формы вне контракта №34: ' + call.name)
        from .generated_types import ProjectTypes
        resolver = ProjectTypes(plan.model, symbol.owner['namespace'], symbol.owner.get('imports', []))
        owners = resolver.resolve(form['objectType'].removesuffix('.Объект'))
        if len(owners) != 1:
            raise UnsupportedSyntaxError('Тип объекта формы отсутствует или неоднозначен')
        owner = owners[0]
        if any(m['name'] == owner['name'] + '.Объект' and m['namespace'] == owner['namespace']
               and any(v['name'] == 'ЭтоНовый' for v in m['methods'])
               for m in plan.model['modules']):
            raise UnsupportedSyntaxError('Проектный ЭтоНовый требует исходную декларацию; системная подмена запрещена')
        from .execution_plan import CapabilityBinding
        return CapabilityBinding('form-system', call.name, form['objectType'], 'form-session-lifecycle',
                                 'Explicit object lifecycle, independent of business fields', symbol.identity.source_file,
                                 symbol.start + call.start, symbol.start + call.end)
    return None


def add_structure(c, name, fields):
    if name in c.definitions:
        raise UnsupportedSyntaxError('Конфликт технического типа формы: ' + name)
    canonical = []
    for f in fields:
        c.require(f['Тип'])
        canonical.append({**f, 'Тип': c.canonical_type(f['Тип'])})
    c.fields[name] = canonical
    def default(f):
        value = c.literal(f['ЗначениеПоУмолчанию'], f['Тип'])
        if f['Тип'] in {'Дата', 'Время'}:
            value = f['Тип'] + '{' + f['ЗначениеПоУмолчанию'] + '}'
        return value
    c.definitions[name] = '@Глобально\nструктура ' + name.split('.')[1] + '\n' + ''.join(
        '    пер ' + f['Имя'] + ': ' + c.sbsl_type(f['Тип']) +
        (' = ' + default(f) if 'ЗначениеПоУмолчанию' in f else '') + '\n'
        for f in canonical) + ';\n'


def generate_form_types(plan, c, *, form=None):
    form = form or plan.form
    if not form:
        return
    c.namespace = form['identity']['namespace']
    c.imports = next((m.get('imports', []) for m in plan.model['modules'] if m['sourceFile'] == form['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl'), [])
    alias = 'ТестФорма' + sha256(str(form['identity']).encode()).hexdigest()[:16]
    # Use a separate technical module per form; canonical metadata owners keep their identity.
    label = alias + '.Надпись'
    add_structure(c, label, [{'Имя':'Значение','Тип':'Строка'}])
    components = alias + '.Компоненты'
    available = []
    for item in form['components'].values():
        if item['properties']:
            value_type = item['properties']['Значение']
            d = item['declaration'].get('Значение', '' if value_type == 'Строка' else '0001-01-01')
            # A YAML expression is inventoried, not evaluated by the state adapter.
            if isinstance(d, str) and d.startswith('='):
                d = '' if value_type == 'Строка' else '0001-01-01'
            typ = alias + '.К' + sha256(item['name'].encode()).hexdigest()[:12]
            add_structure(c, typ, [{'Имя':'Значение','Тип':value_type,'ЗначениеПоУмолчанию':d}])
            available.append({'Имя':item['name'],'Тип':typ})
    add_structure(c, components, available)
    object_fields = []
    if form['objectType']:
        c.require(form['objectType'])
        obj = c.canonical_type(form['objectType'])
        form['objectCanonical'] = obj
        if any(f['Имя'] == 'ТестСостояниеНовизны' for f in c.fields[obj]):
            raise UnsupportedSyntaxError('Конфликт технического поля жизненного цикла')
        if 'ТестСостояниеНовизны' not in c.definitions[obj]:
            c.definitions[obj] = c.definitions[obj].rsplit(';',1)[0] + '    пер ТестСостояниеНовизны: Булево?\n;\n'
        if 'метод ЭтоНовый()' not in c.methods.get(obj, ''):
            c.attach_method(obj, 'метод ЭтоНовый(): Булево\n'
            '    если ТестСостояниеНовизны == Неопределено\n'
            '        выбросить новый ИсключениеНеподдерживаемаяОперация("Не объявлено состояние объекта")\n    ;\n'
            '    возврат ТестСостояниеНовизны как Булево\n;\n', [])
        object_fields = [{'Имя':'Объект','Тип':obj}]
    form_type = alias + '.Экземпляр'
    add_structure(c, form_type, object_fields + form['fields'] + [{'Имя':'Компоненты','Тип':components}])
    form['canonical'] = form_type
    if form is plan.form:
        c.literal(plan.check['context'], form_type)  # Validate before writing artifacts.
    for symbol in plan.symbols:
        if symbol.owner['sourceFile'] != form['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl':
            continue
        node = parse_module(symbol.source)[0][0]
        bindings = method_local_bindings(symbol.source, node)
        roots = {f['Имя'] for f in c.fields[form_type]}
        command_names = passive_argument_names(symbol.source, node)
        def visit(n, parent=None):
            if n.kind == 'assignment':
                bound = rooted_path(member_path(n.children[0]), bindings, n.children[0].start, roots)
                if bound == ['Объект']:
                    raise UnsupportedSyntaxError('Смена объекта формы требует отдельный контракт жизненного цикла')
            path = member_path(n)
            if path and not (parent and parent.kind == 'member'):
                if path[0] in command_names and len(path)>1:
                    raise UnsupportedSyntaxError('API пассивного аргумента команды недоступен')
                bound = rooted_path(path, bindings, n.start, roots)
                if bound is not None:
                    # A call method is validated in bind_form_call/registry, not a field.
                    if parent and parent.kind == 'call' and parent.children[0] is n:
                        bound = bound[:-1]
                    validate_path(c, form_type, bound)
            for child in n.children:
                visit(child, n)
        visit(node.expression_tree)
    observe = plan.check.get('observe') if form is plan.form else plan.check['formEffects']['lifecycle'].get('::'.join(filter(None,(form['identity']['namespace'],form['identity']['name']))),{}).get('observe')
    if observe is not None:
        if not isinstance(observe,list) or not observe or any(not isinstance(p,str) for p in observe) or len(set(observe))!=len(observe):
            raise InvalidTestError('observe формы требует непустой список уникальных путей')
        for path in observe:
            validate_path(c, form_type, path.split('.'), teacher=True)
    if form is not plan.form:
        return
    form['argumentBindings'] = []
    calls = plan.sequence or [{'method': plan.entry.identity.declaration, 'args': plan.check.get('args', [])}]
    for step in calls:
        symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == plan.module['sourceFile']
                      and s.identity.declaration == step['method'])
        for index, (value, typ) in enumerate(zip(step['args'], symbol.parameter_types)):
            expression = form_argument(plan, value, typ, contracts=c)
            if expression is not None:
                form['argumentBindings'].append({'method': step['method'], 'index': index,
                                                'path': value['contextPath'], 'type': c.sbsl_type(typ)})


def validate_path(c, typ, path, teacher=False):
    for name in path:
        known = {f['Имя']: f['Тип'] for f in c.fields.get(typ, [])}
        if name not in known:
            error = InvalidTestError if teacher else UnsupportedSyntaxError
            raise error('Неизвестный или неподдержанный путь формы: ' + '.'.join(path) + ' (' + name + ')')
        typ = known[name]
    return typ


def form_argument(plan, value, typ, *, contracts=None):
    """A declared path can pass the actual session array, with no expression eval."""
    if not plan.form or not isinstance(value, dict) or set(value) != {'contextPath'}:
        return None
    path = value['contextPath']
    if not isinstance(path, str) or not re.fullmatch(IDENT + r'(?:\.' + IDENT + r')*', path):
        raise InvalidTestError('contextPath требует объявленный путь без выражений')
    c = contracts or plan.contracts
    actual_type = validate_path(c, plan.form['canonical'], path.split('.'), teacher=True)
    expected = c.sbsl_type(typ).replace(' ', '')
    actual = c.sbsl_type(actual_type).replace(' ', '')
    if expected != actual:
        raise InvalidTestError('Тип contextPath не совпадает с типом аргумента: ' + path)
    return 'Контекст.' + path


def form_observation(plan, *, form=None, paths=None, variable='Контекст'):
    from .runtime import sbsl_literal
    c, form = plan.contracts, form or plan.form
    def snapshot(expression, typ):
        # Metadata fields deliberately exclude the internal lifecycle bit.
        if typ in c.fields:
            return '{' + ', '.join(sbsl_literal(f['Имя'],'Строка') + ': ' + snapshot(expression+'.'+f['Имя'],f['Тип'])
                                   for f in c.fields[typ]) + '}'
        return expression
    paths = paths or plan.check.get('observe') or [f['Имя'] for f in c.fields[form['canonical']]]
    return '{' + ', '.join(sbsl_literal(p,'Строка') + ': ' + snapshot(variable+'.'+p,
                            validate_path(c,form['canonical'],p.split('.'),teacher=True)) for p in paths) + '}'
