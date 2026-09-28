"""Execution causes remain distinct before SBSL grades the check."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from element_test.integration import run_integration
from element_test.runtime import run_pure
from element_test.yaml_io import InvalidTestError, UnsupportedSyntaxError


class ReasonCodesTest(unittest.TestCase):
    def test_runtime_preparation_causes(self):
        with tempfile.TemporaryDirectory() as directory, patch("element_test.runtime.shutil.which", return_value="docker"):
            model = {"compatibilityVersion": "9.0"}
            for error, status, reason in (
                (InvalidTestError("bad check"), "UNSUPPORTED", "invalid_test"),
                (UnsupportedSyntaxError("bad syntax"), "UNSUPPORTED", "unsupported_syntax"),
            ):
                with self.subTest(reason=reason), patch("element_test.runtime.prepare_script", side_effect=error):
                    result = run_pure(Path(directory), model, {}, Path(directory))
                    self.assertEqual((result["status"], result["reasonCode"]), (status, reason))

    def test_missing_docker_and_integration_contract(self):
        with tempfile.TemporaryDirectory() as directory, patch("element_test.runtime.shutil.which", return_value=None):
            result = run_pure(Path(directory), {}, {}, Path(directory))
        self.assertEqual((result["status"], result["reasonCode"]), ("UNSUPPORTED", "backend_unavailable"))
        result = run_integration({"integration": {"backend": "other", "operation": "sql-smoke"}},
                                 {}, Path("/tmp"), enabled=True)
        self.assertEqual((result["status"], result["reasonCode"]), ("UNSUPPORTED", "unsupported_contract"))
        result = run_integration({"integration": {"backend": "postgres"}}, {}, Path("/tmp"), enabled=True)
        self.assertEqual((result["status"], result["reasonCode"]), ("UNSUPPORTED", "invalid_test"))


if __name__ == "__main__":
    unittest.main()
