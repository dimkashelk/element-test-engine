"""Conservative cross-module call graph over parsed method declarations."""
from .indexer import method_calls, parse_module
from .resolution import import_specs, qualified, resolve_call_modules, resolve_symbols
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
            for owner, called in method_calls(source, method):
                candidates = []
                if owner is None:
                    if called in local_names:
                        candidates = [module]
                    else:
                        for path, alias in specs:
                            if alias: continue
                            for candidate in ([m for m in modules if m["namespace"] == path and m.get("visibility") != "ВПодсистеме"]
                                              if any(m["namespace"] == path for m in modules) else
                                              resolve_symbols(modules, path, module["namespace"], imports, project)):
                                if called in {m.name for m in declarations[candidate["sourceFile"]] if "Локально" not in m.annotations}:
                                    candidates.append(candidate)
                else:
                    candidates = resolve_call_modules(modules, owner, module["namespace"], imports, project)
                    candidates = [m for m in candidates if called in {n.name for n in declarations[m["sourceFile"]]
                                                                    if m["sourceFile"] == source_file or "Локально" not in n.annotations}]
                if len(candidates) > 1:
                    diagnostics.append({"code": "ambiguous_call", "sourceFile": source_file,
                                        "message": f"Неоднозначный вызов {owner + '.' if owner else ''}{called} в {qualified(module)}.{method.name}"})
                elif len(candidates) == 1:
                    destination = candidates[0]
                    edge = {"fromModule": qualified(module), "fromMethod": method.name,
                            "toModule": qualified(destination), "toMethod": called}
                    if edge not in edges: edges.append(edge)
    return edges, diagnostics
