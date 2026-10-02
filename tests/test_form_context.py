"""Task 34: bounded form state, source dependencies, independent mutation grading."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
import yaml
from element_test.assignment import load_assignment
from element_test.bridge import run_test, write_json
from element_test.execution_plan import plan_execution
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import execute_engine, prepare_script, run_pure
from element_test.yaml_io import InputError, InvalidTestError
from form_fixtures import *


def grade(model, checks, temp):
    write_json(temp/'model.json',model)
    write_json(temp/'assignment.json',{'name':'Independent form assessment','checks':checks})
    return execute_engine('test',temp/'model.json',temp/'assignment.json',temp)


class FormPlanTest(unittest.TestCase):
    def test_all_twenty_real_roots_and_plans_exclude_teacher_answers(self):
        a=load_assignment(ASSIGNMENT)
        with open_project(ARCHIVE) as root:
            model=analyze(root);seen=set()
            for c in a['checks']:
                if '_unsupported_' in c['id']:continue
                c={**c,'expected':{'SECRET_TEACHER_ANSWER':937194}}
                p=plan_execution(root,model,c)
                self.assertNotIn('SECRET_TEACHER_ANSWER',p.to_json())
                self.assertTrue(p.form['canonical']);self.assertTrue(p.form['components'])
                if c['target']['method'] in {'ИтогиОтмеченныхОбработчик','СуммаОтмеченногоСпросаОбработчик','ПосчитатьУчастниковОбработчик'}:
                    self.assertTrue(p.form['argumentBindings'])
                    bad={**c,'args':[{}, {'contextPath':'ЗаголовокФормы'}]}
                    with self.assertRaisesRegex(InputError,'Тип contextPath'):plan_execution(root,model,bad)
                    bad={**c,'args':[{}, {'contextPath':'Объект.Missing'}]}
                    with self.assertRaises(InputError):plan_execution(root,model,bad)
                seen.add((p.entry.identity.namespace,p.entry.identity.owner,p.entry.identity.declaration))
                if c['target']['method']=='ПослеСоздания':
                    self.assertTrue(any(b.category=='form-system' for b in p.bindings))
            self.assertEqual(len(seen),20)

    def test_portable_owner_collisions_nested_components_and_native_scope(self):
        for project,assignment in [('ledgers','form-ledgers'),('accounts','form-accounts')]:
            root=CORPUS/project;model=analyze(root)
            for c in load_assignment(REPO/'assignments'/assignment)['checks']:
                p=plan_execution(root,model,c)
                self.assertEqual(set(p.form['components']),{'Outer','Inner','Summary' if project=='ledgers' else 'Banner'})
                self.assertTrue(p.form['objectCanonical'].startswith('ТестТип'))
                with TemporaryDirectory() as d:
                    prepare_script(root,model,c,Path(d))
                    text=(Path(d)/(p.form['canonical'].split('.')[0]+'.sbsl')).read_text()
                    self.assertIn('знч Notice = "local"' if project=='ledgers' else 'знч TitleText = "local"',text)
                    self.assertIn('этот.Helper()' if project=='ledgers' else 'этот.Decorate()',text)

    def test_lifecycle_input_and_observe_validation(self):
        root=CORPUS/'ledgers';model=analyze(root);base=load_assignment(REPO/'assignments/form-ledgers')['checks'][0]
        for change in [{'lifecycle':None},{'lifecycle':{'isNew':1}},
                       {'context':{'Typo':True}},{'context':{'Changed':'yes'}},
                       {'context':{'Компоненты':{'Missing':{}}}},
                       {'context':{'Компоненты':{'Summary':{'Значение':12}}}},
                       {'observe':['Компоненты.Missing.Значение']},{'observe':['Notice','Notice']},
                       {'observe':['Объект.ТестСостояниеНовизны']}, {'args':[1]}]:
            with self.subTest(change=change), self.assertRaises(InputError):
                plan_execution(root,model,{**base,**change})

    def test_duplicate_component_unknown_source_member_and_command_api(self):
        root=CORPUS/'ledgers';base=load_assignment(REPO/'assignments/form-ledgers')['checks'][0]
        with TemporaryDirectory() as d:
            temp=Path(d)/'source';shutil.copytree(root,temp)
            path=temp/'Alpha/Panel.yaml';source=path.read_text()
            path.write_text(source.replace('Имя: Inner','Имя: Outer'))
            with self.assertRaisesRegex(InputError,'повторное'):plan_execution(temp,analyze(temp),base)
            path.write_text(source)
            module=temp/'Alpha/Panel.xbsl';source=module.read_text()
            for changed in [source.replace('Компоненты.Summary','Компоненты.Missing'),
                            source.replace('.Значение','.Wrong'),
                            source.replace('этот.Changed = Changed','C.Размер()'),
                            source.replace('этот.Changed = Changed','Объект = новый Ledger.Объект()')]:
                module.write_text(changed)
                with self.assertRaises(InputError):plan_execution(temp,analyze(temp),base)
            module.write_text(source)
            bad={**base,'sequence':[{'method':'Apply','args':[True,{'Fake':1}]}]}
            with self.assertRaises(InputError):
                with TemporaryDirectory() as out:prepare_script(temp,analyze(temp),bad,Path(out))

    def test_precise_form_effect_boundaries_and_supported_successor(self):
        with open_project(ARCHIVE) as root:
            model=analyze(root);a=load_assignment(ASSIGNMENT)
            for c in a['checks']:
                if '_unsupported_' in c['id']:
                    with self.assertRaisesRegex(InputError,'Эффект формы (Записать|Открыть)'):
                        plan_execution(root,model,c)
                elif '_after_' in c['id']:self.assertTrue(plan_execution(root,model,c).form)

    def test_project_method_not_replaced_by_lifecycle(self):
        root=CORPUS/'ledgers';c=load_assignment(REPO/'assignments/form-ledgers')['checks'][0]
        with TemporaryDirectory() as d:
            temp=Path(d)/'source';shutil.copytree(root,temp)
            (temp/'Alpha/Ledger.Объект.xbsl').write_text('метод ЭтоНовый(): Булево\n    возврат Ложь\n;\n')
            with self.assertRaisesRegex(InputError,'Проектный ЭтоНовый'):plan_execution(temp,analyze(temp),c)
            (temp/'Alpha/Ledger.Объект.xbsl').unlink()
            (temp/'Beta/Ledger.Объект.xbsl').write_text('метод ЭтоНовый(): Булево\n    возврат Ложь\n;\n')
            self.assertTrue(plan_execution(temp,analyze(temp),c).form)

    def test_dynamic_inventory_discovers_new_files_and_methods(self):
        from element_test.coverage import inventory
        with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(original,root)
            first=inventory(root,analyze(root))
            self.assertEqual((first['counts']['files'],first['counts']['methods']),(75,44))
            (root/'New.xbsl').write_text('метод Extra(): Число\n    возврат 7\n;\n')
            second=inventory(root,analyze(root))
            self.assertEqual((second['counts']['files'],second['counts']['methods']),(76,45))
            new=next(m for m in second['methods'] if m['identity']['method']=='Extra')
            self.assertEqual(new['status'],'NO_SCENARIO')
            self.assertTrue(any(f.get('formChecks',{}).get('expressions') for f in second['files']))
            assignment=Path(d)/'assignment.yaml'
            assignment.write_text(yaml.safe_dump({'name':'Coverage evidence','checks':[
                {'id':'coverage-error','type':'runtime','points':1,'target':{'module':'New','method':'Extra'},
                 'args':[],'expected':7}]}))
            evidence=Path(d)/'evidence';evidence.mkdir()
            write_json(evidence/'runtime-evidence.json',[{'criterionId':'coverage-error','status':'EXECUTED'}])
            for status,assessed in [('ERROR',False),('FAIL',True)]:
                write_json(evidence/'result.json',{'sourceHash':analyze(root)['sourceHash'],
                    'checks':[{'id':'coverage-error','status':status}]})
                mapped=inventory(root,analyze(root),[assignment],[evidence])
                extra=next(m for m in mapped['methods'] if m['identity']['method']=='Extra')
                self.assertTrue(extra['executions'][0]['called'])
                self.assertEqual(extra['executions'][0]['independentlyAssessed'],assessed)
                self.assertEqual(extra['status'],'ASSESSED' if assessed else 'CALLED_WITHOUT_ASSESSMENT')

    def test_contextless_existing_helper_preserved(self):
        with open_project(ARCHIVE) as root,TemporaryDirectory() as d:
            c=next(c for c in load_assignment(REPO/'assignments/poc-order')['checks'] if c['type']=='runtime')
            p=plan_execution(root,analyze(root),c);self.assertFalse(p.form)
            prepare_script(root,analyze(root),c,Path(d))

    @unittest.skipUnless((REPO/"script_u_10.0.2_1/lib").is_dir(),"Native Script probe")
    def test_native_probe_defaults_interpolation_and_detached_instances(self):
        script=CORPUS/'native-probes/State.sbsl'
        env={**os.environ,'JAVA_TOOL_OPTIONS':'-Duser.language=ru -Duser.country=RU'}
        result=subprocess.run([str(REPO/'bin/script-runtime'),'-c','9.0',str(script)],capture_output=True,text=True,timeout=30,env=env)
        self.assertEqual(result.returncode,0,result.stderr)
        actual=json.loads(result.stdout)
        self.assertEqual(actual['a']['Компоненты']['Caption']['Значение'],'1; 1,25')
        self.assertEqual(actual['b']['Rows'],[]);self.assertFalse(actual['b']['Enabled']);self.assertTrue(actual['new'])


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Form Docker opt-in')
class FormDockerTest(unittest.TestCase):
    def test_original_archive_all_scenarios_and_independent_grading(self):
        before=ARCHIVE.read_bytes()
        output=EVIDENCE/'run'
        if output.exists():
            shutil.rmtree(output)
        process=subprocess.run([str(REPO/'bin/element-test'),'run','--project',str(ARCHIVE),
                                '--assignment',str(ASSIGNMENT),'--output',str(output)],
                               capture_output=True,text=True,timeout=900)
        self.assertEqual(process.returncode,2,process.stderr)
        result=json.loads((output/'result.json').read_text())
        checks=load_assignment(ASSIGNMENT)['checks']
        self.assertEqual([c['status'] for c in result['checks']],
                         ['UNSUPPORTED' if '_unsupported_' in c['id'] else 'PASS' for c in checks])
        self.assertEqual((result['score'],result['maxScore'],result['unavailablePoints']),(74,74,3))
        facts=json.loads((output/'runtime-evidence.json').read_text())
        roots={(c['target']['namespace'],c['target']['module'],c['target']['method']) for c,f in zip(checks,facts) if f['status']=='EXECUTED'}
        self.assertEqual(len(roots),20)
        self.assertEqual(ARCHIVE.read_bytes(),before)

    def test_portability_and_same_reference_fresh_instances(self):
        for project,assignment in [('ledgers','form-ledgers'),('accounts','form-accounts')]:
            root=CORPUS/project;model=analyze(root);checks=load_assignment(REPO/'assignments'/assignment)['checks']
            with TemporaryDirectory() as d:
                temp=Path(d)
                for c in checks:
                    c['context']['Объект']['Link']={'Идентификатор':'00000000-0000-0000-0000-000000000034'}
                    c['execution']=run_pure(root,model,c,temp)
                    self.assertEqual(c['execution']['status'],'EXECUTED',c['execution'])
                result=grade(model,checks,temp)
                self.assertEqual([c['status'] for c in result['checks']],['PASS','PASS'])
                write_json(EVIDENCE/('portable-'+project+'.json'),result)

    def test_source_mutations_are_fail_in_sbsl_including_dependency(self):
        scenarios=[
            ('branch','Продажи/ЗаказФормаОбъекта.xbsl','если Объект.ЭтоНовый()','если не Объект.ЭтоНовый()',['Заказ_create_1_42']),
            ('title','Продажи/ЗаказФормаОбъекта.xbsl','Заказ (новый)','Заказ broken',['Заказ_create_1_42']),
            ('toggle','Продажи/ЗаказФормаОбъекта.xbsl','РедактированиеДанных = Истина','РедактированиеДанных = Ложь',['Заказ_toggle_0']),
            ('helper','Продажи/ЗаказФормаОбъекта.xbsl','Вернуть из архива','Архивировать',['Заказ_archive_1','Заказ_create_0_empty']),
            ('sum','Продажи/ЗаказФормаОбъекта.xbsl','СуммаТоваров += Элемент.Сумма','СуммаТоваров += 0',['Заказ_totals_fraction','Заказ_helper_fraction']),
            ('input-table','Продажи/ЗаказФормаОбъекта.xbsl','знч Итоги = РассчитатьИтоги(Параметр)',
             'знч Итоги = РассчитатьИтоги(Параметр)\n    если Параметр.Размер() > 0\n        Параметр[0].Сумма = 999\n    ;', ['Заказ_totals_fraction']),
            ('caption','CRM/СделкаФормаОбъекта.xbsl','Отмечено участников:','Wrong:',['Сделка_participants_several']),
            ('demand','CRM/СделкаФормаОбъекта.xbsl','Сумма += Элемент.Сумма','Сумма = Элемент.Сумма',['Сделка_demand_fraction']),
            ('partial','Товары/ОтгрузкаФормаОбъекта.xbsl','если СозданныйНаЧасти','если Истина',['Отгрузка_create_1_empty']),
        ]
        a=load_assignment(ASSIGNMENT);collected=[]
        with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
            temp=Path(d)
            for tag,path,old,new,ids in scenarios:
                root=temp/tag;shutil.copytree(original,root);p=root/path
                self.assertIn(old,p.read_text());p.write_text(p.read_text().replace(old,new))
                model=analyze(root);checks=[copy.deepcopy(c) for c in a['checks'] if c['id'] in ids]
                out=temp/(tag+'-run');out.mkdir()
                for c in checks:
                    c['execution']=run_pure(root,model,c,out)
                    self.assertEqual(c['execution']['status'],'EXECUTED',(tag,c['execution']))
                result=grade(model,checks,out)
                self.assertEqual([c['status'] for c in result['checks']],['FAIL']*len(checks),(tag,result))
                collected.append({'mutation':tag,'sourceFile':path,'replacement':{'from':old,'to':new},'result':result,'observations':checks})
            write_json(EVIDENCE/'mutations.json',collected)

    def test_public_batch_fresh_outputs_isolate_mutated_work_and_states(self):
        root=CORPUS/'ledgers';checks=load_assignment(REPO/'assignments/form-ledgers')['checks']
        for c in checks:
            c['context']['Объект']['Link']={'Идентификатор':'00000000-0000-0000-0000-000000000034'}
        with TemporaryDirectory() as d:
            temp=Path(d);assignment=temp/'assignment.yaml'
            assignment.write_text(yaml.safe_dump({'name':'Batch form isolation','checks':checks},allow_unicode=True))
            submissions=[]
            for index in range(3):
                project=temp/str(index);shutil.copytree(root,project)
                if index==2:
                    p=project/'Alpha/Panel.xbsl';p.write_text(p.read_text().replace('"fresh"','"broken"'))
                submissions.append({'studentId':'form-'+str(index),'project':str(project)})
            manifest=temp/'manifest.json'
            write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'forms','submissions':submissions})
            results=[]
            for name in ['batch-first','batch-fresh']:
                output=EVIDENCE/name
                if output.exists():shutil.rmtree(output)
                process=subprocess.run([str(REPO/'bin/element-test'),'batch','--manifest',str(manifest),
                                        '--output',str(output),'--workers','1'],capture_output=True,text=True,
                                       timeout=300,env={**os.environ,'ELEMENT_TEST_CACHE_DIR':str(temp/'cache')})
                self.assertEqual(process.returncode,1,process.stderr)
                data=json.loads((output/'batch-result.json').read_text())
                self.assertEqual(data['counts']['passed'],2);self.assertEqual(data['counts']['failed'],1)
                for item in data['submissions']:
                    package=json.loads((output/item['grading']).read_text())
                    self.assertEqual(package['schemaVersion'],'1.0')
                    self.assertEqual(len(package['feedback']),2)
                results.append([(i['status'],i['score'],i['maxScore']) for i in data['submissions']])
            self.assertEqual(results[0],results[1])
