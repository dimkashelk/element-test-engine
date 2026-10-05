"""Parallel CI must run every test exactly once and reject incomplete evidence."""
from collections import Counter
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tools.ci import run_nightly as nightly
from tools.ci.run_nightly import run_suite
from tools.ci.sharding import aggregate_reports, shard_suites, test_cases


def named_case(name):
    class NamedCase(unittest.TestCase):
        def id(self):
            return name

        def runTest(self):
            pass
    return NamedCase()


class NightlyShardingTest(unittest.TestCase):
    def fixtures(self):
        return [named_case(f'test_module_{module}.Case.test_{index}')
                for module in range(4) for index in range(module + 1)]

    def test_balanced_deterministic_allocation_keeps_modules_and_unknown_tests(self):
        tests = self.fixtures()
        weights = {'test_module_0': 1000, 'test_module_1': 500, 'test_module_2': 500}
        shards, plans = shard_suites(unittest.TestSuite(tests), 3, weights)
        actual = [test.id() for shard in shards for test in test_cases(shard)]
        self.assertEqual(Counter(actual), Counter(test.id() for test in tests))
        self.assertEqual(len(plans[0]['modules']), 1)  # Largest module gets its own runner.
        _, reordered = shard_suites(unittest.TestSuite(reversed(tests)), 3, weights)
        self.assertEqual([plan['modules'] for plan in plans], [plan['modules'] for plan in reordered])
        self.assertEqual(len({plan['suiteFingerprint'] for plan in plans}), 1)
        self.assertEqual(sum(len(plan['expectedTests']) for plan in plans), len(tests))

    def test_real_suite_has_complete_non_overlapping_module_coverage(self):
        root = Path(__file__).resolve().parents[1]
        suite = unittest.defaultTestLoader.discover(str(root / 'tests'))
        original = list(test_cases(suite))
        self.assertFalse([test for test in original if isinstance(test, unittest.loader._FailedTest)])
        weights = json.loads((root / 'config/nightly-module-timings.json').read_text())['moduleSeconds']
        shards, plans = shard_suites(suite, 4, weights)
        actual = [test.id() for shard in shards for test in test_cases(shard)]
        self.assertEqual(Counter(actual), Counter(test.id() for test in original))
        self.assertTrue(all(plan['expectedTests'] for plan in plans))
        owners = {}
        for plan in plans:
            for module in plan['modules']:
                self.assertNotIn(module, owners)
                owners[module] = plan['index']

    def test_invalid_allocation_is_rejected(self):
        for count in (0, -1, 5):
            with self.subTest(count=count), self.assertRaises(ValueError):
                shard_suites(unittest.TestSuite(self.fixtures()), count, {})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            shard_suites(unittest.TestSuite([named_case('a.b.c'), named_case('a.b.c')]), 1, {})

    def reports(self, directory):
        shards, plans = shard_suites(unittest.TestSuite(self.fixtures()), 4, {})
        paths = []
        for shard, plan in zip(shards, plans):
            path = directory / f'shard-{plan["index"]}' / 'nightly/result.json'
            run_suite(shard, path, {'commit': 'abc', 'startedAt': 'fixture', 'shard': plan},
                      stream=io.StringIO())
            paths.append(path)
        return paths

    def test_aggregate_completed_real_shards_and_form_reports(self):
        with TemporaryDirectory() as directory:
            paths = self.reports(Path(directory))
            for index, path in enumerate(paths):
                report = json.loads(path.read_text())
                report['formCorpus'] = {'expected': 3, 'completed': int(index < 3),
                                       'passed': int(index < 3), 'failed': 0,
                                       'runtimeChecksExecuted': 2 * int(index < 3),
                                       'expressionsWithoutRuntime': int(index < 3)}
                path.write_text(json.dumps(report))
            data = aggregate_reports(paths, 4, {'commit': 'abc'})
            self.assertEqual((data['status'], data['testsRun'], data['passed']), ('passed', 10, 10))
            self.assertEqual((len(data['shards']), len(data['timings'])), (4, 10))
            self.assertEqual(data['formCorpus']['completed'], 3)
            self.assertEqual(data['formCorpus']['runtimeChecksExecuted'], 6)
            self.assertEqual(data['formCorpus']['expressionsWithoutRuntime'], 3)

    def test_aggregate_rejects_missing_duplicate_wrong_commit_and_malformed_reports(self):
        with TemporaryDirectory() as directory:
            paths = self.reports(Path(directory))
            for selected, commit in ((paths[:-1], 'abc'), (paths + [paths[0]], 'abc'),
                                     (paths, 'wrong'), ([], 'abc')):
                with self.subTest(paths=len(selected), commit=commit):
                    self.assertEqual(aggregate_reports(selected, 4, {'commit': commit})['status'], 'incomplete')
            paths[0].write_text('invalid JSON')
            self.assertEqual(aggregate_reports(paths, 4, {'commit': 'abc'})['status'], 'incomplete')

    def test_aggregate_rejects_partial_foreign_duplicate_or_changed_test_plans(self):
        with TemporaryDirectory() as directory:
            paths = self.reports(Path(directory))
            original = json.loads(paths[0].read_text())
            mutations = []
            changed = deepcopy(original); changed['status'] = 'running'; mutations.append(changed)
            changed = deepcopy(original); changed['timings'] = []; mutations.append(changed)
            changed = deepcopy(original); changed['timings'][0]['test'] = 'foreign.Case.test'; mutations.append(changed)
            changed = deepcopy(original); changed['timings'].append(changed['timings'][0]); mutations.append(changed)
            changed = deepcopy(original); changed['shard']['suiteFingerprint'] = 'wrong'; mutations.append(changed)
            changed = deepcopy(original); changed['shard']['expectedTests'] = []; mutations.append(changed)
            changed = deepcopy(original); changed['shard']['count'] = 3; mutations.append(changed)
            changed = deepcopy(original); changed['status'] = 'setup_error'; mutations.append(changed)
            changed = deepcopy(original); changed['passed'] = 0; mutations.append(changed)
            changed = deepcopy(original); del changed['shard']['expectedTests']; mutations.append(changed)
            changed = deepcopy(original); changed['timings'] = [{}]; mutations.append(changed)
            for index, changed in enumerate(mutations):
                with self.subTest(mutation=index):
                    paths[0].write_text(json.dumps(changed))
                    self.assertEqual(aggregate_reports(paths, 4, {'commit': 'abc'})['status'], 'incomplete')

    def test_aggregate_propagates_failures_errors_skips_and_unexpected_successes(self):
        with TemporaryDirectory() as directory:
            paths = self.reports(Path(directory))
            original = json.loads(paths[0].read_text())
            for key in ('failures', 'errors', 'skipped', 'unexpectedSuccesses'):
                report = deepcopy(original)
                report['status'] = 'failed'
                report['passed'] -= 1
                report[key] = [{'test': report['timings'][0]['test'], 'detail': 'fixture'}]
                paths[0].write_text(json.dumps(report))
                with self.subTest(key=key):
                    data = aggregate_reports(paths, 4, {'commit': 'abc'})
                    self.assertEqual((data['status'], data['passed']), ('failed', 9))
                    self.assertEqual(len(data[key]), 1)

    def test_aggregate_cli_writes_summary_and_fails_for_runner_job_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.reports(root / 'shards')
            output = root / 'combined/result.json'
            args = ['run_nightly.py', '--aggregate', str(root / 'shards'), '--shard-count', '4']
            with patch.object(nightly, 'OUTPUT', output), \
                    patch.object(nightly, 'metadata', return_value={'commit': 'abc', 'startedAt': 'fixture'}), \
                    patch('sys.argv', args), patch('sys.stdout', new_callable=io.StringIO):
                with patch.dict('os.environ', {'NIGHTLY_JOB_STATUS': 'success', 'GITHUB_STEP_SUMMARY': ''}):
                    self.assertEqual(nightly.main(), 0)
                    data = json.loads(output.read_text())
                    self.assertEqual((data['status'], data['testsRun']), ('passed', 10))
                    self.assertIn('Parallel runners', output.with_name('summary.md').read_text())
                with patch.dict('os.environ', {'NIGHTLY_JOB_STATUS': 'failure', 'GITHUB_STEP_SUMMARY': ''}):
                    self.assertEqual(nightly.main(), 1)
                    self.assertEqual(json.loads(output.read_text())['status'], 'incomplete')


if __name__ == '__main__':
    unittest.main()
