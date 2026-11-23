"""Author the portable programming criteria from the course fixtures."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

import yaml

from cases import FORMS, ROWS, cases

HERE = Path(__file__).resolve().parent
WELCOME = 'Администрирование::ФормаПриветствие'
PERSON = 'CRM::ФизическиеЛица.Объект'
ORDER = 'Продажи::ЗаказФормаОбъекта'
DEAL = 'CRM::СделкаФормаОбъекта'
SHIPMENT = 'Товары::ОтгрузкаФормаОбъекта'
EXPENSE = 'Финансы::РасходДенежныхСредств'
BUTTON = 'КнопкаРассчитатьПриНажатии'
EDIT = 'РедактированиеДанных'
TITLE = 'ЗаголовокФормы'
EDIT_LABEL = 'ПредставлениеРедактированиеДанных'
EDIT_HANDLER = EDIT_LABEL + 'Обработчик'
SELECTED = [
    (DEAL, 'Сделка.ПервичныйСпрос', 'СуммаОтмеченногоСпросаОбработчик', 'Сумма отмеченного спроса'),
    (DEAL, 'Сделка.Участники', 'ПосчитатьУчастниковОбработчик', 'Посчитать участников'),
    (ORDER, 'Заказ.Товары', 'ИтогиОтмеченныхОбработчик', 'Итоги отмеченных'),
    (ORDER, 'Заказ.Товары', 'ОтгрузитьОбработчик', None),
]


def owner_ref(owner, short=False):
    return {'$owner': owner, **({'short': True} if short else {})}


def name_ref(owner, name, prefix=''):
    return {'$name': [owner, name], **({'prefix': prefix} if prefix else {})}


def query(source, *steps, **identity):
    return {'from': source, **identity, **({'steps': list(steps)} if steps else {})}


def path(*keys):
    return {'path': list(keys)}


def predicate(keys, value, operation='eq'):
    return {**(path(*keys) if keys else {}), operation: value}


def where(*predicates):
    return {'where': list(predicates)}


def element(owner, *steps, fixed=False):
    return query('element', *steps, owner=owner if fixed else owner_ref(owner))


def method(owner, name, *steps, fixed=False):
    return query('method', *steps, owner=owner if fixed else owner_ref(owner),
                 method=name if fixed else name_ref(owner, name))


def expect(spec, *filters, **options):
    return {'query': spec, **({'some': list(filters)} if filters else {}), **options}


def fixed_method_clauses(owner, name, tokens):
    # The method span includes its closing line, with or without its newline.
    ordered = tokens[:-1] if tokens[-1] == '\n' else tokens
    source = method(owner, name, fixed=True)
    positions = [predicate(['tokens', index], token) for index, token in enumerate(ordered)]
    clauses = [expect(source, *positions[:32], count=1)]
    clauses.extend(expect(source, *positions[index:index + 32]) for index in range(32, len(positions), 32))

    # At the one optional position, allow only a newline. Reject unknown tokens
    # with the set check and known non-newline tokens at that position explicitly.
    vocabulary = list(dict.fromkeys([*ordered, '\n']))
    clauses.append(expect(source, predicate(['tokens'], vocabulary, 'setEq'),
                          predicate(['tokens', len(ordered) + 1], False, 'exists')))
    for token in vocabulary:
        if token != '\n':
            suffix = method(owner, name, path('tokens', len(ordered)), where(predicate([], token)), fixed=True)
            clauses.append(expect(suffix, count=0))
    return clauses


def alias_rules():
    rules = []
    for namespace, document, _, _ in FORMS:
        owner = namespace + '::' + document + 'ФормаОбъекта'
        rules.append({'owner': owner, 'unless': element(owner, fixed=True),
                      'select': query('project', path('elements'), {'items': True},
                                      where(predicate(['namespace'], namespace),
                                            predicate(['properties', 'Наследует', 'Тип'], f'ФормаОбъекта<{document}.Объект>')),
                                      path('qualifiedName'))})
        rules.append({'owner': owner, 'name': TITLE,
                      'select': element(owner, path('Наследует', 'Заголовок'), {'stripPrefix': '='})})
        rules.append({'owner': owner, 'name': EDIT_LABEL,
                      'select': element(owner, path('Свойства'), {'items': True},
                                        where(predicate(['ЗначениеПоУмолчанию'], 'Разрешить редактирование реквизитов')),
                                        path('Имя'))})
        rules.append({'owner': owner, 'name': EDIT_HANDLER,
                      'select': element(owner, {'walk': True},
                                        where(predicate(['Представление'], name_ref(owner, EDIT_LABEL, '='))),
                                        path('Обработчик'))})
    signatures = [
        (PERSON, 'СформироватьФИО', ['Строка'] * 3, 'Строка'),
        (WELCOME, 'СформироватьТекстОрганизации', ['Соответствие<Строка, Строка>'], 'Строка'),
        (WELCOME, 'ПолучитьТекстДня', ['Строка', 'Соответствие<Строка, Булево>'], 'Строка'),
        (WELCOME, 'РассчитатьДни', ['Дата', 'Дата', 'Соответствие<Строка, Булево>'], 'Соответствие<Строка, Число>'),
        (ORDER, 'РассчитатьИтоги', ['Массив<Заказ.Товары>'], 'Соответствие<Строка, Число>'),
    ]
    for owner, name, parameters, returned in signatures:
        rules.append({'owner': owner, 'name': name, 'unless': method(owner, name),
                      'select': query('module', path('methods'), {'items': True},
                                      where(predicate(['parameterTypes'], parameters, 'typeEq'),
                                            predicate(['returnType'], returned, 'typeEq')),
                                      path('name'), owner=owner_ref(owner))})
    rules.append({'owner': WELCOME, 'name': BUTTON,
                  'select': element(WELCOME, {'walk': True}, where(predicate(['Тип'], 'Кнопка')),
                                    path('ПриНажатии'), {'distinctStrings': True})})
    for owner, _, name, caption in SELECTED:
        if caption:
            rules.append({'owner': owner, 'name': name,
                          'select': element(owner, {'walk': True}, where(predicate(['Представление'], caption)),
                                            path('Обработчик'), {'distinctStrings': True})})
    return rules


def structural(optional):
    baseline = json.loads((HERE / 'lesson-contract.json').read_text())
    checks = []

    def add(ident, message, *clauses):
        checks.append({'id': ident, 'type': 'assertion', 'points': 1, 'group': 'structural',
                       'failureExplanation': message, 'assertion': {'all': list(clauses)}})

    add('L.project', 'Режим совместимости 9.0', expect(query('project'), predicate(['properties', 'РежимСовместимости'], '9.0', 'typeEq')))
    add('L.diagnostics', 'Нет диагностик метаданных', expect(query('project'), predicate(['diagnostics'], [])))
    for owner, expected in baseline['dataElements'].items():
        for key, value in expected.items():
            if key in {'Импорт', 'Интерфейс', 'СозданиеНаОсновании', 'ОбластьВидимости'}:
                continue
            source = element(owner, path(key), {'withoutIds': True}, fixed=True)
            if isinstance(value, list) and all(isinstance(x, dict) and 'Имя' in x for x in value):
                clauses = [expect(source, predicate([], item, 'contains')) for item in value]
                if not clauses:
                    # An empty baseline places no constraint on the inherited list.
                    clauses = [expect(query('project'), predicate([], True, 'exists'))]
            else:
                clauses = [expect(source, predicate([], value))]
            add(f'L.metadata.{owner}.{key}', f'Сохранить лекционное объявление {owner}.{key}', *clauses)
    for item in baseline['fixedMethods']:
        add(f"L.source.{item['owner']}.{item['method']}", 'Сохранить инструкции и сигнатуру лекционного метода',
            *fixed_method_clauses(item['owner'], item['method'], item['tokens']))

    def event(ident, owner, name, fixed=False, **options):
        add(ident, f'{name} имеет аннотацию @Обработчик',
            expect(method(owner, name, fixed=fixed), predicate(['annotations'], 'Обработчик', 'contains'), **options))

    for ns, name, _, _ in FORMS:
        document = ns + '::' + name
        owner = document + 'ФормаОбъекта'
        controls = (path('Наследует'), {'walk': True})
        add('P.form.' + name, 'Форма редактирует свой документ',
            expect(element(owner), predicate(['Наследует', 'Тип'], f'ФормаОбъекта<{name}.Объект>')))
        add('P.title.binding.' + name, 'Заголовок связан со строковым свойством',
            expect(element(owner), predicate(['Наследует', 'Заголовок'], name_ref(owner, TITLE, '='))))
        add('P.state.' + name, 'Начальное состояние редактирования имеет тип Булево и значение Ложь',
            expect(element(owner, path('Свойства'), {'items': True}), predicate(['Имя'], name_ref(owner, EDIT)),
                   predicate(['Тип'], 'Булево'), predicate(['ЗначениеПоУмолчанию'], False)))
        for field in ('Номер', 'Дата'):
            add(f'P.edit.binding.{name}.{field}', 'Доступность связана с флагом редактирования',
                expect(element(owner, *controls, where(predicate(['Значение'], '=Объект.' + field))),
                       every=[predicate(['Доступность'], name_ref(owner, EDIT, '='))]))
        add('P.edit.command.' + name, 'Команда связана с обработчиком и динамическим представлением',
            expect(element(owner, *controls), predicate(['Обработчик'], name_ref(owner, EDIT_HANDLER)),
                   predicate(['Представление'], name_ref(owner, EDIT_LABEL, '='))))
        add('P.form.registration.' + name, 'Форма зарегистрирована как форма объекта документа',
            expect(element(document, fixed=True), predicate(['Интерфейс', 'Объект', 'Форма'], owner_ref(owner, short=True))))
        if name in {'Сделка', 'Заказ'}:
            add('P04.command.binding.' + name, 'Команда архивирования связана с обработчиком и представлением',
                expect(element(owner, *controls), predicate(['Обработчик'], name_ref(owner, 'АрхивироватьОбработчик')),
                       predicate(['Представление'], name_ref(owner, 'ПредставлениеАрхивирования', '='))))
        event('P.form.event.' + name, owner, 'ПослеСоздания', count=1)
    for owner, table, handler, _ in SELECTED:
        add(f'P08.selected.command.{owner}.{handler}', 'Команда связана с отмеченными строками нужной таблицы',
            expect(element(owner, {'walk': True}, where(predicate(['Тип'], 'Таблица<', 'prefix'),
                                                        predicate(['Тип'], table, 'contains')),
                           path('КомандыНадОтмеченнымиСтроками'), {'walk': True}),
                   predicate(['Обработчик'], name_ref(owner, handler))))
        add(f'P08.signature.{owner}.{handler}', 'Обработчик принимает массив строк своей табличной части',
            expect(method(owner, handler),
                   predicate(['parameterTypes'], [f'КомандаСПараметром<Массив<{table}>>', f'Массив<{table}>'], 'typeEqLocal')))
    add('P06.button.binding', 'Кнопка расчёта связана с обработчиком',
        expect(element(WELCOME, {'walk': True}), predicate(['ПриНажатии'], name_ref(WELCOME, BUTTON))))
    add('P06.button.signature', 'Обработчик кнопки принимает кнопку и событие без результата',
        expect(method(WELCOME, BUTTON), predicate(['parameterTypes'], ['Кнопка', 'СобытиеПриНажатии']),
               predicate(['returnType'], None)))
    event('P05.event', WELCOME, 'ПослеСоздания', count=1)
    event('P01.event', PERSON, 'ПередЗаписью', count=1)
    add('P07.metadata.' + EXPENSE, 'Основание расхода — nullable-ссылка на счёт',
        expect(element(EXPENSE, path('Реквизиты'), {'items': True}, fixed=True),
               predicate(['Имя'], 'Основание'), predicate(['Тип'], 'СчетНаОплату.Ссылка?')))
    for owner, reference in [('Продажи::Заказ', 'Сделка.Ссылка'), (EXPENSE, 'СчетНаОплату.Ссылка')]:
        add('P07.base.' + owner, 'Создание на основании разрешено в метаданных',
            expect(element(owner, fixed=True), predicate(['СозданиеНаОсновании'], reference, 'contains')))
    add('P07.expense.field', 'Основание расхода выведено в форму',
        expect(element(EXPENSE + 'ФормаОбъекта', {'walk': True}), predicate(['Значение'], '=Объект.Основание')))
    for owner, caller, callee in [
        (PERSON, 'ПередЗаписью', 'СформироватьФИО'),
        (WELCOME, BUTTON, 'РассчитатьДни'),
        (WELCOME, 'ПослеСоздания', 'СформироватьТекстОрганизации'),
        (WELCOME, 'ПослеСоздания', 'ПолучитьТекстДня'),
        (ORDER, 'ИтогиОтмеченныхОбработчик', 'РассчитатьИтоги'),
        (ORDER, 'ОтгрузитьОбработчик', 'РассчитатьИтоги'),
    ]:
        add(f'P.reuse.{owner}.{caller}.{callee}', f'Обработчик вызывает выделенный метод {callee}',
            expect(method(owner, caller, path('calls'), {'items': True}), predicate(['name'], name_ref(owner, callee))))
    for owner, name, annotations in [
        ('Товары::Отгрузка', 'СоздатьЧасть', ['ВПроекте', 'ДоступноСКлиента', 'НаСервере']),
        ('Товары::Отгрузка', 'ПеренестиТабличнуюЧасть', ['ВПодсистеме']),
        ('Администрирование::ДанныеКомпании', 'ПолучитьДанные', ['ВПодсистеме', 'ДоступноСКлиента', 'НаСервере']),
        ('Администрирование::РабочиеДни', 'ПолучитьДанные', ['ВПодсистеме', 'ДоступноСКлиента', 'НаСервере']),
        (PERSON, 'СформироватьФИО', ['Локально']),
        (WELCOME, 'СформироватьТекстОрганизации', ['Локально']),
        (WELCOME, 'ПолучитьТекстДня', ['Локально']),
        (WELCOME, 'РассчитатьДни', ['Локально']),
        (ORDER, 'РассчитатьИтоги', ['Локально']),
    ]:
        add(f'P10.annotations.{owner}.{name}', 'Требуемые аннотации — ' + ', '.join(annotations),
            expect(method(owner, name), predicate(['annotations'], annotations, 'setEq')))
    documents = ['Продажи::Заказ', EXPENSE, 'Товары::Отгрузка', 'Финансы::СчетНаОплату', 'Финансы::ПоступлениеДенежныхСредств']
    if optional:
        documents.append('Товары::ПоступлениеТоваров')
    for document in documents:
        owner = document + '.Объект'
        event('P07.event.' + owner, owner, 'ПриСозданииНаОсновании', fixed=True)
    if optional:
        add('X07.base', 'Дополнительный сценарий разрешён в метаданных',
            expect(element(documents[-1], fixed=True), predicate(['СозданиеНаОсновании'], 'Отгрузка.Ссылка', 'contains')))
    for name, present in [('РассчитатьДни', True), (BUTTON, False)]:
        add('P06.loop.' + name, 'Цикл пока находится только в методе расчёта',
            expect(method(WELCOME, name), count=1),
            expect(method(WELCOME, name, path('bodyTokens'), {'items': True}, where(predicate([], 'пока'))),
                   **({'some': [predicate([], 'пока')]} if present else {'count': 0})))
    add('P05.server.boundary', 'Форма получает данные через серверный метод',
        expect(query('module', owner=owner_ref(WELCOME)), count=1),
        expect(query('module', path('calls'), {'items': True},
                     where(predicate(['receiver'], 'ДанныеКомпании', 'suffix'), predicate(['name'], 'Получить')),
                     owner=owner_ref(WELCOME)), count=0))
    return checks


def binding_cases():
    for partial in (True, False):
        yield {'id': 'P09.binding.' + str(partial).lower(), 'type': 'runtime', 'group': 'P09', 'points': 1,
               'formRequirement': {'form': {'namespace': 'Товары', 'name': 'ОтгрузкаФормаОбъекта'},
                                   'clause': {'id': 'P09', 'text': 'Таблица использует строки из ожидаемого источника'},
                                   'requirement': {'id': 'source', 'clause': 'P09', 'kind': 'binding',
                                                   'selector': {'declaration': {'Тип': 'Таблица<ИсточникДанныхМассив<Отгрузка.Товары>>'}},
                                                   'property': 'Источник.Данные', 'outputType': 'Массив<Отгрузка.Товары>'}},
               'context': {'Объект': {'Товары': deepcopy(ROWS)}, 'СозданныйНаЧасти': partial,
                           'ТоварыТабличнаяЧасть': [deepcopy(ROWS[1])]},
               'lifecycle': {'isNew': not partial}, 'steps': [{'snapshot': True}],
               'expected': {'actions': [[deepcopy(ROWS[1])] if partial else deepcopy(ROWS)]}}


def invoice_title_case(check):
    # Use the existing relation evaluator to accept either complete spelling.
    check = dict(check)
    expected = check.pop('expected')
    plain = {TITLE: expected[TITLE].replace('Счёт', 'Счет', 1)}
    assert check.pop('args') == []
    handler = check['target']['method']
    actions = {'op': 'get', 'args': [{'result': True}, {'literal': 'actions'}]}
    observation = {'op': 'at', 'args': [actions, {'literal': 0}]}
    check['generative'] = {
        'schemaVersion': 1, 'seed': 0, 'cases': 1, 'maxShrinks': 0,
        'sequence': {
            'initialState': 'initial', 'finalState': 'observed', 'minLength': 1, 'maxLength': 1,
            'operations': [{'name': 'ObserveTitle', 'method': handler, 'from': 'initial',
                            'to': 'observed', 'domains': []}],
        },
        'relation': {'op': 'or', 'args': [
            {'op': 'equal', 'args': [observation, {'literal': variant}]} for variant in (expected, plain)
        ]},
    }
    check['branches'] = False
    check['failureExplanation'] = 'Заголовок должен быть «' + expected[TITLE] + '» или «' + plain[TITLE] + '»'
    return check


def criteria(optional=False):
    runtime = [dict(c, timeout='30s') for c in [*cases(optional), *binding_cases()]]
    runtime = [invoice_title_case(check) if check['id'].startswith('P03.title.СчетНаОплату.') else check
               for check in runtime]
    source = query('execution', path('trace'), {'items': True}, criterion='P06.button.reverse')
    # Count by exact name or qualified suffix as emitted by the runtime trace.
    no_helper = [expect({**source, 'steps': [*source['steps'], where(predicate(['event'], 'enter'),
                    predicate(['symbol'], name_ref(WELCOME, 'РассчитатьДни', prefix), operation))]}, count=0)
                 for prefix, operation in [('', 'eq'), ('::', 'suffix')]]
    trace = {'id': 'P06.reverse.no_calculation_call', 'type': 'assertion', 'group': 'trace', 'points': 1,
             'failureExplanation': 'Для обратного диапазона обработчик не вызывает метод расчёта',
             'assertion': {'all': [expect(source, predicate([], True, 'exists')), *no_helper]}}
    return {'name': 'Программирование — практическая работа',
            'executionOptions': {'lowerLocalOverloads': True, 'missingMethodFails': True, 'resolveAliases': alias_rules()},
            'checks': [*structural(optional), *runtime, trace]}


class CriteriaDumper(yaml.SafeDumper):
    def ignore_aliases(self, value):
        return True


def dump_criteria(optional=False):
    return yaml.dump(criteria(optional), Dumper=CriteriaDumper, allow_unicode=True, sort_keys=False).encode()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--optional', action='store_true')
    args = parser.parse_args()
    args.output.write_bytes(dump_criteria(args.optional))
