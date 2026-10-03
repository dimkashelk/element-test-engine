"""Lossless bounded YAML structure contracts, lowered to one nominal Script type."""
import re
from .indexer import IDENT
from .resolution import qualified
from .yaml_io import UnsupportedSyntaxError, InputError

SCALARS = {'Строка', 'Число', 'Булево', 'Дата', 'Ууид'}


def generate_structure(c, name, element):
    if name in c.active:
        raise UnsupportedSyntaxError('Рекурсивная YAML-структура: ' + qualified(element))
    props = element['properties']
    allowed = {'ВидЭлемента', 'Ид', 'Имя', 'ОбластьВидимости', 'Окружение', 'Импорт', 'Поля'}
    extra = set(props) - allowed
    if extra:
        raise UnsupportedSyntaxError('Неподдержанное свойство структуры: ' + ', '.join(sorted(extra)))
    if props.get('Окружение', 'КлиентИСервер') not in {'КлиентИСервер', 'Сервер'}:
        raise UnsupportedSyntaxError('Клиентская структура недоступна standalone: ' + qualified(element))
    modules = [m for m in c.model.get('modules', [])
               if m['namespace'] == element['namespace'] and m['name'] == element['name']]
    if modules:
        raise UnsupportedSyntaxError('Пользовательский конструктор/методы YAML-структуры требуют отдельный контракт: ' + qualified(element))
    c.active.add(name)
    previous = c.namespace, c.imports, c.current_source
    c.namespace, c.imports = element['namespace'], props.get('Импорт', [])
    c.current_source = element['sourceFile']
    fields, names, lines = [], set(), ['@Глобально', 'структура Значение']
    try:
        for field in props.get('Поля', []):
            extra = set(field) - {'Имя', 'Тип', 'Обязательное', 'ТолькоЧтение', 'ЗначениеПоУмолчанию'}
            if extra:
                raise UnsupportedSyntaxError('Неподдержанное свойство поля структуры: ' + ', '.join(sorted(extra)))
            label, typ = field.get('Имя'), field.get('Тип')
            if not isinstance(label, str) or not re.fullmatch(IDENT, label) or label in names or not typ:
                raise UnsupportedSyntaxError('Некорректное/повторное поле структуры: ' + str(label))
            names.add(label)
            for modifier in ('Обязательное', 'ТолькоЧтение'):
                if modifier in field and type(field[modifier]) is not bool:
                    raise UnsupportedSyntaxError('Модификатор структуры должен быть Булево: ' + modifier)
            typ = c.canonical_type(typ)
            base = typ.rstrip('?')
            enum = c.canonical_elements.get(base)
            if base not in SCALARS and not base.endswith('.Ссылка') and not (enum and enum['elementType'] == 'Перечисление'):
                raise UnsupportedSyntaxError('Тип поля YAML-структуры вне скалярного контракта: ' + typ)
            c.require(typ)
            default = ''
            if 'ЗначениеПоУмолчанию' in field:
                if base not in SCALARS:
                    raise UnsupportedSyntaxError('Явное умолчание структуры требует скалярный тип: ' + typ)
                try:
                    default = ' = ' + c.literal(field['ЗначениеПоУмолчанию'], typ)
                except InputError as exc:
                    raise UnsupportedSyntaxError('Некорректное умолчание структуры: ' + label + ': ' + str(exc)) from exc
            required = field.get('Обязательное', False)
            fields.append({**field, 'Тип': typ, 'constructorRequired': required,
                           'defaultKind': 'explicit' if default else 'implicit' if not required else 'required'})
            lines.append('    ' + ('обз ' if field.get('Обязательное') else '') +
                         ('знч' if field.get('ТолькоЧтение') else 'пер') + ' ' + label + ': ' + c.sbsl_type(typ) + default)
        c.fields[name] = fields
        c.definitions[name] = '\n'.join(lines + [';', ''])
    finally:
        c.namespace, c.imports, c.current_source = previous
        c.active.remove(name)
