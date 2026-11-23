"""Run the authored criteria and strict conformity checks for lesson 03."""
import argparse
import html
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from element_test.bridge import canonicalize_model_types, run_test, write_json
from element_test.indexer import mask_noncode
from element_test.loader import open_project
from element_test.model import analyze
from element_test.yaml_io import InputError

HERE = Path(__file__).resolve().parent


def normalize(value):
    """Ignore generated IDs and declaration order, retaining every setting."""
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items() if key != 'Ид'}
    if isinstance(value, list):
        items = [normalize(item) for item in value]
        if items and all(isinstance(item, dict) and 'Имя' in item for item in items):
            return sorted(items, key=lambda item: item['Имя'])
        return items
    return value


def identity(item):
    return '::'.join(filter(None, (item['namespace'], item['name'])))


def named(items, value):
    result = {}
    for item in items:
        key = identity(item)
        if key in result:
            raise InputError(f'Неоднозначное имя: {key}')
        result[key] = value(item)
    return result


def snapshot(root, model):
    return {
        'project': normalize(model['properties']),
        'subsystems': {item['namespace']: normalize(item['properties'])
                       for item in model['subsystems']},
        'elements': named(model['elements'], lambda item: normalize(item['properties'])),
        # In this reference all seven modules contain only whitespace/comments.
        'modules': named(model['modules'], lambda item: {
            'hasCode': bool(mask_noncode(
                (root / item['sourceFile']).read_text(encoding='utf-8-sig'),
                strings=False).strip())}),
        'serviceFiles': {item['sourceFile']: normalize(item['properties'])
                         for item in model['serviceFiles']},
        'diagnostics': model['diagnostics'],
    }


def differences(expected, actual, path=''):
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys()):
            location = f'{path}.{key}' if path else key
            if key not in actual:
                result.append({'path': location, 'message': 'Отсутствует', 'expected': expected[key]})
            elif key not in expected:
                result.append({'path': location, 'message': 'Лишнее', 'actual': actual[key]})
            else:
                result.extend(differences(expected[key], actual[key], location))
        return result
    if isinstance(expected, list) and isinstance(actual, list):
        # Named members are matched by name, so missing/extra fields have precise paths.
        if all(isinstance(item, dict) and isinstance(item.get('Имя'), str)
               for item in expected + actual):
            def members(items):
                result = {}
                for item in items:
                    if item['Имя'] in result:
                        return None
                    result[item['Имя']] = item
                return result
            left, right = members(expected), members(actual)
            if left is not None and right is not None:
                return differences(left, right, path)
        result = []
        for index in range(max(len(expected), len(actual))):
            location = f'{path}[{index}]'
            if index >= len(actual):
                result.append({'path': location, 'message': 'Отсутствует', 'expected': expected[index]})
            elif index >= len(expected):
                result.append({'path': location, 'message': 'Лишнее', 'actual': actual[index]})
            else:
                result.extend(differences(expected[index], actual[index], location))
        return result
    same = expected == actual
    # Python considers True == 1; metadata boolean and numeric values differ.
    if isinstance(expected, bool) or isinstance(actual, bool):
        same = same and type(expected) is type(actual)
    if same:
        return []
    return [{'path': path, 'message': 'Значение не совпадает',
             'expected': expected, 'actual': actual}]


def check_contract(expected, actual):
    return [
        {'id': key, 'status': 'FAIL' if (delta := differences(value, actual[key], key)) else 'PASS',
         'differences': delta}
        for key, value in expected.items()
    ]


def render(result, title='Справочники — полное соответствие'):
    escape = html.escape
    score = result['structural']
    rows = ''.join(
        '<tr><td>' + escape(check['id']) + '</td><td>' + check['status'] + '</td><td><pre>'
        + escape(json.dumps(check['differences'], ensure_ascii=False, indent=2)) + '</pre></td></tr>'
        for check in result['strictChecks'])
    return ('<!doctype html><html lang="ru"><meta charset="utf-8">'
            f'<title>{escape(title)}</title>'
            '<style>body{font:16px system-ui;max-width:1100px;margin:40px auto}'
            'table{border-collapse:collapse;width:100%}td,th{padding:12px;border:1px solid #ddd;'
            'text-align:left}pre{white-space:pre-wrap}</style>'
            f'<h1>{escape(title)}</h1>'
            f'<p>Проект: {escape(result["project"])}</p>'
            f'<p>Итог: <strong>{result["status"]}</strong></p>'
            f'<p>Структурные критерии: {score["status"]}, '
            f'{score["score"]:g}/{score["maxScore"]:g} баллов.</p>'
            '<p><a href="structural/report.html">Подробности структурных критериев</a></p>'
            '<p>Полное соответствие требует прохождения и структурных, и строгих проверок. '
            'UUID, комментарии и порядок объявления реквизитов не учитываются. '
            'Проверяется конфигурация из дампа; запуск приложения и его данные не проверяются.</p>'
            '<table><tr><th>Строгая проверка</th><th>Статус</th><th>Расхождения</th></tr>'
            + rows + '</table></html>')


def main(argv=None, *, assignment_dir=HERE, title='Справочники — полное соответствие'):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--project-name')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    source, output = Path(args.project).resolve(), Path(args.output).resolve()
    try:
        if output == source or (source.is_dir() and output.is_relative_to(source)):
            raise InputError('Отчёты нельзя создавать внутри исходного проекта')
        expected = json.loads((assignment_dir / 'reference.json').read_text(encoding='utf-8'))
        with open_project(source, args.project_name) as root:
            if output.is_relative_to(root.resolve()):
                raise InputError('Отчёты нельзя создавать внутри исходного проекта')
            model = analyze(root)
            canonicalize_model_types(model)
            checks = check_contract(expected, snapshot(root, model))
            structural, _ = run_test(source, assignment_dir, output / 'structural',
                                     project_name=args.project_name, _prepared=(root, model))
        passed = structural['status'] == 'passed' and all(c['status'] == 'PASS' for c in checks)
        status = ('passed' if passed else
                  'incomplete' if structural['status'] == 'incomplete' else 'failed')
        result = {
            'project': model['name'], 'sourceHash': model['sourceHash'], 'status': status,
            'structural': {key: structural[key] for key in
                           ('status', 'score', 'maxScore', 'unavailablePoints')},
            'strictChecks': checks,
        }
        write_json(output / 'full-result.json', result)
        (output / 'report.html').write_text(render(result, title), encoding='utf-8')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if passed else 2 if status == 'incomplete' else 1
    except (InputError, OSError, ValueError) as exc:
        print(f'Ошибка проверки: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
