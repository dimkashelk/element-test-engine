"""Allowlisted, versioned assignment implementations installed with the engine.

Criteria select a profile and rename identifiers; they never supply executable
code, expected results, module paths or a runtime command.
"""
from functools import lru_cache
from hashlib import sha256
import importlib.util
from pathlib import Path

from .yaml_io import InputError

ROOT=Path(__file__).resolve().parents[1]
PROFILE_ID='programming-practice'


@lru_cache(maxsize=1)
def implementation():
    path=ROOT/'assignments/programming-practice/check.py'
    spec=importlib.util.spec_from_file_location('_element_programming_profile_v1',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_profile(config):
    if set(config)-{'name','profile'}:
        raise InputError('Профиль нельзя совмещать с checks или formRequirements')
    profile=config['profile']
    if not isinstance(profile,dict) or set(profile)-{'id','version','optional','aliases'}:
        raise InputError('Неизвестные параметры встроенного профиля')
    if profile.get('id')!=PROFILE_ID or profile.get('version')!='1':
        raise InputError('Неизвестный профиль или версия профиля')
    if not isinstance(profile.get('optional',False),bool):
        raise InputError('optional должен быть логическим значением')
    aliases=implementation().aliases_config(value=profile.get('aliases',{}))
    digest=sha256()
    for name in ('check.py','cases.py','lesson-contract.json'):
        digest.update(name.encode()+b'\0'+(ROOT/'assignments/programming-practice'/name).read_bytes()+b'\0')
    return {'name':config.get('name','Программирование — практическая работа'), 'checks':[],
            'profile':{'id':PROFILE_ID,'version':'1','optional':profile.get('optional',False),
                       'aliases':aliases,'implementationSha256':digest.hexdigest()}}


def prepare_profile(root,model,work,profile):
    # Check again at the dispatch boundary, independently of YAML validation.
    if profile['id']!=PROFILE_ID or profile['version']!='1':
        raise InputError('Неизвестный встроенный профиль')
    return implementation().prepare_assessment(root,model,work,optional=profile['optional'],aliases=profile['aliases'])
