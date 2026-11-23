"""Identifier mapping and opt-in source lowering for declarative runtime checks."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil

from .indexer import lex, parse_module, mask_noncode, method_local_bindings, method_binding_visible
from .yaml_io import InputError


def tree(value, path=()):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from tree(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from tree(item, path + (index,))


def aliases_config(path=None, *, value=None):
    value = json.loads(Path(path).read_text()) if path else deepcopy(value) if value is not None else {}
    if not isinstance(value, dict):
        raise InputError('Карта имён должна быть объектом')
    if set(value) - {'owners', 'names'}:
        raise InputError('Карта допускает только owners и names; ожидаемые результаты изменять нельзя')
    for key in ['owners', 'names']:
        if not isinstance(value.get(key, {}), dict):
            raise InputError('Раздел карты должен быть объектом: ' + key)
    for mapping in [value.get('owners', {}), *value.get('names', {}).values()]:
        if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                               or not v.strip() for k, v in mapping.items()):
            raise InputError('В карте разрешены только непустые строковые имена')
        if len(set(mapping.values())) != len(mapping):
            raise InputError('Имена в одной карте должны иметь разные назначения')
    ident=r'[^\W\d]\w*'
    for old,new in value.get('owners',{}).items():
        if not all(re.fullmatch(ident+r'(?:(?:::|\.)'+ident+r')*',s) for s in [old,new]):
            raise InputError('owners требует полные имена модулей или форм')
    reserved={'id','type','group','points','target','namespace','module','method','args','context','expected',
              'execution','observe','sequence','storage','formEffects','runtimeProfile','comparison','timeout'}
    for mapping in value.get('names',{}).values():
        for old,new in mapping.items():
            if old in reserved or new in reserved or not all(re.fullmatch(ident,s) for s in [old,new]):
                raise InputError('names разрешает только идентификаторы приложения; тексты и ключи тестов запрещены')
    return value



def owner_name(owner, config):
    return config.get('owners', {}).get(owner, owner)



def renamed(text, mapping):
    for old in sorted(mapping, key=len, reverse=True):
        text = re.sub(r'(?<!\w)' + re.escape(old) + r'(?!\w)', lambda _: mapping[old], text)
    return text



def adapt(check, config):
    """Map identifiers only; do not alter student code, numeric answers or user text."""
    original = check['target']
    owner = '::'.join(filter(None, (original.get('namespace'), original['module'])))
    names = config.get('names', {}).get(owner, {})
    def convert(v, field=''):
        if isinstance(v, dict):
            result={}
            for k,x in v.items():
                key=renamed(k,names) if isinstance(k,str) else k
                if key in result:raise InputError('Карта объединяет два разных поля: '+key)
                result[key]=convert(x,k)
            return result
        if isinstance(v, list):
            return [convert(x, field) for x in v]
        if isinstance(v, str):
            # Expected text is presentation contract; only identifier paths are mapped.
            if field in {'method','module','owner'} or v in names or v.startswith('Компоненты.'):
                return renamed(v, names)
        return v
    result = convert(deepcopy(check))
    physical = owner_name(owner, config).split('::')
    result['target'].update(namespace='::'.join(physical[:-1]), module=physical[-1])
    if 'formEffects' in result:
        result['formEffects']['open'] = [owner_name(x, config) for x in result['formEffects'].get('open', [])]
        expected = result.get('expected', {})
        for _, v in tree(expected):
            if isinstance(v, dict) and 'owner' in v:
                v['owner'] = owner_name(v['owner'], config)
    return result



def lower_local_overloads(root, destination):
    """Lower distinct-arity local overloads for the standalone adapter, preserving bodies.

    The source archive is never edited. Same-arity overloads and method references
    stay untouched and are reported as unsupported by the existing planner.
    """
    edits_by_file={}
    for path in root.rglob('*.xbsl'):
        source=path.read_text(encoding='utf-8-sig');nodes,_,errors=parse_module(source)
        if errors:continue
        groups={}
        for node in nodes:groups.setdefault(node.name,[]).append(node)
        for name,overloads in groups.items():
            if len(overloads)<2:continue
            arities=[len(n.parameters(source)) for n in overloads]
            if len(set(arities))!=len(arities):continue
            # A callback requires type-based overload resolution beyond this adapter.
            if re.search(r'&\s*'+re.escape(name)+r'\b',mask_noncode(source)):continue
            replacements=[];mapping={n:f'{name}__ТестАрность{n}' for n in arities}
            if any(re.search(r'\b'+re.escape(v)+r'\b',source) for v in mapping.values()):continue
            valid=True
            for node in nodes:
                if node in overloads:
                    token=next(t for t in lex(source) if node.start<=t.start<node.parameters_span[0] and t.value==name)
                    replacements.append((token.start,token.end,mapping[len(node.parameters(source))]))
                bindings=method_local_bindings(source,node)
                for call in node.expression_tree.walk() if node.expression_tree else []:
                    if call.kind!='call':continue
                    callee=call.children[0]
                    if callee.kind=='name' and callee.value==name:
                        target=callee
                    elif callee.kind=='member' and callee.children[0].kind=='name' and callee.children[0].value=='этот' and callee.children[1].value==name:
                        target=callee.children[1]
                    else:continue
                    count=len(call.children)-1
                    if count not in mapping or method_binding_visible(bindings,name,call.start):valid=False;break
                    replacements.append((target.start,target.end,mapping[count]))
                if not valid:break
            if valid:edits_by_file.setdefault(path.relative_to(root),[]).extend(replacements)
    if not edits_by_file:return root,[]
    shutil.copytree(root,destination)
    audit=[]
    from hashlib import sha256
    for relative,replacements in edits_by_file.items():
        path=destination/relative;source=path.read_text(encoding='utf-8-sig');changed=source
        for start,end,text in sorted(set(replacements),reverse=True):changed=changed[:start]+text+changed[end:]
        path.write_text(changed,encoding='utf-8')
        audit.append(dict(sourceFile=str(relative),sourceSha256=sha256(source.encode()).hexdigest(),
                          adaptedSha256=sha256(changed.encode()).hexdigest(),
                          edits=[dict(start=a,end=b,original=source[a:b],replacement=v) for a,b,v in replacements]))
    return destination,audit
