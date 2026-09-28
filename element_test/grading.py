"""Versioned, path-free grading transport built from SBSL's assessed result."""
import re

from .yaml_io import InputError

VERSION = "1.0"
REASON_CODES = {"invalid_test", "unsupported_syntax", "unsupported_contract",
                "backend_unavailable", "execution_error", "timeout",
                "result_mismatch", "skipped"}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SENSITIVE = re.compile(
    r"(?:jdbc:|(?:password|passwd|secret|token|api[_-]?key)\s*[:=]|"
    r"(?:^|\s)(?:/[^\s]+|[A-Za-z]:\\[^\s]+))", re.IGNORECASE)


def identifier(value, label):
    if value is not None and not IDENTIFIER.fullmatch(value):
        raise InputError(f"{label}: требуется идентификатор из букв ASCII, цифр, . _ : - (до 128 символов)")
    return value


def safe(value):
    """Keep teacher-facing facts while suppressing obvious paths and credentials."""
    if isinstance(value, str):
        return "[redacted]" if SENSITIVE.search(value) else value
    if isinstance(value, list):
        return [safe(item) for item in value]
    if isinstance(value, dict):
        return {key: "[redacted]" if re.search(r"password|passwd|secret|token|api[_-]?key", key, re.I)
                else safe(item) for key, item in value.items()}
    return value


def grading_package(result, assignment, model, *, student_id=None, assignment_id=None,
                    run_id=None, integration=False):
    """Do not recalculate scores or statuses: SBSL is the sole grading authority."""
    feedback = []
    for check in result["checks"]:
        item = {"criterionId": safe(check["id"]), "status": check["status"],
                "points": check["points"], "score": check["score"],
                "expected": safe(check.get("expected")), "actual": safe(check.get("actual")),
                "message": safe(check.get("message") or {
                    "PASS": "Критерий выполнен", "FAIL": "Критерий не выполнен",
                    "ERROR": "Проверка завершилась ошибкой", "TIMEOUT": "Превышено время проверки",
                    "UNSUPPORTED": "Проверка не поддерживается", "SKIPPED": "Проверка пропущена",
                }.get(check["status"], "Статус проверки неизвестен"))}
        if "group" in check:
            item["group"] = safe(check["group"])
        if check.get("reasonCode") in REASON_CODES:
            item["reasonCode"] = check["reasonCode"]
        feedback.append(item)
    return {
        "schemaVersion": VERSION,
        "studentId": identifier(student_id, "studentId"),
        "assignmentId": identifier(assignment_id, "assignmentId"),
        "runId": identifier(run_id, "runId"),
        "assignmentName": safe(assignment["name"]),
        "project": safe(result["project"]),
        "sourceHash": result["sourceHash"],
        "compatibilityVersion": model["compatibilityVersion"],
        "runMode": "integration" if integration else "isolated",
        "status": result["status"],
        "score": result["score"],
        "maxScore": result["maxScore"],
        "unavailablePoints": result["unavailablePoints"],
        "feedback": feedback,
    }
