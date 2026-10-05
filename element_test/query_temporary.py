"""Temporary field declarations and insertion expressions; no row evaluation."""
from dataclasses import replace
import re
from .indexer import IDENT
from .query_joins import Expression
from .yaml_io import UnsupportedSyntaxError


def parts(text):
    from .query_composites import top_tokens
    start=0;out=[]
    for token in top_tokens(text):
        if token[1]==',':out.append(text[start:token[2]]);start=token[3]
    out.append(text[start:]);return out


def descriptor(column):
    return column.value if column.kind=='column' and isinstance(column.value,dict) else {}


def cast_text(column,text):
    spec=descriptor(column);typ=column.type.rstrip('?')
    if spec.get('qualifiers'):typ+='('+', '.join(map(str,spec['qualifiers']))+')'
    return 'ВЫРАЗИТЬ('+text+' КАК '+typ+')'


def parse_columns(text,contracts,tables,name):
    from .query_composites import parse_select
    columns=[]
    for raw in parts(text):
        m=re.fullmatch(rf'\s*({IDENT})\s+ВЫЧИСЛЯЕТСЯ\s+КАК\s+(.+?)\s*',raw,re.I|re.S)
        if m:
            label=m[1];column=Expression('column',value={'computed':m[2]})
        else:
            m=re.fullmatch(rf'\s*({IDENT})\s+АВТОНОМЕРЗАПИСИ\s*',raw,re.I)
            if m:label=m[1];column=Expression('column','Число',value={'auto':True})
            else:
                m=re.fullmatch(rf'\s*({IDENT})\s*:\s*({IDENT}(?:::{IDENT})*(?:\.Ссылка)?)(?:\s*\((.*?)\))?\s*(\?)?\s*(?:ПО\s+УМОЛЧАНИЮ\s+(.+?))?\s*',raw,re.I|re.S)
                if not m:raise UnsupportedSyntaxError('Определение поля временной таблицы вне контракта')
                typ=contracts.canonical_type(m[2]);qualifiers=()
                if m[3] is not None:
                    if not re.fullmatch(r'\s*\d+\s*(?:,\s*\d+\s*)?',m[3]):raise UnsupportedSyntaxError('Некорректный квалификатор временного поля')
                    qualifiers=tuple(int(v.strip()) for v in m[3].split(','))
                    if typ=='Число':
                        if not 1<=qualifiers[0]<=32 or len(qualifiers)>2 or len(qualifiers)==2 and qualifiers[1]>32:raise UnsupportedSyntaxError('Квалификатор Число вне контракта')
                    elif typ!='Строка' or len(qualifiers)!=1:raise UnsupportedSyntaxError('Квалификатор временного поля вне контракта')
                typ+='?' if m[4] else '';contracts.require(typ)
                label=m[1];column=Expression('column',typ,value={'qualifiers':qualifiers,'default':m[5]})
        if label in [n for _,n in columns]:raise UnsupportedSyntaxError('Повторяющееся поле временной таблицы')
        columns.append((column,label))
    if sum(bool(descriptor(e).get('auto')) for e,_ in columns)>1:
        raise UnsupportedSyntaxError('Временная таблица допускает один АвтоНомерЗаписи')
    from .query_composites import tokens
    by_name={n:e for e,n in columns};dependencies={}
    for column,label in columns:
        spec=descriptor(column);ts=tokens(spec.get('computed') or spec.get('default') or '')
        dependencies[label]={t[1] for i,t in enumerate(ts) if t[0]=='token' and t[1] in by_name
            and (i==0 or ts[i-1][1] not in ('.','::')) and (i+1==len(ts) or ts[i+1][1]!='(')}
    def reachable(label,active):
        if label in active:raise UnsupportedSyntaxError('Цикл определений временных полей')
        out=set(dependencies[label])
        for dep in dependencies[label]:out.update(reachable(dep,active|{label}))
        return out
    for column,label in columns:
        refs=reachable(label,set())
        if descriptor(column).get('default') and any(descriptor(by_name[dep]).get('auto') for dep in refs):
            raise UnsupportedSyntaxError('Default от АвтоНомерЗаписи требует платформенный порядок вставки')
    for i,(column,label) in enumerate(columns):
        spec=descriptor(column)
        expr=spec.get('computed') or spec.get('default')
        if expr:
            q=parse_select('ВЫБРАТЬ '+expr+' КАК Value ИЗ '+name,contracts,{**tables,name:tuple(columns)})
            e=q.projections[0][0]
            from .query_projections import walk
            if q.parameters or any(c.kind in ('aggregate','uuid','in-query','exists-query') for c in walk(e)):
                raise UnsupportedSyntaxError('Временное поле требует детерминированное выражение без параметров/агрегатов')
            if spec.get('computed'):columns[i]=(replace(column,type=e.type,sql_nullable=e.sql_nullable),label)
            elif e.type.rstrip('?')!=column.type.rstrip('?') and e.kind not in ('null','undefined'):
                raise UnsupportedSyntaxError('Несовместимый тип значения по умолчанию: '+label)
            elif e.type.endswith('?') and not column.type.endswith('?'):
                raise UnsupportedSyntaxError('Nullable default несовместим с '+label)
    return tuple(columns)


def computed_field(parser,e,index,schema):
    column=next(c for c,n in schema if n==e.name);raw=descriptor(column).get('computed')
    if not raw:return None
    active=getattr(parser,'computed_active',set());key=(index,e.name)
    if key in active:parser.fail('Цикл вычисляемых полей временной таблицы',e.range[0])
    parser.computed_active=active;active.add(key)
    from .query_projections import ComputedParser
    p=ComputedParser(raw,parser.contracts)
    value=p.condition()
    if p.pos!=len(p.tokens):parser.fail('Некорректное вычисляемое поле',e.range[0])
    def qualify(v):
        if v.kind=='field' and len(v.value)==1:
            v=replace(v,value=(parser.sources[index].alias,v.name))
        return replace(v,children=tuple(qualify(c) for c in v.children),range=e.range)
    try:return parser.bind(qualify(value),len(parser.sources))
    finally:active.remove(key)


def insert_expressions(schema,supplied):
    """Expand field defaults in the expression domain, never evaluate values."""
    from .query_composites import tokens
    columns=dict((n,e) for e,n in schema);memo={};active=set()
    def expression(name):
        if name in memo:return memo[name]
        if name in active:raise UnsupportedSyntaxError('Цикл значений по умолчанию временной таблицы')
        active.add(name);e=columns[name];spec=descriptor(e)
        if spec.get('computed'):
            if name in supplied:raise UnsupportedSyntaxError('ВСТАВИТЬ не изменяет вычисляемое поле: '+name)
            raw=spec['computed']
        elif name in supplied:
            active.remove(name);memo[name]=cast_text(e,supplied[name]);return memo[name]
        elif spec.get('auto'):
            # Values are assigned at insertion time by the Script renderer.
            active.remove(name);memo[name]='0';return '0'
        elif spec.get('default') is not None:raw=spec['default']
        elif e.type.endswith('?'):raw='NULL'
        else:raise UnsupportedSyntaxError('Обязательное поле ВСТАВИТЬ отсутствует: '+name)
        edits=[]
        ts=tokens(raw)
        for i,t in enumerate(ts):
            if t[0]=='token' and t[1] in columns and (i==0 or ts[i-1][1] not in ('.','::')) and (i+1==len(ts) or ts[i+1][1]!='('):
                edits.append((t[2],t[3],'('+expression(t[1])+')'))
        for a,b,value in reversed(edits):raw=raw[:a]+value+raw[b:]
        active.remove(name);memo[name]=cast_text(e,raw)
        return memo[name]
    return [(expression(n),n) for _,n in schema]
