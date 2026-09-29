"""Serializable project IR and basic dependency diagnostics."""
from hashlib import sha256
import re

from .loader import files
from .indexer import index_module
from .types import parse_type, BUILTINS
from .yaml_io import InputError, InvalidTestError, load_yaml

ELEMENT_TYPES = {"Справочник", "Документ", "Перечисление", "НаборКонстант", "РегистрНакопления",
                 "РегистрСведений", "КомпонентИнтерфейса", "Модуль", "Подсистема"}
MEMBERS = ("Реквизиты", "Измерения", "Ресурсы", "Константы", "Элементы", "ТабличныеЧасти")
COMMAND_ELEMENTS = {"ФрагментКомандногоИнтерфейса"}


def validate_members(properties, source, diagnostics, kind=None):
    for group in MEMBERS:
        members = properties.get(group, [])
        if not isinstance(members, list):
            raise InputError(f"{source}: {group} должен быть списком")
        names = set()
        for member in members:
            if group == "Элементы" and kind in COMMAND_ELEMENTS:
                if isinstance(member, str) and member.startswith("="):
                    continue
                if not isinstance(member, dict) or not isinstance(member.get("Тип"), str):
                    raise InputError(f"{source}: элемент {group} должен иметь Тип")
                validate_members(member, source, diagnostics, kind)
                continue
            if not isinstance(member, dict) or not isinstance(member.get("Имя"), str):
                raise InputError(f"{source}: элемент {group} должен иметь Имя")
            if member["Имя"] in names:
                diagnostics.append({"code": "duplicate_member", "sourceFile": source,
                                    "message": f"Повторяющееся имя в {group}: {member['Имя']}"})
            names.add(member["Имя"])
            if group == "ТабличныеЧасти":
                validate_members(member, source, diagnostics)


def resolve(elements, name, namespace):
    if "::" in name:
        return [e for e in elements if "::".join(filter(None, [e["namespace"], e["name"]])) == name]
    candidates = [e for e in elements if e["name"] == name]
    local = [e for e in candidates if e["namespace"] == namespace]
    return local or candidates


def _analyze_single(root):
    paths = sorted(files(root))
    project = load_yaml(root / "Проект.yaml", metadata=True)
    version = str(project.get("РежимСовместимости", ""))
    if not re.fullmatch(r"\d+\.\d+", version):
        raise InputError("Проект.yaml: некорректный РежимСовместимости")
    diagnostics, elements, modules, subsystems, service_files = [], [], [], [], []
    digest = sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode() + b"\0" + content + b"\0")
        if path.suffix not in {".yaml", ".xbsl"}:
            continue
        if path.suffix == ".xbsl":
            try:
                modules.append(index_module(path, root))
            except UnicodeError as exc:
                raise InputError(f"{relative}: ожидался UTF-8") from exc
            continue
        if path.name == "Проект.yaml":
            continue
        try:
            data = load_yaml(path, metadata=True)
        except InputError as exc:
            raise InputError(f"{relative}: {exc}") from exc
        namespace = "::".join(path.relative_to(root).parts[:-1])
        if path.name == "Подсистема.yaml":
            subsystems.append({"name": path.parent.name, "namespace": namespace, "properties": data,
                               "sourceFile": relative})
            continue
        if path.name in {"Ресурсы.yaml", "ЛокализованныеСтроки.yaml"}:
            service_files.append({"kind": path.stem, "sourceFile": relative, "properties": data})
        if path.name == "Ресурсы.yaml" or (path.name == "ЛокализованныеСтроки.yaml"
                                            and "ВидЭлемента" not in data):
            continue
        name, kind = data.get("Имя"), data.get("ВидЭлемента")
        if not isinstance(name, str) or not isinstance(kind, str):
            raise InputError(f"{relative}: требуются Имя и ВидЭлемента")
        validate_members(data, relative, diagnostics, kind)
        if kind not in ELEMENT_TYPES:
            diagnostics.append({"code": "unsupported_element", "severity": "warning", "sourceFile": relative,
                                "message": f"Вид элемента пока не поддерживается: {kind}"})
        elements.append({"name": name, "elementType": kind, "id": data.get("Ид"),
                         "namespace": namespace, "visibility": data.get("ОбластьВидимости", "ВПодсистеме"),
                         "sourceFile": relative, "properties": data})
    for module in modules:
        owner = module["name"].split(".")[0]
        matching = [e for e in elements if e["namespace"] == module["namespace"] and e["name"] == owner]
        module["visibility"] = matching[0]["visibility"] if len(matching) == 1 else "ВПроекте"
        for error in module.get("parseErrors", []):
            diagnostics.append({"code": "invalid_xbsl", "sourceFile": module["sourceFile"], "message": error})
    identities = set()
    dependencies = []
    for element in elements:
        key = (element["namespace"], element["name"])
        if key in identities:
            diagnostics.append({"code": "duplicate_element", "sourceFile": element["sourceFile"],
                                "message": f"Повторяющееся имя объекта: {element['name']}"})
        identities.add(key)
        containers = [element["properties"]] + element["properties"].get("ТабличныеЧасти", [])
        for container in containers:
            for group in ("Реквизиты", "Измерения", "Ресурсы", "Константы"):
                for member in container.get(group, []):
                    if "Тип" not in member:
                        continue  # System fields may have implicit platform types.
                    try:
                        _, references = parse_type(member["Тип"])
                        for reference in sorted(references):
                            if reference in BUILTINS:
                                continue
                            matches = resolve(elements, reference, element["namespace"])
                            dependencies.append({"from": "::".join(filter(None, key)),
                                                 "field": member["Имя"], "to": reference})
                            if len(matches) != 1:
                                diagnostics.append({"code": "unresolved_type", "sourceFile": element["sourceFile"],
                                                    "message": f"{element['name']}.{member['Имя']}: тип {reference} не найден или неоднозначен"})
                            elif matches[0]["namespace"] != element["namespace"] and matches[0]["visibility"] == "ВПодсистеме":
                                diagnostics.append({"code": "inaccessible_type", "sourceFile": element["sourceFile"],
                                                    "message": f"{element['name']}.{member['Имя']}: {reference} недоступен вне своей подсистемы"})
                    except ValueError as exc:
                        diagnostics.append({"code": "invalid_type", "sourceFile": element["sourceFile"], "message": str(exc)})
    from .call_graph import build_call_graph
    call_graph, call_diagnostics = build_call_graph(root, {"modules": modules, "properties": project})
    diagnostics.extend(call_diagnostics)
    return {"schemaVersion": 1, "name": project.get("Имя", root.name), "compatibilityVersion": version,
            "sourceHash": digest.hexdigest(), "properties": project, "subsystems": subsystems,
            "elements": elements, "modules": modules, "serviceFiles": service_files,
            "dependencies": dependencies,
            "callGraph": call_graph, "diagnostics": diagnostics}


def _declared_libraries(root, project):
    declarations = project.get("Библиотеки", [])
    if not isinstance(declarations, list):
        raise InputError("Проект.yaml: Библиотеки должны быть списком")
    if not declarations:
        return []
    siblings = []
    for candidate in sorted(root.parent.iterdir()):
        if candidate == root or not candidate.is_dir() or candidate.is_symlink():
            continue
        metadata = candidate / "Проект.yaml"
        if metadata.is_file() and not metadata.is_symlink():
            try:
                siblings.append((candidate, load_yaml(metadata, metadata=True)))
            except InputError as exc:
                raise InputError(f"{candidate.name}/Проект.yaml: {exc}") from exc
    selected, seen = [], set()
    for declaration in declarations:
        if not isinstance(declaration, dict) or any(not isinstance(declaration.get(key), str)
                                                     or not declaration[key]
                                                     for key in ("Поставщик", "Имя", "Версия")):
            raise InputError("Проект.yaml: библиотека требует Поставщик, Имя и Версия")
        identity = tuple(declaration[key] for key in ("Поставщик", "Имя", "Версия"))
        if identity in seen:
            raise InputError(f"Проект.yaml: повторная зависимость {identity[0]}::{identity[1]} {identity[2]}")
        seen.add(identity)
        exact = [(path, meta) for path, meta in siblings
                 if tuple(meta.get(key) for key in ("Поставщик", "Имя", "Версия")) == identity
                 and meta.get("ВидПроекта") == "Библиотека"]
        if len(exact) != 1:
            candidates = [(path, meta) for path, meta in siblings
                          if meta.get("Поставщик") == identity[0] and meta.get("Имя") == identity[1]]
            reason = "неоднозначна" if len(exact) > 1 else ("не совпадает по версии или виду" if candidates else "не найдена")
            raise InputError(f"Проект.yaml: библиотека {identity[0]}::{identity[1]} {identity[2]} {reason}")
        selected.append(exact[0][0])
    return selected


def analyze(root):
    model = _analyze_single(root)
    project = model["properties"]
    model["projectIdentity"] = {key: project.get(key) for key in ("Поставщик", "Имя", "Версия", "ВидПроекта")}
    libraries, loaded, visiting = [], set(), {root}

    def visit(parent_root, parent_project):
        for library_root in _declared_libraries(parent_root, parent_project):
            if library_root in visiting:
                raise InputError(f"Циклическая зависимость библиотек: {library_root.name}")
            if library_root in loaded:
                continue
            try:
                library = _analyze_single(library_root)
            except InputError as exc:
                raise InputError(f"{library_root.name}: {exc}") from exc
            metadata = library["properties"]
            libraries.append({"provider": metadata["Поставщик"], "name": metadata["Имя"],
                              "version": metadata["Версия"], "kind": metadata["ВидПроекта"],
                              "sourceHash": library["sourceHash"],
                              "sourceDirectory": library_root.name, "model": library})
            loaded.add(library_root)
            for diagnostic in library["diagnostics"]:
                model["diagnostics"].append({**diagnostic,
                                             "sourceFile": f'{metadata["Поставщик"]}::{metadata["Имя"]}/{diagnostic["sourceFile"]}'})
            visiting.add(library_root)
            visit(library_root, metadata)
            visiting.remove(library_root)

    visit(root, project)
    model["libraries"] = libraries
    if libraries:
        digest = sha256(b"element-test-source-bundle-v1\0" + model["sourceHash"].encode())
        for library in libraries:
            for key in ("provider", "name", "version", "kind", "sourceHash"):
                digest.update(str(library[key]).encode() + b"\0")
        model["sourceHash"] = digest.hexdigest()
    return model


def select_check_project(root, model, check):
    """Resolve an explicitly named library for one check without mixing project models."""
    selector = check.get("library")
    if selector is None:
        return root, model
    if not isinstance(selector, dict) or set(selector) != {"provider", "name", "version"} or any(
            not isinstance(value, str) or not value for value in selector.values()):
        raise InvalidTestError("library требует provider, name и version")
    matches = [item for item in model.get("libraries", [])
               if all(item[key] == selector[key] for key in ("provider", "name", "version"))]
    if len(matches) != 1:
        raise InvalidTestError(f'Библиотека {selector["provider"]}::{selector["name"]} {selector["version"]} не найдена или неоднозначна')
    selected = matches[0]
    library_root = root.parent / selected["sourceDirectory"]
    if (not library_root.is_dir() or library_root.is_symlink()
            or library_root.resolve().parent != root.parent.resolve()):
        raise InvalidTestError("Каталог выбранной библиотеки недоступен")
    return library_root, selected["model"]
