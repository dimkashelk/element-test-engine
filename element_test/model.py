"""Serializable project IR and basic dependency diagnostics."""
from hashlib import sha256
import re

from .loader import files
from .indexer import index_module
from .types import parse_type, BUILTINS
from .yaml_io import InputError, load_yaml

ELEMENT_TYPES = {"Справочник", "Документ", "Перечисление", "НаборКонстант", "РегистрНакопления",
                 "РегистрСведений", "КомпонентИнтерфейса", "Модуль", "Подсистема"}
MEMBERS = ("Реквизиты", "Измерения", "Ресурсы", "Константы", "Элементы", "ТабличныеЧасти")


def validate_members(properties, source, diagnostics):
    for group in MEMBERS:
        members = properties.get(group, [])
        if not isinstance(members, list):
            raise InputError(f"{source}: {group} должен быть списком")
        names = set()
        for member in members:
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


def analyze(root):
    paths = sorted(files(root))
    project = load_yaml(root / "Проект.yaml", metadata=True)
    version = str(project.get("РежимСовместимости", ""))
    if not re.fullmatch(r"\d+\.\d+", version):
        raise InputError("Проект.yaml: некорректный РежимСовместимости")
    diagnostics, elements, modules, subsystems = [], [], [], []
    digest = sha256()
    for path in paths:
        if path.suffix not in {".yaml", ".xbsl"}:
            continue
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode() + b"\0" + content + b"\0")
        if path.suffix == ".xbsl":
            try:
                modules.append(index_module(path, root))
            except UnicodeError as exc:
                raise InputError(f"{relative}: ожидался UTF-8") from exc
            continue
        if path.name == "Проект.yaml":
            continue
        data = load_yaml(path, metadata=True)
        namespace = "::".join(path.relative_to(root).parts[:-1])
        if path.name == "Подсистема.yaml":
            subsystems.append({"name": path.parent.name, "namespace": namespace, "properties": data,
                               "sourceFile": relative})
            continue
        name, kind = data.get("Имя"), data.get("ВидЭлемента")
        if not isinstance(name, str) or not isinstance(kind, str):
            raise InputError(f"{relative}: требуются Имя и ВидЭлемента")
        validate_members(data, relative, diagnostics)
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
            "elements": elements, "modules": modules, "dependencies": dependencies,
            "callGraph": call_graph, "diagnostics": diagnostics}
