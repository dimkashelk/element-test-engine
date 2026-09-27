"""Minimal XBSL → SBSL adapter. Student business logic is copied verbatim."""
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import time
import uuid

from .indexer import IDENT, METHOD, call_code, mask_noncode, split_parameters
from .generated_types import ProjectTypes
from .platform_mocks import PlatformMocks
from .yaml_io import InputError

REPO = Path(__file__).resolve().parent.parent


def decode_output(text):
    """executor prints the entry method return value after its console output."""
    calls = []
    while text.startswith('ELEMENT_CALL '):
        text = text[len('ELEMENT_CALL '):]
        call, end = json.JSONDecoder().raw_decode(text)
        calls.append(call)
        text = text[end:].lstrip()
    value, end = json.JSONDecoder().raw_decode(text.lstrip())
    remainder = text.lstrip()[end:].strip()
    if remainder not in {"", "0", "1"}:
        raise InputError("Runtime вывел посторонние данные после JSON")
    if value.pop('_captureCalls', False):
        observe = value.pop('_observeCallArguments', None)
        if observe is not None:
            calls = [{**call, 'args': {k: v for k, v in call['args'].items() if k in observe}}
                     for call in calls]
        actual = value['actual']
        if value.pop('_captureException', False):
            actual = actual['result']
        actual['calls'] = calls
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


def extract_method(source, name, *, allow_void=False):
    code = mask_noncode(source)
    matches = [m for m in METHOD.finditer(code) if m[1] == name]
    if len(matches) != 1:
        raise InputError("Метод отсутствует или неоднозначен")
    match = matches[0]
    # Track line-oriented blocks conservatively; never truncate at a loop's ';'.
    depth, body_end = 1, None
    offset = match.end()
    for line in code[match.end():].splitlines(keepends=True):
        stripped = line.strip()
        if re.match(r"^(если|пока|для|выбор|попытка)\b", stripped):
            depth += 1
        elif re.search(r"\bметод\b", stripped):
            raise InputError("Вложенные методы или незакрытый метод не поддерживаются")
        elif stripped == ";":
            depth -= 1
            if depth == 0:
                body_end = offset + len(line)
                break
        offset += len(line)
    if body_end is None:
        raise InputError("Не удалось определить границу метода")
    parameters = split_parameters(match[2])
    if any(":" not in p or "=" in p for p in parameters) or (match[3] is None and not allow_void):
        raise InputError("Требуются явно типизированные параметры без значений по умолчанию и результат")
    signature_types = [p.split(":", 1)[1].strip() for p in parameters]
    return source[match.start():body_end], signature_types


def method_closure(source, name):
    """Reachable same-module bare/этот calls, including string interpolation."""
    available = {match[1] for match in METHOD.finditer(mask_noncode(source))}
    visited, methods = set(), []

    def visit(current):
        if current in visited:
            return
        visited.add(current)
        method, parameters = extract_method(source, current, allow_void=True)
        methods.append((method, parameters))
        signature = METHOD.search(mask_noncode(method))
        body = call_code(method[signature.end():])
        calls = re.finditer(rf"(?<![\w.:])(?:этот\s*\.\s*)?({IDENT})\s*\(", body)
        for call in calls:
            prefix = body[:call.start()].rstrip()
            if prefix.endswith((".", ":")) or re.search(r"\bновый$", prefix):
                continue
            if call[1] in available:
                visit(call[1])

    visit(name)
    return methods


def prepare_script(root, model, check, sandbox):
    """Copy the original method and generate only its required data contracts."""
    target = check.get("target", {})
    modules = [m for m in model["modules"] if m["name"] == target.get("module")
               and ("namespace" not in target or m["namespace"] == target["namespace"])]
    if len(modules) != 1:
        raise InputError("Целевой модуль отсутствует или неоднозначен")
    module = modules[0]
    original = (root / module["sourceFile"]).read_text(encoding="utf-8-sig")
    context = check.get("context")
    object_module = module.get("moduleType") == "object"
    is_object = "context" in check
    if is_object and not object_module:
        raise InputError("context допустим только для модуля Объект")
    if is_object and not isinstance(context, dict):
        raise InputError("Для объектного метода требуется context с начальными полями")
    method, types = extract_method(original, target.get("method"), allow_void=is_object)
    if not is_object and re.search(r"\bэтот\b", mask_noncode(method)):
        raise InputError("Для объектного метода требуется context с начальными полями")
    args = check.get("args", [])
    if len(args) != len(types):
        raise InputError("Количество аргументов не совпадает с сигнатурой")
    contracts = ProjectTypes(model, module["namespace"])
    mocks = check.get("mocks", {})
    if not isinstance(mocks, dict) or set(mocks) - {"objects", "registers", "queries"}:
        raise InputError("Поддерживаются mocks.objects, mocks.registers, mocks.queries")
    contracts.configure_references(mocks.get("objects", {}))
    platform = PlatformMocks(contracts, mocks, check)
    if not isinstance(check.get('captureException', False), bool):
        raise InputError('captureException должен быть Булево')
    signature = METHOD.search(mask_noncode(method))
    return_type = signature[3].strip() if signature[3] else None
    if not is_object and return_type == "ничто":
        raise InputError("Для метода без результата требуется объектный context")
    methods = method_closure(original, target.get("method"))
    if not is_object and any(re.search(r"\bэтот\b", call_code(m)) for m, _ in methods):
        raise InputError("Для объектного метода требуется context с начальными полями")
    signature_types = []
    for dependency, parameter_types in methods:
        declaration = METHOD.search(mask_noncode(dependency))
        signature_types.extend(parameter_types)
        if declaration[3] and declaration[3].strip() != "ничто":
            signature_types.append(declaration[3].strip())
    for type_name in signature_types:
        contracts.require(type_name)
    adapted = []
    for dependency, _ in methods:
        declaration = METHOD.search(mask_noncode(dependency))
        # Adapt declarations only; the original business logic remains verbatim.
        replacements = [(declaration.start(2), declaration.end(2), ",".join(
            p.partition(":")[0] + ":" + contracts.sbsl_type(p.partition(":")[2])
            for p in split_parameters(declaration[2])))]
        if declaration[3]:
            replacements.append((declaration.start(3), declaration.end(3), contracts.sbsl_type(declaration[3])))
        for start, end, replacement in reversed(replacements):
            dependency = dependency[:start] + replacement + dependency[end:]
        adapted.append(platform.adapt(dependency))
    calls = platform.finish()
    method = "\n".join(adapted)
    call = ", ".join(contracts.literal(v, t) for v, t in zip(args, types))
    setup = ""
    expression = target["method"] + '(' + call + ')'
    observed = "Контекст"
    if "observe" in check:
        fields = check["observe"]
        if not is_object or not isinstance(fields, list) or not fields or any(not isinstance(f, str) for f in fields):
            raise InputError("observe требует объектный context и непустой список полей")
        contracts.require(module["name"])
        known = {f["Имя"] for f in contracts.fields[module["name"]]}
        if len(set(fields)) != len(fields) or set(fields) - known:
            raise InputError("observe содержит неизвестные или повторяющиеся поля")
        observed = "{" + ", ".join(sbsl_literal(f, "Строка") + ": Контекст." + f for f in fields) + "}"
    if is_object:
        object_type = module["name"]
        if mocks.get('registers'):
            # A handler needs the register, whose dimensions need the owner's
            # reference. Put executable context in a separate module to keep
            # SBSL imports acyclic without changing the handler body.
            contracts.require(object_type)
            context_type = 'ТестКонтекст.Объект'
            contracts.fields[context_type] = contracts.fields[object_type]
            contracts.definitions[context_type] = contracts.definitions[object_type]
            object_type = context_type
        contracts.attach_method(object_type, "\n@Глобально\n".join(adapted), signature_types)
        contracts.method_dependencies[object_type].extend(
            [name + '.НаборЗаписей' for name in mocks.get('registers', [])]
            + (['ТестПлатформа.Запрос'] if mocks.get('queries') else []))
        setup = '    знч Контекст = ' + contracts.literal(context, object_type) + '\n'
        expression = 'Контекст.' + expression
        method = ""
    if return_type and return_type != "ничто":
        invocation = '    знч Результат = ' + expression + '\n'
        actual = '{"return": Результат, "context": ' + observed + '}' if is_object else 'Результат'
    else:
        invocation = '    ' + expression + '\n'
        actual = observed
    if platform.capture:
        actual = '{"context": ' + actual + ', "calls": ' + calls + '}'
    if check.get('captureException', False):
        if not is_object or (return_type and return_type != 'ничто'):
            raise InputError('captureException поддерживает только объектный метод без результата')
        invocation = ('    пер ИсключениеРезультат: Объект? = Неопределено\n    попытка\n'
                      + '    ' + invocation + '    поймать Ошибка: Исключение\n'
                      + '        ИсключениеРезультат = {"type": Ошибка.ПолучитьТип().ВСтроку(), "message": Ошибка.Описание}\n'
                      + '    ;\n')
        actual = '{"result": ' + actual + ', "exception": ИсключениеРезультат}'
    imports = contracts.write(sandbox)
    metadata = ''
    if platform.capture:
        metadata = ', "_captureCalls": Истина, "_captureException": ' + ('Истина' if check.get('captureException') else 'Ложь')
        if platform.observe is not None:
            metadata += ', "_observeCallArguments": ' + contracts.literal(platform.observe, 'Массив<Строка>')
    script = sandbox / "test.sbsl"
    script.write_text(imports + "\n" + method + '\n\nметод Скрипт()\n' + setup + invocation
                      + '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": ' + actual + metadata + '}))\n;\n', encoding="utf-8")
    return script


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
    """Run one standalone method in Docker; return evidence, never award points."""
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
        sandbox = temporary / uuid.uuid4().hex
        sandbox.mkdir()
        generated = sandbox / "generated"
        generated.mkdir()
        script = prepare_script(root, model, check, generated)
        home = Path(os.environ.get("ELEMENT_SCRIPT_HOME", REPO / runtime["directory"])).resolve()
        image = os.environ.get("ELEMENT_TEST_DOCKER_IMAGE", runtime["image"])
        command = ["docker", "create", "--name", container, "--pull", "never",
                   "--network", "none", "--read-only", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--user", "65534:65534",
                   "--memory", "384m", "--memory-swap", "384m", "--cpus", "1", "--pids-limit", "64",
                   "--ulimit", "cpu=8:8", "--ulimit", "fsize=2097152:2097152", "--log-driver", "none",
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "--workdir", "/tmp",
                   "--mount", f"type=bind,source={home},target=/runtime,readonly",
                   "--mount", f"type=bind,source={script.parent},target=/generated,readonly", image,
                   "java", "-Xmx128m", "-XX:MaxMetaspaceSize=128m", "-XX:+UseSerialGC",
                   "--add-opens", "java.base/java.lang=ALL-UNNAMED", "--add-opens", "java.base/java.nio=ALL-UNNAMED",
                   "-Dfile.encoding=UTF-8", "-Djava.io.tmpdir=/tmp", "-Dlogs.root=/tmp",
                   "-Dlogback.configurationFile=/runtime/config/logback.xml", "-Dexecutor.location=/runtime",
                   "-cp", "/runtime/lib/*", "com.e1c.g5rt.executor.boot.ExecutorBootstrap",
                   "-c", model["compatibilityVersion"], "/generated/test.sbsl"]
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
    except subprocess.TimeoutExpired:
        return status("TIMEOUT", "Превышен timeout операции Docker")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return status("ERROR", str(exc))
    finally:
        if created:
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
