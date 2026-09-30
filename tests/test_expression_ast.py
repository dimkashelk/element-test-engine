"""The indexed XBSL body tree retains nested expressions and source spans."""
import unittest

from element_test.indexer import method_call_expressions, parse_module, parse_module_tree


class ExpressionAstTest(unittest.TestCase):
    def test_practical_corpus_syntax_and_broken_counterparts(self):
        cases = {
            'moment literal': (
                '    возврат Момент{2024-03-15T09:45:22Z}\n',
                '    возврат Момент{2024-03-15T09:45:22Z\n', 2, 'literal'),
            'multiline local method': (
                '    знч F = метод(\n        A: Число,\n        B: Строка?\n'
                '    ) ->\n        возврат A\n    ;\n    F(1, "x")\n',
                '    знч F = метод(\n        A: Число,\n        B: Строка?\n'
                '    )\n        возврат A\n    ;\n', 2, 'lambda'),
            'nullable cast after call': (
                '    возврат F() как Пользователи.Ссылка?\n',
                '    возврат F() как Пользователи.Ссылка? +\n', 2, 'cast'),
            'nested function type': (
                '    возврат F() как ((Объект?)->ничто)\n',
                '    возврат F() как ((Объект?)->)\n', 2, 'lambda'),
            'comparison after newline': (
                '    если A\n        > B\n        возврат Истина\n    ;\n',
                '    если A\n        >\n        возврат Истина\n    ;\n', 3, 'binary'),
            'multiline callback': (
                '    X.Подключить(\n'
                '        метод (A: Число, B: Строка?) ->\n'
                '            если A > 0\n                Use(B)\n            ;\n'
                '        ;\n    )\n',
                '    X.Подключить(\n'
                '        метод (A: Число, B: Строка?)\n'
                '            Use(B)\n        ;\n    )\n', 3, 'lambda'),
            'generic type test before a logical operator': (
                '    если X это Соответствие<Строка, неизвестно> и F(\n'
                '            X как Соответствие<Строка, неизвестно>, Истина)\n'
                '        возврат Истина\n    ;\n',
                '    если X это Соответствие<Строка, неизвестно и F(\n'
                '            X как Соответствие<Строка, неизвестно>, Истина)\n'
                '        возврат Истина\n    ;\n', 2, 'generic'),
        }
        for name, (valid, invalid, line, kind) in cases.items():
            with self.subTest(name=name):
                methods, _, errors = parse_module('метод Run()\n' + valid + ';\n')
                self.assertEqual(errors, [])
                self.assertIn(kind, [node.kind for node in methods[0].expression_tree.walk()])
                _, _, errors = parse_module('метод Run()\n' + invalid + ';\n')
                self.assertTrue(any(error.startswith(f'Строка {line}:') for error in errors), errors)

    def test_statements_operators_literals_and_nested_blocks(self):
        source = ('метод Run(Значение: Число): Число\n'
                  '    знч Таблица = {"a": [1, 2]}\n'
                  '    если Значение == 1\n'
                  '        Таблица["a"] = Значение + 2 * 3\n'
                  '    ;\n'
                  '    возврат Значение > 0 ? Таблица["a"] : 0\n;\n')
        methods, _, errors = parse_module(source)
        self.assertEqual(errors, [])
        tree = methods[0].expression_tree
        self.assertEqual([child.kind for child in tree.children],
                         ['statement', 'block', 'statement'])
        nodes = list(tree.walk())
        self.assertIn('map', [node.kind for node in nodes])
        self.assertIn('array', [node.kind for node in nodes])
        self.assertIn('conditional', [node.kind for node in nodes])
        self.assertIn('assignment', [node.kind for node in nodes])
        condition = next(node for node in nodes if node.kind == 'binary' and node.value == '==')
        self.assertEqual(source[condition.start:condition.end], 'Значение == 1')
        assignment = next(node for node in nodes if node.kind == 'assignment')
        self.assertEqual(source[assignment.start:assignment.end],
                         'Таблица["a"] = Значение + 2 * 3')
        self.assertFalse(any(node.kind == 'error' for node in nodes))

    def test_lambda_query_interpolation_and_multiline_chain(self):
        source = ('метод Run()\n'
                  '    знч F = метод(X: Число) ->\n'
                  '        Helper(X)\n'
                  '        Other()\n'
                  '    ;\n'
                  '    знч Запрос = Запрос{ВЫБРАТЬ %{Helper(1)}}\n'
                  '    возврат Клиент\n'
                  '        .Создать()\n'
                  '        .Выполнить("${F(2)|ЧЧ:мм}")\n;\n')
        methods, _, errors = parse_module(source)
        self.assertEqual(errors, [])
        tree = methods[0].expression_tree
        nodes = list(tree.walk())
        self.assertEqual([node.kind for node in nodes].count('lambda'), 1)
        lam = next(node for node in nodes if node.kind == 'lambda')
        self.assertEqual([node.kind for node in lam.children[1].children],
                         ['statement', 'statement'])
        self.assertEqual([node.kind for node in nodes].count('query'), 1)
        self.assertEqual([node.kind for node in nodes].count('interpolation'), 2)
        self.assertEqual([node.kind for node in nodes].count('format'), 1)
        self.assertEqual([call.name for call in method_call_expressions(source, methods[0])],
                         ['Helper', 'Other', 'Helper', 'Создать', 'Выполнить', 'F'])

    def test_constructor_generic_union_and_optional(self):
        source = ('метод Run(X: Объект?)\n'
                  '    знч A = новый Массив<Строка|Булево>()\n'
                  '    знч B = <Строка, Массив<Число>>{:}\n'
                  '    если X это не Неопределено\n'
                  '        X!.Добавить(A)\n'
                  '    ;\n;\n')
        method, _, errors = parse_module(source)
        self.assertEqual(errors, [])
        kinds = [node.kind for node in method[0].expression_tree.walk()]
        self.assertIn('new', kinds)
        self.assertIn('typed_literal', kinds)
        self.assertIn('nonnull', kinds)

    def test_unparsed_expression_marks_index_incomplete(self):
        source = 'метод Run()\n    возврат A +\n;\n'
        method, _, errors = parse_module(source)
        self.assertIsNotNone(method[0].expression_tree)
        self.assertTrue(any('некорректное выражение' in error for error in errors))

    def test_module_constants_defaults_and_quoted_interpolation(self):
        source = ('конст Начало = [1, 2]\n'
                  '@Настройка(Номер = 1)\n'
                  'метод Run(X: Число = Helper(1))\n'
                  '    возврат "${Helper("метод Fake()")}, $X"\n;\n')
        tree, methods, _, errors = parse_module_tree(source)
        self.assertEqual(errors, [])
        self.assertEqual([node.kind for node in tree.children],
                         ['constant', 'annotation', 'method'])
        self.assertEqual([node.kind for node in methods[0].header_tree.walk()].count('call'), 1)
        self.assertEqual([call.name for call in method_call_expressions(source, methods[0])],
                         ['Helper', 'Helper'])
        self.assertEqual([node.kind for node in tree.walk()].count('interpolation'), 2)

    def test_documented_literal_and_operator_forms(self):
        source = ('метод Run()\n'
                  '    знч A = Дата{2020-03-01}\n'
                  '    знч B = 1ч 30м 5с\n'
                  "    знч C = 'а+'\n"
                  '    знч D = 2 ** 3\n'
                  '    возврат "Дата $A, число %D"\n;\n')
        methods, _, errors = parse_module(source)
        self.assertEqual(errors, [])
        kinds = [node.kind for node in methods[0].expression_tree.walk()]
        for kind in ('literal', 'duration', 'regex', 'binary', 'interpolation'):
            self.assertIn(kind, kinds)

    def test_generic_member_call_and_method_reference_invocation(self):
        source = ('метод Run(Значения: Массив<Строка>)\n'
                  '    Значения.Преобразовать<Число>(X -> X.Длина())\n'
                  '    Событие.ПодключитьОбработчик(&Обновить())\n;\n')
        method = parse_module(source)[0][0]
        self.assertEqual([(call.kind, call.name) for call in method_call_expressions(source, method)],
                         [('member', 'Преобразовать'), ('member', 'Длина'),
                          ('member', 'ПодключитьОбработчик'), ('reference', 'Обновить')])

    def test_range_loop_and_type_case(self):
        source = ('метод Run(Значение: Строка|Число)\n'
                  '    для Индекс = 0 по Items.Размер() - 1 шаг 2\n'
                  '        выбор Значение\n'
                  '        когда это Число\n'
                  '            Use(Индекс)\n'
                  '        ;\n'
                  '    ;\n;\n')
        methods, _, errors = parse_module(source)
        self.assertEqual(errors, [])
        kinds = [node.kind for node in methods[0].expression_tree.walk()]
        self.assertIn('range', kinds)
        self.assertIn('type_check', kinds)
        self.assertEqual([call.name for call in method_call_expressions(source, methods[0])],
                         ['Размер', 'Use'])


if __name__ == '__main__':
    unittest.main()
