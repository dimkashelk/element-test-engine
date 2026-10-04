"""Typed relational query AST. NULL is a missing row, distinct from Undefined."""
from dataclasses import dataclass, asdict, replace
import json
import re

from .indexer import IDENT, mask_noncode
from .query_plan import QueryParameter, parse_storage_query
from .yaml_io import UnsupportedSyntaxError


@dataclass(frozen=True)
class Expression:
    kind: str
    type: str = ''
    name: str = ''
    source: int | None = None
    value: object = None
    children: tuple = ()
    range: tuple = ()
    sql_nullable: bool = False


@dataclass(frozen=True)
class Join:
    kind: str
    source: int
    condition: Expression
    range: tuple


@dataclass(frozen=True)
class RelationalQuery:
    owner: str
    alias: str
    projections: tuple
    predicates: tuple
    parameters: tuple
    ordering: tuple
    limit: int | None
    sources: tuple
    joins: tuple
    fill: dict | None = None
    mode: str = 'storage-relational-joins-null-v1'
    source_kind: str = 'relational'

    def to_dict(self):
        return asdict(self)


class Parser:
    def __init__(self, text, contracts):
        self.text, self.contracts = text, contracts
        self.tokens, self.pos, self.sources, self.raw_sources = [], 0, [], []
        self.fields, self.used, self.nullable = [], [], set()
        self.parameters = []
        visible, hidden = mask_noncode(text, strings=False), mask_noncode(text)
        pattern = re.compile(rf'{IDENT}|\d+(?:\.\d+)?|::|==|!=|<>|<=|>=|[().,=<>-]')
        i = 0
        while i < len(text):
            if visible[i].isspace():
                i += 1
                continue
            if visible[i] == '%':
                a = i + 1
                if a < len(text) and text[a] == '{':
                    b, depth = a + 1, 1
                    while b < len(text) and depth:
                        depth += (hidden[b] == '{') - (hidden[b] == '}')
                        b += 1
                    if depth or not text[a+1:b-1].strip():
                        self.fail('Незакрытый или пустой параметр', i)
                    self.tokens.append(('parameter', text[a+1:b-1], a+1, b-1)); i = b
                else:
                    m = re.match(IDENT, text[a:])
                    if not m: self.fail('Некорректный параметр', i)
                    b = a + len(m[0]); self.tokens.append(('parameter', m[0], a, b)); i = b
                continue
            if visible[i] == '"':
                b = i + 1
                while b < len(text):
                    if text[b] == '\\': b += 2
                    elif text[b] == '"': break
                    else: b += 1
                if b == len(text): self.fail('Незакрытая строка', i)
                try: value = json.loads(text[i:b+1])
                except ValueError: self.fail('Строковый литерал вне контракта', i)
                self.tokens.append(('string', value, i, b+1)); i = b+1
                continue
            m = pattern.match(visible, i)
            if not m: self.fail('Запрос вне relational AST', i)
            self.tokens.append(('token', m[0], i, m.end())); i = m.end()

    def fail(self, reason, at=None):
        if at is None: at = self.tokens[self.pos][2] if self.pos < len(self.tokens) else len(self.text)
        raise UnsupportedSyntaxError(f'{reason} ({at})')

    def accept(self, value):
        if self.pos < len(self.tokens) and self.tokens[self.pos][0] == 'token' and self.tokens[self.pos][1].upper() == value:
            self.pos += 1
            return True
        return False

    def expect(self, value):
        if not self.accept(value): self.fail('Ожидается ' + value)

    def identifier(self):
        if self.pos == len(self.tokens) or self.tokens[self.pos][0] != 'token' or not re.fullmatch(IDENT, self.tokens[self.pos][1]):
            self.fail('Требуется идентификатор')
        result = self.tokens[self.pos][1]; self.pos += 1
        return result

    def expression(self):
        start = self.tokens[self.pos][2] if self.pos < len(self.tokens) else len(self.text)
        if self.accept('('):
            e = self.condition(); self.expect(')')
        elif self.pos < len(self.tokens) and self.tokens[self.pos][0] == 'parameter':
            t = self.tokens[self.pos]; self.pos += 1
            e = Expression('parameter', value=t, range=(t[2],t[3]))
        elif self.pos < len(self.tokens) and self.tokens[self.pos][0] == 'string':
            t = self.tokens[self.pos]; self.pos += 1; e = Expression('literal', 'Строка', value=t[1])
        elif self.accept('NULL'): e = Expression('null', sql_nullable=True)
        elif self.accept('НЕОПРЕДЕЛЕНО'): e = Expression('undefined')
        elif self.accept('ИСТИНА'): e = Expression('literal', 'Булево', value=True)
        elif self.accept('ЛОЖЬ'): e = Expression('literal', 'Булево', value=False)
        elif self.pos < len(self.tokens) and (self.tokens[self.pos][1].isdigit() or re.fullmatch(r'\d+\.\d+', self.tokens[self.pos][1]) or self.tokens[self.pos][1] == '-'):
            negative = self.accept('-')
            t = self.tokens[self.pos]; self.pos += 1
            if not re.fullmatch(r'\d+(?:\.\d+)?', t[1]): self.fail('Числовой литерал вне контракта', start)
            value = float(t[1]) if '.' in t[1] else int(t[1])
            e = Expression('literal','Число',value=-value if negative else value)
        else:
            first = self.identifier(); path = [first]
            if self.accept('.'):
                second = self.identifier()
                if second.upper() == 'ЗАМЕНИТЬNULL':
                    self.pos -= 2
                else: path.append(second)
            e = Expression('field', name=path[-1], value=tuple(path))
        e = replace(e, range=(start, self.tokens[self.pos-1][3]))
        while self.accept('.'):
            method = self.identifier()
            if method.upper() != 'ЗАМЕНИТЬNULL': self.fail('Метод проекции вне контракта: ' + method, start)
            self.expect('(')
            default = Expression('default') if self.accept(')') else self.expression()
            if default.kind != 'default': self.expect(')')
            e = Expression('coalesce', children=(e, default))
        return replace(e, range=(start, self.tokens[self.pos-1][3]))

    def comparison(self):
        start = self.tokens[self.pos][2] if self.pos < len(self.tokens) else len(self.text)
        if self.accept('НЕ'):
            return Expression('not', children=(self.comparison(),), range=(start,self.tokens[self.pos-1][3]))
        e = self.expression()
        if self.accept('ЕСТЬ'):
            negative = self.accept('НЕ'); self.expect('NULL')
            return Expression('is-not-null' if negative else 'is-null','Булево',children=(e,),range=(start,self.tokens[self.pos-1][3]))
        if self.pos < len(self.tokens) and self.tokens[self.pos][1] in ('=', '==','!=','<>','<','>','<=','>='):
            op = self.tokens[self.pos][1]; self.pos += 1
            return Expression('compare','Булево',value=op,children=(e,self.expression()),range=(start,self.tokens[self.pos-1][3]))
        return e

    def conjunction(self):
        e = self.comparison()
        while self.accept('И'): e = Expression('and','Булево',children=(e,self.comparison()))
        return e

    def condition(self):
        e = self.conjunction()
        while self.accept('ИЛИ'): e = Expression('or','Булево',children=(e,self.conjunction()))
        return e

    def source(self):
        if self.pos == len(self.tokens): self.fail('Ожидается источник соединения')
        start = self.tokens[self.pos][2]
        owner = self.identifier()
        while self.accept('::'): owner += '::' + self.identifier()
        sliced = self.accept('.')
        if sliced:
            self.expect('СРЕЗПОСЛЕДНИХ'); self.expect('(')
            if not self.accept(')'):
                if self.pos == len(self.tokens) or self.tokens[self.pos][0] != 'parameter': self.fail('Граница среза требует параметр')
                self.pos += 1; self.expect(')')
        end = self.tokens[self.pos-1][3]
        alias = owner.split('::')[-1]
        if self.accept('КАК'):
            alias = self.identifier()
        elif self.pos < len(self.tokens) and self.tokens[self.pos][0] == 'token' and re.fullmatch(IDENT,self.tokens[self.pos][1]) and self.tokens[self.pos][1].upper() not in ('СОЕДИНЕНИЕ','ВНУТРЕННЕЕ','ЛЕВОЕ','ПРАВОЕ','ПОЛНОЕ','ПО','ГДЕ','УПОРЯДОЧИТЬ'):
            alias = self.identifier()
        if alias in [s.alias for s in self.sources]: self.fail('Повторяющийся псевдоним источника: ' + alias,start)
        raw = self.text[start:end]
        # Reuse the established metadata/type/slice contract; no row evaluation.
        base = parse_storage_query('ВЫБРАТЬ ' + ('Период' if sliced else 'Ссылка') + ' ИЗ ' + raw + ' КАК ' + alias, self.contracts)
        raw_start = len('ВЫБРАТЬ ' + ('Период' if sliced else 'Ссылка') + ' ИЗ ')
        parameters = tuple(replace(p,start=p.start-raw_start+start,end=p.end-raw_start+start) for p in base.parameters)
        base = replace(base,source_range=(start,end),source_name=owner,parameters=parameters,
                       period_range=tuple(x-raw_start+start for x in base.period_range) if base.period_range else None)
        self.sources.append(base); self.raw_sources.append(raw)
        self.fields.append({}); self.used.append({})
        return len(self.sources)-1

    def field(self, e, visible):
        path = e.value
        matches = []
        for i,s in enumerate(self.sources[:visible]):
            if len(path) == 2 and path[0] != s.alias: continue
            elements = self.contracts.resolve(s.owner)
            props = elements[0]['properties']
            names = {f['Имя'] for k in ('Реквизиты','Измерения','Ресурсы') for f in props.get(k,[])} | ({'Ссылка'} if s.source_kind=='ordinary' else {'Период'})
            if e.name in names: matches.append(i)
        if len(matches) != 1: self.fail('Неизвестное или неоднозначное поле: ' + '.'.join(path),e.range[0])
        i = matches[0]
        if e.name not in self.fields[i]:
            q = parse_storage_query('ВЫБРАТЬ ' + e.name + ' ИЗ ' + self.raw_sources[i],self.contracts)
            self.fields[i][e.name] = q.projections[0][0]
        f = self.fields[i][e.name]; self.used[i][e.name] = f
        nullable = i in self.nullable
        return replace(e,source=i,type=f.type if not nullable or f.type.endswith('?') else f.type+'?',sql_nullable=nullable)

    def bind(self, e, visible, expected=None):
        if e.kind == 'field': return self.field(e,visible)
        if e.kind == 'parameter':
            if not expected: self.fail('Неизвестный тип параметра',e.range[0])
            t=e.value; self.parameters.append(QueryParameter(t[1],t[2],t[3],expected,-1))
            return replace(e,type=expected)
        if e.kind in ('undefined','null'):
            return replace(e,type=(expected.rstrip('?')+'?') if expected else 'Объект?')
        if e.kind in ('literal','default'): return e
        if e.kind == 'coalesce':
            value=self.bind(e.children[0],visible)
            default=e.children[1]
            if default.kind == 'default':
                typ=self.fields[value.source][value.name].type if value.kind=='field' else value.type
                defaults={'Строка':'','Число':0,'Булево':False}
                if typ.endswith('?'): default=Expression('undefined',type=typ)
                elif typ in defaults: default=Expression('literal',type=typ,value=defaults[typ])
                else: self.fail('ЗаменитьNull без аргумента требует подтверждённое умолчание',e.range[0])
            else: default=self.bind(default,visible,value.type.rstrip('?'))
            if default.kind not in ('undefined','null') and default.type.rstrip('?') != value.type.rstrip('?'):
                self.fail('Несовместимые типы ЗаменитьNull',e.range[0])
            original = self.fields[value.source][value.name].type if value.kind=='field' else value.type
            typ=original.rstrip('?')
            if original.endswith('?') or default.type.endswith('?'): typ+='?'
            return replace(e,type=typ,children=(value,default),sql_nullable=value.sql_nullable and default.sql_nullable)
        if e.kind == 'compare':
            a,b=e.children
            if a.kind in ('parameter','undefined','null'):
                b=self.bind(b,visible); a=self.bind(a,visible,b.type.rstrip('?'))
            else:
                a=self.bind(a,visible); b=self.bind(b,visible,a.type.rstrip('?'))
            if a.kind not in ('null','undefined') and b.kind not in ('null','undefined') and a.type.rstrip('?') != b.type.rstrip('?'):
                self.fail('Несовместимые типы сравнения',e.range[0])
            if e.value not in ('=','==','!=','<>') and (a.type.endswith('?') or b.type.endswith('?') or a.type not in ('Число','Строка','Дата')):
                self.fail('Упорядоченное сравнение вне контракта',e.range[0])
            return replace(e,type='Булево',children=(a,b),sql_nullable=a.sql_nullable or b.sql_nullable)
        children=tuple(self.bind(c,visible) for c in e.children)
        if e.kind in ('and','or','not') and any(c.type!='Булево' for c in children): self.fail('Условие требует Булево',e.range[0] if e.range else 0)
        return replace(e,type='Булево',children=children,sql_nullable=e.kind not in ('is-null','is-not-null') and any(c.sql_nullable for c in children))

    def parse(self):
        self.expect('ВЫБРАТЬ'); limit=None
        if self.accept('ПЕРВЫЕ'):
            t=self.tokens[self.pos]; self.pos+=1
            if not t[1].isdigit() or int(t[1])<=0: self.fail('ПЕРВЫЕ требует положительное целое',t[2])
            limit=int(t[1])
        projections=[]
        while True:
            e=self.condition(); label=self.identifier() if self.accept('КАК') else e.name
            if not label: self.fail('Вычисляемая проекция требует псевдоним',e.range[0] if e.range else 0)
            if label in [x[1] for x in projections]: self.fail('Повторяющийся псевдоним проекции: '+label)
            projections.append((e,label))
            if not self.accept(','): break
        fill_name=fill_span=fill_type_span=None
        if self.accept('ЗАПОЛНИТЬ'):
            start=self.tokens[self.pos-1][2];a=self.tokens[self.pos][2];fill_name=self.identifier()
            while self.accept('::'): fill_name+='::'+self.identifier()
            fill_span=(start,self.tokens[self.pos-1][3]);fill_type_span=(a,self.tokens[self.pos-1][3])
        self.expect('ИЗ');self.source();joins=[]
        while self.pos<len(self.tokens) and self.tokens[self.pos][1].upper() in ('СОЕДИНЕНИЕ','ВНУТРЕННЕЕ','ЛЕВОЕ','ПРАВОЕ','ПОЛНОЕ'):
            start=self.tokens[self.pos][2];kind='inner'
            for keyword,value in [('ВНУТРЕННЕЕ','inner'),('ЛЕВОЕ','left'),('ПРАВОЕ','right'),('ПОЛНОЕ','full')]:
                if self.accept(keyword): kind=value;break
            if kind!='inner':self.accept('ВНЕШНЕЕ')
            self.expect('СОЕДИНЕНИЕ');i=self.source();self.expect('ПО')
            # Bind ON before lifting this join's absent sides. Previous lifts remain.
            on=self.bind(self.condition(),i+1)
            if on.type!='Булево':self.fail('ПО требует Булево',start)
            joins.append(Join(kind,i,on,(start,self.tokens[self.pos-1][3])))
            if kind in ('left','full'):self.nullable.add(i)
            if kind in ('right','full'):self.nullable.update(range(i))
        def projection(e):
            e = self.bind(e,len(self.sources))
            return replace(e,type=e.type+'?') if e.sql_nullable and not e.type.endswith('?') else e
        bound=tuple((projection(e),label) for e,label in projections)
        where=()
        if self.accept('ГДЕ'):
            e=self.bind(self.condition(),len(self.sources))
            if e.type!='Булево':self.fail('ГДЕ требует Булево')
            where=(e,)
        ordering=[]
        if self.accept('УПОРЯДОЧИТЬ'):
            self.expect('ПО')
            while True:
                e=self.expression()
                projected=[f for f,label in bound if e.kind=='field' and e.value==(label,)]
                e=projected[0] if projected else self.bind(e,len(self.sources))
                if e.sql_nullable or e.type not in ('Число','Строка','Дата'):self.fail('Сортировка требует не-NULL скаляр; используйте ЗаменитьNull')
                desc=self.accept('УБЫВ')
                if not desc:self.accept('ВОЗР')
                ordering.append((e,desc))
                if not self.accept(','):break
        if self.pos!=len(self.tokens):self.fail('Достижимый запрос вне relational AST: '+str(self.tokens[self.pos][1]))
        for s in self.sources:self.parameters.extend(s.parameters)
        slots={};types={};parameters=[]
        for p in sorted(self.parameters,key=lambda p:p.start):
            key=p.expression if re.fullmatch(IDENT,p.expression) else (p.start,p.end)
            slot=slots.setdefault(key,len(slots));typ=p.type
            if slot in types and types[slot]!=typ:
                if {types[slot],typ}=={'Дата','Дата?'}:typ='Дата'
                else:self.fail('Несовместимые типы одного параметра запроса',p.start)
            types[slot]=typ;parameters.append(replace(p,slot=slot))
        parameters=tuple(replace(p,type=types[p.slot]) for p in parameters)
        def slot_expr(e):
            if e.kind=='parameter':
                t=e.value;key=t[1] if re.fullmatch(IDENT,t[1]) else (t[2],t[3]);return replace(e,value=slots[key],type=types[slots[key]])
            return replace(e,children=tuple(slot_expr(c) for c in e.children))
        sources=[]
        for i,s in enumerate(self.sources):
            # At least one native column is needed for cardinality-only joins.
            fields=tuple((f,f.name) for f in self.used[i].values()) or s.projections
            sources.append(replace(s,projections=fields))
        fill=None
        if fill_name:
            targets=self.contracts.resolve(fill_name)
            if len(targets)!=1 or targets[0]['elementType']!='Структура':self.fail('Тип ЗАПОЛНИТЬ отсутствует или неоднозначен: '+fill_name,fill_type_span[0])
            from .resolution import qualified
            canonical=self.contracts.canonical_type(qualified(targets[0]));self.contracts.require(canonical)
            fields=self.contracts.fields[canonical];by_name={f['Имя']:f for f in fields};mapping=[]
            for e,label in bound:
                if label not in by_name:self.fail('Неизвестная колонка ЗАПОЛНИТЬ: '+label,e.range[0])
                typ=by_name[label]['Тип']
                if e.type!=typ and e.type+'?'!=typ:self.fail('Несовместимые типы ЗАПОЛНИТЬ: '+e.type+' → '+typ,e.range[0])
                mapping.append({'column':e.name,'parameter':label,'columnType':e.type,'fieldType':typ,'range':e.range})
            for f in fields:
                if f['constructorRequired'] and f['Имя'] not in dict((label,e) for e,label in bound):self.fail('Отсутствующее обязательное поле ЗАПОЛНИТЬ: '+f['Имя'])
            fill={'owner':qualified(targets[0]),'sourceFile':targets[0]['sourceFile'],'type':canonical,'sourceName':fill_name,'range':fill_span,'typeRange':fill_type_span,'constructor':'automatic-named','fields':tuple(fields),'mapping':tuple(mapping)}
        return RelationalQuery(sources[0].owner,sources[0].alias,tuple((slot_expr(e),l) for e,l in bound),tuple(slot_expr(e) for e in where),parameters,tuple((slot_expr(e),d) for e,d in ordering),limit,tuple(sources),tuple(replace(j,condition=slot_expr(j.condition)) for j in joins),fill)


def parse_relational_query(text, contracts):
    parser = Parser(text,contracts)
    try:
        return parser.parse()
    except IndexError as exc:
        raise UnsupportedSyntaxError('Незавершённый relational запрос (' + str(len(text)) + ')') from exc


def generate_relational_query(query, contracts):
    """Nested-loop bag joins and three-valued predicates run in native Script."""
    from .storage_queries import generate_query, query_name, unique_parameters
    name=query_name(query)
    if name in contracts.definitions:return name
    parameters=unique_parameters(query)
    names=[generate_query(s,contracts) for s in query.sources]
    row_types=[n+'.СтрокаРезультата' for n in names]
    def declaration(structure,fields):
        return '@Глобально\nструктура '+structure+'\n'+''.join('    знч '+label+': '+contracts.sbsl_type(typ)+'\n' for label,typ in fields)+';\n'
    text=declaration('Комбинация',[('С'+str(i),typ+'?') for i,typ in enumerate(row_types)])
    row_type=query.fill['type'] if query.fill else 'СтрокаРезультата'
    if not query.fill:text+=declaration(row_type,[(label,e.type+('?' if e.sql_nullable and not e.type.endswith('?') else '')) for e,label in query.projections])

    def null(e,row='С'):
        if e.kind=='field':return row+'.С'+str(e.source)+' == Неопределено'
        if e.kind=='null':return 'Истина'
        if e.kind=='coalesce':return '('+null(e.children[0],row)+') и ('+null(e.children[1],row)+')'
        if e.kind in ('compare','and','or','not'):return tri(e,row)+' == -1'
        return 'Ложь'

    def value(e,row='С'):
        if e.kind=='field':return '('+row+'.С'+str(e.source)+' как '+row_types[e.source]+').'+e.name
        if e.kind in ('null','undefined'):return 'Неопределено'
        if e.kind=='parameter':return 'П'+str(e.value)
        if e.kind=='literal':return contracts.literal(e.value,e.type)
        if e.kind=='coalesce':return '(('+null(e.children[0],row)+') ? '+value(e.children[1],row)+' : '+value(e.children[0],row)+')'
        if e.kind in ('is-null','is-not-null'):
            return ('не (' if e.kind=='is-not-null' else '(')+null(e.children[0],row)+')'
        return '('+tri(e,row)+' == 1)'

    def tri(e,row='С'):
        if e.kind=='compare':
            a,b=e.children;op={'=':'==','<>':'!='}.get(e.value,e.value)
            if a.kind == 'null' or b.kind == 'null': return '-1'
            av,bv=value(a,row),value(b,row)
            if a.kind == 'undefined' or b.kind == 'undefined':
                other = b if a.kind == 'undefined' else a
                original_type = other.type
                if other.kind == 'field':
                    original_type = next(f.type for f,_ in query.sources[other.source].projections if f.name == other.name)
                if not original_type.endswith('?'):
                    answer = '1' if op == '!=' else '0'
                    return '(('+null(a,row)+' или '+null(b,row)+') ? -1 : '+answer+')'
            return '(('+null(a,row)+' или '+null(b,row)+') ? -1 : ('+av+' '+op+' '+bv+' ? 1 : 0))'
        if e.kind in ('and','or'):
            a,b=(tri(c,row) for c in e.children);dominant='0' if e.kind=='and' else '1';other='1' if e.kind=='and' else '0'
            return '(('+a+' == '+dominant+' или '+b+' == '+dominant+') ? '+dominant+' : (('+a+' == -1 или '+b+' == -1) ? -1 : '+other+'))'
        if e.kind=='not':
            v=tri(e.children[0],row);return '('+v+' == -1 ? -1 : ('+v+' == 1 ? 0 : 1))'
        return '(('+null(e,row)+') ? -1 : ('+value(e,row)+' ? 1 : 0))'

    def combination(i,left='Л',right='П',only_right=False):
        return 'новый Комбинация('+', '.join('С'+str(n)+' = '+('Неопределено' if only_right else left+'.С'+str(n)) for n in range(i))+(', ' if i else '')+'С'+str(i)+' = '+right+')'

    text+='@Глобально\nструктура Запрос\n'
    text+=''.join('    знч П'+str(p.slot)+': '+contracts.sbsl_type(p.type)+'\n' for p in parameters)
    text+=''.join('    знч И'+str(i)+': '+n+'.Запрос\n' for i,n in enumerate(names))
    text+='    @Глобально\n    метод Выполнить(): Массив<'+row_type+'>\n'
    for i in range(len(names)):text+='        знч Т'+str(i)+' = И'+str(i)+'.Выполнить()\n'
    text+='        пер Строки = новый Массив<Комбинация>()\n        для Исходная из Т0\n            Строки.Добавить(новый Комбинация(С0 = Исходная))\n        ;\n'
    for j in query.joins:
        i=j.source
        text+='        знч Следующие'+str(i)+' = новый Массив<Комбинация>()\n'
        if j.kind in ('right','full'):text+='        знч Совпавшие'+str(i)+' = новый Массив<Число>()\n'
        text+='        для Л из Строки\n            пер Совпало = Ложь\n            пер Индекс = 0\n            для П из Т'+str(i)+'\n                знч С = '+combination(i)+'\n                если '+tri(j.condition)+' == 1\n                    Следующие'+str(i)+'.Добавить(С)\n                    Совпало = Истина\n'
        if j.kind in ('right','full'):text+='                    Совпавшие'+str(i)+'.Добавить(Индекс)\n'
        text+='                ;\n                Индекс += 1\n            ;\n'
        if j.kind in ('left','full'):text+='            если не Совпало\n                Следующие'+str(i)+'.Добавить('+combination(i,right='Неопределено')+')\n            ;\n'
        text+='        ;\n'
        if j.kind in ('right','full'):
            text+='        пер Индекс'+str(i)+' = 0\n        для П из Т'+str(i)+'\n            если не Совпавшие'+str(i)+'.Содержит(Индекс'+str(i)+')\n                Следующие'+str(i)+'.Добавить('+combination(i,only_right=True)+')\n            ;\n            Индекс'+str(i)+' += 1\n        ;\n'
        text+='        Строки = Следующие'+str(i)+'\n'
    condition=tri(query.predicates[0])+' == 1' if query.predicates else 'Истина'
    text+='        знч Отобранные = новый Массив<Комбинация>()\n        для С из Строки\n            если '+condition+'\n'
    if query.ordering:
        order_args = ''.join(', П'+str(p.slot) for p in parameters)
        text+='                пер Позиция = 0\n                пока Позиция < Отобранные.Размер() и не '+name+'.Раньше(С, Отобранные[Позиция]'+order_args+')\n                    Позиция += 1\n                ;\n                Отобранные.Вставить(Позиция, С)\n'
    else:text+='                Отобранные.Добавить(С)\n'
    text+='            ;\n        ;\n        знч Результат = новый Массив<'+row_type+'>()\n        для С из Отобранные\n'
    if query.limit:text+='            если Результат.Размер() >= '+str(query.limit)+'\n                прервать\n            ;\n'
    def projection(e):
        v=value(e)
        # Output NULL uses the adapter's nullable transport; internal flags keep
        # NULL and an existing row's Undefined separate during relational work.
        if e.sql_nullable:v='(('+null(e)+') ? Неопределено : '+v+')'
        if e.type.rstrip('?').endswith('.Ссылка'):
            typ=e.type.rstrip('?');copy='новый '+typ+'(Идентификатор = ('+v+').Идентификатор)'
            v='('+v+' == Неопределено ? Неопределено : '+copy+')' if e.type.endswith('?') or e.sql_nullable else copy
        return v
    text+='            Результат.Добавить(новый '+row_type+'('+', '.join(label+' = '+projection(e) for e,label in query.projections)+'))\n        ;\n        возврат Результат\n    ;\n;\n'
    if query.ordering:
        order_signature = ''.join(', П'+str(p.slot)+': '+contracts.sbsl_type(p.type) for p in parameters)
        text+='@Глобально\nметод Раньше(А: Комбинация, Б: Комбинация'+order_signature+'): Булево\n'
        for e,descending in query.ordering:
            a,b=value(e,'А'),value(e,'Б')
            text+='    если '+a+' != '+b+'\n        возврат '+a+(' > ' if descending else ' < ')+b+'\n    ;\n'
        text+='    возврат Ложь\n;\n'
    signature=', '.join('П'+str(p.slot)+': '+contracts.sbsl_type(p.type) for p in parameters)
    def captured(p):
        v='П'+str(p.slot)
        if p.type.rstrip('?').endswith('.Ссылка'):
            clone='новый '+p.type.rstrip('?')+'(Идентификатор = '+v+'.Идентификатор)'
            return '('+v+' == Неопределено ? Неопределено : '+clone+')' if p.type.endswith('?') else clone
        return v
    text+='@Глобально\nметод Создать('+signature+'): Запрос\n'
    args=['П'+str(p.slot)+' = '+captured(p) for p in parameters]
    for i,s in enumerate(query.sources):
        source_parameters=unique_parameters(s)
        mapped=[]
        for p in source_parameters:
            original=next(x for x in query.parameters if (x.start,x.end)==(p.start,p.end))
            mapped.append(captured(original))
        text+='    знч Источник'+str(i)+' = '+names[i]+'.Создать('+', '.join(mapped)+')\n'
        args.append('И'+str(i)+' = Источник'+str(i))
    text+='    возврат новый Запрос('+', '.join(args)+')\n;\n'
    contracts.definitions[name]=text
    contracts.method_dependencies[name]=[n+'.Запрос' for n in names]+[e.type for e,_ in query.projections]
    if query.fill:contracts.method_dependencies[name].append(row_type)
    return name
