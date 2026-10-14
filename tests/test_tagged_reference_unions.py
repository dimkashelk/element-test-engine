"""Task 54: nominal round trips, detached snapshots and independent SQL audit."""
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

import yaml
from element_test.bridge import write_json
from element_test.execution_plan import plan_execution
from element_test.integration import run_integration
from element_test.loader import open_project
from element_test.model import analyze
from element_test.runtime import prepare_script, run_pure
from element_test.storage import PostgresSession
from element_test.yaml_io import InputError, InvalidTestError
from tagged_reference_fixtures import *
from test_record_sets import grade


def normalize(storage):
    out=copy.deepcopy(storage)
    for row in out:
        if row['type'].endswith('::Flow'):
            row['id']=json.dumps(json.loads(row['id']),sort_keys=True,separators=(',',':'))
    return sorted(out,key=lambda r:(r['type'],r['id']))


def source(base,folder,id_type):
    root=base/'source';shutil.copytree(CORPUS/folder,root)
    if id_type=='Ууид':
        ns,*_=names(folder);p=root/ns/'Main.xbsl'
        s=p.read_text()
        for id in [CARD_ID,ID]:
            s=s.replace('ПолучитьСсылку("'+id+'")','ПолучитьСсылку(новый Ууид("'+id+'"))')
        s=s.replace('= "33333333-3333-4333-8333-333333333333"', '= новый Ууид("33333333-3333-4333-8333-333333333333")')
        p.write_text(s)
    return root


class ReferenceUnionPlanTest(unittest.TestCase):
    def test_contract_resolved_full_owners_no_expected_and_invalid_tags(self):
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        for folder in ['ordinary','renamed']:
            root=CORPUS/folder;model=analyze(root);c=config(folder)
            c['expected']={'SECRET_ORACLE':17893}
            p=plan_execution(root,model,c);self.assertNotIn('SECRET_ORACLE',p.to_json())
            contract=p.to_dict()['referenceCodec'];self.assertEqual(contract['contract'],'tagged-reference-union-v1')
            self.assertTrue(any(len(f['variants'])==2 and f['nullable'] for f in contract['fields']))
            write_json(EVIDENCE/(folder+'-plan.json'),p.to_dict())
            ns,other,card,item,field,table=names(folder)
            bad=[{'Идентификатор':ID}, {'type':'Missing::Item.Ссылка','value':{'Идентификатор':ID}},
                 {'type':ns+'::'+card+'.Ссылка','value':{'Идентификатор':ID}},
                 {'type':ns+'::'+item+'.Ссылка','value':{'Идентификатор':7}}]
            with TemporaryDirectory() as d:
                for v in bad:
                    c=config(folder);c['args'][0]=v
                    with self.subTest(folder=folder,value=v),self.assertRaises(InputError):
                        prepare_script(root,model,c,Path(d))
                c=config(folder,id_type='Ууид');c['args'][0]=ref(ns+'::'+item,'invalid-uuid')
                with self.assertRaisesRegex(InputError,'Ууид требует'):prepare_script(root,model,c,Path(d))

    def test_metadata_imports_and_union_order_do_not_guess_identity(self):
        from element_test.generated_types import ProjectTypes
        from element_test.reference_codec import encode
        with TemporaryDirectory() as d:
            root=Path(d)/'source';shutil.copytree(CORPUS/'ordinary',root)
            for ns, name in [('External','Left'),('Remote','Right')]:
                (root/ns).mkdir()
                (root/ns/(name+'.yaml')).write_text(yaml.safe_dump({'ВидЭлемента':'Справочник','Имя':name,'ОбластьВидимости':'ВПроекте','Реквизиты':[]},allow_unicode=True))
            p=root/'Data/Card.yaml';data=yaml.safe_load(p.read_text())
            data['Импорт']=['External','Remote'];data['Реквизиты'][0]['Тип']='Right.Ссылка|Left.Ссылка|?'
            p.write_text(yaml.safe_dump(data,allow_unicode=True))
            c=ProjectTypes(analyze(root),'Data');c.rename_collisions=True;c.require('Card.Объект')
            typ=next(f['Тип'] for f in c.fields['Card.Объект'] if f['Имя']=='Choice')
            encoded=encode(c,'Value',typ)
            self.assertIn('External::Left.Ссылка',encoded);self.assertIn('Remote::Right.Ссылка',encoded)
            self.assertNotIn('Data::Left',encoded)
            c.literal(ref('External::Left'),typ)
            c.namespace=''
            with self.assertRaises(InputError):c.literal({'Идентификатор':ID},typ)

    def test_mixed_reference_scalar_union_is_explicitly_unavailable(self):
        with TemporaryDirectory() as d:
            base=Path(d);root=source(base,'ordinary','Строка')
            p=root/'Data/Card.yaml';data=yaml.safe_load(p.read_text())
            data['Реквизиты'][0]['Тип']='Data::Item.Ссылка|Other::Item.Ссылка|Строка|?'
            p.write_text(yaml.safe_dump(data,allow_unicode=True))
            with self.assertRaisesRegex(InputError,'Смешанный union нескольких ссылок'):
                prepare_script(root,analyze(root),config(),base)

    def test_final_declarative_money_source_preparation(self):
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        with open_project(REPO/'Dvizhok-final.xdump') as root, TemporaryDirectory() as d:
            model=analyze(root);before=model['sourceHash']
            for backend in ['memory','postgres']:
                c={'interface':{'schemaVersion':1,'form':{'name':'ПоступлениеДенежныхСредствФормаОбъекта','namespace':'Финансы'}},
                   'context':{'Объект':{}},'lifecycle':{'isNew':True},'storage':{'lifecycle':True,'idType':'Ууид','backend':backend,
                        'registers':['Финансы::РегистрДенежныеСредства']},'formEffects':{'write':True},
                   'sequence':[{'writeInterface':True}], 'observe':['Объект.Основание']}
                p=plan_execution(root,model,c);write_json(EVIDENCE/('final-'+backend+'-plan.json'),p.to_dict())
                out=Path(d)/backend;out.mkdir();prepare_script(root,model,c,out)
                self.assertEqual(analyze(root)['sourceHash'],before)
                write_json(EVIDENCE/('final-'+backend+'-preflight.json'),{'plan':'ready','generation':'ready',
                    'sourceHash':before,'sourceCompatibility':model['compatibilityVersion'],'execution':'not-checked','businessAcceptanceTask':56})


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','opt-in isolated runtime')
class ReferenceUnionRuntimeTest(unittest.TestCase):
    def execute(self,root,c,out,sql):
        if sql:
            reads=[]; original=PostgresSession.audit
            def audit(session,database,docker):
                r=original(session,database,docker);reads.append(copy.deepcopy(r));return r
            with patch.object(PostgresSession,'audit',audit):r=run_integration(c,analyze(root),out,root=root,enabled=True)
            self.assertTrue(r.get('integration',{}).get('cleanup'),r)
            self.sql_audit=reads[-1] if reads else None
            r['independentSqlAudit']=copy.deepcopy(self.sql_audit)
        else:r=run_pure(root,analyze(root),c,out)
        self.assertEqual(r['status'],'EXECUTED',r)
        return r

    def test_transitions_tables_snapshots_identity_query_loading_rollback_and_profiles(self):
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        with patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            for sql in [False,True]:
                for folder in ['ordinary','renamed']:
                    for id_type in ['Строка','Ууид']:
                        with self.subTest(sql=sql,folder=folder,id_type=id_type), TemporaryDirectory() as d:
                            base=Path(d);root=source(base,folder,id_type);c=transition(folder,sql=sql,id_type=id_type)
                            if id_type=='Ууид':c['runtimeProfile']='9.3'
                            r=self.execute(root,c,base,sql);write_json(EVIDENCE/f'{folder}-{sql}-{id_type}-transition.json',r)
                            a,b,rows=values(folder);actions=r['actual']['result']['actions']
                            enters=[t['value'] for t in r['trace'] if t['event']=='enter' and t['symbol'].endswith('::Persist')]
                            self.assertEqual([t['Value'] for t in enters],[a,b,None,a,b])
                            self.assertEqual(enters[0][names(folder)[5]],rows)
                            leaves=[t['value'] for t in r['trace'] if t['event']=='exit' and t['symbol'].endswith('::Persist')]
                            self.assertEqual([t[names(folder)[4]] for t in leaves],[a,b,None,a])
                            for index,(v,previous) in enumerate([(a,'A'),(b,'A'),(None,'B'),(a,'NULL')]):
                                self.assertEqual(actions[index]['result']['result'],card_value(v,previous,rows,folder))
                                self.assertIsNone(actions[index]['result']['exception'])
                                self.assertEqual(actions[index]['args'][1],rows)
                            self.assertEqual(actions[5]['result']['result'],card_value(a,'NULL',rows,folder))
                            self.assertIn('reject-after',actions[4]['result']['exception']['message'])
                            self.assertEqual(actions[6]['result']['result'],[{'Ref':a}])
                            self.assertEqual([s['result']['result'] for s in actions[7:]],['owner-A','owner-B',True,False,False,True,'owner-A',True,'updated-A'])
                            expected=state(a,'NULL',rows,folder)
                            next(s['value'] for s in expected if s['type']==names(folder)[0]+'::'+names(folder)[3])['Label']='updated-A'
                            self.assertEqual(normalize(r['actual']['storage']),normalize(expected))
                            if sql:self.assertEqual(normalize(self.sql_audit),normalize(expected))
                            self.assertEqual(grade(r['actual'],r['actual'],base),'PASS')
                            wrong=copy.deepcopy(r['actual']);wrong['result']['actions'][0]['result']['result'][names(folder)[4]]=b
                            self.assertEqual(grade(r['actual'],wrong,base),'FAIL')
                            if id_type=='Ууид':
                                self.assertEqual(r['runtime']['sourceCompatibilityVersion'],'9.0')
                                self.assertEqual(r['runtime']['executionCompatibilityVersion'],'current')

    def test_nullable_empty_required_and_report_formula(self):
        with patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            for sql in [False,True]:
                with self.subTest(sql=sql),TemporaryDirectory() as d:
                    base=Path(d);root=source(base,'ordinary','Ууид');a,b,rows=values();empty=ref('Data::Item',ZERO)
                    c=config(id_type='Ууид',sql=sql);c['sequence']=[{'method':'Persist','args':[v,[],False]} for v in [empty,None]]
                    r=self.execute(root,c,base,sql);write_json(EVIDENCE/f'empty-null-{sql}.json',r)
                    self.assertEqual([s['Choice'] for s in r['actual']['result']['actions']],[empty,None])
                    p=root/'Data/Card.yaml';data=yaml.safe_load(p.read_text());data['Реквизиты'][0]['НезаполненноеЗначение']='ЗапретитьВсегда';p.write_text(yaml.safe_dump(data,allow_unicode=True))
                    c['captureException']=True
                    r=self.execute(root,c,base,sql);write_json(EVIDENCE/f'required-{sql}.json',r)
                    for action in r['actual']['result']['actions']:self.assertIn('required',action['exception']['message'])
                    self.assertFalse(any(s['type']=='Data::Card' for s in r['actual']['storage']))
                    seed=card_value(b,'B',rows);c=config(id_type='Ууид',sql=sql)
                    c['storage']['initial'].append({'type':'Data::Card','value':seed})
                    c.pop('target');c.pop('args');c['report']={'schemaVersion':1,'report':{'name':'References','namespace':'Data'}}
                    r=self.execute(root,c,base,sql);write_json(EVIDENCE/f'report-{sql}.json',r)
                    self.assertEqual(r['actual']['result']['actions'][0]['values'],[{'ref':b,'label':'owner-B'}])
                    self.assertEqual(r['actual']['result']['actions'][0]['data'],[{'Ref':b}])

    def test_catalog_form_snapshots_and_register_union_resource(self):
        # A catalog uses the same codec as a document; the movement resource
        # retains its own union tag while the registrar keeps its legacy shape.
        with patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            for sql in [False,True]:
                with self.subTest(sql=sql),TemporaryDirectory() as d:
                    base=Path(d);root=source(base,'ordinary','Ууид');a,b,rows=values()
                    p=root/'Data/Card.yaml';m=yaml.safe_load(p.read_text());m['ВидЭлемента']='Справочник';p.write_text(yaml.safe_dump(m,allow_unicode=True))
                    p=root/'Data/Flow.yaml';m=yaml.safe_load(p.read_text());m['Ресурсы'].append({'Имя':'Link','Тип':'Data::Item.Ссылка|Other::Item.Ссылка|?'})
                    p.write_text(yaml.safe_dump(m,allow_unicode=True))
                    p=root/'Data/Card.Объект.xbsl';p.write_text(p.read_text().replace('Key = "effect", Count =','Key = "effect", Link = Choice, Count ='))
                    c=config(id_type='Ууид',sql=sql);c.pop('target');c.pop('args')
                    c.update(interface={'schemaVersion':1,'form':{'name':'Panel','namespace':'Data'}},
                        context={'Объект':{'Ссылка':{'Идентификатор':CARD_ID},'Choice':b,'Rows':rows,'Scalar':'text'}},
                        lifecycle={'isNew':True},formEffects={'write':True},observe=['Объект'],sequence=[{'writeInterface':True}])
                    r=self.execute(root,c,base,sql);write_json(EVIDENCE/f'catalog-form-{sql}.json',r)
                    expected=card_value(b,'B',rows)
                    self.assertEqual(r['actual']['result']['actions'][0]['observation']['state']['Объект'],expected)
                    saved=next(v['value'] for v in r['actual']['storage'] if v['type']=='Data::Card');self.assertEqual(saved,expected)
                    movement=next(v['value'] for v in r['actual']['storage'] if v['type']=='Data::Flow');self.assertEqual(movement[0]['Link'],b)
                    if sql:
                        self.assertEqual(next(v['value'] for v in self.sql_audit if v['type']=='Data::Card'),expected)
                        history=r['actual']['sqlSnapshots'];self.assertTrue(history)
                        last=history[-1]['objects'] if isinstance(history[-1],dict) and 'objects' in history[-1] else history[-1]
                        self.assertTrue(any(v['type']=='Data::Card' and v['value']==expected for v in last),history)
                    (root/'Data/References.xbql').write_text('ВЫБРАТЬ Link КАК Ref ИЗ Flow')
                    c=config(id_type='Ууид',sql=sql);c.pop('target');c.pop('args')
                    c['storage']['initialRegisters']=[{'type':'Data::Flow','filter':{'Регистратор':{'Идентификатор':CARD_ID}},'rows':[{**movement[0],'Link':b}]}]
                    c['report']={'schemaVersion':1,'report':{'name':'References','namespace':'Data'}}
                    r=self.execute(root,c,base,sql);write_json(EVIDENCE/f'register-union-report-{sql}.json',r)
                    self.assertEqual(r['actual']['result']['actions'][0]['values'],[{'ref':b,'label':'owner-B'}])

    def test_nonnullable_union_requires_explicit_constructor_and_round_trips(self):
        with patch.dict(os.environ,{'ELEMENT_TEST_INTEGRATION_PASSWORD':secrets.token_hex(24)}):
            for sql in [False,True]:
                with self.subTest(sql=sql),TemporaryDirectory() as d:
                    base=Path(d);root=source(base,'ordinary','Строка');a,b,rows=values()
                    p=root/'Data/Card.yaml';m=yaml.safe_load(p.read_text());m['Реквизиты'][0]['Тип']=m['Реквизиты'][0]['Тип'].removesuffix('|?')
                    p.write_text(yaml.safe_dump(m,allow_unicode=True))
                    p=root/'Data/Main.xbsl';p.write_text(p.read_text().replace('Value: Data::Item.Ссылка|Other::Item.Ссылка|?, Rows:', 'Value: Data::Item.Ссылка|Other::Item.Ссылка, Rows:'))
                    c=config(sql=sql);c['sequence']=[{'method':'Persist','args':[v,rows,False]} for v in [a,b]]
                    r=self.execute(root,c,base,sql);write_json(EVIDENCE/f'nonnullable-{sql}.json',r)
                    self.assertEqual([x['Choice'] for x in r['actual']['result']['actions']],[a,b])
                    c=config(sql=sql);c['args'][0]=None
                    with self.assertRaises(InputError):prepare_script(root,analyze(root),c,base)


@unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','opt-in public acceptance')
class ReferenceUnionPublicTest(unittest.TestCase):
    def test_test_run_batch_independent_expected_mutation_and_invalid_tag(self):
        assignment=REPO/'assignments/tagged-reference-unions/assignment.yaml';root=CORPUS/'ordinary'
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        for command in ['test','run']:
            out=EVIDENCE/('public-'+command)
            r=subprocess.run([str(REPO/'bin/element-test'),command,'--project',str(root),'--assignment',str(assignment),'--output',str(out)],capture_output=True,text=True,timeout=120)
            self.assertEqual(r.returncode,1,r.stderr)
            fact=json.loads((out/'result.json').read_text());write_json(EVIDENCE/('public-'+command+'.json'),fact)
            self.assertEqual([c['status'] for c in fact['checks']],['PASS','FAIL','UNSUPPORTED'],fact)
            self.assertEqual((fact['score'],fact['maxScore'],fact['unavailablePoints']),(2,4,3))
        with TemporaryDirectory() as d:
            base=Path(d);mutant=base/'mutant';shutil.copytree(root,mutant)
            p=mutant/'Data/Card.Объект.xbsl';p.write_text(p.read_text().replace('? 1 : 2','? 99 : 2'))
            manifest=base/'batch.json';write_json(manifest,{'schemaVersion':'1.0','assignment':str(assignment),'assignmentId':'reference54',
                'submissions':[{'studentId':'one','project':str(root)},{'studentId':'two','project':str(mutant)}]})
            from element_test.batch import run_batch
            out=base/'batch';self.assertEqual(run_batch(manifest,out,workers=2),1)
            shutil.copytree(out,EVIDENCE/'public-batch',dirs_exist_ok=True)
            fact=json.loads((out/'batch-result.json').read_text());write_json(EVIDENCE/'public-batch.json',fact)
            for row,statuses in zip(fact['submissions'],[['PASS','FAIL','UNSUPPORTED'],['FAIL','FAIL','UNSUPPORTED']]):
                result=json.loads((out/row['result']).read_text())
                self.assertEqual([c['status'] for c in result['checks']],statuses,result)
                write_json(EVIDENCE/('public-batch-'+row['studentId']+'.json'),result)
            c=config();c['args'][0]={'type':'Missing.Ссылка','value':{'Идентификатор':ID}}
            result=run_pure(root,analyze(root),c,base);self.assertEqual((result['status'],result['reasonCode']),('UNSUPPORTED','invalid_test'),result)
            write_json(EVIDENCE/'invalid-tag.json',result)
