"""Typed scalar/group XBQL expressions; values are evaluated only by Script."""
from dataclasses import replace
import re
from .query_joins import Parser, Expression
from .yaml_io import UnsupportedSyntaxError

AGGREGATES = {'КОЛИЧЕСТВО', 'СУММА', 'МИНИМУМ', 'МАКСИМУМ', 'СРЕДНЕЕ'}


def walk(e):
    yield e
    for c in e.children:
        yield from walk(c)


def identity(e):
    value=e.value
    if e.kind=='parameter' and isinstance(value,tuple) and re.fullmatch(r'[^\W\d]\w*',value[1]):
        value=value[1]
    return (e.kind, e.type, e.name, e.source, repr(value), e.sql_nullable,
            tuple(identity(c) for c in e.children))


# Signature table is intentionally finite: unverified methods stay UNSUPPORTED.
METHODS = {
 'НачинаетсяС': ('Строка', ('Строка',), 'Булево'),
 'ЗаканчиваетсяНа': ('Строка', ('Строка',), 'Булево'),
 'Подстрока': ('Строка', ('Число', 'Число'), 'Строка'),
 'ВВерхнийРегистр': ('Строка', (), 'Строка'),
 'ВНижнийРегистр': ('Строка', (), 'Строка'),
 'Длина': ('Строка', (), 'Число'),
 'Округлить': ('Число', ('Число',), 'Число'),
 'Дата': ('ДатаВремя', (), 'Дата'),
 'ДобавитьДни': ('Дата', ('Число',), 'Дата'),
}


class ComputedParser(Parser):
    def method_expression(self, child, method, start):
        method=next((m for m in METHODS if m.upper()==method.upper()),method)
        if method.upper()=='ЗАМЕНИТЬNULL':
            self.expect('(')
            default=Expression('default') if self.accept(')') else self.expression()
            if default.kind!='default':self.expect(')')
            return Expression('coalesce',children=(child,default),range=(start,self.tokens[self.pos-1][3]))
        if method not in METHODS:self.fail('Метод вычисления вне контракта: '+method,start)
        args=[];self.expect('(')
        if not self.accept(')'):
            while True:
                args.append(self.expression())
                if not self.accept(','):break
            self.expect(')')
        return Expression('method',name=method,children=(child,*args),range=(start,self.tokens[self.pos-1][3]))

    def expression(self, precedence=0):
        start = self.tokens[self.pos][2] if self.pos < len(self.tokens) else len(self.text)
        if self.accept('-'):
            e = Expression('negate', children=(self.expression(30),))
        elif self.accept('+'):
            e = self.expression(30)
        elif self.accept('ВЫБОР'):
            base = None if self.tokens[self.pos][1].upper() == 'КОГДА' else self.expression()
            branches = []
            while self.accept('КОГДА'):
                cond = self.condition()
                if base is not None:
                    cond = Expression('compare', 'Булево', value='=', children=(base, cond))
                self.expect('ТОГДА')
                branches.extend((cond, self.condition()))
            if not branches:
                self.fail('ВЫБОР требует КОГДА', start)
            default = self.condition() if self.accept('ИНАЧЕ') else Expression('null', sql_nullable=True)
            self.expect('КОНЕЦ')
            e = Expression('case', children=tuple(branches + [default]))
        elif self.accept('ВЫРАЗИТЬ'):
            self.expect('('); child = self.condition(); self.expect('КАК')
            typ = self.identifier()
            while self.accept('::'): typ += '::' + self.identifier()
            if self.accept('.'):
                typ += '.' + self.identifier()
            qualifiers = []
            if self.accept('('):
                while True:
                    t = self.tokens[self.pos]; self.pos += 1
                    if not t[1].isdigit(): self.fail('Квалификатор требует целое', t[2])
                    qualifiers.append(int(t[1]))
                    if not self.accept(','): break
                self.expect(')')
            self.expect(')')
            e = Expression('cast', value=(typ, tuple(qualifiers)), children=(child,))
        elif self.pos < len(self.tokens) and self.tokens[self.pos][1].upper() in AGGREGATES and self.pos+1<len(self.tokens) and self.tokens[self.pos+1][1]=='(':
            fn = self.identifier().upper(); self.expect('(')
            distinct = self.accept('РАЗЛИЧНЫЕ')
            if self.accept('*'):
                if fn != 'КОЛИЧЕСТВО' or distinct: self.fail('Звёздочка допустима только в КОЛИЧЕСТВО(*)', start)
                child = Expression('star')
            else: child = self.condition()
            self.expect(')')
            e = Expression('aggregate', value=(fn, distinct), children=(child,))
        else:
            # Base atoms, parentheses and coalesce use this overridden expression
            # recursively, retaining original source ranges for parameters.
            e = super().expression()
            raw=self.text[e.range[0]:e.range[1]]
            if e.kind=='literal' and e.type=='Число' and re.fullmatch(r'\d+\.\d+',raw):e=replace(e,kind='decimal',value=raw)
        e = replace(e, range=(start, self.tokens[self.pos-1][3]))
        while self.accept('.'):
            e=self.method_expression(e,self.identifier(),start)
        while self.pos < len(self.tokens):
            op = self.tokens[self.pos][1]
            level = {'+': 10, '-': 10, '*': 20, '/': 20, '%': 20}.get(op, -1)
            if level < precedence: break
            self.pos += 1
            right = self.expression(level + 1)
            e = Expression('binary', value=op, children=(e, right), range=(start, right.range[1]))
        return e

    def comparison(self):
        # IN lists are needed by real grouped task queries; no subquery rewrite.
        e = super().comparison()
        negative = self.accept('НЕ')
        if self.accept('В'):
            self.expect('('); items = []
            while True:
                items.append(self.expression())
                if not self.accept(','): break
            self.expect(')')
            e = Expression('in', 'Булево', children=(e, *items), range=(e.range[0], self.tokens[self.pos-1][3]))
            return Expression('not', 'Булево', children=(e,), range=e.range) if negative else e
        if negative: self.fail('НЕ после выражения требует В')
        return e

    def bind(self, e, visible, expected=None):
        if e.kind=='method':
            owner,args,result=METHODS[e.name];base=self.bind(e.children[0],visible,owner)
            if base.type.rstrip('?')!=owner or (len(args)!=len(e.children)-1 and not (e.name=='Подстрока' and len(e.children)==2)):self.fail('Несовместимая сигнатура '+e.name,e.range[0])
            children=(base,*(self.bind(c,visible,t) for c,t in zip(e.children[1:],args)))
            if any(c.type.rstrip('?')!=t for c,t in zip(children,(owner,*args))):self.fail('Несовместимые типы '+e.name,e.range[0])
            nullable=any(c.sql_nullable for c in children)
            return replace(e,children=children,type=result+('?' if nullable else ''),sql_nullable=nullable)
        if e.kind=='decimal':return e
        if e.kind == 'aggregate':
            child = e.children[0]
            if any(c.kind == 'aggregate' for c in walk(child)): self.fail('Вложенный агрегат недопустим', e.range[0])
            child = child if child.kind == 'star' else self.bind(child, visible)
            fn = e.value[0]
            if fn in ('СУММА', 'СРЕДНЕЕ') and child.type.rstrip('?') != 'Число': self.fail('Агрегат требует Число', e.range[0])
            if fn in ('МИНИМУМ','МАКСИМУМ') and child.type.rstrip('?') not in ('Число','Строка','Дата','ДатаВремя'): self.fail('Упорядоченный агрегат требует скаляр', e.range[0])
            return replace(e, children=(child,), type='Число' if fn=='КОЛИЧЕСТВО' else child.type.rstrip('?')+'?', sql_nullable=fn!='КОЛИЧЕСТВО')
        if e.kind in ('negate','binary'):
            raw = e.children
            if len(raw)==2 and raw[0].kind=='parameter':
                b=self.bind(raw[1],visible); a=self.bind(raw[0],visible,b.type.rstrip('?')); children=(a,b)
            else:
                a=self.bind(raw[0],visible,expected or 'Число'); children=(a,) if len(raw)==1 else (a,self.bind(raw[1],visible,a.type.rstrip('?')))
            typ=children[0].type.rstrip('?')
            if (typ != 'Число' and not (e.kind=='binary' and e.value=='+' and typ=='Строка')) or any(c.type.rstrip('?')!=typ for c in children): self.fail('Несовместимые типы вычисления',e.range[0])
            nullable=any(c.sql_nullable for c in children)
            return replace(e,children=children,type=typ+('?' if nullable or any(c.type.endswith('?') for c in children) else ''),sql_nullable=nullable)
        if e.kind == 'case':
            raw=e.children; values=[raw[i] for i in range(1,len(raw),2)]+[raw[-1]]
            typed=[self.bind(v,visible,expected) if v.kind not in ('null','undefined','parameter') else None for v in values]
            typ=next((v.type.rstrip('?') for v in typed if v is not None),expected)
            if not typ: self.fail('Неизвестный тип ветвей ВЫБОР',e.range[0])
            typed=[v if v is not None else self.bind(r,visible,typ) for r,v in zip(values,typed)]
            if any(v.type.rstrip('?')!=typ for v in typed): self.fail('Несовместимые типы ветвей ВЫБОР',e.range[0])
            children=[]
            for i in range(0,len(raw)-1,2):
                cond=self.bind(raw[i],visible)
                if cond.type!='Булево': self.fail('КОГДА требует Булево',e.range[0])
                children.extend((cond,typed[i//2]))
            children.append(typed[-1]); nullable=any(v.sql_nullable for v in typed)
            return replace(e,type=typ+('?' if nullable or any(v.type.endswith('?') for v in typed) else ''),sql_nullable=nullable,children=tuple(children))
        if e.kind=='cast':
            typ=self.contracts.canonical_type(e.value[0]);child=self.bind(e.children[0],visible,typ)
            if child.type.rstrip('?')!=typ: self.fail('ВЫРАЗИТЬ не преобразует несовместимые типы',e.range[0])
            qs=e.value[1]
            if qs and (typ not in ('Число','Строка') or len(qs)>(2 if typ=='Число' else 1) or qs[0]<1 or (qs[0]>32 or (len(qs)==2 and qs[1]>32))): self.fail('Квалификатор ВЫРАЗИТЬ вне контракта',e.range[0])
            self.contracts.require(typ)
            return replace(e,type=typ+('?' if child.type.endswith('?') else ''),sql_nullable=child.sql_nullable,children=(child,))
        if e.kind=='in':
            first=self.bind(e.children[0],visible);items=tuple(self.bind(c,visible,first.type.rstrip('?')) for c in e.children[1:])
            if any(c.type.rstrip('?')!=first.type.rstrip('?') for c in items):self.fail('Несовместимые типы В',e.range[0])
            return replace(e,children=(first,*items),sql_nullable=any(c.sql_nullable for c in (first,*items)))
        # Qualified enum literals retain their exact metadata owner.
        if e.kind=='field' and len(e.value)==2 and not any(s.alias==e.value[0] for s in self.sources[:visible]):
            matches=self.contracts.resolve(e.value[0])
            if len(matches)==1 and matches[0]['elementType']=='Перечисление':
                names={v['Имя'] for v in matches[0]['properties']['Элементы']}
                if e.name not in names:self.fail('Неизвестный элемент перечисления',e.range[0])
                from .resolution import qualified
                typ=self.contracts.canonical_type(qualified(matches[0]));self.contracts.require(typ)
                return replace(e,kind='enum',type=typ,value=e.name)
        return super().bind(e,visible,expected)

    def validate_groups(self, projections, where, joins, groups, having, ordering, distinct):
        for e in [*where,*(j.condition for j in joins),*groups]:
            if any(c.kind=='aggregate' for c in walk(e)):self.fail('Агрегат недопустим в ГДЕ/ПО/GROUP',e.range[0])
        expressions=[e for e,_ in projections]+list(having)+[e for e,_ in ordering]
        aggregated=any(c.kind=='aggregate' for e in expressions for c in walk(e))
        if having and not (groups or aggregated):self.fail('ИМЕЮЩИЕ требует группировку или агрегат')
        if groups or aggregated:
            keys={identity(e) for e in groups}
            def check(e):
                if identity(e) in keys or e.kind=='aggregate':return
                if e.kind=='field':self.fail('Поле вне СГРУППИРОВАТЬ ПО',e.range[0])
                for child in e.children:check(child)
            for e in expressions:check(e)
        if distinct:
            labels={identity(e) for e,_ in projections}
            if any(identity(e) not in labels for e,_ in ordering):self.fail('Сортировка DISTINCT требует проекцию')


def parse_computed_query(text, contracts):
    try:return ComputedParser(text,contracts).parse()
    except IndexError as exc:raise UnsupportedSyntaxError('Незавершённый вычисляемый запрос') from exc

class ExpressionRenderer:
    def __init__(self,query,contracts,row_types):
        self.query,self.contracts,self.row_types=query,contracts,row_types
        self.helpers={}

    def null(self,e,row='С',group='Группа'):
        if e.kind=='field':
            absent=row+'.С'+str(e.source)+' == Неопределено'
            return absent+' или ('+row+'.С'+str(e.source)+' как '+self.row_types[e.source]+').'+e.null_field if e.null_field else absent
        if e.kind=='null':return 'Истина'
        if e.kind=='aggregate':return self.value(e,row,group)+' == Неопределено' if e.value[0]!='КОЛИЧЕСТВО' else 'Ложь'
        if e.kind=='coalesce':return '('+self.null(e.children[0],row,group)+') и ('+self.null(e.children[1],row,group)+')'
        if e.kind in ('compare','and','or','not','in','in-query'):return self.tri(e,row,group)+' == -1'
        if e.kind=='case':
            result=self.null(e.children[-1],row,group)
            for i in reversed(range(0,len(e.children)-1,2)):
                result='('+self.tri(e.children[i],row,group)+' == 1 ? '+self.null(e.children[i+1],row,group)+' : '+result+')'
            return result
        if e.kind in ('binary','negate','cast','method'):return '('+' или '.join('('+self.null(c,row,group)+')' for c in e.children)+')'
        return 'Ложь'

    def value(self,e,row='С',group='Группа'):
        v=lambda c:self.value(c,row,group)
        if e.kind=='field':return '('+row+'.С'+str(e.source)+' как '+self.row_types[e.source]+').'+e.name
        if e.kind in ('null','undefined'):return 'Неопределено'
        if e.kind=='parameter':return 'П'+str(e.value)
        if e.kind=='literal':return self.contracts.literal(e.value,e.type)
        if e.kind=='decimal':return str(e.value)
        if e.kind=='enum':return e.type+'.'+e.value
        if e.kind=='coalesce':return '(('+self.null(e.children[0],row,group)+') ? '+v(e.children[1])+' : '+v(e.children[0])+')'
        if e.kind in ('is-null','is-not-null'):return ('не (' if e.kind=='is-not-null' else '(')+self.null(e.children[0],row,group)+')'
        if e.kind=='binary':
            a,b=e.children
            return '(('+v(a)+' как '+a.type.rstrip('?')+') '+e.value+' ('+v(b)+' как '+b.type.rstrip('?')+'))'
        if e.kind=='negate':return '(-('+v(e.children[0])+' как Число))'
        if e.kind=='case':
            result=v(e.children[-1])
            for i in reversed(range(0,len(e.children)-1,2)):
                result='('+self.tri(e.children[i],row,group)+' == 1 ? '+v(e.children[i+1])+' : '+result+')'
            return result
        if e.kind=='cast':
            result='('+v(e.children[0])+' как '+e.type.rstrip('?')+')';typ,qs=e.value
            if qs:
                if typ=='Число':
                    key=('precision',qs)
                    if key not in self.helpers:
                        method='Квалификатор'+str(len(self.helpers))
                        bound='1'+'0'*qs[0]
                        body='@Глобально\nметод '+method+'(Значение: Число): Число\n    знч Округленное = Значение.Округлить('+str(qs[1] if len(qs)>1 else 0)+')\n    если Округленное >= '+bound+' или Округленное <= -'+bound+'\n        выбросить новый ИсключениеНедопустимоеСостояние("Переполнение квалификатора Число")\n    ;\n    возврат Округленное\n;\n'
                        self.helpers[key]=(method,body)
                    from .storage_queries import query_name
                    result=query_name(self.query)+'.'+self.helpers[key][0]+'('+result+')'
                else:result+='.Подстрока(0, '+str(qs[0])+')'
            return result
        if e.kind=='method':return '('+v(e.children[0])+' как '+e.children[0].type.rstrip('?')+').'+e.name+'('+', '.join(v(c) for c in e.children[1:])+')'
        if e.kind=='aggregate':return self.aggregate(e,group)
        return '('+self.tri(e,row,group)+' == 1)'

    def tri(self,e,row='С',group='Группа'):
        if e.kind=='compare':
            a,b=e.children;op={'=':'==','<>':'!='}.get(e.value,e.value)
            if a.kind=='null' or b.kind=='null':return '-1'
            av,bv=self.value(a,row,group),self.value(b,row,group)
            if a.kind=='undefined' or b.kind=='undefined':
                other=b if a.kind=='undefined' else a;typ=other.type
                if other.kind=='field':typ=next(f.type for f,label in self.query.sources[other.source].projections if label==other.name)
                if not typ.endswith('?'):return '(('+self.null(a,row,group)+' или '+self.null(b,row,group)+') ? -1 : '+('1' if op=='!=' else '0')+')'
            if op not in ('==','!='):
                av='('+av+' как '+a.type.rstrip('?')+')';bv='('+bv+' как '+b.type.rstrip('?')+')'
            return '(('+self.null(a,row,group)+' или '+self.null(b,row,group)+') ? -1 : ('+av+' '+op+' '+bv+' ? 1 : 0))'
        if e.kind=='in-query':
            from .storage_queries import generate_query,query_name,unique_parameters
            from .query_composites import mapped_args,needs_context,internal,null_column
            q=self.query.subqueries[e.value];n=generate_query(q,self.contracts)
            key=('in-query',e.value)
            if key not in self.helpers:
                method='ВПодзапросе'+str(len(self.helpers));child,label=q.projections[0]
                sig=''.join(', П'+str(p.slot)+': '+self.contracts.sbsl_type(p.type) for p in unique_parameters(self.query))
                if needs_context(q):sig+=', Контекст: Соответствие<Строка, Строка>'
                args=mapped_args(self.query,q)+(['Контекст'] if needs_context(q) else [])
                call=n+'.Создать('+', '.join(args)+').'+('ВыполнитьВнутренне' if internal(q) else 'Выполнить')+'()'
                missing='С.'+null_column(q.projections,0) if internal(q) else 'Ложь'
                body='@Глобально\nметод '+method+'(Значение: '+self.contracts.sbsl_type(e.children[0].type)+', Null: Булево'+sig+'): Число\n    пер Неизвестно = Ложь\n    для С из '+call+'\n        если Null или '+missing+'\n            Неизвестно = Истина\n        иначе если Значение == С.'+label+'\n            возврат 1\n        ;\n    ;\n    возврат Неизвестно ? -1 : 0\n;\n'
                self.helpers[key]=(method,body)
                self.contracts.method_dependencies.setdefault(query_name(self.query),[]).append(n+'.Запрос')
            args=''.join(', П'+str(p.slot) for p in unique_parameters(self.query))
            if needs_context(q):args+=', Контекст'
            left=self.value(e.children[0],row,group);missing=self.null(e.children[0],row,group)
            if e.children[0].sql_nullable:left='(('+missing+') ? Неопределено : '+left+')'
            return query_name(self.query)+'.'+self.helpers[key][0]+'('+left+', '+missing+args+')'
        if e.kind=='in':
            comparisons=[Expression('compare','Булево',value='=',children=(e.children[0],c)) for c in e.children[1:]]
            result=comparisons[0]
            for c in comparisons[1:]:result=Expression('or','Булево',children=(result,c))
            return self.tri(result,row,group)
        if e.kind in ('and','or'):
            a,b=(self.tri(c,row,group) for c in e.children);dominant='0' if e.kind=='and' else '1';other='1' if e.kind=='and' else '0'
            return '(('+a+' == '+dominant+' или '+b+' == '+dominant+') ? '+dominant+' : (('+a+' == -1 или '+b+' == -1) ? -1 : '+other+'))'
        if e.kind=='not':
            v=self.tri(e.children[0],row,group);return '('+v+' == -1 ? -1 : ('+v+' == 1 ? 0 : 1))'
        return '(('+self.null(e,row,group)+') ? -1 : ('+self.value(e,row,group)+' ? 1 : 0))'

    def aggregate(self,e,group):
        key=identity(e)
        if key not in self.helpers:
            name='Агрегат'+str(len(self.helpers));self.helpers[key]=(name,'')
            child=e.children[0];fn,distinct=e.value
            sig=''.join(', П'+str(p.slot)+': '+self.contracts.sbsl_type(p.type) for p in __import__('element_test.storage_queries',fromlist=['unique_parameters']).unique_parameters(self.query))
            text='@Глобально\nметод '+name+'(Группа: Массив<Комбинация>'+sig+'): '+e.type+'\n    пер Количество = 0\n'
            if fn!='КОЛИЧЕСТВО':text+='    пер Итог: '+e.type+' = Неопределено\n'
            if distinct:text+='    знч Значения = новый Массив<'+self.contracts.sbsl_type(child.type)+'>()\n'
            text+='    для С из Группа\n'
            if child.kind!='star':
                text+='        если '+self.null(child)+'\n            продолжить\n        ;\n        знч Значение = '+self.value(child)+'\n'
                if distinct:text+='        если Значения.Содержит(Значение)\n            продолжить\n        ;\n        Значения.Добавить(Значение)\n'
            text+='        Количество += 1\n'
            if fn!='КОЛИЧЕСТВО':
                text+='        если Итог == Неопределено\n            Итог = Значение\n        иначе\n'
                if fn in ('СУММА','СРЕДНЕЕ'):text+='            Итог = (Итог как Число) + (Значение как Число)\n'
                else:text+='            если (Значение как '+e.type.rstrip('?')+') '+('>' if fn=='МАКСИМУМ' else '<')+' (Итог как '+e.type.rstrip('?')+')\n                Итог = Значение\n            ;\n'
                text+='        ;\n'
            text+='    ;\n'
            result='Количество' if fn=='КОЛИЧЕСТВО' else ('(Количество == 0 ? Неопределено : (Итог как Число) / Количество)' if fn=='СРЕДНЕЕ' else 'Итог')
            text+='    возврат '+result+'\n;\n';self.helpers[key]=(name,text)
        args=''.join(', П'+str(p.slot) for p in __import__('element_test.storage_queries',fromlist=['unique_parameters']).unique_parameters(self.query))
        from .storage_queries import query_name
        return query_name(self.query)+'.'+self.helpers[key][0]+'('+group+args+')'

    def projection(self,e,row='С',group='Группа'):
        v=self.value(e,row,group)
        if e.sql_nullable:v='(('+self.null(e,row,group)+') ? Неопределено : '+v+')'
        if e.type.rstrip('?').endswith('.Ссылка'):
            copy='новый '+e.type.rstrip('?')+'(Идентификатор = ('+v+').Идентификатор)'
            v='('+v+' == Неопределено ? Неопределено : '+copy+')' if e.type.endswith('?') or e.sql_nullable else copy
        return v

    def equal(self,e,a,b):
        na,nb=self.null(e,a),self.null(e,b)
        return '(('+na+' и '+nb+') или (не ('+na+') и не ('+nb+') и '+self.value(e,a)+' == '+self.value(e,b)+'))'


def grouped_tail(query, name, row_type, renderer, parameters):
    """Filter -> groups -> HAVING -> projection DISTINCT -> ORDER -> LIMIT."""
    from .query_composites import null_column
    condition=renderer.tri(query.predicates[0])+' == 1' if query.predicates else 'Истина'
    grouped=bool(query.grouping) or any(c.kind=='aggregate' for e in [*(e for e,_ in query.projections),*query.having,*(e for e,_ in query.ordering)] for c in walk(e))
    text='        знч Группы = новый Массив<ГруппаДанных>()\n'
    if grouped and not query.grouping:text+='        Группы.Добавить(новый ГруппаДанных(С = новый Комбинация(), Строки = новый Массив<Комбинация>()))\n'
    text+='        для С из Строки\n            если '+condition+'\n'
    if query.grouping:
        equal=' и '.join(renderer.equal(e,'С','Г.С') for e in query.grouping)
        text+='                пер Индекс = -1\n                пер Номер = 0\n                для Г из Группы\n                    если '+equal+'\n                        Индекс = Номер\n                        прервать\n                    ;\n                    Номер += 1\n                ;\n                если Индекс < 0\n                    Группы.Добавить(новый ГруппаДанных(С = С, Строки = [С]))\n                иначе\n                    Группы[Индекс].Строки.Добавить(С)\n                ;\n'
    elif grouped:text+='                Группы[0].Строки.Добавить(С)\n                Группы[0].С = С\n'
    else:text+='                Группы.Добавить(новый ГруппаДанных(С = С, Строки = [С]))\n'
    text+='            ;\n        ;\n        знч Отобранные = новый Массив<ГруппаДанных>()\n        для Г из Группы\n            знч С = Г.С\n            знч Группа = Г.Строки\n'
    if query.having:text+='            если '+renderer.tri(query.having[0])+' != 1\n                продолжить\n            ;\n'
    if query.distinct:
        eq=[]
        for e,_ in query.projections:
            na,nb=renderer.null(e),renderer.null(e,'Другой.С','Другой.Строки')
            eq.append('(('+na+' и '+nb+') или (не ('+na+') и не ('+nb+') и '+renderer.value(e)+' == '+renderer.value(e,'Другой.С','Другой.Строки')+'))')
        text+='            пер Дубликат = Ложь\n            для Другой из Отобранные\n                если '+' и '.join(eq)+'\n                    Дубликат = Истина\n                    прервать\n                ;\n            ;\n            если Дубликат\n                продолжить\n            ;\n'
    args=''.join(', П'+str(p.slot) for p in parameters)
    if query.ordering:text+='            пер Позиция = 0\n            пока Позиция < Отобранные.Размер() и не '+name+'.Раньше(Г, Отобранные[Позиция]'+args+')\n                Позиция += 1\n            ;\n            Отобранные.Вставить(Позиция, Г)\n'
    else:text+='            Отобранные.Добавить(Г)\n'
    text+='        ;\n        знч Результат = новый Массив<'+row_type+'>()\n        для Г из Отобранные\n            знч С = Г.С\n            знч Группа = Г.Строки\n'
    if query.limit:text+='            если Результат.Размер() >= '+str(query.limit)+'\n                прервать\n            ;\n'
    text+='            Результат.Добавить(новый '+row_type+'('+', '.join(label+' = '+renderer.projection(e)+', '+null_column(query.projections,i)+' = '+renderer.null(e) for i,(e,label) in enumerate(query.projections))+'))\n        ;\n        возврат Результат\n    ;\n;\n'
    if query.ordering:
        sig=''.join(', П'+str(p.slot)+': '+renderer.contracts.sbsl_type(p.type) for p in parameters)
        text+='@Глобально\nметод Раньше(А: ГруппаДанных, Б: ГруппаДанных'+sig+'): Булево\n'
        for e,desc in query.ordering:
            a,b=renderer.value(e,'А.С','А.Строки'),renderer.value(e,'Б.С','Б.Строки')
            na,nb=renderer.null(e,'А.С','А.Строки'),renderer.null(e,'Б.С','Б.Строки')
            text+='    если ('+na+') != ('+nb+')\n        возврат '+(nb if desc else na)+'\n    ;\n    если не ('+na+') и '+a+' != '+b+'\n        возврат ('+a+' как '+e.type.rstrip('?')+') '+('>' if desc else '<')+' ('+b+' как '+e.type.rstrip('?')+')\n    ;\n'
        text+='    возврат Ложь\n;\n'
    return text
