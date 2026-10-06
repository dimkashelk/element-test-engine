"""Independent task-44 extension oracles, authored before implementation."""
import json
import os
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from result_fixtures import CORPUS, REPO, check, expected
from element_test.model import analyze
from element_test.execution_plan import plan_execution
from element_test.runtime import run_pure
from element_test.bridge import write_json
from element_test.yaml_io import InputError
from test_query_projections import grade
from unittest.mock import patch

OUT = REPO / 'result/result-resources-completion'
SOURCES = {
 'Legacy': """метод Legacy(Text: Строка): Объект
    знч Q = новый ЗапросСВыборкой(Text)
    Q.УстановитьПараметр("Name", "A")
    исп R = Q.Выполнить()
    возврат R.ВМассив()
;
""",
 'RuntimeError': """метод RuntimeError(Text: Строка): Объект
    знч Q = новый ПроизвольныйЗапрос()
    Q.Текст = Text
    Q.УстановитьПараметр("Name", 42)
    попытка
        исп R = Q.Выполнить()
        возврат R.ВМассив()
    поймать E: Исключение
        возврат {"stored": Q.Текст, "caught": Истина}
    ;
;
""",
 'Unknown': """метод Unknown(): Объект
    знч Q = Запрос{ВЫБРАТЬ Optional КАК Name ЗАПОЛНИТЬ Data::Card ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label УБЫВ}
    исп R = Q.Выполнить()
    пер First = ""
    попытка
        для Row из R
            First = Row.Name
        ;
    поймать E: Исключение
        возврат {"first": First, "failed": Истина}
    ;
    возврат {"first": First, "failed": Ложь}
;
""",
 'RuntimeText': '''метод RuntimeText(Text: Строка, Second: Строка): Объект
    знч Q: ПроизвольныйЗапрос = новый ПроизвольныйЗапрос(Text)
    Q.УстановитьПараметр("Name", "A")
    исп R = Q.Выполнить()
    знч First = R.ВМассив()
    Q.Текст = Second
    исп Again = Q.Выполнить()
    знч Columns = Again.ПолучитьОписанияКолонок()
    знч Values = новый Массив<Объект?>()
    знч Materialized = Again.ВМассив()
    для Row из Materialized
        Values.Добавить(Row.Count)
    ;
    возврат {"first": First, "second": Values, "column": Columns[0].Имя}
;
''',
 'Scoped': '''метод Scoped(): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label")
    знч Values = новый Массив<Объект?>()
    исп R = Q.Выполнить()
    для Row из R
        Values.Добавить(Row["Label"])
    ;
    Q.Текст = "ВЫБРАТЬ Total ИЗ Data::Item УПОРЯДОЧИТЬ ПО Total"
    исп Again = Q.Выполнить()
    для Row из Again
        Values.Добавить(Row["Total"])
    ;
    возврат Values
;
''',
 'Qualifiers': '''метод Qualifiers(): Объект
    знч Q = Запрос{ВЫБРАТЬ ВЫРАЗИТЬ(Label КАК Строка(5)) КАК Name, ВЫРАЗИТЬ(Total КАК Число(4, 2)) КАК Amount ИЗ Data::Item}
    исп R = Q.Выполнить()
    знч C = R.ПолучитьОписанияКолонок()
    возврат [C[0].Тип.КвалификаторСтроки.ДлинаСтроки, C[1].Тип.КвалификаторЧисла.ДлинаЦелойЧасти, C[1].Тип.КвалификаторЧисла.ДлинаДробнойЧасти, C[0].Тип.КвалификаторЧисла == Неопределено]
;
''',
 'Owners': '''метод Owners(): Объект
    возврат {"data": Data::QueryOwner.Values(), "other": Other::QueryOwner.Values()}
;
''',
 'Alias': '''метод Alias(): Объект
    знч Original = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Data::Item")
    знч Q = Original
    Q.Текст = "ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Name"
    Q.УстановитьПараметр("Name", "B")
    исп R = Q.Выполнить()
    знч Copy = R
    для Row из Copy
        возврат Row["Label"]
    ;
    возврат "empty"
;
''',
 'EmptySet': '''метод EmptySet(): Объект
    знч Q = новый ПроизвольныйЗапрос()
    Q.Текст = "invalid query"
    пер Caught = Ложь
    попытка
        Q.Выполнить()
    поймать E: Исключение
        Caught = Истина
    ;
    знч InvalidText = Q.Текст
    Q.Текст = "ВЫБРАТЬ Label ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label"
    исп R = Q.Выполнить()
    возврат {"caught": Caught, "invalid": InvalidText, "rows": R.ВМассив()}
;
''',
 'Source': '''метод Source(): Объект
    знч Rows = новый Массив<Data::Card>()
    Rows.Добавить(новый Data::Card(Name = "Z", Amount = 9))
    Rows.Добавить(новый Data::Card(Name = "A", Amount = 1))
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Name, Amount ИЗ &Rows УПОРЯДОЧИТЬ ПО Name")
    Q.УстановитьИсточникДанных("Unused", 42)
    Q.УстановитьИсточникДанных("Rows", Rows)
    исп R = Q.Выполнить()
    Rows[0].Amount = 13
    исп Again = Q.Выполнить()
    возврат {"old": R.ВМассив(), "fresh": Again.ВМассив()}
;
''',
 'Native': '''метод Native(): Объект
    знч Q = Запрос{ВЫБРАТЬ Label ЗАПОЛНИТЬ Дата ИЗ Data::Item УПОРЯДОЧИТЬ ПО Total УБЫВ}
    исп R = Q.Выполнить()
    пер First = ""
    попытка
        для Value из R
            First = Value.ВСтроку()
        ;
    поймать E: Исключение
        возврат {"first": First, "invalid": Истина}
    ;
    возврат {"first": First, "invalid": Ложь}
;
''',
 'Columns': '''метод Columns(): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label, Total, Optional ИЗ Data::Item")
    исп R = Q.Выполнить()
    знч Columns = R.ПолучитьОписанияКолонок()
    знч Rows = R.ВМассив()
    возврат {"names": [Columns[0].Имя, Columns[1].Имя, Columns[2].Имя],
        "types": [Columns[0].Тип.Типы.Содержит("".ПолучитьТип()), Columns[1].Тип.Типы.Содержит((0).ПолучитьТип()), Columns[2].Тип.Типы.Содержит(Неопределено.ПолучитьТип())],
        "after": R.ПолучитьОписанияКолонок().Размер()}
;
''',
 'Transfer': '''метод Transfer(): Массив<Provider.Shared>
    возврат Provider.Rows()
;
''',
 'Narrow': '''метод Narrow(): Объект
    знч Q = Запрос{ВЫБРАТЬ Optional КАК Name ЗАПОЛНИТЬ Data::Card ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label УБЫВ}
    исп R = Q.Выполнить()
    пер First = ""
    попытка
        для Row из R
            First = Row.Name
        ;
    поймать E: Исключение
        возврат {"first": First, "failed": Истина}
    ;
    возврат {"first": First, "failed": Ложь}
;
''',
 'Index': '''метод Index(): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label, Total ИЗ Data::Item УПОРЯДОЧИТЬ ПО Label")
    исп R = Q.Выполнить()
    знч Values = новый Массив<Объект?>()
    для Row из R
        Values.Добавить(Row["Label"])
        Values.Добавить(Row["Total"])
    ;
    возврат Values
;
''',
 'Text': '''метод Text(Filtered: Булево): Объект
    пер Filter = ""
    если Filtered
        Filter = " ГДЕ Label == &Name"
    ;
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Data::Item" + Filter)
    Q.УстановитьПараметр("Name", "A")
    исп R = Q.Выполнить()
    знч Before = R.ВМассив()
    Q.Текст = "ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Other"
    Q.УстановитьПараметр("Other", "B")
    исп Again = Q.Выполнить()
    возврат {"before": Before, "after": Again.ВМассив(), "text": Q.Текст}
;
''',
 'Direct': '''метод Direct(): Объект
    исп R = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}.Выполнить()
    для Row из R
        возврат Row.Label
    ;
    возврат "empty"
;
''',
}
RUNTIME_ARGS = ['ВЫБРАТЬ S.Label КАК Name ИЗ Data::Item КАК S ГДЕ S.Label == &Name',
                'ВЫБРАТЬ Total КАК Count ИЗ Data::Item ГДЕ Label != &Name УПОРЯДОЧИТЬ ПО Total УБЫВ']
ANSWERS = {'Legacy':[{'Label':'A'}], 'RuntimeError':{'stored':'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Name','caught':True},
           'Unknown':{'first':'note','failed':True},
           'RuntimeText':{'first':[{'Name':'A'}],'second':[1],'column':'Count'},
           'Scoped':['A','B',1,3], 'Qualifiers':[5,4,2,True],
           'Narrow': {'first': 'note', 'failed': True},
           'Owners':{'data':[{'Label':'A'},{'Label':'B'}],'other':[{'Label':'neighbor'}]},
           'Alias':'B',
           'EmptySet':{'caught':True,'invalid':'invalid query','rows':[{'Label':'A'},{'Label':'B'}]},
           'Source': {'old':[{'Name':'A','Amount':1},{'Name':'Z','Amount':9}],
                      'fresh':[{'Name':'A','Amount':1},{'Name':'Z','Amount':13}]},
           'Native': {'first':'2026-09-30','invalid':True},
           'Columns': {'names':['Label','Total','Optional'],'types':[True,True,True],'after':3},
           'Transfer': [{'Label':'A'},{'Label':'B'}],
           'Index': ['A', 3, 'B', 1], 'Direct': 'A',
           'Text': {'before': [{'Label': 'A'}], 'after': [{'Label': 'B'}],
                    'text': 'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Other'}}

def project(temp, method):
    root = Path(temp) / method
    shutil.copytree(CORPUS / 'ordinary', root)
    (root / 'Entry/Main.xbsl').write_text(SOURCES[method])
    if method=='Transfer':
        (root / 'Entry/Provider.xbsl').write_text('''@ВПроекте
метод Rows(): Массив<Shared>
    знч Q = Запрос{ВЫБРАТЬ Label @ВПроекте ПОРОДИТЬ Shared ИЗ Data::Item}
    исп R = Q.Выполнить()
    возврат R.ВМассив()
;
''')
    if method=='Owners':
        for namespace in ('Data','Other'):
            (root/namespace/'QueryOwner.xbsl').write_text('''@ВПроекте
метод Values(): Объект
    знч Q = новый ПроизвольныйЗапрос("ВЫБРАТЬ Label ИЗ Item")
    исп R = Q.Выполнить()
    возврат R.ВМассив()
;
''')
    if method=='Unknown':
        path=root/'Data/Item.yaml';path.write_text(path.read_text().replace('Тип: Строка?','Тип: Объект?'))
    c = check(method=method)
    if method=='RuntimeError':c['args']=[ANSWERS[method]['stored']]
    if method=='Legacy':c['args']=['ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Name']
    if method=='Unknown':c['storage']['initial'][0]['value']['Optional']=42
    if method=='Native':
        c['storage']['initial'][0]['value']['Label']='2026-09-30'
        c['storage']['initial'][1]['value']['Label']='invalid-date'
    if method == 'RuntimeText': c['args']=RUNTIME_ARGS
    if method == 'Text':
        c['args'] = [True]
    return root, c

class CompletionPlanTest(unittest.TestCase):
    def test_same_query_text_keeps_distinct_resolved_owners(self):
        with TemporaryDirectory() as d:
            root,c=project(d,'Owners');p=plan_execution(root,analyze(root),c)
            self.assertEqual({q['ast']['owner'] for q in p.queries},{'Data::Item','Other::Item'})
            self.assertEqual(sum(name.startswith('ТестДинамический') for name in p.contracts.definitions),2)
    def test_runtime_dynamic_rows_remain_readonly_after_materialization(self):
        with TemporaryDirectory() as d:
            root,c=project(d,'RuntimeText')
            source=root/'Entry/Main.xbsl'
            source.write_text(source.read_text().replace('Values.Добавить(Row.Count)','Row.Count = 12'))
            with self.assertRaisesRegex(InputError,'только для чтения'):
                plan_execution(root,analyze(root),c)

    def test_produced_visibility_and_public_constructor_are_enforced(self):
        with TemporaryDirectory() as d:
            root,c=project(d,'Transfer')
            provider=root/'Entry/Provider.xbsl'
            provider.write_text(provider.read_text().replace('@ВПроекте ПОРОДИТЬ','@Локально ПОРОДИТЬ'))
            with self.assertRaisesRegex(InputError,'недоступен'):
                plan_execution(root,analyze(root),c)
        with TemporaryDirectory() as d:
            root,c=project(d,'Transfer')
            source=root/'Entry/Main.xbsl'
            source.write_text(source.read_text().replace('возврат Provider.Rows()',
                'знч Existing = Provider.Rows()\n    возврат [новый Provider.Shared()]'))
            with self.assertRaisesRegex(InputError,'публичного конструктора'):
                plan_execution(root,analyze(root),c)

    def test_runtime_text_is_not_specialized_from_arguments(self):
        with TemporaryDirectory() as d:
            root,c=project(d,'RuntimeText')
            p=plan_execution(root,analyze(root),c)
            self.assertTrue(any(q['api']=='runtime-text-emulation-044' for q in p.queries))
            self.assertNotIn(RUNTIME_ARGS[0],p.to_json())
            c['args']=['secret query one','secret query two']
            other=plan_execution(root,analyze(root),c)
            self.assertEqual(p.queries,other.queries)
            self.assertEqual({k:v for k,v in p.contracts.definitions.items() if k!='ТестСессия'},
                             {k:v for k,v in other.contracts.definitions.items() if k!='ТестСессия'})

    def test_extensions_are_planned_from_source_without_expected(self):
        OUT.mkdir(parents=True, exist_ok=True)
        for method in SOURCES:
            with self.subTest(method=method), TemporaryDirectory() as d:
                root, c = project(d, method)
                c['expected'] = {'oracle-secret': -92483}
                p = plan_execution(root, analyze(root), c)
                self.assertNotIn('oracle-secret', p.to_json())
                write_json(OUT / ('plan-' + method + '.json'), p.to_dict())

    def test_platform_descriptor_alias_does_not_replace_project_structure(self):
        with TemporaryDirectory() as d:
            root,c=project(d,'Columns')
            source=root/'Entry/Main.xbsl'
            source.write_text('структура ОписаниеКолонкиРезультатаЗапроса\n    пер Имя: Строка\n;\n'+
                'метод Columns(C: ОписаниеКолонкиРезультатаЗапроса): Объект\n'+
                '    знч Q = Запрос{ВЫБРАТЬ Label ИЗ Data::Item}\n    исп R = Q.Выполнить()\n'+
                '    возврат C.Имя\n;\n')
            c['args']=[{'Имя':'own'}]
            p=plan_execution(root,analyze(root),c)
            self.assertNotIn('ОписаниеКолонкиРезультатаЗапроса',p.contracts.platform_type_aliases)
            self.assertIn('ОписаниеКолонкиРезультатаЗапроса',p.contracts.required_structures)
            self.assertEqual(p.contracts.platform_type_aliases['Стд::БазаДанных::ОписаниеКолонкиРезультатаЗапроса'],
                             'ТестКолонкаЗапроса.Описание')

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS') == '1', 'Task44 Docker opt-in')
class CompletionDockerTest(unittest.TestCase):
    def test_multiline_constructor_preserves_native_text_and_rows(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root,c=project(d,'Scoped')
            (root/'Entry/Main.xbsl').write_text('''метод Scoped(): Объект
    знч Native =
        "ВЫБРАТЬ Label ИЗ Data::Item
         ГДЕ Label == &Name"
    знч Q = новый ПроизвольныйЗапрос(
        "ВЫБРАТЬ Label ИЗ Data::Item
         ГДЕ Label == &Name")
    Q.УстановитьПараметр("Name", "A")
    исп R = Q.Выполнить()
    возврат {"text": Q.Текст, "native": Native, "rows": R.ВМассив()}
;
'''.replace('Item\n','Item   \n'))
            value='ВЫБРАТЬ Label ИЗ Data::Item\nГДЕ Label == &Name'
            answer={'text':value,'native':value,'rows':[{'Label':'A'}]}
            result=run_pure(root,analyze(root),c,temp)
            self.assertEqual(result['status'],'EXECUTED',result)
            self.assertEqual(result['actual']['result'],answer)
            self.assertEqual(grade(result['actual'],expected(c,answer),temp),'PASS')

    def test_runtime_text_errors_are_deferred_and_schema_checked_when_empty(self):
        for text in ('not a query','ВЫБРАТЬ Missing ИЗ Data::Item',
                     'ВЫБРАТЬ Label КАК Same, Total КАК Same ИЗ Data::Item',
                     'ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Name'):
            with self.subTest(text=text), TemporaryDirectory() as d:
                root,c=project(d,'RuntimeError');c['args']=[text];c['storage']['initial']=[]
                r=run_pure(root,analyze(root),c,Path(d))
                self.assertEqual(r['status'],'EXECUTED',r)
                self.assertEqual(grade(r['actual'],expected(c,{'stored':text,'caught':True}),Path(d)),'PASS')
                write_json(OUT/('error-text-'+str(len(text))+'.json'),r)

    def test_new_business_mutations_fail_and_correct_alternative_passes(self):
        changes = [('Narrow','Label УБЫВ','Label'),('Index','Row["Label"]','Row["Total"]'),
                   ('Source','Amount = 13','Amount = 12'),
                   ('Text','УстановитьПараметр("Other", "B")','УстановитьПараметр("Other", "A")')]
        for method,before,after in changes:
            with self.subTest(method=method), TemporaryDirectory() as d:
                root,c=project(d,method)
                source=root/'Entry/Main.xbsl';source.write_text(source.read_text().replace(before,after))
                r=run_pure(root,analyze(root),c,Path(d))
                write_json(OUT/('mutation-'+method+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',r)
                status=grade(r['actual'],expected(c,ANSWERS[method]),Path(d))
                self.assertEqual(status,'FAIL')
                write_json(OUT/('assessment-mutation-'+method+'.json'),{'status':status,'engine':'SBSL'})
        with TemporaryDirectory() as d:
            root,c=project(d,'Text');c['args']=[False]
            answer={**ANSWERS['Text'],'before':[{'Label':'A'},{'Label':'B'}]}
            r=run_pure(root,analyze(root),c,Path(d))
            write_json(OUT/'Text-false.json',r)
            self.assertEqual(r['status'],'EXECUTED',r)
            self.assertEqual(grade(r['actual'],expected(c,answer),Path(d)),'PASS')

    def test_extensions_independent_answers_and_balanced_resources(self):
        for method, answer in ANSWERS.items():
            with self.subTest(method=method), TemporaryDirectory() as d:
                root, c = project(d, method)
                r = run_pure(root, analyze(root), c, Path(d))
                write_json(OUT / (method + '.json'), r)
                self.assertEqual(r['status'], 'EXECUTED', r)
                self.assertEqual(grade(r['actual'], expected(c, answer), Path(d)), 'PASS', r)
                trace = r['storageTrace']
                self.assertEqual(trace.count('query-result:open'), trace.count('query-result:close'))

@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Task44 public Docker opt-in')
class CompletionPublicTest(unittest.TestCase):
    def test_new_contracts_receive_public_sbsl_grading(self):
        from element_test.bridge import run_test
        from composite_fixtures import dump_yaml
        root=OUT/'new-public-project'
        shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
        (root/'Entry/Main.xbsl').write_text(''.join(SOURCES.values()))
        path=root/'Data/Item.yaml';path.write_text(path.read_text().replace('Тип: Строка?','Тип: Объект?'))
        with TemporaryDirectory() as d:
            for method in ('Transfer','Owners'):
                donor,_=project(d,method)
                for path in donor.rglob('*.xbsl'):
                    if path.name in {'Provider.xbsl','QueryOwner.xbsl'}:
                        destination=root/path.relative_to(donor);destination.write_text(path.read_text())
        checks=[]
        for method,answer in ANSWERS.items():
            c=check(method=method)
            if method=='Text':c['args']=[True]
            if method=='RuntimeText':c['args']=RUNTIME_ARGS
            if method=='RuntimeError':c['args']=[ANSWERS[method]['stored']]
            if method=='Legacy':c['args']=['ВЫБРАТЬ Label ИЗ Data::Item ГДЕ Label == &Name']
            if method=='Unknown':c['storage']['initial'][0]['value']['Optional']=42
            if method=='Native':
                c['storage']['initial'][0]['value']['Label']='2026-09-30'
                c['storage']['initial'][1]['value']['Label']='invalid-date'
            checks.append({'id':method,'type':'runtime','points':1,'comparison':'record-sets-unordered',
                           **c,'expected':expected(c,answer)})
        assignment=OUT/'new-public-assignment';assignment.mkdir(exist_ok=True)
        (assignment/'assignment.yaml').write_text(dump_yaml({'name':'Query results completion 44','checks':checks},allow_unicode=True,sort_keys=False))
        r,g=run_test(root,assignment,OUT/'new-public')
        self.assertTrue(all(c['status']=='PASS' for c in r['checks']),r)
        self.assertEqual(r['score'],len(checks));self.assertEqual(g['unavailablePoints'],0)

@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task44 PostgreSQL opt-in')
class CompletionSqlTest(unittest.TestCase):
    def test_lazy_fill_and_dynamic_text_sql_audit_cleanup(self):
        import secrets
        from element_test.integration import run_integration
        for method in ('Narrow','Text','RuntimeText'):
            with self.subTest(method=method), TemporaryDirectory() as d, patch.dict(os.environ,
                {'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
                root,c=project(d,method)
                c['storage']['backend']='postgres'
                c['integration']={'backend':'postgres','operation':'metadata-storage'}
                r=run_integration(c,analyze(root),Path(d),root=root,enabled=True,inject_failure=True)
                write_json(OUT/('sql-'+method+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',r)
                self.assertTrue(r['integration']['cleanup'])
                self.assertEqual(grade(r['actual'],expected(c,ANSWERS[method]),Path(d)),'PASS')
