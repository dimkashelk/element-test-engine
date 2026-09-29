"""Lexer and a conservative syntax tree for the supported XBSL declarations/blocks."""
from dataclasses import dataclass, field
import re

from .yaml_io import InputError

IDENT = r"[^\W\d]\w*"
_WORD = re.compile(IDENT, re.UNICODE)
_OPENERS = {"если", "пока", "для", "выбор", "попытка"}
_MODIFIERS = {"статический", "экспорт", "асинхронный"}
_BODY_STARTERS = {"знч", "пер", "возврат", "если", "пока", "для", "выбор", "попытка", "выбросить", "исп"}


def mask_noncode(source, *, strings=True):
    """Preserve offsets/newlines while hiding comments and (optionally) strings."""
    output = list(source)
    def blank(a, b):
        output[a:b] = ["\n" if c == "\n" else " " for c in source[a:b]]
    i = 0
    while i < len(source):
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = len(source) if end < 0 else end
            blank(i, end)
            i = end
        elif source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                blank(i, len(source))
                break
            blank(i, end + 2)
            i = end + 2
        elif source[i] == '"':
            start = i
            i += 1
            while i < len(source):
                if source[i] == "\\":
                    i += 2
                elif source[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            if strings:
                blank(start, min(i, len(source)))
        else:
            i += 1
    return "".join(output)


def call_code(source):
    """Mask comments and string text, retaining expressions inside ${...}."""
    output = list(source)
    def blank(a, b):
        output[a:b] = ["\n" if c == "\n" else " " for c in source[a:b]]
    def scan(i, expression=False):
        depth = 0
        while i < len(source):
            if source.startswith("//", i):
                end = source.find("\n", i)
                end = len(source) if end < 0 else end
                blank(i, end)
                i = end
            elif source.startswith("/*", i):
                end = source.find("*/", i + 2)
                end = len(source) if end < 0 else end + 2
                blank(i, end)
                i = end
            elif source[i] == '"':
                blank(i, i + 1)
                i += 1
                while i < len(source):
                    if source[i] == "\\":
                        blank(i, min(i + 2, len(source)))
                        i += 2
                    elif source.startswith("${", i):
                        blank(i, i + 2)
                        i = scan(i + 2, True)
                    elif source[i] == '"':
                        blank(i, i + 1)
                        i += 1
                        break
                    else:
                        blank(i, i + 1)
                        i += 1
            elif expression and source[i] == "}" and depth == 0:
                blank(i, i + 1)
                return i + 1
            else:
                if expression and source[i] == "{": depth += 1
                elif expression and source[i] == "}": depth -= 1
                i += 1
        return i
    scan(0)
    return "".join(output)


@dataclass(frozen=True)
class Token:
    value: str
    start: int
    end: int
    line: int


def lex(code):
    """Token positions refer to the original source, including comments/strings."""
    result = []
    i, line = 0, 1
    while i < len(code):
        c = code[i]
        if c == "\n":
            result.append(Token("\n", i, i + 1, line))
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        else:
            match = _WORD.match(code, i)
            end = match.end() if match else i + (2 if code.startswith("::", i) else 1)
            result.append(Token(code[i:end], i, end, line))
            i = end
    return result


def split_parameters(text):
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char in "<([": depth += 1
        elif char in ">)]": depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    if text.strip(): parts.append(text[start:])
    return parts


@dataclass
class Block:
    kind: str
    start: int
    end: int | None = None
    children: list = field(default_factory=list)


@dataclass
class Method:
    name: str
    start: int
    header_end: int
    end: int | None
    parameters_span: tuple
    return_span: tuple | None
    annotations: list
    line: int
    tree: Block

    def parameters(self, source):
        return split_parameters(source[slice(*self.parameters_span)])

    def return_type(self, source):
        return source[slice(*self.return_span)].strip() if self.return_span else None


def parse_module(source):
    """Build method/block boundaries; malformed declarations leave the index incomplete."""
    tokens = lex(mask_noncode(source))
    methods, imports, errors = [], [], []
    active = None
    stack = []
    pending = []
    i = 0
    def next_line(index):
        while index < len(tokens) and tokens[index].value != "\n": index += 1
        return index
    while i < len(tokens):
        token = tokens[i]
        if token.value == "\n":
            i += 1
            continue
        beginning = i == 0 or tokens[i-1].value == "\n"
        if beginning and active is None and token.value == "@":
            j = next_line(i)
            if i+1 < j and _WORD.fullmatch(tokens[i+1].value): pending.append(tokens[i+1].value)
            i = j
            continue
        if beginning and active is None and token.value in _MODIFIERS:
            i = next_line(i)
            continue
        if beginning and active is None and token.value == "импорт":
            j = next_line(i)
            imports.append(source[token.end:tokens[j].start if j < len(tokens) else len(source)].strip().rstrip(";"))
            pending = []
            i = j
            continue
        if beginning and token.value == "метод":
            if active is not None:
                errors.append(f"Строка {token.line}: предыдущий метод не закрыт")
                active = None
                stack = []
            try:
                name = tokens[i+1]
                opening = tokens[i+2]
                if not _WORD.fullmatch(name.value) or opening.value != "(": raise ValueError
                depth, j = 1, i+3
                while j < len(tokens) and depth:
                    if tokens[j].value == "(": depth += 1
                    elif tokens[j].value == ")": depth -= 1
                    j += 1
                if depth: raise ValueError
                close = tokens[j-1]
                k = next_line(j)
                rest = tokens[j:k]
                ret = None
                if rest:
                    if rest[0].value != ":": raise ValueError
                    type_start = j + 1
                    while type_start < len(tokens) and tokens[type_start].value == "\n":
                        type_start += 1
                    if (type_start == len(tokens) or not _WORD.fullmatch(tokens[type_start].value)
                            or tokens[type_start].value in _BODY_STARTERS):
                        raise ValueError
                    depth = 0
                    type_end = type_start
                    while type_end < len(tokens):
                        value = tokens[type_end].value
                        if value == "\n" and depth == 0 and tokens[type_end - 1].value not in {"<", ",", "|", ".", "::"}:
                            break
                        if value == "<": depth += 1
                        elif value == ">": depth -= 1
                        if depth < 0 or value == ";": raise ValueError
                        type_end += 1
                    if depth or type_end == type_start: raise ValueError
                    ret = (tokens[type_start].start, tokens[type_end - 1].end)
                    k = type_end
                tree = Block("метод", token.start)
                active = Method(name.value, token.start, tokens[k].end if k < len(tokens) else len(source), None,
                                (opening.end, close.start), ret, pending, token.line, tree)
                methods.append(active)
                stack = [tree]
                pending = []
                i = k+1
                continue
            except (IndexError, ValueError):
                errors.append(f"Строка {token.line}: некорректная сигнатура метода")
                pending = []
        if active is not None and beginning:
            if token.value in _OPENERS:
                block = Block(token.value, token.start)
                stack[-1].children.append(block)
                stack.append(block)
            elif token.value == ";":
                stack[-1].end = tokens[next_line(i)].end if next_line(i) < len(tokens) else len(source)
                stack.pop()
                if not stack:
                    active.end = stack_end = tokens[next_line(i)].end if next_line(i) < len(tokens) else len(source)
                    active.tree.end = stack_end
                    active = None
                i = next_line(i)
                continue
        elif active is None and beginning:
            pending = []
        i += 1
    if active is not None:
        errors.append(f"Строка {active.line}: метод не закрыт")
    return methods, imports, errors


def index_module(path, root):
    source = path.read_text(encoding="utf-8-sig")
    parsed, imports, errors = parse_module(source)
    methods = []
    for method in parsed:
        if method.end is None: continue
        parameters = []
        for parameter in method.parameters(source):
            name, separator, type_name = parameter.partition(":")
            parameters.append({"name": name.strip(), "type": type_name.split("=")[0].strip() if separator else None})
        methods.append({"name": method.name, "annotations": method.annotations, "parameters": parameters,
                        "returnType": method.return_type(source), "line": method.line})
    namespace = "::".join(path.relative_to(root).parts[:-1])
    return {"name": path.stem, "namespace": namespace, "sourceFile": path.relative_to(root).as_posix(),
            "moduleType": "object" if path.stem.endswith(".Объект") else "module",
            "imports": imports, "methods": methods, "indexComplete": not errors,
            "parseErrors": errors}


def method_calls(source, method):
    """Return syntactic call sites, retaining interpolation expressions."""
    body = source[method.header_end:method.end]
    tokens = [t for t in lex(call_code(body)) if t.value != "\n"]
    result = []
    for i, token in enumerate(tokens):
        if i+1 >= len(tokens) or tokens[i+1].value != "(" or not _WORD.fullmatch(token.value):
            continue
        before = tokens[i-1].value if i else None
        if before == "." and i >= 2:
            owner = tokens[i-2].value
            j = i-2
            while j >= 2 and tokens[j-1].value == "::":
                owner = tokens[j-2].value + "::" + owner
                j -= 2
            result.append((None if owner == "этот" else owner, token.value))
        elif before not in {"::", "новый", ".", ":"}:
            result.append((None, token.value))
    return result
