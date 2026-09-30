"""Source-spanned XBSL expression and statement syntax tree.

The parser is deliberately syntax-only: name and type resolution belong to the
indexer. Every consumed expression token is represented by a node. Unrecognized
syntax produces an explicit error node instead of silently dropping a call.
"""
from dataclasses import dataclass, field
import re


_IDENT = re.compile(r"[^\W\d]\w*", re.UNICODE)
_MULTI = ("??=", "?.", "::", "->", "??", "<=", ">=", "!=", "==", "<>", "+=", "-=", "*=", "/=", "**", "&&", "||", "<:", ":>")
_PREFIX = {"-", "+", "!", "не", "~", "ожидать"}
_PRECEDENCE = {
    "=": 1, "+=": 1, "-=": 1, "*=": 1, "/=": 1, "??=": 1,
    "?": 2, "??": 3, "или": 4, "||": 4, "и": 5, "&&": 5,
    "==": 6, "!=": 6, "<>": 6, "<": 7, ">": 7,
    "<=": 7, ">=": 7, "в": 7, "это": 7, "как": 8, "есть": 8,
    "|": 9, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10, "**": 11,
}
_RIGHT = {"=", "+=", "-=", "*=", "/=", "??=", "??"}
_INTRO = {"возврат", "выбросить", "если", "иначеесли", "пока", "для",
          "выбор", "когда", "исп", "знч", "пер", "поймать"}
_CONTROL = {"иначе", "попытка", "продолжить", "прервать", "конец", "затем",
            "область", "вконце"}


@dataclass(frozen=True)
class Lexeme:
    value: str
    start: int
    end: int
    kind: str = "symbol"


@dataclass
class Node:
    kind: str
    start: int
    end: int
    value: str | None = None
    children: list["Node"] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


def string_end(source, start, end=None):
    """Find the closing quote, allowing quoted calls inside interpolation."""
    end = len(source) if end is None else end
    i = start + 1
    while i < end:
        if source[i] == "\\":
            i += 2
        elif source.startswith(("${", "%{"), i):
            depth = 1
            i += 2
            while i < end and depth:
                if source[i] == '"':
                    i = string_end(source, i, end)
                elif source[i] == "{":
                    depth += 1
                    i += 1
                elif source[i] == "}":
                    depth -= 1
                    i += 1
                elif source[i] == "\\":
                    i += 2
                else:
                    i += 1
        elif source[i] == '"':
            return i + 1
        else:
            i += 1
    return end


def tokenize(source, start=0, end=None):
    """Keep string/query literal spans and nested interpolation expressions."""
    end = len(source) if end is None else end
    result = []
    i = start
    while i < end:
        c = source[i]
        if c.isspace():
            if c == "\n": result.append(Lexeme("\n", i, i + 1, "newline"))
            i += 1
        elif source.startswith("//", i):
            next_line = source.find("\n", i, end)
            i = end if next_line < 0 else next_line
        elif source.startswith("/*", i):
            close = source.find("*/", i + 2, end)
            i = end if close < 0 else close + 2
        elif c == '"':
            first = i
            i = string_end(source, i, end)
            result.append(Lexeme(source[first:i], first, i, "string"))
        elif c.isdigit():
            first = i
            i += 1
            while i < end and (source[i].isdigit() or source[i] == "_" or
                               (source[i] == "." and i + 1 < end and source[i + 1].isdigit())):
                i += 1
            while i < end and source[i].isalpha():
                i += 1
            result.append(Lexeme(source[first:i], first, i, "number"))
        elif c == "'":
            first = i
            i += 1
            while i < end:
                if source[i] == "\\": i += 2
                elif source[i] == "'":
                    i += 1
                    break
                else: i += 1
            result.append(Lexeme(source[first:i], first, i, "regex"))
        else:
            match = _IDENT.match(source, i)
            if match:
                result.append(Lexeme(match.group(), i, match.end(), "word"))
                i = match.end()
            else:
                operator = next((op for op in _MULTI if source.startswith(op, i)), None)
                width = len(operator) if operator else 1
                result.append(Lexeme(source[i:i + width], i, i + width))
                i += width
    return result


def _interpolations(source, start, end, marker):
    nodes = []
    i = start
    while i < end - 1:
        if source[i] == "\\":
            i += 2
            continue
        active_marker = next((item for item in ((marker,) if marker == "%" else ("$", "%"))
                             if source.startswith(item + "{", i)), None)
        if active_marker:
            opening = i
            i += 2
            depth, expression_start = 1, i
            while i < end and depth:
                if source[i] == '"':
                    i += 1
                    while i < end and source[i] != '"':
                        i += 2 if source[i] == "\\" else 1
                    i += i < end
                    continue
                if source[i] == "{": depth += 1
                elif source[i] == "}": depth -= 1
                i += 1
            if depth:
                nodes.append(Node("error", opening, end, "unclosed interpolation"))
                break
            format_at = None
            nested = 0
            for position in range(expression_start, i - 1):
                if source[position] in "([{": nested += 1
                elif source[position] in ")]}": nested -= 1
                elif source[position] == "|" and nested == 0:
                    format_at = position
                    break
            inner_end = format_at if format_at is not None else i - 1
            inner = parse_expression(source, expression_start, inner_end)
            children = [inner]
            if format_at is not None:
                children.append(Node("format", format_at, i - 1,
                                     source[format_at + 1:i - 1]))
            nodes.append(Node("interpolation", opening, i, active_marker, children))
        elif source[i] in "$%" and i + 1 < end and (match := _IDENT.match(source, i + 1)):
            nodes.append(Node("interpolation", i, match.end(), source[i],
                              [Node("name", i + 1, match.end(), match.group())]))
            i = match.end()
        else:
            i += 1
    return nodes


class _Parser:
    def __init__(self, source, tokens, assignment=False):
        self.source = source
        self.tokens = [token for token in tokens if token.kind != "newline"]
        self.pos = 0
        self.assignment = assignment

    def peek(self, value=None):
        token = self.tokens[self.pos] if self.pos < len(self.tokens) else None
        return token if value is None else token is not None and token.value == value

    def take(self):
        token = self.peek()
        if token: self.pos += 1
        return token

    def parse(self):
        if not self.tokens:
            return Node("empty", 0, 0)
        result = self.expression()
        if self.peek():
            rest = self.tokens[self.pos:]
            result = Node("sequence", result.start, rest[-1].end, children=[result] +
                          [Node("error", token.start, token.end, token.value) for token in rest])
        return result

    def expression(self, minimum=0):
        left = self.prefix()
        while (token := self.peek()) is not None:
            value = token.value
            if value in {"(", "[", "{", ".", "?.", "::"} and 12 >= minimum:
                left = self.postfix(left)
                continue
            if value == "!" and 12 >= minimum:
                self.take()
                left = Node("nonnull", left.start, token.end, children=[left])
                continue
            if value == "<" and left.kind in {"name", "member"} and 12 >= minimum:
                generic = self.generic(left)
                if generic is not None:
                    left = generic
                    continue
            if value == "?" and (self.pos + 1 == len(self.tokens) or
                                  self.tokens[self.pos + 1].value in {
                                      ".", "(", "[", ")", "]", "}", ",", "->", ">", "|"}):
                self.take()
                left = Node("optional", left.start, token.end, children=[left])
                continue
            if value == "->" and minimum <= 1:
                self.take()
                right = self.expression(1)
                left = Node("lambda", left.start, right.end, children=[left, right])
                continue
            precedence = (1 if self.assignment else 6) if value == "=" else _PRECEDENCE.get(value)
            if precedence is None or precedence < minimum:
                break
            self.take()
            if value == "?":
                yes = self.expression(0)
                colon = self.take() if self.peek(":") else None
                no = self.expression(precedence) if colon else Node("error", yes.end, yes.end, "missing :")
                left = Node("conditional", left.start, no.end, children=[left, yes, no])
                continue
            right = self.expression(precedence if value in _RIGHT else precedence + 1)
            kind = "assignment" if precedence == 1 and value in _RIGHT else "cast" if value == "как" else "binary"
            left = Node(kind, left.start, right.end, value, [left, right])
        return left

    def prefix(self):
        token = self.take()
        if token is None:
            end = self.tokens[-1].end if self.tokens else 0
            return Node("error", end, end, "missing operand")
        value = token.value
        if value in _PREFIX:
            operand = self.expression(11)
            return Node("unary", token.start, operand.end, value, [operand])
        if value == "новый":
            type_tokens = []
            generic = 0
            while (part := self.peek()) and (part.kind == "word" or part.value in {".", "::", "<", ">", ",", "?", "|"}):
                if part.value == "<": generic += 1
                elif part.value == ">": generic -= 1
                elif part.value == "," and generic == 0: break
                elif part.value == "?" and generic == 0: break
                type_tokens.append(self.take())
                if generic == 0 and self.peek("("): break
            if not type_tokens:
                return Node("error", token.start, token.end, "missing constructor type")
            type_node = Node("type", type_tokens[0].start, type_tokens[-1].end,
                             self.source[type_tokens[0].start:type_tokens[-1].end])
            result = Node("new", token.start, type_node.end, children=[type_node])
            if self.peek("("): result = self.postfix(result)
            return result
        if value == "метод" and self.peek("("):
            opening = self.take()
            parameters = self.delimited(")")
            parameter_node = Node("parameters", opening.start, self.tokens[self.pos - 1].end,
                                  children=parameters)
            if not self.peek("->"):
                return Node("error", token.start, parameter_node.end, "missing ->")
            self.take()
            first = self.pos
            blocks = 0
            while self.peek():
                part = self.peek()
                previous = self.tokens[self.pos - 1]
                beginning = "\n" in self.source[previous.end:part.start]
                if beginning and part.value in {"если", "пока", "для", "выбор", "попытка"}:
                    blocks += 1
                if part.value == ";" and (beginning or not blocks):
                    if not blocks: break
                    blocks -= 1
                self.take()
            raw_body = self.tokens[first:self.pos]
            body_tokens = (tokenize(self.source, raw_body[0].start, raw_body[-1].end)
                           if raw_body else [])
            body = [_statement(self.source, chunk) for chunk in _segments(body_tokens)
                    if any(item.kind != "newline" for item in chunk)]
            end = self.take().end if self.peek(";") else (body[-1].end if body else parameter_node.end)
            return Node("lambda", token.start, end, children=[parameter_node,
                        Node("body", parameter_node.end, end, children=body)])
        if value == "&":
            name = self.take()
            if not name or name.kind != "word":
                return Node("error", token.start, token.end, "missing method reference")
            end = name.end
            while self.peek(".") or self.peek("::"):
                if self.pos + 1 >= len(self.tokens) or self.tokens[self.pos + 1].kind != "word": break
                self.take()
                end = self.take().end
            return Node("reference", token.start, end, self.source[name.start:end])
        if token.kind in {"word", "number"}:
            if token.kind == "number" and re.search(r"[\d][дчмс]$", value):
                ending = token.end
                while (self.peek() and self.peek().kind == "number" and
                       re.search(r"[\d][дчмс]$", self.peek().value)):
                    ending = self.take().end
                return Node("duration", token.start, ending, self.source[token.start:ending])
            return Node("name" if token.kind == "word" else "number", token.start, token.end, value)
        if token.kind == "regex":
            return Node("regex", token.start, token.end, value)
        if token.kind == "string":
            return Node("string", token.start, token.end, children=
                        _interpolations(self.source, token.start + 1, token.end - 1, "$"))
        if value == "(":
            items = self.delimited(")")
            end = self.tokens[self.pos - 1].end
            if self.peek("->"):
                return Node("parameters", token.start, end, children=items)
            return Node("group" if len(items) == 1 else "tuple", token.start, end, children=items)
        if value == "[":
            items = self.delimited("]")
            return Node("array", token.start, self.tokens[self.pos - 1].end, children=items)
        if value == "{":
            if self.peek(":"):
                self.take()
            items = self.delimited("}")
            return Node("map", token.start, self.tokens[self.pos - 1].end, children=items)
        if value == "<":
            depth = 1
            while self.peek() and depth:
                part = self.take()
                if part.value == "<": depth += 1
                elif part.value == ">": depth -= 1
            if depth:
                return Node("error", token.start, self.tokens[-1].end, "missing >")
            close = self.tokens[self.pos - 1]
            return Node("type", token.start, close.end, self.source[token.start:close.end])
        if value == "<:":
            while self.peek() and not self.peek(":>"):
                self.take()
            close = self.take() if self.peek(":>") else token
            result = Node("typed_literal", token.start, close.end,
                          self.source[token.start:close.end])
            if self.peek("{"): result = self.postfix(result)
            return result
        if value == "<" and self.peek(":"):
            # Typed collection literal, for example <:>{:}.
            while self.peek() and not self.peek(">"):
                self.take()
            if self.peek(">"):
                close = self.take()
                return Node("typed_literal", token.start, close.end,
                            self.source[token.start:close.end])
        return Node("error", token.start, token.end, value)

    def generic(self, left):
        depth, ending = 0, None
        for index in range(self.pos, len(self.tokens)):
            value = self.tokens[index].value
            if value == "<": depth += 1
            elif value == ">":
                depth -= 1
                if depth == 0:
                    ending = index
                    break
            elif value not in {".", "::", "?", ",", "|", "-", "->", "(", ")"} and self.tokens[index].kind != "word":
                return None
        if ending is None:
            return None
        after = self.tokens[ending + 1].value if ending + 1 < len(self.tokens) else None
        if after not in {None, "(", "[", "{", ".", "?.", "::", ",", ")", "]", "}", "?", "|", "и", "или"}:
            return None
        first = self.tokens[self.pos]
        last = self.tokens[ending]
        self.pos = ending + 1
        return Node("generic", left.start, last.end,
                    children=[left, Node("type_arguments", first.start, last.end,
                                         self.source[first.start:last.end])])

    def delimited(self, closing):
        items = []
        while self.peek() and not self.peek(closing):
            before = self.pos
            items.append(self.expression())
            if self.peek(":"):
                self.take()
                right = self.expression() if self.peek() and not self.peek(closing) else Node(
                    "error", items[-1].end, items[-1].end, "missing value")
                items[-1] = Node("entry", items[-1].start, right.end,
                                 children=[items[-1], right])
            if self.peek(","):
                self.take()
            elif self.pos == before:
                self.take()
            elif self.peek() and not self.peek(closing):
                bad = self.take()
                items.append(Node("error", bad.start, bad.end, bad.value))
        if self.peek(closing):
            self.take()
        else:
            end = self.tokens[-1].end if self.tokens else 0
            items.append(Node("error", end, end, "missing " + closing))
        return items

    def postfix(self, left):
        token = self.take()
        if token.value == "(":
            args = self.delimited(")")
            return Node("call", left.start, self.tokens[self.pos - 1].end,
                        children=[left] + args)
        if token.value == "[":
            indexes = self.delimited("]")
            return Node("index", left.start, self.tokens[self.pos - 1].end,
                        children=[left] + indexes)
        if token.value == "{":
            if left.kind == "name" and left.value == "Запрос":
                depth = 1
                while self.peek() and depth:
                    part = self.take()
                    if part.value == "{": depth += 1
                    elif part.value == "}": depth -= 1
                end = self.tokens[self.pos - 1].end
                return Node("query", left.start, end, children=
                            _interpolations(self.source, token.end, end - 1, "%"))
            if left.kind == "name" and left.value in {"Байты", "Время", "Дата", "ДатаВремя", "Момент", "Ууид", "ЧасовойПояс", "Ресурс"}:
                depth = 1
                while self.peek() and depth:
                    part = self.take()
                    if part.value == "{": depth += 1
                    elif part.value == "}": depth -= 1
                if depth:
                    return Node("error", left.start, self.tokens[-1].end, "missing }")
                end = self.tokens[self.pos - 1].end
                return Node("literal", left.start, end, left.value)
            if self.peek(":"):
                self.take()
            entries = self.delimited("}")
            return Node("typed_literal", left.start, self.tokens[self.pos - 1].end,
                        children=[left] + entries)
        name = self.take()
        if not name or name.kind != "word":
            return Node("error", left.start, token.end, "missing member")
        return Node("member", left.start, name.end, token.value, [left, Node("name", name.start, name.end, name.value)])


def parse_expression(source, start=0, end=None):
    end = len(source) if end is None else end
    tokens = tokenize(source, start, end)
    parsed = _Parser(source, tokens).parse()
    if parsed.kind == "empty": parsed.start = start; parsed.end = end
    return parsed


def _segments(tokens):
    """Split statements at top-level newlines and semicolons."""
    segments, current, depth, lambdas = [], [], [], []
    for index, token in enumerate(tokens):
        value = token.value
        if value == "метод" and index + 1 < len(tokens) and tokens[index + 1].value == "(":
            lambdas.append(0)
        elif lambdas and value in {"если", "пока", "для", "выбор", "попытка", "область"} and (
                not current or current[-1].value == "\n"):
            lambdas[-1] += 1
        if value in {"(", "[", "{"}: depth.append(value)
        elif value in {")", "]", "}"} and depth: depth.pop()
        if value == ";" and lambdas:
            current.append(token)
            if lambdas[-1]: lambdas[-1] -= 1
            else: lambdas.pop()
            if not lambdas and not depth:
                segments.append(current)
                current = []
            continue
        if value == "\n" and lambdas:
            current.append(token)
            continue
        if value == "\n" and not depth:
            following = next((item for item in tokens[index + 1:] if item.value != "\n"), None)
            previous = current[-1] if current else None
            if (following and following.value in {".", "?.", "::", "?", ":", "и", "или", "==", "!=", "<>",
                                                  "<", ">", "<=", ">=", "+", "-", "*", "/", "??"}
                    or previous and previous.value in {"=", "+", "-", "*", "/", "&&", "||", "??",
                                                     ",", ":", ".", "?.", "::", "->", "как"}):
                continue
        if value in {"\n", ";"} and not depth:
            if current: segments.append(current)
            current = []
        else:
            current.append(token)
    if current: segments.append(current)
    return segments


def _statement(source, segment):
    segment = [token for token in segment if token.kind != "newline"]
    first = segment[0]
    if first.value == "иначе" and len(segment) > 1 and segment[1].value == "если":
        expr = _Parser(source, segment[2:]).parse()
        return Node("statement", first.start, segment[-1].end, "иначе если", [expr])
    if first.value in _CONTROL:
        return Node("control", first.start, segment[-1].end, first.value)
    remainder = segment[1:] if first.value in _INTRO else segment
    if first.value == "для":
        iterator = next((i for i, item in enumerate(remainder) if item.value == "из"), None)
        if iterator is not None:
            remainder = remainder[iterator + 1:]
        else:
            equals = next((i for i, item in enumerate(remainder) if item.value == "="), None)
            to = next((i for i, item in enumerate(remainder) if item.value == "по"), None)
            step = next((i for i, item in enumerate(remainder) if item.value == "шаг"), None)
            if equals is not None and to is not None and equals < to:
                parts = [remainder[equals + 1:to],
                         remainder[to + 1:step if step is not None else len(remainder)]]
                if step is not None: parts.append(remainder[step + 1:])
                if not all(parts):
                    return Node("statement", first.start, segment[-1].end, "для",
                                [Node("error", first.start, segment[-1].end, "invalid range")])
                values = [_Parser(source, part).parse() for part in parts]
                return Node("statement", first.start, segment[-1].end, "для",
                            [Node("range", parts[0][0].start, parts[-1][-1].end,
                                  children=values)])
            remainder = []
    if first.value == "поймать":
        remainder = []
    if first.value == "когда":
        if remainder and remainder[0].value == "это":
            checked = _Parser(source, remainder[1:]).parse()
            return Node("statement", first.start, segment[-1].end, "когда",
                        [Node("type_check", remainder[0].start, checked.end,
                              children=[checked])])
        choices = []
        beginning = 0
        for i, item in enumerate(remainder + [Lexeme(",", segment[-1].end, segment[-1].end)]):
            if item.value == ",":
                if i > beginning:
                    choices.append(_Parser(source, remainder[beginning:i]).parse())
                beginning = i + 1
        return Node("statement", first.start, segment[-1].end, "когда", choices)
    if first.value in {"знч", "пер", "исп"}:
        depth, equals = [], None
        for i, item in enumerate(remainder):
            if item.value in {"(", "[", "{", "<"}: depth.append(item.value)
            elif item.value in {")", "]", "}", ">"} and depth: depth.pop()
            elif item.value == "=" and not depth:
                equals = i
                break
        remainder = (remainder[equals + 1:] if equals is not None else
                     remainder if first.value == "исп" else [])
    if not remainder:
        return Node("statement", first.start, segment[-1].end, first.value)
    expr = _Parser(source, remainder, assignment=first.value not in _INTRO).parse()
    return Node("statement", first.start, segment[-1].end,
                first.value if first.value in _INTRO else None, [expr])


def parse_method_body(source, method):
    """Parse every statement expression in a method into a traversable tree."""
    tokens = tokenize(source, method.header_end, method.end)
    statements = [_statement(source, segment) for segment in _segments(tokens)
                  if any(token.kind != "newline" for token in segment)]

    def nest(block, top=False):
        children = []
        nested = sorted((child for child in block.children if child.end is not None),
                        key=lambda child: child.start)
        for statement in statements:
            if not block.start <= statement.start < (block.end or method.end):
                continue
            if any(child.start <= statement.start < child.end for child in nested):
                continue
            children.append(statement)
        for child in nested:
            children.append(nest(child))
        children.sort(key=lambda node: node.start)
        return Node("body" if top else "block", block.start, block.end or method.end,
                    None if top else block.kind, children)

    return nest(method.tree, True)
