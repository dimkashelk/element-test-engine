"""The transport must carry SBSL facts without exposing local execution details."""
import unittest

from element_test.grading import grading_package
from element_test.yaml_io import InputError


class GradingTest(unittest.TestCase):
    def setUp(self):
        self.result = {
            "project": "Пример", "sourceHash": "a" * 64, "status": "incomplete",
            "score": 2, "maxScore": 2, "unavailablePoints": 3,
            "checks": [
                {"id": "ok", "group": "Основное", "status": "PASS", "points": 2, "score": 2},
                {"id": "wait", "status": "TIMEOUT", "points": 3, "score": 0,
                 "message": "executor /Users/test/private", "reasonCode": "timeout", "expected": {"result": 5}},
            ],
        }
        self.assignment = {"name": "Задание"}
        self.model = {"compatibilityVersion": "9.0"}

    def test_identity_is_separate_from_assessed_facts(self):
        package = grading_package(self.result, self.assignment, self.model,
                                  student_id="student-1", assignment_id="task-1", run_id="run-1")
        self.assertEqual((package["score"], package["maxScore"], package["unavailablePoints"]), (2, 2, 3))
        self.assertEqual(package["studentId"], "student-1")
        self.assertEqual(package["feedback"][0]["group"], "Основное")
        self.assertEqual(package["feedback"][0]["expected"], None)
        self.assertEqual(package["feedback"][1]["expected"], {"result": 5})
        self.assertEqual(package["feedback"][1]["message"], "[redacted]")
        self.assertEqual(package["feedback"][1]["reasonCode"], "timeout")
        self.assertNotIn("/Users", str(package))

    def test_invalid_identity_rejected(self):
        with self.assertRaises(InputError):
            grading_package(self.result, self.assignment, self.model, student_id="/tmp/student")

    def test_unknown_reason_code_is_not_published(self):
        self.result["checks"][1]["reasonCode"] = "arbitrary details"
        package = grading_package(self.result, self.assignment, self.model)
        self.assertNotIn("reasonCode", package["feedback"][1])


if __name__ == "__main__":
    unittest.main()
