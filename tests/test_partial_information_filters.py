"""Task 53: canonical history, predicate masks, executor and independent SQL audit."""
import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure
from element_test.storage import PostgresSession
from element_test.yaml_io import InputError, InvalidTestError
from partial_filter_fixtures import *
from test_record_sets import normalized, grade

EVIDENCE=REPO/'result/partial-information-filters'


def setUpModule():
    EVIDENCE.mkdir(parents=True, exist_ok=True)


class PartialPlanTest(unittest.TestCase):
    def test_unsupported_forms_and_subordinated_register_boundary(self):
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'ordinary',root)
            path=root/'Ware/Flow.xbsl';original=path.read_text()
            for argument,message in [('"X"','полный позиционный'),
                    ('Product = "X", "A"','Смешанная'),
                    ('Product = "X", Product = "X"','Повторное имя')]:
                path.write_text(original.replace('Установить(Product = "X")','Установить('+argument+')',1))
                with self.assertRaisesRegex(InputError,message):plan_execution(root,analyze(root),check())
            path.write_text(original)
            metadata=root/'Ware/Ledger.yaml';metadata.write_text(metadata.read_text()+'РежимЗаписи: ПодчинениеРегистратору\n')
            with self.assertRaisesRegex(InputError,'Подчинённый регистр'):
                plan_execution(root,analyze(root),check())

    def test_mask_typed_values_source_hash_and_boundaries(self):
        root=CORPUS/'ordinary';p=plan_execution(root,analyze(root),check())
        f=next(r for r in p.record_sets if r['operation']=='Установить')
        self.assertEqual(f['filterMask'],['Product']);self.assertEqual(f['owner'],'Ware::Ledger')
        self.assertEqual(f['filterValues'][0]['type'],'Строка')
        self.assertEqual(f['filterValues'][0]['expression'],'"X"');self.assertEqual(len(f['sourceHash']),64)
        self.assertEqual((root/f['sourceFile']).read_text()[f['start']:f['end']],'R.Фильтр.Установить(Product = "X")')
        write_json(EVIDENCE/'plan.json',p.to_dict())
        with TemporaryDirectory() as d:
            prepare_script(root,analyze(root),check(),Path(d))
            self.assertIn('УстановитьПодмножество1(Product = "X")',(Path(d)/'test.sbsl').read_text())

    def test_empty_unknown_and_teacher_filter_contract(self):
        root=CORPUS/'ordinary'
        for method, message in [('UnknownFilter','Неизвестное измерение'),('WrongType','Неверный тип')]:
            with self.assertRaisesRegex(InputError,message):plan_execution(root,analyze(root),check(method))
        with TemporaryDirectory() as d:
            for filt in ({'Unknown':'X'},{'Product':123}):
                c=check();c['storage']['initialRegisters'][0]['filter']=filt
                with self.assertRaises(InvalidTestError):prepare_script(root,analyze(root),c,Path(d))
            c=check();c['storage']['initialRegisters']=[{'type':'Ware::Ledger','filter':{'Product':'X'},'rows':[rows()[0],rows()[1],rows()[3]]}]
            prepare_script(root,analyze(root),c,Path(d))
            c['storage']['initialRegisters'].append(copy.deepcopy(check()['storage']['initialRegisters'][0]))
            with self.assertRaisesRegex(InvalidTestError,'Неуникальные ключи'):
                prepare_script(root,analyze(root),c,Path(d))

    def test_exact_final_source_preparation(self):
        with open_project(REPO/'Dvizhok-final.xdump') as root, TemporaryDirectory() as d:
            c=copy.deepcopy(FINAL_RECEIPT_CONFIG)
            p=plan_execution(root,analyze(root),c)
            self.assertTrue(any(r.get('filterMask')==['Номенклатура'] for r in p.record_sets))
            write_json(EVIDENCE/'plan-final-receipt-legacy-profile.json',p.to_dict())
            with self.assertRaisesRegex(InputError,'Циклическая зависимость'):
                prepare_script(root,analyze(root),c,Path(d))
            c['storage']['lifecycle']=True
            p=plan_execution(root,analyze(root),c)
            prepare_script(root,analyze(root),c,Path(d))
            write_json(EVIDENCE/'plan-final-receipt.json',p.to_dict())
            write_json(EVIDENCE/'source-preflight-053.json',[
                {'config':FINAL_RECEIPT_CONFIG,'sourceHash':analyze(root)['sourceHash'],'plan':'ready',
                 'generation':'blocked','reason':'Циклическая зависимость SBSL-модулей: ПоступлениеТоваров',
                 'execution':'not-checked','businessAssessment':'not-checked'},
                {'config':c,'sourceHash':analyze(root)['sourceHash'],'plan':'ready','generation':'ready',
                 'execution':'not-checked','businessAssessment':'not-checked'}])


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Docker is opt-in')
class PartialDockerTest(unittest.TestCase):
    def test_compatibility_93_current(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';shutil.copytree(CORPUS/'ordinary',root)
            p=root/'Проект.yaml';p.write_text(p.read_text().replace('9.0','9.3'))
            c=check('Append');c['sequence']=[{'method':'Append','args':[]},{'method':'Latest','args':[]},{'method':'Refilter','args':[]}]
            r=run_pure(root,analyze(root),c,base);write_json(EVIDENCE/'compatibility-93-current.json',r)
            self.assertEqual(r['status'],'EXECUTED',r)
            self.assertEqual(r['actual']['result'],{'actions':[None,99,30]})
            self.assertEqual((r['runtime']['sourceCompatibilityVersion'],r['runtime']['executionCompatibilityVersion']),('9.3','current'))
            self.assertEqual(normalized({'storage':r['actual']['storage']}),normalized({'storage':storage_expected(rows()+[replacement()[-1]])}))

    def test_public_test_run_batch_grades_and_isolation(self):
        from element_test.batch import run_batch
        with TemporaryDirectory() as d:
            base=Path(d);root=base/'source';shutil.copytree(CORPUS/'ordinary',root)
            expected={'result':{'count':3,'sum':70},'storage':storage_expected()}
            good=dict(id='good',type='runtime',points=2,comparison='record-sets-unordered',**check(),expected=expected)
            wrong=copy.deepcopy(good);wrong['id']='wrong';wrong['expected']['result']['sum']=71
            unavailable=copy.deepcopy(good);unavailable.update(id='invalid-filter',points=3)
            unavailable['target']['method']='UnknownFilter'
            assignment=base/'assignment.yaml'
            class Dumper(yaml.SafeDumper):
                def ignore_aliases(self,data):return True
            assignment.write_text(yaml.dump({'name':'Task 53 independent grades','checks':[good,wrong,unavailable]},Dumper=Dumper,allow_unicode=True))
            for command in ('test','run'):
                out=base/command
                r=subprocess.run([str(REPO/'bin/element-test'),command,'--project',str(root),'--assignment',str(assignment),'--output',str(out)],capture_output=True,text=True,timeout=120)
                self.assertEqual(r.returncode,1,r.stderr)
                result=json.loads((out/'result.json').read_text());write_json(EVIDENCE/f'public-{command}.json',result)
                self.assertEqual([x['status'] for x in result['checks']],['PASS','FAIL','UNSUPPORTED'])
                self.assertEqual((result['score'],result['maxScore'],result['unavailablePoints']),(2,4,3))
            second=base/'second';shutil.copytree(root,second)
            path=second/'Ware/Flow.xbsl';path.write_text(path.read_text().replace('Product = "X"','Product = "Y"'))
            manifest=base/'batch.json';manifest.write_text(json.dumps({'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'partial53',
                'submissions':[{'studentId':'one','project':str(root)},{'studentId':'two','project':str(second)}]}))
            self.assertEqual(run_batch(manifest,base/'batch',workers=2),1)
            batch=json.loads((base/'batch/batch-result.json').read_text());write_json(EVIDENCE/'public-batch.json',batch)
            for item,statuses in zip(batch['submissions'],(['PASS','FAIL','UNSUPPORTED'],['FAIL','FAIL','UNSUPPORTED'])):
                result=json.loads((base/'batch'/item['result']).read_text());write_json(EVIDENCE/('public-batch-'+item['studentId']+'.json'),result)
                self.assertEqual([x['status'] for x in result['checks']],statuses)
                self.assertEqual(result['unavailablePoints'],3)

    def test_required_scenarios_and_portability(self):
        with TemporaryDirectory() as d:
            temp=Path(d)
            for namespace,name,folder in [('Ware','Ledger','ordinary'),('Запасы','История','renamed')]:
                root=CORPUS/folder;model=analyze(root)
                for case,c,returned,data in cases():
                    c['target']['namespace']=namespace
                    c['storage']['registers']=[s.replace('Ware::Ledger',namespace+'::'+name).replace('Ware::Mirror',namespace+'::Mirror') for s in c['storage']['registers']]
                    for s in c['storage']['initialRegisters']:
                        s['type']=s['type'].replace('Ware::Ledger',namespace+'::'+name).replace('Ware::Mirror',namespace+'::Mirror')
                    r=run_pure(root,model,c,temp);write_json(EVIDENCE/f'{folder}-{case}.json',r)
                    self.assertEqual(r['status'],'EXECUTED',r)
                    expected=storage_expected(data,namespace,name,case=='Persist')
                    self.assertEqual(normalized({'storage':r['actual']['storage']}),normalized({'storage':expected}),case)
                    if returned is not None:
                        self.assertEqual(r['actual']['result'],returned,case)
                        expect={'result':returned,'storage':expected}
                        self.assertEqual(grade(r['actual'],expect,temp),'PASS')
                        wrong=copy.deepcopy(expect);wrong['storage'][0]['value'].append({'wrong':'teacher-answer'})
                        self.assertEqual(grade(r['actual'],wrong,temp),'FAIL')
                    else:
                        self.assertIsNotNone(r['actual']['result']['exception'],case)
            for method in ('UnknownFilter','WrongType'):
                r=run_pure(CORPUS/'ordinary',analyze(CORPUS/'ordinary'),check(method),temp)
                write_json(EVIDENCE/(method+'.json'),r)
                self.assertEqual(r['status'],'UNSUPPORTED',r)

    def test_partial_teacher_seed_and_backend_unavailable(self):
        root=CORPUS/'ordinary'
        with TemporaryDirectory() as d:
            c=check();c['storage']['initialRegisters']=[{'type':'Ware::Ledger','filter':{'Product':'X'},'rows':[rows()[0],rows()[1],rows()[3]]}]
            r=run_pure(root,analyze(root),c,Path(d));self.assertEqual(r['status'],'EXECUTED',r)
            self.assertEqual(r['actual']['result'],{'count':3,'sum':70})
            self.assertEqual(len(r['actual']['storage']),3)
            with patch('element_test.runtime.shutil.which',return_value=None):
                r=run_pure(root,analyze(root),check(),Path(d))
            self.assertEqual((r['status'],r['reasonCode']),('UNSUPPORTED','backend_unavailable'))


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_INTEGRATION_TESTS')=='1','PostgreSQL is opt-in')
class PartialSqlTest(unittest.TestCase):
    def test_compatibility_93_current_sql(self):
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            base=Path(d);root=base/'source';shutil.copytree(CORPUS/'ordinary',root)
            p=root/'Проект.yaml';p.write_text(p.read_text().replace('9.0','9.3'))
            c=check('Append',sql=True);c['sequence']=[{'method':'Append','args':[]},{'method':'Latest','args':[]},{'method':'Refilter','args':[]}]
            r=run_integration(c,analyze(root),base,root=root,enabled=True);write_json(EVIDENCE/'sql-compatibility-93-current.json',r)
            self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
            self.assertEqual(r['actual']['result'],{'actions':[None,99,30]})
            self.assertEqual((r['runtime']['sourceCompatibilityVersion'],r['runtime']['executionCompatibilityVersion']),('9.3','current'))
            self.assertEqual(normalized({'storage':r['actual']['storage']}),normalized({'storage':storage_expected(rows()+[replacement()[-1]])}))

    def test_required_scenarios_sql_audit_and_portability(self):
        with TemporaryDirectory() as d,patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            temp=Path(d)
            for namespace,name,folder in [('Ware','Ledger','ordinary'),('Запасы','История','renamed')]:
                root=CORPUS/folder;model=analyze(root)
                for case,c,returned,data in cases():
                    c['target']['namespace']=namespace;c['storage']['backend']='postgres'
                    c['integration']={'backend':'postgres','operation':'metadata-storage'}
                    c['storage']['registers']=[s.replace('Ware::Ledger',namespace+'::'+name).replace('Ware::Mirror',namespace+'::Mirror') for s in c['storage']['registers']]
                    for s in c['storage']['initialRegisters']:
                        s['type']=s['type'].replace('Ware::Ledger',namespace+'::'+name).replace('Ware::Mirror',namespace+'::Mirror')
                    sql_reads=[]
                    original_audit=PostgresSession.audit
                    def independent_audit(session, database, docker):
                        value=original_audit(session,database,docker);sql_reads.append(copy.deepcopy(value));return value
                    with patch.object(PostgresSession,'audit',independent_audit):
                        r=run_integration(c,model,temp,root=root,enabled=True)
                    write_json(EVIDENCE/f'sql-{folder}-{case}.json',r)
                    self.assertEqual(r['status'],'EXECUTED',r);self.assertTrue(r['integration']['cleanup'])
                    expected=storage_expected(data,namespace,name,case=='Persist')
                    self.assertEqual(normalized({'storage':sql_reads[-1]}),normalized({'storage':expected}),case)
                    self.assertEqual(normalized({'storage':r['actual']['storage']}),normalized({'storage':expected}),case)
                    if returned is not None:self.assertEqual(r['actual']['result'],returned,case)
                    else:self.assertIsNotNone(r['actual']['result']['exception'])
            c=check('Replace',[False,False],sql=True);root=CORPUS/'ordinary'
            for failure in (True,False):
                r=run_integration(c,analyze(root),temp,root=root,enabled=True,inject_failure=failure)
                write_json(EVIDENCE/f'sql-publication-{failure}.json',r)
                self.assertTrue(r['integration']['cleanup'])
                self.assertEqual(r['status'],'ERROR' if failure else 'EXECUTED',r)
                expected=storage_expected(rows() if failure else replacement())
                audit=r.get('storageEvidence',r.get('actual',{}).get('storage'))
                self.assertEqual(normalized({'storage':audit}),normalized({'storage':expected}))
