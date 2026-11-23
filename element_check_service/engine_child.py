"""Trusted engine entrypoint with a filtered environment and no service DB secret."""
import json
import os
from pathlib import Path
import sys
import zipfile
import jsonschema
from element_test.bridge import run_test
from element_test.loader import open_project
from element_test.model import analyze
from element_test.yaml_io import InputError
from . import ARTIFACTS
from .common import write_json
from .config import Config, ROOT
from .inputs import prepare_criteria


def run(job):
    output = Path(job['output'])
    output.mkdir(mode=0o2770,exist_ok=True)
    outcome = {'status':'execution_failed','grading':None,'error':{'code':'engine_failure','message':'Engine failed','retryable':True}}
    phase = 'criteria'
    try:
        config = Config(data=Path(job['data']),values=job['config'])
        normalized = prepare_criteria(Path(job['criteria']),Path(job['assignment']),config)
        phase = 'dump'
        with open_project(Path(job['dump']),job['manifest'].get('projectName')) as root:
            model = analyze(root)
            if model['compatibilityVersion'] not in ('9.0','9.3','10.0'):
                outcome.update(status='rejected',error={'code':'unsupported_profile','message':'Unsupported compatibility profile','retryable':False})
            else:
                phase = 'engine'
                _,grading = run_test(job['dump'],job['assignment'],output,integration=job['manifest']['runMode']=='integration',
                    student_id=job['manifest']['studentId'],assignment_id=job['manifest']['assignmentId'],run_id=job['checkId'],
                    project_name=job['manifest'].get('projectName'),_prepared=(root,model),render_html=False,
                    max_criteria_yaml_bytes=config.get('limits','maxCriteriaBytes'))
                from element_test.grading import safe
                private = sorted({str(ROOT),job['data'],os.environ.get('ELEMENT_SCRIPT_HOME',''),os.environ.get('ELEMENT_TEST_INTEGRATION_PASSWORD',''),'/var/run/docker.sock','/run/secrets'},key=len,reverse=True)
                def public(value):
                    if isinstance(value,str):
                        for text in private:
                            if text:
                                value=value.replace(text,'[redacted]')
                        return value
                    if isinstance(value,list):
                        return [public(x) for x in value]
                    if isinstance(value,dict):
                        return {k:public(v) for k,v in value.items()}
                    return value
                for kind in ARTIFACTS:
                    path=output/kind
                    write_json(path,public(safe(json.loads(path.read_text()))))
                grading=json.loads((output/'grading.json').read_text())
                jsonschema.validate(grading,json.loads((ROOT/'docs/grading-v1.schema.json').read_text()))
                outcome.update(status='completed',grading=grading,error=None,normalizedCriteriaSha256=normalized)
    except (InputError,zipfile.BadZipFile,ValueError,TypeError,KeyError,RecursionError):
        if phase in ('criteria','dump'):
            outcome.update(status='rejected',error={'code':'invalid_criteria' if phase=='criteria' else 'invalid_dump','message':'Invalid criteria package' if phase=='criteria' else 'Invalid project dump','retryable':False})
    except Exception:
        pass  # No raw engine diagnostics leave the operator-only child process.
    outcome['engine'] = job.get('engine')
    write_json(output/'outcome.json',outcome)
    return 0


if __name__=='__main__':
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
