"""Contracts, unchanged handler logic and strict event decoding."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import subprocess

from element_test.assignment import load_assignment
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import REPO, decode_output, method_closure, prepare_script
from element_test.platform_mocks import PlatformMocks
from element_test.generated_types import ProjectTypes
from element_test.yaml_io import InputError


class PlatformMocksTest(unittest.TestCase):
    @unittest.skipUnless((REPO / 'script_u_10.0.2_1/lib').is_dir(), 'Требуется Script executor')
    def test_trusted_union_literals_and_register_defaults_in_executor(self):
        # Generated contract probe only; no student handler runs on the host.
        model = {'elements': [
            {'name': name, 'namespace': '', 'elementType': 'Документ', 'properties': {}}
            for name in ('Отгрузка', 'Поступление')
        ] + [{'name': 'Движения', 'namespace': '', 'elementType': 'РегистрНакопления', 'properties': {
            'Реквизиты': [{'Имя': 'Регистратор', 'Тип': 'Отгрузка.Ссылка|Поступление.Ссылка|?'}],
            'Измерения': [{'Имя': 'Описание', 'Тип': 'Строка', 'ЗначениеПоУмолчанию': 'default'}],
            'Ресурсы': [{'Имя': 'Количество', 'Тип': 'Число', 'ЗначениеПоУмолчанию': 1.25}]}}]}
        types = ProjectTypes(model)
        platform = PlatformMocks(types, {'registers': ['Движения']}, {'captureCalls': True})
        platform.finish()
        union = 'Отгрузка.Ссылка|Поступление.Ссылка|?'
        values = [types.literal({'type': owner + '.Ссылка', 'value': {'Идентификатор': 'same-id'}}, union)
                  for owner in ('Отгрузка', 'Поступление')]
        values.append(types.literal(None, union))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            imports = types.write(path)
            script = path / 'Probe.sbsl'
            script.write_text(imports + '\nметод Скрипт()\n'
                '    знч Набор = новый Движения.НаборЗаписей()\n' + ''.join(
                    '    Набор.Фильтр.Установить(Регистратор = ' + v + ')\n' for v in values)
                + '    Набор.ДобавитьЗапись(Период = новый ДатаВремя(2024, 2, 29, 1, 2, 3), '
                  'ВидЗаписи = ВидЗаписиРегистраНакопления.Расход)\n'
                  '    Набор.Записать()\n'
                  '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({'
                  '"actual": {"calls": новый Массив<Объект?>()}, "_captureCalls": Истина}))\n;\n')
            result = subprocess.run([str(REPO / 'bin/script-runtime'), '-c', '9.0', str(script)],
                                    capture_output=True, text=True, timeout=30, cwd=path)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = decode_output(result.stdout)['actual']['calls']
            self.assertEqual([c['args']['Регистратор'] for c in calls[:3]],
                             [{'Идентификатор': 'same-id'}, {'Идентификатор': 'same-id'}, None])
            self.assertEqual(calls[3]['args'], {'Период': '2024-02-29T01:02:03', 'ВидЗаписи': 'Расход',
                                              'Описание': 'default', 'Количество': 1.25})
            self.assertEqual(calls[4]['args'], {'Замещать': True})

    def test_real_movement_body_and_union_filter_contract(self):
        archive = REPO / 'Движок.tar'
        if not archive.is_file():
            self.skipTest('Требуется исходный архив')
        check = load_assignment(REPO / 'assignments/poc-movements')['checks'][1]
        with open_project(archive) as root, tempfile.TemporaryDirectory() as directory:
            model, path = analyze(root), Path(directory)
            prepare_script(root, model, check, path)
            module = next(m for m in model['modules'] if m['name'] == 'Отгрузка.Объект')
            method = method_closure((root / module['sourceFile']).read_text(), 'ПослеЗаписи')[0][0]
            self.assertIn(method, (path / 'ТестКонтекст.sbsl').read_text())
            register = (path / 'РегистрТовары.sbsl').read_text()
            self.assertIn('Регистратор: Отгрузка.Ссылка|ПоступлениеТоваров.Ссылка|?', register)
            self.assertIn('#требуется ПоступлениеТоваров.sbsl', register)
            self.assertIn('Период: ДатаВремя', register)
            self.assertIn('метод Записать(Замещать: Булево = Истина)', register)
            bad = copy.deepcopy(model)
            next(e for e in bad['elements'] if e['name'] == 'РегистрТовары')['properties']['ВидРегистра'] = 'Обороты'
            with self.assertRaisesRegex(InputError, 'только Остатки'):
                prepare_script(root, bad, check, path)

    def test_query_matching_preserves_strings_and_token_boundaries(self):
        key = PlatformMocks.query_key
        self.assertEqual(key('ВЫБРАТЬ\n  A /* comment */ КАК B'), key('ВЫБРАТЬ A КАК B'))
        self.assertNotEqual(key('ВЫБРАТЬ "a  b"'), key('ВЫБРАТЬ "a b"'))
        self.assertNotEqual(key('ВЫБРАТЬ AB'), key('ВЫБРАТЬ A B'))
        self.assertEqual(key('ВЫБРАТЬ "https://a  b" // comment'), 'ВЫБРАТЬ "https://a  b"')

    def test_events_projection_and_exception_result(self):
        call = {'owner': 'Prices', 'method': 'ДобавитьЗапись', 'args': {'Цена': 5, 'Период': 'now'}}
        value = {'actual': {'result': {'context': {}, 'calls': []}, 'exception': None},
                 '_captureCalls': True, '_captureException': True, '_observeCallArguments': ['Цена']}
        actual = decode_output('ELEMENT_CALL ' + json.dumps(call, indent=2) + '\n' + json.dumps(value) + '\n0')
        self.assertEqual(actual, {'actual': {'result': {'context': {}, 'calls': [
            {**call, 'args': {'Цена': 5}}]}, 'exception': None}})
        with self.assertRaises(InputError):
            decode_output(json.dumps(value) + '\nunexpected')
        with self.assertRaises(ValueError):
            decode_output('unexpected\n' + json.dumps(value))

    @unittest.skipUnless((REPO / 'Движок.tar').is_file(), 'Требуется исходный архив')
    def test_real_handler_contracts_preserve_logic_and_reject_wrong_fixtures(self):
        archive = REPO / 'Движок.tar'
        before = archive.read_bytes()
        checks = load_assignment(REPO / 'assignments/poc-platform')['checks']
        with open_project(archive) as root, tempfile.TemporaryDirectory() as directory:
            model = analyze(root)
            path = Path(directory)
            price = checks[0]
            prepare_script(root, model, price, path)
            module = next(m for m in model['modules'] if m['name'] == price['target']['module'])
            method = method_closure((root / module['sourceFile']).read_text(), 'ПослеЗаписи')[0][0]
            self.assertIn(method, (path / 'ТестКонтекст.sbsl').read_text())
            self.assertIn('пер Ссылка: Номенклатура.Ссылка', (path / 'Номенклатура.sbsl').read_text())
            self.assertIn('пер Наименование: Строка', (path / 'Номенклатура.sbsl').read_text())
            prepare_script(root, model, checks[1], path)
            generated = (path / 'Отгрузка.sbsl').read_text()
            self.assertIn('Товары.Преобразовать(Данные -> Данные.Номенклатура)', generated)
            self.assertIn('Результат.ВСоответствие(Ключ -> Ключ.Номенклатура, Значение -> Значение.Количество)', generated)
            self.assertIn('выбросить новый ИсключениеНедопустимоеСостояние', generated)
            patches = [ {'mocks': {'queries': []}},
                        {'captureCalls': 'yes'}, {'captureException': 'yes'},
                        {'mocks': {'registers': ['РегистрЦеныНоменклатуры', 'РегистрЦеныНоменклатуры']}},
                        {'mocks': {'registers': ['Отгрузка']}} ]
            for patch in patches:
                with self.subTest(patch=patch), self.assertRaises(InputError):
                    prepare_script(root, model, {**checks[1], **patch}, path)
            for mutate in [lambda q: q.update(text=q['text'].replace('Остатки', 'Обороты')),
                           lambda q: q.update(rows=[{'НетПоля': 1}]),
                           lambda q: q.update(rows=[{'Количество': True}]),
                           lambda q: q.update(fields={'BadName!': 'Число'})]:
                bad = copy.deepcopy(checks[1])
                mutate(bad['mocks']['queries'][0])
                with self.assertRaises(InputError):
                    prepare_script(root, model, bad, path)
        self.assertEqual(archive.read_bytes(), before)
