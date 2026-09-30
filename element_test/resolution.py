"""Namespace/import aware symbol lookup shared by indexing and runtime adaptation."""
import re

from .yaml_io import InputError

_IDENT = r"[^\W\d]\w*"
_IMPORT = re.compile(rf"\s*({_IDENT}(?:(?:::|\.){_IDENT})*)\s*(?:как\s+({_IDENT}))?\s*\Z")


def import_specs(imports):
    result = []
    for raw in imports:
        match = _IMPORT.fullmatch(raw)
        if not match:
            raise InputError("Некорректный импорт: " + raw)
        result.append((match[1], match[2]))
    return result


def _strip_project(name, project):
    props = project or {}
    supplier, title = props.get("Поставщик"), props.get("Имя")
    if supplier and title and name.startswith(f"{supplier}::{title}::"):
        return name[len(supplier)+len(title)+4:]
    return name


def qualified(item):
    return "::".join(filter(None, (item.get("namespace", ""), item["name"])))


def same_subsystem(left, right, library_prefix=None):
    """Packages share the visibility scope of their containing subsystem."""
    if library_prefix:
        prefix = library_prefix + "::"
        if not left.startswith(prefix) or not right.startswith(prefix):
            return False
        left, right = left[len(prefix):], right[len(prefix):]
    if left == right:
        return True
    return bool(left and right and left.split("::", 1)[0] == right.split("::", 1)[0])


def visible_from(item, namespace, library_prefix=None):
    """Project metadata visibility for a caller's namespace and project."""
    same_project = item.get("_libraryPrefix") == library_prefix
    if same_project and same_subsystem(item.get("namespace", ""), namespace, library_prefix):
        return True
    scope = item.get("visibility", "ВПроекте")
    if same_project:
        return scope in {"ВПроекте", "Глобально"}
    return scope == "Глобально"


def method_visible(method, source, destination):
    if source["sourceFile"] == destination["sourceFile"]:
        return True
    annotations = set(method.annotations)
    if "Локально" in annotations:
        return False
    same_project = source.get("_libraryPrefix") == destination.get("_libraryPrefix")
    if not same_project:
        return "Глобально" in annotations
    if not same_subsystem(source.get("namespace", ""), destination.get("namespace", ""),
                          source.get("_libraryPrefix")):
        return "ВПодсистеме" not in annotations
    return True


def resolve_symbols(items, name, namespace="", imports=(), project=None):
    """Return candidates in the strongest scope; never pick one arbitrarily."""
    name = _strip_project(name, project)
    specs = import_specs(imports)
    explicit = "::" in name
    def in_scope(item):
        return (item.get("visibility") != "ВПодсистеме" or
                same_subsystem(item.get("namespace", ""), namespace, item.get("_libraryPrefix")))
    for path, alias in specs:
        path = _strip_project(path, project)
        if alias and (name == alias or name.startswith(alias + "::")):
            name = path + name[len(alias):]
            explicit = True
            break
    if explicit:
        return [item for item in items if qualified(item) == name and
                in_scope(item)]
    local = [item for item in items if item["name"] == name and item.get("namespace", "") == namespace]
    if local:
        return local
    imported = []
    for path, alias in specs:
        path = _strip_project(path, project)
        for item in items:
            if item["name"] != name or not in_scope(item):
                continue
            if path == qualified(item) or path == item.get("namespace"):
                imported.append(item)
    if imported:
        return list({item.get("sourceFile", qualified(item)): item for item in imported}.values())
    return [item for item in items if item["name"] == name and in_scope(item)]


def resolve_call_modules(modules, owner, namespace, imports=(), project=None):
    """Module qualification used by `Module.Method()` and import aliases."""
    specs = import_specs(imports)
    for path, alias in specs:
        if alias == owner:
            return resolve_symbols(modules, path, namespace, imports, project)
    return resolve_symbols(modules, owner, namespace, imports, project)


def combined_library_symbols(root, model, source_model):
    """Add declared library symbols under their provider and project identity."""
    libraries = source_model.get("libraries", [])
    if not libraries:
        return model
    combined = {**model, "modules": list(model["modules"]),
                "elements": list(model["elements"])}
    prefixes = [f'{library["provider"]}::{library["name"]}' for library in libraries]
    combined["_libraryPrefixes"] = prefixes
    for library, prefix in zip(libraries, prefixes):
        directory = library["sourceDirectory"]
        library_root = root.parent / directory
        if not library_root.is_dir() or library_root.is_symlink() or library_root.resolve().parent != root.parent.resolve():
            raise InputError("Каталог объявленной библиотеки недоступен")
        if library_root.resolve() == root.resolve():
            continue
        library_model = library["model"]
        for element in library_model["elements"]:
            combined["elements"].append({**element, "namespace": "::".join(filter(None,
                                          (prefix, element["namespace"]))), "_libraryPrefix": prefix})
        for module in library_model["modules"]:
            imports = []
            for path, alias in import_specs(module.get("imports", [])):
                if not any(path == known or path.startswith(known + "::") for known in prefixes):
                    path = prefix + "::" + path
                imports.append(path + (" как " + alias if alias else ""))
            combined["modules"].append({**module,
                "namespace": "::".join(filter(None, (prefix, module["namespace"]))),
                "sourceFile": "../" + directory + "/" + module["sourceFile"],
                "imports": imports, "_libraryPrefix": prefix})
    return combined
