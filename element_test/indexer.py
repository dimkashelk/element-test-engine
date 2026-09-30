"""Lexer and a conservative syntax tree for the supported XBSL declarations/blocks."""
from dataclasses import dataclass, field
import re

from .expression_ast import Node, _segments, parse_expression, parse_method_body, string_end, tokenize
from .yaml_io import InputError

IDENT = r"[^\W\d]\w*"
_WORD = re.compile(IDENT, re.UNICODE)
_OPENERS = {"если", "пока", "для", "выбор", "попытка", "область"}
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
            i = string_end(source, i)
            if strings:
                blank(start, min(i, len(source)))
        elif source[i] == "'":
            start = i
            i += 1
            while i < len(source):
                if source[i] == "\\": i += 2
                elif source[i] == "'":
                    i += 1
                    break
                else: i += 1
            if strings: blank(start, min(i, len(source)))
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
            elif source[i] == "'":
                start = i
                i += 1
                while i < len(source):
                    if source[i] == "\\": i += 2
                    elif source[i] == "'":
                        i += 1
                        break
                    else: i += 1
                blank(start, min(i, len(source)))
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
    expression_tree: Node | None = None
    header_tree: Node | None = None

    def parameters(self, source):
        return split_parameters(source[slice(*self.parameters_span)])

    def return_type(self, source):
        return source[slice(*self.return_span)].strip() if self.return_span else None


@dataclass(frozen=True)
class CallExpression:
    """A call in the expression tree, with source offsets for lossless rewriting.

    ``kind`` is static, member, or computed. A member receiver is kept as a
    complete path; a computed receiver contains a call or an index operation.
    The latter cannot be resolved from imports alone.
    """
    kind: str
    name: str | None
    receiver: str | None
    start: int
    end: int
    receiver_start: int | None = None
    receiver_end: int | None = None


def parse_module(source):
    """Build method/block boundaries; malformed declarations leave the index incomplete."""
    tokens = lex(mask_noncode(source))
    methods, imports, errors = [], [], []
    active = None
    stack = []
    expression_depth = []
    lambda_blocks = []
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
        if beginning and token.value == "метод" and not (
                active is not None and i + 1 < len(tokens) and tokens[i + 1].value == "("):
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
                expression_depth = []
                lambda_blocks = []
                pending = []
                i = k+1
                continue
            except (IndexError, ValueError):
                errors.append(f"Строка {token.line}: некорректная сигнатура метода")
                pending = []
        if active is not None and beginning:
            if token.value in _OPENERS and not expression_depth:
                block = Block(token.value, token.start)
                stack[-1].children.append(block)
                stack.append(block)
            elif token.value == ";" and lambda_blocks and len(stack) == lambda_blocks[-1]:
                lambda_blocks.pop()
                i += 1
                continue
            elif token.value == ";" and not expression_depth:
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
        if active is not None:
            if token.value == "метод" and i + 1 < len(tokens) and tokens[i + 1].value == "(":
                lambda_blocks.append(len(stack))
            if token.value in {"(", "[", "{"}:
                expression_depth.append(token.value)
            elif token.value in {")", "]", "}"} and expression_depth:
                expression_depth.pop()
        i += 1
    if active is not None:
        errors.append(f"Строка {active.line}: метод не закрыт")
    for method in methods:
        if method.end is not None:
            method.header_tree = _header_expressions(source, method)
            method.expression_tree = parse_method_body(source, method)
            seen = set()
            for node in list(method.header_tree.walk()) + list(method.expression_tree.walk()):
                if node.kind != "error": continue
                line = source.count("\n", 0, node.start) + 1
                if line not in seen:
                    errors.append(f"Строка {line}: некорректное выражение ({node.value})")
                    seen.add(line)
    return methods, imports, errors


def _header_expressions(source, method):
    """Default parameter values, without confusing type commas with separators."""
    start, end = method.parameters_span
    tokens = tokenize(source, start, end)
    parts, current, depth = [], [], []
    for token in tokens:
        if token.value in {"(", "[", "{", "<"}: depth.append(token.value)
        elif token.value in {")", "]", "}", ">"} and depth: depth.pop()
        if token.value == "," and not depth:
            if current: parts.append(current)
            current = []
        else: current.append(token)
    if current: parts.append(current)
    defaults = []
    for part in parts:
        nesting = []
        for token in part:
            if token.value in {"(", "[", "{", "<"}: nesting.append(token.value)
            elif token.value in {")", "]", "}", ">"} and nesting: nesting.pop()
            elif token.value == "=" and not nesting:
                defaults.append(Node("default", token.start, part[-1].end, children=
                                     [parse_expression(source, token.end, part[-1].end)]))
                break
    return Node("header", method.start, method.header_end, children=defaults)


def parse_module_tree(source):
    """Return the module's expression tree, including methods and constants."""
    methods, imports, errors = parse_module(source)
    children = [Node("method", method.start, method.end or method.header_end,
                     method.name, [child for child in (method.header_tree, method.expression_tree)
                                   if child is not None]) for method in methods]
    gaps = []
    previous = 0
    for method in methods:
        gaps.append((previous, method.start))
        previous = method.end or method.header_end
    gaps.append((previous, len(source)))
    for start, end in gaps:
        for segment in _segments(tokenize(source, start, end)):
            tokens = [token for token in segment if token.kind != "newline"]
            if not tokens: continue
            if tokens[0].value == "конст":
                equals = next((index for index, token in enumerate(tokens) if token.value == "="), None)
                if equals is not None and equals + 1 < len(tokens):
                    expression = parse_expression(source, tokens[equals + 1].start, tokens[-1].end)
                    children.append(Node("constant", tokens[0].start, tokens[-1].end,
                                         children=[expression]))
            elif tokens[0].value == "@":
                marks = [index for index, token in enumerate(tokens) if token.value == "@"]
                for position, marker in enumerate(marks):
                    part = tokens[marker + 1:marks[position + 1] if position + 1 < len(marks) else len(tokens)]
                    if part:
                        annotation = parse_expression(source, part[0].start, part[-1].end)
                        children.append(Node("annotation", tokens[marker].start, part[-1].end,
                                             children=[annotation]))
    children.sort(key=lambda node: node.start)
    tree = Node("module", 0, len(source), children=children)
    reported = {re.search(r"Строка (\d+)", error).group(1) for error in errors
                if re.search(r"Строка (\d+)", error)}
    for node in tree.walk():
        if node.kind == "error":
            line = str(source.count("\n", 0, node.start) + 1)
            if line not in reported:
                errors.append(f"Строка {line}: некорректное выражение ({node.value})")
                reported.add(line)
    return tree, methods, imports, errors


def index_module(path, root):
    source = path.read_text(encoding="utf-8-sig")
    _, parsed, imports, errors = parse_module_tree(source)
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


def method_call_expressions(source, method):
    """Extract call sites from the same expression tree as other body syntax."""
    trees = (method.header_tree, method.expression_tree or parse_method_body(source, method))
    result = []

    def path(node):
        if node.kind == "name": return node.value
        if node.kind == "member" and node.value in {".", "::"}:
            left = path(node.children[0])
            return left + node.value + node.children[1].value if left else None
        return None

    for node in (part for tree in trees if tree is not None for part in tree.walk()):
        if node.kind == "reference":
            owner, dot, name = node.value.rpartition(".")
            result.append(CallExpression("reference", name if dot else node.value,
                                         owner if dot else None, node.start, node.end,
                                         node.start + 1 if dot else None,
                                         node.end - len(name) - 1 if dot else None))
        if node.kind != "call": continue
        callee = node.children[0]
        if callee.kind == "generic":
            callee = callee.children[0]
        if callee.kind == "new": continue
        if callee.kind == "reference": continue
        opening = source.find("(", node.children[0].end, node.end)
        end = opening + 1 if opening >= 0 else node.end
        if callee.kind == "member" and callee.value in {".", "?."}:
            receiver_node, name_node = callee.children
            receiver = path(receiver_node)
            result.append(CallExpression("member" if receiver else "computed",
                                         name_node.value, receiver, callee.start, end,
                                         receiver_node.start, receiver_node.end))
        elif callee.kind == "name":
            result.append(CallExpression("static", callee.value, None, callee.start, end))
        else:
            result.append(CallExpression("computed", None, None, callee.start, end))
    return sorted(result, key=lambda call: (call.end, call.start))


def method_call_sites(source, method):
    """Return statically spelled call owners, names and owner spans."""
    return [(call.receiver if call.receiver != "этот" else None, call.name,
             call.receiver_start, call.receiver_end)
            for call in method_call_expressions(source, method) if call.kind != "computed"]


def method_local_bindings(source, method):
    """Names and lexical visibility intervals for parameters and locals."""
    bindings = {part.partition(":")[0].strip(): [(method.header_end, method.end)]
                for part in method.parameters(source) if part.partition(":")[0].strip()}
    body = source[method.header_end:method.end]
    tokens = lex(call_code(body))

    def scope_end(position, block):
        for child in block.children:
            if child.end is not None and child.start <= position < child.end:
                return scope_end(position, child)
        return block.end or method.end

    for index, token in enumerate(tokens[:-1]):
        if token.value in {"пер", "знч", "для", "поймать"} and _WORD.fullmatch(tokens[index + 1].value):
            name = tokens[index + 1].value
            declared_at = method.header_end + tokens[index + 1].start
            start = declared_at
            if token.value in {"пер", "знч"}:
                depth = 0
                has_initializer = False
                for part in tokens[index + 2:]:
                    if part.value in {"(", "[", "{", "<"}: depth += 1
                    elif part.value in {")", "]", "}", ">"}: depth = max(depth - 1, 0)
                    elif part.value == "=" and depth == 0: has_initializer = True
                    elif part.value in {"\n", ";"} and depth == 0:
                        if has_initializer:
                            start = method.header_end + part.end
                        break
            bindings.setdefault(name, []).append((start, scope_end(declared_at, method.tree)))
    # Lambda parameters are visible in the expression after `->`, not in the
    # surrounding method. Parenthesized functional types have no value scope.
    for index, token in enumerate(tokens):
        if token.value != "(":
            continue
        depth, close = 1, index + 1
        while close < len(tokens) and depth:
            if tokens[close].value == "(": depth += 1
            elif tokens[close].value == ")": depth -= 1
            close += 1
        if (depth or close + 1 >= len(tokens) or tokens[close].value != "-"
                or tokens[close + 1].value != ">"):
            continue
        if index > 0 and tokens[index - 1].value == ":":
            continue
        names = []
        at_start = True
        nested = 0
        for parameter in tokens[index + 1:close - 1]:
            if parameter.value in {"(", "<"}: nested += 1
            elif parameter.value in {")", ">"}: nested -= 1
            elif parameter.value == "," and nested == 0: at_start = True
            elif at_start and _WORD.fullmatch(parameter.value):
                names.append(parameter.value)
                at_start = False
        start = method.header_end + tokens[close + 1].end
        end = method.end
        nesting = 0
        for body_token in tokens[close + 2:]:
            if body_token.value in {"(", "[", "{"}: nesting += 1
            elif body_token.value in {")", "]", "}"}:
                if nesting == 0:
                    end = method.header_end + body_token.start
                    break
                nesting -= 1
            elif body_token.value in {"\n", ",", ";"} and nesting == 0:
                end = method.header_end + body_token.start
                break
        for name in names:
            bindings.setdefault(name, []).append((start, end))
    for index in range(len(tokens) - 2):
        if (not _WORD.fullmatch(tokens[index].value) or tokens[index + 1].value != "-"
                or tokens[index + 2].value != ">"):
            continue
        if index > 0 and tokens[index - 1].value in {":", "::", "."}:
            continue
        start = method.header_end + tokens[index + 2].end
        end = method.end
        for body_token in tokens[index + 3:]:
            if body_token.value in {"\n", ";"}:
                end = method.header_end + body_token.start
                break
        bindings.setdefault(tokens[index].value, []).append((start, end))
    return bindings


def method_binding_visible(bindings, name, offset):
    return any(start <= offset < end for start, end in bindings.get(name, ()) if end is not None)


def method_local_callable_bindings(source, method):
    """Local values definitely initialized from a lambda or method reference.

    A later assignment makes the value unknown. This deliberately does not
    infer callbacks passed through parameters or object properties.
    """
    body = source[method.header_end:method.end]
    tokens = lex(call_code(body))
    safe = set()
    for i, token in enumerate(tokens):
        if token.value not in {"пер", "знч"} or i + 1 >= len(tokens):
            continue
        name = tokens[i + 1].value
        if not _WORD.fullmatch(name):
            continue
        # Inspect the declaration through its first top-level assignment.
        depth = 0
        j = i + 2
        while j < len(tokens) and tokens[j].value not in {"\n", ";", "пер", "знч", "возврат"}:
            value = tokens[j].value
            if value in {"(", "<"}: depth += 1
            elif value == ")" or (value == ">" and (j == 0 or tokens[j - 1].value != "-")):
                depth -= 1
            elif value == "=" and depth == 0:
                if j + 1 < len(tokens) and tokens[j + 1].value == "&":
                    safe.add(name)
                elif j + 1 < len(tokens) and tokens[j + 1].value == "(":
                    nesting, k = 1, j + 2
                    while k < len(tokens) and nesting:
                        if tokens[k].value == "(": nesting += 1
                        elif tokens[k].value == ")": nesting -= 1
                        k += 1
                    if k + 1 < len(tokens) and tokens[k].value == "-" and tokens[k + 1].value == ">":
                        safe.add(name)
                elif (j + 3 < len(tokens) and _WORD.fullmatch(tokens[j + 1].value)
                      and tokens[j + 2].value == "-" and tokens[j + 3].value == ">"):
                    safe.add(name)
                break
            j += 1
    for i, token in enumerate(tokens[:-1]):
        if token.value in safe and tokens[i + 1].value == "=" and (i == 0 or tokens[i - 1].value not in {"пер", "знч"}):
            safe.discard(token.value)
    return safe


def method_calls(source, method):
    """Return syntactic call sites, retaining interpolation expressions."""
    return [(owner, name) for owner, name, _, _ in method_call_sites(source, method)]
