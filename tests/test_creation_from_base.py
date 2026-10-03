"""Task 37 targeted acceptance; discovered automatically by nightly CI."""
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
from element_test.runtime import run_pure, prepare_script, execute_engine
from element_test.integration import run_integration
from element_test.yaml_io import InputError, InvalidTestError
from creation_fixtures import *


def assess(root,cases,output):
    output.mkdir(parents=True,exist_ok=True);model=analyze(root)
    for c in cases:
        c['execution']=(run_integration(c,model,output,root=root,enabled=True) if c.get('storage',{}).get('backend')=='postgres' else run_pure(root,model,c,output))
    write_json(output/'model.json',model);write_json(output/'assignment.json',{'name':'Creation from base','checks':cases})
    result=execute_engine('test',output/'model.json',output/'assignment.json',output)
    write_json(output/'observations.json',cases);write_json(output/'result.json',result)
    return result


class CreationPlanTest(unittest.TestCase):
    def test_six_roots_types_ranges_closure_and_independent_expected(self):
        with open_project(ARCHIVE) as root:
            model=analyze(root);found=set();plans=[]
            for c in checks():
                c['expected']={'SECRET_ANSWER_37':817362}
                plan=plan_execution(root,model,c)
                text=plan.to_json();self.assertNotIn('SECRET_ANSWER_37',text)
                found.add((plan.entry.identity.namespace,plan.entry.identity.owner,plan.entry.identity.declaration))
                self.assertGreater(plan.entry.end,plan.entry.start)
                for s in plan.symbols:self.assertGreater(s.end,s.start)
                names={s.identity.declaration for s in plan.symbols}
                self.assertFalse(names & {'ПередЗаписью','ПослеЗаписи'})
                if c['target']['method'] in {'СоздатьЧасть','ПриСозданииНаОсновании'} and c['target']['namespace']=='Товары':self.assertIn('ПеренестиТабличнуюЧасть',names)
                plans.append(plan.to_dict())
                with TemporaryDirectory() as d:prepare_script(root,model,c,Path(d))
            self.assertEqual(found,set(ROOTS))
            OUT.mkdir(parents=True,exist_ok=True);write_json(OUT/'target-plans.json',plans)

    def test_portable_qualified_alias_union_and_same_short_owners(self):
        for project in PORTABLE:
            root=CORPUS/project;model=analyze(root)
            for c in portable_checks(project):
                p=plan_execution(root,model,c)
                self.assertTrue(p.contracts.rename_collisions)
                with TemporaryDirectory() as d:prepare_script(root,model,c,Path(d))
            p=plan_execution(root,model,portable_checks(project)[0])
            self.assertTrue(p.co_located_modules)
            self.assertEqual(len({o for o in p.contracts.owners.values() if o[1]==PORTABLE[project][0]}),2)

    def test_invalid_probe_paths_values_actions_and_targets(self):
        with open_project(ARCHIVE) as root:
            model=analyze(root);base=probe_check('СоздатьЧасть','memory')
            bad=[{'path':'Товары.bad.Количество'},{'path':'Товары.0.Missing'},{'value':'wrong'},
                 {'action':True},{'action':2},{'action':-1},{'target':'argument','argument':99},
                 {'target':'context'},{'path':'Товары.0.Количество; Записать()'},{'extra':True}]
            for change in bad:
                c=copy.deepcopy(base);c['probeMutations'][0].update(change)
                with self.subTest(change=change),self.assertRaises(InvalidTestError):plan_execution(root,model,c)
            for cfg in [{'snapshotArgs':False},{'captureException':True},{'observeFailure':True},{'probeMutations':{}}]:
                with self.subTest(cfg=cfg),self.assertRaises(InvalidTestError):plan_execution(root,model,{**base,**cfg})

    def test_wrong_base_reference_fixture_and_sequence_rejected(self):
        with open_project(ARCHIVE) as root:
            model=analyze(root);base=handler('СчетНаОплату','wrong',ROWS)
            for arg in [{'type':'Финансы::СчетНаОплату.Ссылка','value':ref(ID)},ref('invalid-uuid')]:
                with self.subTest(arg=arg),TemporaryDirectory() as d,self.assertRaises(InvalidTestError):prepare_script(root,model,{**base,'args':[arg]},Path(d))
            c=helper('ПеренестиТабличнуюЧасть','sequence',ROWS,sequence=True)
            for arg in [{'actionArg':[2,1]},{'actionArg':[0,0]},{'actionResult':0},{'actionArg':[0,True]}]:
                c['sequence'][1]['args'][1]=arg
                with self.subTest(arg=arg),TemporaryDirectory() as d,self.assertRaises(InvalidTestError):prepare_script(root,model,c,Path(d))

    def test_source_method_without_final_newline_gets_separate_attached_methods(self):
        from element_test.generated_types import ProjectTypes
        c=ProjectTypes({'elements':[]})
        c.require=lambda typ:None
        c.canonical_type=lambda typ:typ
        c.attach_method('Owner.Объект','метод First()\n;',[])
        c.attach_method('Owner.Объект','метод Second()\n;',[])
        self.assertIn(';\n@Глобально\nметод Second',c.methods['Owner.Объект'])

    def test_transport_keeps_typed_union_and_raw_storage_without_native_type_loading(self):
        from element_test.runtime import decode_output
        tag={'@type':'Scripts::Technical.Ссылка','@value':ref(ID)}
        payload={'actual':{'result':tag,'storage':[{'type':'Ns::Owner','id':ID,'value':json.dumps({'Origin':tag})}]},
                 '_typeIdentities':{'Scripts::Technical.Ссылка':'Ns::Owner.Ссылка'},'_rawStorage':True}
        result=decode_output(json.dumps(payload))
        expected={'type':'Ns::Owner.Ссылка','value':ref(ID)}
        self.assertEqual(result['actual']['result'],expected)
        self.assertEqual(result['actual']['storage'][0]['value'],{'Origin':expected})
        self.assertNotIn('_rawStorage',result)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Creation Script Docker opt-in')
class CreationDockerTest(unittest.TestCase):
    def test_public_test_run_memory_postgres_and_equivalent_evidence(self):
        results=[]
        for command in ['test','run']:
            output=OUT/command
            if output.exists():shutil.rmtree(output)
            output.mkdir(parents=True)
            with (output/'command.log').open('w') as log:
                p=subprocess.run([str(REPO/'bin/element-test'),command,'--project',str(ARCHIVE),'--assignment',str(ASSIGNMENT),'--output',str(output),'--integration'],stdout=log,stderr=subprocess.STDOUT,timeout=1200)
            self.assertEqual(p.returncode,0,(output/'command.log').read_text()[:2000])
            result=json.loads((output/'result.json').read_text())
            self.assertEqual([c['status'] for c in result['checks']],['PASS']*len(checks()),[(c['id'],c['status'],c.get('message')) for c in result['checks'] if c['status']!='PASS'])
            facts=json.loads((output/'runtime-evidence.json').read_text())
            for c,f in zip(checks(),facts):
                self.assertEqual(f['status'],'EXECUTED',f)
                self.assertFalse([t for t in f['storageTrace'] if t != 'sql:commit'])
                entered=[x['symbol'].split('::')[-1] for x in f['trace'] if x['event']=='enter']
                self.assertIn(c['target']['method'],entered)
                if c['target']['method']=='СоздатьЧасть':self.assertLess(entered.index('СоздатьЧасть'),entered.index('ПеренестиТабличнуюЧасть'))
                if c['storage']['backend']=='postgres':self.assertEqual(f['integration']['backend'],'postgres')
            results.append([(c['id'],c['status'],c['actual'],c['score']) for c in result['checks']])
        self.assertEqual(results[0],results[1]);write_json(OUT/'public-equivalence.json',{'checks':len(results[0]),'equal':True})

    def test_portable_original_sources_fresh_loads_nullable_and_union(self):
        for project in PORTABLE:
            for backend in ['memory','postgres']:
                cases=portable_checks(project,backend)
                result=assess(CORPUS/project,cases,OUT/('portable-'+project+'-'+backend))
                self.assertEqual([c['status'] for c in result['checks']],['PASS']*len(cases),[(c['id'],c['status'],c.get('message'),c.get('actual')) for c in result['checks'] if c['status']!='PASS'])

    def test_missing_base_keeps_context_storage_error_and_successor(self):
        with open_project(ARCHIVE) as root:
            for backend in ['memory','postgres']:
                cases=error_checks(backend)
                # Public grading keeps every absent-base failure ERROR, then proceeds.
                cases.append(handler('СчетНаОплату','after-error',ROWS,backend))
                result=assess(root,cases,OUT/('missing-'+backend))
                self.assertEqual([c['status'] for c in result['checks']],['ERROR']*4+['PASS'],result)
                for c in cases[:4]:
                    fact=c['execution'];self.assertEqual(fact['reasonCode'],'execution_error');self.assertIn('actual',fact)
                    self.assertEqual(fact['actual']['result']['args'],[ref(ZERO)])
                    self.assertEqual(fact['actual']['result']['result']['result']['Ссылка'],ref(MARKER))
                    self.assertTrue(fact['actual']['result']['result']['exception']['message'])
                    self.assertFalse([t for t in fact['storageTrace'] if t != 'sql:commit'])
                    self.assertEqual(fact.get('storageEvidence',fact['actual']['storage']),c['expected']['storage'])

    def test_independent_business_mutations_are_fail_not_error(self):
        self._mutations()

    def _mutations(self, tags=None):
        mutations=[
            ('sum-source','Финансы/СчетНаОплату.Объект.xbsl','СуммаИтог += Элемент.Сумма','СуммаИтог += Элемент.Количество * Элемент.Цена','СчетНаОплату'),
            ('accumulator','Финансы/СчетНаОплату.Объект.xbsl','пер СуммаИтог = 0','пер СуммаИтог = Сумма','СчетНаОплату'),
            ('client','Финансы/СчетНаОплату.Объект.xbsl','Контрагент = ЗаказКлиента.Клиент','Контрагент = Неопределено','СчетНаОплату'),
            ('base','Финансы/СчетНаОплату.Объект.xbsl','этот.Основание = Основание','этот.Основание = новый Продажи::Заказ.Ссылка()','СчетНаОплату'),
            ('stale','Финансы/РасходДенежныхСредств.Объект.xbsl','Сумма = Счет.Сумма','знч Старое = Сумма\n    Сумма = Старое','РасходДенежныхСредств'),
            ('stale-base','Финансы/РасходДенежныхСредств.Объект.xbsl','Основание.ЗагрузитьОбъект()',
             'новый СчетНаОплату.Ссылка(Идентификатор = новый Ууид("'+OTHER+'")).ЗагрузитьОбъект()','РасходДенежныхСредств'),
            ('incoming-client','Финансы/ПоступлениеДенежныхСредств.Объект.xbsl','Клиент = Счет.Контрагент','Клиент = Неопределено','ПоступлениеДенежныхСредств'),
            ('wrong-owner','Финансы/ПоступлениеДенежныхСредств.Объект.xbsl','Сумма = Счет.Сумма',
             'знч Wrong = новый Продажи::Заказ.Ссылка(Идентификатор = Основание.Идентификатор).ЗагрузитьОбъект()\n    Сумма = Wrong.Товары[0].Сумма','ПоступлениеДенежныхСредств'),
            ('skip','Товары/Отгрузка.xbsl','Документ.Товары.Добавить(НоваяСтрока)','// skipped','ПеренестиТабличнуюЧасть'),
            ('duplicate','Товары/Отгрузка.xbsl','Документ.Товары.Добавить(НоваяСтрока)','Документ.Товары.Добавить(НоваяСтрока)\n        Документ.Товары.Добавить(НоваяСтрока)','ПеренестиТабличнуюЧасть'),
            ('clear','Товары/Отгрузка.xbsl','для Элемент из ДляПереноса','Документ.Товары.Очистить()\n    для Элемент из ДляПереноса','ПеренестиТабличнуюЧасть'),
            ('input','Товары/Отгрузка.xbsl','Документ.Товары.Добавить(НоваяСтрока)','Документ.Товары.Добавить(НоваяСтрока)\n        Элемент.Сумма = 99','ПеренестиТабличнуюЧасть'),
            ('return','Товары/Отгрузка.xbsl','возврат НовыйДокумент','возврат новый Отгрузка.Объект()','СоздатьЧасть'),
            ('reorder','Товары/Отгрузка.xbsl','Документ.Товары.Добавить(НоваяСтрока)','Документ.Товары.Вставить(0, НоваяСтрока)','ПеренестиТабличнуюЧасть'),
            ('write','Финансы/РасходДенежныхСредств.Объект.xbsl','этот.Основание = Основание','этот.Основание = Основание\n    этот.Записать()','РасходДенежныхСредств')]
        mutations.append(('movements','Товары/Отгрузка.xbsl','Документ.Товары.Добавить(НоваяСтрока)',
                          'Документ.Товары.Добавить(НоваяСтрока)\n        знч Set = новый РегистрТовары.НаборЗаписей()\n        Set.Фильтр.Установить(Регистратор = Документ.Ссылка)\n        Set.ДобавитьЗапись(Период = Документ.Дата, ВидЗаписи = ВидЗаписиРегистраНакопления.Расход, Номенклатура = Элемент.Номенклатура, Склад = Документ.Склад, Количество = Элемент.Количество)\n        Set.Записать()', 'ПеренестиТабличнуюЧасть'))
        for field in ['Номенклатура','Количество','Цена','Сумма']:
            mutations.append(('field-'+field,'Товары/Отгрузка.xbsl',field+' = Элемент.'+field,field+' = '+('Неопределено' if field=='Номенклатура' else '99'),'ПеренестиТабличнуюЧасть'))
        with open_project(ARCHIVE) as original:
            for tag,path,old,new,name in mutations:
                if tags is not None and tag not in tags:continue
                with self.subTest(tag=tag),TemporaryDirectory() as d:
                    root=Path(d)/'source';shutil.copytree(original,root);p=root/path;source=p.read_text();self.assertIn(old,source);p.write_text(source.replace(old,new))
                    for backend in (['memory','postgres'] if tag in {'write','movements'} else ['memory']):
                        c=next(c for c in checks() if c['id']==name+'_many_'+backend)
                        result=assess(root,[c],OUT/'mutations'/(tag+'-'+backend))
                        self.assertEqual(result['checks'][0]['status'],'FAIL',result)

    def test_two_fresh_batches_isolate_equal_uuids_and_mutated_source(self):
        with TemporaryDirectory() as d:
            temp=Path(d);inputs=OUT/'batch-input';inputs.mkdir(parents=True,exist_ok=True)
            cases=[portable_checks('ledgers',b)[i] for b in ['memory','postgres'] for i in [0,1]]
            for i,c in enumerate(cases):c['id']=str(i)
            assignment=inputs/'assignment.yaml';assignment.write_text(yaml.safe_dump({'name':'Creation batch','checks':cases},allow_unicode=True))
            submissions=[]
            for i in range(3):
                root=inputs/str(i)
                if root.exists():shutil.rmtree(root)
                shutil.copytree(CORPUS/'ledgers',root)
                if i==2:
                    p=root/'Alpha/Ledger.Объект.xbsl';p.write_text(p.read_text().replace('Label = Source.Label','Label = "wrong"'))
                submissions.append({'studentId':'creation-'+str(i),'project':str(root)})
            manifest=OUT/'batch-manifest.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'creation37','submissions':submissions})
            for tag in ['batch-first','batch-fresh']:
                output=OUT/tag
                if output.exists():shutil.rmtree(output)
                p=subprocess.run([str(REPO/'bin/element-test'),'batch','--manifest',str(manifest),'--output',str(output),'--workers','1','--integration'],capture_output=True,text=True,timeout=900,env={**os.environ,'ELEMENT_TEST_CACHE_DIR':str(temp/'cache')})
                self.assertEqual(p.returncode,1,p.stderr)
                result=json.loads((output/'batch-result.json').read_text());self.assertEqual((result['counts']['passed'],result['counts']['failed']),(2,1),result)
                self.assertFalse(any(json.loads(line).get('cacheHit',False) for line in (output/'events.jsonl').read_text().splitlines()))


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','Creation PostgreSQL opt-in')
class CreationSqlTest(unittest.TestCase):
    def test_infrastructure_failure_remains_error(self):
        with open_project(ARCHIVE) as original, TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(original,root)
            source=root/'Финансы/СчетНаОплату.Объект.xbsl'
            source.write_text(source.read_text().replace('Контрагент = ЗаказКлиента.Клиент','Контрагент = ЗаказКлиента.Клиент\n    этот.Записать()'))
            c=handler('СчетНаОплату','sql-failure',ROWS,'postgres');out=OUT/'sql-failure';out.mkdir(parents=True,exist_ok=True)
            fact=run_integration(c,analyze(root),out,root=root,enabled=True,inject_failure=True)
            self.assertEqual(fact['status'],'ERROR',fact);self.assertEqual(fact['reasonCode'],'execution_error')
