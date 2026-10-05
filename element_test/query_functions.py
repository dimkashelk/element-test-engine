"""Additional scalar XBQL functions, evaluated by Script, never by Python."""
from dataclasses import replace
from .query_joins import Expression

MATH={'ACOS':('ACos',1),'ASIN':('ASin',1),'ATAN':('ATan',1),'COS':('Cos',1),
      'SIN':('Sin',1),'TAN':('Tan',1),'EXP':('Exp',1),'LOG':('Log',1),'LOG10':('Log10',1),
      'КОРЕНЬ':('Корень',1),'СТЕПЕНЬ':('Степень',2)}


def function_atom(p):
    if p.pos+1 >= len(p.tokens) or p.tokens[p.pos+1][1] != '(':
        return None
    name=p.tokens[p.pos][1].upper()
    if name not in ('ПЕРВЫЙНЕNULL', 'УУИД') and name not in MATH:
        return None
    start=p.tokens[p.pos][2];p.pos+=2;children=[]
    if not p.accept(')'):
        while True:
            children.append(p.condition())
            if not p.accept(','):break
        p.expect(')')
    if name=='УУИД' and children or name=='ПЕРВЫЙНЕNULL' and len(children)<2 or name in MATH and len(children)!=MATH[name][1]:
        p.fail('Неверное число аргументов '+name,start)
    return Expression('math' if name in MATH else 'uuid' if name=='УУИД' else 'first-not-null',name=MATH[name][0] if name in MATH else '',
                      children=tuple(children),range=(start,p.tokens[p.pos-1][3]))


def bind_function(p,e,visible,expected):
    if e.kind=='uuid':return replace(e,type='Ууид')
    if e.kind=='math':
        children=tuple(p.bind(c,visible,'Число') for c in e.children)
        if any(c.type.rstrip('?')!='Число' for c in children):p.fail('Математическая функция требует Число',e.range[0])
        nullable=any(c.sql_nullable for c in children)
        fraction=9
        if e.name=='Степень':
            base=children[0]
            while base.kind=='negate':base=base.children[0]
            if base.kind=='literal':fraction=6
            elif base.kind=='decimal':fraction=min(9,len(base.value.partition('.')[2])+6)
            elif base.kind=='cast' and len(base.value[1])==2:fraction=min(9,base.value[1][1]+6)
        return replace(e,children=children,type='Число'+('?' if nullable else ''),sql_nullable=nullable,value=fraction)
    if e.kind=='first-not-null':
        typed=[p.bind(c,visible,expected) if c.kind not in ('null','undefined','parameter') else None for c in e.children]
        typ=next((c.type.rstrip('?') for c in typed if c),expected or 'Объект')
        children=tuple(c if c else p.bind(raw,visible,typ) for raw,c in zip(e.children,typed))
        if any(c.type.rstrip('?')!=typ for c in children):p.fail('Несовместимые типы ПервыйНеNull',e.range[0])
        # Only reachable alternatives contribute Undefined/nullability.
        reachable=[]
        for c in children:
            reachable.append(c)
            if not c.sql_nullable:break
        nullable=all(c.sql_nullable for c in reachable)
        undefined=any(c.type.endswith('?') and (not c.sql_nullable or c.kind not in ('null',)) for c in reachable)
        return replace(e,children=children,type=typ+('?' if nullable or undefined else ''),sql_nullable=nullable)
    if e.kind=='property':
        child=p.bind(e.children[0],visible)
        if child.type.rstrip('?') not in ('Дата','ДатаВремя','Время') or e.name not in (
            ('Час','Минута','Секунда') if child.type.rstrip('?')=='Время' else
            ('Год','Месяц','День','Час','Минута','Секунда') if child.type.rstrip('?')=='ДатаВремя' else ('Год','Месяц','День')):
            p.fail('Свойство вычисления вне контракта: '+e.name,e.range[0])
        return replace(e,children=(child,),type='Число'+('?' if child.sql_nullable else ''),sql_nullable=child.sql_nullable)
    return None


def function_null(r,e,row,group):
    if e.kind=='uuid':return 'Ложь'
    if e.kind=='first-not-null':return '('+' и '.join('('+r.null(c,row,group)+')' for c in e.children)+')'
    if e.kind=='property':return r.null(e.children[0],row,group)
    if e.kind=='math':return '('+' или '.join('('+r.null(c,row,group)+')' for c in e.children)+')'
    return None


def function_value(r,e,row,group):
    if e.kind=='uuid':return 'Ууид.Случайный()'
    if e.kind=='first-not-null':
        value=r.value(e.children[-1],row,group)
        for child in reversed(e.children[:-1]):
            value='('+r.null(child,row,group)+' ? '+value+' : '+r.value(child,row,group)+')'
        return value
    if e.kind=='property':return '('+r.value(e.children[0],row,group)+' как '+e.children[0].type.rstrip('?')+').'+e.name
    if e.kind=='math':
        if any(c.kind=='null' for c in e.children):return 'Неопределено'
        return 'Стд::'+e.name+'('+', '.join('('+r.value(c,row,group)+' как Число)' for c in e.children)+').Округлить('+str(e.value)+')'
    if e.kind=='method' and e.name=='ПолноеСовпадение':
        from .storage_queries import query_name
        r.contracts.definitions.setdefault('ТестПолноеСовпадение',MATCH_MODULE)
        r.contracts.method_dependencies.setdefault(query_name(r.query),[]).append('ТестПолноеСовпадение.Совпадает')
        return 'ТестПолноеСовпадение.Совпадает('+', '.join('('+r.value(c,row,group)+' как Строка)' for c in e.children)+')'
    return None


# The documented XBQL pattern subset is smaller than Script's regexp engine.
# Validate in Script so captured patterns cannot silently widen that subset.
MATCH_MODULE = r'''@Глобально
метод Совпадает(Текст: Строка, Образец: Строка): Булево
    пер Класс = Ложь
    пер Члены = 0
    пер Н = 0
    пока Н < Образец.Длина()
        знч С = Образец.Подстрока(Н, Н + 1)
        если Класс
            если С == "]"
                если Члены == 0
                    выбросить новый ИсключениеНедопустимоеСостояние("Пустой класс ПолноеСовпадение")
                ;
                Класс = Ложь
            иначе если С == "[" или С == "\\" или С == "-"
                выбросить новый ИсключениеНедопустимоеСостояние("Конструкция ПолноеСовпадение вне XBQL")
            иначе если С != "^" или Члены > 0
                Члены += 1
            ;
        иначе если С == "["
            Класс = Истина
            Члены = 0
        иначе если С == "*"
            если Н == 0 или Образец.Подстрока(Н - 1, Н) != "."
                выбросить новый ИсключениеНедопустимоеСостояние("ПолноеСовпадение допускает только .* ")
            ;
        иначе если "+?(){}|^$]\\".Содержит(С)
            выбросить новый ИсключениеНедопустимоеСостояние("Конструкция ПолноеСовпадение вне XBQL")
        ;
        Н += 1
    ;
    если Класс
        выбросить новый ИсключениеНедопустимоеСостояние("Незакрытый класс ПолноеСовпадение")
    ;
    возврат Текст.ПолноеСовпадение(новый Стд::РегулярныеВыражения::Образец(Образец))
;
'''
