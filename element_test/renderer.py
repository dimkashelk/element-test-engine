"""Render validated source slices; execution order remains in original XBSL."""
import re
from .runtime import (extract_method, constructor_types, body_type_references,
                      _project_body_type, sbsl_literal, RUNTIME_CONSTRUCTORS)
from .indexer import IDENT, parse_module, mask_noncode, call_code
from .generated_types import ProjectTypes
from .local_structures import scalar_structures
from .platform_mocks import PlatformMocks
from .yaml_io import InputError, InvalidTestError


def global_body(source):
    # Project visibility is replaced by the generated Script module boundary.
    return '@Глобально\n' + re.sub(r'^\s*@(ВПроекте|ВПодсистеме|Глобально)\s*\n', '', source, flags=re.M)

def render_plan(plan, sandbox):
    """Copy the original method and generate only its required data contracts."""
    root, model, check = plan.root, plan.model, plan.check
    module, original = plan.module, plan.original
    target = check["target"]
    context = check.get("context")
    is_object = "context" in check
    method, types = plan.entry.source, plan.entry.parameter_types
    args = check.get("args", [])
    runtime_model = plan.model
    contracts = plan.contracts
    storage, platform = plan.storage, plan.platform
    mocks = check.get('mocks', {})
    if not isinstance(check.get('captureException', False), bool):
        raise InvalidTestError('captureException должен быть Булево')
    if not isinstance(check.get('snapshotArgs', False), bool):
        raise InvalidTestError('snapshotArgs должен быть Булево')
    if not isinstance(check.get('observeFailure', False), bool) or check.get('observeFailure') and check.get('captureException'):
        raise InvalidTestError('observeFailure должен быть Булево, отдельно от captureException')
    signature = parse_module(method)[0][0]
    return_type = signature.return_type(method)
    reachable = [(item.owner, item.source, item.parameter_types) for item in plan.symbols]
    aliases, call_rewrites, module_dependencies = plan.aliases, plan.rewrites, plan.module_dependencies
    methods = [(m, types) for owner, m, types in reachable if owner["sourceFile"] == module["sourceFile"]]
    if not is_object and any(re.search(r"\bэтот\b", call_code(m)) for m, _ in methods):
        raise InvalidTestError("Для объектного метода требуется context с начальными полями")
    signature_types, body_types = plan.signature_types, plan.body_types
    module_type_dependencies = plan.module_type_dependencies
    adapted = []
    external = {aliases[path]: [d['source'] for d in declarations] for path,declarations in plan.declarations.items()
                if path != module['sourceFile']}
    root_compiled = {}
    # A project method addressed through a form's module is still an original
    # static declaration. Only the explicit lifecycle roots need an instance.
    static_form_methods = {}
    if plan.open_forms:
        from .indexer import method_call_expressions
        from .runtime import method_closure
        targets = {aliases[f['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl']:
                   f['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl'
                   for f in plan.open_forms.values()
                   if f['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl' in aliases}
        for source_symbol in plan.symbols:
            source_node = parse_module(source_symbol.source)[0][0]
            source_calls = method_call_expressions(source_symbol.source, source_node)
            for a,b,alias in call_rewrites.get((source_symbol.owner['sourceFile'],source_node.name), []):
                if alias not in targets:
                    continue
                path = targets[alias]
                for call in source_calls:
                    if call.receiver_start == a and call.receiver_end == b:
                        text = (root / path).read_text(encoding='utf-8-sig')
                        static_form_methods.setdefault(path, set()).update(
                            parse_module(t)[0][0].name for t,_ in method_closure(text, call.name))
    for owner, dependency, _ in reachable:
        contracts.current_source = owner["sourceFile"]
        contracts.namespace, contracts.imports = owner["namespace"], owner.get("imports", [])
        declaration = parse_module(dependency)[0][0]
        symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == owner['sourceFile']
                      and s.identity.declaration == declaration.name)
        # Adapt declarations and resolved constructor type tokens only.
        replacements = [(*declaration.parameters_span, ",".join(
            p.partition(":")[0] + ":" + contracts.sbsl_type(p.partition(":")[2])
            for p in declaration.parameters(dependency)))]
        if declaration.return_span:
            replacements.append((*declaration.return_span, contracts.sbsl_type(declaration.return_type(dependency))))
        replacements.extend((start, end, contracts.sbsl_type(type_name))
                            for start, end, type_name in constructor_types(dependency)
                            if type_name not in contracts.platform_type_aliases
                            if contracts.sbsl_type(type_name) != dependency[start:end])
        replacements.extend((start, end, contracts.sbsl_type(dependency[start:end]))
                            for start, end, type_name in body_type_references(dependency)
                            if _project_body_type(contracts, type_name)
                            and contracts.sbsl_type(dependency[start:end]) != dependency[start:end])
        for access in re.finditer(rf'({IDENT}(?:::{IDENT})*)\.({IDENT})', call_code(dependency)[declaration.header_end:]):
            candidates = contracts.resolve(access[1])
            if len(candidates) == 1 and candidates[0]['elementType'] == 'Перечисление':
                contracts.require(access[1])
                start = declaration.header_end + access.start(1)
                canonical = contracts.canonical_type(access[1])
                if canonical != access[1]:
                    replacements.append((start, start + len(access[1]), canonical))
                module_type_dependencies.setdefault(owner['sourceFile'], []).append(canonical + '.Значение')
        replacements.extend(call_rewrites.get((owner["sourceFile"], declaration.name), []))
        replacements.extend(plan.source_transforms.get((owner['sourceFile'], declaration.name), []))
        replacements = list(dict.fromkeys(replacements))
        if plan.resources or any(b.category == 'resource-api' for b in plan.bindings):
            module_type_dependencies.setdefault(owner['sourceFile'], []).append('ТестСессия.Записи')
        if plan.exception_types:
            module_type_dependencies.setdefault(owner['sourceFile'], []).append('ТестБизнесИсключения.ИсключениеВалидации')
        if storage:
            from .indexer import method_call_expressions
            for item in method_call_expressions(dependency, declaration):
                metadata_binding = any(b.category == 'metadata' and b.operation == item.name
                                       and b.source_file == owner['sourceFile']
                                       and b.start == symbol.start + item.start for b in plan.bindings)
                if metadata_binding and item.receiver and item.name in {'ПолучитьСсылку', 'СоздатьОбъект'}:
                    matches = contracts.resolve(item.receiver)
                    if len(matches) == 1 and matches[0] in plan.storage_elements:
                        replacements.append((item.receiver_start, item.receiver_end,
                                             contracts.canonical_type(item.receiver)))
                        module_type_dependencies.setdefault(owner['sourceFile'], []).append(
                            contracts.canonical_type(item.receiver) + '.Ссылка')
                if metadata_binding and item.receiver is None and item.receiver_start is None and item.name in {'ПолучитьСсылку', 'СоздатьОбъект'}:
                    matches = contracts.resolve(owner['name'])
                    if len(matches) == 1 and matches[0]['elementType'] in {'Справочник', 'Документ'}:
                        replacements.append((item.start, item.start, contracts.canonical_type(owner['name']) + '.'))
        # Canonical type spans may contain individual exception-type tokens.
        # Keep the enclosing verified type replacement, then reject any partial
        # overlap instead of applying offsets to already modified source.
        replacements = list(dict.fromkeys(replacements))
        replacements = [r for r in replacements if not any(
            a <= r[0] and r[1] <= b and (a,b) != r[:2] and r[0] != r[1]
            for a,b,_ in replacements if a != b)]
        occupied = sorted((a,b) for a,b,_ in replacements if a != b)
        if any(b > c for (a,b),(c,d) in zip(occupied,occupied[1:])):
            raise InputError('Конфликт проверенных преобразований исходных диапазонов')
        # Stable ordering matters for insertions at one lexical boundary:
        # a nested resource tail must precede its enclosing catch fallback.
        for start, end, replacement in sorted(replacements, key=lambda r: (r[0], r[1]), reverse=True):
            dependency = dependency[:start] + replacement + dependency[end:]
        symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == owner['sourceFile']
                      and s.identity.declaration == declaration.name)
        compiled = platform.adapt(dependency)
        if 'ИменованныеПараметры' in symbol.annotations:
            compiled = '@ИменованныеПараметры\n' + compiled
        if check.get('trace', False):
            from .observations import instrument
            symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == owner['sourceFile']
                          and s.identity.declaration == declaration.name)
            enum_value = None
            return_mapping = re.fullmatch(r'(?:Читаемое)?Соответствие<(.+)>', contracts.canonical_type(parse_module(symbol.source)[0][0].return_type(symbol.source) or 'ничто'))
            if return_mapping:
                from .indexer import split_parameters
                key, val = [t.strip() for t in split_parameters(return_mapping[1])]
                if key in contracts.enums:
                    enum_value = contracts.sbsl_type(val)
            compiled = instrument(compiled, '::'.join(filter(None, (symbol.identity.project,
                                  symbol.identity.namespace, symbol.identity.owner, declaration.name))), enum_map_value_type=enum_value)
        if owner["sourceFile"] == module["sourceFile"]:
            adapted.append(compiled)
            root_compiled[declaration.name] = compiled
        elif any(f['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl' == owner['sourceFile'] for f in plan.open_forms.values()):
            target_form = next(f for f in plan.open_forms.values() if f['identity']['sourceFile'].removesuffix('.yaml') + '.xbsl' == owner['sourceFile'])
            contracts.attach_method(target_form['canonical'], compiled, module_type_dependencies.get(owner['sourceFile'], []))
            contracts.method_dependencies[target_form['canonical']].extend(aliases[p] + '.Вызов' for p in module_dependencies.get(owner['sourceFile'], ()))
            if declaration.name in static_form_methods.get(owner['sourceFile'], set()):
                external.setdefault(aliases[owner['sourceFile']], []).append(compiled)
        elif owner.get('moduleType') == 'object':
            object_type = '::'.join(filter(None, (owner['namespace'], owner['name'])))
            contracts.attach_method(object_type, compiled, module_type_dependencies.get(owner['sourceFile'], []))
            contracts.method_dependencies[contracts.canonical_type(object_type)].extend(
                aliases[path] + '.Вызов' for path in module_dependencies.get(owner['sourceFile'], ()))
        else:
            alias = aliases[owner["sourceFile"]]
            external.setdefault(alias, []).append(compiled)
    # A handler may call a scalar helper in the entry module. Import just that
    # helper's closure, avoiding a spurious object <-> full entry-module cycle.
    root_path = module['sourceFile']
    if any(root_path in deps for path,deps in module_dependencies.items() if path != root_path):
        from .runtime import method_closure
        from .indexer import method_call_expressions
        root_alias = aliases[root_path]
        selected = set()
        for symbol in plan.symbols:
            if symbol.identity.source_file == root_path:
                continue
            rewrites = call_rewrites.get((symbol.identity.source_file,symbol.identity.declaration), [])
            calls = method_call_expressions(symbol.source, parse_module(symbol.source)[0][0])
            for start,end,replacement in rewrites:
                if replacement != root_alias:
                    continue
                for call in calls:
                    if call.receiver_start == start and call.name in root_compiled:
                        selected.update(parse_module(t)[0][0].name for t,_ in method_closure(original, call.name))
        external[root_alias] = [d['source'] for d in plan.declarations.get(root_path, [])] + [
            root_compiled[name] for name in sorted(selected)]
    contracts.namespace, contracts.imports = module["namespace"], module.get("imports", [])
    contracts.current_source = module["sourceFile"]
    calls = platform.finish()
    module_declarations = [global_body(d['source']) for d in plan.declarations.get(module['sourceFile'], [])]
    method = "\n".join(module_declarations + list(contracts.required_structures.values()) + adapted)
    from .form_context import form_argument
    def argument(value, typ, index=None):
        if isinstance(value, dict) and set(value) in ({'actionArg'}, {'actionResult'}):
            if index is None or not plan.sequence or not check.get('snapshotArgs'):
                raise InvalidTestError('actionArg/actionResult требуют sequence и snapshotArgs')
            ref = value.get('actionArg', value.get('actionResult'))
            action = ref[0] if isinstance(ref, list) and len(ref) == 2 else ref
            if type(action) is not int or not 0 <= action < int(index):
                raise InvalidTestError('Ссылка sequence требует предыдущий action')
            step = plan.sequence[action]
            symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == module['sourceFile'] and s.identity.declaration == step['method'])
            if 'actionArg' in value:
                if not isinstance(ref, list) or len(ref) != 2 or type(ref[1]) is not int or not 0 <= ref[1] < len(symbol.parameter_types):
                    raise InvalidTestError('actionArg требует [action, argument]')
                actual_type = symbol.parameter_types[ref[1]]
                expression = 'ТестАргумент' + str(action) + '_' + str(ref[1])
            else:
                if type(ref) is not int:
                    raise InvalidTestError('actionResult требует номер action')
                actual_type = parse_module(symbol.source)[0][0].return_type(symbol.source)
                expression = 'Результат' + str(action)
            if not actual_type or contracts.canonical_type(actual_type) != contracts.canonical_type(typ):
                raise InvalidTestError('Несовместимый тип ссылки sequence')
            return expression
        return form_argument(plan, value, typ) or contracts.literal(value, typ)
    call = ", ".join(argument(v, t) for v, t in zip(args, types))
    setup = ""
    runtime_metadata = ''
    expression = target["method"] + '(' + call + ')'
    observed = "Контекст"
    if plan.form:
        from .form_context import form_observation
        observed = form_observation(plan)
    elif "observe" in check:
        fields = check["observe"]
        if not is_object or not isinstance(fields, list) or not fields or any(not isinstance(f, str) for f in fields):
            raise InvalidTestError("observe требует объектный context и непустой список полей")
        contracts.require(module["name"])
        known = {f["Имя"] for f in contracts.fields[contracts.canonical_type(module["name"])]}
        if len(set(fields)) != len(fields) or set(fields) - known:
            raise InvalidTestError("observe содержит неизвестные или повторяющиеся поля")
        observed = "{" + ", ".join(sbsl_literal(f, "Строка") + ": Контекст." + f for f in fields) + "}"
    if is_object:
        object_type = plan.form['canonical'] if plan.form else contracts.canonical_type(module["name"])
        if mocks.get('registers'):
            # A handler needs the register, whose dimensions need the owner's
            # reference. Put executable context in a separate module to keep
            # SBSL imports acyclic without changing the handler body.
            contracts.require(object_type)
            context_type = 'ТестКонтекст.Объект'
            contracts.fields[context_type] = contracts.fields[object_type]
            contracts.definitions[context_type] = contracts.definitions[object_type]
            object_type = context_type
        contracts.attach_method(object_type, "\n@Глобально\n".join(adapted),
                                [typ for values in module_type_dependencies.values() for typ in values])
        contracts.method_dependencies[object_type].extend(
            [name + '.НаборЗаписей' for name in mocks.get('registers', [])]
            + (['ТестПлатформа.Запрос'] if mocks.get('queries') else []))
        contracts.method_dependencies[object_type].extend(module_type_dependencies.get(module['sourceFile'], []))
        contracts.method_dependencies[object_type].extend(aliases[p] + '.Вызов' for p in module_dependencies.get(module['sourceFile'], ()))
        setup = '    знч Контекст = ' + contracts.literal(context, object_type) + '\n'
        if plan.form and plan.form['objectType']:
            setup += '    Контекст.Объект.ТестСостояниеНовизны = ' + sbsl_literal(check['lifecycle']['isNew'], 'Булево') + '\n'
        if plan.form and 'formEffects' in check and plan.form['objectType'] and 'Ссылка' not in context.get('Объект', {}):
            setup += '    Контекст.Объект.Ссылка.Идентификатор = Ууид.Случайный()\n'
        if 'runtimeDateTime' in check:
            field = check['runtimeDateTime']
            date_fields = {f['Имя'] for f in contracts.fields[object_type] if f['Тип'] == 'ДатаВремя'}
            if not isinstance(field, str) or field not in date_fields or field in context:
                raise InvalidTestError('runtimeDateTime требует поле ДатаВремя, отсутствующее в context')
            setup += ('    Контекст.' + field + ' = ДатаВремя.Сейчас(ЧасовойПояс{UTC})\n'
                      '    знч ВремяТеста = Контекст.' + field + '\n')
            runtime_metadata = ', "runtimeDateTime": ВремяТеста'
        expression = 'Контекст.' + expression
        method = ""
    live_values = []
    def drive(name, parameter_types, values, result_type, index=''):
        prepared = [argument(v, t, index if plan.sequence else None) for v, t in zip(values, parameter_types)]
        argument_setup = ''
        if check.get('snapshotArgs', False):
            variables = ['ТестАргумент' + index + '_' + str(i) for i in range(len(prepared))]
            argument_setup = ''.join('    знч ' + n + ' = ' + v + '\n' for n, v in zip(variables, prepared))
            prepared = variables
        arguments = ', '.join(prepared)
        expression = ('Контекст.' if is_object else '') + name + '(' + arguments + ')'
        result = 'Результат' + index
        if result_type and result_type != 'ничто':
            invocation = '    знч ' + result + ' = ' + expression + '\n'
            transported = result
            mapping = re.fullmatch(r'(?:Читаемое)?Соответствие<(.+)>', contracts.canonical_type(result_type))
            if mapping:
                from .indexer import split_parameters
                key_type, value_type = [t.strip() for t in split_parameters(mapping[1])]
                if key_type in contracts.enums:
                    transported = 'ТестСоответствие' + index
                    invocation += ('    знч ' + transported + ' = новый Соответствие<Строка, ' + contracts.sbsl_type(value_type) + '>()\n'
                                   + '    для Элемент из ' + result + '\n        ' + transported + '.Вставить(Элемент.Ключ.ВСтроку(), Элемент.Значение)\n    ;\n')
            actual = '{"return": ' + transported + ', "context": ' + observed + '}' if is_object else transported
        else:
            invocation = '    ' + expression + '\n'
            actual = observed if is_object else 'Неопределено'
        if platform.capture:
            actual = '{"context": ' + actual + ', "calls": ' + calls + '}'
        if check.get('captureException', False) or check.get('observeFailure', False):
            invocation = invocation.replace('знч ' + result + ' =', result + ' =')
            exception = 'ИсключениеРезультат' + index
            invocation = ('    пер ' + result + ': Объект? = Неопределено\n    пер ' + exception + ': Объект? = Неопределено\n    попытка\n'
                          + ''.join('    ' + line + '\n' for line in invocation.splitlines())
                          + '    поймать Ошибка: Исключение\n'
                          + ('        если Ошибка это ИсключениеНеподдерживаемаяОперация\n            выбросить Ошибка\n        ;\n        ТестОтказ = Ошибка.Описание\n' if check.get('observeFailure') else '        если не (Ошибка это ИсключениеНедопустимоеСостояние'
                          + (' или Ошибка это ТестБизнесИсключения.ИсключениеВалидации' if 'ИсключениеВалидации' in plan.exception_types else '')
                          + (' или Ошибка это ИсключениеНедопустимыйАргумент' if 'ИсключениеНедопустимыйАргумент' in check.get('captureExceptionTypes', []) else '')
                          + ')\n            выбросить Ошибка\n        ;\n')
                          + ('        ТестСессия.Откатить()\n' if storage and storage.config.get('transaction') else '')
                          + '        ' + exception + ' = {"type": '
                          + ('(Ошибка это ТестБизнесИсключения.ИсключениеВалидации ? "ИсключениеВалидации" : Ошибка.ПолучитьТип().ВСтроку())'
                             if 'ИсключениеВалидации' in plan.exception_types else 'Ошибка.ПолучитьТип().ВСтроку()')
                          + ', "message": Ошибка.Описание}\n'
                          + '    ;\n')
            actual = '{"result": ' + actual + ', "exception": ' + exception + '}'
        if check.get('snapshotArgs', False):
            actual = '{"result": ' + actual + ', "args": ' + ('[' + arguments + ']' if arguments else 'новый Массив<Объект?>()') + '}'
        if check.get('snapshotConstants', False):
            from .session_contracts import constants_observation
            actual = '{"result": ' + actual + ', "constants": ' + constants_observation(plan) + '}'
        if 'formEffects' in check:
            actual = '{"result": ' + actual + ', "openings": ТестЗапросыФорм.Читать(), "lifecycle": Контекст.Объект.ЭтоНовый(), "storage": ТестСессия.Аудит()}'
        if storage and storage.config.get('transaction', False):
            fact = 'Факты' + index
            invocation = ('    пер ' + fact + ': Объект? = Неопределено\n    ТестСессия.Начать()\n    попытка\n'
                          + ''.join('    ' + line + '\n' for line in invocation.splitlines())
                          + '        ' + fact + ' = ' + actual + '\n        ТестСессия.Завершить()\n'
                          + '    поймать Сбой: Исключение\n        ТестСессия.Откатить()\n        выбросить Сбой\n    ;\n')
            actual = fact
        if 'formEffects' in check and storage.config.get('transaction', False):
            actual = '{"result": ' + actual + ', "committed": ТестСессия.Аудит()}'
        return argument_setup + invocation, actual
    invocation, actual = drive(target['method'], types, args, return_type)
    if plan.sequence:
        invocation = '    знч Действия = новый Массив<Строка>()\n'
        for index, step in enumerate(plan.sequence):
            symbol = next(s for s in plan.symbols if s.owner['sourceFile'] == module['sourceFile']
                          and s.identity.declaration == step['method'])
            result_type = parse_module(symbol.source)[0][0].return_type(symbol.source)
            invoke, value = drive(step['method'], symbol.parameter_types, step['args'], result_type, str(index))
            invocation += invoke + '    Действия.Добавить(СериализацияJson.ЗаписатьОбъект(' + value + '))\n'
            live_values.append(value)
        actual = '{"actions": Действия}'
    else:
        live_values.append(actual)
    if plan.probes:
        invocation += ('    знч СнимокДоПроб = СериализацияJson.ЗаписатьОбъект(' + actual + ')\n'
                       '    знч Пробы = новый Массив<Строка>()\n')
        for probe in plan.probes:
            invocation += ('    ' + probe['expression'] + ' = ' + probe['literal'] + '\n'
                           '    Пробы.Добавить(СериализацияJson.ЗаписатьОбъект([' + ', '.join(live_values) + ']))\n')
        actual = '{"result": СнимокДоПроб, "probes": Пробы}'
    if check.get('observeFailure'):
        setup = '    пер ТестОтказ: Строка? = Неопределено\n' + setup
        runtime_metadata += ', "status": ТестОтказ == Неопределено ? "EXECUTED" : "ERROR", "message": ТестОтказ'
    if storage:
        for element in plan.storage_elements:
            storage.attach(element)
        setup = storage.setup() + setup
        invocation += '    ТестСессия.ПроверитьСессию()\n'
        if storage.config.get('backend') == 'postgres':
            setup = '    ТестСессия.НастроитьSql(Путь)\n' + setup + '    ТестСессия.Опубликовать(Путь, Истина)\n'
            invocation += '    ТестСессия.Опубликовать(Путь)\n'
        actual = '{"result": ' + actual + ', "storage": ТестСессия.Аудит(Истина)}'
    for path, owner in plan.co_located_modules.items():
        bodies = external.pop(owner, [])
        key = owner + '.ИсходныйМодуль'
        contracts.definitions[key] = '\n'.join(global_body(body.rstrip()) + '\n' for body in bodies)
        contracts.method_dependencies[key] = module_type_dependencies.get(path, []) + [
            aliases[dep] + '.Вызов' for dep in module_dependencies.get(path, ())]
    imports = contracts.write(sandbox)
    from .session_contracts import render_session
    session_setup, session_metadata = render_session(plan, sandbox)
    plan.rendered_session = (session_setup, session_metadata)
    setup = session_setup + setup
    runtime_metadata += session_metadata
    generated_owners = {name.split(".")[0] for name in contracts.definitions}
    for alias, bodies in external.items():
        if (sandbox / (alias + ".sbsl")).exists():
            raise InputError("Конфликт имени импортированного модуля: " + alias)
        path = next(path for path, name in aliases.items() if name == alias)
        needed = [f"#требуется {aliases[other]}.sbsl"
                  for other in sorted(module_dependencies.get(path, ()))]
        if path == root_path:
            needed = []
        type_owners = set()
        for canonical in module_type_dependencies.get(path, ()):
            type_owners.update(re.findall(rf"({IDENT})\.", canonical))
            type_owners.update(enum for enum in contracts.enums
                               if re.search(rf"(?<!\w){re.escape(enum)}(?!\w)", canonical))
        needed.extend(f"#требуется {owner}.sbsl" for owner in sorted(type_owners & generated_owners))
        if path == root_path:
            referenced = set(re.findall(rf'({IDENT})\.', call_code('\n'.join(bodies))))
            needed = [f'#требуется {name}.sbsl' for name in sorted(referenced & (generated_owners | set(aliases.values())))
                      if name != alias]
        (sandbox / (alias + ".sbsl")).write_text(
            "\n".join(needed + [global_body(body) for body in bodies]), encoding="utf-8")
    if external:
        imports = "\n".join(f"#требуется {alias}.sbsl" for alias in sorted(external)) + "\n" + imports
    if 'runtimeDateTime' in check and not is_object:
        raise InvalidTestError('runtimeDateTime требует объектный context')
    metadata = runtime_metadata
    snapshot_mode = ('probes-' if plan.probes else '') + ('sequence' if plan.sequence else 'single')
    if plan.probes or plan.sequence:
        metadata += ', "_snapshotMode": ' + sbsl_literal(snapshot_mode, 'Строка')
    from .resolution import qualified
    identities = {'Scripts::' + typ: qualified(contracts.canonical_elements[typ.split('.')[0]]) + '.' + typ.partition('.')[2]
                  for typ in contracts.fields if '.' in typ and typ.split('.')[0] in contracts.canonical_elements}
    metadata += ', "_typeIdentities": ' + contracts.literal(identities, 'Соответствие<Строка, Строка>')
    if storage:
        metadata += ', "_rawStorage": Истина'
        metadata += ', "storageTrace": ТестСессия.Трасса()'
        metadata += ', "storageDiagnostics": {"active": ТестСессия.ЕстьАктивная(), "locks": ТестСессия.Блокировок()}'
    if platform.capture:
        metadata += ', "_captureCalls": Истина, "_captureException": ' + ('Истина' if check.get('captureException') else 'Ложь')
        if platform.observe is not None:
            metadata += ', "_observeCallArguments": ' + contracts.literal(platform.observe, 'Массив<Строка>')
    script = sandbox / "test.sbsl"
    entry = ('    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": ' + actual + metadata + '}))\n')
    statements = setup + invocation + entry
    signature = 'Путь: Строка' if storage and storage.config.get('backend') == 'postgres' else ''
    # Runtime unsupported operations are typed infrastructure outcomes, never captured business exceptions.
    body = ('    попытка\n' + ''.join('    ' + line + '\n' for line in statements.splitlines())
            + '    поймать Недоступно: ИсключениеНеподдерживаемаяОперация\n'
            + '        Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": "UNSUPPORTED"}))\n    ;\n')
    script.write_text(imports + "\n" + method + '\n\nметод Скрипт(' + signature + ')\n' + body + ';\n', encoding="utf-8")
    return script
