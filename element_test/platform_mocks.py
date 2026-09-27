"""Explicit register spies and exact-text query fixtures; no database emulation."""
import re

from .indexer import IDENT, mask_noncode
from .model import resolve
from .yaml_io import InputError


class PlatformMocks:
    def __init__(self, contracts, mocks, check):
        self.contracts = contracts
        if any(e['name'] in {'ТестПлатформа', 'ТестКонтекст'} for e in contracts.model['elements']):
            raise InputError('Проект использует зарезервированное имя тестового модуля')
        self.queries = mocks.get('queries', [])
        self.capture = check.get('captureCalls', False)
        self.observe = check.get('observeCallArguments')
        if not isinstance(self.capture, bool):
            raise InputError('captureCalls должен быть Булево')
        if self.observe is not None and (not self.capture or not isinstance(self.observe, list)
                or any(not isinstance(n, str) for n in self.observe)
                or len(set(self.observe)) != len(self.observe)):
            raise InputError('observeCallArguments требует captureCalls и список уникальных имён')
        registers = mocks.get('registers', [])
        if not isinstance(registers, list) or len(set(str(r) for r in registers)) != len(registers):
            raise InputError('mocks.registers должен быть списком уникальных имён')
        for register in registers:
            self.register(register)
        if not isinstance(self.queries, list):
            raise InputError('mocks.queries должен быть списком')
        self.used_queries = set()
        self.query_definitions = []

    def literal(self, value):
        return self.contracts.literal(value, 'Строка')

    def log(self, owner, method, arguments):
        values = ', '.join(self.literal(n) + ': ' + n for n in arguments)
        args = '{' + values + '}' if arguments else 'новый Соответствие<Строка, Объект?>()'
        return ('ТестПлатформа.ЗаписатьВызов({' + '"owner": ' + self.literal(owner)
                + ', "method": ' + self.literal(method) + ', "args": ' + args + '})')

    def register(self, name):
        if not isinstance(name, str) or not re.fullmatch(IDENT, name):
            raise InputError('Мок регистра требует краткое имя')
        matches = resolve(self.contracts.model['elements'], name, self.contracts.namespace)
        if len(matches) != 1 or matches[0]['elementType'] != 'РегистрСведений':
            raise InputError('Поддержан только однозначный РегистрСведений: ' + name)
        element = matches[0]
        self.contracts.claim_owner(name, element)
        properties = element['properties']
        dimensions = properties.get('Измерения', [])
        fields = dimensions + properties.get('Ресурсы', []) + properties.get('Реквизиты', [])
        if properties.get('Периодичность', 'Непериодический') != 'Непериодический':
            if properties['Периодичность'] != 'Момент':
                raise InputError('Периодичность мока пока поддерживается только Момент')
            fields = [{'Имя': 'Период', 'Тип': 'Момент'}] + fields
        names = [f['Имя'] for f in fields]
        if len(set(names)) != len(names) or any(not re.fullmatch(IDENT, n) for n in names):
            raise InputError('Некорректные поля регистра ' + name)
        for field in fields:
            self.contracts.require(field['Тип'], element['namespace'])
        signature = lambda fs: ', '.join(f['Имя'] + ': ' + self.contracts.sbsl_type(f['Тип']) for f in fs)
        record = name + '.Запись'
        filter_type = name + '.ФильтрНабора'
        self.contracts.fields[record] = fields
        self.contracts.definitions[record] = '@Глобально\nструктура Запись\n' + '\n'.join(
            '    пер ' + signature([f]) for f in fields) + '\n;\n'
        self.contracts.fields[filter_type] = []
        self.contracts.definitions[filter_type] = ('@Глобально\nструктура ФильтрНабора\n'
            '    @Глобально\n    метод Установить(' + signature(dimensions) + ')\n        '
            + self.log(name, 'Фильтр.Установить', [f['Имя'] for f in dimensions]) + '\n    ;\n;\n')
        set_type = name + '.НаборЗаписей'
        self.contracts.fields[set_type] = [{'Имя': 'Фильтр', 'Тип': filter_type},
                                         {'Имя': 'Записи', 'Тип': 'Массив<' + record + '>'}]
        self.contracts.definitions[set_type] = ('@Глобально\nструктура НаборЗаписей\n'
            '    пер Фильтр: ФильтрНабора\n    пер Записи: Массив<Запись>\n'
            '    @Глобально\n    метод ДобавитьЗапись(' + signature(fields) + '): Запись\n'
            '        знч НоваяЗапись = новый Запись(' + ', '.join(n + ' = ' + n for n in names) + ')\n'
            '        Записи.Добавить(НоваяЗапись)\n        ' + self.log(name, 'ДобавитьЗапись', names)
            + '\n        возврат НоваяЗапись\n    ;\n'
            '    @Глобально\n    метод Записать(Замещать: Булево = Истина)\n        '
            + self.log(name, 'Записать', ['Замещать']) + '\n    ;\n;\n')
        self.contracts.method_dependencies[set_type] = ['ТестПлатформа.Вызовы']
        self.contracts.method_dependencies[filter_type] = ['ТестПлатформа.Вызовы']

    @staticmethod
    def query_key(text):
        # Whitespace is insignificant only outside strings. Comments are masked.
        quoted = r'"(?:\\.|[^"\\])*"'
        text = re.sub(quoted + r'|//[^\n]*|/\*[\s\S]*?\*/',
                      lambda m: m[0] if m[0].startswith('"') else ' ', text)
        return re.sub(quoted + r'|\s+',
                      lambda m: m[0] if m[0].startswith('"') else ' ', text).strip()

    def adapt(self, method):
        code = mask_noncode(method)
        replacements = []
        for match in re.finditer(r'\bЗапрос\s*\{', code):
            start, depth, end = match.end(), 1, match.end()
            while end < len(code) and depth:
                depth += (code[end] == '{') - (code[end] == '}')
                end += 1
            if depth:
                raise InputError('Незакрытый литерал Запрос')
            text = method[start:end - 1]
            found = [i for i, q in enumerate(self.queries) if isinstance(q, dict)
                     and isinstance(q.get('text'), str) and self.query_key(q['text']) == self.query_key(text)]
            if len(found) != 1:
                raise InputError('Для литерала Запрос требуется ровно один mocks.queries с совпадающим text')
            i = found[0]
            expressions = list(re.finditer(r'%\{([^{}]+)\}', text))
            if len(expressions) != 1:
                raise InputError('Мок запроса поддерживает ровно один параметр %{...}')
            if i not in self.used_queries:
                self.query(i)
                self.used_queries.add(i)
            replacements.append((match.start(), end, 'ТестПлатформа.СоздатьЗапрос' + str(i)
                                 + '(' + expressions[0][1] + ')'))
        for start, end, value in reversed(replacements):
            method = method[:start] + value + method[end:]
        return method

    def query(self, index):
        q = self.queries[index]
        if set(q) != {'text', 'fields', 'rows', 'parameterType'} or not isinstance(q['fields'], dict) or not q['fields']:
            raise InputError('Мок запроса требует text, fields, rows, parameterType')
        row_type = 'ТестПлатформа.СтрокаЗапроса' + str(index)
        fields = [{'Имя': n, 'Тип': t} for n, t in q['fields'].items()]
        for f in fields:
            if not isinstance(f['Имя'], str) or not re.fullmatch(IDENT, f['Имя']):
                raise InputError('Некорректное поле результата запроса')
            self.contracts.require(f['Тип'])
        self.contracts.require(q['parameterType'])
        self.contracts.fields[row_type] = fields
        self.contracts.definitions[row_type] = '@Глобально\nструктура СтрокаЗапроса' + str(index) + '\n' + '\n'.join(
            '    пер ' + f['Имя'] + ': ' + self.contracts.sbsl_type(f['Тип']) for f in fields) + '\n;\n'
        rows = self.contracts.literal(q['rows'], 'Массив<' + row_type + '>')
        self.query_definitions.append('@Глобально\nструктура Запрос' + str(index)
            + '\n    @Глобально\n    метод Выполнить(): Массив<СтрокаЗапроса' + str(index) + '>\n'
            + '        ТестПлатформа.ЗаписатьВызов({"owner": "query' + str(index) + '", "method": "Выполнить", "args": новый Соответствие<Строка, Объект?>()})\n'
            + '        возврат ' + rows + '\n    ;\n;\n'
            + '@Глобально\nметод СоздатьЗапрос' + str(index) + '(Параметр: ' + self.contracts.sbsl_type(q['parameterType'])
            + '): Запрос' + str(index) + '\n'
            + '    ЗаписатьВызов({"owner": "query' + str(index) + '", "method": "Создать", "args": {"Параметр": Параметр}})\n'
            + '    возврат новый Запрос' + str(index) + '()\n;\n')
        self.contracts.method_dependencies[row_type] = [q['parameterType']]

    def finish(self):
        if len(self.used_queries) != len(self.queries):
            raise InputError('Есть неиспользованные mocks.queries')
        self.contracts.definitions['ТестПлатформа'] = (
            '@Глобально\nметод ЗаписатьВызов(Вызов: Соответствие<Строка, Объект?>)\n'
            '    Консоль.Записать("ELEMENT_CALL " + СериализацияJson.ЗаписатьОбъект(Вызов))\n;\n'
            + '\n'.join(self.query_definitions))
        return 'новый Массив<Объект?>()'
