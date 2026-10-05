"""Typed single-pass result state; lowered loops keep native control flow.

Standalone Script has no database QueryResult or user-defined Iterable. Each
row type therefore gets a concrete result and verified for/исп source lowering.
The cursor methods are private adapter implementation, never platform API.
"""
from hashlib import sha256
import re
from .indexer import IDENT, parse_module, mask_noncode, lex
from .yaml_io import UnsupportedSyntaxError


def result_type(row, c):
    name = 'ТестРезультат' + sha256(row.encode()).hexdigest()[:16]
    typ = name + '.Результат'
    if typ in c.definitions:
        return typ
    text = '''@Глобально
структура Результат
    знч Строки: Массив<ROW>
    пер Позиция: Число = -1
    пер Закрыт: Булево
    пер Начат: Булево
    пер ЗапретОбхода: Булево
    @Глобально
    метод Закрыть()
        ЗапретОбхода = Истина
        ТестОсвободить()
    ;
    метод ТестОсвободить()
        если не Закрыт
            Закрыт = Истина
            ТестСессия.Событие("query-result:close")
        ;
    ;
    @Глобально
    метод ТестНачать()
        если Закрыт или Начат
            выбросить новый ИсключениеНедопустимоеСостояние("Результат запроса допускает один обход")
        ;
        Начат = Истина
    ;
    @Глобально
    метод ТестСледующий(): Булево
        если Закрыт
            возврат Ложь
        ;
        Позиция += 1
        если Позиция >= Строки.Размер()
            ТестОсвободить()
            возврат Ложь
        ;
        если Позиция == Строки.Размер() - 1
            ТестОсвободить()
        ;
        возврат Истина
    ;
    @Глобально
    метод ТестТекущая(): ROW
        возврат Строки[Позиция]
    ;
    @Глобально
    метод ВМассив(): Массив<ROW>
        ТестНачать()
        знч Копия = новый Массив<ROW>()
        пока ТестСледующий()
            Копия.Добавить(ТестТекущая())
        ;
        возврат Копия
    ;
    @Глобально
    метод Пусто(): Булево
        ТестНачать()
        возврат не ТестСледующий()
    ;
    @Глобально
    метод ПервыйИлиНеопределено(): ROW?
        ТестНачать()
        возврат ТестСледующий() ? ТестТекущая() : Неопределено
    ;
    @Глобально
    метод Первый(): ROW
        знч Значение = ПервыйИлиНеопределено()
        если Значение == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("Пустой результат запроса")
        ;
        возврат Значение
    ;
    @Глобально
    метод ЕдинственныйИлиНеопределено(): ROW?
        ТестНачать()
        если Строки.Размер() > 1
            ТестОсвободить()
            выбросить новый ИсключениеНедопустимоеСостояние("Результат содержит несколько строк")
        ;
        возврат ТестСледующий() ? ТестТекущая() : Неопределено
    ;
    @Глобально
    метод Единственный(): ROW
        знч Значение = ЕдинственныйИлиНеопределено()
        если Значение == Неопределено
            выбросить новый ИсключениеНедопустимоеСостояние("Пустой результат запроса")
        ;
        возврат Значение
    ;
;
@Глобально
метод Создать(Строки: Массив<ROW>): Результат
    ТестСессия.Событие("query-result:open")
    знч Р = новый Результат(Строки = Строки)
    если Строки.Пусто()
        Р.ТестОсвободить()
    ;
    возврат Р
;
'''.replace('ROW', row)
    # An empty result is already closed but its first empty traversal is valid.
    text = text.replace('если Закрыт или Начат', 'если ЗапретОбхода или Начат или (Закрыт и не Строки.Пусто())')
    c.definitions[typ] = text
    c.method_dependencies[typ] = [row, 'ТестСессия.Записи']
    return typ


def wrap_query(name, query, c):
    from .storage_queries import unique_parameters
    wrapper = 'ТестРесурсЗапрос' + sha256(name.encode()).hexdigest()[:16]
    row = query.fill['type'] if query.fill else name + '.СтрокаРезультата'
    typ = result_type(row, c)
    if wrapper not in c.definitions:
        args = unique_parameters(query)
        signature = ', '.join('П'+str(p.slot)+': '+c.sbsl_type(p.type) for p in args)
        values = ', '.join('П'+str(p.slot) for p in args)
        c.definitions[wrapper] = ('@Глобально\nструктура Запрос\n    знч Основа: '+name+'.Запрос\n'
            '    @Глобально\n    метод Выполнить(): '+typ+'\n'
            '        возврат '+typ.removesuffix('.Результат')+'.Создать(Основа.Выполнить())\n    ;\n;\n'
            '@Глобально\nметод Создать('+signature+'): Запрос\n'
            '    возврат новый Запрос(Основа = '+name+'.Создать('+values+'))\n;\n')
        c.method_dependencies[wrapper] = [name+'.Запрос', typ]
    return wrapper, typ


def lower_results(plan, symbol, variables):
    """variables maps proven result locals to their declaration end.

    Reject mutable/reassigned aliases before lowering; arbitrary objects with
    Закрыть/Выполнить are left to their project/native declarations.
    """
    from .source_contracts import _scope_end, ResourceScope
    from .execution_plan import CapabilityBinding
    source = symbol.source
    node = parse_module(source)[0][0]
    code = mask_noncode(source)
    spans = plan.source_transforms.setdefault((symbol.identity.source_file, symbol.identity.declaration), [])
    for var, end in variables.items():
        if re.search(rf'\b{re.escape(var)}\s*=(?!=)', code[end:]):
            raise UnsupportedSyntaxError('Переприсваивание результата требует flow-sensitive Query API: ' + var)
    for item in node.expression_tree.walk():
        if item.kind != 'statement' or item.value != 'для' or len(item.children) != 1:
            continue
        e = item.children[0]
        direct = False
        if e.kind == 'name':
            if e.value not in variables or variables[e.value] >= item.start:
                continue
            var = e.value
        else:
            from .query_api import api_receivers
            queries, _ = api_receivers(symbol, plan.model)
            member = e.children[0] if e.kind == 'call' and len(e.children) == 1 else None
            if (not member or member.kind != 'member' or len(member.children) != 2
                or member.children[1].value != 'Выполнить'):
                continue
            receiver = member.children[0]
            if receiver.kind != 'query' and not (receiver.kind == 'name' and receiver.value in queries):
                continue
            direct = True
            var = 'ТестОбход' + sha256((str(symbol.identity)+str(item.start)).encode()).hexdigest()[:12]
        match = re.fullmatch(rf'для\s+({IDENT})\s+из\s*', code[item.start:e.start])
        if not match:
            raise UnsupportedSyntaxError('Неподдержанная форма обхода результата')
        indent = source[source.rfind('\n', 0, item.start)+1:item.start]
        prelude = 'знч ' + var + ' = ' + source[e.start:e.end] + '\n' + indent if direct else ''
        spans.append((item.start, item.end, prelude + var + '.ТестНачать()\n' + indent +
                      'пока ' + var + '.ТестСледующий()\n' + indent +
                      '    знч ' + match[1] + ' = ' + var + '.ТестТекущая()'))
    for statement in node.expression_tree.walk():
        if statement.kind != 'statement' or statement.value != 'исп':
            continue
        line = source[statement.start:statement.end]
        match = re.match(rf'исп\s+({IDENT})\s*=', line)
        if not match or match[1] not in variables:
            continue
        scope, boundary = _scope_end(node.expression_tree, statement)
        if boundary is None:
            boundary = max(t.start for t in lex(code) if scope.start < t.start < scope.end and t.value == ';')
        ending = source.rfind('\n', 0, boundary) + 1
        if source[ending:boundary].strip():
            raise UnsupportedSyntaxError('Конец ресурса результата требует отдельную строку')
        indent = source[source.rfind('\n', 0, statement.start)+1:statement.start]
        spans.append((statement.start, statement.start+3, 'знч'))
        spans.append((statement.end, statement.end, '\n' + indent + 'попытка'))
        spans.append((ending, ending, indent + 'вконце\n' + indent + '    ' + match[1] + '.Закрыть()\n' + indent + ';\n'))
        plan.resources.append(ResourceScope(symbol.identity.source_file, symbol.identity.declaration,
            symbol.start+statement.start, symbol.start+statement.end, symbol.start+scope.start,
            symbol.start+boundary, 'query-result-single-pass-044', 'materialized-script',
            ('no-native-database-cursor',)))
        plan.bindings.append(CapabilityBinding('query-resource', 'исп', 'РезультатЗапроса',
            'single-pass-044', 'Закрытие в finally при возврате, исключении и выходе из области',
            symbol.identity.source_file, symbol.start+statement.start, symbol.start+statement.end))
