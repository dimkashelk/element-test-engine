"""Contracts, unchanged handler logic and strict event decoding."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from element_test.assignment import load_assignment
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import REPO, decode_output, method_closure, prepare_script
from element_test.platform_mocks import PlatformMocks
from element_test.yaml_io import InputError


class PlatformMocksTest(unittest.TestCase):
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
