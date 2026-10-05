"""Temporary-table DML only; stage 45 never writes platform source tables."""
from dataclasses import fields,is_dataclass,replace
import re
from .indexer import IDENT
from .query_joins import Expression
from .yaml_io import UnsupportedSyntaxError

SINGLETON='__ТестОднаСтрока45'
COUNT=((Expression('column','Число'),'КоличествоЗаписей'),)


def relocated(q,segments,span):
    def at(x):
        for start,end,original in segments:
            if start<=x<=end:return original+x-start
        return None
    def visit(v):
        if isinstance(v,tuple):return tuple(visit(x) for x in v)
        if isinstance(v,list):return [visit(x) for x in v]
        if not is_dataclass(v):return v
        changes={f.name:visit(getattr(v,f.name)) for f in fields(v)}
        if isinstance(v,Expression) and v.range:
            mapped=tuple(at(x) for x in v.range)
            changes['range']=mapped if all(x is not None for x in mapped) else ()
        if hasattr(v,'start') and hasattr(v,'end'):
            changes['start']=at(v.start);changes['end']=at(v.end)
            if changes['start'] is None or changes['end'] is None:raise UnsupportedSyntaxError('Потерян исходный диапазон параметра DML')
        return replace(v,**changes)
    return visit(q)


def parse_mutation(raw,contracts,tables,offset):
    from .query_composites import Statement,parse_select,tokens,top_tokens,shift,column_type
    ts=tokens(raw)
    if not ts or ts[0][1].upper() not in ('ВСТАВИТЬ','ИЗМЕНИТЬ','УДАЛИТЬ'):return None
    operation={'ВСТАВИТЬ':'insert','ИЗМЕНИТЬ':'update','УДАЛИТЬ':'delete'}[ts[0][1].upper()]
    pos=1
    def expect(word):
        nonlocal pos
        if pos>=len(ts) or ts[pos][1].upper()!=word:raise UnsupportedSyntaxError('Оператор изменения: ожидается '+word)
        pos+=1
    if operation=='insert':expect('В')
    if operation=='delete':expect('ИЗ')
    if pos>=len(ts) or not re.fullmatch(IDENT,ts[pos][1]):raise UnsupportedSyntaxError('Оператор изменения требует временную таблицу')
    table=ts[pos][1];pos+=1
    if table not in tables:raise UnsupportedSyntaxError('Изменять можно только временную таблицу: '+table)
    schema=tables[table];names=[n for _,n in schema];span=(offset,offset+len(raw));segments=[];targets=[]
    text='ВЫБРАТЬ '
    def append(fragment,origin=None):
        nonlocal text
        if origin is not None:segments.append((len(text),len(text)+len(fragment),offset+origin))
        text+=fragment
    def expression_range(a,b):return raw[a:b].strip(),a+len(raw[a:b])-len(raw[a:b].lstrip())
    def split(a,b):
        parts=[];start=a
        for t in top_tokens(raw[a:b]):
            if t[1]==',':parts.append((start,a+t[2]));start=a+t[3]
        parts.append((start,b));return parts
    if operation=='insert':
        expect('(')
        while True:
            if pos>=len(ts) or ts[pos][1] not in names:raise UnsupportedSyntaxError('Неизвестное поле ВСТАВИТЬ')
            targets.append(ts[pos][1]);pos+=1
            if pos<len(ts) and ts[pos][1]==',':pos+=1;continue
            expect(')');break
        if len(set(targets))!=len(targets):raise UnsupportedSyntaxError('Повторяющееся поле ВСТАВИТЬ')
        if pos>=len(ts):raise UnsupportedSyntaxError('ВСТАВИТЬ требует значения или SELECT')
        if ts[pos][1].upper()=='ВЫБРАТЬ':
            q=shift(parse_select(raw[ts[pos][2]:],contracts,tables),offset+ts[pos][2])
            if len(q.projections)!=len(targets):raise UnsupportedSyntaxError('Число полей ВСТАВИТЬ не совпадает')
            # Preserve original query ranges; mapping is positional, labels may differ.
            projections=list(q.projections)
            for i,((e,label),target) in enumerate(zip(projections,targets)):
                typ=column_type(next(c for c,n in schema if n==target))
                if e.kind=='null':projections[i]=(replace(e,type=typ),label)
            q=replace(q,projections=tuple(projections))
            for (e,_),target in zip(q.projections,targets):
                typ=column_type(next(c for c,n in schema if n==target))
                if column_type(e) not in (typ,typ.rstrip('?')):raise UnsupportedSyntaxError('Несовместимый тип ВСТАВИТЬ: '+target)
            missing=[n for e,n in schema if n not in targets and not e.type.endswith('?')]
            if missing:raise UnsupportedSyntaxError('Обязательные поля ВСТАВИТЬ отсутствуют: '+', '.join(missing))
            return Statement('insert',table,q,columns=schema,index=tuple(targets),range=span)
        expect('ЗНАЧЕНИЯ');expect('(')
        begin=ts[pos][2]
        if ts[-1][1]!=')':raise UnsupportedSyntaxError('Незакрытые ЗНАЧЕНИЯ')
        parts=split(begin,ts[-1][2])
        if len(parts)!=len(targets):raise UnsupportedSyntaxError('Число значений ВСТАВИТЬ не совпадает')
        values=dict(zip(targets,parts))
        for i,(e,n) in enumerate(schema):
            if i:append(', ')
            if n in values:
                a,b=values[n];fragment,a=expression_range(a,b)
                append('ВЫРАЗИТЬ(');append(fragment,a);append(' КАК '+e.type.rstrip('?')+')')
            elif e.type.endswith('?'):append('NULL')
            else:raise UnsupportedSyntaxError('Обязательное поле ВСТАВИТЬ отсутствует: '+n)
            append(' КАК '+n)
        append(' ИЗ '+SINGLETON)
        local={**tables,SINGLETON:((Expression('column','Число'),'Marker'),)}
        q=relocated(parse_select(text,contracts,local),segments,span)
        q=replace(q,projections=tuple((replace(e,type=column_type(schema[i][0])) if e.kind=='null' else e,n) for i,(e,n) in enumerate(q.projections)))
        for i,(e,_) in enumerate(q.projections):
            typ=column_type(schema[i][0])
            if column_type(e) not in (typ,typ.rstrip('?')):raise UnsupportedSyntaxError('Несовместимый тип ЗНАЧЕНИЯ: '+names[i])
        return Statement('insert',table,q,columns=schema,index=tuple(names),range=span)
    # Evaluate predicate and all assignments on the original row, then publish
    # one complete replacement bag. Duplicate rows remain distinct occurrences.
    if operation=='update':expect('УСТАНОВИТЬ')
    rest_start=ts[pos][2] if pos<len(ts) else len(raw)
    top=list(top_tokens(raw[rest_start:]));where=next((t for t in top if t[1].upper()=='ГДЕ'),None)
    if any(t[1].upper() in ('ИЗ','ИСПОЛЬЗУЯ') for t in top):raise UnsupportedSyntaxError('DML с несколькими источниками требует отдельный контракт')
    end=rest_start+where[2] if where else len(raw)
    assignments=[]
    if operation=='update':
        for a,b in split(rest_start,end):
            m=re.match(rf'\s*({IDENT})\s*=\s*',raw[a:b])
            if not m or m[1] not in names or m[1] in targets:raise UnsupportedSyntaxError('Неизвестное или повторяющееся поле ИЗМЕНИТЬ')
            targets.append(m[1]);assignments.append((a+m.end(),b))
    elif raw[rest_start:end].strip():raise UnsupportedSyntaxError('УДАЛИТЬ: неожиданный оператор')
    append(', '.join(names))
    used=set(names)
    def alias():
        value='__Состояние45'
        while value in used:value+='_'
        used.add(value);return value
    for target,(a,b) in zip(targets,assignments):
        e=next(e for e,n in schema if n==target);fragment,a=expression_range(a,b)
        append(', ВЫРАЗИТЬ(');append(fragment,a);append(' КАК '+e.type.rstrip('?')+') КАК '+alias())
    append(', ')
    if where:
        a=rest_start+where[3];fragment,a=expression_range(a,len(raw));append(fragment,a)
    else:append('Истина')
    append(' КАК '+alias()+' ИЗ '+table)
    q=relocated(parse_select(text,contracts,tables),segments,span)
    if operation=='update':
        projections=list(q.projections)
        predicate=projections[-1][0]
        for k,target in enumerate(targets):
            i=len(schema)+k;e,label=projections[i]
            original=projections[names.index(target)][0]
            projections[i]=(Expression('case',e.type,children=(predicate,e,original),range=e.range,sql_nullable=e.sql_nullable or original.sql_nullable),label)
        q=replace(q,projections=tuple(projections))
    for target,(e,_) in zip(targets,q.projections[len(schema):]):
        typ=column_type(next(c for c,n in schema if n==target))
        if column_type(e) not in (typ,typ.rstrip('?')):raise UnsupportedSyntaxError('Nullable assignment несовместим с '+target)
    return Statement(operation,table,q,columns=schema,index=tuple(targets),range=span)


def render_mutation(step,i,child,contracts):
    from .query_composites import declaration,null_column
    n='ТаблицаИзменений'+str(i);key=contracts.literal(step.table,'Строка')
    declarations=declaration(n,step.columns,contracts,flags=True)
    text='        пер Количество'+str(i)+' = 0\n        знч Новые'+str(i)+' = новый Массив<'+n+'>()\n'
    if step.operation=='insert':
        text+='        для Старый из СериализацияJson.ПрочитатьОбъект<Массив<'+n+'>>(Контекст['+key+'], Тип<Массив<'+n+'>>)\n            Новые'+str(i)+'.Добавить(Старый)\n        ;\n'
    text+='        для С из Шаг'+str(i)+'\n'
    match='Истина' if step.operation=='insert' else 'С.'+child.projections[-1][1]+' == Истина'
    text+='            если '+match+'\n                Количество'+str(i)+' += 1\n'
    if step.operation=='delete':text+='                продолжить\n'
    text+='            ;\n'
    values=[]
    for j,(e,label) in enumerate(step.columns):
        if step.operation=='insert':
            if label in step.index:
                k=step.index.index(label);value='С.'+child.projections[k][1];null='С.'+null_column(child.projections,k)
            else:value='Неопределено';null='Истина'
        else:
            value='С.'+child.projections[j][1];null='С.'+null_column(child.projections,j)
            if step.operation=='update' and label in step.index:
                k=len(step.columns)+step.index.index(label)
                value='('+match+' ? С.'+child.projections[k][1]+' : '+value+')'
                null='('+match+' ? С.'+null_column(child.projections,k)+' : '+null+')'
        values.extend((label+' = '+value,null_column(step.columns,j)+' = '+null))
    text+='            Новые'+str(i)+'.Добавить(новый '+n+'('+', '.join(values)+'))\n        ;\n        Контекст.Вставить('+key+', СериализацияJson.ЗаписатьОбъект(Новые'+str(i)+'))\n'
    return declarations,text
