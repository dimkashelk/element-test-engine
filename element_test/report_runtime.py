"""Report data/layout, navigation and report-panel runtime entries (stage 51).

Metadata table sources lower to the shared query AST. Layout formulas use their
own IR and execute in Script. Panel data is freshly read on each queue event.
"""
from copy import deepcopy, copy
from hashlib import sha256
from pathlib import Path

from .execution_plan import ExecutionPlan, SourceSymbol, SymbolIdentity
from .generated_types import ProjectTypes
from .model import select_check_project
from .navigation import select_element, evidence, resolve, build_navigation, select_route
from .resolution import qualified
from .test_contracts import obj, validate_schema
from .yaml_io import InvalidTestError, UnsupportedSyntaxError


def surface_options_schema(kind):
    selector = obj({'name': {'type': 'string', 'minLength': 1}, 'namespace': {'type': 'string'},
                    'sourceHash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                    'projectSourceHash': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}}, ['name'])
    props = {'schemaVersion': {'const': 1, 'type': 'integer'}, kind: selector}
    if kind == 'panel': props['parameters'] = {'type': 'object'}
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': 'runtime.' + kind + ' v1', **obj(props, ['schemaVersion', kind])}


def _plain(check, kind):
    conflicts = {'target', 'args', 'context', 'sequence', 'formEffects', 'formRequirement', 'querySource',
        'interface', 'mocks', 'captureCalls', 'captureException', 'observeFailure', 'observe', 'snapshotArgs',
        'instanceProbes', 'queryResults', 'snapshotConstants', 'clockOracle', 'runtimeDateTime',
        'captureExceptionTypes', 'probeMutations'}
    if set(check) & conflicts:
        raise InvalidTestError(kind + ': несовместимый методный контракт: ' + ', '.join(sorted(set(check) & conflicts)))
    # Profile validation remains in the public runtime path.
    return {k: deepcopy(v) for k,v in check.items() if k not in {
        'report', 'navigation', 'panel', 'expected', 'reportActions', 'navigationActions', 'panelActions',
        'navigationContext', 'queryActions', 'library'}}


def _entry(root, model, check, e):
    module = {'name': e['name'], 'namespace': e['namespace'], 'sourceFile': e['sourceFile'],
              'imports': e['properties'].get('Импорт', []), 'moduleType': 'query'}
    source = (root / e['sourceFile']).read_text(encoding='utf-8-sig')
    ident = SymbolIdentity(model['properties'].get('Поставщик', ''), model['properties'].get('Имя', ''),
                           e['namespace'], e['name'], '', e['sourceFile'])
    symbol = SourceSymbol(ident, module, source, [], 0, len(source))
    plan = ExecutionPlan(root, model, check, module, source, symbol, [], {}, {}, {})
    plan.contracts = ProjectTypes(model, e['namespace'], module['imports'])
    plan.contracts.rename_collisions = True
    from .session_contracts import prepare_session, generate_session_types
    prepare_session(plan); generate_session_types(plan, plan.contracts)
    return plan


def _method(c, owner, text, types):
    c.definitions[owner] = c.definitions.get(owner, '') + '@Глобально\n' + text.rstrip() + '\n'
    c.method_dependencies.setdefault(owner, []).extend(types)


def _report(root, model, plain, e, selector=None, shared=None):
    from .report_formulas import parse_formula, fields, source_field, Compiler
    from .query_sources import source_schema
    from .source_query import plan_source_query
    from .form_context import add_structure
    plain = {k:v for k,v in plain.items() if k != 'timers'}
    if shared is None:
        if not isinstance(plain.get('storage'), dict):
            raise InvalidTestError('Отчет требует явный storage')
        from .storage import MetadataStorage
        shared = _entry(root, model, plain, e)
        config = plain['storage']
        shared.storage = MetadataStorage(shared.contracts, {**config, 'registers': []})
        shared.storage.config = config
        registers = config.get('registers', [])
        if not isinstance(registers, list) or len(set(map(str,registers))) != len(registers):
            raise InvalidTestError('storage.registers требует уникальные типы')
        # Author fixtures have explicit ownership; source lookup still uses the
        # report's visibility scope. Attach each schema in its own declaration.
        for register in registers:
            if not isinstance(register, str): raise InvalidTestError('Тип регистра требует строку')
            owners = [x for x in model['elements'] if qualified(x) == register or x['name'] == register]
            if len(owners) != 1: raise InvalidTestError('Регистр fixture отсутствует или неоднозначен')
            shared.contracts.namespace = owners[0]['namespace']
            shared.contracts.imports = owners[0]['properties'].get('Импорт', [])
            shared.storage.attach_register(qualified(owners[0]))
        shared.contracts.namespace, shared.contracts.imports = e['namespace'], e['properties'].get('Импорт', [])
    props = e['properties']; fact, positions = evidence(root, model, e, selector)
    layout = props.get('Макет', {})
    if layout.get('ВидОтображения', 'СводнаяТаблица') != 'СводнаяТаблица':
        raise UnsupportedSyntaxError('Отчет: вид макета вне контракта')
    declarations = layout.get('Поля')
    if not isinstance(declarations, list) or not declarations or len(declarations) > 200:
        raise UnsupportedSyntaxError('Отчет требует 1..200 полей макета')
    trees, layout_fields, ids = [], [], set()
    for i, f in enumerate(declarations):
        if not isinstance(f, dict) or f.get('Вид') != 'Измерение' or not isinstance(f.get('Ид'), str) or not f['Ид'] or f['Ид'] in ids:
            raise UnsupportedSyntaxError('Макет требует уникальные Ид полей вида Измерение')
        ids.add(f['Ид'])
        roles = f.get('ВизуальныеРоли', [])
        if not isinstance(roles, list) or set(roles) - {'Строки', 'Колонки', 'Иерархия'} or len(set(roles)) != len(roles):
            raise UnsupportedSyntaxError('Визуальные роли макета вне контракта')
        if type(f.get('Использовать', True)) is not bool or f.get('НаправлениеСортировки') not in {None,'ПоВозрастанию','ПоУбыванию'}:
            raise UnsupportedSyntaxError('Настройки поля макета вне контракта')
        tree = parse_formula(f.get('Выражение')); trees.append(tree)
        at = ('Макет','Поля',str(i),'Выражение')
        layout_fields.append({'id': f['Ид'], 'expression': f['Выражение'], 'formula': tree.to_dict(),
            'roles': roles, 'used': f.get('Использовать', True), 'caption': f.get('Представление', ''),
            'sorting': f.get('НаправлениеСортировки'), 'yamlPath': list(at), 'position': positions.get(at)})
    source_kind = props.get('ВидИсточникаДанных', 'Таблица')
    if source_kind == 'Запрос':
        query_file = Path(e['sourceFile']).with_suffix('.xbql').as_posix()
        qcheck = {**plain, 'querySource': {'file': query_file}}
        plan = plan_source_query(root, model, qcheck, shared=shared)
    elif source_kind == 'Таблица':
        c = shared.contracts if shared else ProjectTypes(model, e['namespace'], props.get('Импорт', []))
        c.namespace, c.imports = e['namespace'], props.get('Импорт', [])
        c.rename_collisions = True
        raw = props.get('ИсточникДанных')
        if not isinstance(raw, str) or not raw:
            raise UnsupportedSyntaxError('Отчет требует ИсточникДанных')
        name, dot, member = raw.partition('.')
        schema = source_schema(c, name, member)
        required = list(dict.fromkeys(n for tree in trees for n in fields(tree)))
        if not required:
            raise UnsupportedSyntaxError('Табличный отчет требует хотя бы одно поле источника')
        projection = ', '.join(source_field(n, schema) + ' КАК ' + n for n in required)
        query = 'ВЫБРАТЬ ' + projection + ' ИЗ ' + raw
        source = (root / e['sourceFile']).read_text(encoding='utf-8-sig')
        module = {'name': e['name'], 'namespace': e['namespace'], 'sourceFile': e['sourceFile'],
                  'imports': props.get('Импорт', []), 'moduleType': 'query'}
        qcheck = dict(plain)
        plan = plan_source_query(root, model, qcheck,
            metadata_source=(source, module, (0, len(source), 0, query), fact['sourceHash']), shared=shared)
        plan.query_entry.update({'kind': 'metadata-table', 'family': 'metadata-source',
                                'metadataSource': raw, 'yamlPath': ['ИсточникДанных'],
                                'position': positions.get(('ИсточникДанных',)),
                                'astRangeOrigin': 'metadata-lowering', 'generatedText': query,
                                'sourceDeclaration': source[slice(*positions[('ИсточникДанных',)]['range'])]})
        plan.queries[0].update({'origin':'metadata-table', 'astRangeOrigin':'metadata-lowering',
                                'yamlPath':['ИсточникДанных'], 'position':positions.get(('ИсточникДанных',))})
    else: raise UnsupportedSyntaxError('Источник отчета вне контракта: ' + str(source_kind))
    c = plan.contracts
    row_type = plan.queries[0]['rowType']
    row_fields = {label: field['type'] for field,label in plan.queries[0]['ast']['projections']}
    c.definitions.setdefault(row_type, '')
    c.fields.setdefault(row_type, [{'Имя': name, 'Тип': typ} for name,typ in row_fields.items()])
    suffix = sha256((qualified(e) + fact['sourceHash']).encode()).hexdigest()[:16]
    helper = 'ТестОтчет' + suffix
    add_structure(c, helper + '.Маркер', [])
    compiler = Compiler(plan, row_type, row_fields, helper)
    compiled = []
    for i, (tree, f) in enumerate(zip(trees, layout_fields)):
        expression, typ = compiler.compile(tree)
        f['outputType'] = typ
        compiled.append((expression, typ))
    enabled = [(i,f) for i,f in enumerate(layout_fields) if f['used']]
    # Keep typed layout values while sorting; serialization occurs afterwards.
    value_type = helper + '.СтрокаМакета'
    add_structure(c, value_type, [{'Имя': 'F'+str(i), 'Тип': compiled[i][1]} for i,_ in enabled])
    c.definitions[value_type] = c.definitions[value_type].replace('    пер ', '    обз пер ')
    pairs = ', '.join('F'+str(i)+' = '+compiled[i][0] for i,_ in enabled)
    body = ('    знч Values = новый Массив<' + value_type + '>()\n'
            '    для Row из Data\n        Values.Добавить(новый ' + value_type + '(' + pairs + '))\n    ;\n')
    comparisons = []
    for i,f in enabled:
        if not f['sorting']: continue
        full_type = compiled[i][1]
        typ = full_type.rstrip('?'); left, right = 'A.F'+str(i), 'B.F'+str(i)
        if typ.endswith('.Ссылка') and '|' not in typ:
            left += '.Идентификатор.ВСтроку()' if c.reference_id_type == 'Ууид' else '.Идентификатор'
            right += '.Идентификатор.ВСтроку()' if c.reference_id_type == 'Ууид' else '.Идентификатор'
        elif typ in c.enums:
            members = c.enums[typ]
            # Order is the declared enum order, not its serialized caption.
            def enum_index(expr):
                result = str(len(members)-1)
                for n in reversed(range(len(members)-1)):
                    result = '(' + expr + ' == ' + typ + '.' + members[n] + ' ? ' + str(n) + ' : ' + result + ')'
                return result
            left, right = enum_index(left), enum_index(right)
        elif typ not in {'Число','Строка','Дата','ДатаВремя','Момент'}:
            raise UnsupportedSyntaxError('Сортировка макета вне контракта: ' + typ)
        if full_type.endswith('?'):
            av, bv = 'A.F'+str(i), 'B.F'+str(i)
            null_after = 'Ложь' if f['sorting']=='ПоВозрастанию' else 'Истина'
            comparisons.append('    если '+av+' == Неопределено и '+bv+' != Неопределено\n        возврат '+null_after+'\n    ;\n    если '+av+' != Неопределено и '+bv+' == Неопределено\n        возврат '+('Истина' if null_after=='Ложь' else 'Ложь')+'\n    ;\n')
            comparisons.append('    если '+av+' != Неопределено и '+bv+' != Неопределено\n')
        comparisons.append('    если ' + left + ' != ' + right + '\n        возврат ' + left + (' > ' if f['sorting']=='ПоВозрастанию' else ' < ') + right + '\n    ;\n')
        if full_type.endswith('?'): comparisons.append('    ;\n')
    if comparisons:
        _method(c, helper, 'метод After(A: '+value_type+', B: '+value_type+'): Булево\n'+''.join(comparisons)+'    возврат Ложь\n;', [value_type])
        body += ('    пер I = 1\n    пока I < Values.Размер()\n        пер J = I\n'
                 '        пока J > 0 и After(Values[J - 1], Values[J])\n'
                 '            знч V = Values[J]\n            Values[J] = Values[J - 1]\n            Values[J - 1] = V\n            J -= 1\n        ;\n        I += 1\n    ;\n')
    body += '    знч Rows = новый Массив<Строка>()\n    для Value из Values\n'
    # The report snapshot decoder preserves typed union/reference values.
    values = ', '.join(c.literal(f['id'],'Строка') + ': Value.F'+str(i) for i,f in enabled)
    body += '        Rows.Добавить(СериализацияJson.ЗаписатьОбъект({' + values + '}))\n    ;\n'
    structure = {'fields': [{k:v for k,v in f.items() if k not in {'formula','position','yamlPath'}} for f in layout_fields],
                 'display': layout.get('ВидОтображения','СводнаяТаблица'),
                 'hierarchy': [f['id'] for _,f in enabled if 'Иерархия' in f['roles']]}
    body += '    возврат {"data": Data, "values": Rows, "layout": ' + c.literal(structure,'Объект') + ', "cardinality": Data.Размер()}\n'
    for method in compiler.methods: _method(c, helper, method, compiler.types)
    _method(c, helper, 'метод Evaluate(Data: Массив<'+c.sbsl_type(row_type)+'>): Соответствие<Строка, Объект?>\n'+body+';', [row_type, value_type])
    query_name, query_values, _, _ = plan._query_driver
    read = ('метод Read(): Соответствие<Строка, Объект?>\n'
            '    знч Result = '+query_name+'.Создать('+', '.join(query_values)+').Выполнить()\n'
            '    знч Data = Result.ВМассив()\n    возврат Evaluate(Data)\n;')
    c.definitions.setdefault(query_name+'.Запрос', '')
    _method(c, helper, read, [row_type, query_name+'.Запрос'])
    plan.report = {**fact, 'schemaVersion': 1, 'sourceKind': source_kind, 'query': plan.query_entry,
                   'fields': layout_fields, 'structure': structure, 'support': 'executor-emulation',
                   'stages': {'validation':'ready','planning':'ready','generation':'ready','execution':'not-checked'},
                   'remaining': ['Native pivot rendering, hierarchy expansion and aggregate measures are external',
                                 'Reference sorting uses stable identifiers, not native presentations']}
    plan._report_read = helper + '.Read()'
    return plan


def plan_surface(root, source_model, check):
    kind = next(k for k in ('report','navigation','panel') if k in check)
    validate_schema(check[kind], surface_options_schema(kind), kind)
    root, model = select_check_project(root, source_model, check)
    plain = _plain(check, kind)
    if kind != 'navigation' and 'navigationContext' in check:
        raise InvalidTestError('navigationContext требует вход navigation')
    foreign = {'reportActions','navigationActions','panelActions'} - {kind+'Actions'}
    if set(check) & foreign or 'queryActions' in check:
        raise InvalidTestError('Действия требуют соответствующий вход отчета/навигации/панели')
    e = select_element(model, check[kind][kind], {'report':{'Отчет'}, 'navigation':{'ФрагментКомандногоИнтерфейса','КомпонентИнтерфейса'}, 'panel':{'ПанельОтчетов'}}[kind])
    if kind == 'report':
        if 'timers' in check: raise InvalidTestError('Таймеры обновления требуют panel')
        actions = check.get('reportActions', [{'operation':'execute'}])
        query_actions = []
        for a in _actions(actions):
            if a.get('operation') not in {'execute','write'}: raise InvalidTestError('reportActions: execute/write')
            query_actions.append(a)
        plan = _report(root, model, {**plain, 'queryActions':query_actions}, e, check[kind][kind])
        plan._surface_actions = query_actions
    elif kind == 'navigation':
        if {'storage','timers','queryParameters'} & check.keys():
            raise InvalidTestError('Навигация модели не требует storage/timers/queryParameters')
        plan = _entry(root, model, plain, e)
        plan.navigation = build_navigation(root, model, e, check.get('navigationContext', {}), check[kind][kind])
        plan._surface_actions = []
        for action in _actions(check.get('navigationActions',[{'operation':'resolve'}])):
            if action == {'operation':'resolve'}:
                plan._surface_actions.append(action)
            elif action.get('operation') == 'open' and set(action) == {'operation','selector'}:
                plan._surface_actions.append({'operation':'open','route': select_route(plan.navigation['routes'], action['selector'])})
            else: raise InvalidTestError('navigationActions: resolve/open.selector')
    else:
        if 'queryParameters' in check or 'navigationContext' in check:
            raise InvalidTestError('Параметры виджетов задаются в panel.parameters')
        plan = _panel(root, model, plain, e, check)
    return plan


def _actions(actions):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 40 or any(not isinstance(a,dict) for a in actions):
        raise InvalidTestError('Действия требуют 1..40 объектов')
    return actions


def _panel(root, model, plain, e, check):
    fact, positions = evidence(root, model, e, check['panel']['panel'])
    layout = e['properties'].get('Макет', {}); widgets = layout.get('Виджеты')
    if not isinstance(widgets,list) or not 1 <= len(widgets) <= 100:
        raise UnsupportedSyntaxError('Панель требует 1..100 виджетов')
    ids, reports, entries, plan = set(), [], [], None
    report_cache = {}
    parameters = check['panel'].get('parameters', {})
    if not isinstance(parameters,dict): raise InvalidTestError('panel.parameters требует словарь owner → queryParameters')
    for i,w in enumerate(widgets):
        if not isinstance(w,dict) or w.get('Вид') != 'Отчет' or not isinstance(w.get('Ид'),str) or not w['Ид'] or w['Ид'] in ids:
            raise UnsupportedSyntaxError('Панель требует уникальные Ид виджетов отчетов')
        ids.add(w['Ид']); report = resolve(model, e, w.get('Отчет'), {'Отчет'})
        if qualified(report) not in report_cache:
            report_cache[qualified(report)] = _report(root, model, {**plain, 'queryParameters': parameters.get(qualified(report), {})}, report, shared=plan)
        rp = report_cache[qualified(report)]
        if plan is None: plan = rp
        reports.append(copy(rp))
        entries.append({'id':w['Ид'], 'report':qualified(report), 'source':rp.report,
                        'yamlPath':['Макет','Виджеты',str(i)], 'position': positions.get(('Макет','Виджеты',str(i)))})
    if set(parameters) - {w['report'] for w in entries}: raise InvalidTestError('Параметры неизвестного отчета панели')
    placements = layout.get('НастройкиРасположения', {})
    if not isinstance(placements,dict): raise UnsupportedSyntaxError('НастройкиРасположения требуют объект')
    for device, values in placements.items():
        seen = set()
        if not isinstance(values,list): raise UnsupportedSyntaxError('Расположение требует массив')
        for p in values:
            if not isinstance(p,dict) or p.get('ИдВиджета') not in ids or p['ИдВиджета'] in seen:
                raise UnsupportedSyntaxError('Расположение с неизвестным/повторным виджетом')
            seen.add(p['ИдВиджета'])
            if any(type(p.get(k)) is not int or p[k] < (1 if k in {'Высота','Ширина'} else 0) for k in ('X','Y','Высота','Ширина')):
                raise UnsupportedSyntaxError('Расположение требует неотрицательные X/Y и положительные размеры')
    refresh = layout.get('АвтоматическоеОбновление', {'Использовать':False})
    if not isinstance(refresh,dict) or type(refresh.get('Использовать',False)) is not bool:
        raise UnsupportedSyntaxError('АвтоматическоеОбновление требует Булево Использовать')
    interval = None
    if refresh.get('Использовать',False):
        units = {'Миллисекунда':1,'Секунда':1000,'Минута':60000}
        if refresh.get('Вид') != 'Интервал' or refresh.get('ЕдиницаВремени') not in units or type(refresh.get('Значение')) is not int or refresh['Значение'] <= 0:
            raise UnsupportedSyntaxError('АвтоматическоеОбновление: положительный интервал')
        interval = refresh['Значение'] * units[refresh['ЕдиницаВремени']]
        if interval > 31536000000: raise UnsupportedSyntaxError('Лимит интервала обновления')
        if 'timers' not in check: raise InvalidTestError('Автообновление панели требует timers и управляемые часы')
    plan.check = plain
    plan.sequence = check.get('panelActions',[{'operation':'refresh'}])
    from .timer_events import prepare_timers, validate_step
    prepare_timers(plan)
    from .timer_events import generate_timer_types
    generate_timer_types(plan, plan.contracts)
    prepared = []
    for i,a in enumerate(_actions(plan.sequence)):
        if validate_step(a, plan.check): prepared.append(('timer',a)); continue
        if a == {'operation':'refresh'}: prepared.append(('refresh','')); continue
        if a.get('operation') == 'write' and not set(a) - {'operation','objects','registers'} and set(a)&{'objects','registers'}:
            config = plan.storage.config
            try:
                plan.storage.config = {**config,'initial':a.get('objects',[]),'initialRegisters':a.get('registers',[])}
                prepared.append(('write',plan.storage.setup(variable_prefix='ПанельЗапись'+str(i)+'_')))
            finally: plan.storage.config = config
        else: raise InvalidTestError('panelActions: refresh/write/шаг таймера')
    plan._surface_actions, plan._panel_reports = prepared, reports
    plan._panel_interval = interval
    entry = _entry(root, model, plain, e)
    plan.module, plan.original, plan.entry = entry.module, entry.original, entry.entry
    plan.query_entry = {}
    plan.report = {}
    plan.panel = {**fact,'schemaVersion':1,'widgets':entries,'positions':deepcopy(placements),
                  'refresh':{'intervalMs':interval,'policy':'fixed-rate-full-refresh'},
                  'dependencies':[rp.report for rp in reports], 'support':'executor-emulation',
                  'stages':{'validation':'ready','planning':'ready','generation':'ready','execution':'not-checked'},
                  'remaining':['Native dashboard rendering and pivot UI are external']}
    plan.queries = [q for rp in reports for q in rp.queries]
    return plan


def render_surface(plan, sandbox):
    from .session_contracts import render_session
    from .timer_events import render_event_step
    c = plan.contracts
    if plan.navigation:
        setup = ''
        invocation = '    знч Actions = новый Массив<Строка>()\n'
        for action_index, a in enumerate(plan._surface_actions):
            if a['operation'] == 'resolve':
                actual = {'operation':'resolve','routes':plan.navigation['routes'],'sections':plan.navigation['sections']}
                expression = c.literal(actual, 'Объект')
            else:
                route = a['route']
                outcome, target = 'Outcome'+str(action_index), 'Target'+str(action_index)
                invocation += ('    пер '+outcome+' = "blocked"\n    пер '+target+': Объект? = Неопределено\n'
                    '    если '+c.literal(route['visible'],'Булево')+' и '+c.literal(route['available'],'Булево')+'\n'
                    '        '+outcome+' = "opened-model"\n        '+target+' = '+c.literal(route['target'],'Объект')+'\n    ;\n')
                expression = '{"operation":"open", "outcome":'+outcome+', "routeId":'+c.literal(route['id'],'Строка')+', "target":'+target+'}'
            invocation += '    Actions.Добавить(СериализацияJson.ЗаписатьОбъект('+expression+'))\n'
        actual = '{"actions": Actions}'
    elif plan.report:
        from .source_query import render_source_query
        # Reuse query execution, write sequencing, resources and SQL publication.
        script = render_source_query(plan, sandbox)
        text = script.read_text()
        import re
        text = re.sub(r'\{"rows": (Строки\d+), "cardinality": \1\.Размер\(\), "columns": Выборка\d+\.ПолучитьОписанияКолонок\(\), "resource": Выборка\d+\.ТестНаблюдение\(\)\}',
                      lambda m: plan._report_read.replace('.Read()', '.Evaluate('+m[1]+')'), text)
        text = text.replace(', \"_snapshotMode\": \"sequence\"', ', \"_snapshotMode\": \"sequence\", \"_reportSnapshots\": \"report\"')
        script.write_text(text)
        return script
    else:
        setup = plan.storage.setup()
        invocation = '    знч Actions = новый Массив<Строка>()\n    пер Revision = 0\n    пер Widgets: Объект? = Неопределено\n'
        widget_expr = '{'+', '.join(c.literal(w['id'],'Строка')+': '+rp._report_read for w,rp in zip(plan.panel['widgets'],plan._panel_reports))+'}'
        refresh_code = '    Widgets = '+widget_expr+'\n    Revision += 1\n'
        invocation += refresh_code
        if plan._panel_interval is not None:
            setup += '    ТестТаймеры.ПодключитьОбработчикТаймера("panel-refresh", '+str(plan._panel_interval)+'мс, Ложь)\n'
        observation = '{"revision": Revision, "widgets": Widgets}'
        for i,(op,data) in enumerate(plan._surface_actions):
            if op == 'timer':
                code, events = render_event_step(plan,data,i,observation,False)
                event = 'СобытиеТаймера'+str(i)
                code = code.replace('        '+events+'.Добавить(',
                    '        если '+event+'.Обработчик == "panel-refresh"\n'+''.join('        '+line+'\n' for line in refresh_code.splitlines())+'        ;\n        '+events+'.Добавить(')
                invocation += code
            else:
                invocation += refresh_code if op == 'refresh' else data
            actual_action = '{"operation": '+c.literal(op,'Строка')+', "observation": '+observation
            if op == 'timer': actual_action += ', "events": '+events
            if plan.timers: actual_action += ', "timers": ТестТаймеры.Наблюдение(), "clock": ТестЧасы.Снимок()'
            invocation += '    Actions.Добавить(СериализацияJson.ЗаписатьОбъект('+actual_action+'}))\n'
        actual = '{"structure": '+c.literal({'widgets':[{'id':w['id'],'report':w['report']} for w in plan.panel['widgets']], 'positions':plan.panel['positions'],'refresh':plan.panel['refresh']},'Объект')+', "actions": Actions}'
    if plan.storage:
        if plan.storage.config.get('transaction'):
            raise UnsupportedSyntaxError('Panel requires ordinary isolated storage, without external transaction')
        if plan.storage.config.get('backend') == 'postgres':
            setup = '    ТестСессия.НастроитьSql(Путь)\n'+setup+'    ТестСессия.Опубликовать(Путь, Истина)\n'
            invocation += '    ТестСессия.Опубликовать(Путь)\n'
        invocation += '    ТестСессия.ПроверитьСессию()\n'
        actual = '{"result": '+actual+', "storage": ТестСессия.Аудит(Истина)}'
    imports = c.write(sandbox)
    session_setup, metadata = render_session(plan,sandbox)
    plan.rendered_session = session_setup, metadata
    metadata += ', "_snapshotMode": "sequence"'
    if plan.panel: metadata += ', "_reportSnapshots": "panel"'
    if plan.storage:
        from .storage_compilation import type_name
        identities = {'Scripts::'+type_name(c,t):qualified(c.canonical_elements[t.split('.')[0]])+'.'+t.partition('.')[2]
                      for t in c.fields if '.' in t and t.split('.')[0] in c.canonical_elements}
        metadata += ', "_typeIdentities": '+c.literal(identities,'Соответствие<Строка, Строка>')+', "_rawStorage": Истина, "storageTrace": ТестСессия.Трасса()'
    if plan.timers: invocation += '    ТестТаймеры.Закрыть()\n'
    statements = session_setup+setup+invocation+'    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": '+actual+metadata+'}))\n'
    body = '    попытка\n'+''.join('    '+line+'\n' for line in statements.splitlines())+'    поймать Недоступно: ИсключениеНеподдерживаемаяОперация\n        Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status":"UNSUPPORTED","message":Недоступно.Описание}))\n'
    if plan.timers: body += '    вконце\n        ТестТаймеры.Закрыть()\n'
    body += '    ;\n'
    path = sandbox/'test.sbsl'
    signature = 'Путь: Строка' if plan.storage and plan.storage.config.get('backend')=='postgres' else ''
    path.write_text(imports+'\nметод Скрипт('+signature+')\n'+body+';\n')
    return path
