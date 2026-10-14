"""Mixed CASE types and native Script transport through both storage backends."""
import copy
from hashlib import sha256
import os
from pathlib import Path
import secrets
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.execution_plan import plan_execution
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.storage import PostgresSession
from element_test.runtime import prepare_script, run_pure
from element_test.yaml_io import InputError

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / 'tests/corpus/virtual-tables/ordinary'
REFS = [{'Идентификатор': '00000000-0000-0000-0000-00000000000' + str(i)} for i in (1, 2, 3)]


def tagged(ref, typ='Data::Item.Ссылка'):
    return {'type': typ, 'value': ref}


JOIN = (' ИЗ Data::Item КАК A ЛЕВОЕ СОЕДИНЕНИЕ Data::Item КАК B'
        ' ПО A.Key == B.Key И B.Key != "b" УПОРЯДОЧИТЬ ПО A.Key')


def config(backend='memory'):
    c = {'querySource': {'file': 'Data/Totals.xbql'}, 'runtimeProfile': '9.3',
         'storage': {'idType': 'Ууид', 'backend': backend, 'initial': [
             {'type': 'Data::Item', 'value': {'Ссылка': ref, 'Key': key, 'Phones': [], 'Rows': []}}
             for ref, key in zip(REFS, ('a', 'b', 'c'))]}}
    if backend == 'postgres':
        c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
    return c


class CaseFixture(unittest.TestCase):
    def setUp(self):
        self.work = TemporaryDirectory(); self.addCleanup(self.work.cleanup)
        self.base = Path(self.work.name)
        self.root = self.base / 'project'
        shutil.copytree(CORPUS, self.root)
        self.query = self.root / 'Data/Totals.xbql'

    def plan(self, expression, c=None):
        self.query.write_text('ВЫБРАТЬ A.Key, ' + expression + ' КАК Value' + JOIN)
        return plan_execution(self.root, analyze(self.root), c or config())


class CaseReferencePlanTest(CaseFixture):
    def test_branch_order_nested_unions_and_missing_values(self):
        cases = [
            ('ВЫБОР КОГДА B.Ссылка ЕСТЬ NULL ТОГДА "Нет поставщика" ИНАЧЕ B.Ссылка КОНЕЦ', 'Item.Ссылка|Строка|?', True),
            ('ВЫБОР КОГДА B.Ссылка ЕСТЬ НЕ NULL ТОГДА B.Ссылка ИНАЧЕ "Нет поставщика" КОНЕЦ', 'Item.Ссылка|Строка|?', True),
            ('ВЫБОР КОГДА A.Key == "c" ТОГДА NULL ИНАЧЕ ВЫБОР КОГДА B.Ссылка ЕСТЬ NULL ТОГДА "Нет поставщика" ИНАЧЕ B.Ссылка КОНЕЦ КОНЕЦ', 'Item.Ссылка|Строка|?', True),
            ('ВЫБОР A.Key КОГДА "a" ТОГДА A.Ссылка КОГДА "b" ТОГДА "Нет поставщика" КОНЕЦ', 'Item.Ссылка|Строка|?', True),
            ('ВЫБОР КОГДА A.Key == "c" ТОГДА НЕОПРЕДЕЛЕНО КОГДА A.Key == "b" ТОГДА "Нет поставщика" ИНАЧЕ A.Ссылка КОНЕЦ', 'Item.Ссылка|Строка|?', False),
            ('ВЫБОР КОГДА A.Key == "a" ТОГДА A.Ссылка ИНАЧЕ A.Ссылка КОНЕЦ', 'Item.Ссылка', False),
        ]
        for expression, typ, nullable in cases:
            with self.subTest(expression=expression):
                p = self.plan(expression)
                e = p.queries[0]['ast']['projections'][1][0]
                self.assertEqual(e['type'], typ)
                self.assertEqual(e['sql_nullable'], nullable)
                out = self.base / 'generated'; out.mkdir(exist_ok=True)
                prepare_script(self.root, analyze(self.root), config(), out)

    def test_parameter_capture_retains_nominal_reference_type(self):
        c = config(); c['queryParameters'] = {'Supplier': {'type': 'Data::Item.Ссылка', 'value': REFS[0]}}
        p = self.plan('ВЫБОР КОГДА A.Key == "a" ТОГДА A.Ссылка КОГДА A.Key == "b" ТОГДА &Supplier ИНАЧЕ "Нет поставщика" КОНЕЦ', c)
        self.assertEqual(p.query_entry['parameters'][0]['type'], 'Item.Ссылка')
        c['queryParameters']['Supplier']['type'] = 'Строка'
        c['queryParameters']['Supplier']['value'] = 'wrong'
        with self.assertRaises(InputError):
            self.plan('ВЫБОР КОГДА A.Key == "a" ТОГДА A.Ссылка ИНАЧЕ &Supplier КОНЕЦ', c)

    def test_invalid_conditions_arithmetic_sorting_and_collection_branches(self):
        mixed = 'ВЫБОР КОГДА A.Key == "a" ТОГДА A.Ссылка ИНАЧЕ "Нет поставщика" КОНЕЦ'
        for expression in (mixed + ' + 1', mixed + ' + "!"',
                           'ВЫБОР КОГДА A.Key ТОГДА A.Ссылка ИНАЧЕ "none" КОНЕЦ',
                           'ВЫБОР КОГДА A.Key == "a" ТОГДА A.Phones ИНАЧЕ A.Ссылка КОНЕЦ'):
            with self.subTest(expression=expression), self.assertRaises(InputError):
                self.plan(expression)
        self.query.write_text('ВЫБРАТЬ ' + mixed + ' КАК Value ИЗ Data::Item КАК A УПОРЯДОЧИТЬ ПО Value')
        with self.assertRaises(InputError):
            plan_execution(self.root, analyze(self.root), config())


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Script/PostgreSQL opt-in')
class CaseReferenceRuntimeTest(CaseFixture):
    def execute(self, c, root=None):
        root = root or self.root
        if c['storage']['backend'] == 'postgres':
            audits = []; original = PostgresSession.audit
            def audit(session, database, docker):
                rows = original(session, database, docker)
                audits.append(copy.deepcopy(rows)); return rows
            with patch.dict(os.environ, {'ELEMENT_TEST_INTEGRATION_PASSWORD': secrets.token_hex(24)}), patch.object(PostgresSession, 'audit', audit):
                r = run_integration(c, analyze(root), self.base, root=root, enabled=True)
            self.assertTrue(r.get('integration', {}).get('cleanup'), r)
            self.assertTrue(audits, r)
            self.assertEqual(r['actual']['storage'], audits[-1])
        else:
            r = run_pure(root, analyze(root), c, self.base)
        self.assertEqual(r['status'], 'EXECUTED', r)
        self.assertEqual(r['storageDiagnostics'], {'active': False, 'locks': 0})
        return r['actual']['result']

    def test_mixed_case_join_null_undefined_nested_and_columns(self):
        cases = [
            ('ВЫБОР КОГДА B.Ссылка ЕСТЬ NULL ТОГДА "Нет поставщика" ИНАЧЕ B.Ссылка КОНЕЦ', [REFS[0], 'Нет поставщика', REFS[2]]),
            ('ВЫБОР КОГДА B.Ссылка ЕСТЬ НЕ NULL ТОГДА B.Ссылка ИНАЧЕ "Нет поставщика" КОНЕЦ', [REFS[0], 'Нет поставщика', REFS[2]]),
            ('ВЫБОР КОГДА A.Key == "c" ТОГДА NULL ИНАЧЕ ВЫБОР КОГДА B.Ссылка ЕСТЬ NULL ТОГДА "Нет поставщика" ИНАЧЕ B.Ссылка КОНЕЦ КОНЕЦ', [REFS[0], 'Нет поставщика', None]),
            ('ВЫБОР A.Key КОГДА "a" ТОГДА A.Ссылка КОГДА "b" ТОГДА "Нет поставщика" КОНЕЦ', [REFS[0], 'Нет поставщика', None]),
            ('ВЫБОР КОГДА A.Key == "c" ТОГДА НЕОПРЕДЕЛЕНО КОГДА A.Key == "b" ТОГДА "Нет поставщика" ИНАЧЕ A.Ссылка КОНЕЦ', [REFS[0], 'Нет поставщика', None]),
        ]
        for backend in ('memory', 'postgres'):
            for expression, values in cases:
                with self.subTest(backend=backend, expression=expression):
                    c = config(backend); self.plan(expression, c)
                    observation = self.execute(c)
                    self.assertEqual(observation['rows'], [{'Key': key, 'Value': tagged(value) if isinstance(value, dict) else value} for key, value in zip(('a', 'b', 'c'), values)])
                    self.assertEqual(observation['cardinality'], 3)
                    types = observation['columns'][1]['ТестТип']['Типы']
                    self.assertIn('Std::String', types)
                    self.assertIn('Std::Undefined', types)
                    self.assertEqual(len(types), 3)
                    self.assertIn('Scripts::Item.Ссылка', types)
                    self.assertEqual(observation['resource'], {'kind': 'query-result', 'closed': True, 'started': True})

    def test_repeat_after_write_keeps_detached_reference_results(self):
        for backend in ('memory', 'postgres'):
            with self.subTest(backend=backend):
                c = config(backend)
                self.plan('ВЫБОР КОГДА A.Key == "b" ТОГДА "Нет поставщика" ИНАЧЕ A.Ссылка КОНЕЦ', c)
                c['queryActions'] = [{'operation': 'execute'}, {'operation': 'write', 'objects': [
                    {'type': 'Data::Item', 'value': {'Ссылка': REFS[0], 'Key': 'b', 'Phones': [], 'Rows': []}}]}, {'operation': 'execute'}]
                actions = self.execute(c)['actions']
                self.assertEqual(actions[0]['rows'][0], {'Key': 'a', 'Value': tagged(REFS[0])})
                self.assertEqual([row['Value'] for row in actions[1]['rows']], ['Нет поставщика', 'Нет поставщика', tagged(REFS[2])])

    def test_union_branch_keeps_distinct_owners_with_equal_reference_ids(self):
        root = self.base / 'nominal-project'
        shutil.copytree(REPO / 'tests/corpus/tagged-reference-unions/ordinary', root)
        (root / 'Data/References.xbql').write_text(
            'ВЫБРАТЬ Ссылка КАК Doc, ВЫБОР КОГДА Previous == "none" ТОГДА "Нет поставщика" '
            'ИНАЧЕ Choice КОНЕЦ КАК Value ИЗ Card')
        choices = [tagged(REFS[2]), tagged(REFS[2], 'Other::Item.Ссылка'), None]
        for backend in ('memory', 'postgres'):
            with self.subTest(backend=backend):
                c = {'querySource': {'file': 'Data/References.xbql'}, 'runtimeProfile': '9.3',
                     'storage': {'backend': backend, 'idType': 'Ууид', 'initial': [
                         {'type': 'Data::Card', 'value': {'Ссылка': ref, 'Choice': choice,
                             'Previous': 'none' if choice is None else '', 'Rows': []}}
                         for ref, choice in zip(REFS, choices)]}}
                if backend == 'postgres':
                    c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
                result = self.execute(c, root)
                self.assertCountEqual(result['rows'], [{'Doc': ref, 'Value': choice if choice is not None else 'Нет поставщика'}
                                                  for ref, choice in zip(REFS, choices)])

    def test_original_final_supplier_report_and_main_panel(self):
        archive = REPO / 'Dvizhok-dev-final.xdump'
        before = sha256(archive.read_bytes()).hexdigest()
        with open_project(archive) as root:
            registers = ['Товары::РегистрПоставщикиНоменклатуры', 'Товары::РегистрЦеныНоменклатуры',
                         'Товары::РегистрТовары', 'Финансы::РегистрДенежныеСредства']
            rows = [{'Ссылка': REFS[0], 'Поставщик': tagged(REFS[2], 'CRM::Контрагенты.Ссылка'), 'Период': '2026-10-01T00:00:00Z', 'Цена': 15},
                    {'Ссылка': REFS[1], 'Поставщик': 'Нет поставщика', 'Период': None, 'Цена': 'Нет цены'}]
            for backend in ('memory', 'postgres'):
                for surface in ('report', 'panel'):
                    with self.subTest(backend=backend, surface=surface):
                        c = {'runtimeProfile': '9.3', 'storage': {'backend': backend, 'idType': 'Ууид', 'registers': registers,
                            'initial': [{'type': 'Товары::Номенклатура', 'value': {'Ссылка': ref, 'Наименование': key}} for ref, key in zip(REFS, ('a', 'b'))],
                            'initialRegisters': [{'type': registers[0], 'filter': {'Номенклатура': REFS[0], 'Поставщик': REFS[2]},
                                'rows': [{'Период': '2026-10-01T00:00:00Z', 'Номенклатура': REFS[0], 'Поставщик': REFS[2], 'Цена': 15}]}]},
                            'clock': {'mode': 'fixed', 'date': '2026-10-14', 'time': '12:00:00', 'timezone': 'UTC'}}
                        if backend == 'postgres':
                            c['integration'] = {'backend': 'postgres', 'operation': 'metadata-storage'}
                        if surface == 'report':
                            c['report'] = {'schemaVersion': 1, 'report': {'name': 'ОтчетПоставщикиНоменклатуры', 'namespace': 'Товары'}}
                        else:
                            c['panel'] = {'schemaVersion': 1, 'panel': {'name': 'ПанельОтчетовГлавная', 'namespace': 'Администрирование'}}
                            c['timers'] = {'onClose': 'cancel'}; c['executorLocale'] = 'en-US'
                        result = self.execute(c, root)
                        result = result['actions'][0]
                        if surface == 'panel':
                            result = result['observation']['widgets']['ProductSuppliers']
                        self.assertCountEqual(result['data'], rows)
                        self.assertCountEqual(result['values'], [
                            {'2f54791283c84c2180db869808b5cbc3': REFS[0], '01f7cec8ef944b0fa7d3cebd72626dde': REFS[2],
                             '482209de303e457b861c8d389e559c64': '2026-10-01T00:00:00Z', '5794ecb4996047dd9e366a31526ba980': 15},
                            {'2f54791283c84c2180db869808b5cbc3': REFS[1], '01f7cec8ef944b0fa7d3cebd72626dde': 'Нет поставщика',
                             '482209de303e457b861c8d389e559c64': None, '5794ecb4996047dd9e366a31526ba980': 'Нет цены'}])
        self.assertEqual(sha256(archive.read_bytes()).hexdigest(), before)
