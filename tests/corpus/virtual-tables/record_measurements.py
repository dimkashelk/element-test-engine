"""Summarize retained evidence without executing sources or full discovery."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / 'result/virtual-tables'


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
            if match and not match[1].startswith('unittest.loader._FailedTest.'):
                methods[match[1]] = {'status': match[2], 'evidence': str(log.relative_to(REPO))}
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
    for label in ('First', 'Balance', 'Turnover', 'Rows', 'Nested', 'Phones'):
        runtime = OUT / ('mutation-' + label + '.json')
        assessment = OUT / ('mutation-assessment-' + label + '.json')
        assert read(runtime)['status'] == 'EXECUTED' and read(assessment) == {'status': 'FAIL', 'engine': 'SBSL'}
        mutations.append({'label': label, 'runtime': receipt(runtime), 'assessment': receipt(assessment)})
    assert read(OUT / 'infrastructure-failure.json')['status'] == 'ERROR'
    assert read(OUT / 'infrastructure-recovery.json')['status'] == 'EXECUTED'
    assert read(OUT / 'assessment-alternative.json')['status'] == 'PASS'
    coverage = read(REPO / 'docs/query-stage-043-coverage.json')
    report = {
        'stage': 43, 'date': '2026-10-05', 'status': 'implemented-within-virtual-source-executor-scope',
        'reference': coverage['reference'], 'executor': read(REPO / 'config/runtimes.json')['9.3'],
        'sourceCompatibilityVersions': ['9.0', '9.3'],
        'catalog': {'scopeCount': coverage['scopeCount'], 'counts': coverage['counts'],
                    'familyCounts': dict(Counter(r['family'] for r in coverage['contracts'])),
                    'archives': coverage['archives'], 'scopeOnlyRefresh': True,
                    'completeQueryCatalogExecuted': False,
                    'outsideScopeUnchanged': read(OUT / 'catalog-isolation.json'),
                    'assessedOccurrences': [{k: r.get(k) for k in ('contractId', 'archive', 'fixture', 'fixtureHash', 'file', 'range', 'sourceHash', 'projectSourceHash', 'target', 'criteria', 'evidence')}
                                            for r in coverage['contracts'] if r['independentlyAssessed']]},
        'tests': {'distinctSuccessfulMethods': len(methods),
                  'newMethods': sum(k.startswith('test_query_sources.') for k in methods),
                  'affectedLegacyMethods': sum(not k.startswith('test_query_sources.') for k in methods),
                  'latestSuccessfulEvidence': methods, 'initialFailuresRetained': failures, 'logs': logs,
                  'selectorMistake': 'Two unavailable legacy method names produced loader errors; their actual methods passed in legacy-methods-repeat.log. No additional discovery was run.'},
        'public': public, 'batches': batches, 'schemas': read(OUT / 'schemas.json'),
        'sql': sql, 'mutations': mutations, 'native': read(OUT / 'native/results.json'),
        'metadataValidation': {'evidence': receipt(OUT / 'metadata-final.json'),
                               'summary': read(OUT / 'metadata-final.json')['summary']},
        'nativeArtifacts': [receipt(p) for p in sorted((OUT / 'native').glob('*')) if p.is_file()],
        'preservedFailureArtifacts': [receipt(p) for p in sorted((OUT / 'first-failures').rglob('*')) if p.is_file()],
        'initialProbes': [receipt(p) for p in sorted(OUT.glob('first-*.json'))],
        'portableCases': {'core': 46, 'empty': 9, 'boundaries': 6, 'extraCaptureAndMultiResourceCases': 7,
                          'documentedExactTemplates': 4, 'directRealRoot': 1, 'directRealCriteria': 2},
        'limits': ['native XBQL and access rights are not implemented by standalone Script',
                   'virtual-source filter arguments and explicit/auto period expansion unsupported',
                   'exchange Changes requires platform change registration, not movement delta',
                   'users use six explicit identity fields; locale/authentication/rights unsupported',
                   'entity-contract tables excluded after platform 7.0',
                   'exchange plans, undeleted objects and settings require native lifecycle',
                   'saved definitions support only no-parameter non-null results; external & parameters queued for 44'],
        'localExclusions': ['303 forms', '44 previous roots as a complete set', 'four validate',
                            'full discovery', 'full regression suite', 'nightly execution', 'platform compilation', 'browser UI'],
        'preserved': ['four source archives and SHA-256', 'previous assignment expected', 'runtime/input limits', 'CI schedule', 'unrelated workspace output'],
        'legacyFixtureAdjustment': 'Only negative source probes changed: first slice is now valid, so two task-33 Unsupported methods use Changes; numeric IN is now valid, so task-31 dynamic probe and task-40 builder/two fixtures use incompatible numeric/string members. Previous expected values remain unchanged. The old wrong Date boundary now expects a parameter type error for second-period metadata.',
        'nextStage': '044-result-and-resources',
    }
    (REPO / 'docs/query-stage-043-measurements.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print({'tests': len(methods), 'new': report['tests']['newMethods'], 'legacy': report['tests']['affectedLegacyMethods'],
           'mutations': len(mutations), 'sql': len(sql), 'schemas': report['schemas'], 'catalog': coverage['counts']})


if __name__ == '__main__':
    main()
