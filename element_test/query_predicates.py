"""Stage 45 predicate contracts. Runtime matching stays in Script, never Python."""
from dataclasses import replace
from .query_joins import Expression


def comparison_suffix(p, e, negative):
    start=e.range[0]
    if p.accept('МЕЖДУ'):
        low=p.expression();p.expect('И');high=p.expression()
        if negative:p.fail('МЕЖДУ использует префикс НЕ',start)
        return Expression('between','Булево',children=(e,low,high),range=(start,p.tokens[p.pos-1][3]))
    if p.accept('ОТЛИЧАЕТСЯ'):
        p.expect('ОТ');right=p.expression()
        return Expression('distinct','Булево',value=negative,children=(e,right),range=(start,right.range[1]))
    if p.accept('ПОДОБНО'):
        pattern=p.expression();escape=p.expression() if p.accept('СПЕЦСИМВОЛ') else Expression('literal','Строка',value='\\')
        if any(c.kind not in ('literal','parameter') for c in (pattern,escape)):
            p.fail('ПОДОБНО требует постоянный шаблон/спецсимвол или параметр',start)
        result=Expression('like','Булево',children=(e,pattern,escape),range=(start,p.tokens[p.pos-1][3]))
        return Expression('not','Булево',children=(result,),range=result.range) if negative else result
    if p.pos+1<len(p.tokens) and p.tokens[p.pos][1].upper()=='В' and p.tokens[p.pos+1][1].upper()=='ИЕРАРХИИ':
        p.expect('В');p.expect('ИЕРАРХИИ')
        if p.pos<len(p.tokens) and p.tokens[p.pos][1]!='(':
            p.fail('Дополнительная таблица иерархии требует отдельный контракт',start)
        p.expect('(');parents=[]
        while True:
            parents.append(p.expression())
            if not p.accept(','):break
        p.expect(')')
        result=Expression('hierarchy','Булево',children=(e,*parents),range=(start,p.tokens[p.pos-1][3]))
        return Expression('not','Булево',children=(result,),range=result.range) if negative else result
    return None


def bind_predicate(p,e,visible,expected):
    if e.kind=='hierarchy':
        left=p.bind(e.children[0],visible)
        if not left.type.rstrip('?').endswith('.Ссылка'):p.fail('В ИЕРАРХИИ требует ссылочное выражение',e.range[0])
        if left.type.endswith('?') and not left.sql_nullable:p.fail('Nullable-ссылка иерархии требует отдельный контракт Неопределено',e.range[0])
        owner=left.type.rstrip('?')[:-7]
        element=p.contracts.canonical_elements.get(owner)
        if not element:p.fail('Неизвестный владелец иерархии',e.range[0])
        props=element['properties']
        if props.get('ДополнительныеИерархии') or props.get('ИерархияПоУмолчанию') not in (None,'Иерархия'):
            p.fail('Дополнительная иерархия вне подтверждённого контракта',e.range[0])
        parents=tuple(p.bind(c,visible,left.type.rstrip('?')) for c in e.children[1:])
        if any(c.type.endswith('?') for c in parents):p.fail('Nullable-родитель иерархии требует отдельный контракт',e.range[0])
        if any(c.type.rstrip('?')!=left.type.rstrip('?') for c in parents):p.fail('Несовместимые номинальные типы иерархии',e.range[0])
        from .resolution import qualified
        return replace(e,children=(left,*parents),value=(qualified(element),bool(props.get('Иерархический'))),sql_nullable=left.sql_nullable)
    if e.kind=='between':
        left,low,high=e.children
        a=Expression('compare','Булево',value='>=',children=(left,low),range=e.range)
        b=Expression('compare','Булево',value='<=',children=(left,high),range=e.range)
        return p.bind(Expression('and','Булево',children=(a,b),range=e.range),visible)
    if e.kind=='like':
        children=tuple(p.bind(c,visible,'Строка') for c in e.children)
        if any(c.type.rstrip('?')!='Строка' for c in children):p.fail('ПОДОБНО требует Строка',e.range[0])
        return replace(e,children=children,sql_nullable=any(c.sql_nullable for c in children))
    if e.kind=='distinct':
        a,b=e.children
        if a.kind in ('null','undefined','parameter'):
            b=p.bind(b,visible);a=p.bind(a,visible,b.type.rstrip('?'))
        else:
            a=p.bind(a,visible);b=p.bind(b,visible,a.type.rstrip('?'))
        return replace(e,children=(a,b),sql_nullable=False)
    return None


def render_predicate(r,e,row,group):
    if e.kind=='hierarchy':
        from .storage_queries import query_name
        key=('hierarchy',e.value,e.children[0].type)
        typ=e.children[0].type.rstrip('?')
        if key not in r.helpers:
            method='ВИерархии'+str(len(r.helpers))
            body='@Глобально\nметод '+method+'(Значение: '+typ+', Родители: Массив<'+typ+'>): Булево\n    пер Текущий = Значение.Идентификатор.ВСтроку()\n    знч Посещенные = новый Массив<Строка>()\n    пока не Посещенные.Содержит(Текущий)\n        Посещенные.Добавить(Текущий)\n        для Родитель из Родители\n            если Родитель.Идентификатор.ВСтроку() == Текущий\n                возврат Истина\n            ;\n        ;\n'
            if e.value[1]:
                body+='        знч JSON = ТестСессия.Прочитать('+r.contracts.literal(e.value[0],'Строка')+', Текущий)\n        если JSON == Неопределено\n            возврат Ложь\n        ;\n        знч Данные = СериализацияJson.ПрочитатьОбъект(JSON как Строка) как Соответствие<Строка, Объект?>\n        если не Данные.СодержитКлюч("Родитель") или Данные["Родитель"] == Неопределено\n            возврат Ложь\n        ;\n        знч Предок = Данные["Родитель"] как Соответствие<Строка, Объект?>\n        Текущий = Предок["Идентификатор"] как Строка\n'
            else:body+='        возврат Ложь\n'
            body+='    ;\n    возврат Ложь\n;\n';r.helpers[key]=(method,body)
        values='['+', '.join(r.value(c,row,group) for c in e.children[1:])+']'
        return '(('+r.null(e.children[0],row,group)+') ? -1 : ('+query_name(r.query)+'.'+r.helpers[key][0]+'(('+r.value(e.children[0],row,group)+' как '+typ+'), '+values+') ? 1 : 0))'
    if e.kind=='like':
        r.contracts.definitions.setdefault('ТестШаблоны',LIKE_MODULE)
        from .storage_queries import query_name
        r.contracts.method_dependencies.setdefault(query_name(r.query),[]).append('ТестШаблоны.Совпадает')
        values=[r.value(c,row,group) for c in e.children]
        missing=' или '.join('('+r.null(c,row,group)+')' for c in e.children)
        return '(('+missing+') ? -1 : (ТестШаблоны.Совпадает('+', '.join('('+v+' как Строка)' for v in values)+') ? 1 : 0))'
    if e.kind=='distinct':
        a,b=e.children;na,nb=r.null(a,row,group),r.null(b,row,group)
        eq='Ложь' if a.kind=='null' or b.kind=='null' or a.type.rstrip('?')!=b.type.rstrip('?') else r.value(a,row,group)+' == '+r.value(b,row,group)
        equal='(('+na+' и '+nb+') или (не ('+na+') и не ('+nb+') и ('+eq+')))'
        return '('+('' if e.value else 'не ')+equal+' ? 1 : 0)'
    if e.kind=='exists-query':
        from .storage_queries import generate_query,unique_parameters
        from .query_composites import mapped_args,needs_context,internal
        q=r.query.subqueries[e.value];n=generate_query(q,r.contracts)
        outer={name:(typ,source,field,null_field) for name,typ,source,field,null_field in q.outer_parameters}
        args=[]
        for p in unique_parameters(q):
            if p.expression in outer:
                typ,source,field,null_field=outer[p.expression]
                args.append(r.value(Expression('field',typ,name=field,source=source,null_field=null_field),row,group))
            else:
                args.append('П'+str(next(x.slot for x in r.query.parameters if (x.start,x.end)==(p.start,p.end))))
        if needs_context(q):args.append('Контекст')
        call=n+'.Создать('+', '.join(args)+').'+('ВыполнитьВнутренне' if internal(q) else 'Выполнить')+'()'
        return '('+call+'.Пусто() ? 0 : 1)'
    return None


LIKE_MODULE = r'''@Глобально
метод Совпадает(Значение: Строка, Шаблон: Строка, Экранирование: Строка): Булево
    если Шаблон.Пусто() или Экранирование.Длина() != 1
        выбросить новый ИсключениеНедопустимоеСостояние("Некорректный шаблон ПОДОБНО")
    ;
    знч Текст = Значение.ВВерхнийРегистр()
    знч Маска = Шаблон.ВВерхнийРегистр()
    знч Escape = Экранирование.ВВерхнийРегистр()
    пер Доступ = новый Массив<Булево>()
    пер Н = 0
    пока Н <= Текст.Длина()
        Доступ.Добавить(Н == 0)
        Н += 1
    ;
    пер П = 0
    пока П < Маска.Длина()
        пер Символ = Маска.Подстрока(П, П + 1)
        пер Вид = "literal"
        пер Класс = ""
        пер Отрицание = Ложь
        П += 1
        если Символ == Escape
            если П == Маска.Длина()
                выбросить новый ИсключениеНедопустимоеСостояние("Спецсимвол завершает шаблон")
            ;
            Символ = Маска.Подстрока(П, П + 1)
            П += 1
        иначе если Символ == "%"
            Вид = "many"
        иначе если Символ == "_"
            Вид = "one"
        иначе если Символ == "["
            Вид = "class"
            если П < Маска.Длина() и Маска.Подстрока(П, П + 1) == "^"
                Отрицание = Истина
                П += 1
            ;
            пока П < Маска.Длина() и Маска.Подстрока(П, П + 1) != "]"
                пер Член = Маска.Подстрока(П, П + 1)
                П += 1
                если Член == Escape
                    если П == Маска.Длина()
                        выбросить новый ИсключениеНедопустимоеСостояние("Незакрытый класс шаблона")
                    ;
                    Член = Маска.Подстрока(П, П + 1)
                    П += 1
                ;
                Класс += Член
            ;
            если П == Маска.Длина() или Класс.Пусто()
                выбросить новый ИсключениеНедопустимоеСостояние("Пустой или незакрытый класс шаблона")
            ;
            П += 1
        ;
        знч Следующий = новый Массив<Булево>()
        Следующий.Добавить(Вид == "many" и Доступ[0])
        Н = 1
        пока Н <= Текст.Длина()
            знч Буква = Текст.Подстрока(Н - 1, Н)
            пер Есть = Вид == "one" или Символ == Буква
            если Вид == "class"
                Есть = Класс.Содержит(Буква) != Отрицание
            ;
            Следующий.Добавить(Вид == "many" ? (Доступ[Н] или Следующий[Н - 1]) : (Доступ[Н - 1] и Есть))
            Н += 1
        ;
        Доступ = Следующий
    ;
    возврат Доступ[Текст.Длина()]
;
'''
