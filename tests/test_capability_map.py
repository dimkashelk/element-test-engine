"""Infrastructure regressions using small independent projects, not dump scenarios."""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from element_test.capability_map import build_map
from element_test.coverage import inventory
from element_test.model import analyze
from element_test.test_contracts import (FAMILIES, CAPABILITIES, contract_schema, map_schema,
                                        registry, validate_contract, validate_map)
from element_test.yaml_io import InvalidTestError


class CapabilityMapTest(unittest.TestCase):
    def project(self, root, source=None):
        (root / 'Проект.yaml').write_text('Имя: Probe\nПоставщик: Teacher\nВерсия: 1.0\nРежимСовместимости: 9.0\n')
        (root / 'Item.yaml').write_text('ВидЭлемента: Справочник\nИмя: Item\nОбластьВидимости: ВПроекте\nРеквизиты:\n - Имя: Label\n   Тип: Строка\n   Длина: 20\n')
        (root / 'Main.xbsl').write_text(source or ('метод One(): Объект\n'
            '    возврат Запрос{ВЫБРАТЬ Label ИЗ Item}.Выполнить()\n;\n'
            'метод Two(): Объект\n'
            '    возврат Запрос{ВЫБРАТЬ Label ИЗ Item}.Выполнить()\n;\n'))
        (root / 'Card.yaml').write_text('ВидЭлемента: КомпонентИнтерфейса\nИмя: Card\nНаследует:\n'
            ' Тип: Форма\n Содержимое:\n  Тип: Надпись\n  Имя: Caption\n  Значение: =Текст\n'
            ' ОсновнаяКоманда: =ЗаписатьИЗакрыть\n')
        (root / 'Report.yaml').write_text('ВидЭлемента: Отчет\nИмя: Report\nВидИсточникаДанных: Таблица\nИсточникДанных: Item\n'
            'Макет:\n Поля:\n  - Выражение: |\n      ЕСЛИ(Label = "a", 1, 0)\n    ВизуальныеРоли: [Строки]\n')
        (root / 'Nav.yaml').write_text('ВидЭлемента: ФрагментКомандногоИнтерфейса\nИмя: Nav\nЭлементы:\n'
                                      ' - Тип: НавигационнаяКоманда\n   ТипФормы: Card\n')
        (root / 'Panel.yaml').write_text('ВидЭлемента: ПанельОтчетов\nИмя: Panel\nМакет:\n'
            ' АвтоматическоеОбновление:\n  Значение: 30\n Виджеты:\n  - Отчет: Report\n')
        return analyze(root)

    def test_inventory_keeps_positions_categories_and_unknown_acceptance(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); model = self.project(root)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            data = build_map(root, model)
            self.assertEqual(data, build_map(root, model))
            self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})
            self.assertEqual(data['counts']['method'], 2)
            self.assertEqual(data['counts']['source-query'], 2)
            self.assertEqual(data['counts']['report-formula'], 1)
            self.assertEqual(data['counts']['binding'], 1)
            self.assertEqual(data['counts']['system-action'], 1)
            self.assertEqual(data['counts']['panel-widget'], 1)
            self.assertEqual(data['counts']['technicallyAccepted'], 0)
            queries = [i for i in data['items'] if i['kind'] == 'source-query']
            self.assertNotEqual(queries[0]['id'], queries[1]['id'])
            self.assertTrue(all(i['stages']['planning']['status'] == 'ready' for i in queries))
            self.assertTrue(all(i['stages']['technicalAcceptance']['status'] == 'not-checked' for i in data['items']))
            self.assertTrue(all(i['profile']['sourceCompatibilityVersion'] == '9.0' for i in data['items']))
            formula = next(i for i in data['items'] if i['kind'] == 'report-formula')
            a, b = formula['source']['position']['range']
            self.assertIn('ЕСЛИ(Label', (root / 'Report.yaml').read_text()[a:b])
            self.assertEqual(formula['source']['position']['yamlPath'], ['Макет', 'Поля', '0', 'Выражение'])
            source_kind = next(i for i in data['items'] if i['kind'] == 'report-source' and i['value'] == 'Таблица')
            self.assertNotEqual((source_kind['firstBlocker'] or {}).get('reasonCode'), 'REFERENCE_UNRESOLVED')
            self.assertEqual(source_kind['stages']['planning']['status'], 'not-checked')
            widget = next(i for i in data['items'] if i['kind'] == 'panel-widget')
            self.assertEqual(widget['value']['resolution'], 'unique')
            self.assertEqual(len(widget['dependsOn']), 1)
            self.assertFalse(any(k in json.dumps(data) for k in ('"criterionId":', '"expected":', '"PASS"', '"points":')))

    def test_timer_is_a_gap_without_author_scenario_and_shadowing_is_respected(self):
        source = ('метод Start()\n ПодключитьОбработчикТаймера(&Tick, 1с)\n;\n'
                  'метод Tick()\n ОтключитьОбработчикТаймера(&Tick)\n;\n')
        with TemporaryDirectory() as tmp:
            root = Path(tmp); model = self.project(root, source)
            data = build_map(root, model)
            calls = [i for i in data['items'] if i['kind'] == 'platform-call']
            self.assertEqual(len(calls), 2)
            method = next(i for i in data['items'] if i['kind'] == 'method')
            self.assertEqual(method['authorScenario']['status'], 'absent')
            self.assertEqual(method['firstBlocker']['reasonCode'], 'CAPABILITY_EXTENSION_REQUIRED')
            (root / 'Main.xbsl').write_text(source + 'метод ПодключитьОбработчикТаймера(Action: Объект, Interval: Объект)\n;\n')
            data = build_map(root, analyze(root))
            calls = [i['value']['operation'] for i in data['items'] if i['kind'] == 'platform-call']
            self.assertEqual(calls, ['ОтключитьОбработчикТаймера'])

    def test_source_hash_and_occurrence_completeness_guard_reused_plans(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); model = self.project(root)
            base = inventory(root, model)
            base['methods'][0]['directCriteria'] = [{'planned': True}]
            base['methods'][0]['executions'] = [{'status': 'PASS', 'independentlyAssessed': True}]
            data = build_map(root, model, method_inventory=base)
            self.assertEqual(data['counts']['plannedMethods'], 1)
            self.assertEqual(data['counts']['technicallyAccepted'], 0)
            bad = copy.deepcopy(base); bad['sourceHash'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'different source'):
                build_map(root, model, method_inventory=bad)
            bad = copy.deepcopy(base); bad['methods'].pop()
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                build_map(root, model, method_inventory=bad)

    def test_external_query_has_original_position_without_claiming_execution(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); self.project(root)
            text = 'ВЫБРАТЬ Label ИЗ Item'
            (root / 'Report.xbql').write_text(text)
            data = build_map(root, analyze(root))
            query = next(i for i in data['items'] if i['source']['file'] == 'Report.xbql')
            self.assertEqual(query['source']['position']['range'], [0, len(text)])
            self.assertEqual(query['stages']['planning']['status'], 'ready')
            self.assertEqual(query['stages']['adapter']['status'], 'not-checked')
            self.assertIn('query-entry', query['requires'])
            report = next(i for i in data['items'] if i['kind'] == 'report')
            self.assertIn(query['id'], report['dependsOn'])

    def test_query_parse_failure_keeps_later_stages_unknown(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); self.project(root, 'метод Rows(): Объект\n возврат Запрос{ВЫБРАТЬ Nope ИЗ Missing}.Выполнить()\n;\n')
            data = build_map(root, analyze(root))
            query = next(i for i in data['items'] if i['kind'] == 'source-query')
            self.assertEqual(query['firstBlocker']['reasonCode'], 'SOURCE_PARSE_BLOCKED')
            self.assertEqual(query['stages']['planning']['status'], 'not-checked')
            self.assertEqual(query['stages']['adapter']['status'], 'not-checked')
            method = next(i for i in data['items'] if i['kind'] == 'method')
            self.assertEqual(method['stages']['parsing']['status'], 'ready')
            self.assertEqual(method['firstBlocker']['reasonCode'], 'SOURCE_PARSE_BLOCKED')
            self.assertIn(query['id'], method['firstBlocker']['basis'])

    def contract(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data = build_map(root, self.project(root))
        return {'schemaVersion': 1, 'family': 'method', 'selector': data['items'][0]['source'], 'profile': data['profile'],
                'inputs': [{'name': 'Value', 'type': 'Соответствие<Строка,Массив<Item.Ссылка?>>'}],
                'actions': [{'operation': 'call', 'inputs': ['Value']}],
                'observations': [{'name': 'Result', 'type': 'Число', 'technicalOnly': False}],
                'comparison': {'kind': 'independent', 'oracle': 'author-defined rule', 'engine': 'SBSL'},
                'dependencies': ['method-runtime'], 'unavailableReasonCodes': ['ADAPTER_NOT_CHECKED'],
                'isolation': {'scope': 'check-session', 'clock': 'explicit', 'storage': 'fresh', 'callbacksOnClose': 'cancel'},
                'evidence': []}

    def test_contract_validation_rejects_invalid_versions_oracle_types_and_inputs(self):
        valid = self.contract()
        self.assertEqual(validate_contract(valid), valid)
        variants = []
        for key, value in [('schemaVersion', True), ('family', 'invented'), ('expected', 10), ('type', 'timer')]:
            bad = copy.deepcopy(valid); bad[key] = value; variants.append(bad)
        bad = copy.deepcopy(valid); bad['comparison']['kind'] = 'source-answer'; variants.append(bad)
        bad = copy.deepcopy(valid); bad['actions'][0]['inputs'] = ['Missing']; variants.append(bad)
        bad = copy.deepcopy(valid); bad['inputs'][0]['type'] = 'Массив<>'; variants.append(bad)
        bad = copy.deepcopy(valid); bad['actions'][0]['operation'] = 'unexpected-operation'; variants.append(bad)
        bad = copy.deepcopy(valid); bad['evidence'] = [{'sourceHash': '0' * 64, 'origin': 'old source', 'stage': 'execution'}]; variants.append(bad)
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(InvalidTestError):
                validate_contract(variant)

    def test_map_rejects_false_statuses_mixed_sources_and_dangling_links(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); data = build_map(root, self.project(root))
        for field, value in [('id', data['items'][1]['id']), ('dependsOn', ['missing'])]:
            bad = copy.deepcopy(data); bad['items'][0][field] = value
            with self.assertRaises(InvalidTestError): validate_map(bad)
        bad = copy.deepcopy(data); bad['items'][0]['source']['projectSourceHash'] = '0' * 64
        with self.assertRaises(InvalidTestError): validate_map(bad)
        bad = copy.deepcopy(data); bad['items'][0]['stages']['technicalAcceptance']['status'] = 'PASS'
        with self.assertRaises(InvalidTestError): validate_map(bad)

    def test_all_families_have_api_decisions_and_capability_dependencies(self):
        catalog = registry()
        self.assertEqual(len(catalog['families']), 8)
        self.assertEqual(catalog['publicYamlTypesAdded'], [])
        self.assertEqual({f['id'] for f in catalog['families']}, set(FAMILIES))
        for capability in catalog['capabilities']:
            self.assertLessEqual(set(capability['dependencies']), set(CAPABILITIES))
        self.assertEqual(contract_schema()['properties']['family']['enum'], list(FAMILIES))
        self.assertFalse(map_schema()['additionalProperties'])


if __name__ == '__main__': unittest.main()
