"""Conservative cross-module call graph over parsed method declarations."""
from .indexer import (method_binding_visible, method_callable_binding_visible,
                      method_call_expressions, method_local_bindings,
                      method_local_callable_bindings, parse_module)
from .resolution import (import_specs, method_visible, qualified, resolve_call_modules,
                         resolve_symbols, visible_from)
from .yaml_io import InputError
from .call_types import infer_receiver_type, known_receiver_type, receiver_object_modules


def platform_local_call(module, called, elements):
    """Inherited metadata-manager methods precede imported free methods."""
    if called != "Удалить" or module["name"].endswith(".Объект"):
        return False
    return any(item["name"] == module["name"] and item["namespace"] == module["namespace"]
               and item["elementType"] == "РегистрСведений" for item in elements)


def build_call_graph(root, model):
    modules = model["modules"]
    project = model.get("properties", {})
    sources = {}
    declarations = {}
    for module in modules:
        source = (root / module["sourceFile"]).read_text(encoding="utf-8-sig")
        sources[module["sourceFile"]] = source
        parsed, _, _ = parse_module(source)
        declarations[module["sourceFile"]] = parsed
    edges, diagnostics = [], []
    for module in modules:
        source_file = module["sourceFile"]
        source = sources[source_file]
        current = declarations[source_file]
        local_names = {m.name for m in current}
        imports = module.get("imports", [])
        try:
            specs = import_specs(imports)
        except InputError as exc:
            diagnostics.append({"code": "unsupported_import", "sourceFile": source_file,
                                "message": str(exc)})
            continue
        for method in current:
            if method.end is None: continue
            bindings = method_local_bindings(source, method)
            callable_bindings = method_local_callable_bindings(source, method)
            visible_modules = [candidate for candidate in modules
                               if visible_from(candidate, module["namespace"], module.get("_libraryPrefix"))]
            def callable_names(candidate):
                return {name.name for name in declarations[candidate["sourceFile"]]
                        if method_visible(name, module, candidate)}
            project_methods = {name.name for candidate in visible_modules
                               for name in declarations[candidate["sourceFile"]]}
            for call in method_call_expressions(source, method):
                owner, called, start = (None if call.receiver == "этот" else call.receiver), call.name, call.receiver_start
                if call.kind == "computed":
                    inferred = infer_receiver_type(source, method, call, module, model,
                                                   declarations, sources, bindings)
                    destinations = [candidate for candidate in receiver_object_modules(inferred, module, model)
                                    if called in callable_names(candidate)] if inferred and called else []
                    if len(destinations) == 1:
                        edge = {"fromModule": qualified(module), "fromMethod": method.name,
                                "toModule": qualified(destinations[0]), "toMethod": called}
                        if edge not in edges: edges.append(edge)
                        continue
                    if len(destinations) > 1:
                        diagnostics.append({"code": "ambiguous_call", "sourceFile": source_file,
                                            "message": f"Неоднозначный вызов {called} в {qualified(module)}.{method.name}"})
                        continue
                    if not known_receiver_type(inferred, module, model) and (called is None or called in project_methods):
                        diagnostics.append({"code": "dynamic_call", "severity": "warning",
                                            "sourceFile": source_file,
                                            "message": f"Динамический вызов в {qualified(module)}.{method.name}, строка "
                                                       f"{source.count(chr(10), 0, call.start) + 1}"})
                    continue
                if owner and method_binding_visible(bindings, owner.split(".", 1)[0], start):
                    continue
                candidates = []
                if owner is None:
                    if called in local_names:
                        candidates = [module]
                    elif platform_local_call(module, called, model.get("elements", [])):
                        continue
                    else:
                        for path, alias in specs:
                            if alias: continue
                            for candidate in ([m for m in visible_modules if m["namespace"] == path]
                                              if any(m["namespace"] == path for m in visible_modules) else
                                              resolve_symbols(visible_modules, path, module["namespace"], imports, project)):
                                if called in callable_names(candidate):
                                    candidates.append(candidate)
                else:
                    candidates = resolve_call_modules(visible_modules, owner, module["namespace"], imports, project)
                    if not candidates and module.get("_libraryPrefix") and "::" in owner:
                        candidates = resolve_call_modules(visible_modules, module["_libraryPrefix"] + "::" + owner,
                                                          module["namespace"], imports, project)
                    candidates = [m for m in candidates if called in callable_names(m)]
                if not candidates and owner is None and method_binding_visible(bindings, called, call.start):
                    if not method_callable_binding_visible(callable_bindings, called, call.start):
                        diagnostics.append({"code": "dynamic_call", "severity": "warning",
                                            "sourceFile": source_file,
                                            "message": f"Динамический вызов в {qualified(module)}.{method.name}, строка "
                                                       f"{source.count(chr(10), 0, call.start) + 1}"})
                    continue
                if len(candidates) > 1:
                    diagnostics.append({"code": "ambiguous_call", "sourceFile": source_file,
                                        "message": f"Неоднозначный вызов {owner + '.' if owner else ''}{called} в {qualified(module)}.{method.name}"})
                elif len(candidates) == 1:
                    destination = candidates[0]
                    edge = {"fromModule": qualified(module), "fromMethod": method.name,
                            "toModule": qualified(destination), "toMethod": called}
                    if edge not in edges: edges.append(edge)
    return edges, diagnostics
