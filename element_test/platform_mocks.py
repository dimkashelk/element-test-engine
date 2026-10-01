"""Explicit register spies and exact-text query fixtures; no database emulation."""
import re

from .indexer import IDENT, mask_noncode
from .yaml_io import InputError, InvalidTestError, UnsupportedSyntaxError


class PlatformMocks:
    def __init__(self, contracts, mocks, check):
        self.contracts = contracts
        self.query_contract_explicit = 'queries' in mocks
        if any(e['name'] in {'ТестПлатформа', 'ТестКонтекст'} for e in contracts.model['elements']):
            raise InputError('Проект использует зарезервированное имя тестового модуля')
        self.queries = mocks.get('queries', [])
        self.capture = check.get('captureCalls', False)
        self.observe = check.get('observeCallArguments')
        if not isinstance(self.capture, bool):
            raise InvalidTestError('captureCalls должен быть Булево')
        if self.observe is not None and (not self.capture or not isinstance(self.observe, list)
                or any(not isinstance(n, str) for n in self.observe)
                or len(set(self.observe)) != len(self.observe)):
            raise InvalidTestError('observeCallArguments требует captureCalls и список уникальных имён')
        registers = mocks.get('registers', [])
        if not isinstance(registers, list) or len(set(str(r) for r in registers)) != len(registers):
            raise InvalidTestError('mocks.registers должен быть списком уникальных имён')
        for register in registers:
            self.register(register)
        if not isinstance(self.queries, list):
            raise InvalidTestError('mocks.queries должен быть списком')
        self.used_queries = set()
        self.query_definitions = []
        self.storage_mode = False

    def literal(self, value):
        return self.contracts.literal(value, 'Строка')

    def log(self, owner, method, arguments):
        values = ', '.join(self.literal(n) + ': ' + n for n in arguments)
        args = '{' + values + '}' if arguments else 'новый Соответствие<Строка, Объект?>()'
        return ('ТестПлатформа.ЗаписатьВызов({' + '"owner": ' + self.literal(owner)
                + ', "method": ' + self.literal(method) + ', "args": ' + args + '})')

    def register(self, name):
        if not isinstance(name, str) or not re.fullmatch(rf'{IDENT}(?:::{IDENT})*', name):
            raise InputError('Мок регистра требует имя или квалифицированное имя')
        matches = self.contracts.resolve(name)
        if len(matches) != 1 or matches[0]['elementType'] not in {'РегистрСведений', 'РегистрНакопления'}:
            raise InputError('Требуется однозначный РегистрСведений или РегистрНакопления: ' + name)
        element = matches[0]
        name = self.contracts.canonical_type(name)
        self.contracts.claim_owner(name, element)
        previous = self.contracts.namespace, self.contracts.imports
        self.contracts.namespace, self.contracts.imports = element['namespace'], ()
        try:
            self._register(name, element)
        finally:
            self.contracts.namespace, self.contracts.imports = previous

    def _register(self, name, element):
        properties = element['properties']
        accumulation = element['elementType'] == 'РегистрНакопления'
        dimensions = properties.get('Измерения', [])
        fields = dimensions + properties.get('Ресурсы', []) + properties.get('Реквизиты', [])
        if accumulation:
            if properties.get('ВидРегистра', 'Остатки') != 'Остатки':
                raise InputError('Мок регистра накопления поддерживает только Остатки')
            registrars = [f for f in properties.get('Реквизиты', []) if f['Имя'] == 'Регистратор']
            if len(registrars) != 1:
                raise InputError('Регистр накопления требует типизированный Регистратор')
            dimensions = registrars
            fields = [{'Имя': 'Период', 'Тип': 'ДатаВремя'},
                      {'Имя': 'ВидЗаписи', 'Тип': 'ВидЗаписиРегистраНакопления'}] + [
                          f for f in fields if f['Имя'] != 'Регистратор']
            enum = 'ВидЗаписиРегистраНакопления'
            if any(e['name'] == enum for e in self.contracts.model['elements']):
                raise InputError('Проект использует зарезервированное имя ' + enum)
            self.contracts.enums[enum] = ['Приход', 'Расход']
            self.contracts.enum_types[enum] = enum + '.Значение'
            self.contracts.definitions[enum] = ('@Глобально\nперечисление Значение\n'
                '    Приход умолчание,\n    Расход\n;\n'
                '@Глобально\nконст Приход = Значение.Приход\n'
                '@Глобально\nконст Расход = Значение.Расход\n')
        elif properties.get('Периодичность', 'Непериодический') != 'Непериодический':
            if properties['Периодичность'] != 'Момент':
                raise InputError('Периодичность мока пока поддерживается только Момент')
            fields = [{'Имя': 'Период', 'Тип': 'Момент'}] + fields
        names = [f['Имя'] for f in fields]
        if len(set(names)) != len(names) or any(not re.fullmatch(IDENT, n) for n in names):
            raise InputError('Некорректные поля регистра ' + name)
        fields = [{**f, 'Тип': self.contracts.canonical_type(f['Тип'])} for f in fields]
        dimensions = [{**f, 'Тип': self.contracts.canonical_type(f['Тип'])} for f in dimensions]
        for field in fields:
            self.contracts.require(field['Тип'], element['namespace'])
        for field in dimensions:
            self.contracts.require(field['Тип'], element['namespace'])
        signature = lambda fs: ', '.join(f['Имя'] + ': ' + self.contracts.sbsl_type(f['Тип']) for f in fs)
        def add_signature():
            parameters = []
            for field in fields:
                declaration = signature([field])
                if accumulation and field['Имя'] not in {'Период', 'ВидЗаписи'}:
                    if 'ЗначениеПоУмолчанию' in field:
                        default = self.contracts.literal(field['ЗначениеПоУмолчанию'], field['Тип'])
                    elif field['Тип'].endswith('?'):
                        default = 'Неопределено'
                    elif field['Тип'] in {'Число', 'Строка', 'Булево'}:
                        default = self.contracts.literal({'Число': 0, 'Строка': '', 'Булево': False}[field['Тип']], field['Тип'])
                    else:
                        raise InputError('Значение по умолчанию мока не поддерживается: ' + field['Тип'])
                    declaration += ' = ' + default
                parameters.append(declaration)
            return ', '.join(parameters)
        record = name + '.Запись'
        filter_type = name + '.ФильтрНабора'
        self.contracts.fields[record] = fields
        self.contracts.definitions[record] = '@Глобально\nструктура Запись\n' + '\n'.join(
            '    пер ' + signature([f]) + (' = ' + self.contracts.literal(f['ЗначениеПоУмолчанию'], f['Тип'])
                if 'ЗначениеПоУмолчанию' in f else '') for f in fields) + '\n;\n'
        self.contracts.fields[filter_type] = []
        self.contracts.definitions[filter_type] = ('@Глобально\nструктура ФильтрНабора\n'
            '    @Глобально\n    метод Установить(' + signature(dimensions) + ')\n        '
            + self.log(name, 'Фильтр.Установить', [f['Имя'] for f in dimensions]) + '\n    ;\n;\n')
        set_type = name + '.НаборЗаписей'
        self.contracts.fields[set_type] = [{'Имя': 'Фильтр', 'Тип': filter_type},
                                         {'Имя': 'Записи', 'Тип': 'Массив<' + record + '>'}]
        self.contracts.definitions[set_type] = ('@Глобально\nструктура НаборЗаписей\n'
            '    пер Фильтр: ФильтрНабора\n    пер Записи: Массив<Запись>\n'
            '    @Глобально\n' + ('    @ИменованныеПараметры\n' if accumulation else '')
            + '    метод ДобавитьЗапись(' + add_signature() + '): Запись\n'
            '        знч НоваяЗапись = новый Запись(' + ', '.join(n + ' = ' + n for n in names) + ')\n'
            '        Записи.Добавить(НоваяЗапись)\n        ' + self.log(name, 'ДобавитьЗапись', names)
            + '\n        возврат НоваяЗапись\n    ;\n'
            '    @Глобально\n    метод Записать(Замещать: Булево = Истина)\n        '
            + self.log(name, 'Записать', ['Замещать']) + '\n    ;\n;\n')
        self.contracts.method_dependencies[set_type] = ['ТестПлатформа.Вызовы']
        self.contracts.method_dependencies[filter_type] = ['ТестПлатформа.Вызовы'] + [f['Тип'] for f in dimensions]

    @staticmethod
    def query_key(text):
        # Whitespace is insignificant only outside strings. Comments are masked.
        quoted = r'"(?:\\.|[^"\\])*"'
        text = re.sub(quoted + r'|//[^\n]*|/\*[\s\S]*?\*/',
                      lambda m: m[0] if m[0].startswith('"') else ' ', text)
        return re.sub(quoted + r'|\s+',
                      lambda m: m[0] if m[0].startswith('"') else ' ', text).strip()

    def adapt(self, method):
        if self.storage_mode:
            from .storage_queries import adapt_storage_queries
            return adapt_storage_queries(method, self.contracts)
        code = mask_noncode(method)
        replacements = []
        for match in re.finditer(r'\bЗапрос\s*\{', code):
            start, depth, end = match.end(), 1, match.end()
            while end < len(code) and depth:
                depth += (code[end] == '{') - (code[end] == '}')
                end += 1
            if depth:
                raise UnsupportedSyntaxError('Незакрытый литерал Запрос')
            text = method[start:end - 1]
            found = [i for i, q in enumerate(self.queries) if isinstance(q, dict)
                     and isinstance(q.get('text'), str) and self.query_key(q['text']) == self.query_key(text)]
            if len(found) != 1:
                if not self.query_contract_explicit:
                    raise UnsupportedSyntaxError('Общий литерал Запрос вне поддержанного контракта; требуется явный mocks.queries')
                raise InvalidTestError('Для литерала Запрос требуется ровно один mocks.queries с совпадающим text')
            i = found[0]
            visible = mask_noncode(text)
            expressions = []
            markers = list(re.finditer(rf'%(?:\{{|{IDENT})', visible))
            if len(markers) != visible.count('%'):
                raise UnsupportedSyntaxError('Неподдержанная форма параметра запроса')
            for marker in markers:
                if marker[0] == '%{':
                    expression = re.match(r'%\{([^{}]+)\}', visible[marker.start():])
                    if expression is None or not text[marker.start() + 2:marker.start() + expression.end() - 1].strip():
                        raise UnsupportedSyntaxError('Параметры запроса требуют непустые выражения %{...} без вложенных фигурных скобок')
                    expressions.append(text[marker.start() + 2:marker.start() + expression.end() - 1])
                else:
                    expressions.append(marker[0][1:])
            if i not in self.used_queries:
                self.query(i, len(expressions))
                self.used_queries.add(i)
            replacements.append((match.start(), end, 'ТестПлатформа.СоздатьЗапрос' + str(i)
                                 + '(' + ', '.join(expressions) + ')'))
        for start, end, value in reversed(replacements):
            method = method[:start] + value + method[end:]
        return method

    def query(self, index, parameter_count):
        q = self.queries[index]
        common = {'text', 'fields', 'rows'}
        if (set(q) not in (common | {'parameterType'}, common | {'parameterTypes'})
                or not isinstance(q['fields'], dict) or not q['fields']):
            raise InvalidTestError('Мок запроса требует text, fields, rows и ровно одно из parameterType/parameterTypes')
        if 'parameterType' in q:
            parameter_types = [q['parameterType']]
        else:
            parameter_types = q['parameterTypes']
            if not isinstance(parameter_types, list):
                raise InvalidTestError('parameterTypes должен быть списком типов')
        if len(parameter_types) != parameter_count:
            raise InvalidTestError('Число типов параметров запроса должно совпадать с числом выражений %{...}')
        if any(not isinstance(t, str) or not t.strip() for t in parameter_types):
            raise InvalidTestError('Типы параметров запроса должны быть непустыми строками')
        row_type = 'ТестПлатформа.СтрокаЗапроса' + str(index)
        fields = [{'Имя': n, 'Тип': t} for n, t in q['fields'].items()]
        for f in fields:
            if not isinstance(f['Имя'], str) or not re.fullmatch(IDENT, f['Имя']):
                raise InvalidTestError('Некорректное поле результата запроса')
            self.contracts.require(f['Тип'])
        for type_name in parameter_types:
            self.contracts.require(type_name)
        self.contracts.fields[row_type] = fields
        self.contracts.definitions[row_type] = '@Глобально\nструктура СтрокаЗапроса' + str(index) + '\n' + '\n'.join(
            '    пер ' + f['Имя'] + ': ' + self.contracts.sbsl_type(f['Тип']) for f in fields) + '\n;\n'
        rows = self.contracts.literal(q['rows'], 'Массив<' + row_type + '>')
        parameter_names = (['Параметр'] if 'parameterType' in q else
                           ['Параметр' + str(i + 1) for i in range(parameter_count)])
        signature = ', '.join(name + ': ' + self.contracts.sbsl_type(type_name)
                              for name, type_name in zip(parameter_names, parameter_types))
        arguments = ('{' + ', '.join('"' + name + '": ' + name for name in parameter_names) + '}'
                     if parameter_names else 'новый Соответствие<Строка, Объект?>()')
        self.query_definitions.append('@Глобально\nструктура Запрос' + str(index)
            + '\n    @Глобально\n    метод Выполнить(): Массив<СтрокаЗапроса' + str(index) + '>\n'
            + '        ТестПлатформа.ЗаписатьВызов({"owner": "query' + str(index) + '", "method": "Выполнить", "args": новый Соответствие<Строка, Объект?>()})\n'
            + '        возврат ' + rows + '\n    ;\n;\n'
            + '@Глобально\nметод СоздатьЗапрос' + str(index) + '(' + signature
            + '): Запрос' + str(index) + '\n'
            + '    ЗаписатьВызов({"owner": "query' + str(index) + '", "method": "Создать", "args": ' + arguments + '})\n'
            + '    возврат новый Запрос' + str(index) + '()\n;\n')
        self.contracts.method_dependencies[row_type] = parameter_types

    def finish(self):
        if len(self.used_queries) != len(self.queries):
            raise InvalidTestError('Есть неиспользованные mocks.queries')
        self.contracts.definitions['ТестПлатформа'] = (
            '@Глобально\nметод ЗаписатьВызов(Вызов: Соответствие<Строка, Объект?>)\n'
            '    Консоль.Записать("ELEMENT_CALL " + СериализацияJson.ЗаписатьОбъект(Вызов))\n;\n'
            + '\n'.join(self.query_definitions))
        return 'новый Массив<Объект?>()'
