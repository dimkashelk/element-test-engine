"""Read-only infrastructure map for any Element project; no business execution."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import yaml

from .coverage import inventory
from .generated_types import ProjectTypes
from .indexer import parse_module, method_call_expressions, method_local_bindings, method_binding_visible
from .loader import open_project
from .model import analyze
from .query_catalog import source_records
from .query_catalog_refresh import inspect_query
from .resolution import resolve_call_modules
from .test_contracts import (CAPABILITIES, registry, contract_schema, map_schema, validate_map)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fact(status, basis, reason=None, detail=None):
    return {'status': status, 'basis': basis, 'reasonCode': reason, 'detail': detail}


def yaml_nodes(node, path=()):
    """Raw nodes preserve exact positions including quoted and multiline scalars."""
    yield path, node
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            yield from yaml_nodes(value, path + (key.value,))
    elif isinstance(node, yaml.SequenceNode):
        for i, value in enumerate(node.value):
            yield from yaml_nodes(value, path + (str(i),))


CONSTRAINTS = {'Обязательный', 'Обязательность', 'ПроверкаЗаполнения', 'МинимальноеЗначение',
               'МаксимальноеЗначение', 'Длина', 'ДлинаЦелойЧасти', 'ДлинаДробнойЧасти',
               'Уникальность', 'Автонумерация'}
SYSTEM_ACTIONS = {'ЗаписатьИЗакрыть', 'Записать', 'Закрыть', 'Создать', 'Удалить',
                  'Добавить', 'Копировать', 'СоздатьКопию', 'СоздатьНаОсновании',
                  'ДобавитьСтроку', 'ПереместитьСтрокуВверх', 'ПереместитьСтрокуВниз'}
LIFECYCLE = {'ПередЗаписью', 'ПослеЗаписи', 'ПриСозданииНаОсновании'}


def build_map(root, model, assignments=(), archive='project', adapter_profile='current', method_inventory=None):
    """Reuse inventories and planners, retaining unknown downstream stages explicitly."""
    profile = {'sourceCompatibilityVersion': model['compatibilityVersion'], 'adapterProfile': adapter_profile,
               'queryReferenceVersion': '9.3', 'versionApplicability': 'not-checked'}
    items = []
    modules = {m['sourceFile']: m for m in model['modules']}
    elements = {e['sourceFile']: e for e in model['elements']}
    sources = {p: (root / p).read_text(encoding='utf-8-sig') for p in modules}
    parsed_modules = {p: parse_module(s) for p, s in sources.items()}
    base = method_inventory if method_inventory is not None else inventory(root, model, assignments)
    if base['sourceHash'] != model['sourceHash']:
        raise ValueError('Method inventory belongs to a different source')
    expected = {(p, n.start, n.end, n.name) for p, parsed in parsed_modules.items() for n in parsed[0]}
    actual = {(m['identity']['sourceFile'], m['start'], m['end'], m['identity']['method']) for m in base['methods']}
    if actual != expected or len(actual) != len(base['methods']):
        raise ValueError('Incomplete or mismatched method inventory')

    def add(kind, owner, path, a, b, requires, value=None, yaml_path=()):
        text = (root / path).read_text(encoding='utf-8-sig')
        source = {'file': path, 'fileHash': sha256((root / path).read_bytes()).hexdigest(),
                  'projectSourceHash': model['sourceHash'], 'owner': owner,
                  'position': {'range': [a, b], 'line': text.count('\n', 0, a) + 1,
                               'column': a - text.rfind('\n', 0, a), 'yamlPath': list(yaml_path)}}
        item = {'id': 'occurrence-' + digest([model.get('projectIdentity'), source, kind])[:24],
                'kind': kind, 'family': CAPABILITIES[requires[0]][0], 'source': source, 'profile': dict(profile),
                'value': value, 'requires': list(requires), 'dependsOn': [],
                'stages': {'discovery': fact('ready', 'source inventory'),
                           'parsing': fact('ready', 'metadata/index parser'),
                           'planning': fact('not-checked', 'no scenario-free planner for this occurrence'),
                           'adapter': fact('not-checked', 'registry scope is not source acceptance', 'ADAPTER_NOT_CHECKED'),
                           'technicalAcceptance': fact('not-checked', 'no source execution in this command', 'ACCEPTANCE_NOT_CHECKED')},
                'authorScenario': {'status': 'absent', 'basis': 'not required for infrastructure discovery'},
                'firstBlocker': None}
        extension = next((c for c in requires if CAPABILITIES[c][2] == 'extension-required'), None)
        if extension:
            item['stages']['adapter'] = fact('blocked', CAPABILITIES[extension][3],
                'CAPABILITY_EXTENSION_REQUIRED', CAPABILITIES[extension][5])
        items.append(item)
        return item

    method_items = {}
    for entry in base['methods']:
        identity = entry['identity']; path = identity['sourceFile']; module = modules[path]
        owner = {'namespace': module['namespace'], 'name': module['name'], 'kind': module['moduleType']}
        nodes, _, errors = parsed_modules[path]
        node = next(n for n in nodes if n.start == entry['start'])
        requires = ['method-runtime'] + (['storage-lifecycle'] if node.name in LIFECYCLE else [])
        item = add('method', owner, path, node.start, node.end or len(sources[path]), requires,
                   {'name': node.name, 'parameters': node.parameters(sources[path]), 'returnType': node.return_type(sources[path])})
        method_items[path, node.start] = item
        if errors:
            item['stages']['parsing'] = fact('blocked', 'element_test.indexer', 'SOURCE_PARSE_BLOCKED', '; '.join(map(str, errors)))
        criteria = entry['directCriteria']
        if criteria:
            item['authorScenario'] = {'status': 'declared', 'basis': 'existing assignments; no new criterion or expected'}
            if any(c['planned'] for c in criteria):
                item['stages']['planning'] = fact('ready', 'element_test.coverage / plan_execution',
                    detail='Existing declared contract planned against this exact source; not compiled/executed')
            else:
                reasons = '; '.join(dict.fromkeys(c.get('reason', 'unknown planning limit') for c in criteria))
                item['stages']['planning'] = fact('blocked', 'element_test.execution_plan', 'METHOD_PLAN_BLOCKED', reasons)
        for call in method_call_expressions(sources[path], node):
            capability = ('timer-events' if call.name in {'ПодключитьОбработчикТаймера', 'ОтключитьОбработчикТаймера'} else
                          'clock' if call.name == 'Сейчас' and call.receiver in {'Дата', 'Время', 'ДатаВремя', 'Момент'} else
                          'storage-lifecycle' if call.name in {'Записать', 'Прочитать', 'СоздатьНаборЗаписей', 'ЗагрузитьОбъект', 'ПолучитьДанные'} else
                          'ui-action' if call.name in {'Открыть', 'ЗаписатьИЗакрыть', 'Закрыть'} else None)
            if not capability:
                continue
            bindings = method_local_bindings(sources[path], node)
            destinations = resolve_call_modules(model['modules'], call.receiver, module['namespace'], module.get('imports', []), model['properties']) if call.receiver else [module]
            shadowed = any(m['name'] == call.name for dest in destinations for m in dest['methods'])
            if capability in {'clock', 'timer-events'}:
                shadowed = shadowed or (method_binding_visible(bindings, call.receiver or call.name, call.start))
                if call.receiver:
                    shadowed = shadowed or bool(ProjectTypes(model, module['namespace'], module.get('imports', [])).resolve(call.receiver))
            if shadowed:
                continue
            child = add('platform-call', owner, path, call.start, call.end, [capability],
                        {'operation': call.name, 'receiver': call.receiver, 'resolution': 'source API candidate; adapter not accepted'})
            item['dependsOn'].append(child['id'])
            if capability not in item['requires']:
                item['requires'].append(capability)
        for expr in node.expression_tree.walk() if node.expression_tree else ():
            if expr.kind == 'new' and expr.children and expr.children[0].value == 'Надпись':
                child = add('dynamic-component', owner, path, expr.start, expr.end, ['dynamic-ui'], {'type': 'Надпись', 'resolution': 'candidate; no native type acceptance'})
                item['dependsOn'].append(child['id'])
                if 'dynamic-ui' not in item['requires']:
                    item['requires'].append('dynamic-ui')
            if expr.kind == 'block' and expr.value in {'если', 'пока', 'для', 'выбор'}:
                add('branch', owner, path, expr.start, expr.end, ['branch-measurement'], {'construct': expr.value})

    project = '::'.join(filter(None, (model['properties'].get('Поставщик'), model['properties'].get('Имя'))))
    records = [r for r in source_records(root, model, archive, project) if r['family'] in {'literal', 'xbql-file'}]
    for record in records:
        path = record['file']; module = modules.get(path)
        node = next((n for n in parsed_modules.get(path, ([],))[0] if n.start <= record['range'][0] < n.end), None)
        review = inspect_query(root, model, record, module or {'namespace': record['ownerIdentity']['namespace'], 'imports': []},
                               sources.get(path, (root / path).read_text(encoding='utf-8-sig')), node)
        requires = ['query-source'] + (['query-entry'] if node is None else [])
        item = add('source-query', {'namespace': record['ownerIdentity']['namespace'], 'name': record['ownerIdentity']['module'],
                                   'kind': record['family']}, path, *record['range'], requires,
                   {'form': record['family'], 'operations': record['operations'], 'parameters': review.get('parameters', [])})
        item['stages']['parsing'] = fact('ready' if review['parsed'] else 'blocked', 'element_test.query_catalog_refresh.inspect_query',
            None if review['parsed'] else 'SOURCE_PARSE_BLOCKED', review.get('reason'))
        item['stages']['planning'] = (fact('ready', 'typed AST and generation; no Script execution') if review['queryPlanned'] else
            fact('blocked' if review['parsed'] else 'not-checked', 'element_test.query_catalog_refresh.inspect_query',
                 'AUTHOR_CONTEXT_REQUIRED' if review['status'] == 'needs-author-scenario' else 'QUERY_PLAN_BLOCKED', review.get('reason')))
        if node:
            parent = method_items[path, node.start]
            parent['dependsOn'].append(item['id'])
            parent['requires'].append('query-source') if 'query-source' not in parent['requires'] else None

    for path, element in sorted(elements.items()):
        owner = {'namespace': element['namespace'], 'name': element['name'], 'kind': element['elementType']}
        kind = element['elementType']
        raw = yaml.compose((root / path).read_text(encoding='utf-8-sig'))
        declarations = {'КомпонентИнтерфейса': ('ui-declaration', ['ui-state']), 'Отчет': ('report', ['report-layout']),
                        'ПанельОтчетов': ('panel', ['report-panel']), 'ФрагментКомандногоИнтерфейса': ('navigation', ['navigation'])}
        declaration = None
        if kind in declarations:
            category, requires = declarations[kind]
            if kind == 'КомпонентИнтерфейса' and element['properties'].get('Наследует', {}).get('Тип') in {
                    'ПроизвольноеКлиентскоеПриложение', 'СтандартноеКлиентскоеПриложениеСРазделами'}:
                requires = requires + ['application-ui']
            declaration = add(category, owner, path, raw.start_mark.index, raw.end_mark.index, requires)
        for p, y in yaml_nodes(raw):
            a, b = y.start_mark.index, y.end_mark.index
            key = p[-1] if p else ''
            value = y.value if isinstance(y, yaml.ScalarNode) else None
            if kind == 'Отчет' and key == 'Выражение' and 'Макет' in p:
                add('report-formula', owner, path, a, b, ['report-layout'], value, p)
            if kind == 'Отчет' and len(p) >= 2 and p[-2] == 'Поля' and isinstance(y, yaml.MappingNode):
                add('report-field', owner, path, a, b, ['report-layout'], None, p)
            if kind == 'Отчет' and key in {'ИсточникДанных', 'ВидИсточникаДанных'}:
                add('report-source', owner, path, a, b, ['query-source', 'report-layout'], value, p)
            if key == 'Отчет' and kind == 'ПанельОтчетов':
                add('panel-widget', owner, path, a, b, ['report-panel'], value, p)
            if key == 'АвтоматическоеОбновление' and kind == 'ПанельОтчетов':
                add('panel-refresh', owner, path, a, b, ['timer-events', 'report-panel'], None, p)
            if key in CONSTRAINTS:
                add('metadata-constraint', owner, path, a, b, ['metadata-validation'], value, p)
            if kind == 'КомпонентИнтерфейса' and (key == 'Обработчик' or key.startswith('При') and isinstance(value, str)):
                add('handler', owner, path, a, b, ['ui-action'], value, p)
            if kind == 'КомпонентИнтерфейса' and isinstance(value, str) and value.startswith('='):
                category = 'system-action' if value[1:].split('.')[-1] in SYSTEM_ACTIONS else 'binding'
                add(category, owner, path, a, b, ['ui-action' if category == 'system-action' else 'ui-binding'], value, p)
            if kind in {'ФрагментКомандногоИнтерфейса', 'КомпонентИнтерфейса'} and (
                    key in {'ТипФормы', 'ТипРаздела'} or kind == 'ФрагментКомандногоИнтерфейса' and isinstance(value, str) and value.startswith('=')):
                add('navigation-link', owner, path, a, b, ['navigation'], value, p)
        if declaration and kind == 'Отчет':
            # An external query belongs to the exact report file, never a fabricated method.
            declaration['dependsOn'] += [i['id'] for i in items if i['kind'] == 'source-query' and
                                         i['source']['file'] == str(Path(path).with_suffix('.xbql'))]

    # Link declaration occurrences and references without collapsing equal text.
    declarations = {(i['source']['file']): i for i in items if i['kind'] in {'ui-declaration', 'report', 'panel', 'navigation'}}
    for item in items:
        parent = declarations.get(item['source']['file'])
        if parent and parent is not item:
            parent['dependsOn'].append(item['id'])
        if item['kind'] in {'navigation-link', 'panel-widget', 'report-source'} and isinstance(item['value'], str) and item['source']['position']['yamlPath'][-1] != 'ВидИсточникаДанных':
            name = item['value'].lstrip('=').split('.')[0]
            e = elements[item['source']['file']]
            resolver = ProjectTypes(model, e['namespace'], e['properties'].get('Импорт', []))
            matches = resolver.resolve(name)
            target = declarations.get(matches[0]['sourceFile']) if len(matches) == 1 else None
            if target:
                item['dependsOn'].append(target['id'])
            item['value'] = {'reference': item['value'], 'resolution': 'unique' if len(matches) == 1 else 'unresolved',
                             'targets': [m['sourceFile'] for m in matches],
                             'nativeSurface': '.АвтоматическаяФорма' in item['value']}
            if not matches and name != 'Запрос':
                item['stages']['planning'] = fact('blocked', 'ProjectTypes exact namespace/import resolution', 'REFERENCE_UNRESOLVED', name)
        item['dependsOn'] = list(dict.fromkeys(item['dependsOn']))
        extension = next((c for c in item['requires'] if CAPABILITIES[c][2] == 'extension-required'), None)
        if extension and item['stages']['adapter']['status'] == 'not-checked':
            item['stages']['adapter'] = fact('blocked', CAPABILITIES[extension][3], 'CAPABILITY_EXTENSION_REQUIRED', CAPABILITIES[extension][5])
        item['firstBlocker'] = next((v for v in item['stages'].values() if v['status'] == 'blocked'), None)

    # Dependency blockers remain attributed to their exact occurrence. Never
    # replace the parent's own stage facts (a parsed method can contain a query
    # whose later query parser is blocked). Navigation graphs may contain cycles.
    by_id = {i['id']: i for i in items}
    def first_blocker(item, trail=frozenset()):
        if item['id'] in trail:
            return None
        candidates = [(rank, value) for rank, value in enumerate(item['stages'].values()) if value['status'] == 'blocked']
        for dependency in item['dependsOn']:
            inherited = first_blocker(by_id[dependency], trail | {item['id']})
            if inherited:
                rank, value = inherited
                candidates.append((rank, {**value, 'basis': 'dependency ' + dependency + ' / ' + value['basis']}))
        return min(candidates, key=lambda v: v[0]) if candidates else None
    for item in items:
        blocker = first_blocker(item)
        item['firstBlocker'] = blocker[1] if blocker else None

    counts = dict(Counter(i['kind'] for i in items))
    counts.update(items=len(items), plannedMethods=sum(i['kind'] == 'method' and i['stages']['planning']['status'] == 'ready' for i in items),
                  plannedQueries=sum(i['kind'] == 'source-query' and i['stages']['planning']['status'] == 'ready' for i in items),
                  technicallyAccepted=0)
    queue = []
    stage_dependencies = {47: [46], 48: [46], 49: [46, 48], 50: [46, 47], 51: [47, 48, 49, 50], 52: [46, 47, 48, 49, 50, 51]}
    for stage in range(47, 53):
        caps = [c for c, v in CAPABILITIES.items() if v[1] == stage]
        dependencies = stage_dependencies[stage]
        gaps = list(dict.fromkeys(CAPABILITIES[c][5] for c in caps if CAPABILITIES[c][5]))
        source_blockers = [i['source']['file'] + ':' + str(i['source']['position']['line']) + ': ' + i['firstBlocker']['detail']
                           for i in items if set(i['requires']) & set(caps) and i['firstBlocker'] and
                           i['firstBlocker']['reasonCode'] != 'CAPABILITY_EXTENSION_REQUIRED' and i['firstBlocker']['detail']]
        gaps += list(dict.fromkeys(source_blockers))
        queue.append({'stage': stage, 'dependencies': dependencies, 'capabilities': caps,
                      'occurrences': sum(bool(set(i['requires']) & set(caps)) for i in items), 'firstBlockers': gaps,
                      'externalBoundaries': ['native UI rendering and built-in forms'] if stage == 51 else
                                            ['native platform behavior needs independent evidence'] if stage in {47, 50} else []})
    data = {'schemaVersion': 1, 'sourceHash': model['sourceHash'], 'projectIdentity': model.get('projectIdentity', {}),
            'profile': profile, 'archive': archive, 'archiveSha256': None, 'items': items, 'counts': counts,
            'capabilities': registry()['capabilities'], 'queue': queue,
            'notes': ['Infrastructure discovery is not application PASS', 'No author scenario is not a missing engine capability',
                      'Planning does not establish adapter support or technical acceptance', 'Historical execution is not imported',
                      'Source compatibility and adapter/reference profiles remain separate', 'Branch occurrences are not executed outcomes',
                      'Formula and binding counts are independent', 'No business criteria, expected values or points are generated']}
    return validate_map(data)


def markdown(data):
    lines = ['# Карта возможностей движка', '', 'Исходник: `' + data['sourceHash'] + '`. Исполнение и оценка приложения не выполнялись.', '',
             '| Категория | Вхождений |', '|---|---:|']
    lines += [f'| {k} | {v} |' for k, v in data['counts'].items()]
    lines += ['', '| Этап | Зависимости | Первые пробелы |', '|---|---|---|']
    lines += [f"| №{q['stage']} | {', '.join(map(str, q['dependencies']))} | {'; '.join(q['firstBlockers'])} |" for q in data['queue']]
    lines += ['', '| Исходник / позиция | Категория | Planning | Первая блокировка |', '|---|---|---|---|']
    for item in data['items']:
        s = item['source']; blocker = item['firstBlocker'] or {}
        detail = (blocker.get('detail') or '').replace('|', '/').replace('\n', ' ')
        lines.append(f"| {s['file']}:{s['position']['line']}:{s['position']['column']} | {item['kind']} | {item['stages']['planning']['status']} | {blocker.get('reasonCode', '')}: {detail} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--project-name')
    inventories = parser.add_mutually_exclusive_group()
    inventories.add_argument('--assignments', type=Path)
    inventories.add_argument('--method-inventory', type=Path, help='Reuse a sourceHash-matched coverage inventory')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--adapter-profile', default='current')
    args = parser.parse_args()
    before = sha256(args.project.read_bytes()).hexdigest() if args.project.is_file() else None
    assignments = sorted(p.parent for p in args.assignments.rglob('assignment.yaml')) if args.assignments else []
    with open_project(args.project, args.project_name) as root:
        base = json.loads(args.method_inventory.read_text()) if args.method_inventory else None
        data = build_map(root, analyze(root), assignments, args.project.name, args.adapter_profile, base)
    if before is not None and before != sha256(args.project.read_bytes()).hexdigest():
        raise RuntimeError('Archive changed during read-only discovery')
    data['archiveSha256'] = before
    validate_map(data)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in [('capability-map.json', data), ('test-type-contracts-v1.json', registry()),
                        ('test-type-contract-v1.schema.json', contract_schema()), ('capability-map-v1.schema.json', map_schema())]:
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    (args.output / 'capability-map.md').write_text(markdown(data))
    print(json.dumps(data['counts'], ensure_ascii=False))


if __name__ == '__main__':
    main()
