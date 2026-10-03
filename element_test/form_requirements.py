"""Versioned teacher contracts. Normalization never reads student code."""
from copy import deepcopy
from pathlib import Path
import math
import re
from .yaml_io import InvalidTestError, load_yaml
from .indexer import IDENT, split_parameters
from .generated_types import union_members, SCALARS


def validate_type(text):
    if not isinstance(text,str) or not text.strip():
        raise ValueError('Пустой тип')
    text = text.strip()
    parts = union_members(text)
    if parts:
        concrete = parts[:-1] if parts[-1] == '?' else parts
        if len(concrete)<2 or len(set(concrete)) != len(concrete):
            raise ValueError('Некорректный union')
        for part in concrete:
            validate_type(part)
        return
    text = text.removesuffix('?')
    generic = re.fullmatch(r'(Массив|ЧитаемыйМассив|Обходимое|Соответствие)<(.+)>',text)
    if generic:
        args = split_parameters(generic[2])
        if len(args) != (2 if generic[1] == 'Соответствие' else 1):
            raise ValueError('Некорректный generic')
        for arg in args:
            validate_type(arg)
    elif not re.fullmatch(IDENT+r'(?:::'+IDENT+r')*(?:\.'+IDENT+r')?',text):
        raise ValueError('Некорректный тип')


def normalize_requirements(value, description=None):
    if not isinstance(value, dict) or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
        raise InvalidTestError('formRequirements требует schemaVersion: 1')
    result = deepcopy(value)
    if set(result) - {'schemaVersion','description','clauses','form','requirements','underdetermined'}:
        raise InvalidTestError('Неизвестные поля контракта требований')
    if description is not None:
        result['description'] = description
    if not isinstance(result.get('description'), str) or not result['description'].strip():
        raise InvalidTestError('Требуется исходное description формы')
    clauses = result.get('clauses')
    if (not isinstance(clauses, list) or not clauses or
            any(not isinstance(c, dict) or set(c) != {'id', 'text'} or
                not isinstance(c['id'], str) or not c['id'] or
                not isinstance(c['text'], str) or not c['text'].strip() for c in clauses)):
        raise InvalidTestError('clauses требует пункты {id, text}')
    ids = [c['id'] for c in clauses]
    if len(ids) != len(set(ids)):
        raise InvalidTestError('Повторный пункт описания')
    if (not isinstance(result.get('form'), dict) or set(result['form']) - {'name','namespace'}
            or not isinstance(result['form'].get('name'), str) or not result['form']['name']
            or 'namespace' in result['form'] and not isinstance(result['form']['namespace'], str)):
        raise InvalidTestError('form требует name и необязательный namespace')
    requirements = result.get('requirements')
    if not isinstance(requirements, list) or not requirements:
        raise InvalidTestError('Требуются requirements')
    seen = set()
    for r in requirements:
        if not isinstance(r, dict) or not isinstance(r.get('id'), str) or not r['id'] or r['id'] in seen:
            raise InvalidTestError('Критерии требуют уникальные id')
        seen.add(r['id'])
        if r.get('clause') not in ids or r.get('kind') not in {'structure', 'binding'}:
            raise InvalidTestError('Критерий требует clause и kind: structure/binding')
        if set(r) - {'id','clause','kind','points','selector','assert','property','outputType','scenarios'}:
            raise InvalidTestError('Неизвестные поля критерия')
        points = r.get('points', 1)
        if isinstance(points, bool) or not isinstance(points, (int, float)) or not math.isfinite(points) or points < 0:
            raise InvalidTestError('Некорректные points')
        validate_selector(r.get('selector', {}))
        if r['kind'] == 'structure':
            assertion = r.get('assert')
            if not isinstance(assertion, dict) or not assertion or set(assertion) - {'type','properties','exists','handler','command','order'}:
                raise InvalidTestError('Неизвестный или пустой структурный assert')
            if 'exists' in assertion and not isinstance(assertion['exists'], bool):
                raise InvalidTestError('exists требует Булево')
            if 'properties' in assertion and not isinstance(assertion['properties'], dict):
                raise InvalidTestError('properties требует объект')
            if 'type' in assertion and not isinstance(assertion['type'],str):
                raise InvalidTestError('assert.type требует строку')
            if 'handler' in assertion and not isinstance(assertion['handler'],bool):
                raise InvalidTestError('assert.handler требует Булево')
            if 'order' in assertion and (not isinstance(assertion['order'],list)
                    or any(not isinstance(v,str) for v in assertion['order'])
                    or len(set(assertion['order'])) != len(assertion['order'])):
                raise InvalidTestError('assert.order требует уникальные имена по порядку')
            if 'command' in assertion:
                command = assertion['command']
                if isinstance(command,dict):
                    if (set(command) != {'path','reference'} or not isinstance(command['path'],list)
                            or not isinstance(command['reference'],str) or not command['reference'].startswith('=')):
                        raise InvalidTestError('assert.command требует path/reference')
                    command = command['path']
                if not isinstance(command,(str,list)) or isinstance(command,list) and (not command or any(not isinstance(v,str) for v in command)):
                    raise InvalidTestError('assert.command требует строку пути или список')
        else:
            if not isinstance(r.get('property'), str) or not r['property']:
                raise InvalidTestError('binding требует property')
            try:
                validate_type(r['outputType'])
            except (KeyError, ValueError, TypeError) as exc:
                raise InvalidTestError('binding требует корректный outputType') from exc
            selector = r.get('selector',{})
            if (selector.get('dataType') and r['property'] == 'Значение'
                    and selector['dataType'] in SCALARS and r['outputType'] in SCALARS
                    and selector['dataType'].replace(' ','') != r['outputType'].replace(' ','')):
                raise InvalidTestError('Противоречивые типы данных и ожидаемого значения')
            scenarios = r.get('scenarios')
            if not isinstance(scenarios, list) or not scenarios:
                raise InvalidTestError('binding требует scenarios')
            for s in scenarios:
                if not isinstance(s, dict) or not isinstance(s.get('context'), dict) or 'expected' not in s:
                    raise InvalidTestError('Сценарий требует context и независимый expected')
                if set(s) - {'context','steps','expected','lifecycle','constants','clock','executorLocale','timeout'}:
                    raise InvalidTestError('Неизвестные поля сценария привязок')
                steps = s.setdefault('steps', [{'snapshot': True}])
                if not isinstance(steps, list) or not steps:
                    raise InvalidTestError('steps требует непустой список')
                for step in steps:
                    if not isinstance(step, dict) or len(step) != 1 or not set(step) <= {'snapshot','set','call'}:
                        raise InvalidTestError('Шаг требует snapshot, set или call')
                    if 'snapshot' in step and step['snapshot'] is not True:
                        raise InvalidTestError('snapshot требует true')
                    if 'set' in step and (not isinstance(step['set'],dict) or set(step['set']) != {'path','value'}
                            or not isinstance(step['set']['path'],str) or not step['set']['path']):
                        raise InvalidTestError('set требует path/value')
                    if 'call' in step:
                        call = step['call']
                        if (not isinstance(call,dict) or set(call) not in ({'method','args'},{'selector','args'})
                                or not isinstance(call['args'],list) or 'method' in call and not isinstance(call['method'],str)):
                            raise InvalidTestError('call требует method/args или selector/args связанного обработчика')
                        if 'selector' in call:
                            validate_selector(call['selector'])
                if not any('snapshot' in step for step in steps):
                    raise InvalidTestError('Сценарий должен наблюдать хотя бы один snapshot')
    result.setdefault('underdetermined', [])
    if not isinstance(result['underdetermined'], list) or any(not isinstance(v,str) for v in result['underdetermined']):
        raise InvalidTestError('underdetermined требует список')
    return result


def validate_selector(s):
    if not isinstance(s, dict) or set(s) - {'role','dataType','binding','name','path','within','declaration'}:
        raise InvalidTestError('Неизвестный selector')
    for k in ('role','dataType','binding','name'):
        if k in s and (not isinstance(s[k], str) or not s[k]):
            raise InvalidTestError('selector.' + k + ' требует строку')
    if 'path' in s and (not isinstance(s['path'], list) or any(not isinstance(p,str) for p in s['path'])):
        raise InvalidTestError('selector.path требует список строк')
    if 'declaration' in s and not isinstance(s['declaration'], dict):
        raise InvalidTestError('selector.declaration требует объект')
    if 'within' in s:
        validate_selector(s['within'])


def expand_requirements(contract):
    contract = normalize_requirements(contract)
    checks = []
    for r in contract['requirements']:
        common = {'type':'runtime', 'id':r['id'], 'points':r.get('points',1),
                  'group':r['clause'], 'formRequirement':{
                      'form':contract['form'], 'requirement':{k:v for k,v in r.items() if k != 'scenarios'},
                      'clause':next(c for c in contract['clauses'] if c['id'] == r['clause'])}}
        if r['kind'] == 'structure':
            checks.append({**common, 'expected':True})
        else:
            for i,s in enumerate(r['scenarios']):
                checks.append({**deepcopy(common), 'id':r['id']+'-'+str(i), 'points':r.get('points',1)/len(r['scenarios']),
                               **deepcopy(s)})
    return checks


def author_form(description, contract, output):
    import yaml
    normalized = normalize_requirements(load_yaml(Path(contract)), Path(description).read_text(encoding='utf-8'))
    Path(output).write_text(yaml.safe_dump(normalized,allow_unicode=True,sort_keys=False),encoding='utf-8')
    return normalized
