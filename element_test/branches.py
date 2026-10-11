"""Source-bound outcome probes. Probes never evaluate a source expression twice."""
from dataclasses import asdict
from hashlib import sha256
import json

from .indexer import parse_module
from .yaml_io import InvalidTestError, UnsupportedSyntaxError


HELPER = '''@Глобально
метод Condition(Id: Строка, Value: Булево): Булево
    Event(Id, Value ? "true" : "false")
    возврат Value
;
@Глобально
метод Event(Id: Строка, Outcome: Строка)
    Консоль.Записать("ELEMENT_TRACE " + СериализацияJson.ЗаписатьОбъект({"event": "branch", "branchId": Id, "outcome": Outcome}))
;
'''


def prepare(plan):
    value = plan.check.get('branches', False)
    if type(value) is not bool:
        raise InvalidTestError('branches требует Булево')
    if not value:
        return
    plan.observations['branches'] = True
    plan.branch_helper = 'ТестВетви' + sha256(plan.model['sourceHash'].encode()).hexdigest()[:16]
    if any(e['name'] == plan.branch_helper for e in plan.model['elements']) or any(m['name'] == plan.branch_helper for m in plan.model['modules']):
        raise UnsupportedSyntaxError('Конфликт технического модуля ветвей')
    plan.branches = []
    for symbol in plan.symbols:
        full = (plan.root / symbol.identity.source_file).read_text(encoding='utf-8-sig')
        file_hash = sha256((plan.root / symbol.identity.source_file).read_bytes()).hexdigest()
        tree = parse_module(symbol.source)[0][0].expression_tree
        selections = [n for n in tree.walk() if n.kind == 'block' and n.value == 'выбор']
        for node in tree.walk():
            condition = node.kind == 'conditional' or node.kind == 'statement' and node.value in {'если', 'иначеесли', 'иначе если', 'пока'}
            loop = node.kind == 'block' and node.value in {'для', 'пока'}
            selection = node.kind == 'block' and node.value == 'выбор'
            arm = node.kind == 'statement' and node.value == 'когда' or node.kind == 'control' and node.value == 'иначе' and any(n.start < node.start < n.end and node in n.children for n in selections)
            if not (condition or loop or selection or arm):
                continue
            if node.kind == 'conditional':
                expr = node.children[0]
            elif condition:
                if not node.children or node.children[0].kind == 'error':
                    raise UnsupportedSyntaxError('Условие вне AST контракта ветвей')
                expr = node.children[0]
            else:
                expr = node
            start, end = symbol.start + expr.start, symbol.start + expr.end
            record = {'symbol': asdict(symbol.identity), 'sourceFile': symbol.identity.source_file,
                      'fileHash': file_hash, 'projectSourceHash': plan.model['sourceHash'],
                      'range': [start, end], 'line': full.count('\n', 0, start) + 1,
                      'column': start - full.rfind('\n', 0, start),
                      'kind': 'condition' if condition else 'loop' if loop else 'selection' if selection else 'arm',
                      'construct': node.value or 'ternary',
                      'outcomes': ['true', 'false'] if condition else ['zero', 'nonzero', 'exhausted', 'break-exit', 'abrupt-exit'] if loop else ['enter', 'complete', 'no-match'] if selection else ['selected']}
            record['id'] = 'branch-' + sha256(json.dumps(record, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
            record['localRange'] = [expr.start, expr.end]
            plan.branches.append(record)


def edits(plan, symbol):
    """Insert at original offsets, alongside the adapter's verified replacements."""
    result = []
    source = symbol.source
    node = parse_module(source)[0][0]
    records = [r for r in plan.branches if r['symbol'] == asdict(symbol.identity)]
    def event(record, outcome):
        return plan.branch_helper + '.Event(' + json.dumps(record['id']) + ', ' + json.dumps(outcome) + ')'
    def variable(record, suffix):
        name = 'ТестЦикл' + record['id'].removeprefix('branch-') + suffix
        while name in source: name += '_'
        return name
    for record in records:
        a, b = record['localRange']
        if record['kind'] == 'condition':
            result.extend([(a, a, plan.branch_helper + '.Condition(' + json.dumps(record['id']) + ', ('), (b, b, '))')])
        elif record['kind'] in {'loop', 'selection'}:
            header = next(n for n in node.expression_tree.walk() if n.kind == 'statement' and n.start == a)
            header_end = source.index('\n', header.end)
            indent = source[source.rfind('\n', 0, a) + 1:a]
            if record['kind'] == 'loop':
                count, done, broken = [variable(record, s) for s in ('Count', 'Done', 'Break')]
                prefix = '\n'.join(['пер ' + count + ' = 0', 'пер ' + done + ' = Ложь', 'пер ' + broken + ' = Ложь', event(record, 'enter'), 'попытка'])
                result.append((a, a, prefix.replace('\n', '\n' + indent) + '\n' + indent))
                result.append((header_end + 1, header_end + 1, indent + '    ' + count + ' += 1\n' + indent + '    ' + event(record, 'iteration') + '\n'))
                tail = [done + ' = Истина', event(record, 'complete'), 'вконце',
                        plan.branch_helper + '.Event(' + json.dumps(record['id']) + ', ' + done + ' ? (' + broken + ' ? "break-exit" : "exhausted") : "abrupt-exit")',
                        plan.branch_helper + '.Event(' + json.dumps(record['id']) + ', ' + count + ' == 0 ? "zero" : "nonzero")', ';']
                result.append((b, b, indent + ('\n' + indent).join(tail) + '\n'))
            else:
                selected = variable(record, 'Selected')
                result.append((a, a, 'пер ' + selected + ' = Ложь\n' + indent + event(record, 'enter') + '\n' + indent))
                tail = indent + event(record, 'complete') + '\n' + indent + 'если не ' + selected + '\n' + indent + '    ' + event(record, 'no-match') + '\n' + indent + ';\n'
                result.append((b, b, tail))
        else:
            end = source.index('\n', a)
            indent = source[source.rfind('\n', 0, a) + 1:a]
            parent = min((r for r in records if r['kind'] == 'selection' and r['localRange'][0] < a < r['localRange'][1]), key=lambda r: r['localRange'][1]-r['localRange'][0])
            result.append((end + 1, end + 1, indent + '    ' + variable(parent, 'Selected') + ' = Истина\n' + indent + '    ' + event(record, 'selected') + '\n'))
    for item in node.expression_tree.walk():
        if item.kind == 'control' and item.value in {'прервать', 'продолжить'}:
            loops = [r for r in records if r['kind'] == 'loop' and r['localRange'][0] <= item.start < r['localRange'][1]]
            if loops:
                record = min(loops, key=lambda r: r['localRange'][1] - r['localRange'][0])
                indent = source[source.rfind('\n', 0, item.start) + 1:item.start]
                prefix = variable(record, 'Break') + ' = Истина\n' + indent if item.value == 'прервать' else ''
                result.append((item.start, item.start, prefix + event(record, 'break' if item.value == 'прервать' else 'continue') + '\n' + indent))
    return result


def summarize(records, trace):
    counts = {r['id']: {} for r in records}
    for event in trace:
        if event.get('event') == 'branch' and event.get('branchId') in counts:
            outcomes = counts[event['branchId']]
            key = event['outcome']; outcomes[key] = outcomes.get(key, 0) + 1
    conditions = [r for r in records if r['kind'] == 'condition']
    return {'schemaVersion': 1, 'technicalOnly': True,
            'denominator': 'condition outcomes in prepared source closure',
            'totalOutcomes': len(conditions) * 2,
            'coveredOutcomes': sum(outcome in counts[r['id']] for r in conditions for outcome in r['outcomes']),
            'records': [{**r, 'counts': counts[r['id']], 'status': 'observed' if counts[r['id']] else 'no-scenario'} for r in records],
            'unreachable': [], 'methodCoverageIsNotBranchCoverage': True}


def inventory(root, model, evidence=()):
    """Full-project static denominator, with exact source-bound runtime evidence."""
    from types import SimpleNamespace
    from .execution_plan import SourceSymbol, SymbolIdentity
    symbols, blocked = [], []
    props = model.get('properties', {})
    for module in model['modules']:
        source = (root/module['sourceFile']).read_text(encoding='utf-8-sig')
        nodes, _, errors = parse_module(source)
        if errors:
            blocked.append({'sourceFile': module['sourceFile'], 'reason': '; '.join(errors)})
            continue
        for node in nodes:
            if any(n.kind == 'error' for n in node.expression_tree.walk()):
                blocked.append({'sourceFile': module['sourceFile'], 'method': node.name, 'reason': 'Expression outside branch AST'})
                continue
            identity = SymbolIdentity(str(props.get('Поставщик', '')), str(props.get('Имя', '')),
                module['namespace'], module['name'], node.name, module['sourceFile'], str(props.get('Версия') or ''))
            symbols.append(SourceSymbol(identity, module, source[node.start:node.end], [], node.start, node.end))
    plan = SimpleNamespace(root=root, model=model, check={'branches': True}, observations={}, symbols=symbols)
    prepare(plan)
    known = {r['id']: r for r in plan.branches}; trace = []; rejected = 0
    for item in evidence:
        for record in item.get('branchCoverage', {}).get('records', []):
            current = known.get(record.get('id'))
            if current is None or any(current[k] != record.get(k) for k in ('projectSourceHash', 'fileHash', 'symbol', 'range')):
                rejected += 1; continue
            for outcome, count in record.get('counts', {}).items():
                if type(count) is not int or not 0 <= count <= 1000000:
                    raise InvalidTestError('Некорректный счётчик исходов в свидетельстве')
                # Keep bounded aggregation without duplicating the event journal.
                trace.append({'event': 'branch', 'branchId': current['id'], 'outcome': outcome, 'count': count})
    result = summarize(plan.branches, [])
    counts = {r['id']: {} for r in result['records']}
    for event in trace:
        count = counts[event['branchId']]; key = event['outcome']; count[key] = count.get(key, 0) + event['count']
    for record in result['records']:
        record['counts'] = counts[record['id']]
        record['status'] = 'observed' if record['counts'] else 'no-scenario'
    result['coveredOutcomes'] = sum(outcome in r['counts'] and r['counts'][outcome] > 0 for r in result['records'] if r['kind'] == 'condition' for outcome in r['outcomes'])
    result.update(sourceHash=model['sourceHash'], denominator='condition outcomes in parsed project methods',
                  methods=len(symbols), blockedMethods=blocked, rejectedEvidenceRecords=rejected,
                  completeDenominator=not blocked, sourceCompatibilityVersion=model['compatibilityVersion'],
                  platformSemantics='not-checked', execution='evidence-only' if evidence else 'not-checked')
    return result


def main():
    import argparse
    from pathlib import Path
    from .loader import open_project
    from .model import analyze
    parser = argparse.ArgumentParser(description='Read-only branch inventory, no business grading')
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--project-name')
    parser.add_argument('--evidence', action='append', default=[], type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source, output = args.project.resolve(), args.output.resolve()
    if output == source or source.is_dir() and output.is_relative_to(source):
        raise InvalidTestError('Инвентарь нельзя записывать во входной проект')
    before = sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    with open_project(source, args.project_name) as root:
        result = inventory(root, analyze(root), [e for path in args.evidence for e in json.loads(path.read_text())])
    if before and before != sha256(source.read_bytes()).hexdigest():
        raise InvalidTestError('Входной архив изменился')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
