"""The service upload contract and its dispatch to the complete trusted checker."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import zipfile

import jsonschema
import yaml

from element_check_service.config import Config
from element_check_service.inputs import prepare_criteria
from element_test.assignment import load_assignment
from element_test.batch import _cacheable
from element_test.bridge import run_test
from element_test.yaml_io import InputError

ROOT=Path(__file__).resolve().parents[1]
PROFILE={'name':'Программирование','profile':{'id':'programming-practice','version':'1'}}


class ProfileContractTests(unittest.TestCase):
    def test_yaml_zip_and_fingerprint(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'criteria.yaml'
            source.write_text(yaml.safe_dump(PROFILE,allow_unicode=True))
            first=prepare_criteria(source,root/'yaml',Config(data=root))
            with zipfile.ZipFile(root/'criteria.zip','w') as archive:
                archive.write(source,'assignment.yaml')
            self.assertEqual(first,prepare_criteria(root/'criteria.zip',root/'zip',Config(data=root)))
            loaded=load_assignment(source)
            self.assertEqual(64,len(loaded['profile']['implementationSha256']))
            self.assertFalse(_cacheable({'unavailablePoints':0,'checks':[]},loaded))
            changed=deepcopy(PROFILE);changed['profile']['optional']=True
            source.write_text(yaml.safe_dump(changed))
            self.assertNotEqual(first,prepare_criteria(source,root/'optional',Config(data=root)))
            config=Config(data=root);config.values['limits']['maxExpandedChecks']=319
            with self.assertRaises(InputError):prepare_criteria(source,root/'limit',config)

    def test_profile_cannot_supply_code_answers_or_skip_checks(self):
        values=[]
        for key,value in [('id','../../evil'),('version','2'),('optional','false'),
                          ('module','evil.py'),('expected',True),('only','P01'),('command','echo')]:
            item=deepcopy(PROFILE);item['profile'][key]=value;values.append(item)
        for key in ('checks','formRequirements'):
            item=deepcopy(PROFILE);item[key]=[];values.append(item)
        for aliases in ({'expected':True},{'names':{'X':{'expected':'Answer'}}},[],
                        {'owners':{'A':'B','C':'B'}}):
            item=deepcopy(PROFILE);item['profile']['aliases']=aliases;values.append(item)
        with TemporaryDirectory() as directory:
            root=Path(directory);source=root/'criteria.yaml'
            for i,value in enumerate(values):
                with self.subTest(value=value):
                    source.write_text(yaml.safe_dump(value,allow_unicode=True))
                    with self.assertRaises(InputError):prepare_criteria(source,root/str(i),Config(data=root))

    @unittest.skipUnless(os.environ.get('ELEMENT_TEST_DOCKER_TESTS')=='1','Requires real Script and Docker')
    def test_full_profile_through_service_child(self):
        from element_check_service.engine_child import run
        with TemporaryDirectory() as directory:
            root=Path(directory);criteria=root/'criteria.yaml'
            criteria.write_text(yaml.safe_dump(PROFILE,allow_unicode=True))
            job={'output':str(root/'output'),'criteria':str(criteria),'assignment':str(root/'assignment'),
                 'dump':str(ROOT/'tests/corpus/programming/reference'),'data':str(root),'config':{},
                 'manifest':{'studentId':'profile-reference','assignmentId':'programming','runMode':'isolated'},
                 'checkId':'profile-acceptance'}
            self.assertEqual(0,run(job))
            outcome=json.loads((root/'output/outcome.json').read_text())
            self.assertEqual('completed',outcome['status'],outcome)
            grading=outcome['grading']
            self.assertEqual(('passed',320,320,0),(grading['status'],grading['score'],grading['maxScore'],grading['unavailablePoints']))
            self.assertEqual(320,len(grading['feedback']))
            jsonschema.validate(grading,json.loads((ROOT/'docs/grading-v1.schema.json').read_text()))
            evidence=json.loads((root/'output/runtime-evidence.json').read_text())
            self.assertEqual(179,sum(c.get('observationKind')=='structural' for c in evidence))
            self.assertEqual(1,sum(c.get('observationKind')=='trace' for c in evidence))
            self.assertEqual(140,sum('observationKind' not in c for c in evidence))
            # Persist the real, path-redacted transport for deployment review.
            target=ROOT/'result/programming-service-acceptance';target.mkdir(parents=True,exist_ok=True)
            for name in ('outcome.json','grading.json','result.json','runtime-evidence.json','execution-plans.json'):
                (target/name).write_bytes((root/'output'/name).read_bytes())


if __name__=='__main__':unittest.main()
