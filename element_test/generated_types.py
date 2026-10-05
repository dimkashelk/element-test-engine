"""Lazy YAML → SBSL contracts and explicit, per-check object API fakes."""
import re
from datetime import datetime

from .indexer import IDENT, split_parameters
from .resolution import import_specs, resolve_symbols, visible_from
from .yaml_io import InputError, InvalidTestError

SCALARS = {"Строка", "Число", "Булево", "Дата", "Время", "ДатаВремя", "Момент", "Ууид"}


def union_members(type_name):
    depth, start, parts = 0, 0, []
    for i, char in enumerate(type_name):
        depth += (char == '<') - (char == '>')
        if char == '|' and depth == 0:
            parts.append(type_name[start:i])
            start = i + 1
    return parts + [type_name[start:]] if parts else []


class ProjectTypes:
    def __init__(self, model, namespace="", imports=()):
        self.model, self.namespace, self.imports = model, namespace, imports
        self.definitions, self.fields, self.owners = {}, {}, {}
        self.active = set()
        self.enums, self.methods, self.method_dependencies = {}, {}, {}
        self.enum_types = {}
        self.reference_mocks = {}
        self.local_structures = {}
        self.local_source = None
        self.current_source = None
        self.required_structures = {}
        self.local_by_source = {}
        self.local_names = {}
        self.canonical_elements = {}
        self.rename_collisions = False
        self.inline_locals = True
        self.reference_id_type = "Строка"
        self.platform_type_aliases = {}
        self.declarative_read = False
        self.produced_types = {}
        self.query_results = False

    def resolve(self, name, namespace=None):
        if name in self.canonical_elements:
            return [self.canonical_elements[name]]
        namespace = self.namespace if namespace is None else namespace
        imports = self.imports if namespace == self.namespace else ()
        prefix = next((prefix for prefix in self.model.get("_libraryPrefixes", [])
                       if namespace == prefix or namespace.startswith(prefix + "::")), None)
        elements = [item for item in self.model["elements"] if visible_from(item, namespace, prefix)]
        matches = resolve_symbols(elements, name, namespace, imports,
                                  self.model.get("properties"))
        if not matches and "::" in name:
            for prefix in self.model.get("_libraryPrefixes", []):
                if namespace == prefix or namespace.startswith(prefix + "::"):
                    return resolve_symbols(elements, prefix + "::" + name,
                                           namespace, imports, self.model.get("properties"))
        return matches

    def canonical_type(self, type_name):
        """Resolve explicit project namespaces before shortening SBSL names."""
        if not isinstance(type_name, str):
            raise InputError("Имя типа должно быть строкой")
        if type_name.strip() in self.platform_type_aliases:
            return self.platform_type_aliases[type_name.strip()]
        from .query_results import result_type
        type_name = re.sub(r'(?<![\w:])(?:Стд::БазаДанных::)?РезультатЗапроса<([^<>]+)>',
            lambda m: result_type(self.canonical_type(m[1]), self), type_name)
        for path, alias in import_specs(self.imports):
            if alias and resolve_symbols(self.model["elements"], path, self.namespace,
                                         self.imports, self.model.get("properties")):
                type_name = re.sub(rf"(?<![\w:]){re.escape(alias)}(?=\.|[<>,|?]|$)",
                                   path, type_name)
        pattern = rf"{IDENT}(?:::{IDENT})*(?:\.{IDENT})?"

        def shorten(match):
            token = match[0]
            produced = getattr(self, 'produced_types', {}).get((self.current_source, token))
            if produced:
                return produced
            if token in self.definitions:
                return token
            if token in self.platform_type_aliases:
                return self.platform_type_aliases[token]
            owner, dot, variant = token.partition(".")
            if owner in self.canonical_elements:
                element = self.canonical_elements[owner]
                return token + '.Значение' if element['elementType'] == 'Структура' and not dot else token
            local = self.local_by_source.get(self.current_source, {})
            if owner in local:
                key = (self.current_source, owner)
                if key not in self.local_names:
                    from hashlib import sha256
                    # Keep the historical spelling for the sole entry-local declaration.
                    if self.current_source == self.local_source and self.inline_locals:
                        self.local_names[key] = owner
                    else:
                        self.local_names[key] = "ТестТип" + sha256((str(key)).encode()).hexdigest()[:16] + ".Значение"
                return self.local_names[key] + (dot + variant if dot else "")
            if owner in SCALARS or owner in {"Массив", "ЧитаемыйМассив", "Обходимое", "Соответствие", "ЧитаемоеСоответствие", "ничто"}:
                return token
            matches = self.resolve(owner)
            if len(matches) != 1:
                if "::" in owner:
                    raise InputError(f"Квалифицированный тип отсутствует или неоднозначен: {token}")
                return token
            element = matches[0]
            duplicates = [e for e in self.model['elements'] if e['name'] == element['name']]
            technical = element['name']
            if self.rename_collisions and len(duplicates) > 1:
                from hashlib import sha256
                identity = (element.get('_libraryPrefix', ''), element['namespace'], element['name'])
                technical = 'ТестТип' + sha256(str(identity).encode()).hexdigest()[:16]
            self.claim_owner(technical, element)
            self.canonical_elements[technical] = element
            return technical + (dot + variant if dot else '.Значение' if element['elementType'] == 'Структура' else "")

        return re.sub(pattern, shorten, type_name)

    def sbsl_type(self, type_name):
        """Enums live in script modules; constants retain their XBSL spelling."""
        # Already lowered Enum.Value must stay idempotent when reused in
        # dependency contracts; only an unqualified enum type needs a variant.
        return re.sub(rf'(?<![\w.]){IDENT}(?![\w.])', lambda m: self.enum_types.get(m[0], m[0]),
                      self.canonical_type(type_name))

    def require(self, type_name, namespace=None):
        if not isinstance(type_name, str):
            raise InputError('Имя типа должно быть строкой')
        produced = getattr(self, 'produced_types', {}).get((self.current_source, type_name))
        if produced:
            return
        result = re.fullmatch(r'(?:Стд::БазаДанных::)?РезультатЗапроса<([^<>]+)>', type_name)
        if result:
            self.require(result[1], namespace)
            self.canonical_type(type_name)
            return
        if type_name in self.platform_type_aliases and self.platform_type_aliases[type_name] in self.definitions:
            return
        namespace = self.namespace if namespace is None else namespace
        members = union_members(type_name)
        if members:
            concrete = members[:-1] if members[-1] == '?' else members
            if len(concrete) < 2 or any(not member or member == '?' or member.endswith('?') for member in concrete):
                raise InputError(f"Некорректный union-type: {type_name}")
            canonical = [self.canonical_type(member) for member in concrete]
            if len(set(canonical)) != len(canonical):
                raise InputError(f"Повторяющийся член union-type: {type_name}")
            for member in concrete:
                self.require(member, namespace)
            return
        if type_name.endswith("?"):
            self.require(type_name[:-1], namespace)
            return
        if type_name in SCALARS:
            return
        if type_name in {'Исключение', 'ИсключениеНедопустимоеСостояние', 'Объект', 'Тип'}:
            return
        generic = re.fullmatch(r"(Массив|ЧитаемыйМассив|Обходимое|Соответствие|ЧитаемоеСоответствие)<(.+)>", type_name)
        if generic:
            arguments = split_parameters(generic[2])
            if len(arguments) != (2 if generic[1] in {"Соответствие", "ЧитаемоеСоответствие"} else 1):
                raise InputError(f"Некорректный тип: {type_name}")
            for argument in arguments:
                self.require(argument.strip(), namespace)
            return
        local = self.local_by_source.get(self.current_source, self.local_structures)
        if type_name in local:
            if not self.local_by_source and self.current_source != self.local_source:
                raise InputError(f'Структура {type_name} требует контракт другого модуля')
            declaration, fields, error = local[type_name]
            if error:
                raise InputError(error)
            if self.resolve(type_name, namespace):
                raise InputError(f'Конфликт имени структуры и объекта: {type_name}')
            canonical = self.canonical_type(type_name)
            if canonical in self.fields:
                return
            if canonical in self.active:
                raise InputError(f'Циклическая зависимость структуры: {type_name}')
            self.active.add(canonical)
            adapted = declaration
            for field in fields:
                self.require(field['Тип'], namespace)
                adapted = adapted.replace(': ' + field['Тип'], ': ' + self.sbsl_type(field['Тип']))
            self.fields[canonical] = fields
            if '.' in canonical:
                adapted = re.sub(r'(?<=структура )' + re.escape(type_name) + r'\b', 'Значение', adapted, count=1)
                self.definitions[canonical] = '@Глобально\n' + adapted + '\n'
            else:
                self.required_structures[canonical] = adapted
            self.active.remove(canonical)
            return
        if type_name in self.local_structures and self.current_source != self.local_source and type_name not in local:
            raise InputError(f'Структура {type_name} требует контракт другого модуля')
        type_name = self.canonical_type(type_name)
        if type_name in self.canonical_elements:
            namespace = self.canonical_elements[type_name]['namespace']
        if type_name in self.definitions:
            return
        structure_owner = type_name.removesuffix('.Значение')
        element = self.canonical_elements.get(structure_owner)
        if element and element['elementType'] == 'Структура':
            from .project_structures import generate_structure
            generate_structure(self, type_name, element)
            return
        if re.fullmatch(IDENT, type_name):
            matches = [self.canonical_elements[type_name]] if type_name in self.canonical_elements else self.resolve(type_name, namespace)
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
        matches = [self.canonical_elements[owner]] if owner in self.canonical_elements else self.resolve(owner, namespace)
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
            fields = [{"Имя": "Идентификатор", "Тип": self.reference_id_type}]
        elif variant == "ПараметрыЗаписи":
            # Empty test contract: no platform flags or write semantics are invented.
            fields = []
        elif variant in {"Объект", "Данные"}:
            fields = [dict(f) for f in element["properties"].get("Реквизиты", [])]
            if (element['elementType'] == 'Справочник'
                    and element['properties'].get('Иерархический') is True
                    and not any(f['Имя'] == 'Родитель' for f in fields)):
                fields.append({'Имя':'Родитель','Тип':owner+'.Ссылка?'})
            for field in fields:
                if field['Имя'] == 'Наименование' and element['elementType'] == 'Справочник':
                    field.setdefault('Тип', 'Строка')
            if variant == "Объект":
                fields = [{"Имя": "Ссылка", "Тип": owner + ".Ссылка"}] + fields
            fields += [{"Имя": t["Имя"], "Тип": f"Массив<{owner}.{t['Имя']}>"}
                       for t in element["properties"].get("ТабличныеЧасти", [])]
        else:
            tables = [t for t in element["properties"].get("ТабличныеЧасти", []) if t["Имя"] == variant]
            if len(tables) != 1:
                raise InputError(f"Табличная часть {type_name} отсутствует или неоднозначна")
            fields = [dict(f) for f in tables[0].get("Реквизиты", [])]
        lines, names = ["@Глобально", f"структура {variant}"], set()
        for field in fields:
            name, field_type = field["Имя"], field.get("Тип")
            if not re.fullmatch(IDENT, name) or name in names or not field_type:
                raise InputError(f"Некорректное поле сгенерированного типа {type_name}: {name}")
            names.add(name)
            previous = self.namespace, self.imports
            self.namespace, self.imports = element['namespace'], ()
            try:
                self.require(field_type, element['namespace'])
                field_type = self.canonical_type(field_type)
            finally:
                self.namespace, self.imports = previous
            field = {**field, 'Тип': field_type}
            fields[fields.index(next(f for f in fields if f['Имя'] == name))] = field
            default = ""
            if "ЗначениеПоУмолчанию" in field:
                default = " = " + self.literal(field["ЗначениеПоУмолчанию"], field_type)
            lines.append(f"    пер {name}: {self.sbsl_type(field_type)}{default}")
        self.active.remove(type_name)
        self.fields[type_name] = fields
        self.definitions[type_name] = "\n".join(lines + [";", ""])

    def claim_owner(self, owner, element):
        identity = (element["namespace"], element["name"])
        if owner in self.owners and self.owners[owner] != identity:
            raise InputError(f"Конфликт кратких имён сгенерированных типов: {owner}")
        self.owners[owner] = identity

    def attach_method(self, type_name, method, signature_types):
        self.require(type_name)
        type_name = self.canonical_type(type_name)
        self.methods[type_name] = self.methods.get(type_name, "") + "@Глобально\n" + method.rstrip() + "\n"
        self.method_dependencies.setdefault(type_name, []).extend(signature_types)

    def configure_references(self, mocks):
        """Known IDs load fresh typed snapshots; absent IDs fail in the executor."""
        if not isinstance(mocks, dict):
            raise InvalidTestError("mocks.objects должен быть объектом")
        for reference_type, objects in mocks.items():
            reference_type = self.canonical_type(reference_type)
            if not re.fullmatch(rf"{IDENT}\.Ссылка", reference_type) or not isinstance(objects, dict):
                raise InvalidTestError("mocks.objects: требуются тип Ссылка и отображение идентификаторов")
            if reference_type in self.reference_mocks:
                raise InvalidTestError(f"Повторяющийся тип fake-ссылки: {reference_type}")
            self.reference_mocks[reference_type] = objects
            owner = reference_type.split(".")[0]
            object_type = owner + ".Объект"
            self.require(reference_type)
            self.require(object_type)
            lines = [f"метод ЗагрузитьОбъект(): {object_type}",
                     f"    знч Объекты = новый Соответствие<Строка, {object_type}>()"]
            for identifier, value in objects.items():
                lines.append("    Объекты.Вставить(" + self.literal(identifier, "Строка") + ", "
                             + self.literal(value, object_type) + ")")
            lines += ["    возврат Объекты.Получить(Идентификатор)", ";\n"]
            self.attach_method(reference_type, "\n".join(lines), [object_type])

    def literal(self, value, type_name):
        from .runtime import sbsl_literal
        type_name = self.canonical_type(type_name)
        members = union_members(type_name)
        if members:
            if value is None and members[-1] == '?':
                return 'Неопределено'
            variants = [m for m in members if m != '?']
            if isinstance(value, dict) and set(value) == {'type', 'value'}:
                selected = self.canonical_type(value['type'])
                if selected not in variants:
                    raise InvalidTestError(f'Вариант {selected} не входит в {type_name}')
                return self.literal(value['value'], selected)
            candidates = []
            for variant in variants:
                try:
                    candidates.append(self.literal(value, variant))
                except InputError:
                    pass
            if len(candidates) != 1:
                raise InvalidTestError(f'Для {type_name} требуется явный вариант {{type, value}}')
            return candidates[0]
        if type_name.endswith("?"):
            return "Неопределено" if value is None else self.literal(value, type_name[:-1])
        if type_name in {"Строка", "Число", "Булево"}:
            return sbsl_literal(value, type_name)
        if type_name == 'Ууид':
            import uuid
            if not isinstance(value, str) or not re.fullmatch(
                    r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value):
                raise InvalidTestError('Ууид требует строку UUID в формате 8-4-4-4-12')
            return 'новый Ууид(' + sbsl_literal(str(uuid.UUID(value)), 'Строка') + ')'
        if type_name == 'Время':
            match = (re.fullmatch(r'([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.([0-9]{3}))?)?', value)
                     if isinstance(value, str) else None)
            if not match or int(match[1]) > 23 or int(match[2]) > 59 or (match[3] and int(match[3]) > 59):
                raise InvalidTestError('Время требует строку HH:MM[:SS[.SSS]] в диапазоне 00:00:00–23:59:59.999')
            return 'новый Время("' + value + '")'
        if type_name == 'ДатаВремя':
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', value):
                raise InvalidTestError('ДатаВремя требует строку YYYY-MM-DDTHH:MM:SS без часового пояса')
            try:
                date = datetime.fromisoformat(value)
            except ValueError as exc:
                raise InvalidTestError('Некорректная ДатаВремя') from exc
            return 'новый ДатаВремя(' + ', '.join(str(n) for n in (
                date.year, date.month, date.day, date.hour, date.minute, date.second)) + ')'
        if type_name == 'Момент':
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z', value):
                raise InvalidTestError('Момент требует UTC строку YYYY-MM-DDTHH:MM:SSZ')
            try:
                datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError as exc:
                raise InvalidTestError('Некорректный Момент') from exc
            return 'новый Момент(' + sbsl_literal(value, 'Строка') + ')'
        if type_name == 'Дата':
            if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                raise InvalidTestError('Дата требует строку YYYY-MM-DD')
            try:
                date = datetime.fromisoformat(value)
            except ValueError as exc:
                raise InvalidTestError('Некорректная Дата') from exc
            return f'новый Дата({date.year}, {date.month}, {date.day})'
        if type_name in self.enums:
            if not isinstance(value, str):
                raise InvalidTestError(f"Ожидалось имя элемента {type_name}")
            name = value.removeprefix(type_name + ".")
            if name not in self.enums[type_name]:
                raise InvalidTestError(f"Неизвестный элемент {type_name}: {value}")
            return f"{type_name}.{name}"
        mapping = re.fullmatch(r'Соответствие<(.+)>', type_name)
        if mapping:
            key_type, value_type = [p.strip() for p in split_parameters(mapping[1])]
            if key_type != 'Строка' or not isinstance(value, dict):
                raise InvalidTestError('Соответствие fixture требует строковые ключи и объект')
            if not value:
                return 'новый ' + self.sbsl_type(type_name) + '()'
            return 'новый ' + self.sbsl_type(type_name) + '({' + ', '.join(
                self.literal(k, key_type) + ': ' + self.literal(v, value_type) for k, v in value.items()) + '})'
        collection = re.fullmatch(r'(Массив|ЧитаемыйМассив|Обходимое)<(.+)>', type_name)
        if collection:
            if not isinstance(value, list):
                raise InvalidTestError(f"Ожидался массив для {type_name}")
            inner = collection[2].strip()
            # Explicit constructor keeps the element type even for an empty array.
            return f"новый Массив<{self.sbsl_type(inner)}>([" + ", ".join(self.literal(v, inner) for v in value) + "])"
        if type_name in self.fields:
            if not isinstance(value, dict):
                raise InvalidTestError(f"Ожидался объект для {type_name}")
            fields = {f["Имя"]: f["Тип"] for f in self.fields[type_name]}
            if set(value) - fields.keys():
                raise InvalidTestError(f"Неизвестные поля {type_name}: {sorted(set(value) - fields.keys())}")
            missing = [f['Имя'] for f in self.fields[type_name] if f.get('constructorRequired') and f['Имя'] not in value]
            if missing:
                raise InvalidTestError(f"Отсутствуют обязательные поля {type_name}: {missing}")
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
        from .runtime import check_dependency_cycles
        check_dependency_cycles(graph)
        for owner, definitions in grouped.items():
            imports = [f"#требуется {dep}.sbsl" for dep in sorted(graph[owner])]
            (directory / f"{owner}.sbsl").write_text("\n".join(imports + definitions), encoding="utf-8")
        return "\n".join(f"#требуется {owner}.sbsl" for owner in sorted(grouped))
