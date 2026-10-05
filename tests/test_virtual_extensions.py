"""Independent acceptance of the remaining task-43 executor source arguments.

Expected values below are authored from the fixed histories, never derived
from the planner, renderer, or a first execution of student code.
"""
import copy
import json
import os
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from yaml import safe_dump, safe_load
from composite_fixtures import dump_yaml
from virtual_fixtures import CORPUS, REPO, START, END, check, expected, REFS
from element_test.bridge import write_json, run_test
from element_test.execution_plan import plan_execution
from element_test.model import analyze
from element_test.runtime import run_pure
from element_test.integration import run_integration
from element_test.generated_types import ProjectTypes
from element_test.query_plan import parse_storage_query
from element_test.yaml_io import InputError
from test_query_projections import grade

OUT = REPO / 'result/virtual-tables-completion'
OUT.mkdir(parents=True, exist_ok=True)
NEXT = '2026-10-02T00:00:00'

SOURCE = '''
метод Filter(T: ДатаВремя, K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ Key, TotalОстаток ИЗ Data::Ledger.Остатки(%T, Key == %K ИЛИ Key == "c") УПОРЯДОЧИТЬ ПО Key}.Выполнить()
;
метод Days(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ Key, Период, TotalОборот ИЗ Data::Ledger.Обороты(%S, %E, ПериодичностьИтоговРегистраНакопления.День) УПОРЯДОЧИТЬ ПО Key, Период}.Выполнить()
;
метод Monthly(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ Key, Период, TotalНачальныйОстаток, TotalОборот, TotalКонечныйОстаток ИЗ Data::Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Месяц) УПОРЯДОЧИТЬ ПО Key, Период}.Выполнить()
;
метод Auto(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ Key, ПериодДень, TotalОборот ИЗ Data::Ledger.Обороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Авто, Key.НачинаетсяС("a"))}.Выполнить()
;
метод BoundSaved(K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ Key, TotalОстаток ИЗ Data::Totals(%K) УПОРЯДОЧИТЬ ПО Key}.Выполнить()
;
метод Joined(T: ДатаВремя, K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ Q.Key КАК Key, Q.TotalОстаток + 1 КАК Value ИЗ Data::Ledger.Остатки(%T, Key == %K) КАК Q СОЕДИНЕНИЕ Data::Item КАК I ПО Q.Key == I.Key}.Выполнить()
;
метод Records(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ Key, Период, Индекс, НомерСтроки, TotalОборот ИЗ Data::Ledger.Обороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Запись) УПОРЯДОЧИТЬ ПО Индекс}.Выполнить()
;
метод Calendar(): Объект
    возврат Запрос{ВЫБРАТЬ Key, Период, TotalОборот ИЗ Data::Ledger.Обороты(,, ПериодичностьИтоговРегистраНакопления.Декада) УПОРЯДОЧИТЬ ПО Key, Период}.Выполнить()
;
метод NextPeriod(D: Дата): Объект
    возврат Запрос{ВЫБРАТЬ Key, Период, СледующийПериод ИЗ Data::Book.СрезПоследних(%D) УПОРЯДОЧИТЬ ПО Key}.Выполнить()
;
'''

def project(temp, variant='ordinary'):
    root = temp / variant
    shutil.copytree(CORPUS / variant, root)
    ns, entry, main = ('Учет','Вход','Поток') if variant=='renamed' else ('Data','Entry','Main')
    source = root / entry / (main+'.xbsl')
    source.write_text(source.read_text()+SOURCE.replace('Data::',ns+'::'))
    meta = root / ns / 'Totals.yaml'
    value = safe_load(meta.read_text())
    value['Параметры'] = [{'Имя':'Code','Тип':'Строка'}]
    meta.write_text(safe_dump(value,allow_unicode=True,sort_keys=False))
    (root / ns / 'Totals.xbql').write_text('ВЫБРАТЬ Key, TotalОстаток ИЗ Ledger.Остатки(, Key == &Code)')
    return root


CASES = {
    'Filter': ([END,'a'], [{'Key':'a','TotalОстаток':5},{'Key':'c','TotalОстаток':2}]),
    'Days': ([START,NEXT], [{'Key':'a','Период':START,'TotalОборот':-5}, {'Key':'a','Период':NEXT,'TotalОборот':8}, {'Key':'b','Период':START,'TotalОборот':1}, {'Key':'c','Период':START,'TotalОборот':2}, {'Key':'c','Период':NEXT,'TotalОборот':-2}]),
    'Monthly': ([START,NEXT], [{'Key':'a','Период':START,'TotalНачальныйОстаток':10,'TotalОборот':3,'TotalКонечныйОстаток':13}, {'Key':'b','Период':START,'TotalНачальныйОстаток':-2,'TotalОборот':1,'TotalКонечныйОстаток':-1}, {'Key':'c','Период':START,'TotalНачальныйОстаток':0,'TotalОборот':0,'TotalКонечныйОстаток':0}]),
    'Auto': ([START,NEXT], [{'Key':'a','ПериодДень':START,'TotalОборот':-5},{'Key':'a','ПериодДень':NEXT,'TotalОборот':8}]),
    'BoundSaved': (['a'], [{'Key':'a','TotalОстаток':13}]),
    'Joined': ([END,'a'], [{'Key':'a','Value':6}]),
    'Records': ([START,END], [{'Key':'a','Период':START,'Индекс':1,'НомерСтроки':2,'TotalОборот':-5},{'Key':'a','Период':END,'Индекс':2,'НомерСтроки':3,'TotalОборот':8},{'Key':'b','Период':START,'Индекс':5,'НомерСтроки':6,'TotalОборот':1},{'Key':'c','Период':START,'Индекс':6,'НомерСтроки':7,'TotalОборот':2},{'Key':'c','Период':END,'Индекс':7,'НомерСтроки':8,'TotalОборот':-2}]),
    'Calendar': ([], [{'Key':'a','Период':'2026-09-21T00:00:00','TotalОборот':10},{'Key':'a','Период':START,'TotalОборот':3},{'Key':'b','Период':'2026-09-21T00:00:00','TotalОборот':-2},{'Key':'b','Период':START,'TotalОборот':1},{'Key':'c','Период':START,'TotalОборот':0}]),
    'NextPeriod': (['2026-10-01'], [{'Key':'a','Период':'2026-10-01','СледующийПериод':'2026-10-02'},{'Key':'b','Период':'2026-09-29','СледующийПериод':'2026-10-02'}]),
}


def config(method, variant='ordinary', empty=False, sql=False):
    c = check(variant, method, empty=empty, sql=sql)
    c['args'] = copy.deepcopy(CASES[method][0])
    if method in ('Days','Monthly','Auto'):
        for seed in c['storage']['initialRegisters']:
            if seed['type'].endswith('::Ledger'):
                for row in seed['rows']:
                    if row['Период']==END:row['Период']=NEXT
    return c


class VirtualArgumentsPlanTest(unittest.TestCase):
    def test_ast_types_ranges_and_independent_oracle(self):
        with TemporaryDirectory() as d:
            root = project(Path(d))
            for method in CASES:
                c = config(method); c['expected'] = {'private-oracle':-91234}
                p = plan_execution(root,analyze(root),c)
                self.assertNotIn('private-oracle',p.to_json())
                for q in p.queries:
                    for a in q['ast']['parameters']:
                        self.assertEqual(q['text'][a['start']:a['end']],a['expression'])

    def test_invalid_filter_fields_argument_counts_and_enums(self):
        c = ProjectTypes(analyze(CORPUS/'ordinary'),'Entry'); c.query_root=CORPUS/'ordinary'
        bad = ['ВЫБРАТЬ Key ИЗ Data::Ledger.Остатки(, Total > 0)',
               'ВЫБРАТЬ Key ИЗ Data::Ledger.Остатки(, СУММА(Key) == 1)',
               'ВЫБРАТЬ Key ИЗ Data::Ledger.Остатки(, Key == 1)',
               'ВЫБРАТЬ Key ИЗ Data::Ledger.Остатки(, Key == "a", Истина)',
               'ВЫБРАТЬ Период ИЗ Data::Ledger.Обороты(,, ПериодичностьИтоговРегистраНакопления.Неверно)',
               'ВЫБРАТЬ Период ИЗ Data::Ledger.Обороты(,, Другая.Месяц)',
               'ВЫБРАТЬ Период ИЗ Data::Ledger.Обороты(,, %P)']
        for text in bad:
            with self.subTest(text=text), self.assertRaises(InputError): parse_storage_query(text,c)

    def test_saved_missing_wrong_declarations_and_cycles(self):
        with TemporaryDirectory() as d:
            root=project(Path(d)); meta=root/'Data/Totals.yaml'
            for declaration in ([{'Имя':'Other','Тип':'Строка'}],[{'Имя':'Code','Тип':'Число'}]):
                value=safe_load(meta.read_text());value['Параметры']=declaration
                meta.write_text(safe_dump(value,allow_unicode=True))
                with self.assertRaises(InputError): plan_execution(root,analyze(root),config('BoundSaved'))


@unittest.skipUnless(os.getenv('ELEMENT_TEST_DOCKER_TESTS')=='1','Script opt-in')
class VirtualArgumentsDockerTest(unittest.TestCase):
    def assess(self, root, c, temp, result, label, ordered=False):
        r=run_pure(root,analyze(root),c,temp);write_json(OUT/(label+'.json'),r)
        self.assertEqual(r['status'],'EXECUTED',(label,r.get('message'),r.get('stderr')))
        status=grade(r['actual'],expected(c,result),temp,ordered=ordered)
        write_json(OUT/('assessment-'+label+'.json'),{'status':status,'engine':'SBSL','sourceHash':analyze(root)['sourceHash']})
        self.assertEqual(status,'PASS',(label,r['actual']))
        return r

    def test_portable_arguments_filters_periods_and_saved_bindings(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for variant in ('ordinary','renamed'):
                root=project(temp,variant)
                for method,(_,result) in CASES.items():
                    with self.subTest(variant=variant,method=method):
                        self.assess(root,config(method,variant),temp,result,variant+'-'+method,ordered=True)

    def test_empty_histories_and_reversed_interval(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp)
            for method in ('Filter','Days','Monthly','BoundSaved'):
                self.assess(root,config(method,empty=True),temp,[],'empty-'+method)
            c=config('Monthly');c['args']=[END,START]
            self.assess(root,c,temp,[],'reversed')

    def test_calendar_leap_day_boundaries_and_all_periods(self):
        buckets={'Год':'2028-01-01T00:00:00','Полугодие':'2028-01-01T00:00:00',
                 'Квартал':'2028-01-01T00:00:00','Месяц':'2028-02-01T00:00:00',
                 'Декада':'2028-02-21T00:00:00','Неделя':'2028-02-28T00:00:00',
                 'День':'2028-02-29T00:00:00','Час':'2028-02-29T23:00:00',
                 'Минута':'2028-02-29T23:59:00','Секунда':'2028-02-29T23:59:59'}
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);p=root/'Entry/Main.xbsl'
            entries=[f'"{name}": Запрос{{ВЫБРАТЬ Период, TotalОборот ИЗ Data::Ledger.Обороты(,, ПериодичностьИтоговРегистраНакопления.{name})}}.Выполнить()' for name in buckets]
            p.write_text(p.read_text()+'\nметод Leap(): Объект\n    возврат {'+', '.join(entries)+'}\n;\n')
            c=check(method='Leap');rows=c['storage']['initialRegisters'][-2]['rows'];row=copy.deepcopy(rows[0]);row.update(Период='2028-02-29T23:59:59',Total=7)
            rows[:]=[row]
            answer={name:[{'Период':period,'TotalОборот':7}] for name,period in buckets.items()}
            self.assess(root,c,temp,answer,'leap-calendar')

    def test_combined_boundaries_records_only_and_hidden_filter_dimension(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);p=root/'Entry/Main.xbsl'
            p.write_text(p.read_text()+'''
метод Boundary(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ Период, TotalНачальныйОстаток, TotalОборот, TotalКонечныйОстаток ИЗ Data::Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Месяц,, Key == "a") УПОРЯДОЧИТЬ ПО Период}.Выполнить()
;
метод RecordsOnly(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ Период, TotalОборот ИЗ Data::Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Месяц, СпособДополненияПериодовРегистраНакопления.Записи, Key == "a")}.Выполнить()
;
метод Hidden(T: ДатаВремя, K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ TotalОстаток ИЗ Data::Ledger.Остатки(%T, Key == %K)}.Выполнить()
;
''')
            c=check(method='Boundary');c['args']=['2026-10-15T00:00:00','2026-12-15T00:00:00']
            answer=[{'Период':'2026-10-01T00:00:00','TotalНачальныйОстаток':13,'TotalОборот':0,'TotalКонечныйОстаток':13},{'Период':'2026-12-01T00:00:00','TotalНачальныйОстаток':13,'TotalОборот':0,'TotalКонечныйОстаток':13}]
            self.assess(root,c,temp,answer,'boundary-periods',ordered=True)
            c['target']['method']='RecordsOnly';self.assess(root,c,temp,[],'records-only')
            c=check(method='Hidden');c['args']=[END,'a'];self.assess(root,c,temp,[{'TotalОстаток':5}],'hidden-filter')

    def test_moment_history_and_next_period_maximum(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);meta=root/'Data/Book.yaml';value=safe_load(meta.read_text());value['Периодичность']='Момент';meta.write_text(safe_dump(value,allow_unicode=True))
            p=root/'Entry/Main.xbsl';p.write_text(p.read_text()+'''
метод Moments(T: Момент): Объект
    возврат Запрос{ВЫБРАТЬ Key, Период, СледующийПериод ИЗ Data::Book.СрезПоследних(%T) УПОРЯДОЧИТЬ ПО Key}.Выполнить()
;
''')
            c=check(method='Moments');c['args']=['2026-10-03T00:00:00Z']
            for seed in c['storage']['initialRegisters']:
                if seed['type']=='Data::Book':
                    for row in seed['rows']:row['Период']+='T00:00:00Z'
            # Independently probed Script 10.0.2-1 constant (HistoryLimits.sbsl).
            # Moment has a different maximum from Date/DateTime in this runtime.
            answer=[{'Key':'a','Период':'2026-10-02T00:00:00Z','СледующийПериод':'4000-01-01T17:59:59.999Z'},{'Key':'b','Период':'2026-10-02T00:00:00Z','СледующийПериод':'4000-01-01T17:59:59.999Z'}]
            self.assess(root,c,temp,answer,'moment-next-maximum',ordered=True)

    def test_auto_period_intersection_and_default_period_records_only(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);p=root/'Entry/Main.xbsl'
            p.write_text(p.read_text()+'''
метод Intersection(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ ПериодМесяц, ПериодНеделя, TotalНачальныйОстаток, TotalКонечныйОстаток ИЗ Data::Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Авто,, Key == "a") УПОРЯДОЧИТЬ ПО ПериодМесяц}.Выполнить()
;
метод WholePeriod(S: ДатаВремя, E: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ TotalОборот ИЗ Data::Ledger.ОстаткиИОбороты(%S, %E, ПериодичностьИтоговРегистраНакопления.Период, СпособДополненияПериодовРегистраНакопления.Записи, Key == "a")}.Выполнить()
;
''')
            c=check(method='Intersection');c['args']=['2026-09-29T00:00:00',NEXT]
            answer=[{'ПериодМесяц':'2026-09-01T00:00:00','ПериодНеделя':'2026-09-28T00:00:00','TotalНачальныйОстаток':0,'TotalКонечныйОстаток':10},{'ПериодМесяц':START,'ПериодНеделя':'2026-09-28T00:00:00','TotalНачальныйОстаток':10,'TotalКонечныйОстаток':13}]
            self.assess(root,c,temp,answer,'auto-intersection',ordered=True)
            c['target']['method']='WholePeriod';c['args']=['2026-10-15T00:00:00','2026-12-15T00:00:00']
            self.assess(root,c,temp,[],'whole-period-records-only')
            c=check(method='Combined');c['args']=[END,START]
            self.assess(root,c,temp,[],'default-combined-reversed')

    def test_source_array_capture_and_like(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);p=root/'Entry/Main.xbsl'
            p.write_text(p.read_text()+'''
метод SourceArray(T: ДатаВремя, Keys: Массив<Строка>): Объект
    знч Q = Запрос{ВЫБРАТЬ TotalОстаток ИЗ Data::Ledger.Остатки(%T, Key В (%Keys))}
    знч Before = Q.Выполнить()
    Keys[0] = "b"
    возврат {"before": Before, "again": Q.Выполнить(), "new": Запрос{ВЫБРАТЬ TotalОстаток ИЗ Data::Ledger.Остатки(%T, Key В (%Keys))}.Выполнить()}
;
метод SourceLike(T: ДатаВремя): Объект
    возврат Запрос{ВЫБРАТЬ TotalОстаток ИЗ Data::Ledger.Остатки(%T, Key ПОДОБНО "a%")}.Выполнить()
;
''')
            c=check(method='SourceArray');c['args']=[END,['a']]
            self.assess(root,c,temp,{'before':[{'TotalОстаток':5}],'again':[{'TotalОстаток':5}],'new':[{'TotalОстаток':-1}]},'source-array-capture')
            c=check(method='SourceLike');c['args']=[END]
            self.assess(root,c,temp,[{'TotalОстаток':5}],'source-like')

    def test_mutations_are_sbsl_fail_and_alternative_pass(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);p=root/'Entry/Main.xbsl';source=p.read_text()
            for method,old,new in [('Filter','Key == %K ИЛИ Key == "c"','Key != %K'),('Days','ПериодичностьИтоговРегистраНакопления.День','ПериодичностьИтоговРегистраНакопления.Месяц')]:
                p.write_text(source.replace(old,new));c=config(method)
                r=run_pure(root,analyze(root),c,temp);write_json(OUT/('mutation-'+method+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',r)
                status=grade(r['actual'],expected(c,CASES[method][1]),temp)
                self.assertEqual(status,'FAIL');write_json(OUT/('mutation-assessment-'+method+'.json'),{'status':status,'engine':'SBSL'})
            p.write_text(source.replace('Key == %K ИЛИ Key == "c"','НЕ (Key != %K И Key != "c")'))
            self.assess(root,config('Filter'),temp,CASES['Filter'][1],'alternative')

    def test_saved_null_flags_and_undefined_remain_distinct(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);p=root/'Data/Totals.xbql'
            source=root/'Entry/Main.xbsl'
            source.write_text(source.read_text()+'''
метод SavedNull(K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ Key, Missing.ЗаменитьNull(0) КАК Value ИЗ Data::Totals(%K) ГДЕ Missing ЕСТЬ NULL}.Выполнить()
;
метод SavedUndefined(K: Строка): Объект
    возврат Запрос{ВЫБРАТЬ Key ИЗ Data::Totals(%K) ГДЕ Missing == НЕОПРЕДЕЛЕНО}.Выполнить()
;
''')
            # A typed non-NULL branch fixes the nullable result type without
            # conflating query NULL with the stored optional value.
            p.write_text('ВЫБРАТЬ Key, ВЫБОР КОГДА Key == &Code ТОГДА NULL КОГДА Key == "b" ТОГДА НЕОПРЕДЕЛЕНО ИНАЧЕ 4 КОНЕЦ КАК Missing ИЗ Item')
            c=check(method='SavedNull');c['args']=['a']
            self.assess(root,c,temp,[{'Key':'a','Value':0}],'saved-null')
            c['target']['method']='SavedUndefined'
            self.assess(root,c,temp,[{'Key':'b'}],'saved-undefined')

    def test_public_test_run_two_isolated_batches_and_unavailable_points(self):
        from element_test.batch import run_batch
        with TemporaryDirectory() as d:
            temp=Path(d);root=project(temp);assignment=temp/'assignment';assignment.mkdir()
            checks=[]
            for method in ('Filter','Days','BoundSaved'):
                c=config(method);checks.append({'id':method,'type':'runtime','points':1,'comparison':'query-rows-unordered',**c,'expected':expected(c,CASES[method][1])})
            assignment.joinpath('assignment.yaml').write_text(dump_yaml({'name':'Virtual arguments 43','checks':checks},allow_unicode=True,sort_keys=False))
            for mode in ('test','run'):
                folder=OUT/('new-public-'+mode)
                import subprocess
                p=subprocess.run([str(REPO/'bin/element-test'),mode,'--project',str(root),'--assignment',str(assignment),'--output',str(folder)],capture_output=True,text=True,timeout=900)
                write_json(OUT/(mode+'-process.json'),{'exitCode':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                result=json.loads((folder/'result.json').read_text());self.assertEqual(result['score'],3)
            bad=temp/'mutated';shutil.copytree(root,bad);p=bad/'Entry/Main.xbsl';p.write_text(p.read_text().replace('Key == %K ИЛИ Key == "c"','Key != %K'))
            neighbor=temp/'neighbor';shutil.copytree(root,neighbor)
            manifest=temp/'manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'virtual-043-completion','submissions':[{'studentId':label,'project':str(p)} for label,p in [('correct',root),('mutated',bad),('neighbor',neighbor)]]})
            saved=[]
            for n in (1,2):
                folder=OUT/('new-batch-'+str(n));suffix=0
                while folder.exists():suffix+=1;folder=OUT/f'new-batch-{n}-repeat-{suffix}'
                with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/('cache-'+str(n)))}):code=run_batch(manifest,folder,2)
                self.assertEqual(code,1);r=json.loads((folder/'batch-result.json').read_text())
                self.assertEqual([s['score'] for s in r['submissions']],[3,2,3]);self.assertFalse(any(s['cacheHit'] for s in r['submissions']))
                saved.append(str(folder.relative_to(REPO)))
            write_json(OUT/'new-batch-latest.json',saved)
            # A platform-only source stays unavailable; its point cannot be
            # credited or mistaken for an error in a student's implementation.
            control=copy.deepcopy(checks[:1]);unsupported=copy.deepcopy(checks[0]);unsupported['id']='native';unsupported['target']['method']='Unsupported';control.append(unsupported)
            assignment.joinpath('assignment.yaml').write_text(dump_yaml({'name':'Native unavailable','checks':control},allow_unicode=True,sort_keys=False))
            result,grading=run_test(root,assignment,OUT/'new-public-control')
            self.assertEqual([c['status'] for c in result['checks']],['PASS','UNSUPPORTED']);self.assertEqual(grading['unavailablePoints'],1)

    def test_three_exact_external_archive_queries_with_api_bindings(self):
        from hashlib import sha256
        from element_test.loader import open_project
        from element_test.yaml_io import load_yaml
        baseline=json.loads((REPO/'docs/query-stage-043-baseline.json').read_text())
        entries=[r for r in baseline['contracts'] if r['family']=='xbql-file']
        with TemporaryDirectory() as d:
            temp=Path(d)
            for index,entry in enumerate(entries):
                identity=entry['projectIdentity'];selector=identity['Поставщик']+'::'+identity['Имя']
                with open_project(REPO/entry['archive'],selector) as original:
                    source=original/entry['file'];self.assertEqual(sha256(source.read_bytes()).hexdigest(),entry['sourceHash'])
                    root=temp/str(index);shutil.copytree(original,root)
                text=(root/entry['file']).read_text();self.assertEqual(text,entry['text'])
                namespace=str(Path(entry['file']).parent)
                bounds=[('ДатаОстатков',END)] if index==2 else [('НачалоПериода',START),('КонецПериода',END)]
                wrapper=root/namespace/'Virtual43Probe.xbsl'
                setters=''.join('    Q.УстановитьПараметр("'+name+'", новый ДатаВремя("'+value+'"))\n' for name,value in bounds)
                wrapper.write_text('метод Execute(): Объект\n    знч Q = новый ПроизвольныйЗапрос("'+text+'")\n'+setters+'    исп R = Q.Выполнить()\n    возврат R.ВМассив()\n;\n')
                register='ОстаткиНоменклатуры' if index==2 else 'Продажи'
                metadata=load_yaml(root/namespace/(register+'.yaml'))
                dimensions={f['Имя']:copy.deepcopy(REFS[n%len(REFS)]) for n,f in enumerate(metadata['Измерения'])}
                registrar=copy.deepcopy(REFS[2])
                if index==2:registrar={'type':namespace+'::ПриходнаяНакладная.Ссылка','value':registrar}
                resources={f['Имя']:1 for f in metadata['Ресурсы']}
                times=['2026-09-30T00:00:00',START,END,'2026-10-02T00:00:00',START]
                values=[10,3,4,20,99] if index!=2 else [10,3,4,20,99]
                rows=[]
                for n,(time,value) in enumerate(zip(times,values)):
                    row={'Период':time,'Регистратор':copy.deepcopy(registrar),'Активность':n!=4,**dimensions,**resources}
                    if index==2:row.update(ВидЗаписи='Расход' if n==1 else 'Приход',Количество=value,Сумма=value*10)
                    else:row['Сумма']=value
                    rows.append(row)
                c={'target':{'namespace':namespace,'module':'Virtual43Probe','method':'Execute'},'args':[],'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','registers':[namespace+'::'+register],'initialRegisters':[{'type':namespace+'::'+register,'filter':{'Регистратор':registrar},'rows':rows}]}}
                answer=([{'Направление':dimensions['Направление'],'Услуга':dimensions['Услуга'],'Сумма':7}] if index==0 else [{'Организация':dimensions['Организация'],'СуммаОборот':7}] if index==1 else [{'Номенклатура':dimensions['Номенклатура'],'Склад':dimensions['Склад'],'КоличествоОстаток':7,'СуммаОстаток':70}])
                model=analyze(root);plan=plan_execution(root,model,c)
                self.assertEqual(plan.queries[0]['text'],text)
                r=run_pure(root,model,c,temp);label='external-'+str(index);write_json(OUT/(label+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',(label,r.get('message'),r.get('stderr')))
                status=grade({'result':r['actual']['result'],'storage':[]},{'result':answer,'storage':[]},temp)
                self.assertEqual(status,'PASS',(label,r['actual']['result']))
                self.assertEqual(len(r['actual']['storage']),1)
                self.assertEqual(r['actual']['storage'][0]['type'],namespace+'::'+register)
                self.assertCountEqual(r['actual']['storage'][0]['value'],rows)
                self.assertEqual(sha256((root/entry['file']).read_bytes()).hexdigest(),entry['sourceHash'])
                write_json(OUT/('assessment-'+label+'.json'),{'status':status,'engine':'SBSL','contractId':entry['contractId'],'sourceHash':entry['sourceHash'],'projectSourceHash':entry['projectSourceHash'],'executionProjectSourceHash':model['sourceHash'],'wrapperSourceHash':sha256(wrapper.read_bytes()).hexdigest(),'file':entry['file'],'range':entry['range'],'mode':'exact-external-query-through-api','nativeReportLifecycle':False,'storageRowsUnchanged':True,'ast':plan.queries[0]['ast'],'runtime':str((OUT/(label+'.json')).relative_to(REPO))})


@unittest.skipUnless(os.getenv('ELEMENT_TEST_INTEGRATION_TESTS')=='1','PostgreSQL opt-in')
class VirtualArgumentsSqlTest(unittest.TestCase):
    def test_filter_calendar_and_parameterized_source_sql_audit(self):
        import secrets
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            temp=Path(d);root=project(temp)
            for method in ('Filter','Monthly','BoundSaved'):
                c=config(method,sql=True)
                r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=True)
                write_json(OUT/('sql-'+method+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',r)
                self.assertTrue(r['integration']['cleanup'])
                self.assertEqual(grade(r['actual'],expected(c,CASES[method][1]),temp),'PASS')
