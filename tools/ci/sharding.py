"""Balance whole unittest modules and verify all isolated runner results."""
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
import unittest

OUTCOMES = ('failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses')


def test_cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_cases(item)
        else:
            yield item


def fingerprint(ids):
    return sha256('\n'.join(sorted(ids)).encode()).hexdigest()


def shard_suites(suite, count, weights):
    if count < 1:
        raise ValueError('Shard count must be positive')
    modules = defaultdict(list)
    for test in test_cases(suite):
        modules[test.id().split('.')[0]].append(test)
    ids = [test.id() for tests in modules.values() for test in tests]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate test IDs in discovered suite')
    if count > len(modules):
        raise ValueError('Shard count exceeds test module count')
    # Keep module/class setup and teardown in one process. Unknown modules still
    # participate automatically; weights affect placement, never inclusion.
    estimates = {name: max(1, weights.get(name, max(60, len(tests) * 5)))
                 for name, tests in modules.items()}
    loads = [0.0] * count
    assigned = [[] for _ in range(count)]
    for name in sorted(modules, key=lambda name: (-estimates[name], name)):
        index = min(range(count), key=lambda index: (loads[index], index))
        assigned[index].append(name)
        loads[index] += estimates[name]
    suites, plans = [], []
    for index, names in enumerate(assigned):
        tests = [test for name in sorted(names) for test in modules[name]]
        suites.append(unittest.TestSuite(tests))
        plans.append({'index': index, 'count': count, 'modules': sorted(names),
                      'expectedTests': [test.id() for test in tests],
                      'estimatedSeconds': round(loads[index], 3),
                      'suiteCount': len(ids), 'suiteFingerprint': fingerprint(ids)})
    return suites, plans


def aggregate_reports(paths, count, info):
    data = {**info, 'status': 'incomplete', 'testsRun': 0, 'passed': 0,
            'timings': [], 'seconds': 0, 'totalRunnerSeconds': 0,
            'shards': [], **{key: [] for key in OUTCOMES}}
    issues, reports = [], []
    for path in sorted(paths):
        try:
            report = json.loads(path.read_text())
            shard = report['shard']
            if shard['count'] != count or not 0 <= shard['index'] < count:
                raise ValueError('Invalid shard identity')
            if report['commit'] != info['commit']:
                raise ValueError('Report belongs to a different commit')
            planned = shard['expectedTests']
            if not isinstance(planned, list) or not all(isinstance(test, str) for test in planned):
                raise ValueError('Invalid planned test IDs')
            if not isinstance(shard['suiteCount'], int) or not isinstance(shard['suiteFingerprint'], str):
                raise ValueError('Invalid full suite identity')
            observed = report.get('timings', [])
            if not isinstance(observed, list) or not all(
                    isinstance(row, dict) and isinstance(row.get('test'), str)
                    and isinstance(row.get('seconds'), (int, float))
                    and math.isfinite(row['seconds']) and row['seconds'] >= 0 for row in observed):
                raise ValueError('Invalid completed test timings')
            if any(not isinstance(report.get(key, 0), int) or report.get(key, 0) < 0
                   for key in ('testsRun', 'passed')):
                raise ValueError('Invalid result counts')
            if any(not isinstance(report.get(key, []), list) or not all(
                    isinstance(row, dict) and isinstance(row.get('test'), str)
                    for row in report.get(key, [])) for key in OUTCOMES):
                raise ValueError('Invalid outcome lists')
            seconds = report.get('seconds', 0)
            if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError('Invalid runner duration')
            if report['status'] == 'passed' and (
                    report.get('passed', 0) + len(report.get('expectedFailures', [])) != report.get('testsRun', 0)
                    or any(report.get(key) for key in ('failures', 'errors', 'skipped', 'unexpectedSuccesses'))):
                raise ValueError('Passing status contradicts test outcomes')
            reports.append(report)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append(f'Invalid report {path}: {exc}')
    indices = Counter(report['shard']['index'] for report in reports)
    for index in range(count):
        if indices[index] != 1:
            issues.append(f'Shard {index}: expected one report, found {indices[index]}')
    expected, completed = [], []
    for report in reports:
        shard = report['shard']
        planned = shard['expectedTests']
        observed = [row['test'] for row in report.get('timings', [])]
        expected.extend(planned)
        completed.extend(observed)
        if Counter(observed) != Counter(planned):
            issues.append(f'Shard {shard["index"]}: completed tests differ from its plan')
        if report.get('testsRun', 0) != len(observed):
            issues.append(f'Shard {shard["index"]}: result count differs from completed test IDs')
        if report['status'] not in ('passed', 'failed'):
            issues.append(f'Shard {shard["index"]}: {report["status"]}')
        data['testsRun'] += report.get('testsRun', 0)
        data['passed'] += report.get('passed', 0)
        data['timings'].extend(report.get('timings', []))
        for key in OUTCOMES:
            data[key].extend(report.get(key, []))
        seconds = report.get('seconds', 0)
        data['seconds'] = max(data['seconds'], seconds)
        data['totalRunnerSeconds'] += seconds
        data['shards'].append({'index': shard['index'], 'status': report['status'],
                               'testsRun': report.get('testsRun', 0),
                               'planned': len(planned), 'seconds': seconds})
        if report.get('message'):
            issues.append(f'Shard {shard["index"]}: {report["message"]}')
    if len(expected) != len(set(expected)) or len(completed) != len(set(completed)):
        issues.append('Duplicate test IDs across shards')
    for report in reports:
        shard = report['shard']
        if len(expected) != shard['suiteCount'] or fingerprint(expected) != shard['suiteFingerprint']:
            issues.append('Shard plans do not cover the same complete suite')
            break
    data['plannedTests'] = len(expected)
    forms = [report['formCorpus'] for report in reports if 'formCorpus' in report]
    if forms:
        data['formCorpus'] = {'expected': forms[0]['expected'],
                             **{key: sum(form[key] for form in forms) for key in
                                ('completed', 'passed', 'failed', 'runtimeChecksExecuted',
                                 'expressionsWithoutRuntime')}}
        if data['formCorpus']['completed'] != data['formCorpus']['expected']:
            issues.append('Reference form coverage is incomplete')
    if issues:
        data['message'] = '\n'.join(issues)
    elif not expected:
        data['message'] = 'No tests were planned'
    else:
        failed = any(report['status'] != 'passed' for report in reports)
        failed |= any(data[key] for key in ('failures', 'errors', 'skipped', 'unexpectedSuccesses'))
        data['status'] = 'failed' if failed else 'passed'
    return data
