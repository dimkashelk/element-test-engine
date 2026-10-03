"""Runner defaults must preserve explicit deadlines and bounded execution."""
import os
import unittest
from unittest.mock import patch

from element_test.runtime import execution_timeout


class RuntimeTimeoutTest(unittest.TestCase):
    def test_local_and_runner_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(execution_timeout({}), 5)
        with patch.dict(os.environ, {'ELEMENT_TEST_RUNTIME_TIMEOUT': '15s'}):
            self.assertEqual(execution_timeout({}), 15)

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
