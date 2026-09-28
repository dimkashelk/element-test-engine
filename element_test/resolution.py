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


def resolve_symbols(items, name, namespace="", imports=(), project=None):
    """Return candidates in the strongest scope; never pick one arbitrarily."""
    name = _strip_project(name, project)
    specs = import_specs(imports)
    explicit = "::" in name
    for path, alias in specs:
        path = _strip_project(path, project)
        if alias and (name == alias or name.startswith(alias + "::")):
            name = path + name[len(alias):]
            explicit = True
            break
    if explicit:
        return [item for item in items if qualified(item) == name and
                (item.get("namespace", "") == namespace or item.get("visibility") != "ВПодсистеме")]
    local = [item for item in items if item["name"] == name and item.get("namespace", "") == namespace]
    if local:
        return local
    imported = []
    for path, alias in specs:
        path = _strip_project(path, project)
        for item in items:
            if item["name"] != name or item.get("visibility") == "ВПодсистеме":
                continue
            if path == qualified(item) or path == item.get("namespace"):
                imported.append(item)
    if imported:
        return list({item.get("sourceFile", qualified(item)): item for item in imported}.values())
    return [item for item in items if item["name"] == name and item.get("visibility") != "ВПодсистеме"]


def resolve_call_modules(modules, owner, namespace, imports=(), project=None):
    """Module qualification used by `Module.Method()` and import aliases."""
    specs = import_specs(imports)
    for path, alias in specs:
        if alias == owner:
            return resolve_symbols(modules, path, namespace, imports, project)
    return resolve_symbols(modules, owner, namespace, imports, project)
