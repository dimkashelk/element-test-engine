"""Declaration matching and typed AST lowering; Script computes all values."""
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from .expression_ast import parse_expression
from .form_context import tree_values, member_path, add_structure, validate_path
from .generated_types import ProjectTypes, union_members
from .indexer import IDENT, split_parameters, parse_module
from .yaml_io import InvalidTestError, UnsupportedSyntaxError, InputError


class DeclarationMismatch(InputError):
    """A supported requirement is violated by the student's declaration."""


def get_path(value, path):
    for part in path:
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise DeclarationMismatch('Отсутствует YAML-путь: ' + '/'.join(path)) from None
    return value


def fixture_path(c, typ, path):
    """Typed fixture mutation, including an actual live array row."""
    expression = 'Контекст'
    for part in path.split('.'):
        collection = re.fullmatch(r'Массив<(.+)>',typ)
        if collection and part.isdigit():
            expression += '['+part+']'
            typ = collection[1]
        else:
            if not re.fullmatch(IDENT,part):
                raise InvalidTestError('Некорректный путь fixture: '+path)
            typ = validate_path(c,typ,[part],teacher=True)
            expression += '.'+part
    return expression,typ


def form_element(model, selector):
    forms = [e for e in model['elements'] if e['elementType'] == 'КомпонентИнтерфейса'
             and e['name'] == selector['name'] and ('namespace' not in selector or e['namespace'] == selector['namespace'])]
    if not forms:
        raise DeclarationMismatch('Обязательная форма отсутствует')
    if len(forms) != 1:
        raise UnsupportedSyntaxError('Неоднозначная форма: ' + str([e['sourceFile'] for e in forms]))
    return forms[0]


def resolver_for(model, element):
    path = element['sourceFile'].removesuffix('.yaml') + '.xbsl'
    imports = next((m.get('imports',[]) for m in model['modules'] if m['sourceFile'] == path), [])
    c = ProjectTypes(model, element['namespace'], imports)
    c.rename_collisions = True
    c.reference_id_type = 'Ууид'
    c.declarative_read = True
    return c


def validate_declaration(element):
    base = element['properties'].get('Наследует')
    if not isinstance(base,dict) or not isinstance(base.get('Тип'),str):
        raise DeclarationMismatch('Наследует требует объявленный тип формы')
    properties = element['properties'].get('Свойства',[])
    if not isinstance(properties,list):
        raise DeclarationMismatch('Свойства формы должны быть списком')
    names = set()
    for field in properties:
        if (not isinstance(field,dict) or not isinstance(field.get('Тип'),str)
                or not re.fullmatch(IDENT,str(field.get('Имя',''))) or field['Имя'] in names):
            raise DeclarationMismatch('Некорректное или повторное свойство формы')
        names.add(field['Имя'])
    component_names = set()
    for _,item in tree_values(element['properties'].get('Наследует',{})):
        if 'Имя' not in item or 'Тип' not in item or item['Тип'] == 'АбсолютныйШрифт':
            continue
        if not isinstance(item['Тип'],str):
            raise DeclarationMismatch('Тип компонента должен быть строкой')
        name = item['Имя']
        if not isinstance(name,str) or not re.fullmatch(IDENT,name) or name in component_names:
            raise DeclarationMismatch('Некорректное или повторное имя компонента: '+str(name))
        component_names.add(name)


def component_value_type(item):
    typ = item.get('Тип','')
    if typ == 'Флажок':
        return 'Булево'
    if typ == 'Надпись':
        return 'Строка'
    if typ.startswith('ПолеВвода<') and typ.endswith('>'):
        return typ[10:-1]
    if typ.startswith('СтандартнаяКолонкаТаблицы<') and typ.endswith('>'):
        parts = split_parameters(typ[len('СтандартнаяКолонкаТаблицы<'):-1])
        return parts[1].strip() if len(parts) == 2 else None
    return None


def select_components(element, selector, c):
    props = element['properties']
    if not selector:
        return [(('Наследует',), props.get('Наследует',{}))]
    if 'path' in selector:
        path = tuple(selector['path'])
        candidates = [(path,get_path(props,path))]
    elif selector.get('role') == 'СвойствоФормы':
        candidates = [(('Свойства',str(i)),v) for i,v in enumerate(props.get('Свойства',[]))]
    else:
        candidates = [(p,v) for p,v in tree_values(props.get('Наследует',{}),('Наследует',)) if 'Тип' in v]
    matched = []
    for p,v in candidates:
        if not isinstance(v, dict):
            continue
        if 'name' in selector and v.get('Имя') != selector['name']:
            continue
        if 'role' in selector and selector['role'] != 'СвойствоФормы' and v.get('Тип','').split('<')[0] != selector['role']:
            continue
        if 'dataType' in selector and (component_value_type(v) is None or
                c.canonical_type(component_value_type(v)) != c.canonical_type(selector['dataType'])):
            continue
        if 'binding' in selector:
            value = v.get('Значение')
            if not isinstance(value,str) or not value.startswith('='):
                continue
            paths = []
            for node in parse_expression(value[1:]).walk():
                bound = member_path(node)
                if bound:
                    paths.append(bound[1:] if bound[0] == 'этот' else bound)
            if selector['binding'].split('.') not in paths:
                continue
        if any(v.get(k) != val for k,val in selector.get('declaration',{}).items()):
            continue
        if 'within' in selector:
            ancestors = select_components(element,selector['within'],c)
            if not any(len(a)<len(p) and p[:len(a)] == a for a,_ in ancestors):
                continue
        matched.append((p,v))
    return matched


def unique_component(element, selector, c):
    matches = select_components(element,selector,c)
    if not matches:
        raise DeclarationMismatch('Обязательный компонент отсутствует: ' + str(selector))
    if len(matches) != 1:
        raise UnsupportedSyntaxError('Неоднозначное сопоставление: ' + str([list(p) for p,_ in matches]))
    return matches[0]


def table_context(element, path, c):
    nearest = None
    for n in range(1,len(path)+1):
        item = get_path(element['properties'],path[:n])
        if isinstance(item,dict):
            typ = item.get('Тип','')
            if typ.startswith('Таблица<ИсточникДанныхМассив<') and typ.endswith('>>'):
                nearest = (path[:n],item,typ[len('Таблица<ИсточникДанныхМассив<'):-2])
    return nearest


def canonical_ui_type(c, typ):
    # ProjectTypes resolves metadata tokens inside UI generic containers too.
    return c.canonical_type(typ).replace(' ','')


def command_binding(element, path, expression, c):
    if not isinstance(expression,str) or not expression.startswith('='):
        raise DeclarationMismatch('Ожидается декларативная ссылка на команду')
    ast = parse_expression(expression.removeprefix('='))
    chain = member_path(ast)
    if chain and chain[0] == 'этот':
        chain = chain[1:]
    if not chain:
        raise DeclarationMismatch('Команда должна быть ссылкой на объявленную команду')
    base = element['properties']['Наследует']['Тип']
    fields = {v['Имя'] for v in element['properties'].get('Свойства',[])}
    allowed = {'Обновить','Записать','ЗаписатьИЗакрыть'} if base.startswith(('ФормаОбъекта<','ФормаЗаписиНабораКонстант<')) else set()
    if base.startswith('ФормаОбъекта<'):
        allowed |= {'СоздатьКопию','СоздатьНаОсновании','Удалить','Восстановить'}
    if len(chain) == 1 and chain[0] in allowed and chain[0] not in fields:
        return {'owner':base,'command':chain[0]}
    if len(chain) == 3 and chain[0] == 'Компоненты' and 'Компоненты' not in fields and chain[2] in {'ДобавитьСтроку','ПереместитьСтрокуВверх','ПереместитьСтрокуВниз','Удалить'}:
        components = select_components(element,{'name':chain[1]},c)
        table = table_context(element,path,c)
        if len(components) == 1 and table and components[0][0] == table[0]:
            fragment = None
            for n in range(len(table[0]),len(path)):
                item = get_path(element['properties'],path[:n])
                if isinstance(item,dict) and item.get('Тип','').startswith('ФрагментКомандногоИнтерфейса'):
                    fragment = canonical_ui_type(c,item['Тип'])
            row = c.canonical_type(table[2])
            allowed_fragment = ({'ФрагментКомандногоИнтерфейса'} if chain[2] == 'ДобавитьСтроку' else
                {'ФрагментКомандногоИнтерфейса<КомандаСПараметром<'+row+'>>',
                 'ФрагментКомандногоИнтерфейса<КомандаСПараметром<Массив<'+row+'>>>'})
            if fragment not in allowed_fragment:
                raise DeclarationMismatch('Тип команды не соответствует фрагменту таблицы')
            return {'owner':list(table[0]),'command':chain[2],'rowType':c.canonical_type(table[2])}
    raise DeclarationMismatch('Команда не разрешена в текущем фрагменте: ' + expression)


def handler_binding(root, model, element, path, item, c):
    name = item.get('Обработчик',item.get('ПриНажатии'))
    module = next((m for m in model['modules'] if m['sourceFile'] == element['sourceFile'].removesuffix('.yaml')+'.xbsl'),None)
    if not module:
        raise DeclarationMismatch('Нет модуля собственного обработчика')
    source = (root/module['sourceFile']).read_text(encoding='utf-8-sig')
    nodes = [n for n in parse_module(source)[0] if n.name == name]
    if len(nodes) != 1:
        raise DeclarationMismatch('Обработчик отсутствует или неоднозначен: ' + str(name))
    node = nodes[0]
    if node.return_type(source) not in {None,'ничто'}:
        raise DeclarationMismatch('Обработчик должен иметь void-результат')
    typ = item.get('Тип','')
    expected = ['Кнопка','СобытиеПриНажатии'] if typ == 'Кнопка' else ['ОбычнаяКоманда'] if typ == 'ОбычнаяКоманда' else None
    if typ.startswith('КомандаСПараметром<'):
        expected = [typ,typ[len('КомандаСПараметром<'):-1]]
    actual = [p.partition(':')[2].strip() for p in node.parameters(source)]
    if expected is None or [canonical_ui_type(c,t) for t in actual] != [canonical_ui_type(c,t) for t in expected]:
        raise DeclarationMismatch('Несовместимая сигнатура обработчика: ' + str(name))
    from .resolution import method_visible
    method = next((m for m in module['methods'] if m['name'] == name),None)
    if method and not method_visible(method,module,module):
        raise DeclarationMismatch('Обработчик недоступен')
    return {'method':name,'sourceFile':module['sourceFile'],'parameters':actual}


def structural_facts(root, model, check):
    req = check['formRequirement']['requirement']
    try:
        e = form_element(model,check['formRequirement']['form']); c = resolver_for(model,e)
        validate_declaration(e)
        a = req['assert']; matches = select_components(e,req.get('selector',{}),c)
        if a.get('exists') is False:
            return {'status':'EXECUTED','actual':not matches,'components':[list(p) for p,_ in matches]}
        path,item = unique_component(e,req.get('selector',{}),c)
        if 'type' in a and canonical_ui_type(c,item.get('Тип','')) != canonical_ui_type(c,a['type']):
            raise DeclarationMismatch('Несовместимый тип: ' + str(item.get('Тип')))
        for key,value in a.get('properties',{}).items():
            if get_path(item,key.split('.')) != value:
                raise DeclarationMismatch('Не соответствует свойство: ' + key)
        details = {}
        if a.get('command'):
            spec = a['command']
            command_path = tuple(spec['path']) if isinstance(spec,dict) else tuple(spec) if isinstance(spec,list) else path + tuple(spec.split('.'))
            expression = get_path(e['properties'],command_path)
            details['command'] = command_binding(e,command_path,expression,c)
            actual_path = member_path(parse_expression(expression.removeprefix('=')))
            expected_path = member_path(parse_expression(spec['reference'].removeprefix('='))) if isinstance(spec,dict) else None
            if actual_path and actual_path[0] == 'этот':
                actual_path = actual_path[1:]
            if isinstance(spec,dict) and actual_path != expected_path:
                raise DeclarationMismatch('Команда не соответствует объявленному назначению')
        if a.get('handler'):
            details['handler'] = handler_binding(root,model,e,path,item,c)
        elif a.get('handler') is False and any(item.get(key) is not None for key in ('Обработчик','ПриНажатии')):
            raise DeclarationMismatch('Собственный обработчик запрещён описанием')
        if 'order' in a:
            names = [v.get('Имя') for v in item.get('Содержимое',[]) if isinstance(v,dict)]
            ordered = a['order']
            if [n for n in names if n in ordered] != ordered:
                raise DeclarationMismatch('Нарушен явно заданный порядок')
        return {'status':'EXECUTED','actual':True,'sourceFile':e['sourceFile'],
                'yamlPath':list(command_path if a.get('command') else path),**details}
    except DeclarationMismatch as exc:
        return {'status':'EXECUTED','actual':False,'message':str(exc)}
    except InvalidTestError as exc:
        return {'status':'ERROR','reasonCode':'invalid_test','message':str(exc)}
    except UnsupportedSyntaxError as exc:
        return {'status':'UNSUPPORTED','reasonCode':'unsupported_syntax','message':str(exc)}


class ExpressionCompiler:
    def __init__(self,c,context_type,row_type=None,variable='Контекст'):
        self.c,self.context_type,self.row_type = c,context_type,row_type
        self.variable = variable
        self.symbols = []

    def compile(self, text):
        if not isinstance(text,str) or not text.startswith('='):
            raise DeclarationMismatch('Для выражения требуется строка с =')
        source = text[1:]
        return self.visit(parse_expression(source),source)

    def visit(self,n,source):
        c = self.c
        path = member_path(n)
        if path:
            if path == ['Истина'] or path == ['Ложь']:
                return path[0], 'Булево'
            if path == ['Неопределено']:
                return 'Неопределено','ничто'
            own_names = {f['Имя'] for f in c.fields.get(self.context_type,[])}
            root,path_tail = ('RowData',path[1:]) if path[0] == 'RowData' and 'RowData' not in own_names else (self.variable,path[1:] if path[0] == 'этот' else path)
            typ = self.row_type if root == 'RowData' else self.context_type
            if not typ:
                raise DeclarationMismatch('RowData вне колонки текущей таблицы')
            if path[0] == 'Компоненты' and not any(f['Имя'] == 'Компоненты' for f in c.fields.get(typ,[])):
                raise UnsupportedSyntaxError('Состояние компонентов вне объявленного адаптера контекста')
            try:
                result = validate_path(c,typ,path_tail)
            except InputError as exc:
                raise DeclarationMismatch(str(exc)) from exc
            self.symbols.append({'path':path,'type':result})
            return root + ('.'+'.'.join(path_tail) if path_tail else ''), result
        if n.kind == 'group':
            expr,typ = self.visit(n.children[0],source)
            return '('+expr+')',typ
        if n.kind == 'number':
            return source[n.start:n.end], 'Число'
        if n.kind == 'string' and not n.children:
            return source[n.start:n.end], 'Строка'
        if n.kind == 'conditional':
            (cond,t),(yes,y),(no,z) = [self.visit(v,source) for v in n.children]
            if t != 'Булево' or c.canonical_type(y) != c.canonical_type(z):
                raise DeclarationMismatch('Несовместимые типы условного выражения')
            return '('+cond+' ? '+yes+' : '+no+')',y
        if n.kind == 'unary' and n.value in {'не','-','+'}:
            expr,typ = self.visit(n.children[0],source)
            if typ != ('Булево' if n.value == 'не' else 'Число'):
                raise DeclarationMismatch('Несовместимый тип унарного оператора')
            return '('+n.value+' '+expr+')',typ
        if n.kind == 'binary' and n.value in {'==','!=','>','<','>=','<=','и','или','+','-','*','/'}:
            (a,t),(b,u) = [self.visit(v,source) for v in n.children]
            if c.canonical_type(t) != c.canonical_type(u):
                raise DeclarationMismatch('Несовместимые типы оператора ' + n.value)
            if (n.value in {'и','или'} and t != 'Булево' or n.value in {'-','*','/'} and t != 'Число'
                    or n.value == '+' and t not in {'Число','Строка'}
                    or n.value in {'>','<','>=','<='} and t not in {'Число','Строка','Дата','ДатаВремя','Время'}):
                raise DeclarationMismatch('Недопустимый тип оператора')
            return '('+a+' '+n.value+' '+b+')', 'Булево' if n.value in {'==','!=','>','<','>=','<=','и','или'} else t
        raise UnsupportedSyntaxError('YAML AST вне контракта: ' + n.kind)


@dataclass
class BindingPlan:
    root: object
    model: dict
    check: dict
    element: dict
    contracts: object
    form: dict
    binding: dict
    steps: list
    base: object = None
    executor_locale: str = 'en-US'

    def to_dict(self):
        result = self.base.to_dict() if self.base else {'schemaVersion':1,'entry':None,'symbols':[],
            'executorProfile':self.model['compatibilityVersion'],'executorLocale':self.executor_locale}
        result['declarativeBindings'] = [self.binding]
        result['bindingSteps'] = self.steps
        result['formContext'] = self.form
        return result

    def to_json(self):
        return json.dumps(self.to_dict(),ensure_ascii=False,indent=2)+'\n'


def plan_binding(root, model, check):
    from .execution_plan import plan_execution
    from .model import select_check_project
    root,model = select_check_project(root,model,check)
    e = form_element(model,check['formRequirement']['form'])
    validate_declaration(e)
    req = check['formRequirement']['requirement']; c = resolver_for(model,e)
    try:
        c.require(req['outputType'])
        selector_type = req.get('selector',{}).get('dataType')
        if selector_type and req['property'] == 'Значение' and c.canonical_type(selector_type) != c.canonical_type(req['outputType']):
            raise InvalidTestError('Противоречивые типы данных и результата в описании')
    except InputError as exc:
        raise InvalidTestError('Некорректный outputType преподавателя: '+str(exc)) from exc
    path,item = unique_component(e,req.get('selector',{}),c)
    expression_path = path + tuple(req['property'].split('.'))
    text = get_path(e['properties'],expression_path)
    resolved_steps = deepcopy(check.get('steps',[{'snapshot':True}]))
    for step in resolved_steps:
        if 'call' in step and 'selector' in step['call']:
            call = step['call']
            handler_path,handler_item = unique_component(e,call['selector'],c)
            bound = handler_binding(root,model,e,handler_path,handler_item,c)
            step['call'] = {'method':bound['method'],'args':call['args']}
    calls = [s['call'] for s in resolved_steps if 'call' in s]
    base = None
    if calls:
        plain = {k:deepcopy(v) for k,v in check.items() if k not in {'expected','formRequirement','steps'}}
        plain['target'] = {'module':e['name'],'namespace':e['namespace'],'method':calls[0]['method']}
        plain['sequence'] = calls
        plain['args'] = calls[0]['args']
        plain['_declarativeBindings'] = True
        # Planning closures uses original methods; declaration evaluation is opt-in.
        base = plan_execution(root,model,plain)
        c,form = base.contracts,base.form
    else:
        typ = e['properties'].get('Наследует',{}).get('Тип','')
        match = re.fullmatch(r'(ФормаОбъекта|ФормаЗаписиНабораКонстант)<(.+)>',typ)
        if typ != 'Форма' and not match:
            raise DeclarationMismatch('Неподдержанный базовый тип формы')
        fields = deepcopy(e['properties'].get('Свойства',[]))
        alias = 'ТестПривязки'+sha256(str((e['namespace'],e['name'])).encode()).hexdigest()[:16]
        form = {'identity':{k:e[k] for k in ('name','namespace','sourceFile')},'canonical':alias+'.Экземпляр','objectType':None}
        if match:
            field_name = 'Объект' if match[1] == 'ФормаОбъекта' else 'Запись'
            if field_name == 'Запись':
                owners = c.resolve(match[2].removesuffix('.Запись'))
                if len(owners) != 1 or owners[0]['elementType'] != 'НаборКонстант':
                    raise DeclarationMismatch('Неоднозначный owner Запись')
                record = alias+'.Запись'
                add_structure(c,record,owners[0]['properties'].get('Константы',[]))
                fields.insert(0,{'Имя':field_name,'Тип':record})
                form['recordOwner'] = owners[0]['sourceFile']
            else:
                fields.insert(0,{'Имя':field_name,'Тип':match[2]})
                form['objectType'] = match[2]
        if len({f['Имя'] for f in fields}) != len(fields):
            raise DeclarationMismatch('Повторное или системное свойство формы')
        add_structure(c,form['canonical'],fields)
    if 'expected' in check:
        expected = check['expected']
        count = sum('snapshot' in s for s in check.get('steps',[{'snapshot':True}]))
        if not isinstance(expected,dict) or set(expected) != {'actions'} or not isinstance(expected['actions'],list) or len(expected['actions']) != count:
            raise InvalidTestError('expected требует actions по числу снимков')
    row = table_context(e,path,c)
    row_type = row[2] if row and 'Колонки' in path[len(row[0]):] else None
    if row_type:
        c.require(row_type)
        column_parts = split_parameters(item.get('Тип','')[len('СтандартнаяКолонкаТаблицы<'):-1])
        if len(column_parts) != 2 or c.canonical_type(column_parts[0].strip()) != c.canonical_type(row_type):
            raise DeclarationMismatch('Тип RowData не совпадает с ближайшей таблицей')
    compiler = ExpressionCompiler(c,form['canonical'],c.canonical_type(row_type) if row_type else None)
    if isinstance(text,str) and text.startswith('='):
        expression,actual_type = compiler.compile(text)
    else:
        actual_type = req['outputType']; c.require(actual_type)
        try:
            expression = c.literal(text,actual_type)
        except InputError as exc:
            raise DeclarationMismatch(str(exc)) from exc
    try:
        c.require(req['outputType'])
    except InputError as exc:
        raise InvalidTestError('Некорректный outputType преподавателя: '+str(exc)) from exc
    expected_type = c.canonical_type(req['outputType'])
    if c.canonical_type(actual_type) != expected_type:
        raise DeclarationMismatch('Тип выражения '+actual_type+' не соответствует '+req['outputType'])
    declared_type = component_value_type(item) if req['property'] == 'Значение' else 'Булево' if req['property'] in {'Видимость','Доступность','ТолькоЧтение'} else 'Строка' if req['property'] in {'Заголовок','Представление'} else None
    if declared_type and c.canonical_type(declared_type) != expected_type:
        raise DeclarationMismatch('Тип компонента не соответствует типу привязки')
    c.literal(check['context'],form['canonical'])
    binding = {'sourceFile':e['sourceFile'],'yamlPath':list(expression_path),'expression':text,
               'compiled':expression,'inputType':form['canonical'],'outputType':expected_type,'symbols':compiler.symbols}
    if isinstance(text,str) and text.startswith('='):
        from dataclasses import asdict
        binding['ast'] = asdict(parse_expression(text[1:]))
        binding['coordinateSpace'] = 'expression-text-without-leading-equals'
    if row_type:
        source_text = get_path(row[1],('Источник','Данные'))
        source_compiler = ExpressionCompiler(c,form['canonical'])
        source,source_type = source_compiler.compile(source_text)
        if c.canonical_type(source_type) != 'Массив<'+c.canonical_type(row_type)+'>':
            raise DeclarationMismatch('Несовместимый тип источника таблицы')
        binding.update(rowType=c.canonical_type(row_type),tablePath=list(row[0]),sourceExpression=source_text,
                       sourceCompiled=source,sourceSymbols=source_compiler.symbols)
    steps = []
    for step in resolved_steps:
        if 'set' in step:
            value = step['set']; p = value['path']
            if not isinstance(p,str) or not p:
                raise InvalidTestError('set.path требует путь объявленных полей')
            expression,typ = fixture_path(c,form['canonical'],p)
            steps.append({'set':{'path':p,'expression':expression,'type':typ,'literal':c.literal(value['value'],typ)}})
        else:
            steps.append(deepcopy(step))
    # Read-only constants cannot be changed through fixture steps.
    if form.get('recordOwner') and any(s.get('set',{}).get('path','').startswith('Запись') for s in steps):
        raise InvalidTestError('Запись констант доступна только для чтения')
    return BindingPlan(root,model,check,e,c,form,binding,steps,base)


def opening_observation(plan, form, specs, variable):
    """Compile explicitly requested bindings in the actual opened form session."""
    c = plan.contracts
    element = form_element(plan.model, form['identity'])
    code, values = '', []
    evidence = []
    for index,spec in enumerate(specs):
        path,item = unique_component(element,spec.get('selector',{}),c)
        expression_path = path+tuple(spec['property'].split('.'))
        text = get_path(element['properties'],expression_path)
        table = table_context(element,path,c)
        row = table[2] if table and 'Колонки' in path[len(table[0]):] else None
        c.namespace = element['namespace']
        compiler = ExpressionCompiler(c,form['canonical'],c.canonical_type(row) if row else None,variable)
        value,typ = compiler.compile(text)
        if c.canonical_type(typ) != c.canonical_type(spec['outputType']):
            raise DeclarationMismatch('Тип привязки открытой формы не соответствует требованию')
        if row:
            source,_ = ExpressionCompiler(c,form['canonical'],variable=variable).compile(table[1]['Источник']['Данные'])
            array = 'ТестКолонка'+str(index)
            code += '    знч '+array+' = новый Массив<'+c.sbsl_type(typ)+'>()\n'
            code += '    для RowData из '+source+'\n        '+array+'.Добавить('+value+')\n    ;\n'
            value = array
        values.append(c.literal(spec['id'],'Строка')+': '+value)
        evidence.append({'id':spec['id'],'sourceFile':element['sourceFile'],'yamlPath':list(expression_path),
                         'expression':text,'outputType':typ,'symbols':compiler.symbols})
    form['declarativeBindings'] = evidence
    return code, '{'+', '.join(values)+'}'


def render_binding(plan, sandbox):
    from .runtime import sbsl_literal
    c,form,b = plan.contracts,plan.form,plan.binding
    imports = ''
    if plan.base:
        from .renderer import render_plan
        script = render_plan(plan.base,sandbox)
        # Replace only the trusted generated driver; original methods and types
        # have already been rendered by the shared source renderer.
        imports = script.read_text(encoding='utf-8').split('\nметод Скрипт(',1)[0]
    else:
        imports = c.write(sandbox)+'\n'
    setup = '    знч Контекст = '+c.literal(plan.check['context'],form['canonical'])+'\n'
    if plan.base and form.get('objectType'):
        setup += '    Контекст.Объект.ТестСостояниеНовизны = '+sbsl_literal(plan.check['lifecycle']['isNew'],'Булево')+'\n'
    if plan.base:
        session_setup,_ = plan.base.rendered_session
        setup = session_setup+setup
    setup += '    знч Снимки = новый Массив<Строка>()\n'
    for index,step in enumerate(plan.steps):
        if 'set' in step:
            value = step['set'];setup += '    '+value['expression']+' = '+value['literal']+'\n'
        elif 'call' in step:
            call = step['call'];symbol = next(s for s in plan.base.symbols if s.owner['sourceFile'] == plan.base.module['sourceFile'] and s.identity.declaration == call['method'])
            from .form_context import form_argument
            args = [form_argument(plan.base,v,t) or c.literal(v,t) for v,t in zip(call['args'],symbol.parameter_types)]
            setup += '    Контекст.'+call['method']+'('+', '.join(args)+')\n'
        else:
            value = observation_value(c,b['compiled'],b['outputType'])
            if b.get('rowType'):
                array = 'Значения'+str(index)
                setup += '    знч '+array+' = новый Массив<'+c.sbsl_type(b['outputType'])+'>()\n'
                setup += '    для RowData из '+b['sourceCompiled']+'\n        '+array+'.Добавить('+value+')\n    ;\n'
                value = array
            setup += '    Снимки.Добавить(СериализацияJson.ЗаписатьОбъект('+value+'))\n'
    identities = {'Scripts::'+typ:'::'.join(filter(None,(c.canonical_elements[typ.split('.')[0]]['namespace'],c.canonical_elements[typ.split('.')[0]]['name'])))+'.'+typ.partition('.')[2]
                  for typ in c.fields if '.' in typ and typ.split('.')[0] in c.canonical_elements}
    setup += '    Консоль.Записать(СериализацияJson.ЗаписатьОбъект({"actual": {"actions": Снимки}, "_snapshotMode": "sequence", "_typeIdentities": '+c.literal(identities,'Соответствие<Строка, Строка>')+'}))\n'
    script = sandbox/'test.sbsl'
    script.write_text(imports+'\nметод Скрипт()\n'+setup+';\n',encoding='utf-8')
    return script


def observation_value(c, expression, typ):
    """Preserve union ownership even when Script serializes equal-shaped refs."""
    variants = union_members(typ)
    if not variants:
        return expression
    nullable = variants[-1] == '?'
    variants = [v for v in variants if v != '?']
    from .resolution import qualified
    result = 'Неопределено'
    for variant in reversed(variants):
        owner = c.canonical_elements.get(variant.partition('.')[0])
        if not owner:
            raise UnsupportedSyntaxError('Наблюдение union требует объявленные проектные варианты')
        identity = qualified(owner)+'.'+variant.partition('.')[2]
        tagged = '{"type": '+c.literal(identity,'Строка')+', "value": ('+expression+' как '+c.sbsl_type(variant)+')}'
        result = '('+expression+' это '+c.sbsl_type(variant)+' ? '+tagged+' : '+result+')'
    return '('+expression+' == Неопределено ? Неопределено : '+result+')' if nullable else result


def preflight(root,model,check):
    """Return structural facts or a declaration failure; never compare answers."""
    if check['formRequirement']['requirement']['kind'] == 'structure':
        return structural_facts(root,model,check)
    try:
        plan_binding(root,model,check)
    except DeclarationMismatch as exc:
        return {'status':'EXECUTED','actual':{'declarationViolation':str(exc)}}
    except InvalidTestError as exc:
        return {'status':'ERROR','reasonCode':'invalid_test','message':str(exc)}
    except UnsupportedSyntaxError as exc:
        return {'status':'UNSUPPORTED','reasonCode':'unsupported_syntax','message':str(exc)}
    except InputError as exc:
        return {'status':'EXECUTED','actual':{'declarationViolation':str(exc)}}
    return None
