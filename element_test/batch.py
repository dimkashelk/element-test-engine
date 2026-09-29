"""Bounded, process-isolated batch transport for teacher-owned manifests."""
import fcntl
from hashlib import sha256
import json
import multiprocessing as mp
import os
from pathlib import Path
import re
import signal
import time
import uuid

from .assignment import load_assignment
from .bridge import report, run_test, write_json
from .grading import identifier
from .loader import open_project
from .model import analyze
from .runtime import REPO
from .yaml_io import InputError


VERSION = "1.0"
ARCHIVES = (".tar", ".tar.gz", ".tgz", ".zip", ".xdump")


def _inside(path, root):
    return path == root or path.is_relative_to(root)


def load_manifest(path, output):
    path, output = Path(path).resolve(), Path(output).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("Manifest не читается как JSON") from exc
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "assignment", "assignmentId", "submissions"}:
        raise InputError("Manifest: требуются schemaVersion, assignment, assignmentId, submissions")
    if data["schemaVersion"] != VERSION:
        raise InputError("Manifest: неподдерживаемая schemaVersion")
    if not isinstance(data["assignmentId"], str) or not identifier(data["assignmentId"], "assignmentId"):
        raise InputError("Manifest: некорректный assignmentId")
    if not isinstance(data["assignment"], str) or not data["assignment"]:
        raise InputError("Manifest: некорректный assignment")
    assignment_candidate = path.parent / data["assignment"]
    if assignment_candidate.is_symlink():
        raise InputError("Manifest: символическая ссылка на задание запрещена")
    assignment = assignment_candidate.resolve()
    if not assignment.exists() or (not assignment.is_dir() and not assignment.is_file()):
        raise InputError("Manifest: задание не найдено")
    if _inside(output, assignment) or output == path:
        raise InputError("Выходной каталог пересекается с заданием или manifest")
    load_assignment(assignment)
    _hash_tree(sha256(), assignment)
    submissions = data["submissions"]
    if not isinstance(submissions, list) or not submissions:
        raise InputError("Manifest: submissions должен быть непустым массивом")
    seen_ids, seen_paths, entries = set(), set(), []
    for position, item in enumerate(submissions, 1):
        if not isinstance(item, dict) or not {"studentId", "project"} <= set(item) or set(item) - {"studentId", "project", "projectName"}:
            raise InputError("Manifest: каждая работа требует studentId и project")
        student_id, raw = item["studentId"], item["project"]
        project_name = item.get("projectName")
        if project_name is not None and (not isinstance(project_name, str) or not project_name.strip()):
            raise InputError("Manifest: некорректный projectName")
        if not isinstance(student_id, str) or not identifier(student_id, "studentId"):
            raise InputError("Manifest: некорректный studentId")
        if student_id in seen_ids:
            raise InputError("Manifest: повторяющийся studentId")
        if not isinstance(raw, str) or not raw:
            raise InputError("Manifest: некорректный project")
        candidate = path.parent / raw
        if candidate.is_symlink():
            raise InputError("Manifest: символическая ссылка на проект запрещена")
        project = candidate.resolve()
        if not project.exists() or not (project.is_dir() or project.is_file()):
            raise InputError("Manifest: проект не найден")
        if project.is_file() and not project.name.lower().endswith(ARCHIVES):
            raise InputError("Manifest: тип проекта не поддерживается")
        if project == path or project == assignment or project in seen_paths or (project.is_dir() and _inside(path, project)):
            raise InputError("Manifest: повторный или коллидирующий вход")
        if _inside(output, project) or _inside(project, output):
            raise InputError("Выходной каталог пересекается с проектом")
        if project.is_dir() and (_inside(assignment, project) or _inside(project, assignment)):
            raise InputError("Проект пересекается с заданием")
        for other in seen_paths:
            if ((project.is_dir() and _inside(other, project))
                    or (other.is_dir() and _inside(project, other))):
                raise InputError("Manifest: пересекающиеся проекты")
        seen_ids.add(student_id)
        seen_paths.add(project)
        entries.append({"ordinal": position, "studentId": student_id, "project": project,
                        "projectName": project_name})
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise InputError("Выходной каталог должен быть пустым каталогом")
    return assignment, data["assignmentId"], entries


def _hash_tree(digest, root):
    if root.is_file():
        paths = [root]
        base = root.parent
    else:
        all_paths = sorted(root.rglob("*"))
        if any(p.is_symlink() for p in all_paths):
            raise InputError("Символическая ссылка в задании или конфигурации")
        paths = [p for p in all_paths if p.is_file()]
        base = root
    for path in paths:
        if path.is_symlink():
            raise InputError("Символическая ссылка в задании или конфигурации")
        digest.update(path.relative_to(base).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")


def _cache_key(source_hash, assignment, integration):
    # The hash comes from the analyzed, safely opened project. Archive metadata
    # and filenames never determine identity.
    digest = sha256(b"element-test-batch-cache-v1\0" + source_hash.encode())
    _hash_tree(digest, assignment)
    for directory in (REPO / "element_test", REPO / "src"):
        for path in sorted(directory.glob("*.py" if directory.name == "element_test" else "*.sbsl")):
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    for path in (REPO / "docs/grading-v1.schema.json",
                 Path(os.environ.get("ELEMENT_TEST_RUNTIMES", REPO / "config/runtimes.json")),
                 Path(os.environ.get("ELEMENT_TEST_INTEGRATION_CONFIG", REPO / "config/integration.json"))):
        digest.update(path.read_bytes() + b"\0")
    digest.update(json.dumps({"integration": integration,
                              "dockerImage": os.environ.get("ELEMENT_TEST_DOCKER_IMAGE"),
                              "scriptHome": os.environ.get("ELEMENT_SCRIPT_HOME")},
                             sort_keys=True).encode())
    return digest.hexdigest()


def _cacheable(result, assignment):
    # Runtime is deliberately excluded until a contract proves deterministic.
    return (all(c["type"] != "runtime" for c in assignment["checks"])
            and result.get("unavailablePoints") == 0
            and all(c["status"] in {"PASS", "FAIL"} for c in result.get("checks", [])))


def _valid_cache(stored, key, source_hash, config, integration):
    if not isinstance(stored, dict) or stored.get("schemaVersion") != VERSION or stored.get("key") != key:
        return False
    result, package = stored.get("result"), stored.get("grading")
    if not isinstance(result, dict) or not isinstance(package, dict):
        return False
    if (result.get("sourceHash") != source_hash
            or result.get("status") not in {"passed", "failed"}
            or not isinstance(result.get("checks"), list)
            or len(result["checks"]) != len(config["checks"])):
        return False
    if not _cacheable(result, config):
        return False
    package_fields = {"schemaVersion", "studentId", "assignmentId", "runId",
                      "assignmentName", "project", "sourceHash", "compatibilityVersion",
                      "runMode", "status", "score", "maxScore", "unavailablePoints", "feedback"}
    if not package_fields <= set(package) or set(package) - package_fields - {"projectIdentity", "libraries"}:
        return False
    if (any(package[field] is not None for field in ("studentId", "assignmentId", "runId"))
            or not all(isinstance(package[field], str) for field in ("assignmentName", "project"))
            or not isinstance(package.get("compatibilityVersion"), str)
            or not re.fullmatch(r"\d+\.\d+", package["compatibilityVersion"])):
        return False
    if (package.get("schemaVersion") != VERSION
            or package.get("sourceHash") != result["sourceHash"]
            or package.get("libraries", []) != result.get("libraries", [])
            or package.get("projectIdentity", {}) != result.get("projectIdentity", {})
            or package.get("status") != result["status"]
            or package.get("runMode") != ("integration" if integration else "isolated")
            or not isinstance(package.get("feedback"), list)
            or len(package["feedback"]) != len(result["checks"])):
        return False
    for field in ("score", "maxScore", "unavailablePoints"):
        if (not isinstance(result.get(field), (int, float)) or isinstance(result[field], bool)
                or package.get(field) != result[field]):
            return False
    for check, feedback, specification in zip(result["checks"], package["feedback"], config["checks"]):
        if (not isinstance(check, dict) or not isinstance(feedback, dict)
                or not {"criterionId", "status", "points", "score", "expected", "actual", "message"} <= set(feedback)
                or set(feedback) - {"criterionId", "status", "points", "score", "expected", "actual", "message", "group", "reasonCode"}
                or not isinstance(feedback.get("message"), str)
                or check.get("id") != specification["id"]
                or feedback.get("criterionId") != check["id"]
                or feedback.get("status") != check.get("status")
                or feedback.get("score") != check.get("score")
                or feedback.get("points") != check.get("points")):
            return False
    return True


def _write_atomic(path, data):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        write_json(temporary, data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _assess(entry, assignment, assignment_id, run_id, output, cache_dir, integration):
    packet = output / f"submissions/{entry['ordinal']:06d}"
    config = load_assignment(assignment)
    with open_project(entry["project"], entry.get("projectName")) as root:
        try:
            model = analyze(root)
        except OSError as exc:
            raise InputError("Не удалось прочитать входной проект") from exc
        source_hash = model["sourceHash"]
        cache_key = _cache_key(source_hash, assignment, integration)
        cache_dir.mkdir(parents=True, exist_ok=True)
        lock_path = cache_dir / (cache_key + ".lock")
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            cache_path = cache_dir / (cache_key + ".json")
            if cache_path.exists():
                try:
                    stored = json.loads(cache_path.read_text(encoding="utf-8"))
                    result, package = stored["result"], stored["grading"]
                    if _valid_cache(stored, cache_key, source_hash, config, integration):
                        package = {**package, "studentId": entry["studentId"],
                                   "assignmentId": assignment_id, "runId": run_id}
                        packet.mkdir(parents=True, exist_ok=True)
                        write_json(packet / "result.json", result)
                        write_json(packet / "grading.json", package)
                        (packet / "report.html").write_text(report(result, package), encoding="utf-8")
                        return result, package, True
                except (OSError, ValueError, TypeError, KeyError):
                    pass
            result, package = run_test(entry["project"], assignment, packet,
                                       integration=integration, student_id=entry["studentId"],
                                       assignment_id=assignment_id, run_id=run_id,
                                       project_name=entry.get("projectName"),
                                       _prepared=(root, model))
            if _cacheable(result, config):
                _write_atomic(cache_path, {"schemaVersion": VERSION, "key": cache_key,
                                           "result": result,
                                           "grading": {**package, "studentId": None,
                                                       "assignmentId": None, "runId": None}})
            return result, package, False


def _worker(entry, assignment, assignment_id, run_id, output, cache_dir, integration, response):
    try:
        result, package, hit = _assess(entry, assignment, assignment_id, run_id,
                                       output, cache_dir, integration)
        payload = {"kind": "completed", "status": result["status"],
                   "score": result["score"], "maxScore": result["maxScore"],
                   "unavailablePoints": result["unavailablePoints"], "cacheHit": hit,
                   "checks": [{"criterionId": c["id"], "status": c["status"],
                               "score": c["score"], "points": c["points"]}
                              for c in result["checks"]]}
    except InputError:
        payload = {"kind": "inputError"}
    except Exception:
        payload = {"kind": "incomplete"}
    _write_atomic(response, payload)


def run_batch(manifest, output, workers=1, integration=False):
    if not isinstance(workers, int) or isinstance(workers, bool) or not 1 <= workers <= 4:
        raise InputError("workers должен быть в диапазоне 1..4")
    assignment, assignment_id, entries = load_manifest(manifest, output)
    output = Path(output).resolve()
    cache_dir = Path(os.environ.get("ELEMENT_TEST_CACHE_DIR", output.parent / ".element-test-cache")).resolve()
    if (any(_inside(cache_dir, e["project"]) for e in entries)
            or _inside(cache_dir, assignment) or _inside(cache_dir, output)
            or _inside(output, cache_dir)):
        raise InputError("Кэш пересекается с проектом, заданием или выходным каталогом")
    output.mkdir(parents=True, exist_ok=True)
    batch_id = uuid.uuid4().hex
    events_path = output / "events.jsonl"
    active, results = {}, {}
    context = mp.get_context("spawn")

    def event(kind, **fields):
        record = {"schemaVersion": VERSION, "batchId": batch_id, "type": kind, **fields}
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def finish(ordinal, payload):
        entry = entries[ordinal - 1]
        submission = f"{ordinal:06d}"
        status = payload["status"] if payload["kind"] == "completed" else payload["kind"]
        if payload["kind"] == "completed":
            for check in payload["checks"]:
                raw_id = check["criterionId"]
                check_id = "check-" + sha256(raw_id.encode()).hexdigest()[:16]
                event("checkFinished", submissionId=submission, runId=f"{batch_id}-{submission}",
                      criterionId=check_id, status=check["status"],
                      score=check["score"], points=check["points"])
        item = {"submissionId": submission, "studentId": entry["studentId"],
                "runId": f"{batch_id}-{submission}", "status": status}
        if payload["kind"] == "completed":
            item.update({"score": payload["score"], "maxScore": payload["maxScore"],
                         "unavailablePoints": payload["unavailablePoints"],
                         "cacheHit": payload["cacheHit"],
                         "result": f"submissions/{submission}/result.json",
                         "grading": f"submissions/{submission}/grading.json",
                         "report": f"submissions/{submission}/report.html"})
        results[ordinal] = item
        event("submissionFinished", submissionId=submission, runId=item["runId"],
              status=status, score=item.get("score"), cacheHit=item.get("cacheHit"))

    event("batchStarted", total=len(entries))
    next_ordinal = 1
    interrupted = False
    try:
        while next_ordinal <= len(entries) or active:
            while next_ordinal <= len(entries) and len(active) < workers:
                entry = entries[next_ordinal - 1]
                submission = f"{next_ordinal:06d}"
                response = output / f".worker-{submission}.json"
                process = context.Process(target=_worker,
                                          args=(entry, assignment, assignment_id,
                                                f"{batch_id}-{submission}", output,
                                                cache_dir, integration, response))
                try:
                    process.start()
                except BaseException:
                    if process.pid is not None:
                        active[next_ordinal] = (process, response)
                    raise
                active[next_ordinal] = (process, response)
                event("submissionStarted", submissionId=submission,
                      runId=f"{batch_id}-{submission}")
                next_ordinal += 1
            for ordinal, (process, response) in list(active.items()):
                if process.is_alive():
                    continue
                process.join()
                try:
                    payload = json.loads(response.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    payload = {"kind": "incomplete"}
                response.unlink(missing_ok=True)
                finish(ordinal, payload)
                del active[ordinal]
            if active:
                time.sleep(0.05)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if active:
            for process, _ in active.values():
                process.join(timeout=2)
            for process, _ in active.values():
                if process.is_alive():
                    try:
                        os.kill(process.pid, signal.SIGINT)
                    except ProcessLookupError:
                        pass
            for process, _ in active.values():
                process.join(timeout=10)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            for ordinal, (_, response) in active.items():
                response.unlink(missing_ok=True)
                finish(ordinal, {"kind": "incomplete"})
    counters = {"passed": 0, "failed": 0, "incomplete": 0, "inputError": 0}
    for item in results.values():
        counters[item["status"]] += 1
    index = {"schemaVersion": VERSION, "batchId": batch_id, "assignmentId": assignment_id,
             "status": "interrupted" if interrupted else "completed", "counts": counters,
             "submissions": [results[i] for i in sorted(results)]}
    _write_atomic(output / "batch-result.json", index)
    event("batchFinished", status=index["status"], counts=counters)
    return 130 if interrupted else (0 if counters["failed"] == counters["incomplete"] == counters["inputError"] == 0 else 1)
