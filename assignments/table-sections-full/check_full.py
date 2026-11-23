"""Check the frozen non-UI contract of dump_practice.xdump, compatibility 9.0."""
import argparse
import html
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from element_test.bridge import canonicalize_model_types, run_test, write_json
from element_test.loader import open_project
from element_test.model import analyze
from element_test.yaml_io import InputError

SPEC = importlib.util.spec_from_file_location(
    'table_sections_reference_comparison', HERE.parent / 'catalogs-full/check_full.py')
COMPARISON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARISON)


def snapshot(root, model):
    value = COMPARISON.snapshot(root, model)
    for element in value['elements'].values():
        element.pop('Интерфейс', None)
    return value


def check_contract(expected, actual):
    return [
        {'id': key, 'status': 'FAIL' if delta else 'PASS', 'differences': delta}
        for key, value in expected.items()
        for delta in [COMPARISON.differences(value, actual.get(key), key)]
    ]


def render(result):
    escape = html.escape
    rows = ''.join(
        '<tr><td>' + escape(check['id']) + '</td><td>' + escape(check['status'])
        + '</td><td><pre>'
        + escape(json.dumps(check['differences'], ensure_ascii=False, indent=2))
        + '</pre></td></tr>' for check in result['strictChecks'])
    score = result['structural']
    return ('<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<title>Табличные части — полное соответствие без интерфейса</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}'
            'table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ddd;'
            'text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>Табличные части — полное соответствие без интерфейса</h1>'
            f'<p>Проект: {escape(result["project"])}</p>'
            f'<p>Итог: <strong>{escape(result["status"])}</strong></p>'
            f'<p>Структурные критерии: {score["score"]:g}/{score["maxScore"]:g}; '
            f'недоступно: {score["unavailablePoints"]:g}.</p>'
            '<p><a href="structural/report.html">Подробности критериев и баллы</a></p>'
            '<p>Проверяется сохранённый эталон метаданных, включая все табличные части, '
            'колонки, ограничения, ссылки, перечисления и отсутствие исполняемого кода. '
            'Интерфейсные настройки исключены. UUID, комментарии и порядок именованных '
            'деклараций не учитываются. Данные пользователей и работа платформы '
            'не проверяются. В исходном дампе исполняемых методов нет.</p>'
            '<table><tr><th>Строгая проверка</th><th>Статус</th><th>Расхождения</th></tr>'
            + rows + '</table></html>')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--project-name')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    source, output = Path(args.project).resolve(), Path(args.output).resolve()
    try:
        if output == source or (source.is_dir() and output.is_relative_to(source)):
            raise InputError('Отчёты нельзя создавать внутри исходного проекта')
        expected = json.loads((HERE / 'reference.json').read_text(encoding='utf-8'))
        with open_project(source, args.project_name) as root:
            if output.is_relative_to(root.resolve()):
                raise InputError('Отчёты нельзя создавать внутри исходного проекта')
            model = analyze(root)
            canonicalize_model_types(model)
            strict = check_contract(expected, snapshot(root, model))
            structural, _ = run_test(source, HERE, output / 'structural',
                                     project_name=args.project_name, _prepared=(root, model))
        passed = (structural['status'] == 'passed'
                  and all(check['status'] == 'PASS' for check in strict))
        status = ('passed' if passed else
                  'incomplete' if structural['status'] == 'incomplete' else 'failed')
        result = {
            'project': model['name'], 'sourceHash': model['sourceHash'], 'status': status,
            'scope': 'non-ui-metadata',
            'structural': {key: structural[key] for key in
                           ('status', 'score', 'maxScore', 'unavailablePoints')},
            'strictChecks': strict,
        }
        write_json(output / 'full-result.json', result)
        (output / 'report.html').write_text(render(result), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if passed else 2 if status == 'incomplete' else 1
    except (InputError, OSError, ValueError) as exc:
        print(f'Ошибка проверки: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
