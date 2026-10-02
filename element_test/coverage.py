"""Reproducible file/method inventory. Evidence and declared coverage stay separate."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from .assignment import load_assignment
from .execution_plan import plan_execution
from .form_context import describe_form, expression_values
from .indexer import parse_module
from .loader import open_project
from .model import analyze
from .yaml_io import InputError


def inventory(root, model, assignments=(), evidence=()):
    methods=[];file_items=[]
    modules={m['sourceFile']:m for m in model['modules']}
    elements={e['sourceFile']:e for e in model['elements']}
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix not in {'.yaml','.xbsl'}:continue
        relative=path.relative_to(root).as_posix()
        module=modules.get(relative);element=elements.get(relative)
        diagnostics=[d for d in model['diagnostics'] if d.get('sourceFile',d.get('file'))==relative]
        item={'sourceFile':relative,'sha256':sha256(path.read_bytes()).hexdigest(),
              'role':('object-module' if module and module['moduleType']=='object' else
                      'module' if module else element['elementType'] if element else 'service'),
              'indexed':True,'staticDiagnostics':diagnostics,'staticStatus':'DIAGNOSTICS' if diagnostics else 'NO_DIAGNOSTICS',
              'platformCompilation':'NOT_CHECKED','applicableChecks':['index','limited-static-validation'],
              'runtimeStatus':'NO_INDEXED_METHODS','methods':[]}
        if path.suffix=='.xbsl':
            nodes,_,errors=parse_module(path.read_text(encoding='utf-8-sig'))
            for node in nodes:
                identity={'project':model.get('projectIdentity',{}),'namespace':module['namespace'] if module else '',
                          'module':module['name'] if module else path.stem,'sourceFile':relative,'method':node.name}
                methods.append({'identity':identity,'start':node.start,'end':node.end,'directCriteria':[],
                                'dependencyOf':[],'executions':[],'status':'NO_SCENARIO',
                                'nextContract':'Independent scenario and expected outcome required'})
                item['methods'].append(identity)
            if nodes:item['runtimeStatus']='METHOD_EVIDENCE_REQUIRED'
        elif element and element['elementType']=='КомпонентИнтерфейса':
            item['yamlDeclaration']=element['properties']
            item['applicableChecks']+=['form-properties','base-type','component-names','own-handler-signatures']
            try:
                form=describe_form(element)
                owner=next((m for m in model['modules'] if m['name']==element['name'] and m['namespace']==element['namespace']),None)
                declared={m['name']:m for m in owner['methods']} if owner else {}
                checks=[]
                for handler in form['handlers']:
                    name=handler['name'];method=declared.get(name)
                    if not isinstance(name,str) or name.startswith('='):
                        status='EXPRESSION_CONTRACT_REQUIRED'
                    elif not method:status='MISSING_OWN_HANDLER'
                    elif handler['type']=='ОбычнаяКоманда':
                        status='MATCH' if [p['type'] for p in method['parameters']]==['ОбычнаяКоманда'] and method['returnType'] in {None,'ничто'} else 'SIGNATURE_MISMATCH'
                    elif isinstance(handler['type'],str) and handler['type'].startswith('КомандаСПараметром<'):
                        argument=handler['type'][len('КомандаСПараметром<'):-1]
                        status='MATCH' if [p['type'] for p in method['parameters']]==[handler['type'],argument] and method['returnType'] in {None,'ничто'} else 'SIGNATURE_MISMATCH'
                    else:status='SIGNATURE_CONTRACT_REQUIRED'
                    checks.append({**handler,'status':status})
                item['formChecks']={'fields':form['fields'],'objectType':form['objectType'],
                                    'components':[{k:v for k,v in c.items() if k!='declaration'} for c in form['components'].values()],
                                    'handlers':checks,'expressions':form['expressions'],
                                    'declarativeUi':'NOT_CHECKED','browserUi':'NOT_CHECKED'}
            except InputError as exc:
                item['formChecks']={'status':'UNSUPPORTED','reason':str(exc),'declarativeUi':'NOT_CHECKED',
                                    'expressions':list(expression_values(element['properties']))}
        file_items.append(item)
    by_key={(m['identity']['namespace'],m['identity']['module'],m['identity']['method']):m for m in methods}
    logs=[]
    for directory in evidence:
        directory=Path(directory)
        result=directory/'result.json';runtime=directory/'runtime-evidence.json';plans=directory/'execution-plans.json'
        if not result.exists():continue
        data=json.loads(result.read_text())
        if data.get('sourceHash')!=model['sourceHash']:continue
        logs.append((directory,{c['id']:c for c in data['checks']},
                     {c['criterionId']:c for c in json.loads(runtime.read_text())} if runtime.exists() else {},
                     {c['criterionId']:c for c in json.loads(plans.read_text())} if plans.exists() else {}))
    for assignment_path in assignments:
        a=load_assignment(assignment_path)
        for c in a['checks']:
            if c['type']!='runtime' or c.get('library'):continue
            target=c.get('target',{})
            matches=[m for key,m in by_key.items() if key[1:]==(target.get('module'),target.get('method'))
                     and ('namespace' not in target or target['namespace']==key[0])]
            if len(matches)!=1:continue
            entry=matches[0]
            criteria={'assignment':str(assignment_path),'criterionId':c['id'],'scenario':c.get('scenario',c['id']),
                      'planned':False,'dependencies':[],'status':'DECLARED'}
            try:
                p=plan_execution(root,model,c)
                criteria['planned']=True
                criteria['dependencies']=[s.identity.__dict__ for s in p.symbols[1:]]
                for s in p.symbols[1:]:
                    dependency=by_key.get((s.identity.namespace,s.identity.owner,s.identity.declaration))
                    if dependency:dependency['dependencyOf'].append({'assignment':str(assignment_path),'criterionId':c['id']})
            except InputError as exc:
                criteria['status']='UNSUPPORTED';criteria['reason']=str(exc)
            for directory,results,facts,plans in logs:
                result=results.get(c['id']);fact=facts.get(c['id']);plan=plans.get(c['id'])
                if not result or not fact:continue
                actual_entry=(plan or {}).get('plan',{}).get('entry',{})
                if actual_entry and (actual_entry.get('source_file')!=entry['identity']['sourceFile'] or actual_entry.get('declaration')!=entry['identity']['method']):continue
                execution={'criterionId':c['id'],'scenario':criteria['scenario'],'status':result['status'],
                           'called':fact['status']=='EXECUTED',
                           'independentlyAssessed':fact['status']=='EXECUTED' and result['status'] in {'PASS','FAIL'},
                           'planPrepared':bool(plan and 'plan' in plan),'journal':str(directory/'runtime-evidence.json'),
                           'result':str(directory/'result.json'),'plan':str(directory/'execution-plans.json'),
                           'reasonCode':fact.get('reasonCode'),'reason':fact.get('message')}
                entry['executions'].append(execution)
                for trace in fact.get('trace',[]):
                    if trace.get('event')!='enter':continue
                    for dep in methods:
                        i=dep['identity'];project=model.get('properties',{}).get('Имя','')
                        identity='::'.join(filter(None,(project,i['namespace'],i['module'],i['method'])))
                        if identity==trace.get('symbol') and dep is not entry:
                            dep['dependencyOf'].append({'criterionId':c['id'],'called':True,'journal':str(directory/'runtime-evidence.json'),
                                                       'status':'CALLED_AS_DEPENDENCY','independentlyAssessed':False})
            entry['directCriteria'].append(criteria)
    for method in methods:
        module=modules.get(method['identity']['sourceFile'])
        element=next((e for e in model['elements'] if module and e['name']==module['name']
                      and e['namespace']==module['namespace']),None)
        if element and element['elementType']=='НаборКонстант':
            method['nextContract']='Constants state and native collection API, independent expectations'
        elif element and element['elementType']=='КомпонентИнтерфейса' and not method['directCriteria']:
            method['nextContract']='Form/events/constants/calendar API and independent branch scenarios'
        elif module and module['moduleType']=='object' and not method['directCriteria']:
            method['nextContract']='Creation from a typed base object; independent object/table/reference effects'
        actual=method['executions']
        if any(e['independentlyAssessed'] for e in actual):
            method['status']='ASSESSED'
            method['nextContract']='Additional independent branch/API scenarios; no claim of full branch coverage'
        elif any(e['called'] for e in actual):
            method['status']='CALLED_WITHOUT_ASSESSMENT'
            method['nextContract']='Successful independent SBSL assessment required'
        elif actual:
            method['status']='UNSUPPORTED' if all(e['status']=='UNSUPPORTED' for e in actual) else 'UNASSESSED'
            reasons=[e.get('reason') or e['status'] for e in actual]
            method['nextContract']='; '.join(dict.fromkeys(reasons))
        elif method['directCriteria']:
            method['status']='PLAN_PREPARED' if any(c['planned'] for c in method['directCriteria']) else 'UNSUPPORTED'
            reasons=[c.get('reason') for c in method['directCriteria'] if c.get('reason')]
            method['nextContract']='; '.join(dict.fromkeys(reasons)) or 'Actual execution and independent assessment required'
    for f in file_items:
        owned=[m for m in methods if m['identity']['sourceFile']==f['sourceFile']]
        if owned:
            f['runtimeStatus']='PARTLY_ASSESSED' if any(m['status']=='ASSESSED' for m in owned) else 'METHOD_EVIDENCE_REQUIRED'
    prepared=sum(any(c['planned'] for c in m['directCriteria']) for m in methods)
    return {'schemaVersion':1,'sourceHash':model['sourceHash'],'projectIdentity':model.get('projectIdentity'),
            'counts':{'files':len(file_items),'yaml':sum(f['sourceFile'].endswith('.yaml') for f in file_items),
                      'xbsl':sum(f['sourceFile'].endswith('.xbsl') for f in file_items),'methods':len(methods),
                      'directTargets':sum(bool(m['directCriteria']) for m in methods),
                      'preparedDirectTargets':prepared,'withoutPreparedDirectScenario':len(methods)-prepared,
                      'assessedMethods':sum(m['status']=='ASSESSED' for m in methods),
                      'withoutDirectScenario':sum(not m['directCriteria'] for m in methods)},
            'notes':['No indexed methods is not runtime PASS','No diagnostics is not platform compilation',
                     'Dependency invocation is not independent branch assessment','YAML expressions and browser UI remain NOT_CHECKED'],
            'files':file_items,'methods':methods}


def markdown(data):
    lines=['# Карта фактического покрытия Dvizhok','',json.dumps(data['counts'],ensure_ascii=False),'',
           'Вызов, план и оценка разделены; полнота веток не заявляется. YAML/UI остаются непроверенными.','',
           '| Файл | Роль | Статическая проверка | Runtime |','|---|---|---|---|']
    lines += [f"| {f['sourceFile']} | {f['role']} | {f['staticStatus']} | {f['runtimeStatus']} |" for f in data['files']]
    lines += ['','| Метод | Сценарии | Статус | Следующий контракт |','|---|---|---|---|']
    lines += [f"| {m['identity']['namespace']}::{m['identity']['module']}.{m['identity']['method']} | {len(m['directCriteria'])} | {m['status']} | {m['nextContract'].replace('|','/')} |" for m in data['methods']]
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description='Rebuild file/method coverage from archive, assignments and execution evidence')
    parser.add_argument('--project',required=True);parser.add_argument('--assignments',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,action='append',default=[]);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    assignments=sorted(p.parent for p in args.assignments.rglob('assignment.yaml'))
    with open_project(args.project) as root:
        data=inventory(root,analyze(root),assignments,args.evidence)
    source=Path(args.project)
    data['archive']=source.name
    if source.is_file():data['archiveSha256']=sha256(source.read_bytes()).hexdigest()
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'coverage.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (args.output/'coverage.md').write_text(markdown(data))
    print(json.dumps(data['counts']))


if __name__=='__main__':main()
