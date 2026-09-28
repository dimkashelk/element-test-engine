"""Batch manifest, isolation, cache and per-submission failure regression."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from element_test.batch import load_manifest, run_batch
from element_test.runtime import REPO
from element_test.yaml_io import InputError


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.assignment = self.root / "assignment"
        self.assignment.mkdir()
        (self.assignment / "assignment.yaml").write_text(
            "name: Demo\nchecks:\n  - id: absent\n    type: element\n    name: Missing\n"
            "    exists: false\n    points: 2\n", encoding="utf-8")
        self.projects = []
        for number in range(3):
            project = self.root / f"project-{number}"
            project.mkdir()
            (project / "Проект.yaml").write_text(
                "Имя: Пример\nРежимСовместимости: 9.0\n", encoding="utf-8")
            self.projects.append(project)
        self.cache = self.root / "cache"

    def manifest(self, projects=None, ids=None):
        projects = projects or self.projects
        ids = ids or [f"student-{i}" for i in range(len(projects))]
        path = self.root / "manifest.json"
        data = {"schemaVersion": "1.0", "assignment": "assignment",
                "assignmentId": "task", "submissions": [
                    {"studentId": student, "project": project.name}
                    for student, project in zip(ids, projects)]}
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def run_it(self, manifest, output, workers, integration=False):
        with patch.dict(os.environ, {"ELEMENT_TEST_CACHE_DIR": str(self.cache)}):
            code = run_batch(manifest, output, workers, integration)
        return code, json.loads((output / "batch-result.json").read_text())

    def test_manifest_rejected_before_output(self):
        path = self.manifest(ids=["duplicate"] * 3)
        output = self.root / "out"
        with self.assertRaises(InputError):
            load_manifest(path, output)
        self.assertFalse(output.exists())
        path = self.manifest()
        with self.assertRaises(InputError):
            load_manifest(path, self.projects[0] / "reports")
        self.assertFalse((self.projects[0] / "reports").exists())
        duplicate_path = self.manifest([self.projects[0], self.projects[0]], ["one", "two"])
        with self.assertRaises(InputError):
            load_manifest(duplicate_path, output)
        self.assertFalse(output.exists())

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется локальный Script executor")
    def test_workers_cache_and_unique_packages(self):
        manifest = self.manifest()
        before = [(p / "Проект.yaml").read_bytes() for p in self.projects]
        code1, first = self.run_it(manifest, self.root / "out-1", 1)
        code2, second = self.run_it(manifest, self.root / "out-2", 2)
        self.assertEqual((code1, code2), (0, 0))
        self.assertEqual(first["counts"], second["counts"])
        self.assertEqual(first["counts"]["passed"], 3)
        self.assertEqual([x["cacheHit"] for x in first["submissions"]], [False, True, True])
        self.assertTrue(all(x["cacheHit"] for x in second["submissions"]))
        self.assertEqual(len({x["runId"] for x in second["submissions"]}), 3)
        for item in second["submissions"]:
            package = json.loads((self.root / "out-2" / item["grading"]).read_text())
            self.assertEqual(package["runId"], item["runId"])
            self.assertEqual(package["studentId"], item["studentId"])
        self.assertEqual(before, [(p / "Проект.yaml").read_bytes() for p in self.projects])
        self.assertNotIn(str(self.root), (self.root / "out-2/events.jsonl").read_text())

        # Any assignment byte or source byte changes the cache key.
        (self.assignment / "notes.txt").write_text("changed", encoding="utf-8")
        _, changed_assignment = self.run_it(manifest, self.root / "out-3", 2)
        self.assertTrue(any(not x["cacheHit"] for x in changed_assignment["submissions"]))
        (self.projects[0] / "Проект.yaml").write_text(
            "Имя: Другой\nРежимСовместимости: 9.0\n", encoding="utf-8")
        _, changed_source = self.run_it(manifest, self.root / "out-4", 1)
        self.assertFalse(changed_source["submissions"][0]["cacheHit"])
        runtimes = self.root / "runtimes.json"
        runtimes.write_text((REPO / "config/runtimes.json").read_text() + "\n", encoding="utf-8")
        with patch.dict(os.environ, {"ELEMENT_TEST_RUNTIMES": str(runtimes)}):
            _, changed_config = self.run_it(manifest, self.root / "out-5", 1)
            _, changed_mode = self.run_it(manifest, self.root / "out-6", 1, integration=True)
        self.assertFalse(changed_config["submissions"][0]["cacheHit"])
        self.assertFalse(changed_mode["submissions"][0]["cacheHit"])

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется локальный Script executor")
    def test_bad_archive_does_not_stop_queue(self):
        broken = self.root / "broken.tar"
        broken.write_bytes(b"not a tar archive")
        path = self.manifest([self.projects[0], broken, self.projects[2]])
        code, index = self.run_it(path, self.root / "out", 2)
        self.assertEqual(code, 1)
        self.assertEqual([x["status"] for x in index["submissions"]],
                         ["passed", "inputError", "passed"])
        self.assertNotIn("grading", index["submissions"][1])
        self.assertFalse((self.root / "out/submissions/000002/grading.json").exists())

    @unittest.skipUnless((REPO / "script_u_10.0.2_1/lib").is_dir(), "Требуется локальный Script executor")
    def test_sbsl_failure_and_unavailable_result_are_not_cached(self):
        config = self.assignment / "assignment.yaml"
        config.write_text("name: Demo\nchecks:\n  - id: negative\n    type: element\n"
                          "    name: Missing\n    points: 2\n", encoding="utf-8")
        path = self.manifest([self.projects[0]])
        code, failed = self.run_it(path, self.root / "failed", 1)
        self.assertEqual(code, 1)
        self.assertEqual(failed["counts"]["failed"], 1)
        self.assertEqual(failed["submissions"][0]["score"], 0)
        grade = json.loads((self.root / "failed/submissions/000001/grading.json").read_text())
        self.assertEqual(grade["feedback"][0]["status"], "FAIL")
        _, repeat = self.run_it(path, self.root / "repeat", 1)
        self.assertTrue(repeat["submissions"][0]["cacheHit"])

        config.write_text("name: Demo\nchecks:\n  - id: unavailable\n"
                          "    type: query\n    points: 2\n", encoding="utf-8")
        _, unavailable = self.run_it(path, self.root / "unavailable", 1)
        _, again = self.run_it(path, self.root / "again", 1)
        self.assertEqual(unavailable["counts"]["incomplete"], 1)
        self.assertFalse(unavailable["submissions"][0]["cacheHit"])
        self.assertFalse(again["submissions"][0]["cacheHit"])
        self.assertEqual(again["submissions"][0]["unavailablePoints"], 2)


if __name__ == "__main__":
    unittest.main()
