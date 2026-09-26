"""Opt-in tests of real student code; ELEMENT_TEST_DOCKER_TESTS=1."""
import copy
import os
from pathlib import Path
import tempfile
import unittest

from element_test.assignment import load_assignment
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import run_pure

REPO = Path(__file__).resolve().parent.parent


@unittest.skipUnless(os.environ.get("ELEMENT_TEST_DOCKER_TESTS") == "1", "Docker integration is opt-in")
class DockerTest(unittest.TestCase):
    def run_case(self, modify):
        archive = REPO / "Движок.tar"
        before = archive.read_bytes()
        with tempfile.TemporaryDirectory() as directory, open_project(archive) as root:
            model = analyze(root)
            check = copy.deepcopy(load_assignment(REPO / "assignments/poc-fio")["checks"][0])
            modify(check, model)
            result = run_pure(root, model, check, Path(directory))
        self.assertEqual(archive.read_bytes(), before)
        return result

    def test_real_method_and_input_interpolation_escape(self):
        text = '${Консоль.Записать("probe")}'
        result = self.run_case(lambda check, model: check.update(args=[text, "", ""]))
        self.assertEqual(result, {"status": "EXECUTED", "actual": text})

    def test_timeout(self):
        result = self.run_case(lambda check, model: check.update(timeout="0.001s"))
        self.assertEqual(result["status"], "TIMEOUT")

    def test_unconfigured_version(self):
        result = self.run_case(lambda check, model: model.update(compatibilityVersion="999.0"))
        self.assertEqual(result["status"], "UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
