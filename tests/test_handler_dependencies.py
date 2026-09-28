"""Reachable methods and explicit object fakes, with trusted executor fixtures."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest

from element_test.assignment import load_assignment
from element_test.generated_types import ProjectTypes
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import REPO, decode_output, method_closure, prepare_script
from element_test.yaml_io import InputError


SOURCE = '''метод ПередЗаписью(До: Лица.Данные, Параметры: Лица.ПараметрыЗаписи)
    ФИО = СформироватьФИО(Фамилия)
;
метод СформироватьФИО(ФамилияЧеловека: Строка): Строка
    возврат "${Нормализовать(ФамилияЧеловека)}!"
;
метод Нормализовать(Значение: Строка): Строка
    возврат Значение.Сократить()
;
метод НеНужен(Параметр: НеподдерживаемыйТип): Строка
    возврат "unused"
;
'''


def fixture(root):
    (root / 'Лица.Объект.xbsl').write_text(SOURCE)
    (root / 'Заказ.Объект.xbsl').write_text('''метод Создать(Основание: Поставщик::Проект::CRM::Сделка.Ссылка)
    знч ИсходнаяСделка = Основание.ЗагрузитьОбъект()
    Клиент = ИсходнаяСделка.Клиент
    этот.Сделка = Основание
;
''')
    return {'properties': {'Поставщик': 'Поставщик', 'Имя': 'Проект'}, 'elements': [
        {'name': 'Лица', 'namespace': 'CRM', 'elementType': 'Справочник', 'properties': {
            'Реквизиты': [{'Имя': 'Фамилия', 'Тип': 'Строка'}, {'Имя': 'ФИО', 'Тип': 'Строка'}]}},
        {'name': 'Заказ', 'namespace': '', 'elementType': 'Документ', 'properties': {
            'Реквизиты': [{'Имя': 'Клиент', 'Тип': 'Контрагенты.Ссылка?'},
                          {'Имя': 'Сделка', 'Тип': 'Сделка.Ссылка?'}]}},
        {'name': 'Сделка', 'namespace': 'CRM', 'elementType': 'Документ', 'properties': {
            'Реквизиты': [{'Имя': 'Клиент', 'Тип': 'Контрагенты.Ссылка?'}]}},
        {'name': 'Контрагенты', 'namespace': 'CRM', 'elementType': 'Справочник', 'properties': {}}],
        'modules': [{'name': owner + '.Объект', 'namespace': namespace, 'moduleType': 'object',
                     'sourceFile': owner + '.Объект.xbsl'} for owner, namespace in (('Лица', 'CRM'), ('Заказ', ''))]}


def person_check():
    return {'target': {'module': 'Лица.Объект', 'method': 'ПередЗаписью'},
            'context': {'Фамилия': ' Иванов ', 'ФИО': 'old'}, 'args': [{}, {}], 'observe': ['ФИО']}


def order_check(identifier='deal-1'):
    return {'target': {'module': 'Заказ.Объект', 'method': 'Создать'}, 'context': {},
            'args': [{'Идентификатор': identifier}], 'observe': ['Клиент', 'Сделка'],
            'mocks': {'objects': {'Сделка.Ссылка': {
                'deal-1': {'Клиент': {'Идентификатор': 'client-1'}},
                'deal-2': {'Клиент': {'Идентификатор': 'client-2'}}}}}}


class HandlerDependenciesTest(unittest.TestCase):
    def test_transitive_closure_interpolation_and_recursive_calls(self):
        names = lambda source, name: [m.split('(')[0].strip() for m, _ in method_closure(source, name)]
        self.assertEqual(names(SOURCE, 'ПередЗаписью'),
                         ['метод ПередЗаписью', 'метод СформироватьФИО', 'метод Нормализовать'])
        source = '''метод A(): Строка
    // B()
    знч Текст = "B() \\${B()}"
    Чужой . B()
    возврат этот . C()
;
метод C(): Строка
    возврат A()
;
метод B(): Строка
    возврат "unused"
;
'''
        self.assertEqual(names(source, 'A'), ['метод A', 'метод C'])
        with self.assertRaisesRegex(InputError, 'неоднозначен'):
            method_closure(SOURCE + 'метод Нормализовать(): Строка\n;\n', 'ПередЗаписью')

    def test_real_archive_preparation_preserves_all_method_bodies(self):
        archive = REPO / 'Dvizhok.xdump'
        if not archive.is_file():
            self.skipTest('Требуется исходный архив')
        before = archive.read_bytes()
        with open_project(archive) as root, tempfile.TemporaryDirectory() as directory:
            model = analyze(root)
            for check in load_assignment(REPO / 'assignments/poc-handlers')['checks']:
                path = Path(directory) / check['id']
                path.mkdir()
                prepare_script(root, model, check, path)
                owner = check['target']['module'].split('.')[0]
                generated = (path / (owner + '.sbsl')).read_text()
                module = next(m for m in model['modules'] if m['name'] == check['target']['module'])
                source = (root / module['sourceFile']).read_text()
                for method, _ in method_closure(source, check['target']['method']):
                    self.assertIn(method[method.index('\n'):], generated)
                if owner == 'ФизическиеЛица':
                    self.assertIn('структура ПараметрыЗаписи', generated)
                    self.assertIn('метод СформироватьФИО', generated)
                else:
                    self.assertIn('Основание: Сделка.Ссылка', generated)
                    self.assertIn('метод ЗагрузитьОбъект', (path / 'Сделка.sbsl').read_text())
        self.assertEqual(before, archive.read_bytes())

    def test_invalid_mock_contracts_parameters_and_observe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = fixture(root)
            for patch in ({'observe': []}, {'observe': ['НеПоле']}, {'observe': ['ФИО', 'ФИО']},
                          {'mocks': {'queries': {}}}, {'mocks': {'objects': []}},
                          {'args': [{}, {'НеизвестныйФлаг': True}]}):
                with self.subTest(patch=patch), self.assertRaises(InputError):
                    prepare_script(root, model, {**person_check(), **patch}, root)
            types = ProjectTypes(model)
            for mocks in ({True: {}}, {'Сделка.Объект': {}}, {'Сделка.Ссылка': []},
                          {'Сделка.Ссылка': {1: {}}}, {'Сделка.Ссылка': {'a': {'НетПоля': 1}}}):
                with self.subTest(mocks=mocks), self.assertRaises(InputError):
                    ProjectTypes(model).configure_references(mocks)
            self.assertEqual(types.canonical_type('Массив<Поставщик::Проект::CRM::Сделка.Ссылка?>'),
                             'Массив<Сделка.Ссылка?>')
            for name in ('Чужой::Проект::CRM::Сделка.Ссылка', 'Неверно::Сделка.Ссылка'):
                with self.assertRaises(InputError):
                    types.require(name)
            duplicate = copy.deepcopy(model)
            duplicate['elements'].append({**model['elements'][2], 'namespace': 'CRM'})
            with self.assertRaises(InputError):
                ProjectTypes(duplicate).require('CRM::Сделка.Ссылка')

    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Требуется Script executor')
    def test_trusted_executor_dependencies_typed_lookup_and_missing_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = fixture(root)
            cases = [(person_check(), {'ФИО': 'Иванов!'}),
                     ({'target': {'module': 'Лица.Объект', 'method': 'СформироватьФИО'},
                       'args': [' Иванов ']}, 'Иванов!'),
                     ({**person_check(), 'target': {'module': 'Лица.Объект', 'method': 'СформироватьФИО'},
                       'args': [' Иванов ']}, {'return': 'Иванов!', 'context': {'ФИО': 'old'}}),
                     (order_check(), {'Клиент': {'Идентификатор': 'client-1'}, 'Сделка': {'Идентификатор': 'deal-1'}}),
                     (order_check('deal-2'), {'Клиент': {'Идентификатор': 'client-2'}, 'Сделка': {'Идентификатор': 'deal-2'}}),
                     (order_check('absent'), None)]
            for index, (check, expected) in enumerate(cases):
                sandbox = root / str(index)
                sandbox.mkdir()
                script = prepare_script(root, model, check, sandbox)
                result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                        cwd=sandbox, capture_output=True, text=True, timeout=30)
                if expected is None:
                    self.assertNotEqual(result.returncode, 0)
                else:
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(decode_output(result.stdout)['actual'], expected)
