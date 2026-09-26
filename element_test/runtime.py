"""Minimal XBSL → SBSL adapter. Student business logic is copied verbatim."""
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import time
import uuid

from .indexer import METHOD, mask_noncode
from .yaml_io import InputError

REPO = Path(__file__).resolve().parent.parent


def decode_output(text):
    """executor prints the entry method return value after its console output."""
    value, end = json.JSONDecoder().raw_decode(text.lstrip())
    remainder = text.lstrip()[end:].strip()
    if remainder not in {"", "0", "1"}:
        raise InputError("Runtime вывел посторонние данные после JSON")
    return value


def execute_engine(command, model, assignment, temporary):
    result = subprocess.run([str(REPO / "bin/script-runtime"), str(REPO / "src/ДвижокТестирования.sbsl"),
                             command, str(model), str(assignment)], cwd=temporary,
                            env={**os.environ, "TMPDIR": str(temporary)}, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise InputError(f"Ошибка движка Скрипт: {result.stderr or result.stdout}")
    try:
        return decode_output(result.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise InputError(f"Некорректный JSON движка: {result.stdout[:1000]}") from exc


def extract_method(source, name):
    code = mask_noncode(source)
    matches = [m for m in METHOD.finditer(code) if m[1] == name]
    if len(matches) != 1:
        raise InputError("Метод отсутствует или неоднозначен")
    match = matches[0]
    # Initial PoC only supports standalone methods ending with a separate ';' line.
    end = re.search(r"^\s*;\s*$", code[match.end():], re.MULTILINE)
    if not end:
        raise InputError("Не удалось определить границу метода")
    body_end = match.end() + end.end()
    body = code[match.end():body_end]
    if re.search(r"\b(если|пока|для|выбор|попытка|метод)\b", body):
        raise InputError("PoC пока поддерживает только чистые методы без вложенных блоков")
    signature_types = [p.split(":", 1)[1].strip() for p in match[2].split(",") if ":" in p]
    if any(t not in {"Строка", "Число", "Булево"} for t in signature_types) or match[3] is None:
        raise InputError("Для PoC требуются скалярные параметры и результат")
    if match[3].strip() not in {"Строка", "Число", "Булево"}:
        raise InputError("Тип результата пока не поддерживается")
    return source[match.start():body_end], signature_types


def sbsl_literal(value, type_name):
    if type_name == "Строка" and isinstance(value, str):
        # Escape interpolation so teacher inputs cannot inject SBSL expressions.
        return json.dumps(value, ensure_ascii=False).replace("${", "\\${")
    if type_name == "Булево" and isinstance(value, bool):
        return "Истина" if value else "Ложь"
    if type_name == "Число" and isinstance(value, (int, float)) and not isinstance(value, bool):
        import math
        if math.isfinite(value):
            return str(value)
    raise InputError(f"Вход не соответствует типу {type_name}")


def run_pure(root, model, check, temporary):
    """Run one scalar method in Docker; return evidence, never award points."""
    def status(name, message):
        return {"status": name, "message": message}

    if not shutil.which("docker"):
        return status("UNSUPPORTED", "Для runtime-тестов требуется Docker")
    config_path = Path(os.environ.get("ELEMENT_TEST_RUNTIMES", REPO / "config/runtimes.json"))
    try:
        runtimes = json.loads(config_path.read_text(encoding="utf-8"))
        runtime = runtimes.get(model["compatibilityVersion"])
        if runtime is None:
            return status("UNSUPPORTED", "Для режима совместимости проекта не настроен runtime")
    except (OSError, ValueError) as exc:
        return status("UNSUPPORTED", f"Не удалось прочитать конфигурацию runtime: {exc}")
    container = "element-test-" + uuid.uuid4().hex
    process = None
    created = False
    try:
        target = check.get("target", {})
        modules = [m for m in model["modules"] if m["name"] == target.get("module")]
        if len(modules) != 1:
            return status("ERROR", "Целевой модуль отсутствует или неоднозначен")
        original = (root / modules[0]["sourceFile"]).read_text(encoding="utf-8-sig")
        method, types = extract_method(original, target.get("method"))
        args = check.get("args", [])
        if len(args) != len(types):
            return status("ERROR", "Количество аргументов не совпадает с сигнатурой")
        call = ", ".join(sbsl_literal(v, t) for v, t in zip(args, types))
        sandbox = temporary / uuid.uuid4().hex
        sandbox.mkdir()
        script = sandbox / "test.sbsl"
        script.write_text(method + '\n\nметод Скрипт()\n    знч Результат = ' + target["method"] + '(' + call + ')\n'
                          '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": Результат}))\n;\n', encoding="utf-8")
        home = Path(os.environ.get("ELEMENT_SCRIPT_HOME", REPO / runtime["directory"])).resolve()
        image = os.environ.get("ELEMENT_TEST_DOCKER_IMAGE", runtime["image"])
        command = ["docker", "create", "--name", container, "--pull", "never",
                   "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--user", "65534:65534",
                   "--memory", "384m", "--memory-swap", "384m", "--cpus", "1", "--pids-limit", "64",
                   "--ulimit", "cpu=8:8", "--ulimit", "fsize=2097152:2097152", "--log-driver", "none",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--workdir", "/tmp",
                   "--mount", f"type=bind,source={home},target=/runtime,readonly",
                   "--mount", f"type=bind,source={script},target=/test.sbsl,readonly", image,
                   "java", "-Xmx128m", "-XX:MaxMetaspaceSize=128m", "-XX:+UseSerialGC",
                   "--add-opens", "java.base/java.lang=ALL-UNNAMED", "--add-opens", "java.base/java.nio=ALL-UNNAMED",
                   "-Dfile.encoding=UTF-8", "-Djava.io.tmpdir=/tmp", "-Dlogs.root=/tmp",
                   "-Dlogback.configurationFile=/runtime/config/logback.xml", "-Dexecutor.location=/runtime",
                   "-cp", "/runtime/lib/*", "com.e1c.g5rt.executor.boot.ExecutorBootstrap",
                   "-c", model["compatibilityVersion"], "/test.sbsl"]
        timeout = float(str(check.get("timeout", "5s")).removesuffix("s"))
        if not 0 < timeout <= 30:
            return status("ERROR", "timeout должен быть в диапазоне (0, 30] секунд")
        creation = subprocess.run(command, capture_output=True, text=True, timeout=30)
        if creation.returncode:
            return status("UNSUPPORTED", creation.stderr[:4000])
        created = True
        stdout_path, stderr_path = sandbox / "stdout", sandbox / "stderr"
        with stdout_path.open("w+b") as out, stderr_path.open("w+b") as err:
            process = subprocess.Popen(["docker", "start", "--attach", container], stdout=out, stderr=err)
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if time.monotonic() > deadline:
                    return status("TIMEOUT", f"Превышен timeout {timeout}s")
                if stdout_path.stat().st_size + stderr_path.stat().st_size > 1024 * 1024:
                    return status("ERROR", "Превышен лимит вывода runtime (1 MiB)")
                time.sleep(0.05)
            out.seek(0)
            err.seek(0)
            stdout, stderr = out.read(1024 * 1024).decode("utf-8", errors="replace"), err.read(1024 * 1024).decode("utf-8", errors="replace")
        if process.returncode in {125, 126, 127}:
            return status("UNSUPPORTED", (stderr or "Docker runtime недоступен")[:4000])
        if process.returncode:
            return status("ERROR", (stderr or stdout or f"Код runtime: {process.returncode}")[:4000])
        return {"status": "EXECUTED", "actual": decode_output(stdout)["actual"]}
    except InputError as exc:
        return status("UNSUPPORTED", str(exc))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return status("ERROR", str(exc))
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
