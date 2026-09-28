"""Bounded YAML loading with duplicate-key rejection and exact scalar types."""
from pathlib import Path
import yaml


class InputError(ValueError):
    pass


class InvalidTestError(InputError):
    """The teacher's check cannot be executed as specified."""


class UnsupportedSyntaxError(InputError):
    """The student's XBSL cannot be safely interpreted by this adapter."""


class Loader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise InputError("Ключ YAML должен быть строкой")
        if key in result:
            raise InputError(f"Повторяющийся ключ YAML: {key}")
        # Compatibility versions are lexical values, including 9.10 versus 9.1.
        if key in {"РежимСовместимости", "Версия"} and isinstance(value_node, yaml.ScalarNode):
            result[key] = loader.construct_scalar(value_node)
        else:
            result[key] = loader.construct_object(value_node, deep=deep)
    return result


Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
# Dates remain JSON-safe strings.
Loader.add_constructor("tag:yaml.org,2002:timestamp", lambda loader, node: loader.construct_scalar(node))


def load_yaml(path: Path, *, metadata=False):
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise InputError(f"Слишком большой YAML: {path.name}")
        text = path.read_text(encoding="utf-8-sig")
        # Metadata may contain aliases; reject them to avoid cycles and expansion bombs.
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
               for token in yaml.scan(text)):
            raise InputError(f"Якоря и ссылки YAML пока не поддерживаются: {path.name}")
        value = yaml.load(text, Loader=Loader)
        if value is None and path.name == "Подсистема.yaml":
            value = {}
        if not isinstance(value, dict):
            raise InputError(f"Ожидался объект YAML: {path.name}")
        if metadata:
            return normalize(value)
        return value
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InputError(f"Ошибка чтения {path.name}: {exc}") from exc


def normalize(value):
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if value == "Истина":
        return True
    if value == "Ложь":
        return False
    return value
