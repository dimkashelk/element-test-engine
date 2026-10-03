"""Task 36 acceptance: real Script effects, independent SBSL grading and SQL audit."""
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
import yaml
from element_test.assignment import load_assignment
from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import run_pure, execute_engine, prepare_script
from element_test.integration import run_integration
from element_test.yaml_io import InputError, InvalidTestError, UnsupportedSyntaxError
from form_effect_fixtures import *


def assess(root,checks,output,sql=False,inject_failure=False):
    output.mkdir(parents=True,exist_ok=True)
    model=analyze(root)
    for c in checks:
        c['execution']=(run_integration(c,model,output,root=root,enabled=True,inject_failure=inject_failure)
                        if c.get('storage',{}).get('backend')=='postgres' else run_pure(root,model,c,output))
    write_json(output/'model.json',model)
    write_json(output/'assignment.json',{'name':'Independent form effects','checks':checks})
    result=execute_engine('test',output/'model.json',output/'assignment.json',output)
    write_json(output/'observations.json',checks);write_json(output/'result.json',result)
    return result


class FormEffectPlanTest(unittest.TestCase):
    def test_all_real_plans_types_ranges_and_no_expected_in_ir(self):
        with open_project(ARCHIVE) as root:
            model=analyze(root);roots=set()
            for c in checks():
                c['expected']={'SECRET_FORM_ANSWER':771362}
                plan=plan_execution(root,model,c)
                text=plan.to_json();self.assertNotIn('SECRET_FORM_ANSWER',text)
                self.assertTrue(any(b.category in {'form-write','form-open'} for b in plan.bindings))
                for o in plan.openings:
                    self.assertEqual(o['owner'],'Товары::ОтгрузкаФормаОбъекта')
                    for a in o['arguments']:self.assertTrue(a['canonical']);self.assertGreater(a['end'],a['start'])
                roots.add((plan.entry.identity.namespace,plan.entry.identity.owner,plan.entry.identity.declaration))
                self.assertNotIn('ПриСозданииНаОсновании',{s.identity.declaration for s in plan.symbols})
            self.assertEqual(len(roots),3)

    def test_invalid_teacher_contracts_identity_and_existing_empty_number(self):
        with open_project(ARCHIVE) as root:
            model=analyze(root);base=archive_check('Продажи','Заказ',False,False,'')
            self.assertTrue(plan_execution(root,model,base))
            for change in [{'formEffects':{}},{'formEffects':{'write':1}}, {'formEffects':{'open':['missing']}},
                           {'formEffects':{'open':['Товары::ОтгрузкаФормаОбъекта']*2}},
                           {'formEffects':{'lifecycle':{'Missing':{}}}},
                           {'storage':{'idType':'Строка'}},{'storage':{'idType':'Ууид'}},
                           {'context':{'Объект':{}}}, {'lifecycle':{'isNew':True}}]:
                with self.subTest(change=change),self.assertRaises(InvalidTestError):plan_execution(root,model,{**base,**change})

    def test_opt_in_operations_independent_and_previous_three_boundaries(self):
        with open_project(ARCHIVE) as root:
            model=analyze(root)
            for c in load_assignment(REPO/'assignments/dvizhok-form-context')['checks']:
                if '_unsupported_' in c['id']:
                    with self.assertRaisesRegex(InputError,'Эффект формы'):plan_execution(root,model,c)
            for c in [archive_check('CRM','Сделка',False,True,''),opening_check('empty',[],0,0,0)]:
                c['formEffects']={'write':False}
                with self.assertRaisesRegex(InputError,'Эффект формы'):plan_execution(root,model,c)

    def test_portable_aliases_duplicate_short_owners_and_nullable_arrays(self):
        for project in PORTABLE:
            root=CORPUS/project;model=analyze(root)
            for c in portable_checks(project):
                p=plan_execution(root,model,c)
                self.assertTrue(p.form['objectCanonical'].startswith('ТестТип'))
                if c['id'].endswith('open'):
                    self.assertEqual(len(p.openings),2)
                    self.assertEqual(p.openings[0]['arguments'][1]['canonical'][-1],'?')
                    self.assertNotEqual(p.form['objectCanonical'],next(iter(p.open_forms.values()))['objectCanonical'])

    def test_open_unknown_duplicate_positional_and_wrong_type_have_source_range(self):
        root=CORPUS/'ledgers';base=portable_checks('ledgers')[1]
        with TemporaryDirectory() as d:
            temp=Path(d)/'source';shutil.copytree(root,temp);path=temp/'Alpha/Panel.xbsl';original=path.read_text()
            for old,new,reason in [('Optional = Неопределено','Missing = Неопределено','Неизвестное'),
                                   ('Optional = Неопределено','Lines = Rows','повторное'),
                                   ('Lines = Rows, Optional = Неопределено','Rows','именованные'),
                                   ('Lines = Rows','Lines = 42','тип')]:
                path.write_text(original.replace(old,new))
                with self.subTest(new=new),self.assertRaisesRegex(InputError,reason):plan_execution(temp,analyze(temp),base)

    def test_project_write_and_open_methods_not_replaced(self):
        root=CORPUS/'ledgers';base=portable_checks('ledgers')[0]
        with TemporaryDirectory() as d:
            temp=Path(d)/'source';shutil.copytree(root,temp);path=temp/'Alpha/Panel.xbsl'
            path.write_text(path.read_text()+'\nметод Записать()\n    Caption = "own"\n;\n')
            p=plan_execution(temp,analyze(temp),base)
            self.assertFalse(any(b.category=='form-write' for b in p.bindings))
            other=portable_checks('ledgers')[1];path=temp/'Beta/Window.xbsl'
            path.write_text(path.read_text()+'\n@ВПроекте\n@ИменованныеПараметры\nметод Открыть(Lines: Массив<Ledger.Rows>, Optional: Ledger.Ссылка?)\n;\n')
            panel=temp/'Alpha/Panel.xbsl'
            panel.write_text(panel.read_text().replace('Destination.Открыть(Lines = Rows)','Destination.Открыть(Lines = Rows, Optional = Неопределено)'))
            p=plan_execution(temp,analyze(temp),other)
            self.assertFalse(p.openings)

    def test_local_callable_and_typed_receiver_shadow_system_names(self):
        root=CORPUS/'ledgers';base=portable_checks('ledgers')[0]
        with TemporaryDirectory() as d:
            temp=Path(d)/'source';shutil.copytree(root,temp);panel=temp/'Alpha/Panel.xbsl'
            panel.write_text(panel.read_text()+'''
метод Callback()
    знч Записать = () -> 7
    знч X = Записать()
;
метод Shadow(Rows: Массив<Beta::Ledger.Rows>)
    знч Destination = новый Beta::Ledger.Объект()
    Destination.Открыть(Rows)
;
''')
            (temp/'Beta/Ledger.Объект.xbsl').write_text('''@ВПроекте
метод Открыть(Rows: Массив<Ledger.Rows>)
    Rows[0].Amount = 55
;
''')
            for method,args in [('Callback',[]),('Shadow',[[{'Amount':1.25}]])]:
                c={**base,'target':{**base['target'],'method':method},'args':args}
                p=plan_execution(temp,analyze(temp),c)
                self.assertFalse(any(b.category in {'form-write','form-open'} for b in p.bindings))
                with TemporaryDirectory() as out:prepare_script(temp,analyze(temp),c,Path(out))

    def test_write_arguments_this_receiver_and_handler_closure(self):
        root=CORPUS/'ledgers';base=portable_checks('ledgers')[0]
        with TemporaryDirectory() as d:
            temp=Path(d)/'source';shutil.copytree(root,temp);path=temp/'Alpha/Panel.xbsl';source=path.read_text()
            path.write_text(source.replace('Записать()','этот.Записать()'))
            self.assertTrue(plan_execution(temp,analyze(temp),base))
            path.write_text(source.replace('Записать()','Записать(Истина)'))
            with self.assertRaisesRegex(InputError,'ноль аргументов'):plan_execution(temp,analyze(temp),base)
            path.write_text(source)
            (temp/'Alpha/Ledger.Объект.xbsl').write_text('''@Обработчик
метод ПередЗаписью(До: Ledger.Данные, П: Ledger.ПараметрыЗаписи)
    если Label == "reject"
        выбросить новый ИсключениеНедопустимоеСостояние("rejected")
    ;
;
@Обработчик
метод ПослеЗаписи(До: Ledger.Данные, П: Ledger.ПараметрыЗаписи)
    Label = Label + "after"
;
''')
            p=plan_execution(temp,analyze(temp),base)
            self.assertTrue({'ПередЗаписью','ПослеЗаписи'}<={s.identity.declaration for s in p.symbols})


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Form effects Docker opt-in')
class FormEffectDockerTest(unittest.TestCase):
    def test_public_test_run_full_memory_sql_assignment(self):
        results=[]
        for command in ['test','run']:
            output=OUT/command
            if output.exists():shutil.rmtree(output)
            p=subprocess.run([str(REPO/'bin/element-test'),command,'--project',str(ARCHIVE),
                              '--assignment',str(ASSIGNMENT),'--output',str(output),'--integration'],
                             capture_output=True,text=True,timeout=900)
            self.assertEqual(p.returncode,0,p.stderr)
            result=json.loads((output/'result.json').read_text())
            self.assertEqual([c['status'] for c in result['checks']],['PASS']*len(checks()))
            facts=json.loads((output/'runtime-evidence.json').read_text())
            for fact, criterion in zip(facts, checks()):
                calls=[t['symbol'].split('::')[-1] for t in fact.get('trace',[]) if t['event']=='enter']
                if criterion['target']['method']=='АрхивироватьОбработчик':
                    self.assertLess(calls.index('form:write'),calls.index('ОбновитьПредставлениеАрхивирования'))
                else:
                    order=['РассчитатьИтоги','СоздатьЧасть','ПеренестиТабличнуюЧасть','form:open']
                    self.assertEqual(sorted(order,key=calls.index),order)
                    self.assertFalse(any('Записи' in t for t in fact.get('storageTrace',[])))
                self.assertEqual(fact['status'],'EXECUTED')
                if fact.get('integration'):self.assertEqual(fact['integration']['backend'],'postgres')
            results.append([(c['id'],c['status'],c['actual'],c['score']) for c in result['checks']])
        self.assertEqual(results[0],results[1]);write_json(OUT/'public-equivalence.json',{'checks':len(results[0]),'equal':True})

    def test_portable_snapshots_two_requests_lifecycle_and_no_aliasing(self):
        for project in PORTABLE:
            cases=portable_checks(project)
            result=assess(CORPUS/project,cases,OUT/('portable-'+project))
            self.assertEqual([c['status'] for c in result['checks']],['PASS']*4,result)

    def test_project_methods_and_lexical_shadow_execute_originals(self):
        for project,names in PORTABLE.items():
            record,form,target,table,flag,number,field,amount,apply,helper,show,init=names
            with TemporaryDirectory() as d:
                root=Path(d)/'source';shutil.copytree(CORPUS/project,root)
                panel=root/'Alpha'/(form+'.xbsl');source=panel.read_text()
                panel.write_text(source+'''\nметод Записать()\n    Caption = "own write"\n;\n''')
                c=portable_checks(project)[0]
                stored=[entry('Alpha::'+record,c['context']['Объект'])]
                c['expected']['storage']=stored;c['expected']['result']['storage']=stored
                result=assess(root,[c],OUT/('own-write-'+project))
                self.assertEqual(result['checks'][0]['status'],'PASS',result)
                # Static project Открыть runs as source, even when an unrelated
                # target lifecycle is explicitly available in the same plan.
                panel.write_text(source.replace('Destination.Открыть(Lines = Rows)', 'Destination.Открыть(Lines = Rows, Optional = Неопределено)'))
                target_path=root/'Beta'/(target+'.xbsl')
                target_path.write_text(target_path.read_text()+f'''
@ВПроекте
@ИменованныеПараметры
метод Открыть(Lines: Массив<{record}.{table}>, Optional: {record}.Ссылка?)
    Lines[0].{amount} = 77
;
''')
                c=portable_checks(project)[1]
                c['expected']['result']['result']['args']=[[{amount:77}]];c['expected']['result']['openings']=[]
                result=assess(root,[c],OUT/('own-open-'+project))
                self.assertEqual(result['checks'][0]['status'],'PASS',result)
                self.assertFalse(any(t['symbol'].endswith('::'+init) for t in c['execution'].get('trace',[])))
                panel.write_text(f'''метод {show}(Rows: Массив<Beta::{record}.{table}>)
    знч Destination = новый Beta::{record}.Объект()
    Destination.Открыть(Rows)
;
''')
                (root/'Beta'/(record+'.Объект.xbsl')).write_text(f'''@ВПроекте
метод Открыть(Rows: Массив<{record}.{table}>)
    Rows[0].{amount} = 77
;
''')
                result=assess(root,[c],OUT/('shadow-receiver-'+project))
                self.assertEqual(result['checks'][0]['status'],'PASS',result)

    def test_generated_new_uuid_and_lifecycle_change_after_write(self):
        root=CORPUS/'ledgers';c=portable_checks('ledgers')[0]
        c['context']['Объект'].pop('Ссылка');c['lifecycle']['isNew']=True;c['storage'].pop('initial')
        with TemporaryDirectory() as d:
            actual=run_pure(root,analyze(root),c,Path(d))
            self.assertEqual(actual['status'],'EXECUTED',actual)
            value=actual['actual'];ref=value['storage'][0]['value']['Ссылка']['Идентификатор']
            import uuid
            self.assertEqual(uuid.UUID(ref).version,4);self.assertNotEqual(ref,ID)
            self.assertFalse(value['result']['lifecycle']);self.assertEqual(value['storage'][0]['id'],ref)
            write_json(OUT/'new-identity.json',actual)

    def test_source_mutations_independently_fail_full_result_in_sbsl(self):
        scenarios=[]
        for name,ns in [('Сделка','CRM'),('Заказ','Продажи')]:
            path=ns+'/'+name+'ФормаОбъекта.xbsl'
            for tag,old,new in [('toggle','Объект.Архивный = Истина','Объект.Архивный = Ложь'),
                                ('skip-write','    Записать()','    // Записать()'),
                                ('wrong-object','    Записать()','    Объект.Ссылка.Идентификатор = Ууид{00000000-0000-4000-8000-000000000037}\n    Записать()'),
                                ('text','Вернуть из архива','wrong'),
                                ('lost-field','    Записать()', '    Объект.Номер = "lost"\n    Записать()')]:
                scenarios.append((name+'-'+tag,path,old,new,archive_check(ns,name,False,False,'42')))
        opening=opening_check('mutation',ROWS,3,2.5,18.75)
        for tag,old,new in [('totals','СуммаТоваров += Элемент.Сумма','СуммаТоваров += 0'),
                            ('flag','СозданныйНаЧасти = Истина','СозданныйНаЧасти = Ложь'),
                            ('parameter','КоличествоТоваров = Итоги.Получить("Товаров")','КоличествоТоваров = 7'),
                            ('extra-open','    ОтгрузкаФормаОбъекта.Открыть(', '    ОтгрузкаФормаОбъекта.Открыть()\n    ОтгрузкаФормаОбъекта.Открыть('),
                            ('owner','ОтгрузкаФормаОбъекта.Открыть','Товары::ЛожнаяФорма.Открыть'),
                            ('input','    знч Итоги = РассчитатьИтоги(Параметр)','    Параметр[0].Сумма = 99\n    знч Итоги = РассчитатьИтоги(Параметр)')]:
            c=deepcopy(opening)
            if tag=='owner':c['formEffects']['open'].append('Товары::ЛожнаяФорма')
            scenarios.append((tag,'Продажи/ЗаказФормаОбъекта.xbsl',old,new,c))
        for field in ['Номенклатура','Количество','Цена','Сумма']:
            scenarios.append(('field-'+field,'Товары/Отгрузка.xbsl',field+' = Элемент.'+field,
                              field+' = '+('Неопределено' if field=='Номенклатура' else '99'),deepcopy(opening)))
        with open_project(ARCHIVE) as original:
            from element_test.indexer import parse_module
            source=(original/'Продажи/ЗаказФормаОбъекта.xbsl').read_text()
            node=next(n for n in parse_module(source)[0] if n.name=='ОтгрузитьОбработчик')
            ast=next(n for n in node.expression_tree.walk() if n.kind=='call' and source[n.start:n.end].startswith('ОтгрузкаФормаОбъекта.Открыть('))
            scenarios.append(('no-open','Продажи/ЗаказФормаОбъекта.xbsl',source[ast.start:ast.end],'// omitted open',deepcopy(opening)))
        for tag,old,new in [('skip-rows','Документ.Товары.Добавить(НоваяСтрока)','// skip row'),
                            ('reorder','Документ.Товары.Добавить(НоваяСтрока)','Документ.Товары.Вставить(0, НоваяСтрока)'),
                            ('helper-create','ПеренестиТабличнуюЧасть(Параметр, НовыйДокумент)','ПеренестиТабличнуюЧасть(новый Массив<Заказ.Товары>(), НовыйДокумент)')]:
            scenarios.append((tag,'Товары/Отгрузка.xbsl',old,new,deepcopy(opening)))
        observations=[]
        with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
            for tag,path,old,new,c in scenarios:
                root=Path(d)/tag;shutil.copytree(original,root);p=root/path
                self.assertIn(old,p.read_text());p.write_text(p.read_text().replace(old,new))
                if tag == 'owner':
                    value=yaml.safe_load((root/'Товары/ОтгрузкаФормаОбъекта.yaml').read_text());value['Имя']='ЛожнаяФорма'
                    import uuid
                    value['Ид']=str(uuid.uuid4())
                    (root/'Товары/ЛожнаяФорма.yaml').write_text(yaml.safe_dump(value,allow_unicode=True))
                result=assess(root,[c],OUT/'mutations'/tag)
                self.assertEqual(result['checks'][0]['status'],'FAIL',(tag,result))
                observations.append({'mutation':tag,'sourceFile':path,'status':'FAIL'})
        write_json(OUT/'mutations.json',observations)

    def test_write_rejection_storage_instance_properties_and_successor(self):
        self._write_failure('memory')

    def _write_failure(self,backend):
        with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
            for handler in ['ПередЗаписью','ПослеЗаписи']:
                root=Path(d)/handler;shutil.copytree(original,root)
                (root/'CRM/Сделка.Объект.xbsl').write_text(f'''@Обработчик
метод {handler}(До: Сделка.Данные, П: Сделка.ПараметрыЗаписи)
    если Номер == "reject"
        выбросить новый ИсключениеНедопустимоеСостояние("rejected")
    ;
;
''')
                cases=[]
                for new in [False,True]:
                    failed=archive_check('CRM','Сделка',False,new,'reject',backend)
                    failed['captureException']=True
                    changed=deepcopy(failed['context']['Объект']);changed['Архивный']=True
                    stored=[entry('CRM::Сделка',x['value']) for x in failed['storage']['initial']]
                    failed['expected']={'result':{'result':{'result':{'Объект':changed,'ПредставлениеАрхивирования':'Архивировать','ЗаголовокФормы':'untouched'},
                                                            'exception':{'type':'Std::IllegalStateException','message':'rejected'}},
                                                 'openings':[],'lifecycle':new,'storage':stored},'storage':stored}
                    if backend=='postgres':
                        ordered=sorted(stored,key=lambda x:(x['type'],x['id']))
                        failed['expected']['storage']=ordered
                        failed['expected']['sqlSnapshots']=[ordered]
                    cases.append(failed)
                cases.append(archive_check('CRM','Сделка',False,False,'okay',backend))
                result=assess(root,cases,OUT/('rejected-'+backend)/handler)
                self.assertEqual([c['status'] for c in result['checks']],['PASS']*3,[(c['id'],c['status']) for c in result['checks']])
                for failed in cases[:-1]:
                    trace=failed['execution'].get('trace',[])
                    self.assertFalse(any('ОбновитьПредставлениеАрхивирования' in t.get('symbol','') for t in trace))

    def test_successful_write_then_error_with_and_without_driver_rollback(self):
        self._late_error('memory')

    def _late_error(self,backend):
        with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(original,root)
            p=root/'CRM/СделкаФормаОбъекта.xbsl'
            p.write_text(p.read_text().replace('    Записать()','    Записать()\n    если Объект.Номер == ""\n        выбросить новый ИсключениеНедопустимоеСостояние("late")\n    ;'))
            cases=[]
            for transaction in [False,True]:
                c=archive_check('CRM','Сделка',False,False,'',backend,sequence=True)
                c.pop('sequence');c['id']=str(transaction);c['captureException']=True;c['storage']['transaction']=transaction
                changed=deepcopy(c['context']['Объект']);changed['Архивный']=True
                initial=[entry('CRM::Сделка',x['value']) for x in c['storage']['initial']]
                updated=[entry('CRM::Сделка',changed),initial[1]]
                stored=initial if transaction else updated
                fact={'result':{'result':{'Объект':changed,'ПредставлениеАрхивирования':'Архивировать','ЗаголовокФормы':'untouched'},
                                'exception':{'type':'Std::IllegalStateException','message':'late'}},
                      'openings':[],'lifecycle':False,'storage':stored}
                if transaction:fact={'result':fact,'committed':stored}
                c['expected']={'result':fact,'storage':stored}
                if backend=='postgres':c['expected']['sqlSnapshots']=[initial]+([] if transaction else [updated])
                cases.append(c)
            result=assess(root,cases,OUT/('late-error-'+backend))
            self.assertEqual([c['status'] for c in result['checks']],['PASS','PASS'],result)

    def test_two_public_batches_memory_sql_same_uuid_and_fresh_outputs(self):
        with TemporaryDirectory() as d:
            temp=Path(d);base=portable_checks('ledgers')
            inputs=OUT/'batch-input'
            if inputs.exists():shutil.rmtree(inputs)
            inputs.mkdir()
            # One write plus one opening for each backend, identical UUIDs across submissions.
            cases=[base[0],base[1],portable_checks('ledgers','postgres')[0],portable_checks('ledgers','postgres')[1]]
            for i,c in enumerate(cases):c['id']=str(i)
            assignment=inputs/'assignment.yaml';assignment.write_text(yaml.safe_dump({'name':'Form effects batch','checks':cases},allow_unicode=True))
            submissions=[]
            for i in range(3):
                root=inputs/str(i);shutil.copytree(CORPUS/'ledgers',root)
                if i==2:
                    p=root/'Alpha/Panel.xbsl';p.write_text(p.read_text().replace('Объект.Archived = не Объект.Archived','Объект.Archived = Ложь'))
                submissions.append({'studentId':'effects-'+str(i),'project':str(root)})
            manifest=OUT/'batch-manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'forms36','submissions':submissions})
            for tag in ['batch-first','batch-fresh']:
                output=OUT/tag
                if output.exists():shutil.rmtree(output)
                p=subprocess.run([str(REPO/'bin/element-test'),'batch','--manifest',str(manifest),'--output',str(output),'--workers','1','--integration'],
                                 capture_output=True,text=True,timeout=900,env={**os.environ,'ELEMENT_TEST_CACHE_DIR':str(temp/'cache')})
                self.assertEqual(p.returncode,1,p.stderr)
                result=json.loads((output/'batch-result.json').read_text())
                self.assertEqual((result['counts']['passed'],result['counts']['failed']),(2,1))
                for submission in result['submissions']:
                    facts=json.loads((output/Path(submission['result']).parent/'runtime-evidence.json').read_text())
                    self.assertEqual([f['status'] for f in facts],['EXECUTED']*4)
                self.assertFalse(any(json.loads(line).get('cacheHit',False) for line in (output/'events.jsonl').read_text().splitlines()))


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Form effects SQL opt-in')
class FormEffectSqlTest(unittest.TestCase):
    _write_failure = FormEffectDockerTest._write_failure
    _late_error = FormEffectDockerTest._late_error

    def test_sql_successful_write_late_error_and_external_rollback(self):self._late_error('postgres')
    def test_sql_rejection_and_successor(self):self._write_failure('postgres')

    def test_sequence_intermediate_commit_is_independently_audited(self):
        with open_project(ARCHIVE) as original,TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(original,root)
            p=root/'CRM/СделкаФормаОбъекта.xbsl'
            p.write_text(p.read_text().replace('    Записать()', '    если не Объект.Архивный\n        Записать()\n    ;'))
            c=archive_check('CRM','Сделка',False,False,'','postgres',sequence=True)
            result=assess(root,[c],OUT/'sql-missing-first-commit')
            self.assertEqual(result['checks'][0]['status'],'FAIL',result)
            self.assertEqual(len(c['execution']['actual']['sqlSnapshots']),1)

    def test_sql_infrastructure_failure_never_passes_and_audits_database(self):
        with open_project(ARCHIVE) as root:
            c=archive_check('CRM','Сделка',False,False,'42','postgres')
            p=OUT/'sql-failure';p.mkdir(exist_ok=True)
            actual=run_integration(c,analyze(root),p,enabled=True,root=root,inject_failure=True)
            self.assertEqual(actual['status'],'ERROR',actual)
            self.assertEqual(actual['storageEvidence'],sorted([entry('CRM::Сделка',x['value']) for x in c['storage']['initial']],key=lambda x:x['id']))
            write_json(p/'failure.json',actual)
            result=assess(root,[archive_check('CRM','Сделка',False,False,'42','postgres')],OUT/'sql-after-failure')
            self.assertEqual(result['checks'][0]['status'],'PASS',result)
