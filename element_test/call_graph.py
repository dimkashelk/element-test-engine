"""Conservative cross-module call graph over parsed method declarations."""
from .indexer import method_call_sites, method_local_bindings, parse_module
from .resolution import (import_specs, method_visible, qualified, resolve_call_modules,
                         resolve_symbols, visible_from)
from .yaml_io import InputError


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
            visible_modules = [candidate for candidate in modules
                               if visible_from(candidate, module["namespace"], module.get("_libraryPrefix"))]
            def callable_names(candidate):
                return {name.name for name in declarations[candidate["sourceFile"]]
                        if method_visible(name, module, candidate)}
            for owner, called, start, _ in method_call_sites(source, method):
                if owner and owner in bindings and bindings[owner] <= start:
                    continue
                candidates = []
                if owner is None:
                    if called in local_names:
                        candidates = [module]
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
                if len(candidates) > 1:
                    diagnostics.append({"code": "ambiguous_call", "sourceFile": source_file,
                                        "message": f"Неоднозначный вызов {owner + '.' if owner else ''}{called} в {qualified(module)}.{method.name}"})
                elif len(candidates) == 1:
                    destination = candidates[0]
                    edge = {"fromModule": qualified(module), "fromMethod": method.name,
                            "toModule": qualified(destination), "toMethod": called}
                    if edge not in edges: edges.append(edge)
    return edges, diagnostics
