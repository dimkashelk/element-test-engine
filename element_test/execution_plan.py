"""Internal execution IR shared by single and batch assessment.

The graph selects declarations, never schedules business calls. Renderer consumes
source slices from this plan; teacher expectations are deliberately absent.
"""
from dataclasses import dataclass, field, asdict
from hashlib import sha256
import json
import re

from .indexer import call_code, parse_module, method_call_expressions, method_local_callable_bindings
from .model import select_check_project
from .resolution import combined_library_symbols
from .yaml_io import InvalidTestError, UnsupportedSyntaxError


@dataclass(frozen=True)
class SymbolIdentity:
    provider: str
    project: str
    namespace: str
    owner: str
    declaration: str
    source_file: str
    version: str = ""

    def technical_name(self, prefix="ТестСимвол"):
        return prefix + sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class SourceSymbol:
    identity: SymbolIdentity
    owner: dict
    source: str
    parameter_types: list
    start: int
    end: int
    annotations: tuple = ()


@dataclass(frozen=True)
class CapabilityBinding:
    category: str
    operation: str
    owner: str
    adapter: str
    explanation: str
    source_file: str
    start: int
    end: int


class CapabilityRegistry:
    """Explicit native and metadata contracts, independent of business names."""
    native_owners = {"Консоль", "СериализацияJson", "Математика", "ДатаВремя", "Время",
                     "Дата", "Момент", "Ууид", "ЧасовойПояс", "Число", "Строка",
                     "Булево", "Тип", "Округление", "РежимОкругления"}
    native_functions = {"ТипЗнч"}
    native_methods = {
        "Размер", "Добавить", "ДобавитьВсе", "Удалить", "УдалитьВсе", "Очистить", "Получить",
        "Вставить", "ВставитьЕслиОтсутствует", "Содержит", "СодержитКлюч", "ПолучитьИлиУмолчание", "ПолучитьИлиНеопределено", "Ключи", "Значения",
        "Преобразовать", "Отфильтровать", "Отсортировать", "СоздатьКопию", "ВСтроку", "Представление",
        "Сократить", "СократитьСлева", "СократитьСправа", "Разделить", "Соединить",
        "Заменить", "Найти", "НачинаетсяС", "ЗаканчиваетсяНа", "Подстрока", "ВВерхнийРегистр",
        "ВНижнийРегистр", "ПолучитьТип", "ПолучитьОписание", "Форматировать", "Свернуть",
        "Любой", "Все", "Первый", "Последний", "Индекс", "ИндексПервого", "Округлить", "Пусто", "Длина", "Единственный", "ПолноеСовпадение", "Сумма", "СортироватьПо", "УпорядочитьПо", "Отобрать", "ЕдинственныйИлиНеопределено", "ВСоответствие", "ВМассив", "Выполнить"}
    metadata_operations = {
        "Справочник": {"СоздатьОбъект", "ПолучитьСсылку", "ЗагрузитьОбъект", "Записать"},
        "Документ": {"СоздатьОбъект", "ПолучитьСсылку", "ЗагрузитьОбъект", "Записать"},
        "РегистрСведений": {"ДобавитьЗапись", "Записать", "Установить"},
        "РегистрНакопления": {"ДобавитьЗапись", "Записать", "Установить"},
    }

    def bind(self, operation, owner, *, project=False, callback=False, metadata=None,
             backend=False, source_file="", start=0, end=0):
        if project:
            category, adapter, why = "project", "original-source", "Разрешённая исходная декларация"
        elif callback:
            category, adapter, why = "callable", "native", "Вызов объявленного функционального параметра/лямбды"
        elif metadata:
            if operation not in self.metadata_operations.get(metadata["elementType"], set()):
                raise UnsupportedSyntaxError(f"Неподдержанная операция {owner}.{operation}")
            from .resolution import qualified
            owner = qualified(metadata)
            category = "metadata"
            adapter = "storage-session" if backend else "explicit-fixture"
            why = "Контракт вида " + metadata["elementType"]
        elif owner in self.native_owners or operation in self.native_functions or owner == "typed-native":
            category, adapter, why = "native", "executor-9.0", "Native обращение; компиляция проверяет сигнатуру"
        else:
            raise UnsupportedSyntaxError(f"Неразрешённый достижимый вызов {owner + chr(46) if owner else chr(32)}{operation}: {source_file}")
        return CapabilityBinding(category, operation, owner or "", adapter, why, source_file, start, end)


@dataclass
class ExecutionPlan:
    root: object
    model: dict
    check: dict
    module: dict
    original: str
    entry: SourceSymbol
    symbols: list
    aliases: dict
    rewrites: dict
    module_dependencies: dict
    bindings: list = field(default_factory=list)
    observations: dict = field(default_factory=dict)
    declarations: dict = field(default_factory=dict)
    unavailable: list = field(default_factory=list)
    storage_elements: list = field(default_factory=list)
    sequence: list = field(default_factory=list)
    type_requirements: list = field(default_factory=list)
    contracts: object = None
    platform: object = None
    storage: object = None
    signature_types: list = field(default_factory=list)
    body_types: list = field(default_factory=list)
    module_type_dependencies: dict = field(default_factory=dict)
    source_transforms: dict = field(default_factory=dict)
    resources: list = field(default_factory=list)
    exception_types: set = field(default_factory=set)
    queries: list = field(default_factory=list)

    def to_dict(self):
        return {"schemaVersion": 1, "executorProfile": self.model.get("compatibilityVersion", "9.0"),
                "entry": asdict(self.entry.identity),
                "symbols": [{"identity": asdict(s.identity), "start": s.start, "end": s.end,
                             "source": s.source, "parameterTypes": s.parameter_types, "annotations": list(s.annotations)} for s in self.symbols],
                "dependencies": {k: sorted(v) for k, v in self.module_dependencies.items()},
                "bindings": [asdict(b) for b in self.bindings],
                "transforms": [{"sourceFile": path, "method": name, "spans": spans}
                               for (path, name), spans in self.rewrites.items()],
                "sourceTransforms": [{"sourceFile": path, "symbol": name, "spans": spans}
                                     for (path,name),spans in self.source_transforms.items() if spans],
                "resources": [asdict(r) for r in self.resources],
                "exceptionContracts": sorted(self.exception_types),
                "queries": self.queries,
                "observations": self.observations, "declarations": self.declarations,
                "typeRequirements": self.type_requirements, "unavailable": self.unavailable}

    def to_json(self):
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"


def plan_execution(root, source_model, check):
    from .runtime import extract_method, project_method_closure
    root, selected = select_check_project(root, source_model, check)
    if 'storage' in check and not isinstance(check['storage'], dict):
        raise InvalidTestError('storage должен быть объектом')
    if 'mocks' in check and not isinstance(check['mocks'], dict):
        raise InvalidTestError('mocks должен быть объектом')
    target = check.get("target", {})
    if not isinstance(target, dict) or not isinstance(target.get("method"), str):
        raise InvalidTestError("target требует module и method")
    modules = [m for m in selected["modules"] if m["name"] == target.get("module")
               and ("namespace" not in target or m["namespace"] == target["namespace"])]
    if len(modules) != 1:
        raise InvalidTestError("Целевой модуль отсутствует или неоднозначен")
    module = modules[0]
    original = (root / module["sourceFile"]).read_text(encoding="utf-8-sig")
    if "context" in check and (module.get("moduleType") != "object" or not isinstance(check["context"], dict)):
        raise InvalidTestError("context допустим только как объект для модуля Объект")
    method, types = extract_method(original, target["method"], allow_void=True)
    if "context" not in check and re.search(r"\bэтот\b", call_code(method)):
        raise InvalidTestError("Для объектного метода требуется context с начальными полями")
    if not isinstance(check.get("args", []), list) or len(check.get("args", [])) != len(types):
        raise InvalidTestError("Количество аргументов не совпадает с сигнатурой")
    runtime_model = combined_library_symbols(root, selected, source_model)
    reachable, aliases, rewrites, dependencies = project_method_closure(root, runtime_model, module, target["method"])
    sequence = check.get('sequence')
    if sequence is not None:
        if not isinstance(sequence, list) or not sequence or len(sequence) > 40:
            raise InvalidTestError('sequence требует 1..40 вызовов того же целевого модуля')
        all_reachable = {(o['sourceFile'], parse_module(t)[0][0].name): (o,t,p) for o,t,p in reachable}
        for step in sequence:
            if not isinstance(step, dict) or set(step) != {'method', 'args'} or not isinstance(step['args'], list):
                raise InvalidTestError('sequence вызов требует method и args')
            step_method, step_types = extract_method(original, step['method'], allow_void=True)
            if len(step['args']) != len(step_types):
                raise InvalidTestError('Количество аргументов sequence не совпадает с сигнатурой')
            more, step_aliases, step_rewrites, step_deps = project_method_closure(root, runtime_model, module, step['method'])
            aliases.update(step_aliases)
            rewrites.update(step_rewrites)
            for path, deps in step_deps.items():
                dependencies.setdefault(path, set()).update(deps)
            for owner, text, params in more:
                all_reachable[(owner['sourceFile'], parse_module(text)[0][0].name)] = (owner,text,params)
        reachable = list(all_reachable.values())
    # Contract handlers are additional roots; their bodies remain original declarations.
    if 'storage' in check:
        from .generated_types import ProjectTypes
        from .runtime import constructor_types, body_type_references
        pending = list(reachable)
        seen_methods = {(o['sourceFile'], parse_module(t)[0][0].name) for o,t,_ in reachable}
        visited_owners = set()
        while pending:
            owner, text, parameters = pending.pop()
            c = ProjectTypes(runtime_model, owner['namespace'], owner.get('imports', []))
            node = parse_module(text)[0][0]
            requirement_types = parameters + [node.return_type(text) or '']
            requirement_types += [t for _,_,t in constructor_types(text) + body_type_references(text)]
            for requirement in requirement_types:
                for type_owner in re.findall(r"([^\s<>,|?]+)\.(?:Объект|Ссылка|Данные)", requirement):
                    matches = c.resolve(type_owner)
                    if len(matches) != 1:
                        continue
                    element = matches[0]
                    identity = (element['namespace'], element['name'])
                    if identity in visited_owners:
                        continue
                    visited_owners.add(identity)
                    object_modules = [m for m in runtime_model['modules'] if m['namespace'] == element['namespace']
                                      and m['name'] == element['name'] + '.Объект']
                    if len(object_modules) != 1:
                        continue
                    objmod = object_modules[0]
                    source = (root / objmod['sourceFile']).read_text(encoding='utf-8-sig')
                    names = {n.name for n in parse_module(source)[0]}
                    for handler in sorted(names & {'ПередЗаписью', 'ПослеЗаписи'}):
                        more, more_aliases, more_rewrites, more_deps = project_method_closure(root, runtime_model, objmod, handler)
                        aliases.update(more_aliases)
                        rewrites.update(more_rewrites)
                        for path, deps in more_deps.items():
                            dependencies.setdefault(path, set()).update(deps)
                        for o,t,p in more:
                            key = o['sourceFile'], parse_module(t)[0][0].name
                            if key not in seen_methods:
                                seen_methods.add(key)
                                reachable.append((o,t,p))
                                pending.append((o,t,p))
    symbols = []
    original_nodes = {}
    for owner, text, parameters in reachable:
        node = parse_module(text)[0][0]
        path = owner['sourceFile']
        if path not in original_nodes:
            source = (root / path).read_text(encoding="utf-8-sig")
            original_nodes[path] = {n.name: n for n in parse_module(source)[0]}
        absolute = original_nodes[path][node.name]
        props = runtime_model.get("properties", {})
        library = next((item for item in source_model.get('libraries', [])
                        if owner.get('_libraryPrefix') == item['provider'] + '::' + item['name']), None)
        provider = library['provider'] if library else str(props.get('Поставщик', ''))
        project_name = library['name'] if library else str(props.get('Имя', ''))
        version = library['version'] if library else str(props.get('Версия') or '')
        identity = SymbolIdentity(provider, project_name, owner['namespace'], owner['name'], node.name,
                                  owner['sourceFile'], version)
        annotation_contract = {'Глобально', 'ВПроекте', 'ВПодсистеме', 'Локально', 'Обработчик',
                               'НаСервере', 'НаКлиенте', 'ИменованныеПараметры'}
        unsupported = set(absolute.annotations) - annotation_contract
        if unsupported:
            from .yaml_io import InputError
            raise InputError('Неподдержанная аннотация достижимого метода: ' + ', '.join(sorted(unsupported)))
        symbols.append(SourceSymbol(identity, owner, text, parameters, absolute.start, absolute.end, tuple(absolute.annotations)))
    plan = ExecutionPlan(root, runtime_model, check, module, original, symbols[0], symbols,
                         aliases, rewrites, dependencies,
                         observations={k: check[k] for k in ("observe", "captureCalls", "captureException", "trace") if k in check})
    if sequence:
        plan.sequence = sequence
    if not isinstance(check.get('trace', False), bool):
        raise InvalidTestError('trace должен быть Булево')
    if 'storage' in check:
        from .generated_types import ProjectTypes
        from .runtime import constructor_types, body_type_references
        c = ProjectTypes(runtime_model, module['namespace'], module.get('imports', []))
        requested = []
        for symbol in symbols:
            c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
            node = parse_module(symbol.source)[0][0]
            tokens = list(symbol.parameter_types) + [node.return_type(symbol.source) or '']
            tokens += [t for _, _, t in constructor_types(symbol.source) + body_type_references(symbol.source)]
            for token in tokens:
                for owner in re.findall(r"([^\s<>,|?]+)\.(?:Объект|Ссылка|Данные)", token):
                    for element in c.resolve(owner):
                        if element['elementType'] in {'Справочник', 'Документ'} and element not in requested:
                            requested.append(element)
        plan.storage_elements = requested
    from .declarations import declaration_closure
    plan.declarations = declaration_closure(root, runtime_model, reachable, aliases, rewrites, dependencies)
    from .source_contracts import bind_source_contracts, bind_system_ids
    bind_source_contracts(plan)
    registry = CapabilityRegistry()
    reachable_names = {s.identity.declaration for s in symbols}
    source_cache = {path: (root / path).read_text(encoding='utf-8-sig')
                    for path in dict.fromkeys(s.owner['sourceFile'] for s in symbols)}
    declaration_cache = {path: parse_module(source)[0] for path,source in source_cache.items()}
    for symbol in symbols:
        node = parse_module(symbol.source)[0][0]
        callable_bindings = method_local_callable_bindings(symbol.source, node)
        for call in method_call_expressions(symbol.source, node):
            if call.name is None:
                continue  # project closure rejects unproved dynamic destinations
            owner = call.receiver
            from .resolution import resolve_call_modules
            from .indexer import method_local_bindings, method_callable_binding_visible
            from .call_types import infer_receiver_type, receiver_object_modules
            bindings = method_local_bindings(symbol.source, node)
            inferred = infer_receiver_type(symbol.source, node, call, symbol.owner, runtime_model,
                                           declaration_cache, source_cache, bindings)
            destinations = receiver_object_modules(inferred, symbol.owner, runtime_model)
            if owner and not destinations:
                destinations = resolve_call_modules(runtime_model['modules'], owner, symbol.owner['namespace'],
                                                    symbol.owner.get('imports', []), runtime_model.get('properties'))
            project = ((owner is None and call.receiver_start is None and call.name in
                        {s.identity.declaration for s in symbols if s.identity.source_file == symbol.identity.source_file})
                       or any(s.owner['sourceFile'] == dest['sourceFile'] and s.identity.declaration == call.name
                              for dest in destinations for s in symbols))
            callback = owner is None and method_callable_binding_visible(callable_bindings, call.name, call.start)
            if not project and owner in {'Транзакции', 'Стд::БазаДанных::Транзакции'}:
                continue  # Bound to verified resource/API spans by bind_source_contracts.
            receiver = owner
            if not project and call.receiver_start is not None and (inferred or call.name in registry.native_methods):
                receiver = 'typed-native'
            metadata = None
            if not project and owner and call.name in {'ПолучитьСсылку', 'СоздатьОбъект'}:
                from .generated_types import ProjectTypes
                lookup = ProjectTypes(runtime_model, symbol.owner['namespace'], symbol.owner.get('imports', []))
                candidates = lookup.resolve(owner)
                if len(candidates) == 1 and candidates[0]['elementType'] in {'Справочник', 'Документ'}:
                    if 'storage' not in check:
                        from .yaml_io import InputError
                        raise InputError('Операция менеджера требует storage')
                    metadata = candidates[0]
                    if metadata not in plan.storage_elements:
                        plan.storage_elements.append(metadata)
            if inferred and call.name in {'ЗагрузитьОбъект', 'Записать', 'Установить', 'ДобавитьЗапись'}:
                from .generated_types import ProjectTypes
                c = ProjectTypes(runtime_model, symbol.owner['namespace'], symbol.owner.get('imports', []))
                matches = c.resolve(inferred.rstrip('?').partition('.')[0])
                if len(matches) == 1 and matches[0]['elementType'] in {'РегистрСведений', 'РегистрНакопления'}:
                    metadata = matches[0]
                    allowed = check.get('mocks', {}).get('registers', []) + check.get('storage', {}).get('registers', [])
                    from .resolution import qualified
                    if matches[0]['name'] not in allowed and qualified(matches[0]) not in allowed:
                        from .yaml_io import InputError
                        raise InputError('Операция регистра требует явный контракт')
                if len(matches) == 1 and matches[0]['elementType'] in {'Справочник', 'Документ'}:
                    metadata = matches[0]
                    if 'storage' not in check and not (call.name == 'ЗагрузитьОбъект' and check.get('mocks', {}).get('objects')):
                        from .yaml_io import InputError
                        raise InputError('Операция объекта требует явный storage контракт')
            if call.name in {'ПолучитьСсылку', 'СоздатьОбъект'} and owner is None:
                matches = [e for e in runtime_model['elements'] if e['name'] == symbol.owner['name']
                           and e['namespace'] == symbol.owner['namespace']]
                if len(matches) == 1 and matches[0]['elementType'] in {'Справочник', 'Документ'}:
                    if 'storage' not in check:
                        from .yaml_io import InputError
                        raise InputError('Операция менеджера с Ууид/хранением требует явный storage контракт')
                    metadata = matches[0]
            plan.bindings.append(registry.bind(call.name, receiver, project=project, callback=callback, metadata=metadata, backend="storage" in check,
                                  source_file=symbol.identity.source_file,
                                  start=symbol.start + call.start, end=symbol.start + call.end))
    bind_types(plan)
    from .storage_queries import bind_queries
    bind_queries(plan)
    bind_system_ids(plan)
    return plan


def bind_types(plan):
    """Close and validate declarations before renderer writes any source files."""
    from .generated_types import ProjectTypes
    from .local_structures import scalar_structures
    from .platform_mocks import PlatformMocks
    from .runtime import constructor_types, body_type_references, _project_body_type, RUNTIME_CONSTRUCTORS
    c = ProjectTypes(plan.model, plan.module['namespace'], plan.module.get('imports', []))
    c.rename_collisions = True
    c.inline_locals = 'context' not in plan.check
    c.local_source = c.current_source = plan.module['sourceFile']
    c.local_structures = scalar_structures(plan.original)
    c.local_by_source = {path: scalar_structures((plan.root / path).read_text(encoding='utf-8-sig'))
                         for path in dict.fromkeys(s.owner['sourceFile'] for s in plan.symbols)}
    from .source_contracts import EXCEPTION_OWNER
    c.platform_type_aliases = {typ: EXCEPTION_OWNER + '.' + typ for typ in plan.exception_types}
    for typ, canonical in list(c.platform_type_aliases.items()):
        c.definitions[canonical] = '@Глобально\nисключение ' + typ + '\n;\n'
        c.platform_type_aliases['Стд::' + typ] = canonical
    mocks = plan.check.get('mocks', {})
    if set(mocks) - {'objects', 'registers', 'queries'}:
        raise InvalidTestError('Поддерживаются mocks.objects, mocks.registers, mocks.queries')
    if 'storage' in plan.check:
        from .storage import MetadataStorage
        if mocks:
            raise InvalidTestError('storage и mocks требуют разных сценариев')
        plan.storage = MetadataStorage(c, plan.check['storage'])
    c.configure_references(mocks.get('objects', {}))
    plan.platform = PlatformMocks(c, mocks, plan.check)
    plan.platform.storage_mode = plan.storage is not None
    for symbol in plan.symbols:
        c.current_source = symbol.owner['sourceFile']
        c.namespace, c.imports = symbol.owner['namespace'], symbol.owner.get('imports', [])
        node = parse_module(symbol.source)[0][0]
        types = symbol.parameter_types + ([node.return_type(symbol.source)] if node.return_type(symbol.source) not in {None,'ничто'} else [])
        plan.signature_types.extend(types)
        body = [t for _,_,t in constructor_types(symbol.source) if t not in RUNTIME_CONSTRUCTORS]
        body += [t for _,_,t in body_type_references(symbol.source) if _project_body_type(c,t)]
        plan.body_types.extend(body)
        for typ in types + body:
            c.require(typ)
            canonical = c.sbsl_type(typ)
            plan.module_type_dependencies.setdefault(symbol.owner['sourceFile'], []).append(canonical)
            plan.type_requirements.append({'type': typ, 'canonical': canonical,
                'sourceFile': symbol.owner['sourceFile'], 'symbol': symbol.identity.declaration})
    # Field dependencies are generated recursively from metadata, never from business names.
    for typ,fields in c.fields.items():
        for f in fields:
            plan.type_requirements.append({'owner': typ, 'field': f['Имя'], 'type': f['Тип']})
    plan.contracts = c
