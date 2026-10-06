"""Refresh planning separately from historical execution and assessment evidence.

No Script, Docker, database or student code is executed. Source occurrences are
never merged by query text. API consumers are linked only to proven declarations.
"""
import argparse
from collections import Counter, defaultdict
import copy
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from .call_types import _local_type
from .execution_plan import plan_execution
from .generated_types import ProjectTypes
from .indexer import IDENT, mask_noncode, method_binding_visible, method_local_bindings, parse_module
from .loader import open_project
from .local_structures import scalar_structures
from .model import analyze
from .query_api import api_receivers, dynamic_queries, normalized_text, query_aliases
from .query_catalog import REPO, source_records
from .query_composites import leaves
from .query_plan import parse_storage_query
from .resolution import resolve_call_modules
from .yaml_io import InputError, InvalidTestError

HISTORICAL = ('parsed', 'planned', 'executed', 'independentlyAssessed', 'ast',
              'unsupportedReason', 'criteria', 'evidence')
ENTITY_REFERENCE = 'tests/corpus/virtual-tables/reference-9.3.json'
ENTITY_DOC = 'documentation-table-entitycontractname_ru'


def fingerprint(path):
    return sha256(path.read_bytes()).hexdigest()


def identity(record):
    p = record.get('projectIdentity') or {}
    return p.get('Поставщик', '') + '::' + p.get('Имя', '')


def occurrence_key(record):
    """Exact source identity: equal text in different roots stays independent."""
    return (record.get('archive'), identity(record), record.get('sourceHash'),
            record.get('file'), tuple(record.get('range') or ()), record['family'])


def classify(record):
    if record['family'] in {'literal', 'xbql-file'}:
        return 'source-query'
    if record['family'] == 'api-candidate':
        return 'api-call'
    if record['family'] == 'documentation-index':
        return 'documentation'
    # Two historical .xbql fixtures are English requirement outlines.
    if record['contractId'] in {'documented-state', 'documented-rights'}:
        return 'requirement-outline'
    if record['contractId'] == 'documented-dynamic-api':
        return 'api-example'
    return 'query-example'


def applicability(record, reason=None):
    if record['contractId'] == ENTITY_DOC:
        return {'status': 'excluded-from-9.3-matrix', 'referenceVersion': '9.3',
                'sourceCompatibilityVersion': record.get('compatibilityVersion'),
                'basis': ENTITY_REFERENCE, 'condition': 'removed after platform 7.0',
                'verification': 'preserved-local-reference',
                'reason': 'Historical entity-contract table is excluded only from the 9.3 documentation matrix.'}
    if 'Таблица контракта сущности удалена' in (reason or ''):
        return {'status': 'legacy-source-version-review', 'referenceVersion': '9.3',
                'sourceCompatibilityVersion': record.get('compatibilityVersion'),
                'basis': ENTITY_REFERENCE,
                'reason': 'Archive source is retained as required work. A 9.3 exclusion does not prove a 9.0 source inapplicable.'}
    return {'status': 'required', 'referenceVersion': '9.3',
            'sourceCompatibilityVersion': record.get('compatibilityVersion')}


def cause_category(reason):
    reason = reason or ''
    if 'удалена из платформы' in reason:
        return 'version-review'
    if any(s in reason for s in ('Количество аргументов', 'context', 'storage.registers',
                                 'initial', 'queryContext', 'queryAccess')):
        return 'author-scenario'
    if any(s in reason for s in ('Пользовател', 'Доступ', 'Настроек', 'Неудаленные', 'Обмен')):
        return 'system-sources-and-rights'
    if any(s in reason for s in ('Срез', 'период', 'регистр', 'источник', 'Источник')):
        return 'sources'
    if any(s in reason for s in ('тип', 'Тип', 'поле', 'Поле', 'owner', 'владел', 'Владел', 'Затен')):
        return 'types-and-resolution'
    return 'syntax-and-operations'


def contracts_for(root, model, record, module, source, node):
    c = ProjectTypes(model, module.get('namespace', record['ownerIdentity']['namespace']),
                     module.get('imports', []))
    c.rename_collisions = True
    c.reference_id_type = 'Ууид'
    c.current_source = c.local_source = record['file']
    c.query_root = root
    c.local_structures = scalar_structures(source)
    c.local_by_source = {record['file']: c.local_structures}
    c.query_results = True
    if node:
        bindings = method_local_bindings(source, node)
        c.query_declaration_annotations = node.annotations
        c.query_parameter_type = lambda expression, at: _local_type(
            source, node, expression, record['bodyStart'] + at, bindings, module, model
        ) if re.fullmatch(IDENT, expression) else None
    return c


def inspect_query(root, model, record, module, source, node):
    result = {'status': 'blocked', 'parsed': False, 'queryPlanned': False,
              'mode': 'typed-query-AST-and-generation', 'reason': None}
    c = contracts_for(root, model, record, module, source, node)
    try:
        if record['family'] == 'literal':
            bindings = method_local_bindings(source, node) if node else {}
            if (node and method_binding_visible(bindings, 'Запрос', record['range'][0])
                    or 'Запрос' in c.local_structures or c.resolve('Запрос')
                    or resolve_call_modules(model['modules'], 'Запрос', c.namespace, c.imports, model['properties'])):
                raise InputError('Затенённый владелец литерала Запрос')
        text = normalized_text(record['text']) if record['family'] == 'xbql-file' else record['text']
        result['textNormalization'] = 'named-&-parameters-to-%' if text != record['text'] else 'none'
        query = parse_storage_query(text, c)
        ast = query.to_dict()
        result.update(parsed=True, astHash=sha256(json.dumps(ast, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                      astMode=query.mode, sourceKind=query.source_kind,
                      parameters=[{'expression': p.expression, 'type': p.type, 'slot': p.slot} for p in query.parameters])
        # Bind the same declared storage schemas as the production query planner.
        from .storage import MetadataStorage
        from .storage_queries import generate_public_query
        storage = MetadataStorage(c, {'idType': 'Ууид', 'initial': []})
        register_owners = []
        for leaf in leaves(query):
            if leaf.source_kind in {'slice-last', 'slice-first', 'register', 'balance', 'turnover', 'balance-turnover'}:
                storage.attach_register(leaf.owner)
                register_owners.append(leaf.owner)
            elif leaf.source_kind in {'ordinary', 'table-part', 'collection'}:
                elements = c.resolve(leaf.owner)
                if elements:
                    storage.attach(elements[0], query_only=True)
            elif leaf.source_kind == 'constants':
                raise InvalidTestError('Источник констант требует авторский session-контракт')
        generate_public_query(query, c)
        # A digest of generated query modules is reproducible; the unused session
        # definition contains a random /tmp path and must not enter this digest.
        generated = {k: v for k, v in c.definitions.items() if k != 'ТестСессия'}
        result.update(status='planned-query', queryPlanned=True,
                      generatedQueryHash=sha256(json.dumps(generated, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                      registerOwners=sorted(set(register_owners)))
    except InvalidTestError as exc:
        result.update(status='needs-author-scenario', reason=str(exc))
    except InputError as exc:
        result['reason'] = str(exc)
    return result


def method_probe(root, model, record, module, source, node, query_reviews):
    """Plan declarations with unknown arguments; never generate author answers.

    None placeholders establish only argument count. Success is not validation
    of runtime inputs, compilation, execution or independent assessment.
    """
    if not node or not record.get('target'):
        return {'status': 'not-applicable', 'reason': 'External query has no method entry.'}
    config = {'target': {k: record['target'][k] for k in ('namespace', 'module', 'method')},
              'args': [None] * len(node.parameters(source)), 'queryResults': True,
              'runtimeProfile': '9.3', 'storage': {'idType': 'Ууид', 'initial': [],
              'registers': sorted({owner for r in query_reviews for owner in r.get('registerOwners', [])})}}
    try:
        plan = plan_execution(root, model, config)
        return {'status': 'planned-probe', 'mode': 'symbolic-signature-empty-memory-storage',
                'argumentsValidated': False, 'authorCriterion': False,
                'reachableDeclarations': len(plan.symbols), 'queries': len(plan.queries),
                'queryOccurrences': [{'file': q['sourceFile'], 'range': [q['start'], q['end']]} for q in plan.queries]}
    except InvalidTestError as exc:
        return {'status': 'needs-author-scenario', 'reason': str(exc)}
    except InputError as exc:
        return {'status': 'blocked', 'reason': str(exc), 'cause': cause_category(str(exc))}


def link_api(record, queries, source, node, module, model):
    binding = record.get('apiBinding') or {}
    if binding.get('resolution') == 'project-declaration':
        return {'status': 'project-call', 'reason': 'Resolved project declaration; not a platform Query API obligation.'}
    if not node:
        return {'status': 'unresolved', 'reason': 'No unambiguous containing method.'}
    candidates = [q for q in queries if (q.get('target') or {}).get('range') == record['target']['range']]
    receiver = binding.get('receiver', '')
    # An inline consumer's receiver span contains the exact literal.
    inline = [q for q in candidates if record['range'][0] <= q['range'][0]
              and q['range'][1] <= record['range'][1]]
    if inline:
        return {'status': 'linked-source-query', 'relatedContractIds': [q['contractId'] for q in inline]}
    method_source = source[node.start:node.end]
    symbol = SimpleNamespace(source=method_source, owner=module, local_structures=tuple(scalar_structures(source)))
    try:
        proven, _ = api_receivers(symbol, model)
        if receiver not in proven or proven[receiver] >= record['range'][0] - node.start:
            return {'status': 'unresolved', 'reason': 'Receiver/signature is not proven by current Query API resolver.'}
        linked = []
        for q in candidates:
            a = q['range'][0] - node.start
            code = mask_noncode(method_source)
            declaration = re.fullmatch(rf'\s*(?:знч|пер)\s+({IDENT})\s*=\s*', code[code.rfind('\n', 0, a)+1:a])
            if declaration and receiver in query_aliases(method_source, parse_module(method_source)[0][0], declaration[1]):
                # Ignore consumers following a re-assignment: the API resolver
                # deliberately refuses to prove that flow.
                q_end = q['range'][1] - node.start
                if re.search(rf'\b{re.escape(declaration[1])}\s*=(?!=)', code[q_end:proven[receiver]]):
                    continue
                end = max(q_end, proven[receiver])
                if not re.search(rf'\b{re.escape(receiver)}\s*=(?!=)', code[end:record['range'][0]-node.start]):
                    linked.append(q['contractId'])
        if linked:
            return {'status': 'linked-source-query', 'relatedContractIds': linked}
        for dynamic in dynamic_queries(method_source, model, module, symbol.local_structures):
            if receiver in query_aliases(method_source, parse_module(method_source)[0][0], dynamic.variable):
                return {'status': 'confirmed-api-source',
                        'sourceRange': [node.start + dynamic.start, node.start + dynamic.end],
                        'sourceVariable': dynamic.variable,
                        'sourceTextHash': sha256(method_source[dynamic.start:dynamic.end].encode()).hexdigest(),
                        'reason': 'Proven dynamic/API declaration; all its consumers share one author scenario.'}
        return {'status': 'confirmed-api-receiver', 'reason': 'API receiver requires its own author scenario.'}
    except InputError as exc:
        return {'status': 'unresolved', 'reason': str(exc)}


def stage_memberships(repo):
    memberships = defaultdict(set)
    for stage in range(40, 46):
        completion = repo / f'docs/query-stage-{stage:03}-completion-coverage.json'
        # Task 44 completion is only a nine-record selection, not its full scope.
        path = repo / f'docs/query-stage-{stage:03}-baseline.json' if stage == 44 or not completion.exists() else completion
        for r in json.loads(path.read_text())['contracts']:
            memberships[r['contractId']].add(f'{stage:03}')
    return memberships


def build_queue(records, reviews, memberships):
    unique = {}
    for record in records:
        review = reviews[record['contractId']]
        if review['kind'] not in {'source-query', 'api-call'}:
            continue
        if review['kind'] == 'api-call' and review.get('apiResolution', {}).get('status') in {'linked-source-query', 'project-call'}:
            continue
        key = occurrence_key(record)
        api = review.get('apiResolution', {})
        if api.get('status') == 'confirmed-api-source':
            key = (*key[:4], tuple(api['sourceRange']), 'api-source')
        if key in unique:
            unique[key]['contractIds'].append(record['contractId'])
            unique[key]['stages'] = sorted(set(unique[key]['stages']) | memberships[record['contractId']])
            continue
        query = review.get('queryPlanning', {})
        method = review.get('methodPlanning', {})
        reason = method.get('reason') or query.get('reason') or review.get('apiResolution', {}).get('reason')
        if record['independentlyAssessed']:
            status = 'assessed-historically'
        elif review['applicability']['status'] == 'legacy-source-version-review':
            status = 'version-review'
        elif query.get('queryPlanned') and method.get('status') == 'planned-probe' and {
                'file': record['file'], 'range': record['range']} in method.get('queryOccurrences', []):
            status = 'needs-author-criterion'
        elif query.get('queryPlanned') and record['family'] == 'xbql-file':
            status = 'needs-query-api-scenario'
        elif method.get('status') == 'needs-author-scenario' or query.get('status') == 'needs-author-scenario':
            status = 'needs-author-scenario'
        elif review['kind'] == 'api-call':
            status = 'needs-api-scenario' if api.get('status') == 'confirmed-api-source' else 'needs-api-resolution'
        else:
            status = 'blocked'
        unique[key] = {'workId': record['contractId'], 'contractIds': [record['contractId']],
                       'kind': 'api-source' if api.get('status') == 'confirmed-api-source' else review['kind'],
                       'family': record['family'], 'archive': record.get('archive'),
                       'project': identity(record), 'sourceHash': record.get('sourceHash'),
                       'file': record.get('file'), 'range': api.get('sourceRange', record.get('range')), 'target': record.get('target'),
                       'stages': sorted(memberships[record['contractId']]), 'status': status,
                       'queryPlanningStatus': query.get('status'), 'methodPlanningStatus': method.get('status'),
                       'exactQueryInMethodPlan': {'file': record['file'], 'range': record['range']} in method.get('queryOccurrences', []),
                       'historicallyAssessed': bool(record['independentlyAssessed']),
                       'reason': reason, 'cause': cause_category(reason) if reason else None,
                       'applicability': review['applicability'], 'relatedApiContractIds': []}
    rows = list(unique.values())
    by_id = {i: r for r in rows for i in r['contractIds']}
    for record in records:
        for related in reviews[record['contractId']].get('apiResolution', {}).get('relatedContractIds', []):
            if related in by_id:
                row = by_id[related]
                row['relatedApiContractIds'].append(record['contractId'])
                row['stages'] = sorted(set(row['stages']) | memberships[record['contractId']])
    return sorted(rows, key=lambda r: (r['archive'], r['project'], r['file'], r['range']))


def refresh(repo=REPO):
    path = repo / 'docs/query-contract-catalog.json'
    original = json.loads(path.read_text())
    catalog = copy.deepcopy(original)
    records = catalog['contracts']
    if len({r['contractId'] for r in records}) != len(records):
        raise InputError('Duplicate contractId in input catalog')
    reviews = {r['contractId']: {'kind': classify(r), 'applicability': applicability(r)} for r in records}
    archives = {a['file']: fingerprint(repo / a['file']) for a in catalog['archives']}
    for a in catalog['archives']:
        if archives[a['file']] != a['sha256']:
            raise InputError('Archive hash differs from catalog: ' + a['file'])
    grouped = defaultdict(list)
    for r in records:
        if r.get('archive'):
            grouped[r['archive'], identity(r)].append(r)
    discovery = []
    for (archive, project), group in sorted(grouped.items()):
        print('Read-only planning:', archive, project, flush=True)
        with open_project(repo / archive, project) as root:
            model = analyze(root)
            fresh = source_records(root, model, archive, project)
            old_ids, new_ids = {r['contractId'] for r in group}, {r['contractId'] for r in fresh}
            if old_ids != new_ids:
                raise InputError('Discovery identity changed; explicit source migration required: ' + project)
            discovery.append({'archive': archive, 'project': project, 'sourceOccurrences': len(group),
                              'projectSourceHash': model['sourceHash'], 'inventoryMatches': True})
            modules = {m['sourceFile']: m for m in model['modules']}
            sources = {file: (root / file).read_text(encoding='utf-8-sig') for file in {r['file'] for r in group}}
            nodes = {file: parse_module(source)[0] for file, source in sources.items() if file in modules}
            query_records = [r for r in group if r['family'] in {'literal', 'xbql-file'}]
            for r in group:
                if fingerprint(root / r['file']) != r['sourceHash'] or model['sourceHash'] != r['projectSourceHash']:
                    raise InputError('Exact source hash mismatch: ' + r['contractId'])
                a, b = r['range']
                start = r['bodyStart'] if r['family'] in {'literal', 'xbql-file'} else a
                end = b - 1 if r['family'] == 'literal' else b
                if sources[r['file']][start:end] != r['text']:
                    raise InputError('Exact source range mismatch: ' + r['contractId'])
                if r['family'] not in {'literal', 'xbql-file'}:
                    continue
                node = next((n for n in nodes.get(r['file'], []) if n.start <= a < n.end), None)
                review = inspect_query(root, model, r, modules.get(r['file'], {}), sources[r['file']], node)
                reviews[r['contractId']]['queryPlanning'] = review
                reviews[r['contractId']]['applicability'] = applicability(r, review.get('reason'))
            method_reviews = {}
            for r in group:
                source = sources[r['file']]
                node = next((n for n in nodes.get(r['file'], []) if n.start <= r['range'][0] < n.end), None)
                module = modules.get(r['file'], {})
                if node:
                    key = r['file'], node.start, node.end
                    if key not in method_reviews:
                        siblings = [reviews[q['contractId']]['queryPlanning'] for q in query_records
                                    if q['file'] == r['file'] and node.start <= q['range'][0] < node.end]
                        method_reviews[key] = method_probe(root, model, r, module, source, node, siblings)
                    reviews[r['contractId']]['methodPlanning'] = method_reviews[key]
                if r['family'] == 'api-candidate':
                    reviews[r['contractId']]['apiResolution'] = link_api(r, query_records, source, node, module, model)
            discovery[-1]['methodProbes'] = len(method_reviews)
    for archive, before in archives.items():
        if fingerprint(repo / archive) != before:
            raise InputError('Archive changed during planning: ' + archive)
    memberships = stage_memberships(repo)
    queue = build_queue(records, reviews, memberships)
    for r in records:
        current = reviews[r['contractId']]
        current['historicalEvidence'] = {'executed': r['executed'], 'independentlyAssessed': r['independentlyAssessed'],
                                       'currentRuntimeRevalidated': False}
        # Preserve prior review snapshots only when the actual review changes.
        if r.get('currentReview') and r['currentReview'] != current:
            r.setdefault('currentReviewHistory', []).append(r['currentReview'])
        r['currentReview'] = current
    summary = {'recordKinds': dict(Counter(r['kind'] for r in reviews.values())),
               'sourceQueryPlanning': dict(Counter(r.get('queryPlanning', {}).get('status') for r in reviews.values()
                                                  if r['kind'] == 'source-query')),
               'sourceQueriesParsed': sum(r.get('queryPlanning', {}).get('parsed', False) for r in reviews.values()),
               'sourceQueriesRequiringGeneration': sum(r.get('queryPlanning', {}).get('parsed', False)
                                                       and not r.get('queryPlanning', {}).get('queryPlanned', False)
                                                       for r in reviews.values()),
               'apiResolution': dict(Counter(r.get('apiResolution', {}).get('status') for r in reviews.values()
                                            if r['kind'] == 'api-call')),
               'uniqueWorkItems': len(queue), 'queueStatuses': dict(Counter(r['status'] for r in queue)),
               'historicallyAssessedSourceQueries': sum(r['historicallyAssessed'] for r in queue if r['kind'] == 'source-query'),
               'unassessedSourceQueries': sum(not r['historicallyAssessed'] for r in queue if r['kind'] == 'source-query')}
    stage_counts = {}
    for stage in range(40, 46):
        stage = f'{stage:03}'
        ids = {i for i, stages in memberships.items() if stage in stages}
        selected = [r for r in records if r['contractId'] in ids]
        work = [r for r in queue if stage in r['stages']]
        stage_counts[stage] = {'historicalScopeCount': len(selected),
                              'recordKinds': dict(Counter(reviews[r['contractId']]['kind'] for r in selected)),
                              'historicallyUnassessedRecords': sum(not r['independentlyAssessed'] for r in selected),
                              'uniqueWorkItems': len(work), 'queueStatuses': dict(Counter(r['status'] for r in work)),
                              'overlapsOtherStages': True}
    engine_files = sorted((repo / 'element_test').glob('*.py')) + sorted((repo / 'element_test').glob('*.sbsl'))
    engine = {str(p.relative_to(repo)): fingerprint(p) for p in engine_files}
    engine_hash = sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest()
    git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    meta = {'schemaVersion': 1, 'date': datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat(),
            'engineHash': engine_hash, 'engineArtifacts': engine,
            'baseCommit': git, 'referenceVersion': '9.3', 'executionCompatibilityVersion': 'current',
            'sourceCompatibilityPreserved': True, 'runtimeExecuted': False, 'assessmentAdded': False,
            'archives': [{'file': f, 'sha256': h, 'unchanged': True} for f, h in archives.items()],
            'discovery': discovery, 'summary': summary, 'stages': stage_counts,
            'policy': {'historicalFields': list(HISTORICAL), 'historicalFlagsAreNotCurrentRuntimeEvidence': True,
                       'documentationExcludedFromSourceDenominator': True, 'sourceTextNeverDeduplicated': True,
                       'legacyArchiveQueriesNotExcludedByReferenceVersion': True,
                       'methodProbesDoNotValidateArgumentsOrAuthorCriteria': True}}
    # Assertion protects all pre-existing fields, including history/evidence.
    for before, after in zip(original['contracts'], records):
        for key, value in before.items():
            if key not in {'currentReview', 'currentReviewHistory'} and after[key] != value:
                raise InputError('Historical field changed: ' + before['contractId'] + '/' + key)
    catalog['currentReview'] = meta
    report = {**meta, 'workItems': queue,
              'documentation': [{'contractId': r['contractId'], 'kind': reviews[r['contractId']]['kind'],
                                 'stages': sorted(memberships[r['contractId']]),
                                 'applicability': reviews[r['contractId']]['applicability'],
                                 'historicallyAssessed': r['independentlyAssessed'], 'fixture': r.get('fixture')}
                                for r in records if not r.get('archive')]}
    return catalog, report


def overview(report):
    s = report['summary']
    lines = ['# Текущая очередь контрактов запросов', '',
             f"Обновление: {report['date']} (Europe/Moscow). Только чтение исходников, разбор типов и планирование; "
             'Script, Docker, SQL и независимое оценивание не запускались.', '',
             'Воспроизведение: `python3 -m element_test.query_catalog_refresh`.', '',
             'Исходные архивы, contractId, тексты, диапазоны, критерии, доказательства и исторические P/L/E/A сохранены. '
             '`currentReview` хранит отдельную проверку текущего движка. Полный метод проверяется символическим '
             'планом с неизвестными аргументами и пустым memory-хранилищем; это не готовое авторское задание.', '',
             '| Вид записи | Количество |', '|---|---:|']
    labels = {'source-query': 'Реальные запросы из архивов', 'api-call': 'Кандидаты вызовов API',
              'documentation': 'Страницы документации', 'requirement-outline': 'Текстовые требования',
              'query-example': 'Примеры запросов', 'api-example': 'Примеры API'}
    lines += [f'| {labels[k]} | {v} |' for k, v in sorted(s['recordKinds'].items())]
    lines += ['', '| Текущая проверка реального запроса | Количество |', '|---|---:|---:|']
    lines += [f'| {k} | {v} |' for k, v in sorted(s['sourceQueryPlanning'].items())]
    lines += ['', f"В уникальной очереди {s['uniqueWorkItems']} записей. Реальные запросы: "
              f"{s['historicallyAssessedSourceQueries']} исторически оценены, "
              f"{s['unassessedSourceQueries']} ещё без независимой оценки. Документация в этот знаменатель не входит.", '',
              'Вызовы API с доказанной связью включены в работу соответствующего запроса. '
              'Проектные вызовы исключены из очереди платформенного API. Неоднозначные API оставлены отдельно. '
              'Одинаковый текст в разных исходных местах не объединяется.', '',
              '| Состояние уникальной очереди | Количество |', '|---|---:|',
              *[f'| {k} | {v} |' for k, v in sorted(s['queueStatuses'].items())], '',
              '`needs-author-criterion`: запрос и символический метод планируются; нужны реальные входы, ожидания и исполнение. '
              '`needs-query-api-scenario`: внешний запрос планируется; необходима авторская обвязка API. '
              '`needs-author-scenario`: отсутствует явно заданный контекст/сессия/fixture. '
              '`blocked`: текущий разбор или план выявил ограничение. '
              '`needs-api-resolution`: получатель/сигнатура API или его связь с исходным запросом требуют проверки. '
              '`needs-api-scenario`: доказанная декларация динамического API требует авторского сценария; её потребители объединены. '
              '`assessed-historically`: есть прежнее доказательство; нового runtime здесь нет.', '',
              '## Версионные исключения', '',
              'Таблица контракта сущности исключена только из матрицы документации 9.3 по сохранённой '
              '[инвентаризации](../tests/corpus/virtual-tables/reference-9.3.json), где записано удаление после 7.0. '
              'Это сохранённое основание, а не новая проверка сайта платформы. Историческая запись и ссылка 9.1 сохранены. '
              'Реальные запросы архивов совместимости 9.0 с таким источником остаются в очереди `version-review`: '
              'необходим явный контракт совместимости/эмуляции, исключение по справке 9.3 их не закрывает.', '',
              'Текстовые требования state/rights вынесены из числа исполняемых примеров; требования состояния и прав остаются открытыми. '
              'Страницы документации не получают PASS от успешного похожего примера.', '',
              '## Работа по заданиям', '',
              'Области пересекаются. API может добавить связь с другим этапом; суммы строк не являются общей очередью.', '',
              '| Этап | Историческая область | Уникальные работы с учётом связанных API | Состояния работ |',
              '|---|---:|---:|---|']
    for stage, values in report['stages'].items():
        counts = ', '.join(f'{k}: {v}' for k, v in sorted(values['queueStatuses'].items()))
        lines.append(f"| {stage} | {values['historicalScopeCount']} | {values['uniqueWorkItems']} | {counts} |")
    lines += ['', '## Уникальные работы', '',
              '| contractId | Проект / источник | Этапы | Текущее состояние | Причина |', '|---|---|---|---|---|']
    for r in report['workItems']:
        source = f"{r['project']} / {r['file']}:{r['range'][0]}"
        reason = (r.get('reason') or '').replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {r['workId']} | {source} | {', '.join(r['stages'])} | {r['status']} | {reason} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=REPO)
    args = parser.parse_args()
    catalog, report = refresh(args.repo)
    from .query_catalog import markdown
    outputs = {'docs/query-contract-catalog.json': json.dumps(catalog, ensure_ascii=False, indent=2) + '\n',
               'docs/query-catalog-current.json': json.dumps(report, ensure_ascii=False, indent=2) + '\n',
               'docs/query-catalog-current.md': overview(report),
               'docs/query-contract-catalog.md': markdown(catalog)}
    for name, content in outputs.items():
        (args.repo / name).write_text(content)
    print(json.dumps(report['summary'], ensure_ascii=False))


if __name__ == '__main__':
    main()
