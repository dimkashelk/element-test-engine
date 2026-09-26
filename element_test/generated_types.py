"""Lazy YAML → SBSL data contracts. No persistence or platform API emulation."""
import re

from .indexer import IDENT, split_parameters
from .model import resolve
from .yaml_io import InputError

SCALARS = {"Строка", "Число", "Булево", "Дата", "ДатаВремя", "Момент"}


class ProjectTypes:
    def __init__(self, model, namespace=""):
        self.model, self.namespace = model, namespace
        self.definitions, self.fields, self.owners = {}, {}, {}
        self.active = set()
        self.enums, self.methods, self.method_dependencies = {}, {}, {}
        self.enum_types = {}

    def sbsl_type(self, type_name):
        """Enums live in script modules; constants retain their XBSL spelling."""
        return re.sub(IDENT, lambda m: self.enum_types.get(m[0], m[0]), type_name)

    def require(self, type_name, namespace=None):
        namespace = self.namespace if namespace is None else namespace
        if type_name.endswith("?"):
            self.require(type_name[:-1], namespace)
            return
        if type_name in SCALARS:
            return
        generic = re.fullmatch(r"(Массив|Соответствие)<(.+)>", type_name)
        if generic:
            arguments = split_parameters(generic[2])
            if len(arguments) != (1 if generic[1] == "Массив" else 2):
                raise InputError(f"Некорректный тип: {type_name}")
            for argument in arguments:
                self.require(argument.strip(), namespace)
            return
        if re.fullmatch(IDENT, type_name):
            matches = resolve(self.model["elements"], type_name, namespace)
            if len(matches) != 1 or matches[0]["elementType"] != "Перечисление":
                raise InputError(f"Перечисление {type_name} отсутствует, неоднозначно или не поддерживается")
            element = matches[0]
            self.claim_owner(type_name, element)
            if type_name in self.definitions:
                return
            members = element["properties"].get("Элементы", [])
            names = [m["Имя"] for m in members]
            if (not names or len(set(names)) != len(names)
                    or any(not re.fullmatch(IDENT, n) for n in names)
                    or sum(m.get("ПоУмолчанию", False) is True for m in members) > 1):
                raise InputError(f"Некорректные элементы перечисления {type_name}")
            self.enums[type_name] = names
            variant = "Значение"
            while variant in names:
                variant += "_"
            self.enum_types[type_name] = type_name + "." + variant
            lines = ["@Глобально", f"перечисление {variant}",
                     ",\n".join("    " + m["Имя"] + (" умолчание" if m.get("ПоУмолчанию") is True else "")
                                for m in members), ";"]
            for name in names:
                lines += ["@Глобально", f"конст {name} = {variant}.{name}"]
            self.definitions[type_name] = "\n".join(lines) + "\n"
            return
        if not re.fullmatch(rf"{IDENT}\.{IDENT}", type_name):
            raise InputError(f"Генерация типа пока не поддерживается: {type_name}")
        owner, variant = type_name.split(".")
        matches = resolve(self.model["elements"], owner, namespace)
        if len(matches) != 1 or matches[0]["elementType"] not in {"Документ", "Справочник"}:
            raise InputError(f"Объект типа {type_name} отсутствует, неоднозначен или не поддерживается")
        element = matches[0]
        self.claim_owner(owner, element)
        if type_name in self.active:
            raise InputError(f"Циклическая зависимость типов: {type_name}")
        if type_name in self.definitions:
            return
        self.active.add(type_name)
        if variant == "Ссылка":
            fields = [{"Имя": "Идентификатор", "Тип": "Строка"}]
        elif variant in {"Объект", "Данные"}:
            fields = list(element["properties"].get("Реквизиты", []))
            fields += [{"Имя": t["Имя"], "Тип": f"Массив<{owner}.{t['Имя']}>"}
                       for t in element["properties"].get("ТабличныеЧасти", [])]
        else:
            tables = [t for t in element["properties"].get("ТабличныеЧасти", []) if t["Имя"] == variant]
            if len(tables) != 1:
                raise InputError(f"Табличная часть {type_name} отсутствует или неоднозначна")
            fields = tables[0].get("Реквизиты", [])
        lines, names = ["@Глобально", f"структура {variant}"], set()
        for field in fields:
            name, field_type = field["Имя"], field.get("Тип")
            if not re.fullmatch(IDENT, name) or name in names or not field_type:
                raise InputError(f"Некорректное поле сгенерированного типа {type_name}: {name}")
            names.add(name)
            self.require(field_type, element["namespace"])
            default = ""
            if "ЗначениеПоУмолчанию" in field:
                default = " = " + self.literal(field["ЗначениеПоУмолчанию"], field_type)
            lines.append(f"    пер {name}: {self.sbsl_type(field_type)}{default}")
        self.active.remove(type_name)
        self.fields[type_name] = fields
        self.definitions[type_name] = "\n".join(lines + [";", ""])

    def claim_owner(self, owner, element):
        identity = (element["namespace"], owner)
        if owner in self.owners and self.owners[owner] != identity:
            raise InputError(f"Конфликт кратких имён сгенерированных типов: {owner}")
        self.owners[owner] = identity

    def attach_method(self, type_name, method, signature_types):
        self.require(type_name)
        self.methods[type_name] = "@Глобально\n" + method
        self.method_dependencies[type_name] = signature_types

    def literal(self, value, type_name):
        from .runtime import sbsl_literal
        if type_name.endswith("?"):
            return "Неопределено" if value is None else self.literal(value, type_name[:-1])
        if type_name in {"Строка", "Число", "Булево"}:
            return sbsl_literal(value, type_name)
        if type_name in self.enums:
            if not isinstance(value, str):
                raise InputError(f"Ожидалось имя элемента {type_name}")
            name = value.removeprefix(type_name + ".")
            if name not in self.enums[type_name]:
                raise InputError(f"Неизвестный элемент {type_name}: {value}")
            return f"{type_name}.{name}"
        if type_name.startswith("Массив<") and type_name.endswith(">"):
            if not isinstance(value, list):
                raise InputError(f"Ожидался массив для {type_name}")
            inner = type_name[7:-1].strip()
            # Explicit constructor keeps the element type even for an empty array.
            return f"новый {self.sbsl_type(type_name)}([" + ", ".join(self.literal(v, inner) for v in value) + "])"
        if type_name in self.fields:
            if not isinstance(value, dict):
                raise InputError(f"Ожидался объект для {type_name}")
            fields = {f["Имя"]: f["Тип"] for f in self.fields[type_name]}
            if set(value) - fields.keys():
                raise InputError(f"Неизвестные поля {type_name}: {sorted(set(value) - fields.keys())}")
            arguments = [f"{key} = {self.literal(v, fields[key])}" for key, v in value.items()]
            return f"новый {type_name}(" + ", ".join(arguments) + ")"
        raise InputError(f"Вход для типа {type_name} пока не поддерживается")

    def write(self, directory):
        grouped = {}
        for name, definition in sorted(self.definitions.items()):
            owner = name.split(".")[0]
            if name in self.methods:
                definition = definition.rsplit(";", 1)[0] + self.methods[name] + "\n;\n"
            grouped.setdefault(owner, []).append(definition)
        graph = {}
        for owner in grouped:
            dependencies = set()
            for name, fields in self.fields.items():
                if name.split(".")[0] == owner:
                    for field in fields:
                        dependencies.update(re.findall(rf"({IDENT})\.", self.sbsl_type(field["Тип"])))
            for name, types in self.method_dependencies.items():
                if name.split(".")[0] == owner:
                    for type_name in types:
                        dependencies.update(re.findall(rf"({IDENT})\.", self.sbsl_type(type_name)))
            graph[owner] = dependencies - {owner}
        visited, active = set(), set()

        def visit(owner):
            if owner in active:
                raise InputError(f"Циклическая зависимость SBSL-модулей: {owner}")
            if owner in visited:
                return
            active.add(owner)
            for dependency in graph[owner]:
                visit(dependency)
            active.remove(owner)
            visited.add(owner)

        for owner in grouped:
            visit(owner)
        for owner, definitions in grouped.items():
            imports = [f"#требуется {dep}.sbsl" for dep in sorted(graph[owner])]
            (directory / f"{owner}.sbsl").write_text("\n".join(imports + definitions), encoding="utf-8")
        return "\n".join(f"#требуется {owner}.sbsl" for owner in sorted(grouped))
