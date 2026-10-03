"""Task 38 only: authoring, declarations, runtime snapshots and mutation grading."""
import copy
import importlib.util
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
from element_test.declarative_bindings import preflight, plan_binding, DeclarationMismatch
from element_test.form_requirements import normalize_requirements, expand_requirements, author_form
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import REPO, prepare_script, run_pure, execute_engine
from element_test.yaml_io import InvalidTestError, UnsupportedSyntaxError

CORPUS = REPO/'tests/corpus/declarative-bindings'
spec=importlib.util.spec_from_file_location('binding_author',CORPUS/'build_portable.py')
author=importlib.util.module_from_spec(spec);spec.loader.exec_module(author)


def checks():
    return expand_requirements(author.teacher())


def grade(root, model, entries, temp):
    for c in entries:
        c['execution'] = preflight(root,model,c) or run_pure(root,model,c,temp)
    write_json(temp/'model.json',model)
    write_json(temp/'assignment.json',{'name':'Bindings','checks':entries})
    return execute_engine('test',temp/'model.json',temp/'assignment.json',temp)


class BindingPlanTest(unittest.TestCase):
    def test_normalization_source_text_and_authoring(self):
        contract=author.teacher()
        self.assertEqual(normalize_requirements(contract),normalize_requirements(normalize_requirements(contract)))
        with TemporaryDirectory() as d:
            path=Path(d);(path/'text.txt').write_text('Новое русское описание формы')
            author.write(path/'contract.yaml',contract)
            result=author_form(path/'text.txt',path/'contract.yaml',path/'out.yaml')
            self.assertEqual(result['description'],'Новое русское описание формы')
            self.assertEqual(result['requirements'],normalize_requirements(contract)['requirements'])
        for mutate in [lambda c:c.update(schemaVersion=2),lambda c:c.update(schemaVersion=True),lambda c:c.update(description=''),
                       lambda c:c['form'].update(namespace=123),lambda c:c['form'].update(typo='x'),
                       lambda c:c['requirements'][0].update(clause='missing'),
                       lambda c:c['requirements'][1].update(outputType='Массив<>'),
                       lambda c:c['requirements'][1]['scenarios'][0].pop('expected'),
                       lambda c:c['requirements'][0].update(points=float('nan')),
                       lambda c:c['requirements'][0]['assert'].update(command={'path':'bad','reference':'=Записать'}),
                       lambda c:c['requirements'][0]['selector'].update(typo='x')]:
            bad=copy.deepcopy(contract);mutate(bad)
            with self.assertRaises(InvalidTestError):normalize_requirements(bad)

    def test_no_methods_no_answers_real_yaml_paths_and_owner_identity(self):
        for project in ('first','second'):
            root=CORPUS/project;model=analyze(root)
            for c in checks():
                if c['formRequirement']['requirement']['kind']=='structure':
                    self.assertTrue(preflight(root,model,c)['actual']);continue
                c['expected']={'actions':['TEACHER_EXPECTATION-974217'] * sum('snapshot' in step for step in c.get('steps',[{'snapshot':True}]))}
                plan=plan_binding(root,model,c)
                self.assertIsNone(plan.to_dict()['entry'])
                self.assertEqual(plan.to_dict()['symbols'],[])
                self.assertNotIn('TEACHER_EXPECTATION',plan.to_json())
                self.assertEqual(plan.binding['yamlPath'][0],'Наследует')
                self.assertNotEqual(plan.contracts.canonical_type('Alpha::Ledger.Entries'),plan.contracts.canonical_type('Beta::Ledger.Entries'))
                with TemporaryDirectory() as d:
                    script=prepare_script(root,model,c,Path(d)).read_text()
                    self.assertNotIn('TEACHER_EXPECTATION',script)
                    self.assertNotIn('метод Fake',script)

    def test_invalid_fixture_unknown_ast_ambiguity_and_successor(self):
        root=CORPUS/'first';model=analyze(root);c=checks()[1]
        for context in [{'Unknown':1},{'Объект':{'Caption':False}},{'Selected':'true'}]:
            bad={**c,'context':context}
            self.assertEqual(preflight(root,model,bad)['reasonCode'],'invalid_test')
        with TemporaryDirectory() as d:
            project=Path(d)/'source';shutil.copytree(root,project)
            file=project/'Alpha/Panel.yaml';data=yaml.safe_load(file.read_text())
            item=data['Наследует']['Содержимое']['Содержимое'][0]
            item['Значение']='=новый Strange()';author.write(file,data)
            c=checks()[1];c['formRequirement']['requirement']['selector']={'role':'ПолеВвода'}
            self.assertEqual(preflight(project,analyze(project),c)['status'],'UNSUPPORTED')
            item['Значение']='=Объект.Caption';other=copy.deepcopy(item);other['Имя']='OtherCaption';data['Наследует']['Содержимое']['Содержимое'].append(other);author.write(file,data)
            self.assertIn('Неоднозначное',preflight(project,analyze(project),checks()[1])['message'])
        self.assertIsNone(preflight(root,model,checks()[1]))

    def test_missing_binding_bad_generic_owner_and_row_scope_are_declaration_failures(self):
        root=CORPUS/'first'
        with TemporaryDirectory() as d:
            project=Path(d)/'source';shutil.copytree(root,project);file=project/'Alpha/Panel.yaml'
            original=yaml.safe_load(file.read_text())
            mutations=[lambda x:x['Наследует']['Содержимое']['Содержимое'].pop(0),
                       lambda x:x['Наследует']['Содержимое']['Содержимое'][1]['Колонки'][0].update(Тип='СтандартнаяКолонкаТаблицы<Beta::Ledger.Entries, Число>'),
                       lambda x:x['Наследует']['Содержимое']['Содержимое'][1]['Колонки'][0].update(Значение='=RowData.Missing')]
            for mutation,index in zip(mutations,[1,3,3]):
                data=copy.deepcopy(original);mutation(data);author.write(file,data)
                facts=preflight(project,analyze(project),checks()[index])
                self.assertEqual(facts['status'],'EXECUTED');self.assertIn('declarationViolation',facts['actual'])
                result=grade(project,analyze(project),[checks()[index]],Path(d))
                self.assertEqual(result['checks'][0]['status'],'FAIL',result)

    def test_plain_and_record_forms_without_xbsl_and_read_only_record(self):
        root=CORPUS/'first';model=analyze(root)
        for name,selector,prop,context,expected in [('Plain',{},'Заголовок',{'Title':'caption'},'caption'),
                ('RecordPanel',{'role':'ПолеВвода'},'Значение',{'Запись':{'Text':'company','Enabled':True}},'company')]:
            c=copy.deepcopy(checks()[1]);c['formRequirement']['form']['name']=name
            c['formRequirement']['requirement'].update(selector=selector,property=prop)
            c.update(context=context,steps=[{'snapshot':True}],expected={'actions':[expected]})
            self.assertIsNone(preflight(root,model,c))
            with TemporaryDirectory() as d:prepare_script(root,model,c,Path(d))
            if name=='RecordPanel':
                c['steps']=[{'set':{'path':'Запись.Text','value':'bad'}},{'snapshot':True}]
                self.assertEqual(preflight(root,model,c)['reasonCode'],'invalid_test')

    def test_equivalent_binding_expression_and_project_rowdata_shadow(self):
        root=CORPUS/'first'
        with TemporaryDirectory() as d:
            project=Path(d)/'source';shutil.copytree(root,project)
            file=project/'Alpha/Panel.yaml';data=yaml.safe_load(file.read_text())
            data['Наследует']['Содержимое']['Содержимое'][0]['Значение']='=этот.Объект.Caption + ""'
            author.write(file,data)
            self.assertIsNone(preflight(project,analyze(project),checks()[1]))
            author.write(project/'Alpha/Shadow.yaml',{'ВидЭлемента':'КомпонентИнтерфейса','Имя':'Shadow',
              'Свойства':[{'Имя':'RowData','Тип':'Alpha::Ledger.Entries'}],
              'Наследует':{'Тип':'Форма','Содержимое':{'Тип':'ПолеВвода<Число>','Имя':'Number','Значение':'=RowData.Debit'}}})
            c=copy.deepcopy(checks()[1]);c['formRequirement']['form']['name']='Shadow'
            c['formRequirement']['requirement'].update(selector={'role':'ПолеВвода'},outputType='Число')
            c.update(context={'RowData':{'Debit':91,'Credit':37}},expected={'actions':[91]})
            plan=plan_binding(project,analyze(project),c)
            self.assertEqual(plan.binding['compiled'],'Контекст.RowData.Debit')

    def test_another_description_changes_grading_without_engine_changes(self):
        contract=author.teacher();contract['requirements']=[contract['requirements'][-1]]
        contract['requirements'][0]['selector']={'role':'ПолеВвода'}
        root=CORPUS/'first';model=analyze(root)
        self.assertFalse(preflight(root,model,expand_requirements(contract)[0])['actual'])

    def test_explicit_order_and_forbidden_handler(self):
        root=CORPUS/'first';model=analyze(root)
        c=copy.deepcopy(checks()[0])
        c['formRequirement']['requirement'].update(selector={'role':'Группа'},
            **{'assert':{'order':['LabelA','GridA']}})
        self.assertTrue(preflight(root,model,c)['actual'])
        c['formRequirement']['requirement']['assert']['order'].reverse()
        self.assertFalse(preflight(root,model,c)['actual'])
        with TemporaryDirectory() as d:
            result=grade(root,model,[copy.deepcopy(c)],Path(d))
            self.assertEqual(result['checks'][0]['status'],'FAIL',result)
            write_json(REPO/'result/dvizhok-declarative-bindings/mutations/explicit-order.json',result)
        c['formRequirement']['requirement'].update(selector={'role':'ПолеВвода'},
            **{'assert':{'handler':False}})
        self.assertTrue(preflight(root,model,c)['actual'])
        with TemporaryDirectory() as d:
            project=Path(d)/'source';shutil.copytree(root,project)
            file=project/'Alpha/Panel.yaml';data=yaml.safe_load(file.read_text())
            data['Наследует']['Содержимое']['Содержимое'][0]['ПриНажатии']='Unexpected'
            author.write(file,data)
            self.assertFalse(preflight(project,analyze(project),c)['actual'])
            result=grade(project,analyze(project),[c],Path(d))
            self.assertEqual(result['checks'][0]['status'],'FAIL',result)
            write_json(REPO/'result/dvizhok-declarative-bindings/mutations/forbidden-handler.json',result)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Task 38 Docker opt-in')
class BindingDockerTest(unittest.TestCase):
    def test_two_implementations_and_independent_sbsl_mutations(self):
        for name in ('first','second'):
            root=CORPUS/name
            with TemporaryDirectory() as d:
                result=grade(root,analyze(root),checks(),Path(d))
                self.assertEqual([c['status'] for c in result['checks']],['PASS']*5,result)
        root=CORPUS/'first'
        with TemporaryDirectory() as d:
            temp=Path(d);project=temp/'project';shutil.copytree(root,project)
            file=project/'Alpha/Panel.yaml';original=yaml.safe_load(file.read_text())
            for label,mutate in [
                ('branches',lambda x:x['Наследует']['Содержимое']['Содержимое'][1]['Источник'].update(Данные='=Selected ? Объект.Entries : Supplied')),
                ('column',lambda x:x['Наследует']['Содержимое']['Содержимое'][1]['Колонки'][0].update(Значение='=RowData.Credit')),
                *[(label,lambda x,name=name:x['Наследует']['Содержимое']['Содержимое'][1]['Источник'].update(Данные='='+name)) for label,name in [('lost-row','Lost'),('reordered-row','Reordered'),('duplicated-row','Duplicated')]],
                ('constant-source',lambda x:x['Наследует']['Содержимое']['Содержимое'][1]['Источник'].update(Данные='=Объект.Entries')),
                ('forbidden',lambda x:x['Наследует']['Содержимое']['Содержимое'].append({'Тип':'Кнопка','Имя':'Forbidden'})),
                ('missing',lambda x:x['Наследует']['Содержимое']['Содержимое'].pop(0))]:
                data=copy.deepcopy(original);mutate(data);author.write(file,data)
                result=grade(project,analyze(project),checks(),temp)
                write_json(REPO/'result/dvizhok-declarative-bindings/mutations'/(label+'.json'),result)
                self.assertIn('FAIL',[c['status'] for c in result['checks']],label)
                self.assertNotIn('UNSUPPORTED',[c['status'] for c in result['checks']],result)

    def test_errors_are_unavailable_and_do_not_block_supported_successor(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';author.build(root)
            entries=[]
            for name,text in [('UnknownPanel','=новый Strange()'),('BrokenPanel','=10 / Divisor')]:
                author.write(root/'Alpha'/(name+'.yaml'),{'ВидЭлемента':'КомпонентИнтерфейса','Имя':name,
                    'Свойства':[{'Имя':'Divisor','Тип':'Число'}],
                    'Наследует':{'Тип':'Форма','Содержимое':{'Тип':'ПолеВвода<Число>','Имя':'Number','Значение':text}}})
                c=copy.deepcopy(checks()[1]);c['id']=name;c['formRequirement']['form']['name']=name
                c['formRequirement']['requirement'].update(selector={'role':'ПолеВвода'},outputType='Число')
                c.update(context={},expected={'actions':[0]});entries.append(c)
            invalid=copy.deepcopy(checks()[1]);invalid['id']='invalid-fixture';invalid['context']={'Unknown':True}
            entries.insert(1,invalid);entries.append(checks()[1])
            result=grade(root,analyze(root),entries,temp)
            self.assertEqual([c['status'] for c in result['checks']],['UNSUPPORTED','ERROR','ERROR','PASS'],result)
            self.assertEqual(result['unavailablePoints'],3)

    def test_linked_handler_renaming_and_wrong_compatible_effect(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';author.build(root)
            author.write(root/'Alpha/Actions.yaml',{'ВидЭлемента':'КомпонентИнтерфейса','Имя':'Actions',
              'Свойства':[{'Имя':'Caption','Тип':'Строка'},{'Имя':'Enabled','Тип':'Булево'}],
              'Наследует':{'Тип':'Форма','Заголовок':'=Caption','ДополнительныеКоманды':{'Тип':'ОбычнаяКоманда','Имя':'Action','Обработчик':'Switch'}}})
            module=root/'Alpha/Actions.xbsl'
            original='метод Switch(C: ОбычнаяКоманда)\n    если Enabled\n        Caption = "before"\n    иначе\n        Caption = "changed"\n    ;\n    Enabled = не Enabled\n;\n'
            module.write_text(original)
            c=copy.deepcopy(checks()[1]);c['formRequirement']['form']['name']='Actions'
            c['formRequirement']['requirement'].update(selector={},property='Заголовок')
            action={'call':{'selector':{'role':'ОбычнаяКоманда'},'args':[{}]}}
            c.update(context={'Caption':'before','Enabled':False},steps=[{'snapshot':True},action,{'snapshot':True},action,{'snapshot':True}],expected={'actions':['before','changed','before']})
            structure=copy.deepcopy(checks()[0]);structure['formRequirement']['form']['name']='Actions'
            structure['formRequirement']['requirement'].update(selector={'role':'ОбычнаяКоманда'})
            structure['formRequirement']['requirement']['assert']={'handler':True}
            for name in ['Switch','Flip']:
                data=yaml.safe_load((root/'Alpha/Actions.yaml').read_text());data['Наследует']['ДополнительныеКоманды']['Обработчик']=name;author.write(root/'Alpha/Actions.yaml',data)
                module.write_text(original.replace('Switch',name))
                result=grade(root,analyze(root),[copy.deepcopy(structure),copy.deepcopy(c)],temp)
                self.assertEqual([x['status'] for x in result['checks']],['PASS','PASS'],result)
            module.write_text(original.replace('Switch','Flip').replace('Enabled = не Enabled','Enabled = Ложь'))
            result=grade(root,analyze(root),[structure,c],temp)
            self.assertEqual([x['status'] for x in result['checks']],['PASS','FAIL'],result)

    def test_live_array_mutation_preserves_earlier_snapshots(self):
        root=CORPUS/'first';c=checks()[3]
        c.update(steps=[{'snapshot':True},{'set':{'path':'Объект.Entries.0.Debit','value':99}},{'snapshot':True}],
                 expected={'actions':[[17,19,17],[99,19,17]]})
        with TemporaryDirectory() as d:
            result=grade(root,analyze(root),[c],Path(d))
            self.assertEqual(result['checks'][0]['status'],'PASS',result)

    def test_plain_record_and_explicit_original_calls(self):
        with TemporaryDirectory() as d:
            temp=Path(d);root=temp/'source';author.build(root)
            (root/'Alpha/Plain.xbsl').write_text('метод Switch()\n    Title = "changed"\n;\n')
            c=copy.deepcopy(checks()[1]);c['formRequirement']['form']['name']='Plain'
            c['formRequirement']['requirement'].update(selector={},property='Заголовок')
            c.update(context={'Title':'before'},steps=[{'snapshot':True},{'call':{'method':'Switch','args':[]}},{'snapshot':True}],expected={'actions':['before','changed']})
            result=grade(root,analyze(root),[c],temp)
            self.assertEqual(result['checks'][0]['status'],'PASS',result)
            c['formRequirement']['form']['name']='RecordPanel'
            c['formRequirement']['requirement'].update(selector={'role':'ПолеВвода'},property='Значение')
            c.update(context={'Запись':{'Text':'record-marker'}},steps=[{'snapshot':True}],expected={'actions':['record-marker']})
            result=grade(root,analyze(root),[c],temp)
            self.assertEqual(result['checks'][0]['status'],'PASS',result)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Task 38 targeted SQL opening opt-in')
class BindingOpeningTest(unittest.TestCase):
    def test_opened_shipment_source_columns_visibility_and_independent_sql_audit(self):
        import secrets
        from form_effect_fixtures import opening_check, ROWS
        from element_test.integration import run_integration
        c=opening_check('bindings-38',ROWS,3,2.5,18.75,backend='postgres',lifecycle=True)
        c['id']='opening-bindings-38'
        cfg=c['formEffects']['lifecycle']['Товары::ОтгрузкаФормаОбъекта']
        cfg['bindings']=[
          {'id':'source','selector':{'role':'Таблица'},'property':'Источник.Данные','outputType':'Массив<Отгрузка.Товары>'},
          {'id':'amount','selector':{'name':'Товары_Сумма'},'property':'Значение','outputType':'Число'},
          {'id':'visible','selector':{'name':'НадписьИтогиЧасти'},'property':'Видимость','outputType':'Булево'}]
        c['expected']['result']['openings'][0]['context']['bindings']={'source':copy.deepcopy(ROWS),'amount':[7.75,7.75,3.25],'visible':True}
        previous=os.environ.get('ELEMENT_TEST_INTEGRATION_PASSWORD')
        os.environ['ELEMENT_TEST_INTEGRATION_PASSWORD']=secrets.token_hex(24)
        try:
            with open_project(REPO/'Dvizhok.xdump') as root,TemporaryDirectory() as d:
                model=analyze(root);temp=Path(d)
                plan=prepare_script(root,model,c,temp)
                c['execution']=run_integration(c,model,temp,root=root,enabled=True)
                self.assertEqual(c['execution']['status'],'EXECUTED',c['execution'])
                write_json(temp/'model.json',model);write_json(temp/'assignment.json',{'name':'Opened bindings','checks':[c]})
                result=execute_engine('test',temp/'model.json',temp/'assignment.json',temp)
                self.assertEqual(result['checks'][0]['status'],'PASS',result)
                out=REPO/'result/dvizhok-declarative-bindings/opening';out.mkdir(parents=True,exist_ok=True)
                write_json(out/'result.json',result);write_json(out/'runtime-evidence.json',[{'criterionId':c['id'],**c['execution']}])
                write_json(out/'execution-plan.json',json.loads((temp/'execution-plan.json').read_text()))
        finally:
            if previous is None:os.environ.pop('ELEMENT_TEST_INTEGRATION_PASSWORD',None)
            else:os.environ['ELEMENT_TEST_INTEGRATION_PASSWORD']=previous

if __name__ == "__main__":unittest.main()
