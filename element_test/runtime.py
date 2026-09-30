"""Minimal XBSL → SBSL adapter. Student business logic is copied verbatim."""
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import time
import uuid

from .indexer import (IDENT, call_code, lex, method_binding_visible,
                      method_callable_binding_visible,
                      method_calls, method_call_expressions,
                      method_local_bindings, method_local_callable_bindings,
                      mask_noncode, parse_module, split_parameters)
from .resolution import (combined_library_symbols, import_specs, method_visible,
                         qualified, resolve_call_modules, resolve_symbols, visible_from)
from .model import select_check_project
from .generated_types import ProjectTypes
from .local_structures import scalar_structures
from .platform_mocks import PlatformMocks
from .yaml_io import InputError, InvalidTestError, UnsupportedSyntaxError
from .call_types import infer_receiver_type, known_receiver_type, receiver_object_modules

REPO = Path(__file__).resolve().parent.parent
# Runtime constructors that need no YAML contract. Keep this allowlist explicit.
RUNTIME_CONSTRUCTORS = {'ИсключениеНедопустимоеСостояние'}


def decode_output(text):
    """executor prints the entry method return value after its console output."""
    calls = []
    while text.startswith('ELEMENT_CALL '):
        text = text[len('ELEMENT_CALL '):]
        call, end = json.JSONDecoder().raw_decode(text)
        calls.append(call)
        text = text[end:].lstrip()
    value, end = json.JSONDecoder().raw_decode(text.lstrip())
    remainder = text.lstrip()[end:].strip()
    if remainder not in {"", "0", "1"}:
        raise InputError("Runtime вывел посторонние данные после JSON")
    if value.pop('_captureCalls', False):
        observe = value.pop('_observeCallArguments', None)
        if observe is not None:
            calls = [{**call, 'args': {k: v for k, v in call['args'].items() if k in observe}}
                     for call in calls]
        actual = value['actual']
        if value.pop('_captureException', False):
            actual = actual['result']
        actual['calls'] = calls
    return value


def execute_engine(command, model, assignment, temporary):
    result = subprocess.run([str(REPO / "bin/script-runtime"), str(REPO / "src/ДвижокТестирования.sbsl"),
                             command, str(model), str(assignment)], cwd=temporary,
                            env={**os.environ, "TMPDIR": str(temporary)}, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise InputError(f"Ошибка движка Скрипт: {result.stderr or result.stdout}")
    try:
        return decode_output(result.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InputError(f"Некорректный JSON движка: {result.stdout[:1000]}") from exc


def _selected_method(source, name, *, allow_void=False):
    methods, _, errors = parse_module(source)
    if errors:
        raise UnsupportedSyntaxError("Некорректный XBSL: " + "; ".join(errors))
    matches = [method for method in methods if method.name == name]
    if len(matches) != 1:
        raise InputError("Метод отсутствует или неоднозначен")
    method = matches[0]
    if method.end is None:
        raise UnsupportedSyntaxError("Не удалось определить границу метода")
    parameters = method.parameters(source)
    if any(":" not in p or "=" in p for p in parameters) or (method.return_span is None and not allow_void):
        raise InputError("Требуются явно типизированные параметры без значений по умолчанию и результат")
    return method, [p.split(":", 1)[1].strip() for p in parameters]


def extract_method(source, name, *, allow_void=False):
    method, types = _selected_method(source, name, allow_void=allow_void)
    return source[method.start:method.end], types



def method_closure(source, name):
    """Reachable same-module methods, including calls inside interpolation."""
    parsed, _, errors = parse_module(source)
    if errors:
        raise UnsupportedSyntaxError("Некорректный XBSL: " + "; ".join(errors))
    available = {method.name for method in parsed}
    visited, methods = set(), []
    def visit(current):
        if current in visited:
            return
        visited.add(current)
        method, parameters = extract_method(source, current, allow_void=True)
        methods.append((method, parameters))
        for owner, called in method_calls(method, parse_module(method)[0][0]):
            if owner is None and called in available:
                visit(called)
    visit(name)
    return methods


def project_method_closure(root, model, module, name):
    """Reachable methods across visible imported modules; preserve each source slice."""
    modules = model["modules"]
    project = model.get("properties", {})
    cache = {}
    parsed = {}
    def source_of(item):
        path = item["sourceFile"]
        if path not in cache:
            cache[path] = (root / path).read_text(encoding="utf-8-sig")
        return cache[path]
    def methods_of(item):
        path = item["sourceFile"]
        if path not in parsed:
            parsed[path] = parse_module(source_of(item))[0]
        return parsed[path]
    seen, result, aliases, rewrites, dependencies = set(), [], {}, {}, {}
    reserved = {m["name"] for m in modules} | {e["name"] for e in model.get("elements", [])}
    def alias_for(item):
        path = item["sourceFile"]
        if path not in aliases:
            number = len(aliases) + 1
            alias = f"ТестВнешнийМодуль{number}"
            while alias in reserved:
                number += 1
                alias = f"ТестВнешнийМодуль{number}"
            aliases[path] = alias
            reserved.add(alias)
        return aliases[path]
    def visit(item, current):
        key = (item["sourceFile"], current)
        if key in seen: return
        seen.add(key)
        source = source_of(item)
        node, types = _selected_method(source, current, allow_void=True)
        method = source[node.start:node.end]
        result.append((item, method, types))
        names = {m.name for m in methods_of(item)}
        imports = item.get("imports", [])
        bindings = method_local_bindings(source, node)
        callable_bindings = method_local_callable_bindings(source, node)
        visible_modules = [candidate for candidate in modules
                           if visible_from(candidate, item["namespace"], item.get("_libraryPrefix"))]
        def callable_names(candidate):
            return {method.name for method in methods_of(candidate)
                    if method_visible(method, item, candidate)}
        project_names = {method.name for candidate in visible_modules for method in methods_of(candidate)}
        for call in method_call_expressions(source, node):
            owner = None if call.receiver == "этот" else call.receiver
            called, owner_start, owner_end = call.name, call.receiver_start, call.receiver_end
            if call.kind == "computed":
                inferred = infer_receiver_type(source, node, call, item, model,
                                               parsed, cache, bindings)
                destinations = [candidate for candidate in receiver_object_modules(inferred, item, model)
                                if called in callable_names(candidate)] if inferred and called else []
                if len(destinations) == 1:
                    if destinations[0]['sourceFile'] != item['sourceFile']:
                        raise InputError("Внешний объектный метод требует собственный context")
                    visit(item, called)
                    continue
                if len(destinations) > 1:
                    raise InputError(f"Неоднозначный вызов: {called}")
                if not known_receiver_type(inferred, item, model) and (called is None or called in project_names):
                    raise UnsupportedSyntaxError(
                        f"Динамический вызов нельзя разрешить статически: {item['sourceFile']}:"
                        f"{source.count(chr(10), 0, call.start) + 1}")
                continue
            if owner and method_binding_visible(bindings, owner.split(".", 1)[0], owner_start):
                continue
            candidates = []
            if owner is None:
                if called in names:
                    candidates = [item]
                else:
                    for path, alias in import_specs(imports):
                        if alias: continue
                        imported = ([m for m in visible_modules if m["namespace"] == path]
                                    if any(m["namespace"] == path for m in visible_modules) else
                                    resolve_symbols(visible_modules, path, item["namespace"], imports, project))
                        candidates.extend(m for m in imported if called in callable_names(m))
            else:
                owners = resolve_call_modules(visible_modules, owner, item["namespace"], imports, project)
                if not owners and item.get("_libraryPrefix") and "::" in owner:
                    owners = resolve_call_modules(visible_modules, item["_libraryPrefix"] + "::" + owner,
                                                  item["namespace"], imports, project)
                candidates = [m for m in owners if called in callable_names(m)]
                if owners and not candidates and any(
                        called == n.name for m in owners for n in methods_of(m)):
                    raise InputError(f"Недоступный метод: {owner}.{called}")
                if not owners and any(called in {n.name for n in methods_of(m)} for m in
                                      resolve_call_modules(modules, owner, item["namespace"], imports, project)):
                    raise InputError(f"Недоступный модуль: {owner}")
            unique = {m["sourceFile"]: m for m in candidates}
            if (not unique and owner is None and method_binding_visible(bindings, called, call.start)
                    and not method_callable_binding_visible(callable_bindings, called, call.start)):
                raise UnsupportedSyntaxError(
                    f"Динамический вызов нельзя разрешить статически: {item['sourceFile']}:"
                    f"{source.count(chr(10), 0, call.start) + 1}")
            if len(unique) > 1:
                raise InputError(f"Неоднозначный вызов: {owner + '.' if owner else ''}{called}")
            if unique:
                other = next(iter(unique.values()))
                if other["sourceFile"] != item["sourceFile"]:
                    if other.get("moduleType") == "object":
                        raise InputError("Внешний объектный метод требует собственный context")
                    alias = alias_for(other)
                    dependencies.setdefault(item["sourceFile"], set()).add(other["sourceFile"])
                    if owner is not None:
                        rewrites.setdefault(key, []).append((owner_start - node.start,
                                                               owner_end - node.start, alias))
                visit(other, called)
    visit(module, name)
    active, complete = set(), set()
    def check_cycles(path):
        if path in active:
            raise InputError("Циклическая зависимость SBSL-модулей: " + path)
        if path in complete:
            return
        active.add(path)
        for dependency in dependencies.get(path, ()):
            check_cycles(dependency)
        active.remove(path)
        complete.add(path)
    check_cycles(module["sourceFile"])
    return result, aliases, rewrites, dependencies


def constructor_types(method):
    """Return constructor type token spans, including interpolation expressions.

    Only the token between `новый` and `(` is eligible for adaptation.
    This is deliberately not general expression or external-call resolution.
    """
    declaration = parse_module(method)[0][0]
    code = call_code(method)
    found = []
    for match in re.finditer(r'\bновый\s+', code[declaration.header_end:]):
        start = declaration.header_end + match.end()
        end = code.find('(', start)
        if end < 0:
            raise UnsupportedSyntaxError('Не удалось определить тип конструктора новый')
        token = code[start:end].rstrip()
        atom = rf'{IDENT}(?:::{IDENT})*(?:\.{IDENT})?'
        if not re.fullmatch(rf'{atom}(?:\s*<[^()\n]+>)?', token):
            raise UnsupportedSyntaxError('Неподдержанная форма типа конструктора: ' + token)
        found.append((start, start + len(token), re.sub(r'\s+', '', token)))
    return found


def body_type_references(method):
    """Type spans in reachable local declarations and casts, ignoring text/comments."""
    declaration = parse_module(method)[0][0]
    tokens = [token for token in lex(call_code(method)) if token.start >= declaration.header_end]
    references = []
    for index, token in enumerate(tokens):
        if token.value in {"пер", "знч"}:
            if index + 2 >= len(tokens) or not re.fullmatch(IDENT, tokens[index + 1].value) or tokens[index + 2].value != ":":
                continue
            first = index + 3
        elif token.value == "как":
            first = index + 1
        else:
            continue
        if first >= len(tokens):
            raise UnsupportedSyntaxError("Тип в теле метода не указан")
        depth, last = 0, first
        while last < len(tokens):
            value = tokens[last].value
            if value == "\n":
                if depth == 0:
                    break
                last += 1
                continue
            if value == "<":
                depth += 1
            elif value == ">":
                depth -= 1
                if depth < 0:
                    break
            elif value == "," and depth == 0:
                break
            elif not (re.fullmatch(IDENT, value) or value in {"::", ".", ",", "|", "?"}):
                break
            last += 1
        if last == first or depth != 0:
            raise UnsupportedSyntaxError("Некорректный тип в теле метода")
        ending = last - 1
        while ending >= first and tokens[ending].value == "\n":
            ending -= 1
        if ending < first:
            raise UnsupportedSyntaxError("Тип в теле метода не указан")
        start, end = tokens[first].start, tokens[ending].end
        references.append((start, end, re.sub(r"\s+", "", method[start:end])))
    return references


def _project_body_type(contracts, type_name):
    """Leave platform types to Script; resolve every project-shaped reference."""
    atoms = re.findall(rf"{IDENT}(?:::{IDENT})*(?:\.{IDENT})?", type_name)
    return any("::" in atom or "." in atom or contracts.resolve(atom) or atom in contracts.local_structures
               for atom in atoms if atom not in {"Массив", "Обходимое", "Соответствие"})


def prepare_script(root, model, check, sandbox):
    """Copy the original method and generate only its required data contracts."""
    source_model = model
    root, model = select_check_project(root, model, check)
    target = check.get("target", {})
    modules = [m for m in model["modules"] if m["name"] == target.get("module")
               and ("namespace" not in target or m["namespace"] == target["namespace"])]
    if len(modules) != 1:
        raise InvalidTestError("Целевой модуль отсутствует или неоднозначен")
    module = modules[0]
    original = (root / module["sourceFile"]).read_text(encoding="utf-8-sig")
    context = check.get("context")
    object_module = module.get("moduleType") == "object"
    is_object = "context" in check
    if is_object and not object_module:
        raise InvalidTestError("context допустим только для модуля Объект")
    if is_object and not isinstance(context, dict):
        raise InvalidTestError("Для объектного метода требуется context с начальными полями")
    method, types = extract_method(original, target.get("method"), allow_void=is_object)
    if not is_object and re.search(r"\bэтот\b", mask_noncode(method)):
        raise InvalidTestError("Для объектного метода требуется context с начальными полями")
    args = check.get("args", [])
    if len(args) != len(types):
        raise InvalidTestError("Количество аргументов не совпадает с сигнатурой")
    runtime_model = combined_library_symbols(root, model, source_model)
    contracts = ProjectTypes(runtime_model, module["namespace"], module.get("imports", []))
    contracts.local_structures = scalar_structures(original)
    if is_object:
        # Inline declarations inside generated object modules need their own scope contract.
        contracts.local_structures = {}
    contracts.local_source = contracts.current_source = module["sourceFile"]
    mocks = check.get("mocks", {})
    if not isinstance(mocks, dict) or set(mocks) - {"objects", "registers", "queries"}:
        raise InvalidTestError("Поддерживаются mocks.objects, mocks.registers, mocks.queries")
    contracts.configure_references(mocks.get("objects", {}))
    platform = PlatformMocks(contracts, mocks, check)
    if not isinstance(check.get('captureException', False), bool):
        raise InvalidTestError('captureException должен быть Булево')
    signature = parse_module(method)[0][0]
    return_type = signature.return_type(method)
    if not is_object and return_type == "ничто":
        raise InputError("Для метода без результата требуется объектный context")
    reachable, aliases, call_rewrites, module_dependencies = project_method_closure(
        root, runtime_model, module, target.get("method"))
    methods = [(m, types) for owner, m, types in reachable if owner["sourceFile"] == module["sourceFile"]]
    if not is_object and any(re.search(r"\bэтот\b", call_code(m)) for m, _ in methods):
        raise InvalidTestError("Для объектного метода требуется context с начальными полями")
    signature_types = []
    module_type_dependencies = {}
    for owner, dependency, parameter_types in reachable:
        contracts.current_source = owner["sourceFile"]
        contracts.namespace, contracts.imports = owner["namespace"], owner.get("imports", [])
        declaration = parse_module(dependency)[0][0]
        signature_types.extend(parameter_types)
        if declaration.return_type(dependency) and declaration.return_type(dependency) != "ничто":
            signature_types.append(declaration.return_type(dependency))
        for type_name in parameter_types + ([declaration.return_type(dependency)] if declaration.return_type(dependency) and declaration.return_type(dependency) != "ничто" else []):
            contracts.require(type_name)
            module_type_dependencies.setdefault(owner["sourceFile"], []).append(contracts.sbsl_type(type_name))
    body_types = []
    for owner, dependency, _ in reachable:
        contracts.current_source = owner["sourceFile"]
        contracts.namespace, contracts.imports = owner["namespace"], owner.get("imports", [])
        for _, _, type_name in constructor_types(dependency):
            if type_name in RUNTIME_CONSTRUCTORS:
                continue
            contracts.require(type_name)
            body_types.append(type_name)
            module_type_dependencies.setdefault(owner["sourceFile"], []).append(contracts.sbsl_type(type_name))
        for _, _, type_name in body_type_references(dependency):
            if _project_body_type(contracts, type_name):
                contracts.require(type_name)
                body_types.append(type_name)
                module_type_dependencies.setdefault(owner["sourceFile"], []).append(contracts.sbsl_type(type_name))
    adapted = []
    external = {}
    for owner, dependency, _ in reachable:
        contracts.current_source = owner["sourceFile"]
        contracts.namespace, contracts.imports = owner["namespace"], owner.get("imports", [])
        declaration = parse_module(dependency)[0][0]
        # Adapt declarations and resolved constructor type tokens only.
        replacements = [(*declaration.parameters_span, ",".join(
            p.partition(":")[0] + ":" + contracts.sbsl_type(p.partition(":")[2])
            for p in declaration.parameters(dependency)))]
        if declaration.return_span:
            replacements.append((*declaration.return_span, contracts.sbsl_type(declaration.return_type(dependency))))
        replacements.extend((start, end, contracts.sbsl_type(type_name))
                            for start, end, type_name in constructor_types(dependency)
                            if contracts.sbsl_type(type_name) != dependency[start:end])
        replacements.extend((start, end, contracts.sbsl_type(dependency[start:end]))
                            for start, end, type_name in body_type_references(dependency)
                            if _project_body_type(contracts, type_name)
                            and contracts.sbsl_type(dependency[start:end]) != dependency[start:end])
        replacements.extend(call_rewrites.get((owner["sourceFile"], declaration.name), []))
        for start, end, replacement in sorted(replacements, reverse=True):
            dependency = dependency[:start] + replacement + dependency[end:]
        compiled = platform.adapt(dependency)
        if owner["sourceFile"] == module["sourceFile"]:
            adapted.append(compiled)
        else:
            alias = aliases[owner["sourceFile"]]
            external.setdefault(alias, []).append(compiled)
    contracts.namespace, contracts.imports = module["namespace"], module.get("imports", [])
    contracts.current_source = module["sourceFile"]
    calls = platform.finish()
    method = "\n".join(list(contracts.required_structures.values()) + adapted)
    call = ", ".join(contracts.literal(v, t) for v, t in zip(args, types))
    setup = ""
    runtime_metadata = ''
    expression = target["method"] + '(' + call + ')'
    observed = "Контекст"
    if "observe" in check:
        fields = check["observe"]
        if not is_object or not isinstance(fields, list) or not fields or any(not isinstance(f, str) for f in fields):
            raise InvalidTestError("observe требует объектный context и непустой список полей")
        contracts.require(module["name"])
        known = {f["Имя"] for f in contracts.fields[module["name"]]}
        if len(set(fields)) != len(fields) or set(fields) - known:
            raise InvalidTestError("observe содержит неизвестные или повторяющиеся поля")
        observed = "{" + ", ".join(sbsl_literal(f, "Строка") + ": Контекст." + f for f in fields) + "}"
    if is_object:
        object_type = module["name"]
        if mocks.get('registers'):
            # A handler needs the register, whose dimensions need the owner's
            # reference. Put executable context in a separate module to keep
            # SBSL imports acyclic without changing the handler body.
            contracts.require(object_type)
            context_type = 'ТестКонтекст.Объект'
            contracts.fields[context_type] = contracts.fields[object_type]
            contracts.definitions[context_type] = contracts.definitions[object_type]
            object_type = context_type
        contracts.attach_method(object_type, "\n@Глобально\n".join(adapted), signature_types + body_types)
        contracts.method_dependencies[object_type].extend(
            [name + '.НаборЗаписей' for name in mocks.get('registers', [])]
            + (['ТестПлатформа.Запрос'] if mocks.get('queries') else []))
        setup = '    знч Контекст = ' + contracts.literal(context, object_type) + '\n'
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
    if return_type and return_type != "ничто":
        invocation = '    знч Результат = ' + expression + '\n'
        actual = '{"return": Результат, "context": ' + observed + '}' if is_object else 'Результат'
    else:
        invocation = '    ' + expression + '\n'
        actual = observed
    if platform.capture:
        actual = '{"context": ' + actual + ', "calls": ' + calls + '}'
    if check.get('captureException', False):
        if not is_object or (return_type and return_type != 'ничто'):
            raise InvalidTestError('captureException поддерживает только объектный метод без результата')
        invocation = ('    пер ИсключениеРезультат: Объект? = Неопределено\n    попытка\n'
                      + '    ' + invocation + '    поймать Ошибка: Исключение\n'
                      + '        ИсключениеРезультат = {"type": Ошибка.ПолучитьТип().ВСтроку(), "message": Ошибка.Описание}\n'
                      + '    ;\n')
        actual = '{"result": ' + actual + ', "exception": ИсключениеРезультат}'
    imports = contracts.write(sandbox)
    generated_owners = {name.split(".")[0] for name in contracts.definitions}
    for alias, bodies in external.items():
        if (sandbox / (alias + ".sbsl")).exists():
            raise InputError("Конфликт имени импортированного модуля: " + alias)
        path = next(path for path, name in aliases.items() if name == alias)
        needed = [f"#требуется {aliases[other]}.sbsl"
                  for other in sorted(module_dependencies.get(path, ()))]
        type_owners = set()
        for canonical in module_type_dependencies.get(path, ()):
            type_owners.update(re.findall(rf"({IDENT})\.", canonical))
            type_owners.update(enum for enum in contracts.enums
                               if re.search(rf"(?<!\w){re.escape(enum)}(?!\w)", canonical))
        needed.extend(f"#требуется {owner}.sbsl" for owner in sorted(type_owners & generated_owners))
        (sandbox / (alias + ".sbsl")).write_text(
            "\n".join(needed + ["@Глобально\n" + body for body in bodies]), encoding="utf-8")
    if external:
        imports = "\n".join(f"#требуется {alias}.sbsl" for alias in sorted(external)) + "\n" + imports
    if 'runtimeDateTime' in check and not is_object:
        raise InvalidTestError('runtimeDateTime требует объектный context')
    metadata = runtime_metadata
    if platform.capture:
        metadata += ', "_captureCalls": Истина, "_captureException": ' + ('Истина' if check.get('captureException') else 'Ложь')
        if platform.observe is not None:
            metadata += ', "_observeCallArguments": ' + contracts.literal(platform.observe, 'Массив<Строка>')
    script = sandbox / "test.sbsl"
    script.write_text(imports + "\n" + method + '\n\nметод Скрипт()\n' + setup + invocation
                      + '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": ' + actual + metadata + '}))\n;\n', encoding="utf-8")
    return script


def sbsl_literal(value, type_name):
    if type_name == "Строка" and isinstance(value, str):
        # Escape interpolation so teacher inputs cannot inject SBSL expressions.
        return json.dumps(value, ensure_ascii=False).replace("${", "\\${")
    if type_name == "Булево" and isinstance(value, bool):
        return "Истина" if value else "Ложь"
    if type_name == "Число" and isinstance(value, (int, float)) and not isinstance(value, bool):
        import math
        if math.isfinite(value):
            return str(value)
    raise InvalidTestError(f"Вход не соответствует типу {type_name}")


def run_pure(root, model, check, temporary):
    """Run one standalone method in Docker; return evidence, never award points."""
    def status(name, message, reason):
        return {"status": name, "message": message, "reasonCode": reason}

    if 'integration' in check:
        return status("UNSUPPORTED", "Интеграционный контракт требует отдельного run_integration и --integration", "unsupported_contract")
    if not shutil.which("docker"):
        return status("UNSUPPORTED", "Для runtime-тестов требуется Docker", "backend_unavailable")
    config_path = Path(os.environ.get("ELEMENT_TEST_RUNTIMES", REPO / "config/runtimes.json"))
    try:
        runtimes = json.loads(config_path.read_text(encoding="utf-8"))
        runtime = runtimes.get(model["compatibilityVersion"])
        if runtime is None:
            return status("UNSUPPORTED", "Для режима совместимости проекта не настроен runtime", "backend_unavailable")
    except (OSError, ValueError) as exc:
        return status("UNSUPPORTED", f"Не удалось прочитать конфигурацию runtime: {exc}", "backend_unavailable")
    container = "element-test-" + uuid.uuid4().hex
    process = None
    created = False
    try:
        sandbox = temporary / uuid.uuid4().hex
        sandbox.mkdir()
        generated = sandbox / "generated"
        generated.mkdir()
        script = prepare_script(root, model, check, generated)
        home = Path(os.environ.get("ELEMENT_SCRIPT_HOME", REPO / runtime["directory"])).resolve()
        image = os.environ.get("ELEMENT_TEST_DOCKER_IMAGE", runtime["image"])
        command = ["docker", "create", "--name", container, "--pull", "never",
                   "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--user", "65534:65534",
                   "--memory", "384m", "--memory-swap", "384m", "--cpus", "1", "--pids-limit", "64",
                   "--ulimit", "cpu=8:8", "--ulimit", "fsize=2097152:2097152", "--log-driver", "none",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--workdir", "/tmp",
                   "--mount", f"type=bind,source={home},target=/runtime,readonly",
                   "--mount", f"type=bind,source={script.parent},target=/generated,readonly", image,
                   "java", "-Xmx128m", "-XX:MaxMetaspaceSize=128m", "-XX:+UseSerialGC",
                   "--add-opens", "java.base/java.lang=ALL-UNNAMED", "--add-opens", "java.base/java.nio=ALL-UNNAMED",
                   "-Dfile.encoding=UTF-8", "-Djava.io.tmpdir=/tmp", "-Dlogs.root=/tmp",
                   "-Dlogback.configurationFile=/runtime/config/logback.xml", "-Dexecutor.location=/runtime",
                   "-cp", "/runtime/lib/*", "com.e1c.g5rt.executor.boot.ExecutorBootstrap",
                   "-c", model["compatibilityVersion"], "/generated/test.sbsl"]
        try:
            timeout = float(str(check.get("timeout", "5s")).removesuffix("s"))
        except ValueError:
            return status("ERROR", "timeout должен быть числом секунд", "invalid_test")
        if not 0 < timeout <= 30:
            return status("ERROR", "timeout должен быть в диапазоне (0, 30] секунд", "invalid_test")
        creation = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if creation.returncode:
            return status("UNSUPPORTED", creation.stderr[:4000], "backend_unavailable")
        created = True
        stdout_path, stderr_path = sandbox / "stdout", sandbox / "stderr"
        with stdout_path.open("w+b") as out, stderr_path.open("w+b") as err:
            process = subprocess.Popen(["docker", "start", "--attach", container], stdout=out, stderr=err)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if time.monotonic() > deadline:
                    return status("TIMEOUT", f"Превышен timeout {timeout}s", "timeout")
                if stdout_path.stat().st_size + stderr_path.stat().st_size > 1024 * 1024:
                    return status("ERROR", "Превышен лимит вывода runtime (1 MiB)", "execution_error")
                time.sleep(0.05)
            out.seek(0)
            err.seek(0)
            stdout, stderr = out.read(1024 * 1024).decode("utf-8", errors="replace"), err.read(1024 * 1024).decode("utf-8", errors="replace")
        if process.returncode in {125, 126, 127}:
            return status("UNSUPPORTED", (stderr or "Docker runtime недоступен")[:4000], "backend_unavailable")
        if process.returncode:
            return status("ERROR", (stderr or stdout or f"Код runtime: {process.returncode}")[:4000], "execution_error")
        try:
            decoded = decode_output(stdout)
        except (InputError, ValueError, KeyError, TypeError):
            return status("ERROR", "Runtime вернул некорректный результат", "execution_error")
        evidence = {"status": "EXECUTED", "actual": decoded['actual']}
        if 'runtimeDateTime' in check:
            evidence['runtimeDateTime'] = decoded.get('runtimeDateTime')
        return evidence
    except UnsupportedSyntaxError as exc:
        return status("UNSUPPORTED", str(exc), "unsupported_syntax")
    except InvalidTestError as exc:
        return status("UNSUPPORTED", str(exc), "invalid_test")
    except InputError as exc:
        return status("UNSUPPORTED", str(exc), "unsupported_contract")
    except subprocess.TimeoutExpired:
        return status("TIMEOUT", "Превышен timeout операции Docker", "timeout")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return status("ERROR", str(exc), "execution_error")
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
