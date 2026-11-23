"""Check frozen metadata and form declarations without browser execution (9.0)."""
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
    'object_forms_reference_comparison', HERE.parent / 'catalogs-full/check_full.py')
COMPARISON = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPARISON)


def without_ids(value):
    if isinstance(value, dict):
        return {k: without_ids(v) for k, v in value.items() if k != 'Ид'}
    if isinstance(value, list):
        return [without_ids(v) for v in value]
    return value


def snapshot(root, model):
    value = COMPARISON.snapshot(root, model)
    for element in value['elements'].values():
        element.pop('Интерфейс', None)
    for element in model['elements']:
        if element['elementType'] == 'КомпонентИнтерфейса':
            value['elements'][COMPARISON.identity(element)] = without_ids(element['properties'])
    return value


def ordered_differences(expected, actual, path):
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys()):
            location = path + '.' + key
            if key not in expected or key not in actual:
                result.extend(COMPARISON.differences(
                    {key: expected[key]} if key in expected else {},
                    {key: actual[key]} if key in actual else {}, path))
            else:
                result.extend(ordered_differences(expected[key], actual[key], location))
        return result
    if isinstance(expected, list) and isinstance(actual, list):
        result = []
        for i in range(max(len(expected), len(actual))):
            location = f'{path}[{i}]'
            if i >= len(expected):
                result.append({'path': location, 'message': 'Лишнее', 'actual': actual[i]})
            elif i >= len(actual):
                result.append({'path': location, 'message': 'Отсутствует', 'expected': expected[i]})
            else:
                result.extend(ordered_differences(expected[i], actual[i], location))
        return result
    return COMPARISON.differences(expected, actual, path)


def element_differences(expected, actual):
    if not isinstance(expected, dict) or not isinstance(actual, dict):
        return COMPARISON.differences(expected, actual, 'elements')
    result = []
    for name in sorted(expected.keys() | actual.keys()):
        left, right = expected.get(name), actual.get(name)
        compare = (ordered_differences if isinstance(left, dict)
                   and left.get('ВидЭлемента') == 'КомпонентИнтерфейса'
                   else COMPARISON.differences)
        result.extend(compare(left, right, 'elements.' + name))
    return result


def check_contract(expected, actual):
    return [
        {'id': key, 'status': 'FAIL' if delta else 'PASS', 'differences': delta}
        for key, value in expected.items()
        for delta in [(element_differences(value, actual.get(key)) if key == 'elements'
                       else COMPARISON.differences(value, actual.get(key), key))]
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
            '<title>Формы объектов — структура без браузера</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}'
            'table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ddd;'
            'text-align:left}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            '<h1>Формы объектов — структура без браузера</h1>'
            f'<p>Проект: {escape(result["project"])}</p>'
            f'<p>Итог: <strong>{escape(result["status"])}</strong></p>'
            f'<p>Структурные критерии: {score["score"]:g}/{score["maxScore"]:g}; '
            f'недоступно: {score["unavailablePoints"]:g}.</p>'
            '<p><a href="structural/report.html">Подробности критериев и баллы</a></p>'
            '<p>Проверяются метаданные и объявления 14 форм: типы, привязки, команды, '
            'свойства и порядок компонентов. Браузер и работа платформы не проверяются. '
            'В исходнике исполняемых методов нет. UUID и комментарии не учитываются.</p>'
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
            for element in model['elements']:
                if element['elementType'] == 'КомпонентИнтерфейса':
                    element['properties'] = without_ids(element['properties'])
            strict = check_contract(expected, snapshot(root, model))
            structural, _ = run_test(source, HERE, output / 'structural',
                                     project_name=args.project_name, _prepared=(root, model))
        passed = (structural['status'] == 'passed'
                  and all(check['status'] == 'PASS' for check in strict))
        status = ('passed' if passed else
                  'incomplete' if structural['status'] == 'incomplete' else 'failed')
        result = {
            'project': model['name'], 'sourceHash': model['sourceHash'], 'status': status,
            'scope': 'metadata-and-form-declarations',
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
