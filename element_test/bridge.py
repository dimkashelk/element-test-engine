"""CLI transport: format conversion, Script invocation and report rendering."""
import argparse
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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
    return ('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Element Test Engine</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto}table{border-collapse:collapse;width:100%}'
            'td,th{padding:12px;border:1px solid #ddd;text-align:left}pre{white-space:pre-wrap}h1{color:#21486b}</style>'
            f'<h1>{escape(package["project"] if package else data["project"])}</h1>' + identity +
            f'<p>{escape(data["status"])}: {escape(data["score"])}/{escape(data["maxScore"])}</p>'
            f'<p>Баллы недоступных/пропущенных проверок: {escape(data["unavailablePoints"])}</p>'
            '<table><tr><th>Проверка</th><th>Статус</th><th>Баллы</th><th>Подробности</th></tr>' + rows + '</table></html>')


def main():
    parser = argparse.ArgumentParser(description="Element Test Engine — ядро на 1С:Элемент Скрипт")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "validate"):
        sub = commands.add_parser(name)
        sub.add_argument("project")
        sub.add_argument("--output", type=Path)
    test = commands.add_parser("test", aliases=["run"])
    test.add_argument("--project", required=True)
    test.add_argument("--assignment", required=True)
    test.add_argument("--output", type=Path, required=True)
    test.add_argument("--integration", action="store_true", help="Разрешить SQL smoke и ограниченный адаптер отгрузки в отдельном PostgreSQL")
    test.add_argument("--student-id")
    test.add_argument("--assignment-id")
    test.add_argument("--run-id")
    args = parser.parse_args()
    try:
        if args.command in ("test", "run"):
            for name, value in (("studentId", args.student_id), ("assignmentId", args.assignment_id),
                                ("runId", args.run_id)):
                identifier(value, name)
        source = Path(args.project).resolve()
        output = args.output.resolve() if args.output else None
        if source.is_dir() and output and output.is_relative_to(source):
            raise InputError("Отчёты нельзя создавать внутри исходного проекта")
        if output == source:
            raise InputError("Отчёт не может заменить входной архив")
        with TemporaryDirectory(prefix="element-test-") as work:
            temporary = Path(work)
            with open_project(source) as root:
                if output and output.is_relative_to(root.resolve()):
                    raise InputError("Отчёт находится внутри исходного проекта")
                model = analyze(root)
                # Canonicalize type unions in the transport model, not student files.
                for element in model["elements"]:
                    containers = [element["properties"]] + element["properties"].get("ТабличныеЧасти", [])
                    for container in containers:
                        for group in ("Реквизиты", "Измерения", "Ресурсы", "Константы"):
                            for member in container.get(group, []):
                                if "Тип" in member:
                                    try:
                                        member["Тип"] = parse_type(member["Тип"])[0]
                                    except ValueError:
                                        pass  # Validation diagnostics already describe it.
                model_path, assignment_path = temporary / "model.json", temporary / "assignment.json"
                write_json(model_path, model)
                command = "test" if args.command == "run" else args.command
                if command == "test":
                    assignment = load_assignment(args.assignment)
                    for check in assignment["checks"]:
                        if check["type"] == "runtime" and not check.get("skip"):
                            if 'integration' in check:
                                check["execution"] = run_integration(check, model, temporary, enabled=args.integration, root=root)
                            else:
                                check["execution"] = run_pure(root, model, check, temporary)
                    write_json(assignment_path, assignment)
                result = execute_engine(command, model_path, assignment_path, temporary)
        if command == "test":
            package = grading_package(result, assignment, model, student_id=args.student_id,
                                      assignment_id=args.assignment_id, run_id=args.run_id,
                                      integration=args.integration)
            output.mkdir(parents=True, exist_ok=True)
            write_json(output / "result.json", result)
            write_json(output / "grading.json", package)
            (output / "report.html").write_text(report(result, package), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "passed" else (2 if result["status"] == "incomplete" else 1)
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
