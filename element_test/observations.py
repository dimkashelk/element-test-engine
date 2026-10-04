"""Technical source trace: evaluate each return expression once, retain branches."""
import json
from .indexer import lex, parse_module


def instrument(source, identity, enum_map_value_type=None):
    node = parse_module(source)[0][0]
    label = json.dumps(identity, ensure_ascii=False).replace('${', '\\${')
    params = [p.partition(':')[0].strip() for p in node.parameters(source)]
    args = '{' + ', '.join(json.dumps(p, ensure_ascii=False) + ': ' + p for p in params) + '}'
    if not params:
        args = 'новый Соответствие<Строка, Объект?>()'
    def log(event, value):
        return 'Консоль.Записать("ELEMENT_TRACE " + СериализацияJson.ЗаписатьОбъект({' + '\"symbol\": ' + label + ', "event": "' + event + '", "value": ' + value + '}))'
    entry = '\n    ' + log('enter', args) + '\n'
    if not node.return_span:
        entry += '    попытка\n'
    edits = [(node.header_end, node.header_end, entry)]
    def walk(tree):
        if tree.kind == 'statement' and tree.value == 'возврат' and tree.children:
            yield tree
        for child in tree.children:
            yield from walk(child)
    for index, statement in enumerate(walk(node.expression_tree)):
        expression = statement.children[0]
        name = 'ТестВозврат' + str(index)
        while name in source:
            name += '_'
        indent = source[source.rfind('\n', 0, statement.start) + 1:statement.start]
        original = source[expression.start:expression.end]
        transported = name
        transport = ''
        if enum_map_value_type:
            transported = name + 'Транспорт'
            transport = (indent + 'знч ' + transported + ' = новый Соответствие<Строка, ' + enum_map_value_type + '>()\n'
                         + indent + 'для Элемент из ' + name + '\n' + indent + '    ' + transported + '.Вставить(Элемент.Ключ.ВСтроку(), Элемент.Значение)\n'
                         + indent + ';\n')
        edits.append((statement.start, statement.end, 'знч ' + name + ' = ' + original + '\n'
                      + transport + indent + log('exit', transported) + '\n' + indent + 'возврат ' + name))
    if not node.return_span:
        closing = max(t.start for t in lex(source) if node.header_end <= t.start < node.end and t.value == ';')
        ending = source.rfind('\n', 0, closing) + 1
        # A void method may always throw or return early. A finally observation
        # remains reachable and reports leaving the method on either outcome.
        edits.append((ending, ending, '    вконце\n        ' + log('leave', 'Неопределено') + '\n    ;\n'))
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source
