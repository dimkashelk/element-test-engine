"""Metadata type grammar; union ordering is semantically irrelevant."""
import re

PRIMITIVES = {"Строка", "Число", "Булево", "Дата", "Время", "ДатаВремя", "Момент",
              "Ууид", "СекретПриложения"}
# Platform-owned metadata types are not present in an exported application or library.
BUILTINS = {"ДвоичныйОбъект", "Пользователи", "Стд::Пользователи::Пользователи"}


def parse_type(text):
    if not isinstance(text, str) or not text or any(c.isspace() for c in text):
        raise ValueError("Тип должен быть непустой строкой без пробелов")
    depth, start, parts = 0, 0, []
    for index, char in enumerate(text):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth < 0:
                raise ValueError("Несбалансированные скобки типа")
        elif char == "|" and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    if depth:
        raise ValueError("Несбалансированные скобки типа")
    parts.append(text[start:])
    if len(parts) > 1:
        nullable = parts[-1] == "?"
        concrete = parts[:-1] if nullable else parts
        if len(concrete) < 2 or any(not p or p.endswith("?") for p in concrete):
            raise ValueError("Некорректный union-type")
        nodes = [parse_type(p) for p in concrete]
        canonical = sorted({n[0] for n in nodes})
        if len(canonical) != len(nodes):
            raise ValueError("Повторяющийся член union-type")
        return "|".join(canonical) + ("|?" if nullable else ""), set().union(*(n[1] for n in nodes))
    nullable = text.endswith("?")
    base = text[:-1] if nullable else text
    if base.startswith("Массив<") and base.endswith(">"):
        inner, refs = parse_type(base[7:-1])
        return f"Массив<{inner}>" + ("?" if nullable else ""), refs
    if not re.fullmatch(r"[^\W\d]\w*(?:::[^\W\d]\w*)*(?:\.Ссылка)?", base, re.UNICODE):
        raise ValueError(f"Некорректный тип: {text}")
    if base == "Неопределено":
        raise ValueError("Используйте nullable-тип вместо Неопределено")
    refs = set() if base in PRIMITIVES else {base.removesuffix(".Ссылка")}
    return text, refs
