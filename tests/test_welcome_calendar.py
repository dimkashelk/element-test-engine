"""Stage 35 contracts, original archive assessment, portability and mutations."""
import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import yaml
from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine, prepare_script, run_pure
from element_test.yaml_io import InputError
from welcome_fixtures import *


def setUpModule():
    EVIDENCE.mkdir(parents=True, exist_ok=True)


def grade(model, checks, output):
    write_json(output/'model.json',model)
    write_json(output/'assignment.json',{'name':'Independent welcome assessment','checks':checks})
    return execute_engine('test',output/'model.json',output/'assignment.json',output)


class WelcomePlanTest(unittest.TestCase):
    def test_all_eight_roots_and_no_expected_in_plan(self):
        roots=set()
        with open_project(ARCHIVE) as root:
            model=analyze(root)
            from element_test.coverage import inventory
            mapped=inventory(root,model)
            welcome=next(f for f in mapped['files'] if f['sourceFile']=='Администрирование/ФормаПриветствие.yaml')
            self.assertEqual([h['status'] for h in welcome['formChecks']['handlers']],['MATCH'])
            for check in load_assignment(ASSIGNMENT)['checks']:
                c={**check,'expected':{'SECRET_TEACHER_ANSWER':1739}}
                p=plan_execution(root,model,c)
                self.assertNotIn('SECRET_TEACHER_ANSWER',p.to_json())
                roots.add((p.entry.identity.owner,p.entry.identity.declaration))
                if p.form:
                    self.assertIsNone(p.form['objectType'])
                    self.assertNotIn('Объект',[f['Имя'] for f in p.contracts.fields[p.form['canonical']]])
                    self.assertTrue(any(h.get('status')=='SIGNATURE_CHECKED_NO_UI_EVENT' for h in p.form['handlers']))
                for symbol in p.symbols:
                    if 'ДоступноСКлиента' in symbol.annotations:
                        self.assertIn('ДоступноСКлиента',[r['name'] for r in symbol.annotation_ranges])
        self.assertEqual(len(roots),8)

    def test_constants_fixture_types_defaults_and_alias_duplicates(self):
        root=CORPUS/'options';model=analyze(root)
        base=load_assignment(REPO/'assignments/welcome-options')['checks'][0]
        for fixture in [[], {'Missing':{}}, {'Alpha::Options':{'Missing':'x'}},
                        {'Alpha::Options':{'Enabled':'yes'}},{'Alpha::Options':{'Text':12}},
                        {'Alpha::Options':{},'Options':{}}]:
            with self.subTest(fixture=fixture),self.assertRaises(InputError):
                plan_execution(root,model,{**base,'constants':fixture})
        defaults=plan_execution(root,model,{**base,'constants':{}}).constants
        self.assertEqual(defaults['Alpha::Options']['values'],{'Text':'default-Alpha','Enabled':False})
        self.assertEqual(defaults['Beta::Options']['values'],{'Text':'default-Beta','Enabled':False})

    def test_plain_form_context_events_and_clock_validation(self):
        root=CORPUS/'options';model=analyze(root);base=load_assignment(REPO/'assignments/welcome-options')['checks'][0]
        changes=[{'lifecycle':{'isNew':True}},{'context':{'Объект':{}}},
                 {'context':{'Компоненты':{'DateField':{'Значение':'2028-02-30'}}}},
                 {'context':{'Компоненты':{'Summary':{'Значение':5}}}},
                 {'observe':['Компоненты.Missing.Значение']}, {'observe':['Компоненты.DateField.Wrong']},
                 {'clock':{'mode':'fixed','date':'bad','time':'12:00:00','timezone':'UTC'}},
                 {'clock':{'mode':'fixed','date':'2028-02-29','time':'25:00','timezone':'UTC'}},
                 {'clock':{'mode':'docker','date':'2028-02-29','timezone':'UTC'}},
                 {'clock':{'mode':'docker','timezone':'Host'}},{'executorLocale':'auto'},
                 {'snapshotArgs':1},{'captureExceptionTypes':['Исключение']},
                 {'sequence':[{'method':'Apply','args':[{'invalid':1},{}]}]}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(InputError),TemporaryDirectory() as d:
                prepare_script(root,model,{**base,**change},Path(d))

    def test_source_errors_are_preparation_refusals(self):
        base=load_assignment(REPO/'assignments/welcome-options')['checks'][0]
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'options',root)
            p=root/'Alpha/CalendarView.xbsl';source=p.read_text()
            for mutation in [source.replace('Source: Кнопка','Source: Строка'),
                             source.replace('Компоненты.Summary','Компоненты.Missing'),
                             source.replace('знч Title = "local"','Source.Размер()'),
                             source.replace('знч Title = "local"','Event.ПолучитьТип()'),
                             source.replace('Дата.Сейчас()','Дата.Сейчас(ЧасовойПояс{UTC})')]:
                p.write_text(mutation)
                with self.assertRaises(InputError):plan_execution(root,analyze(root),base)
            p.write_text(source)
            p=root/'Alpha/Options.yaml';source=p.read_text()
            p.write_text(source.replace('Имя: Enabled','Имя: Text'))
            with self.assertRaisesRegex(InputError,'повторная константа'):plan_execution(root,analyze(root),base)

    def test_unknown_annotation_and_constant_write_refused_then_successor(self):
        base=load_assignment(REPO/'assignments/welcome-options')['checks'][0]
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'options',root)
            p=root/'Alpha/Options.xbsl';source=p.read_text()
            p.write_text(source.replace('@ДоступноСКлиента','@UnknownAnnotation'))
            with self.assertRaisesRegex(InputError,'UnknownAnnotation'):plan_execution(root,analyze(root),base)
            p.write_text(source.replace('Options.Получить()','Options.Записать()'))
            with self.assertRaisesRegex(InputError,'Эффект набора констант'):plan_execution(root,analyze(root),base)
            p.write_text(source)
            self.assertTrue(plan_execution(root,analyze(root),base).constants)

    def test_project_get_now_and_parameter_shadow_are_not_system_bindings(self):
        root0=CORPUS/'options';base=load_assignment(REPO/'assignments/welcome-options')['checks'][0]
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(root0,root)
            (root/'Alpha/Options.xbsl').write_text('''@ВПроекте
метод Получить(): Соответствие<Строка, Строка>
    возврат <Строка, Строка>{"own": "project"}
;
''')
            p=root/'Alpha/CalendarView.xbsl';p.write_text('''метод Start(): Соответствие<Строка, Строка>
    возврат Options.Получить()
;
''')
            c={'target':base['target'],'args':[],'clock':author.FIXED,'constants':{}}
            plan=plan_execution(root,analyze(root),c)
            self.assertFalse(plan.constants)
            self.assertTrue(any(b.category=='project' and b.operation=='Получить' for b in plan.bindings))
            (root/'Alpha/Дата.xbsl').write_text('метод Сейчас(): Строка\n    возврат "project"\n;\n')
            p.write_text('метод Start(): Строка\n    возврат Дата.Сейчас()\n;\n')
            plan=plan_execution(root,analyze(root),c)
            self.assertFalse(any(b.category=='clock' for b in plan.bindings))
            p.write_text('метод Start(Время: Соответствие<Строка, Строка>): Строка\n    возврат Время.Сейчас()\n;\n')
            plan=plan_execution(root,analyze(root),{**c,'args':[{}]})
            self.assertFalse(any(b.category=='clock' for b in plan.bindings))

    def test_renamed_projects_have_no_special_form_renderer(self):
        for name in ['options','preferences']:
            root=CORPUS/name;model=analyze(root)
            for c in load_assignment(REPO/'assignments'/('welcome-'+name))['checks']:
                p=plan_execution(root,model,c)
                with TemporaryDirectory() as d:
                    prepare_script(root,model,c,Path(d))
                    source='\n'.join(f.read_text() for f in Path(d).glob('*.sbsl'))
                    self.assertNotIn('ФормаПриветствие',source)
                    if p.form:self.assertIn('знч Title = "local"' if name=='options' else 'знч Caption = "local"',source)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Welcome Docker opt-in')
class WelcomeDockerTest(unittest.TestCase):
    def test_native_calendar_dictionary_contract_and_locales_in_docker(self):
        spec=__import__('importlib.util',fromlist=['spec_from_file_location']).spec_from_file_location('native35',CORPUS/'run_native_probes.py')
        native=__import__('importlib.util',fromlist=['module_from_spec']).module_from_spec(spec);spec.loader.exec_module(native)
        for locale in ['ru-RU','en-US']:
            actual=native.run(locale,EVIDENCE/'native-probes')
            self.assertEqual([actual[k] for k in ['month','year','leap','afterLeap']],['2026-10-01','2027-01-01','2028-02-29','2028-03-01'])
            self.assertEqual(actual['missingGet'],'Std::IllegalArgumentException')
            self.assertEqual(actual['missingIndex'],'Std::IllegalArgumentException');self.assertIsNone(actual['optional'])
            self.assertEqual(actual['dictionary'],{'present':True})
            self.assertEqual(actual['defaults'],[False,'','0001-01-01'])
            self.assertEqual(actual['day'],'Пятница' if locale=='ru-RU' else 'Friday')
            self.assertEqual(actual['time'],'01:02:03' if locale=='ru-RU' else '1:02:03 AM')

    def test_original_archive_public_test_run_all_seventy_criteria(self):
        before=ARCHIVE.read_bytes();results=[]
        for command in ['test','run']:
            output=EVIDENCE/command
            if output.exists():shutil.rmtree(output)
            p=subprocess.run([str(REPO/'bin/element-test'),command,'--project',str(ARCHIVE),'--assignment',str(ASSIGNMENT),'--output',str(output)],capture_output=True,text=True,timeout=900)
            (EVIDENCE/('public-'+command+'-verification.log')).write_text(p.stdout+p.stderr)
            self.assertEqual(p.returncode,0,p.stderr+p.stdout)
            r=json.loads((output/'result.json').read_text())
            self.assertEqual([c['status'] for c in r['checks']],['PASS']*70)
            self.assertEqual((r['score'],r['maxScore'],r['unavailablePoints']),(70,70,0))
            results.append(r)
        # The live Docker clock deliberately changes. All static results and scores match.
        for a,b in zip(results[0]['checks'],results[1]['checks']):
            if a['id']!='welcome-create-docker-clock':self.assertEqual(a,b)
            else:self.assertEqual((a['status'],a['score']),(b['status'],b['score']))
        self.assertEqual(ARCHIVE.read_bytes(),before)
        write_json(EVIDENCE/'public-equivalence.json',{'criteria':70,'deterministicObservationsEqual':69,'liveClockIndependentlyAssessed':True,'scoresEqual':True})
        facts=json.loads((EVIDENCE/'test/runtime-evidence.json').read_text())
        live=next(f for f in facts if f['criterionId']=='welcome-create-docker-clock')
        self.assertEqual(live['clock']['timezone'],'Europe/Moscow')
        traced={e['symbol'].split('::')[-1] for f in facts for e in f.get('trace',[])}
        self.assertTrue({'ПолучитьДанные','РассчитатьДни','ПолучитьТекстДня','СформироватьТекстОрганизации'} <= traced)
        reverse=next(f for f in facts if f['criterionId']=='welcome-button-reverse')
        self.assertFalse(any(e['symbol'].endswith('::РассчитатьДни') for e in reverse.get('trace',[])))

    def test_portable_aliases_shadowing_and_isolated_sequence(self):
        for name in ['options','preferences']:
            root=CORPUS/name;model=analyze(root);checks=load_assignment(REPO/'assignments'/('welcome-'+name))['checks']
            with TemporaryDirectory() as d:
                output=Path(d)
                for c in checks:
                    c['execution']=run_pure(root,model,c,output)
                    self.assertEqual(c['execution']['status'],'EXECUTED',c['execution'])
                result=grade(model,checks,output)
                self.assertEqual([c['status'] for c in result['checks']],['PASS']*3,result)
                write_json(EVIDENCE/('portable-'+name+'.json'),result)

    def test_independent_source_mutations_including_helpers_and_counters(self):
        form='Администрирование/ФормаПриветствие.xbsl'
        cases=[
          ('constant-swap','Администрирование/ДанныеКомпании.xbsl','Данные.Телефон','Данные.ЮрАдрес',['company-full','create-060000-True']),
          ('constant-loss','Администрирование/РабочиеДни.xbsl','Результат.Вставить("Пятница", Данные.Пятница)','Результат.Вставить("Missing", Данные.Пятница)',['days-week']),
          ('boundary-06',form,'ВремяСейчас < Время06','ВремяСейчас <= Время06',['create-060000-True']),
          ('boundary-12',form,'ВремяСейчас < Время12','ВремяСейчас <= Время12',['create-120000-True']),
          ('boundary-18',form,'ВремяСейчас < Время18','ВремяСейчас <= Время18',['create-180000-True']),
          ('org',form,'Вы работаете в','Ошибка организации',['org-full','create-060000-True']),
          ('day-text',form,'— рабочий день','— ошибка дня',['day-Пятница-True','create-060000-True']),
          ('exclude-end',form,'ТекущаяДата <= ДатаКонец','ТекущаяДата < ДатаКонец',['calculate-weekend','button-weekend']),
          ('skip-weekend',form,'Нерабочих += 1','Нерабочих += 0',['calculate-weekend','button-weekend']),
          ('counter',form,'Рабочих += 1','Рабочих = 1',['calculate-weekend','button-weekend']),
          ('date-corruption',form,'знч ДатаКонец = Компоненты.ДатаКонца.Значение','знч ДатаКонец = Компоненты.ДатаКонца.Значение\n    Компоненты.ДатаКонца.Значение = Дата{2000-01-01}',['button-weekend']),
          ('reverse-guard',form,'если ДатаНачало > ДатаКонец','если Ложь',['button-reverse']),
          ('dictionary-mutation',form,'знч ПолноеНаименование = Данные.Получить','Данные.Вставить("Extra", "changed")\n    знч ПолноеНаименование = Данные.Получить',['org-full'])]
        assignment=load_assignment(ASSIGNMENT);collected=[]
        with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
            temp=Path(d)
            for tag,path,old,new,ids in cases:
                root=temp/tag;shutil.copytree(original,root);p=root/path
                self.assertIn(old,p.read_text());p.write_text(p.read_text().replace(old,new))
                model=analyze(root);checks=[copy.deepcopy(c) for c in assignment['checks'] if c['id'] in ['welcome-'+id for id in ids]]
                self.assertEqual(len(checks),len(ids));output=temp/(tag+'-out');output.mkdir()
                for c in checks:
                    c['execution']=run_pure(root,model,c,output)
                    self.assertEqual(c['execution']['status'],'EXECUTED',(tag,c['execution']))
                result=grade(model,checks,output)
                self.assertEqual([c['status'] for c in result['checks']],['FAIL']*len(checks),(tag,result))
                collected.append({'mutation':tag,'sourceFile':path,'replacement':{'from':old,'to':new},'result':result,'observations':checks})
                write_json(EVIDENCE/'mutations.json',collected)

    def test_two_public_batches_fresh_clocks_constants_and_mutated_neighbor(self):
        checks=load_assignment(REPO/'assignments/welcome-options')['checks'][:2]
        # Distinct criterion clocks, even with identical metadata UUIDs.
        checks[1]['clock']['date']='2027-01-01';checks[1]['clock']['time']='00:00:00'
        checks[1]['constants']['Alpha::Options']['Text']='Other A'
        checks[1]['constants']['Beta::Options']['Text']='Other B'
        for a in checks[1]['expected']['actions']:
            a['result']['Компоненты.DateField.Значение']='2027-01-01'
            a['result']['Title']='Other B/Other A'
            if a['result']['Компоненты.Summary.Значение'] != 'local':
                a['result']['Компоненты.Summary.Значение']='Other B/Other A'
            a['constants']=copy.deepcopy(checks[1]['constants'])
        with TemporaryDirectory() as d:
            temp=Path(d);assignment=temp/'assignment.yaml'
            assignment.write_text(yaml.dump({'name':'Batch welcome isolation','checks':checks},Dumper=author.FixtureDumper,allow_unicode=True))
            submissions=[]
            for i in range(3):
                root=temp/str(i);shutil.copytree(CORPUS/'options',root)
                if i==2:
                    p=root/'Alpha/CalendarView.xbsl';p.write_text(p.read_text().replace('"local"','"broken"'))
                submissions.append({'studentId':'welcome-'+str(i),'project':str(root)})
            manifest=temp/'manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'welcome','submissions':submissions})
            write_json(EVIDENCE/'batch-manifest.json',json.loads(manifest.read_text()))
            results=[]
            for name in ['batch-first','batch-fresh']:
                out=EVIDENCE/name
                if out.exists():shutil.rmtree(out)
                p=subprocess.run([str(REPO/'bin/element-test'),'batch','--manifest',str(manifest),'--output',str(out),'--workers','1'],capture_output=True,text=True,timeout=300,env={**os.environ,'ELEMENT_TEST_CACHE_DIR':str(temp/'cache')})
                self.assertEqual(p.returncode,1,p.stderr+p.stdout)
                data=json.loads((out/'batch-result.json').read_text());self.assertEqual(data['counts']['passed'],2);self.assertEqual(data['counts']['failed'],1)
                results.append([(i['status'],i['score'],i['maxScore']) for i in data['submissions']])
            self.assertEqual(results[0],results[1])

    def test_unsupported_constant_effect_then_supported_criterion(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';shutil.copytree(CORPUS/'options',root)
            p=root/'Alpha/Options.xbsl'
            p.write_text(p.read_text()+'\nметод Change()\n    Options.Записать()\n;\n')
            model=analyze(root)
            checks=[{'id':'write-unavailable','type':'runtime','points':1,
                     'target':{'module':'Options','namespace':'Alpha','method':'Change'},'args':[]},
                    load_assignment(REPO/'assignments/welcome-options')['checks'][0]]
            for c in checks:c['execution']=run_pure(root,model,c,temp)
            result=grade(model,checks,temp)
            self.assertEqual([c['status'] for c in result['checks']],['UNSUPPORTED','PASS'])
            self.assertEqual((result['score'],result['maxScore'],result['unavailablePoints']),(1,1,1))
            self.assertIn('Эффект набора констант',checks[0]['execution']['message'])
            write_json(EVIDENCE/'unsupported-then-supported.json',result)


if __name__=='__main__':unittest.main()
