"""CLI transport: format conversion, Script invocation and report rendering."""
import argparse
from contextlib import nullcontext
import html
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import sys

from .assignment import load_assignment
from .loader import open_project
from .model import analyze
from .runtime import execute_engine, run_pure
from .integration import run_integration
from .grading import grading_package, identifier
from .types import parse_type
from .yaml_io import InputError


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def canonicalize_model_types(model):
    """Normalize member types in the app and every selected library model."""
    for project in [model, *(library["model"] for library in model.get("libraries", []))]:
        for element in project["elements"]:
            containers = [element["properties"]] + element["properties"].get("ТабличныеЧасти", [])
            for container in containers:
                for group in ("Реквизиты", "Измерения", "Ресурсы", "Константы", "Поля"):
                    for member in container.get(group, []):
                        if "Тип" in member:
                            try:
                                member["Тип"] = parse_type(member["Тип"])[0]
                            except ValueError:
                                pass


def report(data, package=None):
    escape = lambda v: html.escape(str(v))
    entries = package["feedback"] if package else data["checks"]
    rows = "".join(f'<tr><td>{escape(c["criterionId"] if package else c["id"])}</td><td>{escape(c["status"])}</td>'
                   f'<td>{escape(c["score"])}</td><td><pre>{escape(json.dumps(c, ensure_ascii=False, indent=2))}</pre></td></tr>'
                   for c in entries)
    identity = ''
    if package:
        identity = (f'<p>Задание: {escape(package["assignmentName"])}; '
                    f'студент: {escape(package["studentId"] or "—")}; '
                    f'запуск: {escape(package["runId"] or "—")}; '
                    f'режим: {escape(package["runMode"])}</p>')
        project_identity = package.get("projectIdentity", {})
        if project_identity.get("Поставщик"):
            identity += (f'<p>Приложение: {escape(project_identity["Поставщик"])}::'
                         f'{escape(project_identity.get("Имя") or package["project"])}'
                         f' {escape(project_identity.get("Версия") or "")}</p>')
        if package.get("libraries"):
            identity += '<p>Библиотеки: ' + ', '.join(
                escape(f'{library["provider"]}::{library["name"]} {library["version"]}')
                for library in package["libraries"]) + '</p>'
    return ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Element Test Engine</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto}table{border-collapse:collapse;width:100%}'
            'td,th{padding:12px;border:1px solid #ddd;text-align:left}pre{white-space:pre-wrap}h1{color:#21486b}</style>'
            f'<h1>{escape(package["project"] if package else data["project"])}</h1>' + identity +
            f'<p>{escape(data["status"])}: {escape(data["score"])}/{escape(data["maxScore"])}</p>'
            f'<p>Баллы недоступных/пропущенных проверок: {escape(data["unavailablePoints"])}</p>'
            '<table><tr><th>Проверка</th><th>Статус</th><th>Баллы</th><th>Подробности</th></tr>' + rows + '</table></html>')


def run_test(source, assignment_source, output, *, integration=False, student_id=None,
             assignment_id=None, run_id=None, project_name=None, _prepared=None):
    """Assess one submission; batch may supply its open root and analyzed model."""
    for name, value in (("studentId", student_id), ("assignmentId", assignment_id),
                        ("runId", run_id)):
        identifier(value, name)
    source, output = Path(source).resolve(), Path(output).resolve()
    if source.is_dir() and output.is_relative_to(source):
        raise InputError("Отчёты нельзя создавать внутри исходного проекта")
    if output == source:
        raise InputError("Отчёт не может заменить входной архив")
    with TemporaryDirectory(prefix="element-test-") as work:
        temporary = Path(work)
        with (open_project(source, project_name) if _prepared is None else nullcontext(_prepared[0])) as root:
            if output.is_relative_to(root.resolve()):
                raise InputError("Отчёт находится внутри исходного проекта")
            model = analyze(root) if _prepared is None else _prepared[1]
            canonicalize_model_types(model)
            model_path, assignment_path = temporary / "model.json", temporary / "assignment.json"
            write_json(model_path, model)
            assignment = load_assignment(assignment_source)
            plans = []
            for check in assignment["checks"]:
                if check["type"] == "runtime" and not check.get("skip"):
                    if 'formRequirement' in check:
                        from .declarative_bindings import preflight
                        facts = preflight(root, model, check)
                        if facts is not None:
                            check['execution'] = facts
                            continue
                    if 'integration' in check:
                        if 'library' in check:
                            check["execution"] = {"status": "UNSUPPORTED", "reasonCode": "unsupported_contract",
                                                  "message": "Интеграционный runtime для библиотечного метода не поддерживается"}
                        else:
                            prepared = []
                            check["execution"] = run_integration(check, model, temporary,
                                                                   enabled=integration, root=root, plan_sink=prepared)
                            if prepared:
                                plans.append({'criterionId': check['id'], 'plan': prepared[0]})
                    else:
                        prepared = []
                        check["execution"] = run_pure(root, model, check, temporary, plan_sink=prepared)
                        if prepared:
                            plans.append({'criterionId': check['id'], 'plan': prepared[0]})
                        else:
                            plans.append({'criterionId': check['id'], 'unavailable': {
                                k: check['execution'].get(k) for k in ('status', 'reasonCode', 'message')}})
            write_json(assignment_path, assignment)
            result = execute_engine("test", model_path, assignment_path, temporary)
            # Add teacher provenance and source diagnostics without changing
            # the status, comparison or score computed by SBSL.
            authored = {c['id']:c for c in assignment['checks'] if 'formRequirement' in c}
            for item in result['checks']:
                check = authored.get(item['id'])
                if check is None:
                    continue
                cfg = check['formRequirement']
                item['requirement'] = cfg
                execution = check.get('execution',{})
                sources = execution.get('bindings',[])
                if 'sourceFile' in execution:
                    sources = [execution]
                locations = [v['sourceFile']+': '+ '/'.join(v.get('yamlPath',[])) for v in sources]
                message = cfg['clause']['text']
                if execution.get('message'):
                    message += ' '+execution['message']
                if locations:
                    message += ' Источник: '+ '; '.join(locations)
                item['message'] = message
    result["projectIdentity"] = model["projectIdentity"]
    result["libraries"] = [{key: library[key] for key in ("provider", "name", "version", "kind", "sourceHash")}
                           for library in model["libraries"]]
    package = grading_package(result, assignment, model, student_id=student_id,
                              assignment_id=assignment_id, run_id=run_id,
                              integration=integration)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "result.json", result)
    write_json(output / "grading.json", package)
    # Technical preparation evidence stays separate from the v1 grading transport.
    for item in plans:
        for symbol in item.get('plan', {}).get('symbols', []):
            from hashlib import sha256
            source = symbol.pop('source')
            symbol['sourceHash'] = sha256(source.encode()).hexdigest()
        for query in item.get('plan', {}).get('queries', []):
            query['sourceHash'] = sha256(query.pop('text').encode()).hexdigest()
            for parameter in query['ast']['parameters']:
                parameter['expressionHash'] = sha256(parameter.pop('expression').encode()).hexdigest()
    for item in plans:
        for opening in item.get('plan', {}).get('formOpenings', []):
            for argument in opening['arguments']:
                argument['expressionHash'] = sha256(argument.pop('expression').encode()).hexdigest()
    write_json(output / 'execution-plans.json', plans)
    from .grading import safe
    write_json(output / 'runtime-evidence.json', safe([
        {'criterionId': check['id'], **check['execution']} for check in assignment['checks'] if 'execution' in check]))
    (output / "report.html").write_text(report(result, package), encoding="utf-8")
    return result, package


def main():
    parser = argparse.ArgumentParser(description="Element Test Engine — ядро на 1С:Элемент Скрипт")
    commands = parser.add_subparsers(dest="command", required=True)
    author = commands.add_parser('author-form')
    author.add_argument('--description', required=True, type=Path)
    author.add_argument('--contract', required=True, type=Path)
    author.add_argument('--output', required=True, type=Path)
    for name in ("inspect", "validate"):
        sub = commands.add_parser(name)
        sub.add_argument("project")
        sub.add_argument("--project-name", help="Имя проекта или Поставщик::Имя в составном архиве")
        sub.add_argument("--output", type=Path)
    test = commands.add_parser("test", aliases=["run"])
    test.add_argument("--project", required=True)
    test.add_argument("--project-name", help="Имя проекта или Поставщик::Имя в составном архиве")
    test.add_argument("--assignment", required=True)
    test.add_argument("--output", type=Path, required=True)
    test.add_argument("--integration", action="store_true", help="Разрешить SQL smoke и ограниченный адаптер отгрузки в отдельном PostgreSQL")
    test.add_argument("--student-id")
    test.add_argument("--assignment-id")
    test.add_argument("--run-id")
    batch = commands.add_parser("batch")
    batch.add_argument("--manifest", type=Path, required=True)
    batch.add_argument("--output", type=Path, required=True)
    batch.add_argument("--workers", type=int, default=1, choices=range(1, 5), metavar="1..4")
    batch.add_argument("--integration", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == 'author-form':
            from .form_requirements import author_form
            author_form(args.description, args.contract, args.output)
            return 0
        if args.command == "batch":
            from .batch import run_batch
            return run_batch(args.manifest, args.output, args.workers, args.integration)
        if args.command in ("test", "run"):
            result, _ = run_test(args.project, args.assignment, args.output,
                                 integration=args.integration, student_id=args.student_id,
                                 assignment_id=args.assignment_id, run_id=args.run_id,
                                 project_name=args.project_name)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "passed" else (2 if result["status"] == "incomplete" else 1)
        source = Path(args.project).resolve()
        output = args.output.resolve() if args.output else None
        if source.is_dir() and output and output.is_relative_to(source):
            raise InputError("Отчёты нельзя создавать внутри исходного проекта")
        if output == source:
            raise InputError("Отчёт не может заменить входной архив")
        with TemporaryDirectory(prefix="element-test-") as work:
            temporary = Path(work)
            with open_project(source, args.project_name) as root:
                if output and output.is_relative_to(root.resolve()):
                    raise InputError("Отчёт находится внутри исходного проекта")
                model = analyze(root)
                # Canonicalize type unions in the transport model, not student files.
                canonicalize_model_types(model)
                model_path, assignment_path = temporary / "model.json", temporary / "assignment.json"
                write_json(model_path, model)
                command = args.command
                result = execute_engine(command, model_path, assignment_path, temporary)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output, result)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if command == "inspect" or result["valid"] else 1
    except (InputError, OSError, ValueError) as exc:
        print(f"element-test: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
