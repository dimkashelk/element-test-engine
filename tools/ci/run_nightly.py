"""Full unittest regression with durable progress and strict skip accounting."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / 'result/nightly/result.json'
REQUIRED_DUMPS = (
    'Dvizhok.xdump', 'Demo-SRM-dev-2026-09-28-21-38.xdump',
    'Prakticheskie-primery-2026-09-30-15-20.xdump', 'autocheck-2026-09-24-16-14.xdump',
)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def metadata():
    sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.strip()
    return {'schemaVersion': 1, 'commit': sha,
            'startedAt': datetime.now(timezone.utc).isoformat(),
            'runUrl': (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                       f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
                       if os.environ.get('GITHUB_RUN_ID') else None)}


def preflight():
    for key in ('ELEMENT_TEST_DOCKER_TESTS', 'ELEMENT_TEST_INTEGRATION_TESTS'):
        if os.environ.get(key) != '1':
            raise RuntimeError(key + '=1 is required for a full regression')
    for name in REQUIRED_DUMPS:
        if not (ROOT / name).is_file():
            raise RuntimeError('Required fixture missing: ' + name)
    if not (ROOT / 'script_u_10.0.2_1/lib').is_dir():
        raise RuntimeError('Script executor missing from the checkout')
    subprocess.run(['java', '-version'], check=True, capture_output=True, timeout=30)
    from element_test.integration import preflight as sql_preflight
    sql_preflight({'integration': {'backend': 'postgres', 'operation': 'sql-smoke'}},
                  {'compatibilityVersion': '9.0'}, enabled=True)


def run_suite(suite, output, info, stream=None):
    started = time.monotonic()
    data = {**info, 'status': 'running', 'testsRun': 0, 'passed': 0,
            'failures': [], 'errors': [], 'skipped': [], 'expectedFailures': [],
            'unexpectedSuccesses': [], 'timings': []}
    save(output, data)

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            self.test_started = time.monotonic()
            super().startTest(test)

        def stopTest(self, test):
            data['testsRun'] = self.testsRun
            data['timings'].append({'test': test.id(), 'seconds': round(time.monotonic() - self.test_started, 3)})
            data['seconds'] = round(time.monotonic() - started, 3)
            save(output, data)
            super().stopTest(test)

        def addSuccess(self, test):
            data['passed'] += 1
            super().addSuccess(test)

        def addFailure(self, test, err):
            super().addFailure(test, err)
            data['failures'].append({'test': test.id(), 'detail': self.failures[-1][1]})

        def addError(self, test, err):
            super().addError(test, err)
            data['errors'].append({'test': test.id(), 'detail': self.errors[-1][1]})

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                kind = 'failures' if issubclass(err[0], test.failureException) else 'errors'
                data[kind].append({'test': subtest.id(), 'detail': getattr(self, kind)[-1][1]})

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            data['skipped'].append({'test': test.id(), 'detail': reason})

        def addExpectedFailure(self, test, err):
            super().addExpectedFailure(test, err)
            data['expectedFailures'].append({'test': test.id(), 'detail': self.expectedFailures[-1][1]})

        def addUnexpectedSuccess(self, test):
            super().addUnexpectedSuccess(test)
            data['unexpectedSuccesses'].append({'test': test.id()})

    try:
        result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=Result).run(suite)
    except BaseException:
        data['status'] = 'incomplete'
        raise
    else:
        successful = result.wasSuccessful() and not result.skipped and result.testsRun > 0
        data['status'] = 'passed' if successful else 'failed'
    finally:
        data['seconds'] = round(time.monotonic() - started, 3)
        save(output, data)
    return 0 if data['status'] == 'passed' else 1


def form_corpus_summary(manifest, directory):
    """Report completed form checks separately from unsupported UI behavior."""
    expected=json.loads(manifest.read_text())['forms']
    reports=[json.loads(path.read_text()) for path in directory.glob('*/*/coverage.json')]
    return {'expected':expected,'completed':len(reports),
            'passed':sum(r.get('status')=='passed' for r in reports),
            'failed':sum(r.get('status')=='failed' for r in reports),
            'runtimeChecksExecuted':sum(r['runtimeChecksExecuted'] for r in reports),
            'expressionsWithoutRuntime':sum(len(r['notRuntimeChecked']) for r in reports)}


def summary(output, job_status='success'):
    data = json.loads(output.read_text()) if output.exists() else {
        **metadata(), 'status': 'not_started', 'message': 'Tests did not start; inspect setup steps.'}
    if data['status'] == 'running':
        data['status'] = 'incomplete'
    if data['status'] == 'passed' and job_status != 'success':
        data['status'] = 'incomplete'
    manifest=ROOT/'tests/corpus/all-dump-forms/manifest.json'
    if output==OUTPUT and manifest.exists():
        data['formCorpus']=form_corpus_summary(manifest,ROOT/'result/all-dump-forms/nightly')
    save(output, data)
    lines = ['# Nightly regression', '', f"Status: **{data['status']}**",
             f"Commit: `{data['commit']}`", f"Started (UTC): {data['startedAt']}"]
    if data.get('runUrl'):
        lines.append(f"[Workflow and logs]({data['runUrl']})")
    if 'testsRun' in data:
        lines += ['', '| Metric | Count |', '| --- | ---: |',
                  f"| Tests run | {data['testsRun']} |", f"| Passed | {data['passed']} |"]
        for key in ('failures', 'errors', 'skipped', 'expectedFailures', 'unexpectedSuccesses'):
            lines.append(f"| {key} | {len(data[key])} |")
        lines += ['', f"Duration: {data.get('seconds', 0):.1f} seconds."]
        for key in ('failures', 'errors', 'skipped', 'unexpectedSuccesses'):
            if data[key]:
                lines += ['', '## ' + key, '']
                lines += ['- `' + row['test'].replace('`', '') + '`' for row in data[key]]
        lines += ['', '## Slowest tests', '']
        for row in sorted(data['timings'], key=lambda row: row['seconds'], reverse=True)[:10]:
            lines.append(f"- `{row['test']}`: {row['seconds']:.1f}s")
    if data.get('message'):
        lines += ['', data['message']]
    if 'formCorpus' in data:
        forms=data['formCorpus']
        lines += ['', '## Reference forms', '',
                  f"Forms completed: {forms['completed']}/{forms['expected']}; passed: {forms['passed']}; failed: {forms['failed']}.",
                  f"Executed supported criteria: {forms['runtimeChecksExecuted']}.",
                  f"Expressions checked only in the declaration baseline: {forms['expressionsWithoutRuntime']}.",
                  'Per-form results, observations, plans and runtime limitations: result/all-dump-forms/nightly/.',
                  'Declaration baselines do not establish native/browser behavior.']
    lines += ['', 'Detailed results and generated reports are in the workflow artifacts.']
    text = '\n'.join(lines) + '\n'
    output.with_name('summary.md').write_text(text, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as destination:
            destination.write(text)
    print(text)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary-only', action='store_true')
    args = parser.parse_args()
    if args.summary_only:
        summary(OUTPUT, os.environ.get('NIGHTLY_JOB_STATUS', 'success'))
        return 0
    info = metadata()
    try:
        preflight()
    except Exception as exc:
        save(OUTPUT, {**info, 'status': 'setup_error', 'message': str(exc)})
        print(str(exc), file=sys.stderr)
        return 2
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'))
    return run_suite(suite, OUTPUT, info)


if __name__ == '__main__':
    sys.exit(main())
