"""Prevent refresh from turning planning, duplicates or version gaps into PASS."""
import copy
from collections import defaultdict
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from element_test.indexer import parse_module
from element_test.model import analyze
from element_test.query_catalog import source_records
from element_test.query_catalog_refresh import (
    REPO, applicability, build_queue, classify, inspect_query, link_api, method_probe,
)


class CatalogRefreshTest(unittest.TestCase):
    def project(self, folder, source):
        root = Path(folder) / 'project'
        shutil.copytree(REPO / 'tests/corpus/query-040-042-completion/ordinary', root)
        path = root / 'Entry/Main.xbsl'
        path.write_text(source)
        model = analyze(root)
        records = [r for r in source_records(root, model, 'input.xdump', 'Teacher::projection-ordinary')
                   if r['file'] == 'Entry/Main.xbsl']
        module = next(m for m in model['modules'] if m['sourceFile'] == 'Entry/Main.xbsl')
        return root, model, records, module

    def test_current_planning_does_not_assess_or_change_old_failure(self):
        source = ('метод Rows(Name: Строка): Объект\n'
                  '    возврат Запрос{ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == %Name}.Выполнить()\n;\n')
        with TemporaryDirectory() as d:
            root, model, records, module = self.project(d, source)
            record = next(r for r in records if r['family'] == 'literal')
            record.update(parsed=False, unsupportedReason='Historical unsupported source')
            before = copy.deepcopy(record)
            node = parse_module(source)[0][0]
            query = inspect_query(root, model, record, module, source, node)
            method = method_probe(root, model, record, module, source, node, [query])
            self.assertEqual(query['status'], 'planned-query', query)
            self.assertEqual(method['status'], 'planned-probe', method)
            self.assertFalse(method['argumentsValidated'])
            self.assertFalse(method['authorCriterion'])
            self.assertEqual(before, record)
            again = inspect_query(root, model, record, module, source, node)
            self.assertEqual(query, again)  # random storage paths cannot leak

    def test_shadowed_query_is_not_promoted_by_old_ast(self):
        source = ('метод Rows(Запрос: Строка): Объект\n'
                  '    возврат Запрос{ВЫБРАТЬ Label ИЗ Data::Item}.Выполнить()\n;\n')
        with TemporaryDirectory() as d:
            root, model, records, module = self.project(d, source)
            r = next(r for r in records if r['family'] == 'literal')
            r.update(parsed=True, ast={'fake': 'historical'})
            review = inspect_query(root, model, r, module, source, parse_module(source)[0][0])
            self.assertFalse(review['parsed'])
            self.assertIn('Затенённый', review['reason'])

    def test_api_alias_links_only_to_exact_query_and_reassignment_stays_open(self):
        source = ('метод Rows(): Объект\n'
                  '    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}\n'
                  '    знч Alias = Q\n'
                  '    возврат Alias.Выполнить()\n;\n')
        with TemporaryDirectory() as d:
            _, model, records, module = self.project(d, source)
            q = next(r for r in records if r['family'] == 'literal')
            api = next(r for r in records if r['family'] == 'api-candidate')
            node = parse_module(source)[0][0]
            linked = link_api(api, [q], source, node, module, model)
            self.assertEqual(linked, {'status': 'linked-source-query', 'relatedContractIds': [q['contractId']]})
        changed = source.replace('знч Alias = Q', 'знч Alias = Q\n    Alias = "different"')
        with TemporaryDirectory() as d:
            _, model, records, module = self.project(d, changed)
            q = next(r for r in records if r['family'] == 'literal')
            api = next(r for r in records if r['family'] == 'api-candidate')
            review = link_api(api, [q], changed, parse_module(changed)[0][0], module, model)
            self.assertNotEqual(review['status'], 'linked-source-query')

    def test_equal_text_different_methods_remains_two_work_items(self):
        source = ''.join('метод ' + name + '(): Объект\n'
                         '    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}\n'
                         '    возврат Q.Выполнить()\n;\n' for name in ('One', 'Two'))
        with TemporaryDirectory() as d:
            _, model, records, module = self.project(d, source)
            queries = [r for r in records if r['family'] == 'literal']
            reviews = {}
            memberships = defaultdict(set)
            for r in records:
                memberships[r['contractId']] = {'040', '042'}
                review = {'kind': classify(r), 'applicability': applicability(r)}
                if r['family'] == 'literal':
                    review.update(queryPlanning={'queryPlanned': True, 'status': 'planned-query'},
                                  methodPlanning={'status': 'planned-probe',
                                                  'queryOccurrences': [{'file': r['file'], 'range': r['range']}]})
                else:
                    node = next(n for n in parse_module(source)[0] if n.start <= r['range'][0] < n.end)
                    review['apiResolution'] = link_api(r, queries, source, node, module, model)
                reviews[r['contractId']] = review
            queue = build_queue(records, reviews, memberships)
            self.assertEqual(len(queue), 2)
            self.assertEqual({r['status'] for r in queue}, {'needs-author-criterion'})
            self.assertTrue(all(len(r['relatedApiContractIds']) == 1 for r in queue))
            self.assertNotEqual(queue[0]['range'], queue[1]['range'])

    def test_version_exclusion_does_not_remove_legacy_source(self):
        doc = {'contractId': 'documentation-table-entitycontractname_ru', 'compatibilityVersion': '9.0'}
        source = {'contractId': 'real-query', 'compatibilityVersion': '9.0'}
        self.assertEqual(applicability(doc)['status'], 'excluded-from-9.3-matrix')
        self.assertEqual(applicability(source, 'Таблица контракта сущности удалена из платформы после 7.0')['status'],
                         'legacy-source-version-review')
        for name in ('state', 'rights'):
            self.assertEqual(classify({'contractId': 'documented-' + name, 'family': 'documented-form'}),
                             'requirement-outline')

    def test_two_consumers_of_dynamic_query_share_one_source_obligation(self):
        source = ('метод Rows(): Объект\n'
                  '    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Data::Item")\n'
                  '    Q.УстановитьПараметр("Unused", 1)\n'
                  '    возврат Q.Выполнить().ВМассив()\n;\n')
        with TemporaryDirectory() as d:
            _, model, records, module = self.project(d, source)
            reviews = {}
            node = parse_module(source)[0][0]
            for r in records:
                reviews[r['contractId']] = {'kind': classify(r), 'applicability': applicability(r),
                    'apiResolution': link_api(r, [], source, node, module, model)}
            queue = build_queue(records, reviews, defaultdict(set))
            self.assertEqual(len(queue), 1, queue)
            self.assertEqual(queue[0]['kind'], 'api-source')
            self.assertEqual(queue[0]['status'], 'needs-api-scenario')
            self.assertEqual(len(queue[0]['contractIds']), 2)

    def test_reused_query_variable_does_not_link_to_earlier_literal(self):
        source = ('метод Rows(): Объект\n'
                  '    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}\n'
                  '    знч Q = Запрос{ВЫБРАТЬ Amount ИЗ Data::Item}\n'
                  '    возврат Q.Выполнить()\n;\n')
        with TemporaryDirectory() as d:
            _, model, records, module = self.project(d, source)
            queries = [r for r in records if r['family'] == 'literal']
            api = next(r for r in records if r['family'] == 'api-candidate')
            review = link_api(api, queries, source, parse_module(source)[0][0], module, model)
            self.assertEqual(review['relatedContractIds'], [queries[-1]['contractId']])


if __name__ == '__main__':
    unittest.main()
