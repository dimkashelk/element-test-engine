"""Summarize saved acceptance evidence; never execute student code or discovery."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / 'result/unions-nesting'


def read(path):
    return json.loads(path.read_text())


def receipt(path):
    return {'file': str(path.relative_to(REPO)), 'sha256': sha256(path.read_bytes()).hexdigest()}


def main():
    methods, failures, logs = {}, [], []
    for log in sorted(OUT.glob('*.log'), key=lambda p: p.stat().st_mtime):
        body = log.read_text()
        logs.append({**receipt(log), 'summary': re.findall(r'^Ran \d+ tests?.*|^FAILED.*|^OK.*', body, re.M)})
        for line in body.splitlines():
            match = re.match(r'test_\w+ \(([^)]+)\) \.\.\. (ok|FAIL|ERROR|skipped.*)$', line)
            if match:
                key, status = match.groups()
                methods[key] = {'status': status, 'evidence': str(log.relative_to(REPO))}
        if 'FAILED' in body:
            failures.append({**receipt(log), 'failures': re.findall(r'^(?:FAIL|ERROR):.*', body, re.M)})
    assert methods and all(v['status'] == 'ok' for v in methods.values()), methods
    public = []
    for folder in ('public-real-test', 'public-real-run', 'public-control'):
        result = read(OUT / folder / 'result.json')
        public.append({'folder': str((OUT / folder).relative_to(REPO)),
                       **{k: result[k] for k in ('sourceHash', 'score', 'maxScore', 'unavailablePoints')},
                       'checks': [c['status'] for c in result['checks']]})
    batches = []
    for folder in read(OUT / 'batch-latest.json'):
        result = read(REPO / folder / 'batch-result.json')
        scores = [s['score'] for s in result['submissions']]
        hits = [s['cacheHit'] for s in result['submissions']]
        assert scores == [3, 0, 3] and not any(hits)
        batches.append({'folder': folder, 'counts': result['counts'], 'scores': scores, 'cacheHits': hits})
    sql = []
    for path in sorted(OUT.glob('sql-*.json')):
        result = read(path)
        assert result['status'] == 'EXECUTED' and result['integration']['cleanup']
        sql.append({**receipt(path), 'status': result['status'], 'cleanup': True})
    mutations = []
    for label in ('Union', 'All', 'Nested', 'NestedNull', 'Temp', 'Compound', 'TempJoin'):
        runtime = OUT / ('mutation-' + label + '.json')
        assessment = OUT / ('mutation-assessment-' + label + '.json')
        assert read(runtime)['status'] == 'EXECUTED' and read(assessment) == {'status': 'FAIL', 'engine': 'SBSL'}
        mutations.append({'label': label, 'runtime': receipt(runtime), 'assessment': receipt(assessment)})
    coverage = read(REPO / 'docs/query-stage-042-coverage.json')
    report = {
        'stage': 42, 'date': '2026-10-04', 'status': 'implemented-within-unions-nesting-scope',
        'reference': coverage['reference'], 'executor': read(REPO / 'config/runtimes.json')['9.3'],
        'sourceCompatibilityVersions': ['9.0', '9.3'],
        'catalog': {'scopeCount': coverage['scopeCount'], 'counts': coverage['counts'],
                    'familyCounts': dict(Counter(r['family'] for r in coverage['contracts'])),
                    'archives': coverage['archives'], 'scopeOnlyRefresh': True,
                    'completeQueryCatalogExecuted': False,
                    'assessedOccurrences': [{k: r.get(k) for k in ('contractId', 'archive', 'fixture', 'fixtureHash', 'file', 'range', 'sourceHash', 'projectSourceHash', 'target', 'criteria', 'evidence')}
                                            for r in coverage['contracts'] if r['independentlyAssessed']]},
        'tests': {'distinctSuccessfulMethods': len(methods),
                  'newMethods': sum(k.startswith('test_query_composites.') for k in methods),
                  'affectedLegacyMethods': sum(not k.startswith('test_query_composites.') for k in methods),
                  'latestSuccessfulEvidence': methods, 'initialFailuresRetained': failures, 'logs': logs,
                  'scopeDeviation': {'evidence': 'result/unions-nesting/binding-final.log',
                                     'reason': 'An overbroad FillPlanTest class selector also ran two inventory/evidence tests, including one complete read-only build_catalog. No repeat or full suite was run; the committed catalog refresh still uses only the saved 46-record scope.'}},
        'public': public, 'batches': batches, 'schemas': read(OUT / 'schemas.json'),
        'sql': sql, 'mutations': mutations, 'native': read(OUT / 'native/results.json'),
        'nativeArtifacts': [receipt(p) for p in sorted((OUT / 'native').glob('*')) if p.is_file()],
        'preservedFailureArtifacts': [receipt(p) for p in sorted((OUT / 'first-failures').rglob('*')) if p.is_file()],
        'portableCases': {'core': 56, 'empty': 7, 'edges': 6, 'documentedExactTemplates': 5,
                          'directRealRoot': 1, 'directRealCriteria': 5},
        'limits': ['indices are validated logical hints, no physical index performance proof',
                   'current-user context is an explicit identity fixture, not authentication/rights',
                   'correlated/EXISTS subqueries and extended temporary field definitions unsupported',
                   'mixed virtual table/table-part/rights/library sources remain queued'],
        'localExclusions': ['303 forms', '44 previous roots as a complete set', 'four validate',
                            'full regression suite', 'nightly execution', 'native XBQL/platform compilation', 'browser UI'],
        'preserved': ['four source archives and SHA-256', 'previous assignment expected', 'runtime/input limits', 'CI schedule', 'unrelated workspace output'],
        'legacyFixtureAdjustment': 'Only the Unsupported method in the task-41 fixture builder and two generated copies changed: its previously unsupported UNION is now valid, so the negative probe uses incompatible numeric/string types. Previous expected values remain unchanged.',
        'nextStage': '043-virtual-tables',
    }
    (REPO / 'docs/query-stage-042-measurements.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print({'tests': len(methods), 'new': report['tests']['newMethods'], 'legacy': report['tests']['affectedLegacyMethods'],
           'mutations': len(mutations), 'sql': len(sql), 'schemas': report['schemas'], 'catalog': coverage['counts']})


if __name__ == '__main__':
    main()
