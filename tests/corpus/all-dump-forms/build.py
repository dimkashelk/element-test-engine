"""Offline fixture authoring; grading never derives expected from a submission."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import uuid

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
from element_test.assignment import load_assignment
from element_test.declarative_bindings import component_value_type, get_path, plan_binding, table_context
from element_test.expression_ast import parse_expression
from element_test.form_context import expression_values, member_path
from element_test.generated_types import union_members
from element_test.model import analyze
from element_test.resolution import qualified
from element_test.xdump import extract_project
from element_test.yaml_io import InputError, UnsupportedSyntaxError, load_yaml

OUT=Path(__file__).resolve().parent


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def fixture(c,typ,label,state,parents=()):
    typ=c.canonical_type(typ)
    variants=union_members(typ)
    if variants:
        concrete=[v for v in variants if v!='?']
        if variants[-1]=='?' and state==0:return None
        variant=concrete[(state-1)%len(concrete)]
        owner=c.canonical_elements.get(variant.partition('.')[0])
        if not owner:raise InputError('Fixture union owner is not declared: '+typ)
        return {'type':qualified(owner)+'.'+variant.partition('.')[2],
                'value':fixture(c,variant,label,state,parents)}
    if typ.endswith('?'):
        return None if state==0 or typ[:-1] in parents else fixture(c,typ[:-1],label,state,parents)
    if typ=='Строка':return '' if state==0 else label+'-marker-'+str(state)
    if typ=='Булево':return state%2==1
    if typ=='Число':return 0 if state==0 else (sum(map(ord,label))%53)+state+0.25
    if typ=='Ууид':return str(uuid.UUID(sha256(label.encode()).hexdigest()[:32],version=4))
    if typ=='Дата':return '2026-10-03' if state!=2 else '2024-02-29'
    if typ=='ДатаВремя':return '2026-10-03T12:34:56'
    if typ=='Время':return '12:34:56'
    if typ=='Момент':return '2026-10-03T12:34:56Z'
    if typ in c.enums:return c.enums[typ][state%len(c.enums[typ])]
    array=re.fullmatch(r'Массив<(.+)>',typ)
    if array:
        if state==0 or array[1] in parents:return []
        row=fixture(c,array[1],label+'.first',state,parents)
        return [row,fixture(c,array[1],label+'.other',state,parents),deepcopy(row)]
    mapping=re.fullmatch(r'Соответствие<(.+)>',typ)
    if mapping:return {}
    if typ in parents:raise InputError('Required recursive fixture: '+typ)
    if typ in c.fields:
        return {f['Имя']:fixture(c,f['Тип'],label+'.'+f['Имя'],state,parents+(typ,)) for f in c.fields[typ]}
    raise InputError('No independent fixture for type: '+typ)


def read(value,path):
    for p in path:
        if isinstance(value,dict) and set(value)=={'type','value'}:value=value['value']
        value=value[p]
    return deepcopy(value)


def runtime_requirement(root,model,form,expression,index):
    path=expression['yamlPath'];key=path[-1]
    parent=path[:-2] if key=='Данные' and path[-2]=='Источник' else path[:-1]
    item=get_path(form['properties'],parent)
    if not isinstance(item,dict):raise InputError('Binding has no component declaration')
    prop='Источник.Данные' if len(path)>1 and path[-2]=='Источник' and key=='Данные' else key
    if key=='Значение':output=component_value_type(item)
    elif key in {'Видимость','Доступность','ТолькоЧтение'}:output='Булево'
    elif key in {'Заголовок','Представление','Текст'}:output='Строка'
    elif prop=='Источник.Данные':
        typ=item.get('Тип','')
        match=re.fullmatch(r'Таблица<ИсточникДанныхМассив<(.+)>>',typ)
        output='Массив<'+match[1]+'>' if match else None
    else:output=None
    if not output:raise UnsupportedSyntaxError('No value/type adapter for property '+prop)
    source_path=member_path(parse_expression(expression['expression'][1:]))
    if not source_path:raise UnsupportedSyntaxError('Independent oracle requires an explicitly described field path')
    if source_path[0]=='этот':source_path=source_path[1:]
    req={'id':'binding-'+str(index).zfill(4),'clause':'values','kind':'binding',
         'selector':{'path':parent},'property':prop,'outputType':output}
    cfg={'form':{'name':form['name'],'namespace':form['namespace']},'requirement':req}
    plan=plan_binding(root,model,{'formRequirement':cfg,'context':{},'steps':[{'snapshot':True}]})
    contexts=[fixture(plan.contracts,plan.form['canonical'],form['name'],state) for state in range(3)]
    row=table_context(form,parent,plan.contracts)
    if source_path[0]=='RowData' and 'RowData' not in contexts[0]:
        if not row:raise InputError('No nearest table for independent row oracle')
        rows_path=member_path(parse_expression(row[1]['Источник']['Данные'][1:]))
        if not rows_path:raise UnsupportedSyntaxError('Independent row oracle requires a field source')
        if rows_path[0]=='этот':rows_path=rows_path[1:]
        values=[[read(r,source_path[1:]) for r in read(context,rows_path)] for context in contexts]
    else:values=[read(context,source_path) for context in contexts]
    if plan.form.get('recordOwner'):
        req['scenarios']=[{'context':context,'expected':{'actions':[value]}} for context,value in zip(contexts,values)]
    else:
        steps=[{'snapshot':True}]
        for context in contexts[1:]:
            steps.extend({'set':{'path':key,'value':value}} for key,value in context.items())
            steps.append({'snapshot':True})
        req['scenarios']=[{'context':contexts[0],'steps':steps,'expected':{'actions':values}}]
    # Prove all fixtures/steps are typed before freezing, without executing the reference.
    from element_test.form_requirements import expand_requirements
    contract={'schemaVersion':1,'description':'Указанные поля показывают собственные данные формы и строки своего источника.',
      'clauses':[{'id':'values','text':'Каждый указанный путь показывает значение предназначенного поля; порядок и повторы строк сохраняются.'}],
      'form':cfg['form'],'requirements':[req]}
    for check in expand_requirements(contract):plan_binding(root,model,check)
    return req


def main():
    dvizhok=load_assignment(REPO/'assignments/dvizhok-declarative-bindings')['checks']
    projects=[]
    for dump in sorted(REPO.glob('*.xdump')):
        with TemporaryDirectory() as directory:
            for root in extract_project(dump,Path(directory)):
                meta=load_yaml(root/'Проект.yaml',metadata=True);model=analyze(root)
                identity=meta.get('Поставщик','')+'::'+meta['Имя']
                slug=sha256((dump.name+identity).encode()).hexdigest()[:12]
                forms=sorted((e for e in model['elements'] if e['elementType']=='КомпонентИнтерфейса'),key=lambda e:e['sourceFile'])
                files=[]
                for index,form in enumerate(forms):
                    expressions=list(expression_values(form['properties']))
                    requirements=[];unavailable=[];existing=[]
                    if dump.name=='Dvizhok.xdump':
                        existing=[deepcopy(c) for c in dvizhok if c['formRequirement']['form']=={'name':form['name'],'namespace':form['namespace']}]
                    else:
                        for number,expression in enumerate(expressions):
                            try:requirements.append(runtime_requirement(root,model,form,expression,number))
                            except InputError as exc:unavailable.append({**expression,'runtimeStatus':'NOT_CHECKED','reason':str(exc)})
                    fixture_data={'schemaVersion':1,'form':{k:form[k] for k in ('name','namespace','sourceFile')},
                      'description':'Регрессия декларации эталонной формы '+form['name']+'; поведение оценивают отдельно зафиксированные сценарии.',
                      'declaration':deepcopy(form['properties']), 'expressions':expressions,
                      'runtimeContract':{'schemaVersion':1,'description':'Форма показывает собственные поля и данные строк своего источника по указанным путям.',
                        'clauses':[{'id':'values','text':'Значения полей на пустых/непустых fixtures, с независимыми маркерами, порядком и повторными строками.'}],
                        'form':{'name':form['name'],'namespace':form['namespace']},'requirements':requirements},
                      'existingChecks':existing,'unavailable':unavailable}
                    module=next((m for m in model['modules'] if m['sourceFile']==form['sourceFile'].removesuffix('.yaml')+'.xbsl'),None)
                    fixture_data['moduleSignatures']=[{k:v for k,v in method.items() if k!='line'} for method in module['methods']] if module else []
                    # No empty requirements contract is handed to the evaluator.
                    if not requirements:fixture_data['runtimeContract']=None
                    file='forms/'+slug+'/'+str(index).zfill(3)+'.json';write(OUT/file,fixture_data);files.append(file)
                projects.append({'dump':dump.name,'project':identity,'archiveSha256':sha256(dump.read_bytes()).hexdigest(),
                                 'sourceHash':model['sourceHash'],'forms':files})
                print(identity,len(forms),'forms')
    write(OUT/'manifest.json',{'schemaVersion':1,'projects':projects,'forms':sum(len(p['forms']) for p in projects)})

if __name__=='__main__':main()
