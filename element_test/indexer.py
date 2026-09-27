"""Conservative signature index, not a compiler or a behavioral validator."""
import re

IDENT = r"[^\W\d]\w*"
METHOD = re.compile(rf"\bметод\s+({IDENT})\s*\(([^()]*)\)\s*(?::\s*([^\n;]+))?", re.UNICODE)


def mask_noncode(source, *, strings=True):
    pattern = r'//[^\n]*|/\*[\s\S]*?\*/'
    if strings:
        pattern += r'|"(?:\\.|[^"\\])*"'
    return re.sub(pattern, lambda match: "".join("\n" if c == "\n" else " " for c in match[0]), source)


def call_code(source):
    """Mask literals/comments while preserving code inside string interpolation."""
    output = list(source)

    def blank(start, end):
        output[start:end] = ["\n" if c == "\n" else " " for c in source[start:end]]

    def scan(index, expression=False):
        depth = 0
        while index < len(source):
            if source.startswith("//", index):
                end = source.find("\n", index)
                end = len(source) if end < 0 else end
                blank(index, end)
                index = end
            elif source.startswith("/*", index):
                end = source.find("*/", index + 2)
                if end < 0:
                    end = len(source) - 2
                blank(index, end + 2)
                index = end + 2
            elif source[index] == '"':
                blank(index, index + 1)
                index += 1
                while index < len(source):
                    if source[index] == "\\":
                        blank(index, min(index + 2, len(source)))
                        index += 2
                    elif source.startswith("${", index):
                        blank(index, index + 2)
                        index = scan(index + 2, True)
                    elif source[index] == '"':
                        blank(index, index + 1)
                        index += 1
                        break
                    else:
                        blank(index, index + 1)
                        index += 1
            elif expression and source[index] == "}" and depth == 0:
                blank(index, index + 1)
                return index + 1
            else:
                if expression and source[index] == "{":
                    depth += 1
                elif expression and source[index] == "}":
                    depth -= 1
                index += 1
        return index

    scan(0)
    return "".join(output)


def split_parameters(text):
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char in "<([":
            depth += 1
        elif char in ">)]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    if text.strip():
        parts.append(text[start:])
    return parts


def index_module(path, root):
    source = path.read_text(encoding="utf-8-sig")
    code = mask_noncode(source)
    methods = []
    for match in METHOD.finditer(code):
        prefix = code[:match.start()]
        # Only immediately preceding annotation/modifier lines belong to this method.
        annotations = []
        for line in reversed(prefix.splitlines()):
            if not line.strip():
                continue
            if line.strip().startswith("@"):
                annotations[:0] = re.findall(rf"@({IDENT})", line)
            elif line.strip() in {"статический", "экспорт", "асинхронный"}:
                continue
            else:
                break
        parameters = []
        for parameter in split_parameters(match[2]):
            name, separator, type_name = parameter.partition(":")
            parameters.append({"name": name.strip(), "type": type_name.split("=")[0].strip() if separator else None})
        methods.append({"name": match[1], "annotations": annotations, "parameters": parameters,
                        "returnType": match[3].strip() if match[3] else None,
                        "line": source[:match.start()].count("\n") + 1})
    namespace = "::".join(path.relative_to(root).parts[:-1])
    return {"name": path.stem, "namespace": namespace, "sourceFile": path.relative_to(root).as_posix(),
            "moduleType": "object" if path.stem.endswith(".Объект") else "module",
            "imports": re.findall(r"^\s*импорт\s+([^\n;]+)", code, re.MULTILINE), "methods": methods,
            "indexComplete": len(re.findall(r"\bметод\s+", code)) == len(methods)}
