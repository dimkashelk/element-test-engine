"""Call expression shapes and functional values in the XBSL index."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from element_test.call_graph import build_call_graph
from element_test.indexer import (method_call_expressions, method_local_callable_bindings,
                                  parse_module)
from element_test.runtime import REPO, decode_output, prepare_script, project_method_closure
from element_test.yaml_io import UnsupportedSyntaxError


class ExpressionCallsTest(unittest.TestCase):
    def test_typed_reference_chain_reaches_its_object_method(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = ('метод Main(Ссылка: Запись.Ссылка): Число\n'
                      '    возврат Ссылка.ЗагрузитьОбъект().Копировать()\n;\n')
            object_source = ('метод Копировать(): Число\n    возврат 7\n;\n')
            (root / 'Main.xbsl').write_text(source)
            (root / 'Запись.Объект.xbsl').write_text(object_source)
            model = {'modules': [
                {'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                 'moduleType': 'module', 'imports': []},
                {'name': 'Запись.Объект', 'namespace': '', 'sourceFile': 'Запись.Объект.xbsl',
                 'moduleType': 'object', 'imports': []}],
                'elements': [{'name': 'Запись', 'namespace': '', 'elementType': 'Документ',
                              'properties': {}, 'sourceFile': 'Запись.yaml'}], 'properties': {}}
            edges, diagnostics = build_call_graph(root, model)
            self.assertEqual([(e['toModule'], e['toMethod']) for e in edges],
                             [('Запись.Объект', 'Копировать')])
            self.assertEqual(diagnostics, [])

    def test_typed_collection_and_optional_call_do_not_claim_module_edges(self):
        source = ('метод Main(Ссылка: Запись.Ссылка)\n'
                  '    знч Набор: Соответствие<Строка, Массив<Запись.Ссылка>>\n'
                  '    Набор["x"].Добавить(Ссылка)\n'
                  '    Ссылка.ЗагрузитьОбъект()?.Удалить()\n;\n'
                  'метод Добавить()\n;\nметод Удалить()\n;\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [{'name': 'Запись', 'namespace': '', 'elementType': 'Документ',
                                   'properties': {}, 'sourceFile': 'Запись.yaml'}], 'properties': {}}
            edges, diagnostics = build_call_graph(root, model)
            self.assertEqual(edges, [])
            self.assertEqual(diagnostics, [])

    def test_cast_after_previous_statement_is_not_computed_function_call(self):
        source = ('метод Main()\n'
                  '    знч X = Factory()\n'
                  '    (X как Форма).Инициализировать()\n;\n')
        calls = method_call_expressions(source, parse_module(source)[0][0])
        self.assertEqual([(call.kind, call.name) for call in calls],
                         [('static', 'Factory'), ('computed', 'Инициализировать')])
        self.assertEqual(source[calls[1].receiver_start:calls[1].receiver_end], '(X как Форма)')

    def test_local_name_is_not_visible_in_its_initializer(self):
        source = ('метод Main(): Булево\n'
                  '    знч ЭтоНовый = ЭтоНовый()\n'
                  '    возврат ЭтоНовый\n;\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [], 'properties': {}}
            self.assertEqual(build_call_graph(root, model), ([], []))

    def test_computed_receiver_never_becomes_static_module_edge(self):
        source = ('метод Main(): Число\n'
                  '    возврат Factory().Run() + Items[0].Run()\n;\n'
                  'метод Factory(): Объект\n    возврат Неопределено\n;\n'
                  'метод Run(): Число\n    возврат 1\n;\n')
        method = parse_module(source)[0][0]
        calls = method_call_expressions(source, method)
        self.assertEqual([(c.kind, c.name, c.receiver) for c in calls],
                         [('static', 'Factory', None), ('computed', 'Run', None),
                          ('computed', 'Run', None)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [], 'properties': {}}
            edges, diagnostics = build_call_graph(root, model)
            self.assertEqual([(e['fromMethod'], e['toMethod']) for e in edges],
                             [('Main', 'Factory')])
            self.assertEqual([d['code'] for d in diagnostics], ['dynamic_call', 'dynamic_call'])
            with self.assertRaisesRegex(UnsupportedSyntaxError, 'Динамический вызов'):
                prepare_script(root, model, {'target': {'module': 'Main', 'method': 'Main'},
                                             'args': []}, root)

    def test_lambda_and_method_reference_are_reachable(self):
        source = ('метод Main(): Число\n'
                  '    знч A = &Helper\n'
                  '    знч B = (X: Число) -> Helper() + X\n'
                  '    возврат A() + B(1)\n;\n'
                  'метод Helper(): Число\n    возврат 2\n;\n')
        method = parse_module(source)[0][0]
        self.assertEqual(method_local_callable_bindings(source, method), {'A', 'B'})
        self.assertIn(('reference', 'Helper'),
                      [(c.kind, c.name) for c in method_call_expressions(source, method)])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [], 'properties': {}}
            reachable, _, _, _ = project_method_closure(root, model, model['modules'][0], 'Main')
            self.assertEqual([method.split('(')[0].strip() for _, method, _ in reachable],
                             ['метод Main', 'метод Helper'])
            edges, diagnostics = build_call_graph(root, model)
            self.assertEqual([e['toMethod'] for e in edges], ['Helper'])
            self.assertEqual(diagnostics, [])

    def test_unknown_function_value_is_explicit(self):
        source = ('метод Main(Fn: (Число)->Число): Число\n'
                  '    возврат Fn(1)\n;\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [], 'properties': {}}
            _, diagnostics = build_call_graph(root, model)
            self.assertEqual([d['code'] for d in diagnostics], ['dynamic_call'])
            with self.assertRaisesRegex(UnsupportedSyntaxError, 'Динамический вызов'):
                project_method_closure(root, model, model['modules'][0], 'Main')

        declaration_only = ('метод Main(): Число\n'
                            '    пер F: (Число)->Число\n'
                            '    X = &Helper\n'
                            '    возврат F(1)\n;\n')
        self.assertEqual(method_local_callable_bindings(
            declaration_only, parse_module(declaration_only)[0][0]), set())

    def test_method_name_has_priority_over_function_value(self):
        source = ('метод Main(): Число\n'
                  '    знч Helper = (X: Число) -> X\n'
                  '    возврат Helper()\n;\n'
                  'метод Helper(): Число\n    возврат 3\n;\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [], 'properties': {}}
            edges, diagnostics = build_call_graph(root, model)
            self.assertEqual([e['toMethod'] for e in edges], ['Helper'])
            self.assertEqual(diagnostics, [])

    def test_import_alias_in_method_reference_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'A').mkdir()
            (root / 'B').mkdir()
            output = root / 'out'
            output.mkdir()
            (root / 'A/Main.xbsl').write_text(
                'импорт B::Helper как Alias\n'
                'метод Run(): Число\n    знч F = &Alias.Value\n    возврат F()\n;\n')
            (root / 'B/Helper.xbsl').write_text(
                '@Глобально\nметод Value(): Число\n    возврат 4\n;\n')
            model = {'modules': [
                {'name': 'Main', 'namespace': 'A', 'sourceFile': 'A/Main.xbsl',
                 'moduleType': 'module', 'imports': ['B::Helper как Alias']},
                {'name': 'Helper', 'namespace': 'B', 'sourceFile': 'B/Helper.xbsl',
                 'moduleType': 'module', 'imports': []}], 'elements': [], 'properties': {}}
            original = (root / 'A/Main.xbsl').read_bytes()
            script = prepare_script(root, model, {'target': {'module': 'Main', 'method': 'Run'},
                                                  'args': []}, output)
            self.assertIn('&ТестВнешнийМодуль1.Value', script.read_text())
            self.assertEqual(original, (root / 'A/Main.xbsl').read_bytes())
            if (REPO / 'script_u_10.0.2_1/lib').is_dir():
                process = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                         cwd=output, capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
                self.assertEqual(decode_output(process.stdout)['actual'], 4)

    def test_block_local_shadow_ends_with_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(
                'метод Run(): Число\n'
                '    для Helper из [1]\n'
                '        Helper.Value()\n'
                '    ;\n'
                '    возврат Helper.Value()\n;\n')
            (root / 'Helper.xbsl').write_text(
                'метод Value(): Число\n    возврат 1\n;\n')
            model = {'modules': [
                {'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                 'moduleType': 'module', 'imports': []},
                {'name': 'Helper', 'namespace': '', 'sourceFile': 'Helper.xbsl',
                 'moduleType': 'module', 'imports': []}], 'elements': [], 'properties': {}}
            edges, diagnostics = build_call_graph(root, model)
            self.assertEqual([(e['fromModule'], e['toModule']) for e in edges],
                             [('Main', 'Helper')])
            self.assertEqual(diagnostics, [])

    def test_lambda_parameter_call_is_dynamic(self):
        source = ('метод Main(): Число\n'
                  '    знч A = (F: ()->Число) -> F()\n'
                  '    возврат A(&Helper)\n;\n'
                  'метод Helper(): Число\n    возврат 1\n;\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [], 'properties': {}}
            edges, diagnostics = build_call_graph(root, model)
            self.assertEqual([e['toMethod'] for e in edges], ['Helper'])
            self.assertEqual([d['code'] for d in diagnostics], ['dynamic_call'])
            with self.assertRaisesRegex(UnsupportedSyntaxError, 'Динамический вызов'):
                project_method_closure(root, model, model['modules'][0], 'Main')

    def test_single_parameter_lambda_is_known_local_value(self):
        source = ('метод Main(): Число\n'
                  '    знч F = X -> X + Helper()\n'
                  '    возврат F(1)\n;\n'
                  'метод Helper(): Число\n    возврат 2\n;\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Main.xbsl').write_text(source)
            model = {'modules': [{'name': 'Main', 'namespace': '', 'sourceFile': 'Main.xbsl',
                                  'moduleType': 'module', 'imports': []}],
                     'elements': [], 'properties': {}}
            reachable, _, _, _ = project_method_closure(root, model, model['modules'][0], 'Main')
            self.assertEqual([method.split('(')[0].strip() for _, method, _ in reachable],
                             ['метод Main', 'метод Helper'])


if __name__ == '__main__':
    unittest.main()
