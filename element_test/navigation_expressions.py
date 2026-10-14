"""Typed navigation gates. Python resolves declarations; Script evaluates them."""
import re

from .declarative_bindings import ExpressionCompiler, DeclarationMismatch
from .form_context import add_structure, member_path, validate_path
from .indexer import IDENT
from .yaml_io import InvalidTestError, UnsupportedSyntaxError

CONTEXT_TYPE = 'ТестНавигация.Контекст'


def prepare_context(c, context):
    if not isinstance(context, dict):
        raise InvalidTestError('navigationContext требует словарь именованных значений')
    fields, values = [], {}
    for name, value in context.items():
        if not isinstance(name, str) or not re.fullmatch(IDENT, name):
            raise InvalidTestError('Некорректное имя navigationContext')
        if isinstance(value, dict) and set(value) == {'type', 'value'}:
            typ, value = value['type'], value['value']
            if not isinstance(typ, str):
                raise InvalidTestError('navigationContext.type требует строку')
        else:
            typ = 'Булево' if type(value) is bool else 'Число' if type(value) in {int, float} else 'Строка' if isinstance(value, str) else None
            if typ is None:
                raise InvalidTestError('Составной navigationContext требует type/value')
        c.require(typ)
        fields.append({'Имя': name, 'Тип': c.canonical_type(typ)})
        values[name] = value
    add_structure(c, CONTEXT_TYPE, fields)
    c.literal(values, CONTEXT_TYPE)
    return values


class NavigationCompiler(ExpressionCompiler):
    def __init__(self, plan):
        super().__init__(plan.contracts, CONTEXT_TYPE)
        self.plan = plan

    def visit(self, node, source):
        # Native constants spelling: Owner.Получить().Field (including imports).
        tail, current = [], node
        while current.kind == 'member' and current.children[-1].kind == 'name':
            tail.insert(0, current.children[-1].value)
            current = current.children[0]
        if tail and current.kind == 'call' and len(current.children) == 1:
            path = member_path(current.children[0])
            if path and path[-1] == 'Получить':
                from .session_contracts import add_constants, generate_session_types
                matches = self.c.resolve('.'.join(path[:-1]))
                if len(matches) != 1 or matches[0]['elementType'] != 'НаборКонстант':
                    raise UnsupportedSyntaxError('Неизвестный API условия навигации')
                state = add_constants(self.plan, matches[0])
                generate_session_types(self.plan, self.c, reuse=True)
                typ = validate_path(self.c, state['type'], tail)
                self.symbols.append({'owner': state['owner'], 'path': tail, 'type': typ})
                return state['alias'] + '.Получить().' + '.'.join(tail), typ
        return super().visit(node, source)


def compile_gate(value, plan, owner):
    if type(value) is bool:
        return 'Истина' if value else 'Ложь'
    if value in ('Истина', 'Ложь'):
        return value
    c = plan.contracts
    previous = c.namespace, c.imports
    c.namespace, c.imports = owner['namespace'], owner['properties'].get('Импорт', [])
    try:
        expression, typ = NavigationCompiler(plan).compile(value)
        if typ != 'Булево':
            raise UnsupportedSyntaxError('Условие навигации требует результат Булево')
        return expression
    except DeclarationMismatch as exc:
        raise UnsupportedSyntaxError('Условие навигации: ' + str(exc)) from exc
    finally:
        c.namespace, c.imports = previous


def set_context(plan, action):
    from .declarative_bindings import fixture_path
    if not isinstance(action['path'], str):
        raise InvalidTestError('navigationActions.set.path требует строку')
    expression, typ = fixture_path(plan.contracts, CONTEXT_TYPE, action['path'])
    return '    ' + expression + ' = ' + plan.contracts.literal(action['value'], typ) + '\n'


def set_constant(plan, action, index):
    from .navigation import resolve
    from .session_contracts import add_constants, generate_session_types
    if not isinstance(action['path'], str) or not all(re.fullmatch(IDENT, part) for part in action['path'].split('.')):
        raise InvalidTestError('navigationActions.setConstant.path требует путь поля')
    declaration = next(e for e in plan.model['elements'] if e['sourceFile'] == plan.entry.identity.source_file)
    owner = resolve(plan.model, declaration, action['owner'], {'НаборКонстант'})
    state = add_constants(plan, owner)
    generate_session_types(plan, plan.contracts, reuse=True)
    typ = validate_path(plan.contracts, state['type'], action['path'].split('.'), teacher=True)
    name = 'Constants' + str(index)
    return ('    знч ' + name + ' = ' + state['alias'] + '.Получить()\n'
            '    ' + name + '.' + action['path'] + ' = ' + plan.contracts.literal(action['value'], typ) + '\n'
            '    ' + state['alias'] + '.ТестЗаписать(' + name + ')\n')


def render_navigation(plan):
    c, nav = plan.contracts, plan.navigation
    setup = '    знч Контекст = ' + c.literal(plan._navigation_values, CONTEXT_TYPE) + '\n'
    code = '    знч Actions = новый Массив<Строка>()\n'
    def item_expression(item):
        props = {k: v for k, v in item.items() if k not in {'visible', 'available'}}
        literal = '{' + ', '.join(c.literal(k, 'Строка') + ': ' + c.literal(v, 'Объект') for k, v in props.items()) + '}'
        conditions = nav['conditions'][item['id']]
        return literal[:-1] + ', "visible": ' + conditions['visible'] + ', "available": ' + conditions['available'] + '}'
    for i, action in enumerate(plan._surface_actions):
        op = action['operation']
        if op in {'set', 'setConstant'}:
            code += action['code']
            expression = '{"operation":' + c.literal(op, 'Строка') + ', "context": Контекст}'
        elif op == 'resolve':
            routes = '[' + ', '.join(item_expression(r) for r in nav['routes']) + ']' if nav['routes'] else 'новый Массив<Объект>()'
            sections = '[' + ', '.join(item_expression(r) for r in nav['sections']) + ']' if nav['sections'] else 'новый Массив<Объект>()'
            expression = '{"operation":"resolve", "routes": ' + routes + ', "sections": ' + sections + '}'
        else:
            route = action['route']
            conditions = nav['conditions'][route['id']]
            outcome, target = 'Outcome'+str(i), 'Target'+str(i)
            code += ('    пер '+outcome+' = "blocked"\n    пер '+target+': Объект? = Неопределено\n'
                     '    если '+conditions['visible']+' и '+conditions['available']+'\n'
                     '        '+outcome+' = "opened-model"\n        '+target+' = '+c.literal(route['target'], 'Объект')+'\n    ;\n')
            expression = '{"operation":"open", "outcome":'+outcome+', "routeId":'+c.literal(route['id'], 'Строка')+', "target":'+target+'}'
        code += '    Actions.Добавить(СериализацияJson.ЗаписатьОбъект('+expression+'))\n'
    return setup, code
