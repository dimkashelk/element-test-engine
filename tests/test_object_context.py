"""Object context executes with native SBSL scope, without rewriting field names."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from element_test.generated_types import ProjectTypes
from element_test.runtime import REPO, decode_output, prepare_script, extract_method
from element_test.loader import open_project
from element_test.model import analyze
from element_test.yaml_io import InputError


SOURCE = '''@Обработчик
метод Обновить(Номер: Строка, НовыйСтатус: СтатусЗаказа): Число
    этот.Номер = Номер
    Статус = НовыйСтатус
    пер Итог = 0
    для Строка из Товары
        Итог += Строка.Сумма
    ;
    возврат Итог
;
@Обработчик
метод Закрыть()
    Статус = СтатусЗаказа.Закрыт
    этот.Номер = "${Номер.Сократить()}!"
;
'''


def fixture(root):
    (root / 'Заказ.Объект.xbsl').write_text(SOURCE, encoding='utf-8')
    return {'compatibilityVersion': '9.0', 'elements': [
        {'name': 'Заказ', 'namespace': '', 'elementType': 'Документ', 'properties': {
            'Реквизиты': [{'Имя': 'Номер', 'Тип': 'Строка'},
                          {'Имя': 'Итог', 'Тип': 'Число'},
                          {'Имя': 'Статус', 'Тип': 'СтатусЗаказа', 'ЗначениеПоУмолчанию': 'Новый'}],
            'ТабличныеЧасти': [{'Имя': 'Товары', 'Реквизиты': [{'Имя': 'Сумма', 'Тип': 'Число'}]}]}},
        {'name': 'СтатусЗаказа', 'namespace': '', 'elementType': 'Перечисление', 'properties': {
            'Элементы': [{'Имя': 'Новый', 'ПоУмолчанию': True}, {'Имя': 'Закрыт'}]}}],
        'modules': [{'name': 'Заказ.Объект', 'namespace': '', 'moduleType': 'object',
                     'sourceFile': 'Заказ.Объект.xbsl'}]}


def checks():
    return [
        {'id': 'return_and_context', 'type': 'runtime', 'points': 1,
         'target': {'module': 'Заказ.Объект', 'method': 'Обновить'},
         'context': {'Номер': 'old', 'Товары': [{'Сумма': 7}, {'Сумма': 2.5}]},
         'args': ['new', 'СтатусЗаказа.Закрыт'],
         'expected': {'return': 9.5, 'context': {'Номер': 'new', 'Статус': 'Закрыт', 'Итог': 0,
                                              'Товары': [{'Сумма': 7}, {'Сумма': 2.5}]}}},
        {'id': 'void_and_defaults', 'type': 'runtime', 'points': 1,
         'target': {'module': 'Заказ.Объект', 'method': 'Закрыть'},
         'context': {'Номер': ' 001 '}, 'args': [],
         'expected': {'Номер': '001!', 'Статус': 'Закрыт', 'Товары': [], 'Итог': 0}}]


class ObjectContextTest(unittest.TestCase):
    def test_enum_inputs_defaults_identity_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            data = fixture(Path(directory))
            types = ProjectTypes(data)
            types.require('Заказ.Объект')
            types.require('Массив<СтатусЗаказа?>')
            self.assertEqual(types.literal(['Новый', None], 'Массив<СтатусЗаказа?>'),
                             'новый Массив<СтатусЗаказа.Значение?>([СтатусЗаказа.Новый, Неопределено])')
            for invalid in ('Нет', '${probe()}', 'Другое.Новый', True, {}):
                with self.subTest(invalid=invalid), self.assertRaises(InputError):
                    types.literal(invalid, 'СтатусЗаказа')
            for members in ([], [{'Имя': 'Новый'}, {'Имя': 'Новый'}],
                            [{'Имя': 'A', 'ПоУмолчанию': True}, {'Имя': 'B', 'ПоУмолчанию': True}]):
                bad = copy.deepcopy(data)
                bad['elements'][1]['properties']['Элементы'] = members
                with self.assertRaises(InputError):
                    ProjectTypes(bad).require('СтатусЗаказа')
            ambiguous = copy.deepcopy(data)
            ambiguous['elements'].append({**data['elements'][1], 'namespace': 'Other'})
            with self.assertRaises(InputError):
                ProjectTypes(ambiguous, 'Elsewhere').require('СтатусЗаказа')

    def test_context_required_and_original_body_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = fixture(root)
            check = checks()[0]
            with self.assertRaisesRegex(InputError, 'context'):
                prepare_script(root, data, {**check, 'context': None}, root)
            prepare_script(root, data, check, root)
            method, _ = extract_method(SOURCE, 'Обновить')
            body = method[method.index('\n'):]
            self.assertIn(body, (root / 'Заказ.sbsl').read_text())
            self.assertIn('НовыйСтатус: СтатусЗаказа.Значение', (root / 'Заказ.sbsl').read_text())
            with self.assertRaisesRegex(InputError, 'Неизвестные поля'):
                prepare_script(root, data, {**check, 'context': {'НеПоле': 1}}, root)
            data['modules'][0]['moduleType'] = 'module'
            with self.assertRaisesRegex(InputError, 'context'):
                prepare_script(root, data, check, root)

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Требуется Script executor')
    def test_native_context_enum_parameter_and_void_handler(self):
        # Trusted fixture only; student code continues to run exclusively in Docker.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = fixture(root)
            for index, check in enumerate(checks()):
                sandbox = root / str(index)
                sandbox.mkdir()
                script = prepare_script(root, data, check, sandbox)
                result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                        cwd=sandbox, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(decode_output(result.stdout)['actual'], check['expected'])

    @unittest.skipUnless((REPO / 'Движок.tar').is_file() and (REPO / 'script_u_10.0.2_1/lib').is_dir(),
                         'Требуются исходный архив и Script executor')
    def test_full_archive_order_contract_in_executor(self):
        before = (REPO / 'Движок.tar').read_bytes()
        with open_project(REPO / 'Движок.tar') as root, tempfile.TemporaryDirectory() as directory:
            types = ProjectTypes(analyze(root), 'Продажи')
            types.require('Заказ.Объект')
            types.require('Заказ.Данные')
            path = Path(directory)
            imports = types.write(path)
            literal = types.literal({'СтатусЗаказа': 'Новый', 'Товары': [{'Сумма': 5}]}, 'Заказ.Объект')
            script = path / 'Probe.sbsl'
            script.write_text(imports + '\nметод Скрипт()\n    знч О = ' + literal + '\n'
                '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"status": О.СтатусЗаказа, '
                '"sum": О.Товары[0].Сумма, "data": новый Заказ.Данные()}))\n;\n')
            result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                    cwd=path, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = json.loads(result.stdout)
            self.assertEqual((actual['status'], actual['sum']), ('Новый', 5))
            self.assertIsNone(actual['data']['СтатусЗаказа'])
        self.assertEqual((REPO / 'Движок.tar').read_bytes(), before)
