"""Runner defaults must preserve explicit deadlines and bounded execution."""
import os
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.assignment import load_assignment
from element_test.runtime import REPO, execution_timeout, run_pure
from element_test.integration import EXECUTOR_TIMEOUT, executor_command


class RuntimeTimeoutTest(unittest.TestCase):
    def test_ci_cpu_budget_reaches_both_docker_executors(self):
        def prepare(root, model, check, directory):
            (directory / 'execution-plan.json').write_text(json.dumps({}))
            return directory / 'test.sbsl'

        with TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'ELEMENT_TEST_RUNTIME_TIMEOUT': '15s'}, clear=True), \
                patch('element_test.runtime.prepare_script', side_effect=prepare), \
                patch('element_test.runtime.shutil.which', return_value='/docker'), \
                patch('element_test.runtime.subprocess.run', return_value=(
                    subprocess.CompletedProcess([], 1, '', 'fixture image absent'))) as command:
            result = run_pure(None, {'compatibilityVersion': '9.0'}, {}, Path(directory))
            self.assertEqual(result['reasonCode'], 'backend_unavailable')
            self.assertIn('cpu=15:15', command.call_args.args[0])
            self.assertNotIn('cpu=8:8', command.call_args.args[0])
        command = executor_command({'image': 'fixture'}, Path('/runtime'), Path('/inputs'),
                                   'network', 'executor', '9.0')
        self.assertIn('cpu=30:30', command)
        self.assertEqual(EXECUTOR_TIMEOUT, 30)

    def test_local_and_runner_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(execution_timeout({}), 5)
        with patch.dict(os.environ, {'ELEMENT_TEST_RUNTIME_TIMEOUT': '15s'}):
            self.assertEqual(execution_timeout({}), 15)

    def test_fio_assignment_uses_runner_deadline(self):
        with patch.dict(os.environ, {'ELEMENT_TEST_RUNTIME_TIMEOUT': '15s'}):
            for assignment in ('poc-fio', 'dvizhok-runtime-regression'):
                checks = [check for check in load_assignment(REPO / 'assignments' / assignment)['checks']
                          if check['target']['method'] == 'СформироватьФИО']
                with self.subTest(assignment=assignment):
                    self.assertEqual([execution_timeout(check) for check in checks], [15] * 3)

    def test_explicit_deadline_wins_even_with_invalid_runner_default(self):
        with patch.dict(os.environ, {'ELEMENT_TEST_RUNTIME_TIMEOUT': 'invalid'}):
            self.assertEqual(execution_timeout({'timeout': '0.001s'}), 0.001)
            self.assertEqual(execution_timeout({'timeout': 30}), 30)

    def test_unbounded_or_invalid_deadlines_are_rejected(self):
        for value in ('nan', 'inf', '-inf', 'invalid', '0s', '-1s', '31s', None):
            with self.subTest(value=value), patch.dict(os.environ, {'ELEMENT_TEST_RUNTIME_TIMEOUT': str(value)}):
                with self.assertRaises(ValueError):
                    execution_timeout({})
                with self.assertRaises(ValueError):
                    execution_timeout({'timeout': value})
