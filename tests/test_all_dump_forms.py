"""Every supplied UI declaration has an individually reported nightly test."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from all_dump_forms import (CORPUS, MANIFEST, REPO, checks_for, coverage_summary,
                            find_form, grade_form, load_fixture)
from element_test.bridge import write_json
from element_test.form_requirements import normalize_requirements
from element_test.model import analyze
from element_test.runtime import prepare_script
from element_test.xdump import extract_project


class FormCorpusTest(unittest.TestCase):
    def test_new_binding_plans_use_frozen_typed_scenarios(self):
        planned=0
        for project in MANIFEST['projects']:
            if project['dump']=='Dvizhok.xdump':continue
            with TemporaryDirectory() as directory:
                root=next(root for root in extract_project(REPO/project['dump'],Path(directory))
                          if root.name==project['project'].split('::')[-1])
                model=analyze(root)
                for file in project['forms']:
                    for check in checks_for(load_fixture(file)):
                        output=Path(directory)/'plans'/str(planned);output.mkdir(parents=True)
                        script=prepare_script(root,model,check,output)
                        self.assertTrue(script.is_file())
                        plan=json.loads((output/'execution-plan.json').read_text())
                        self.assertIsNone(plan['entry']);self.assertNotIn('expected',plan)
                        planned+=1
        self.assertEqual(planned,153)

    def test_complete_manifest_and_contracts(self):
        self.assertEqual({p['dump'] for p in MANIFEST['projects']},{p.name for p in REPO.glob('*.xdump')})
        self.assertEqual(len(MANIFEST['projects']),5)
        self.assertEqual(MANIFEST['forms'],303)
        identities=set();criteria=0
        for project in MANIFEST['projects']:
            self.assertEqual(sha256((REPO/project['dump']).read_bytes()).hexdigest(),project['archiveSha256'])
            for file in project['forms']:
                data=load_fixture(file);identity=(project['dump'],project['project'],data['form']['sourceFile'])
                self.assertNotIn(identity,identities);identities.add(identity)
                self.assertTrue(data['description']);self.assertTrue(data['declaration']['Наследует']['Тип'])
                if data['runtimeContract']:normalize_requirements(data['runtimeContract'])
                ids=[c['id'] for c in checks_for(data)]
                self.assertEqual(len(ids),len(set(ids)))
                covered={tuple(c['formRequirement']['requirement']['selector']['path'])+
                         tuple(c['formRequirement']['requirement']['property'].split('.'))
                         for c in checks_for(data) if c['formRequirement']['requirement']['kind']=='binding'
                         and 'path' in c['formRequirement']['requirement'].get('selector',{})}
                if project['dump']!='Dvizhok.xdump':
                    self.assertEqual(len(data['expressions']),len(covered)+len(data['unavailable']))
                criteria+=len(ids)
        self.assertEqual(len(identities),303);self.assertGreater(criteria,485)
        cases=[v for v in globals().values() if isinstance(v,type) and issubclass(v,NightlyFormCase) and v is not NightlyFormCase]
        self.assertEqual(sum(unittest.defaultTestLoader.loadTestsFromTestCase(case).countTestCases() for case in cases),303)
        write_json(REPO/'result/all-dump-forms/prepared-coverage.json',coverage_summary())

    def test_declaration_mutations_are_scored_by_sbsl(self):
        project=MANIFEST['projects'][0];data=load_fixture(project['forms'][0])
        with TemporaryDirectory() as directory:
            root=extract_project(REPO/project['dump'],Path(directory))[0]
            model=analyze(root)
            original=grade_form(root,model,data,Path(directory)/'original',execute_bindings=False)
            self.assertEqual(original['checks'][0]['status'],'PASS')
            altered=deepcopy(model);find_form(altered,data['form'])['properties']['Наследует']['Тип']='Форма'
            wrong=grade_form(root,altered,data,Path(directory)/'wrong',execute_bindings=False)
            self.assertEqual(wrong['checks'][0]['status'],'FAIL')
            self.assertEqual(wrong['checks'][0]['score'],0)


class NightlyFormCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary=TemporaryDirectory(prefix='element-form-corpus-')
        cls.addClassCleanup(temporary.cleanup)
        selected=next(root for root in extract_project(REPO/cls.project['dump'],Path(temporary.name))
                      if (root/'Проект.yaml').is_file() and root.name==cls.project['project'].split('::')[-1])
        cls.root=selected;cls.model=analyze(selected)
        actual={(e['namespace'],e['name'],e['sourceFile']) for e in cls.model['elements'] if e['elementType']=='КомпонентИнтерфейса'}
        expected={(data['form']['namespace'],data['form']['name'],data['form']['sourceFile'])
                  for data in (load_fixture(file) for file in cls.project['forms'])}
        if actual!=expected:raise AssertionError('Reference form inventory changed: '+str(actual^expected))
        if cls.model['sourceHash']!=cls.project['sourceHash']:raise AssertionError('Reference source hash changed')


def form_test(file):
    def test(self):
        data=load_fixture(file)
        output=REPO/'result/all-dump-forms/nightly'/Path(file).with_suffix('').relative_to('forms')
        result=grade_form(self.root,self.model,data,output)
        bad=[(c['id'],c['status'],c.get('reasonCode')) for c in result['checks'] if c['status']!='PASS']
        self.assertFalse(bad,str(data['form'])+' '+str(bad))
        self.assertEqual(result['unavailablePoints'],0)
    test.__doc__='Frozen declaration plus independent supported values: '+file
    return test


# Named cases are discovered by the existing nightly unittest discovery.
# No archive is opened, source executed or oracle generated during discovery.
for project in MANIFEST['projects']:
    slug=Path(project['forms'][0]).parent.name
    members={'project':project,'__module__':__name__}
    for file in project['forms']:
        data=load_fixture(file)
        members['test_form_'+Path(file).stem+'_'+data['form']['name']]=form_test(file)
    case=type('Forms_'+slug,(NightlyFormCase,),members)
    case=unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','All-form runtime is scheduled in nightly with Docker')(case)
    globals()[case.__name__]=case
del case
