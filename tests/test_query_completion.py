"""Independent completion criteria for joins, projections and temporary fields."""
import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from query_completion_fixtures import *
from element_test.bridge import write_json,run_test
from element_test.execution_plan import plan_execution
from element_test.generated_types import ProjectTypes
from element_test.model import analyze
from element_test.query_plan import parse_storage_query
from element_test.runtime import run_pure
from element_test.integration import run_integration
from element_test.yaml_io import InputError
from test_query_projections import grade


class CompletionPlanTest(unittest.TestCase):
    def test_typed_new_functions_temporary_columns_and_original_captures(self):
        for variant in ('ordinary','renamed'):
            for method in ANSWERS:
                c=config(method,variant);c['expected']={'private-answer':-97814}
                p=plan_execution(CORPUS/variant,analyze(CORPUS/variant),c)
                self.assertNotIn('private-answer',p.to_json())
                for q in p.queries:
                    for a in q['ast']['parameters']:
                        self.assertEqual(q['text'][a['start']:a['end']],a['expression'])
                write_json(OUT/('plan-'+variant+'-'+method+'.json'),p.to_dict())

    def test_invalid_defaults_computed_cycles_types_qualifiers_and_indices(self):
        c=ProjectTypes(analyze(CORPUS/'ordinary'),'Entry')
        cases=[
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число(0, 2))',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Строка(2, 3))',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число ПО УМОЛЧАНИЮ "bad")',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число ПО УМОЛЧАНИЮ NULL)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число ПО УМОЛЧАНИЮ %Captured)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число ПО УМОЛЧАНИЮ B, B: Число ПО УМОЛЧАНИЮ A)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Id АвтоНомерЗаписи, A: Число ПО УМОЛЧАНИЮ Id)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Id АвтоНомерЗаписи, A ВЫЧИСЛЯЕТСЯ КАК Id * 2, B: Число ПО УМОЛЧАНИЮ A)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A ВЫЧИСЛЯЕТСЯ КАК B, B ВЫЧИСЛЯЕТСЯ КАК A)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число ПО УМОЛЧАНИЮ B, B: Число ПО УМОЛЧАНИЮ A); ВСТАВИТЬ В T (A) ЗНАЧЕНИЯ (NULL)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число, B ВЫЧИСЛЯЕТСЯ КАК A * 2); СОЗДАТЬ ИНДЕКС I ДЛЯ T (B)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A АвтоНомерЗаписи, B АвтоНомерЗаписи)',
            'СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (A: Число, B ВЫЧИСЛЯЕТСЯ КАК A * 2); ИЗМЕНИТЬ T УСТАНОВИТЬ B = 3',
            'ВЫБРАТЬ ПервыйНеNull(1, "bad") КАК V ИЗ Data::Item',
            'ВЫБРАТЬ ПервыйНеNull(1) КАК V ИЗ Data::Item',
            'ВЫБРАТЬ Ууид(1) КАК V ИЗ Data::Item',
            'ВЫБРАТЬ Label.Год КАК V ИЗ Data::Item',
        ]
        for text in cases:
            with self.subTest(text=text),self.assertRaises(InputError):parse_storage_query(text,c)

    def test_read_only_file_snapshot_does_not_claim_object_or_file_operations(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'ordinary',root)
            p=root/'Data/Item.yaml';meta=safe_load(p.read_text());meta['Реквизиты'].append({'Имя':'Файлы'})
            p.write_text(safe_dump(meta,allow_unicode=True,sort_keys=False))
            c=config('DateParts');plan=plan_execution(root,analyze(root),c)
            self.assertTrue(plan.query_storage_elements)
            # Actual object access retains its complete-schema requirement.
            contracts=ProjectTypes(analyze(root),'Entry')
            with self.assertRaises(InputError):contracts.require('Data::Item.Объект')
            with self.assertRaises(InputError):parse_storage_query('ВЫБРАТЬ Файлы ИЗ Data::Item',contracts)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Script opt-in')
class CompletionDockerTest(unittest.TestCase):
    def assess(self,root,c,temp,answer,label,ordered=True):
        r=run_pure(root,analyze(root),c,temp)
        runtime=OUT/(label+'.json');write_json(runtime,r)
        self.assertEqual(r['status'],'EXECUTED',(label,r.get('reason'),r.get('stderr'),r.get('diagnostics')))
        status=grade(r['actual'],expected(c,answer),temp,ordered=ordered)
        self.assertEqual(status,'PASS',(label,r['actual']))
        write_json(OUT/('assessment-'+label+'.json'),{'status':status,'engine':'SBSL','criterionId':c['target']['method'],
            'sourceHash':sha256(next((root/'Entry').glob('*.xbsl')).read_bytes()).hexdigest(),'projectSourceHash':analyze(root)['sourceHash'],
            'runtime':str(runtime.relative_to(REPO)),'expected':expected(c,answer)})
        return r

    def test_scalar_functions_null_undefined_lazy_branches_regex_dates_uuid(self):
        with TemporaryDirectory() as d:
            for variant in ('ordinary','renamed'):
                for method in ('First','AllNull','OptionalValue','Match','DateParts','Uuid','Math'):
                    self.assess(CORPUS/variant,config(method,variant),Path(d),ANSWERS[method],variant+'-'+method)

    def test_temporary_defaults_qualifiers_computed_auto_null_and_repeat_lifetime(self):
        with TemporaryDirectory() as d:
            for variant in ('ordinary','renamed'):
                for method in ('TempFields','TempSelect','TempCapture','TempLife','NullDefaults'):
                    self.assess(CORPUS/variant,config(method,variant),Path(d),ANSWERS[method],variant+'-'+method)

    def test_six_exact_join_null_templates_with_independent_bag_answers(self):
        with TemporaryDirectory() as d:
            for variant in ('ordinary','renamed'):
                for label,answer in JOIN_ANSWERS.items():
                    method='join_'+label.replace('-','_')
                    self.assess(CORPUS/variant,config(method,variant),Path(d),answer,variant+'-'+method,ordered=False)

    def test_invalid_runtime_regex_overflow_and_empty_sources(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=CORPUS/'ordinary'
            c=config('Match');c['args']=['a*']
            r=run_pure(root,analyze(root),c,temp);write_json(OUT/'regex-invalid.json',r);self.assertEqual(r['status'],'ERROR',r)
            for method,answer in [('First',[]),('Match',[]),('TempSelect',[]),('DateParts',[])]:
                self.assess(root,config(method,empty=True),temp,answer,'empty-'+method)
            altered=temp/'source';shutil.copytree(root,altered);p=altered/'Entry/Main.xbsl'
            p.write_text(p.read_text().replace('2.34','9999'))
            r=run_pure(altered,analyze(altered),config('TempFields'),temp);write_json(OUT/'temporary-overflow.json',r);self.assertEqual(r['status'],'ERROR',r)

    def test_math_sql_null_negative_power_and_pattern_subset(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'ordinary',root)
            p=root/'Entry/Main.xbsl'
            p.write_text(p.read_text()+'''метод BoundaryMath(): Объект
    возврат Запрос{ВЫБРАТЬ Sin(NULL) КАК Missing, ПервыйНеNull(Sin(NULL), 7) КАК Fallback, (-2) ** -10 КАК Tiny, 2 ** 3 ** 2 КАК Power ИЗ Other::Item}.Выполнить()
;
метод BoundaryDefault(): Объект
    возврат Запрос{СОЗДАТЬ ВРЕМЕННУЮ ТАБЛИЦУ T (Key: Число, Value: Число? ПО УМОЛЧАНИЮ NULL, Optional: Строка? ПО УМОЛЧАНИЮ Неопределено); ВСТАВИТЬ В T (Key) ЗНАЧЕНИЯ (1); ВЫБРАТЬ Value ЕСТЬ NULL КАК Missing, Optional ЕСТЬ NULL КАК UndefinedIsNull ИЗ T}.Выполнить()
;
метод BoundaryDate(): Объект
    возврат Запрос{ВЫБРАТЬ D.Day.Год КАК Year, D.Day.Месяц КАК Month, D.Day.День КАК Day ИЗ Data::Item КАК D}.Выполнить()
;
''')
            self.assess(root,config('BoundaryMath'),temp,[{'Missing':None,'Fallback':7,'Tiny':0.000977,'Power':512}],'boundary-math')
            self.assess(root,config('BoundaryDefault'),temp,[{'Missing':True,'UndefinedIsNull':False}],'boundary-default')
            self.assess(root,config('BoundaryDate'),temp,[{'Year':2024,'Month':2,'Day':28}]*4,'boundary-date')
            for pattern,flags in [('a.*',[True,False,False,False]),('[^ab]',[False,False,True,True]),('.',[True]*4),('',[False]*4)]:
                c=config('Match');c['args']=[pattern]
                answer=[{'Label':label,'Match':flag} for label,flag in zip('abcd',flags)]
                self.assess(root,c,temp,answer,'pattern-'+str(len(pattern))+'-'+str(flags.count(True)))

    def test_mutations_independently_fail_and_equivalent_alternative_pass(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'ordinary',root);p=root/'Entry/Main.xbsl';source=p.read_text()
            for method,old,new in [('First','B.Amount, 9','B.Amount, 99'),('Match','Label.ПолноеСовпадение(%Pattern)','Label.ПолноеСовпадение(".*")'),('DateParts','КОЛИЧЕСТВО(*)','КОЛИЧЕСТВО(РАЗЛИЧНЫЕ Key)'),('TempFields','Price: Число(3, 2) ПО УМОЛЧАНИЮ 2.555','Price: Число(3, 2) ПО УМОЛЧАНИЮ 1'),('TempSelect','Qty * Price','Qty + Price'),('join_full_join','ПОЛНОЕ СОЕДИНЕНИЕ','ЛЕВОЕ СОЕДИНЕНИЕ')]:
                self.assertIn(old,source);p.write_text(source.replace(old,new));c=config(method)
                r=run_pure(root,analyze(root),c,temp);write_json(OUT/('mutation-'+method+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',r)
                answer=JOIN_ANSWERS['full-join'] if method.startswith('join_') else ANSWERS[method]
                status=grade(r['actual'],expected(c,answer),temp,ordered=not method.startswith('join_'))
                self.assertEqual(status,'FAIL',method);write_json(OUT/('assessment-mutation-'+method+'.json'),{'engine':'SBSL','status':status})
            p.write_text(source.replace('Qty * Price','Price * Qty'))
            self.assess(root,config('TempFields'),temp,ANSWERS['TempFields'],'alternative')

    def test_exact_real_sales_query_duplicates_null_owner_dates_and_empty(self):
        from element_test.loader import open_project
        entry=next(r for r in json.loads((REPO/'docs/query-stage-041-baseline.json').read_text())['contracts'] if r['contractId']=='query-d565e4520d6ae9fa6c758141')
        with TemporaryDirectory() as d:
            temp=Path(d)
            with open_project(REPO/entry['archive']) as original:
                self.assertEqual(analyze(original)['sourceHash'],entry['projectSourceHash'])
                root=temp/'real';shutil.copytree(original,root)
            source=root/entry['file'];self.assertEqual(sha256(source.read_bytes()).hexdigest(),entry['sourceHash'])
            text=source.read_text();self.assertEqual(text,entry['text'])
            wrapper=root/'Пресейл/Completion41Probe.xbsl'
            wrapper.write_text('метод Execute(): Объект\n    возврат Запрос{'+text+'}.Выполнить()\n;\n')
            rows=[]
            for i,(who,day,total) in enumerate([(REFS[0],'2026-10-01T12:00:00',2),(REFS[0],'2026-10-01T12:00:00',3),(REFS[1],'2026-10-01T12:00:00',7),(None,'2026-10-01T12:00:00',11),(REFS[0],'2026-10-02T12:00:00',-2)]):
                value={'Ссылка':REFS[i],'Дата':day,'Номер':'T'+str(i),'Наименование':'authored',
                       'Ответственный':who,'ДатаЗакрытия':day,'Направление':None,'Клиент':None,'КонтактноеЛицо':None,'Стадия':None,'ВалютаСделки':None,'СуммаВВалютеСделки':total,'СуммаВВалютеУчета':total,'Услуги':[]}
                rows.append({'type':'Пресейл::Сделки','value':value})
            answer=[{'Дата':'2026-10-01T12:00:00','Ответственный':REFS[0],'Сумма':5},{'Дата':'2026-10-01T12:00:00','Ответственный':REFS[1],'Сумма':7},{'Дата':'2026-10-01T12:00:00','Ответственный':None,'Сумма':11},{'Дата':'2026-10-02T12:00:00','Ответственный':REFS[0],'Сумма':-2}]
            for empty in (False,True):
                c={'target':{'namespace':'Пресейл','module':'Completion41Probe','method':'Execute'},'runtimeProfile':'9.3','timeout':'15s','trace':True,'storage':{'idType':'Ууид','initial':[] if empty else rows}}
                model=analyze(root);plan=plan_execution(root,model,c);self.assertEqual(plan.queries[0]['text'],text)
                r=run_pure(root,model,c,temp);label='external-sales-empty' if empty else 'external-sales'
                write_json(OUT/(label+'.json'),r);self.assertEqual(r['status'],'EXECUTED',r)
                status=grade(r['actual'],expected(c,[] if empty else answer),temp)
                self.assertEqual(status,'PASS',r)
                self.assertEqual(sha256(source.read_bytes()).hexdigest(),entry['sourceHash'])
                write_json(OUT/('assessment-'+label+'.json'),{'status':status,'engine':'SBSL','contractId':entry['contractId'],
                    **{key:entry[key] for key in ('sourceHash','projectSourceHash','file','range')},'ast':plan.queries[0]['ast'],
                    'executionProjectSourceHash':model['sourceHash'],'wrapperSourceHash':sha256(wrapper.read_bytes()).hexdigest(),
                    'nativeReportLifecycle':False,'mode':'exact-external-query-through-literal','runtime':str((OUT/(label+'.json')).relative_to(REPO)),'expected':expected(c,[] if empty else answer)})
            if os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1':
                import secrets
                c['storage']['initial']=rows;c['storage']['backend']='postgres'
                c['integration']={'backend':'postgres','operation':'metadata-storage'}
                with patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
                    r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=True)
                write_json(OUT/'sql-external-sales.json',r)
                self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
                self.assertNotIn('source:commit',r['storageTrace'])
                self.assertEqual(grade(r['actual'],expected(c,answer),temp),'PASS',r)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','PostgreSQL opt-in')
class CompletionSqlTest(unittest.TestCase):
    def test_selected_storage_queries_independent_sql_audit_and_cleanup(self):
        import secrets
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            for variant,method in [('ordinary','First'),('renamed','TempSelect'),('ordinary','join_full_join')]:
                root=CORPUS/variant;c=config(method,variant,sql=True)
                r=run_integration(c,analyze(root),Path(d),root=root,enabled=True,inject_failure=True)
                write_json(OUT/('sql-'+variant+'-'+method+'.json'),r)
                self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup']);self.assertNotIn('source:commit',r['storageTrace'])
                answer=JOIN_ANSWERS['full-join'] if method.startswith('join_') else ANSWERS[method]
                self.assertEqual(grade(r['actual'],expected(c,answer),Path(d),ordered=not method.startswith('join_')),'PASS',r)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Public Script opt-in')
class CompletionPublicTest(unittest.TestCase):
    def test_public_new_criteria_test_run_and_unavailable_points(self):
        import subprocess
        from composite_fixtures import dump_yaml
        assignment=OUT/'new-assignment';assignment.mkdir(exist_ok=True)
        checks=[]
        for method in ANSWERS:
            c=config(method)
            checks.append({'id':method,'type':'runtime','points':1,'comparison':'record-sets-unordered',**c,'expected':expected(c,ANSWERS[method])})
        for label,answer in JOIN_ANSWERS.items():
            method='join_'+label.replace('-','_');c=config(method)
            checks.append({'id':method,'type':'runtime','points':1,'comparison':'query-rows-unordered',**c,'expected':expected(c,answer)})
        (assignment/'assignment.yaml').write_text(dump_yaml({'name':'Дополнение №40–42','checks':checks},allow_unicode=True,sort_keys=False))
        for mode in ('test','run'):
            folder=OUT/('public-new-'+mode)
            cmd=[str(REPO/'bin/element-test'),mode,'--project',str(CORPUS/'ordinary'),'--assignment',str(assignment),'--output',str(folder)]
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
            (OUT/('public-new-'+mode+'.stdout')).write_text(p.stdout);(OUT/('public-new-'+mode+'.stderr')).write_text(p.stderr)
            self.assertEqual(p.returncode,0,p.stderr)
            r=json.loads((folder/'result.json').read_text());self.assertEqual(r['score'],len(checks));self.assertTrue(all(c['status']=='PASS' for c in r['checks']))
        control=OUT/'control-assignment';control.mkdir(exist_ok=True)
        c=config('First');wrong={'id':'wrong','type':'runtime','points':1,**c,'expected':expected(c,[])}
        unavailable={'id':'unsupported','type':'runtime','points':1,**config('Unsupported'),'expected':None}
        correct={'id':'restored','type':'runtime','points':1,**c,'expected':expected(c,ANSWERS['First'])}
        (control/'assignment.yaml').write_text(dump_yaml({'checks':[wrong,unavailable,correct]},allow_unicode=True,sort_keys=False))
        r,package=run_test(CORPUS/'ordinary',control,OUT/'public-control')
        self.assertEqual([c['status'] for c in r['checks']],['FAIL','UNSUPPORTED','PASS']);self.assertEqual(package['unavailablePoints'],1)

    def test_two_fresh_batches_new_semantics_no_state_leak(self):
        from element_test.batch import run_batch
        from composite_fixtures import dump_yaml
        with TemporaryDirectory() as d:
            temp=Path(d);assignment=OUT/'batch-assignment';assignment.mkdir(exist_ok=True)
            checks=[]
            for method in ('First','Match','TempFields'):
                c=config(method);checks.append({'id':method,'type':'runtime','points':1,**c,'expected':expected(c,ANSWERS[method])})
            (assignment/'assignment.yaml').write_text(dump_yaml({'checks':checks},allow_unicode=True,sort_keys=False))
            submissions=[]
            for label in ('correct','mutated','neighbor'):
                root=OUT/'batch-inputs'/label;shutil.copytree(CORPUS/'ordinary',root,dirs_exist_ok=True)
                if label=='mutated':
                    p=root/'Entry/Main.xbsl';p.write_text(p.read_text().replace('B.Amount, 9','B.Amount, 99').replace('Label.ПолноеСовпадение(%Pattern)','Label.ПолноеСовпадение(".*")').replace('Price: Число(3, 2) ПО УМОЛЧАНИЮ 2.555','Price: Число(3, 2) ПО УМОЛЧАНИЮ 1'))
                submissions.append({'studentId':label,'project':str(root)})
            manifest=OUT/'batch-manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'completion-040-042','submissions':submissions})
            folders=[]
            for n in (1,2):
                folder=OUT/('batch-'+str(n));suffix=0
                while folder.exists():suffix+=1;folder=OUT/('batch-'+str(n)+'-repeat-'+str(suffix))
                with patch.dict(os.environ,{'ELEMENT_TEST_CACHE_DIR':str(temp/('cache-'+str(n)))}):code=run_batch(manifest,folder,2)
                self.assertEqual(code,1)
                r=json.loads((folder/'batch-result.json').read_text());self.assertEqual([s['score'] for s in r['submissions']],[3,0,3]);self.assertFalse(any(s['cacheHit'] for s in r['submissions']))
                folders.append(str(folder.relative_to(REPO)))
            write_json(OUT/'batch-latest.json',folders)
