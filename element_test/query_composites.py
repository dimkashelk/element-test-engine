"""Typed UNION, derived tables and sequential batch queries, evaluated in Script.

Every nested parameter keeps its range in the original literal. Temporary rows
belong to one Execute call, never to Python or a process-global table cache.
"""
from dataclasses import dataclass, asdict, replace
import re
from .indexer import IDENT
from .query_joins import Expression
from .query_plan import QueryField, QueryParameter
from .query_projections import ComputedParser
from .yaml_io import UnsupportedSyntaxError

MODE = 'storage-unions-nesting-v1'

@dataclass(frozen=True)
class CompositeQuery:
    owner: str
    alias: str
    projections: tuple
    parameters: tuple = ()
    sources: tuple = ()
    operators: tuple = ()
    statements: tuple = ()
    ordering: tuple = ()
    limit: int | None = None
    fill: dict | None = None
    source_kind: str = 'union'
    mode: str = MODE
    source_name: str = ''
    source_range: tuple | None = None
    outer_parameters: tuple = ()

    def to_dict(self):
        return asdict(self)

@dataclass(frozen=True)
class Statement:
    operation: str
    table: str = ''
    query: object = None
    columns: tuple = ()
    index: tuple = ()
    range: tuple = ()


def tokens(text):
    return ComputedParser(text, None).tokens


def top_tokens(text):
    depth = 0
    for t in tokens(text):
        if t[0] != 'token': continue
        if t[1] == ')': depth -= 1
        if depth == 0: yield t
        if t[1] == '(': depth += 1
        if depth < 0: raise UnsupportedSyntaxError('Лишняя закрывающая скобка запроса')
    if depth: raise UnsupportedSyntaxError('Незакрытая скобка запроса')


def shift(query, offset):
    """Relocate parameter occurrences throughout a child AST, preserving slots."""
    kwargs={'parameters':tuple(replace(p,start=p.start+offset,end=p.end+offset) for p in query.parameters)}
    if hasattr(query,'sources'): kwargs['sources']=tuple(shift(q,offset) for q in query.sources)
    if hasattr(query,'subqueries'):kwargs['subqueries']=tuple(shift(q,offset) for q in query.subqueries)
    if hasattr(query,'statements'):
        kwargs['statements']=tuple(replace(s,query=shift(s.query,offset) if s.query else None,
                                         range=tuple(x+offset for x in s.range)) for s in query.statements)
    def expression(e):
        if not isinstance(e,Expression):return e
        return replace(e,range=tuple(x+offset for x in e.range),children=tuple(expression(c) for c in e.children))
    kwargs['projections']=tuple((expression(e),label) for e,label in query.projections)
    if getattr(query,'source_filter',None):kwargs['source_filter']=expression(query.source_filter)
    if query.source_kind=='relational':
        kwargs['predicates']=tuple(expression(e) for e in query.predicates)
        kwargs['ordering']=tuple((expression(e),d) for e,d in query.ordering)
        kwargs['grouping']=tuple(expression(e) for e in query.grouping)
        kwargs['having']=tuple(expression(e) for e in query.having)
        kwargs['joins']=tuple(replace(j,condition=expression(j.condition),range=tuple(x+offset for x in j.range)) for j in query.joins)
    if query.fill:
        kwargs['fill']={**query.fill,**{key:tuple(x+offset for x in query.fill[key]) for key in ('range','typeRange')},'mapping':tuple({**m,'range':tuple(x+offset for x in m['range'])} for m in query.fill['mapping'])}
    if hasattr(query,'source_range') and query.source_range:
        kwargs['source_range']=tuple(x+offset for x in query.source_range)
    if hasattr(query,'period_range') and query.period_range:
        kwargs['period_range']=tuple(x+offset for x in query.period_range)
    return replace(query,**kwargs)


def capture(queries):
    out=[];slots={};types={}
    for p in sorted((p for q in queries for p in q.parameters),key=lambda p:p.start):
        key=p.expression if re.fullmatch(IDENT,p.expression) else (p.start,p.end)
        slot=slots.setdefault(key,len(slots))
        typ=p.type
        if slot in types and types[slot]!=typ:
            if types[slot].rstrip('?')==typ.rstrip('?') and typ.rstrip('?') in {'Дата','ДатаВремя','Момент'}:typ=typ.rstrip('?')
            else:raise UnsupportedSyntaxError('Несовместимые типы общего captured параметра')
        types[slot]=typ;out.append(replace(p,slot=slot))
    return tuple(replace(p,type=types[p.slot]) for p in out)


def null_column(columns,index):
    name='Н'+str(index)
    labels={label for _,label in columns}
    while name in labels:name+='_'
    return name


def column_type(e):
    return e.type + ('?' if getattr(e,'sql_nullable',False) and not e.type.endswith('?') else '')


def nodes(query):
    yield query
    if getattr(query,'definition',None):yield from nodes(query.definition)
    for s in getattr(query,'sources',()): yield from nodes(s)
    for s in getattr(query,'statements',()):
        if s.query: yield from nodes(s.query)
    for s in getattr(query,'subqueries',()):yield from nodes(s)


def leaves(query):
    from .query_sources import LEAF_KINDS
    return (q for q in nodes(query) if q.source_kind in LEAF_KINDS)


def needs_context(query):
    return query.source_kind == 'temporary' or any(needs_context(s) for s in (*getattr(query,'sources',()),*getattr(query,'subqueries',())))


def internal(query):
    return query.source_kind in ('relational','union','temporary','batch','saved')


def row_type(query,name,private=False):
    if private and internal(query): return name+'.СтрокаДанных'
    return query.fill['type'] if query.fill else name+'.СтрокаРезультата'


class NestedParser(ComputedParser):
    def __init__(self,text,contracts,tables,outer=None,cardinality_only=False):
        super().__init__(text,contracts);self.tables=tables;self.subqueries=[]
        self.outer=outer;self.outer_parameters=[]
        self.cardinality_only=cardinality_only

    def source(self):
        if self.pos >= len(self.tokens): self.fail('Ожидается источник')
        start=self.tokens[self.pos][2]
        if self.accept('('):
            begin=self.tokens[self.pos][2];depth=1
            while self.pos < len(self.tokens) and depth:
                t=self.tokens[self.pos]
                if t[0]=='token':depth+=(t[1]=='(')-(t[1]==')')
                self.pos+=1
            if depth:self.fail('Незакрытый вложенный источник',start)
            end=self.tokens[self.pos-1][2]
            q=shift(parse_composite_query(self.text[begin:end],self.contracts,self.tables),begin)
            if q.source_kind=='batch':self.fail('Пакет во вложенном источнике недопустим',start)
            if self.accept('КАК'):alias=self.identifier()
            elif self.pos<len(self.tokens) and self.tokens[self.pos][0]=='token' and re.fullmatch(IDENT,self.tokens[self.pos][1]) and self.tokens[self.pos][1].upper() not in ('ГДЕ','СОЕДИНЕНИЕ','ВНУТРЕННЕЕ','ЛЕВОЕ','ПРАВОЕ','ПОЛНОЕ','СГРУППИРОВАТЬ','ИМЕЮЩИЕ','УПОРЯДОЧИТЬ'):
                alias=self.identifier()
            else:alias='ВложенныйИсточник'+str(len(self.sources))
        elif self.tokens[self.pos][1] in self.tables:
            name=self.identifier();schema=self.tables[name]
            q=CompositeQuery(name,name,schema,source_kind='temporary')
            alias=self.identifier() if self.accept('КАК') else name
        else:return super().source()
        if alias in [s.alias for s in self.sources]:self.fail('Повторяющийся псевдоним источника: '+alias,start)
        q=replace(q,alias=alias)
        self.sources.append(q);self.raw_sources.append('');self.fields.append({});self.used.append({})
        return len(self.sources)-1

    def field(self,e,visible):
        from .query_sources import LEAF_KINDS
        matching=[]
        for i,s in enumerate(self.sources[:visible]):
            if len(e.value)==2 and e.value[0]!=s.alias:continue
            if s.source_kind in LEAF_KINDS:
                names={f.name for f in s.fields}
            else:names={label for _,label in s.projections}
            if e.name in names:matching.append(i)
        if (not matching and self.outer and len(e.value)==2 and e.value[0] not in [s.alias for s in self.sources]
                and e.value[0] in [s.alias for s in self.outer.sources]):
            bound=self.outer.field(e,len(self.outer.sources))
            if bound.kind!='field':self.fail('Многоуровневая корреляция требует отдельный контракт',e.range[0])
            if bound.sql_nullable or bound.type.endswith('?'):
                self.fail('Корреляция nullable-внешнего поля требует отдельный NULL-контракт',e.range[0])
            existing=next((x[0] for x in self.outer_parameters if x[2:4]==(bound.source,bound.name)),None)
            key=existing or '__Outer45_'+str(bound.source)+'_'+bound.name
            if not existing:
                names={t[1] for t in self.tokens if t[0]=='parameter'}|{x[0] for x in self.outer_parameters}
                while key in names:key+='_'
                self.outer_parameters.append((key,bound.type,bound.source,bound.name,bound.null_field))
            from .query_plan import QueryParameter
            self.parameters.append(QueryParameter(key,e.range[0],e.range[1],bound.type,-1))
            return replace(e,kind='parameter',value=('parameter',key,*e.range),name='',source=None,type=bound.type)
        if len(matching)!=1:self.fail('Неизвестное или неоднозначное поле: '+'.'.join(e.value),e.range[0])
        i=matching[0];s=self.sources[i]
        if s.source_kind in LEAF_KINDS:return super().field(e,visible)
        index=next(n for n,(_,l) in enumerate(s.projections) if l==e.name)
        column=s.projections[index][0];typ=column_type(column)
        self.fields[i][e.name]=QueryField(s.owner,e.name,typ)
        nullable=i in self.nullable or getattr(column,'sql_nullable',False)
        return replace(e,source=i,type=typ if not nullable or typ.endswith('?') else typ+'?',
                       sql_nullable=nullable,null_field=null_column(s.projections,index))

    def comparison(self):
        # The parent comparison parser binds scalars and list IN separately.
        from .query_joins import Parser
        if self.accept('СУЩЕСТВУЕТ'):
            start=self.tokens[self.pos-1][2]
            self.expect('(')
            begin=self.tokens[self.pos][2];depth=1
            while self.pos<len(self.tokens) and depth:
                t=self.tokens[self.pos]
                if t[0]=='token':depth+=(t[1]=='(')-(t[1]==')')
                self.pos+=1
            if depth:self.fail('Незакрытый СУЩЕСТВУЕТ',start)
            end=self.tokens[self.pos-1][2]
            q=shift(parse_composite_query(self.text[begin:end],self.contracts,self.tables,outer=self,cardinality_only=True),begin)
            if q.source_kind=='batch':self.fail('СУЩЕСТВУЕТ требует SELECT',start)
            index=len(self.subqueries);self.subqueries.append(q)
            return Expression('exists-query','Булево',value=index,range=(start,self.tokens[self.pos-1][3]))
        e=Parser.comparison(self)
        negative=self.accept('НЕ')
        from .query_predicates import comparison_suffix
        extended=comparison_suffix(self,e,negative)
        if extended is not None:return extended
        if not self.accept('В'):
            if negative:self.fail('НЕ после выражения требует В')
            return e
        self.expect('(')
        if self.pos < len(self.tokens) and self.tokens[self.pos][1].upper()=='ВЫБРАТЬ':
            begin=self.tokens[self.pos][2];depth=1
            while self.pos < len(self.tokens) and depth:
                t=self.tokens[self.pos]
                if t[0]=='token':depth+=(t[1]=='(')-(t[1]==')')
                self.pos+=1
            if depth:self.fail('Незакрытый подзапрос В')
            end=self.tokens[self.pos-1][2]
            q=shift(parse_composite_query(self.text[begin:end],self.contracts,self.tables,outer=self),begin)
            if len(q.projections)!=1 or q.source_kind=='batch':self.fail('Подзапрос В требует одну колонку')
            index=len(self.subqueries);self.subqueries.append(q)
            e=Expression('in-query','Булево',value=index,children=(e,),range=(e.range[0],self.tokens[self.pos-1][3]))
        else:
            items=[]
            while True:
                items.append(self.expression())
                if not self.accept(','):break
            self.expect(')')
            kind='in-capture' if len(items)==1 and items[0].kind=='parameter' else 'in'
            e=Expression(kind,'Булево',children=(e,*items),range=(e.range[0],self.tokens[self.pos-1][3]))
        return Expression('not','Булево',children=(e,),range=e.range) if negative else e

    def bind(self,e,visible,expected=None):
        if e.kind=='exists-query':
            q=self.subqueries[e.value]
            outer={x[0] for x in q.outer_parameters}
            self.parameters.extend(p for p in q.parameters if p.expression not in outer)
            return e
        if e.kind=='in-capture':
            capture=e.children[1]
            inferred=getattr(self.contracts,'query_parameter_type',lambda *args:None)(capture.value[1],capture.range[0])
            is_array=inferred.startswith('Массив<') if inferred else bool(re.search(r'\.Преобразовать\s*\(|\bновый\s+Массив<',capture.value[1]))
            if not is_array:
                return super().bind(replace(e,kind='in'),visible,expected)
            first=self.bind(e.children[0],visible)
            array=self.bind(e.children[1],visible,'Массив<'+first.type+'>')
            return replace(e,kind='in-array',children=(first,array),sql_nullable=first.sql_nullable)
        if e.kind=='in-query':
            q=self.subqueries[e.value];typ=q.projections[0][0].type
            if q.outer_parameters:self.fail('Коррелированный В требует отдельный контракт',e.range[0])
            left=self.bind(e.children[0],visible,typ.rstrip('?'))
            if typ!='Объект?' and left.type.rstrip('?')!=typ.rstrip('?'):self.fail('Несовместимые типы подзапроса В',e.range[0])
            outer={x[0] for x in q.outer_parameters}
            self.parameters.extend(p for p in q.parameters if p.expression not in outer)
            return replace(e,children=(left,),sql_nullable=left.sql_nullable or getattr(q.projections[0][0],'sql_nullable',False))
        return super().bind(e,visible,expected)

    def parse(self):
        q=super().parse()
        return replace(q,subqueries=tuple(self.subqueries),outer_parameters=tuple(self.outer_parameters))

    def prune_source(self,i,s):
        from .query_sources import LEAF_KINDS
        if s.source_kind not in LEAF_KINDS:return s
        return super().prune_source(i,s)


def parse_select(text,contracts,tables,outer=None,cardinality_only=False):
    try:
        q=NestedParser(text,contracts,tables,outer,cardinality_only).parse()
        return replace(q,mode=MODE) if q.subqueries or any(s.source_kind not in ('ordinary','slice-last') for s in q.sources) else q
    except IndexError as e:raise UnsupportedSyntaxError('Незавершённый вложенный запрос') from e


def parse_composite_query(text,contracts,tables=None,outer=None,cardinality_only=False):
    tables={} if tables is None else tables
    top=list(top_tokens(text));separators=[t for t in top if t[1]==';']
    structural=any(t[1].upper() in ('ПОМЕСТИТЬ','СОЗДАТЬ','УНИЧТОЖИТЬ','ОБРЕЗАТЬ','ВСТАВИТЬ','ИЗМЕНИТЬ','УДАЛИТЬ') for t in top)
    if separators or structural:
        if outer:raise UnsupportedSyntaxError('Пакет не допускается в предикатном подзапросе')
        return parse_batch(text,contracts,tables,separators)
    unions=[t for t in top if t[1].upper()=='ОБЪЕДИНИТЬ']
    if not unions:return parse_select(text,contracts,tables,outer,cardinality_only)
    order_token=next((t for t in top if t[1].upper()=='УПОРЯДОЧИТЬ'),None)
    order_text=''
    if order_token:
        if order_token[2]<unions[-1][2]:raise UnsupportedSyntaxError('Сортировка UNION должна следовать после всех ветвей')
        order_text=text[order_token[2]:];text=text[:order_token[2]]
    branches=[];ops=[];begin=0
    for t in unions:
        branches.append(shift(parse_select(text[begin:t[2]],contracts,tables,outer,cardinality_only),begin))
        begin=t[3]
        m=re.match(r'\s*(ВСЕ|РАЗЛИЧНЫЕ)\b',text[begin:],re.I)
        ops.append('all' if m and m[1].upper()=='ВСЕ' else 'distinct')
        if m:begin+=m.end()
    branches.append(shift(parse_select(text[begin:],contracts,tables,outer,cardinality_only),begin))
    width=len(branches[0].projections)
    if any(len(b.projections)!=width for b in branches):raise UnsupportedSyntaxError('UNION требует одинаковое число колонок')
    if any(b.fill for b in branches[1:]):raise UnsupportedSyntaxError('ЗАПОЛНИТЬ задаётся только в первой ветви UNION')
    projections=[]
    for i,(first,label) in enumerate(branches[0].projections):
        columns=[b.projections[i][0] for b in branches];types={e.type.rstrip('?') for e in columns if e.type!='Объект?'}
        if len(types)>1:raise UnsupportedSyntaxError('Несовместимые номинальные типы UNION: '+', '.join(sorted(types)))
        typ=next(iter(types),'Объект')
        if any(column_type(e).endswith('?') for e in columns):typ+='?'
        projections.append((Expression('column',typ,sql_nullable=any(getattr(e,'sql_nullable',False) for e in columns)),label))
    if branches[0].fill:
        declared={f['Имя']:f['Тип'] for f in branches[0].fill['fields']}
        for e,label in projections:
            if e.type!=declared[label] and e.type+'?'!=declared[label]:
                raise UnsupportedSyntaxError('Несовместимые типы UNION/ЗАПОЛНИТЬ: '+label)
    ordering=[]
    if order_text:
        m=re.fullmatch(r'УПОРЯДОЧИТЬ\s+ПО\s+(.+?)\s*',order_text,re.I|re.S)
        if not m:raise UnsupportedSyntaxError('Некорректная сортировка UNION')
        for key in m[1].split(','):
            m=re.fullmatch(rf'\s*({IDENT})\s*(УБЫВ|ВОЗР)?\s*',key,re.I)
            if not m or m[1] not in [l for _,l in projections]:raise UnsupportedSyntaxError('Сортировка UNION требует псевдоним первой ветви')
            i=next(i for i,(_,l) in enumerate(projections) if l==m[1])
            if projections[i][0].type.rstrip('?') not in ('Число','Строка','Дата','ДатаВремя'):raise UnsupportedSyntaxError('Сортировка UNION требует скаляр')
            ordering.append((i,m[2] and m[2].upper()=='УБЫВ'))
    outer_parameters=tuple(dict.fromkeys(x for branch in branches for x in branch.outer_parameters))
    return CompositeQuery(branches[0].owner,branches[0].alias,tuple(projections),capture(branches),tuple(branches),tuple(ops),ordering=tuple(ordering),fill=branches[0].fill,outer_parameters=outer_parameters)


def parse_batch(text,contracts,outer,separators):
    tables=dict(outer);steps=[];queries=[];indices={}
    ends=[t[2] for t in separators]+[len(text)]
    starts=[0]+[t[3] for t in separators]
    for a,b in zip(starts,ends):
        raw=text[a:b];code=raw.strip()
        if not code:
            if b==len(text):continue
            raise UnsupportedSyntaxError('Пустой оператор пакета')
        span=(a,b)
        from .query_state import parse_mutation
        mutation=parse_mutation(raw,contracts,tables,a)
        if mutation:
            steps.append(mutation);queries.append(mutation.query);continue
        create=re.fullmatch(rf'\s*СОЗДАТЬ\s+ВРЕМЕННУЮ\s+ТАБЛИЦУ\s+({IDENT})\s*\((.*)\)\s*',raw,re.I|re.S)
        index=re.fullmatch(rf'\s*СОЗДАТЬ\s+ИНДЕКС\s+({IDENT})\s+ДЛЯ\s+({IDENT})\s*\((.*?)\)\s*(?:ДОПОЛНИТЕЛЬНО\s+ПО\s*\((.*?)\))?\s*',raw,re.I|re.S)
        drop=re.fullmatch(rf'\s*(УНИЧТОЖИТЬ|ОБРЕЗАТЬ)\s+({IDENT})\s*',raw,re.I)
        if create:
            name=create[1]
            if name in tables:raise UnsupportedSyntaxError('Временная таблица уже существует: '+name)
            columns=[]
            for declaration in create[2].split(','):
                m=re.fullmatch(rf'\s*({IDENT})\s*:\s*({IDENT}(?:::{IDENT})*(?:\.Ссылка)?\??)\s*',declaration)
                if not m:raise UnsupportedSyntaxError('Определение поля временной таблицы вне подтверждённого контракта')
                typ=contracts.canonical_type(m[2]);contracts.require(typ)
                if m[1] in [l for _,l in columns]:raise UnsupportedSyntaxError('Повторяющееся поле временной таблицы')
                columns.append((Expression('column',typ),m[1]))
            tables[name]=tuple(columns);steps.append(Statement('create',name,columns=tuple(columns),range=span));continue
        if drop:
            name=drop[2]
            if name not in tables:raise UnsupportedSyntaxError('Неизвестная временная таблица: '+name)
            steps.append(Statement('drop' if drop[1].upper()=='УНИЧТОЖИТЬ' else 'truncate',name,range=span))
            if drop[1].upper()=='УНИЧТОЖИТЬ':del tables[name];indices.pop(name,None)
            continue
        if index:
            name=index[2];fields=tuple(x.strip() for x in (index[3]+(','+index[4] if index[4] else '')).split(','))
            validate_index(name,fields,tables,indices)
            steps.append(Statement('index',name,index=fields,range=span));continue
        top=list(top_tokens(raw));into=next((t for t in top if t[1].upper()=='ПОМЕСТИТЬ'),None)
        indexed=next((t for t in top if t[1].upper()=='ИНДЕКСИРОВАТЬ'),None)
        edited=raw
        index_fields=()
        if indexed:
            m=re.fullmatch(r'ИНДЕКСИРОВАТЬ\s+ПО\s+(.+?)\s*',raw[indexed[2]:],re.I|re.S)
            if not m:raise UnsupportedSyntaxError('Некорректное ИНДЕКСИРОВАТЬ ПО')
            index_fields=tuple(x.strip() for x in m[1].split(','));edited=edited[:indexed[2]]+' '*(len(raw)-indexed[2])
        name=''
        if into:
            m=re.match(rf'ПОМЕСТИТЬ\s+({IDENT})',raw[into[2]:],re.I)
            if not m:raise UnsupportedSyntaxError('ПОМЕСТИТЬ требует имя таблицы')
            name=m[1]
            if name in tables:raise UnsupportedSyntaxError('Временная таблица уже существует: '+name)
            edited=edited[:into[2]]+' '*m.end()+edited[into[2]+m.end():]
        q=shift(parse_composite_query(edited,contracts,tables),a);queries.append(q)
        if name:
            tables[name]=q.projections
            if index_fields:
                # The source field name and projected alias both identify the
                # same field. No unprojected column can become an index key.
                mapped=[]
                for key in index_fields:
                    candidates=[label for e,label in q.projections if key==label or key==getattr(e,'name','') or getattr(e,'kind','')=='field' and key=='.'.join(e.value)]
                    if len(candidates)!=1:raise UnsupportedSyntaxError('Индекс требует выбранное поле: '+key)
                    mapped.append(candidates[0])
                validate_index(name,tuple(mapped),tables,indices);index_fields=tuple(mapped)
        elif index_fields:raise UnsupportedSyntaxError('ИНДЕКСИРОВАТЬ ПО требует ПОМЕСТИТЬ')
        steps.append(Statement('into' if name else 'select',name,q,index=index_fields,range=span))
    if not steps:raise UnsupportedSyntaxError('Пустой пакет')
    from .query_state import COUNT
    last=steps[-1];projections=last.query.projections if last.operation=='select' else COUNT if last.operation in ('insert','update','delete') else ()
    return CompositeQuery('', '', projections,capture(queries),statements=tuple(steps),source_kind='batch',fill=last.query.fill if last.operation=='select' else None)


def validate_index(name,fields,tables,indices):
    if name not in tables:raise UnsupportedSyntaxError('Индекс требует существующую временную таблицу: '+name)
    if name in indices:raise UnsupportedSyntaxError('У временной таблицы может быть только один индекс')
    names={label for _,label in tables[name]}
    if len(set(fields))!=len(fields) or not fields or any(f not in names for f in fields):raise UnsupportedSyntaxError('Неизвестное или повторяющееся поле индекса')
    indices[name]=fields


def declaration(name,columns,contracts,flags=False):
    text='@Глобально\nструктура '+name+'\n'
    text+=''.join('    знч '+label+': '+contracts.sbsl_type(column_type(e))+'\n' for e,label in columns)
    if flags:text+=''.join('    знч '+null_column(columns,i)+': Булево\n' for i in range(len(columns)))
    return text+';\n'


def public_result(query,contracts):
    result=query.fill['type'] if query.fill else 'СтрокаРезультата'
    text='' if query.fill else declaration(result,query.projections,contracts)
    from .query_construction import constructor_args
    text+='    @Глобально\n    метод Выполнить(): Массив<'+result+'>\n        знч Результат = новый Массив<'+result+'>()\n        для С из ВыполнитьВнутренне()\n            Результат.Добавить(новый '+result+'('+constructor_args(query, ['С.'+label for _,label in query.projections])+'))\n        ;\n        возврат Результат\n    ;\n'
    return text


def mapped_args(parent,child):
    from .storage_queries import unique_parameters
    return ['П'+str(next(x.slot for x in parent.parameters if (x.start,x.end)==(p.start,p.end))) for p in unique_parameters(child)]


def generate_composite_query(query,contracts):
    from .storage_queries import query_name,unique_parameters,generate_query
    name=query_name(query)
    if name in contracts.definitions:return name
    params=unique_parameters(query)
    sig=', '.join('П'+str(p.slot)+': '+contracts.sbsl_type(p.type) for p in params)
    def captured(p):
        v='П'+str(p.slot)
        if p.type.startswith('Массив<'):
            return 'СериализацияJson.ПрочитатьОбъект<'+contracts.sbsl_type(p.type)+'>(СериализацияJson.ЗаписатьОбъект('+v+'), Тип<'+contracts.sbsl_type(p.type)+'>)'
        if p.type.rstrip('?').endswith('.Ссылка'):
            clone='новый '+p.type.rstrip('?')+'(Идентификатор = '+v+'.Идентификатор)'
            return '('+v+' == Неопределено ? Неопределено : '+clone+')' if p.type.endswith('?') else clone
        return v
    args=['П'+str(p.slot)+' = '+captured(p) for p in params]
    fields=''.join('    знч П'+str(p.slot)+': '+contracts.sbsl_type(p.type)+'\n' for p in params)
    context='Контекст: Соответствие<Строка, Строка>'
    children=[]
    text=declaration('СтрокаДанных',query.projections,contracts,flags=True)
    public=public_result(query,contracts);decl_end=public.index('    @Глобально')
    text+=public[:decl_end]+'@Глобально\nструктура Запрос\n'+fields
    if query.source_kind=='temporary':
        text+='    знч '+context+'\n'+public[decl_end:]
        text+='    @Глобально\n    метод ВыполнитьВнутренне(): Массив<СтрокаДанных>\n        возврат СериализацияJson.ПрочитатьОбъект<Массив<СтрокаДанных>>(Контекст['+contracts.literal(query.owner,'Строка')+'], новый Массив<СтрокаДанных>().ПолучитьТип())\n    ;\n;\n'
        sig=context;args=['Контекст = Контекст']
    elif query.source_kind=='union':
        children=[generate_query(s,contracts) for s in query.sources]
        text+=''.join('    знч И'+str(i)+': '+n+'.Запрос\n' for i,n in enumerate(children))
        text+=public[decl_end:]+'    @Глобально\n    метод ВыполнитьВнутренне(): Массив<СтрокаДанных>\n        пер Результат = новый Массив<СтрокаДанных>()\n'
        for i,(s,n) in enumerate(zip(query.sources,children)):
            call='ВыполнитьВнутренне' if internal(s) else 'Выполнить'
            if i and query.operators[i-1]=='distinct':
                # DISTINCT applies to the whole left-hand bag as well.
                text+='        Результат = '+name+'.Различные(Результат)\n'
            text+='        для С из И'+str(i)+'.'+call+'()\n            знч Новая = новый СтрокаДанных('+', '.join(label+' = '+('С.'+s.projections[j][1] if column_type(s.projections[j][0])==column_type(query.projections[j][0]) else '(С.'+s.projections[j][1]+' как '+column_type(query.projections[j][0])+')')+', '+null_column(query.projections,j)+' = '+('С.'+null_column(s.projections,j) if internal(s) else 'Ложь') for j,(_,label) in enumerate(query.projections))+')\n'
            if i and query.operators[i-1]=='distinct':text+='            если '+name+'.Содержит(Результат, Новая)\n                продолжить\n            ;\n'
            text+='            Результат.Добавить(Новая)\n        ;\n'
        if query.ordering:
            text+='        знч Отсортированные = новый Массив<СтрокаДанных>()\n        для С из Результат\n            пер Позиция = 0\n            пока Позиция < Отсортированные.Размер() и не '+name+'.Раньше(С, Отсортированные[Позиция])\n                Позиция += 1\n            ;\n            Отсортированные.Вставить(Позиция, С)\n        ;\n        Результат = Отсортированные\n'
        text+='        возврат Результат\n    ;\n;\n'
        if query.ordering:
            text+='@Глобально\nметод Раньше(А: СтрокаДанных, Б: СтрокаДанных): Булево\n'
            for i,desc in query.ordering:
                e,label=query.projections[i];a='А.'+label;b='Б.'+label
                text+='    если А.'+null_column(query.projections,i)+' != Б.'+null_column(query.projections,i)+'\n        возврат '+('Б.' if desc else 'А.')+null_column(query.projections,i)+'\n    ;\n    если не А.'+null_column(query.projections,i)+' и '+a+' != '+b+'\n        возврат ('+a+' как '+e.type.rstrip('?')+') '+('>' if desc else '<')+' ('+b+' как '+e.type.rstrip('?')+')\n    ;\n'
            text+='    возврат Ложь\n;\n'
        eq=' и '.join('(А.'+null_column(query.projections,i)+' == Б.'+null_column(query.projections,i)+' и (А.'+null_column(query.projections,i)+' или А.'+label+' == Б.'+label+'))' for i,(_,label) in enumerate(query.projections))
        text+='@Глобально\nметод Содержит(Строки: Массив<СтрокаДанных>, Б: СтрокаДанных): Булево\n    для А из Строки\n        если '+eq+'\n            возврат Истина\n        ;\n    ;\n    возврат Ложь\n;\n'
        text+='@Глобально\nметод Различные(Строки: Массив<СтрокаДанных>): Массив<СтрокаДанных>\n    знч Итог = новый Массив<СтрокаДанных>()\n    для С из Строки\n        если не Содержит(Итог, С)\n            Итог.Добавить(С)\n        ;\n    ;\n    возврат Итог\n;\n'
        construction=''
        for i,(s,n) in enumerate(zip(query.sources,children)):
            values=mapped_args(query,s)+(['Контекст'] if needs_context(s) else [])
            construction+='    знч Источник'+str(i)+' = '+n+'.Создать('+', '.join(values)+')\n';args.append('И'+str(i)+' = Источник'+str(i))
        if needs_context(query):sig+=(', ' if sig else '')+context
    else:
        text+=public[decl_end:]+'    @Глобально\n    метод ВыполнитьВнутренне(): Массив<СтрокаДанных>\n        знч Контекст = новый Соответствие<Строка, Строка>()\n'
        for i,step in enumerate(query.statements):
            key=contracts.literal(step.table,'Строка')
            if step.query:
                if step.operation=='insert':
                    from .query_state import SINGLETON
                    text+='        Контекст.Вставить('+contracts.literal(SINGLETON,'Строка')+', '+contracts.literal('[{"Marker":0,"Н0":false}]','Строка')+')\n'
                n=generate_query(step.query,contracts);children.append(n)
                values=mapped_args(query,step.query)+(['Контекст'] if needs_context(step.query) else [])
                call='ВыполнитьВнутренне' if internal(step.query) else 'Выполнить'
                text+='        знч Шаг'+str(i)+' = '+n+'.Создать('+', '.join(values)+').'+call+'()\n'
                if step.operation=='into':text+='        Контекст.Вставить('+key+', СериализацияJson.ЗаписатьОбъект(Шаг'+str(i)+'))\n'
                if step.operation in ('insert','update','delete'):
                    from .query_state import render_mutation
                    declaration_,body=render_mutation(step,i,step.query,contracts)
                    text=declaration_+text+body
            elif step.operation=='create' or step.operation=='truncate':text+='        Контекст.Вставить('+key+', "[]")\n'
            elif step.operation=='drop':text+='        Контекст.Удалить('+key+')\n'
            # An index is a validated performance hint: no bag/order semantics.
        text+='        знч Результат = новый Массив<СтрокаДанных>()\n'
        last=query.statements[-1]
        if last.operation=='select':
            s=last.query
            text+='        для С из Шаг'+str(len(query.statements)-1)+'\n            Результат.Добавить(новый СтрокаДанных('+', '.join(label+' = С.'+label+', '+null_column(query.projections,i)+' = '+('С.'+null_column(s.projections,i) if internal(s) else 'Ложь') for i,(_,label) in enumerate(query.projections))+'))\n        ;\n'
        elif last.operation in ('insert','update','delete'):
            text+='        Результат.Добавить(новый СтрокаДанных(КоличествоЗаписей = Количество'+str(len(query.statements)-1)+', Н0 = Ложь))\n'
        text+='        возврат Результат\n    ;\n;\n'
    construction=locals().get('construction','')
    text+='@Глобально\nметод Создать('+sig+'): Запрос\n'+construction+'    возврат новый Запрос('+', '.join(args)+')\n;\n'
    contracts.definitions[name]=text;contracts.method_dependencies[name]=[n+'.Запрос' for n in children]+[e.type for e,_ in query.projections]
    if query.fill:contracts.method_dependencies[name].append(query.fill['type'])
    return name
