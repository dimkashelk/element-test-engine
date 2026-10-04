"""Read-only, reproducible per-occurrence query inventory; discovery is not PASS."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from .generated_types import ProjectTypes
from .indexer import IDENT, mask_noncode, parse_module, method_local_bindings, method_binding_visible, method_call_expressions
from .loader import open_project
from .model import analyze
from .query_plan import query_literals, parse_storage_query
from .resolution import qualified, resolve_call_modules
from .yaml_io import InputError

REPO=Path(__file__).resolve().parent.parent
REFERENCE040={'sources':[{'version':'9.3','url':'https://1cmycloud.com/console/help/element/9.3/docs/topics/'+slug+'/'} for slug in ('select-from','replace-null-function','is-null-expression')], 'executorVersion':'10.0.2-1','executionCompatibilityVersion':'current'}
ARCHIVES=[('Demo-SRM-dev-2026-09-28-21-38.xdump',('ДемоСРМ::ДемоСРМ',)),('Dvizhok.xdump',('Движок::Движок',)),
 ('Prakticheskie-primery-2026-09-30-15-20.xdump',('Примеры::Примеры_new',)),
 ('autocheck-2026-09-24-16-14.xdump',('dimkashelk::check','e1c::БазаЗнаний'))]
OPERATIONS={'source':r'\bИЗ\b','alias':r'\bКАК\b','parameter':r'%', 'projection':r'\bВЫБРАТЬ\b',
 'predicate':r'\bГДЕ\b','null':r'\bNULL\b|ЗаменитьNull|ЕСТЬ\s+NULL','join':r'\bСОЕДИНЕНИЕ\b',
 'aggregate':r'\b(СУММА|КОЛИЧЕСТВО|МИНИМУМ|МАКСИМУМ|СРЕДНЕЕ)\s*\(', 'group':r'\bСГРУППИРОВАТЬ\b',
 'having':r'\bИМЕЮЩИЕ\b','distinct':r'\bРАЗЛИЧНЫЕ\b','union':r'\bОБЪЕДИНИТЬ\b','order':r'\bУПОРЯДОЧИТЬ\b',
 'limit':r'\bПЕРВЫЕ\b','slice-last':r'СрезПоследних\s*\(', 'slice-first':r'СрезПервых\s*\(',
 'balance':r'\.Остатки\b','turnover':r'\.Обороты\b','fill':r'\bЗАПОЛНИТЬ\b','generate':r'\bПОРОДИТЬ\b',
 'compound':r';','nested':r'\(\s*ВЫБРАТЬ\b','update':r'\bИЗМЕНИТЬ\b','insert':r'\bВСТАВИТЬ\b','delete':r'\bУДАЛИТЬ\b',
 'temporary':r'\b(ПОМЕСТИТЬ|УНИЧТОЖИТЬ|ОБРЕЗАТЬ)\b|СОЗДАТЬ\s+ВРЕМЕННУЮ', 'index':r'ИНДЕКСИРОВАТЬ\s+ПО|СОЗДАТЬ\s+ИНДЕКС',
 'fetch':r'\bПОЛУЧИТЬ\b','hierarchy':r'В\s+ИЕРАРХИИ','exists':r'\bСУЩЕСТВУЕТ\b','cast':r'\bВЫРАЗИТЬ\b'}
STAGES={'join':'040-joins-and-null','null':'040-joins-and-null','aggregate':'041-projections-and-aggregates',
 'group':'041-projections-and-aggregates','having':'041-projections-and-aggregates','union':'042-unions-and-nesting',
 'nested':'042-unions-and-nesting','compound':'042-unions-and-nesting','slice-first':'043-virtual-tables',
 'turnover':'043-virtual-tables','balance':'043-virtual-tables','fill':'039-named-fill-criteria',
 'generate':'044-result-and-resources','update':'045-state-rights-and-combinations','insert':'045-state-rights-and-combinations',
 'delete':'045-state-rights-and-combinations'}

def digest(value):return sha256(value.encode()).hexdigest()

def literals_with_failures(source):
 """Retain malformed candidates, including their original source range."""
 try:return [(a,b,c,t,None) for a,b,c,t in query_literals(source)]
 except InputError:
  code=mask_noncode(source);out=[];consumed=0
  for m in re.finditer(r'\bЗапрос\s*\{',code):
   if m.start()<consumed:continue
   end=m.end();depth=1
   while end<len(code) and depth:
    depth+=(code[end]=='{')-(code[end]=='}');end+=1
   out.append((m.start(),end,m.end(),source[m.end():end-1 if not depth else end],
               'Незакрытый литерал Запрос' if depth else None))
   if not depth:consumed=end
  return out


def source_records(root,model,archive,project):
 records=[];modules={m['sourceFile']:m for m in model['modules']}
 identity={k:model['properties'].get(k) for k in ('Поставщик','Имя','Версия','ВидПроекта')}
 for path in sorted(root.rglob('*')):
  if not path.is_file() or path.suffix not in {'.xbsl','.xbql'}:continue
  relative=path.relative_to(root).as_posix();source=path.read_text(encoding='utf-8-sig');code=mask_noncode(source)
  module=modules.get(relative);nodes=parse_module(source)[0] if module else []
  candidates=literals_with_failures(source) if module else [(0,len(source),0,source,None)]
  literal_spans=[(a,b) for a,b,_,_,_ in candidates]
  api_bindings={}
  # Keep dynamic/API candidates visible. Resolution below never promotes a
  # project module/type named Запрос or an unrelated Выполнить to system API.
  api_names={'Запрос','ЗапросСВыборкой','ЗапросБезВыборки','ТипизированныйЗапрос','СоздатьЗапрос','Выполнить','УстановитьПараметр'}
  for node in nodes:
   for call in method_call_expressions(source,node):
    if call.name not in api_names or any(a<=call.start<b for a,b in literal_spans):continue
    a=call.receiver_start if call.receiver_start is not None else call.start
    locals_=method_local_bindings(source,node)
    receiver=call.receiver or ''
    local=bool(receiver and method_binding_visible(locals_,receiver.split('::')[0].split('.')[0],call.start))
    owners=resolve_call_modules(model['modules'],receiver,module['namespace'],module.get('imports',[]),model['properties']) if receiver and not local else []
    own=not receiver and any(m['name']==call.name for m in module['methods'])
    api_bindings[a,call.end]={'operation':call.name,'receiver':receiver,'localBinding':local,
     'projectOwners':[{'namespace':m['namespace'],'module':m['name'],'sourceFile':m['sourceFile']} for m in owners],
     'resolution':'project-declaration' if owners or own else 'receiver-contract-required'}
    candidates.append((a,call.end,a,source[a:call.end],'Query API candidate requires receiver/signature contract'))
  for start,end,body,text,failure in sorted(candidates):
   node=next((n for n in nodes if n.start<=start<(n.end or len(source))),None)
   family='xbql-file' if not module else 'literal' if any(a==start and b==end for a,b in literal_spans) else 'api-candidate'
   operations=[name for name,pattern in OPERATIONS.items() if re.search(pattern,mask_noncode(text,strings=False),re.I)]
   target=None
   if node:
    target={'namespace':module['namespace'],'module':module['name'],'method':node.name,
            'parameters':node.parameters(source),'returnType':node.return_type(source),'range':[node.start,node.end]}
   c=ProjectTypes(model,module['namespace'] if module else '',module.get('imports',[]) if module else ())
   c.rename_collisions=True;c.reference_id_type='Ууид';ast=None
   if module and family=='literal' and not failure:
    locals_=method_local_bindings(source,node) if node else {}
    from .local_structures import scalar_structures
    if ((node and method_binding_visible(locals_,'Запрос',start)) or 'Запрос' in scalar_structures(source) or c.resolve('Запрос') or
        resolve_call_modules(model['modules'],'Запрос',c.namespace,c.imports,model['properties'])):
     failure='Затенённый владелец литерала Запрос'
    else:
     try:ast=parse_storage_query(text,c).to_dict()
     except InputError as exc:failure=str(exc)
   owner={'namespace':module['namespace'],'module':module['name']} if module else {'namespace':'::'.join(path.relative_to(root).parts[:-1]),'module':path.stem}
   key=[archive,project,relative,start,end,family]
   dependencies=[{'namespace':e['namespace'],'owner':e['name'],'kind':e['elementType'],'sourceFile':e['sourceFile']}
                 for e in c.canonical_elements.values()]
   record={'contractId':'query-'+digest(json.dumps(key,ensure_ascii=False))[:24], 'family':family,'operations':operations,
    'archive':archive,'projectIdentity':identity,'libraryIdentity':identity if model['properties'].get('ВидПроекта')=='Библиотека' else None,
    'ownerIdentity':owner,'sourceHash':sha256(path.read_bytes()).hexdigest(),'projectSourceHash':model['sourceHash'],
    'file':relative,'range':[start,end],'bodyStart':body,'line':source.count('\n',0,start)+1,'text':text,'target':target,
    'dependencies':dependencies,'semantics':(['adapter-storage-projections-aggregates-041','element-reference-9.3','script-current'] if ast and ast.get('mode')=='storage-projections-aggregates-v1' else ['adapter-storage-relational-040','element-reference-9.3','script-current'] if ast and ast.get('mode')=='storage-relational-joins-null-v1' else ['adapter-storage-31-33-39'] if ast else ['query-reference-9.1']),
    'compatibilityVersion':model['compatibilityVersion'],'discovered':True,'parsed':ast is not None,
    'planned':False,'executed':False,'independentlyAssessed':False,'ast':ast,'unsupportedReason':failure,
    'nextStage':next((STAGES[op] for op in STAGES if op in operations),'044-result-and-resources' if family=='api-candidate' else '045-state-rights-and-combinations'),
    'criteria':[],'evidence':[], 'apiBinding':api_bindings.get((start,end))}
   records.append(record)
 return records


def attach_evidence(records,directories):
 for folder in directories:
  folder=Path(folder)
  if not (folder/'result.json').exists() or not (folder/'execution-plans.json').exists():continue
  result=json.loads((folder/'result.json').read_text());checks={c['id']:c for c in result['checks']}
  runtimes={r.get('criterionId'):r for r in json.loads((folder/'runtime-evidence.json').read_text())} if (folder/'runtime-evidence.json').exists() else {}
  for item in json.loads((folder/'execution-plans.json').read_text()):
   plan=item.get('plan',item);criterion=item.get('criterionId');check=checks.get(criterion,{})
   for query in plan.get('queries',[]):
    for record in records:
     if (record.get('projectSourceHash')==result.get('sourceHash') and record.get('file')==query['sourceFile'] and
         record['range']==[query['start'],query['end']]):
      record['planned']=True
      runtime=runtimes.get(criterion,{})
      executed=runtime.get('status')=='EXECUTED'
      direct=plan.get('target',{})=={k:record['target'][k] for k in ('namespace','module','method')} if record['target'] else False
      entry=plan.get('entry',{})
      direct=direct or (entry.get('declaration')==record['target']['method'] and entry.get('source_file')==record['file'] if record['target'] else False)
      record['executed']|=executed
      record['independentlyAssessed']|=bool(executed and direct and check.get('status') in ('PASS','FAIL'))
      origin = ('fresh-041' if query.get('ast', {}).get('mode') == 'storage-projections-aggregates-v1' else 'fresh-040' if query.get('ast', {}).get('mode') == 'storage-relational-joins-null-v1' else 'fresh-039')
      record['criteria'].append({'criterionId':criterion,'direct':direct,'status':check.get('status'),'origin':origin})
      record['evidence'].append(str(folder.relative_to(REPO)) if folder.is_relative_to(REPO) else str(folder))


def build_catalog(repo=REPO,evidence=()):
 records=[];archives=[]
 for archive,projects in ARCHIVES:
  path=repo/archive;before=sha256(path.read_bytes()).hexdigest()
  for project in projects:
   with open_project(path,project) as root:
    records.extend(source_records(root,analyze(root),archive,project))
  after=sha256(path.read_bytes()).hexdigest()
  if before!=after:raise InputError('Archive changed during read-only inventory: '+archive)
  archives.append({'file':archive,'sha256':before,'unchanged':True})
 docs=json.loads((repo/'tests/corpus/storage-query-fill/documented-contracts.json').read_text())
 records.extend(docs['contracts']);attach_evidence(records,evidence)
 return {'schemaVersion':1,'compatibilityVersion':'9.0','archives':archives,'sources':docs['sources'],
  'documentationReferenceVersion':'9.1','documentationReferenceVersions':['9.1','9.3'], 'stage040Reference':REFERENCE040,
  'documentationCompleteness':'family-inventory-with-explicit-execution-queue','contracts':records,
  'counts':dict(Counter(r['family'] for r in records))}


def markdown(catalog):
 stage040=catalog.get('stage040Reference')
 reference=('Для №40–41 пользователь выбрал справку Элемента **9.3** и установленный Script **10.0.2-1**. Профиль `9.3` использует режим Script `current`: отдельного режима `-c 9.3` в этом executor нет. Исходные версии архивов сохраняются. Исторические свидетельства №39 относятся к справке 9.1 и режиму `-c 9.0`.' if stage040 else
  'Официальная справка Элемента **9.1** выбрана пользователем для проекта с режимом совместимости **9.0**. Версия источника сохраняется; Script-пробы и runtime выполняются с `-c 9.0`. Раздел 9.0 возвращает 404 и отсутствует в меню архивных версий.')
 lines=['# Каталог контрактов запросов — №39/40/41' if catalog.get('stage041Reference') else '# Каталог контрактов запросов — №39/40' if stage040 else '# Каталог контрактов запросов — №39','',
  'Воспроизведение: `python3 -m element_test.query_catalog`. Архивы открываются только для чтения.',
  '',reference+' Каталог сохраняет очередь исполнения; только шаблоны с exact fixtureHash и независимой оценкой являются исполнимыми критериями.',
  '', '213 исходных литералов не сливаются. API-кандидаты сохраняются отдельно: обнаружение имени метода не подтверждает системный receiver.',
  '', '| Проект | Литералы | API-кандидаты | XBQL |', '|---|---:|---:|---:|']
 counts=Counter((str(r['projectIdentity'].get('Поставщик'))+'::'+str(r['projectIdentity'].get('Имя')),r['family']) for r in catalog['contracts'] if r.get('archive'))
 for project in sorted({key[0] for key in counts}):lines.append(f'| {project} | {counts[project,"literal"]} | {counts[project,"api-candidate"]} | {counts[project,"xbql-file"]} |')
 lines+=['','P — parsed, L — planned, E — executed, A — independentlyAssessed. Исторические журналы №31–33 не являются свежей приёмкой.','','| contractId | Источник / метод | Операции | P/L/E/A | Причина / следующий этап |','|---|---|---|---|---|']
 for r in catalog['contracts']:
  target=r.get('target') or {};source=(r.get('file') or r.get('fixture') or '')+(':'+str(r['range'][0]) if r.get('range') else '')
  reason=(r.get('unsupportedReason') or '')+' / '+r['nextStage']
  lines.append('| '+r['contractId']+' | '+source+' '+target.get('method','')+' | '+', '.join(r['operations'])+' | '+ '/'.join(str(int(r[k])) for k in ('parsed','planned','executed','independentlyAssessed'))+' | '+reason.replace('|','\\|').replace('\n',' ')+' |')
 return '\n'.join(lines)+'\n'


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=REPO/'docs/query-contract-catalog.json');parser.add_argument('--evidence',action='append',default=[]);args=parser.parse_args()
 evidence=args.evidence or [REPO/'result/storage-query-fill/public-test', REPO/'result/joins-null/public-test', REPO/'result/joins-null/public-run', REPO/'result/joins-null/public-cases-test', REPO/'result/joins-null/public-cases-run', REPO/'result/projections-aggregates/public-tasks-test', REPO/'result/projections-aggregates/public-tasks-run', REPO/'result/projections-aggregates/public-max-test', REPO/'result/projections-aggregates/public-max-run']
 catalog=build_catalog(evidence=evidence);args.output.parent.mkdir(parents=True,exist_ok=True)
 args.output.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n');args.output.with_suffix('.md').write_text(markdown(catalog));print(json.dumps(catalog['counts'],ensure_ascii=False))
if __name__=='__main__':main()
