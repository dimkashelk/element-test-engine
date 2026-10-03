"""Explicit declaration-derived form effects, executed and snapshotted in Script."""
from hashlib import sha256
from types import SimpleNamespace
import re
from .yaml_io import InvalidTestError, UnsupportedSyntaxError
from .indexer import parse_module, method_local_bindings, method_binding_visible
from .generated_types import ProjectTypes
from .resolution import qualified


def contract(check, model, module):
    if 'formEffects' not in check:
        return None
    from .form_context import form_declaration, describe_form
    value = check['formEffects']
    if not form_declaration(model, module) or 'context' not in check:
        raise InvalidTestError('formEffects требует context формы')
    if form_declaration(model,module) and not describe_form(form_declaration(model,module))['objectType']:
        raise InvalidTestError('formEffects требует ФормаОбъекта<T.Объект>')
    if not isinstance(value, dict) or not value or set(value) - {'write', 'open', 'lifecycle'}:
        raise InvalidTestError('formEffects принимает write, open, lifecycle')
    if 'write' in value and not isinstance(value['write'], bool):
        raise InvalidTestError('formEffects.write должен быть Булево')
    owners = value.get('open', [])
    if not isinstance(owners, list) or any(not isinstance(o, str) for o in owners) or len(set(owners)) != len(owners):
        raise InvalidTestError('formEffects.open требует уникальные полные owners')
    forms = {qualified(e): e for e in model['elements'] if e['elementType'] == 'КомпонентИнтерфейса'}
    for owner in owners:
        if owner not in forms:
            raise InvalidTestError('Неизвестный полный owner formEffects.open: ' + owner)
    lifecycle = value.get('lifecycle', {})
    if not isinstance(lifecycle, dict) or set(lifecycle) - set(owners):
        raise InvalidTestError('formEffects.lifecycle требует разрешённые open owners')
    for owner, cfg in lifecycle.items():
        if (not isinstance(cfg, dict) or set(cfg) - {'method', 'isNew', 'observe', 'bindings'}
                or not {'method', 'isNew', 'observe'} <= set(cfg)
                or not isinstance(cfg['method'], str) or not isinstance(cfg['isNew'], bool)
                or not isinstance(cfg['observe'], list) or not cfg['observe']
                or any(not isinstance(p,str) for p in cfg['observe'])):
            raise InvalidTestError('lifecycle открытия требует method, isNew, observe')
        if 'bindings' in cfg:
            from .form_requirements import validate_selector, validate_type
            bindings = cfg['bindings']
            if not isinstance(bindings,list) or not bindings:
                raise InvalidTestError('bindings открытия требует непустой список')
            ids = set()
            for binding in bindings:
                if (not isinstance(binding,dict) or set(binding) != {'id','selector','property','outputType'}
                        or not isinstance(binding['id'],str) or binding['id'] in ids
                        or not isinstance(binding['property'],str)):
                    raise InvalidTestError('Некорректный контракт binding открытия')
                ids.add(binding['id'])
                validate_selector(binding['selector'])
                try:
                    validate_type(binding['outputType'])
                except ValueError as exc:
                    raise InvalidTestError(str(exc)) from exc
    if 'storage' not in check or check['storage'].get('idType') != 'Ууид':
        raise InvalidTestError('formEffects требует storage с idType: Ууид')
    if 'mocks' in check:
        raise InvalidTestError('formEffects не принимает mocks')
    return value


def lifecycle_roots(root, model, cfg):
    """Additional source roots explicitly requested by the teacher, never implicit events."""
    roots = []
    for owner, spec in (cfg or {}).get('lifecycle', {}).items():
        modules = [m for m in model['modules'] if qualified(m) == owner]
        if len(modules) != 1:
            raise UnsupportedSyntaxError('Модуль lifecycle отсутствует или неоднозначен: ' + owner)
        source = (root/modules[0]['sourceFile']).read_text(encoding='utf-8-sig')
        methods = [n for n in parse_module(source)[0] if n.name == spec['method']]
        if len(methods) != 1 or methods[0].parameters(source) or methods[0].return_span:
            raise UnsupportedSyntaxError('lifecycle открытия требует метод без параметров/результата: ' + owner)
        roots.append((modules[0],spec['method']))
    return roots


def bind_effect(plan, symbol, node, call, path, bindings):
    if call.name not in {'Записать','Открыть'}:
        return None
    cfg = plan.check.get('formEffects') or {}
    from .indexer import method_local_callable_bindings, method_callable_binding_visible
    if call.receiver is None and method_callable_binding_visible(method_local_callable_bindings(symbol.source,node),call.name,call.start):
        return None
    from .execution_plan import CapabilityBinding
    from .form_context import form_declaration, describe_form
    from .resolution import resolve_call_modules
    shadow = call.receiver and method_binding_visible(bindings, call.receiver.split('.')[0], call.receiver_start or call.start)
    own = any(s.identity.declaration == call.name and s.owner['sourceFile'] == symbol.owner['sourceFile'] for s in plan.symbols)
    if (call.receiver is None or call.receiver == 'этот') and own:
        return None
    ast = next(n for n in node.expression_tree.walk() if n.kind == 'call' and n.start == (call.receiver_start if call.receiver_start is not None else call.start))
    def fail(reason):
        raise UnsupportedSyntaxError(f'{reason}: {symbol.identity.source_file}:{symbol.start+call.start}-{symbol.start+call.end}')
    if call.name == 'Записать' and (call.receiver is None or call.receiver == 'этот'):
        if symbol.owner['sourceFile'] != plan.module['sourceFile']:
            fail('Запись из lifecycle целевой формы вне контракта')
        if not cfg.get('write'):
            fail('Эффект формы Записать вне контракта №34')
        if not plan.form['objectType'] or len(ast.children) != 1:
            fail('Записать формы требует объект и ноль аргументов')
        plan.source_transforms.setdefault((symbol.owner['sourceFile'],node.name), []).append((call.start,ast.end,'ТестЗаписатьФорму()'))
        return CapabilityBinding('form-write','Записать',plan.form['objectType'],'storage-session',
            'Explicit synchronous write of the current form object',symbol.identity.source_file,symbol.start+call.start,symbol.start+call.end)
    if call.name == 'Открыть' and call.receiver and not shadow:
        destinations = resolve_call_modules(plan.model['modules'],call.receiver,symbol.owner['namespace'],symbol.owner.get('imports',[]),plan.model.get('properties'))
        if any(any(m['name']=='Открыть' for m in d['methods']) for d in destinations):
            return None
        resolver = ProjectTypes(plan.model,symbol.owner['namespace'],symbol.owner.get('imports',[]))
        candidates = resolver.resolve(call.receiver)
        if not candidates:
            return None
        if len(candidates) != 1:
            fail('Неоднозначный owner открытия')
        element = candidates[0]
        if element['elementType'] != 'КомпонентИнтерфейса':
            return None
        owner = qualified(element)
        if owner not in cfg.get('open',[]):
            fail('Эффект формы Открыть вне контракта №34')
        form = describe_form(element)
        fields = {f['Имя']:f['Тип'] for f in form['fields']}
        arguments = []
        for arg in ast.children[1:]:
            if arg.kind != 'assignment' or arg.value != '=' or arg.children[0].kind != 'name':
                fail('Открыть принимает только именованные свойства YAML')
            name = arg.children[0].value
            if name not in fields or any(a['name']==name for a in arguments):
                fail('Неизвестное или повторное свойство Открыть: '+name)
            value = arg.children[1]
            arguments.append({'name':name,'type':fields[name],'expression':symbol.source[value.start:value.end],
                              'owner':owner,'start':symbol.start+value.start,'end':symbol.start+value.end,
                              '_node':value})
        alias = 'ТестОткрытие'+sha256(str((symbol.identity,call.start)).encode()).hexdigest()[:16]
        plan.openings.append({'owner':owner,'alias':alias,'arguments':arguments,'form':form,
                              'sourceFile':symbol.identity.source_file,'start':symbol.start+call.start,'end':symbol.start+ast.end,
                              '_symbol':symbol,'_method':node})
        plan.source_transforms.setdefault((symbol.owner['sourceFile'],node.name), []).append((call.receiver_start,call.receiver_end,alias))
        plan.module_type_dependencies.setdefault(symbol.owner['sourceFile'],[]).append(alias+'.Вызов')
        return CapabilityBinding('form-open','Открыть',owner,'typed-open-request',
            'Detached request snapshot; lifecycle only by explicit contract',symbol.identity.source_file,symbol.start+call.start,symbol.start+call.end)
    if path is not None and call.name in {'Записать','Открыть'}:
        fail('Операция объекта формы требует неявный Записать формы')
    return None


def generate_effects(plan,c):
    from .form_context import generate_form_types,validate_path,form_observation
    if 'formEffects' not in plan.check:
        return
    if plan.form['objectType']:
        obj=plan.form['objectCanonical']
        owners=c.resolve(obj.removesuffix('.Объект'))
        if len(owners)!=1:
            raise UnsupportedSyntaxError('Неоднозначный объект записи формы')
        element=owners[0]
        if element not in plan.storage_elements:plan.storage_elements.append(element)
        value=plan.check['context'].get('Объект',{})
        ref=value.get('Ссылка')
        if not plan.check['lifecycle']['isNew'] and ref is None:
            raise InvalidTestError('Существующий объект формы требует Ссылка и storage.initial')
        initial=plan.check['storage'].get('initial',[])
        same=[i for i in initial if i.get('type','').removesuffix('.Объект')==qualified(element)
              and i.get('value',{}).get('Ссылка')==ref] if ref else []
        if plan.check['lifecycle']['isNew'] and same:
            raise InvalidTestError('Новый объект формы уже есть в storage.initial')
        if not plan.check['lifecycle']['isNew'] and (len(same)!=1 or same[0]['value']!=value):
            raise InvalidTestError('Существующий context.Объект должен совпадать с storage.initial')
        if plan.check['formEffects'].get('write'):
            object_modules=[m for m in plan.model['modules'] if m['namespace']==element['namespace'] and m['name']==element['name']+'.Объект']
            if any(any(n['name']=='Записать' for n in m['methods']) for m in object_modules):
                raise UnsupportedSyntaxError('Проектный Записать объекта требует исходную декларацию; системная подмена запрещена: '+element['sourceFile'])
            c.attach_method(plan.form['canonical'],'''метод ТестЗаписатьФорму()
    знч Собственная = не ТестСессия.ЕстьАктивная()
    если Собственная
        ТестСессия.НачатьИсходную()
    ;
    попытка
        ТестСессия.Событие("form:write")
        Консоль.Записать("ELEMENT_TRACE " + СериализацияJson.ЗаписатьОбъект({"symbol": "form:write", "event": "enter", "value": Неопределено}))
        Объект.Записать()
        если Собственная
            ТестСессия.ЗавершитьИсходную()
        ;
        Объект.ТестСостояниеНовизны = Ложь
    поймать Ошибка: Исключение
        если Собственная
            ТестСессия.ОткатитьИсходную()
        ;
        выбросить Ошибка
    ;
;
''',['ТестСессия.Записи'])
    for owner,form in plan.open_forms.items():
        generate_form_types(plan,c,form=form)
    sources={m['sourceFile']:(plan.root/m['sourceFile']).read_text(encoding='utf-8-sig') for m in plan.model['modules']}
    declarations={p:parse_module(s)[0] for p,s in sources.items()}
    from .call_types import infer_receiver_type
    from .indexer import split_parameters
    for opening in plan.openings:
        symbol,node=opening['_symbol'],opening['_method']
        c.namespace,c.imports=symbol.owner['namespace'],symbol.owner.get('imports',[])
        args=opening['arguments']
        for a in args:
            value=a['_node']
            fake=SimpleNamespace(receiver_start=value.start,receiver_end=value.end)
            typ=('Булево' if value.kind=='name' and value.value in {'Истина','Ложь'} else
                 'Число' if value.kind=='number' else 'Строка' if value.kind=='string' else
                 infer_receiver_type(symbol.source,node,fake,symbol.owner,plan.model,declarations,sources,method_local_bindings(symbol.source,node)))
            if value.kind=='call' and typ is None:
                receiver=value.children[0]
                if receiver.kind=='member' and receiver.children[1].value=='Получить':
                    r=receiver.children[0]
                    rt=infer_receiver_type(symbol.source,node,SimpleNamespace(receiver_start=r.start,receiver_end=r.end),symbol.owner,plan.model,declarations,sources,method_local_bindings(symbol.source,node))
                    if rt and rt.startswith('Соответствие<'):typ=split_parameters(rt[len('Соответствие<'):-1])[1].strip()
            target=opening['form']
            c.namespace,c.imports=target['identity']['namespace'],[]
            c.require(a['type']);expected=c.sbsl_type(a['type'])
            c.namespace,c.imports=symbol.owner['namespace'],symbol.owner.get('imports',[])
            actual=c.sbsl_type(typ) if typ else None
            if value.kind=='name' and value.value=='Неопределено' and expected.endswith('?'):actual=expected
            if actual != expected and not (expected.endswith('?') and actual == expected[:-1]):
                raise UnsupportedSyntaxError(f'Несовместимый или недоказанный тип Открыть.{a["name"]}: {actual} != {expected}: {opening["sourceFile"]}:{a["start"]}-{a["end"]}')
            a['canonical']=expected
            a['sourceType']=typ
            a.pop('_node')
        props='{'+', '.join(c.literal(a['name'],'Строка')+': '+a['name'] for a in args)+'}' if args else 'новый Соответствие<Строка, Объект?>()'
        lifecycle=plan.check['formEffects'].get('lifecycle',{}).get(opening['owner'])
        body='    ТестЗапросыФорм.Добавить({"owner": '+c.literal(opening['owner'],'Строка')+', "properties": '+props+'})\n'
        deps=[a['canonical'] for a in args]+['ТестЗапросыФорм.Журнал']
        if lifecycle:
            form=plan.open_forms[opening['owner']]
            initial_name='ТестНачальный'+opening['alias'][11:]
            target_name='ТестЦелевой'+opening['alias'][11:]
            while initial_name in {a['name'] for a in args}:initial_name+='_'
            while target_name in {a['name'] for a in args}:target_name+='_'
            body+='    знч '+initial_name+' = новый '+form['canonical']+'('+', '.join(a['name']+' = '+a['name'] for a in args)+')\n'
            body+='    знч '+target_name+' = СериализацияJson.ПрочитатьОбъект<'+form['canonical']+'>(СериализацияJson.ЗаписатьОбъект('+initial_name+'), '+initial_name+'.ПолучитьТип())\n'
            if form['objectType']:
                body+='    '+target_name+'.Объект.ТестСостояниеНовизны = '+c.literal(lifecycle['isNew'],'Булево')+'\n'
            body+='    '+target_name+'.'+lifecycle['method']+'()\n'
            observation = form_observation(plan,form=form,paths=lifecycle['observe'],variable=target_name)
            if lifecycle.get('bindings'):
                from .declarative_bindings import opening_observation
                code,bindings = opening_observation(plan,form,lifecycle['bindings'],target_name)
                body += code
                observation = observation[:-1]+', "bindings": '+bindings+'}'
            body+='    ТестЗапросыФорм.Дополнить('+observation+')\n'
            deps.append(form['canonical'])
        c.definitions[opening['alias']]='@Глобально\n@ИменованныеПараметры\nметод Открыть('+', '.join(a['name']+': '+a['canonical'] for a in args)+')\n'+body+';\n'
        c.method_dependencies[opening['alias']]=deps
        opening.pop('_symbol');opening.pop('_method')
    c.definitions['ТестЗапросыФорм']='''конст Путь = '''+c.literal(plan.storage.path+'.openings','Строка')+'''
@Глобально
метод Читать(): Массив<Объект?>
    знч Пустой = новый Массив<Объект?>()
    знч Ф = новый Файл(Путь)
    если не Ф.Существует()
        возврат Пустой
    ;
    исп Поток = Ф.ОткрытьПотокЧтения()
    возврат СериализацияJson.ПрочитатьОбъект<Массив<Объект?>>(Поток, Пустой.ПолучитьТип())
;
@Глобально
метод Добавить(Запрос: Объект?)
    знч Журнал = Читать()
    Журнал.Добавить(СериализацияJson.ПрочитатьОбъект(СериализацияJson.ЗаписатьОбъект(Запрос)))
    исп Поток = новый Файл(Путь).ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Поток, Журнал)
    Консоль.Записать("ELEMENT_TRACE " + СериализацияJson.ЗаписатьОбъект({"symbol": "form:open", "event": "enter", "value": Запрос}))
;
@Глобально
метод Дополнить(Контекст: Объект?)
    знч Журнал = Читать()
    знч Запрос = Журнал[Журнал.Размер() - 1] как Соответствие<Строка, Объект?>
    Запрос.Вставить("context", Контекст)
    исп Поток = новый Файл(Путь).ОткрытьПотокЗаписи()
    СериализацияJson.ЗаписатьОбъект(Поток, Журнал)
;
'''
    c.namespace,c.imports=plan.module['namespace'],plan.module.get('imports',[])
