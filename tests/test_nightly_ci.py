"""CI must reject skipped/empty suites and preserve progress on interruption."""
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.ci.assets import digest, package, unpack
from tools.ci.run_nightly import run_suite, summary


class NightlyCiTest(unittest.TestCase):
    def run_example(self, case):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            code = run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(case), output,
                             {'commit': 'abc', 'startedAt': 'fixture'}, stream=io.StringIO())
            return code, json.loads(output.read_text())

    def test_failures_subtests_errors_and_skips_are_reported(self):
        class Example(unittest.TestCase):
            def test_success(self):
                pass
            def test_error(self):
                raise ValueError('fixture error')
            def test_subtest(self):
                with self.subTest(value=1):
                    self.fail('fixture failure')
            def test_skip(self):
                self.skipTest('fixture unavailable')
        code, data = self.run_example(Example)
        self.assertEqual(code, 1)
        self.assertEqual((data['status'], data['testsRun'], data['passed']), ('failed', 4, 1))
        self.assertEqual([len(data[key]) for key in ('errors', 'failures', 'skipped')], [1, 1, 1])
        self.assertEqual(len(data['timings']), 4)

    def test_only_skipped_and_empty_suites_are_not_green(self):
        class Skipped(unittest.TestCase):
            @unittest.skip('fixture')
            def test_skip(self):
                pass
        self.assertEqual(self.run_example(Skipped)[0], 1)
        class Empty(unittest.TestCase):
            pass
        self.assertEqual(self.run_example(Empty)[0], 1)
        class Passed(unittest.TestCase):
            def test_pass(self):
                pass
        self.assertEqual(self.run_example(Passed)[0], 0)

    def test_interruption_saves_completed_results(self):
        class Interrupted(unittest.TestCase):
            def test_a_pass(self):
                pass
            def test_b_interrupt(self):
                raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'result.json'
            with self.assertRaises(KeyboardInterrupt):
                run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(Interrupted), output,
                          {'commit': 'abc', 'startedAt': 'fixture'}, stream=io.StringIO())
            data = json.loads(output.read_text())
            self.assertEqual((data['status'], data['passed']), ('incomplete', 1))
            data['status'] = 'running'  # Hard-killed processes retain the last checkpoint.
            output.write_text(json.dumps(data))
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': ''}), \
                    unittest.mock.patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(summary(output, 'cancelled')['status'], 'incomplete')

    def test_bundle_roundtrip_readable_by_container_and_checksum_enforced(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / 'source'
            (source / 'script_u_10.0.2_1/lib').mkdir(parents=True)
            jar = source / 'script_u_10.0.2_1/lib/fixture.jar'
            jar.write_bytes(b'jar')
            jar.chmod(0o600)
            (source / 'Dvizhok.xdump').write_bytes(b'fixture')
            bundle = base / 'assets.tar.gz'
            checksum = package(bundle, source)
            destination = base / 'destination'
            destination.mkdir()
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                unpack(bundle, '0' * 64, destination)
            self.assertFalse(any(destination.iterdir()))
            unpack(bundle, checksum, destination)
            self.assertEqual((destination / 'Dvizhok.xdump').read_bytes(), b'fixture')
            self.assertEqual((destination / 'script_u_10.0.2_1/lib/fixture.jar').stat().st_mode & 0o777, 0o644)
            with self.assertRaisesRegex(ValueError, 'existing local assets'):
                unpack(bundle, checksum, destination)

    def test_bundle_rejects_traversal_and_links_before_extracting(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name, kind in [('script_u_10.0.2_1/../../escape', tarfile.REGTYPE),
                               ('script_u_10.0.2_1/link', tarfile.SYMTYPE),
                               ('README.md', tarfile.REGTYPE)]:
                bundle = base / 'bad.tar.gz'
                with tarfile.open(bundle, 'w:gz') as archive:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = '/tmp'
                    archive.addfile(member)
                with self.assertRaisesRegex(ValueError, 'Unexpected path'):
                    unpack(bundle, digest(bundle), base)
            self.assertFalse((base / 'script_u_10.0.2_1').exists())


if __name__ == '__main__':
    unittest.main()
