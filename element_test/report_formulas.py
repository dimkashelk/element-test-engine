"""A small report formula language. It is neither XBSL nor an expression eval.

The parser preserves formula offsets; the compiler emits typed Script methods.
ЕСЛИ evaluates one branch, and reference members load fresh storage snapshots.
"""
from dataclasses import dataclass, asdict
import json
import re

from .indexer import IDENT
from .yaml_io import UnsupportedSyntaxError


@dataclass(frozen=True)
class Formula:
    kind: str
    value: object
    children: tuple
    range: tuple

    def to_dict(self):
        return asdict(self)


class Parser:
    def __init__(self, text):
        if not isinstance(text, str) or not text or text.startswith('=') or len(text) > 16000:
            raise UnsupportedSyntaxError('Формула отчета требует отдельный язык, без XBSL =')
        self.text, self.tokens, self.pos = text, [], 0
        token = re.compile(r'"(?:[^"\\]|\\.)*"|\d+(?:\.\d+)?|' + IDENT + r'|<>|!=|<=|>=|[().,=+*/<>-]')
        at = 0
        while at < len(text):
            if text[at].isspace():
                at += 1; continue
            match = token.match(text, at)
            if not match:
                raise UnsupportedSyntaxError('Неизвестный синтаксис формулы отчета: ' + str(at))
            self.tokens.append((match[0], at, match.end())); at = match.end()
        if len(self.tokens) > 1000:
            raise UnsupportedSyntaxError('Лимит сложности формулы отчета')

    def take(self, token):
        if self.pos < len(self.tokens) and self.tokens[self.pos][0] == token:
            self.pos += 1; return True
        return False

    def expect(self, token):
        if not self.take(token):
            raise UnsupportedSyntaxError('Формула отчета: ожидается ' + token)

    def expression(self, precedence=0, depth=0):
        if depth > 40 or self.pos >= len(self.tokens):
            raise UnsupportedSyntaxError('Незавершенная или слишком глубокая формула отчета')
        token, start, end = self.tokens[self.pos]; self.pos += 1
        if token == '(':
            node = self.expression(depth=depth+1); self.expect(')')
        elif token.startswith('"'):
            try: value = json.loads(token)
            except ValueError as exc: raise UnsupportedSyntaxError('Строка формулы отчета') from exc
            node = Formula('string', value, (), (start, end))
        elif token[0].isdigit():
            node = Formula('number', token, (), (start, end))
        elif token == '-':
            child = self.expression(30, depth+1)
            node = Formula('negate', None, (child,), (start, child.range[1]))
        elif re.fullmatch(IDENT, token):
            if self.take('('):
                args = []
                if not self.take(')'):
                    while True:
                        args.append(self.expression(depth=depth+1))
                        if not self.take(','): break
                    self.expect(')')
                if token.upper() not in {'ЕСЛИ', 'ССЫЛКА'} or len(args) != 3:
                    raise UnsupportedSyntaxError('Функция формулы вне контракта: ' + token)
                node = Formula(token.upper(), None, tuple(args), (start, self.tokens[self.pos-1][2]))
            else:
                parts = [token]
                while self.take('.'):
                    if self.pos >= len(self.tokens) or not re.fullmatch(IDENT, self.tokens[self.pos][0]):
                        raise UnsupportedSyntaxError('Член поля формулы отчета')
                    parts.append(self.tokens[self.pos][0]); self.pos += 1
                node = Formula('field', '.'.join(parts), (), (start, self.tokens[self.pos-1][2]))
        else:
            raise UnsupportedSyntaxError('Синтаксис формулы вне контракта: ' + token)
        while self.pos < len(self.tokens):
            op = self.tokens[self.pos][0]
            level = {'=': 1, '!=': 1, '<>': 1, '<': 1, '>': 1, '<=': 1, '>=': 1,
                     '+': 10, '-': 10, '*': 20, '/': 20}.get(op, -1)
            if level < precedence: break
            self.pos += 1
            right = self.expression(level+1, depth+1)
            node = Formula('binary', op, (node, right), (start, right.range[1]))
        return node


def parse_formula(text):
    p = Parser(text); result = p.expression()
    if p.pos != len(p.tokens):
        raise UnsupportedSyntaxError('Остаток формулы отчета')
    return result


def fields(node):
    if node.kind == 'field': yield node.value.split('.')[0]
    for child in node.children: yield from fields(child)


# Document/register system names used by the report engine, not business names.
SYSTEM_FIELDS = {'Number': 'Номер', 'Date': 'Дата', 'Period': 'Период',
                 'Recorder': 'Регистратор', 'RecordKind': 'ВидЗаписи'}


def source_field(name, schema):
    declared = {f.name for f in schema['fields']}
    target = SYSTEM_FIELDS.get(name, name)
    if name in declared and target in declared and name != target:
        raise UnsupportedSyntaxError('Неоднозначное внутреннее имя отчета: ' + name)
    if name in declared: return name
    if target in declared: return target
    if name.endswith('Balance') and name[:-7] + 'Остаток' in declared:
        return name[:-7] + 'Остаток'
    raise UnsupportedSyntaxError('Неизвестное поле отчета: ' + name)


class Compiler:
    def __init__(self, plan, row_type, row_fields, owner):
        self.plan, self.c, self.row_type, self.row_fields, self.owner = plan, plan.contracts, row_type, row_fields, owner
        self.methods, self.types = [], [row_type]

    def method(self, name, typ, body):
        self.types.append(typ)
        self.methods.append('метод ' + name + '(Row: ' + self.c.sbsl_type(self.row_type) + '): ' + self.c.sbsl_type(typ) + '\n' + body + '\n;\n')
        return self.owner + '.' + name + '(Row)', typ

    def compile(self, node):
        c = self.c
        if node.kind == 'string': return c.literal(node.value, 'Строка'), 'Строка'
        if node.kind == 'number': return node.value, 'Число'
        if node.kind == 'field':
            parts = node.value.split('.')
            if parts[0] not in self.row_fields:
                raise UnsupportedSyntaxError('Неизвестное поле результата отчета: ' + parts[0])
            typ = self.row_fields[parts[0]]
            expr = 'Row.' + parts[0]
            if len(parts) == 1: return expr, typ
            if len(parts) != 2:
                raise UnsupportedSyntaxError('Член формулы требует обязательную ссылку')
            nullable = typ.endswith('?')
            body = '    если ' + expr + ' == Неопределено\n        возврат Неопределено\n    ;\n' if nullable else ''
            result_types = set()
            for member in typ.rstrip('?').rstrip('|').split('|'):
                if not member.endswith('.Ссылка'):
                    raise UnsupportedSyntaxError('Член формулы требует ссылку/union ссылок')
                element = c.canonical_elements[member.split('.')[0]]
                self.plan.storage.attach(element, query_only=True)
                obj = member.removesuffix('.Ссылка') + '.Объект'
                attrs = {f['Имя']: f['Тип'] for f in c.fields[obj]}
                name = parts[1] if parts[1] in attrs else SYSTEM_FIELDS.get(parts[1], parts[1])
                if name not in attrs:
                    raise UnsupportedSyntaxError('Член отсутствует у варианта регистратора: ' + member + '.' + name)
                result_types.add(attrs[name]); self.types.extend([member, obj])
                body += ('    если ' + expr + ' это ' + c.sbsl_type(member) + '\n'
                         '        знч Loaded = (' + expr + ' как ' + c.sbsl_type(member) + ').ЗагрузитьОбъект()\n'
                         '        если Loaded == Неопределено\n'
                         '            выбросить новый ИсключениеНеподдерживаемаяОперация("Ссылка отчета отсутствует в storage")\n        ;\n'
                         '        возврат Loaded.' + name + '\n    ;\n')
            if len(result_types) != 1:
                raise UnsupportedSyntaxError('Варианты регистратора имеют разные типы члена')
            body += '    выбросить новый ИсключениеНеподдерживаемаяОперация("Неизвестный вариант регистратора")'
            result_type = result_types.pop()
            return self.method('Member' + str(len(self.methods)), result_type + ('?' if nullable and not result_type.endswith('?') else ''), body)
        if node.kind == 'ССЫЛКА':
            if any(a.kind != 'string' for a in node.children):
                raise UnsupportedSyntaxError('ССЫЛКА требует три строковых литерала')
            value, owner, caption = [a.value for a in node.children]
            if owner != 'Enum.Std::AccumulationRegisters::AccumulationRegisterRecordKind' or value not in {'Receipt', 'Expense'}:
                raise UnsupportedSyntaxError('ССЫЛКА: enum вне проверенного контракта')
            # The common query adapter owns the nominal register enum.
            typ = 'ВидЗаписиРегистраНакопления'
            c.require(typ)
            return typ + '.' + {'Receipt': 'Приход', 'Expense': 'Расход'}[value], typ
        if node.kind == 'ЕСЛИ':
            cond, ct = self.compile(node.children[0]); yes, yt = self.compile(node.children[1]); no, nt = self.compile(node.children[2])
            if ct != 'Булево': raise UnsupportedSyntaxError('ЕСЛИ требует условие Булево')
            nullable = yt.endswith('?') or nt.endswith('?')
            members = list(dict.fromkeys(yt.rstrip('?').rstrip('|').split('|') + nt.rstrip('?').rstrip('|').split('|')))
            typ = '|'.join(members) + (('|' if len(members) > 1 else '') + '?' if nullable else '')
            return self.method('If' + str(len(self.methods)), typ,
                '    если ' + cond + '\n        возврат ' + yes + '\n    иначе\n        возврат ' + no + '\n    ;')
        if node.kind == 'negate':
            value, typ = self.compile(node.children[0])
            if typ != 'Число': raise UnsupportedSyntaxError('Минус формулы требует Число')
            return '-(' + value + ')', typ
        left, lt = self.compile(node.children[0]); right, rt = self.compile(node.children[1]); op = node.value
        if op in {'=', '!=', '<>', '<', '>', '<=', '>='}:
            if lt.rstrip('?') != rt.rstrip('?') or '|' in lt:
                raise UnsupportedSyntaxError('Сравнение формулы требует одинаковые обязательные типы')
            if op not in {'=', '!=', '<>'} and lt.rstrip('?') not in {'Число', 'Строка', 'Дата', 'ДатаВремя', 'Момент'}:
                raise UnsupportedSyntaxError('Упорядочивание формулы вне контракта')
            if op not in {'=', '!=', '<>'} and (lt.endswith('?') or rt.endswith('?')):
                raise UnsupportedSyntaxError('Сравнение nullable по порядку требует отдельный контракт')
            return '(' + left + ' ' + {'=': '==', '<>': '!='}.get(op, op) + ' ' + right + ')', 'Булево'
        nullable = lt.endswith('?') or rt.endswith('?')
        guard = ''
        if nullable:
            guard = '    если ' + ' или '.join(x + ' == Неопределено' for x,t in ((left,lt),(right,rt)) if t.endswith('?')) + '\n        возврат Неопределено\n    ;\n'
        lt, rt = lt.rstrip('?'), rt.rstrip('?')
        if op == '+' and 'Строка' in {lt, rt} and {lt, rt} <= {'Число', 'Строка'}:
            if lt == 'Число': left = '(' + left + ').ВСтроку()'
            if rt == 'Число': right = '(' + right + ').ВСтроку()'
            expression = '(' + left + ' + ' + right + ')'
            return self.method('Concat'+str(len(self.methods)), 'Строка?', guard+'    возврат '+expression) if nullable else (expression,'Строка')
        if lt != 'Число' or rt != 'Число':
            raise UnsupportedSyntaxError('Арифметика формулы требует Число')
        expression = '(' + left + ' ' + op + ' ' + right + ')'
        return self.method('Math'+str(len(self.methods)), 'Число?', guard+'    возврат '+expression) if nullable else (expression,'Число')
