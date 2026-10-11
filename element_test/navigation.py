"""Declaration-resolved command routes and model openings, without native UI."""
from hashlib import sha256
import re
import yaml

from .capability_map import yaml_nodes
from .indexer import IDENT
from .resolution import qualified, resolve_symbols, visible_from
from .yaml_io import InvalidTestError, UnsupportedSyntaxError


def select_element(model, selector, kinds):
    if (not isinstance(selector, dict) or set(selector) - {'name', 'namespace', 'sourceHash', 'projectSourceHash'}
            or not isinstance(selector.get('name'), str) or not selector['name']
            or 'namespace' in selector and not isinstance(selector['namespace'], str)):
        raise InvalidTestError('Селектор требует name, опциональные namespace/sourceHash/projectSourceHash')
    found = [e for e in model['elements'] if e['name'] == selector['name'] and e['elementType'] in kinds
             and ('namespace' not in selector or e['namespace'] == selector['namespace'])]
    if len(found) != 1:
        raise InvalidTestError('Декларация отсутствует или неоднозначна: ' + selector['name'])
    if selector.get('projectSourceHash', model['sourceHash']) != model['sourceHash']:
        raise InvalidTestError('Устаревший projectSourceHash')
    return found[0]


def evidence(root, model, e, selector=None):
    source = (root / e['sourceFile']).read_text(encoding='utf-8-sig')
    digest = sha256((root / e['sourceFile']).read_bytes()).hexdigest()
    if selector and selector.get('sourceHash', digest) != digest:
        raise InvalidTestError('Устаревший sourceHash декларации')
    positions = {tuple(p): {'range': [n.start_mark.index, n.end_mark.index],
                           'line': n.start_mark.line+1, 'column': n.start_mark.column+1}
                 for p, n in yaml_nodes(yaml.compose(source))}
    return {'owner': qualified(e), 'sourceFile': e['sourceFile'], 'sourceHash': digest,
            'projectSourceHash': model['sourceHash']}, positions


def resolve(model, source, name, kinds):
    if not isinstance(name, str) or not re.fullmatch(IDENT + r'(?:::' + IDENT + r')*', name):
        raise UnsupportedSyntaxError('Ссылка метаданных требует имя/квалифицированное имя')
    found = resolve_symbols(model['elements'], name, source['namespace'], source['properties'].get('Импорт', []), model['properties'])
    found = [e for e in found if e['elementType'] in kinds and visible_from(e, source['namespace'], source.get('_libraryPrefix'))]
    if len(found) != 1:
        raise UnsupportedSyntaxError('Ссылка отсутствует, неоднозначна или недоступна: ' + name)
    return found[0]


def surface(model, source, spelling):
    if not isinstance(spelling, str) or not spelling:
        raise UnsupportedSyntaxError('ТипФормы требует ссылку на поверхность')
    owner, dot, member = spelling.rpartition('.')
    if not dot:
        native = {'ФормаУправленияУдалениемДанных', 'ФормаКонсолиЗапросов'}
        if spelling in native and not any(e['name'] == spelling for e in model['elements']):
            return {'owner':'Стд::'+spelling, 'surface':'native', 'sourceFile':None,
                    'kind':'NativeForm', 'rendering':'external-native-ui'}
        e = resolve(model, source, spelling, {'КомпонентИнтерфейса', 'ПанельОтчетов'})
        inherited = e['properties'].get('Наследует', {}).get('Тип', '')
        if e['elementType'] == 'КомпонентИнтерфейса' and not (inherited.startswith('Форма') or 'КлиентскоеПриложение' in inherited):
            raise UnsupportedSyntaxError('Ссылка навигации требует форму или приложение')
        return {'owner': qualified(e), 'surface': 'declared', 'sourceFile': e['sourceFile'],
                'kind': e['elementType'], 'rendering': 'external-native-ui'}
    e = resolve(model, source, owner, {'Справочник', 'Документ', 'Отчет', 'ПанельОтчетов'})
    allowed = {'Справочник': {'АвтоматическаяФормаСписка', 'АвтоматическаяФормаОбъекта'},
               'Документ': {'АвтоматическаяФормаСписка', 'АвтоматическаяФормаОбъекта'},
               'Отчет': {'АвтоматическаяФорма'}, 'ПанельОтчетов': {'АвтоматическаяФорма'}}
    if member not in allowed[e['elementType']]:
        raise UnsupportedSyntaxError('Стандартная поверхность вне контракта: ' + spelling)
    return {'owner': qualified(e), 'surface': member, 'sourceFile': e['sourceFile'],
            'kind': e['elementType'], 'rendering': 'external-native-ui'}


def gate(value, context):
    if type(value) is bool: return value
    if isinstance(value, str) and value in {'Истина', 'Ложь'}: return value == 'Истина'
    if isinstance(value, str) and re.fullmatch('=' + IDENT, value) and type(context.get(value[1:])) is bool:
        # Context is an explicit typed model input, not a precomputed opening.
        return context[value[1:]]
    raise UnsupportedSyntaxError('Видимость/доступность навигации требует Булево или =booleanContext')


def build_navigation(root, model, e, context, selector=None):
    if not isinstance(context, dict) or any(not re.fullmatch(IDENT, str(k)) or type(v) is not bool for k,v in context.items()):
        raise InvalidTestError('navigationContext требует словарь именованных Булево')
    routes, groups, dependencies = [], [], {}
    def visit(owner, items, path, section, visible=True, enabled=True, stack=()):
        if len(stack) > 40 or len(routes) > 1000:
            raise UnsupportedSyntaxError('Лимит дерева навигации')
        fact, positions = evidence(root, model, owner)
        dependencies[owner['sourceFile']] = fact
        if not isinstance(items, list): raise UnsupportedSyntaxError('Элементы навигации требуют массив')
        for i, item in enumerate(items):
            if len(routes) + len(groups) >= 1000:
                raise UnsupportedSyntaxError('Лимит дерева навигации')
            at = path + (str(i),)
            if isinstance(item, str) and item.startswith('='):
                dest = resolve(model, owner, item[1:], {'ФрагментКомандногоИнтерфейса'})
                if dest['sourceFile'] in stack:
                    raise UnsupportedSyntaxError('Цикл фрагментов командного интерфейса')
                visit(dest, dest['properties'].get('Элементы'), ('Элементы',), section, visible, enabled,
                      stack + (repr(at), dest['sourceFile']))
                continue
            if not isinstance(item, dict): raise UnsupportedSyntaxError('Элемент навигации вне контракта')
            v = visible and gate(item.get('Видимость', True), context)
            a = enabled and gate(item.get('Доступность', True), context)
            # Validate hidden descendants too; short circuit must not mask unsupported declarations.
            gate(item.get('Видимость', True), context); gate(item.get('Доступность', True), context)
            typ = item.get('Тип')
            item_fact = {**fact, 'yamlPath': list(at), 'position': positions.get(at),
                         'section': list(section), 'visible': v, 'available': a,
                         'caption': item.get('Представление', ''), 'name': item.get('Имя', '')}
            if typ == 'ГруппаКомандногоИнтерфейса':
                group_id = 'section-' + sha256(repr((stack, at, section)).encode()).hexdigest()[:24]
                groups.append({**item_fact, 'id': group_id})
                visit(owner, item.get('Элементы'), at + ('Элементы',), section + (group_id,), v, a, stack)
            elif typ == 'НавигационнаяКоманда':
                resolved = surface(model, owner, item.get('ТипФормы'))
                route_id = 'route-' + sha256(repr((stack, at, section, resolved)).encode()).hexdigest()[:24]
                routes.append({**item_fact, 'id': route_id, 'target': resolved})
                if resolved['sourceFile'] is not None:
                    target = next(x for x in model['elements'] if x['sourceFile'] == resolved['sourceFile'])
                    dependencies[target['sourceFile']] = evidence(root, model, target)[0]
            else: raise UnsupportedSyntaxError('Тип команды вне контракта: ' + str(typ))
    fact, positions = evidence(root, model, e, selector)
    if e['elementType'] == 'ФрагментКомандногоИнтерфейса':
        visit(e, e['properties'].get('Элементы'), ('Элементы',), (), stack=(e['sourceFile'],))
    else:
        inherited = e['properties'].get('Наследует', {})
        if inherited.get('Тип') != 'СтандартноеКлиентскоеПриложениеСРазделами':
            raise UnsupportedSyntaxError('Навигация приложения требует стандартное приложение с разделами')
        raw = inherited.get('КомандныйИнтерфейсПанелиНавигации')
        if not isinstance(raw, str) or not raw.startswith('='):
            raise UnsupportedSyntaxError('Навигация приложения требует ссылку на фрагмент')
        visit(e, [raw], ('Наследует', 'КомандныйИнтерфейсПанелиНавигации'), (), stack=(e['sourceFile'],))
        dependencies[e['sourceFile']] = fact
    return {**fact, 'schemaVersion': 1, 'routes': routes, 'sections': groups,
            'dependencies': list(dependencies.values()), 'context': context,
            'support': 'declaration-model', 'execution': 'not-checked',
            'stages': {'validation':'ready','planning':'ready','generation':'ready','execution':'not-checked'},
            'remaining': ['Native surfaces and custom form handlers are not executed by model opening']}


def select_route(routes, selector):
    if not isinstance(selector, dict) or not selector or set(selector) - {'id', 'owner', 'surface', 'caption', 'name'}:
        raise InvalidTestError('open.selector требует id/owner/surface/caption/name')
    found = [r for r in routes if all((r['target'][k] if k in {'owner','surface'} else r[k]) == v for k,v in selector.items())]
    if len(found) != 1: raise InvalidTestError('Маршрут отсутствует или неоднозначен')
    return found[0]
